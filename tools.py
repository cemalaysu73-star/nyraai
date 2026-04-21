from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import win32clipboard

# ── Protected paths — never delete/overwrite these ───────────────────────────

_PROTECTED = {
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
    Path("C:/ProgramData"),
    Path("C:/System Volume Information"),
}


def _is_protected(path: Path) -> bool:
    p = path.resolve()
    for protected in _PROTECTED:
        try:
            p.relative_to(protected.resolve())
            return True
        except ValueError:
            pass
    # Also protect drive roots (C:\, D:\, etc.)
    if len(p.parts) <= 1:
        return True
    return False


# ── File tools ────────────────────────────────────────────────────────────────

def read_file(path: str) -> str:
    try:
        content = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
        if len(content) > 8000:
            content = content[:8000] + "\n...[truncated]"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(path: str, content: str) -> str:
    try:
        p = Path(path).expanduser()
        if _is_protected(p):
            return f"Blocked: {path} is a protected system location."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def delete_file(path: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        if _is_protected(p):
            return f"Blocked: {path} is a protected system location."
        if not p.exists():
            return f"Not found: {path}"
        if p.is_dir():
            shutil.rmtree(p)
            return f"Deleted directory: {path}"
        p.unlink()
        return f"Deleted: {path}"
    except Exception as e:
        return f"Error deleting: {e}"


def move_file(src: str, dst: str) -> str:
    try:
        s = Path(src).expanduser().resolve()
        d = Path(dst).expanduser()
        if _is_protected(s) or _is_protected(d):
            return "Blocked: protected system location."
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return f"Moved {src} → {dst}"
    except Exception as e:
        return f"Error moving: {e}"


def list_files(directory: str = ".") -> str:
    try:
        p = Path(directory).expanduser()
        items = sorted(p.rglob("*"))[:60]
        return "\n".join(
            str(f.relative_to(p)) for f in items if f.is_file()
        ) or "No files found."
    except Exception as e:
        return f"Error: {e}"


def download_file(url: str, dest: str = "") -> str:
    try:
        url = url.strip()
        if not dest:
            filename = url.split("/")[-1].split("?")[0] or "download"
            dest = str(Path.home() / "Downloads" / filename)
        dest_path = Path(dest).expanduser()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, dest_path)
        size = dest_path.stat().st_size
        return f"Downloaded {size:,} bytes → {dest_path}"
    except Exception as e:
        return f"Download error: {e}"


# ── Shell ────────────────────────────────────────────────────────────────────

def run_command(cmd: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return (out.strip() or f"Exit code: {result.returncode}")[:4000]
    except subprocess.TimeoutExpired:
        return "Command timed out."
    except Exception as e:
        return f"Error: {e}"


# ── Process management ────────────────────────────────────────────────────────

def list_processes() -> str:
    try:
        import psutil
        procs = sorted(
            {p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"]},
        )
        return "\n".join(procs[:80])
    except Exception as e:
        return f"Error: {e}"


def kill_process(name: str) -> str:
    try:
        import psutil
        name_lower = name.lower().rstrip(".exe") + ".exe" if not name.lower().endswith(".exe") else name.lower()
        killed = []
        for proc in psutil.process_iter(["name", "pid"]):
            if proc.info["name"].lower() == name_lower:
                proc.terminate()
                killed.append(str(proc.info["pid"]))
        if killed:
            return f"Terminated {name} (PIDs: {', '.join(killed)})"
        return f"{name} not found running."
    except Exception as e:
        return f"Error: {e}"


# ── Clipboard ────────────────────────────────────────────────────────────────

def get_clipboard() -> str:
    try:
        win32clipboard.OpenClipboard()
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return str(data)[:4000] if data else "Clipboard is empty."
    except Exception as e:
        return f"Clipboard error: {e}"


def set_clipboard(text: str) -> str:
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return "Copied to clipboard."
    except Exception as e:
        return f"Clipboard error: {e}"


# ── Notifications ─────────────────────────────────────────────────────────────

def notify(title: str, message: str) -> str:
    try:
        ps = (
            f'$n = New-Object System.Windows.Forms.NotifyIcon;'
            f'Add-Type -AssemblyName System.Windows.Forms;'
            f'$n.Icon = [System.Drawing.SystemIcons]::Information;'
            f'$n.Visible = $true;'
            f'$n.BalloonTipTitle = "{title}";'
            f'$n.BalloonTipText = "{message}";'
            f'$n.ShowBalloonTip(4000);'
            f'Start-Sleep -s 5; $n.Dispose()'
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-c", ps],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return "Notification sent."
    except Exception as e:
        return f"Notification error: {e}"
