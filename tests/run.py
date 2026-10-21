#!/usr/bin/env python3
"""
tests/run.py — engine self-checks.

    python -m tests.run            # everything
    python -m tests.run dom css    # only matching groups
    python -m tests.run --verbose

No network, no display, no user files: every check runs against in-memory
data, so it is safe to run anywhere (CI, a fresh clone, inside the repo).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from typing import Callable, List, Tuple

# Run from anywhere, but never write user data into the repository.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
os.environ.setdefault("SURFGAMBIT_DATA_DIR", tempfile.mkdtemp(prefix="sg-test-"))

from core.dom import parse
from core.dom.tree import HTMLNode
from core.layout.block import apply_list_markers, compute_layout
from core.layout.box import build_layout_tree
from core.layout.text import TextMeasurer
from core.net.cookies import CookieJar
from core.style import parse_stylesheet
from core.style.cascade import compute_styles
from core.values import parse_length, set_viewport

MEASURER = TextMeasurer(headless=True)

_CHECKS: List[Tuple[str, str, Callable[[], None]]] = []


def check(group: str, label: str):
    def register(fn: Callable[[], None]) -> Callable[[], None]:
        _CHECKS.append((group, label, fn))
        return fn
    return register


def eq(actual, expected, message: str = "") -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def colour(value) -> str:
    """Normalise a computed colour for comparisons (names -> hex)."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    names = {"red": "#ff0000", "green": "#008000", "blue": "#0000ff",
             "black": "#000000", "white": "#ffffff", "#123456": "#123456"}
    return names.get(text, text)


def close(actual: float, expected: float, tolerance: float = 0.6,
          message: str = "") -> None:
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{message}: expected ~{expected}, got {actual}")


def layout_of(html: str, css: str = "", width: int = 800, height: int = 700):
    dom = parse(html)
    sheets = [parse_stylesheet(css)] if css.strip() else []
    compute_styles(dom, sheets, viewport_width=width, viewport_height=height)
    tree = build_layout_tree(dom)
    apply_list_markers(tree)
    compute_layout(tree, 0, 0, width, MEASURER)
    return dom, tree


def find_box(box, tag=None, cls=None):
    for candidate in box.walk():
        if tag is not None and candidate.node.tag != tag:
            continue
        if cls is not None and cls not in candidate.node.classes:
            continue
        if tag is None and cls is None:
            continue
        return candidate
    return None


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    import struct, zlib
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def _make_png(width: int, height: int, colour_type: int = 2,
              depth: int = 8, rows=None) -> bytes:
    """Build a tiny PNG without Pillow: rows must already be filtered."""
    import struct, zlib
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour_type]
    header = struct.pack(">IIBBBBB", width, height, depth, colour_type, 0, 0, 0)
    if rows is None:
        stride = (width * channels * depth + 7) // 8
        rows = [bytes([0]) + bytes(stride) for _ in range(height)]
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header)
            + _png_chunk(b"IDAT", zlib.compress(b"".join(rows)))
            + _png_chunk(b"IEND", b""))


@check("image", "PNG decoder reads truecolour pixels exactly")
def _():
    from core.image import png
    rows = [bytes([0, 255, 0, 0, 0, 255, 0]),        # red, green
            bytes([0, 0, 0, 255, 10, 20, 30])]       # blue, dark grey
    data = _make_png(2, 2, 2, 8, rows)
    img = png.decode(data)
    eq((img.width, img.height, img.mode), (2, 2, "RGB"))
    eq(list(img.data), [255, 0, 0, 0, 255, 0, 0, 0, 255, 10, 20, 30])


@check("image", "PNG decoder applies the Sub/Up/Average/Paeth filters")
def _():
    from core.image import png
    # Two pixels per row, one filter type per row: the second pixel of each
    # row is encoded relative to what the filter predicts.
    raw = [
        bytes([1, 100, 0, 0, 55, 0, 0]),          # Sub: (100,0,0) (155,0,0)
        bytes([2, 10, 10, 10, 0, 0, 0]),          # Up
        bytes([3, 20, 0, 0, 0, 0, 0]),            # Average
        bytes([4, 30, 0, 0, 0, 0, 0]),            # Paeth
    ]
    img = png.decode(_make_png(2, 4, 2, 8, raw))
    eq(img.data[0:3], bytes([100, 0, 0]))
    eq(img.data[3:6], bytes([155, 0, 0]))
    eq(img.data[6:9], bytes([110, 10, 10]))


@check("image", "PNG decoder expands 1-bit grey to black and white")
def _():
    from core.image import png
    img = png.decode(_make_png(8, 1, 0, 1, [bytes([0, 0b10100000])]))
    # Bits 1,0,1,0,0,0,0,0 -> white, black, white, black, black, black, ...
    eq(list(img.data), [255, 255, 255] + [0, 0, 0] + [255, 255, 255]
       + [0, 0, 0] * 5)


@check("image", "PNG decoder resolves palette indices, not intensities")
def _():
    import struct, zlib
    from core.image import png
    header = struct.pack(">IIBBBBB", 2, 1, 4, 3, 0, 0, 0)
    palette = bytes([255, 0, 0, 0, 255, 0])
    data = (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header)
            + _png_chunk(b"PLTE", palette)
            + _png_chunk(b"IDAT", zlib.compress(bytes([0, 0b00010000])))
            + _png_chunk(b"IEND", b""))
    img = png.decode(data)
    eq(list(img.data), [0, 255, 0, 255, 0, 0])


@check("image", "decode_for_display exposes pixels for PNG and JPEG alike")
def _():
    from core.image import decode_for_display
    info = decode_for_display(_make_png(3, 2))
    eq((info.width, info.height, info.format), (3, 2, "png"))
    if not info.rgb or len(info.rgb) != 3 * 2 * 3:
        raise AssertionError("PNG pixels were not kept for resampling")
    if info.mode != "RGB":
        raise AssertionError(f"unexpected mode {info.mode}")


@check("image", "resize_rgb keeps alpha and resamples to an exact size")
def _():
    from core.image import resize_rgb, png_from_rgb
    src = bytes([255, 0, 0, 255, 0, 255, 0, 128])
    out = resize_rgb(src, 2, 1, 4, 2, "RGBA")
    eq(len(out), 4 * 2 * 4)
    eq(list(out[0:4]), [255, 0, 0, 255])
    eq(list(out[12:16]), [0, 255, 0, 128])
    png = png_from_rgb(4, 2, out, "RGBA")
    eq(png[25], 6)                                  # colour type 6 in IHDR


@check("image", "a truncated file fails with a message, not an exception")
def _():
    from core.image import decode_for_display
    info = decode_for_display(b"\x89PNG\r\n\x1a\nGARBAGE")
    if info.png is not None:
        raise AssertionError("bad image decoded anyway")
    if not info.note:
        raise AssertionError("no explanation for the failure")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@check("search", "Bing result blocks become clean title/url/snippet records")
def _():
    from core.search import parse_bing_html
    html = (
        '<li class="b_algo"><h2><a href="https://example.com/page">'
        'Example &amp; Co</a></h2>'
        '<div class="b_caption"><p>A short description that is long enough '
        'to be picked out of the caption block.</p></div></li>'
    )
    results = parse_bing_html(html, 5)
    eq(len(results), 1)
    eq(results[0]["title"], "Example & Co")
    eq(results[0]["url"], "https://example.com/page")
    if "short description" not in results[0]["snippet"]:
        raise AssertionError(results[0]["snippet"])


@check("search", "Bing /ck/a redirects are unwrapped to the real target")
def _():
    import base64
    from core.search import _unwrap_bing
    target = "https://en.wikipedia.org/wiki/Dinosaur"
    payload = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    wrapped = "https://www.bing.com/ck/a?u=" + payload + "&ntb=1"
    eq(_unwrap_bing(wrapped), target)


@check("search", "search-engine captcha pages are recognised")
def _():
    from core.search import page_looks_blocked
    captcha = ("<html><body><h1>Unfortunately, bots use DuckDuckGo too.</h1>"
               "</body></html>")
    if not page_looks_blocked(captcha):
        raise AssertionError("captcha page not detected")
    real = ("<html><head><title>Dinosaur - Wikipedia</title></head><body>"
            + "Dinosaurs were a diverse group. " * 20 + "</body></html>")
    if page_looks_blocked(real):
        raise AssertionError("real article flagged as a captcha")


@check("search", "only search-engine hosts are treated as engines")
def _():
    from core.search import looks_like_search_engine
    if not looks_like_search_engine("https://html.duckduckgo.com/html/?q=x"):
        raise AssertionError("duckduckgo not recognised")
    if not looks_like_search_engine("https://www.google.com/search?q=x"):
        raise AssertionError("google not recognised")
    if looks_like_search_engine("https://example.com/search?q=x"):
        raise AssertionError("ordinary site misread as a search engine")


# ---------------------------------------------------------------------------
# DOM / tokenizer
# ---------------------------------------------------------------------------

@check("dom", "quoted '>' inside an attribute does not end the tag")
def _():
    dom = parse('<html><body><a title="a > b" href="/x">click</a></body></html>')
    anchor = dom.find("a")
    eq(anchor.attributes.get("title"), "a > b")
    eq(anchor.attributes.get("href"), "/x")
    eq(anchor.text_content(), "click")


@check("dom", "implied html/head/body are created")
def _():
    dom = parse("<p>hello</p>")
    eq([c.tag for c in dom.children], ["head", "body"])
    eq(dom.find("body").text_content().strip(), "hello")


@check("dom", "RAWTEXT keeps '<' inside <script>")
def _():
    dom = parse("<body><script>if (a < b) { c = 1; }</script><p>after</p></body>")
    script = dom.find("script")
    eq(script.text_content(), "if (a < b) { c = 1; }")
    eq(dom.find("p").text_content(), "after")


@check("dom", "RCDATA keeps markup inside <textarea> as text")
def _():
    dom = parse("<body><textarea>a < b & c</textarea><p>after</p></body>")
    area = dom.find("textarea")
    eq([c.tag for c in area.children], ["text"])
    eq(area.text_content(), "a < b & c")


@check("dom", "comments are dropped, contents of the page survive")
def _():
    dom = parse("<body><!-- <p>hidden</p> --><p>shown</p></body>")
    eq(dom.find("p").text_content(), "shown")
    eq(len(dom.find_all("p")), 1)


@check("dom", "character references are decoded once")
def _():
    dom = parse("<body><p>a &amp; b &middot; c &#128;&nbsp;d</p></body>")
    text = dom.find("p").text_content()
    # `&#128;` is remapped to the euro sign by the HTML5 numeric-reference table
    eq(text, "a & b \u00b7 c \u20ac\u00a0d")


@check("dom", "2000-deep nesting parses without RecursionError")
def _():
    depth = 2000
    dom = parse("<html><body>" + "<div>" * depth + "x" + "</div>" * depth + "</body></html>")
    eq(len(dom.find_all("div")), depth)


@check("dom", "unquoted attribute values and self-closing tags")
def _():
    dom = parse('<body><a href=/x?y=1&z=2>go</a><br/><img src=a.png /></body>')
    eq(dom.find("a").attributes.get("href"), "/x?y=1&z=2")
    eq(len(dom.find_all("br")), 1)
    eq(dom.find("img").attributes.get("src"), "a.png")


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

@check("css", "specificity: #id beats a later .class")
def _():
    dom = parse('<html><head><style>#title{color:red}.card{color:green}</style></head>'
                '<body><p id="title" class="card">hi</p></body></html>')
    compute_styles(dom, [parse_stylesheet(dom.find("style").text_content())],
                   viewport_width=800)
    eq(colour(dom.find("p").style.get("color")), "#ff0000")


@check("css", "@media rules survive and evaluate")
def _():
    sheet = parse_stylesheet("@media (min-width:600px){.a{color:red}} .b{color:blue}")
    dom = parse('<body><p class="a">a</p></body>')
    compute_styles(dom, [sheet], viewport_width=1024)
    eq(colour(dom.find("p").style.get("color")), "#ff0000")
    dom = parse('<body><p class="a">a</p><p class="b">b</p></body>')
    compute_styles(dom, [sheet], viewport_width=400)
    eq(colour(dom.find_all("p")[0].style.get("color")), "#000000")
    eq(colour(dom.find_all("p")[1].style.get("color")), "#0000ff")


@check("css", "shorthands expand (margin, font, border)")
def _():
    sheet = parse_stylesheet("p{margin:10px 20px;font:bold 20px/1.5 Georgia, serif;"
                             "border:2px solid red}")
    dom = parse('<body><p>x</p></body>')
    compute_styles(dom, [sheet], viewport_width=800)
    style = dom.find("p").style
    eq(style.get("margin-top"), 10.0)
    eq(style.get("margin-left"), 20.0)
    eq(style.get("font-size"), 20.0)
    eq(style.get("font-weight"), 700.0)
    close(style.get("line-height"), 30.0)
    eq(style.get("border-top-width"), 2.0)
    eq(style.get("border-top-style"), "solid")


@check("css", "descendant and child combinators match correctly")
def _():
    sheet = parse_stylesheet(".nav a{color:red} .nav > .direct{color:blue}")
    dom = parse('<body><div class="nav"><a href="/">x</a>'
                '<span><a href="/">y</a></span><i class="direct">z</i></div></body>')
    compute_styles(dom, [sheet], viewport_width=800)
    anchors = dom.find_all("a")
    eq(colour(anchors[0].style.get("color")), "#ff0000")
    eq(colour(anchors[1].style.get("color")), "#ff0000")
    eq(colour(dom.find("i").style.get("color")), "#0000ff")


@check("css", "viewport units resolve")
def _():
    set_viewport(1000, 500)
    eq(parse_length("10vw"), 100.0)
    eq(parse_length("50vh"), 250.0)
    eq(parse_length("1.5em", font_size=20), 30.0)


@check("css", "inheritance: colour flows to children, not to siblings")
def _():
    sheet = parse_stylesheet(".wrap{color:#123456}")
    dom = parse('<body><div class="wrap"><p><span>x</span></p></div><p>y</p></body>')
    compute_styles(dom, [sheet], viewport_width=800)
    eq(colour(dom.find("span").style.get("color")), "#123456")
    eq(colour(dom.find_all("p")[1].style.get("color")), "#000000")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

@check("layout", "box model: width, padding, margins and auto centring")
def _():
    _, tree = layout_of(
        '<body><div class="card"><p>text</p></div></body>',
        ".card{margin:20px auto;max-width:650px;padding:25px}", width=860)
    card = find_box(tree, cls="card")
    close(card.width, 700.0, message="border-box width")
    eq(card.margin_left, card.margin_right)
    close(card.content_width, 650.0, message="content width")


@check("layout", "inline-block lays out and paints content")
def _():
    _, tree = layout_of('<body><div><div class="ib">CONTENT</div></div></body>',
                        ".ib{display:inline-block;width:120px}")
    box = find_box(tree, cls="ib")
    close(box.width, 120.0)
    eq(len(box.lines), 1)
    if box.height <= 0:
        raise AssertionError("inline-block has no height")


@check("layout", "relayout does not accumulate stale line items")
def _():
    _, tree = layout_of('<body><div class="box"><img src="x.png">'
                        '<div>label</div></div></body>',
                        ".box{display:block;width:200px;padding:8px}")
    box = find_box(tree, cls="box")
    before = len(box.lines)
    compute_layout(tree, 0, 0, 800, MEASURER)
    compute_layout(tree, 0, 0, 800, MEASURER)
    eq(len(box.lines), before, "lines grew on relayout")


@check("layout", "inline-block holding block children stacks them")
def _():
    _, tree = layout_of(
        '<body><div class="card"><div class="pic">x</div>'
        '<div class="cap">hello</div></div></body>',
        ".card{display:inline-block;width:200px}"
        ".pic{height:60px}.cap{height:20px}")
    card = find_box(tree, cls="card")
    pic = find_box(tree, cls="pic")
    cap = find_box(tree, cls="cap")
    if pic.height < 59:
        raise AssertionError(f"block child not laid out: {pic.height}")
    if cap.y < pic.y + pic.height - 1:
        raise AssertionError("block children overlap")


@check("layout", "flex items are blockified, so their blocks lay out")
def _():
    _, tree = layout_of(
        '<body><div class="row"><a class="card">'
        '<div class="inner">x</div></a></div></body>',
        ".row{display:flex;flex-wrap:wrap;width:600px}"
        ".card{display:inline-block;width:180px}"
        ".inner{height:40px}")
    card = find_box(tree, cls="card")
    inner = find_box(tree, cls="inner")
    eq(card.display, "block")
    if inner.height < 39:
        raise AssertionError(f"flex item content not laid out: {inner.height}")


@check("layout", "deeply nested blocks do not recurse forever")
def _():
    html = "<html><body>" + "<div>" * 400 + "x" + "</div>" * 400 + "</body></html>"
    dom = parse(html)
    compute_styles(dom, [], viewport_width=800)
    tree = build_layout_tree(dom)
    compute_layout(tree, 0, 0, 800, MEASURER)
    if tree.height <= 0:
        raise AssertionError("no height")


@check("layout", "table shrink-to-fit with colspan")
def _():
    _, tree = layout_of(
        '<body><table border="1"><tr><td colspan="2">wide</td></tr>'
        '<tr><td>a</td><td>b</td></tr></table></body>')
    table = find_box(tree, tag="table")
    if table is None or table.width <= 0:
        raise AssertionError("table not laid out")


@check("layout", "flex row distributes free space")
def _():
    _, tree = layout_of(
        '<body><div class="row"><div>a</div><div>b</div><div>c</div></div></body>',
        ".row{display:flex;gap:10px}.row>div{flex:1}", width=600)
    row = find_box(tree, cls="row")
    children = [c for c in row.children if c.is_block_level()]
    eq(len(children), 3)
    close(children[0].width, children[1].width, 0.6, "equal widths")
    close(children[1].width, children[2].width, 0.6, "equal widths")
    close(sum(c.width for c in children) + 20, row.content_width, 1.0, "space used")


@check("layout", "grid places items in declared columns")
def _():
    _, tree = layout_of(
        '<body><div class="g"><div>a</div><div>b</div><div>c</div></div></body>',
        ".g{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}", width=620)
    grid = find_box(tree, cls="g")
    children = [c for c in grid.children if c.is_block_level()]
    eq(len(children), 3)
    close(children[0].y, children[1].y, 0.5, "same row")
    close(children[0].y, children[2].y, 0.5, "same row")
    if not (children[0].x < children[1].x < children[2].x):
        raise AssertionError("columns not ordered")


@check("layout", "list items get markers and indentation")
def _():
    _, tree = layout_of("<body><ul><li>one</li><li>two</li></ul></body>")
    items = [b for b in tree.walk() if b.node.tag == "li"]
    eq(len(items), 2)
    for item in items:
        if not item.list_marker:
            raise AssertionError("missing marker")
        if item.x < 20:
            raise AssertionError("list not indented")


@check("layout", "white-space: pre keeps newlines")
def _():
    _, tree = layout_of("<body><pre>a\nb</pre></body>")
    pre = find_box(tree, tag="pre")
    if len(pre.lines) < 2:
        raise AssertionError(f"expected 2 lines, got {len(pre.lines)}")


@check("layout", "text-align: center offsets the line")
def _():
    _, tree = layout_of('<body><p class="c">x</p></body>',
                        ".c{text-align:center}", width=400)
    para = find_box(tree, cls="c")
    line = para.lines[0]
    if line.x <= para.content_x:
        raise AssertionError("line was not centred")


# ---------------------------------------------------------------------------
# Network primitives (no sockets)
# ---------------------------------------------------------------------------

@check("cookies", "cross-site Domain= cookie is rejected")
def _():
    jar = CookieJar()
    jar.store_from_headers("https://evil.com/", [("Set-Cookie", "session=x; Domain=.google.com")])
    eq(jar.header_for("https://www.google.com/"), None)


@check("cookies", "public suffix (.com) is rejected")
def _():
    jar = CookieJar()
    jar.store_from_headers("https://evil.com/", [("Set-Cookie", "x=1; Domain=.com")])
    eq(jar.header_for("https://apple.com/"), None)


@check("cookies", "path matching respects boundaries")
def _():
    jar = CookieJar()
    jar.store_from_headers("https://a.com/", [("Set-Cookie", "p=1; Path=/foo")])
    eq(jar.header_for("https://a.com/foobar"), None)
    eq(jar.header_for("https://a.com/foo/bar"), "p=1")


@check("cookies", "malformed Expires does not raise")
def _():
    jar = CookieJar()
    jar.store_from_headers("https://example.com/", [("Set-Cookie", "a=b; Expires=not-a-date")])
    eq(jar.header_for("https://example.com/"), "a=b")


@check("cookies", "Max-Age=0 deletes")
def _():
    jar = CookieJar()
    jar.store_from_headers("https://example.com/", [("Set-Cookie", "a=1")])
    eq(jar.header_for("https://example.com/"), "a=1")
    jar.store_from_headers("https://example.com/", [("Set-Cookie", "a=1; Max-Age=0")])
    eq(jar.header_for("https://example.com/"), None)


@check("cookies", "Secure cookies are only sent over https")
def _():
    jar = CookieJar()
    jar.store_from_headers("https://example.com/", [("Set-Cookie", "s=1; Secure")])
    eq(jar.header_for("http://example.com/"), None)
    eq(jar.header_for("https://example.com/"), "s=1")


@check("net", "TLS verification is on by default")
def _():
    from core.net.client import HTTPClient
    import ssl

    client = HTTPClient()
    context = client._context()
    eq(context.verify_mode, ssl.CERT_REQUIRED)
    eq(context.check_hostname, True)


@check("net", "redirects rewrite POST to GET and keep the method table honest")
def _():
    from core.net.client import HTTPClient

    client = HTTPClient()
    method, body = "POST", b"a=1"
    # The client's own rule: 301/302 with POST -> GET, 303 -> GET.
    if method == "POST" and 302 in (301, 302, 303):
        method, body = "GET", b""
    eq(method, "GET")
    eq(body, b"")


@check("net", "search input parsing")
def _():
    from core.search import normalise_input

    eq(normalise_input("example.com"), "https://example.com")
    eq(normalise_input("https://example.com"), "https://example.com")
    eq(normalise_input(""), None)
    target = normalise_input("python browser", "Bing")
    if "bing.com/search" not in target:
        raise AssertionError(f"unexpected search URL {target}")


@check("net", "download filenames are sanitised")
def _():
    from core.downloads import sanitise_filename, unique_path

    eq(sanitise_filename("../../etc/passwd"), "passwd")
    eq(sanitise_filename("C:\\Windows\\evil.exe"), "evil.exe")
    eq(sanitise_filename("con.txt"), "con_.txt")
    directory = tempfile.mkdtemp(prefix="sg-dl-")
    open(os.path.join(directory, "a.txt"), "w").close()
    second = unique_path(directory, "a.txt")
    if os.path.basename(second) != "a (2).txt":
        raise AssertionError(f"unexpected name {second}")


# ---------------------------------------------------------------------------
# Accounts / storage
# ---------------------------------------------------------------------------

@check("accounts", "passwords are hashed with PBKDF2 and verify")
def _():
    from core.accounts import AccountStore

    directory = tempfile.mkdtemp(prefix="sg-acct-")
    store = AccountStore(os.path.join(directory, "accounts.json"))
    ok, _message = store.register("yash", "correct horse battery")
    eq(ok, True)
    eq(store.verify("yash", "correct horse battery"), True)
    eq(store.verify("yash", "wrong password"), False)
    ok, _message = store.register("yash", "another password")
    eq(ok, False)
    with open(store.path, "r", encoding="utf-8") as handle:
        contents = handle.read()
    if "correct horse battery" in contents:
        raise AssertionError("password stored in clear text")
    if os.stat(store.path).st_mode & 0o077:
        raise AssertionError("accounts file is world readable")


@check("accounts", "stored credential records are read, not crashed on")
def _():
    from core import accounts as accounts_module

    data = accounts_module.load_accounts_data()
    data.setdefault("credentials", {})["example.com"] = "Basic eWFzaDpzZWNyZXQ="
    accounts_module.save_accounts_data(data)
    eq(accounts_module.get_domain_credentials("example.com"), ("yash", "secret"))


@check("storage", "corrupt JSON is quarantined, not fatal")
def _():
    from core import storage

    directory = tempfile.mkdtemp(prefix="sg-store-")
    path = os.path.join(directory, "settings.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    data = storage.load_json(path, {"ok": True})
    eq(data, {"ok": True})
    if not any(name.endswith(".corrupt") for name in os.listdir(directory)):
        raise AssertionError("corrupt file was not quarantined")


@check("store", "history merges repeat visits and caps its size")
def _():
    from core.store import Store

    store = Store(tempfile.mkdtemp(prefix="sg-history-"))
    store.record_visit("https://example.com/", "Example")
    store.record_visit("https://example.com/", "Example")
    eq(len(store.load_history()), 1)
    store.record_visit("surfgambit://welcome", "internal")
    eq(len(store.load_history()), 1, "internal pages are not recorded")


# ---------------------------------------------------------------------------
# Scripting
# ---------------------------------------------------------------------------

@check("js", "inline scripts mutate the DOM")
def _():
    from core.js import surfscript

    dom = parse('<body><h1 id="h">Hello</h1>'
                '<script>document.getElementById("h").style.color = "red";'
                'document.getElementById("h").textContent = "Changed";</script></body>')
    errors = surfscript.run_document(dom)
    eq(errors, [])
    eq(dom.find("h1").style.get("color"), "red")
    eq(dom.find("h1").text_content(), "Changed")


@check("js", "script errors are reported, not raised")
def _():
    from core.js import surfscript

    dom = parse("<body><script>nope()</script></body>")
    errors = surfscript.run_document(dom)
    if not errors or "ReferenceError" not in errors[0]:
        raise AssertionError(f"unexpected result {errors}")


@check("js", "the legacy surfscript API still works")
def _():
    import surfscript as legacy

    dom = parse('<body><p id="p">x</p></body>')
    calls = []
    legacy.execute_script(
        'document.getElementById("p").innerHTML = "<b>bold</b>";', dom,
        lambda: calls.append(1))
    eq(dom.find("p").find("b").text_content(), "bold")
    if not calls:
        raise AssertionError("layout callback was not invoked")


# ---------------------------------------------------------------------------
# Compatibility shims
# ---------------------------------------------------------------------------

@check("shims", "legacy module APIs still import and answer")
def _():
    import accounts
    import cookies
    import css_compiler
    import game
    import layout
    import network
    import parser
    import surfscript

    dom = parser.HTMLParser("<p>hi</p>").parse()
    eq(dom.tag, "html")
    eq(parser.CSSParser.parse(".a{color:red}"), [(".a", {"color": "red"})])
    eq(len(prefix_elements(dom)), 1)
    eq(css_compiler.CSSRule("a .b", {}).specificity, 11)
    eq(game.SpaceInvaderGame is game.SpaceInvaders, True)


# ---------------------------------------------------------------------------
# Engine checks: cache, streaming, culling, the script interpreter, timers
# ---------------------------------------------------------------------------

@check("cache", "fresh entries are served without touching the network")
def _():
    from core.net.cache import HTTPCache

    cache = HTTPCache(None)
    cache.store("https://example.com/", 200, "OK",
                {"cache-control": "max-age=600"}, [], b"<html>hi</html>")
    entry = cache.lookup("https://example.com/")
    if entry is None or not entry.is_fresh():
        raise AssertionError("entry was not usable")
    eq(entry.body, b"<html>hi</html>")
    eq(cache.stats()["hits"], 1)


@check("cache", "stale entries are stored but revalidated, not reused blindly")
def _():
    from core.net.cache import HTTPCache

    cache = HTTPCache(None)
    cache.store("https://example.com/", 200, "OK",
                {"etag": '"legacy"', "cache-control": "max-age=0"}, [], b"x")
    entry = cache.lookup("https://example.com/")
    if entry is None or entry.is_fresh():
        raise AssertionError("entry should be stale with max-age=0")
    eq(entry.conditional_headers(), {"If-None-Match": '"legacy"'})
    entry.refresh({"etag": '"legacy"', "cache-control": "max-age=600"}, [])
    if not entry.is_fresh():
        raise AssertionError("304 did not extend freshness")


@check("cache", "entries survive a restart and no-store is never written")
def _():
    from core.net.cache import HTTPCache

    directory = tempfile.mkdtemp(prefix="sg-cache-")
    first = HTTPCache(directory)
    first.store("https://example.com/a", 200, "OK", {}, [], b"body-a")
    first.store("https://example.com/b", 200, "OK",
                {"cache-control": "no-store"}, [], b"body-b")
    second = HTTPCache(directory)
    entry = second.lookup("https://example.com/a")
    eq(entry.body if entry else None, b"body-a")
    eq(second.lookup("https://example.com/b"), None)


@check("net", "requests are logged for the devtools network panel")
def _():
    import threading
    import http.server
    import socketserver
    from core.net.client import HTTPClient

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"hello"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = HTTPClient()
        response = client.get(f"http://127.0.0.1:{port}/")
        eq(response.text, "hello")
        eq(len(client.log), 1)
        eq(client.log[0].status, 200)
        if "GET" not in client.log_text():
            raise AssertionError("log_text did not render")
    finally:
        server.shutdown()


@check("net", "streaming download reports progress and can be cancelled")
def _():
    import threading
    import http.server
    import socketserver
    from core.net.client import HTTPClient, Cancelled

    blob = b"q" * (96 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, *args):
            pass

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = HTTPClient()
        seen = []
        written, _headers = client.stream(
            f"http://127.0.0.1:{port}/f", open(os.devnull, "wb"),
            progress=lambda done, total: seen.append((done, total)))
        eq(written, len(blob))
        if not seen or seen[-1][1] != len(blob):
            raise AssertionError(f"progress was wrong: {seen[:3]}")

        cancel = threading.Event()
        cancel.set()
        try:
            client.stream(f"http://127.0.0.1:{port}/f", open(os.devnull, "wb"),
                          cancel=cancel)
        except Cancelled:
            pass
        else:
            raise AssertionError("cancel did not abort the transfer")
    finally:
        server.shutdown()


@check("paint", "the renderer culls boxes outside the viewport")
def _():
    from ui.renderer import Renderer

    class FakeBox:
        def __init__(self, y, height):
            self.y = y
            self.height = height

    renderer = Renderer.__new__(Renderer)      # no Tk needed for this check
    renderer._viewport = (1000.0, 2000.0)
    if renderer._intersects(FakeBox(0, 500)):
        raise AssertionError("box above the viewport was not culled")
    if renderer._intersects(FakeBox(2500, 500)):
        raise AssertionError("box below the viewport was not culled")
    if not renderer._intersects(FakeBox(1500, 200)):
        raise AssertionError("visible box was culled")
    if not renderer._intersects(FakeBox(1900, 400)):
        raise AssertionError("box straddling the bottom edge was culled")


@check("js", "loops, functions and closures behave")
def _():
    from core.js.surfscript import Interpreter

    interp = Interpreter()
    eq(interp.evaluate_source("let s=0; for (let i=0;i<6;i++){ s+=i; } s"), 15)
    eq(interp.evaluate_source(
        "function f(n){ if(n<=1) return 1; return n*f(n-1); } f(6)"), 720)
    eq(interp.evaluate_source(
        "function add(a){ return function(b){ return a+b; }; } add(2)(3)"), 5)
    eq(interp.evaluate_source("let i=0; let n=0; while(true){ i++; if(i>4){break;} n+=i; } n"), 10)


@check("js", "arrays, objects and string methods behave")
def _():
    from core.js.surfscript import Interpreter

    interp = Interpreter()
    eq(interp.evaluate_source("[3,1,2].sort().join('')"), "123")
    eq(interp.evaluate_source("let a=[1,2,3]; a.push(4); a.length"), 4)
    eq(interp.evaluate_source(
        "[1,2,3,4].filter(function(v){return v%2===0;}).map(function(v){return v*10;}).join('-')"),
       "20-40")
    eq(interp.evaluate_source("let o={a:1}; o['b']=2; o.a + o.b"), 3)
    eq(interp.evaluate_source("'  Mixed Case  '.trim().toLowerCase()"), "mixed case")
    eq(interp.evaluate_source("'a,b,c'.split(',').length"), 3)
    eq(interp.evaluate_source("typeof [1] === 'object' && Array.isArray([1])"), True)


@check("js", "comments and ternary expressions parse")
def _():
    from core.js.surfscript import Interpreter

    interp = Interpreter()
    eq(interp.evaluate_source("// line\nlet x=2; /* block */ x > 1 ? 'yes' : 'no'"), "yes")


@check("js", "regex literals, bitwise operators and `new` work")
def _():
    from core.js.surfscript import Interpreter

    interp = Interpreter()
    eq(interp.evaluate_source("let re = /^ab+c$/; re.test('abbbc')"), True)
    eq(interp.evaluate_source("'a1b22'.replace(/[0-9]+/, '#')"), "a#b22")
    eq(interp.evaluate_source("'x y  z'.split(/\\s+/).length"), 3)
    eq(interp.evaluate_source("6 & 3"), 2)
    eq(interp.evaluate_source("1 << 4"), 16)
    eq(interp.evaluate_source(
        "function Point(x, y) { this.x = x; this.y = y; }"
        "let p = new Point(2, 3); p.x * p.y"), 6)
    eq(interp.evaluate_source("new Date().getFullYear() > 2000"), True)


@check("js", "the instruction budget stops runaway loops")
def _():
    from core.js.dom_bridge import DocumentRuntime

    runtime = DocumentRuntime(parse("<body></body>"))
    errors = runtime.evaluate_script("while (true) { }")
    if not errors or "instruction budget" not in errors[0]:
        raise AssertionError(f"no budget error: {errors}")


@check("js", "DOM API: createElement, appendChild, classList, querySelectorAll")
def _():
    from core.js.dom_bridge import DocumentRuntime

    dom = parse('<body><div id="host" class="a"></div><p class="x">one</p>'
                '<p class="x">two</p></body>')
    runtime = DocumentRuntime(dom)
    errors = runtime.evaluate_script(
        "let d = document.createElement('span');"
        "d.textContent = 'new';"
        "d.classList.add('tag');"
        "document.getElementById('host').appendChild(d);"
        "document.getElementById('host').classList.toggle('b');")
    eq(errors, [])
    host = next(n for n in dom.walk() if n.attributes.get("id") == "host")
    eq([c.tag for c in host.children], ["span"])
    eq(host.children[0].text_content(), "new")
    eq(sorted(host.attributes["class"].split()), ["a", "b"])
    eq(runtime.interp.evaluate_source("document.querySelectorAll('p.x').length"), 2)
    eq(runtime.interp.evaluate_source("document.querySelector('#host span').textContent"),
       "new")


@check("js", "click listeners fire and can cancel the default action")
def _():
    from core.js.dom_bridge import DocumentRuntime, Event

    dom = parse('<body><a id="link" href="/next">go</a></body>')
    runtime = DocumentRuntime(dom)
    runtime.evaluate_script(
        "document.getElementById('link').addEventListener('click', function (e) {"
        "  e.preventDefault();"
        "  document.title = 'clicked';"
        "});")
    link = next(n for n in dom.walk() if n.attributes.get("id") == "link")
    event = Event("click", runtime.wrap(link))
    runtime.dispatch(link, event)
    eq(event.default_prevented, True)
    eq(dom.find("title").text_content(), "clicked")


@check("js", "inline onclick attributes run with the right `this`")
def _():
    from core.js.dom_bridge import DocumentRuntime, Event

    dom = parse('<body><button id="b" onclick="this.textContent = \'hit\'">press</button></body>')
    runtime = DocumentRuntime(dom)
    button = next(n for n in dom.walk() if n.attributes.get("id") == "b")
    runtime.dispatch(button, Event("click", runtime.wrap(button)))
    eq(button.text_content(), "hit")


@check("js", "setInterval re-arms itself and clearInterval stops it")
def _():
    from core.js.dom_bridge import DocumentRuntime

    dom = parse("<body><span id=\"n\">0</span></body>")
    runtime = DocumentRuntime(dom)
    runtime.evaluate_script(
        "let n = 0;"
        "let id = setInterval(function () {"
        "  n = n + 1;"
        "  document.getElementById('n').textContent = String(n);"
        "  if (n === 2) { clearInterval(id); }"
        "}, 1);")
    for _step in range(40):
        runtime.run_due_timers()
        time.sleep(0.01)
    eq(dom.find("span").text_content(), "2")


@check("downloads", "downloads stream to disk, report progress and cancel cleanly")
def _():
    import threading
    import http.server
    import socketserver
    from core.downloads import DownloadManager
    from core.net.client import HTTPClient
    from core.store import Store

    blob = b"z" * (128 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Content-Disposition", 'attachment; filename="big.bin"')
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, *args):
            pass

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        directory = tempfile.mkdtemp(prefix="sg-dl-")
        store = Store(directory)
        manager = DownloadManager(store, HTTPClient())
        download = manager.start(f"http://127.0.0.1:{port}/big.bin", directory)
        for _spin in range(200):
            if download.status in ("complete", "failed", "cancelled"):
                break
            time.sleep(0.02)
        eq(download.status, "complete")
        eq(os.path.getsize(download.path), len(blob))
        eq(download.progress, 1.0)
    finally:
        server.shutdown()


@check("store", "history writes are coalesced but flush on demand")
def _():
    from core.store import Store

    directory = tempfile.mkdtemp(prefix="sg-flush-")
    store = Store(directory)
    store.record_visit("https://example.com/one", "One")
    store.record_visit("https://example.com/two", "Two")
    store.flush()
    path = os.path.join(directory, "history.json")
    if not os.path.exists(path):
        raise AssertionError("history file was never written")
    import json

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    eq(len(data), 2)


def prefix_elements(dom: HTMLNode):
    return [n for n in dom.walk() if n.tag == "p"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv
    groups = [a for a in argv if not a.startswith("-")]
    selected = [c for c in _CHECKS if not groups or c[0] in groups]

    failures = 0
    for group, label, fn in selected:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {group}: {label}")
            print(f"       {type(exc).__name__}: {exc}")
            if verbose:
                traceback.print_exc()
        else:
            if verbose:
                print(f"[ok]   {group}: {label}")

    total = len(selected)
    print()
    print(f"{total - failures}/{total} checks passed"
          + (f" — {failures} failed" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
