"""
ui/pages.py — the browser's own pages.

Every value interpolated into these templates goes through ``html.escape``.
legacy pasted usernames, bookmark titles, history URLs and search queries straight
into markup, which meant a bookmark titled `<script>` became markup.
"""

from __future__ import annotations

import html
import urllib.parse
from typing import Dict, Iterable, List, Optional

__all__ = [
    "esc", "welcome_page", "bookmarks_page", "history_page", "downloads_page",
    "settings_page", "login_page", "register_page", "error_page",
    "image_search_page", "source_page",
]

esc = html.escape


def _host_of(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).hostname or url
    except ValueError:
        host = url
    return host[4:] if host.startswith("www.") else host


def _ellipsis(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"

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
            '<a href="surfgambit://demo">Script demo</a>'
            '</div>')


# ---------------------------------------------------------------------------
# Welcome
# ---------------------------------------------------------------------------

def welcome_page(profile: Optional[str], bookmarks: List[Dict[str, str]]) -> str:
    """The original start page, markup and styling unchanged."""

    if profile:
        greeting = (f'<p style="color: #00adb5; font-size: 16px; font-weight: bold;">'
                    f'\U0001F47E Welcome back, {esc(profile)}! (Logged in via Secure Cookie)</p>'
                    f'<a href="surfgambit://logout" style="font-size: 13px; color: #ff5722; '
                    f'font-weight: bold;">Sign Out</a>')
    else:
        greeting = ('<a href="surfgambit://login" style="font-size: 14px; color: #00adb5; '
                    'font-weight: bold;">\U0001F464 Sign In / Register</a>')

    dial = ""
    if bookmarks:
        cells = []
        for bookmark in bookmarks[:6]:
            url = bookmark.get("url", "")
            name = bookmark.get("title") or _host_of(url)
            cells.append(
                f'<div class="col" style="display: inline-block; width: 135px; '
                f'background-color: #0d0d0d; border: 1px solid #222; border-radius: 8px; '
                f'padding: 12px; margin: 6px; text-align: center; height: 60px;">'
                f'<a href="{esc(url, quote=True)}" style="font-size: 11px; '
                f'text-decoration: none; color: #00adb5;">{esc(_ellipsis(name, 22))}</a>'
                f'</div>')
        dial = ('<div style="text-align:center; margin: 0 auto 20px; max-width: 700px;">'
                + "".join(cells) + '</div>')

    return f"""<html>
<head>
    <style>
        body {{
            font-family: sans-serif;
            background-color: #000000;
            color: #e0e0e0;
            margin: 40px;
            text-align: center;
        }}
        .card {{
            background-color: #0d0d0d;
            border: 1px solid #222;
            padding: 25px;
            margin: 20px auto;
            max-width: 650px;
            text-align: left;
            border-radius: 8px;
        }}
        h1 {{
            color: #00adb5;
            font-size: 38px;
            margin-bottom: 5px;
            text-align: center;
        }}
        h2 {{
            color: #00adb5;
            font-size: 20px;
            border-bottom: 1px solid #333;
            padding-bottom: 8px;
            margin-top: 0px;
        }}
        p {{
            line-height: 1.6;
            font-size: 14px;
        }}
        a {{
            color: #00adb5;
            font-weight: bold;
            text-decoration: underline;
        }}
        .tech-list {{
            background-color: #151515;
            padding: 15px;
            border-left: 4px solid #00adb5;
            font-family: monospace;
            font-size: 13px;
            line-height: 1.5;
        }}
        alien {{ display: block; width: 64px; height: 64px; margin: 8px auto 0; }}
    </style>
</head>
<body>
    <h1>SurfGambit Browser</h1>
    <p style="color: #888; font-size: 16px; margin-top: 5px;">A custom-built Python web engine rendered on raw Canvas.</p>

    {greeting}

    <div style="text-align: center; margin-top: 10px; margin-bottom: 4px;">
        <alien></alien>
    </div>

    <div style="text-align: center; margin-bottom: 20px; margin-top: 15px;">
        <a href="surfgambit://bookmarks" style="margin-right: 15px; font-weight: bold;">\u2b50 Bookmarks</a> |
        <a href="surfgambit://history" style="margin-right: 15px; font-weight: bold;">\U0001F4DC History Log</a> |
        <a href="surfgambit://downloads" style="font-weight: bold;">\u2b07\ufe0f Downloads</a> |
        <a href="surfgambit://settings" style="font-weight: bold;">\u2699\ufe0f Settings</a>
    </div>

    {dial}

    <div class="card">
        <h2>\U0001F680 Deeply Custom Architecture</h2>
        <p>This browser is written from scratch without high-level rendering frameworks, implementing a modular engine model:</p>
        <div class="tech-list">
            - [network.py]: TCP Sockets, TLS, Gzip decompression, Chunked Transfer Decoding.<br>
            - [cookies.py]: Custom stateful Cookie Jar parsing and injecting active sessions.<br>
            - [accounts.py]: PBKDF2-HMAC-SHA256 password hashing and local credential caches.<br>
            - [parser.py]: Custom state-machine HTML DOM Parser and Cascading CSS Styling Engine.<br>
            - [layout.py]: Vertical block and horizontal inline-wrap layout, plus alignment controls.
        </div>
    </div>

    <div class="card">
        <h2>\U0001F5BC Real Image Decoding</h2>
        <p>Images are fetched through the same raw socket client as the page and decoded here: JPEG (baseline and progressive), PNG (every colour type and bit depth) and BMP, with EXIF rotation applied. GIF goes straight to Tk.</p>
        <p>Each picture is resampled to the exact size its CSS box asks for, so nothing is squashed, stretched or left with a gap. Pick <b>Images</b> in the search box to see it.</p>
    </div>

    <div class="card">
        <h2>\U0001F310 Interactive Features</h2>
        <p>This browser is equipped with power-user controls for testing:</p>
        <p>
            - <b>Multiple Tabs</b>: Browse multiple pages concurrently.<br>
            - <b>Precise Zoom</b>: Use \u2795 / \u2796 buttons to scale elements with responsive re-layout.<br>
            - <b>Developer Tools</b>: Toggle the side panel to view DOM, CSS, or Raw HTML source!<br>
            - <b>History Navigation</b>: Full back/forward page history tracking.<br>
            - <b>Word Wrapping &amp; Alignment</b>: Modern word flow and CSS style resolution.
        </p>
    </div>

    <div class="card">
        <h2>\U0001F517 Standard Web Testbench</h2>
        <p>Try entering these lightweight URLs in the address bar above, or type search terms to query the selected engine directly:</p>
        <p>
            - <a href="http://example.com">http://example.com</a> (Standard HTTP test site)<br>
            - <a href="http://neverssl.com">http://neverssl.com</a> (Great raw no-SSL sandbox)<br>
            - <a href="https://text.npr.org">https://text.npr.org</a> (NPR News Text-Only)<br>
            - <a href="https://news.ycombinator.com">https://news.ycombinator.com</a> (Hacker News)
        </p>
    </div>

    <div style="color: #444; font-size: 12px; margin-top: 40px;">
        SurfGambit | Standalone Sandbox Session
    </div>
</body>
</html>
"""


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
    """The original ⚙️ Browser Configuration card, plus the two switches the
    engine needs (TLS verification and page scripts)."""

    banner = (f'<p style="color: #4caf50; font-weight: bold; text-align: center;">'
              f'\U0001F49A {esc(message)}</p>') if message else ""
    if error:
        banner += (f'<p style="color: #ff5722; font-weight: bold; text-align: center;">'
                   f'\U0001F6D1 {esc(error)}</p>')

    ua_val = esc(settings.get("user_agent_name", "Chrome"))
    se_val = esc(settings.get("search_engine", "DuckDuckGo"))
    mascot_val = esc(settings.get("mascot_active", "on"))
    verify_val = esc(settings.get("verify_tls", "on"))
    js_val = esc(settings.get("javascript", "off"))

    return f"""<html>
<head>
    <style>
        body {{ font-family: sans-serif; background-color: #000000; color: #e0e0e0; margin: 40px; text-align: center; }}
        h1 {{ color: #00adb5; }}
        .card {{ background-color: #0d0d0d; border: 1px solid #222; padding: 30px; margin: 20px auto; max-width: 500px; text-align: left; border-radius: 8px; }}
        a {{ color: #00adb5; font-weight: bold; text-decoration: underline; }}
        .nav {{ margin-bottom: 25px; }}
        input, button {{ width: 100%; height: 28px; margin-bottom: 15px; border-radius: 4px; }}
    </style>
</head>
<body>
    <h1>SurfGambit Settings</h1>
    <div class="nav">
        <a href="surfgambit://welcome">Home</a> |
        <a href="surfgambit://bookmarks">Bookmarks</a> |
        <a href="surfgambit://history">History</a>
    </div>

    <div class="card">
        <h2 style="color: #00adb5; margin-top: 0px; margin-bottom: 20px; text-align: center;">\u2699\ufe0f Browser Configuration</h2>
        {banner}
        <form action="surfgambit://settings-save" method="GET">
            <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">User-Agent Preset (Chrome, Firefox, Safari)</p>
            <input type="text" name="ua" value="{ua_val}"><br>

            <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">Default Search Engine (Google, DuckDuckGo, Wikipedia, Bing)</p>
            <input type="text" name="se" value="{se_val}"><br>

            <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">Mascot Active (on / off)</p>
            <input type="text" name="mascot" value="{mascot_val}"><br>

            <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">Verify TLS Certificates (on / off)</p>
            <input type="text" name="verify" value="{verify_val}"><br>

            <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">Run Page Scripts / SurfScript (on / off)</p>
            <input type="text" name="js" value="{js_val}"><br>

            <button type="submit" style="background-color: #00adb5; color: white; border: none; font-weight: bold; cursor: pointer; height: 32px; margin-top: 10px;">Save Settings</button>
        </form>
    </div>
</body>
</html>
"""


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


def search_page(query: str, results: List[Dict[str, object]], source: str = "",
                error: str = "") -> str:
    """The browser's own results page, in the same palette as the start page."""
    if error:
        results_html = (f'<p style="color:#ff5722;font-weight:bold;">{esc(error)}</p>'
                        '<p style="color:#888;">Check the spelling, or try a different '
                        'engine from the dropdown in the toolbar.</p>')
    elif not results:
        results_html = ('<p style="color:#888;">No results came back. The engine may be '
                        'rate-limiting this connection — try again in a moment, or '
                        'open the engine directly from the toolbar.</p>')
    else:
        rows = []
        for item in results:
            title = esc(str(item.get("title") or ""))
            url = str(item.get("url") or "")
            snippet = esc(str(item.get("snippet") or ""))
            host = esc(urllib.parse.urlsplit(url).hostname or url)
            rows.append(
                f'<div style="border-bottom:1px solid #1e1e1e; padding:14px 0;">'
                f'<a href="{esc(url, quote=True)}" style="font-size:17px; '
                f'text-decoration:none; color:#4dd8e0; font-weight:bold;">{title}</a>'
                f'<div style="color:#4caf50; font-size:12px; margin:3px 0 6px;">{host}</div>'
                f'<div style="color:#c8c8c8; font-size:14px; line-height:1.55;">{snippet}</div>'
                f'</div>')
        results_html = "".join(rows)

    source_line = (f'<p style="color:#666; font-size:12px; margin-top:18px;">'
                   f'Results from {esc(source)}</p>') if source else ""

    body = f"""
    <h1 style="text-align:left; margin:0 0 4px;">{esc(query)}</h1>
    <p style="color:#777; font-size:13px; margin:0 0 18px;">
        Site: <a href="surfgambit://search?q={esc(urllib.parse.quote(query), quote=True)}">surfgambit://search</a>
    </p>
    <div style="max-width:760px;">
        {results_html}
        {source_line}
    </div>"""
    return _document(f"{query} - SurfGambit search", body)


def image_search_page(query: str, images: List[Dict[str, object]],
                      error: str = "") -> str:
    if error:
        content = f'<p style="color:#ff5722;font-weight:bold;">{esc(error)}</p>'
    elif not images:
        content = '<p style="color:#888;">No pictures came back for that query.</p>'
    else:
        # A wrapping flex row: the engine's flexbox does this properly, while
        # inline-block cards overlap once they carry padding and labels.
        cells = []
        for item in images:
            thumb = str(item.get("thumb") or "")
            page = str(item.get("page") or thumb)
            title = esc(str(item.get("title") or query))
            cells.append(
                f'<a href="{esc(page, quote=True)}" title="{title}" '
                f'style="display:block; width:196px; height:196px; margin:6px; '
                f'text-decoration:none;">'
                f'<div style="background-color:#0d0d0d; border:1px solid #222; '
                f'border-radius:8px; padding:8px; text-align:center; height:178px;">'
                f'<img src="{esc(thumb, quote=True)}" alt="{title}" '
                f'style="width:178px; height:132px;">'
                f'<div style="color:#9aa; font-size:11px; margin-top:6px;">'
                f'{esc(_ellipsis(str(item.get("title") or ""), 25))}</div>'
                f'</div></a>')
        content = ('<div style="display:flex; flex-wrap:wrap; '
                   'width:940px;">' + "".join(cells) + "</div>")

    body = f"""
    <h1 style="text-align:left;">Images: {esc(query)}</h1>
    <p style="color:#777; font-size:13px;">Pictures from Wikimedia Commons.</p>
    <div style="max-width:980px; text-align:left;">{content}</div>"""
    return _document(f"Images: {query}", body)


def source_page(title: str, source: str) -> str:
    return _document(title, f'<h1>{esc(title)}</h1><pre style="white-space:pre; '
                            f'font-family:monospace; font-size:12px;">{esc(source)}</pre>')

# ---------------------------------------------------------------------------
# SurfScript demo — the page that proves the engine is wired end to end
# ---------------------------------------------------------------------------

def demo_page(js_enabled: bool) -> str:
    body = """
    <h1>SurfScript demo</h1>
    <p class="sub">Every number below is produced by the script on this page,
       running inside SurfGambit's own interpreter.</p>
    {notice}
    <div class="card">
      <h2>1 &middot; Counter (addEventListener)</h2>
      <p>Count: <strong id="count">0</strong></p>
      <p><button id="inc">Add one</button>
         <button id="reset">Reset</button></p>
    </div>
    <div class="card">
      <h2>2 &middot; Styling from script</h2>
      <p><button id="paint">Toggle the highlight</button></p>
      <div id="box" style="padding:12px;border:1px solid #d0d0d0;">
        This paragraph is restyled by <code>classList.toggle()</code>.
      </div>
    </div>
    <div class="card">
      <h2>3 &middot; Building DOM</h2>
      <p><button id="add">Add a list item</button></p>
      <ul id="list"><li>built by createElement()</li></ul>
    </div>
    <div class="card">
      <h2>4 &middot; Timers</h2>
      <p>Uptime: <span id="clock">0</span>s &mdash; driven by setInterval().</p>
    </div>
    <div class="card">
      <h2>5 &middot; A small program</h2>
      <pre id="fib"></pre>
    </div>
    <script>
      let count = 0;
      let uptime = 0;
      let added = 0;

      document.getElementById('inc').addEventListener('click', function () {
        count = count + 1;
        document.getElementById('count').textContent = String(count);
      });

      document.getElementById('reset').addEventListener('click', function () {
        count = 0;
        document.getElementById('count').textContent = '0';
      });

      document.getElementById('paint').addEventListener('click', function () {
        document.getElementById('box').classList.toggle('hl');
      });

      document.getElementById('add').addEventListener('click', function () {
        added = added + 1;
        let item = document.createElement('li');
        item.textContent = 'item ' + added;
        document.getElementById('list').appendChild(item);
      });

      setInterval(function () {
        uptime = uptime + 1;
        document.getElementById('clock').textContent = String(uptime);
      }, 1000);

      function fib(n) {
        if (n < 2) { return n; }
        return fib(n - 1) + fib(n - 2);
      }

      let parts = [];
      for (let i = 0; i < 12; i = i + 1) {
        parts.push(fib(i));
      }
      document.getElementById('fib').textContent = 'fib(0..11) = ' + parts.join(', ');

      console.log('demo ready');
    </script>
    """
    if js_enabled:
        notice = ('<p class="muted">JavaScript is <strong>on</strong> for this page.</p>')
    else:
        notice = ('<div class="card"><p class="warn">JavaScript is off, so nothing on this '
                  'page will react.</p><p><a class="btn" href="surfgambit://settings">'
                  'Open settings &rarr; enable JavaScript &rarr; reload</a></p></div>')
    return _document("SurfScript demo", body.replace("{notice}", notice),
                     extra_css=(".hl { background:#fff3bf; border-color:#e0b400; color:#111111; }"
                                " .hl code { color:#111111; }"))
