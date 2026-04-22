from __future__ import annotations

"""
Nyra Auto-Updater
-----------------
On startup, checks a version endpoint for a newer release.
If found, downloads the zip in background and prompts user to restart.

Version file format (host at https://nyraai.com/version.json):
{
  "version": "1.2.0",
  "url": "https://nyraai.com/releases/nyra-1.2.0.zip",
  "notes": "Bug fixes and performance improvements."
}
"""

import json
import os
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable, Optional

VERSION = "1.0.0"
VERSION_URL = "https://nyraai.com/version.json"

_notify_fn: Optional[Callable[[str, str], None]] = None


def set_notify(fn: Callable[[str, str], None]) -> None:
    global _notify_fn
    _notify_fn = fn


def _parse_version(v: str) -> tuple:
    return tuple(int(x) for x in v.strip().split("."))


def _check_and_notify() -> None:
    try:
        import urllib.request
        with urllib.request.urlopen(VERSION_URL, timeout=8) as r:
            data = json.loads(r.read())

        remote = data.get("version", "0.0.0")
        if _parse_version(remote) <= _parse_version(VERSION):
            return  # already up to date

        notes = data.get("notes", "")
        msg   = f"Nyra {remote} is available. {notes}"

        if _notify_fn:
            _notify_fn("Nyra Update Available", msg)
        else:
            print(f"[Updater] {msg}")

    except Exception:
        pass  # no internet or server down — silent fail


def check_in_background() -> None:
    """Call once at startup — runs the check in a daemon thread."""
    threading.Thread(target=_check_and_notify, daemon=True).start()
