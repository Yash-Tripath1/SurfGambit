"""
layout.py — compatibility surface for the v1 layout API.

The real engine is `core.layout` (block formatting, inline line boxes, tables).
This module adapts the old entry points — `build_layout_tree` and
`compute_layout(box, x, y, max_width, measurer)` — onto it, including
support for the old `TextMeasurer.measure_text(...)` protocol so existing
callers and benchmarks keep working.
"""

from __future__ import annotations

from typing import Optional, Tuple

from core.dom import HTMLNode
from core.layout.block import apply_list_markers, compute_layout as _compute
from core.layout.box import LayoutBox, build_layout_tree
from core.layout.text import FontSpec, TextMeasurer, estimate_metrics

__all__ = [
    "LayoutBox", "TextMeasurer", "build_layout_tree", "compute_layout",
    "parse_px_val",
]


class _LegacyAdapter:
    """Presents a v1 measurer (`measure_text`) as the v2 `TextMeasurer` API."""

    def __init__(self, legacy) -> None:
        self.legacy = legacy
        self.zoom = getattr(legacy, "zoom", 1.0)
        self.headless = getattr(legacy, "headless", False)

    def spec_from_style(self, style) -> FontSpec:
        size = style.get("font-size", 16)
        try:
            size = float(size)
        except (TypeError, ValueError):
            size = 16.0
        weight = style.get("font-weight", 400)
        try:
            weight = int(weight)
        except (TypeError, ValueError):
            weight = 700 if str(weight).lower() == "bold" else 400
        return FontSpec(str(style.get("font-family", "sans-serif")), size, weight,
                        str(style.get("font-style", "normal")),
                        bool(style.get("underline", False)),
                        bool(style.get("line-through", False)))

    def _legacy_measure(self, text: str, spec: FontSpec) -> Tuple[float, float]:
        try:
            width, height = self.legacy.measure_text(
                text, spec.family, f"{spec.size}px",
                "bold" if spec.weight >= 600 else "normal", spec.slant)
            return float(width), float(height)
        except Exception:
            return estimate_metrics(text, spec.size)[0], spec.size * 1.2

    def measure(self, text: str, spec: FontSpec) -> float:
        return self._legacy_measure(text, spec)[0]

    def metrics(self, spec: FontSpec) -> Tuple[float, float, float]:
        _, height = self._legacy_measure("Hg", spec)
        ascent = height * 0.8
        return ascent, ascent, max(1.0, height - ascent)

    def get_font(self, spec: FontSpec):
        try:
            return self.legacy.get_font(spec.family, f"{spec.size}px",
                                        "bold" if spec.weight >= 600 else "normal",
                                        spec.slant)
        except Exception:
            return None

    def line_height_for(self, style, spec: FontSpec) -> float:
        value = style.get("line-height", "normal")
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value)
        if text.endswith("px"):
            try:
                return float(text[:-2])
            except ValueError:
                pass
        _, height = self._legacy_measure("Hg", spec)
        return height

    def clear(self) -> None:
        pass


def compute_layout(box: Optional[LayoutBox], x: int, y: int,
                   max_width: int, measurer) -> None:
    if box is None:
        return
    adapter = _LegacyAdapter(measurer)
    apply_list_markers(box)
    _compute(box, x, y, max_width, adapter)


def parse_px_val(val_str: str, default: int = 0) -> int:
    from core.values import parse_length

    length = parse_length(val_str, default=None)
    return default if length is None else int(round(length))
