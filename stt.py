from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import numpy as np

from config import APP_CONFIG

try:
    from faster_whisper import WhisperModel
    _WHISPER_OK = True
except ImportError:
    WhisperModel = None
    _WHISPER_OK = False


@dataclass(slots=True)
class TranscriptResult:
    text: str
    language: str
    confidence: float

    @property
    def valid(self) -> bool:
        return bool(self.text.strip())


_INITIAL_PROMPT = (
    "Nyra AI assistant. Commands in English or Turkish. "
    "English: open chrome, open steam, open spotify, open discord, open brave, open vlc, "
    "search for, google, look up, play on youtube, install, download, close, volume up, "
    "volume down, mute, next track, pause music, lock screen, what is, tell me about. "
    "Turkish: chrome aç, steam aç, spotify aç, discord aç, ara, google'la, youtube'da oynat, "
    "indir, kur, kapat, sesi aç, sesi kıs, sessiz yap, sonraki şarkı, müziği durdur, "
    "ekranı kilitle, ne söyle, anlat, hatırla. "
    "Names: Nyra, Aria, Valorant, Discord, Spotify, GitHub, Netflix, Reddit, Twitch."
)


class STT:
    def __init__(self) -> None:
        self._model = None
        self._loading = False
        self._lock = threading.Lock()
        self.status = "loading" if _WHISPER_OK else "unavailable"
        self._load_async()

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def available(self) -> bool:
        return _WHISPER_OK

    def transcribe(self, raw_pcm: bytes) -> TranscriptResult:
        self._await_ready()
        if self._model is None:
            return TranscriptResult("", APP_CONFIG.default_language, 0.0)

        audio = self._prepare(raw_pcm)
        if audio.size == 0:
            return TranscriptResult("", APP_CONFIG.default_language, 0.0)

        try:
            return self._do_transcribe(audio)
        except RuntimeError as exc:
            # cuBLAS / CUDA DLL missing — model loaded but can't run; fall back to CPU
            if any(k in str(exc).lower() for k in ("dll", "cuda", "cublas", "cudnn")):
                self._reload_cpu()
                if self._model is None:
                    return TranscriptResult("", APP_CONFIG.default_language, 0.0)
                return self._do_transcribe(audio)
            raise

    # Minimum audio to bother transcribing (~0.4s at 16kHz)
    _MIN_SAMPLES = 6_400

    def _do_transcribe(self, audio) -> TranscriptResult:
        if len(audio) < self._MIN_SAMPLES:
            return TranscriptResult("", APP_CONFIG.default_language, 0.0)

        beam = getattr(APP_CONFIG, "whisper_beam_size", 1)
        with self._lock:
            segments, info = self._model.transcribe(
                audio,
                beam_size=beam,
                best_of=beam,           # match beam — no wasted passes
                temperature=0.0,        # single-pass, no retry temperature
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": 150,
                    "speech_pad_ms": 60,
                    "threshold": 0.25,
                },
                without_timestamps=True,
                condition_on_previous_text=False,
                no_speech_threshold=0.50,
                initial_prompt=_INITIAL_PROMPT,
            )
            text = " ".join(s.text for s in segments).strip()
        language = getattr(info, "language", None) or APP_CONFIG.default_language
        confidence = float(getattr(info, "language_probability", 0.85))
        return TranscriptResult(text, language, confidence)

    def _reload_cpu(self) -> None:
        try:
            cpu_threads = max(1, min(APP_CONFIG.whisper_threads, (os.cpu_count() or 4) - 1))
            model = WhisperModel(
                APP_CONFIG.whisper_model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
            )
            with self._lock:
                self._model = model
            self.status = "ready (cpu)"
        except Exception as exc:
            self._model = None
            self.status = f"error: {exc}"

    # ── internals ────────────────────────────────────────────────────────────

    def _prepare(self, raw_pcm: bytes) -> np.ndarray:
        if not raw_pcm:
            return np.array([], dtype=np.float32)
        audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
        audio -= float(np.mean(audio))  # DC offset removal only — don't peak-normalize
        return audio

    def _load_async(self) -> None:
        if not _WHISPER_OK or self._loading:
            return
        self._loading = True
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            self._model = WhisperModel(
                APP_CONFIG.whisper_model_size,
                device="cuda",
                compute_type=APP_CONFIG.whisper_compute_type,
            )
            self.status = "ready"
        except Exception:
            try:
                cpu_threads = max(1, min(APP_CONFIG.whisper_threads, (os.cpu_count() or 4) - 1))
                self._model = WhisperModel(
                    APP_CONFIG.whisper_model_size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=cpu_threads,
                )
                self.status = "ready (cpu)"
            except Exception as exc:
                self.status = f"error: {exc}"
        finally:
            self._loading = False

    def _await_ready(self, timeout: float = 45.0) -> None:
        deadline = time.time() + timeout
        while self._loading and time.time() < deadline:
            time.sleep(0.05)
