from __future__ import annotations

"""
ModeDetector — classifies user input into one of four operational modes.

Modes and what they change:
  ACTION   — fast execution, one-sentence reply, no tools
  RESEARCH — ReAct loop, tool access, thorough synthesis
  QUICK    — simple factual, 1-2 sentence answer, no tools
  CHAT     — conversational, warm tone, no tools

Detection is pure regex + heuristics — never calls LLM, never blocks.
"""

import re
from enum import Enum


class Mode(str, Enum):
    ACTION   = "action"
    RESEARCH = "research"
    QUICK    = "quick"
    CHAT     = "chat"


# ── Pattern libraries ─────────────────────────────────────────────────────────

# Imperative verbs that start a command
_ACTION_START = re.compile(
    r"^(?:open|close|launch|start|run|play|pause|resume|stop|quit|kill|exit|"
    r"install|download|uninstall|remove|set|turn on|turn off|switch|"
    r"go to|navigate|show|bring up|load|mute|unmute|maximize|minimize|"
    r"lock|sleep|restart|reboot|"
    # Turkish
    r"aç|ac|kapat|kapa|başlat|baslat|çalıştır|calistir|çal|cal|"
    r"durdur|kur|indir|yükle|yukle|ara|bul|git|göster|goster|"
    r"kilitle|yeniden|kapat)\b",
    re.I,
)

# Deep analysis, comparison, realtime data — all need the agent loop
_RESEARCH_SIGNALS = re.compile(
    r"\b(?:"
    # Deep analysis
    r"research|analyze|analyse|investigate|compare|summarize|"
    r"explain (?:in detail|how|why)|give me a (?:report|summary|overview)|"
    r"what (?:is the best|are the pros|are the differences|are the advantages)|"
    r"which is better|help me understand|deep dive|"
    r"history of|overview of|how does .{3,40} work|"
    r"what causes|what are the implications|tell me everything about|"
    # Realtime / current data
    r"what(?:'s| is) (?:the )?(?:current|latest|today'?s?|price|status|weather)|"
    r"how much (?:is|does|cost)|price of|cost of|"
    r"latest (?:news|updates?|price|release|version)|"
    r"current (?:price|status|version)|right now|"
    r"is .{3,30} (?:down|available|working|running)|"
    r"this (?:week|month|year)|find out|look up|search for|search\b|"
    # Turkish — deep
    r"araştır|analiz et|karşılaştır|en iyi[si]?|hangisi daha|"
    r"nasıl çalışır|neden|tarih[ici]|özet|rapor|"
    # Turkish — realtime
    r"fiyat[ıi]|kaç (?:lira|euro|dolar)|ne kadar|şu an|güncel|"
    r"son (?:haber|fiyat|sürüm|güncelleme)|bu (?:hafta|ay|yıl)|"
    r"(?:^|\s)ara\b|bul\b"
    r")\b",
    re.I,
)

# Short factual questions with a definite answer
_QUICK_START = re.compile(
    r"^(?:what (?:is|are|was|were)|what's|"
    r"who (?:is|was|are|were|invented|made|created|discovered|wrote|founded|built)|who's|"
    r"when (?:did|is|was|were|does)|"
    r"where (?:is|was|are)|where's|"
    r"how (?:many|much|old|far|long|tall|big|small)|"
    r"is it|are there|define |meaning of|capital of|population of|"
    # Turkish
    r"ne (?:zaman|demek)|nerede|kim |kaç |hangi )\b",
    re.I,
)

# Conversational openers
_CHAT_SIGNALS = re.compile(
    r"\b(?:how are you|how'?s it going|tell me a joke|what do you think|"
    r"can we (?:talk|chat)|i (?:feel|think|believe|wonder)|"
    r"do you (?:like|know|think|want|enjoy)|what'?s your (?:opinion|favorite|take)|"
    r"thank(?:s| you)|good (?:morning|night|evening|afternoon)|"
    r"hey|hi |"
    # Turkish
    r"nasılsın|şaka yap|ne düşünüyorsun|teşekkür|günaydın|iyi geceler|"
    r"merhaba|selam)\b",
    re.I,
)


# ── Public API ────────────────────────────────────────────────────────────────

def detect(text: str, is_code: bool = False) -> Mode:
    """
    Classify text into one of four operational modes.
    is_code=True forces RESEARCH (uses the agent loop with tools).
    """
    if is_code:
        return Mode.RESEARCH

    stripped = text.strip()
    words = stripped.split()
    word_count = len(words)

    # Strong action signal: imperative verb at start, short command
    if _ACTION_START.match(stripped):
        # Don't classify "find out everything about X" as action
        if word_count <= 10 and not _RESEARCH_SIGNALS.search(stripped):
            return Mode.ACTION

    # Research signals override everything else
    if _RESEARCH_SIGNALS.search(stripped):
        return Mode.RESEARCH

    # Quick factual: matches pattern AND is concise (≤12 words)
    if _QUICK_START.match(stripped) and word_count <= 12:
        return Mode.QUICK

    # Chat
    if _CHAT_SIGNALS.search(stripped):
        return Mode.CHAT

    # Heuristic fallback
    if stripped.endswith("?") and word_count <= 7:
        return Mode.QUICK
    if word_count >= 15:
        return Mode.RESEARCH

    return Mode.CHAT


# ── Display helpers ───────────────────────────────────────────────────────────

_MODE_STATUS = {
    Mode.ACTION:   "Executing",
    Mode.RESEARCH: "Researching",
    Mode.QUICK:    "Thinking",
    Mode.CHAT:     "Thinking",
}

_MODE_STATUS_TR = {
    Mode.ACTION:   "Çalışıyor",
    Mode.RESEARCH: "Araştırıyor",
    Mode.QUICK:    "Düşünüyor",
    Mode.CHAT:     "Düşünüyor",
}


def status_text(mode: Mode, language: str = "en") -> str:
    if language == "tr":
        return _MODE_STATUS_TR.get(mode, "Düşünüyor")
    return _MODE_STATUS.get(mode, "Thinking")
