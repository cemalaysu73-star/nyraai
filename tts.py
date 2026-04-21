from __future__ import annotations

import asyncio
import os
import re
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import pyttsx3
from edge_tts import Communicate

try:
    from piper.voice import PiperVoice
    _PIPER_OK = True
except ImportError:
    PiperVoice = None
    _PIPER_OK = False
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from config import APP_CONFIG


@dataclass(slots=True)
class SpeechPayload:
    kind: str   # "edge" | "fallback"
    value: str  # file path for "edge", raw text for "fallback"


class TTSManager(QObject):
    finished = Signal()
    error = Signal(str)
    _next_sentence_ready = Signal(object)
    _playback_complete = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self._on_player_state)
        self.player.errorOccurred.connect(
            lambda err, msg: print(f"[QMediaPlayer] error {err}: {msg}")
        )
        self._temp_path: Path | None = None
        self._fallback_engine = None
        self._fallback_lock = threading.Lock()
        self._playing = False
        self._stopping = False
        self._stderr_saved: int | None = None
        self._stderr_null: int | None = None
        self._sentence_queue: list[str] = []
        self._sentence_language = "en"
        self._piper_voices: dict[str, "PiperVoice"] = {}
        self._piper_lock = threading.Lock()
        self._next_sentence_ready.connect(self._on_next_ready)
        self._playback_complete.connect(self._advance_or_finish)

    @property
    def is_speaking(self) -> bool:
        return self._playing

    # ── Public API ────────────────────────────────────────────────────────────

    def speak(self, text: str, language: str) -> None:
        """Split text into sentences and play them sequentially. First sentence starts ASAP."""
        self.stop()
        self._stopping = False
        self._sentence_language = language
        sentences = self._split_sentences(text)
        if not sentences:
            self.finished.emit()
            return
        self._sentence_queue = sentences[1:]
        threading.Thread(
            target=self._prepare_and_signal,
            args=(sentences[0],),
            daemon=True,
        ).start()

    def prepare(self, text: str, language: str = "en") -> SpeechPayload | None:
        if not APP_CONFIG.voice_enabled or not text.strip():
            return None
        try:
            path = self._edge_audio(text, language)
            return SpeechPayload("edge", path)
        except Exception as exc:
            print(f"[TTS] edge-tts failed: {exc}")
        try:
            path = self._piper_audio(text, language)
            return SpeechPayload("edge", path)
        except Exception as exc:
            print(f"[TTS] piper failed: {exc}")
        print("[TTS] falling back to pyttsx3")
        return SpeechPayload("fallback", text)

    def play_prepared(self, payload: SpeechPayload | None) -> None:
        saved_q = list(self._sentence_queue)
        saved_lang = self._sentence_language
        self.stop()
        self._stopping = False
        self._sentence_queue = saved_q
        self._sentence_language = saved_lang

        if payload is None:
            self._playback_complete.emit()
            return

        if payload.kind == "edge":
            self._playing = True
            self._temp_path = Path(payload.value)
            self._silence_stderr()
            self.player.setSource(QUrl.fromLocalFile(str(self._temp_path)))
            self.player.play()
            QTimer.singleShot(900, self._restore_stderr)
        else:
            self._play_fallback(payload.value)

    def stop(self) -> None:
        self._stopping = True
        self._sentence_queue.clear()
        if self.player.playbackState() != QMediaPlayer.StoppedState:
            self.player.stop()
        engine = self._fallback_engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
        self._restore_stderr()
        self._cleanup_temp()
        self._playing = False

    # ── Sentence pipeline ─────────────────────────────────────────────────────

    def _prepare_and_signal(self, sentence: str) -> None:
        if self._stopping:
            return
        payload = self.prepare(sentence, self._sentence_language)
        if not self._stopping:
            self._next_sentence_ready.emit(payload)

    def _on_next_ready(self, payload: object) -> None:
        if not self._stopping:
            self.play_prepared(payload)

    def _advance_or_finish(self) -> None:
        if self._stopping:
            return
        if not self._sentence_queue:
            self.finished.emit()
            return
        sentence = self._sentence_queue.pop(0)
        threading.Thread(
            target=self._prepare_and_signal,
            args=(sentence,),
            daemon=True,
        ).start()

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        result: list[str] = []
        carry = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            combined = (carry + " " + part).strip() if carry else part
            if len(combined) < 15:
                carry = combined
            else:
                result.append(combined)
                carry = ""
        if carry:
            result.append(carry)
        return result or [text.strip()]

    # ── Playback internals ────────────────────────────────────────────────────

    def _on_player_state(self, state) -> None:
        if state != QMediaPlayer.StoppedState:
            self._restore_stderr()
            return
        was_stopping = self._stopping
        self._restore_stderr()
        self._cleanup_temp()
        self._playing = False
        self._stopping = False
        if not was_stopping:
            self._playback_complete.emit()

    def _play_fallback(self, text: str) -> None:
        self._playing = True

        def run() -> None:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            with self._fallback_lock:
                engine = pyttsx3.init()
                self._fallback_engine = engine
                self._configure_fallback(engine)
                engine.setProperty("rate", 178)
                engine.setProperty("volume", 1.0)
                try:
                    engine.say(text)
                    engine.runAndWait()
                    if not self._stopping:
                        self._playback_complete.emit()
                except Exception as exc:
                    self.error.emit(str(exc))
                finally:
                    try:
                        engine.stop()
                    except Exception:
                        pass
                    self._fallback_engine = None
                    self._playing = False

        threading.Thread(target=run, daemon=True).start()

    def _edge_audio(self, text: str, language: str) -> str:
        voice, rate, pitch = self._voice_profile(language)
        cleaned = self._clean_for_tts(text)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fh:
            path = Path(fh.name)
        communicate = Communicate(text=cleaned, voice=voice, rate=rate, pitch=pitch)
        loop = asyncio.new_event_loop()
        # Python 3.12+: threads have no default event loop — must set explicitly
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(communicate.save(str(path)))
        finally:
            asyncio.set_event_loop(None)
            loop.close()
        if not path.exists() or path.stat().st_size < 100:
            path.unlink(missing_ok=True)
            raise RuntimeError("edge-tts returned empty audio")
        return str(path)

    def _piper_audio(self, text: str, language: str) -> str:
        if not _PIPER_OK:
            raise RuntimeError("piper-tts not installed")
        voice = self._get_piper_voice(language)
        cleaned = self._clean_for_tts(text)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as fh:
            path = fh.name
        with wave.open(path, "wb") as wf:
            voice.synthesize(cleaned, wf)
        return path

    def _get_piper_voice(self, language: str) -> "PiperVoice":
        model_path = APP_CONFIG.tr_piper_model if language == "tr" else APP_CONFIG.en_piper_model
        if not model_path:
            raise ValueError("Piper model path not configured")
        resolved = self._resolve_piper_model(model_path)
        with self._piper_lock:
            if resolved not in self._piper_voices:
                self._piper_voices[resolved] = PiperVoice.load(resolved)
            return self._piper_voices[resolved]

    @staticmethod
    def _resolve_piper_model(model: str) -> str:
        """Accept full path or bare model name. Searches default Piper data dirs."""
        p = Path(model)
        if p.is_file():
            return str(p)
        # bare name — search common install locations
        search_dirs = [
            Path.home() / "AppData" / "Local" / "piper",
            Path.home() / ".local" / "share" / "piper",
            Path(__file__).parent / "piper_models",
        ]
        name = model if model.endswith(".onnx") else f"{model}.onnx"
        for d in search_dirs:
            candidate = d / name
            if candidate.is_file():
                return str(candidate)
        raise FileNotFoundError(f"Piper model not found: {model}")

    def _voice_profile(self, language: str) -> tuple[str, str, str]:
        if language == "tr":
            return APP_CONFIG.tr_voice, APP_CONFIG.tr_voice_rate, APP_CONFIG.tr_voice_pitch
        return APP_CONFIG.en_voice, APP_CONFIG.en_voice_rate, APP_CONFIG.en_voice_pitch

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        t = text.strip()
        t = t.replace("\n•", ". ").replace("\n-", ". ").replace("\n", ". ")
        t = re.sub(r"\s+", " ", t)
        t = re.sub(r"\bAI\b", "A I", t)
        t = t.replace("...", ".")
        if t and t[-1] not in ".!?":
            t += "."
        return t

    def _configure_fallback(self, engine) -> None:
        preferred = ("zira", "hazel", "aria", "jenny", "female")
        try:
            for voice in engine.getProperty("voices"):
                name = f"{getattr(voice, 'name', '')} {getattr(voice, 'id', '')}".lower()
                if any(token in name for token in preferred):
                    engine.setProperty("voice", voice.id)
                    return
        except Exception:
            pass

    def _cleanup_temp(self) -> None:
        if self._temp_path is not None:
            try:
                self._temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._temp_path = None

    def _silence_stderr(self) -> None:
        if self._stderr_saved is not None:
            return
        try:
            self._stderr_saved = os.dup(2)
            self._stderr_null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(self._stderr_null, 2)
        except OSError:
            self._restore_stderr()

    def _restore_stderr(self) -> None:
        if self._stderr_saved is None:
            return
        try:
            os.dup2(self._stderr_saved, 2)
        except OSError:
            pass
        for fd in (self._stderr_saved, self._stderr_null):
            try:
                if fd is not None:
                    os.close(fd)
            except OSError:
                pass
        self._stderr_saved = None
        self._stderr_null = None
