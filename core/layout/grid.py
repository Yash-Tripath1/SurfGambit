"""
core/layout/grid.py: CSS grid, the subset real sites use.

Wikipedia's whole skin is a three-row, two-column grid; without grid support
the site renders as a single vertical pile, which is exactly the complaint
that started this rewrite. This module implements:

    display: grid | inline-grid
    grid-template-columns / grid-template-rows      (lengths, %, fr, auto,
                                                     min-content, max-content,
                                                     minmax(), repeat())
    grid-template-areas                             (named areas)
    grid-template                                  (rows / columns shorthand)
    grid-area, grid-column, grid-row               (names and 1-based lines,
                                                     `a / b` spans, `span n`)
    column-gap / row-gap / gap
    justify-items / justify-self                   (start | center | end | stretch)
    align-items / align-self
    grid-auto-flow: row (the default)

Anything outside that subset degrades to auto-placement inside the declared
tracks, which keeps pages readable instead of collapsing them.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..values import Length, parse_length
from .box import LayoutBox, resolve_length

__all__ = ["layout_grid", "is_grid", "parse_track_list"]

_NUM_RE = re.compile(r"^([-+]?\d*\.?\d+)([a-z%]*)$", re.I)


def is_grid(display: str) -> bool:
    return display in ("grid", "inline-grid")


# ---------------------------------------------------------------------------
# Track parsing
# ---------------------------------------------------------------------------

class Track:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: float = 0.0) -> None:
        self.kind = kind          # px | percent | fr | auto | min | max
        self.value = value

    def __repr__(self) -> str:
        return f"Track({self.kind},{self.value:g})"


def _split_top_level(text: str, sep: str = " ") -> List[str]:
    out: List[str] = []
    buf = ""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def _repeat_count(spec: str) -> Tuple[int, str]:
    """Expand `repeat(3, 1fr)` into (3, '1fr')."""
    body = spec[len("repeat("):-1]
    parts = _split_top_level(body, ",")
    if len(parts) != 2:
        return 1, spec
    try:
        count = int(parts[0])
    except ValueError:
        count = 1
    return max(1, min(count, 24)), parts[1]


def _one_track(token: str) -> List[Track]:
    token = token.strip()
    low = token.lower()
    if low.startswith("repeat(") and low.endswith(")"):
        count, inner = _repeat_count(token)
        tracks: List[Track] = []
        for piece in _split_top_level(inner):
            tracks.extend(_one_track(piece))
        return (tracks * count)[: len(tracks) * count]
    if low.startswith("minmax(") and low.endswith(")"):
        # The upper bound drives distribution; the lower bound only matters
        # for shrinking, which auto-sized tracks already handle.
        inner = token[len("minmax("):-1]
        parts = _split_top_level(inner, ",")
        if len(parts) == 2:
            upper = _one_track(parts[1])
            if upper:
                return upper
        return [_one_track(parts[0])][0] if parts else [Track("auto")]
    if low in ("auto", "fit-content", "max-content"):
        return [Track("auto")]
    if low == "min-content":
        return [Track("min")]
    if low.endswith("fr"):
        try:
            return [Track("fr", float(low[:-2] or 1))]
        except ValueError:
            return [Track("fr", 1.0)]
    if low.endswith("%"):
        try:
            return [Track("percent", float(low[:-1]))]
        except ValueError:
            return [Track("auto")]
    value = parse_length(low, default=None)
    if value is not None:
        return [Track("px", float(value))]
    return [Track("auto")]


def parse_track_list(value) -> List[Track]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.lower() in ("none", "auto"):
        return []
    # `grid-template: <rows> / <columns>` shorthand
    tracks: List[Track] = []
    for token in _split_top_level(text):
        tracks.extend(_one_track(token))
    return tracks


def _parse_areas(value) -> Dict[str, Tuple[int, int, int, int]]:
    """Map area name -> (row, column, row_span, column_span), 0-based."""
    if value is None:
        return {}
    text = str(value)
    grid: List[str] = []
    for match in re.finditer(r'"([^"]*)"|\'([^\']*)\'', text):
        line = match.group(1) if match.group(1) is not None else match.group(2)
        if line:
            grid.append(line)
    if not grid:
        return {}
    cells = [line.split() for line in grid]
    areas: Dict[str, Tuple[int, int, int, int]] = {}
    for r, line in enumerate(cells):
        for c, name in enumerate(line):
            if name in (".", "none", ""):
                continue
            if name in areas:
                row0, col0, rowspan, colspan = areas[name]
                areas[name] = (row0, col0,
                               max(rowspan, r - row0 + 1),
                               max(colspan, c - col0 + 1))
            else:
                areas[name] = (r, c, 1, 1)
    return areas


# ---------------------------------------------------------------------------
# Placement helpers
# ---------------------------------------------------------------------------

def _line_index(value, total: int) -> Optional[int]:
    """Turn a grid line spec ('2', '-1', 'span 2') into a 0-based index."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text.startswith("span"):
        return None
    try:
        number = int(float(text))
    except ValueError:
        return None
    if number > 0:
        return number - 1
    return max(0, total + number)


def _span_count(value) -> int:
    if value is None:
        return 1
    text = str(value).strip().lower()
    match = re.match(r"span\s+(\d+)", text)
    if match:
        return max(1, int(match.group(1)))
    return 1


def _placement(child: LayoutBox, areas: Dict[str, Tuple[int, int, int, int]],
               rows: int, cols: int) -> Optional[Tuple[int, int, int, int]]:
    style = child.style

    area_name = style.get("grid-area")
    if area_name is not None:
        parts = str(area_name).split("/")
        head = parts[0].strip()
        if head in areas:
            return areas[head]
        if len(parts) >= 4:
            r0 = _line_index(parts[0], rows)
            c0 = _line_index(parts[1], cols)
            r1 = _line_index(parts[2], rows)
            c1 = _line_index(parts[3], cols)
            if r0 is not None and c0 is not None:
                rowspan = (r1 - r0 + 1) if r1 is not None and r1 >= r0 else 1
                colspan = (c1 - c0 + 1) if c1 is not None and c1 >= c0 else 1
                return (r0, c0, rowspan, colspan)

    col_spec = style.get("grid-column") or style.get("grid-column-start")
    row_spec = style.get("grid-row") or style.get("grid-row-start")
    if col_spec is None and row_spec is None:
        return None

    col_start = col_end = None
    if col_spec is not None:
        parts = str(col_spec).split("/")
        col_start = _line_index(parts[0], cols)
        if len(parts) > 1:
            col_end = _line_index(parts[1], cols)
            if col_end is None:
                colspan = _span_count(parts[1])
            else:
                colspan = max(1, col_end - (col_start or 0) + 1)
        else:
            colspan = 1
    else:
        colspan = 1

    row_start = row_end = None
    if row_spec is not None:
        parts = str(row_spec).split("/")
        row_start = _line_index(parts[0], rows)
        if len(parts) > 1:
            row_end = _line_index(parts[1], rows)
            rowspan = max(1, row_end - (row_start or 0) + 1) if row_end is not None \
                else _span_count(parts[1])
        else:
            rowspan = 1
    else:
        rowspan = 1

    if col_start is None and row_start is None:
        return None
    return (row_start if row_start is not None else -1,
            col_start if col_start is not None else -1,
            rowspan, colspan)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _num(value, base: float, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, Length):
        return value.resolve(base)
    text = str(value).strip().lower()
    if text in ("", "auto", "normal"):
        return default
    if text.endswith("%"):
        try:
            return base * float(text[:-1]) / 100.0
        except ValueError:
            return default
    parsed = parse_length(text, default=None)
    return default if parsed is None else float(parsed)


def _gap(style, which: str) -> float:
    value = style.get(which) or style.get("gap") or style.get("grid-gap")
    if value is None:
        return 0.0
    parts = str(value).split()
    if which == "row-gap":
        return _num(parts[1] if len(parts) > 1 else parts[0], 0.0)
    return _num(parts[0], 0.0)


def _content_width(child: LayoutBox, measurer, available: float) -> float:
    from .inline import intrinsic_widths

    try:
        _min_w, max_w = intrinsic_widths(child, measurer)
    except Exception:
        max_w = available
    explicit = resolve_length(child.style.get("width"), available)
    if explicit is not None:
        return explicit
    return max(0.0, min(max_w, available))


def layout_grid(box: LayoutBox, measurer, available_width: float) -> float:
    """Lay out a grid container. Returns the content height."""
    from .block import compute_layout, read_box_model

    style = box.style
    content_width = box.content_width if box.content_width > 0 else available_width
    column_gap = _gap(style, "column-gap")
    row_gap = _gap(style, "row-gap")

    columns = _column_tracks(style, content_width)
    if not columns:
        columns = [Track("fr", 1.0)]
    n_cols = len(columns)

    areas = _parse_areas(style.get("grid-template-areas"))
    # Named areas can declare more columns than the template lists.
    if areas:
        n_cols = max(n_cols, max(c + span for (_r, c, _rs, span) in areas.values()))

    row_tracks = _row_tracks(style)

    # -- resolve column widths -------------------------------------------
    fixed_total = 0.0
    fr_total = 0.0
    auto_columns: List[int] = []
    widths: List[float] = [0.0] * n_cols

    for index, track in enumerate(columns):
        if index >= n_cols:
            break
        if track.kind == "px":
            widths[index] = track.value
            fixed_total += track.value
        elif track.kind == "percent":
            widths[index] = content_width * track.value / 100.0
            fixed_total += widths[index]
        elif track.kind == "fr":
            fr_total += track.value or 1.0
        else:
            auto_columns.append(index)

    free = max(0.0, content_width - fixed_total - column_gap * (n_cols - 1))

    # size auto columns from their content first
    items = [child for child in box.children
             if child.display != "none"
             and not (child.display == "text" and not (child.text or "").strip())]
    placements: Dict[int, Tuple[int, int, int, int]] = {}
    occupied: set = set()

    # 1. explicit placements first
    for position, child in enumerate(items):
        read_box_model(child, content_width)
        placement = _placement(child, areas, 99, n_cols)
        if placement is not None and placement[0] >= 0 and placement[1] >= 0:
            row, col, rowspan, colspan = placement
            col = max(0, min(col, n_cols - 1))
            placements[position] = (row, col, max(1, rowspan), max(1, colspan))
            for r in range(row, row + rowspan):
                for c in range(col, min(n_cols, col + colspan)):
                    occupied.add((r, c))

    # 2. auto-placement: first free cell, row-major, never overlapping
    def fits(row: int, col: int, rowspan: int, colspan: int) -> bool:
        if col + colspan > n_cols:
            return False
        for r in range(row, row + rowspan):
            for c in range(col, col + colspan):
                if (r, c) in occupied:
                    return False
        return True

    cursor_row = 0
    for position, child in enumerate(items):
        if position in placements:
            continue
        row, col, rowspan, colspan = 0, 0, 1, 1
        spec = _placement(child, areas, 99, n_cols)
        if spec is not None:
            row = max(0, spec[0])
            col = max(0, spec[1])
            rowspan, colspan = max(1, spec[2]), max(1, spec[3])
            colspan = min(colspan, n_cols - min(col, n_cols - 1))
            search_from = row
        else:
            search_from = cursor_row

        placed = False
        r = search_from
        while not placed and r < search_from + 400:
            start_col = col if spec is not None and r == row else 0
            for c in range(start_col, n_cols):
                if fits(r, c, rowspan, colspan):
                    placements[position] = (r, c, rowspan, colspan)
                    for rr in range(r, r + rowspan):
                        for cc in range(c, c + colspan):
                            occupied.add((rr, cc))
                    cursor_row = max(cursor_row, r)
                    placed = True
                    break
            r += 1
        if not placed:
            placements[position] = (cursor_row, 0, 1, 1)
            cursor_row += 1

    for index in auto_columns:
        needed = 0.0
        for position, child in enumerate(items):
            row, col, _rs, span = placements[position]
            if col == index and span == 1:
                needed = max(needed, _content_width(child, measurer, free))
        widths[index] = needed
        free -= needed

    if fr_total > 0 and free > 0:
        for index, track in enumerate(columns):
            if index >= n_cols:
                break
            if track.kind == "fr":
                widths[index] = free * ((track.value or 1.0) / fr_total)

    # Column x positions
    col_x: List[float] = []
    cursor = 0.0
    for index in range(n_cols):
        col_x.append(cursor)
        cursor += widths[index] + column_gap

    # -- place and measure every item ------------------------------------
    rows_used = max([placements[p][0] + placements[p][2] for p in placements] + [1])
    row_heights: List[float] = [0.0] * max(rows_used, len(row_tracks) or 1)

    justify_items = str(style.get("justify-items", "stretch")).strip().lower()
    align_items = str(style.get("align-items", "stretch")).strip().lower()

    measured: List[Tuple[LayoutBox, int, int, int, int, float, float]] = []
    max_row = 0
    for position, child in enumerate(items):
        row, col, rowspan, colspan = placements[position]
        if row < 0:
            row = max_row
        col = max(0, min(col, n_cols - 1))
        colspan = max(1, min(colspan, n_cols - col))
        cell_width = sum(widths[col:col + colspan]) + column_gap * (colspan - 1)
        cell_width = max(1.0, cell_width)

        inner_w = cell_width - (child.margin_left + child.margin_right
                                + child.border_left + child.border_right
                                + child.padding_left + child.padding_right)
        inner_w = max(1.0, inner_w)
        stretch = str(child.style.get("justify-self", justify_items)).lower() not in (
            "start", "center", "end", "left", "right")
        if not stretch:
            inner_w = min(inner_w, max(1.0, _content_width(child, measurer, inner_w)))
        compute_layout(child, 0.0, 0.0, inner_w, measurer)
        measured.append((child, row, col, rowspan, colspan, cell_width, inner_w))

    for child, row, _col, rowspan, _colspan, _cell_w, _inner in measured:
        if rowspan == 1:
            row_heights[row] = max(row_heights[row], child.height + child.margin_top
                                   + child.margin_bottom)
    for child, row, _col, rowspan, _cell_w, _inner in [
            (m[0], m[1], m[2], m[3], m[5], m[6]) for m in measured]:
        if rowspan > 1:
            spanned = sum(row_heights[row:row + rowspan]) + row_gap * (rowspan - 1)
            need = child.height + child.margin_top + child.margin_bottom
            if need > spanned:
                row_heights[row] += need - spanned

    row_y: List[float] = []
    cursor = 0.0
    for height in row_heights:
        row_y.append(cursor)
        cursor += height + row_gap
    content_height = max(0.0, cursor - row_gap) if row_heights else 0.0

    # -- final positioning -------------------------------------------------
    for child, row, col, rowspan, colspan, cell_width, inner_w in measured:
        self_justify = str(child.style.get("justify-self", justify_items)).lower()
        self_align = str(child.style.get("align-self", align_items)).lower()

        x = box.content_x + col_x[col]
        if self_justify == "center":
            x += (cell_width - child.width) / 2.0
        elif self_justify in ("end", "right"):
            x += cell_width - child.width
        x += child.margin_left

        if rowspan == 1:
            row_height = row_heights[row] if row < len(row_heights) else child.height
        else:
            row_height = (sum(row_heights[row:row + rowspan])
                          + row_gap * (rowspan - 1))
        y = box.content_y + row_y[row]
        if self_align == "center":
            y += max(0.0, (row_height - child.height) / 2.0)
        elif self_align in ("end", "flex-end"):
            y += max(0.0, row_height - child.height)
        y += child.margin_top

        # Move the whole subtree: grandchildren were laid out relative to
        # this box's provisional position.
        child.translate(x - child.x, y - child.y)
        if self_align in ("stretch", "normal") and rowspan == 1 \
                and _auto_height(child.style):
            child.height = max(child.height, row_height - child.margin_top
                               - child.margin_bottom)

    # Container height: rows plus any 1fr rows that should absorb slack.
    explicit_height = resolve_length(style.get("height"), available_width)
    if explicit_height is not None:
        content_height = max(content_height, explicit_height)
    min_height = resolve_length(style.get("min-height"), available_width)
    if min_height is not None:
        content_height = max(content_height, min_height)
    return content_height


def _auto_height(style) -> bool:
    value = style.get("height")
    if value is None:
        return True
    return str(value).strip().lower() in ("auto", "", "initial")


def _column_tracks(style, content_width: float) -> List[Track]:
    for key in ("grid-template-columns",):
        value = style.get(key)
        if value is not None:
            tracks = parse_track_list(value)
            if tracks:
                return tracks
    template = style.get("grid-template")
    if template is not None:
        text = str(template)
        if "/" in text:
            parts = text.split("/", 1)
            tracks = parse_track_list(parts[1])
            if tracks:
                return tracks
        else:
            tracks = parse_track_list(text)
            if tracks:
                return tracks
    return []


def _row_tracks(style) -> List[Track]:
    value = style.get("grid-template-rows")
    if value is not None:
        return parse_track_list(value)
    template = style.get("grid-template")
    if template is not None and "/" in str(template):
        return parse_track_list(str(template).split("/", 1)[0])
    return []
