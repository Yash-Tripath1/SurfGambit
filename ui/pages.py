"""
ui/pages.py — the browser's own pages.

Every value interpolated into these templates goes through ``html.escape``.
v1 pasted usernames, bookmark titles, history URLs and search queries straight
into markup, which meant a bookmark titled `<script>` became markup.
"""

from __future__ import annotations

import html
import time
import urllib.parse
from typing import Dict, Iterable, List, Optional, Tuple

__all__ = [
    "esc", "welcome_page", "bookmarks_page", "history_page", "downloads_page",
    "settings_page", "login_page", "register_page", "error_page",
    "image_search_page", "source_page",
]

esc = html.escape

BASE_CSS = """
body { font-family: sans-serif; background-color: #0b0b0c; color: #d8d8d8;
       margin: 0; padding: 32px 20px 60px; }
h1 { color: #00c2cc; font-size: 34px; text-align: center; margin: 0 0 6px; }
h2 { color: #00c2cc; font-size: 19px; margin: 0 0 14px;
     border-bottom: 1px solid #242424; padding-bottom: 8px; }
p  { font-size: 14px; line-height: 1.65; margin: 10px 0; }
a  { color: #22c3cc; text-decoration: none; }
a:hover { text-decoration: underline; }
.sub { color: #7d7d7d; text-align: center; font-size: 14px; margin-bottom: 26px; }
.nav { text-align: center; margin: 18px 0 30px; font-size: 14px; }
.nav a { margin: 0 9px; font-weight: bold; }
.card { background-color: #121214; border: 1px solid #232326; border-radius: 10px;
        padding: 22px 26px; margin: 18px auto; max-width: 680px; }
.item { border-bottom: 1px solid #1e1e21; padding: 10px 0; margin: 0; }
.item:last-child { border-bottom: none; }
.mono { font-family: monospace; font-size: 12px; color: #8a8a8a; }
.muted { color: #7d7d7d; font-size: 13px; }
.actions { margin-top: 18px; }
.btn { display: inline-block; background-color: #00a7b0; color: #04141a;
       font-weight: bold; padding: 8px 16px; border-radius: 6px;
       text-decoration: none; font-size: 13px; }
.danger { color: #ff6b52; font-weight: bold; }
.ok { color: #4ecb71; font-weight: bold; }
.warn { color: #ffb454; font-weight: bold; }
label { display: block; font-size: 12px; color: #9a9a9a; margin: 14px 0 4px; }
input, textarea, select { width: 100%; box-sizing: border-box; background-color: #1a1a1d;
       border: 1px solid #2f2f34; border-radius: 5px; color: #e6e6e6;
       padding: 7px 9px; font-size: 14px; }
button { background-color: #00a7b0; color: #04141a; border: none;
         font-weight: bold; padding: 9px 20px; border-radius: 5px;
         font-size: 14px; margin-top: 16px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
td, th { text-align: left; padding: 6px 8px; border-bottom: 1px solid #1e1e21; }
th { color: #9a9a9a; font-weight: bold; font-size: 12px; }
code { font-family: monospace; background-color: #1a1a1d; padding: 1px 4px;
       border-radius: 3px; font-size: 12px; }
"""


def _document(title: str, body: str, extra_css: str = "") -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>{BASE_CSS}{extra_css}</style></head>
<body>{body}</body></html>"""


def _nav() -> str:
    return ('<div class="nav">'
            '<a href="surfgambit://welcome">Home</a>'
            '<a href="surfgambit://bookmarks">Bookmarks</a>'
            '<a href="surfgambit://history">History</a>'
            '<a href="surfgambit://downloads">Downloads</a>'
            '<a href="surfgambit://settings">Settings</a>'
            '</div>')


# ---------------------------------------------------------------------------
# Welcome
# ---------------------------------------------------------------------------

def welcome_page(profile: Optional[str], bookmarks: List[Dict[str, str]],
                 version: str = "2.0") -> str:
    if profile:
        greeting = (f'<p class="sub">Signed in as <strong>{esc(profile)}</strong> &middot; '
                    f'<a href="surfgambit://logout">Sign out</a></p>')
    else:
        greeting = ('<p class="sub">Browsing as a guest &middot; '
                    '<a href="surfgambit://login">Sign in</a></p>')

    quick = ""
    if bookmarks:
        cells = []
        for bookmark in bookmarks[:6]:
            url = bookmark.get("url", "")
            title = bookmark.get("title") or _host_of(url)
            cells.append(
                f'<a class="btn" href="{esc(url, quote=True)}" '
                f'title="{esc(url, quote=True)}">{esc(_ellipsis(title, 22))}</a> '
            )
        quick = (f'<div class="card"><h2>Quick dial</h2>'
                 f'<p>{" ".join(cells)}</p></div>')

    body = f"""
    <h1>SurfGambit</h1>
    {greeting}
    {_nav()}
    <div class="card">
      <h2>Engine status</h2>
      <table>
        <tr><td>HTML tokenizer</td><td class="ok">state machine, implied tags, RAWTEXT</td></tr>
        <tr><td>CSS cascade</td><td class="ok">specificity, inheritance, @media, shorthands</td></tr>
        <tr><td>Layout</td><td class="ok">box model, inline flow, tables, collapsing margins</td></tr>
        <tr><td>Network</td><td class="ok">GET/POST, TLS verified, gzip, chunked</td></tr>
        <tr><td>Scripting</td><td class="warn">SurfScript subset (opt-in per page)</td></tr>
      </table>
    </div>
    {quick}
    <div class="card">
      <h2>Try these</h2>
      <p class="mono">
        <a href="https://example.com">example.com</a> &middot;
        <a href="https://text.npr.org">text.npr.org</a> &middot;
        <a href="https://news.ycombinator.com">news.ycombinator.com</a> &middot;
        <a href="https://en.wikipedia.org/wiki/Web_browser">wikipedia.org</a>
      </p>
      <p class="muted">Type a search phrase in the address bar to search the web.</p>
    </div>
    <p class="muted" style="text-align:center; margin-top:34px;">
      SurfGambit {esc(version)} &middot; built from scratch in Python
    </p>
    """
    return _document("SurfGambit", body)


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).hostname or url
    except ValueError:
        return url


def _ellipsis(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

def bookmarks_page(bookmarks: List[Dict[str, str]], message: str = "") -> str:
    rows = []
    for bookmark in bookmarks:
        url = bookmark.get("url", "")
        title = bookmark.get("title") or url
        rows.append(
            f'<div class="item"><a href="{esc(url, quote=True)}">{esc(title)}</a>'
            f'<br><span class="mono">{esc(url)}</span>'
            f' &middot; <a class="danger" href="surfgambit://remove-bookmark?url='
            f'{esc(urllib.parse.quote(url), quote=True)}">remove</a></div>'
        )
    content = "".join(rows) or '<p class="muted">No bookmarks yet. Press the star in the toolbar on any page.</p>'
    banner = f'<p class="ok">{esc(message)}</p>' if message else ""
    body = f"""
    <h1>Bookmarks</h1>{_nav()}
    <div class="card"><h2>Saved pages ({len(bookmarks)})</h2>{banner}{content}
      {"" if not bookmarks else '<p class="actions"><a class="danger" href="surfgambit://clear-bookmarks">Delete all bookmarks</a></p>'}
    </div>"""
    return _document("Bookmarks", body)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def history_page(entries: Iterable[Dict[str, str]]) -> str:
    rows = []
    for entry in entries:
        url = entry.get("url", "")
        rows.append(
            f'<div class="item"><span class="mono">{esc(entry.get("time", ""))}</span> '
            f'<a href="{esc(url, quote=True)}">{esc(_ellipsis(url, 90))}</a></div>'
        )
    entries = list(entries)
    content = "".join(rows) or '<p class="muted">Nothing recorded yet.</p>'
    body = f"""
    <h1>History</h1>{_nav()}
    <div class="card"><h2>{len(entries)} visited pages</h2>{content}
      {"" if not entries else '<p class="actions"><a class="danger" href="surfgambit://clear-history">Delete history</a></p>'}
    </div>"""
    return _document("History", body)


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def downloads_page(entries: List[Dict[str, str]]) -> str:
    rows = []
    for entry in entries:
        status = entry.get("status", "")
        css = "ok" if status == "Completed" else ("danger" if "fail" in status.lower() else "warn")
        rows.append(
            f'<div class="item"><strong>{esc(entry.get("name", ""))}</strong>'
            f'<br><span class="mono">{esc(entry.get("time", ""))} &middot; '
            f'<span class="{css}">{esc(status)}</span></span></div>'
        )
    content = "".join(rows) or '<p class="muted">No downloads yet.</p>'
    body = f"""
    <h1>Downloads</h1>{_nav()}
    <div class="card"><h2>Files</h2>{content}</div>"""
    return _document("Downloads", body)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def settings_page(settings: Dict[str, str], message: str = "",
                  error: str = "") -> str:
    # Keep these lists in step with core.search, otherwise a saved choice
    # silently falls back to the default engine.
    from core.search import SEARCH_ENGINES, USER_AGENTS

    ua_options = {
        "Chrome": "Chrome on Windows",
        "Firefox": "Firefox on Windows",
        "Safari": "Safari on macOS",
        "SurfGambit": "SurfGambit (itself)",
    }
    engines = list(SEARCH_ENGINES)

    def options(values, selected):
        return "".join(
            f'<option value="{esc(v, quote=True)}"'
            f'{" selected" if v == selected else ""}>{esc(label if isinstance(values, dict) else v)}</option>'
            for v, label in (values.items() if isinstance(values, dict) else [(v, v) for v in values])
        )

    ua_map = dict(ua_options)
    banner = ""
    if message:
        banner = f'<p class="ok">{esc(message)}</p>'
    if error:
        banner = f'<p class="danger">{esc(error)}</p>'

    verify_checked = " checked" if settings.get("verify_tls", "on") == "on" else ""
    js_checked = " checked" if settings.get("javascript", "off") == "on" else ""

    body = f"""
    <h1>Settings</h1>{_nav()}
    <div class="card">
      <h2>Browser</h2>{banner}
      <form action="surfgambit://settings-save" method="GET">
        <label>Default search engine</label>
        <select name="se">{options(engines, settings.get("search_engine", "Bing"))}</select>

        <label>User agent</label>
        <select name="ua" style="width:auto;">{options(ua_map, settings.get("user_agent_name", "Chrome"))}</select>

        <label>Certificate validation</label>
        <select name="verify" style="width:auto;">
          <option value="on"{verify_checked}>Verify TLS certificates (recommended)</option>
          <option value="off"{"" if verify_checked else " selected"}>Do not verify (unsafe)</option>
        </select>

        <label>Run page scripts (SurfScript)</label>
        <select name="js" style="width:auto;">
          <option value="off"{"" if js_checked else " selected"}>Off</option>
          <option value="on"{js_checked}>On</option>
        </select>

        <label>Mascot animation</label>
        <select name="mascot" style="width:auto;">
          <option value="on"{' selected' if settings.get('mascot_active','on')=='on' else ''}>On</option>
          <option value="off"{' selected' if settings.get('mascot_active','on')!='on' else ''}>Off</option>
        </select>

        <button type="submit">Save settings</button>
      </form>
    </div>
    <div class="card">
      <h2>Data</h2>
      <p class="muted">Everything is stored locally in <code>{esc(settings.get("data_dir", ""))}</code>.</p>
      <p class="actions">
        <a class="danger" href="surfgambit://clear-history">Clear history</a> &middot;
        <a class="danger" href="surfgambit://clear-bookmarks">Clear bookmarks</a> &middot;
        <a class="danger" href="surfgambit://clear-cookies">Clear cookies</a>
      </p>
    </div>"""
    return _document("Settings", body)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_page(error: str = "", message: str = "", has_users: bool = True) -> str:
    banner = ""
    if error:
        banner = f'<p class="danger">{esc(error)}</p>'
    if message:
        banner = f'<p class="ok">{esc(message)}</p>'
    body = f"""
    <h1>Sign in</h1>
    <p class="sub">Local profile &mdash; stored on this machine only</p>
    {_nav()}
    <div class="card" style="max-width:420px;">
      <h2>Existing profile</h2>{banner}
      <form action="surfgambit://login-submit" method="POST">
        <label>Username</label>
        <input type="text" name="username" autocomplete="username">
        <label>Password</label>
        <input type="password" name="password">
        <button type="submit">Sign in</button>
      </form>
      <p class="muted" style="margin-top:18px;">
        No profile yet? <a href="surfgambit://register">Create one</a>.
      </p>
    </div>"""
    return _document("Sign in", body)


def register_page(error: str = "") -> str:
    banner = f'<p class="danger">{esc(error)}</p>' if error else ""
    body = f"""
    <h1>Create profile</h1>
    <p class="sub">Passwords are hashed with PBKDF2-SHA256 (600 000 rounds)</p>
    {_nav()}
    <div class="card" style="max-width:420px;">
      <h2>New profile</h2>{banner}
      <form action="surfgambit://register-submit" method="POST">
        <label>Username</label>
        <input type="text" name="username">
        <label>Password (minimum 8 characters)</label>
        <input type="password" name="password">
        <label>Confirm password</label>
        <input type="password" name="confirm">
        <button type="submit">Create profile</button>
      </form>
    </div>"""
    return _document("Create profile", body)


# ---------------------------------------------------------------------------
# Error / misc
# ---------------------------------------------------------------------------

def error_page(url: str, message: str, suggestions: bool = True) -> str:
    hint = ""
    if suggestions:
        hint = ('<p class="muted">Check the address for typos, or try '
                '<a href="https://example.com">example.com</a>.</p>')
    body = f"""
    <h1 style="color:#ff6b52;">Page could not be loaded</h1>
    <div class="card" style="max-width:560px;">
      <p class="mono">{esc(url)}</p>
      <p class="danger">{esc(message)}</p>
      {hint}
      <p class="actions"><a class="btn" href="surfgambit://welcome">Back to start</a></p>
    </div>"""
    return _document("Load error", body)


def image_search_page(query: str, image_urls: List[str], error: str = "") -> str:
    if error:
        content = f'<p class="danger">{esc(error)}</p>'
    elif not image_urls:
        content = '<p class="muted">No images found for that query.</p>'
    else:
        content = "".join(
            f'<img src="{esc(url, quote=True)}" alt="{esc(query)}" '
            f'style="width:180px; height:150px; margin:8px; border:1px solid #242424;">'
            for url in image_urls
        )
    body = f"""
    <h1>Images: {esc(query)}</h1>{_nav()}
    <div class="card" style="max-width:760px; text-align:center;">{content}</div>"""
    return _document(f"Images: {query}", body)


def source_page(title: str, source: str) -> str:
    return _document(title, f'<h1>{esc(title)}</h1><pre style="white-space:pre; '
                            f'font-family:monospace; font-size:12px;">{esc(source)}</pre>')
