from __future__ import annotations

"""
LearnedPatterns — persistent store for auto-learned phrase mappings.

Mapping types:
  "alias"     — phrase → app/action label (e.g. "my music" → "spotify")
  "shortcut"  — full phrase → known action the user expected instead of LLM
  "stt_fix"   — bad transcription → corrected phrase (complements stt_repair.py,
                 which handles word-level; this handles meaning-level)

Each mapping tracks hits/misses and is auto-removed if reliability drops below 0.30.
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from threading import Lock

from config import DATA_DIR

PATTERNS_FILE = DATA_DIR / "learned_patterns.json"
MIN_SCORE = 0.30   # below this a mapping is discarded


@dataclass
class LearnedMapping:
    pattern: str        # normalized (lower, stripped) phrase
    target: str         # what it maps to
    mapping_type: str   # "alias" | "shortcut" | "stt_fix"
    confidence: float   # initial confidence from evidence strength
    hits: int = 0
    misses: int = 0
    created_at: str = ""
    source_count: int = 1   # number of evidence examples

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LearnedMapping":
        return LearnedMapping(
            pattern=d.get("pattern", ""),
            target=d.get("target", ""),
            mapping_type=d.get("mapping_type", "alias"),
            confidence=d.get("confidence", 0.7),
            hits=d.get("hits", 0),
            misses=d.get("misses", 0),
            created_at=d.get("created_at", ""),
            source_count=d.get("source_count", 1),
        )

    @property
    def score(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return self.confidence
        reliability = self.hits / total
        return self.confidence * 0.4 + reliability * 0.6


class LearnedPatterns:
    def __init__(self) -> None:
        self._lock = Lock()
        self._mappings: dict[str, LearnedMapping] = {}
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> None:
        if not PATTERNS_FILE.exists():
            return
        try:
            raw = json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
            with self._lock:
                self._mappings = {
                    k: LearnedMapping.from_dict(v)
                    for k, v in raw.get("mappings", {}).items()
                }
        except Exception:
            self._mappings = {}

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = {"mappings": {k: v.to_dict() for k, v in self._mappings.items()}}
        PATTERNS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Adding and resolving ──────────────────────────────────────────────────

    def add(
        self,
        pattern: str,
        target: str,
        mapping_type: str,
        confidence: float = 0.70,
        source_count: int = 1,
    ) -> None:
        key = _key(pattern, mapping_type)
        with self._lock:
            if key in self._mappings:
                m = self._mappings[key]
                m.source_count += source_count
                m.confidence = min(0.92, m.confidence + 0.06)
            else:
                self._mappings[key] = LearnedMapping(
                    pattern=pattern.lower().strip(),
                    target=target,
                    mapping_type=mapping_type,
                    confidence=confidence,
                    created_at=datetime.now().isoformat(),
                    source_count=source_count,
                )
        self.save()

    def resolve(self, text: str, mapping_type: str | None = None) -> str | None:
        """
        Check if text matches a learned mapping.
        Returns the target string if found and score >= 0.50, else None.
        """
        normalized = text.lower().strip()
        types = (mapping_type,) if mapping_type else ("alias", "shortcut", "stt_fix")
        with self._lock:
            for mt in types:
                k = _key(normalized, mt)
                if k in self._mappings:
                    m = self._mappings[k]
                    if m.score >= 0.50:
                        return m.target
        return None

    def bump(self, pattern: str, mapping_type: str, success: bool) -> None:
        """Update hit/miss counter. Discards mapping if score drops below MIN_SCORE."""
        key = _key(pattern, mapping_type)
        with self._lock:
            if key not in self._mappings:
                return
            m = self._mappings[key]
            if success:
                m.hits += 1
            else:
                m.misses += 1
            if m.score < MIN_SCORE:
                del self._mappings[key]
        self.save()

    def remove(self, pattern: str, mapping_type: str) -> None:
        key = _key(pattern, mapping_type)
        with self._lock:
            self._mappings.pop(key, None)
        self.save()

    def get_all(self, mapping_type: str | None = None) -> list[LearnedMapping]:
        with self._lock:
            vals = list(self._mappings.values())
        if mapping_type:
            vals = [v for v in vals if v.mapping_type == mapping_type]
        return sorted(vals, key=lambda m: m.score, reverse=True)

    def count(self) -> int:
        with self._lock:
            return len(self._mappings)


def _key(pattern: str, mapping_type: str) -> str:
    return f"{mapping_type}:{pattern.lower().strip()}"
