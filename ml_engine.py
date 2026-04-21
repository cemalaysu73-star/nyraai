from __future__ import annotations

"""
Lightweight embedding engine for Nyra.

Primary:  Ollama nomic-embed-text (768-dim, multilingual, GPU-accelerated)
          Setup: ollama pull nomic-embed-text
Fallback: Jaccard token similarity (always available, no deps)

Embeddings are normalized so cosine similarity = dot product.
Results are cached to disk to avoid re-encoding the same phrases.
"""

import json
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import requests

from config import DATA_DIR, APP_CONFIG

CACHE_FILE = DATA_DIR / "embed_cache.json"
EMBED_MODEL = "nomic-embed-text"
_OLLAMA_BASE = APP_CONFIG.ollama_url.rstrip("/")


# ── Text helpers (always-available fallback) ──────────────────────────────────

def _normalize(text: str) -> str:
    import re
    return re.sub(r"[^\w\s]", "", text.lower().strip())

def _tokens(text: str) -> set[str]:
    return set(_normalize(text).split())

def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    score = len(ta & tb) / len(ta | tb)
    an, bn = _normalize(a), _normalize(b)
    if bn in an or an in bn:
        score = max(score, 0.55)
    return score


# ── Embedding engine ──────────────────────────────────────────────────────────

class EmbeddingEngine:
    """
    Wraps Ollama's embedding endpoint.
    Thread-safe. Falls back to Jaccard when Ollama is unavailable.
    """

    def __init__(self) -> None:
        self._cache: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self.available = False
        self._load_cache()
        threading.Thread(target=self._probe, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def encode(self, text: str) -> Optional[np.ndarray]:
        """Return a normalized 768-dim numpy array, or None if unavailable."""
        key = _normalize(text)
        with self._lock:
            if key in self._cache:
                return np.array(self._cache[key], dtype=np.float32)

        if not self.available:
            return None

        try:
            resp = requests.post(
                f"{_OLLAMA_BASE}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
                timeout=8,
            )
            if not resp.ok:
                return None
            vec = np.array(resp.json()["embedding"], dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            with self._lock:
                self._cache[key] = vec.tolist()
                if len(self._cache) % 20 == 0:
                    self._save_cache()
            return vec
        except Exception:
            return None

    def similarity(self, a: str, b: str) -> float:
        """Cosine similarity between two phrases. Falls back to Jaccard."""
        ea, eb = self.encode(a), self.encode(b)
        if ea is not None and eb is not None:
            return float(np.clip(np.dot(ea, eb), 0.0, 1.0))
        return _jaccard(a, b)

    def best_match(
        self,
        query: str,
        candidates: list[str],
        threshold: float = 0.0,
    ) -> tuple[int, float]:
        """
        Return (index, score) of the best matching candidate.
        Returns (-1, 0.0) if no candidate exceeds threshold.
        """
        if not candidates:
            return -1, 0.0
        eq = self.encode(query)
        best_idx, best_score = -1, 0.0
        for i, cand in enumerate(candidates):
            if eq is not None:
                ec = self.encode(cand)
                score = float(np.dot(eq, ec)) if ec is not None else _jaccard(query, cand)
            else:
                score = _jaccard(query, cand)
            if score > best_score:
                best_score, best_idx = score, i
        if best_score >= threshold:
            return best_idx, best_score
        return -1, 0.0

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _load_cache(self) -> None:
        if not CACHE_FILE.exists():
            return
        try:
            self._cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            self._cache = {}

    def _save_cache(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            CACHE_FILE.write_text(
                json.dumps(self._cache, separators=(",", ":")),
                encoding="utf-8",
            )
        except Exception:
            pass

    def flush_cache(self) -> None:
        with self._lock:
            self._save_cache()

    # ── Probe ─────────────────────────────────────────────────────────────────

    def _probe(self) -> None:
        """Check if nomic-embed-text is available. Run once at startup."""
        try:
            resp = requests.post(
                f"{_OLLAMA_BASE}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": "test"},
                timeout=10,
            )
            if resp.ok and "embedding" in resp.json():
                self.available = True
                print(f"[ML] Embedding engine ready ({EMBED_MODEL})")
            else:
                print(f"[ML] {EMBED_MODEL} not found — run: ollama pull {EMBED_MODEL}")
        except Exception:
            print("[ML] Ollama unreachable — using Jaccard fallback")


# ── Module-level singleton ────────────────────────────────────────────────────

_engine: Optional[EmbeddingEngine] = None

def get_engine() -> EmbeddingEngine:
    global _engine
    if _engine is None:
        _engine = EmbeddingEngine()
    return _engine
