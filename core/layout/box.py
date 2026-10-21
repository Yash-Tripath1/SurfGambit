"""
core/layout/box.py — layout boxes and the layout tree.

A LayoutBox is the geometric counterpart of a DOM node:

    x, y, width, height        border-box rectangle (CSS pixels)
    margin_*, border_*, padding_*   the full box model, per side
    lines                      inline content, once laid out
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..dom.tree import HTMLNode
from ..values import Length

__all__ = ["LayoutBox", "InlineItem", "Line", "build_layout_tree", "resolve_length"]

# Tags that never generate a box.
_SKIP_TAGS = frozenset({
    "head", "title", "meta", "link", "base", "script", "style", "template",
    "param", "source", "track", "col", "colgroup", "datalist", "area", "map",
    "svg", "path", "g", "circle", "rect", "line", "polyline", "polygon",
    "ellipse", "defs", "use", "symbol", "iframe", "video", "audio", "noscript",
    "object", "embed", "canvas",
})

_REPLACED = frozenset({"img", "input", "button", "select", "textarea", "meter", "progress"})

BLOCK_DISPLAYS = frozenset({"block", "list-item", "table", "flex", "grid"})
INLINE_BLOCK_DISPLAYS = frozenset({"inline-block", "inline-table"})


class InlineItem:
    """One atomic thing on a text line: a word, an image, a widget."""

    __slots__ = (
        "kind", "node", "style", "text", "x", "y", "width", "height",
        "ascent", "descent", "line_height", "is_space", "extra",
    )

    def __init__(
        self,
        kind: str,
        node: Optional[HTMLNode] = None,
        style: Optional[Dict[str, object]] = None,
        text: str = "",
        width: float = 0.0,
        height: float = 0.0,
        ascent: float = 0.0,
        descent: float = 0.0,
        line_height: float = 0.0,
        is_space: bool = False,
        extra: object = None,
    ) -> None:
        self.kind = kind            # word | space | br | image | widget | inline-block | hr
        self.node = node
        self.style = style or {}
        self.text = text
        self.x = 0.0
        self.y = 0.0
        self.width = width
        self.height = height
        self.ascent = ascent
        self.descent = descent
        self.line_height = line_height
        self.is_space = is_space
        self.extra = extra

    def __repr__(self) -> str:
        return f"InlineItem({self.kind},{self.text!r},w={self.width:.0f})"


class Line:
    __slots__ = ("items", "x", "y", "width", "height", "ascent", "text")

    def __init__(self) -> None:
        self.items: List[InlineItem] = []
        self.x = 0.0
        self.y = 0.0
        self.width = 0.0
        self.height = 0.0
        self.ascent = 0.0
        self.text = ""

    def __repr__(self) -> str:
        return f"Line({self.text[:32]!r}, {len(self.items)} items)"


class LayoutBox:
    """A box in the layout tree."""

    __slots__ = (
        "node", "style", "display", "x", "y", "width", "height",
        "margin_top", "margin_right", "margin_bottom", "margin_left",
        "border_top", "border_right", "border_bottom", "border_left",
        "padding_top", "padding_right", "padding_bottom", "padding_left",
        "children", "parent", "lines", "text", "replaced_width",
        "replaced_height", "list_marker", "intrinsic_min_width",
        "intrinsic_max_width", "collapsed_margin_top", "skip",
    )

    def __init__(self, node: HTMLNode, style: Dict[str, object]) -> None:
        self.node = node
        self.style = style
        self.display = str(style.get("display", "inline"))

        self.x = 0.0
        self.y = 0.0
        self.width = 0.0
        self.height = 0.0

        self.margin_top = self.margin_right = 0.0
        self.margin_bottom = self.margin_left = 0.0
        self.border_top = self.border_right = 0.0
        self.border_bottom = self.border_left = 0.0
        self.padding_top = self.padding_right = 0.0
        self.padding_bottom = self.padding_left = 0.0

        self.children: List["LayoutBox"] = []
        self.parent: Optional["LayoutBox"] = None
        self.lines: List[Line] = []
        self.text = ""
        self.replaced_width: Optional[float] = None
        self.replaced_height: Optional[float] = None
        self.list_marker = ""
        self.intrinsic_min_width = 0.0
        self.intrinsic_max_width = 0.0
        self.collapsed_margin_top = 0.0
        self.skip = False

    # -- geometry helpers -------------------------------------------------
    @property
    def content_x(self) -> float:
        return self.x + self.border_left + self.padding_left

    @property
    def content_y(self) -> float:
        return self.y + self.border_top + self.padding_top

    @property
    def content_width(self) -> float:
        return max(0.0, self.width - self.border_left - self.border_right
                   - self.padding_left - self.padding_right)

    @property
    def content_height(self) -> float:
        return max(0.0, self.height - self.border_top - self.border_bottom
                   - self.padding_top - self.padding_bottom)

    @property
    def margin_box_height(self) -> float:
        return self.height + self.margin_top + self.margin_bottom

    def is_block_level(self) -> bool:
        return self.display in BLOCK_DISPLAYS or self.display in (
            "list-item", "table-row", "table-cell", "table-caption"
        )

    def is_inline_level(self) -> bool:
        return not self.is_block_level()

    def is_replaced(self) -> bool:
        return self.node.tag in _REPLACED

    def translate(self, dx: float, dy: float) -> None:
        """Move this box and every descendant by (dx, dy), lines included."""
        if dx == 0.0 and dy == 0.0:
            return
        stack = [self]
        while stack:
            box = stack.pop()
            box.x += dx
            box.y += dy
            for line in box.lines:
                line.x += dx
                line.y += dy
                for item in line.items:
                    item.x += dx
                    item.y += dy
            stack.extend(box.children)

    def walk(self):
        """Iterative pre-order traversal (safe on pathological trees)."""
        stack = [self]
        while stack:
            box = stack.pop()
            yield box
            stack.extend(reversed(box.children))

    def is_table_part(self) -> bool:
        return self.display.startswith("table")

    def __repr__(self) -> str:
        return (f"LayoutBox(<{self.node.tag}>, {self.display}, "
                f"x={self.x:.0f}, y={self.y:.0f}, w={self.width:.0f}, h={self.height:.0f})")


def resolve_length(value, base: float, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    if isinstance(value, Length):
        return value.resolve(base)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_layout_tree(root: HTMLNode) -> Optional[LayoutBox]:
    """
    Build the box tree iteratively (no recursion, so deep DOMs are safe).

    Inline elements that contain block-level children are upgraded to block,
    which is the same compromise the previous engine made but done without
    mutating the DOM.
    """
    root_box = _make_box(root)
    if root_box is None:
        return None

    stack = [(root, root_box)]
    while stack:
        node, box = stack.pop()
        for child in node.children:
            child_box = _make_box(child)
            if child_box is None:
                continue
            child_box.parent = box
            box.children.append(child_box)
            if child.tag != "text":
                stack.append((child, child_box))

    _fix_inline_containers(root_box)
    return root_box


def _make_box(node: HTMLNode) -> Optional[LayoutBox]:
    if node.tag == "text":
        text = node.text or ""
        if not text.strip():
            # Whitespace-only text still matters between inline elements, so
            # keep it as a lightweight box; the inline layout collapses it.
            box = LayoutBox(node, node.style)
            box.display = "text"
            box.text = text
            return box
        box = LayoutBox(node, node.style)
        box.display = "text"
        box.text = text
        return box

    if node.tag in _SKIP_TAGS:
        return None
    display = str(node.style.get("display", "inline"))
    if display == "none":
        return None

    box = LayoutBox(node, node.style)
    if node.tag in _REPLACED:
        box.replaced_width = node.replaced_width
        box.replaced_height = node.replaced_height
        if display in ("inline", "inline-block"):
            box.display = "inline-block"
    return box


def _fix_inline_containers(box: LayoutBox) -> None:
    """Promote inline boxes that hold block children to block boxes."""
    stack = list(box.children)
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.display in ("inline", "text"):
            if any(c.is_block_level() for c in node.children):
                node.display = "block"
