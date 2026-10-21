"""
core/layout/text.py: font handling and text measurement.

Two rules this file exists to enforce:

1. A cached ``tkinter.font.Font`` object is immutable. Decoration (underline /
   overstrike) is part of the cache key, never applied by mutating a shared
   font; that bug used to underline every link on the page.
2. Measurement always reports baseline metrics (ascent/descent), so inline
   layout can align text of different sizes on a shared baseline.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from ..style.cascade import resolve_font_family

__all__ = ["TextMeasurer", "FontSpec", "estimate_metrics"]


class FontSpec:
    """Everything that identifies a concrete font instance."""

    __slots__ = ("family", "size", "weight", "slant", "underline", "overstrike")

    def __init__(self, family: str, size: float, weight: int,
                 slant: str, underline: bool, overstrike: bool) -> None:
        self.family = family
        self.size = max(1, int(round(size)))
        self.weight = weight
        self.slant = slant
        self.underline = underline
        self.overstrike = overstrike

    def key(self) -> Tuple:
        return (self.family, self.size, self.weight, self.slant,
                self.underline, self.overstrike)

    def __repr__(self) -> str:
        return f"FontSpec({self.family},{self.size},{self.weight},{self.slant})"


def estimate_metrics(text: str, size: float, bold: bool = False) -> Tuple[float, float, float]:
    """Headless fallback: (width, ascent, descent) for DejaVu-like metrics."""
    per_char = 0.548 if not bold else 0.60
    width = len(text) * size * per_char
    return width, size * 0.80, size * 0.20


class TextMeasurer:
    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._root = None
        self._font_module = None
        self._fonts: Dict[Tuple, object] = {}
        self._metrics: Dict[Tuple, Tuple[float, float, float]] = {}
        self._width_cache: Dict[Tuple[str, Tuple], float] = {}
        self._available: Optional[set] = None
        self.zoom = 1.0

    # -- font access ------------------------------------------------------
    def _ensure_tk(self) -> bool:
        if self.headless:
            return False
        if self._font_module is not None:
            return True
        try:
            import tkinter.font as tkfont
            self._font_module = tkfont
            self._available = {f.lower() for f in tkfont.families()}
            return True
        except Exception:
            self.headless = True
            return False

    def make_spec(self, family: Optional[str], size: float, weight,
                  slant: str = "normal", decoration: str = "none") -> FontSpec:
        fam = resolve_font_family(family, self._available if self._available is not None else None)
        try:
            w = int(float(weight))
        except (TypeError, ValueError):
            w = 400
        sl = "italic" if "italic" in str(slant).lower() else "roman"
        deco = str(decoration or "none")
        return FontSpec(
            fam, size, w, sl,
            "underline" in deco,
            "line-through" in deco or "overstrike" in deco,
        )

    def get_font(self, spec: FontSpec):
        key = spec.key()
        font = self._fonts.get(key)
        if font is not None:
            return font
        if not self._ensure_tk():
            return None
        try:
            font = self._font_module.Font(
                family=spec.family,
                size=-spec.size,                 # negative = pixels, not points
                weight="bold" if spec.weight >= 600 else "normal",
                slant=spec.slant,
                underline=spec.underline,
                overstrike=spec.overstrike,
            )
        except Exception:
            try:
                font = self._font_module.Font(size=-spec.size)
            except Exception:
                return None
        self._fonts[key] = font
        return font

    # -- metrics ----------------------------------------------------------
    def metrics(self, spec: FontSpec) -> Tuple[float, float, float]:
        """Return (ascent, descent, linespace) in px."""
        key = spec.key()
        cached = self._metrics.get(key)
        if cached is not None:
            return cached

        font = self.get_font(spec)
        if font is None:
            _, asc, desc = estimate_metrics("x", spec.size, spec.weight >= 600)
            result = (asc, desc, asc + desc)
        else:
            try:
                ascent = float(font.metrics("ascent"))
                descent = float(font.metrics("descent"))
                linespace = float(font.metrics("linespace"))
                result = (ascent, descent, linespace)
            except Exception:
                _, asc, desc = estimate_metrics("x", spec.size, spec.weight >= 600)
                result = (asc, desc, asc + desc)
        self._metrics[key] = result
        return result

    def measure(self, text: str, spec: FontSpec) -> float:
        if not text:
            return 0.0
        key = (text, spec.key())
        cached = self._width_cache.get(key)
        if cached is not None:
            return cached
        font = self.get_font(spec)
        if font is None:
            width, _, _ = estimate_metrics(text, spec.size, spec.weight >= 600)
        else:
            try:
                width = float(font.measure(text))
            except Exception:
                width, _, _ = estimate_metrics(text, spec.size, spec.weight >= 600)
        if len(self._width_cache) > 20000:
            self._width_cache.clear()
        self._width_cache[key] = width
        return width

    # -- convenience ------------------------------------------------------
    def spec_from_style(self, style: Dict[str, object]) -> FontSpec:
        family = style.get("font-family")
        size = style.get("font-size", 16.0)
        try:
            size_px = float(size)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            size_px = 16.0
        return self.make_spec(
            str(family) if family else None,
            size_px,
            style.get("font-weight", 400),
            str(style.get("font-style", "normal")),
            str(style.get("text-decoration", "none")),
        )

    def line_height_for(self, style: Dict[str, object], spec: FontSpec) -> float:
        explicit = style.get("line-height")
        if isinstance(explicit, (int, float)) and explicit > 0:
            return float(explicit)
        _, _, linespace = self.metrics(spec)
        return linespace

    def clear(self) -> None:
        self._width_cache.clear()
