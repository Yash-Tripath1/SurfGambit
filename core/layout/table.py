"""
core/layout/table.py — table layout.

Implements the automatic table layout algorithm closely enough for the pages
that actually still use tables (Hacker News, Wikipedia infoboxes, email-style
HTML):

  * column widths from min/max content widths, distributed to fill the table
  * `colspan`
  * `border-collapse` vs `border-spacing`
  * per-row heights sized to the tallest cell
"""

from __future__ import annotations

from typing import List

from .box import LayoutBox, resolve_length
from .inline import intrinsic_widths
from .text import TextMeasurer

__all__ = ["layout_table"]

_MAX_COLS = 64


def _rows(box: LayoutBox) -> List[LayoutBox]:
    rows: List[LayoutBox] = []
    for child in box.children:
        if child.display == "table-row":
            rows.append(child)
        elif child.display in ("table-row-group", "table-header-group",
                               "table-footer-group"):
            for row in child.children:
                if row.display == "table-row":
                    rows.append(row)
    return rows


def _colspan(cell: LayoutBox) -> int:
    try:
        return max(1, min(_MAX_COLS, int(cell.node.attributes.get("colspan", "1"))))
    except (TypeError, ValueError):
        return 1


def _rowspan(cell: LayoutBox) -> int:
    try:
        return max(1, int(cell.node.attributes.get("rowspan", "1")))
    except (TypeError, ValueError):
        return 1


def layout_table(box: LayoutBox, measurer: TextMeasurer, available_width: float,
                 x: float, y: float) -> None:
    from .block import compute_layout, read_box_model

    read_box_model(box, available_width)
    style = box.style

    explicit = resolve_length(style.get("width"), available_width)
    if explicit is not None:
        content_width = explicit
    else:
        content_width = max(0.0, available_width - box.margin_left - box.margin_right
                            - box.padding_left - box.padding_right
                            - box.border_left - box.border_right)

    box.x = x + box.margin_left
    box.y = y
    box.width = (content_width + box.padding_left + box.padding_right
                 + box.border_left + box.border_right)

    rows = _rows(box)
    if not rows:
        box.height = box.padding_top + box.padding_bottom + box.border_top + box.border_bottom
        return

    collapse = style.get("border-collapse") == "collapse"
    spacing = 0.0 if collapse else float(style.get("border-spacing", 2.0) or 2.0)

    # -- column geometry ------------------------------------------------
    n_cols = 1
    for row in rows:
        n_cols = max(n_cols, sum(_colspan(c) for c in row.children
                                 if c.display == "table-cell"))
    n_cols = min(n_cols, _MAX_COLS)

    min_w = [0.0] * n_cols
    max_w = [0.0] * n_cols

    for row in rows:
        col = 0
        for cell in row.children:
            if cell.display != "table-cell":
                continue
            span = _colspan(cell)
            read_box_model(cell, content_width)
            cmin, cmax = intrinsic_widths(cell, measurer)
            extras = (cell.padding_left + cell.padding_right
                      + cell.border_left + cell.border_right)
            explicit_cell = resolve_length(cell.style.get("width"), content_width)
            if explicit_cell is not None:
                cmin = cmax = max(cmin, explicit_cell)
            cmin += extras
            cmax += extras
            share = max(1, span)
            for k in range(span):
                if col + k < n_cols:
                    min_w[col + k] = max(min_w[col + k], cmin / share)
                    max_w[col + k] = max(max_w[col + k], cmax / share)
            col += span

    for i in range(n_cols):
        max_w[i] = max(max_w[i], min_w[i])

    total_min = sum(min_w)
    total_max = sum(max_w)

    # A table with `width: auto` shrinks to fit its content (CSS table layout).
    if explicit is None:
        fitted = total_max + spacing * (n_cols + 1)
        content_width = min(content_width, max(fitted, total_min))
        box.width = (content_width + box.padding_left + box.padding_right
                     + box.border_left + box.border_right)

    inner = max(0.0, content_width - spacing * (n_cols + 1))
    extra = inner - total_min

    if extra <= 0:
        col_widths = list(min_w)
    elif total_max > total_min:
        spread = total_max - total_min
        col_widths = [w + extra * ((max_w[i] - min_w[i]) / spread)
                      for i, w in enumerate(min_w)]
    elif total_min > 0:
        col_widths = [w + extra * (w / total_min) for w in min_w]
    else:
        even = extra / n_cols if n_cols else 0.0
        col_widths = [even] * n_cols

    # -- lay out cells ---------------------------------------------------
    cursor_y = box.content_y + spacing
    content_x = box.content_x

    for row in rows:
        row.x = content_x
        row.y = cursor_y
        row.width = sum(col_widths) + spacing * (n_cols - 1)

        col = 0
        row_height = 0.0
        cell_boxes: List[LayoutBox] = []
        for cell in row.children:
            if cell.display != "table-cell":
                continue
            span = _colspan(cell)
            cell_width = sum(col_widths[col:col + span]) + spacing * (span - 1)
            cell_x = content_x + spacing + sum(col_widths[:col]) + spacing * col
            # `cell_width` is the border-box width; compute_layout subtracts
            # padding/border itself, so pass the border-box value through.
            compute_layout(cell, cell_x, cursor_y + spacing,
                           max(1.0, cell_width), measurer)
            row_height = max(row_height, cell.height)
            cell_boxes.append(cell)
            col += span

        for cell in cell_boxes:
            cell.height = row_height

        row.height = row_height + spacing * 2
        cursor_y += row.height

    table_height = cursor_y - box.content_y + spacing
    box.height = (max(0.0, table_height) + box.padding_top + box.padding_bottom
                  + box.border_top + box.border_bottom)
