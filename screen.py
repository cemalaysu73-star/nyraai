from __future__ import annotations

from dataclasses import dataclass

try:
    import psutil
    import win32gui
    import win32process
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


@dataclass(slots=True)
class ScreenContext:
    process_name: str = ""
    window_title: str = ""

    def context_line(self) -> str:
        if self.process_name:
            return f"Active app: {self.process_name}."
        return ""

    def is_empty(self) -> bool:
        return not self.process_name


class ScreenAwareness:
    @property
    def available(self) -> bool:
        return _AVAILABLE

    def get_context(self) -> ScreenContext:
        if not _AVAILABLE:
            return ScreenContext()
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            name = proc.name().lower().removesuffix(".exe")
            return ScreenContext(process_name=name, window_title=title[:80])
        except Exception:
            return ScreenContext()
