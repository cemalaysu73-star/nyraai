# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Nyra v2 — "Offline Jarvis"
# Build: pyinstaller nyra.spec

import sys
from pathlib import Path

block_cipher = None

# ── Collect faster-whisper and ctranslate2 model files ───────────────────────
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

binaries = []
datas = []

# faster-whisper assets (tokenizer configs, etc.)
datas += collect_data_files("faster_whisper")

# ctranslate2 native libs
binaries += collect_dynamic_libs("ctranslate2")

# PySide6 Qt plugins (multimedia, platform)
datas += collect_data_files("PySide6", includes=["*.dll", "plugins/**/*"])

# edge-tts certificates / metadata
try:
    datas += collect_data_files("edge_tts")
except Exception:
    pass

# ── Hidden imports ────────────────────────────────────────────────────────────
hidden = [
    "psutil",
    "pynvml",
    "numpy",
    "sounddevice",
    "requests",
    "edge_tts",
    "faster_whisper",
    "ctranslate2",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "win32clipboard",
    "win32con",
    "pywintypes",
    "pyperclip",
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "tkinter",
        "PyQt5",
        "PyQt6",
        "wx",
        "scipy",
        "pandas",
        "IPython",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── One-folder build (recommended — faster startup than onefile) ──────────────
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
    console=False,          # no terminal window
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
    upx_exclude=[],
    name="Nyra",
)
