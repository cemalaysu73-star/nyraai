from __future__ import annotations

import threading
import time
from pathlib import Path

from config import APP_CONFIG

try:
    import chromadb
    _OK = True
except ImportError:
    _OK = False


class LongTermMemory:
    def __init__(self) -> None:
        self._collection = None
        self._lock = threading.Lock()
        self._ready = False
        self._last_query: str = ""
        self._last_result: str = ""
        threading.Thread(target=self._init, daemon=True).start()

    def _init(self) -> None:
        if not _OK:
            return
        try:
            db_path = str(APP_CONFIG.memory_file.parent / "chroma")
            client = chromadb.PersistentClient(path=db_path)
            self._collection = client.get_or_create_collection(
                name="nyra_memory",
                metadata={"hnsw:space": "cosine"},
            )
            self._ready = True
        except Exception:
            pass

    def store(self, text: str, role: str = "conversation") -> None:
        """Non-blocking — writes in a background thread."""
        if not self._ready or not text.strip():
            return
        threading.Thread(target=self._store_sync, args=(text, role), daemon=True).start()

    def _store_sync(self, text: str, role: str) -> None:
        with self._lock:
            try:
                self._collection.add(
                    documents=[text],
                    ids=[f"{int(time.time() * 1000)}"],
                    metadatas=[{"role": role, "ts": int(time.time())}],
                )
                # Invalidate cache so next query reflects the new entry
                self._last_query = ""
            except Exception:
                pass

    def search(self, query: str, n: int = 4) -> list[str]:
        if not self._ready:
            return []
        with self._lock:
            try:
                res = self._collection.query(query_texts=[query], n_results=n)
                return res["documents"][0] if res["documents"] else []
            except Exception:
                return []

    def format_context(self, query: str) -> str:
        # Simple single-entry cache — avoids duplicate ChromaDB round-trips
        if query == self._last_query and self._last_result != "":
            return self._last_result
        hits = self.search(query, n=3)
        result = ("Relevant past context:\n" + "\n---\n".join(hits)) if hits else ""
        self._last_query = query
        self._last_result = result
        return result
