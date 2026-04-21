from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from config import DATA_DIR
from ml_engine import get_engine
from router import RouteResult

INTENT_FILE = DATA_DIR / "intents.json"

# How many times a (phrase-cluster, app-set) pair must be observed before
# Nyra auto-executes it without going through the LLM.
LEARN_THRESHOLD = 3

# Phrase similarity to merge a new observation into an existing group
GROUP_THRESHOLD = 0.52

# Phrase similarity to fire a promoted (auto-execute) intent
MATCH_THRESHOLD = 0.62

# First word of a direct command — never learn these
_DIRECT_VERBS = frozenset({
    "open", "launch", "run", "start", "close", "quit", "kill", "exit",
    "search", "find", "play", "pause", "stop", "resume", "next", "previous",
    "volume", "mute", "unmute", "type", "click", "download", "install",
    "aç", "ac", "başlat", "baslat", "çalıştır", "calistir",
    "kapat", "kapa", "çık", "cik", "ara", "bul", "çal", "durdur",
    "indir", "kur", "yükle",
})


# ── Text helpers ──────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower().strip())

def _tokens(text: str) -> set[str]:
    return set(_normalize(text).split())

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

def _score(q: str, p: str) -> float:
    qn, pn = _normalize(q), _normalize(p)
    if qn == pn:
        return 1.0
    engine = get_engine()
    if engine.available:
        return engine.similarity(q, p)
    # Jaccard fallback (always works, no deps)
    s = _jaccard(_tokens(q), _tokens(p))
    if pn in qn or qn in pn:
        s = max(s, 0.58)
    return s

def is_direct_command(text: str) -> bool:
    parts = _normalize(text).split()
    return bool(parts and parts[0] in _DIRECT_VERBS)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class _UsageGroup:
    """
    A cluster of similar phrases that consistently led to the same app set.
    Promoted (auto-executes) once observations >= LEARN_THRESHOLD.
    """
    id: str
    representative: str         # most recent phrase used
    phrases: list[str]          # all observed phrase variants
    apps: list[str]             # sorted action keys: ["OPEN:spotify", "WEB:https://twitch.tv"]
    observations: int           # how many times seen
    promoted: bool              # True once threshold met
    last_seen: float
    created: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "representative": self.representative,
            "phrases": self.phrases,
            "apps": self.apps,
            "observations": self.observations,
            "promoted": self.promoted,
            "last_seen": self.last_seen,
            "created": self.created,
        }

    @staticmethod
    def from_dict(d: dict) -> "_UsageGroup":
        return _UsageGroup(
            id=d.get("id", str(uuid.uuid4())[:8]),
            representative=d.get("representative", ""),
            phrases=d.get("phrases", []),
            apps=d.get("apps", []),
            observations=d.get("observations", 0),
            promoted=d.get("promoted", False),
            last_seen=d.get("last_seen", 0.0),
            created=d.get("created", 0.0),
        )

    @property
    def app_labels(self) -> list[str]:
        labels = []
        for a in self.apps:
            if a.upper().startswith("OPEN:"):
                labels.append(a[5:].title())
            elif a.upper().startswith("WEB:"):
                domain = re.sub(r"https?://(www\.)?", "", a[4:]).split("/")[0]
                labels.append(domain)
        return labels


@dataclass
class MatchResult:
    group: _UsageGroup
    score: float


# ── IntentStore ───────────────────────────────────────────────────────────────

class IntentStore:

    def __init__(self) -> None:
        self._groups: list[_UsageGroup] = []
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> None:
        if not INTENT_FILE.exists():
            return
        try:
            raw = json.loads(INTENT_FILE.read_text(encoding="utf-8"))
            self._groups = [_UsageGroup.from_dict(d) for d in raw.get("groups", [])]
        except Exception:
            self._groups = []

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        INTENT_FILE.write_text(
            json.dumps({"groups": [g.to_dict() for g in self._groups]}, indent=2),
            encoding="utf-8",
        )

    # ── Core API ──────────────────────────────────────────────────────────────

    def record(self, phrase: str, apps: list[str]) -> None:
        """
        Call this every time Nyra opens apps in response to a voice phrase.
        apps = sorted list of action keys, e.g. ["OPEN:spotify", "WEB:https://twitch.tv"]
        Once the same (phrase-cluster, app-set) is seen LEARN_THRESHOLD times,
        the intent is promoted and will auto-execute next time.
        """
        if is_direct_command(phrase) or not apps:
            return

        normalized_apps = sorted(apps)
        group = self._find_group(phrase, normalized_apps)

        if group:
            group.observations += 1
            group.last_seen = time.time()
            group.representative = phrase
            if phrase not in group.phrases:
                group.phrases.append(phrase)
            if not group.promoted and group.observations >= LEARN_THRESHOLD:
                group.promoted = True
                labels = ", ".join(group.app_labels)
                print(f"[Intent] Auto-execute unlocked: '{phrase}' -> {labels}")
        else:
            group = _UsageGroup(
                id=str(uuid.uuid4())[:8],
                representative=phrase,
                phrases=[phrase],
                apps=normalized_apps,
                observations=1,
                promoted=False,
                last_seen=time.time(),
                created=time.time(),
            )
            self._groups.append(group)
            remaining = LEARN_THRESHOLD - 1
            labels = ", ".join(group.app_labels)
            print(f"[Intent] Tracking: '{phrase}' -> {labels} ({remaining} more needed)")

        self.save()

    def match(self, text: str) -> Optional[MatchResult]:
        """Return the best promoted group matching text, or None."""
        best: Optional[MatchResult] = None
        for group in self._groups:
            if not group.promoted:
                continue
            top = max((_score(text, p) for p in group.phrases), default=0.0)
            if top >= MATCH_THRESHOLD:
                if best is None or top > best.score:
                    best = MatchResult(group=group, score=top)
        return best

    def hit(self, group: _UsageGroup) -> None:
        group.observations += 1
        group.last_seen = time.time()
        self.save()

    def progress(self, phrase: str, apps: list[str]) -> int:
        """Return how many more observations are needed before auto-execute (0 = promoted)."""
        g = self._find_group(phrase, sorted(apps))
        if g is None:
            return LEARN_THRESHOLD
        if g.promoted:
            return 0
        return max(0, LEARN_THRESHOLD - g.observations)

    def all_groups(self) -> list[_UsageGroup]:
        return list(self._groups)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _find_group(self, phrase: str, apps: list[str]) -> Optional[_UsageGroup]:
        """Find an existing group with the same app set and a similar enough phrase."""
        for group in self._groups:
            if group.apps != apps:
                continue    # different app set = different intent
            best = max((_score(phrase, p) for p in group.phrases), default=0.0)
            if best >= GROUP_THRESHOLD:
                return group
        return None


# ── Route conversion helpers ──────────────────────────────────────────────────

def group_to_routes(group: _UsageGroup) -> list[RouteResult]:
    """Convert a matched group into a list of RouteResults to execute."""
    results = []
    for a in group.apps:
        if a.upper().startswith("OPEN:"):
            app = a[5:].lower().strip()
            results.append(RouteResult(True, "launch_app", {"app": app}))
        elif a.upper().startswith("WEB:"):
            url = a[4:].strip()
            results.append(RouteResult(True, "open_web", {"url": url}))
    return results


def actions_to_keys(routes: list[RouteResult]) -> list[str]:
    """Convert RouteResults to sorted action key strings for storage."""
    keys = []
    for r in routes:
        if r.action == "launch_app":
            keys.append(f"OPEN:{r.params.get('app', '').lower()}")
        elif r.action == "open_web":
            keys.append(f"WEB:{r.params.get('url', '')}")
    return sorted(keys)
