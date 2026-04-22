from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import urllib.request
import webbrowser
from pathlib import Path

import psutil
import win32api
import win32con
import win32gui

_CONFLICTLY_URL = "https://www.conflictly.app"

_LAUNCH_EN = [
    "{app} is up.", "{app} opened.", "Launched {app}.", "{app}, running.",
    "On it — {app} launching.", "{app} is loading.", "Done. {app} is up.",
]
_LAUNCH_TR = [
    "{app} açıldı.", "{app} hazır.", "{app} başlatıldı.", "{app} yükleniyor.",
    "Tamam — {app} açılıyor.", "{app} açıldı, efendim.",
]
_OPEN_EN = [
    "Opening {label}.", "{label}, loading.", "On it.", "{label} coming up.", "Done.",
]
_OPEN_TR = [
    "{label} açılıyor.", "Tamam.", "{label} yükleniyor.", "Hazır.", "Açıldı.",
]


def _pick(variants: list[str], key: str) -> str:
    idx = int(hashlib.md5(key.encode()).hexdigest(), 16) % len(variants)
    return variants[idx]

# ── Desktop target resolution (lazy, cached) ─────────────────────────────────

_targets: dict[str, list[str]] | None = None


def _desktop_targets() -> dict[str, list[str]]:
    global _targets
    if _targets is not None:
        return _targets

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    lad = os.environ.get("LOCALAPPDATA", "")

    def resolve(candidates: list[str], extra: list[str] | None = None) -> list[str]:
        for path in candidates:
            if Path(path).is_file():
                return [path] + (extra or [])
            if shutil.which(path):
                return [path] + (extra or [])
        return [candidates[-1]] + (extra or [])

    discord_extra = ["--processStart", "Discord.exe"] if lad else []

    _targets = {
        "steam": resolve([
            rf"{pf86}\Steam\steam.exe",
            rf"{pf}\Steam\steam.exe",
            "steam.exe",
        ]),
        "vscode": resolve([
            rf"{lad}\Programs\Microsoft VS Code\Code.exe",
            rf"{pf}\Microsoft VS Code\Code.exe",
            "Code.exe",
        ]),
        "discord": resolve([
            rf"{lad}\Discord\Update.exe",
            "Discord.exe",
        ], discord_extra),
        "spotify": resolve([
            rf"{lad}\Spotify\Spotify.exe",
            "Spotify.exe",
        ]),
        "chrome": resolve([
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            "chrome.exe",
        ]),
        "brave": resolve([
            rf"{lad}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe",
            "brave.exe",
        ]),
        "firefox": resolve([
            rf"{pf}\Mozilla Firefox\firefox.exe",
            rf"{pf86}\Mozilla Firefox\firefox.exe",
            "firefox.exe",
        ]),
        "edge": resolve([
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
            rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
            "msedge.exe",
        ]),
        "valorant": resolve([
            rf"{pf}\Riot Games\VALORANT\live\VALORANT.exe",
            "VALORANT.exe",
        ]),
        "vlc": resolve([
            rf"{pf}\VideoLAN\VLC\vlc.exe",
            rf"{pf86}\VideoLAN\VLC\vlc.exe",
            "vlc.exe",
        ]),
        "obs": resolve([
            rf"{pf}\obs-studio\bin\64bit\obs64.exe",
            rf"{pf86}\obs-studio\bin\64bit\obs64.exe",
            "obs64.exe",
            "obs.exe",
        ]),
        "calculator": ["calc.exe"],
        "notepad": ["notepad.exe"],
        "explorer": ["explorer.exe"],
        "task manager": ["taskmgr.exe"],
        "paint": ["mspaint.exe"],
        "word": resolve([
            rf"{pf}\Microsoft Office\root\Office16\WINWORD.EXE",
            rf"{pf86}\Microsoft Office\root\Office16\WINWORD.EXE",
            "WINWORD.EXE",
        ]),
        "excel": resolve([
            rf"{pf}\Microsoft Office\root\Office16\EXCEL.EXE",
            rf"{pf86}\Microsoft Office\root\Office16\EXCEL.EXE",
            "EXCEL.EXE",
        ]),
    }
    return _targets


# ── Public actions ────────────────────────────────────────────────────────────

def launch_app(app_key: str, language: str = "en") -> str:
    targets = _desktop_targets()
    command = targets.get(app_key.lower())
    if not command:
        return (
            f"I don't have {app_key} configured, sir."
            if language == "en"
            else f"{app_key} yapılandırılmamış."
        )

    exe, args = command[0], command[1:]

    try:
        if Path(exe).is_file():
            subprocess.Popen([exe] + args, shell=False)
        elif not args:
            os.startfile(exe)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(" ".join([exe] + args), shell=True)

        name = app_key.title()
        if language == "en":
            return _pick(_LAUNCH_EN, app_key).format(app=name)
        return _pick(_LAUNCH_TR, app_key).format(app=name)
    except OSError:
        return (
            f"Found {app_key}, but Windows couldn't start it, sir."
            if language == "en"
            else f"{app_key} bulunamadı veya başlatılamadı."
        )


def open_web(url: str, language: str = "en") -> str:
    try:
        webbrowser.open(url)
        label = url.split("//")[-1].split("/")[0].replace("www.", "")
        if language == "en":
            return _pick(_OPEN_EN, url).format(label=label.title())
        return _pick(_OPEN_TR, url).format(label=label)
    except Exception:
        return "Couldn't open that." if language == "en" else "Açılamadı."


def recent_files(language: str = "en") -> str:
    import glob
    import time as _time

    home = Path.home()
    candidates: list[tuple[float, str]] = []

    patterns = [
        str(home / "Documents" / "**" / "*.py"),
        str(home / "Documents" / "**" / "*.txt"),
        str(home / "Desktop" / "**" / "*.*"),
    ]
    for pattern in patterns:
        for f in glob.glob(pattern, recursive=True)[:30]:
            try:
                mtime = os.path.getmtime(f)
                candidates.append((mtime, f))
            except OSError:
                pass

    candidates.sort(reverse=True)
    top = [Path(p).name for _, p in candidates[:5]]

    if not top:
        return "No recent files found." if language == "en" else "Son dosya bulunamadı."

    header = "Recent files:" if language == "en" else "Son dosyalar:"
    return header + "\n" + "\n".join(f"· {name}" for name in top)


# ── Volume control ────────────────────────────────────────────────────────────

def _get_volume_control():
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def volume_set(level: float, language: str = "en") -> str:
    """level: 0.0–1.0"""
    vc = _get_volume_control()
    if vc is None:
        return "Volume control unavailable." if language == "en" else "Ses kontrolü kullanılamıyor."
    try:
        vc.SetMasterVolumeLevelScalar(max(0.0, min(1.0, level)), None)
        pct = int(level * 100)
        return f"Volume set to {pct}%." if language == "en" else f"Ses %{pct} olarak ayarlandı."
    except Exception:
        return "Could not change volume." if language == "en" else "Ses ayarlanamadı."


def volume_up(language: str = "en") -> str:
    vc = _get_volume_control()
    if vc is None:
        return volume_set(0.7, language)
    try:
        current = vc.GetMasterVolumeLevelScalar()
        return volume_set(min(1.0, current + 0.1), language)
    except Exception:
        return "Could not change volume." if language == "en" else "Ses ayarlanamadı."


def volume_down(language: str = "en") -> str:
    vc = _get_volume_control()
    if vc is None:
        return volume_set(0.3, language)
    try:
        current = vc.GetMasterVolumeLevelScalar()
        return volume_set(max(0.0, current - 0.1), language)
    except Exception:
        return "Could not change volume." if language == "en" else "Ses ayarlanamadı."


def volume_mute(language: str = "en") -> str:
    vc = _get_volume_control()
    if vc is None:
        return "Volume control unavailable." if language == "en" else "Ses kontrolü kullanılamıyor."
    try:
        muted = vc.GetMute()
        vc.SetMute(0 if muted else 1, None)
        if muted:
            return "Unmuted." if language == "en" else "Ses açıldı."
        return "Muted." if language == "en" else "Ses kapatıldı."
    except Exception:
        return "Could not mute." if language == "en" else "Sessizleştirilemedi."


# ── Media control (Spotify / any player) ─────────────────────────────────────

_VK_PLAY_PAUSE = 0xB3
_VK_NEXT       = 0xB0
_VK_PREV       = 0xB1
_VK_STOP       = 0xB2


def _media_key(vk: int) -> None:
    win32api.keybd_event(vk, 0, 0, 0)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def media_play_pause(language: str = "en") -> str:
    _media_key(_VK_PLAY_PAUSE)
    return "Done." if language == "en" else "Tamam."


def media_next(language: str = "en") -> str:
    _media_key(_VK_NEXT)
    return "Next track." if language == "en" else "Sonraki parça."


def media_prev(language: str = "en") -> str:
    _media_key(_VK_PREV)
    return "Previous track." if language == "en" else "Önceki parça."


def media_stop(language: str = "en") -> str:
    _media_key(_VK_STOP)
    return "Stopped." if language == "en" else "Durduruldu."


# ── Window control ────────────────────────────────────────────────────────────

def window_close(language: str = "en") -> str:
    try:
        hwnd = win32gui.GetForegroundWindow()
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return "Window closed." if language == "en" else "Pencere kapatıldı."
    except Exception:
        return "Could not close window." if language == "en" else "Pencere kapatılamadı."


def window_minimize(language: str = "en") -> str:
    try:
        hwnd = win32gui.GetForegroundWindow()
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return "Minimized." if language == "en" else "Küçültüldü."
    except Exception:
        return "Could not minimize." if language == "en" else "Küçültülemedi."


def window_maximize(language: str = "en") -> str:
    try:
        hwnd = win32gui.GetForegroundWindow()
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return "Maximized." if language == "en" else "Büyütüldü."
    except Exception:
        return "Could not maximize." if language == "en" else "Büyütülemedi."


# ── App close ────────────────────────────────────────────────────────────────

_PROC_NAMES: dict[str, list[str]] = {
    "steam":    ["steam.exe"],
    "discord":  ["discord.exe"],
    "spotify":  ["spotify.exe"],
    "chrome":   ["chrome.exe"],
    "brave":    ["brave.exe"],
    "firefox":  ["firefox.exe"],
    "edge":     ["msedge.exe"],
    "vscode":   ["code.exe"],
    "valorant": ["valorant.exe", "valorant-win64-shipping.exe"],
    "vlc":      ["vlc.exe"],
    "obs":      ["obs64.exe", "obs.exe"],
    "notepad":  ["notepad.exe"],
    "calculator": ["calculatorapp.exe", "calc.exe"],
    "explorer": ["explorer.exe"],
}


def close_app(app_key: str, language: str = "en") -> str:
    names = _PROC_NAMES.get(app_key.lower(), [f"{app_key}.exe"])
    names_lower = {n.lower() for n in names}
    killed = False
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"].lower() in names_lower:
                proc.terminate()
                killed = True
        except Exception:
            pass
    if killed:
        return f"{app_key} closed." if language == "en" else f"{app_key} kapatıldı."
    return f"{app_key} is not running." if language == "en" else f"{app_key} çalışmıyor."


# ── System control ────────────────────────────────────────────────────────────

def system_lock(language: str = "en") -> str:
    subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
    return "Screen locked." if language == "en" else "Ekran kilitlendi."


def fetch_world_news(language: str = "en") -> tuple[str, str]:
    """Open conflictly.app in browser and return (brief_text, full_text) for TTS + display."""
    webbrowser.open(_CONFLICTLY_URL)

    try:
        req = urllib.request.Request(
            _CONFLICTLY_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Nyra/2.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        fallback = (
            "Conflictly is now open, sir. I couldn't fetch the summary — check the page directly."
            if language == "en"
            else "Conflictly açıldı, efendim. Özet alınamadı — sayfaya göz atabilirsiniz."
        )
        return fallback, fallback

    text = _extract_text(html)
    summary = _build_summary(text, language)
    return summary, summary


def _extract_text(html: str) -> str:
    """Strip tags and collapse whitespace."""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&[a-z]+;", " ", html)
    lines = [l.strip() for l in html.splitlines() if len(l.strip()) > 40]
    return " ".join(lines[:120])


def _build_summary(text: str, language: str) -> str:
    """Return a short spoken briefing from page text."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chosen = [s.strip() for s in sentences if len(s.strip()) > 50][:5]
    if not chosen:
        return (
            "Conflictly is open, sir. No readable content was found."
            if language == "en"
            else "Conflictly açıldı, efendim. Okunabilir içerik bulunamadı."
        )
    intro = (
        "Here's today's world briefing from Conflictly, sir. "
        if language == "en"
        else "İşte Conflictly'den günün dünya özeti, efendim. "
    )
    return intro + " ".join(chosen[:3])


def system_sleep(language: str = "en") -> str:
    import ctypes
    # Turn off monitor only — Nyra stays running so she can hear the wake command
    HWND_BROADCAST = 0xFFFF
    WM_SYSCOMMAND  = 0x0112
    SC_MONITORPOWER = 0xF170
    ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
    return "Display off." if language == "en" else "Ekran kapatıldı."


def system_wake(language: str = "en") -> str:
    import ctypes
    MOUSEEVENTF_MOVE = 0x0001
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
    return "Display on." if language == "en" else "Ekran açıldı."


# ── System info ───────────────────────────────────────────────────────────────

def system_info(language: str = "en") -> str:
    import psutil
    cpu = psutil.cpu_percent(interval=0.1)
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    bat = psutil.sensors_battery()

    ram_used = vm.used >> 30
    ram_total = vm.total >> 30
    disk_free = disk.free >> 30

    batt = ""
    if bat:
        plug = "⚡" if bat.power_plugged else ""
        batt = f" | Battery: {bat.percent:.0f}%{plug}" if language == "en" else f" | Pil: %{bat.percent:.0f}{plug}"

    if language == "en":
        return f"CPU {cpu}% | RAM {ram_used}/{ram_total} GB ({vm.percent}%) | Disk C: {disk_free} GB free{batt}"
    return f"CPU %{cpu} | RAM {ram_used}/{ram_total} GB (%{vm.percent}) | Disk C: {disk_free} GB boş{batt}"


# ── Screenshot ────────────────────────────────────────────────────────────────

def take_screenshot(language: str = "en") -> str:
    from datetime import datetime as _dt
    fname = Path.home() / "Pictures" / f"nyra_{_dt.now().strftime('%Y%m%d_%H%M%S')}.png"
    fname.parent.mkdir(exist_ok=True)
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.save(str(fname))
        os.startfile(str(fname.parent))
        return (f"Screenshot saved: {fname.name}" if language == "en"
                else f"Ekran görüntüsü kaydedildi: {fname.name}")
    except Exception:
        subprocess.Popen("snippingtool", shell=True)
        return "Snipping tool opened." if language == "en" else "Ekran görüntüsü aracı açıldı."


# ── Clipboard ─────────────────────────────────────────────────────────────────

def clipboard_read(language: str = "en") -> str:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        preview = (data or "").strip()[:500]
        if not preview:
            return "Clipboard is empty." if language == "en" else "Pano boş."
        return preview
    except Exception:
        return "Couldn't read clipboard." if language == "en" else "Pano okunamadı."


def clipboard_write(text: str, language: str = "en") -> str:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return "Copied." if language == "en" else "Kopyalandı."
    except Exception:
        return "Clipboard write failed." if language == "en" else "Panoya yazılamadı."


# ── Keyboard: type text + hotkeys ─────────────────────────────────────────────

def type_text(text: str, language: str = "en") -> str:
    """Type text at cursor using Win32 SendInput (Unicode-safe)."""
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD   = 1
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP   = 0x0002

    class _KI(ctypes.Structure):
        _fields_ = [
            ("wVk",         wintypes.WORD),
            ("wScan",       wintypes.WORD),
            ("dwFlags",     wintypes.DWORD),
            ("time",        wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_ulong),
        ]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", _KI)]
        _anonymous_ = ("_u",)
        _fields_ = [("type", wintypes.DWORD), ("_u", _U)]

    inputs = []
    for ch in text:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = _INPUT()
            inp.type = INPUT_KEYBOARD
            inp.ki.wVk = 0
            inp.ki.wScan = ord(ch)
            inp.ki.dwFlags = flags
            inputs.append(inp)

    arr = (_INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    return "Done." if language == "en" else "Tamam."


_HOTKEYS: dict[str, list[int]] = {
    "win+d":          [0x5B, 0x44],   # show desktop
    "win+l":          [0x5B, 0x4C],   # lock
    "win+e":          [0x5B, 0x45],   # file explorer
    "win+r":          [0x5B, 0x52],   # run dialog
    "alt+tab":        [0x12, 0x09],
    "alt+f4":         [0x12, 0x73],
    "ctrl+c":         [0x11, 0x43],
    "ctrl+v":         [0x11, 0x56],
    "ctrl+z":         [0x11, 0x5A],
    "ctrl+s":         [0x11, 0x53],
    "ctrl+shift+esc": [0x11, 0x10, 0x1B],
    "printscreen":    [0x2C],
}


def send_hotkey(combo: str, language: str = "en") -> str:
    keys = _HOTKEYS.get(combo.lower().replace(" ", ""))
    if not keys:
        return (f"Unknown shortcut: {combo}" if language == "en"
                else f"Bilinmeyen kısayol: {combo}")
    for vk in keys:
        win32api.keybd_event(vk, 0, 0, 0)
    for vk in reversed(keys):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    return "Done." if language == "en" else "Tamam."


# ── File operations ───────────────────────────────────────────────────────────

_USER_FOLDERS: dict[str, Path] = {
    "documents":  Path.home() / "Documents",
    "desktop":    Path.home() / "Desktop",
    "downloads":  Path.home() / "Downloads",
    "pictures":   Path.home() / "Pictures",
    "music":      Path.home() / "Music",
    "videos":     Path.home() / "Videos",
    # Turkish
    "belgeler":   Path.home() / "Documents",
    "masaüstü":   Path.home() / "Desktop",
    "masaustu":   Path.home() / "Desktop",
    "indirmeler": Path.home() / "Downloads",
    "resimler":   Path.home() / "Pictures",
    "müzik":      Path.home() / "Music",
    "muzik":      Path.home() / "Music",
    "videolar":   Path.home() / "Videos",
}


def open_folder(name_or_path: str, language: str = "en") -> str:
    key = name_or_path.lower().strip()
    path = _USER_FOLDERS.get(key) or Path(name_or_path).expanduser()
    try:
        os.startfile(str(path))
        return (f"Opened {path.name}." if language == "en"
                else f"{path.name} açıldı.")
    except Exception:
        return ("Couldn't open that location." if language == "en"
                else "Konum açılamadı.")


def file_find(name: str, language: str = "en") -> str:
    import glob
    home = str(Path.home())
    results: list[str] = []
    for pattern in [f"{home}/**/{name}", f"{home}/**/*{name}*"]:
        for f in glob.glob(pattern, recursive=True):
            if f not in results:
                results.append(f)
            if len(results) >= 8:
                break
        if len(results) >= 8:
            break
    if not results:
        return (f"No files matching '{name}'." if language == "en"
                else f"'{name}' ile eşleşen dosya yok.")
    header = f"Found {len(results)}:" if language == "en" else f"{len(results)} dosya:"
    lines = [f"· {Path(p).name}  →  {p}" for p in results[:5]]
    return header + "\n" + "\n".join(lines)


# ── Network info ──────────────────────────────────────────────────────────────

def network_info(language: str = "en") -> str:
    import socket
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        hostname = local_ip = "unknown"

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 53))
        s.close()
        status = "online" if language == "en" else "çevrimiçi"
    except Exception:
        status = "offline" if language == "en" else "çevrimdışı"

    if language == "en":
        return f"Host: {hostname} | IP: {local_ip} | Internet: {status}"
    return f"Bilgisayar: {hostname} | IP: {local_ip} | İnternet: {status}"


# ── Process list ──────────────────────────────────────────────────────────────

def process_list(language: str = "en") -> str:
    import psutil
    procs: list[tuple[int, str]] = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            mem = p.info["memory_info"].rss >> 20
            if mem > 10:
                procs.append((mem, p.info["name"] or ""))
        except Exception:
            pass
    procs.sort(reverse=True)
    if not procs:
        return "No processes." if language == "en" else "İşlem yok."
    header = "Running (by RAM):" if language == "en" else "Çalışanlar (RAM'e göre):"
    lines = [f"· {name} ({mem} MB)" for mem, name in procs[:10]]
    return header + "\n" + "\n".join(lines)


# ── Screen brightness ─────────────────────────────────────────────────────────

def brightness_set(level: int, language: str = "en") -> str:
    level = max(0, min(100, level))
    try:
        subprocess.Popen(
            f'powershell -NoProfile -WindowStyle Hidden -Command '
            f'"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)'
            f'.WmiSetBrightness(1,{level})"',
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return (f"Brightness {level}%." if language == "en"
                else f"Parlaklık %{level}.")
    except Exception:
        return ("Brightness control unavailable." if language == "en"
                else "Parlaklık kontrolü kullanılamıyor.")


# ── Steam ────────────────────────────────────────────────────────────────────

def steam_update_games(language: str = "en") -> str:
    """Open Steam downloads/update page."""
    subprocess.Popen("cmd /c start steam://open/downloads", shell=True)
    return (
        "Opening Steam updates, sir."
        if language == "en"
        else "Steam güncellemeler açılıyor, efendim."
    )


# ── Install + launch ─────────────────────────────────────────────────────────

_WINGET_IDS: dict[str, str] = {
    "spotify":   "Spotify.Spotify",
    "discord":   "Discord.Discord",
    "chrome":    "Google.Chrome",
    "brave":     "Brave.Brave",
    "firefox":   "Mozilla.Firefox",
    "vscode":    "Microsoft.VisualStudioCode",
    "steam":     "Valve.Steam",
    "vlc":       "VideoLAN.VLC",
    "obs":       "OBSProject.OBSStudio",
    "7zip":      "7zip.7zip",
    "notepad++": "Notepad++.Notepad++",
    "git":       "Git.Git",
    "python":    "Python.Python.3.13",
    "nodejs":    "OpenJS.NodeJS",
}


def install_app(app_key: str, language: str = "en") -> str:
    """Install via winget in background; launch if already present."""
    key = app_key.lower().strip()

    # Already installed? Just open it.
    targets = _desktop_targets()
    if key in targets:
        exe = targets[key][0]
        if Path(exe).is_file():
            subprocess.Popen([exe], shell=False)
            return (
                f"{app_key} is already installed — launching now."
                if language == "en"
                else f"{app_key} zaten kurulu — başlatılıyor, efendim."
            )

    winget_id = _WINGET_IDS.get(key)
    if not winget_id:
        return (
            f"I don't have a winget ID for {app_key}."
            if language == "en"
            else f"{app_key} için kurulum bilgisi yok."
        )

    subprocess.Popen(
        f'cmd /c winget install --id {winget_id} -e --silent',
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return (
        f"Installing {app_key} in the background, sir. I'll let you know when it's ready."
        if language == "en"
        else f"{app_key} arka planda kuruluyor, efendim. Hazır olduğunda bildiririm."
    )
