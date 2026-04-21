from __future__ import annotations

import base64
import io

import requests

from config import APP_CONFIG

try:
    import mss
    from PIL import Image
    _OK = True
except ImportError:
    _OK = False


def take_screenshot() -> str | None:
    if not _OK:
        return None
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img_data = sct.grab(monitor)
            img = Image.frombytes("RGB", img_data.size, img_data.bgra, "raw", "BGRX")
            img.thumbnail((1280, 720), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def describe_screen(prompt: str = "Describe what you see on the screen in detail. Focus on any code, errors, or text visible.") -> str:
    img_b64 = take_screenshot()
    if img_b64 is None:
        return "Screenshot unavailable — install mss and Pillow."
    try:
        payload = {
            "model": APP_CONFIG.vision_model,
            "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
            "stream": False,
            "options": {"temperature": 0.1},
        }
        resp = requests.post(
            f"{APP_CONFIG.ollama_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "No response from vision model.")
    except Exception as e:
        return f"Vision error: {e}"
