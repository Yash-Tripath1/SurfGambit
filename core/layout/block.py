"""
core/layout/block.py — block formatting context and the layout entry point.

Rules implemented:

  * the full box model per side (margin / border / padding), including
    multi-value shorthands, percentages and `margin: auto` centring
  * `box-sizing: border-box`
  * `min-width` / `max-width` clamping
  * block children stack vertically with **collapsing margins**
  * mixed inline + block children become anonymous block boxes
    (`<div>text<p>para</p>more</div>` lays out correctly)
  * `display: list-item` gets a marker positioned in the parent's padding
"""

from __future__ import annotations

from typing import List, Optional

from ..dom.tree import HTMLNode
from ..values import Length
from .box import LayoutBox, resolve_length
from .inline import layout_inline
from .text import TextMeasurer

__all__ = ["compute_layout", "read_box_model"]

_EPSILON = 0.01


def _px(value, base: float, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str):
        return default                      # "auto" and friends
    if isinstance(value, Length):
        return value.resolve(base)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_auto(value) -> bool:
    return isinstance(value, str) and value.strip().lower() == "auto"


def read_box_model(box: LayoutBox, available_width: float) -> None:
    """Fill margin/border/padding from the computed style (px floats)."""
    style = box.style
    base = available_width

    box.margin_top = _px(style.get("margin-top"), base)
    box.margin_right = _px(style.get("margin-right"), base)
    box.margin_bottom = _px(style.get("margin-bottom"), base)
    box.margin_left = _px(style.get("margin-left"), base)

    box.padding_top = _px(style.get("padding-top"), base)
    box.padding_right = _px(style.get("padding-right"), base)
    box.padding_bottom = _px(style.get("padding-bottom"), base)
    box.padding_left = _px(style.get("padding-left"), base)

    box.border_top = _px(style.get("border-top-width"), base)
    box.border_right = _px(style.get("border-right-width"), base)
    box.border_bottom = _px(style.get("border-bottom-width"), base)
    box.border_left = _px(style.get("border-left-width"), base)


def _resolve_content_width(box: LayoutBox, available_width: float,
                           shrink_to_fit: bool) -> float:
    style = box.style
    explicit = resolve_length(style.get("width"), available_width)

    horizontal_extras = (box.margin_left + box.margin_right
                         + box.border_left + box.border_right
                         + box.padding_left + box.padding_right)

    if explicit is None:
        if shrink_to_fit:
            content = available_width - box.padding_left - box.padding_right \
                - box.border_left - box.border_right
        else:
            content = available_width - horizontal_extras
    else:
        content = explicit
        if style.get("box-sizing") == "border-box":
            content -= (box.padding_left + box.padding_right
                        + box.border_left + box.border_right)

    min_w = resolve_length(style.get("min-width"), available_width)
    max_w = resolve_length(style.get("max-width"), available_width)
    if min_w is not None:
        content = max(content, min_w)
    if max_w is not None:
        content = min(content, max_w)

    return max(0.0, content)


# ---------------------------------------------------------------------------
# Anonymous inline runs
# ---------------------------------------------------------------------------

def _layout_run(children: List[LayoutBox], parent: LayoutBox, y: float,
                measurer: TextMeasurer) -> float:
    """Lay out a run of inline-level siblings at `y`; append the lines."""
    if not children:
        return 0.0
    shim = LayoutBox(parent.node, parent.style)
    shim.display = "block"
    shim.children = children
    shim.x = parent.content_x
    shim.y = y
    shim.width = parent.content_width
    shim.padding_left = shim.padding_right = 0.0
    shim.padding_top = shim.padding_bottom = 0.0
    shim.border_left = shim.border_right = 0.0
    shim.border_top = shim.border_bottom = 0.0
    height = layout_inline(shim, measurer, parent.content_width)
    parent.lines.extend(shim.lines)
    return height


# ---------------------------------------------------------------------------
# Block layout
# ---------------------------------------------------------------------------

def _layout_block(box: LayoutBox, x: float, y: float, available_width: float,
                  measurer: TextMeasurer) -> None:
    read_box_model(box, available_width)
    style = box.style

    # `margin: auto` centring for fixed-width blocks.
    explicit = resolve_length(style.get("width"), available_width)
    auto_left = _is_auto(style.get("margin-left"))
    auto_right = _is_auto(style.get("margin-right"))
    if auto_left:
        box.margin_left = 0.0
    if auto_right:
        box.margin_right = 0.0

    content_width = _resolve_content_width(box, available_width,
                                           shrink_to_fit=False)
    border_box_width = (content_width + box.padding_left + box.padding_right
                        + box.border_left + box.border_right)

    if auto_left or auto_right:
        free = available_width - box.margin_right - box.margin_left - border_box_width
        if auto_left and auto_right:
            box.margin_left = box.margin_right = max(0.0, free / 2.0)
        elif auto_left:
            box.margin_left = max(0.0, free)
        else:
            box.margin_right = max(0.0, free)

    box.x = x + box.margin_left
    box.y = y
    box.width = border_box_width

    content_x = box.content_x
    cursor = box.content_y

    children = box.children
    has_block_child = any(c.is_block_level() for c in children)

    if not has_block_child:
        content_height = layout_inline(box, measurer, content_width)
    else:
        run: List[LayoutBox] = []
        previous_margin = 0.0
        first_block = True

        for child in children:
            if not child.is_block_level():
                run.append(child)
                continue

            if run:
                run_height = _layout_run(run, box, cursor, measurer)
                if run_height > 0:
                    cursor += run_height
                run = []

            read_box_model(child, content_width)
            gap = child.margin_top if first_block else max(previous_margin, child.margin_top)
            child_top = cursor + gap
            compute_layout(child, content_x, child_top, content_width, measurer)
            cursor = child.y + child.height
            previous_margin = child.margin_bottom
            first_block = False

        if run:
            run_height = _layout_run(run, box, cursor, measurer)
            if run_height > 0:
                cursor += run_height

        if not first_block:
            cursor += previous_margin

        content_height = max(0.0, cursor - box.content_y)

    # Resolve explicit / minimum heights.
    explicit_height = resolve_length(style.get("height"), available_width)
    min_height = resolve_length(style.get("min-height"), available_width)
    max_height = resolve_length(style.get("max-height"), available_width)

    if explicit_height is not None:
        content_height = explicit_height
        if style.get("box-sizing") == "border-box":
            content_height -= (box.padding_top + box.padding_bottom
                               + box.border_top + box.border_bottom)
    if min_height is not None:
        content_height = max(content_height, min_height)
    if max_height is not None:
        content_height = min(content_height, max_height)

    box.height = (max(0.0, content_height) + box.padding_top + box.padding_bottom
                  + box.border_top + box.border_bottom)


def _geometry_is_sane(box: LayoutBox, slack: float = 2.0) -> bool:
    """
    Cheap post-layout sanity check for the advanced formatting contexts.

    Real pages mix flex/grid with features this engine does not implement
    (`position: absolute`, `float`, container queries, `@supports` gating the
    layout it actually uses). When the result would be visibly broken —
    children escaping the container, siblings overlapping, zero-width boxes
    that contain content — the container is re-laid-out as plain block flow,
    which always produces a readable (if simpler) page.
    """
    left = box.content_x - slack
    right = box.content_x + box.content_width + slack
    spans = []
    for child in box.children:
        if child.display == "none":
            continue
        if child.display == "text":
            continue
        if child.width <= 0.5 and (child.children or child.text.strip()):
            return False
        if child.height < 0 or child.width < 0:
            return False
        if child.x < left - 40 or child.x + child.width > right + 40:
            return False
        spans.append((child.x, child.y, child.width, child.height))

    # Overlapping siblings mean the placement algorithm was defeated.
    for i in range(len(spans)):
        ax, ay, aw, ah = spans[i]
        if aw <= 0 or ah <= 0:
            continue
        for j in range(i + 1, len(spans)):
            bx, by, bw, bh = spans[j]
            if bw <= 0 or bh <= 0:
                continue
            if (ax < bx + bw and bx < ax + aw and ay < by + bh - 4 and by < ay + ah - 4):
                return False
    return True


def _fall_back_to_block(box: LayoutBox, x: float, y: float,
                        available_width: float, measurer: TextMeasurer) -> None:
    box.display = "block"
    for child in box.children:
        if child.display in ("grid", "inline-grid", "flex", "inline-flex"):
            child.display = "block"
    _layout_block(box, x, y, available_width, measurer)


def _layout_flex_container(box: LayoutBox, x: float, y: float,
                           available_width: float, measurer: TextMeasurer) -> None:
    """Set up the flex container's own box, then lay out its items."""
    from .flex import layout_flex

    if box.display == "inline-flex":
        read_box_model(box, available_width)
        from .inline import intrinsic_widths
        try:
            min_w, max_w = intrinsic_widths(box, measurer)
        except Exception:
            min_w, max_w = 0.0, available_width
        box.x = x + box.margin_left
        box.y = y
        box.width = max(1.0, min(max_w, available_width)) + (
            box.padding_left + box.padding_right + box.border_left + box.border_right)
        box.height = 0.0
        content_height = layout_flex(box, measurer, box.content_width)
    else:
        read_box_model(box, available_width)
        explicit_width = resolve_length(box.style.get("width"), available_width)
        if explicit_width is not None:
            width = explicit_width
            if box.style.get("box-sizing") != "border-box":
                width += (box.padding_left + box.padding_right
                          + box.border_left + box.border_right)
        else:
            width = max(0.0, available_width - box.margin_left - box.margin_right)
        box.x = x + box.margin_left
        box.y = y
        box.width = width
        box.height = 0.0
        content_height = layout_flex(box, measurer, box.content_width)

    explicit_height = resolve_length(box.style.get("height"), available_width)
    if explicit_height is not None:
        content_height = explicit_height
    min_height = resolve_length(box.style.get("min-height"), available_width)
    if min_height is not None:
        content_height = max(content_height, min_height)

    box.height = (max(0.0, content_height) + box.padding_top + box.padding_bottom
                  + box.border_top + box.border_bottom)

    if not _geometry_is_sane(box):
        _fall_back_to_block(box, x, y, available_width, measurer)


def _layout_grid_container(box: LayoutBox, x: float, y: float,
                           available_width: float, measurer: TextMeasurer) -> None:
    from .grid import layout_grid

    read_box_model(box, available_width)
    explicit_width = resolve_length(box.style.get("width"), available_width)
    if explicit_width is not None:
        width = explicit_width
    else:
        width = max(0.0, available_width - box.margin_left - box.margin_right)
    box.x = x + box.margin_left
    box.y = y
    box.width = width
    box.height = 0.0
    content_height = layout_grid(box, measurer, box.content_width)

    explicit_height = resolve_length(box.style.get("height"), available_width)
    if explicit_height is not None:
        content_height = explicit_height
    box.height = (max(0.0, content_height) + box.padding_top + box.padding_bottom
                  + box.border_top + box.border_bottom)

    if not _geometry_is_sane(box):
        _fall_back_to_block(box, x, y, available_width, measurer)


def _layout_inline_root(box: LayoutBox, x: float, y: float,
                        available_width: float, measurer: TextMeasurer) -> None:
    read_box_model(box, available_width)
    box.x = x + box.margin_left
    box.y = y
    box.width = max(0.0, available_width - box.margin_left - box.margin_right)
    height = layout_inline(box, measurer, box.content_width)
    box.height = height + box.padding_top + box.padding_bottom


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def compute_layout(box: LayoutBox, x: float, y: float, available_width: float,
                   measurer: TextMeasurer) -> None:
    """Lay out `box` into the rectangle whose containing block is `available_width`."""
    display = box.display

    if display in ("flex", "inline-flex"):
        _layout_flex_container(box, x, y, available_width, measurer)
        return

    if display in ("grid", "inline-grid"):
        _layout_grid_container(box, x, y, available_width, measurer)
        return

    if display == "table" or display == "inline-table":
        from .table import layout_table
        layout_table(box, measurer, available_width, x, y)
        return

    if display in ("table-row", "table-cell", "table-row-group",
                   "table-header-group", "table-footer-group", "table-caption",
                   "table-column", "table-column-group"):
        # Laid out by the table algorithm; when met standalone, fall back to block.
        box.display = "block"
        _layout_block(box, x, y, available_width, measurer)
        return

    if box.is_block_level():
        _layout_block(box, x, y, available_width, measurer)
    else:
        _layout_inline_root(box, x, y, available_width, measurer)


# ---------------------------------------------------------------------------
# List markers
# ---------------------------------------------------------------------------

_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"),
          (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"),
          (5, "v"), (4, "iv"), (1, "i")]


def _roman(n: int) -> str:
    out = []
    for value, sym in _ROMAN:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def _alpha(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(97 + rem) + out
    return out


def marker_text(list_style: str, index: int) -> str:
    if list_style in ("none", ""):
        return ""
    if list_style == "disc":
        return "\u2022"
    if list_style == "circle":
        return "\u25e6"
    if list_style == "square":
        return "\u25aa"
    if list_style == "decimal":
        return f"{index}."
    if list_style == "decimal-leading-zero":
        return f"{index:02d}."
    if list_style == "lower-alpha":
        return _alpha(index) + "."
    if list_style == "upper-alpha":
        return _alpha(index).upper() + "."
    if list_style == "lower-roman":
        return _roman(index) + "."
    if list_style == "upper-roman":
        return _roman(index).upper() + "."
    if list_style == "lower-greek":
        return chr(0x3B1 + (index - 1) % 24) + "."
    return "\u2022"


def apply_list_markers(root: LayoutBox) -> None:
    """Attach a marker string to every list-item box (iterative)."""
    stack = [root]
    while stack:
        box = stack.pop()
        stack.extend(box.children)
        if box.display != "list-item":
            continue
        parent = box.parent
        start = 1
        list_style = str(box.style.get("list-style-type", "disc"))
        if parent is not None:
            list_style = str(box.style.get("list-style-type")
                             or parent.style.get("list-style-type") or "disc")
            if parent.node.tag == "ol":
                try:
                    start = int(parent.node.attributes.get("start", "1"))
                except ValueError:
                    start = 1
            items = [c for c in parent.children if c.display == "list-item"]
            try:
                index = items.index(box) + start
            except ValueError:
                index = start
        else:
            index = 1
        box.list_marker = marker_text(list_style, index)
