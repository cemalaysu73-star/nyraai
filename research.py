from __future__ import annotations

"""
Deep research engine for Nyra.

Supports:
  - deep_research(topic)   — multi-round web research + LLM synthesis
  - price_check(product)   — cross-source price extraction
  - multi_search(queries)  — parallel search on multiple queries

All results are saved to data/reports/ automatically.
"""

import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import search as _search
from config import DATA_DIR
from web_fetch import extract_prices, extract_urls, fetch_text

REPORTS_DIR = DATA_DIR / "reports"

_LLM_LOCK = threading.Lock()


# ── Public API ────────────────────────────────────────────────────────────────

def deep_research(
    topic: str,
    llm_fn: Callable[[str], str] | None = None,
    depth: int = 2,
    language: str = "en",
) -> str:
    """
    Multi-round web research on a topic.

    depth=1  search snippets only (fast, ~5s)
    depth=2  search + fetch top pages (thorough, ~20s)
    depth=3  search + fetch + follow-up queries (deep, ~60s)

    Returns a formatted report string.
    """
    print(f"[Research] Starting: {topic!r} depth={depth}")
    snippets_raw = _search.web_search(topic, max_results=5)
    urls = extract_urls(snippets_raw)

    page_blocks: list[str] = []
    if depth >= 2 and urls:
        page_blocks = _fetch_pages_parallel(urls[:3], max_chars=3500)

    follow_up_raw = ""
    if depth >= 3 and llm_fn:
        follow_up_raw = _follow_up_search(topic, snippets_raw, llm_fn)

    compiled = _compile(topic, snippets_raw, page_blocks, follow_up_raw)

    if llm_fn:
        report = _synthesize(topic, compiled, llm_fn, language)
    else:
        report = compiled[:4000]

    path = _save_report(topic, report)
    print(f"[Research] Done → {path}")
    return report + f"\n\n[Report saved: {path}]"


def price_check(product: str, region: str = "", language: str = "en") -> str:
    """Search for product prices across multiple sources."""
    print(f"[Research] Price check: {product!r} region={region!r}")
    queries = [
        f"{product} price {region}".strip(),
        f"buy {product} {region}".strip(),
        f"{product} cheapest deal".strip(),
    ]

    all_snippets = ""
    for q in queries:
        all_snippets += _search.web_search(q, max_results=4) + "\n"

    urls = extract_urls(all_snippets)
    source_prices: list[tuple[str, list[str]]] = []

    pages = _fetch_pages_parallel(urls[:4], max_chars=2000)
    for i, page in enumerate(pages):
        if page.startswith("[fetch error"):
            continue
        prices = extract_prices(page)
        if prices:
            domain = _domain(urls[i]) if i < len(urls) else "unknown"
            source_prices.append((domain, prices[:4]))

    snippet_prices = extract_prices(all_snippets)

    if not source_prices and not snippet_prices:
        return f"No prices found for '{product}'. Search: {queries[0]}"

    lines: list[str] = []
    if language == "tr":
        lines.append(f"Fiyat araştırması: {product}")
        if region:
            lines.append(f"Bölge: {region}")
    else:
        lines.append(f"Price check: {product}")
        if region:
            lines.append(f"Region: {region}")
    lines.append("")

    if source_prices:
        lines.append("By source:")
        for domain, prices in source_prices:
            lines.append(f"  • {domain}: {', '.join(prices)}")

    if snippet_prices:
        unique = list(dict.fromkeys(snippet_prices))[:8]
        lines.append(f"Prices found: {', '.join(unique)}")

    path = _save_report(f"price_{product}", "\n".join(lines))
    lines.append(f"[Saved: {path}]")
    return "\n".join(lines)


def multi_search(queries: list[str], llm_fn: Callable[[str], str] | None = None) -> str:
    """Search multiple queries in parallel and synthesize."""
    results: list[str] = []
    threads: list[threading.Thread] = []
    lock = threading.Lock()

    def do_search(q: str) -> None:
        r = _search.web_search(q, max_results=3)
        with lock:
            results.append(f"Query: {q}\n{r}")

    for q in queries:
        t = threading.Thread(target=do_search, args=(q,), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=15)

    combined = "\n\n".join(results)

    if llm_fn:
        prompt = (
            f"Synthesize these search results into a clear, concise summary:\n\n"
            f"{combined[:6000]}\n\n"
            "Format: key findings as bullet points, then a 2-sentence conclusion."
        )
        try:
            return llm_fn(prompt)
        except Exception:
            pass

    return combined[:3000]


# ── Internals ─────────────────────────────────────────────────────────────────

def _fetch_pages_parallel(urls: list[str], max_chars: int) -> list[str]:
    """Fetch multiple URLs in parallel threads."""
    results: list[str] = [""] * len(urls)
    threads: list[threading.Thread] = []

    def fetch(i: int, url: str) -> None:
        results[i] = fetch_text(url, max_chars=max_chars)

    for i, url in enumerate(urls):
        t = threading.Thread(target=fetch, args=(i, url), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=15)

    return results


def _follow_up_search(topic: str, initial: str, llm_fn: Callable[[str], str]) -> str:
    """Ask LLM to generate follow-up queries, then search them."""
    prompt = (
        f"Based on this initial research about '{topic}', identify 2-3 specific follow-up "
        f"queries that would fill the most important gaps. Return ONLY the queries, one per line.\n\n"
        f"{initial[:2000]}"
    )
    try:
        with _LLM_LOCK:
            follow_queries_raw = llm_fn(prompt)
        queries = [l.strip() for l in follow_queries_raw.splitlines() if len(l.strip()) > 5][:3]
        return multi_search(queries)
    except Exception:
        return ""


def _compile(topic: str, snippets: str, pages: list[str], follow_up: str) -> str:
    parts = [f"Research topic: {topic}", "", "=== Search Snippets ===", snippets]
    for i, page in enumerate(pages):
        if page and not page.startswith("[fetch error"):
            parts += ["", f"=== Page {i+1} Content ===", page[:2500]]
    if follow_up:
        parts += ["", "=== Follow-up Research ===", follow_up[:2000]]
    return "\n".join(parts)


def _synthesize(topic: str, compiled: str, llm_fn: Callable[[str], str], language: str) -> str:
    if language == "tr":
        prompt = (
            f"Aşağıdaki araştırma verilerini kullanarak '{topic}' konusunda "
            f"kapsamlı ve düzenli bir rapor hazırla.\n\n"
            "Format:\n"
            "• Ana bulgular (madde madde)\n"
            "• Fiyat/tarih/kaynak varsa belirt\n"
            "• 2-3 cümlelik özet\n\n"
            f"Veri:\n{compiled[:7000]}"
        )
    else:
        prompt = (
            f"Write a comprehensive research report about: {topic}\n\n"
            "Use the data below. Format:\n"
            "• Key findings as bullet points\n"
            "• Include prices, dates, sources when available\n"
            "• 2-3 sentence conclusion\n\n"
            f"Data:\n{compiled[:7000]}"
        )
    try:
        with _LLM_LOCK:
            return llm_fn(prompt)
    except Exception as exc:
        return f"Synthesis failed: {exc}\n\n{compiled[:2000]}"


def _save_report(topic: str, content: str) -> str:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\s-]", "", topic)[:40].strip().replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = REPORTS_DIR / f"{safe}_{ts}.txt"
    path.write_text(
        f"Nyra Research Report\n"
        f"Topic: {topic}\n"
        f"Date:  {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'='*60}\n\n{content}",
        encoding="utf-8",
    )
    return str(path)


def _domain(url: str) -> str:
    try:
        return url.split("//")[1].split("/")[0].replace("www.", "")
    except Exception:
        return url[:30]
