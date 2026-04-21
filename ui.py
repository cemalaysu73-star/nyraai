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
    QEasingCurve, QPoint, QPropertyAnimation, QRunnable,
    QThread, QThreadPool, QTimer, Qt, QObject, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QLinearGradient,
    QPainter, QPen, QPixmap, QRadialGradient, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QSizePolicy,
    QSystemTrayIcon, QVBoxLayout, QMenu, QWidget,
)

import vad as _vad
from actions import (
    launch_app, open_web, recent_files, close_app,
    volume_up, volume_down, volume_mute, volume_set,
    media_play_pause, media_next, media_prev, media_stop,
    window_close, window_minimize, window_maximize,
    system_lock, system_sleep,
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

    def __init__(self, stt: STT, stop_event: threading.Event) -> None:
        super().__init__()
        self._stt = stt
        self._stop_event = stop_event

    def run(self) -> None:
        raw = capture_command(stop_event=self._stop_event)
        if not raw or self._stop_event.is_set():
            self.failed.emit()
            return
        result = self._stt.transcribe(raw)
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

class OrbWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.state = "idle"
        self.phase = 0.0
        self._points = self._build_points()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def _build_points(self):
        random.seed(7)
        pts = []
        for _ in range(240):
            x, y = random.random(), random.random()
            r = random.randint(1, 3)
            offset = random.random() * math.pi * 2
            # ~7% become bright "sparkle" stars
            bright = random.random() < 0.07
            pts.append((x, y, r, offset, bright))
        return pts

    def _tick(self) -> None:
        self.phase = (self.phase + 0.055) % (math.pi * 2)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        bg = QLinearGradient(0, 0, rect.width(), rect.height())
        bg.setColorAt(0.0, QColor("#050507"))
        bg.setColorAt(0.5, QColor("#0C0A0D"))
        bg.setColorAt(1.0, QColor("#060608"))
        painter.fillRect(rect, bg)

        for x_n, y_n, r, offset, bright in self._points:
            x = int(16 + x_n * (rect.width() - 32))
            y = int(16 + y_n * (rect.height() - 32))
            twinkle = math.sin(self.phase * 1.4 + offset)
            if bright:
                alpha = min(255, 65 + int((twinkle + 1) * 95))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 250, 245, alpha))
                painter.drawEllipse(QPoint(x, y), r + 1, r + 1)
                span = r * 4
                painter.setPen(QPen(QColor(255, 248, 240, max(0, alpha // 4)), 1))
                painter.drawLine(x - span, y, x + span, y)
                painter.drawLine(x, y - span, x, y + span)
            else:
                alpha = 5 + int((twinkle + 1) * 17)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(210, 228, 255, alpha))
                painter.drawEllipse(QPoint(x, y), r, r)

        cx = int(rect.width() * 0.5)
        cy = int(rect.height() * 0.5)
        center = QPoint(cx, cy)
        radius = min(rect.width(), rect.height()) * 0.22

        amp = {"idle": 3, "listening": 10, "thinking": 6, "speaking": 14, "wake": 16}.get(self.state, 3)
        waveform_y = rect.height() - 28
        painter.setPen(QPen(QColor(255, 210, 180, 90), 1.4))
        last = QPoint(14, waveform_y)
        for x in range(14, rect.width() - 14, 7):
            wave = math.sin(self.phase * 4.0 + x * 0.044) + math.cos(self.phase * 2.4 + x * 0.021)
            cur = QPoint(x, int(waveform_y + wave * amp))
            painter.drawLine(last, cur)
            last = cur

        beam = QLinearGradient(cx, rect.top(), cx, rect.bottom())
        beam.setColorAt(0.0, QColor(255, 245, 235, 0))
        beam.setColorAt(0.5, QColor(255, 240, 224, 22))
        beam.setColorAt(1.0, QColor(255, 245, 235, 0))
        painter.fillRect(cx - 22, rect.top(), 44, rect.height(), beam)

        outer = QRadialGradient(center, radius * 2.0)
        speaking_boost = 30 if self.state == "speaking" else 0
        outer.setColorAt(0.0, QColor(255, 200, 170, 80 + speaking_boost))
        outer.setColorAt(0.5, QColor(244, 172, 132, 28))
        outer.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(outer)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, int(radius * 2.1), int(radius * 2.1))

        core = QRadialGradient(cx - radius * 0.18, cy - radius * 0.22, radius * 1.05)
        core.setColorAt(0.0, QColor("#FFF2E8"))
        core.setColorAt(0.22, QColor("#D8B4A0"))
        core.setColorAt(0.58, QColor("#4E3D3B"))
        core.setColorAt(1.0, QColor("#161416"))
        painter.setBrush(core)
        painter.setPen(QPen(QColor(255, 228, 210, 100), 1.1))
        painter.drawEllipse(center, int(radius), int(radius))

        for scale_x, scale_y, color, rot in [
            (1.55, 0.40, QColor(244, 200, 175, 120), self.phase * 32),
            (1.32, 0.66, QColor(222, 210, 205, 90), -self.phase * 26),
            (1.80, 0.84, QColor(255, 165, 130, 55), self.phase * 17),
        ]:
            painter.save()
            painter.translate(center)
            painter.rotate(rot * 57.2958)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(color, 1.1))
            painter.drawEllipse(QPoint(0, 0), int(radius * scale_x), int(radius * scale_y))
            painter.restore()

        painter.setPen(QColor(222, 210, 200, 140))
        painter.setFont(QFont("Segoe UI Variable", 8, QFont.Medium))
        painter.drawText(14, 20, "NYRA")
        painter.setPen(QColor(255, 165, 120, 130))
        painter.drawText(14, 34, self.state.upper())


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
        get_engine()   # start probing Ollama in background

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

        QTimer(self, timeout=lambda: self.clock_label.setText(datetime.now().strftime("%H:%M"))).start(10_000)
        return bar

    def _build_orb(self) -> QWidget:
        self.orb = OrbWidget()
        return self.orb

    def _build_response_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("ResponseBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)

        self.response_label = QLabel("")
        self.response_label.setObjectName("ResponseLabel")
        self.response_label.setWordWrap(True)
        self.response_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.response_label)
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
        self._voice_thread = VoiceInputThread(self.stt, self._capture_stop)
        self._voice_thread.transcribed.connect(self._on_transcribed)
        self._voice_thread.failed.connect(self._on_voice_failed)
        self._voice_thread.start()

    def _on_transcribed(self, text: str, language: str) -> None:
        self._voice_inflight = False
        self._language = language
        self.memory.set_language(language)

        lower = text.lower().strip()

        bargein = self._bargein_mode
        self._bargein_mode = False

        # Wake word required unless this is a barge-in
        if not bargein and not any(w in lower for w in _WAKE_VARIANTS):
            QTimer.singleShot(100, self._queue_voice_capture)
            return

        # Strip wake word, keep the actual command
        command = lower
        for w in _WAKE_VARIANTS:
            command = command.replace(w, "")
        command = command.strip(" ,.!?")

        # STT repair: fix known transcription errors before routing
        command = self._stt_repair.repair(command)

        self._speech_paused = False

        if command:
            self._last_voice_text = command
            self._process_input(command, source="voice")
        else:
            # Only "nyra" was said — acknowledge
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

        # Log every command + notify ambient that user is active
        if self._life_log:
            self._life_log.log_command(text)
        if self._ambient:
            self._ambient.notify_user_active()

        self._set_state("thinking", "Thinking")
        result = route(text)
        if result.matched:
            self._execute_action(result, text)
            return

        # Intent store: check frequency-learned patterns before hitting LLM
        intent_match = self._intent_store.match(text)
        if intent_match:
            # Medium confidence (0.45–0.62): transcript might be corrupted → log for repair
            if intent_match.score < MATCH_THRESHOLD:
                self._stt_repair.observe(text, intent_match.group.representative)
            self._execute_learned_intent(intent_match)
            return

        self._run_llm(text)

    def _execute_action(self, result: RouteResult, original_text: str) -> None:
        lang = self._language

        if result.action == "stop":
            self._pause_speech()
            return

        elif result.action == "night_task":
            desc = result.params.get("description", "")
            if self._night_agent:
                self._night_agent.schedule(desc)
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
            response = recent_files(lang)
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
        thinking_msg = "Düşünüyorum..." if self._language == "tr" else "Thinking..."
        self._set_response(thinking_msg)
        app_ctx = self._current_screen.process_name
        # Inject behavior context so LLM knows user's habits
        behavior_ctx = self._behavior.context_for_llm()
        long_mem = self._long_memory
        self._llm_thread = LLMThread(self._agent, text, self._language, app_ctx, long_mem, behavior_ctx)
        self._llm_thread.token.connect(self._on_llm_token)
        self._llm_thread.done.connect(self._on_llm_done)
        self._llm_thread.error.connect(self._on_llm_error)
        self._llm_thread.start()
        # Timeout: if Ollama hangs, recover after 45s
        self._llm_timer = QTimer(self)
        self._llm_timer.setSingleShot(True)
        self._llm_timer.timeout.connect(self._on_llm_timeout)
        self._llm_timer.start(45_000)

    def _on_llm_timeout(self) -> None:
        if hasattr(self, "_llm_thread") and self._llm_thread.isRunning():
            self._llm_thread.terminate()
            msg = "Ollama yanıt vermedi." if self._language == "tr" else "No response — is Ollama running?"
            self._set_response(msg)
            self._finish_turn()

    def _on_llm_token(self, _token: str) -> None:
        pass  # streaming tokens not displayed in orb-only mode

    def _cancel_llm_timer(self) -> None:
        if hasattr(self, "_llm_timer"):
            self._llm_timer.stop()

    def _on_llm_done(self, response: str) -> None:
        self._cancel_llm_timer()
        clean_text, actions = parse_llm_actions(response)
        final_text = clean_text or response

        launched: list[RouteResult] = []
        for action in actions:
            if action.action == "launch_app":
                app_name = action.params.get("app", "")
                launch_app(app_name, self._language)
                launched.append(action)
                if self._life_log:
                    self._life_log.log_app_opened(app_name)
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
        self._set_response(text)
        if APP_CONFIG.voice_enabled and not self._speech_paused:
            self._set_state("speaking", "Speaking")
            self._start_bargein_monitor()
            self.tts.speak(text, self._language)
        else:
            self._finish_turn()

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
        QTimer.singleShot(80, self._queue_voice_capture)

    def _finish_turn(self) -> None:
        self._voice_inflight = False
        self._set_state("idle", "Listening")
        QTimer.singleShot(300, self._queue_voice_capture)

    def _pause_speech(self) -> None:
        self._speech_paused = True
        self.tts.stop()
        self._set_state("idle", "Paused")

    # ── Status ────────────────────────────────────────────────────────────────

    def _set_state(self, orb_state: str, status_text: str) -> None:
        self.status_label.setText(status_text)
        self.orb.set_state(orb_state)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _set_response(self, text: str) -> None:
        self.response_label.setText(text[:320])

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
            if _NVML_OK:
                mem = _nvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                vram = mem.used / mem.total * 100
                self._sys_label.setText(f"CPU {cpu:.0f}%  RAM {ram:.0f}%  VRAM {vram:.0f}%")
            else:
                self._sys_label.setText(f"CPU {cpu:.0f}%  RAM {ram:.0f}%")
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
    color: #B8C5D6;
    font-family: 'Segoe UI Variable', 'Segoe UI', system-ui;
    font-size: 12px;
    background: transparent;
}
#Shell {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #060810, stop:0.5 #080912, stop:1 #060810);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 16px;
}
#TopBar {
    background: rgba(6, 8, 16, 0.95);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px;
}
#BrandLabel {
    font-size: 19px; font-weight: 700; color: #E0E8F4; letter-spacing: 0.4px;
}
#StatusChip {
    color: #4A5E78;
    font-family: 'Cascadia Code', monospace;
    font-size: 10px;
    letter-spacing: 0.4px;
    padding: 2px 8px;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.04);
    border-radius: 5px;
}
#ClockLabel {
    color: #3A4E66; font-family: 'Cascadia Code', monospace; font-size: 12px; font-weight: 600;
}
#SysLabel {
    color: #2A3B52; font-family: 'Cascadia Code', monospace; font-size: 9px;
}
#WinBtn {
    padding: 3px 10px;
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.04);
    border-radius: 5px;
    color: #2D3B52;
    font-size: 13px;
    min-width: 26px;
}
#WinBtn:hover { background: rgba(255,255,255,0.07); color: #7090A8; }
#ResponseBar {
    background: rgba(5, 7, 14, 0.60);
    border: 1px solid rgba(255,255,255,0.04);
    border-radius: 10px;
    min-height: 48px;
    max-height: 120px;
}
#ResponseLabel {
    color: #7A9AAB;
    font-size: 12px;
    line-height: 1.6;
}
QMenu {
    background: #080C14;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 9px;
    padding: 4px;
    color: #B8C5D6;
}
QMenu::item { padding: 7px 16px; border-radius: 5px; }
QMenu::item:selected { background: rgba(99,102,241,0.15); color: #A5B4FC; }
QMenu::separator { background: rgba(255,255,255,0.05); height: 1px; margin: 4px 8px; }
        """)

    # ── Window events ─────────────────────────────────────────────────────────

    def show_and_greet(self) -> None:
        self.reveal()
        QTimer.singleShot(120, self._maybe_greet)

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
