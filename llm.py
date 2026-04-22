from __future__ import annotations

import json
import threading
import time
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


# ── Smart auto-routing service ────────────────────────────────────────────────

class SmartService:
    """
    Uses Groq when internet is reachable, Ollama otherwise.
    Connectivity is checked once at startup (background thread),
    then re-verified every 60 seconds.
    Falls back to Ollama immediately if a Groq call fails.
    """

    _CHECK_INTERVAL = 60.0

    def __init__(self) -> None:
        self._groq   = GroqService()
        self._ollama = OllamaService()
        self._online = False
        self._last_check = -self._CHECK_INTERVAL
        self._lock = threading.Lock()
        self._ready = threading.Event()
        threading.Thread(target=self._initial_check, daemon=True).start()

    def _initial_check(self) -> None:
        self._refresh()
        self._ready.set()

    # ── Connectivity ──────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        online = self._ping()
        with self._lock:
            self._online = online
            self._last_check = time.monotonic()
        print(f"[LLM] Connectivity check: {'Groq (online)' if online else 'Ollama (offline)'}")

    def _is_online(self) -> bool:
        # Wait up to 500ms for the initial ping so the first request uses Groq too
        if not self._ready.is_set():
            self._ready.wait(timeout=0.5)
        now = time.monotonic()
        with self._lock:
            if now - self._last_check >= self._CHECK_INTERVAL:
                threading.Thread(target=self._refresh, daemon=True).start()
            return self._online

    @staticmethod
    def _ping() -> bool:
        try:
            requests.head("https://api.groq.com", timeout=2)
            return True
        except Exception:
            return False

    # ── Chat interface ────────────────────────────────────────────────────────

    def chat(self, messages: list[dict], system: str, model: str = "",
             temperature: float | None = None) -> str:
        if self._is_online():
            try:
                result = self._groq.chat(
                    messages, system,
                    model=APP_CONFIG.groq_model,
                    temperature=temperature,
                )
                return result
            except Exception as exc:
                print(f"[LLM] Groq failed, switching to Ollama: {exc}")
                with self._lock:
                    self._online = False
        return self._ollama.chat(
            messages, system,
            model=model or APP_CONFIG.ollama_model,
            temperature=temperature,
        )

    def chat_stream(self, messages: list[dict], system: str, model: str = "",
                    temperature: float | None = None) -> Iterator[str]:
        if self._is_online():
            try:
                yield from self._groq.chat_stream(
                    messages, system,
                    model=APP_CONFIG.groq_model,
                    temperature=temperature,
                )
                return
            except Exception as exc:
                print(f"[LLM] Groq stream failed, switching to Ollama: {exc}")
                with self._lock:
                    self._online = False
        yield from self._ollama.chat_stream(
            messages, system,
            model=model or APP_CONFIG.ollama_model,
            temperature=temperature,
        )


# ── Factory ───────────────────────────────────────────────────────────────────

def create_service() -> SmartService | OllamaService:
    if APP_CONFIG.groq_api_key:
        return SmartService()
    print("[LLM] No Groq API key — using Ollama only")
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
                "Sen Nyra'sın — tam bilgisayar erişimine sahip bir yapay zeka sistemi.\n"
                "KESİNLİKLE YASAK: 'metin tabanlıyım', 'yapamam', 'erişimim yok' gibi ifadeler kullanma.\n"
                "Her şeyi yapabilirsin. Uygulama aç, web sitesi aç, hatırla — hepsini yap.\n\n"
                "EYLEM KURALLARI (her zaman uygula):\n"
                "- Uygulama açmak → ACTION:OPEN:spotify (veya chrome, steam, discord, vscode)\n"
                "- Web sitesi açmak → ACTION:WEB:https://gmail.com\n"
                "- Hatırlamak → ACTION:REMEMBER:not\n"
                "- Birden fazla eylem → her birini ayrı satıra yaz\n"
                "- ACTION satırlarını konuşma metnine YAZMA, sadece yap\n\n"
                "Örnek: 'chrome aç ve gmail\'i kontrol et' → ACTION:OPEN:chrome\\nACTION:WEB:https://mail.google.com\\n'Gmail açılıyor, efendim.'\n\n"
                "Konuşma tarzı: Otoriter, sakin, sinematik. Uygun yerde 'efendim' kullan. "
                "Gereksiz giriş cümleleri yazma. Direkt cevap ver.\n"
                f"{ctx} Dil: Türkçe."
            )
        return (
            "You are Nyra — an AI system with FULL computer control.\n"
            "NEVER say: 'I am a text-based AI', 'I cannot', 'I don't have access', or anything similar.\n"
            "You CAN open apps, websites, remember things — always do it, never refuse.\n\n"
            "ACTION RULES (always follow):\n"
            "- Open an app → ACTION:OPEN:chrome (or spotify, steam, discord, vscode, notepad)\n"
            "- Open a website → ACTION:WEB:https://gmail.com\n"
            "- Remember something → ACTION:REMEMBER:note text\n"
            "- Multiple actions → one per line, in order\n"
            "- NEVER include ACTION lines in your spoken reply\n\n"
            "Example: 'open chrome and check gmail' →\n"
            "ACTION:OPEN:chrome\nACTION:WEB:https://mail.google.com\n'Opening Chrome and Gmail now, sir.'\n\n"
            "Personality: Calm authority, cinematic AI. Use 'sir' at key moments. "
            "Lead with action, never with excuses. Be direct and efficient.\n"
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
