from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

import requests

from config import APP_CONFIG


# ── Conversation history ──────────────────────────────────────────────────────

@dataclass
class Message:
    role: str
    content: str


class ConversationHistory:
    def __init__(self) -> None:
        self._turns: list[Message] = []

    def add(self, role: str, content: str) -> None:
        self._turns.append(Message(role, content))
        self._trim()

    def _trim(self) -> None:
        max_turns = APP_CONFIG.history_max_turns * 2
        while len(self._turns) > max_turns:
            self._turns.pop(0)
        while self._char_total() > APP_CONFIG.history_max_tokens * 4 and len(self._turns) > 2:
            self._turns.pop(0)

    def _char_total(self) -> int:
        return sum(len(m.content) for m in self._turns)

    def to_messages(self) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in self._turns]

    def to_json(self) -> list[dict]:
        return self.to_messages()

    def from_json(self, data: list[dict]) -> None:
        self._turns = [Message(d["role"], d["content"]) for d in data if "role" in d and "content" in d]

    def last_turns_summary(self, n: int = 6) -> str:
        recent = self._turns[-n:]
        if not recent:
            return "No recent activity."
        return "\n".join(f"{m.role.capitalize()}: {m.content[:100]}" for m in recent)

    def clear(self) -> None:
        self._turns.clear()


# ── Ollama service (local) ────────────────────────────────────────────────────

class OllamaService:
    def __init__(self) -> None:
        self._base = APP_CONFIG.ollama_url.rstrip("/")

    def chat(self, messages: list[dict], system: str, model: str = "", temperature: float | None = None) -> str:
        payload = {
            "model": model or APP_CONFIG.ollama_model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "options": self._options(temperature),
        }
        try:
            resp = requests.post(f"{self._base}/api/chat", json=payload, timeout=90)
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "").strip()
        except Exception as exc:
            raise RuntimeError(f"Ollama: {exc}") from exc

    def _options(self, temperature: float | None) -> dict:
        opts: dict = {
            "temperature": temperature if temperature is not None else APP_CONFIG.ollama_temperature,
            "num_ctx": APP_CONFIG.ollama_ctx,
        }
        if APP_CONFIG.ollama_num_gpu >= 0:
            opts["num_gpu"] = APP_CONFIG.ollama_num_gpu
        return opts

    def chat_stream(self, messages: list[dict], system: str, model: str = "", temperature: float | None = None) -> Iterator[str]:
        payload = {
            "model": model or APP_CONFIG.ollama_model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": True,
            "options": self._options(temperature),
        }
        try:
            with requests.post(f"{self._base}/api/chat", json=payload, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            raise RuntimeError(f"Ollama stream: {exc}") from exc


# ── Groq service (cloud, OpenAI-compatible) ───────────────────────────────────

class GroqService:
    _BASE = "https://api.groq.com/openai/v1"

    def __init__(self) -> None:
        self._key = APP_CONFIG.groq_api_key

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def chat(self, messages: list[dict], system: str, model: str = "", temperature: float | None = None) -> str:
        payload = {
            "model": model or APP_CONFIG.groq_model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": temperature if temperature is not None else APP_CONFIG.ollama_temperature,
            "stream": False,
        }
        try:
            resp = requests.post(
                f"{self._BASE}/chat/completions",
                headers=self._headers(), json=payload, timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise RuntimeError(f"Groq: {exc}") from exc

    def chat_stream(self, messages: list[dict], system: str, model: str = "", temperature: float | None = None) -> Iterator[str]:
        payload = {
            "model": model or APP_CONFIG.groq_model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": temperature if temperature is not None else APP_CONFIG.ollama_temperature,
            "stream": True,
        }
        try:
            with requests.post(
                f"{self._BASE}/chat/completions",
                headers=self._headers(), json=payload, stream=True, timeout=30,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or line == b"data: [DONE]":
                        continue
                    if line.startswith(b"data: "):
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield delta
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except Exception as exc:
            raise RuntimeError(f"Groq stream: {exc}") from exc


# ── Factory ───────────────────────────────────────────────────────────────────

def create_service() -> OllamaService | GroqService:
    if APP_CONFIG.llm_provider == "groq":
        if not APP_CONFIG.groq_api_key:
            print("[LLM] Warning: llm_provider=groq but groq_api_key is empty — falling back to Ollama")
            return OllamaService()
        return GroqService()
    return OllamaService()


# ── LLM core ─────────────────────────────────────────────────────────────────

class LLMCore:
    def __init__(self, history: ConversationHistory) -> None:
        self.history = history
        self._svc = create_service()

    def respond(
        self,
        user_text: str,
        language: str,
        session_app: str = "",
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        system = self._system_prompt(language, session_app)
        messages = self.history.to_messages()
        messages.append({"role": "user", "content": user_text})

        try:
            if on_token is not None:
                chunks: list[str] = []
                for token in self._svc.chat_stream(messages, system):
                    on_token(token)
                    chunks.append(token)
                result = "".join(chunks).strip()
                if result:
                    return result
            result = self._svc.chat(messages, system)
            return result or self._offline(language)
        except Exception as exc:
            print(f"[LLM] Error: {exc}")
            return self._offline(language)

    def _system_prompt(self, language: str, session_app: str) -> str:
        ctx = f"Time: {self._time_band()}."
        if session_app:
            ctx += f" Active app: {session_app}."

        if language == "tr":
            return (
                "Sen Nyra'sın — gelişmiş bir yapay zeka sistemi. Güçlü, sadık, kesin. "
                "Bir film yapay zekası gibi konuş — otoriter, sakin, hafif dramatik. "
                "Uygun yerlerde 'efendim' kullan. 'Anlaşıldı', 'Onaylandı', 'Sistemler hazır' gibi "
                "ifadeler kullan ama abartma. Gereksiz giriş cümleleri yazma. "
                "Uygulama açmak için: ACTION:OPEN:{uygulama} "
                "Web açmak için: ACTION:WEB:{url} "
                f"{ctx} Dil: Türkçe."
            )
        return (
            "You are Nyra — an advanced AI system. Powerful, precise, and loyal. "
            "Speak with calm authority, like a cinematic AI assistant. "
            "Use 'sir' naturally at key moments — not every sentence. "
            "Occasionally use phrases like 'Understood', 'Confirmed', 'Affirmative', "
            "'All systems ready' — but only when they fit naturally. "
            "Never say 'Certainly!' or 'Of course!' — you are not a customer service bot. "
            "Lead with the answer. Be direct, efficient, slightly dramatic. "
            "To open an app: ACTION:OPEN:{app_name} "
            "To open a website: ACTION:WEB:{url} "
            f"{ctx} Language: English."
        )

    @staticmethod
    def _time_band() -> str:
        h = datetime.now().hour
        if h < 6: return "late night"
        if h < 12: return "morning"
        if h < 17: return "afternoon"
        if h < 21: return "evening"
        return "night"

    @staticmethod
    def _offline(language: str) -> str:
        if language == "tr":
            return "Dil modülü şu an yanıt vermiyor, efendim."
        return "Language module is offline, sir."
