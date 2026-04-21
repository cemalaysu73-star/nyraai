from __future__ import annotations

"""
LifeLog — continuous activity logger with temporal querying.

Logs every 30 seconds: active window, running app.
Logs every Nyra command and action.

Query examples:
  "What was I doing last Tuesday at 3pm?"
  "Show me everything about the payment project"
  "When did I last open Discord?"
  "What files was I working on yesterday?"

Queries are answered by filtering log entries by time/keyword,
then passing them to the LLM for synthesis.
"""

import ctypes
import ctypes.wintypes
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import DATA_DIR

LOG_FILE   = DATA_DIR / "life_log.json"
POLL_INTERVAL = 30   # seconds between passive window polls
MAX_ENTRIES   = 5000 # rotate when exceeded


# ── Active window detection (no extra deps) ───────────────────────────────────

def _get_active_window() -> str:
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    ts: str          # ISO timestamp
    kind: str        # "window" | "command" | "action" | "app_opened"
    text: str        # human-readable content
    meta: dict       # extra data (app name, url, result, etc.)

    def to_dict(self) -> dict:
        return {"ts": self.ts, "kind": self.kind, "text": self.text, "meta": self.meta}

    @staticmethod
    def from_dict(d: dict) -> "LogEntry":
        return LogEntry(ts=d["ts"], kind=d["kind"], text=d["text"], meta=d.get("meta", {}))

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.ts)


# ── Time parsing ──────────────────────────────────────────────────────────────

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "pazartesi": 0, "sali": 1, "salı": 1, "carsamba": 2, "çarşamba": 2,
    "persembe": 3, "perşembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
}

def _parse_time_range(query: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """Extract a (start, end) time window from a natural language query."""
    now = datetime.now()
    q = query.lower()

    if "yesterday" in q or "dün" in q:
        d = now - timedelta(days=1)
        return d.replace(hour=0, minute=0, second=0), d.replace(hour=23, minute=59, second=59)

    if "today" in q or "bugün" in q or "bugun" in q:
        return now.replace(hour=0, minute=0, second=0), now

    if "last week" in q or "geçen hafta" in q or "gecen hafta" in q:
        start = now - timedelta(days=7)
        return start.replace(hour=0, minute=0, second=0), now

    for name, wd in _WEEKDAYS.items():
        if name in q:
            delta = (now.weekday() - wd) % 7 or 7
            d = now - timedelta(days=delta)
            return d.replace(hour=0, minute=0, second=0), d.replace(hour=23, minute=59, second=59)

    # Hour reference: "at 3pm", "saat 15"
    m = re.search(r"\bat\s*(\d{1,2})\s*(am|pm)?\b", q)
    if m:
        h = int(m.group(1))
        if m.group(2) == "pm" and h < 12:
            h += 12
        # Return a 1-hour window on today (or the most recent occurrence)
        candidate = now.replace(hour=h, minute=0, second=0)
        if candidate > now:
            candidate -= timedelta(days=1)
        return candidate, candidate.replace(minute=59, second=59)

    # Default: last 24 hours
    return now - timedelta(hours=24), now


# ── LifeLog ───────────────────────────────────────────────────────────────────

class LifeLog:

    def __init__(self) -> None:
        self._entries: list[LogEntry] = []
        self._lock = threading.Lock()
        self._last_window = ""
        self._poll_thread: Optional[threading.Thread] = None
        self._agent = None
        self.load()

    def set_agent(self, agent) -> None:
        self._agent = agent

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        print("[LifeLog] Started.")

    # ── Logging ───────────────────────────────────────────────────────────────

    def log_command(self, text: str) -> None:
        self._add(LogEntry(
            ts=_now(), kind="command",
            text=f"User: {text}",
            meta={"query": text},
        ))

    def log_action(self, action: str, detail: str = "") -> None:
        self._add(LogEntry(
            ts=_now(), kind="action",
            text=f"Nyra: {action}" + (f" — {detail}" if detail else ""),
            meta={"action": action, "detail": detail},
        ))

    def log_app_opened(self, app: str) -> None:
        self._add(LogEntry(
            ts=_now(), kind="app_opened",
            text=f"Opened: {app}",
            meta={"app": app},
        ))

    def log_response(self, response: str) -> None:
        self._add(LogEntry(
            ts=_now(), kind="response",
            text=f"Nyra replied: {response[:200]}",
            meta={},
        ))

    # ── Querying ──────────────────────────────────────────────────────────────

    def query(self, question: str, language: str = "en") -> str:
        """Answer a temporal query about past activity."""
        start, end = _parse_time_range(question)

        with self._lock:
            # Filter by time window
            entries = [
                e for e in self._entries
                if start <= e.dt <= end
            ] if start and end else list(self._entries[-200:])

        if not entries:
            if language == "tr":
                return "O zaman dilimi için kayıt bulunamadı, efendim."
            return "No activity found for that time period, sir."

        # Keyword filter if time window is large
        keywords = _extract_keywords(question)
        if keywords and len(entries) > 50:
            scored = []
            for e in entries:
                score = sum(1 for kw in keywords if kw in e.text.lower())
                if score > 0:
                    scored.append((score, e))
            if scored:
                scored.sort(key=lambda x: -x[0])
                entries = [e for _, e in scored[:40]]

        # Format for LLM
        log_text = "\n".join(
            f"[{e.dt.strftime('%H:%M')}] {e.text}"
            for e in entries[:60]
        )

        if self._agent is None:
            # No LLM — return raw log
            return f"Activity log ({len(entries)} entries):\n{log_text}"

        prompt = (
            f"The user asked: \"{question}\"\n\n"
            f"Here is their activity log for that period:\n{log_text}\n\n"
            f"Answer concisely in {'Turkish' if language == 'tr' else 'English'}. "
            f"Be specific about times and activities. Speak as Nyra."
        )

        try:
            return self._agent.respond(prompt, language=language, session_app="")
        except Exception:
            return log_text[:500]

    def recent_summary(self, hours: int = 2, language: str = "en") -> str:
        """Brief summary of recent activity."""
        cutoff = datetime.now() - timedelta(hours=hours)
        with self._lock:
            entries = [e for e in self._entries if e.dt >= cutoff]
        if not entries:
            return ""
        apps = list(dict.fromkeys(
            e.meta.get("app", "")
            for e in entries if e.kind == "app_opened" and e.meta.get("app")
        ))
        commands = [e.meta.get("query", "") for e in entries if e.kind == "command"][-3:]
        parts = []
        if apps:
            parts.append(("Açık uygulamalar" if language == "tr" else "Apps used") + f": {', '.join(apps)}")
        if commands:
            parts.append(("Son komutlar" if language == "tr" else "Recent commands") + f": {', '.join(commands)}")
        return ". ".join(parts)

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> None:
        if not LOG_FILE.exists():
            return
        try:
            raw = json.loads(LOG_FILE.read_text(encoding="utf-8"))
            self._entries = [LogEntry.from_dict(d) for d in raw.get("entries", [])]
        except Exception:
            self._entries = []

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            # Rotate: keep last MAX_ENTRIES
            entries = self._entries[-MAX_ENTRIES:]
        LOG_FILE.write_text(
            json.dumps({"entries": [e.to_dict() for e in entries]}, indent=2),
            encoding="utf-8",
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    def _add(self, entry: LogEntry) -> None:
        with self._lock:
            self._entries.append(entry)
        # Save every 10 entries
        with self._lock:
            count = len(self._entries)
        if count % 10 == 0:
            self.save()

    def _poll_loop(self) -> None:
        while True:
            try:
                window = _get_active_window()
                if window and window != self._last_window:
                    self._last_window = window
                    self._add(LogEntry(
                        ts=_now(), kind="window",
                        text=f"Window: {window[:120]}",
                        meta={"window": window},
                    ))
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

def _extract_keywords(query: str) -> list[str]:
    stopwords = {
        "what", "was", "i", "doing", "when", "show", "me", "the", "a", "an",
        "at", "on", "in", "to", "for", "of", "and", "or", "my", "did", "do",
        "ne", "yapiyordum", "yapıyordum", "göster", "goster", "ne", "zaman",
    }
    return [w for w in re.sub(r"[^\w\s]", "", query.lower()).split() if w not in stopwords and len(w) > 2]
