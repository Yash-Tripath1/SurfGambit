"""
core/dom/tokenizer.py — HTML tokenizer.

A character-by-character state machine following the shape of the WHATWG
tokenizer (data, tag open, tag name, attribute name/value, comment, doctype,
bogus comment, RCDATA and RAWTEXT). Not a regex and not a `find('>')`, which is
why it survives:

    <a title="a > b" href="/x">        quoted '>' inside an attribute
    <p class=foo>                      unquoted attribute values
    <br/>  <img src=x />               self-closing syntax
    <!-- <div>not a tag</div> -->      comments containing markup
    <script>if (a<b) { … }</script>    RAWTEXT (no tag parsing inside)
    <style>a{content:"}"}</style>      RAWTEXT with braces in strings
"""

from __future__ import annotations

import html as _htmllib
from typing import Dict, List, Optional, Tuple

__all__ = ["Token", "Tokenizer", "VOID_ELEMENTS", "RAWTEXT_ELEMENTS", "RCDATA_ELEMENTS"]

VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr", "keygen",
})

RAWTEXT_ELEMENTS = frozenset({"script", "style", "xmp", "iframe", "noembed", "noframes"})
RCDATA_ELEMENTS = frozenset({"textarea", "title"})


class Token:
    __slots__ = ("kind", "name", "attributes", "data", "self_closing")

    def __init__(
        self,
        kind: str,
        name: str = "",
        attributes: Optional[Dict[str, str]] = None,
        data: str = "",
        self_closing: bool = False,
    ) -> None:
        self.kind = kind                    # "start" | "end" | "text" | "comment"
        self.name = name
        self.attributes = attributes or {}
        self.data = data
        self.self_closing = self_closing

    def __repr__(self) -> str:
        if self.kind == "text":
            return f"Text({self.data[:30]!r})"
        if self.kind == "comment":
            return "Comment(...)"
        return f"<{self.kind} {self.name} {self.attributes}>"


_WS = "\t\n\f\r "


class Tokenizer:
    def __init__(self, source: str) -> None:
        self.src = source
        self.n = len(source)
        self.i = 0
        self.tokens: List[Token] = []

    # -- main loop --------------------------------------------------------
    def tokenize(self) -> List[Token]:
        src, n = self.src, self.n
        text_start = 0
        i = 0

        while i < n:
            if src[i] != "<":
                i += 1
                continue

            # Flush pending text.
            if i > text_start:
                self._emit_text(src[text_start:i])

            if src.startswith("<!--", i):
                end = src.find("-->", i + 4)
                if end == -1:
                    self.tokens.append(Token("comment", data=src[i + 4:]))
                    i = n
                else:
                    self.tokens.append(Token("comment", data=src[i + 4:end]))
                    i = end + 3
                text_start = i
                continue

            if src.startswith("<![CDATA[", i):
                end = src.find("]]>", i + 9)
                if end == -1:
                    self.tokens.append(Token("comment", data=src[i + 9:]))
                    i = n
                else:
                    # Treat CDATA content as literal text.
                    self.tokens.append(Token("text", data=src[i + 9:end]))
                    i = end + 3
                text_start = i
                continue

            if src.startswith("<!", i) or src.startswith("<?", i):
                end = src.find(">", i)
                if end == -1:
                    i = n
                else:
                    self.tokens.append(Token("comment", data=src[i + 2:end]))
                    i = end + 1
                text_start = i
                continue

            # End tag?
            if src.startswith("</", i):
                j = i + 2
                # "</>" is a parse error; treat as text.
                if j < n and src[j].isalpha():
                    name, j, _ = self._read_tag_name(j)
                    while j < n and src[j] in _WS:
                        j += 1
                    if j < n and src[j] == ">":
                        j += 1
                    else:
                        # Attributes on end tags are ignored; skip to '>'.
                        gt = src.find(">", j)
                        j = n if gt == -1 else gt + 1
                    self.tokens.append(Token("end", name=name))
                    i = j
                else:
                    # "</" not followed by a letter: literal text.
                    self._emit_text("</")
                    i += 2
                text_start = i
                continue

            # Start tag?
            if i + 1 < n and src[i + 1].isalpha():
                name, j = self._read_start_tag(i + 1)
                self.tokens.append(name)
                tok = self.tokens[-1]
                i = j

                # RAWTEXT / RCDATA: consume until the matching end tag.
                if tok.kind == "start" and not tok.self_closing and (
                    tok.name in RAWTEXT_ELEMENTS or tok.name in RCDATA_ELEMENTS
                ):
                    close, after = self._find_close_tag(i, tok.name)
                    content = src[i:close]
                    if tok.name in RCDATA_ELEMENTS:
                        content = _htmllib.unescape(content)
                    if content:
                        self.tokens.append(Token("text", data=content))
                    # The element still needs its end tag, otherwise every
                    # following node is nested inside the script/style.
                    if after <= n and close < n:
                        self.tokens.append(Token("end", name=tok.name))
                    i = after
                text_start = i
                continue

            # A bare '<' that starts nothing: literal text.
            i += 1

        if text_start < n:
            self._emit_text(src[text_start:n])
        return self.tokens

    # -- helpers ----------------------------------------------------------
    def _emit_text(self, text: str) -> None:
        if not text:
            return
        # Character references are decoded for normal text nodes; RAWTEXT and
        # RCDATA build their tokens directly (RCDATA decodes first, RAWTEXT
        # must not decode at all).
        text = _htmllib.unescape(text)
        # Merge with a preceding text token to keep the tree flat.
        if self.tokens and self.tokens[-1].kind == "text":
            self.tokens[-1].data += text
        else:
            self.tokens.append(Token("text", data=text))

    def _read_tag_name(self, j: int) -> Tuple[str, int, bool]:
        src, n = self.src, self.n
        start = j
        while j < n and src[j] not in _WS and src[j] not in "/>":
            j += 1
        return src[start:j].lower(), j, False

    def _read_start_tag(self, j: int) -> Tuple[Token, int]:
        src, n = self.src, self.n
        name, j, _ = self._read_tag_name(j)
        attributes: Dict[str, str] = {}
        self_closing = False

        while j < n:
            # Skip whitespace
            while j < n and src[j] in _WS:
                j += 1
            if j >= n:
                break
            ch = src[j]
            if ch == ">":
                j += 1
                break
            if ch == "/":
                j += 1
                if j < n and src[j] == ">":
                    self_closing = True
                    j += 1
                    break
                continue

            # Attribute name
            name_start = j
            while j < n and src[j] not in _WS and src[j] not in "=/>":
                j += 1
            attr_name = src[name_start:j].lower()
            if not attr_name:
                j += 1
                continue

            while j < n and src[j] in _WS:
                j += 1

            attr_value = ""
            if j < n and src[j] == "=":
                j += 1
                while j < n and src[j] in _WS:
                    j += 1
                if j < n and src[j] in "\"'":
                    quote = src[j]
                    j += 1
                    val_start = j
                    while j < n and src[j] != quote:
                        j += 1
                    attr_value = src[val_start:j]
                    if j < n:
                        j += 1
                else:
                    val_start = j
                    while j < n and src[j] not in _WS and src[j] != ">":
                        j += 1
                    attr_value = src[val_start:j]

            if attr_name not in attributes:     # first duplicate wins
                attributes[attr_name] = _htmllib.unescape(attr_value)

        return Token("start", name, attributes, self_closing=self_closing), j

    def _find_close_tag(self, start: int, tag: str) -> Tuple[int, int]:
        """Find the `</tag` that closes a RAWTEXT/RCDATA element."""
        src, n = self.src, self.n
        needle = "</" + tag
        low = src.lower()
        pos = start
        while True:
            pos = low.find(needle, pos)
            if pos == -1:
                return n, n
            after = pos + len(needle)
            # Must be followed by whitespace, '/', or '>' to be a real close.
            if after >= n or src[after] in _WS or src[after] in "/>":
                gt = src.find(">", after)
                return pos, (n if gt == -1 else gt + 1)
            pos = after


def tokenize(source: str) -> List[Token]:
    return Tokenizer(source).tokenize()
