# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Nyra v2
# Build: pyinstaller nyra.spec --clean

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_all

block_cipher = None

binaries = []
datas    = []

# ── faster-whisper / ctranslate2 ──────────────────────────────────────────────
datas   += collect_data_files("faster_whisper")
binaries += collect_dynamic_libs("ctranslate2")

# ── PySide6 ───────────────────────────────────────────────────────────────────
datas += collect_data_files("PySide6", includes=["*.dll", "plugins/**/*"])

# ── edge-tts ──────────────────────────────────────────────────────────────────
try:
    datas += collect_data_files("edge_tts")
except Exception:
    pass

# ── vosk ──────────────────────────────────────────────────────────────────────
import importlib.util
_vosk_spec = importlib.util.find_spec("vosk")
if _vosk_spec and _vosk_spec.submodule_search_locations:
    _vosk_dir = Path(list(_vosk_spec.submodule_search_locations)[0])
    datas    += [(str(_vosk_dir), "vosk")]
    for _dll in _vosk_dir.glob("*.dll"):
        binaries += [(str(_dll), "vosk")]

# ── silero-vad ────────────────────────────────────────────────────────────────
try:
    _sil_d, _sil_b, _ = collect_all("silero_vad")
    datas   += _sil_d
    binaries += _sil_b
except Exception:
    pass

# ── torch (needed by silero-vad) ──────────────────────────────────────────────
try:
    datas   += collect_data_files("torch")
    binaries += collect_dynamic_libs("torch")
except Exception:
    pass

# ── duckduckgo-search ─────────────────────────────────────────────────────────
try:
    datas += collect_data_files("duckduckgo_search")
except Exception:
    pass

# ── Static web UI ─────────────────────────────────────────────────────────────
if Path("static").exists():
    datas += [("static", "static")]

# ── Python DLLs next to the EXE ──────────────────────────────────────────────
import glob as _glob
_py_ver = f"{sys.version_info.major}{sys.version_info.minor}"
for _dll_name in [f"python{_py_ver}.dll", "python3.dll"]:
    for _src in (
        _glob.glob(rf"{sys.base_prefix}\{_dll_name}") +
        _glob.glob(rf"C:\Windows\System32\{_dll_name}") +
        _glob.glob(rf"C:\Windows\SysWOW64\{_dll_name}")
    ):
        if Path(_src).exists():
            binaries.append((_src, "."))
            break

# ── Hidden imports ────────────────────────────────────────────────────────────
hidden = [
    # audio / STT
    "vosk", "sounddevice", "faster_whisper", "ctranslate2",
    "silero_vad", "torch", "torchaudio",
    # UI
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork",
    # system
    "psutil", "pynvml", "numpy",
    "win32api", "win32con", "win32gui", "win32clipboard", "pywintypes",
    "comtypes", "comtypes.client",
    "pycaw", "pycaw.pycaw",
    # web / search
    "requests", "urllib3", "certifi",
    "duckduckgo_search",
    # TTS
    "edge_tts", "pyttsx3",
    # vision
    "PIL", "PIL.Image", "PIL.ImageGrab",
    "mss",
    # utils
    "dotenv", "python_dotenv",
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=["rthooks"],
    hooksconfig={},
    runtime_hooks=["rthooks/rthook_vosk.py"],
    excludes=[
        "matplotlib", "tkinter", "PyQt5", "PyQt6", "wx",
        "scipy", "pandas", "IPython", "notebook",
        "tensorflow", "keras", "sklearn",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE (one-folder — faster startup than onefile) ───────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Nyra",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/nyra.ico" if Path("assets/nyra.ico").exists() else None,
    version="version_info.txt" if Path("version_info.txt").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=["vcruntime140.dll", "msvcp140.dll", "python*.dll"],
    name="Nyra",
)
