from __future__ import annotations

import math
import random
import sys
import threading
from collections.abc import Callable
from datetime import datetime

import numpy as np
import psutil

try:
    import pynvml as _nvml
    _nvml.nvmlInit()
    _GPU_HANDLE = _nvml.nvmlDeviceGetHandleByIndex(0)
    _NVML_OK = True
except Exception:
    _NVML_OK = False
    _GPU_HANDLE = None

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRunnable,
    QThread, QThreadPool, QTimer, Qt, QObject, Signal,
)
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QLinearGradient,
    QPainter, QPen, QPixmap, QRadialGradient, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy,
    QSystemTrayIcon, QVBoxLayout, QMenu, QWidget,
)

import vad as _vad
from actions import (
    launch_app, open_web, recent_files, close_app,
    volume_up, volume_down, volume_mute, volume_set,
    media_play_pause, media_next, media_prev, media_stop,
    window_close, window_minimize, window_maximize,
    steam_update_games, install_app, fetch_world_news,
    system_lock, system_sleep, system_wake,
    system_info, take_screenshot, clipboard_read, clipboard_write,
    type_text, send_hotkey, open_folder, file_find,
    network_info, process_list, brightness_set,
)
from agent import AgentCore
from config import APP_CONFIG
from memory import Memory
from memory_long import LongTermMemory
from behavior import BehaviorTracker
from intent import MATCH_THRESHOLD, IntentStore, actions_to_keys, group_to_routes
from ml_engine import get_engine
from stt_repair import STTRepair
from router import RouteResult, parse_llm_actions, route
from screen import ScreenAwareness, ScreenContext
from stt import STT
from tts import TTSManager
from wake import WakeDetector, capture_command
from self_improve import SelfImprove
from mode_detector import detect as _detect_mode, status_text as _mode_status
from agent import clean_for_tts as _clean_for_tts


# ── Worker helpers ────────────────────────────────────────────────────────────

class _Signals(QObject):
    finished = Signal(object)
    error = Signal(str)


class _Runnable(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _Signals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self.fn(*self.args, **self.kwargs))
        except Exception as exc:
            self.signals.error.emit(str(exc))


class VoiceInputThread(QThread):
    transcribed = Signal(str, str)   # text, language
    failed = Signal()

    def __init__(self, stt: STT, stop_event: threading.Event, language_hint: str = "") -> None:
        super().__init__()
        self._stt = stt
        self._stop_event = stop_event
        self._language_hint = language_hint or None

    def run(self) -> None:
        raw = capture_command(stop_event=self._stop_event)
        if not raw or self._stop_event.is_set():
            self.failed.emit()
            return
        result = self._stt.transcribe(raw, language_hint=self._language_hint)
        if result.valid:
            self.transcribed.emit(result.text, result.language)
        else:
            self.failed.emit()


class LLMThread(QThread):
    token = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        agent: AgentCore,
        text: str,
        language: str,
        app: str,
        long_memory: LongTermMemory | None = None,
        behavior_ctx: str = "",
    ) -> None:
        super().__init__()
        self._agent = agent
        self._text = text
        self._language = language
        self._app = app
        self._long_memory = long_memory
        self._behavior_ctx = behavior_ctx

    def run(self) -> None:
        try:
            long_mem = self._long_memory.format_context(self._text) if self._long_memory else ""
            if self._behavior_ctx:
                long_mem = f"{self._behavior_ctx}\n{long_mem}".strip()
            result = self._agent.respond(
                self._text,
                self._language,
                session_app=self._app,
                long_mem_context=long_mem,
                on_token=lambda t: self.token.emit(t),
            )
            self.done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class BargeinMonitor(QThread):
    """Listens for speech while TTS is playing. Emits detected() on barge-in."""
    detected = Signal()

    def __init__(self, stop_event: threading.Event) -> None:
        super().__init__()
        self._stop = stop_event

    def run(self) -> None:
        import queue as _queue
        import sounddevice as _sd
        audio_q: _queue.Queue[bytes] = _queue.Queue()
        speech_count = 0

        def cb(indata, frames, time_info, status) -> None:
            audio_q.put(bytes(indata))

        try:
            with _sd.RawInputStream(
                samplerate=16_000, blocksize=512,
                dtype="int16", channels=1, callback=cb,
            ):
                while not self._stop.is_set():
                    try:
                        chunk = audio_q.get(timeout=0.1)
                    except Exception:
                        continue
                    audio = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                    if _vad.is_speech(audio, threshold=0.60):
                        speech_count += 1
                        if speech_count >= 4:   # ~128ms of confirmed speech
                            self.detected.emit()
                            return
                    else:
                        speech_count = 0
        except Exception:
            pass


# ── Orb ──────────────────────────────────────────────────────────────────────

_STATE_GLOW = {
    "idle":      (255, 200, 170,  70),
    "listening": (110, 170, 255,  95),
    "thinking":  (170, 130, 255,  85),
    "speaking":  (255, 210, 100, 115),
    "wake":      (255, 255, 180, 130),
}
_STATE_CORE = {
    "idle":      ("#FFF2E8", "#D8B4A0", "#4E3D3B", "#161416"),
    "listening": ("#E8F2FF", "#90B8E0", "#243A58", "#090D14"),
    "thinking":  ("#F2E8FF", "#B490E0", "#382458", "#0E0914"),
    "speaking":  ("#FFFAE0", "#E0CC80", "#584A24", "#141209"),
    "wake":      ("#FFFFF0", "#E0E080", "#585824", "#141409"),
}
_STATE_WAVE = {
    "idle": (255, 210, 180),
    "listening": (120, 180, 255),
    "thinking": (180, 140, 255),
    "speaking": (255, 210, 100),
    "wake": (255, 255, 160),
}
_STATE_STATUS_COLOR = {
    "idle":      "#3A5270",
    "listening": "#4A90D9",
    "thinking":  "#9060D9",
    "speaking":  "#D9A030",
    "wake":      "#D9D040",
}


class OrbWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.state = "idle"
        self.phase = 0.0
        self._frame = 0
        self._stars = self._build_stars()
        self._shooting: list[dict] = []
        self._shoot_cd = random.randint(150, 320)
        # Cached pixmaps — rebuilt only when size changes or state changes
        self._bg_pix: QPixmap | None = None
        self._star_pix: QPixmap | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)   # 60 fps
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._star_pix = None   # force star rebuild on state change

    # ── Star field construction ───────────────────────────────────────────────

    def _build_stars(self) -> list[dict]:
        rng = random.Random(42)
        stars: list[dict] = []

        def add(n, r_lo, r_hi, kind, colors):
            for _ in range(n):
                stars.append({
                    "x": rng.random(), "y": rng.random(),
                    "r": rng.uniform(r_lo, r_hi),
                    "offset": rng.random() * math.pi * 2,
                    "speed": rng.uniform(0.4, 1.4),
                    "kind": kind,
                    "rgb": rng.choice(colors),
                })

        add(160, 0.4, 1.0, "dim",      [(210, 225, 255), (240, 245, 255)])
        add(80,  1.0, 1.7, "mid",      [(200, 220, 255), (255, 248, 235)])
        add(30,  1.6, 2.5, "bright",   [(220, 235, 255), (255, 230, 180)])
        add(8,   2.8, 3.6, "brilliant",[(200, 225, 255), (255, 220, 160)])
        return stars

    # ── Animation tick ────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self.phase = (self.phase + 0.020) % (math.pi * 2)
        self._frame += 1

        self._shoot_cd -= 1
        if self._shoot_cd <= 0:
            self._shoot_cd = random.randint(200, 400)
            self._shooting.append({
                "x": random.uniform(0.0, 0.70),
                "y": random.uniform(0.04, 0.45),
                "dx": random.uniform(0.003, 0.008),
                "dy": random.uniform(0.001, 0.003),
                "life": 1.0,
                "tail": random.uniform(0.10, 0.22),
            })

        alive = []
        for s in self._shooting:
            s["x"] += s["dx"]
            s["y"] += s["dy"]
            s["life"] -= 0.028
            if s["life"] > 0 and s["x"] < 1.1:
                alive.append(s)
        self._shooting = alive

        # Stars run at 15 fps (every 4 frames) — orb runs at full 60 fps
        if self._frame % 4 == 0:
            self._star_pix = None

        self.update()

    # ── Cached background (gradient + nebula) ─────────────────────────────────

    def _build_bg_pix(self, W: int, H: int) -> QPixmap:
        pix = QPixmap(W, H)
        p = QPainter(pix)
        bg = QLinearGradient(0, 0, W * 0.6, H)
        bg.setColorAt(0.0, QColor("#020307"))
        bg.setColorAt(0.45, QColor("#050810"))
        bg.setColorAt(1.0, QColor("#030508"))
        p.fillRect(pix.rect(), bg)
        for nx, ny, nr, ng, nb, na in [
            (0.12, 0.22, 55, 15, 110, 18),
            (0.88, 0.72, 15, 50, 110, 14),
            (0.50, 0.08, 80, 25,  25, 12),
            (0.75, 0.30, 20, 80,  60, 10),
        ]:
            grad = QRadialGradient(nx * W, ny * H, min(W, H) * 0.28)
            grad.setColorAt(0.0, QColor(nr, ng, nb, na))
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(pix.rect(), grad)
        p.end()
        return pix

    # ── Cached star field (rebuilt at 15 fps) ─────────────────────────────────

    def _build_star_pix(self, W: int, H: int, boost: int) -> QPixmap:
        pix = QPixmap(W, H)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        ph = self.phase
        for s in self._stars:
            sx = int(s["x"] * W)
            sy = int(s["y"] * H)
            tw = math.sin(ph * s["speed"] + s["offset"])
            rgb = s["rgb"]
            kind = s["kind"]

            if kind == "dim":
                a = max(0, 8 + int((tw + 1) * 16) + boost // 4)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*rgb, a))
                ri = max(1, int(s["r"]))
                p.drawEllipse(QPoint(sx, sy), ri, ri)

            elif kind == "mid":
                a = max(0, 22 + int((tw + 1) * 34) + boost // 3)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*rgb, a))
                p.drawEllipse(QPoint(sx, sy), int(s["r"]), int(s["r"]))

            elif kind == "bright":
                a = min(255, max(0, 60 + int((tw + 1) * 90) + boost))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*rgb, a))
                ri = int(s["r"])
                p.drawEllipse(QPoint(sx, sy), ri, ri)
                span = int(s["r"] * 5)
                p.setPen(QPen(QColor(*rgb, max(0, a // 3)), 1.0))
                p.drawLine(sx - span, sy, sx + span, sy)
                p.drawLine(sx, sy - span, sx, sy + span)

            elif kind == "brilliant":
                a = min(255, max(0, 90 + int((tw + 1) * 110) + boost))
                glow_r = int(s["r"] * 6)
                gl = QRadialGradient(sx, sy, glow_r)
                gl.setColorAt(0.0, QColor(*rgb, a // 2))
                gl.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.setBrush(gl)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPoint(sx, sy), glow_r, glow_r)
                ri = int(s["r"])
                p.setBrush(QColor(*rgb, a))
                p.drawEllipse(QPoint(sx, sy), ri, ri)
                span = int(s["r"] * 9)
                p.setPen(QPen(QColor(*rgb, max(0, a // 2)), 1.1))
                p.drawLine(sx - span, sy, sx + span, sy)
                p.drawLine(sx, sy - span, sx, sy + span)
        p.end()
        return pix

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        W, H = r.width(), r.height()

        # 1 ── Background (cached — only rebuilt on resize)
        if self._bg_pix is None or self._bg_pix.width() != W or self._bg_pix.height() != H:
            self._bg_pix = self._build_bg_pix(W, H)
            self._star_pix = None   # force star rebuild on resize too
        p.drawPixmap(0, 0, self._bg_pix)

        # 2 ── Star field (cached — rebuilt at 15 fps)
        boost = {"idle": 0, "listening": 18, "thinking": 10, "speaking": 24, "wake": 35}.get(self.state, 0)
        if self._star_pix is None or self._star_pix.width() != W or self._star_pix.height() != H:
            self._star_pix = self._build_star_pix(W, H, boost)
        p.drawPixmap(0, 0, self._star_pix)

        # 3 ── Shooting stars (always live — rare, cheap)
        for ss in self._shooting:
            hx = int(ss["x"] * W)
            hy = int(ss["y"] * H)
            tx = int((ss["x"] - ss["dx"] * ss["tail"] * 90) * W)
            ty = int((ss["y"] - ss["dy"] * ss["tail"] * 90) * H)
            a = int(ss["life"] * 220)
            sg = QLinearGradient(float(hx), float(hy), float(tx), float(ty))
            sg.setColorAt(0.0, QColor(255, 255, 255, a))
            sg.setColorAt(0.6, QColor(200, 220, 255, a // 3))
            sg.setColorAt(1.0, QColor(180, 200, 255, 0))
            p.setPen(QPen(QBrush(sg), 1.5))
            p.drawLine(hx, hy, tx, ty)
            hg = QRadialGradient(float(hx), float(hy), 3.5)
            hg.setColorAt(0.0, QColor(255, 255, 255, min(255, a)))
            hg.setColorAt(1.0, QColor(200, 220, 255, 0))
            p.setBrush(hg)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(hx, hy), 3, 3)

        # ── Orb geometry ──────────────────────────────────────────────────────
        cx, cy = W // 2, H // 2
        center = QPoint(cx, cy)
        radius = min(W, H) * 0.22
        pulse = 1.0 + math.sin(self.phase * 1.9) * 0.013   # subtle breathe
        core_r = int(radius * pulse)

        gr   = _STATE_GLOW.get(self.state, (255, 200, 170, 70))
        wrgb = _STATE_WAVE.get(self.state, (255, 210, 180))
        cc   = _STATE_CORE.get(self.state, _STATE_CORE["idle"])

        # 4 ── Outer atmosphere (wide, very soft)
        atm = QRadialGradient(center, radius * 3.4)
        atm.setColorAt(0.0, QColor(gr[0], gr[1], gr[2], 20))
        atm.setColorAt(0.55, QColor(gr[0], gr[1], gr[2], 6))
        atm.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(atm)
        p.setPen(Qt.NoPen)
        p.drawEllipse(center, int(radius * 3.4), int(radius * 3.4))

        # 5 ── Glow ring
        outer = QRadialGradient(center, radius * 2.2)
        outer.setColorAt(0.0, QColor(*gr))
        outer.setColorAt(0.42, QColor(gr[0], gr[1], gr[2], gr[3] // 3))
        outer.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(outer)
        p.drawEllipse(center, int(radius * 2.2), int(radius * 2.2))

        # 6 ── Orbital rings (4, clean)
        for sx_r, sy_r, col, rot, lw in [
            (1.52, 0.38, QColor(*gr[:3], 88),        self.phase * 28,  1.0),
            (1.30, 0.62, QColor(222, 210, 205, 60),  -self.phase * 22, 0.9),
            (1.78, 0.80, QColor(*gr[:3], 36),         self.phase * 15,  0.8),
            (2.10, 0.24, QColor(180, 210, 255, 28),  -self.phase * 10,  0.7),
        ]:
            p.save()
            p.translate(center)
            p.rotate(rot * 57.2958)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, lw))
            p.drawEllipse(QPoint(0, 0), int(radius * sx_r * pulse), int(radius * sy_r * pulse))
            p.restore()

        # 7 ── Orbiting particles
        for i in range(6):
            angle = self.phase * 1.4 + (i / 6) * math.pi * 2
            px2 = int(cx + radius * 1.44 * math.cos(angle))
            py2 = int(cy + radius * 0.36 * math.sin(angle))
            pa = max(0, int(115 * (math.sin(angle) + 1) / 2))
            ps = max(1, int(2.0 * (math.sin(angle * 0.5) + 1.5)))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*gr[:3], pa))
            p.drawEllipse(QPoint(px2, py2), ps, ps)

        # 8 ── Orb core (glass sphere)
        sphere = QRadialGradient(cx - radius * 0.26, cy - radius * 0.30, radius * 1.1)
        sphere.setColorAt(0.0,  QColor(cc[0]))
        sphere.setColorAt(0.20, QColor(cc[1]))
        sphere.setColorAt(0.55, QColor(cc[2]))
        sphere.setColorAt(1.0,  QColor(cc[3]))
        p.setBrush(sphere)
        p.setPen(QPen(QColor(*gr[:3], 75), 1.0))
        p.drawEllipse(center, core_r, core_r)

        # Specular highlight — top-left catch light (glass effect)
        spec = QRadialGradient(cx - radius * 0.34, cy - radius * 0.38, radius * 0.50)
        spec.setColorAt(0.0, QColor(255, 255, 255, 95))
        spec.setColorAt(0.45, QColor(255, 255, 255, 22))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(spec)
        p.setPen(Qt.NoPen)
        p.drawEllipse(center, core_r, core_r)

        # Inner colored glow
        inner_g = QRadialGradient(center, radius * 0.65)
        inner_g.setColorAt(0.0, QColor(*wrgb, 40))
        inner_g.setColorAt(1.0, QColor(*wrgb, 0))
        p.setBrush(inner_g)
        p.drawEllipse(center, core_r, core_r)

        # 9 ── Waveform (3-pixel step, smoother)
        amp = {"idle": 2, "listening": 12, "thinking": 6, "speaking": 16, "wake": 19}.get(self.state, 2)
        wy = H - 32
        for ph_off, alpha, lw in [(0.0, 80, 1.4), (math.pi * 0.30, 32, 0.9)]:
            p.setPen(QPen(QColor(*wrgb, alpha), lw))
            lpt = QPoint(14, wy)
            for x in range(14, W - 14, 3):
                wave = (math.sin(self.phase * 4.5 + x * 0.040 + ph_off)
                        + math.cos(self.phase * 2.8 + x * 0.018 + ph_off) * 0.50)
                cpt = QPoint(x, int(wy + wave * amp))
                p.drawLine(lpt, cpt)
                lpt = cpt

        # 10 ── Vertical beam
        beam = QLinearGradient(cx, 0, cx, H)
        beam.setColorAt(0.0,  QColor(*wrgb, 0))
        beam.setColorAt(0.35, QColor(*wrgb, 11))
        beam.setColorAt(0.65, QColor(*wrgb, 11))
        beam.setColorAt(1.0,  QColor(*wrgb, 0))
        p.fillRect(cx - 14, 0, 28, H, beam)

        # 11 ── HUD text
        p.setPen(QColor(200, 215, 235, 75))
        p.setFont(QFont("Segoe UI Variable", 7, QFont.Medium))
        p.drawText(14, 18, "NYRA  v2")
        sc = _STATE_STATUS_COLOR.get(self.state, "#3A5270")
        p.setPen(QColor(sc))
        p.setFont(QFont("Cascadia Code", 7))
        p.drawText(14, 32, self.state.upper())


# ── Main window ───────────────────────────────────────────────────────────────

_WAKE_VARIANTS = (
    "nyra", "nira", "nıra", "niora", "nyro", "naira",
    "nera", "neyra", "nyera", "near", "neera", "nara",
    "nyara", "naera", "nyr", "nia", "niya",
)


class NyraWindow(QMainWindow):
    _wake_signal = Signal(str)

    def __init__(
        self,
        memory: Memory,
        stt: STT,
        agent: AgentCore,
        tts: TTSManager,
        screen: ScreenAwareness,
        long_memory: LongTermMemory,
        night_agent=None,
        life_log=None,
        ambient=None,
    ) -> None:
        super().__init__()
        self.memory = memory
        self.stt = stt
        self._agent = agent
        self.tts = tts
        self.screen = screen
        self._long_memory = long_memory
        self._night_agent = night_agent
        self._life_log = life_log
        self._ambient = ambient

        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(4)
        self._intent_store = IntentStore()
        self._behavior = BehaviorTracker()
        self._stt_repair = STTRepair()
        self._improve = SelfImprove()
        self._improve.start_background_loop()
        get_engine()   # start probing Ollama in background

        self._si_route = ""
        self._si_raw = ""

        self._language = memory.session.language or APP_CONFIG.default_language
        self._current_screen = ScreenContext()
        self._voice_inflight = False
        self._speech_paused = False
        self._startup_greeted = False
        self._allow_close = False
        self._is_fullscreen = False
        self._normal_geometry = None
        self._drag_offset = QPoint()
        self._last_voice_text = ""
        self._capture_stop = threading.Event()
        self._bargein_stop = threading.Event()
        self._bargein_mode = False

        self.setWindowTitle("Nyra")
        self.resize(APP_CONFIG.window_width, APP_CONFIG.window_height)
        self.setMinimumSize(480, 520)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._setup_ui()
        self._setup_tray()
        self._apply_styles()
        self._animate_startup()

        self.tts.finished.connect(self._on_tts_done)
        self.tts.error.connect(lambda _: self._finish_turn())
        self._wake_signal.connect(self._on_wake)

        self._start_services()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)

        self.shell = QFrame()
        self.shell.setObjectName("Shell")
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(10, 10, 10, 10)
        shell_layout.setSpacing(12)

        shell_layout.addWidget(self._build_topbar())
        shell_layout.addWidget(self._build_orb(), 1)
        shell_layout.addWidget(self._build_response_bar())

        root_layout.addWidget(self.shell)
        self.setCentralWidget(root)

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(46)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(10)

        brand = QLabel("Nyra")
        brand.setObjectName("BrandLabel")

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusChip")

        self.clock_label = QLabel(datetime.now().strftime("%H:%M"))
        self.clock_label.setObjectName("ClockLabel")

        self._sys_label = QLabel("—")
        self._sys_label.setObjectName("SysLabel")

        min_btn = QPushButton("—")
        full_btn = QPushButton("⛶")
        close_btn = QPushButton("×")
        for btn, cb in [
            (min_btn, self.showMinimized),
            (full_btn, self.toggle_fullscreen),
            (close_btn, self.hide_to_tray),
        ]:
            btn.setObjectName("WinBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(cb)

        layout.addWidget(brand)
        layout.addSpacing(8)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addWidget(self._sys_label)
        layout.addWidget(self.clock_label)
        layout.addSpacing(6)
        layout.addWidget(min_btn)
        layout.addWidget(full_btn)
        layout.addWidget(close_btn)

        QTimer(self, timeout=lambda: self.clock_label.setText(datetime.now().strftime("%H:%M"))).start(30_000)
        return bar

    def _build_orb(self) -> QWidget:
        self.orb = OrbWidget()
        return self.orb

    def _build_response_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("ResponseBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(6)

        self.response_label = QLabel("")
        self.response_label.setObjectName("ResponseLabel")
        self.response_label.setWordWrap(True)
        self.response_label.setAlignment(Qt.AlignCenter)
        self.response_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        scroll = QScrollArea()
        scroll.setObjectName("ResponseScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(self.response_label)
        scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(scroll)
        return bar

    # ── System tray ───────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(self._build_icon(), self)
        self.tray.setToolTip("Nyra")
        menu = QMenu()
        for label, cb in [
            ("Show Nyra", self.reveal),
            ("Hide to tray", self.hide_to_tray),
            (None, None),
            ("Quit", self._quit),
        ]:
            if label is None:
                menu.addSeparator()
            else:
                a = QAction(label, self)
                a.triggered.connect(cb)
                menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.reveal() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        QShortcut(Qt.Key_F11, self).activated.connect(self.toggle_fullscreen)
        QShortcut(Qt.Key_Escape, self).activated.connect(self._exit_fullscreen)
        QShortcut(Qt.CTRL | Qt.SHIFT | Qt.Key_N, self).activated.connect(self._toggle_visibility)

    def _build_icon(self) -> QIcon:
        pix = QPixmap(96, 96)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, 96, 96)
        grad.setColorAt(0.0, QColor("#C4936A"))
        grad.setColorAt(1.0, QColor("#8B5A3C"))
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(10, 10, 76, 76, 12, 12)
        p.end()
        return QIcon(pix)

    # ── Startup ───────────────────────────────────────────────────────────────

    def _animate_startup(self) -> None:
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(380)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._startup_anim = anim

    def _start_services(self) -> None:
        self.wake_detector = WakeDetector()
        if APP_CONFIG.background_listening and self.wake_detector.available:
            self.wake_detector.start(lambda text: self._wake_signal.emit(text))

        self._set_status("Listening" if APP_CONFIG.always_listening else "Ready")

        if self.screen.available:
            QTimer(self, timeout=self._poll_screen).start(APP_CONFIG.screen_poll_ms)

        QTimer(self, timeout=self._refresh_stats).start(3000)

    # ── Wake (from Vosk — optional) ───────────────────────────────────────────

    def _on_wake(self, inline_command: str) -> None:
        if APP_CONFIG.auto_show_on_wake:
            self.reveal()
        if inline_command.strip():
            self._process_input(inline_command, source="voice")

    # ── Voice capture loop ────────────────────────────────────────────────────

    def _queue_voice_capture(self) -> None:
        if self._voice_inflight:
            return
        self._voice_inflight = True
        self._set_state("listening", "Listening")
        self._capture_stop = threading.Event()
        self._voice_thread = VoiceInputThread(self.stt, self._capture_stop, self._language)
        self._voice_thread.transcribed.connect(self._on_transcribed)
        self._voice_thread.failed.connect(self._on_voice_failed)
        self._voice_thread.start()

    def _on_transcribed(self, text: str, language: str) -> None:
        self._voice_inflight = False
        self._language = language
        # Disk write — don't block main thread
        worker = _Runnable(self.memory.set_language, language)
        self._pool.start(worker)

        lower = text.lower().strip()

        bargein = self._bargein_mode
        self._bargein_mode = False

        if not bargein and not any(w in lower for w in _WAKE_VARIANTS):
            self._queue_voice_capture()
            return

        command = lower
        for w in _WAKE_VARIANTS:
            command = command.replace(w, "")
        command = command.strip(" ,.!?")

        command = self._stt_repair.repair(command)

        self._speech_paused = False

        if command:
            self._last_voice_text = command
            # Set state BEFORE any processing so the orb updates instantly
            self._set_state("thinking", "Thinking")
            self._process_input(command, source="voice")
        else:
            ack = "Yes, sir." if self._language == "en" else "Efendim?"
            self._set_response(ack)
            self._set_state("speaking", "Speaking")
            self.tts.speak(ack, self._language)

    def _on_voice_failed(self) -> None:
        self._voice_inflight = False
        QTimer.singleShot(300, self._queue_voice_capture)

    # ── Core routing ──────────────────────────────────────────────────────────

    def _process_input(self, text: str, source: str = "voice") -> None:
        if not text.strip():
            self._finish_turn()
            return

        if text.lower().strip() in {"stop", "pause", "dur", "sus", "kapat"}:
            self._pause_speech()
            return

        self._improve.check_and_flag_correction(text)

        # Fire-and-forget disk I/O — never block the main thread
        if self._life_log:
            worker = _Runnable(self._life_log.log_command, text)
            self._pool.start(worker)
        if self._ambient:
            threading.Thread(target=self._ambient.notify_user_active, daemon=True).start()

        alias = self._improve.resolve(text)
        if alias:
            text = alias

        self._si_raw = text

        result = route(text)
        if result.matched:
            self._execute_action(result, text)
            return

        # Intent store: check frequency-learned patterns before hitting LLM
        intent_match = self._intent_store.match(text)
        if intent_match:
            self._si_route = "intent"
            # Medium confidence (0.45–0.62): transcript might be corrupted → log for repair
            if intent_match.score < MATCH_THRESHOLD:
                self._stt_repair.observe(text, intent_match.group.representative)
            self._execute_learned_intent(intent_match)
            return

        self._run_llm(text)

    def _run_slow_action(self, fn: Callable, *args) -> None:
        """Run a potentially-blocking action in the thread pool; deliver result when done."""
        lang = self._language
        worker = _Runnable(fn, *args)
        worker.signals.finished.connect(self._deliver_response)
        worker.signals.error.connect(
            lambda e: self._deliver_response(
                f"Error: {e}" if lang == "en" else f"Hata: {e}"
            )
        )
        self._pool.start(worker)

    def _execute_action(self, result: RouteResult, original_text: str) -> None:
        self._si_route = result.action   # captured for self-improvement recording
        lang = self._language

        if result.action == "stop":
            self._pause_speech()
            return

        elif result.action == "night_task":
            desc = result.params.get("description", "")
            if self._night_agent:
                self._night_agent.schedule(desc, language=lang)
                if lang == "tr":
                    response = "Görev kuyruğa alındı, efendim. Tamamlanınca bildirim göndereceğim."
                else:
                    response = "Task queued, sir. I'll notify you when it's done."
            else:
                response = "Night agent not available."

        elif result.action == "night_status":
            if self._night_agent:
                response = self._night_agent.status_summary(lang)
            else:
                response = "Night agent not available."

        elif result.action == "log_query":
            question = result.params.get("question", "")
            if self._life_log:
                self._set_response("Searching logs..." if lang == "en" else "Kayıtlar aranıyor...")
                response = self._life_log.query(question, lang)
            else:
                response = "Life log not available."

        elif result.action == "launch_app":
            response = launch_app(result.params.get("app", ""), lang)
        elif result.action == "close_app":
            response = close_app(result.params.get("app", ""), lang)
        elif result.action == "open_web":
            response = open_web(result.params.get("url", ""), lang)
        elif result.action == "remember":
            self.memory.remember(result.params.get("text", ""))
            response = result.response or ("Remembered." if lang == "en" else "Kaydedildi.")
        elif result.action == "resume":
            response = self.memory.resume_summary(lang)
        elif result.action == "show_recent":
            self._run_slow_action(recent_files, lang)
            return
        elif result.action == "switch_mode":
            response = result.response or f"Switching to {result.params.get('mode', '')} mode."
        elif result.action == "volume_up":
            response = volume_up(lang)
        elif result.action == "volume_down":
            response = volume_down(lang)
        elif result.action == "volume_mute":
            response = volume_mute(lang)
        elif result.action == "media_play":
            response = media_play_pause(lang)
        elif result.action == "media_pause":
            response = media_play_pause(lang)
        elif result.action == "media_next":
            response = media_next(lang)
        elif result.action == "media_prev":
            response = media_prev(lang)
        elif result.action == "window_close":
            response = window_close(lang)
        elif result.action == "window_minimize":
            response = window_minimize(lang)
        elif result.action == "window_maximize":
            response = window_maximize(lang)
        elif result.action == "system_lock":
            response = system_lock(lang)
        elif result.action == "system_sleep":
            response = system_sleep(lang)
        elif result.action == "system_wake":
            response = system_wake(lang)
        elif result.action == "system_info":
            self._run_slow_action(system_info, lang)
            return
        elif result.action == "take_screenshot":
            self._run_slow_action(take_screenshot, lang)
            return
        elif result.action == "clipboard_read":
            self._run_slow_action(clipboard_read, lang)
            return
        elif result.action == "clipboard_write":
            response = clipboard_write(result.params.get("text", ""), lang)
        elif result.action == "type_text":
            response = type_text(result.params.get("text", ""), lang)
        elif result.action == "send_hotkey":
            response = send_hotkey(result.params.get("combo", ""), lang)
        elif result.action == "open_folder":
            response = open_folder(result.params.get("name", ""), lang)
        elif result.action == "file_find":
            self._run_slow_action(file_find, result.params.get("name", ""), lang)
            return
        elif result.action == "network_info":
            self._run_slow_action(network_info, lang)
            return
        elif result.action == "process_list":
            self._run_slow_action(process_list, lang)
            return
        elif result.action == "brightness_set":
            response = brightness_set(result.params.get("level", 50), lang)
        elif result.action == "steam_update":
            response = steam_update_games(lang)
        elif result.action == "install_app":
            response = install_app(result.params.get("app", ""), lang)
        elif result.action == "research_task":
            desc = result.params.get("description", text)
            if self._night_agent:
                self._night_agent.schedule(desc, language=lang)
                response = (
                    f"Research queued, sir. I'll notify you when the report is ready."
                    if lang == "en"
                    else f"Araştırma kuyruğa alındı, efendim. Rapor hazır olduğunda bildiririm."
                )
            else:
                response = "Background agent unavailable."
        elif result.action == "price_task":
            desc = result.params.get("description", text)
            if self._night_agent:
                self._night_agent.schedule(desc, language=lang)
                response = (
                    "Price check queued, sir. I'll have results shortly."
                    if lang == "en"
                    else "Fiyat araştırması kuyruğa alındı, efendim."
                )
            else:
                response = "Background agent unavailable."
        elif result.action == "world_news":
            # Fetch in background — browser opens instantly, summary comes after
            loading = (
                "Opening Conflictly and fetching today's briefing, sir."
                if lang == "en"
                else "Conflictly açılıyor, günün özeti hazırlanıyor, efendim."
            )
            self._set_response(loading)
            self._set_state("thinking", "Fetching")
            worker = _Runnable(fetch_world_news, lang)
            worker.signals.finished.connect(
                lambda result: self._deliver_response(result[0])
            )
            worker.signals.error.connect(
                lambda _: self._deliver_response(loading)
            )
            self._pool.start(worker)
            return
        else:
            self._run_llm(original_text)
            return

        self._deliver_response(response)

    def _execute_learned_intent(self, match) -> None:
        """Execute a frequency-learned intent (possibly multi-app)."""
        self._intent_store.hit(match.group)
        lang = self._language
        routes = group_to_routes(match.group)

        for r in routes:
            if r.action == "launch_app":
                launch_app(r.params.get("app", ""), lang)
                self._behavior.record(f"OPEN:{r.params.get('app', '')}")
            elif r.action == "open_web":
                open_web(r.params.get("url", ""), lang)
                self._behavior.record(f"WEB:{r.params.get('url', '')}")

        labels = match.group.app_labels
        if not labels:
            self._deliver_response("Done, sir." if lang == "en" else "Tamam, efendim.")
            return

        if lang == "tr":
            joined = " ve ".join(labels)
            resp = f"{joined} açılıyor, efendim."
        else:
            if len(labels) == 1:
                resp = f"Opening {labels[0]}, sir."
            else:
                *rest, last = labels
                resp = f"Launching {', '.join(rest)} and {last}, sir."

        self._deliver_response(resp)

    def _run_llm(self, text: str) -> None:
        self._stream_buf = ""
        self._stream_dots = 0
        self._stream_first_sent = ""
        self._stream_first_payload = None
        self._set_response("· · ·")
        mode = _detect_mode(text)
        self._set_state("thinking", _mode_status(mode, self._language))
        self._dots_timer = QTimer(self)
        self._dots_timer.timeout.connect(self._animate_dots)
        self._dots_timer.start(420)
        app_ctx = self._current_screen.process_name
        behavior_ctx = self._behavior.context_for_llm()
        long_mem = self._long_memory
        self._llm_thread = LLMThread(self._agent, text, self._language, app_ctx, long_mem, behavior_ctx)
        self._llm_thread.token.connect(self._on_llm_token)
        self._llm_thread.done.connect(self._on_llm_done)
        self._llm_thread.error.connect(self._on_llm_error)
        self._llm_thread.start()
        # RESEARCH can take 120s (6 tool calls × up to 10s each + LLM calls)
        # Other modes get 30s — fail fast so the user isn't left waiting
        from mode_detector import Mode
        timeout_ms = 120_000 if mode == Mode.RESEARCH else 30_000
        self._llm_timer = QTimer(self)
        self._llm_timer.setSingleShot(True)
        self._llm_timer.timeout.connect(self._on_llm_timeout)
        self._llm_timer.start(timeout_ms)

    def _animate_dots(self) -> None:
        self._stream_dots = (self._stream_dots + 1) % 4
        dots = "· " * self._stream_dots or "·"
        if not self._stream_buf:
            self.response_label.setText(dots)

    def _on_llm_timeout(self) -> None:
        if hasattr(self, "_llm_thread") and self._llm_thread.isRunning():
            self._llm_thread.terminate()
            msg = "Ollama yanıt vermedi." if self._language == "tr" else "No response — is Ollama running?"
            self._set_response(msg)
            self._finish_turn()

    def _on_llm_token(self, token: str) -> None:
        if hasattr(self, "_dots_timer"):
            self._dots_timer.stop()
        buf = getattr(self, "_stream_buf", "") + token
        self._stream_buf = buf
        self.response_label.setText(buf[:480])

        # Pre-warm TTS: start synthesizing first sentence while LLM is still generating.
        # By the time _deliver_response fires, the audio file is already on disk.
        if not self._stream_first_sent and APP_CONFIG.voice_enabled:
            sent = self._extract_first_sentence(buf)
            if sent:
                self._stream_first_sent = sent
                lang = self._language
                threading.Thread(
                    target=self._prewarm_sentence,
                    args=(sent, lang),
                    daemon=True,
                ).start()

    def _extract_first_sentence(self, text: str) -> str:
        """Return the first sentence from streaming text once it's complete."""
        import re as _re
        m = _re.search(r"[A-Za-zÀ-ÿ0-9\$€][^.!?]{6,}[.!?](?:\s|$)", text)
        return m.group(0).strip() if m else ""

    def _prewarm_sentence(self, sentence: str, language: str) -> None:
        """Synthesize first sentence in background while LLM is generating."""
        cleaned = _clean_for_tts(sentence)
        if cleaned:
            self._stream_first_payload = self.tts.prepare(cleaned, language)

    def _cancel_llm_timer(self) -> None:
        if hasattr(self, "_llm_timer"):
            self._llm_timer.stop()
        if hasattr(self, "_dots_timer"):
            self._dots_timer.stop()

    def _on_llm_done(self, response: str) -> None:
        self._cancel_llm_timer()
        self._stream_buf = ""
        self._si_route = "llm"   # mark LLM path for self-improvement
        clean_text, actions = parse_llm_actions(response)
        final_text = clean_text or response

        launched: list[RouteResult] = []
        for action in actions:
            if action.action == "launch_app":
                app_name = action.params.get("app", "")
                launch_app(app_name, self._language)
                launched.append(action)
                if self._life_log:
                    worker = _Runnable(self._life_log.log_app_opened, app_name)
                    self._pool.start(worker)
            elif action.action == "close_app":
                close_app(action.params.get("app", ""), self._language)
                launched.append(action)
            elif action.action == "open_web":
                open_web(action.params.get("url", ""), self._language)
                launched.append(action)
            elif action.action == "remember":
                self.memory.remember(action.params.get("text", ""))

        # Frequency-based learning: record (phrase, apps) pair every time
        if launched and self._last_voice_text:
            keys = actions_to_keys(launched)
            self._intent_store.record(self._last_voice_text, keys)
            for k in keys:
                self._behavior.record(k)

        self.memory.add_turn("user", self._last_voice_text)
        self.memory.add_turn("assistant", final_text)
        self._long_memory.store(f"User: {self._last_voice_text}\nNyra: {final_text}")

        self._deliver_response(final_text)

    def _on_llm_error(self, error: str) -> None:
        self._cancel_llm_timer()
        low = error.lower()
        if any(k in low for k in ("connect", "refused", "ollama")):
            msg = "Can't reach Ollama — run: ollama serve"
        else:
            msg = f"Error: {error[:140]}"
        self._set_response(msg)
        self._finish_turn()

    def _deliver_response(self, text: str) -> None:
        # Record interaction for self-improvement (every completed turn)
        if self._si_raw:
            mode = _detect_mode(self._si_raw)
            self._improve.record(
                raw_text=self._si_raw,
                language=self._language,
                route=self._si_route or "unknown",
                action_detail=mode.value,
                response_text=text,
            )
            self._si_raw = ""
            self._si_route = ""

        self._set_response(text)
        if APP_CONFIG.voice_enabled and not self._speech_paused:
            self._set_state("speaking", "Speaking")
            self._start_bargein_monitor()
            cleaned = _clean_for_tts(text)
            self._speak_with_prewarm(cleaned)
        else:
            self._finish_turn()

    def _speak_with_prewarm(self, cleaned: str) -> None:
        """Use pre-warmed first sentence if ready; otherwise fall back to tts.speak()."""
        first_sent_raw = getattr(self, "_stream_first_sent", "")
        first_payload = getattr(self, "_stream_first_payload", None)
        self._stream_first_sent = ""
        self._stream_first_payload = None

        if first_payload and first_sent_raw:
            cleaned_first = _clean_for_tts(first_sent_raw)
            if cleaned.startswith(cleaned_first):
                rest = cleaned[len(cleaned_first):].strip()
                self.tts._stopping = False
                self.tts._sentence_language = self._language
                self.tts._sentence_queue = self.tts._split_sentences(rest) if rest else []
                self.tts._next_sentence_ready.emit(first_payload)
                return

        self.tts.speak(cleaned, self._language)

    def _start_bargein_monitor(self) -> None:
        self._bargein_stop = threading.Event()
        self._bargein_monitor = BargeinMonitor(self._bargein_stop)
        self._bargein_monitor.detected.connect(self._on_bargein)
        self._bargein_monitor.start()

    def _stop_bargein_monitor(self) -> None:
        self._bargein_stop.set()

    def _on_bargein(self) -> None:
        self._stop_bargein_monitor()
        self.tts.stop()
        self._bargein_mode = True
        self._voice_inflight = False
        self._set_state("listening", "Listening")
        QTimer.singleShot(80, self._queue_voice_capture)

    # ── TTS callbacks ─────────────────────────────────────────────────────────

    def _on_tts_done(self) -> None:
        self._stop_bargein_monitor()
        self._set_state("idle", "Listening")
        QTimer.singleShot(50, self._queue_voice_capture)

    def _finish_turn(self) -> None:
        self._voice_inflight = False
        self._set_state("idle", "Listening")
        QTimer.singleShot(80, self._queue_voice_capture)

    def _pause_speech(self) -> None:
        self._speech_paused = True
        self.tts.stop()
        self._set_state("idle", "Paused")

    # ── Status ────────────────────────────────────────────────────────────────

    def _set_state(self, orb_state: str, status_text: str) -> None:
        self.status_label.setText(status_text)
        self.orb.set_state(orb_state)
        col = _STATE_STATUS_COLOR.get(orb_state, "#3A5270")
        self.status_label.setStyleSheet(
            f"color: {col}; font-family: 'Cascadia Code', monospace; "
            f"font-size: 9px; letter-spacing: 0.8px; padding: 3px 10px; "
            f"background: rgba(255,255,255,0.025); "
            f"border: 1px solid {col}44; border-radius: 5px;"
        )

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _set_response(self, text: str) -> None:
        self.response_label.setText(text[:480])

    # ── Screen & stats ────────────────────────────────────────────────────────

    def _poll_screen(self) -> None:
        worker = _Runnable(self.screen.get_context)
        worker.signals.finished.connect(self._on_screen_context)
        self._pool.start(worker)

    def _on_screen_context(self, ctx: ScreenContext) -> None:
        self._current_screen = ctx
        self.memory.set_app(ctx.process_name)

    def _refresh_stats(self) -> None:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent

            parts: list[str] = []
            if _NVML_OK:
                mem = _nvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                vram = mem.used / mem.total * 100
                parts = [f"CPU {cpu:.0f}%", f"RAM {ram:.0f}%", f"VRAM {vram:.0f}%"]
            else:
                parts = [f"CPU {cpu:.0f}%", f"RAM {ram:.0f}%"]

            if self._night_agent:
                summary = self._night_agent.status_summary(self._language)
                if "running" in summary or "çalışıyor" in summary:
                    parts.append("⬤ BG")

            self._sys_label.setText("  ".join(parts))
        except Exception:
            pass

    # ── Greeting ──────────────────────────────────────────────────────────────

    def _maybe_greet(self) -> None:
        if self._startup_greeted:
            return
        self._startup_greeted = True

        # Check for overnight completed tasks
        briefing = ""
        if self._night_agent:
            briefing = self._night_agent.morning_briefing(self._language)
            if briefing:
                self._night_agent.clear_done()

        base = (
            "All systems online. Nyra active. Standing by, sir."
            if self._language == "en"
            else "Tüm sistemler aktif. Nyra hazır. Emirlerinizi bekliyorum, efendim."
        )
        greeting = f"{base}\n{briefing}".strip() if briefing else base

        self._set_response(greeting)
        if APP_CONFIG.voice_enabled:
            QTimer.singleShot(150, lambda: self.tts.speak(greeting, self._language))
            QTimer.singleShot(10_000, self._ensure_listening)
        else:
            QTimer.singleShot(500, self._queue_voice_capture)

    def _ensure_listening(self) -> None:
        if not self._voice_inflight and APP_CONFIG.always_listening:
            self._queue_voice_capture()

    def _ambient_speak(self, text: str, language: str) -> None:
        """Called by AmbientCopilot from a background thread — marshal to Qt main thread."""
        from PySide6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(
            self, "_do_ambient_speak",
            Qt.ConnectionType.QueuedConnection,
        )
        self._ambient_text = text
        self._ambient_lang = language

    def _do_ambient_speak(self) -> None:
        text = getattr(self, "_ambient_text", "")
        lang = getattr(self, "_ambient_lang", "en")
        if text:
            self._set_response(text)
            self._deliver_response(text)

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
* { outline: none; }

QWidget {
    color: #A8BFCF;
    font-family: 'Segoe UI Variable', 'Segoe UI', system-ui;
    font-size: 12px;
    background: transparent;
}

#Shell {
    background: qlineargradient(x1:0, y1:0, x2:0.6, y2:1,
        stop:0 #030509,
        stop:0.4 #05070E,
        stop:1   #030508);
    border: 1px solid rgba(255,255,255,0.055);
    border-radius: 18px;
}

#TopBar {
    background: rgba(4, 6, 12, 0.96);
    border: 1px solid rgba(255,255,255,0.045);
    border-radius: 11px;
}

#BrandLabel {
    font-size: 18px;
    font-weight: 700;
    color: #D4E2F2;
    letter-spacing: 0.6px;
}

#StatusChip {
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 9px;
    letter-spacing: 0.8px;
    padding: 3px 10px;
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.04);
    border-radius: 5px;
}

#ClockLabel {
    color: #2E4560;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}

#SysLabel {
    color: #1E3048;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 8px;
    letter-spacing: 0.3px;
}

#WinBtn {
    padding: 3px 10px;
    background: rgba(255,255,255,0.018);
    border: 1px solid rgba(255,255,255,0.035);
    border-radius: 5px;
    color: #253545;
    font-size: 13px;
    min-width: 24px;
}
#WinBtn:hover {
    background: rgba(255,255,255,0.065);
    color: #6A96B8;
    border-color: rgba(255,255,255,0.08);
}

#ResponseBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(8,12,24,0.75),
        stop:1 rgba(4,7,16,0.90));
    border: 1px solid rgba(255,255,255,0.042);
    border-top: 1px solid rgba(255,255,255,0.065);
    border-radius: 12px;
    min-height: 70px;
    max-height: 160px;
}

#ResponseScroll {
    background: transparent;
    border: none;
}

#ResponseLabel {
    color: #8AACBE;
    font-size: 13px;
    line-height: 1.7;
    padding: 4px 2px;
    background: transparent;
    qproperty-alignment: AlignCenter;
}

QScrollBar:vertical {
    background: transparent;
    width: 4px;
    margin: 4px 2px;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.08);
    border-radius: 2px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }

QMenu {
    background: #060A14;
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 10px;
    padding: 5px;
    color: #A8BFCF;
    font-size: 12px;
}
QMenu::item { padding: 8px 18px; border-radius: 6px; }
QMenu::item:selected {
    background: rgba(80,110,200,0.18);
    color: #90B4E8;
}
QMenu::separator {
    background: rgba(255,255,255,0.05);
    height: 1px;
    margin: 4px 10px;
}
        """)

    # ── Window events ─────────────────────────────────────────────────────────

    def show_and_greet(self) -> None:
        self.reveal()
        if not self.stt.ready:
            self._set_state("thinking", "Loading AI model…")
            self._poll_stt_ready()
        else:
            QTimer.singleShot(120, self._maybe_greet)

    def _poll_stt_ready(self) -> None:
        if self.stt.ready:
            self._set_state("idle", "Listening")
            QTimer.singleShot(120, self._maybe_greet)
        elif "error" in self.stt.status:
            self._set_state("idle", "Listening")
            QTimer.singleShot(120, self._maybe_greet)
        else:
            QTimer.singleShot(1000, self._poll_stt_ready)

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.reveal()

    def hide_to_tray(self) -> None:
        self.hide()
        self.tray.showMessage("Nyra", "Running in background.", QSystemTrayIcon.Information, 1200)

    def reveal(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_fullscreen(self) -> None:
        if self._is_fullscreen:
            self._is_fullscreen = False
            self.showNormal()
            if self._normal_geometry:
                self.setGeometry(self._normal_geometry)
        else:
            self._normal_geometry = self.geometry()
            self._is_fullscreen = True
            self.showFullScreen()

    def _exit_fullscreen(self) -> None:
        if self._is_fullscreen:
            self.toggle_fullscreen()

    def _quit(self) -> None:
        self._allow_close = True
        self._capture_stop.set()
        self._bargein_stop.set()
        self.wake_detector.stop()
        self.close()

    def closeEvent(self, event) -> None:
        if not self._allow_close:
            event.ignore()
            self.hide_to_tray()
            return
        self.tray.hide()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(100, self._maybe_greet)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if not self._is_fullscreen and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.toggle_fullscreen()


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Nyra")
    app.setFont(QFont("Segoe UI", 9))
    return app
