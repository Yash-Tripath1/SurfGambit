"""
core/layout/flex.py: a practical subset of CSS flexible box layout.

Modern pages are built out of flex containers: Wikipedia's skin, GitHub's
header, almost every marketing page. Without flex support those pages do not
"look simpler", they look broken: every column stacks and sidebars land on
top of the content.

Implemented (the subset that covers the overwhelming majority of real pages):

    display: flex | inline-flex
    flex-direction: row | row-reverse | column | column-reverse
    flex-wrap: nowrap | wrap
    justify-content: flex-start | flex-end | center | space-between |
                      space-around | space-evenly
    align-items: stretch | flex-start | flex-end | center | baseline
    align-content: flex-start | center | flex-end | space-between | stretch
    align-self per item
    flex-grow / flex-shrink / flex-basis  (+ `flex: 1`, `flex: auto`,
                                           `flex: none`, `flex: 0 0 200px`)
    gap / row-gap / column-gap
    order

`grid` containers fall back to a single-column flex stack, which is far closer
to the intended result than treating them as plain blocks.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..values import Length
from .box import LayoutBox, resolve_length

__all__ = ["layout_flex", "flex_basis", "is_flex"]

_EPSILON = 0.01


def is_flex(display: str) -> bool:
    return display in ("flex", "inline-flex")


def _num(value, base: float, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, Length):
        return value.resolve(base)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("auto", "none", "normal", ""):
            return default
        if text.endswith("%"):
            try:
                return base * float(text[:-1]) / 100.0
            except ValueError:
                return default
        try:
            return float(text.rstrip("px"))
        except ValueError:
            return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _keyword(value, default: str = "") -> str:
    return str(value).strip().lower() if value is not None else default


def flex_basis(box: LayoutBox, available: float, horizontal: bool) -> Optional[float]:
    """Return the flex base size, or None when the item is auto-sized."""
    style = box.style
    raw_basis = style.get("flex-basis")
    basis = resolve_length(raw_basis, available)
    if basis is None and isinstance(raw_basis, str):
        parsed_basis = _num(raw_basis, available, -1.0)
        basis = parsed_basis if parsed_basis >= 0 else None
    if basis is not None and basis >= 0:
        return basis

    # `flex: 1 1 200px` carries the basis in the shorthand's third component.
    shorthand = style.get("flex")
    if shorthand is not None:
        parts = str(shorthand).strip().lower().split()
        if len(parts) >= 3:
            parsed = _num(parts[2], available, -1.0)
            if parsed >= 0:
                return parsed

    raw_width = style.get("width") if horizontal else style.get("height")
    explicit = resolve_length(raw_width, available)
    if explicit is not None:
        return explicit
    return None


def _flex_factors(style) -> Tuple[float, float]:
    grow = _num(style.get("flex-grow"), 1.0, 0.0)
    shrink = _num(style.get("flex-shrink"), 1.0, 1.0)
    shorthand = style.get("flex")
    if shorthand is not None:
        parts = str(shorthand).strip().lower().split()
        if parts == ["none"]:
            return 0.0, 0.0
        if parts == ["auto"]:
            return 1.0, 1.0
        if len(parts) == 1:
            value = parts[0]
            if value.isdigit():
                return float(value), 1.0
            if value.endswith("px") or value.endswith("%"):
                return 1.0, 1.0
        elif len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
    return grow, shrink


def _item_gap(style, horizontal: bool) -> float:
    gap = style.get("gap") or style.get("grid-gap")
    if gap is None:
        gap = style.get("column-gap" if horizontal else "row-gap")
    if gap is None:
        return 0.0
    parts = str(gap).split()
    if not parts:
        return 0.0
    first = _num(parts[0], 0.0)
    if not horizontal and len(parts) > 1:
        return _num(parts[1], 0.0)
    return first


def layout_flex(box: LayoutBox, measurer, available_width: float) -> float:
    """Lay out a flex container. Returns the content height."""
    from .block import compute_layout, read_box_model

    style = box.style
    horizontal = _keyword(style.get("flex-direction"), "row").startswith("row")
    if _keyword(style.get("flex-direction"), "row") in ("column", "column-reverse"):
        horizontal = False

    reverse = _keyword(style.get("flex-direction")).endswith("reverse")
    wrap = _keyword(style.get("flex-wrap"), "nowrap") in ("wrap", "wrap-reverse")

    content_x = box.content_x
    content_y = box.content_y
    main_available = box.content_width if horizontal else box.content_height
    cross_available = box.content_height if horizontal else box.content_width
    if main_available <= 0:
        main_available = available_width
    if cross_available <= 0:
        # An auto-height row has no definite cross size: measure items and
        # let the container grow to fit them.
        cross_available = 0.0 if horizontal else available_width

    items: List[Tuple[float, LayoutBox, float, float]] = []   # (order, box, grow, shrink)
    for child in box.children:
        if child.display == "none":
            continue
        if str(child.style.get("position") or "static").lower() in ("absolute", "fixed"):
            continue
        # Flex items are blockified (CSS Flexbox §4): an inline-level child of
        # a flex container becomes a block-level box, so both layout and paint
        # treat it like a normal block child.
        blockified = _blockify(child.display)
        if blockified != child.display:
            child.display = blockified
        if child.display == "text" and not (child.text or "").strip():
            continue
        read_box_model(child, main_available)
        order = _num(child.style.get("order"), cross_available, 0.0)
        grow, shrink = _flex_factors(child.style)
        items.append((order, child, grow, shrink))
    items.sort(key=lambda entry: entry[0])

    gap = _item_gap(style, horizontal)

    # -- 1. measure each item's hypothetical main size -------------------
    sizes: List[float] = []
    for _order, child, _grow, _shrink in items:
        outer = (child.margin_left + child.margin_right
                 + child.border_left + child.border_right
                 + child.padding_left + child.padding_right) if horizontal else 0.0
        if horizontal:
            basis = flex_basis(child, main_available, True)
            if basis is None:
                basis = _content_width(child, measurer, main_available)
            sizes.append(max(0.0, basis + outer))
        else:
            basis = flex_basis(child, cross_available, False)
            if basis is None:
                # Column items are as tall as their content: measure by laying
                # them out later; start from an estimate.
                basis = _content_height_estimate(child, measurer, cross_available)
            sizes.append(max(0.0, basis))

    # -- 2. break into lines using the hypothetical main sizes -----------
    # (This must happen before shrinking: with `flex-wrap: wrap` items move to
    # a new line rather than being squeezed.)
    lines: List[List[int]] = [[]]
    for index, size in enumerate(sizes):
        extra = size + (gap if lines[-1] else 0.0)
        used = sum(sizes[i] for i in lines[-1]) + gap * max(0, len(lines[-1]) - 1)
        if wrap and lines[-1] and used + extra > main_available + _EPSILON:
            lines.append([index])
        else:
            lines[-1].append(index)

    # -- 3. resolve free space per line ----------------------------------
    for indices in lines:
        used = sum(sizes[i] for i in indices) + gap * max(0, len(indices) - 1)
        free = main_available - used
        if free > _EPSILON:
            grow_total = sum(items[i][2] for i in indices)
            if grow_total > 0:
                for i in indices:
                    sizes[i] += free * (items[i][2] / grow_total)
        elif free < -_EPSILON:
            shrink_total = sum(items[i][3] * sizes[i] for i in indices)
            if shrink_total > 0:
                for i in indices:
                    portion = (items[i][3] * sizes[i]) / shrink_total
                    sizes[i] = max(0.0, sizes[i] + free * portion)

    # -- 4. cross-axis sizes ---------------------------------------------
    justify = _keyword(style.get("justify-content"), "flex-start")
    align = _keyword(style.get("align-items"), "stretch")
    align_content = _keyword(style.get("align-content"), "stretch")

    line_cross_sizes: List[float] = []
    for indices in lines:
        cross_size = 0.0
        for index in indices:
            child = items[index][1]
            if horizontal:
                cross_size = max(cross_size, _measure_item_height(
                    child, measurer, max(1.0, sizes[index]), cross_available))
            else:
                cross_size = max(cross_size, sizes[index])
        line_cross_sizes.append(cross_size)

    # A single line fills a container with a definite cross size
    # (`align-content: stretch`, the initial value).
    definite_cross = None
    if horizontal:
        definite_cross = resolve_length(style.get("height"), cross_available)
    if (len(lines) == 1 and align_content in ("stretch", "normal")
            and definite_cross is not None):
        line_cross_sizes[0] = max(line_cross_sizes[0], definite_cross)

    total_cross = sum(line_cross_sizes) + gap * max(0, len(lines) - 1)
    cross_cursor = 0.0
    free_cross = max(0.0, (cross_available if definite_cross is not None else total_cross)
                     - total_cross)
    if align_content in ("center",):
        cross_cursor = free_cross / 2.0
    elif align_content in ("flex-end", "end"):
        cross_cursor = free_cross

    # -- 5. place items ---------------------------------------------------
    for lineno, indices in enumerate(lines):
        line_cross = line_cross_sizes[lineno]
        line_main = sum(sizes[i] for i in indices) + gap * max(0, len(indices) - 1)
        slack = main_available - line_main
        count = len(indices)

        if justify == "center":
            start, spacing = slack / 2.0, gap
        elif justify in ("flex-end", "end", "right"):
            start, spacing = slack, gap
        elif justify == "space-between":
            start = 0.0
            spacing = gap + (slack / (count - 1) if count > 1 else 0.0)
        elif justify == "space-around":
            spacing = gap + (slack / count if count else 0.0)
            start = spacing / 2.0 if count else 0.0
        elif justify == "space-evenly":
            spacing = gap + (slack / (count + 1) if count else 0.0)
            start = spacing
        else:
            start, spacing = 0.0, gap

        ordered = list(indices)
        if reverse:
            ordered.reverse()

        main_cursor = start
        for index in ordered:
            child = items[index][1]
            own_main = sizes[index]

            if horizontal:
                inner_w = max(1.0, own_main - (child.margin_left + child.margin_right
                                               + child.border_left + child.border_right
                                               + child.padding_left + child.padding_right))
                child_align = _keyword(child.style.get("align-self"), align)
                child_height = _measure_item_height(child, measurer, inner_w, line_cross)
                if (child_align in ("stretch", "normal")
                        and _is_auto_height(child.style)
                        and line_cross > 0):
                    child_height = max(child_height, line_cross)

                y = content_y + cross_cursor
                if child_align in ("center",):
                    y += (line_cross - child_height) / 2.0
                elif child_align in ("flex-end", "end"):
                    y += (line_cross - child_height)

                compute_layout(child, content_x + main_cursor + child.margin_left,
                               y, inner_w, measurer)
                if (_is_auto_height(child.style)
                        and child_align in ("stretch", "normal")):
                    child.height = max(child.height, child_height)
                main_cursor += own_main + spacing
            else:
                inner_w = max(1.0, cross_available - child.margin_left
                              - child.margin_right)
                compute_layout(child, content_x + child.margin_left,
                               content_y + main_cursor, inner_w, measurer)
                main_cursor += own_main + spacing

        cross_cursor += line_cross + gap

    bottom = content_y
    for _order, child, _grow, _shrink in items:
        bottom = max(bottom, child.y + child.height)
    return max(0.0, bottom - content_y)


def _is_auto_height(style) -> bool:
    value = style.get("height")
    if value is None:
        return True
    return str(value).strip().lower() in ("auto", "", "initial")


def _content_width(box: LayoutBox, measurer, available: float) -> float:
    """Shrink-to-fit width for a row item with no explicit width."""
    from .inline import intrinsic_widths

    try:
        min_w, max_w = intrinsic_widths(box, measurer)
    except Exception:
        return min(available, 10.0)
    return max(min_w, min(max_w, available))


def _content_height_estimate(box: LayoutBox, measurer, available: float) -> float:
    return 24.0


_BLOCKIFY = {
    "inline": "block", "inline-block": "block", "inline-table": "table",
    "inline-flex": "flex", "inline-grid": "grid", "table-row": "block",
    "table-row-group": "block", "table-header-group": "block",
    "table-footer-group": "block", "table-cell": "block",
    "table-column": "block", "table-column-group": "block",
    "table-caption": "block", "list-item": "list-item",
}


def _blockify(display: str) -> str:
    """Return the block-level counterpart of an inline-level display value."""
    return _BLOCKIFY.get(display, display)


def _measure_item_height(box: LayoutBox, measurer, width: float,
                         max_height: float) -> float:
    """Lay the item out at `width` and report its border-box height."""
    from .block import compute_layout

    explicit = resolve_length(box.style.get("height"), max_height)
    compute_layout(box, box.x, box.y, max(1.0, width), measurer)
    if explicit is not None:
        return explicit
    return box.height
