from __future__ import annotations

"""
Web content fetcher for Nyra.
Fetches URLs, strips HTML, returns clean readable text.
Used by the research engine and TOOL:fetch handler.
"""

import re
import urllib.request

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_NOISE_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "form")
_PRICE_PAT = re.compile(
    r"(?:[\$€£¥₺]\s*[\d][,\d]*(?:\.\d{1,2})?|[\d][,\d]*(?:\.\d{1,2})?\s*(?:USD|EUR|GBP|TRY|TL|€|\$|£))",
    re.I,
)


def fetch_text(url: str, max_chars: int = 5000, timeout: int = 12) -> str:
    """Fetch a URL and return extracted plain text. Returns error string on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(400_000)
            enc = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(enc, errors="replace")
    except Exception as exc:
        return f"[fetch error: {exc}]"
    return _clean(html, max_chars)


def extract_prices(text: str) -> list[str]:
    """Find all price-like strings in text."""
    found = _PRICE_PAT.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for p in found:
        p = p.strip()
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:12]


def extract_urls(text: str) -> list[str]:
    """Extract HTTP URLs, filtering out static assets."""
    urls = re.findall(r"https?://[^\s\n\"'<>]{10,}", text)
    skip = re.compile(r"\.(png|jpg|gif|svg|ico|woff|woff2|css|js|mp4|webp)(\?|$)", re.I)
    skip_domains = {"google.com/search", "duckduckgo.com", "bing.com/search"}
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if skip.search(u):
            continue
        if any(d in u for d in skip_domains):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:8]


# ── Internal ──────────────────────────────────────────────────────────────────

def _clean(html: str, limit: int) -> str:
    for tag in _NOISE_TAGS:
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    for ent, ch in [
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "),
        ("&mdash;", "—"), ("&ndash;", "-"), ("&hellip;", "..."),
    ]:
        html = html.replace(ent, ch)
    html = re.sub(r"&#\d+;", " ", html)
    html = re.sub(r"&\w+;", " ", html)
    lines = [l.strip() for l in html.splitlines()]
    lines = [l for l in lines if len(l) > 25]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit].strip()
