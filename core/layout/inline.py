"""
core/layout/inline.py — inline formatting context.

Turns a run of mixed inline content (text, `<b>`, `<a>`, images, inputs,
`inline-block` boxes) into line boxes with:

  * CSS whitespace collapsing (and `pre` / `pre-wrap` / `pre-line` honouring)
  * greedy line breaking at spaces, with hard breaks for over-long words
  * baseline alignment across mixed font sizes
  * text-align (left / right / centre / justify) and text-indent

Everything here is iterative; no recursion over user content.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..dom.tree import HTMLNode
from .box import LayoutBox, InlineItem, Line, resolve_length
from .text import FontSpec, TextMeasurer

__all__ = ["layout_inline", "intrinsic_widths", "collect_inline_items"]

_WS_RE = re.compile(r"[ \t\n\r\f]+")
_TAB_RE = re.compile(r"\t")
_CHUNK_RE = re.compile(r"[^ ]+| +")

DEFAULT_IMAGE_W = 120.0
DEFAULT_IMAGE_H = 80.0


def _is_pre(white_space: str) -> bool:
    return white_space in ("pre", "pre-wrap", "break-spaces")


def _no_wrap(white_space: str) -> bool:
    return white_space in ("pre", "nowrap")


# ---------------------------------------------------------------------------
# Token collection
# ---------------------------------------------------------------------------

def collect_inline_items(
    box: LayoutBox,
    measurer: TextMeasurer,
    available_width: float,
) -> List[InlineItem]:
    """Flatten the inline subtree into a token stream, in document order."""
    items: List[InlineItem] = []
    base_ws = str(box.style.get("white-space", "normal"))
    stack: List[Tuple[LayoutBox, str]] = [
        (child, base_ws) for child in reversed(box.children)
    ]

    while stack:
        child, parent_ws = stack.pop()
        if isinstance(child, _GapMarker):
            items.append(InlineItem("gap", style=child.box.style,
                                    width=child.width, height=0.0,
                                    line_height=0.0))
            continue
        ws_mode = str(child.style.get("white-space", parent_ws)) or parent_ws

        if child.display == "text":
            _append_text(items, child, measurer, ws_mode)
            continue

        if child.node.tag == "br":
            items.append(InlineItem("br", node=child.node, style=child.style))
            continue

        if child.is_replaced() or child.display in ("inline-block", "table", "inline-table"):
            items.append(_atomic_item(child, measurer, available_width))
            continue

        # Inline container: honour its own horizontal margin/padding/border,
        # then splice its children in at this position. Without the gaps,
        # `a { margin: 0 9px }` renders links flush against each other.
        left_gap = (_len(child.style.get("margin-left"), 0.0)
                    + _len(child.style.get("padding-left"), 0.0)
                    + _len(child.style.get("border-left-width"), 0.0))
        right_gap = (_len(child.style.get("margin-right"), 0.0)
                     + _len(child.style.get("padding-right"), 0.0)
                     + _len(child.style.get("border-right-width"), 0.0))
        if left_gap > 0.05:
            items.append(InlineItem("gap", style=child.style, width=left_gap,
                                    height=0.0, line_height=0.0))
        # Push order matters: LIFO means the marker is pushed first so that it
        # pops *after* the children, and the children keep document order.
        if right_gap > 0.05:
            stack.append((_GapMarker(child, right_gap), ws_mode))
        for grandchild in reversed(child.children):
            stack.append((grandchild, ws_mode))

    return items


class _GapMarker:
    """Placeholder pushed on the traversal stack to emit a trailing gap."""

    __slots__ = ("box", "width")

    def __init__(self, box: LayoutBox, width: float) -> None:
        self.box = box
        self.width = width


def _append_text(items: List[InlineItem], box: LayoutBox, measurer: TextMeasurer,
                 white_space: str) -> None:
    raw = box.text or ""
    if not raw:
        return
    style = box.style
    spec = measurer.spec_from_style(style)
    ascent, descent, _natural = measurer.metrics(spec)
    # line-height (unitless, px, em or %) sets the line box; the font's own
    # line spacing is only the floor.
    linespace = max(ascent + descent, measurer.line_height_for(style, spec))

    def emit(chunk: str) -> None:
        _push_chunk(items, chunk, style, spec, measurer, ascent, descent, linespace,
                    box.node)

    if _is_pre(white_space):
        text = _TAB_RE.sub("    ", raw)
        if white_space == "pre":
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        for idx, part in enumerate(text.split("\n")):
            if idx > 0:
                items.append(InlineItem("br", style=style))
            for chunk in _CHUNK_RE.findall(part):
                emit(chunk)
        return

    if white_space == "pre-line":
        for idx, part in enumerate(_TAB_RE.sub(" ", raw).split("\n")):
            if idx > 0:
                items.append(InlineItem("br", style=style))
            for chunk in _CHUNK_RE.findall(_WS_RE.sub(" ", part)):
                emit(chunk)
        return

    for chunk in _CHUNK_RE.findall(_WS_RE.sub(" ", raw)):
        emit(chunk)


def _push_chunk(items, chunk, style, spec, measurer, ascent, descent, linespace,
                node=None) -> None:
    if not chunk:
        return
    if chunk[0] == " ":
        items.append(InlineItem(
            "space", style=style, text=" ", node=node,
            width=measurer.measure(" ", spec),
            height=linespace, ascent=ascent, descent=descent,
            line_height=linespace, is_space=True,
        ))
        return
    items.append(InlineItem(
        "word", style=style, text=chunk, node=node,
        width=measurer.measure(chunk, spec),
        height=ascent + descent, ascent=ascent, descent=descent,
        line_height=linespace,
    ))


def _atomic_item(box: LayoutBox, measurer: TextMeasurer, available: float) -> InlineItem:
    """A replaced element or inline-block: sized from its own content."""
    style = box.style
    tag = box.node.tag

    # Resolve margins so spacing between widgets is honoured.
    mt = _len(style.get("margin-top"), 0.0)
    mr = _len(style.get("margin-right"), 0.0)
    mb = _len(style.get("margin-bottom"), 0.0)
    ml = _len(style.get("margin-left"), 0.0)

    if tag in ("img", "input", "button", "select", "textarea", "meter", "progress"):
        width, height = _replaced_size(box, measurer, available)
        item = InlineItem("image" if tag == "img" else "widget",
                          node=box.node, style=style, width=width + ml + mr,
                          height=height + mt + mb)
        item.ascent = item.height
        item.descent = 0.0
        item.line_height = item.height
        item.extra = (box, ml, mt)
        box.width = width
        box.height = height
        return item

    # Inline-block / table: shrink to fit, then lay out for real.
    inner_min, inner_max = intrinsic_widths(box, measurer)
    explicit = resolve_length(style.get("width"), available)
    if explicit is not None:
        width = explicit
    else:
        width = min(inner_max, max(available, inner_min))
        width = max(width, inner_min)
    width = max(1.0, min(width, max(available, inner_min)))

    from .block import compute_layout  # local import: avoids a cycle
    # Pass the border-box width: compute_layout removes padding/border itself.
    border_box_w = (width + _len(style.get("padding-left"), 0.0)
                    + _len(style.get("padding-right"), 0.0)
                    + _len(style.get("border-left-width"), 0.0)
                    + _len(style.get("border-right-width"), 0.0))
    compute_layout(box, 0.0, 0.0, max(1.0, border_box_w), measurer)

    item = InlineItem("inline-block", node=box.node, style=style,
                      width=box.width + ml + mr, height=box.height + mt + mb)
    item.ascent = item.height
    item.descent = 0.0
    item.line_height = item.height
    item.extra = (box, ml, mt)
    return item


def _replaced_size(box: LayoutBox, measurer: TextMeasurer,
                   available: float) -> Tuple[float, float]:
    style = box.style
    tag = box.node.tag
    attrs = box.node.attributes
    width = resolve_length(style.get("width"), available)
    height = resolve_length(style.get("height"), available)

    if tag == "img":
        nat_w = box.replaced_width or _num(attrs.get("width"))
        nat_h = box.replaced_height or _num(attrs.get("height"))
        if width is None:
            width = nat_w or DEFAULT_IMAGE_W
        if height is None:
            height = nat_h or DEFAULT_IMAGE_H
        if nat_w and nat_h:
            if style.get("width") is not None and style.get("height") is None:
                height = width * nat_h / nat_w
            elif style.get("height") is not None and style.get("width") is None:
                width = height * nat_w / nat_h
        return max(0.0, width), max(0.0, height)

    if tag == "input":
        itype = (attrs.get("type") or "text").lower()
        if itype == "hidden":
            return 0.0, 0.0
        if itype in ("checkbox", "radio"):
            return width or 13.0, height or 13.0
        if itype in ("submit", "button", "reset"):
            label = attrs.get("value") or itype.title()
            spec = measurer.spec_from_style(style)
            return width or max(60.0, measurer.measure(label, spec) + 18), height or 24.0
        return width or 170.0, height or 22.0

    if tag == "button":
        label = "".join(c.text for c in box.node.children if c.tag == "text").strip() or "Submit"
        spec = measurer.spec_from_style(style)
        return width or max(70.0, measurer.measure(label, spec) + 22), height or 26.0

    if tag == "select":
        return width or 150.0, height or 22.0

    if tag == "textarea":
        cols = _num(attrs.get("cols")) or 28
        rows = _num(attrs.get("rows")) or 4
        spec = measurer.spec_from_style(style)
        char_w = measurer.measure("M", spec) or 8.0
        _, _, linespace = measurer.metrics(spec)
        return width or max(120.0, cols * char_w + 8), height or max(40.0, rows * linespace + 8)

    return width or 160.0, height or 24.0


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace("px", "").strip())
    except (TypeError, ValueError):
        return None


def _len(value, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)  # already computed to px by the cascade
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Line breaking
# ---------------------------------------------------------------------------

def _split_long_item(item: InlineItem, max_width: float,
                     measurer: TextMeasurer) -> List[InlineItem]:
    if item.kind != "word" or item.width <= max_width or max_width <= 1:
        return [item]
    spec = measurer.spec_from_style(item.style)
    chunks: List[InlineItem] = []
    current = ""
    for ch in item.text:
        candidate = current + ch
        if current and measurer.measure(candidate, spec) > max_width:
            chunks.append(_clone_word(item, current, measurer, spec))
            current = ch
        else:
            current = candidate
    if current:
        chunks.append(_clone_word(item, current, measurer, spec))
    return chunks


def _clone_word(item: InlineItem, text: str, measurer: TextMeasurer,
                spec: FontSpec) -> InlineItem:
    return InlineItem(
        "word", node=item.node, style=item.style, text=text,
        width=measurer.measure(text, spec),
        height=item.height, ascent=item.ascent, descent=item.descent,
        line_height=item.line_height,
    )


def _flush_line(line_items: List[InlineItem], y: float, content_x: float,
                available: float, align: str, indent: float) -> Line:
    while line_items and line_items[-1].is_space:
        line_items.pop()

    line = Line()
    line.items = line_items
    line.y = y
    if not line_items:
        line.height = 0.0
        return line

    max_ascent = max(i.ascent for i in line_items)
    max_descent = max(i.descent for i in line_items)
    min_height = max(i.line_height for i in line_items)
    height = max(max_ascent + max_descent, min_height)
    line.ascent = max_ascent
    line.height = height

    width = sum(i.width for i in line_items)
    line.width = width

    start_x = content_x + indent
    space = available - indent - width
    if align == "center":
        start_x += space / 2.0
    elif align in ("right", "end"):
        start_x += space

    baseline = y + (height - (max_ascent + max_descent)) / 2.0 + max_ascent
    x = start_x
    for item in line_items:
        item.x = x
        item.y = baseline - item.ascent
        x += item.width
    line.x = start_x
    line.text = "".join(i.text for i in line_items if not i.is_space)
    return line


def _justify(line: Line, available: float) -> None:
    spaces = [i for i in line.items if i.is_space]
    if not spaces:
        return
    extra = available - line.width
    if extra <= 0:
        return
    add = extra / len(spaces)
    x = line.items[0].x
    for item in line.items:
        item.x = x
        x += item.width + (add if item.is_space else 0.0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def layout_inline(box: LayoutBox, measurer: TextMeasurer, available_width: float) -> float:
    """Lay out `box`'s inline content; fills ``box.lines`` and returns height."""
    available = max(1.0, available_width)
    items = collect_inline_items(box, measurer, available)
    if not items:
        box.lines = []
        return 0.0

    style = box.style
    white_space = str(style.get("white-space", "normal"))
    align = str(style.get("text-align", "left"))
    indent = _len(style.get("text-indent"), 0.0)
    nowrap = _no_wrap(white_space)

    content_x = box.content_x
    y = box.content_y
    lines: List[Line] = []
    current: List[InlineItem] = []
    current_width = 0.0
    first_line = True

    def flush() -> None:
        nonlocal current, current_width, y, first_line
        line_indent = indent if first_line else 0.0
        line = _flush_line(current, y, content_x, available, align, line_indent)
        lines.append(line)
        y += line.height
        first_line = False
        current = []
        current_width = 0.0

    for item in items:
        if item.kind == "br":
            if not current:
                spec = measurer.spec_from_style(style)
                ascent, descent, _ = measurer.metrics(spec)
                current.append(InlineItem(
                    "word", style=style, text="", width=0.0,
                    height=ascent + descent, ascent=ascent,
                    descent=descent, line_height=ascent + descent,
                ))
            flush()
            continue

        if item.is_space:
            if not current:
                continue
            current.append(item)
            current_width += item.width
            continue

        lead = indent if first_line else 0.0
        if current and not nowrap and current_width + lead + item.width > available:
            flush()

        if not current and not nowrap and item.width > available - lead:
            pieces = _split_long_item(item, available - lead, measurer)
            for piece in pieces[:-1]:
                current = [piece]
                current_width = piece.width
                flush()
            if pieces:
                current = [pieces[-1]]
                current_width = pieces[-1].width
            continue

        current.append(item)
        current_width += item.width

    if current:
        flush()

    if align == "justify" and len(lines) > 1:
        for line in lines[:-1]:
            _justify(line, available)

    box.lines = lines
    return sum(line.height for line in lines)


# ---------------------------------------------------------------------------
# Intrinsic widths
# ---------------------------------------------------------------------------

def intrinsic_widths(box: LayoutBox, measurer: TextMeasurer) -> Tuple[float, float]:
    """(min-content, max-content) width of a box, computed bottom-up."""
    order: List[LayoutBox] = []
    stack: List[LayoutBox] = [box]
    while stack:
        node = stack.pop()
        order.append(node)
        stack.extend(node.children)

    for node in reversed(order):
        if node.display == "text":
            spec = measurer.spec_from_style(node.style)
            text = _WS_RE.sub(" ", node.text or "")
            words = [w for w in text.split(" ") if w]
            node.intrinsic_min_width = max((measurer.measure(w, spec) for w in words), default=0.0)
            node.intrinsic_max_width = measurer.measure(text.strip(), spec)
            continue

        explicit = resolve_length(node.style.get("width"), 0.0)
        if explicit is not None:
            node.intrinsic_min_width = node.intrinsic_max_width = float(explicit)
            continue

        min_w = 0.0
        max_w = 0.0
        inline_run = 0.0
        saw_inline = False

        for child in node.children:
            if child.display == "text" or not child.is_block_level():
                saw_inline = True
                min_w = max(min_w, child.intrinsic_min_width)
                inline_run += child.intrinsic_max_width
            else:
                min_w = max(min_w, child.intrinsic_min_width)
                max_w = max(max_w, child.intrinsic_max_width)

        node.intrinsic_min_width = max(min_w, node.intrinsic_min_width)
        node.intrinsic_max_width = max(max_w, inline_run if saw_inline else 0.0)

    return box.intrinsic_min_width, box.intrinsic_max_width
