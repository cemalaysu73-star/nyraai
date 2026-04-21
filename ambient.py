from __future__ import annotations

"""
AmbientCopilot — proactive screen watcher.

Runs silently in the background. When the user appears stuck
(same window for > STUCK_MINUTES with no recent Nyra interaction),
it takes a screenshot, analyzes it, and speaks proactively.

Does NOT interrupt when:
  - User is gaming (fullscreen detected)
  - Nyra was used recently (COOLDOWN_MINUTES)
  - A meeting app is active
  - User explicitly disabled it
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

CHECK_INTERVAL   = 60    # seconds between checks
STUCK_MINUTES    = 10    # how long on same window before we consider intervening
COOLDOWN_MINUTES = 20    # min gap between proactive interventions
MIN_SINCE_USER   = 5     # don't interrupt if Nyra was used < N minutes ago

_MEETING_KEYWORDS = {"zoom", "teams", "meet", "webex", "skype", "toplantı"}
_GAME_FULLSCREEN_HINTS = {"fullscreen", "game", "valorant", "steam", "epic"}


class AmbientCopilot:

    def __init__(self) -> None:
        self._agent = None
        self._speak_fn: Optional[Callable[[str, str], None]] = None

        self._enabled = True
        self._last_window: str = ""
        self._window_since: Optional[datetime] = None
        self._last_intervention: Optional[datetime] = None
        self._last_user_activity: Optional[datetime] = None

        self._thread: Optional[threading.Thread] = None

    # ── Wiring ────────────────────────────────────────────────────────────────

    def set_agent(self, agent) -> None:
        self._agent = agent

    def set_speak(self, fn: Callable[[str, str], None]) -> None:
        """fn(text, language) — called when ambient has something to say."""
        self._speak_fn = fn

    def notify_user_active(self) -> None:
        """Call this whenever the user gives Nyra a command."""
        self._last_user_activity = datetime.now()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[Ambient] Copilot active.")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while True:
            time.sleep(CHECK_INTERVAL)
            if not self._enabled:
                continue
            try:
                self._check()
            except Exception as exc:
                print(f"[Ambient] Error: {exc}")

    def _check(self) -> None:
        window = _get_active_window()
        now = datetime.now()

        # Track window change
        if window != self._last_window:
            self._last_window = window
            self._window_since = now
            return  # just changed — give user time to settle

        # Don't interrupt meetings or games
        wl = window.lower()
        if any(k in wl for k in _MEETING_KEYWORDS | _GAME_FULLSCREEN_HINTS):
            return

        # Respect cooldown
        if self._last_intervention:
            if (now - self._last_intervention).total_seconds() < COOLDOWN_MINUTES * 60:
                return

        # Respect recent user activity
        if self._last_user_activity:
            if (now - self._last_user_activity).total_seconds() < MIN_SINCE_USER * 60:
                return

        # Check stuck threshold
        if self._window_since is None:
            return
        stuck_seconds = (now - self._window_since).total_seconds()
        if stuck_seconds < STUCK_MINUTES * 60:
            return

        # User has been on the same window for a while — analyze and offer help
        self._intervene(window, stuck_seconds)

    def _intervene(self, window: str, stuck_seconds: float) -> None:
        if self._agent is None or self._speak_fn is None:
            return

        minutes = int(stuck_seconds // 60)

        try:
            import vision as _vision
            context = _vision.describe_screen(
                "The user has been on this screen for a while. "
                "Briefly identify: what they are doing, if they seem stuck, "
                "and what the most useful thing to offer would be. "
                "Be concise — 1-2 sentences max."
            )
        except Exception:
            context = f"Active window: {window}"

        prompt = (
            f"You are Nyra, proactively checking in. "
            f"The user has been on '{window[:60]}' for {minutes} minutes without speaking to you. "
            f"Screen context: {context}. "
            f"Offer one short, useful observation or offer of help. "
            f"Keep it to 1 sentence. Cinematic tone. Don't ask multiple questions."
        )

        try:
            response = self._agent.respond(prompt, language="en", session_app=window[:40])
            if response:
                self._last_intervention = datetime.now()
                self._speak_fn(response, "en")
                print(f"[Ambient] Intervened after {minutes}m on '{window[:40]}'")
        except Exception as exc:
            print(f"[Ambient] Intervention failed: {exc}")


# ── Window helper (same as life_log, no extra deps) ───────────────────────────

def _get_active_window() -> str:
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if not length:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""
