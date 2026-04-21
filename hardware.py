from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class HardwareProfile:
    ram_gb: float
    vram_gb: float
    cpu_cores: int
    gpu_name: str
    ollama_installed: bool


@dataclass
class ModelRecommendation:
    llm_model: str
    llm_code_model: str
    stt_model: str
    provider: str           # "ollama" | "groq"
    tier: str               # "high" | "mid" | "low" | "cloud"
    description: str
    needs_cloud: bool


def detect() -> HardwareProfile:
    import psutil
    ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    cpu_cores = psutil.cpu_count(logical=False) or 2
    vram_gb = 0.0
    gpu_name = "No GPU detected"

    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        vram_gb = info.total / (1024 ** 3)
        gpu_name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(gpu_name, bytes):
            gpu_name = gpu_name.decode()
    except Exception:
        pass

    ollama_ok = False
    try:
        r = subprocess.run(["ollama", "--version"], capture_output=True, timeout=5)
        ollama_ok = r.returncode == 0
    except Exception:
        pass

    return HardwareProfile(
        ram_gb=round(ram_gb, 1),
        vram_gb=round(vram_gb, 1),
        cpu_cores=cpu_cores,
        gpu_name=gpu_name,
        ollama_installed=ollama_ok,
    )


def recommend(profile: HardwareProfile) -> ModelRecommendation:
    vram = profile.vram_gb
    ram = profile.ram_gb

    if vram >= 8:
        return ModelRecommendation(
            llm_model="qwen2.5:7b",
            llm_code_model="qwen2.5-coder:7b",
            stt_model="small",
            provider="ollama",
            tier="high",
            description=f"Full local mode — GPU detected ({vram:.0f}GB VRAM)",
            needs_cloud=False,
        )
    if vram >= 4:
        return ModelRecommendation(
            llm_model="qwen2.5:3b",
            llm_code_model="qwen2.5-coder:7b",
            stt_model="small",
            provider="ollama",
            tier="mid",
            description=f"Local mode — smaller model ({vram:.0f}GB VRAM)",
            needs_cloud=False,
        )
    if ram >= 16:
        return ModelRecommendation(
            llm_model="qwen2.5:3b",
            llm_code_model="qwen2.5:3b",
            stt_model="tiny",
            provider="ollama",
            tier="mid",
            description=f"CPU local mode — {ram:.0f}GB RAM, no GPU",
            needs_cloud=False,
        )
    if ram >= 8:
        return ModelRecommendation(
            llm_model="qwen2.5:1.5b",
            llm_code_model="qwen2.5:1.5b",
            stt_model="tiny",
            provider="ollama",
            tier="low",
            description=f"Lightweight local mode — {ram:.0f}GB RAM",
            needs_cloud=False,
        )
    # Not enough RAM for local — needs cloud
    return ModelRecommendation(
        llm_model="llama-3.3-70b-versatile",
        llm_code_model="llama-3.3-70b-versatile",
        stt_model="tiny",
        provider="groq",
        tier="cloud",
        description=f"Cloud mode required — only {ram:.0f}GB RAM (Groq free API)",
        needs_cloud=True,
    )
