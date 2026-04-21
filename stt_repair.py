from __future__ import annotations

"""
STTRepair — learns and corrects repeated Whisper transcription errors.

How it learns:
  1. When the intent store returns a medium-confidence match (0.45–0.62),
     the transcript might be a corrupted version of a stored phrase.
     We log the (transcript, canonical_phrase) pair.

  2. After CONFIRM_THRESHOLD identical (wrong → correct) pairs are seen,
     the correction is promoted and applied automatically before routing.

  3. Explicit teaching: "Nyra, 'disc' means 'discord'"
     (handled via router → ui → learn_explicit)

How it works at inference:
  The repair is a two-pass process:
    Pass 1 — word-level: replace known misheard words ("disc" → "discord")
    Pass 2 — phrase-level: replace full known bad phrases

This runs BEFORE router and intent matching, so all downstream logic benefits.
"""

import json
import re
from pathlib import Path

from config import DATA_DIR

REPAIR_FILE = DATA_DIR / "stt_repair.json"
CONFIRM_THRESHOLD = 3   # observations before auto-applying


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower().strip())


class STTRepair:

    def __init__(self) -> None:
        # promoted corrections: norm(wrong) -> correct
        self._corrections: dict[str, str] = {}
        # pending: norm(wrong) -> {correct: count}
        self._pending: dict[str, dict[str, int]] = {}
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> None:
        if not REPAIR_FILE.exists():
            return
        try:
            raw = json.loads(REPAIR_FILE.read_text(encoding="utf-8"))
            self._corrections = raw.get("corrections", {})
            self._pending = raw.get("pending", {})
        except Exception:
            self._corrections, self._pending = {}, {}

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        REPAIR_FILE.write_text(
            json.dumps(
                {"corrections": self._corrections, "pending": self._pending},
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── Inference ─────────────────────────────────────────────────────────────

    def repair(self, text: str) -> str:
        """Apply known corrections. Returns original text if no corrections apply."""
        if not self._corrections:
            return text

        result = text

        # Pass 1 — full phrase replacement
        norm = _norm(text)
        if norm in self._corrections:
            return self._corrections[norm]

        # Pass 2 — word-level replacement
        words = result.split()
        changed = False
        new_words = []
        for word in words:
            nw = _norm(word)
            if nw in self._corrections:
                new_words.append(self._corrections[nw])
                changed = True
            else:
                new_words.append(word)
        if changed:
            result = " ".join(new_words)

        return result

    # ── Learning ──────────────────────────────────────────────────────────────

    def observe(self, transcript: str, canonical: str) -> None:
        """
        Called when the intent store matched `transcript` to a promoted intent
        whose representative phrase is `canonical`, at medium confidence.
        The transcript might be a corrupted version of the canonical phrase.
        """
        if _norm(transcript) == _norm(canonical):
            return  # identical — nothing to learn
        if _norm(transcript) in self._corrections:
            return  # already a known correction

        key = _norm(transcript)
        if key not in self._pending:
            self._pending[key] = {}
        bucket = self._pending[key]
        bucket[canonical] = bucket.get(canonical, 0) + 1

        # Promote if threshold reached
        best_correct = max(bucket, key=bucket.__getitem__)
        if bucket[best_correct] >= CONFIRM_THRESHOLD:
            self._corrections[key] = best_correct
            del self._pending[key]
            print(f"[STT] Correction promoted: '{transcript}' -> '{best_correct}'")

        self.save()

    def learn_explicit(self, wrong: str, correct: str) -> None:
        """Teach Nyra a correction directly: 'when I say X I mean Y'."""
        key = _norm(wrong)
        self._corrections[key] = correct
        self._pending.pop(key, None)
        self.save()
        print(f"[STT] Explicit correction learned: '{wrong}' -> '{correct}'")

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def known_corrections(self) -> dict[str, str]:
        return dict(self._corrections)

    def pending_count(self) -> int:
        return len(self._pending)
