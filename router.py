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
    "brave": "brave",
    "brave browser": "brave",
    "firefox": "firefox",
    "mozilla firefox": "firefox",
    "edge": "edge",
    "microsoft edge": "edge",
    "notepad": "notepad",
    "explorer": "explorer",
    "file explorer": "explorer",
    "task manager": "task manager",
    "görev yöneticisi": "task manager",
    "dosya gezgini": "explorer",
    "valorant": "valorant",
    "vlc": "vlc",
    "vlc media player": "vlc",
    "obs": "obs",
    "obs studio": "obs",
    "calculator": "calculator",
    "hesap makinesi": "calculator",
    # Turkish aliases
    "not defteri": "notepad",
    "krom": "chrome",
    "valo": "valorant",
    "hesap": "calculator",
    "medya": "vlc",
    "tarayıcı": "chrome",
    "tarayici": "chrome",
}

_WEB: dict[str, str] = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "maps": "https://www.google.com/maps",
    "harita": "https://www.google.com/maps",
    "reddit": "https://www.reddit.com",
    "github": "https://www.github.com",
    "twitter": "https://www.twitter.com",
    "x": "https://www.x.com",
    "instagram": "https://www.instagram.com",
    "netflix": "https://www.netflix.com",
    "twitch": "https://www.twitch.tv",
    "discord web": "https://discord.com/channels/@me",
    "spotify web": "https://open.spotify.com",
    "chatgpt": "https://chat.openai.com",
    "wikipedia": "https://www.wikipedia.org",
    "amazon": "https://www.amazon.com",
    "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com",
    "calendar": "https://calendar.google.com",
    "takvim": "https://calendar.google.com",
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
_SYS_SLEEP = re.compile(r"\b(uyku\s*moduna\s*ge[cç]|ekran[ı]?\s*(kapat|söndür|söndür)|sleep\s*(mode)?|monitor\s*off|display\s*off|turn\s*off\s*(the\s*)?(?:monitor|display|screen))\b", re.I)
_SYS_WAKE  = re.compile(r"\b(uyku\s*modundan\s*[cç][ıi][kx]|ekran[ıi]?\s*(aç|uyandır|uyandır)|wake\s*(up|display|screen|monitor)?|display\s*on|monitor\s*on|turn\s*on\s*(the\s*)?(?:monitor|display|screen))\b", re.I)

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

# World news / conflict briefing
_WORLD_NEWS = re.compile(
    r"\b(what(?:'s| is) going on|world today|news today|any news|conflict today"
    r"|world news|global news|what happened today|brief me|brief me"
    r"|dünyada ne var|dünyada neler oluyor|dünya haberleri|günün haberleri"
    r"|ne oldu bugün|haber var mı|çatışma haberleri)\b",
    re.I,
)

# Install / download
_EN_INSTALL = re.compile(r"^(?:install|download|get|setup)\s+(.+)$", re.I)
_TR_INSTALL = re.compile(
    r"^(.+?)\s+(?:indir|kur|yükle|yukle|indir\s+ve\s+aç|kur\s+ve\s+aç)$", re.I
)

# Steam update
_STEAM_UPDATE = re.compile(
    r"\b(steam.*(?:güncelle|güncelleme|update|yenile|oyunlar)"
    r"|(?:tüm\s+)?oyunlar.*(?:güncelle|update)"
    r"|update.*(?:steam|game)s?)\b",
    re.I,
)

# Deep research (→ night_agent background task)
_EN_RESEARCH = re.compile(
    r"^(?:research|investigate|study|deep\s+dive|find\s+out\s+about|analyze|tell\s+me\s+everything\s+about)\s+(.+)$", re.I
)
_TR_RESEARCH = re.compile(
    r"^(.+?)\s+(?:araştır|incele|analiz\s*et|hakkında\s+araştır|öğren|rapor\s+yaz)$", re.I
)

# Price check (→ night_agent background task)
_EN_PRICE = re.compile(
    r"(?:how much (?:is|does|costs?|cost)|(?:check|find|what(?:'s| is) the?) (?:price|cost|prices?) (?:of|for)|price check)\s+(.+)$",
    re.I,
)
_TR_PRICE = re.compile(
    r"^(.+?)\s+(?:fiyatını\s+(?:bul|ara|kontrol\s+et)|ne\s+kadar|kaç\s+(?:lira|euro|dolar|tl))$", re.I
)

# Browser-only navigation — just "google X" opens browser; actual search goes to LLM
_EN_GOOGLE = re.compile(r"^(?:google|open google for)\s+(.+)$", re.I)
_TR_GOOGLE = re.compile(r"^(.+?)\s+(?:googlela|googla|google'la)$", re.I)
_EN_YOUTUBE = re.compile(
    r"^(?:play(?:\s+on\s+youtube)?|youtube|watch)\s+(.+)$", re.I
)
_TR_YOUTUBE = re.compile(
    r"^(?:youtube'da\s+|youtubeda\s+|)(.+?)\s+(?:oynat|aç|izle|youtube)$", re.I
)

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

# ── System-access patterns ────────────────────────────────────────────────────

# System info (CPU / RAM / disk / battery)
_SYS_INFO = re.compile(
    r"\b(system info|sistem durumu|bilgisayar durumu|cpu kullanım[ıi]|ram kullanım[ıi]"
    r"|disk durumu|battery|pil durumu|kaç ram|cpu kaç|sistem istatistik|"
    r"cpu usage|ram usage|disk usage|memory usage|system stats|performance)\b",
    re.I,
)

# Screenshot
_SCREENSHOT = re.compile(
    r"\b(take (?:a )?screenshot|screenshot|ekran görüntüsü al|ekranı yakala"
    r"|ekran görüntüsü|screenshot al|snap screen)\b",
    re.I,
)

# Clipboard read
_CLIP_READ = re.compile(
    r"\b(show clipboard|read clipboard|what(?:'s| is) in (?:my )?clipboard"
    r"|panoyu göster|panoda ne var|clipboard(?:'ı| içeriği| ne)?)\b",
    re.I,
)

# Type text at cursor: "type hello world"
_TYPE_TEXT = re.compile(r"^(?:type|yaz(?:ı yaz)?)\s+(.+)$", re.I)

# Send a hotkey combo: "press win+d", "show desktop"
_SHOW_DESKTOP = re.compile(
    r"\b(show desktop|masaüstünü göster|hepsini küçült|tüm pencereleri küçült"
    r"|press win\+?d|win d)\b",
    re.I,
)
_HOTKEY_EN = re.compile(
    r"^(?:press|hold|hit)\s+(.+)$", re.I
)

# Process list
_PROC_LIST = re.compile(
    r"\b(what(?:'s| is) running|running processes|process list|show processes"
    r"|neler çalışıyor|çalışan programlar|çalışan uygulamalar|işlem listesi)\b",
    re.I,
)

# Network / IP info
_NET_INFO = re.compile(
    r"\b(network info|wifi (?:status|info|durumu)|internet (?:status|durumu|bilgisi)"
    r"|(?:my )?ip (?:address)?|ip adresim|ağ bilgisi|bağlantı durumu)\b",
    re.I,
)

# Screen brightness: "brightness 60", "parlaklık 50"
_BRIGHT = re.compile(
    r"(?:(?:set\s+)?brightness|parlaklı[kğ](?:[ıi])?)\s*(?:to\s*)?(\d{1,3})\s*%?",
    re.I,
)

# Open user folder: "open documents", "belgeler klasörünü aç"
_OPEN_FOLDER_EN = re.compile(
    r"^open (?:my\s+)?(?:the\s+)?(documents|desktop|downloads|pictures|music|videos)\s*(?:folder)?$",
    re.I,
)
_OPEN_FOLDER_TR = re.compile(
    r"^(belgeler|masaüstü|masaustu|indirmeler|resimler|müzik|muzik|videolar)"
    r"\s*(?:klasörünü|dizinini|aç|klasörü|klasöründe)?$",
    re.I,
)

# Find file: "find file report.pdf", "dosya bul rapor"
_FIND_FILE_EN = re.compile(r"^(?:find|locate|where is|search for file)\s+(?:file\s+)?(.+)$", re.I)
_FIND_FILE_TR = re.compile(r"^(?:dosya bul|bul dosya|dosyayı bul)\s+(.+)$", re.I)


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

    # Deep research → background task
    m = _EN_RESEARCH.match(lower)
    if m:
        return RouteResult(True, "research_task", {"description": text})
    m = _TR_RESEARCH.match(lower)
    if m:
        return RouteResult(True, "research_task", {"description": text})

    # Price check → background task
    m = _EN_PRICE.search(lower)
    if m:
        return RouteResult(True, "price_task", {"description": text})
    m = _TR_PRICE.match(lower)
    if m:
        return RouteResult(True, "price_task", {"description": text})

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

    # Full-access direct routes
    if _SYS_INFO.search(lower):
        return RouteResult(True, "system_info")

    if _SCREENSHOT.search(lower):
        return RouteResult(True, "take_screenshot")

    if _CLIP_READ.search(lower):
        return RouteResult(True, "clipboard_read")

    m = _TYPE_TEXT.match(lower)
    if m:
        return RouteResult(True, "type_text", {"text": text[m.start(1):].strip()})

    if _SHOW_DESKTOP.search(lower):
        return RouteResult(True, "send_hotkey", {"combo": "win+d"})

    m = _HOTKEY_EN.match(lower)
    if m:
        return RouteResult(True, "send_hotkey", {"combo": m.group(1).strip()})

    if _PROC_LIST.search(lower):
        return RouteResult(True, "process_list")

    if _NET_INFO.search(lower):
        return RouteResult(True, "network_info")

    m = _BRIGHT.search(lower)
    if m:
        return RouteResult(True, "brightness_set", {"level": int(m.group(1))})

    m = _OPEN_FOLDER_EN.match(lower)
    if m:
        return RouteResult(True, "open_folder", {"name": m.group(1).lower()})

    m = _OPEN_FOLDER_TR.match(lower)
    if m:
        return RouteResult(True, "open_folder", {"name": m.group(1).lower()})

    m = _FIND_FILE_EN.match(lower)
    if m:
        return RouteResult(True, "file_find", {"name": m.group(1).strip()})

    m = _FIND_FILE_TR.match(lower)
    if m:
        return RouteResult(True, "file_find", {"name": m.group(1).strip()})

    # System
    if _SYS_WAKE.search(lower):
        return RouteResult(True, "system_wake")
    if _SYS_LOCK.search(lower):
        return RouteResult(True, "system_lock")
    if _SYS_SLEEP.search(lower):
        return RouteResult(True, "system_sleep")

    # World news briefing
    if _WORLD_NEWS.search(lower):
        return RouteResult(True, "world_news")

    # Steam update — before generic open/install patterns
    if _STEAM_UPDATE.search(lower):
        return RouteResult(True, "steam_update")

    # Browser-only Google nav — "google X" opens browser; "search X" goes to LLM
    m = _EN_GOOGLE.match(lower)
    if m:
        query = m.group(1).strip().replace(" ", "+")
        return RouteResult(True, "open_web", {"url": f"https://www.google.com/search?q={query}"})

    m = _TR_GOOGLE.match(lower)
    if m:
        query = m.group(1).strip().replace(" ", "+")
        return RouteResult(True, "open_web", {"url": f"https://www.google.com/search?q={query}"})

    # YouTube
    m = _EN_YOUTUBE.match(lower)
    if m:
        query = m.group(1).strip().replace(" ", "+")
        return RouteResult(True, "open_web", {"url": f"https://www.youtube.com/results?search_query={query}"})

    m = _TR_YOUTUBE.match(lower)
    if m:
        query = m.group(1).strip().replace(" ", "+")
        return RouteResult(True, "open_web", {"url": f"https://www.youtube.com/results?search_query={query}"})

    # English open/launch
    m = _EN_OPEN.match(lower)
    if m:
        target = m.group(1).strip()
        result = _resolve_target(target)
        if result:
            return result

    # English install/download
    m = _EN_INSTALL.match(lower)
    if m:
        target = m.group(1).strip()
        app = _APPS.get(target) or _fuzzy_app(target) or target
        return RouteResult(True, "install_app", {"app": app})

    # English close/quit app — always route, close_app handles unknown names via .exe fallback
    m = _EN_CLOSE.match(lower)
    if m:
        target = m.group(1).strip()
        app = _APPS.get(target) or _fuzzy_app(target) or target
        return RouteResult(True, "close_app", {"app": app})

    # Turkish "app ac" style
    m = _TR_OPEN.match(lower)
    if m:
        target = m.group(1).strip()
        result = _resolve_target(target)
        if result:
            return result

    # Turkish "app indir/kur" style
    m = _TR_INSTALL.match(lower)
    if m:
        target = m.group(1).strip()
        app = _APPS.get(target) or _fuzzy_app(target) or target
        return RouteResult(True, "install_app", {"app": app})

    # Turkish "app kapat" style — always route
    m = _TR_CLOSE.match(lower)
    if m:
        target = m.group(1).strip()
        app = _APPS.get(target) or _fuzzy_app(target) or target
        return RouteResult(True, "close_app", {"app": app})

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
        elif stripped.startswith("ACTION:CLOSE:"):
            app = stripped[len("ACTION:CLOSE:"):].strip().lower()
            actions.append(RouteResult(True, "close_app", {"app": app}))
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
