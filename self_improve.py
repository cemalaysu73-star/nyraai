from __future__ import annotations

"""
SelfImprove — background self-improvement coordinator for Nyra.

Architecture:
  FailureLog        — logs every interaction with outcome metadata
  LearnedPatterns   — persists auto-learned alias/shortcut mappings
  ImprovementEngine — clusters failures, generates improvement candidates
  SelfImprove       — wires it all together, runs background loop

Background loop:
  Runs 2 minutes after startup, then every 30 minutes.
  Analyzes last 300 interactions.
  Auto-applies low-risk improvements (alias, shortcut).
  Queues high-risk improvements (route_hint) for review.

Integration contract (called from ui.py):
  improve.record(raw_text, language, route, action_detail, response_text)
  improve.check_and_flag_correction(text)   → bool (True = was correction)
  improve.resolve(text)                     → str | None (substituted text)
  improve.run_cycle()                       → dict summary
  improve.get_suggestions()                 → list[dict] for display
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR
from failure_log import FailureLog, InteractionLog
from improvement_engine import ImprovementEngine, Improvement, is_correction
from learned_patterns import LearnedPatterns

SUGGESTIONS_FILE = DATA_DIR / "improvement_suggestions.json"
CYCLE_INTERVAL   = 1800    # 30 minutes
STARTUP_DELAY    = 120     # 2 minutes after launch

# Correction window: if the next input arrives within this many seconds
# and looks like a correction, the previous interaction is marked.
CORRECTION_WINDOW = 25.0


class SelfImprove:

    def __init__(self) -> None:
        self._log      = FailureLog()
        self._patterns = LearnedPatterns()
        self._engine   = ImprovementEngine()

        self._last_entry: Optional[InteractionLog] = None
        self._last_entry_ts: float = 0.0
        self._last_raw: str = ""

        self._suggestions: list[dict] = []
        self._suggestions_lock = threading.Lock()
        self._load_suggestions()

        print(f"[SelfImprove] Ready — {self._log.count()} logged, "
              f"{self._patterns.count()} patterns learned")

    # ── Core API (called from ui.py) ──────────────────────────────────────────

    def record(
        self,
        raw_text: str,
        language: str,
        route: str,
        action_detail: str = "",
        response_text: str = "",
        duration_ms: int = 0,
    ) -> InteractionLog:
        """
        Log every completed interaction.
        Call AFTER the response is delivered, or just before _deliver_response.
        """
        entry = self._log.new_entry(
            raw_text, language, route, action_detail, response_text, duration_ms
        )
        self._last_entry = entry
        self._last_entry_ts = time.monotonic()
        self._last_raw = raw_text
        return entry

    def check_and_flag_correction(self, text: str) -> bool:
        """
        Call at the START of each new _process_input before routing.
        If the new input looks like a correction of the previous response
        and arrived within the correction window, marks the previous entry.
        Returns True so the caller can note the event.
        """
        if not self._last_entry:
            return False
        elapsed = time.monotonic() - self._last_entry_ts
        if elapsed > CORRECTION_WINDOW:
            return False
        if is_correction(text):
            self._log.mark_correction(self._last_entry.id, text)
            print(f"[SelfImprove] Correction detected for '{self._last_raw[:40]}'")
            return True
        return False

    def resolve(self, text: str) -> str | None:
        """
        Check learned mappings before routing.
        Returns the substituted target text if a confident mapping exists.
        The caller should use this as the new routing text if not None.
        """
        return self._patterns.resolve(text)

    def bump(self, text: str, mapping_type: str, success: bool) -> None:
        """Feedback: was a learned mapping used correctly or not?"""
        self._patterns.bump(text, mapping_type, success)

    # ── Analysis cycle ────────────────────────────────────────────────────────

    def run_cycle(self) -> dict:
        """
        One full improvement cycle.
        Returns a summary dict: {status, applied, queued, total_patterns}.
        """
        logs = self._log.get_recent(300)
        if len(logs) < 5:
            return {"status": "not_enough_data", "applied": 0, "queued": 0,
                    "total_patterns": self._patterns.count()}

        candidates = self._engine.analyze(logs)
        applied = 0
        queued = 0

        for imp in candidates:
            if self._is_auto_applicable(imp):
                self._patterns.add(
                    pattern=imp.phrase,
                    target=imp.suggestion,
                    mapping_type=imp.type,
                    confidence=imp.confidence,
                    source_count=imp.evidence_count,
                )
                applied += 1
                print(f"[SelfImprove] Applied '{imp.type}': "
                      f"'{imp.phrase}' -> '{imp.suggestion}' "
                      f"(conf={imp.confidence:.2f}, n={imp.evidence_count})")
            else:
                self._queue_suggestion(imp)
                queued += 1

        self._save_suggestions()
        summary = {
            "status": "ok",
            "applied": applied,
            "queued": queued,
            "total_patterns": self._patterns.count(),
            "total_logs": len(logs),
        }
        print(f"[SelfImprove] Cycle done — {summary}")
        return summary

    def start_background_loop(self) -> None:
        """Start the periodic improvement loop (daemon thread)."""
        t = threading.Thread(target=self._loop, daemon=True, name="SelfImprove")
        t.start()

    # ── Suggestion queue (high-risk, for review) ──────────────────────────────

    def get_suggestions(self) -> list[dict]:
        with self._suggestions_lock:
            return list(self._suggestions)

    def dismiss_suggestion(self, phrase: str) -> None:
        with self._suggestions_lock:
            self._suggestions = [s for s in self._suggestions if s["phrase"] != phrase]
        self._save_suggestions()

    def apply_suggestion(self, phrase: str) -> bool:
        """Manually approve a queued high-risk suggestion."""
        with self._suggestions_lock:
            match = next((s for s in self._suggestions if s["phrase"] == phrase), None)
            if not match:
                return False
            self._suggestions = [s for s in self._suggestions if s["phrase"] != phrase]

        self._patterns.add(
            pattern=match["phrase"],
            target=match["suggestion"],
            mapping_type=match["type"],
            confidence=match["confidence"],
        )
        self._save_suggestions()
        print(f"[SelfImprove] Suggestion manually applied: '{phrase}'")
        return True

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "total_interactions": self._log.count(),
            "total_corrections": len(self._log.get_corrections()),
            "learned_patterns": self._patterns.count(),
            "pending_suggestions": len(self._suggestions),
            "aliases": len(self._patterns.get_all("alias")),
            "shortcuts": len(self._patterns.get_all("shortcut")),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _is_auto_applicable(imp: Improvement) -> bool:
        """
        Low-risk auto-apply criteria:
          • type is alias or shortcut (not route_hint / prompt_hint)
          • confidence >= 0.60
          • evidence_count >= 2
        """
        return (
            imp.risk == "low"
            and imp.type in ("alias", "shortcut", "stt_fix")
            and imp.confidence >= 0.60
            and imp.evidence_count >= 2
        )

    def _queue_suggestion(self, imp: Improvement) -> None:
        with self._suggestions_lock:
            existing = [s["phrase"] for s in self._suggestions]
            if imp.phrase not in existing:
                self._suggestions.append({
                    "type": imp.type,
                    "risk": imp.risk,
                    "phrase": imp.phrase,
                    "suggestion": imp.suggestion,
                    "confidence": round(imp.confidence, 3),
                    "evidence_count": imp.evidence_count,
                    "reasoning": imp.reasoning,
                    "examples": imp.examples[:2],
                    "queued_at": datetime.now().isoformat(),
                })

    def _loop(self) -> None:
        time.sleep(STARTUP_DELAY)
        while True:
            try:
                self.run_cycle()
            except Exception as exc:
                print(f"[SelfImprove] Loop error: {exc}")
            time.sleep(CYCLE_INTERVAL)

    def _load_suggestions(self) -> None:
        if not SUGGESTIONS_FILE.exists():
            return
        try:
            raw = json.loads(SUGGESTIONS_FILE.read_text(encoding="utf-8"))
            self._suggestions = raw.get("suggestions", [])
        except Exception:
            self._suggestions = []

    def _save_suggestions(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._suggestions_lock:
            data = {"suggestions": self._suggestions}
        SUGGESTIONS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
