from __future__ import annotations

import collections
import json
import queue
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

import vad as _vad
from config import APP_CONFIG

try:
    from vosk import KaldiRecognizer, Model as VoskModel
    _VOSK_OK = True
except ImportError:
    KaldiRecognizer = None
    VoskModel = None
    _VOSK_OK = False


# ── Noise floor (shared between wake loop and capture) ──────────────────────

class _NoiseFloor:
    def __init__(self) -> None:
        self.rms = APP_CONFIG.rms_floor
        self.peak = APP_CONFIG.peak_floor

    def update(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        rms = float(np.sqrt(np.mean(np.square(audio))))
        peak = float(np.max(np.abs(audio)))
        rms_thresh, peak_thresh = self.thresholds()
        if rms < rms_thresh * 1.6 and peak < peak_thresh * 1.6:
            self.rms = self.rms * 0.92 + rms * 0.08
            self.peak = self.peak * 0.92 + peak * 0.08

    def thresholds(self) -> tuple[float, float]:
        rms = max(APP_CONFIG.rms_floor, min(0.0075, self.rms * 3.5))
        peak = max(APP_CONFIG.peak_floor, min(0.085, self.peak * 3.2))
        return rms, peak

    def is_silent(self, audio: np.ndarray) -> bool:
        if audio.size == 0:
            return True
        rms_t, peak_t = self.thresholds()
        rms = float(np.sqrt(np.mean(np.square(audio))))
        peak = float(np.max(np.abs(audio)))
        return rms < rms_t and peak < peak_t


_noise = _NoiseFloor()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _to_float32(raw: bytes) -> np.ndarray:
    if not raw:
        return np.array([], dtype=np.float32)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _is_wake(text: str) -> bool:
    lower = text.lower().strip()
    return any(phrase in lower for phrase in APP_CONFIG.wake_phrases)


def _strip_wake(text: str) -> str:
    lower = text.lower().strip()
    for phrase in APP_CONFIG.wake_phrases:
        lower = lower.replace(phrase, "").strip()
    return lower


# ── Audio capture (standalone, called from UI thread) ───────────────────────

def capture_command(
    max_seconds: int | None = None,
    stop_event: threading.Event | None = None,
) -> bytes:
    """Record voice until end-of-speech, timeout, or stop_event.

    Key behaviour:
    - Pre-buffer: keeps the last ~0.6s before speech starts, so the first
      syllable is never clipped.
    - Inclusive recording: once speech begins, ALL audio is kept — including
      natural pauses mid-sentence. Only sustained silence ends the capture.
    """
    listen_limit = max_seconds or APP_CONFIG.capture_max_seconds
    silence_limit = APP_CONFIG.capture_silence_seconds
    # Pre-buffer holds ~0.6s of audio before speech triggers
    pre_buf_blocks = max(1, int(0.6 * APP_CONFIG.sample_rate / APP_CONFIG.blocksize))
    pre_buffer: collections.deque[bytes] = collections.deque(maxlen=pre_buf_blocks)

    started = time.time()
    last_voice = time.time()
    heard_speech = False
    recording_chunks: list[bytes] = []
    audio_q: queue.Queue[bytes] = queue.Queue()

    def callback(indata, frames, time_info, status) -> None:
        audio_q.put(bytes(indata))

    try:
        with sd.RawInputStream(
            samplerate=APP_CONFIG.sample_rate,
            blocksize=APP_CONFIG.blocksize,
            dtype="int16",
            channels=1,
            callback=callback,
        ):
            while time.time() - started < listen_limit:
                if stop_event and stop_event.is_set():
                    break
                try:
                    chunk = audio_q.get(timeout=0.15)
                except queue.Empty:
                    if heard_speech and time.time() - last_voice > silence_limit:
                        break
                    continue

                audio = _to_float32(chunk)
                is_speech_now = _vad.is_speech(audio)

                if not heard_speech:
                    # Waiting for speech to begin — keep pre-buffer rolling
                    if is_speech_now:
                        heard_speech = True
                        last_voice = time.time()
                        # Flush pre-buffer first so we don't clip the first word
                        recording_chunks.extend(pre_buffer)
                        pre_buffer.clear()
                        recording_chunks.append(chunk)
                    else:
                        _noise.update(audio)
                        pre_buffer.append(chunk)
                else:
                    # Speech in progress — keep ALL audio, update voice timestamp
                    recording_chunks.append(chunk)
                    if is_speech_now:
                        last_voice = time.time()
                    elif time.time() - last_voice > silence_limit:
                        break   # sustained silence → done

    except Exception:
        pass

    if not heard_speech:
        return b""
    return b"".join(recording_chunks)


# ── Wake detector ────────────────────────────────────────────────────────────

class WakeDetector:
    def __init__(self) -> None:
        self._model = self._load_vosk_model()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_wake = 0.0

    @property
    def available(self) -> bool:
        return self._model is not None

    def start(self, on_wake: Callable[[str], None]) -> None:
        if not self.available:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(on_wake,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ── internals ────────────────────────────────────────────────────────────

    def _load_vosk_model(self):
        if not _VOSK_OK:
            return None
        from pathlib import Path
        for path_str in [APP_CONFIG.vosk_model_path, APP_CONFIG.vosk_model_path_tr]:
            if not path_str:
                continue
            p = Path(path_str).expanduser()
            if p.exists():
                try:
                    return VoskModel(str(p))
                except Exception:
                    pass
        return None

    def _make_recognizer(self):
        grammar_phrases = list(APP_CONFIG.wake_phrases) + list(APP_CONFIG.wake_grammar_extra)
        grammar = json.dumps(grammar_phrases)
        rec = KaldiRecognizer(self._model, APP_CONFIG.sample_rate, grammar)
        rec.SetWords(False)
        return rec

    def _loop(self, on_wake: Callable[[str], None]) -> None:
        rec = self._make_recognizer()
        audio_q: queue.Queue[bytes] = queue.Queue()
        context: list[str] = []

        def callback(indata, frames, time_info, status) -> None:
            if not self._stop.is_set():
                audio_q.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=APP_CONFIG.sample_rate,
                blocksize=APP_CONFIG.blocksize,
                dtype="int16",
                channels=1,
                callback=callback,
            ):
                while not self._stop.is_set():
                    try:
                        chunk = audio_q.get(timeout=0.25)
                    except queue.Empty:
                        continue

                    audio = _to_float32(chunk)
                    silent = _noise.is_silent(audio)

                    if silent:
                        _noise.update(audio)
                        if not context:
                            continue
                        # feed silence through so recognizer can flush partials

                    # Energy gate when no pending context
                    if not silent and not context:
                        rms = float(np.sqrt(np.mean(np.square(audio))))
                        rms_t, _ = _noise.thresholds()
                        if rms < rms_t * 1.4:
                            continue

                    transcript = ""
                    if rec.AcceptWaveform(chunk):
                        result = json.loads(rec.Result())
                        transcript = result.get("text", "").strip()
                    else:
                        partial = json.loads(rec.PartialResult())
                        transcript = partial.get("partial", "").strip()

                    if not transcript:
                        continue

                    if not silent:
                        context.append(transcript)
                        context = context[-3:]

                    combined = " ".join(context)
                    if not _is_wake(transcript) and not _is_wake(combined):
                        if not silent and len(transcript.split()) >= 3:
                            context.clear()
                        continue

                    now = time.time()
                    if now - self._last_wake < APP_CONFIG.wake_cooldown_seconds:
                        context.clear()
                        continue

                    self._last_wake = now
                    inline = _strip_wake(combined)
                    context.clear()
                    on_wake(inline)

        except Exception:
            pass
