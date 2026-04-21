from __future__ import annotations

"""
Nyra Remote Server — FastAPI + WebSocket
Serves the mobile PWA and bridges phone commands to Nyra's pipeline.
"""

import asyncio
import base64
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import DATA_DIR
from router import route

TOKEN_FILE = DATA_DIR / "server_token.txt"
STATIC_DIR = Path(__file__).parent / "static"

_agent = None
_life_log = None
_night_agent = None
_token: str = ""


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/ping")
async def ping():
    return {"status": "online", "name": "Nyra"}


@app.get("/token-check")
async def token_check(t: str = ""):
    return {"valid": t == _token}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token = ws.query_params.get("token", "")
    if token != _token:
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    await ws.send_json({"type": "status", "state": "idle", "text": "Connected — Standing by, sir."})

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type", "command")

            if msg_type == "command":
                text = data.get("text", "").strip()
                lang = data.get("lang", "en")
                if not text:
                    continue

                await ws.send_json({"type": "status", "state": "thinking",
                                    "text": "Thinking..." if lang == "en" else "Düşünüyorum..."})

                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, _handle_command, text, lang)
                await ws.send_json({"type": "response", "text": response})
                await ws.send_json({"type": "status", "state": "idle", "text": "Standing by."})

            elif msg_type == "screenshot":
                await ws.send_json({"type": "status", "state": "thinking", "text": "Capturing screen..."})
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, _take_screenshot)
                await ws.send_json(result)
                await ws.send_json({"type": "status", "state": "idle", "text": "Standing by."})

            elif msg_type == "status_request":
                summary = _night_agent.status_summary() if _night_agent else "Night agent offline."
                await ws.send_json({"type": "system_status", "text": summary})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "text": str(exc)})
        except Exception:
            pass


# ── Command handler (sync, runs in thread pool) ───────────────────────────────

def _handle_command(text: str, lang: str) -> str:
    if _life_log:
        _life_log.log_command(f"[remote] {text}")

    result = route(text)

    if result.matched:
        if result.action == "launch_app":
            from actions import launch_app
            return launch_app(result.params.get("app", ""), lang)
        elif result.action == "open_web":
            from actions import open_web
            return open_web(result.params.get("url", ""), lang)
        elif result.action == "night_task":
            if _night_agent:
                _night_agent.schedule(text)
                return "Task queued, sir. I'll notify you when done." if lang == "en" \
                    else "Görev kuyruğa alındı, efendim."
        elif result.action == "night_status":
            return _night_agent.status_summary(lang) if _night_agent else "Night agent offline."
        elif result.action == "log_query":
            return _life_log.query(text, lang) if _life_log else "Life log offline."
        elif result.action == "volume_up":
            from actions import volume_up
            return volume_up(lang)
        elif result.action == "volume_down":
            from actions import volume_down
            return volume_down(lang)
        elif result.action == "volume_mute":
            from actions import volume_mute
            return volume_mute(lang)
        elif result.action == "media_play":
            from actions import media_play_pause
            return media_play_pause(lang)
        elif result.action == "media_next":
            from actions import media_next
            return media_next(lang)
        elif result.action == "media_prev":
            from actions import media_prev
            return media_prev(lang)
        elif result.action == "window_close":
            from actions import window_close
            return window_close(lang)
        elif result.action == "remember":
            return "Remembered." if lang == "en" else "Kaydedildi."

    if _agent is None:
        return "Agent not ready."
    try:
        return _agent.respond(text, lang, session_app="remote")
    except Exception as exc:
        return f"Error: {exc}"


def _take_screenshot() -> dict:
    try:
        import mss, mss.tools
        from PIL import Image
        import io
        with mss.mss() as s:
            shot = s.grab(s.monitors[0])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            # Resize for mobile (max 1200px wide)
            img.thumbnail((1200, 2400), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82)
            b64 = base64.b64encode(buf.getvalue()).decode()

        desc = ""
        try:
            import vision as _vision
            desc = _vision.describe_screen("What is on screen? Be brief.")
        except Exception:
            pass

        return {"type": "screenshot", "data": b64, "desc": desc}
    except Exception as exc:
        return {"type": "error", "text": f"Screenshot failed: {exc}"}


# ── Token management ──────────────────────────────────────────────────────────

def _load_or_create_token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(8)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    return token


# ── Public API ────────────────────────────────────────────────────────────────

def start(
    agent=None,
    life_log=None,
    night_agent=None,
    host: str = "0.0.0.0",
    port: int = 7437,
) -> str:
    """Start the server in a background thread. Returns the access token."""
    global _agent, _life_log, _night_agent, _token
    _agent = agent
    _life_log = life_log
    _night_agent = night_agent
    _token = _load_or_create_token()

    def run():
        uvicorn.run(app, host=host, port=port, log_level="warning")

    threading.Thread(target=run, daemon=True).start()
    return _token
