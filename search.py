from __future__ import annotations

import time
import threading

try:
    from duckduckgo_search import DDGS
    _OK = True
except ImportError:
    _OK = False

# ── Simple TTL cache — avoids hitting DDG twice for the same query ────────────

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 300.0   # 5 minutes
_CACHE_LOCK = threading.Lock()


def _cached(key: str) -> str | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
    if entry and time.monotonic() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _store(key: str, value: str) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
        # Evict oldest entries if cache grows large
        if len(_CACHE) > 200:
            oldest = sorted(_CACHE, key=lambda k: _CACHE[k][0])
            for k in oldest[:50]:
                del _CACHE[k]


# ── Public API ────────────────────────────────────────────────────────────────

def web_search(query: str, max_results: int = 4) -> str:
    """Search DuckDuckGo and return snippets + URLs. Results cached 5 min."""
    if not _OK:
        return "Web search unavailable. Run: pip install duckduckgo-search"

    key = f"text:{query}:{max_results}"
    cached = _cached(key)
    if cached:
        return cached

    try:
        results: list[str] = []
        with DDGS(timeout=8) as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(f"{r['title']}\n{r['body']}\n{r['href']}")
        out = "\n\n---\n\n".join(results) if results else "No results found."
        _store(key, out)
        return out
    except Exception as e:
        return f"Search error: {e}"


def get_urls(query: str, max_results: int = 5) -> list[str]:
    """Return a list of URLs from a search query."""
    if not _OK:
        return []
    key = f"urls:{query}:{max_results}"
    cached = _cached(key)
    if cached:
        return cached.split("\n") if cached else []
    try:
        with DDGS(timeout=8) as ddgs:
            urls = [r["href"] for r in ddgs.text(query, max_results=max_results) if "href" in r]
        _store(key, "\n".join(urls))
        return urls
    except Exception:
        return []


def news_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo news."""
    if not _OK:
        return "Web search unavailable."

    key = f"news:{query}:{max_results}"
    cached = _cached(key)
    if cached:
        return cached

    try:
        results: list[str] = []
        with DDGS(timeout=8) as ddgs:
            for r in ddgs.news(query, max_results=max_results):
                ts = r.get("date", "")
                results.append(f"[{ts}] {r['title']}\n{r.get('body', '')}\n{r['url']}")
        out = "\n\n---\n\n".join(results) if results else "No news found."
        _store(key, out)
        return out
    except Exception as e:
        return f"News search error: {e}"
