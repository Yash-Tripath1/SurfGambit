"""
core/style/cascade.py — user-agent stylesheet, presentational hints, cascade
and inheritance.

This is what turns a DOM + a pile of rules into a *computed style* per element:

    font-size       -> float px
    color           -> "#rrggbb"
    margin-top      -> float px
    width           -> Length | None ("auto")
    display         -> "block" | "inline" | "inline-block" | "table" | ...

Cascade order (low -> high):
    1. inherited value (for inherited properties)
    2. user-agent rules
    3. presentational HTML attributes (bgcolor, width, align, <font>)
    4. author rules
    5. inline `style` attribute
    6. author `!important`
    7. inline `!important`

Specificity is the real (a,b,c) tuple, so `#id` finally beats `.class`.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from .. import values
from ..dom.tree import HTMLNode, iter_elements
from .css import Rule, RuleIndex, Stylesheet, media_matches, parse_stylesheet
from .selectors import parse_selector

__all__ = [
    "compute_styles",
    "UA_CSS",
    "INITIAL_STYLE",
    "get_style",
    "resolve_font_family",
]

# ---------------------------------------------------------------------------
# Initial values (CSS 2.2 / what a blank document uses)
# ---------------------------------------------------------------------------

INITIAL_STYLE: Dict[str, object] = {
    "display": "inline",
    "color": "#000000",
    "background-color": None,
    "font-family": "serif",
    "font-size": 16.0,
    "font-weight": 400,
    "font-style": "normal",
    "line-height": None,          # None = normal (1.2 x font-size)
    "text-align": "left",
    "text-decoration": "none",
    "text-transform": "none",
    "white-space": "normal",
    "letter-spacing": 0.0,
    "word-spacing": 0.0,
    "vertical-align": "baseline",
    "list-style-type": "disc",
    "list-style-position": "outside",
    "width": None,
    "height": None,
    "min-width": None,
    "max-width": None,
    "min-height": None,
    "max-height": None,
    "margin-top": 0.0,
    "margin-right": 0.0,
    "margin-bottom": 0.0,
    "margin-left": 0.0,
    "padding-top": 0.0,
    "padding-right": 0.0,
    "padding-bottom": 0.0,
    "padding-left": 0.0,
    "border-top-width": 0.0,
    "border-right-width": 0.0,
    "border-bottom-width": 0.0,
    "border-left-width": 0.0,
    "border-top-style": "none",
    "border-right-style": "none",
    "border-bottom-style": "none",
    "border-left-style": "none",
    "border-top-color": "#000000",
    "border-right-color": "#000000",
    "border-bottom-color": "#000000",
    "border-left-color": "#000000",
    "border-top-left-radius": 0.0,
    "border-top-right-radius": 0.0,
    "border-bottom-right-radius": 0.0,
    "border-bottom-left-radius": 0.0,
    "box-sizing": "content-box",
    "visibility": "visible",
    "float": "none",
    "border-collapse": "separate",
    "border-spacing": 2.0,
    "caption-side": "top",
    "overflow": "visible",
    "opacity": 1.0,
    "text-indent": 0.0,
}

_INHERITED = values.INHERITED_PROPERTIES

# Properties whose computed value is a plain float (px).
_NUMERIC = {
    "font-size", "font-weight", "letter-spacing", "word-spacing",
    "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
    "border-top-left-radius", "border-top-right-radius",
    "border-bottom-right-radius", "border-bottom-left-radius",
    "border-spacing", "line-height", "opacity", "text-indent",
}

# Properties stored as Length-or-None.
_LENGTHY = {"width", "height", "min-width", "max-width", "min-height", "max-height"}

# Properties stored as a colour or None.
_COLOURS = {
    "color", "background-color",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
}


# ---------------------------------------------------------------------------
# User-agent stylesheet
# ---------------------------------------------------------------------------

UA_CSS = r"""
html, body, div, p, h1, h2, h3, h4, h5, h6, ul, ol, li, dl, dt, dd,
blockquote, pre, form, fieldset, hr, table, thead, tbody, tfoot, tr, td, th,
section, article, header, footer, nav, main, aside, figure, figcaption,
address, center, details, summary, hgroup, search, menu, dir, legend,
caption, colgroup, optgroup, marquee, xmp, listing, plaintext {
    display: block;
}
span, a, b, i, em, strong, code, small, sub, sup, u, s, strike, font, label,
cite, q, abbr, acronym, time, mark, kbd, samp, var, dfn, bdi, bdo, big, tt,
nobr, blink { display: inline; }

img, input, select, textarea, button, canvas, meter, progress, object, embed {
    display: inline-block;
}
br { display: inline; }
script, style, head, title, meta, link, base, template, param, source, track,
area, col, datalist, svg, path, g, circle, rect, line, polyline, polygon,
ellipse, defs, use, symbol, iframe, video, audio, noscript, map {
    display: none;
}

html { color: black; background-color: transparent; }
body { margin: 8px; }

h1 { font-size: 2em;   font-weight: bold; margin: 0.67em 0; }
h2 { font-size: 1.5em; font-weight: bold; margin: 0.83em 0; }
h3 { font-size: 1.17em; font-weight: bold; margin: 1em 0; }
h4 { font-size: 1em;   font-weight: bold; margin: 1.33em 0; }
h5 { font-size: 0.83em; font-weight: bold; margin: 1.67em 0; }
h6 { font-size: 0.67em; font-weight: bold; margin: 2.33em 0; }

p { margin: 1em 0; }
blockquote { margin: 1em 40px; }
figure { margin: 1em 40px; }
dl { margin: 1em 0; }
dd { margin-left: 40px; }

ul, ol, menu, dir { margin: 1em 0; padding-left: 40px; }
ul ul, ol ol, ul ol, ol ul { margin: 0; }
li { display: list-item; }
ol { list-style-type: decimal; }
ul { list-style-type: disc; }
ul ul { list-style-type: circle; }
ul ul ul { list-style-type: square; }

b, strong, th { font-weight: bold; }
i, em, cite, var, address, dfn { font-style: italic; }
u, ins { text-decoration: underline; }
s, strike, del { text-decoration: line-through; }
code, kbd, samp, pre, tt, xmp, listing, plaintext { font-family: monospace; }
pre, xmp, listing, plaintext { white-space: pre; margin: 1em 0; }
small, sub, sup { font-size: smaller; }
big { font-size: larger; }
mark { background-color: yellow; color: black; }
center { text-align: center; }
th { text-align: center; }
a:link, a:any-link { color: #0000ee; text-decoration: underline; }
q { font-style: normal; }

hr {
    display: block;
    margin: 0.5em auto;
    border-top: 1px inset #808080;
    height: 0;
}

table { display: table; border-collapse: separate; border-spacing: 2px; }
thead, tbody, tfoot { display: table-row-group; }
tr { display: table-row; }
td, th { display: table-cell; padding: 1px; vertical-align: middle; }
caption { display: table-caption; text-align: center; }
colgroup, col { display: table-column; }

fieldset { margin: 0 2px; border: 1px solid #808080; padding: 0.35em 0.75em 0.625em; }
legend { padding: 0 2px; }

input { border: 1px solid #767676; padding: 1px 2px; background-color: white; }
button { border: 1px solid #767676; padding: 1px 6px; background-color: #efefef; }
textarea { border: 1px solid #767676; padding: 2px; background-color: white; white-space: pre-wrap; }
select { border: 1px solid #767676; background-color: white; }

[hidden] { display: none; }

sup { vertical-align: super; }
sub { vertical-align: sub; }

/* Generic fallbacks so unknown/custom tags don't vanish or overflow. */
[style*="display: none"] { display: none; }
"""

_UA_SHEET: Optional[Stylesheet] = None


def _ua_sheet() -> Stylesheet:
    global _UA_SHEET
    if _UA_SHEET is None:
        _UA_SHEET = parse_stylesheet(UA_CSS, origin="user-agent")
        for rule in _UA_SHEET.rules:
            if rule.selector is not None:
                # UA rules lose to author rules regardless of specificity.
                rule.order = rule.order
    return _UA_SHEET


# ---------------------------------------------------------------------------
# Presentational attributes (legacy HTML, but everywhere on the old web)
# ---------------------------------------------------------------------------

_ALIGN_MAP = {
    "left": "left", "right": "right", "center": "center",
    "middle": "center", "justify": "justify",
}

_FONT_SIZE_BY_ATTR = {"1": 10.0, "2": 13.0, "3": 16.0, "4": 18.0, "5": 24.0,
                      "6": 32.0, "7": 48.0}


def _presentational(node: HTMLNode) -> Dict[str, str]:
    attrs = node.attributes
    out: Dict[str, str] = {}

    bg = attrs.get("bgcolor")
    if bg:
        out["background-color"] = bg

    color = attrs.get("color") or (attrs.get("text") if node.tag == "body" else None)
    if color:
        out["color"] = color

    if node.tag == "body" and attrs.get("background"):
        pass  # background images on <body> are out of scope

    width = attrs.get("width")
    if width:
        out["width"] = width if width.endswith("%") or width.endswith("px") else f"{width}px"
    height = attrs.get("height")
    if height:
        out["height"] = height if height.endswith("%") or height.endswith("px") else f"{height}px"

    align = attrs.get("align")
    if align and align.lower() in _ALIGN_MAP:
        if node.tag in ("table", "img", "div", "p", "h1", "h2", "h3", "tr", "td", "th"):
            # On non-text elements `align` positions horizontally.
            if node.tag in ("td", "th", "tr", "table", "img", "div"):
                out["_align_hint"] = _ALIGN_MAP[align.lower()]
            else:
                out["text-align"] = _ALIGN_MAP[align.lower()]
        else:
            out["text-align"] = _ALIGN_MAP[align.lower()]

    if node.tag == "font":
        face = attrs.get("face")
        if face:
            out["font-family"] = face
        size = attrs.get("size")
        if size:
            out["font-size"] = f"{_FONT_SIZE_BY_ATTR.get(size.strip(), 16.0)}px"

    if node.tag == "hr":
        size = attrs.get("size")
        if size:
            try:
                out["border-top-width"] = f"{float(size):g}px"
            except ValueError:
                pass
        if attrs.get("noshade") is not None:
            out["border-top-color"] = "#808080"

    if node.tag in ("td", "th") and attrs.get("valign"):
        out["vertical-align"] = attrs["valign"]

    border = attrs.get("border")
    if border is not None and node.tag in ("table", "img", "object"):
        try:
            bw = max(0.0, float(border))
        except ValueError:
            bw = 1.0
        if bw > 0:
            for side in ("top", "right", "bottom", "left"):
                out[f"border-{side}-width"] = f"{bw:g}px"
                out[f"border-{side}-style"] = "solid"
                out[f"border-{side}-color"] = "#808080"

    if node.tag in ("td", "th"):
        cellpad = attrs.get("cellpadding")
        if cellpad:
            out["padding"] = cellpad if cellpad.endswith("px") else f"{cellpad}px"

    if attrs.get("nowrap") is not None:
        out["white-space"] = "nowrap"

    if node.tag == "ol" and attrs.get("start"):
        pass  # handled by the list marker renderer

    if node.tag == "img" and attrs.get("border"):
        pass  # already handled above

    return out


# ---------------------------------------------------------------------------
# Computed-value resolution
# ---------------------------------------------------------------------------

def _resolve_decl(
    prop: str,
    raw: str,
    parent: Dict[str, object],
    base: Dict[str, object],
    root_font: float,
    background: Optional[str],
) -> Optional[object]:
    """Convert a declaration's raw string to a computed value."""
    low = raw.strip().lower()

    if prop == "display":
        if low in ("block", "inline", "inline-block", "list-item", "table",
                   "table-row", "table-cell", "table-row-group", "table-header-group",
                   "table-footer-group", "table-caption", "table-column",
                   "table-column-group", "flex", "grid", "none"):
            return low
        return "block" if low in ("flow-root", "block flow") else None

    if prop == "font-family":
        return raw.strip()

    if prop == "font-size":
        if low in ("inherit", "unset", "revert"):
            return None
        size = values.parse_font_size(raw, float(parent.get("font-size", 16.0)), root_font)
        return size

    if prop == "font-weight":
        if low in ("inherit", "unset", "revert"):
            return None
        if low == "bolder":
            return min(900, int(parent.get("font-weight", 400)) + 300)
        if low == "lighter":
            return max(100, int(parent.get("font-weight", 400)) - 300)
        w = values.parse_font_weight(raw)
        return float(w) if w is not None else None

    if prop == "line-height":
        if low in ("inherit", "unset", "revert"):
            return None
        fs = float(base.get("font-size", 16.0))
        lh = values.parse_line_height(raw, fs)
        return lh

    if prop in _COLOURS:
        if low in ("inherit", "unset", "initial"):
            return None
        return values.parse_color(raw, background)

    if prop in _NUMERIC:
        if low in ("inherit", "unset"):
            return None
        if low == "auto" and prop.startswith("margin"):
            return "auto"
        fs = float(base.get("font-size", 16.0))
        px = values.parse_length(raw, None, font_size=fs, root_font_size=root_font)
        if px is None and (prop.startswith("margin") or prop.startswith("padding")):
            # Percentages resolve against the containing block at layout time.
            return values.parse_length_or_percent(raw)
        return px

    if prop in _LENGTHY:
        if low in ("auto", "none", "inherit", "unset", "initial"):
            return None
        return values.parse_length_or_percent(raw)

    if prop == "box-sizing":
        return low if low in ("content-box", "border-box") else None

    if prop == "white-space":
        return {"normal": "normal", "pre": "pre", "nowrap": "nowrap",
                "pre-wrap": "pre-wrap", "pre-line": "pre-line",
                "break-spaces": "pre-wrap"}.get(low)

    if prop == "text-align":
        return low if low in ("left", "right", "center", "justify", "start", "end") else None

    if prop == "text-decoration":
        return {"underline": "underline", "line-through": "line-through",
                "overline": "overline", "none": "none",
                "underline line-through": "underline line-through"}.get(low)

    if prop == "text-transform":
        return low if low in ("none", "uppercase", "lowercase", "capitalize") else None

    if prop == "font-style":
        if "italic" in low or "oblique" in low:
            return "italic"
        if low == "normal":
            return "normal"
        return None

    if prop == "list-style-type":
        return low if low in (
            "disc", "circle", "square", "decimal", "lower-alpha", "upper-alpha",
            "lower-roman", "upper-roman", "none", "lower-greek", "decimal-leading-zero",
        ) else None

    if prop == "vertical-align":
        return low if low in ("baseline", "middle", "top", "bottom", "sub", "super",
                              "text-top", "text-bottom") else None

    if prop == "visibility":
        return low if low in ("visible", "hidden", "collapse") else None

    if prop == "float":
        return low if low in ("left", "right", "none") else None

    if prop == "border-collapse":
        return low if low in ("collapse", "separate") else None

    if prop.endswith("-style"):
        return low if low in values._BORDER_STYLES else None

    return raw.strip()


# Cascade origins, lowest priority first. `!important` reverses the outer order.
_ORIGIN_UA = 0
_ORIGIN_AUTHOR = 1
_ORIGIN_INLINE = 2


def _sort_key(rule: Rule, important: bool, origin: int) -> Tuple:
    """Sort key implementing the CSS cascade: importance, origin, specificity, order."""
    return (
        1 if important else 0,
        origin,
        rule.specificity,
        rule.order,
    )


def compute_styles(
    root: HTMLNode,
    sheets: Iterable[Stylesheet] = (),
    viewport_width: int = 1024,
    zoom: float = 1.0,
    root_font_size: float = 16.0,
    page_background: Optional[str] = None,
    viewport_height: int = 720,
) -> None:
    """
    Compute and attach `node.style` for every element and text node.

    Iterative (explicit stack) so a 2,000-deep document cannot blow the
    Python recursion limit.
    """
    # vw/vh/vmin/vmax need to know how big the window is.
    try:
        from ..values import set_viewport

        set_viewport(viewport_width, viewport_height)
    except Exception:
        pass

    author_rules: List[Rule] = []
    ua_rules: List[Rule] = []
    for sheet in sheets:
        target = ua_rules if sheet.origin == "user-agent" else author_rules
        for rule in sheet.rules:
            if media_matches(rule.media, viewport_width):
                target.append(rule)

    author_index = RuleIndex()
    author_index.add(author_rules)
    ua_index = RuleIndex()
    ua_index.add(_ua_sheet().rules)

    # Pre-parse inline styles once.
    stack: List[Tuple[HTMLNode, Optional[Dict[str, object]]]] = [(root, None)]

    while stack:
        node, parent_style = stack.pop()

        if node.is_text():
            style: Dict[str, object] = {}
            if parent_style:
                for prop in _INHERITED:
                    if prop in parent_style:
                        style[prop] = parent_style[prop]
            style["display"] = "inline"
            style.setdefault("font-size", 16.0 * zoom)
            style.setdefault("color", "#000000")
            node.style = style
            continue

        inherited: Dict[str, object] = {}
        if parent_style is not None:
            for prop in _INHERITED:
                if prop in parent_style:
                    inherited[prop] = parent_style[prop]
        else:
            inherited = dict(INITIAL_STYLE)

        # Effective background for alpha compositing.
        background = None
        if parent_style:
            background = parent_style.get("background-color")
        if background is None:
            background = page_background

        # Collect winning declarations per property.
        # winners[prop] = (sort_key, raw_value)
        winners: Dict[str, Tuple[Tuple, str]] = {}

        def apply(rules: Iterable[Rule], origin: int) -> None:
            for rule in rules:
                if rule.selector is None or not rule.selector.matches(node):
                    continue
                for key, raw in rule.declarations.items():
                    important = key.endswith("!important")
                    prop = key[:-len("!important")] if important else key
                    sk = _sort_key(rule, important, origin)
                    cur = winners.get(prop)
                    if cur is None or sk >= cur[0]:
                        winners[prop] = (sk, raw)

        # 2. UA rules
        apply(ua_index.candidates(node), _ORIGIN_UA)

        # 3. presentational hints (beat UA, lose to author)
        present = _presentational(node)
        hint_key = (0, _ORIGIN_UA, (1000, 0, 0), -1)
        for prop, raw in present.items():
            expanded = values.expand_declaration(prop, raw) if prop not in (
                "width", "height", "background-color", "color", "font-family",
                "font-size", "text-align", "white-space", "vertical-align",
                "padding", "_align_hint",
            ) else {prop: raw}
            for k, v in expanded.items():
                winners[k] = (hint_key, v)

        # 4. author rules
        apply(author_index.candidates(node), _ORIGIN_AUTHOR)

        # 5. inline style
        inline = node.attributes.get("style")
        if inline:
            inline_rule = Rule(None, _parse_inline_declarations(inline), 10 ** 9)
            for key, raw in inline_rule.declarations.items():
                important = key.endswith("!important")
                prop = key[:-len("!important")] if important else key
                sk = (1 if important else 0, _ORIGIN_INLINE, (1000, 0, 0), 10 ** 9)
                winners[prop] = (sk, raw)

        # Build the computed style.
        style: Dict[str, object] = dict(INITIAL_STYLE)
        for prop in _INHERITED:
            if prop in inherited:
                style[prop] = inherited[prop]

        base = style  # for em/% resolution within this element
        # Resolve font-size first: every other length (`em`, `line-height`,
        # `ch`, percentages of the font) is relative to it.
        ordered = sorted(winners.items(),
                         key=lambda item: 0 if item[0] in ("font-size", "font-family",
                                                           "font-weight", "font-style")
                         else 1)
        for prop, (sk, raw) in ordered:
            if prop.startswith("_"):
                continue
            resolved = _resolve_decl(prop, raw, inherited, base, root_font_size * zoom, background)
            if resolved is None:
                if raw.strip().lower() in ("inherit", "unset"):
                    if prop in _INHERITED and prop in inherited:
                        style[prop] = inherited[prop]
                continue
            style[prop] = resolved

        _normalise(node, style, inherited, zoom)
        node.style = style

        for child in reversed(node.children):
            stack.append((child, style))


def _parse_inline_declarations(text: str) -> Dict[str, str]:
    from .css import parse_declarations
    return parse_declarations(text)


_TAG_DISPLAY = {
    "block": {"html", "body", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
              "ul", "ol", "li", "dl", "dt", "dd", "blockquote", "pre", "form",
              "fieldset", "hr", "table", "section", "article", "header",
              "footer", "nav", "main", "aside", "figure", "figcaption",
              "address", "center", "details", "summary", "hgroup", "search",
              "legend", "caption", "thead", "tbody", "tfoot", "tr"},
}
_BLOCK_TAGS = _TAG_DISPLAY["block"]


def _normalise(node: HTMLNode, style: Dict[str, object], inherited: Dict[str, object],
               zoom: float) -> None:
    """Final adjustments: zoom, tag-based display fallbacks, border width = 0
    when the style is `none`."""
    # Zoom scales every absolute length. Percentages stay relative.
    if zoom != 1.0:
        for prop in _NUMERIC:
            if prop == "opacity":
                continue
            val = style.get(prop)
            if isinstance(val, (int, float)):
                style[prop] = float(val) * zoom
        for prop in ("width", "height", "min-width", "max-width", "min-height", "max-height"):
            val = style.get(prop)
            if isinstance(val, values.Length) and val.unit == "px":
                style[prop] = values.Length(val.value * zoom, "px")

    for side in ("top", "right", "bottom", "left"):
        if style.get(f"border-{side}-style", "none") in ("none", "hidden"):
            style[f"border-{side}-width"] = 0.0

    if node.tag in _BLOCK_TAGS and style.get("display") == "inline":
        # A `<li>`/`<tr>` etc. whose display we couldn't resolve keeps its
        # natural block behaviour rather than collapsing into the text flow.
        if node.tag in ("li", "tr", "td", "th", "thead", "tbody", "tfoot", "caption"):
            pass
        else:
            style["display"] = "block"

    if style.get("font-size") is None:
        style["font-size"] = float(inherited.get("font-size", 16.0 * zoom))


def get_style(node) -> Dict[str, object]:
    return getattr(node, "style", None) or INITIAL_STYLE


# ---------------------------------------------------------------------------
# Font family resolution
# ---------------------------------------------------------------------------

_GENERIC_FALLBACKS = {
    "monospace": ["Courier New", "DejaVu Sans Mono", "Consolas", "Liberation Mono", "monospace"],
    "sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "Liberation Sans", "Nimbus Sans", "sans-serif"],
    "serif": ["Times New Roman", "Times", "DejaVu Serif", "Liberation Serif", "Nimbus Roman", "serif"],
    "cursive": ["Comic Sans MS", "DejaVu Sans", "cursive"],
    "fantasy": ["Impact", "DejaVu Sans", "fantasy"],
    "system-ui": ["Segoe UI", "Helvetica Neue", "DejaVu Sans", "sans-serif"],
}


def parse_font_families(raw: Optional[str]) -> List[str]:
    """Split a CSS font-family list into individual family names."""
    if not raw:
        return []
    out: List[str] = []
    buf: List[str] = []
    quote = None
    for ch in raw:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            name = "".join(buf).strip()
            if name:
                out.append(name)
            buf = []
        else:
            buf.append(ch)
    name = "".join(buf).strip()
    if name:
        out.append(name)
    return out


_available_families: Optional[set] = None


def resolve_font_family(raw: Optional[str], available: Optional[set] = None) -> str:
    """
    Pick the first family in the CSS list that actually exists on this machine,
    falling back through the generic-family table.
    """
    global _available_families
    if available is None:
        if _available_families is None:
            try:
                import tkinter.font as tkfont
                _available_families = {f.lower() for f in tkfont.families()}
            except Exception:
                _available_families = set()
        available = _available_families

    candidates = parse_font_families(raw) or ["serif"]
    for name in candidates:
        key = name.lower()
        if key in _GENERIC_FALLBACKS:
            for fallback in _GENERIC_FALLBACKS[key]:
                if not available or fallback.lower() in available or fallback == key:
                    return fallback
            return _GENERIC_FALLBACKS[key][-2]
        if not available or name.lower() in available:
            return name
    # Nothing matched — use the generic of the last entry if it is one.
    for name in reversed(candidates):
        if name.lower() in _GENERIC_FALLBACKS:
            return _GENERIC_FALLBACKS[name.lower()][0]
    return "DejaVu Sans" if available and "dejavu sans" in available else "Arial"
