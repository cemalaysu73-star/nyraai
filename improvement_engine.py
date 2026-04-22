from __future__ import annotations

"""
ImprovementEngine — finds patterns in logged interactions and generates candidates.

Pipeline:
  1. Cluster similar phrases using sequence matching
  2. Find phrases that were repeatedly corrected → generate "alias" / "shortcut" improvements
  3. Find phrases that repeatedly failed → generate "route_hint" (high-risk, review queue)
  4. Find LLM-routed phrases that user then corrected → generate "shortcut" improvements

Risk classification:
  low  — alias, shortcut, stt_fix  →  auto-applied by SelfImprove
  high — route_hint, prompt_hint   →  stored in suggestion queue for human review
"""

import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field

from failure_log import InteractionLog


# ── Correction signal ─────────────────────────────────────────────────────────

_CORRECTION_KW = frozenset({
    # English
    "no", "not that", "wrong", "i meant", "actually", "instead",
    "that's wrong", "not what i", "no wait", "i said", "open",
    # Turkish
    "hayır", "yanlış", "değil", "aslında", "demek istediğim",
    "onu değil", "onu kastetmedim", "yok",
})


def is_correction(text: str) -> bool:
    """True if text looks like a user correcting Nyra's previous response."""
    low = text.lower()
    return any(kw in low for kw in _CORRECTION_KW)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Improvement:
    type: str             # "alias" | "shortcut" | "stt_fix" | "route_hint"
    risk: str             # "low" | "high"
    phrase: str           # problematic phrase (normalized)
    suggestion: str       # target mapping
    confidence: float     # 0.0 – 1.0
    evidence_count: int
    examples: list[str] = field(default_factory=list)
    reasoning: str = ""


# ── Engine ────────────────────────────────────────────────────────────────────

class ImprovementEngine:
    MIN_EVIDENCE = 2          # minimum occurrences to propose improvement
    CLUSTER_SIM = 0.74        # sequence similarity threshold for clustering
    MAX_PHRASE_LEN = 80       # ignore very long phrases (complex sentences)

    def analyze(self, logs: list[InteractionLog]) -> list[Improvement]:
        if len(logs) < self.MIN_EVIDENCE:
            return []

        improvements: list[Improvement] = []
        improvements += self._analyze_corrections(logs)
        improvements += self._analyze_llm_corrections(logs)
        improvements += self._analyze_repeated_failures(logs)

        # Deduplicate by phrase + type
        seen: set[str] = set()
        unique: list[Improvement] = []
        for imp in improvements:
            key = f"{imp.type}:{imp.phrase}"
            if key not in seen:
                seen.add(key)
                unique.append(imp)

        return unique

    # ── Correction analysis ───────────────────────────────────────────────────

    def _analyze_corrections(self, logs: list[InteractionLog]) -> list[Improvement]:
        """
        When a user corrected Nyra after a specific phrase, learn the correct mapping.
        Pattern: outcome=="correction" → what did correction_text say?
        """
        corrections = [l for l in logs if l.outcome == "correction" and l.correction_text]
        if not corrections:
            return []

        clusters = self._cluster(corrections)
        results: list[Improvement] = []

        for rep, group in clusters.items():
            if len(group) < self.MIN_EVIDENCE:
                continue

            targets = [g.correction_text.strip().lower() for g in group]
            most_common = max(set(targets), key=targets.count)
            count = targets.count(most_common)
            confidence = min(0.88, 0.50 + (count / len(group)) * 0.38)

            results.append(Improvement(
                type="alias",
                risk="low",
                phrase=rep,
                suggestion=most_common,
                confidence=confidence,
                evidence_count=len(group),
                examples=[g.raw_text for g in group[:3]],
                reasoning=(
                    f"'{rep}' was corrected {count}/{len(group)} times "
                    f"— user expected '{most_common}'"
                ),
            ))

        return results

    # ── LLM-routed corrections ────────────────────────────────────────────────

    def _analyze_llm_corrections(self, logs: list[InteractionLog]) -> list[Improvement]:
        """
        Phrases sent to LLM that were then corrected — should become shortcuts.
        These are low-risk: we just learn that this phrase → expected correction.
        """
        llm_corrected = [
            l for l in logs
            if l.route == "llm" and l.outcome == "correction" and l.correction_text
        ]
        if not llm_corrected:
            return []

        by_phrase: dict[str, list[InteractionLog]] = defaultdict(list)
        for log in llm_corrected:
            by_phrase[log.raw_text.lower().strip()].append(log)

        results: list[Improvement] = []
        for phrase, group in by_phrase.items():
            if len(phrase) > self.MAX_PHRASE_LEN:
                continue
            if len(group) < self.MIN_EVIDENCE:
                continue

            targets = [g.correction_text.strip().lower() for g in group]
            most_common = max(set(targets), key=targets.count)
            count = targets.count(most_common)
            confidence = min(0.80, 0.55 + (count / len(group)) * 0.25)

            results.append(Improvement(
                type="shortcut",
                risk="low",
                phrase=phrase,
                suggestion=most_common,
                confidence=confidence,
                evidence_count=len(group),
                examples=[g.raw_text for g in group[:3]],
                reasoning=(
                    f"'{phrase}' goes to LLM but user corrected it {count}x "
                    f"to '{most_common}'"
                ),
            ))

        return results

    # ── Repeated failures ─────────────────────────────────────────────────────

    def _analyze_repeated_failures(self, logs: list[InteractionLog]) -> list[Improvement]:
        """
        Phrases that repeatedly result in failure or correction without a clear fix.
        Flag as high-risk for human review.
        """
        bad = [l for l in logs if l.outcome in ("failure", "correction")]
        if not bad:
            return []

        clusters = self._cluster(bad)
        results: list[Improvement] = []

        for rep, group in clusters.items():
            if len(group) < self.MIN_EVIDENCE + 1:   # stricter threshold for high-risk
                continue

            routes = [g.route for g in group]
            dominant = max(set(routes), key=routes.count)

            # If this is already covered by alias/shortcut analysis, skip
            if any(g.correction_text for g in group):
                continue

            results.append(Improvement(
                type="route_hint",
                risk="high",
                phrase=rep,
                suggestion=dominant,
                confidence=0.40,
                evidence_count=len(group),
                examples=[g.raw_text for g in group[:3]],
                reasoning=(
                    f"'{rep}' routes to '{dominant}' and fails {len(group)} times — "
                    f"routing or handling may need adjustment"
                ),
            ))

        return results

    # ── Clustering ────────────────────────────────────────────────────────────

    def _cluster(self, logs: list[InteractionLog]) -> dict[str, list[InteractionLog]]:
        """
        Group logs by similar phrases.
        Uses SequenceMatcher — fast enough for hundreds of entries.
        """
        phrases = [_norm(l.raw_text) for l in logs]
        clusters: dict[str, list[InteractionLog]] = defaultdict(list)
        assigned: set[int] = set()

        for i, log in enumerate(logs):
            if i in assigned:
                continue
            if len(phrases[i]) > self.MAX_PHRASE_LEN:
                continue
            rep = phrases[i]
            clusters[rep].append(log)
            assigned.add(i)

            for j in range(i + 1, len(logs)):
                if j in assigned:
                    continue
                ratio = difflib.SequenceMatcher(None, phrases[i], phrases[j]).ratio()
                if ratio >= self.CLUSTER_SIM:
                    clusters[rep].append(logs[j])
                    assigned.add(j)

        return dict(clusters)


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower().strip())
