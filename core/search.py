"""
core/search.py — the address bar's two jobs: turn input into a URL, and find
pictures.

The engine list is ordered by what actually works with a plain TLS client and
no JavaScript. DuckDuckGo's HTML endpoints now answer with a bot-challenge
(HTTP 202) instead of results, so it is present but not the default; Bing and
Wikipedia return real HTML to this client today.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Dict, List, Optional, Tuple

__all__ = [
    "SEARCH_ENGINES", "search_url", "normalise_input", "looks_like_url",
    "fetch_image_urls", "USER_AGENTS",
]

SEARCH_ENGINES: Dict[str, str] = {
    "Bing": "https://www.bing.com/search?q={q}",
    "Google": "https://www.google.com/search?q={q}",
    "DuckDuckGo": "https://html.duckduckgo.com/html/?q={q}",
    "Startpage": "https://www.startpage.com/sp/search?query={q}",
    "Wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
}

USER_AGENTS: Dict[str, str] = {
    "Chrome": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Firefox": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
                "Gecko/20100101 Firefox/126.0"),
    "Safari": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
    "SurfGambit": "SurfGambit/2.0 (Python; Tk; +https://github.com/Yash-Tripath1/SurfGambit)",
}

_SCHEMES = ("http://", "https://", "file://", "surfgambit://", "about:")


def looks_like_url(text: str) -> bool:
    text = text.strip()
    if not text or " " in text:
        return False
    if text.startswith(_SCHEMES):
        return True
    if text.startswith("localhost") or text.startswith("127.0.0.1"):
        return True
    host = text.split("/", 1)[0]
    if "." not in host:
        return False
    tld = host.rsplit(".", 1)[-1]
    return len(tld) >= 2 and tld.isalpha()


def normalise_input(text: str, engine: str = "Bing") -> Optional[str]:
    """Turn address-bar text into a URL. Returns None for empty input."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith(_SCHEMES):
        return text
    if looks_like_url(text):
        return "https://" + text
    template = SEARCH_ENGINES.get(engine) or SEARCH_ENGINES["Bing"]
    return template.format(q=urllib.parse.quote_plus(text))


def search_url(query: str, engine: str = "Bing") -> str:
    template = SEARCH_ENGINES.get(engine) or SEARCH_ENGINES["Bing"]
    return template.format(q=urllib.parse.quote_plus(query))


def fetch_image_urls(client, query: str, limit: int = 8) -> List[str]:
    """
    Ask Wikipedia's PageImages API for thumbnails matching `query`.

    It needs no API key, returns stable https URLs, and the images are then
    loaded through the tab's normal image pipeline (so they respect the
    "don't load images" setting and are cached like any other image).
    """
    query = (query or "").strip()
    if not query:
        return []
    api = ("https://en.wikipedia.org/w/api.php?action=query&prop=pageimages"
           "&format=json&piprop=thumbnail&pithumbsize=320&generator=search"
           "&gsrlimit={limit}&gsrsearch={q}").format(
        limit=max(1, min(20, limit)), q=urllib.parse.quote(query))
    response = client.get(api)
    if not response.ok:
        raise RuntimeError(f"Image search failed: HTTP {response.status}")
    try:
        payload = json.loads(response.text)
    except ValueError as exc:
        raise RuntimeError("Image search returned malformed JSON") from exc

    pages = (payload.get("query") or {}).get("pages") or {}
    if not isinstance(pages, dict):
        return []
    ordered = sorted(pages.values(), key=lambda page: page.get("index", 999))
    urls: List[str] = []
    for page in ordered:
        thumb = page.get("thumbnail") or {}
        source = thumb.get("source")
        if not source:
            continue
        if not source.startswith("https://"):
            source = source.replace("http://", "https://", 1)
        if source not in urls:
            urls.append(source)
        if len(urls) >= limit:
            break
    return urls
