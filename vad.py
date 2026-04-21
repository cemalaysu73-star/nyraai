from __future__ import annotations

import threading

import numpy as np

_lock = threading.Lock()
_model = None
_VAD_OK = False
_CHUNK = 512
_RATE = 16_000
_ready = threading.Event()


def _load() -> None:
    global _model, _VAD_OK
    try:
        from silero_vad import load_silero_vad
        _model = load_silero_vad()
        _VAD_OK = True
    except Exception:
        pass
    finally:
        _ready.set()


threading.Thread(target=_load, daemon=True).start()


def is_speech(audio: np.ndarray, threshold: float = 0.45) -> bool:
    if audio.size == 0:
        return False
    # Wait up to 5s for model to load on first call, then fall back to energy
    if not _ready.is_set():
        _ready.wait(timeout=5.0)
    if not _VAD_OK or _model is None:
        return _energy_fallback(audio)
    try:
        import torch
        with _lock:
            for i in range(0, max(len(audio), _CHUNK), _CHUNK):
                chunk = audio[i:i + _CHUNK]
                if len(chunk) < _CHUNK:
                    chunk = np.pad(chunk, (0, _CHUNK - len(chunk)))
                if _model(torch.from_numpy(chunk.copy()).float(), _RATE).item() > threshold:
                    return True
    except Exception:
        return _energy_fallback(audio)
    return False


def _energy_fallback(audio: np.ndarray) -> bool:
    return float(np.sqrt(np.mean(np.square(audio)))) > 0.008
