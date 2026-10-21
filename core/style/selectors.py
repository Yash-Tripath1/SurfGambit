"""
core/style/selectors.py — CSS selector parsing and matching.

Supports the subset that actually shows up on real pages:

    *                 universal
    div, p, h1        type
    .class            class
    #id               id
    [attr]            attribute presence
    [attr=value]      exact, ^= prefix, $= suffix, *= substring, ~= word, |= dash
    :first-child :last-child :only-child :nth-child(an+b) :nth-of-type :first-of-type
    :root :empty :not(...) :link :any-link :hover :focus :active :visited :checked
    A B   A > B   A + B   A ~ B     (descendant, child, adjacent, general sibling)
    ,     selector lists are split by the caller

Specificity is the real (a, b, c) tuple — ids, classes/attrs/pseudos, types.

`:hover`, `:focus`, `:active`, `:visited` never match (we have no interaction
state), so `a:hover { color: red }` simply loses to the base rule — which is
the correct fallback behaviour, unlike matching it always.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = ["Selector", "parse_selector", "parse_selector_list", "matches"]


class Compound:
    """A run of simple selectors that apply to a single element: `div.card#x`."""

    __slots__ = ("tag", "id", "classes", "attrs", "pseudos")

    def __init__(self) -> None:
        self.tag: Optional[str] = None          # None means '*'
        self.id: Optional[str] = None
        self.classes: List[str] = []
        self.attrs: List[Tuple[str, Optional[str], Optional[str]]] = []
        self.pseudos: List[Tuple[str, Optional[str]]] = []

    def matches(self, node, index: int = -1, count: int = -1) -> bool:
        tag = getattr(node, "tag", "")
        if self.tag is not None and tag != self.tag:
            return False

        attrs = getattr(node, "attributes", {}) or {}

        if self.id is not None and attrs.get("id") != self.id:
            return False

        if self.classes:
            node_classes = set((attrs.get("class") or "").split())
            for cls in self.classes:
                if cls not in node_classes:
                    return False

        for name, op, value in self.attrs:
            if name not in attrs:
                return False
            if op is None:
                continue
            actual = attrs.get(name) or ""
            if op == "=" and actual != value:
                return False
            if op == "^=" and value and not actual.startswith(value):
                return False
            if op == "$=" and value and not actual.endswith(value):
                return False
            if op == "*=" and value and value not in actual:
                return False
            if op == "~=" and value and value not in actual.split():
                return False
            if op == "|=" and value and not (actual == value or actual.startswith(value + "-")):
                return False

        for name, arg in self.pseudos:
            if not _match_pseudo(node, name, arg, index, count):
                return False
        return True

    def key(self) -> Tuple[int, str]:
        """Bucket key used for fast candidate lookup."""
        if self.id:
            return (0, self.id)          # id buckets are rarest
        if self.classes:
            return (1, self.classes[0])
        if self.tag:
            return (2, self.tag)
        return (3, "*")

    def __repr__(self) -> str:
        bits = [self.tag or "*"]
        if self.id:
            bits.append("#" + self.id)
        bits.extend("." + c for c in self.classes)
        return "".join(bits)


# ---------------------------------------------------------------------------
# Pseudo classes
# ---------------------------------------------------------------------------

_NEVER = {"hover", "focus", "active", "visited", "focus-within", "focus-visible",
          "target", "disabled", "enabled"}

_ALWAYS = {"root", "link", "any-link"}


def _siblings(node) -> List:
    parent = getattr(node, "parent", None)
    if parent is None:
        return [node]
    return [c for c in getattr(parent, "children", []) if getattr(c, "tag", "") != "text"]


def _element_index(node) -> Tuple[int, int]:
    """1-based position among element siblings, plus total element count."""
    sibs = _siblings(node)
    try:
        return sibs.index(node) + 1, len(sibs)
    except ValueError:
        return 1, len(sibs)


def _nth_matches(n: int, expr: str) -> bool:
    expr = (expr or "").replace(" ", "").lower()
    if not expr:
        return False
    if expr == "odd":
        return n % 2 == 1
    if expr == "even":
        return n % 2 == 0
    m = re.fullmatch(r"([+-]?\d*)n([+-]\d+)?", expr)
    if not m:
        try:
            return n == int(expr)
        except ValueError:
            return False
    a_raw, b_raw = m.group(1), m.group(2)
    a = 1 if a_raw in ("", "+") else -1 if a_raw == "-" else int(a_raw)
    b = int(b_raw) if b_raw else 0
    if a == 0:
        return n == b
    q, r = divmod(n - b, a)
    return r == 0 and q >= 0


def _match_pseudo(node, name: str, arg: Optional[str], index: int, count: int) -> bool:
    if name in _NEVER:
        return False
    if name == "root":
        return getattr(node, "parent", None) is None
    if name in ("link", "any-link"):
        return getattr(node, "tag", "") in ("a", "area") and "href" in (
            getattr(node, "attributes", {}) or {}
        )
    if name == "empty":
        for child in getattr(node, "children", []):
            if getattr(child, "tag", "") == "text":
                if (child.text or "").strip():
                    return False
            else:
                return False
        return True
    if name == "first-child":
        return _element_index(node)[0] == 1
    if name == "last-child":
        i, total = _element_index(node)
        return i == total
    if name == "only-child":
        return _element_index(node)[1] == 1
    if name == "nth-child":
        return _nth_matches(_element_index(node)[0], arg or "")
    if name == "nth-last-child":
        i, total = _element_index(node)
        return _nth_matches(total - i + 1, arg or "")
    if name in ("first-of-type", "last-of-type"):
        tag = getattr(node, "tag", "")
        same = [c for c in _siblings(node) if getattr(c, "tag", "") == tag]
        try:
            pos = same.index(node)
        except ValueError:
            return False
        return pos == 0 if name == "first-of-type" else pos == len(same) - 1
    if name == "not":
        inner = _parse_compound(arg or "")
        return inner is not None and not inner.matches(node, index, count)
    if name in ("before", "after", "first-line", "first-letter", "placeholder",
                "selection", "marker", "backdrop"):
        # Pseudo-elements: the rule applies to the element's pseudo box. We
        # don't generate those boxes, so never match (avoids wrong styling).
        return False
    return False


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_IDENT = r"[-_a-zA-Z\u00a0-\uffff][-_a-zA-Z0-9\u00a0-\uffff]*"
_COMPOUND_RE = re.compile(
    r"""
    (?P<tag>\*|%(id)s)
    | \#(?P<id>%(id)s)
    | \.(?P<cls>%(id)s)
    | \[(?P<attr>[^\]]*)\]
    | :(?P<pseudo>%(id)s)(?:\((?P<parg>[^)]*)\))?
    """ % {"id": _IDENT},
    re.X,
)


def _parse_compound(text: str) -> Optional[Compound]:
    text = text.strip()
    if not text:
        return None
    c = Compound()
    pos = 0
    if text.startswith("*"):
        pos = 1
    while pos < len(text):
        m = _COMPOUND_RE.match(text, pos)
        if not m:
            return None
        if m.group("tag"):
            if c.tag is not None or c.id or c.classes:
                return None
            c.tag = m.group("tag").lower() if m.group("tag") != "*" else None
        elif m.group("id"):
            c.id = m.group("id")
        elif m.group("cls"):
            c.classes.append(m.group("cls"))
        elif m.group("attr") is not None:
            raw = m.group("attr").strip()
            am = re.match(r"^(%s)\s*(?:([~^$*|]?=)\s*(.+))?$" % _IDENT, raw)
            if not am:
                return None
            name = am.group(1).lower()
            op = am.group(2)
            val = am.group(3)
            if val is not None:
                val = val.strip().strip("\"'")
            c.attrs.append((name, op, val))
        elif m.group("pseudo"):
            c.pseudos.append((m.group("pseudo").lower(), (m.group("parg") or "").strip()))
        pos = m.end()
    return c


class Selector:
    """A complex selector: compounds joined by combinators, matched right-to-left."""

    __slots__ = ("parts", "specificity", "text", "pseudo_element")

    def __init__(self, parts: Sequence[Tuple[Optional[str], Compound]], text: str) -> None:
        # parts[0] is the right-most (subject) compound; its combinator is None.
        self.parts = list(parts)
        self.text = text
        self.pseudo_element = None
        a = b = c = 0
        for _, comp in self.parts:
            if comp.id:
                a += 1
            b += len(comp.classes) + len(comp.attrs)
            b += sum(1 for name, _ in comp.pseudos
                     if name not in ("before", "after", "first-line", "first-letter"))
            if comp.tag:
                c += 1
        self.specificity: Tuple[int, int, int] = (a, b, c)

    def __repr__(self) -> str:
        return f"Selector({self.text!r}, spec={self.specificity})"

    def matches(self, node) -> bool:
        if getattr(node, "tag", "") == "text":
            return False
        return self._match_from(node, 0)

    def _match_from(self, node, idx: int) -> bool:
        combinator, compound = self.parts[idx]
        if not compound.matches(node):
            return False
        if idx + 1 >= len(self.parts):
            return True
        next_comb, _ = self.parts[idx + 1]
        return self._match_relation(node, next_comb, idx + 1)

    def _match_relation(self, node, combinator: Optional[str], idx: int) -> bool:
        if combinator == ">":
            parent = getattr(node, "parent", None)
            return parent is not None and self._match_from(parent, idx)
        if combinator == " ":
            parent = getattr(node, "parent", None)
            while parent is not None:
                if self._match_from(parent, idx):
                    return True
                parent = getattr(parent, "parent", None)
            return False
        if combinator in ("+", "~"):
            sibs = _siblings(node)
            try:
                pos = sibs.index(node)
            except ValueError:
                return False
            if combinator == "+":
                return pos > 0 and self._match_from(sibs[pos - 1], idx)
            for prev in reversed(sibs[:pos]):
                if self._match_from(prev, idx):
                    return True
            return False
        return False


_COMBI_RE = re.compile(r"\s*([>+~])\s*|\s+")


def parse_selector(text: str) -> Optional[Selector]:
    """Parse one complex selector. Returns None if unsupported/invalid."""
    original = text.strip()
    if not original:
        return None

    # Split into compounds + combinators.
    tokens: List[str] = []
    combs: List[Optional[str]] = []
    pos, buf, pending = 0, "", None
    while pos < len(original):
        m = _COMBI_RE.match(original, pos)
        if m:
            if buf.strip():
                tokens.append(buf.strip())
                buf = ""
                combs.append(pending)
                pending = None
            pending = m.group(1) or " "
            pos = m.end()
            continue
        buf += original[pos]
        pos += 1
    if buf.strip():
        tokens.append(buf.strip())
        combs.append(pending)

    if not tokens:
        return None

    parts: List[Tuple[Optional[str], Compound]] = []
    # last token is the subject
    for i, tok in enumerate(reversed(tokens)):
        comp = _parse_compound(tok)
        if comp is None:
            return None
        # The combinator that links this compound to the one on its right is
        # combs[len(tokens) - i] (combs[j] precedes tokens[j] in source order).
        comb = combs[len(tokens) - i] if i > 0 else None
        parts.append((comb, comp))

    sel = Selector(parts, original)
    return sel


def parse_selector_list(text: str) -> List[Selector]:
    """Parse a comma-separated selector list, dropping unsupported entries."""
    out: List[Selector] = []
    depth = 0
    buf = ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            sel = parse_selector(buf)
            if sel is not None:
                out.append(sel)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        sel = parse_selector(buf)
        if sel is not None:
            out.append(sel)
    return out


def matches(selector: str, node) -> bool:
    """Convenience: does `selector` match `node`?"""
    sel = parse_selector(selector)
    return bool(sel and sel.matches(node))
