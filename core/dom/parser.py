"""
core/dom/parser.py — HTML tree construction.

Turns tokens into a DOM with the tree-shaping rules that matter for layout:

  * one real `<html>` root with `<head>` and `<body>` children (no synthetic
    duplicate wrapper, so `body` is always findable at depth 1 of the root)
  * implied `<head>`/`<body>`
  * implied end tags for `<p>`, `<li>`, `<dt>/<dd>`, `<option>`, table rows/cells
  * void elements
  * scope-aware end-tag handling
  * a hard depth ceiling, so a hostile document cannot blow the stack
"""

from __future__ import annotations

from typing import List, Optional

from .tokenizer import VOID_ELEMENTS, Token, Tokenizer
from .tree import HTMLNode

__all__ = ["parse", "parse_fragment", "ParseResult"]

_HEAD_TAGS = {"title", "meta", "link", "style", "script", "base", "noscript", "template"}
_RAWTEXT_TAGS = {"style", "script", "title", "textarea", "xmp", "iframe",
                 "noembed", "noframes"}

_CLOSES_P = {
    "address", "article", "aside", "blockquote", "details", "div", "dl",
    "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "main",
    "menu", "nav", "ol", "p", "pre", "section", "table", "ul", "listing",
    "plaintext", "xmp", "dir", "center",
}

_SCOPE_BOUNDARY = {"html", "body", "table", "td", "th", "caption", "button",
                   "marquee", "object", "template"}

MAX_DEPTH = 20000


class ParseResult:
    __slots__ = ("root", "errors", "truncated")

    def __init__(self, root: HTMLNode, errors: Optional[List[str]] = None,
                 truncated: bool = False) -> None:
        self.root = root
        self.errors = errors or []
        self.truncated = truncated

    def __repr__(self) -> str:
        return f"ParseResult(truncated={self.truncated}, errors={len(self.errors)})"


class TreeBuilder:
    def __init__(self, source: str, max_depth: int = MAX_DEPTH) -> None:
        self.source = source
        self.tokens: List[Token] = []
        self.max_depth = max_depth
        self.truncated = False
        self.errors: List[str] = []

    # -- public -----------------------------------------------------------
    def build(self) -> ParseResult:
        self.tokens = Tokenizer(self.source).tokenize()

        html = HTMLNode("html")
        head = html.append(HTMLNode("head"))
        body = html.append(HTMLNode("body"))
        self.html, self.head, self.body = html, head, body

        self.stack: List[HTMLNode] = [html]
        self.in_body = False
        self.head_open = False

        for tok in self.tokens:
            try:
                self._consume(tok)
            except Exception as exc:                     # never lose the page
                self.errors.append(f"{type(exc).__name__}: {exc}")

        return ParseResult(html, self.errors, self.truncated)

    # -- current insertion point -----------------------------------------
    def _current(self) -> HTMLNode:
        return self.stack[-1]

    def _ensure_body(self) -> None:
        if self.in_body:
            return
        self.in_body = True
        self.head_open = False
        # Drop everything back to <html>, then open <body>.
        del self.stack[1:]
        self.stack.append(self.body)

    def _insert(self, node: HTMLNode) -> Optional[HTMLNode]:
        if len(self.stack) >= self.max_depth:
            self.truncated = True
            return None
        self._current().append(node)
        return node

    # -- token dispatch ---------------------------------------------------
    def _consume(self, tok: Token) -> None:
        if tok.kind == "comment":
            return

        if tok.kind == "text":
            self._handle_text(tok.data)
            return

        if tok.kind == "start":
            self._handle_start(tok)
            return

        if tok.kind == "end":
            self._handle_end(tok.name)

    def _handle_text(self, data: str) -> None:
        if not data:
            return
        if not self.in_body:
            # Text inside <style>/<script>/<title> stays inside that element.
            if self.stack[-1].tag in _RAWTEXT_TAGS:
                self._insert(HTMLNode("text", text=data))
                return
            if not data.strip():
                return
            # Non-whitespace text before <body>: everything moves into body.
            self._ensure_body()
        self._insert(HTMLNode("text", text=data))

    def _handle_start(self, tok: Token) -> None:
        name = tok.name

        if name == "html":
            for k, v in tok.attributes.items():
                self.html.attributes.setdefault(k, v)
            return

        if name == "head":
            if not self.in_body:
                del self.stack[1:]
                self.stack.append(self.head)
                self.head_open = True
            return

        if name == "body":
            for k, v in tok.attributes.items():
                self.body.attributes.setdefault(k, v)
            self._ensure_body()
            return

        if name == "frameset":
            self._ensure_body()
            return

        if not self.in_body:
            if name in _HEAD_TAGS:
                if not self.head_open:
                    del self.stack[1:]
                    self.stack.append(self.head)
                    self.head_open = True
                self._insert_head_element(tok)
                return
            self._ensure_body()

        self._insert_body_element(tok)

    def _insert_head_element(self, tok: Token) -> None:
        node = HTMLNode(tok.name, dict(tok.attributes))
        if tok.name in VOID_ELEMENTS:
            self._insert(node)
            return
        inserted = self._insert(node)
        if inserted is None:
            return
        # Raw-text elements must stay open so their text token lands inside
        # them; the matching end tag closes them.
        if not tok.self_closing:
            self.stack.append(node)

    def _insert_body_element(self, tok: Token) -> None:
        name = tok.name

        # -- implied end tags ------------------------------------------
        if name in _CLOSES_P:
            self._close("p")
        if name == "li":
            self._close("li")
        if name in ("dt", "dd"):
            self._close("dt")
            self._close("dd")
        if name == "option":
            if self._current().tag == "option":
                self.stack.pop()
        if name == "optgroup":
            if self._current().tag == "option":
                self.stack.pop()
            if self._current().tag == "optgroup":
                self.stack.pop()
        if name in ("td", "th"):
            self._close("td")
            self._close("th")
        if name == "tr":
            self._close("td")
            self._close("th")
            self._close("tr")
        if name in ("thead", "tbody", "tfoot"):
            self._close("td")
            self._close("th")
            self._close("tr")
            self._close("thead")
            self._close("tbody")
            self._close("tfoot")
        if name == "a":
            self._close("a")
        if name == "button":
            self._close("button")
        if name == "select" and self._current().tag == "select":
            self._close("select")

        node = HTMLNode(name, dict(tok.attributes))

        # <script>/<style> get their body text appended by the tokenizer as a
        # following text token; push them so the text lands inside.
        inserted = self._insert(node)
        if inserted is None:
            return
        if name in VOID_ELEMENTS:
            return
        if tok.self_closing and name not in ("html", "body"):
            # Foreign-style self-closing: honour it for unknown tags, ignore
            # for HTML elements that cannot self-close.
            if name in ("div", "span", "p", "a", "td", "th", "tr", "li"):
                pass
            else:
                return
        self.stack.append(node)

    def _handle_end(self, name: str) -> None:
        if name in ("html", "body"):
            return
        if name == "head":
            if self.head_open:
                self._close("head")
                self.head_open = False
            return
        if name == "br":
            self._insert_body_element(Token("start", "br", {}))
            return
        if name == "p":
            if not self._in_scope("p"):
                self._insert_body_element(Token("start", "p", {}))
                self._close("p")
                return
        self._close(name)

    # -- stack helpers ----------------------------------------------------
    def _in_scope(self, tag: str) -> bool:
        for node in reversed(self.stack):
            if node.tag == tag:
                return True
            if node.tag in _SCOPE_BOUNDARY:
                return False
        return False

    def _close(self, tag: str) -> bool:
        """Pop the open stack up to and including `tag`, if it is in scope."""
        if not self._in_scope(tag):
            return False
        for idx in range(len(self.stack) - 1, 0, -1):
            if self.stack[idx].tag == tag:
                del self.stack[idx:]
                if tag == "head":
                    self.head_open = False
                return True
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(source: str) -> HTMLNode:
    return TreeBuilder(source).build().root


def parse_with_report(source: str) -> ParseResult:
    return TreeBuilder(source).build()


def parse_fragment(source: str, context_tag: str = "div") -> HTMLNode:
    """Parse a snippet into a detached container (used by tests / widgets)."""
    context = HTMLNode(context_tag)
    tokens = Tokenizer(source).tokenize()
    stack: List[HTMLNode] = [context]
    for tok in tokens:
        if tok.kind == "text":
            stack[-1].append(HTMLNode("text", text=tok.data))
        elif tok.kind == "start":
            node = HTMLNode(tok.name, dict(tok.attributes))
            stack[-1].append(node)
            if tok.name not in VOID_ELEMENTS and not tok.self_closing:
                stack.append(node)
        elif tok.kind == "end" and len(stack) > 1:
            for idx in range(len(stack) - 1, 0, -1):
                if stack[idx].tag == tok.name:
                    del stack[idx:]
                    break
    return context
