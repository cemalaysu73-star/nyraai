from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"


@dataclass
class Config:
    # ── Paths ──────────────────────────────────────────────────────────
    memory_file: Path = field(default_factory=lambda: DATA_DIR / "memory.json")
    vosk_model_path: str = ""         # e.g. C:/models/vosk-model-en-us-0.22
    vosk_model_path_tr: str = ""      # e.g. C:/models/vosk-model-tr

    # ── Audio ──────────────────────────────────────────────────────────
    sample_rate: int = 16_000
    blocksize: int = 4_096
    rms_floor: float = 0.0028
    peak_floor: float = 0.028

    # ── Wake detection ─────────────────────────────────────────────────
    wake_phrases: tuple[str, ...] = ("hey nyra", "nyra")
    wake_grammar_extra: tuple[str, ...] = ("hey niora", "niora", "hey nyro", "nyro")
    wake_cooldown_seconds: float = 3.0

    # ── Voice capture ──────────────────────────────────────────────────
    capture_max_seconds: int = 12
    capture_silence_seconds: float = 1.1

    # ── STT ────────────────────────────────────────────────────────────
    whisper_model_size: str = "small"
    whisper_compute_type: str = "float16"
    whisper_threads: int = 4

    # ── LLM provider ───────────────────────────────────────────────────
    # "ollama" → local Ollama   |   "groq" → Groq cloud API
    llm_provider: str = "ollama"

    # ── Ollama (local) ──────────────────────────────────────────────────
    ollama_model: str = "qwen2.5:7b"
    ollama_code_model: str = "qwen2.5-coder:7b"
    ollama_url: str = "http://localhost:11434"
    ollama_num_gpu: int = -1   # -1 = all layers on GPU | e.g. 20 = partial GPU

    # ── Groq (cloud, free tier) ─────────────────────────────────────────
    groq_api_key: str = ""  # set in data/secrets.env: GROQ_API_KEY=...
    groq_model: str = "llama-3.3-70b-versatile"  # general conversation
    groq_code_model: str = "llama-3.3-70b-versatile"  # coding tasks

    # ── Shared LLM settings ─────────────────────────────────────────────
    vision_model: str = "llava:7b"
    ollama_temperature: float = 0.7
    ollama_ctx: int = 8192
    history_max_turns: int = 15
    history_max_tokens: int = 2000

    # ── TTS ────────────────────────────────────────────────────────────
    voice_enabled: bool = True
    # edge-tts (online)
    en_voice: str = "en-US-AriaNeural"
    en_voice_rate: str = "+10%"
    en_voice_pitch: str = "-8Hz"
    tr_voice: str = "tr-TR-EmelNeural"
    tr_voice_rate: str = "+5%"
    tr_voice_pitch: str = "+0Hz"
    # Piper (offline fallback) — set paths after installing piper-tts models
    piper_executable: str = "piper"          # or full path e.g. C:/piper/piper.exe
    en_piper_model: str = ""                 # e.g. C:/piper/models/en_GB-alba-medium.onnx
    tr_piper_model: str = ""                 # e.g. C:/piper/models/tr_TR-dfki-medium.onnx

    # ── UI ─────────────────────────────────────────────────────────────
    window_width: int = 860
    window_height: int = 700

    # ── Behaviour ──────────────────────────────────────────────────────
    background_listening: bool = True
    always_listening: bool = True      # continuous mic — no wake word needed
    screen_poll_ms: int = 4_000
    followup_turns: int = 3
    followup_timeout_seconds: int = 9
    default_language: str = "en"
    auto_show_on_wake: bool = False


APP_CONFIG = Config()
