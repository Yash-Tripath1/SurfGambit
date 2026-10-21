"""
core/dom/tree.py: the DOM node.

A deliberately small node type: tag, attributes, children, parent, text and a
computed-style dict. Everything else (layout boxes, render items) is derived.
"""

from __future__ import annotations

from typing import Dict, Iterator, List, Optional

__all__ = [
    "HTMLNode", "TEXT_TAG", "iter_elements", "iter_nodes", "find_first",
    "find_all", "text_content", "clone_shallow", "path_of",
]

TEXT_TAG = "text"


class HTMLNode:
    """One element or text node in the document."""

    __slots__ = ("tag", "attributes", "children", "parent", "text", "style",
                 "replaced_width", "replaced_height", "_line")

    def __init__(
        self,
        tag: str,
        attributes: Optional[Dict[str, str]] = None,
        parent: Optional["HTMLNode"] = None,
        text: str = "",
    ) -> None:
        self.tag = tag.lower()
        self.attributes: Dict[str, str] = attributes if attributes is not None else {}
        self.parent: Optional["HTMLNode"] = parent
        self.text: str = text
        self.children: List["HTMLNode"] = []
        self.style: Dict[str, object] = {}
        # Intrinsic pixel size once an <img> has been decoded; None means
        # "not known yet" and the layout falls back to the attributes.
        self.replaced_width: Optional[float] = None
        self.replaced_height: Optional[float] = None
        self._line: int = 0        # source line, for diagnostics

    # -- identity ---------------------------------------------------------
    def is_text(self) -> bool:
        return self.tag == TEXT_TAG

    def is_element(self) -> bool:
        return self.tag != TEXT_TAG

    @property
    def id(self) -> str:
        return self.attributes.get("id", "")

    @property
    def classes(self) -> List[str]:
        return (self.attributes.get("class") or "").split()

    def has_class(self, name: str) -> bool:
        return name in self.classes

    def get(self, name: str, default: str = "") -> str:
        return self.attributes.get(name, default)

    # -- tree mutation ----------------------------------------------------
    def append(self, child: "HTMLNode") -> "HTMLNode":
        child.parent = self
        self.children.append(child)
        return child

    def insert(self, index: int, child: "HTMLNode") -> "HTMLNode":
        child.parent = self
        self.children.insert(index, child)
        return child

    def remove(self) -> None:
        if self.parent is not None:
            try:
                self.parent.children.remove(self)
            except ValueError:
                pass
            self.parent = None

    def clear(self) -> None:
        for child in self.children:
            child.parent = None
        self.children = []

    def set_children(self, children: List["HTMLNode"]) -> None:
        self.clear()
        for child in children:
            self.append(child)

    # -- traversal --------------------------------------------------------
    def walk(self) -> Iterator["HTMLNode"]:
        """Depth-first, pre-order, iterative (safe for very deep documents)."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            if node.children:
                stack.extend(reversed(node.children))

    def walk_elements(self) -> Iterator["HTMLNode"]:
        for node in self.walk():
            if node.tag != TEXT_TAG:
                yield node

    def ancestors(self) -> Iterator["HTMLNode"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def siblings(self) -> List["HTMLNode"]:
        if self.parent is None:
            return [self]
        return [c for c in self.parent.children if c.tag != TEXT_TAG]

    def depth(self) -> int:
        d, node = 0, self
        while node.parent is not None:
            d += 1
            node = node.parent
        return d

    def text_content(self) -> str:
        if self.tag == TEXT_TAG:
            return self.text
        out: List[str] = []
        stack = [self]
        while stack:
            node = stack.pop()
            if node.tag == TEXT_TAG:
                out.append(node.text)
            else:
                stack.extend(reversed(node.children))
        return "".join(out)

    def find(self, tag: str) -> Optional["HTMLNode"]:
        for node in self.walk():
            if node.tag == tag:
                return node
        return None

    def find_all(self, tag: str) -> List["HTMLNode"]:
        return [n for n in self.walk() if n.tag == tag]

    # -- debug ------------------------------------------------------------
    def dump(self, indent: int = 0, max_depth: int = 400) -> str:
        lines: List[str] = []
        stack = [(self, indent)]
        while stack:
            node, depth = stack.pop()
            if depth > max_depth:
                continue
            prefix = "  " * depth
            if node.tag == TEXT_TAG:
                body = (node.text or "").strip()
                if not body:
                    continue
                lines.append(f"{prefix}{body[:80]!r}")
            else:
                attrs = "".join(f' {k}="{v}"' for k, v in node.attributes.items())
                lines.append(f"{prefix}<{node.tag}{attrs}>")
                for child in reversed(node.children):
                    stack.append((child, depth + 1))
        return "\n".join(lines)

    def __repr__(self) -> str:
        if self.tag == TEXT_TAG:
            return f"Text({self.text[:24]!r})"
        ident = self.attributes.get("id")
        cls = self.attributes.get("class")
        extra = f"#{ident}" if ident else (f".{cls.split()[0]}" if cls else "")
        return f"<{self.tag}{extra}> ({len(self.children)} children)"


# ---------------------------------------------------------------------------
# Module-level helpers (match the old parser.py API surface)
# ---------------------------------------------------------------------------

def iter_nodes(root: HTMLNode) -> Iterator[HTMLNode]:
    yield from root.walk()


def iter_elements(root: HTMLNode) -> Iterator[HTMLNode]:
    yield from root.walk_elements()


def find_first(root: HTMLNode, tag: str) -> Optional[HTMLNode]:
    for node in root.walk():
        if node.tag == tag:
            return node
    return None


def find_all(root: HTMLNode, tag: str) -> List[HTMLNode]:
    return [n for n in root.walk() if n.tag == tag]


def text_content(root: HTMLNode) -> str:
    return root.text_content()


# Elements whose text is never shown to the reader.
_HIDDEN_TEXT_TAGS = frozenset({
    "script", "style", "noscript", "template", "head", "title", "meta",
    "link", "svg", "canvas",
})


def visible_text(root: HTMLNode) -> str:
    """Text a reader would actually see.

    `text_content()` includes inline `<script>` source, which makes a blank
    JavaScript-only page look like it has 18 KB of content.
    """
    out: List[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.tag == TEXT_TAG:
            out.append(node.text)
            continue
        if node.tag in _HIDDEN_TEXT_TAGS:
            continue
        stack.extend(reversed(node.children))
    return "".join(out)


def clone_shallow(node: HTMLNode) -> HTMLNode:
    copy = HTMLNode(node.tag, dict(node.attributes), None, node.text)
    copy.style = dict(node.style)
    return copy


def path_of(node: HTMLNode) -> List[int]:
    """Child indices from the root, handy for stable fingerprints."""
    out: List[int] = []
    cur = node
    while cur.parent is not None:
        try:
            out.append(cur.parent.children.index(cur))
        except ValueError:
            break
        cur = cur.parent
    out.reverse()
    return out
