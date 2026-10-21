"""
core/search.py: the address bar's two jobs: turn input into a URL, and search.

Search engines have made plain HTTP clients unwelcome: Google now answers with a
JavaScript shell, DuckDuckGo's HTML endpoint hands back a bot challenge, and
Startpage renders results client-side. Rather than show an empty page, this
module asks the engines that still return server-rendered HTML (Bing), plus the
Wikipedia API, and returns structured results the browser can draw itself on
`surfgambit://search`.

The engine list in the toolbar still navigates directly when chosen; the
built-in results page is the safety net and the default for the Images mode.
"""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.parse
from typing import Dict, List, Optional, Tuple

__all__ = [
    "SEARCH_ENGINES", "SEARCH_ENGINE_NAMES", "search_url", "normalise_input",
    "looks_like_url", "fetch_image_urls", "web_results", "image_results",
    "USER_AGENTS", "looks_like_search_engine",
]

SEARCH_ENGINES: Dict[str, str] = {
    "Google": "https://www.google.com/search?q={q}",
    "DuckDuckGo": "https://html.duckduckgo.com/html/?q={q}",
    "Bing": "https://www.bing.com/search?q={q}",
    "Wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "Startpage": "https://www.startpage.com/sp/search?query={q}",
}

# Order shown in the toolbar.
SEARCH_ENGINE_NAMES = ["Google", "DuckDuckGo", "Wikipedia", "Bing", "Startpage"]

USER_AGENTS: Dict[str, str] = {
    "Chrome": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Firefox": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
                "Gecko/20100101 Firefox/126.0"),
    "Safari": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
    "SurfGambit": "SurfGambit/1.0 (Python; Tk; +https://github.com/Yash-Tripath1/SurfGambit)",
}

_SCHEMES = ("http://", "https://", "file://", "surfgambit://", "about:")

_SEARCH_HOSTS = ("google.", "duckduckgo.com", "bing.com", "startpage.com",
                 "search.yahoo.", "ecosia.org", "brave.com")


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
    template = SEARCH_ENGINES.get(engine) or SEARCH_ENGINES["Google"]
    return template.format(q=urllib.parse.quote_plus(text))


def search_url(query: str, engine: str = "Bing") -> str:
    template = SEARCH_ENGINES.get(engine) or SEARCH_ENGINES["Google"]
    return template.format(q=urllib.parse.quote_plus(query))


def looks_like_search_engine(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(marker in host for marker in _SEARCH_HOSTS)


# Phrases that search engines and CDNs put on the page they serve instead of
# results when they decide the client is a robot. Matched case-insensitively
# against the first few thousand characters of body text.
_BLOCK_PHRASES = (
    "bots use duckduckgo too",
    "unusual traffic from your computer",
    "our systems have detected unusual traffic",
    "verify you are a human",
    "verify you are human",
    "are you a robot",
    "complete the challenge below",
    "please complete the security check",
    "checking your browser before accessing",
    "attention required! | cloudflare",
    "cf-challenge",
    "enable javascript and cookies to continue",
    "captcha",
)

_BLOCK_TITLES = (
    "before you continue",
    "just a moment",
    "security check",
    "access denied",
)


def page_looks_blocked(html_text: str) -> bool:
    """True when the response is a bot wall rather than a real page.

    Search engines answer plain HTTP clients with a captcha or a "checking
    your browser" interstitial. Detecting it lets the browser fall back to its
    own results page instead of showing a dead end.
    """
    if not html_text:
        return True
    lowered = html_text[:12000].lower()
    if any(phrase in lowered for phrase in _BLOCK_PHRASES):
        return True
    from core.dom import parse
    try:
        dom = parse(html_text)
        title = ""
        for node in dom.walk():
            if node.tag == "title":
                title = node.text_content().strip().lower()
                break
    except Exception:
        title = ""
    return bool(title) and any(marker in title for marker in _BLOCK_TITLES)


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def _unwrap_bing(url: str) -> str:
    """Bing wraps outbound links in /ck/a?u=a1<base64url> redirects."""
    if "bing.com/ck/a" not in url:
        return url
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    target = (query.get("u") or [""])[0]
    if target.startswith("a1"):
        payload = target[2:]
        payload += "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            return url
    return url


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _elems(node, cls: str, out: List) -> None:
    if cls in node.classes:
        out.append(node)
    for child in node.children:
        _elems(child, cls, out)


def _first_link(node) -> Tuple[str, str]:
    """Title + href for one Bing result.

    Bing nests a "site icon" link (`div.b_tpcn > a.tilk`) before the real
    heading, so prefer an `<h2>` anchor and ignore the icon block.
    """
    def skip(child) -> bool:
        return "b_tpcn" in child.classes or "b_attribution" in child.classes

    def walk(container):
        for child in container.children:
            if skip(child):
                continue
            if child.tag == "a" and child.attributes.get("href"):
                return child
            if child.tag == "h2":
                found = walk(child)
                if found is not None:
                    return found
        return None

    anchor = walk(node)
    if anchor is None:
        for child in node.walk():
            if child.tag == "a" and child.attributes.get("href"):
                anchor = child
                break
    if anchor is None:
        return "", ""
    title = _clean(anchor.text_content())
    href = anchor.attributes.get("href") or ""
    return title, href


def parse_bing_html(html_text: str, limit: int = 12) -> List[Dict[str, str]]:
    """Extract results from a Bing SERP. Pure function: easy to test."""
    from core.dom import parse

    dom = parse(html_text)
    blocks: List = []
    _elems(dom, "b_algo", blocks)
    results: List[Dict[str, str]] = []
    seen = set()
    for block in blocks:
        title, href = _first_link(block)
        href = _unwrap_bing(href)
        if not href.startswith("http") or "bing.com" in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = re.sub(r"https?://\S+", "", title).strip()
        title = re.sub(r"\s*›\s*.*$", "", title).strip() if len(title) > 40 else title
        snippet = ""
        paragraphs: List = []
        _elems(block, "b_caption", paragraphs)
        source = paragraphs[0] if paragraphs else block
        for node in source.walk():
            if node.tag in ("p", "div") and len(node.text_content().strip()) > 40:
                snippet = _clean(node.text_content())
                break
        if not snippet:
            snippet = _clean(block.text_content())[:240]
        results.append({"title": title or href, "url": href,
                        "snippet": snippet[:320], "source": "Bing"})
        if len(results) >= limit:
            break
    return results


def _bing_results(client, query: str, limit: int) -> List[Dict[str, str]]:
    url = ("https://www.bing.com/search?q=" + urllib.parse.quote_plus(query)
           + "&setlang=en&cc=us")
    response = client.get(url)
    if not response.ok:
        raise RuntimeError(f"Bing answered HTTP {response.status}")
    return parse_bing_html(response.text, limit)


def _wikipedia_results(client, query: str, limit: int) -> List[Dict[str, str]]:
    api = ("https://en.wikipedia.org/w/api.php?action=query&list=search"
           "&format=json&srlimit={limit}&srsearch={q}").format(
        limit=max(1, min(20, limit)), q=urllib.parse.quote(query))
    response = client.get(api)
    if not response.ok:
        raise RuntimeError(f"Wikipedia answered HTTP {response.status}")
    payload = json.loads(response.text)
    hits = ((payload.get("query") or {}).get("search") or [])
    results = []
    for hit in hits:
        title = hit.get("title") or ""
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet") or "")
        results.append({
            "title": title,
            "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            "snippet": _clean(snippet),
            "source": "Wikipedia",
        })
    return results[:limit]


def web_results(client, query: str, limit: int = 12) -> Dict[str, object]:
    """Search the web and return {'results': [...], 'source': str, 'errors': [...]}."""
    query = (query or "").strip()
    if not query:
        return {"results": [], "source": "", "errors": ["Type something to search for."]}
    errors: List[str] = []
    results: List[Dict[str, str]] = []
    source = ""
    try:
        results = _bing_results(client, query, limit)
        if results:
            source = "Bing"
    except Exception as exc:                    # noqa: BLE001 - try the next one
        errors.append(str(exc))
    if len(results) < 3:
        try:
            extra = _wikipedia_results(client, query, limit)
            known = {r["url"] for r in results}
            results.extend(r for r in extra if r["url"] not in known)
            source = (source + " + Wikipedia").strip(" +") if source else "Wikipedia"
        except Exception as exc:                # noqa: BLE001
            errors.append(str(exc))
    return {"results": results[:limit], "source": source, "errors": errors}


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def image_results(client, query: str, limit: int = 24) -> List[Dict[str, object]]:
    """Pictures for a query, from Wikimedia Commons then Wikipedia's lead images.

    Returns dicts with `thumb` (small URL for the grid), `page` (where it came
    from), `title`, `width` and `height` when known.
    """
    query = (query or "").strip()
    if not query:
        return []
    found: List[Dict[str, object]] = []
    seen = set()

    api = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
           "&generator=search&gsrnamespace=6&gsrlimit={limit}"
           "&gsrsearch={q}&prop=imageinfo&iiprop=url|size|mime&iiurlwidth=360"
           ).format(limit=max(1, min(40, limit)), q=urllib.parse.quote(query))
    try:
        response = client.get(api)
        if response.ok:
            payload = json.loads(response.text)
            pages = (payload.get("query") or {}).get("pages") or {}
            ordered = sorted(pages.values(), key=lambda p: p.get("index", 999))
            for page in ordered:
                info = (page.get("imageinfo") or [{}])[0]
                thumb = info.get("thumburl") or info.get("url")
                if not thumb:
                    continue
                if info.get("mime") not in (None, "image/jpeg", "image/png", "image/gif"):
                    continue
                if thumb in seen:
                    continue
                seen.add(thumb)
                found.append({
                    "thumb": thumb,
                    "page": info.get("descriptionurl") or thumb,
                    "title": (page.get("title") or "").replace("File:", ""),
                    "width": info.get("thumbwidth") or info.get("width") or 0,
                    "height": info.get("thumbheight") or info.get("height") or 0,
                })
                if len(found) >= limit:
                    return found
    except Exception:
        pass

    # Fallback: the lead image of each matching article.
    api = ("https://en.wikipedia.org/w/api.php?action=query&prop=pageimages"
           "&format=json&piprop=thumbnail&pithumbsize=360&generator=search"
           "&gsrlimit={limit}&gsrsearch={q}").format(
        limit=max(1, min(24, limit)), q=urllib.parse.quote(query))
    try:
        response = client.get(api)
        if response.ok:
            payload = json.loads(response.text)
            pages = (payload.get("query") or {}).get("pages") or {}
            ordered = sorted(pages.values(), key=lambda p: p.get("index", 999))
            for page in ordered:
                thumb = (page.get("thumbnail") or {}).get("source")
                if not thumb or thumb in seen:
                    continue
                seen.add(thumb)
                found.append({
                    "thumb": thumb,
                    "page": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(
                        (page.get("title") or "").replace(" ", "_")),
                    "title": page.get("title") or "",
                    "width": 0, "height": 0,
                })
    except Exception:
        pass
    return found[:limit]


def fetch_image_urls(client, query: str, limit: int = 8) -> List[str]:
    """Compatibility helper: just the thumbnail URLs."""
    return [str(item["thumb"]) for item in image_results(client, query, limit)]
