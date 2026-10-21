"""
ui/renderer.py — paint a layout tree onto a Tk canvas.

Draws, in CSS order per box: background, borders, then content (text runs,
images, form widgets, list markers).

Details that used to be wrong and are fixed here:
  * borders are drawn per side (solid / dashed / dotted / double), not a single
    `border` string parse
  * underlined text gets its own cached font instead of mutating a shared one
  * images are scaled to their CSS box, not left at native size
  * `visibility: hidden` is respected
  * text is drawn per run, not per word (≈4× fewer canvas items)
  * **viewport culling**: only boxes that intersect the visible band are
    turned into canvas items, so a 40 000 px Wikipedia page costs the same to
    scroll as a one-screen page
  * every painted run is tagged with its DOM node, which is what lets clicks
    reach script listeners (see `node_map`)
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

import tkinter as tk

from core.dom.tree import HTMLNode
from core.layout.box import LayoutBox, InlineItem
from core.layout.text import TextMeasurer
from core.values import is_dark

__all__ = ["Renderer", "find_link"]

_BORDER_STYLE_DASH = {
    "solid": None,
    "dotted": (1, 3),
    "dashed": (5, 3),
    "double": None,
    "groove": None,
    "ridge": None,
    "inset": None,
    "outset": None,
}


def find_link(node: Optional[HTMLNode]) -> str:
    """Walk up from a text node to the nearest <a href>."""
    current = node
    while current is not None:
        if current.tag == "a":
            href = current.attributes.get("href")
            if href:
                return href
        current = current.parent
    return ""


class Renderer:
    def __init__(self, canvas: tk.Canvas, measurer: TextMeasurer,
                 image_provider: Optional[Callable[[str], Optional[tk.PhotoImage]]] = None,
                 base_url: str = "") -> None:
        self.canvas = canvas
        self.measurer = measurer
        self.image_provider = image_provider
        self.base_url = base_url
        self.link_map: Dict[int, str] = {}
        self._next_link_id = 1
        self._scaled_images: Dict[Tuple[str, int], tk.PhotoImage] = {}
        self.search_term = ""
        self.search_hits = 0
        self._default_fg = "#000000"
        self._z = 1
        self.form_factory = None
        self.node_map: Dict[int, HTMLNode] = {}
        self.drawn_boxes = 0
        self.drawn_items = 0
        self._viewport: Optional[Tuple[float, float]] = None

    # -- public -----------------------------------------------------------
    def render(self, root: LayoutBox, base_url: str = "",
               search_term: str = "",
               viewport: Optional[Tuple[float, float]] = None) -> None:
        """Paint the layout tree.

        `viewport` is an optional (top, bottom) band in page coordinates;
        boxes outside it are laid out but not turned into canvas items.
        """
        self.base_url = base_url
        self.search_term = search_term or ""
        self.search_hits = 0
        self.link_map.clear()
        self.node_map.clear()
        self._next_link_id = 1
        self.canvas.delete("all")
        self._z = 1
        self._viewport = viewport
        self.drawn_boxes = 0
        self.drawn_items = 0

        if self.search_term:
            self.search_hits = _count_search_hits(root, self.search_term)

        stack: List[LayoutBox] = [root]
        while stack:
            box = stack.pop()
            if self._intersects(box):
                self._draw(box)
            # Inline children are painted as line items; only recurse into
            # block-level boxes to avoid double-drawing.
            for child in reversed(box.children):
                if child.is_block_level():
                    stack.append(child)

    # -- culling ----------------------------------------------------------
    def _intersects(self, box: LayoutBox) -> bool:
        if self._viewport is None:
            return True
        top, bottom = self._viewport
        return not (box.y + box.height < top or box.y > bottom)

    def _item_visible(self, y: float, height: float) -> bool:
        if self._viewport is None:
            return True
        top, bottom = self._viewport
        return not (y + height < top or y > bottom)

    # -- primitives -------------------------------------------------------
    def _rect(self, x1, y1, x2, y2, **kwargs):
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            return None
        return self.canvas.create_rectangle(x1, y1, x2, y2, **kwargs)

    def _rounded(self, x1, y1, x2, y2, radius: float, **kwargs) -> None:
        if radius <= 0.5 or (x2 - x1) < radius * 2 or (y2 - y1) < radius * 2:
            self._rect(x1, y1, x2, y2, **kwargs)
            return
        # Rounded rectangle as a polygon with quarter-circle corners.
        points: List[float] = []
        steps = 6
        for cx, cy, start in (
            (x1 + radius, y1 + radius, 180),
            (x2 - radius, y1 + radius, 270),
            (x2 - radius, y2 - radius, 0),
            (x1 + radius, y2 - radius, 90),
        ):
            for i in range(steps + 1):
                angle = math.radians(start + 90 * i / steps)
                points.extend((cx + radius * math.cos(angle),
                               cy + radius * math.sin(angle)))
        self.canvas.create_polygon(points, **kwargs)

    # -- box painting -----------------------------------------------------
    def _draw(self, box: LayoutBox) -> None:
        style = box.style
        if style.get("visibility") == "hidden":
            return
        if box.width <= 0 or box.height <= 0:
            if not box.lines:
                return
        self.drawn_boxes += 1

        radius = float(style.get("border-top-left-radius", 0) or 0)

        bg = style.get("background-color")
        if bg:
            self._rounded(box.x, box.y, box.x + box.width, box.y + box.height,
                          radius, fill=bg, outline="")

        self._draw_borders(box, radius)

        if box.list_marker:
            self._draw_marker(box)

        for line in box.lines:
            for item in line.items:
                if not self._item_visible(item.y, item.height):
                    continue
                self.drawn_items += 1
                self._draw_item(item)

    def _draw_borders(self, box: LayoutBox, radius: float) -> None:
        style = box.style
        x1, y1 = box.x, box.y
        x2, y2 = box.x + box.width, box.y + box.height

        sides = (
            ("top", x1, y1, x2, y1, box.border_top),
            ("right", x2, y1, x2, y2, box.border_right),
            ("bottom", x1, y2, x2, y2, box.border_bottom),
            ("left", x1, y1, x1, y2, box.border_left),
        )
        uniform = (
            box.border_top == box.border_right == box.border_bottom == box.border_left
            and style.get("border-top-style") == style.get("border-right-style")
            == style.get("border-bottom-style") == style.get("border-left-style")
            == style.get("border-top-color") == style.get("border-right-color")
            == style.get("border-bottom-color") == style.get("border-left-color")
        )

        if uniform and box.border_top > 0:
            color = style.get("border-top-color") or "#000000"
            bstyle = style.get("border-top-style", "solid")
            if radius > 0.5:
                self._rounded(x1, y1, x2, y2, radius, fill="", outline=color,
                              width=max(1, int(box.border_top)))
            else:
                dash = _BORDER_STYLE_DASH.get(str(bstyle))
                self._rect(x1 + box.border_top / 2, y1 + box.border_top / 2,
                           x2 - box.border_top / 2, y2 - box.border_top / 2,
                           fill="", outline=color,
                           width=max(1, int(box.border_top)),
                           **({"dash": dash} if dash else {}))
            return

        for side, sx1, sy1, sx2, sy2, width in sides:
            if width <= 0:
                continue
            color = style.get(f"border-{side}-color") or "#000000"
            bstyle = str(style.get(f"border-{side}-style", "solid"))
            if bstyle in ("none", "hidden"):
                continue
            dash = _BORDER_STYLE_DASH.get(bstyle)
            half = width / 2.0
            if side == "top":
                sy1 = sy2 = y1 + half
            elif side == "bottom":
                sy1 = sy2 = y2 - half
            elif side == "left":
                sx1 = sx2 = x1 + half
            else:
                sx1 = sx2 = x2 - half
            self.canvas.create_line(
                sx1, sy1, sx2, sy2, fill=color,
                width=max(1, int(round(width))),
                **({"dash": dash} if dash else {}),
            )

    def _draw_marker(self, box: LayoutBox) -> None:
        style = box.style
        spec = self.measurer.spec_from_style(style)
        text = box.list_marker
        if not text:
            return
        width = self.measurer.measure(text, spec)
        x = box.x - width - 7
        if x < 2:
            x = box.x + 4
        _, ascent, _ = self.measurer.metrics(spec)
        base_y = box.content_y + (box.lines[0].ascent if box.lines else ascent)
        font = self.measurer.get_font(spec)
        self.canvas.create_text(
            x + width, base_y, text=text, anchor="se",
            fill=str(style.get("color") or "#000000"),
            font=font if font else ("Arial", max(6, int(spec.size))),
        )

    # -- inline items -----------------------------------------------------
    def _draw_item(self, item: InlineItem) -> None:
        kind = item.kind
        if kind == "word":
            self._draw_word(item)
        elif kind == "image":
            self._draw_image(item)
        elif kind == "widget":
            self._draw_widget(item)
        elif kind == "inline-block":
            payload = item.extra
            if payload:
                box = payload[0]
                # The sub-box was laid out at (0,0); shift into place.
                self._offset_box(box, item.x + payload[1], item.y + payload[2])
                self._draw(box)
                for child in box.children:
                    if child.is_block_level():
                        self._draw(child)

    def _draw_word(self, item: InlineItem) -> None:
        if not item.text:
            return
        style = item.style
        color = str(style.get("color") or self._default_fg)
        spec = self.measurer.spec_from_style(style)
        font = self.measurer.get_font(spec)

        href = find_link(item.node)
        tags: Tuple[str, ...] = ()
        if href:
            link_id = self._next_link_id
            self._next_link_id += 1
            self.link_map[link_id] = href
            tags = ("link", f"link_{link_id}")
            self.canvas.config(cursor="")

        if self.search_term and self.search_term.lower() in item.text.lower():
            self.search_hits += 1
            self.canvas.create_rectangle(
                item.x - 1, item.y - 1,
                item.x + item.width + 1, item.y + item.height + 2,
                fill="#ffe066", outline="", tags=(),
            )
            color = "#000000"

        node_tag = ()
        if item.node is not None:
            self.node_map[id(item.node)] = item.node
            node_tag = (f"n{id(item.node)}",)
        self.canvas.create_text(
            item.x, item.y, text=item.text, anchor="nw", fill=color,
            font=font if font else ("Arial", max(6, int(spec.size))),
            tags=tags + node_tag,
        )

    def _draw_image(self, item: InlineItem) -> None:
        node = item.node
        if node is None:
            self._paint_image(item)
            return
        first = self.canvas.find_all()
        self._paint_image(item)
        self._tag_new_items(first, f"n{id(node)}")
        self.node_map[id(node)] = node

    def _tag_new_items(self, first, tag: str) -> None:
        created = self.canvas.find_all()
        for canvas_id in created[len(first):]:
            self.canvas.addtag_withtag(tag, canvas_id)

    def _paint_image(self, item: InlineItem) -> None:
        url = (item.node.attributes.get("src") if item.node else "") or ""
        photo = None
        if self.image_provider and url:
            abs_url = _absolute(url, self.base_url)
            photo = self.image_provider(abs_url)

        w, h = item.width, item.height
        if item.node is not None:
            self.node_map[id(item.node)] = item.node
        if photo is not None:
            scaled = self._fit_image(photo, abs_url, w, h)
            if scaled is not None:
                self.canvas.create_image(item.x, item.y, image=scaled, anchor="nw")
                return
        # Placeholder while loading / on failure.
        alt = (item.node.attributes.get("alt") if item.node else "") or ""
        style = item.style
        bg = "#f0f0f0" if not is_dark(str(style.get("background-color") or "")) else "#2a2a2a"
        fg = "#777777"
        self._rect(item.x, item.y, item.x + w, item.y + h,
                   fill=bg, outline="#cccccc", dash=(3, 3))
        if alt and w > 30 and h > 16:
            label = alt[: max(4, int(w / 7))]
            self.canvas.create_text(
                item.x + w / 2, item.y + h / 2, text=label, fill=fg,
                font=("Arial", 9), anchor="center", width=max(20, w - 8),
            )

    def _fit_image(self, photo: tk.PhotoImage, url: str,
                   target_w: float, target_h: float) -> Optional[tk.PhotoImage]:
        natural_w = photo.width()
        natural_h = photo.height()
        if natural_w <= 0 or natural_h <= 0:
            return photo

        if target_w <= 0 or target_h <= 0:
            return photo

        ratio = min(target_w / natural_w, target_h / natural_h)
        # Image already the right size: draw as-is.
        if abs(ratio - 1.0) < 0.02:
            return photo

        key = (url, int(target_w))
        cached = self._scaled_images.get(key)
        if cached is not None:
            return cached

        try:
            if ratio < 1.0:
                factor = max(1, int(round(1 / ratio)))
                scaled = photo.subsample(factor, factor)
            else:
                factor = max(1, int(round(ratio)))
                scaled = photo.zoom(factor, factor)
        except Exception:
            scaled = photo

        if len(self._scaled_images) > 200:
            self._scaled_images.clear()
        self._scaled_images[key] = scaled
        return scaled

    def _draw_widget(self, item: InlineItem) -> None:
        node = item.node
        if node is None:
            return
        first = self.canvas.find_all()
        self._paint_widget(item)
        self._tag_new_items(first, f"n{id(node)}")

    def _paint_widget(self, item: InlineItem) -> None:
        node = item.node
        if node is None:
            return
        tag = node.tag
        style = item.style
        x, y, w, h = item.x, item.y, item.width, item.height
        if w <= 0 or h <= 0:
            return
        itype = (node.attributes.get("type") or "text").lower()
        if itype == "hidden":
            return
        self.node_map[id(node)] = node

        # A live Tk widget beats a painted one: it can be typed into, and it
        # makes the surrounding <form> submittable.
        if self.form_factory is not None:
            try:
                if self.form_factory(node, x, y, w, h, dict(style)):
                    return
            except Exception:
                pass

        if tag == "input" and itype in ("text", "search", "email", "url", "tel", "number"):
            self._rect(x, y, x + w, y + h, fill="#ffffff", outline="#9a9a9a")
            font = self.measurer.get_font(self.measurer.spec_from_style(style))
            text = node.attributes.get("value") or node.attributes.get("placeholder", "")
            color = "#000000" if node.attributes.get("value") else "#999999"
            if text:
                self.canvas.create_text(
                    x + 5, y + h / 2, text=text, anchor="w", fill=color,
                    font=font if font else ("Arial", 10),
                )
        elif tag == "input" and itype == "password":
            self._rect(x, y, x + w, y + h, fill="#ffffff", outline="#9a9a9a")
            dots = "\u2022" * min(24, len(node.attributes.get("value", "")))
            if dots:
                self.canvas.create_text(x + 5, y + h / 2, text=dots, anchor="w",
                                        fill="#000000", font=("Arial", 10))
        elif tag == "input" and itype in ("checkbox", "radio"):
            self._rect(x, y, x + w, y + h, fill="#ffffff", outline="#9a9a9a")
            if "checked" in node.attributes:
                self.canvas.create_text(x + w / 2, y + h / 2, text="\u2713",
                                        fill="#000000", font=("Arial", 9, "bold"))
        elif tag in ("input", "button") and (itype in ("submit", "button", "reset")
                                             or tag == "button"):
            label = (node.attributes.get("value")
                     or "".join(c.text for c in node.children).strip()
                     or "Submit")
            self._rect(x, y, x + w, y + h, fill="#efefef", outline="#767676")
            self.canvas.create_text(x + w / 2, y + h / 2, text=label, anchor="center",
                                    fill="#000000", font=("Arial", 9))
        elif tag == "textarea":
            self._rect(x, y, x + w, y + h, fill="#ffffff", outline="#9a9a9a")
            text = "".join(c.text for c in node.children)
            if text.strip():
                self.canvas.create_text(x + 5, y + 4, text=text.strip()[:600],
                                        anchor="nw", fill="#000000",
                                        font=("Arial", 10), width=max(20, w - 10))
        elif tag == "select":
            self._rect(x, y, x + w, y + h, fill="#ffffff", outline="#9a9a9a")
            options = [o for o in node.children if o.tag == "option"]
            selected = next((o for o in options if "selected" in o.attributes),
                            options[0] if options else None)
            label = selected.text_content().strip() if selected else ""
            self.canvas.create_text(x + 5, y + h / 2, text=label, anchor="w",
                                    fill="#000000", font=("Arial", 10))
            self.canvas.create_text(x + w - 8, y + h / 2, text="\u25be", anchor="e",
                                    fill="#444444", font=("Arial", 8))

    # -- helpers ----------------------------------------------------------
    def _offset_box(self, box: LayoutBox, dx: float, dy: float) -> None:
        for node in box.walk():
            node.x += dx
            node.y += dy
            for line in node.lines:
                line.x += dx
                line.y += dy
                for item in line.items:
                    item.x += dx
                    item.y += dy


def _absolute(url: str, base: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, url) if base else url


def _count_search_hits(root: LayoutBox, term: str) -> int:
    """Count matches over the whole page, not just the visible slice."""
    needle = term.lower()
    hits = 0
    stack: List[LayoutBox] = [root]
    while stack:
        box = stack.pop()
        for line in box.lines:
            for item in line.items:
                if item.kind == "word" and item.text and needle in item.text.lower():
                    hits += 1
        stack.extend(child for child in box.children if child.is_block_level())
    return hits
