from __future__ import annotations

"""
FailureLog — structured interaction log for self-improvement.

Every user turn is logged as a JSON line with:
  raw_text, language, route, action_detail, outcome, response_text

Outcomes start as "success" and are updated to "correction" if the
very next user turn looks like a correction, or "failure" on errors.
"""

import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from threading import Lock

from config import DATA_DIR

LOG_FILE = DATA_DIR / "interaction_log.jsonl"
MAX_ENTRIES = 3000


@dataclass
class InteractionLog:
    id: str
    timestamp: str
    raw_text: str
    language: str
    route: str            # action name from router, or "llm", or "intent"
    action_detail: str    # params / snippet
    outcome: str          # "success" | "correction" | "failure"
    correction_text: str = ""   # next user turn if it was a correction
    response_text: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "InteractionLog":
        fields = InteractionLog.__dataclass_fields__
        return InteractionLog(**{k: d.get(k, "" if k != "duration_ms" else 0) for k in fields})


class FailureLog:
    def __init__(self) -> None:
        self._lock = Lock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Writing ───────────────────────────────────────────────────────────────

    def new_entry(
        self,
        raw_text: str,
        language: str,
        route: str,
        action_detail: str = "",
        response_text: str = "",
        duration_ms: int = 0,
    ) -> InteractionLog:
        entry = InteractionLog(
            id=str(uuid.uuid4())[:8],
            timestamp=datetime.now().isoformat(),
            raw_text=raw_text.strip(),
            language=language,
            route=route,
            action_detail=action_detail[:200],
            outcome="success",
            response_text=response_text[:300],
            duration_ms=duration_ms,
        )
        self._append(entry)
        return entry

    def mark_correction(self, entry_id: str, correction_text: str) -> None:
        entries = self._load_all()
        for e in entries:
            if e.id == entry_id:
                e.outcome = "correction"
                e.correction_text = correction_text[:200]
                break
        self._save_all(entries)

    def mark_failure(self, entry_id: str) -> None:
        entries = self._load_all()
        for e in entries:
            if e.id == entry_id:
                e.outcome = "failure"
                break
        self._save_all(entries)

    # ── Reading ───────────────────────────────────────────────────────────────

    def get_recent(self, n: int = 300) -> list[InteractionLog]:
        return self._load_all()[-n:]

    def get_corrections(self) -> list[InteractionLog]:
        return [e for e in self._load_all() if e.outcome == "correction"]

    def get_failures(self) -> list[InteractionLog]:
        return [e for e in self._load_all() if e.outcome in ("correction", "failure")]

    def count(self) -> int:
        if not LOG_FILE.exists():
            return 0
        return sum(1 for _ in LOG_FILE.open(encoding="utf-8"))

    # ── Internals ─────────────────────────────────────────────────────────────

    def _append(self, entry: InteractionLog) -> None:
        with self._lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        self._rotate_if_needed()

    def _load_all(self) -> list[InteractionLog]:
        if not LOG_FILE.exists():
            return []
        entries: list[InteractionLog] = []
        with self._lock:
            with open(LOG_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(InteractionLog.from_dict(json.loads(line)))
                    except Exception:
                        pass
        return entries

    def _save_all(self, entries: list[InteractionLog]) -> None:
        with self._lock:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")

    def _rotate_if_needed(self) -> None:
        if not LOG_FILE.exists():
            return
        with self._lock:
            lines = LOG_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > MAX_ENTRIES:
            with self._lock:
                LOG_FILE.write_text("".join(lines[-MAX_ENTRIES:]), encoding="utf-8")
