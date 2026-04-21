from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass(slots=True)
class RouteResult:
    matched: bool
    action: str = ""        # "launch_app" | "open_web" | "remember" | "stop"
                            # | "resume" | "show_recent" | "switch_mode"
    params: dict = field(default_factory=dict)
    response: str = ""      # pre-built response for simple deterministic actions


# ── App aliases ──────────────────────────────────────────────────────────────

_APPS: dict[str, str] = {
    "steam": "steam",
    "vscode": "vscode",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "code": "vscode",
    "discord": "discord",
    "spotify": "spotify",
    "chrome": "chrome",
    "google chrome": "chrome",
    "notepad": "notepad",
    "explorer": "explorer",
    "file explorer": "explorer",
    "task manager": "task manager",
    "görev yöneticisi": "task manager",
    "dosya gezgini": "explorer",
    "valorant": "valorant",
}

_WEB: dict[str, str] = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "maps": "https://www.google.com/maps",
    "harita": "https://www.google.com/maps",
}

_MODES = {"coding", "research", "focus", "ideas", "conversation"}

# ── Patterns ─────────────────────────────────────────────────────────────────

# English: "open X", "launch X", "run X", "start X"
_EN_OPEN = re.compile(r"^(?:open|launch|run|start)\s+(.+)$", re.I)

# English: "close X", "quit X", "kill X", "exit X"
_EN_CLOSE = re.compile(r"^(?:close|quit|kill|exit)\s+(.+)$", re.I)

# Turkish: "X ac", "X aç", "X baslat", "X başlat", "X calistir", "X çalıştır"
_TR_OPEN = re.compile(r"^(.+?)\s+(?:ac|aç|baslat|başlat|calistir|çalıştır)$", re.I)

# Turkish: "X kapat", "X kapa", "X çık", "X çıkar"
_TR_CLOSE = re.compile(r"^(.+?)\s+(?:kapat|kapa|çık|cik|cikart|çıkar)$", re.I)

# Mode switch: "switch to X mode", "X mode", "X moduna geç"
_MODE_SWITCH = re.compile(
    r"(?:switch to |switch |)(\w+)\s+mode$"
    r"|(\w+)\s+mod(?:una\s+ge[cç]|a\s+ge[cç]|u)$",
    re.I,
)

# Remember
_REMEMBER = re.compile(r"^(?:remember|hatırla|bunu hatırla|hatirla)\s+(.+)$", re.I)

# Volume
_VOL_UP   = re.compile(r"\b(ses[i]?\s*(aç|yükselt|artır|kaldır)|volume\s*up|louder|turn\s*up)\b", re.I)
_VOL_DOWN = re.compile(r"\b(ses[i]?\s*(kıs|azalt|indir|düşür)|volume\s*down|quieter|turn\s*down)\b", re.I)
_VOL_MUTE = re.compile(r"\b(ses[i]?\s*(kapat|sustur|kes|mute)|^mute$|sessize\s*al)\b", re.I)

# Media
_MEDIA_PLAY  = re.compile(r"\b(müzik[i]?\s*(çal|başlat|aç|devam)|play\s*(music)?|resume\s*music|çalmaya\s*devam)\b", re.I)
_MEDIA_PAUSE = re.compile(r"\b(müzik[i]?\s*(durdur|pause|beklet)|pause\s*music|müziği\s*durdur)\b", re.I)
_MEDIA_NEXT  = re.compile(r"\b(sonraki\s*(şarkı|parça|track)?|next\s*(track|song)?|atla|ileri\s*al)\b", re.I)
_MEDIA_PREV  = re.compile(r"\b(önceki\s*(şarkı|parça|track)?|previous\s*(track|song)?|geri\s*al)\b", re.I)

# Window / standalone close
_WIN_CLOSE = re.compile(
    r"^(?:close|kapat|kapa)$"
    r"|\b(pencere[yi]?\s*kapat|close\s*window|kapat\s*pencere[yi]?)\b",
    re.I,
)
_WIN_MIN   = re.compile(r"\b(pencere[yi]?\s*(küçült|minimize)|minimize\s*window)\b", re.I)
_WIN_MAX   = re.compile(r"\b(pencere[yi]?\s*(büyüt|maximize|tam\s*ekran\s*yap)|maximize\s*window)\b", re.I)

# System
_SYS_LOCK  = re.compile(r"\b(ekran[ı]?\s*kilitle|kilitle|lock\s*(screen)?|lock\s*computer)\b", re.I)
_SYS_SLEEP = re.compile(r"\b(uyku\s*(moduna\s*geç|modu)?|sleep\s*(mode)?|hibernate)\b", re.I)

_STOP_WORDS = {
    "stop", "pause", "dur", "sus", "stop talking", "stop speaking",
    "konuşmayı durdur", "konusmayi durdur",
}

_RESUME_PHRASES = {
    "continue my work", "continue last task",
    "resume", "kaldığım yerden", "kaldigim yerden", "devam et", "continue",
}

_RECENT_PHRASES = {
    "show recent files", "recent files", "son dosyalar",
    "son dosyaları göster", "son dosyalari goster",
}

# Night agent — background/overnight tasks
_NIGHT_EN = re.compile(
    r"\b(while i sleep|overnight|by morning|when i wake up|in the background"
    r"|background task|while i'm sleeping|do it tonight)\b", re.I
)
_NIGHT_TR = re.compile(
    r"\b(uyurken|sabaha kadar|arka planda|uyuduğumda|uyuduğum|sabah hazır"
    r"|gece yap|ben uyurken|arka plan)\b", re.I
)

# Life log queries — temporal / history questions
_LOG_EN = re.compile(
    r"\b(what was i doing|what did i do|show my history|what happened"
    r"|when did i|what were you doing|show me what|my activity)\b", re.I
)
_LOG_TR = re.compile(
    r"\b(ne yapıyordum|ne yapiyordum|geçmişimi göster|gecmisimi goster"
    r"|ne yaptım|ne yaptim|aktivitem|geçmişim|gecmisim|ne oldu)\b", re.I
)

# Night agent status
_NIGHT_STATUS = re.compile(
    r"\b(task status|görev durumu|what are you working on|ne üstünde çalışıyorsun"
    r"|overnight results|gece sonuçları|tasks done|görevler bitti mi)\b", re.I
)


# ── Main entry point ─────────────────────────────────────────────────────────

def route(text: str) -> RouteResult:
    lower = text.lower().strip()
    if not lower:
        return RouteResult(False)

    # Stop / pause — highest priority
    if lower in _STOP_WORDS or any(lower.startswith(p + " ") for p in ("stop", "dur")):
        return RouteResult(True, "stop")

    # Night agent — background tasks
    if _NIGHT_EN.search(lower) or _NIGHT_TR.search(lower):
        return RouteResult(True, "night_task", {"description": text})

    # Night agent status
    if _NIGHT_STATUS.search(lower):
        return RouteResult(True, "night_status")

    # Life log temporal query
    if _LOG_EN.search(lower) or _LOG_TR.search(lower):
        return RouteResult(True, "log_query", {"question": text})

    # Remember
    m = _REMEMBER.match(lower)
    if m:
        payload = m.group(1).strip()
        return RouteResult(True, "remember", {"text": payload}, "Remembered.")

    # Resume / what was i doing
    if lower in _RESUME_PHRASES or any(phrase in lower for phrase in _RESUME_PHRASES):
        return RouteResult(True, "resume")

    # Recent files
    if lower in _RECENT_PHRASES:
        return RouteResult(True, "show_recent")

    # Mode switch
    m = _MODE_SWITCH.search(lower)
    if m:
        mode = (m.group(1) or m.group(2) or "").lower().strip()
        if mode in _MODES:
            return RouteResult(True, "switch_mode", {"mode": mode}, f"Switching to {mode} mode.")

    # Volume
    if _VOL_UP.search(lower):
        return RouteResult(True, "volume_up")
    if _VOL_DOWN.search(lower):
        return RouteResult(True, "volume_down")
    if _VOL_MUTE.search(lower):
        return RouteResult(True, "volume_mute")

    # Media
    if _MEDIA_PAUSE.search(lower):
        return RouteResult(True, "media_pause")
    if _MEDIA_PLAY.search(lower):
        return RouteResult(True, "media_play")
    if _MEDIA_NEXT.search(lower):
        return RouteResult(True, "media_next")
    if _MEDIA_PREV.search(lower):
        return RouteResult(True, "media_prev")

    # Window
    if _WIN_CLOSE.search(lower):
        return RouteResult(True, "window_close")
    if _WIN_MIN.search(lower):
        return RouteResult(True, "window_minimize")
    if _WIN_MAX.search(lower):
        return RouteResult(True, "window_maximize")

    # System
    if _SYS_LOCK.search(lower):
        return RouteResult(True, "system_lock")
    if _SYS_SLEEP.search(lower):
        return RouteResult(True, "system_sleep")

    # English open/launch
    m = _EN_OPEN.match(lower)
    if m:
        target = m.group(1).strip()
        result = _resolve_target(target)
        if result:
            return result

    # English close/quit app
    m = _EN_CLOSE.match(lower)
    if m:
        target = m.group(1).strip()
        if target in _APPS or _fuzzy_app(target):
            app = _APPS.get(target) or _fuzzy_app(target)
            return RouteResult(True, "close_app", {"app": app}, f"Closing {app}.")

    # Turkish "app ac" style
    m = _TR_OPEN.match(lower)
    if m:
        target = m.group(1).strip()
        result = _resolve_target(target)
        if result:
            return result

    # Turkish "app kapat" style
    m = _TR_CLOSE.match(lower)
    if m:
        target = m.group(1).strip()
        if target in _APPS or _fuzzy_app(target):
            app = _APPS.get(target) or _fuzzy_app(target)
            return RouteResult(True, "close_app", {"app": app}, f"{app} kapatılıyor.")

    return RouteResult(False)


def parse_llm_actions(response: str) -> tuple[str, list[RouteResult]]:
    """Extract ACTION: lines from an LLM response.
    Returns (clean_text, [actions]) where clean_text has ACTION: lines removed."""
    lines = response.splitlines()
    clean: list[str] = []
    actions: list[RouteResult] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ACTION:OPEN:"):
            app = stripped[len("ACTION:OPEN:"):].strip().lower()
            actions.append(RouteResult(True, "launch_app", {"app": app}))
        elif stripped.startswith("ACTION:WEB:"):
            url = stripped[len("ACTION:WEB:"):].strip()
            actions.append(RouteResult(True, "open_web", {"url": url}))
        elif stripped.startswith("ACTION:REMEMBER:"):
            text = stripped[len("ACTION:REMEMBER:"):].strip()
            actions.append(RouteResult(True, "remember", {"text": text}))
        else:
            clean.append(line)

    return "\n".join(clean).strip(), actions


# ── Internals ────────────────────────────────────────────────────────────────

def _fuzzy_app(target: str) -> str:
    best_ratio, best_key = 0.0, ""
    for alias in _APPS:
        ratio = SequenceMatcher(None, target, alias).ratio()
        if ratio > best_ratio:
            best_ratio, best_key = ratio, alias
    return _APPS[best_key] if best_ratio >= 0.78 else ""


def _resolve_target(target: str) -> RouteResult | None:
    # Web alias?
    for keyword, url in _WEB.items():
        if keyword in target:
            return RouteResult(True, "open_web", {"url": url}, f"Opening {keyword}.")

    # Exact app match?
    if target in _APPS:
        app = _APPS[target]
        return RouteResult(True, "launch_app", {"app": app}, f"Launching {app}.")

    # Fuzzy app match
    app = _fuzzy_app(target)
    if app:
        return RouteResult(True, "launch_app", {"app": app}, f"Launching {app}.")

    return None
