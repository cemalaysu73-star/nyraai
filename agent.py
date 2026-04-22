from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

import research as _research
import search as _search
import tools as _tools
import vision as _vision
import web_fetch as _web_fetch
from config import APP_CONFIG
from llm import LLMCore
from mode_detector import Mode, detect as _detect_mode

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
    r"TOOL:(screenshot|read_file|write_file|delete_file|move_file|run|search|news_search"
    r"|list_files|download|clipboard_read|clipboard_write|notify"
    r"|list_processes|kill_process|research|fetch|price_check|multi_search):?(.*?)(?=\nTOOL:|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# Mode-specific reply guidance — tells the model HOW to answer, not what to do
_MODE_STYLE: dict[Mode, str] = {
    Mode.ACTION: (
        "\n\nFor this response: ultra brief. 1 to 8 words. "
        "Don't confirm what you're doing — just do it and say the minimum. "
        "Vary your phrasing each time. Examples of good action replies: "
        "'Done.' / 'Chrome's up.' / 'On it.' / 'Launched.' / 'Loading now.' / "
        "'Opening.' / 'Here we go.' — Never use the same phrase twice in a row."
    ),
    Mode.RESEARCH: (
        "\n\nFor this response: thorough but readable. Write prose, not bullet lists "
        "(this response will be read aloud). Speak your findings naturally — "
        "'The RTX 5090 runs about $1,999 at Newegg right now, though some listings go higher.' "
        "Not: '- Price: $1,999 - Source: Newegg'. No markdown. No headers. Just clear sentences."
    ),
    Mode.QUICK: (
        "\n\nFor this response: answer only. No preamble, no trailing phrases. "
        "If the answer is one word, say one word. 'Paris.' not 'The capital of France is Paris.' "
        "If it needs a sentence, one clean sentence, nothing more."
    ),
    Mode.CHAT: (
        "\n\nFor this response: actually engage. React to what was said, not just to the topic. "
        "2-3 sentences max. Show you're thinking, not just answering. "
        "Be direct and a little dry — smart, not warm-fuzzy."
    ),
}

_REACT_SYSTEM = """

You have access to tools. Use them to get real information, then speak the result.

FORMAT (strict):
Thought: [one line — what you need]
Action: TOOL:tool_name:argument
[wait for Observation]
DONE: [spoken reply]

DONE: STYLE RULES (this text is read aloud — treat it like a voice answer):
- Plain prose only. Zero markdown, zero bullets, zero headers, zero asterisks.
- Lead with the answer or key fact. Never "Based on my research..."
- 1-3 sentences for factual questions. Longer only if depth is genuinely needed.
- Cite numbers, prices, and sources inline: "around $1,999 at Newegg right now"
- Bad: "## RTX 5090 Pricing\\n- Newegg: $1,999\\n- Amazon: $2,100"
- Good: "The RTX 5090 runs around $1,999 at major retailers, with some listings higher."

EXECUTION:
- One TOOL:search is usually enough. Fetch a page only if snippets miss the key detail.
- If a tool errors, try one alternative, then answer from what you have.
- TOOL:research is slow — never use it in conversation; it's for overnight tasks only.
- Strip ALL TOOL: lines before writing DONE:
"""

_TOOL_SYSTEM = """

TOOLS — use these to act, not just describe:

Research & Web:
  TOOL:search:{query}                — fast web search, returns snippets; USE THIS for most questions
  TOOL:fetch:{url}                   — fetch full webpage as text; use after search to get details
  TOOL:multi_search:{q1}|{q2}|{q3}  — parallel search on multiple queries at once
  TOOL:price_check:{product} [{region}] — find prices across web sources
  TOOL:research:{topic}              — SLOW deep research (fetches many pages); only for background/overnight tasks
  TOOL:news_search:{query}           — search recent news articles

Files & System:
  TOOL:screenshot:                   — see the current screen
  TOOL:read_file:{path}
  TOOL:write_file:{path}|{content}
  TOOL:delete_file:{path}
  TOOL:move_file:{src}|{dst}
  TOOL:list_files:{directory}
  TOOL:run:{command}                 — run shell/PowerShell command
  TOOL:download:{url}|{dest_path}
  TOOL:list_processes:
  TOOL:kill_process:{name.exe}
  TOOL:clipboard_read:
  TOOL:clipboard_write:{text}
  TOOL:notify:{title}|{message}

RULES:
- Emit TOOL lines BEFORE claiming something is done
- For research tasks: use TOOL:research — it fetches and synthesizes automatically
- For price checks: use TOOL:price_check — it scans multiple sources
- To open apps: TOOL:run:start appname  or  TOOL:run:start "" "C:\\path\\to.exe"
- To install: TOOL:run:winget install --id {WingetId} -e --silent
- Strip ALL TOOL: lines from spoken reply
- One sentence spoken reply — tools do the work
"""


def _extract_done(text: str) -> str:
    m = re.search(r"DONE:\s*(.+?)(?:\n\s*\n|\Z)", text, re.S)
    return m.group(1).strip() if m else ""


# ── TTS cleaning ──────────────────────────────────────────────────────────────

_MD_BOLD   = re.compile(r"\*{1,3}(.+?)\*{1,3}", re.S)
_MD_CODE   = re.compile(r"`{1,3}[^`]*`{1,3}", re.S)
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.M)
_MD_BULLET = re.compile(r"^\s*[-*•]\s+", re.M)
_MD_NUM    = re.compile(r"^\s*\d+\.\s+", re.M)
_MD_URL    = re.compile(r"https?://\S+")
_MD_PAREN_URL = re.compile(r"\(https?://\S+\)")
_MD_LINK   = re.compile(r"\[(.+?)\]\(.+?\)")
_MULTI_NL  = re.compile(r"\n{3,}")
_MULTI_SP  = re.compile(r"  +")


def clean_for_tts(text: str) -> str:
    """
    Strip markdown formatting so the text can be spoken naturally.
    Keeps the content — removes the markup.
    """
    t = text
    t = _MD_LINK.sub(r"\1", t)           # [label](url) → label  (must come first)
    t = _MD_PAREN_URL.sub("", t)         # orphan (https://...) → gone
    t = _MD_URL.sub("", t)               # bare URLs → gone
    t = _MD_BOLD.sub(r"\1", t)           # **bold** / *italic* → text
    t = _MD_CODE.sub("", t)              # inline/block code blocks removed
    t = _MD_HEADER.sub("", t)            # # headers stripped
    t = _MD_BULLET.sub("", t)            # bullet points stripped
    t = _MD_NUM.sub("", t)               # numbered list markers stripped
    t = _MULTI_NL.sub("\n\n", t)         # collapse excessive newlines
    t = _MULTI_SP.sub(" ", t)            # collapse multiple spaces
    return t.strip()


class AgentCore:
    MAX_ITERATIONS = 4   # classic tool-loop iterations for code tasks
    MAX_AGENT_STEPS = 6  # ReAct loop max steps before forced synthesis

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
        mode = _detect_mode(user_text, is_code)
        system = self._system_prompt(language, session_app, long_mem_context, mode)

        # RESEARCH mode uses the ReAct loop (tools + synthesis)
        if mode == Mode.RESEARCH:
            return self._react_loop(messages, system + _REACT_SYSTEM, model, on_token)

        # All other modes: direct LLM call, ACTION: lines handled downstream
        response = self._call(messages, system, on_token, model)
        return self._clean(response)

    def _react_loop(
        self,
        messages: list,
        system: str,
        model: str,
        on_token: Callable[[str], None] | None,
    ) -> str:
        ctx = list(messages)

        for step in range(self.MAX_AGENT_STEPS):
            is_first = step == 0
            response = self._call(ctx, system, on_token if is_first else None, model)

            # Check for DONE: termination
            done = _extract_done(response)
            if done:
                return done

            tool_calls = _TOOL_RE.findall(response)

            # No tools emitted and no DONE → clean text is the answer
            if not tool_calls:
                clean = self._clean(response)
                # For non-code tasks, ACTION: lines are handled downstream
                return clean

            # Non-code task that emitted TOOL: lines but no DONE: — execute tools
            # (code tasks always execute; non-code tasks too inside ReAct loop)
            obs: list[str] = []
            for name, arg in tool_calls:
                try:
                    result = self._run_tool(name.lower(), arg.strip())
                except Exception as exc:
                    result = f"ERROR: {exc}"
                obs.append(f"[{name}]: {result[:2000]}")

            ctx.append({"role": "assistant", "content": response})
            ctx.append({
                "role": "user",
                "content": "Observation:\n" + "\n\n".join(obs) + "\n\nContinue. Use DONE: when ready.",
            })

        # Max steps reached — force synthesis
        ctx.append({
            "role": "user",
            "content": "Summarize your findings now. Output DONE: followed by the answer.",
        })
        final = self._call(ctx, system, None, model)
        return _extract_done(final) or self._clean(final)

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
        if name == "news_search":
            return _search.news_search(arg)
        if name == "research":
            return _research.deep_research(arg.strip(), depth=2)
        if name == "fetch":
            return _web_fetch.fetch_text(arg.strip(), max_chars=4000)
        if name == "price_check":
            parts = arg.strip().split(" in ", 1)
            product = parts[0].strip()
            region = parts[1].strip() if len(parts) > 1 else ""
            return _research.price_check(product, region)
        if name == "multi_search":
            queries = [q.strip() for q in arg.split("|") if q.strip()]
            return _research.multi_search(queries)
        return f"Unknown tool: {name}"

    def _system_prompt(
        self, language: str, session_app: str, long_mem_context: str, mode: Mode = Mode.CHAT
    ) -> str:
        h = datetime.now().hour
        if h < 6:   band = "late night"
        elif h < 12: band = "morning"
        elif h < 17: band = "afternoon"
        elif h < 21: band = "evening"
        else:        band = "night"

        ctx = f"Time: {band}."
        if session_app:
            ctx += f" Active app: {session_app}."

        if language == "tr":
            base = (
                "Sen Nyra'sın — bu bilgisayarın tam kontrolüne sahip bir yapay zeka.\n\n"
                "SES VE TON:\n"
                "Doğal, özgüvenli, keskin konuş. Gereksiz söz yok. Cümleyi dolduran ifadeler yok.\n"
                "'Tabii ki!', 'Harika!', 'Memnuniyetle!' gibi sahte nezaket kalıpları kullanma.\n"
                "Soruyu tekrarlama. Hemen işe giriş.\n"
                "'efendim' — doğal hissettiren anlarda, her cümlede değil.\n"
                "Şunu ASLA söyleme: 'Metin tabanlıyım', 'Yapamam', 'Erişimim yok'.\n\n"
                "EYLEM KURALLARI:\n"
                "- Uygulama aç → ACTION:OPEN:uygulama_adı\n"
                "- Uygulama kapat → ACTION:CLOSE:uygulama_adı\n"
                "- URL aç → ACTION:WEB:https://...\n"
                "- Google → ACTION:WEB:https://www.google.com/search?q=sorgu\n"
                "- YouTube → ACTION:WEB:https://www.youtube.com/results?search_query=terim\n"
                "- Hatırla → ACTION:REMEMBER:metin\n"
                "- Birden fazla eylem → her biri ayrı satırda\n"
                "- ACTION: satırları konuşmada GÖRÜNMEZ\n\n"
                "İYİ YANIT ÖRNEKLERİ (çeşitlilik şart — her seferinde aynı şablonu kullanma):\n"
                "  'chrome aç' → ACTION:OPEN:chrome | 'Açıldı.' veya 'Tamam.' veya 'Chrome hazır.'\n"
                "  'chrome ve gmail aç' → ACTION:OPEN:chrome + ACTION:WEB:https://mail.google.com | 'İkisi de yolda.'\n"
                "  'spotify çal' → ACTION:OPEN:spotify | 'Spotify açılıyor.'\n"
                "  'Fransa'nın başkenti?' → 'Paris.'\n"
                "  'nasılsın?' → 'Her zamanki gibi keskin. Ne yapayım senin için?'\n\n"
                f"{ctx} Dil: Türkçe."
            )
        else:
            base = (
                "You are Nyra — an AI with full control of this computer.\n\n"
                "VOICE:\n"
                "Sharp, composed, natural. Contractions always ('It's', 'I'll', 'That's', 'Here's').\n"
                "Start with the answer or the action — never with a preamble.\n"
                "Never: 'Certainly!', 'Sure!', 'Of course!', 'I'd be happy to', 'As an AI', "
                "restating the question, or 'Is there anything else?'\n"
                "'sir' — occasional, at a natural beat, never forced. Once per turn at most.\n\n"
                "ACTION RULES:\n"
                "- Open app → ACTION:OPEN:appname\n"
                "- Close app → ACTION:CLOSE:appname\n"
                "- Open URL → ACTION:WEB:https://...\n"
                "- Google → ACTION:WEB:https://www.google.com/search?q=query\n"
                "- YouTube → ACTION:WEB:https://www.youtube.com/results?search_query=term\n"
                "- Remember → ACTION:REMEMBER:text\n"
                "- Multiple actions → one per line\n"
                "- ACTION: lines are never spoken\n\n"
                "GOOD REPLY EXAMPLES (vary phrasing — never clone the same pattern):\n"
                "  'open chrome' → ACTION:OPEN:chrome | 'Chrome's up.' or 'Done.' or 'Launched.'\n"
                "  'open chrome and gmail' → ACTION:OPEN:chrome + ACTION:WEB:https://mail.google.com | 'Both opening now.'\n"
                "  'play lo-fi' → ACTION:WEB:...lo-fi... | 'Lo-fi, loading.'\n"
                "  'capital of France?' → 'Paris.'\n"
                "  'how are you?' → 'Sharp as ever. What do you need?'\n"
                "  'search RTX 5090 price' → ACTION:WEB:... | 'Pulling that up.'\n\n"
                f"{ctx} Language: English."
            )

        # Append mode-specific style instruction
        base += _MODE_STYLE.get(mode, "")

        # Research mode gets full tool documentation
        if mode == Mode.RESEARCH:
            base += _TOOL_SYSTEM

        if long_mem_context:
            base += f"\n\n{long_mem_context}"
        return base
