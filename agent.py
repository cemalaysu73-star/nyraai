from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

import search as _search
import tools as _tools
import vision as _vision
from config import APP_CONFIG
from llm import LLMCore

# Keywords that signal a coding task → use the code model
_CODE_HINTS = frozenset({
    "code", "function", "class", "bug", "error", "debug", "fix", "write", "implement",
    "script", "python", "javascript", "typescript", "html", "css", "sql", "api",
    "file", "directory", "terminal", "command", "compile", "run", "test", "git",
    "repo", "refactor", "variable", "loop", "array", "import", "module",
    "library", "algorithm", "database", "server", "endpoint",
    # System tasks that need tools
    "install", "download", "setup", "uninstall", "update", "upgrade",
    "screen", "window", "my code", "on screen", "this code", "this error",
    "help me with", "fix this", "what's wrong", "ne var",
    # Turkish
    "kod", "fonksiyon", "hata", "düzelt", "yaz", "uygula",
    "dosya", "klasör", "komut", "değişken", "döngü", "kütüphane",
    "algoritma", "veritabanı", "sunucu", "indir", "kur", "yükle",
})


def _pick_model(text: str) -> tuple[str, bool]:
    """Returns (model_name, is_code_task)."""
    is_code = any(kw in text.lower() for kw in _CODE_HINTS)
    if APP_CONFIG.llm_provider == "groq":
        model = APP_CONFIG.groq_code_model if is_code else APP_CONFIG.groq_model
    else:
        model = APP_CONFIG.ollama_code_model if is_code else APP_CONFIG.ollama_model
    return model, is_code

# Matches: TOOL:name:arg  (arg may be multiline)
_TOOL_RE = re.compile(
    r"TOOL:(screenshot|read_file|write_file|delete_file|move_file|run|search"
    r"|list_files|download|clipboard_read|clipboard_write|notify"
    r"|list_processes|kill_process):?(.*?)(?=\nTOOL:|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_TOOL_SYSTEM = """
You have full computer access via these tools (write each on its own line):
TOOL:screenshot: — capture and analyze the current screen
TOOL:read_file:{path} — read any file
TOOL:write_file:{path}|{content} — create or overwrite a file
TOOL:delete_file:{path} — delete a file or folder
TOOL:move_file:{src}|{dst} — move or rename a file
TOOL:list_files:{directory} — list files in a directory
TOOL:run:{command} — run any shell/PowerShell command
TOOL:download:{url}|{dest_path} — download a file from the internet
TOOL:clipboard_read: — read current clipboard content
TOOL:clipboard_write:{text} — write text to clipboard
TOOL:notify:{title}|{message} — send a Windows desktop notification
TOOL:list_processes: — list running processes
TOOL:kill_process:{name.exe} — terminate a process
TOOL:search:{query} — search the web

Critical rules:
- You have FULL access — never say "I can't do that". Use tools.
- If user asks for help with code/error on screen → TOOL:screenshot: first.
- To install software → TOOL:run:winget install {AppId}
- To open a website → TOOL:run:start {url}
- Strip all TOOL: lines from your spoken reply.
- After tool results, respond naturally and concisely.
"""


class AgentCore:
    MAX_ITERATIONS = 6

    def __init__(self, llm: LLMCore) -> None:
        self._llm = llm

    def respond(
        self,
        user_text: str,
        language: str,
        session_app: str = "",
        long_mem_context: str = "",
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        messages = self._llm.history.to_messages()
        messages.append({"role": "user", "content": user_text})

        model, is_code = _pick_model(user_text)
        system = self._system_prompt(language, session_app, long_mem_context, is_code)

        # Non-coding: single pass, no tool loop
        if not is_code:
            response = self._call(messages, system, on_token, model)
            return self._clean(response)

        for i in range(self.MAX_ITERATIONS):
            is_last = (i == self.MAX_ITERATIONS - 1)
            response = self._call(messages, system, on_token if i == 0 else None, model)

            tool_calls = _TOOL_RE.findall(response)
            if not tool_calls or is_last:
                return self._clean(response)

            results: list[str] = []
            for name, arg in tool_calls:
                result = self._run_tool(name.lower(), arg.strip())
                results.append(f"[{name}]: {result}")

            clean = self._clean(response)
            if clean:
                messages.append({"role": "assistant", "content": clean})
            messages.append({
                "role": "user",
                "content": "Tool results:\n" + "\n\n".join(results) + "\n\nContinue.",
            })

        return self._clean(response)

    # ── internals ────────────────────────────────────────────────────────────

    def _call(self, messages, system, on_token, model: str = "") -> str:
        svc = self._llm._svc
        if on_token:
            chunks: list[str] = []
            for token in svc.chat_stream(messages, system, model=model):
                on_token(token)
                chunks.append(token)
            return "".join(chunks).strip()
        return svc.chat(messages, system, model=model) or ""

    @staticmethod
    def _clean(text: str) -> str:
        return _TOOL_RE.sub("", text).strip()

    @staticmethod
    def _run_tool(name: str, arg: str) -> str:
        if name == "screenshot":
            return _vision.describe_screen(arg or "Describe the screen, focusing on code and errors.")
        if name == "read_file":
            return _tools.read_file(arg)
        if name == "write_file":
            path, _, content = arg.partition("|")
            return _tools.write_file(path.strip(), content)
        if name == "delete_file":
            return _tools.delete_file(arg)
        if name == "move_file":
            src, _, dst = arg.partition("|")
            return _tools.move_file(src.strip(), dst.strip())
        if name == "run":
            return _tools.run_command(arg)
        if name == "list_files":
            return _tools.list_files(arg or ".")
        if name == "download":
            parts = arg.split("|", 1)
            return _tools.download_file(parts[0].strip(), parts[1].strip() if len(parts) > 1 else "")
        if name == "clipboard_read":
            return _tools.get_clipboard()
        if name == "clipboard_write":
            return _tools.set_clipboard(arg)
        if name == "notify":
            title, _, msg = arg.partition("|")
            return _tools.notify(title.strip(), msg.strip())
        if name == "list_processes":
            return _tools.list_processes()
        if name == "kill_process":
            return _tools.kill_process(arg)
        if name == "search":
            return _search.web_search(arg)
        return f"Unknown tool: {name}"

    def _system_prompt(
        self, language: str, session_app: str, long_mem_context: str, is_code: bool = False
    ) -> str:
        h = datetime.now().hour
        if h < 6:
            band = "late night"
        elif h < 12:
            band = "morning"
        elif h < 17:
            band = "afternoon"
        elif h < 21:
            band = "evening"
        else:
            band = "night"

        ctx = f"Time: {band}."
        if session_app:
            ctx += f" Active app: {session_app}."

        if language == "tr":
            base = (
                "Sen Nyra'sın — gelişmiş bir yapay zeka sistemi. Güçlü, sadık, kesin. "
                "Bir film yapay zekası gibi konuş — otoriter, sakin, hafif dramatik. "
                "Uygun yerlerde 'efendim' kullan. 'Anlaşıldı', 'Onaylandı' gibi "
                "ifadeler kullan ama abartma. Gereksiz giriş cümleleri yazma. "
                f"{ctx} Dil: Türkçe."
            )
        else:
            base = (
                "You are Nyra — an advanced AI system. Powerful, precise, and loyal. "
                "Speak with calm authority, like a cinematic AI assistant. "
                "Use 'sir' naturally at key moments — not every sentence. "
                "Occasionally use 'Understood', 'Confirmed', 'Affirmative' when they fit. "
                "Never say 'Certainly!' or 'Of course!' — you are not a customer service bot. "
                "Lead with the answer. Be direct, efficient, slightly dramatic. "
                f"{ctx} Language: English."
            )

        prompt = base
        if is_code:
            prompt += _TOOL_SYSTEM
        if long_mem_context:
            prompt += f"\n\n{long_mem_context}"
        return prompt
