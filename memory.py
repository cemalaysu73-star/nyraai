from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config import APP_CONFIG
from llm import ConversationHistory


@dataclass
class UserProfile:
    name: str = ""
    preferred_language: str = "en"
    notes: list[str] = field(default_factory=list)


@dataclass
class SessionState:
    current_goal: str = ""
    current_app: str = ""
    language: str = "en"
    mode: str = "conversation"


class Memory:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or APP_CONFIG.memory_file
        self.profile = UserProfile()
        self.session = SessionState()
        self.history = ConversationHistory()
        self._lock = threading.Lock()
        self._last_save = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if "profile" in raw:
                p = raw["profile"]
                self.profile = UserProfile(
                    name=p.get("name", ""),
                    preferred_language=p.get("preferred_language", "en"),
                    notes=p.get("notes", []),
                )
            if "session" in raw:
                s = raw["session"]
                self.session = SessionState(
                    current_goal=s.get("current_goal", ""),
                    current_app=s.get("current_app", ""),
                    language=s.get("language", "en"),
                    mode=s.get("mode", "conversation"),
                )
            if "history" in raw:
                self.history.from_json(raw["history"])
        except Exception:
            pass

    def save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_save < 30:
            return
        self._last_save = now
        self._write()

    def add_turn(self, role: str, content: str) -> None:
        self.history.add(role, content)
        self.save()

    def remember(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            if text not in self.profile.notes:
                self.profile.notes.append(text)
                if len(self.profile.notes) > 60:
                    self.profile.notes = self.profile.notes[-60:]
        self.save(force=True)

    def resume_summary(self, language: str = "en") -> str:
        summary = self.history.last_turns_summary()
        if language == "tr":
            return f"Son etkileşimleriniz:\n{summary}"
        return f"Recent activity:\n{summary}"

    def set_language(self, lang: str) -> None:
        if lang not in ("en", "tr"):
            return
        self.session.language = lang
        if lang != self.profile.preferred_language:
            self.profile.preferred_language = lang
            self.save()

    def set_app(self, app_name: str) -> None:
        self.session.current_app = app_name

    # ── Internals ─────────────────────────────────────────────────────────────

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "profile": asdict(self.profile),
            "session": asdict(self.session),
            "history": self.history.to_json(),
        }
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception:
            pass
