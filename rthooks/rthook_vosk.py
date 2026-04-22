"""
PyInstaller runtime hook — runs before any user code.
Adds the bundled vosk directory to the DLL search path so
vosk/__init__.py's os.add_dll_directory() doesn't crash.
"""
import os
import sys

if hasattr(sys, "_MEIPASS"):
    vosk_dir = os.path.join(sys._MEIPASS, "vosk")
    if os.path.isdir(vosk_dir):
        os.add_dll_directory(vosk_dir)
