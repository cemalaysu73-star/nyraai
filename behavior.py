from __future__ import annotations

"""
BehaviorTracker — learns what the user does, when.

Stores (app_key, hour_bucket, weekday) frequency counts.
Used to:
  1. Generate context for the LLM system prompt
     e.g. "User typically opens Steam on Friday evenings."
  2. Rank intent predictions by time-of-day likelihood
  3. Power proactive suggestions in the future
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config import DATA_DIR

BEHAVIOR_FILE = DATA_DIR / "behavior.json"

# 3-hour buckets: 0=0-3h, 1=3-6h, ..., 7=21-24h
_BUCKET_LABELS = [
    "late night", "early morning", "morning", "mid-morning",
    "noon", "afternoon", "evening", "night",
]
_WEEKDAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _now() -> tuple[int, int]:
    """Return (hour_bucket 0-7, weekday 0-6)."""
    n = datetime.now()
    return n.hour // 3, n.weekday()


class BehaviorTracker:

    def __init__(self) -> None:
        # key: "app_key|bucket|weekday" → count
        self._counts: dict[str, int] = {}
        # key: "app_key" → total all-time opens
        self._totals: dict[str, int] = {}
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> None:
        if not BEHAVIOR_FILE.exists():
            return
        try:
            raw = json.loads(BEHAVIOR_FILE.read_text(encoding="utf-8"))
            self._counts = raw.get("counts", {})
            self._totals = raw.get("totals", {})
        except Exception:
            self._counts, self._totals = {}, {}

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        BEHAVIOR_FILE.write_text(
            json.dumps({"counts": self._counts, "totals": self._totals}, indent=2),
            encoding="utf-8",
        )

    # ── Recording ─────────────────────────────────────────────────────────────

    def record(self, app_key: str) -> None:
        """
        Record that this app was used right now.
        app_key: "OPEN:steam", "WEB:https://youtube.com", etc.
        """
        bucket, weekday = _now()
        slot = f"{app_key}|{bucket}|{weekday}"
        self._counts[slot] = self._counts.get(slot, 0) + 1
        self._totals[app_key] = self._totals.get(app_key, 0) + 1
        self.save()

    # ── Querying ──────────────────────────────────────────────────────────────

    def top_apps_now(self, n: int = 3) -> list[str]:
        """Return the n most likely app keys for the current time slot."""
        bucket, weekday = _now()
        scores: dict[str, float] = defaultdict(float)

        for slot, count in self._counts.items():
            parts = slot.rsplit("|", 2)
            if len(parts) != 3:
                continue
            app, b, w = parts[0], int(parts[1]), int(parts[2])

            if w == weekday:
                if b == bucket:
                    scores[app] += float(count)          # exact time match
                elif abs(b - bucket) == 1:
                    scores[app] += float(count) * 0.4   # adjacent bucket

        return sorted(scores, key=scores.__getitem__, reverse=True)[:n]

    def top_apps_alltime(self, n: int = 5) -> list[str]:
        return sorted(self._totals, key=self._totals.__getitem__, reverse=True)[:n]

    def usage_count(self, app_key: str) -> int:
        return self._totals.get(app_key, 0)

    # ── LLM context ───────────────────────────────────────────────────────────

    def context_for_llm(self) -> str:
        """
        Short string to append to the LLM system prompt.
        Tells the model about the user's habits so it can give better suggestions.
        """
        bucket, weekday = _now()
        day = _WEEKDAY_LABELS[weekday]
        time_label = _BUCKET_LABELS[bucket]

        top_now = self.top_apps_now(3)
        top_all  = self.top_apps_alltime(5)

        lines: list[str] = []

        if top_now:
            labels = [_app_label(a) for a in top_now]
            lines.append(
                f"Based on past behavior, user often uses {', '.join(labels)} "
                f"on {day} {time_label}."
            )

        if top_all:
            labels = [_app_label(a) for a in top_all]
            lines.append(f"Most used apps overall: {', '.join(labels)}.")

        return " ".join(lines)

    def summary(self) -> str:
        """Human-readable summary for debugging / status display."""
        lines = [f"Total tracked apps: {len(self._totals)}"]
        for app, count in sorted(self._totals.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {_app_label(app)}: {count}x")
        return "\n".join(lines)


def _app_label(app_key: str) -> str:
    import re
    if app_key.upper().startswith("OPEN:"):
        return app_key[5:].title()
    if app_key.upper().startswith("WEB:"):
        domain = re.sub(r"https?://(www\.)?", "", app_key[4:]).split("/")[0]
        return domain
    return app_key
