from __future__ import annotations

import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

import psutil
import win32api
import win32con
import win32gui

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
        "valorant": resolve([
            rf"{pf}\Riot Games\VALORANT\live\VALORANT.exe",
            "VALORANT.exe",
        ]),
        "notepad": ["notepad.exe"],
        "explorer": ["explorer.exe"],
        "task manager": ["taskmgr.exe"],
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

        return (
            f"Launching {app_key}."
            if language == "en"
            else f"{app_key} başlatılıyor."
        )
    except OSError:
        return (
            f"Found {app_key}, but Windows could not start it."
            if language == "en"
            else f"{app_key} bulunamadı veya başlatılamadı."
        )


def open_web(url: str, language: str = "en") -> str:
    try:
        webbrowser.open(url)
        label = url.split("//")[-1].split("/")[0].replace("www.", "")
        return f"Opening {label}." if language == "en" else f"{label} açılıyor."
    except Exception:
        return "Could not open that." if language == "en" else "Açılamadı."


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
    "vscode":   ["code.exe"],
    "valorant": ["valorant.exe", "valorant-win64-shipping.exe"],
    "notepad":  ["notepad.exe"],
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


def system_sleep(language: str = "en") -> str:
    subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
    return "Going to sleep." if language == "en" else "Uyku moduna geçiliyor."
