from __future__ import annotations

import socket
import sys

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


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    apply_user_config()

    if needs_setup():
        from PySide6.QtWidgets import QApplication
        from setup_wizard import SetupWizard
        app = QApplication(sys.argv)
        wizard = SetupWizard()
        wizard.show()
        app.exec()
        apply_user_config()
        app.quit()

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
    from ui import NyraWindow, create_app

    app = create_app()

    memory = Memory()
    memory.load()

    stt = STT()
    screen = ScreenAwareness()
    tts = TTSManager()
    llm = LLMCore(memory.history)
    agent = AgentCore(llm)
    long_memory = LongTermMemory()

    # ── Game-changer modules ──────────────────────────────────────────────────
    night_agent = NightAgent()
    night_agent.set_agent(agent)

    life_log = LifeLog()
    life_log.set_agent(agent)
    life_log.start()

    ambient = AmbientCopilot()
    ambient.set_agent(agent)

    window = NyraWindow(
        memory=memory,
        stt=stt,
        agent=agent,
        tts=tts,
        screen=screen,
        long_memory=long_memory,
        night_agent=night_agent,
        life_log=life_log,
        ambient=ambient,
    )

    # Wire ambient speak → Nyra TTS
    ambient.set_speak(lambda text, lang: window._ambient_speak(text, lang))
    # Wire night agent notifications → Windows toast
    from tools import notify
    night_agent.set_notify(lambda title, msg: notify(title, msg))

    ambient.start()

    # ── Remote server ─────────────────────────────────────────────────────────
    import server as _server
    _port = 7437
    _token = _server.start(
        agent=agent,
        life_log=life_log,
        night_agent=night_agent,
        host="0.0.0.0",
        port=_port,
    )
    _ip = _local_ip()
    print(f"\n{'='*54}")
    print(f"  Nyra Remote  →  http://{_ip}:{_port}/?t={_token}")
    print(f"  Token        →  {_token}")
    print(f"  LAN URL      →  http://{_ip}:{_port}")
    print(f"{'='*54}\n")

    window.show_and_greet()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
