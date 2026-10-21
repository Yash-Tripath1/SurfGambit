"""
core/values.py — CSS value parsing.

Everything a stylesheet can say, converted into things layout can use:

    "12px"              -> 12.0
    "1.5em"             -> 24.0        (relative to the parent font-size)
    "120%"              -> (120.0, "%")   (caller resolves against a base)
    "bold"              -> 700
    "#00adb5"           -> "#00adb5"
    "rgb(0, 173, 181)"  -> "#00adb5"
    "margin: 0 auto"    -> {"margin-top": 0, "margin-right": "auto", ...}

Only public-domain-avoiding plain CSS here, no dependencies.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Lengths
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))")

# Absolute units -> CSS px
_ABS_UNITS = {
    "px": 1.0,
    "pt": 4.0 / 3.0,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "q": 96.0 / 101.6,
}

# Font-relative units resolved against a font size
_REL_UNITS = {"em", "rem", "ex", "ch"}


# Viewport size for vw/vh/vmin/vmax. The cascade sets this once per layout;
# explicit parameters still win (parse_length(font_size=...) etc.).
_VIEWPORT = [0.0, 0.0]


def set_viewport(width: float, height: float) -> None:
    """Record the viewport used to resolve vw/vh units."""
    _VIEWPORT[0] = max(0.0, float(width))
    _VIEWPORT[1] = max(0.0, float(height))


def get_viewport() -> Tuple[float, float]:
    return _VIEWPORT[0], _VIEWPORT[1]


class Length:
    """A parsed length: either absolute px or a percentage of a base."""

    __slots__ = ("value", "unit")

    def __init__(self, value: float, unit: str = "px") -> None:
        self.value = value
        self.unit = unit

    def resolve(self, base: float) -> float:
        if self.unit == "%":
            return base * self.value / 100.0
        return self.value

    def __repr__(self) -> str:
        return f"Length({self.value}{self.unit})"

    def __float__(self) -> float:
        return self.value


def parse_length(
    text: Optional[str],
    default: Optional[float] = None,
    font_size: float = 16.0,
    root_font_size: float = 16.0,
) -> Optional[float]:
    """Parse a length to CSS px. Returns ``default`` for auto/none/invalid."""
    if text is None:
        return default
    s = str(text).strip().lower()
    if not s or s in ("auto", "none", "normal", "inherit", "initial", "unset", "-moz-fit-content"):
        return default

    m = _NUM_RE.match(s)
    if not m:
        return default
    try:
        num = float(m.group(1))
    except ValueError:
        return default
    unit = s[m.end():].strip()

    if unit in ("", "px"):
        return num
    if unit in _ABS_UNITS:
        return num * _ABS_UNITS[unit]
    if unit == "em":
        return num * font_size
    if unit == "rem":
        return num * root_font_size
    if unit in ("ex", "ch"):
        # Rough but stable: ex ~ 0.5em, ch ~ 0.5em for most proportional faces.
        return num * font_size * 0.5
    if unit == "%":
        # Percentage lengths need a containing block; callers that care use
        # parse_length_or_percent(). Here we return the raw number scaled by
        # the font size only as a last resort.
        return None
    if unit in ("vw", "vh", "vmin", "vmax"):
        width, height = _VIEWPORT
        if width <= 0 and height <= 0:
            return default
        if unit == "vw":
            return num * width / 100.0
        if unit == "vh":
            return num * height / 100.0
        smaller = min(width, height) if width > 0 and height > 0 else max(width, height)
        larger = max(width, height)
        return num * (smaller if unit == "vmin" else larger) / 100.0
    return default


def parse_length_or_percent(text: Optional[str]) -> Optional[Length]:
    """Parse to a Length that may carry a '%' unit."""
    if text is None:
        return None
    s = str(text).strip().lower()
    if not s or s in ("auto", "none", "normal", "inherit", "initial", "unset"):
        return None
    m = _NUM_RE.match(s)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = s[m.end():].strip() or "px"
    if unit == "%":
        return Length(num, "%")
    px = parse_length(s)
    if px is None:
        return None
    return Length(px, "px")


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

# The CSS named colours (CSS Color Module 4) as Tk-friendly hex strings.
NAMED_COLORS: Dict[str, str] = {
    "aliceblue": "#f0f8ff", "antiquewhite": "#faebd7", "aqua": "#00ffff",
    "aquamarine": "#7fffd4", "azure": "#f0ffff", "beige": "#f5f5dc",
    "bisque": "#ffe4c4", "black": "#000000", "blanchedalmond": "#ffebcd",
    "blue": "#0000ff", "blueviolet": "#8a2be2", "brown": "#a52a2a",
    "burlywood": "#deb887", "cadetblue": "#5f9ea0", "chartreuse": "#7fff00",
    "chocolate": "#d2691e", "coral": "#ff7f50", "cornflowerblue": "#6495ed",
    "cornsilk": "#fff8dc", "crimson": "#dc143c", "cyan": "#00ffff",
    "darkblue": "#00008b", "darkcyan": "#008b8b", "darkgoldenrod": "#b8860b",
    "darkgray": "#a9a9a9", "darkgreen": "#006400", "darkgrey": "#a9a9a9",
    "darkkhaki": "#bdb76b", "darkmagenta": "#8b008b", "darkolivegreen": "#556b2f",
    "darkorange": "#ff8c00", "darkorchid": "#9932cc", "darkred": "#8b0000",
    "darksalmon": "#e9967a", "darkseagreen": "#8fbc8f", "darkslateblue": "#483d8b",
    "darkslategray": "#2f4f4f", "darkslategrey": "#2f4f4f", "darkturquoise": "#00ced1",
    "darkviolet": "#9400d3", "deeppink": "#ff1493", "deepskyblue": "#00bfff",
    "dimgray": "#696969", "dimgrey": "#696969", "dodgerblue": "#1e90ff",
    "firebrick": "#b22222", "floralwhite": "#fffaf0", "forestgreen": "#228b22",
    "fuchsia": "#ff00ff", "gainsboro": "#dcdcdc", "ghostwhite": "#f8f8ff",
    "gold": "#ffd700", "goldenrod": "#daa520", "gray": "#808080",
    "green": "#008000", "greenyellow": "#adff2f", "grey": "#808080",
    "honeydew": "#f0fff0", "hotpink": "#ff69b4", "indianred": "#cd5c5c",
    "indigo": "#4b0082", "ivory": "#fffff0", "khaki": "#f0e68c",
    "lavender": "#e6e6fa", "lavenderblush": "#fff0f5", "lawngreen": "#7cfc00",
    "lemonchiffon": "#fffacd", "lightblue": "#add8e6", "lightcoral": "#f08080",
    "lightcyan": "#e0ffff", "lightgoldenrodyellow": "#fafad2", "lightgray": "#d3d3d3",
    "lightgreen": "#90ee90", "lightgrey": "#d3d3d3", "lightpink": "#ffb6c1",
    "lightsalmon": "#ffa07a", "lightseagreen": "#20b2aa", "lightskyblue": "#87cefa",
    "lightslategray": "#778899", "lightslategrey": "#778899", "lightsteelblue": "#b0c4de",
    "lightyellow": "#ffffe0", "lime": "#00ff00", "limegreen": "#32cd32",
    "linen": "#faf0e6", "magenta": "#ff00ff", "maroon": "#800000",
    "mediumaquamarine": "#66cdaa", "mediumblue": "#0000cd", "mediumorchid": "#ba55d3",
    "mediumpurple": "#9370db", "mediumseagreen": "#3cb371", "mediumslateblue": "#7b68ee",
    "mediumspringgreen": "#00fa9a", "mediumturquoise": "#48d1cc", "mediumvioletred": "#c71585",
    "midnightblue": "#191970", "mintcream": "#f5fffa", "mistyrose": "#ffe4e1",
    "moccasin": "#ffe4b5", "navajowhite": "#ffdead", "navy": "#000080",
    "oldlace": "#fdf5e6", "olive": "#808000", "olivedrab": "#6b8e23",
    "orange": "#ffa500", "orangered": "#ff4500", "orchid": "#da70d6",
    "palegoldenrod": "#eee8aa", "palegreen": "#98fb98", "paleturquoise": "#afeeee",
    "palevioletred": "#db7093", "papayawhip": "#ffefd5", "peachpuff": "#ffdab9",
    "peru": "#cd853f", "pink": "#ffc0cb", "plum": "#dda0dd",
    "powderblue": "#b0e0e6", "purple": "#800080", "rebeccapurple": "#663399",
    "red": "#ff0000", "rosybrown": "#bc8f8f", "royalblue": "#4169e1",
    "saddlebrown": "#8b4513", "salmon": "#fa8072", "sandybrown": "#f4a460",
    "seagreen": "#2e8b57", "seashell": "#fff5ee", "sienna": "#a0522d",
    "silver": "#c0c0c0", "skyblue": "#87ceeb", "slateblue": "#6a5acd",
    "slategray": "#708090", "slategrey": "#708090", "snow": "#fffafa",
    "springgreen": "#00ff7f", "steelblue": "#4682b4", "tan": "#d2b48c",
    "teal": "#008080", "thistle": "#d8bfd8", "tomato": "#ff6347",
    "turquoise": "#40e0d0", "violet": "#ee82ee", "wheat": "#f5deb3",
    "white": "#ffffff", "whitesmoke": "#f5f5f5", "yellow": "#ffff00",
    "yellowgreen": "#9acd32",
}

_FUNC_COLOR_RE = re.compile(r"^(rgba?|hsla?)\((.*)\)$", re.I | re.S)


def _clamp(v: float, lo: float = 0.0, hi: float = 255.0) -> int:
    return int(max(lo, min(hi, round(v))))


def _hsl_to_rgb(h: float, s: float, l: float) -> Tuple[int, int, int]:
    h = (h % 360) / 360.0
    s = max(0.0, min(1.0, s))
    l = max(0.0, min(1.0, l))
    if s == 0:
        v = _clamp(l * 255)
        return v, v, v
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    def hue(t: float) -> float:
        t = t % 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    return _clamp(hue(h + 1 / 3) * 255), _clamp(hue(h) * 255), _clamp(hue(h - 1 / 3) * 255)


def _split_args(body: str) -> list:
    """Split 'rgb(1, 2, 3)' style arguments, honouring slash syntax."""
    body = body.replace("/", " ").replace(",", " ")
    return [p for p in body.split() if p]


def _channel(tok: str) -> Optional[float]:
    tok = tok.strip()
    if tok.endswith("%"):
        try:
            return float(tok[:-1]) * 255.0 / 100.0
        except ValueError:
            return None
    try:
        return float(tok)
    except ValueError:
        return None


def _alpha(tok: Optional[str]) -> Optional[float]:
    if tok is None:
        return None
    tok = tok.strip()
    try:
        if tok.endswith("%"):
            return float(tok[:-1]) / 100.0
        return float(tok)
    except ValueError:
        return None


def parse_color(value: Optional[str], background: Optional[str] = None) -> Optional[str]:
    """
    Normalise a CSS colour to ``#rrggbb``.

    Returns ``None`` for `transparent`, `inherit`, `currentcolor` and anything
    unparseable, which the caller then treats as "no colour set here".

    Alpha is composited over ``background`` when both are known (Tk's canvas
    does not support per-item alpha), otherwise the colour is treated as opaque.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in ("transparent", "inherit", "initial", "unset", "currentcolor", "none"):
        return None

    if s.startswith("#"):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) == 8:                      # #rrggbbaa
            try:
                a = int(h[6:8], 16) / 255.0
            except ValueError:
                return None
            h = h[:6]
            rgb = _hex_to_rgb("#" + h)
            if rgb is None:
                return None
            return _composite(rgb, a, background)
        if len(h) == 6:
            try:
                int(h, 16)
            except ValueError:
                return None
            return "#" + h
        return None

    m = _FUNC_COLOR_RE.match(s)
    if m:
        kind = m.group(1).lower()
        args = _split_args(m.group(2))
        if len(args) < 3:
            return None
        if kind.startswith("rgb"):
            chans = [_channel(a) for a in args[:3]]
            if any(c is None for c in chans):
                return None
            a = _alpha(args[3]) if len(args) > 3 else None
            rgb = tuple(_clamp(c) for c in chans)  # type: ignore[arg-type]
        else:
            try:
                hue = float(args[0].replace("deg", ""))
                sat = float(args[1].rstrip("%")) / 100.0
                lig = float(args[2].rstrip("%")) / 100.0
            except ValueError:
                return None
            rgb = _hsl_to_rgb(hue, sat, lig)
            a = _alpha(args[3]) if len(args) > 3 else None
        if a is None or a >= 0.999:
            return "#%02x%02x%02x" % rgb
        return _composite(rgb, a, background)

    if s in NAMED_COLORS:
        return NAMED_COLORS[s]

    # Already a Tk-compatible "#hex" or an X11 name we don't know: pass through
    # only if it looks safe (Tk raises on garbage colours).
    if re.fullmatch(r"#[0-9a-f]{6}", s):
        return s
    return None


def _hex_to_rgb(hexstr: str) -> Optional[Tuple[int, int, int]]:
    try:
        h = hexstr.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return None


def _composite(rgb: Tuple[int, int, int], alpha: float, background: Optional[str]) -> str:
    if background:
        bg = parse_color(background)
        base = _hex_to_rgb(bg) if bg else None
    else:
        base = None
    if base is None:
        base = (255, 255, 255)
    r = _clamp(rgb[0] * alpha + base[0] * (1 - alpha))
    g = _clamp(rgb[1] * alpha + base[1] * (1 - alpha))
    b = _clamp(rgb[2] * alpha + base[2] * (1 - alpha))
    return "#%02x%02x%02x" % (r, g, b)


def is_dark(color: Optional[str]) -> bool:
    """True if a colour is dark enough that white text will read on it."""
    rgb = _hex_to_rgb(color) if color else None
    if not rgb:
        return False
    r, g, b = rgb
    # Relative luminance (sRGB approximation).
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 140


# ---------------------------------------------------------------------------
# Font weights and keyword maps
# ---------------------------------------------------------------------------

_FONT_WEIGHT_KEYWORDS: Dict[str, int] = {
    "thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200,
    "light": 300, "normal": 400, "regular": 400, "medium": 500,
    "semibold": 600, "demibold": 600, "bold": 700, "bolder": 700,
    "extrabold": 800, "ultrabold": 800, "black": 900, "heavy": 900,
}


def parse_font_weight(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    s = str(text).strip().lower()
    if s in _FONT_WEIGHT_KEYWORDS:
        return _FONT_WEIGHT_KEYWORDS[s]
    try:
        v = int(float(s))
        return max(1, min(1000, v))
    except ValueError:
        return None


_FONT_SIZE_KEYWORDS: Dict[str, float] = {
    "xx-small": 9.0, "x-small": 10.0, "small": 13.0, "medium": 16.0,
    "large": 18.0, "x-large": 24.0, "xx-large": 32.0, "xxx-large": 48.0,
}


def parse_font_size(text: Optional[str], parent_size: float = 16.0,
                    root_size: float = 16.0) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().lower()
    if not s:
        return None
    if s in ("inherit", "initial", "unset", "revert"):
        return None
    if s == "smaller":
        return max(1.0, parent_size / 1.2)
    if s == "larger":
        return parent_size * 1.2
    if s in _FONT_SIZE_KEYWORDS:
        return _FONT_SIZE_KEYWORDS[s]
    return parse_length(s, None, font_size=parent_size, root_font_size=root_size)


def parse_line_height(text: Optional[str], font_size: float) -> Optional[float]:
    """`line-height` is unitless (multiplier), a length, or 'normal'."""
    if text is None:
        return None
    s = str(text).strip().lower()
    if not s or s in ("normal", "inherit", "initial", "unset"):
        return None
    if s.endswith("%"):
        try:
            return font_size * float(s[:-1]) / 100.0
        except ValueError:
            return None
    if re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)", s):
        try:
            return font_size * float(s)
        except ValueError:
            return None
    return parse_length(s, None, font_size=font_size)


# ---------------------------------------------------------------------------
# Shorthand expansion
# ---------------------------------------------------------------------------

_SIDES = ("top", "right", "bottom", "left")

_BORDER_STYLES = {
    "none", "hidden", "dotted", "dashed", "solid", "double",
    "groove", "ridge", "inset", "outset",
}


def _split_tokens(value: str) -> list:
    """Split on whitespace but keep parenthesised groups together."""
    out, buf, depth = [], [], 0
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch.isspace() and depth == 0:
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def expand_box_shorthand(prop: str, value: str) -> Dict[str, str]:
    """`margin: 1px 2px` -> four longhands."""
    parts = _split_tokens(value)
    if not parts:
        return {}
    if len(parts) == 1:
        top = right = bottom = left = parts[0]
    elif len(parts) == 2:
        top = bottom = parts[0]
        right = left = parts[1]
    elif len(parts) == 3:
        top, right, bottom = parts[0], parts[1], parts[2]
        left = parts[1]
    else:
        top, right, bottom, left = parts[0], parts[1], parts[2], parts[3]
    return {
        f"{prop}-top": top, f"{prop}-right": right,
        f"{prop}-bottom": bottom, f"{prop}-left": left,
    }


def expand_border(value: str) -> Dict[str, str]:
    """`border: 1px solid #333` -> width/style/colour for all four sides."""
    width, style, color = None, None, None
    for tok in _split_tokens(value):
        low = tok.lower()
        if low in _BORDER_STYLES:
            style = low
        elif low in ("thin", "medium", "thick"):
            width = {"thin": "1px", "medium": "3px", "thick": "5px"}[low]
        elif parse_length(low) is not None:
            width = low
        elif parse_color(low) is not None:
            color = low
        elif low.startswith("var("):
            continue
    out: Dict[str, str] = {}
    for side in _SIDES:
        if width is not None:
            out[f"border-{side}-width"] = width
        if style is not None:
            out[f"border-{side}-style"] = style
        if color is not None:
            out[f"border-{side}-color"] = color
    return out


def expand_border_radius(value: str) -> Dict[str, str]:
    parts = _split_tokens(value)
    if not parts:
        return {}
    if len(parts) == 1:
        tl = tr = br = bl = parts[0]
    elif len(parts) == 2:
        tl = br = parts[0]
        tr = bl = parts[1]
    elif len(parts) == 3:
        tl, tr, br = parts[0], parts[1], parts[2]
        bl = parts[1]
    else:
        tl, tr, br, bl = parts[0], parts[1], parts[2], parts[3]
    # "10px / 20px" elliptical radii: keep the horizontal component only.
    return {
        "border-top-left-radius": tl.split("/")[0].strip(),
        "border-top-right-radius": tr.split("/")[0].strip(),
        "border-bottom-right-radius": br.split("/")[0].strip(),
        "border-bottom-left-radius": bl.split("/")[0].strip(),
    }


_FONT_SIZE_KEYWORDS = {
    "xx-small": 9.0, "x-small": 10.0, "small": 13.0, "medium": 16.0,
    "large": 18.0, "x-large": 24.0, "xx-large": 32.0, "xxx-large": 48.0,
}


_FONT_SIZE_TOKEN_RE = re.compile(
    r"^(?:%(kw)s|smaller|larger|[-+]?\d*\.?\d+(?:px|pt|pc|in|cm|mm|q|em|rem|ex|ch|vw|vh|vmin|vmax|%%)?)"
    r"(?:/[^\s]+)?$" % {"kw": "|".join(sorted(_FONT_SIZE_KEYWORDS, key=len, reverse=True))},
    re.I,
)


def expand_font(value: str) -> Dict[str, str]:
    """
    Expand the `font` shorthand.

    Accepts what browsers accept in practice:

        font: [style] [variant] [weight] size[/line-height] family-list
        font: bold 24px/1.2 Georgia, serif
        font: 16px/1.6 system-ui, sans-serif
        font: italic small-caps 12px "Times New Roman", serif
    """
    raw = value.strip()
    if not raw:
        return {}
    lowered = raw.lower()
    if lowered in ("caption", "icon", "menu", "message-box", "small-caption",
                   "status-bar"):
        return {"font-size": "13px", "font-family": "sans-serif"}
    if lowered in ("inherit", "initial", "unset"):
        return {"font": raw}

    out: Dict[str, str] = {}
    size_match = None
    prefix_tokens: List[str] = []
    for match in re.finditer(r"\S+", raw):
        token = match.group(0).rstrip(",")
        if _FONT_SIZE_TOKEN_RE.match(token):
            size_match = (match, token)
            break
        prefix_tokens.append(token.lower())
    if size_match is None:
        return {}

    match, token = size_match
    if "/" in token:
        size_value, line_height = token.split("/", 1)
        out["line-height"] = line_height
    else:
        size_value = token
    out["font-size"] = _FONT_SIZE_KEYWORDS.get(size_value.lower(), size_value)

    family = raw[match.end():].strip()
    family = family.lstrip(",").strip()
    if not family:
        return {}
    out["font-family"] = family

    for token in prefix_tokens:
        if token in ("italic", "oblique"):
            out["font-style"] = token
        elif token == "normal":
            out.setdefault("font-style", "normal")
        elif token in ("bold", "bolder", "lighter"):
            out["font-weight"] = token
        elif token.isdigit() and len(token) == 3:
            out["font-weight"] = token
        elif token == "small-caps":
            out["font-variant"] = token
    return out


def expand_background(value: str) -> Dict[str, str]:
    """Expand `background` to the longhands this engine paints."""
    out: Dict[str, str] = {}
    text = value.strip()
    if not text:
        return {}
    lowered = text.lower()
    if lowered in ("none", "inherit", "initial", "unset"):
        return {"background-color": "transparent"} if lowered == "none" else {"background": text}

    tokens = _split_top_level_commas_aware(text)
    for token in tokens:
        low = token.lower()
        if low.startswith("url(") or low.startswith(("linear-gradient", "radial-gradient")):
            out["background-image"] = token
            continue
        if low in ("repeat", "no-repeat", "repeat-x", "repeat-y", "scroll", "fixed",
                   "left", "right", "top", "bottom", "center", "cover", "contain"):
            continue
        if parse_color(token) is not None:
            out["background-color"] = token
    return out


def _split_top_level_commas_aware(text: str) -> List[str]:
    """Split on whitespace, but keep `rgb(1, 2, 3)` and `url(a b)` in one piece."""
    tokens: List[str] = []
    buf = ""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch.isspace() and depth == 0:
            if buf:
                tokens.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def expand_text_decoration(value: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for token in value.split():
        low = token.lower()
        if low in ("none", "underline", "overline", "line-through", "blink"):
            out["text-decoration-line"] = low if "text-decoration-line" not in out                 else out["text-decoration-line"] + " " + low
        elif low in ("solid", "double", "dotted", "dashed", "wavy"):
            out["text-decoration-style"] = low
        elif parse_color(token) is not None:
            out["text-decoration-color"] = token
    return out


def expand_list_style(value: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for token in value.split():
        low = token.lower()
        if low in ("inside", "outside"):
            out["list-style-position"] = low
        elif low == "none":
            out["list-style-type"] = "none"
        elif low.startswith("url("):
            out["list-style-image"] = token
        else:
            out["list-style-type"] = low
    return out


_SHORTHANDS = {
    "font": expand_font,
    "background": expand_background,
    "text-decoration": expand_text_decoration,
    "list-style": expand_list_style,
    "margin": lambda v: expand_box_shorthand("margin", v),
    "padding": lambda v: expand_box_shorthand("padding", v),
    "border-width": lambda v: expand_box_shorthand("border-width", v),
    "border-style": lambda v: expand_box_shorthand("border-style", v),
    "border-color": lambda v: expand_box_shorthand("border-color", v),
    "border-radius": expand_border_radius,
    "border": expand_border,
    "border-top": lambda v: _one_side("top", v),
    "border-right": lambda v: _one_side("right", v),
    "border-bottom": lambda v: _one_side("bottom", v),
    "border-left": lambda v: _one_side("left", v),
}

# Longhands that must reset when their shorthand is re-declared.
_SHORTHAND_RESETS = {
    "border": [f"border-{s}-{p}" for s in _SIDES for p in ("width", "style", "color")],
    "margin": [f"margin-{s}" for s in _SIDES],
    "padding": [f"padding-{s}" for s in _SIDES],
    "border-width": [f"border-{s}-width" for s in _SIDES],
    "border-style": [f"border-{s}-style" for s in _SIDES],
    "border-color": [f"border-{s}-color" for s in _SIDES],
    "border-radius": [
        "border-top-left-radius", "border-top-right-radius",
        "border-bottom-right-radius", "border-bottom-left-radius",
    ],
    "font": ["font-style", "font-variant", "font-weight", "font-size",
             "line-height", "font-family"],
    "background": ["background-color", "background-image"],
    "text-decoration": ["text-decoration-line", "text-decoration-style",
                        "text-decoration-color"],
    "list-style": ["list-style-type", "list-style-position", "list-style-image"],
}


def _one_side(side: str, value: str) -> Dict[str, str]:
    width, style, color = None, None, None
    for tok in _split_tokens(value):
        low = tok.lower()
        if low in _BORDER_STYLES:
            style = low
        elif low in ("thin", "medium", "thick"):
            width = {"thin": "1px", "medium": "3px", "thick": "5px"}[low]
        elif parse_length(low) is not None:
            width = low
        elif parse_color(low) is not None:
            color = low
    out = {}
    if width:
        out[f"border-{side}-width"] = width
    if style:
        out[f"border-{side}-style"] = style
    if color:
        out[f"border-{side}-color"] = color
    return out


def expand_declaration(prop: str, value: str) -> Dict[str, str]:
    """Expand one declaration into longhands (or return it unchanged)."""
    prop = prop.strip().lower()
    value = value.strip()
    fn = _SHORTHANDS.get(prop)
    if fn is None:
        return {prop: value}
    try:
        out = fn(value)
    except Exception:
        return {}
    if not out:
        # An unparseable shorthand still resets its longhands (CSS spec).
        return {longhand: "initial" for longhand in _SHORTHAND_RESETS.get(prop, [])}
    return out


# Properties that inherit from parent to child.
INHERITED_PROPERTIES = frozenset({
    "color", "font-family", "font-size", "font-style", "font-variant",
    "font-weight", "line-height", "letter-spacing", "word-spacing",
    "text-align", "text-indent", "text-transform", "white-space",
    "visibility", "cursor", "list-style-type", "list-style-position",
    "border-collapse", "border-spacing", "direction", "quotes",
    "caption-side", "empty-cells", "orphans", "widows", "tab-size",
})

# Properties that accept `inherit` explicitly with a different meaning.
INITIAL_FONT_SIZE = 16.0
