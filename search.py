from __future__ import annotations

try:
    from duckduckgo_search import DDGS
    _OK = True
except ImportError:
    _OK = False


def web_search(query: str, max_results: int = 4) -> str:
    if not _OK:
        return "Web search unavailable. Run: pip install duckduckgo-search"
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(f"{r['title']}\n{r['body']}\n{r['href']}")
        if not results:
            return "No results found."
        return "\n\n---\n\n".join(results)
    except Exception as e:
        return f"Search error: {e}"
