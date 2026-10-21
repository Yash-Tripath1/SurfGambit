r"""
core/style/css.py — a real CSS tokenizer and rule parser.

Replaces the single regex `([^{]+)\{([^}]+)\}` that silently destroyed every
rule after the first `@media` block.

Handles correctly:
  * comments inside and between declarations
  * strings containing braces/semicolons
  * nested at-rules (@media, @supports) without leaking them into selectors
  * @font-face / @keyframes / @import (skipped cleanly, not misparsed)
  * `!important`
  * multiple selectors per rule with independent specificity
  * unbalanced/truncated stylesheets (never raises)

Produces `Rule` objects carrying a parsed `Selector` (or None if unsupported)
so the cascade can use bucket lookups instead of testing every rule on every
element.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

from .. import values
from .selectors import Selector, parse_selector_list

__all__ = ["Rule", "Stylesheet", "parse_stylesheet", "RuleIndex"]


class Rule:
    """One declaration block bound to one selector."""

    __slots__ = ("selector", "declarations", "order", "media")

    def __init__(
        self,
        selector: Optional[Selector],
        declarations: Dict[str, str],
        order: int,
        media: Optional[str] = None,
    ) -> None:
        self.selector = selector
        self.declarations = declarations      # longhand -> value (no !important)
        self.order = order
        self.media = media

    @property
    def specificity(self) -> Tuple[int, int, int]:
        return self.selector.specificity if self.selector else (0, 0, 0)

    def __repr__(self) -> str:
        return f"Rule({self.selector!r} -> {len(self.declarations)} decls)"


class Stylesheet:
    """A parsed stylesheet plus its origin (user-agent / author / inline)."""

    __slots__ = ("rules", "source", "origin")

    def __init__(self, rules: List[Rule], source: str = "", origin: str = "author") -> None:
        self.rules = rules
        self.source = source
        self.origin = origin

    def __len__(self) -> int:
        return len(self.rules)

    def __repr__(self) -> str:
        return f"Stylesheet({len(self.rules)} rules from {self.source!r})"


# ---------------------------------------------------------------------------
# Low-level scanning helpers
# ---------------------------------------------------------------------------

def _skip_string(css: str, i: int) -> int:
    """Return index just past the string starting at i (i points at the quote)."""
    quote = css[i]
    i += 1
    n = len(css)
    while i < n:
        ch = css[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return n


def _strip_comments(css: str) -> str:
    out: List[str] = []
    i, n = 0, len(css)
    while i < n:
        ch = css[i]
        if ch == "/" and i + 1 < n and css[i + 1] == "*":
            end = css.find("*/", i + 2)
            i = n if end == -1 else end + 2
            # A comment is a token separator.
            out.append(" ")
            continue
        if ch in "\"'":
            j = _skip_string(css, i)
            out.append(css[i:j])
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_matching_brace(css: str, open_idx: int) -> int:
    """Given index of '{', return index of its matching '}' (or len(css))."""
    depth = 0
    i, n = open_idx, len(css)
    while i < n:
        ch = css[i]
        if ch in "\"'":
            i = _skip_string(css, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _split_top_level(text: str, sep: str) -> List[str]:
    """Split on `sep` outside of strings and parentheses."""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            j = _skip_string(text, i)
            buf.append(text[i:j])
            i = j
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

def parse_declarations(block: str) -> Dict[str, str]:
    """
    Parse `color: red; margin: 0 auto !important` into longhands.

    Values keep their raw text (layout knows how to read them); only the
    `!important` flag is stripped, recorded via a `!important` suffix on the
    key so the cascade can rank it.
    """
    out: Dict[str, str] = {}
    for decl in _split_top_level(block, ";"):
        decl = decl.strip()
        if not decl:
            continue
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if not prop or not value:
            continue
        if not re.fullmatch(r"-{0,2}[a-z][a-z0-9-]*", prop):
            continue

        important = False
        if value.lower().endswith("!important"):
            value = value[: -len("!important")].strip()
            important = True
        elif re.search(r"!\s*important\s*$", value, re.I):
            value = re.sub(r"!\s*important\s*$", "", value, flags=re.I).strip()
            important = True

        if not value:
            continue

        # `inherit`/`unset`/`initial` are handled by the cascade.
        expanded = values.expand_declaration(prop, value)
        for k, v in expanded.items():
            out[k + ("!important" if important else "")] = v
    return out


# ---------------------------------------------------------------------------
# Rule parsing
# ---------------------------------------------------------------------------

_SKIP_AT_RULES = {
    "font-face", "keyframes", "-webkit-keyframes", "-moz-keyframes",
    "charset", "namespace", "page", "counter-style", "font-feature-values",
    "property", "layer", "container", "scope",
}

_GROUP_AT_RULES = {"media", "supports", "document", "-moz-document"}


def _normalise_media(query: str) -> Optional[str]:
    """Return a normalised media query, or None if it can never apply."""
    q = query.strip().lower()
    if not q:
        return ""
    parts = [p.strip() for p in q.split(",")]
    kept = []
    for part in parts:
        part = part.replace("only ", "").strip()
        if part in ("all", "screen", ""):
            kept.append("")
        elif part.startswith(("print", "speech", "aural", "braille", "embossed",
                              "handheld", "projection", "tty", "tv")):
            continue
        else:
            kept.append(part)
    if not kept:
        # `@media print` only — never applies on screen.
        return None if parts else ""
    return ",".join(kept)


def media_matches(query: Optional[str], viewport_width: int) -> bool:
    """Evaluate the tiny media-query subset that matters for a 2D browser."""
    if query is None:
        return True
    if query == "":
        return True
    for part in query.split(","):
        part = part.strip()
        if not part:
            return True
        ok = True
        for feature in re.findall(r"\(([^)]+)\)", part):
            feature = feature.strip().lower()
            if ":" not in feature:
                continue
            name, _, val = feature.partition(":")
            name = name.strip()
            val = val.strip()
            num = values.parse_length(val)
            if num is None:
                continue
            if name in ("max-width", "device-width"):
                ok = ok and viewport_width <= num
            elif name == "min-width":
                ok = ok and viewport_width >= num
            elif name == "max-height":
                ok = ok and viewport_width <= num * 4
            elif name == "min-height":
                ok = ok and viewport_width >= 0
        if ok:
            return True
    return False


class _Parser:
    def __init__(self, css: str, origin: str = "author", media_ctx: Optional[str] = None):
        self.css = _strip_comments(css)
        self.origin = origin
        self.media_ctx = media_ctx
        self.rules: List[Rule] = []
        self._order = 0

    def parse(self) -> Stylesheet:
        self._parse_rule_list(0, len(self.css), self.media_ctx)
        return Stylesheet(self.rules, origin=self.origin)

    def _parse_rule_list(self, start: int, end: int, media: Optional[str]) -> None:
        css = self.css
        i = start
        while i < end:
            # Skip whitespace
            while i < end and css[i] in " \t\r\n\f":
                i += 1
            if i >= end:
                break

            # Find the next '{' or ';' at this level.
            j = i
            while j < end and css[j] not in "{;":
                if css[j] in "\"'":
                    j = _skip_string(css, j)
                    continue
                j += 1

            if j >= end:
                break

            if css[j] == ";":
                # Statement at-rule (@import, @charset...) — ignore.
                i = j + 1
                continue

            prelude = css[i:j].strip()
            close = _find_matching_brace(css, j)
            body = css[j + 1:close]
            i = close + 1

            if not prelude:
                continue

            if prelude.startswith("@"):
                name = prelude[1:].split(None, 1)[0].lower()
                rest = prelude[1 + len(name):].strip()
                if name in _GROUP_AT_RULES:
                    if name == "media":
                        sub = _normalise_media(rest)
                        if sub is None:
                            continue          # print-only block: skip entirely
                        combined = self._combine_media(media, sub)
                        if combined is None:
                            continue
                        self._parse_rule_list(j + 1, close, combined)
                    elif name == "supports":
                        # Treat `not (...)` as false, everything else as true.
                        if rest.lower().startswith("not "):
                            continue
                        self._parse_rule_list(j + 1, close, media)
                    else:
                        self._parse_rule_list(j + 1, close, media)
                elif name in _SKIP_AT_RULES:
                    continue
                # Unknown at-rule: ignore its body.
                continue

            declarations = parse_declarations(body)
            if not declarations:
                continue
            for selector in parse_selector_list(prelude):
                self._order += 1
                self.rules.append(
                    Rule(selector, declarations, self._order, media)
                )

    @staticmethod
    def _combine_media(outer: Optional[str], inner: str) -> Optional[str]:
        """
        Nested media contexts combine with `and`.

        `None` means "no media condition" (top level), *not* "never matches" —
        the "never matches" case is `_normalise_media` returning None, which is
        handled by the caller. Treating the two the same dropped every rule
        inside every @media block.
        """
        if outer is None:
            return inner or ""
        if not outer:
            return inner or ""
        if not inner:
            return outer
        return outer + " and " + inner


def parse_stylesheet(css: str, origin: str = "author") -> Stylesheet:
    if not css:
        return Stylesheet([], origin=origin)
    try:
        return _Parser(css, origin=origin).parse()
    except Exception:
        return Stylesheet([], origin=origin)


# ---------------------------------------------------------------------------
# Fast candidate lookup
# ---------------------------------------------------------------------------

class RuleIndex:
    """
    Buckets rules by their subject compound so the cascade only tests a handful
    of selectors per element instead of every rule in the document.
    """

    __slots__ = ("by_id", "by_class", "by_tag", "universal", "all_rules")

    def __init__(self) -> None:
        self.by_id: Dict[str, List[Rule]] = {}
        self.by_class: Dict[str, List[Rule]] = {}
        self.by_tag: Dict[str, List[Rule]] = {}
        self.universal: List[Rule] = []
        self.all_rules: List[Rule] = []

    def add(self, rules: Iterable[Rule]) -> None:
        for rule in rules:
            self.all_rules.append(rule)
            if rule.selector is None or not rule.selector.parts:
                continue
            subject = rule.selector.parts[0][1]
            kind, key = subject.key()
            if kind == 0:
                self.by_id.setdefault(key, []).append(rule)
            elif kind == 1:
                self.by_class.setdefault(key, []).append(rule)
            elif kind == 2:
                self.by_tag.setdefault(key, []).append(rule)
            else:
                self.universal.append(rule)

    def candidates(self, node) -> List[Rule]:
        attrs = getattr(node, "attributes", {}) or {}
        out: List[Rule] = list(self.universal)
        tag = getattr(node, "tag", "")
        if tag:
            out.extend(self.by_tag.get(tag, ()))
        node_id = attrs.get("id")
        if node_id:
            out.extend(self.by_id.get(node_id, ()))
        for cls in (attrs.get("class") or "").split():
            out.extend(self.by_class.get(cls, ()))
        return out

    def __len__(self) -> int:
        return len(self.all_rules)
