from __future__ import annotations

import io
import socket
import sys

# Force UTF-8 output so arrow/emoji chars don't crash on Windows terminals
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from config import DATA_DIR
from setup_wizard import apply_user_config, needs_setup


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _build_core():
    """Instantiate all shared modules (used by both desktop and web mode)."""
    from agent import AgentCore
    from ambient import AmbientCopilot
    from life_log import LifeLog
    from llm import LLMCore
    from memory import Memory
    from memory_long import LongTermMemory
    from night_agent import NightAgent
    from screen import ScreenAwareness
    from stt import STT
    from tts import TTSManager

    memory = Memory()
    memory.load()

    stt = STT()
    screen = ScreenAwareness()
    tts = TTSManager()
    llm = LLMCore(memory.history)
    agent = AgentCore(llm)
    long_memory = LongTermMemory()

    night_agent = NightAgent()
    night_agent.set_agent(agent)

    life_log = LifeLog()
    life_log.set_agent(agent)
    life_log.start()

    ambient = AmbientCopilot()
    ambient.set_agent(agent)

    return dict(
        memory=memory, stt=stt, screen=screen, tts=tts,
        agent=agent, long_memory=long_memory,
        night_agent=night_agent, life_log=life_log, ambient=ambient,
    )


def _start_server(core: dict, port: int) -> str:
    import server as _server
    token = _server.start(
        agent=core["agent"],
        life_log=core["life_log"],
        night_agent=core["night_agent"],
        host="0.0.0.0",
        port=port,
    )
    ip = _local_ip()
    print(f"\n{'='*54}")
    print(f"  Nyra Remote  →  http://{ip}:{port}/?t={token}")
    print(f"  Token        →  {token}")
    print(f"  LAN URL      →  http://{ip}:{port}")
    print(f"{'='*54}\n")
    return token


def web_mode() -> None:
    """Headless web-only mode — no PySide6, browser UI on localhost:3000."""
    import time
    import webbrowser

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    apply_user_config()

    print("[Nyra] Starting in web mode...")
    core = _build_core()

    port = 3000
    token = _start_server(core, port)

    from tools import notify
    core["night_agent"].set_notify(lambda title, msg: notify(title, msg))
    core["ambient"].start()

    url = f"http://localhost:{port}/?t={token}"
    print(f"[Nyra] Opening browser → {url}")
    webbrowser.open(url)

    print("[Nyra] Running. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Nyra] Shutting down.")


def desktop_mode() -> None:
    """Full desktop mode with PySide6 UI."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    apply_user_config()

    # ── License check ─────────────────────────────────────────────────────────
    import license as _lic
    _info = _lic.check()
    if not _info.valid:
        from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog
        _qa = QApplication.instance() or QApplication(sys.argv)
        msg = QMessageBox()
        msg.setWindowTitle("Nyra — License Required")
        msg.setText(f"<b>{_info.message}</b><br><br>Enter your license key to continue.<br>"
                    f"Get one at <a href='https://nyraai.com'>nyraai.com</a>")
        msg.setIcon(QMessageBox.Warning)
        key_btn = msg.addButton("Enter Key", QMessageBox.AcceptRole)
        msg.addButton("Quit", QMessageBox.RejectRole)
        msg.exec()
        if msg.clickedButton() == key_btn:
            key, ok = QInputDialog.getText(None, "License Key", "Paste your license key:")
            if ok and key.strip():
                result = _lic.activate(key.strip())
                if not result.valid:
                    QMessageBox.critical(None, "Invalid Key", result.message)
                    sys.exit(1)
            else:
                sys.exit(0)
        else:
            sys.exit(0)
    elif _info.trial:
        print(f"[License] {_info.message}")

    if needs_setup():
        from PySide6.QtWidgets import QApplication
        from setup_wizard import SetupWizard
        app = QApplication(sys.argv)
        wizard = SetupWizard()
        wizard.show()
        app.exec()
        apply_user_config()
        app.quit()

    from ui import NyraWindow, create_app

    app = create_app()
    core = _build_core()

    window = NyraWindow(
        memory=core["memory"],
        stt=core["stt"],
        agent=core["agent"],
        tts=core["tts"],
        screen=core["screen"],
        long_memory=core["long_memory"],
        night_agent=core["night_agent"],
        life_log=core["life_log"],
        ambient=core["ambient"],
    )

    core["ambient"].set_speak(lambda text, lang: window._ambient_speak(text, lang))

    from tools import notify
    core["night_agent"].set_notify(lambda title, msg: notify(title, msg))

    core["ambient"].start()
    _start_server(core, port=7437)

    # ── Auto-updater ──────────────────────────────────────────────────────────
    import updater
    updater.set_notify(lambda t, m: core["night_agent"]._notify_fn and core["night_agent"]._notify_fn(t, m))
    updater.check_in_background()

    window.show_and_greet()
    sys.exit(app.exec())


def main() -> None:
    if "--web" in sys.argv:
        web_mode()
    else:
        desktop_mode()


if __name__ == "__main__":
    main()
