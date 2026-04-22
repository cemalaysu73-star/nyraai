from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config import DATA_DIR
import hardware as hw

SETUP_DONE = DATA_DIR / "setup_done"
USER_CONFIG = DATA_DIR / "user_config.json"

_BG     = "#0a0c14"
_PANEL  = "#111520"
_ACCENT = "#4fc3f7"
_GREEN  = "#4caf50"
_RED    = "#ef5350"
_TEXT   = "#e0e8f0"
_DIM    = "#5a6a7a"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _style_btn(btn: QPushButton, color: str = _ACCENT) -> None:
    btn.setFixedHeight(44)
    btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Medium))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background: {color}22;
            color: {color};
            border: 1px solid {color};
            border-radius: 6px;
            padding: 0 24px;
        }}
        QPushButton:hover {{ background: {color}44; }}
        QPushButton:disabled {{
            color: {_DIM}; border-color: {_DIM}; background: transparent;
        }}
        """
    )


def _label(text: str, size: int = 11, color: str = _TEXT, bold: bool = False) -> QLabel:
    lbl = QLabel(text)
    font = QFont("Segoe UI", size)
    if bold:
        font.setWeight(QFont.Weight.Bold)
    lbl.setFont(font)
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    lbl.setWordWrap(True)
    return lbl


def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {_DIM}44;")
    return line


# ── Background workers ────────────────────────────────────────────────────────

class PullThread(QThread):
    progress = Signal(str)
    done = Signal(bool)

    def __init__(self, model: str) -> None:
        super().__init__()
        self._model = model

    def run(self) -> None:
        try:
            proc = subprocess.Popen(
                ["ollama", "pull", self._model],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self.progress.emit(line)
            proc.wait()
            self.done.emit(proc.returncode == 0)
        except Exception as exc:
            self.progress.emit(f"Error: {exc}")
            self.done.emit(False)


class GroqTestThread(QThread):
    result = Signal(bool, str)   # ok, message

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._key = api_key

    def run(self) -> None:
        try:
            import urllib.request
            import json as _json
            body = _json.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 4,
            }).encode()
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            self.result.emit(True, "API key verified.")
        except Exception as exc:
            msg = str(exc)
            if "401" in msg or "invalid_api_key" in msg.lower():
                self.result.emit(False, "Invalid API key.")
            elif "403" in msg:
                self.result.emit(False, "Key has no permissions.")
            else:
                self.result.emit(False, f"Connection error: {msg[:80]}")


# ── Main wizard window ────────────────────────────────────────────────────────

class SetupWizard(QWidget):
    finished = Signal()
    _detected = Signal(object, object)
    _detect_failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Nyra — First-Time Setup")
        self.setFixedSize(700, 600)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self._apply_palette()
        self._profile: hw.HardwareProfile | None = None
        self._rec: hw.ModelRecommendation | None = None
        self._pull_thread: PullThread | None = None
        self._groq_thread: GroqTestThread | None = None
        self._groq_verified = False
        self._detected.connect(self._on_detected)
        self._detect_failed.connect(self._on_detect_error)
        self._build_ui()
        self._start_detect()

    def _apply_palette(self) -> None:
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(_BG))
        pal.setColor(QPalette.ColorRole.Base, QColor(_PANEL))
        pal.setColor(QPalette.ColorRole.Text, QColor(_TEXT))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background: {_BG};")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 32)
        root.setSpacing(0)

        # Title
        title_row = QHBoxLayout()
        title = _label("NYRA  SETUP", 18, _ACCENT, bold=True)
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        subtitle = _label("First-time configuration wizard", 9, _DIM)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(subtitle)
        root.addLayout(title_row)
        root.addSpacing(8)
        root.addWidget(_hline())
        root.addSpacing(18)

        # Step 1 — Hardware
        root.addWidget(_label("SYSTEM ANALYSIS", 9, _DIM, bold=True))
        root.addSpacing(8)
        self._hw_panel = self._make_panel()
        hw_layout = QVBoxLayout(self._hw_panel)
        hw_layout.setContentsMargins(16, 12, 16, 12)
        hw_layout.setSpacing(6)
        self._hw_status = _label("Scanning hardware…", 11, _DIM)
        hw_layout.addWidget(self._hw_status)
        root.addWidget(self._hw_panel)
        root.addSpacing(16)

        # Step 2 — Recommendation
        root.addWidget(_label("RECOMMENDED CONFIGURATION", 9, _DIM, bold=True))
        root.addSpacing(8)
        self._rec_panel = self._make_panel()
        rec_layout = QVBoxLayout(self._rec_panel)
        rec_layout.setContentsMargins(16, 12, 16, 12)
        rec_layout.setSpacing(6)
        self._rec_status = _label("Waiting for hardware scan…", 11, _DIM)
        rec_layout.addWidget(self._rec_status)
        root.addWidget(self._rec_panel)
        root.addSpacing(16)

        # Step 3 — API key (hidden until needed)
        self._api_section = QWidget()
        self._api_section.setVisible(False)
        api_outer = QVBoxLayout(self._api_section)
        api_outer.setContentsMargins(0, 0, 0, 0)
        api_outer.setSpacing(8)
        api_outer.addWidget(_label("GROQ API KEY", 9, _DIM, bold=True))
        api_panel = self._make_panel()
        api_inner = QVBoxLayout(api_panel)
        api_inner.setContentsMargins(16, 14, 16, 14)
        api_inner.setSpacing(10)
        api_inner.addWidget(_label(
            "Nyra uses Groq's free API for AI responses. Get a free key at console.groq.com",
            10, _DIM
        ))
        key_row = QHBoxLayout()
        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("Paste your Groq API key here…")
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setFixedHeight(38)
        self._key_input.setFont(QFont("Segoe UI", 10))
        self._key_input.setStyleSheet(
            f"background: {_BG}; color: {_TEXT}; border: 1px solid {_DIM}66; "
            "border-radius: 6px; padding: 0 12px;"
        )
        self._key_input.textChanged.connect(self._on_key_changed)
        self._btn_test = QPushButton("Test")
        _style_btn(self._btn_test, _ACCENT)
        self._btn_test.setFixedWidth(80)
        self._btn_test.setEnabled(False)
        self._btn_test.clicked.connect(self._test_groq_key)
        key_row.addWidget(self._key_input)
        key_row.addWidget(self._btn_test)
        api_inner.addLayout(key_row)
        self._key_status = _label("", 10, _DIM)
        api_inner.addWidget(self._key_status)
        api_outer.addWidget(api_panel)
        root.addWidget(self._api_section)
        root.addSpacing(16)

        # Step 4 — Log (Ollama)
        self._log_section = QWidget()
        log_outer = QVBoxLayout(self._log_section)
        log_outer.setContentsMargins(0, 0, 0, 0)
        log_outer.setSpacing(6)
        log_outer.addWidget(_label("DOWNLOAD LOG", 9, _DIM, bold=True))
        self._log = _label("", 9, _DIM)
        self._log.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._log.setFixedHeight(40)
        self._log.setStyleSheet(
            f"color: {_DIM}; background: {_PANEL}; border: 1px solid {_DIM}33; "
            "border-radius: 4px; padding: 6px 10px;"
        )
        log_outer.addWidget(self._log)
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.setFixedHeight(4)
        self._bar.setVisible(False)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {_PANEL}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {_ACCENT}; border-radius: 2px; }}"
        )
        log_outer.addWidget(self._bar)
        root.addWidget(self._log_section)
        root.addStretch()

        # Buttons
        root.addWidget(_hline())
        root.addSpacing(14)
        btn_row = QHBoxLayout()

        self._btn_skip = QPushButton("Skip Setup")
        _style_btn(self._btn_skip, _DIM)
        self._btn_skip.clicked.connect(self._skip)

        self._btn_install = QPushButton("Install Ollama")
        _style_btn(self._btn_install, _RED)
        self._btn_install.setVisible(False)
        self._btn_install.clicked.connect(self._open_ollama_site)

        self._btn_groq_link = QPushButton("Get Free API Key  ↗")
        _style_btn(self._btn_groq_link, _ACCENT)
        self._btn_groq_link.setVisible(False)
        self._btn_groq_link.clicked.connect(self._open_groq_site)

        self._btn_pull = QPushButton("Download Model")
        _style_btn(self._btn_pull, _ACCENT)
        self._btn_pull.setEnabled(False)
        self._btn_pull.clicked.connect(self._pull_model)

        self._btn_launch = QPushButton("Launch Nyra  →")
        _style_btn(self._btn_launch, _GREEN)
        self._btn_launch.setVisible(False)
        self._btn_launch.clicked.connect(self._launch)

        btn_row.addWidget(self._btn_skip)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_install)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._btn_groq_link)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._btn_pull)
        btn_row.addWidget(self._btn_launch)
        root.addLayout(btn_row)

    def _make_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(
            f"background: {_PANEL}; border: 1px solid {_DIM}44; border-radius: 8px;"
        )
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return panel

    # ── Hardware detection ────────────────────────────────────────────────────

    def _start_detect(self) -> None:
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self) -> None:
        try:
            profile = hw.detect()
            rec = hw.recommend(profile)
            self._detected.emit(profile, rec)
        except Exception as exc:
            self._detect_failed.emit(str(exc))

    def _on_detected(self, profile: hw.HardwareProfile, rec: hw.ModelRecommendation) -> None:
        self._profile = profile
        self._rec = rec
        self._show_hardware(profile)
        self._show_recommendation(rec)

    def _on_detect_error(self, msg: str) -> None:
        self._hw_status.setText(f"Detection failed: {msg}")
        self._hw_status.setStyleSheet(f"color: {_RED}; background: transparent;")

    def _show_hardware(self, p: hw.HardwareProfile) -> None:
        self._hw_status.setText(
            f"CPU  {p.cpu_cores} cores    RAM  {p.ram_gb:.1f} GB    "
            f"GPU  {p.gpu_name}    VRAM  {p.vram_gb:.1f} GB"
        )
        self._hw_status.setStyleSheet(f"color: {_TEXT}; background: transparent;")
        layout = self._hw_panel.layout()
        ollama_row = _label(
            f"Ollama:  {'Detected ✓' if p.ollama_installed else 'Not found — install for offline AI'}",
            10,
            _GREEN if p.ollama_installed else _DIM,
        )
        layout.addWidget(ollama_row)
        if not p.ollama_installed:
            self._btn_install.setVisible(True)

    def _show_recommendation(self, r: hw.ModelRecommendation) -> None:
        layout = self._rec_panel.layout()
        self._rec_status.setText(r.description)
        self._rec_status.setStyleSheet(f"color: {_ACCENT}; background: transparent;")
        layout.addWidget(_label(f"LLM model:   {r.llm_model}", 10, _TEXT))
        layout.addWidget(_label(f"Code model:  {r.llm_code_model}", 10, _TEXT))
        layout.addWidget(_label(f"STT model:   faster-whisper/{r.stt_model}", 10, _TEXT))

        if r.needs_cloud:
            layout.addWidget(_label("Recommended: Groq cloud API (free tier, fastest)", 9, _DIM))
            self._btn_pull.setVisible(False)
            self._btn_groq_link.setVisible(True)
            self._api_section.setVisible(True)
            # Check if key already saved
            existing = self._load_existing_groq_key()
            if existing:
                self._key_input.setText(existing)
        else:
            if self._profile and self._profile.ollama_installed:
                self._btn_pull.setEnabled(True)
            self._btn_pull.setText(f"Download  {r.llm_model}")

    # ── Groq key handling ─────────────────────────────────────────────────────

    def _load_existing_groq_key(self) -> str:
        secrets = DATA_DIR / "secrets.env"
        if not secrets.exists():
            return ""
        for line in secrets.read_text().splitlines():
            if line.startswith("GROQ_API_KEY="):
                return line.partition("=")[2].strip()
        return ""

    def _on_key_changed(self, text: str) -> None:
        ok = len(text.strip()) > 20
        self._btn_test.setEnabled(ok)
        if self._groq_verified:
            self._groq_verified = False
            self._btn_launch.setVisible(False)
            self._key_status.setText("")

    def _test_groq_key(self) -> None:
        key = self._key_input.text().strip()
        if not key:
            return
        self._btn_test.setEnabled(False)
        self._btn_test.setText("…")
        self._key_status.setText("Testing key…")
        self._key_status.setStyleSheet(f"color: {_DIM}; background: transparent;")
        self._groq_thread = GroqTestThread(key)
        self._groq_thread.result.connect(self._on_groq_result)
        self._groq_thread.start()

    def _on_groq_result(self, ok: bool, message: str) -> None:
        self._btn_test.setText("Test")
        self._btn_test.setEnabled(True)
        color = _GREEN if ok else _RED
        self._key_status.setText(("✓  " if ok else "✗  ") + message)
        self._key_status.setStyleSheet(f"color: {color}; background: transparent;")
        if ok:
            self._groq_verified = True
            self._save_groq_key(self._key_input.text().strip())
            if self._rec:
                self._save_config(self._rec)
            self._btn_launch.setVisible(True)

    def _save_groq_key(self, key: str) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        secrets = DATA_DIR / "secrets.env"
        lines = []
        found = False
        if secrets.exists():
            for line in secrets.read_text().splitlines():
                if line.startswith("GROQ_API_KEY="):
                    lines.append(f"GROQ_API_KEY={key}")
                    found = True
                else:
                    lines.append(line)
        if not found:
            lines.append(f"GROQ_API_KEY={key}")
        secrets.write_text("\n".join(lines) + "\n")

    # ── Ollama model pull ─────────────────────────────────────────────────────

    def _pull_model(self) -> None:
        if self._rec is None:
            return
        self._btn_pull.setEnabled(False)
        self._btn_skip.setEnabled(False)
        self._bar.setVisible(True)
        self._log.setText("Starting download…")
        self._pull_thread = PullThread(self._rec.llm_model)
        self._pull_thread.progress.connect(lambda l: self._log.setText(l[-120:]))
        self._pull_thread.done.connect(self._on_pull_done)
        self._pull_thread.start()

    def _on_pull_done(self, success: bool) -> None:
        self._bar.setVisible(False)
        if success:
            self._log.setText("Download complete.")
            self._log.setStyleSheet(
                f"color: {_GREEN}; background: {_PANEL}; border: 1px solid {_DIM}33; "
                "border-radius: 4px; padding: 6px 10px;"
            )
            if self._rec:
                self._save_config(self._rec)
                if self._rec.llm_code_model != self._rec.llm_model:
                    t = PullThread(self._rec.llm_code_model)
                    t.done.connect(lambda ok: self._log.setText(
                        "All models ready." if ok else "Code model failed — main model will be used."
                    ))
                    t.start()
            self._btn_launch.setVisible(True)
        else:
            self._log.setText("Download failed. Check your connection and retry.")
            self._btn_pull.setEnabled(True)
            self._btn_skip.setEnabled(True)

    # ── Config saving ─────────────────────────────────────────────────────────

    def _save_config(self, r: hw.ModelRecommendation) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cfg: dict = {
            "llm_provider": r.provider,
            "ollama_model": r.llm_model,
            "ollama_code_model": r.llm_code_model,
            "whisper_model_size": r.stt_model,
        }
        if r.provider == "groq":
            cfg["groq_model"] = r.llm_model
            cfg["groq_code_model"] = r.llm_code_model
        USER_CONFIG.write_text(json.dumps(cfg, indent=2))
        SETUP_DONE.touch()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _skip(self) -> None:
        SETUP_DONE.touch()
        self.finished.emit()
        self.close()

    def _launch(self) -> None:
        self.finished.emit()
        self.close()

    def _open_ollama_site(self) -> None:
        import webbrowser
        webbrowser.open("https://ollama.com/download")

    def _open_groq_site(self) -> None:
        import webbrowser
        webbrowser.open("https://console.groq.com/keys")

    # ── Drag (frameless) ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, "_drag_pos"):
            self.move(event.globalPosition().toPoint() - self._drag_pos)


# ── Entry points ──────────────────────────────────────────────────────────────

def needs_setup() -> bool:
    return not SETUP_DONE.exists()


def apply_user_config() -> None:
    from config import APP_CONFIG
    secrets_file = DATA_DIR / "secrets.env"
    if secrets_file.exists():
        for line in secrets_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key == "GROQ_API_KEY":
                APP_CONFIG.groq_api_key = val.strip()
    if not USER_CONFIG.exists():
        return
    try:
        data = json.loads(USER_CONFIG.read_text())
        for k, v in data.items():
            if hasattr(APP_CONFIG, k):
                setattr(APP_CONFIG, k, v)
    except Exception as exc:
        print(f"[Setup] Could not load user config: {exc}")


def run_wizard() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    wizard = SetupWizard()
    wizard.show()
    app.exec()


if __name__ == "__main__":
    run_wizard()
