"""
css_compiler.py
================================================================================
Standalone CSS Stylesheet Compiler & Specificity Solver
================================================================================
A high-performance, self-contained CSS parser and cascade specificity resolver
built entirely from Python's standard library.

Pipeline:
    [Raw CSS Stylesheet] ──> Lexer & Parser ──> [Structured CSS Rules List]
                                                        │
                                                        ▼
    [HTMLNode / Element] ──> Selector Matcher ──> Specificity Sorter ──> [Resolved Style Dict]

Author  : CSS Compiler Project
Version : 1.0.0
License : MIT
"""

from __future__ import annotations

import re
import json
import copy
import logging
import textwrap
import itertools
import functools
from collections import defaultdict, OrderedDict
from dataclasses import dataclass, field
from typing import (
    Dict, List, Tuple, Optional, Any, Set, Union, Iterator,
    Callable, Generator, NamedTuple, FrozenSet
)

# ──────────────────────────────────────────────────────────────────────────────
# Logging Setup
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("css_compiler")


# ──────────────────────────────────────────────────────────────────────────────
# Constants & Regex Patterns
# ──────────────────────────────────────────────────────────────────────────────

# Specificity weights following W3C guidelines
SPECIFICITY_ID        = 100   # e.g. #welcome
SPECIFICITY_CLASS     = 10    # e.g. .card, :hover, [attr]
SPECIFICITY_ELEMENT   = 1     # e.g. div, h1, ::before
SPECIFICITY_UNIVERSAL = 0     # e.g. *

# Regex: strip block comments /* ... */  (including multi-line)
RE_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)

# Regex: collapse whitespace runs inside selectors for normalisation
RE_WHITESPACE_COLLAPSE = re.compile(r'\s+')

# Regex: split a compound selector string on combinators while keeping tokens
#        Group 1 captures the combinator character (space / > / ~ / +)
RE_COMBINATOR_SPLIT = re.compile(
    r'\s*([ >~+])\s*(?=[^\s>~+])'
)

# Regex: tokenise a simple selector into its component parts
RE_ID_TOKEN        = re.compile(r'#([\w-]+)')
RE_CLASS_TOKEN     = re.compile(r'\.([\w-]+)')
RE_PSEUDO_CLASS    = re.compile(r':([\w-]+)(?:\(([^)]*)\))?')
RE_PSEUDO_ELEMENT  = re.compile(r'::([\w-]+)')
RE_ATTRIBUTE       = re.compile(r'\[([^\]]+)\]')
RE_TAG_TOKEN       = re.compile(r'^([a-zA-Z][a-zA-Z0-9_-]*|\*)')

# Regex: parse attribute selector internals  [attr op value]
RE_ATTR_INTERNAL   = re.compile(
    r'([\w-]+)\s*(?:(=|~=|\|=|\^=|\$=|\*=)\s*["\']?([^"\']*?)["\']?)?\s*$'
)

# Regex: find all @-rule starts (we'll skip them gracefully)
RE_AT_RULE         = re.compile(r'@[\w-]+')

# CSS property value normalisation
RE_IMPORTANT       = re.compile(r'\s*!important\s*$', re.IGNORECASE)

# Valid CSS property name pattern
RE_VALID_PROPERTY  = re.compile(r'^-?[a-zA-Z_][\w-]*$')

# ──────────────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SpecificityVector:
    """
    Represents a CSS specificity as a three-component vector (a, b, c) where:
        a = number of ID selectors
        b = number of class / pseudo-class / attribute selectors
        c = number of element / pseudo-element selectors

    The scalar integer value used for quick comparison is:
        a * 100 + b * 10 + c * 1
    """
    ids:      int = 0
    classes:  int = 0
    elements: int = 0

    @property
    def value(self) -> int:
        return self.ids * SPECIFICITY_ID + \
               self.classes * SPECIFICITY_CLASS + \
               self.elements * SPECIFICITY_ELEMENT

    def __add__(self, other: "SpecificityVector") -> "SpecificityVector":
        return SpecificityVector(
            self.ids      + other.ids,
            self.classes  + other.classes,
            self.elements + other.elements,
        )

    def __lt__(self, other: "SpecificityVector") -> bool:
        return self.value < other.value

    def __le__(self, other: "SpecificityVector") -> bool:
        return self.value <= other.value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SpecificityVector):
            return NotImplemented
        return self.value == other.value

    def __repr__(self) -> str:
        return (
            f"SpecificityVector(ids={self.ids}, classes={self.classes}, "
            f"elements={self.elements}, value={self.value})"
        )

    def to_tuple(self) -> Tuple[int, int, int]:
        return (self.ids, self.classes, self.elements)


@dataclass
class CSSDeclaration:
    """
    A single property/value pair extracted from a CSS rule block.

    Attributes
    ----------
    property_name : str
        Normalised, lower-cased CSS property name (e.g. ``background-color``).
    value         : str
        Trimmed property value string (e.g. ``rgba(0,0,0,0.5)``).
    important     : bool
        ``True`` if the value had ``!important`` appended.
    """
    property_name: str
    value:         str
    important:     bool = False

    def __repr__(self) -> str:
        bang = " !important" if self.important else ""
        return f"CSSDeclaration({self.property_name}: {self.value}{bang})"


@dataclass
class HTMLNode:
    """
    A lightweight DOM-like element node used by the selector matcher.

    Attributes
    ----------
    tag        : str            Tag name in lower case  (e.g. ``div``, ``h1``).
    id         : str            Value of the ``id`` attribute (empty string if absent).
    classes    : List[str]      List of CSS class names (split on whitespace).
    attributes : Dict[str,str]  All HTML attributes including ``id`` and ``class``.
    parent     : Optional[HTMLNode]   Reference to the direct parent node.
    children   : List[HTMLNode]       Direct child nodes.
    inline_style: Dict[str,str]       Inline style declarations (highest precedence).
    """
    tag:          str                       = "div"
    id:           str                       = ""
    classes:      List[str]                 = field(default_factory=list)
    attributes:   Dict[str, str]            = field(default_factory=dict)
    parent:       Optional["HTMLNode"]      = field(default=None, repr=False)
    children:     List["HTMLNode"]          = field(default_factory=list, repr=False)
    inline_style: Dict[str, str]            = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tag = self.tag.strip().lower()
        # Sync id / classes from attributes dict if provided there
        if "id" in self.attributes and not self.id:
            self.id = self.attributes["id"]
        if self.id and "id" not in self.attributes:
            self.attributes["id"] = self.id
        if "class" in self.attributes and not self.classes:
            raw = self.attributes["class"]
            self.classes = raw.split()
        if self.classes and "class" not in self.attributes:
            self.attributes["class"] = " ".join(self.classes)

    # ── Tree helpers ──────────────────────────────────────────────────────────

    def add_child(self, child: "HTMLNode") -> "HTMLNode":
        """Append a child node and set its parent pointer."""
        child.parent = self
        self.children.append(child)
        return child

    def ancestors(self) -> Generator["HTMLNode", None, None]:
        """Yield every ancestor node from direct parent up to root."""
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def preceding_siblings(self) -> List["HTMLNode"]:
        """Return all sibling nodes that appear before this node."""
        if self.parent is None:
            return []
        siblings = self.parent.children
        try:
            idx = siblings.index(self)
        except ValueError:
            return []
        return siblings[:idx]

    def immediately_preceding_sibling(self) -> Optional["HTMLNode"]:
        """Return the single sibling node immediately before this node."""
        sibs = self.preceding_siblings()
        return sibs[-1] if sibs else None

    def has_class(self, cls: str) -> bool:
        return cls in self.classes

    def get_attribute(self, name: str) -> Optional[str]:
        return self.attributes.get(name)

    def __repr__(self) -> str:
        parts = [f"<{self.tag}"]
        if self.id:
            parts.append(f" id='{self.id}'")
        if self.classes:
            parts.append(f" class='{' '.join(self.classes)}'")
        parts.append(">")
        return "".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Lexer / Tokeniser
# ──────────────────────────────────────────────────────────────────────────────

class TokenType:
    """Enumeration of CSS token categories produced by the lexer."""
    SELECTOR    = "SELECTOR"
    OPEN_BRACE  = "OPEN_BRACE"
    CLOSE_BRACE = "CLOSE_BRACE"
    PROPERTY    = "PROPERTY"
    VALUE       = "VALUE"
    SEMICOLON   = "SEMICOLON"
    AT_RULE     = "AT_RULE"
    EOF         = "EOF"
    UNKNOWN     = "UNKNOWN"


@dataclass
class Token:
    """A single lexical token produced by :class:`CSSLexer`."""
    type:  str
    value: str
    line:  int = 0
    col:   int = 0

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value!r}, line={self.line})"


class CSSLexer:
    """
    Low-level CSS tokeniser that converts raw CSS text into a flat stream of
    :class:`Token` objects.

    The lexer performs the following steps:
    1. Strip all ``/* ... */`` comments.
    2. Walk character-by-character, grouping characters into semantic tokens.
    3. Yield ``SELECTOR``, ``OPEN_BRACE``, ``PROPERTY``, ``VALUE``,
       ``SEMICOLON``, ``CLOSE_BRACE``, ``AT_RULE``, and ``EOF`` tokens.

    Robustness guarantees
    ----------------------
    - Unclosed braces are tolerated; EOF is synthesised.
    - Unclosed parentheses inside values are tolerated.
    - Malformed declarations (missing ``:`` or ``;``) are skipped gracefully.
    """

    def __init__(self, css_text: str) -> None:
        self._source = self._strip_comments(css_text)
        self._pos    = 0
        self._line   = 1
        self._col    = 1
        self._tokens: List[Token] = []

    # ── public ────────────────────────────────────────────────────────────────

    def tokenise(self) -> List[Token]:
        """Return the complete list of tokens for the input CSS text."""
        self._tokens = list(self._generate_tokens())
        return self._tokens

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _strip_comments(text: str) -> str:
        """Remove all CSS block comments from *text*."""
        return RE_COMMENT.sub("", text)

    def _peek(self, offset: int = 0) -> str:
        idx = self._pos + offset
        return self._source[idx] if idx < len(self._source) else ""

    def _advance(self) -> str:
        ch = self._source[self._pos]
        self._pos += 1
        if ch == "\n":
            self._line += 1
            self._col   = 1
        else:
            self._col += 1
        return ch

    def _skip_whitespace(self) -> None:
        while self._pos < len(self._source) and self._source[self._pos].isspace():
            self._advance()

    def _read_until(self, stop_chars: str, also_stop_on_newline: bool = False) -> str:
        """
        Read characters until one of *stop_chars* is encountered, without
        consuming the terminating character.
        Handles nested parentheses inside values so that e.g.
        ``rgba(0, 0, 0, 0.5)`` is consumed as a single value token.
        """
        buf: List[str] = []
        depth = 0  # nesting depth for parentheses
        while self._pos < len(self._source):
            ch = self._source[self._pos]
            if ch == "(":
                depth += 1
                buf.append(self._advance())
                continue
            if ch == ")" and depth > 0:
                depth -= 1
                buf.append(self._advance())
                continue
            if depth == 0 and ch in stop_chars:
                break
            if also_stop_on_newline and ch == "\n" and depth == 0:
                break
            buf.append(self._advance())
        return "".join(buf)

    def _read_block(self) -> str:
        """
        Read everything between the current ``{`` and its matching ``}``,
        handling nested braces (e.g. inside ``@media`` blocks).
        Returns the inner text without the outermost braces.
        """
        depth = 1
        buf: List[str] = []
        while self._pos < len(self._source) and depth > 0:
            ch = self._advance()
            if ch == "{":
                depth += 1
                buf.append(ch)
            elif ch == "}":
                depth -= 1
                if depth > 0:
                    buf.append(ch)
            else:
                buf.append(ch)
        return "".join(buf)

    def _current_pos_token(self, token_type: str, value: str) -> Token:
        return Token(token_type, value, self._line, self._col)

    def _generate_tokens(self) -> Generator[Token, None, None]:
        """Core generator that yields :class:`Token` instances."""
        source_len = len(self._source)

        while self._pos < source_len:
            self._skip_whitespace()
            if self._pos >= source_len:
                break

            ch = self._source[self._pos]

            # ── @-rules ───────────────────────────────────────────────────────
            if ch == "@":
                line, col = self._line, self._col
                at_text = self._read_until("{;")
                at_text = at_text.strip()
                if self._pos < source_len and self._source[self._pos] == "{":
                    self._advance()          # consume '{'
                    block_content = self._read_block()  # consume up to matching '}'
                    yield Token(TokenType.AT_RULE, at_text.strip(), line, col)
                    # Recursively tokenise the block content so nested rules
                    # inside @media / @supports are also yielded.
                    inner_lexer = CSSLexer.__new__(CSSLexer)
                    inner_lexer._source = block_content
                    inner_lexer._pos    = 0
                    inner_lexer._line   = line
                    inner_lexer._col    = col
                    for tok in inner_lexer._generate_tokens():
                        yield tok
                else:
                    if self._pos < source_len and self._source[self._pos] == ";":
                        self._advance()
                    yield Token(TokenType.AT_RULE, at_text.strip(), line, col)
                continue

            # ── open brace → we should be inside a rule block ─────────────
            # Normally '{' is consumed after reading the selector; but guard
            # against unexpected braces.
            if ch == "}":
                self._advance()
                yield self._current_pos_token(TokenType.CLOSE_BRACE, "}")
                continue

            # ── selector ──────────────────────────────────────────────────────
            # Read until '{' which signals the start of the declaration block.
            line, col  = self._line, self._col
            raw_sel    = self._read_until("{}")
            selector   = raw_sel.strip()

            if not selector:
                # Could be trailing content after the last '}'
                if self._pos < source_len and self._source[self._pos] == "}":
                    self._advance()
                    yield Token(TokenType.CLOSE_BRACE, "}", line, col)
                elif self._pos < source_len:
                    self._advance()  # skip stray character
                continue

            if self._pos >= source_len:
                # Selector with no following block – malformed; skip.
                logger.debug("Orphaned selector (no block): %r", selector)
                break

            next_ch = self._source[self._pos]

            if next_ch == "{":
                self._advance()   # consume '{'
                yield Token(TokenType.SELECTOR, selector, line, col)
                yield Token(TokenType.OPEN_BRACE, "{", line, col)

                # ── parse declarations inside the block ───────────────────
                while self._pos < source_len:
                    self._skip_whitespace()
                    if self._pos >= source_len:
                        break
                    if self._source[self._pos] == "}":
                        self._advance()
                        yield self._current_pos_token(TokenType.CLOSE_BRACE, "}")
                        break

                    prop_line, prop_col = self._line, self._col
                    raw_prop = self._read_until(":;{}")
                    prop     = raw_prop.strip()

                    if self._pos >= source_len:
                        if prop:
                            logger.debug("Truncated property (no colon): %r", prop)
                        break

                    next_c = self._source[self._pos]

                    if next_c == ":":
                        self._advance()   # consume ':'
                        if not prop:
                            # skip pseudo-element / empty artefact
                            continue
                        val_line, val_col = self._line, self._col
                        raw_val = self._read_until(";}")
                        val     = raw_val.strip()

                        if self._pos < source_len and self._source[self._pos] == ";":
                            self._advance()   # consume ';'

                        if RE_VALID_PROPERTY.match(prop):
                            yield Token(TokenType.PROPERTY, prop, prop_line, prop_col)
                            yield Token(TokenType.VALUE,    val,  val_line,  val_col)
                        else:
                            logger.debug("Skipping invalid property token: %r", prop)

                    elif next_c in ";}":
                        # Missing colon – skip this declaration.
                        if next_c == ";":
                            self._advance()
                        elif next_c == "}":
                            self._advance()
                            yield self._current_pos_token(TokenType.CLOSE_BRACE, "}")
                            break
                    elif next_c == "{":
                        # Nested block (unexpected in plain CSS) – consume and skip.
                        self._advance()
                        self._read_block()

            elif next_ch == "}":
                self._advance()
                yield Token(TokenType.CLOSE_BRACE, "}", line, col)

        yield Token(TokenType.EOF, "", self._line, self._col)


# ──────────────────────────────────────────────────────────────────────────────
# CSS Rule
# ──────────────────────────────────────────────────────────────────────────────

class CSSRule:
    """
    Represents a single parsed CSS rule, mapping a **selector** to a
    dictionary of **property → value** declarations.

    Attributes
    ----------
    selector      : str
        The normalised selector string, e.g. ``"body .card h2"``.
    properties    : Dict[str, str]
        Mapping of property names to their string values,
        e.g. ``{"color": "#ff5722", "border-radius": "8px"}``.
    important     : Dict[str, bool]
        Tracks whether each property was declared ``!important``.
    specificity   : int
        The integer specificity weight of the selector,
        calculated once on construction.
    specificity_vector : SpecificityVector
        The full (a, b, c) specificity breakdown.
    source_order  : int
        Zero-based index of this rule in the parsed stylesheet, used as a
        tiebreaker when two rules share identical specificity.
    """

    def __init__(
        self,
        selector:     str,
        properties:   Dict[str, str],
        important:    Optional[Dict[str, bool]] = None,
        source_order: int = 0,
    ) -> None:
        self.selector:           str                 = selector.strip()
        self.properties:         Dict[str, str]      = properties
        self.important:          Dict[str, bool]     = important or {}
        self.source_order:       int                 = source_order
        self.specificity_vector: SpecificityVector   = self._compute_specificity_vector()
        self.specificity:        int                 = self.specificity_vector.value

    # ── Specificity Calculation ───────────────────────────────────────────────

    def calculate_specificity(self) -> int:
        """Public API: return the integer specificity of this rule's selector."""
        return self.specificity

    def _compute_specificity_vector(self) -> SpecificityVector:
        """
        Break the selector into simple-selector parts and sum their
        W3C specificity contributions.

        Handles:
        - Descendant combinators (space)
        - Child combinators (>)
        - Adjacent sibling combinators (+)
        - General sibling combinators (~)
        - ID selectors (#foo)
        - Class selectors (.bar)
        - Pseudo-classes (:hover, :nth-child(2))
        - Attribute selectors ([type="text"])
        - Pseudo-elements (::before, ::after)
        - Type / tag selectors (div, h1)
        - Universal selector (*)
        """
        selector = self.selector

        # :is(), :not(), :has(), :where() – specificity of most complex argument
        # :where() contributes 0 specificity – handle before generic pseudo scan
        selector = self._expand_functional_pseudos(selector)

        # Strip pseudo-elements before further processing
        # (they contribute 1 element each)
        pe_count = len(RE_PSEUDO_ELEMENT.findall(selector))
        selector = RE_PSEUDO_ELEMENT.sub("", selector)

        # Split on combinators to get individual simple selectors
        parts = self._split_on_combinators(selector)

        total = SpecificityVector(elements=pe_count)
        for part in parts:
            total = total + self._simple_selector_specificity(part)

        return total

    @staticmethod
    def _expand_functional_pseudos(selector: str) -> str:
        """
        Replace functional pseudo-classes with a placeholder that encodes their
        specificity contribution correctly.

        :where()  contributes 0 → remove entirely.
        :is()     contributes specificity of most specific argument → keep most
                  specific simple selector from argument list.
        :not()    same as :is() for specificity purposes.
        :has()    same as :is().
        :nth-child() etc. → treat as class-level (10).
        """
        # :where(...) – zero specificity; remove the whole thing
        selector = re.sub(r':where\([^)]*\)', '', selector)

        # :is(), :not(), :has() – replace with most specific part of argument
        for fn in ('is', 'not', 'has'):
            pattern = re.compile(r':' + fn + r'\(([^)]*)\)', re.IGNORECASE)
            def _replace(m: re.Match) -> str:
                args = [a.strip() for a in m.group(1).split(",") if a.strip()]
                # Pick the argument with highest specificity
                best = ""
                best_spec = -1
                for arg in args:
                    dummy = CSSRule.__new__(CSSRule)
                    dummy.selector = arg
                    sv = dummy._compute_specificity_vector()
                    if sv.value > best_spec:
                        best_spec = sv.value
                        best      = arg
                return best
            selector = pattern.sub(_replace, selector)

        return selector

    @staticmethod
    def _split_on_combinators(selector: str) -> List[str]:
        """
        Split a full selector string on CSS combinators:
        ``>``  ``+``  ``~``  and whitespace (descendant).
        Returns a list of simple-selector tokens.
        """
        # Normalise whitespace
        selector = RE_WHITESPACE_COLLAPSE.sub(" ", selector).strip()

        # Split on ' > ', ' + ', ' ~ ', or plain ' '
        parts = re.split(r'\s*[>+~]\s*|\s+', selector)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _simple_selector_specificity(simple: str) -> SpecificityVector:
        """
        Calculate the :class:`SpecificityVector` for a **single** simple selector,
        e.g. ``div#app.card:hover``.
        """
        if not simple or simple == "*":
            return SpecificityVector()

        ids      = 0
        classes  = 0
        elements = 0

        working = simple

        # Count & strip ID selectors
        ids += len(RE_ID_TOKEN.findall(working))
        working = RE_ID_TOKEN.sub("", working)

        # Count & strip attribute selectors
        classes += len(RE_ATTRIBUTE.findall(working))
        working  = RE_ATTRIBUTE.sub("", working)

        # Count & strip pseudo-classes  (NOT pseudo-elements – handled elsewhere)
        # Guard: don't match ::pseudo-element (double colon)
        pseudo_class_matches = re.findall(r'(?<!:):([\w-]+)(?:\([^)]*\))?', working)
        classes += len(pseudo_class_matches)
        working  = re.sub(r'(?<!:):([\w-]+)(?:\([^)]*\))?', '', working)

        # Count & strip class selectors
        classes += len(RE_CLASS_TOKEN.findall(working))
        working  = RE_CLASS_TOKEN.sub("", working)

        # Remaining token after stripping all of the above is the type selector
        working = working.strip()
        if working and working != "*":
            elements += 1

        return SpecificityVector(ids=ids, classes=classes, elements=elements)

    # ── Representation ────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this rule to a JSON-compatible dictionary."""
        return {
            "selector":    self.selector,
            "specificity": self.specificity,
            "specificity_vector": {
                "ids":      self.specificity_vector.ids,
                "classes":  self.specificity_vector.classes,
                "elements": self.specificity_vector.elements,
            },
            "properties":  self.properties,
            "important":   self.important,
            "source_order": self.source_order,
        }

    def __repr__(self) -> str:
        return (
            f"CSSRule(selector={self.selector!r}, "
            f"specificity={self.specificity}, "
            f"properties={self.properties!r})"
        )

    def __lt__(self, other: "CSSRule") -> bool:
        if self.specificity != other.specificity:
            return self.specificity < other.specificity
        return self.source_order < other.source_order

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CSSRule):
            return NotImplemented
        return (self.specificity == other.specificity and
                self.source_order == other.source_order)


# ──────────────────────────────────────────────────────────────────────────────
# Selector Matcher Engine
# ──────────────────────────────────────────────────────────────────────────────

class SelectorMatcher:
    """
    Evaluates whether a given :class:`HTMLNode` matches a CSS selector string.

    Supported selector features
    ----------------------------
    - Universal selector  ``*``
    - Type selector       ``div``, ``h1``, ``span``
    - Class selector      ``.card``, ``.btn-primary``
    - ID selector         ``#main``, ``#search-box``
    - Attribute selectors ``[href]``, ``[type="text"]``, ``[class~="btn"]``,
                           ``[lang|="en"]``, ``[src^="https"]``,
                           ``[href$=".pdf"]``, ``[title*="hello"]``
    - Pseudo-classes      ``:hover`` (always false – static context),
                           ``:first-child``, ``:last-child``,
                           ``:nth-child(n)``, ``:nth-child(odd/even)``,
                           ``:nth-of-type(n)``, ``:first-of-type``,
                           ``:last-of-type``, ``:only-child``,
                           ``:only-of-type``, ``:not(…)``, ``:is(…)``,
                           ``:has(…)`` (simplified), ``:empty``, ``:root``
    - Pseudo-elements     ``::before``, ``::after`` (always false – no rendering)
    - Combinators         ``' '`` (descendant), ``'>'`` (child),
                          ``'+'`` (adjacent sibling), ``'~'`` (general sibling)
    - Grouped selectors   ``h1, h2, h3`` → any one must match
    """

    def match(self, node: HTMLNode, selector: str) -> bool:
        """
        Return ``True`` if *node* is matched by *selector*.

        For grouped selectors (comma-separated), returns ``True`` if the node
        matches **any** of the groups.
        """
        selector = selector.strip()
        groups   = self._split_groups(selector)
        return any(self._match_complex(node, g.strip()) for g in groups)

    # ── Group splitting ───────────────────────────────────────────────────────

    @staticmethod
    def _split_groups(selector: str) -> List[str]:
        """
        Split on top-level commas (not inside parentheses).
        e.g. ``"h1, .card:not(a, b), div"`` → ``["h1", ".card:not(a, b)", "div"]``
        """
        groups: List[str] = []
        depth   = 0
        current: List[str] = []
        for ch in selector:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                groups.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            groups.append("".join(current).strip())
        return [g for g in groups if g]

    # ── Complex selector (combinator chain) ───────────────────────────────────

    def _match_complex(self, node: HTMLNode, selector: str) -> bool:
        """
        Match *node* against a complex selector that may contain combinators.

        Strategy: parse the selector right-to-left.
        The right-most simple selector must match *node*; combinators then
        constrain the context (ancestor / parent / sibling).
        """
        # Tokenise the complex selector into (combinator, simple_selector) pairs
        parts = self._parse_complex_selector(selector)
        if not parts:
            return False

        # Match right-to-left:  parts[-1] must match node
        return self._match_parts(node, parts, len(parts) - 1)

    def _match_parts(
        self,
        node:  HTMLNode,
        parts: List[Tuple[str, str]],
        idx:   int,
    ) -> bool:
        """
        Recursively verify that ``parts[idx]`` matches *node* and that
        the preceding parts match the appropriate ancestor/sibling.

        Each entry in *parts* is ``(combinator_to_reach_this_part, simple_sel)``.
        The combinator in ``parts[0]`` is always ``""`` (the root, no combinator).
        """
        if idx < 0:
            return True

        combinator, simple = parts[idx]

        if not self._match_simple(node, simple):
            return False

        if idx == 0:
            # No more parts to satisfy – full match
            return True

        prev_combinator, _ = parts[idx]
        # The combinator in parts[idx] describes how parts[idx] relates to
        # parts[idx-1].  We look at parts[idx]'s own combinator field.
        # Actually our tuple is (combinator_BEFORE_this_part, simple_sel)
        # so combinator == combinator that must hold between parts[idx-1]
        # and parts[idx].

        if combinator == " ":
            # Descendant – any ancestor must match parts[idx-1]
            ancestor = node.parent
            while ancestor is not None:
                if self._match_parts(ancestor, parts, idx - 1):
                    return True
                ancestor = ancestor.parent
            return False

        elif combinator == ">":
            # Child – direct parent must match
            if node.parent is None:
                return False
            return self._match_parts(node.parent, parts, idx - 1)

        elif combinator == "+":
            # Adjacent sibling – immediately preceding sibling must match
            sib = node.immediately_preceding_sibling()
            if sib is None:
                return False
            return self._match_parts(sib, parts, idx - 1)

        elif combinator == "~":
            # General sibling – any preceding sibling must match
            for sib in node.preceding_siblings():
                if self._match_parts(sib, parts, idx - 1):
                    return True
            return False

        else:
            # No combinator (should not reach here for idx > 0)
            return False

    @staticmethod
    def _parse_complex_selector(selector: str) -> List[Tuple[str, str]]:
        """
        Tokenise a complex selector into a list of
        ``(combinator, simple_selector)`` pairs ordered left to right.

        The first pair always has ``combinator == ""``.

        Examples
        --------
        ``"body .card > h2"``
        → ``[("", "body"), (" ", ".card"), (">", "h2")]``

        ``"ul > li + li"``
        → ``[("", "ul"), (">", "li"), ("+", "li")]``
        """
        selector = RE_WHITESPACE_COLLAPSE.sub(" ", selector).strip()
        parts: List[Tuple[str, str]] = []

        # Walk through and collect (combinator, simple_sel) pairs
        i         = 0
        length    = len(selector)
        combinator = ""
        buf: List[str] = []

        def flush(comb: str) -> None:
            token = "".join(buf).strip()
            if token:
                parts.append((comb, token))
            buf.clear()

        depth = 0   # track parenthesis depth

        while i < length:
            ch = selector[i]

            if ch == "(":
                depth += 1
                buf.append(ch)
                i += 1
                continue
            if ch == ")":
                depth -= 1
                buf.append(ch)
                i += 1
                continue

            if depth > 0:
                buf.append(ch)
                i += 1
                continue

            if ch in ">+~":
                flush(combinator)
                combinator = ch
                i += 1
                # Skip surrounding whitespace
                while i < length and selector[i] == " ":
                    i += 1
                continue

            if ch == " ":
                # Could be descendant combinator or just whitespace between tokens
                # Look ahead: if next non-space is a combinator char → not descendant
                j = i + 1
                while j < length and selector[j] == " ":
                    j += 1
                if j < length and selector[j] in ">+~":
                    # The actual combinator is > + ~ – just skip this space
                    i += 1
                    continue
                else:
                    flush(combinator)
                    combinator = " "
                    i += 1
                    while i < length and selector[i] == " ":
                        i += 1
                    continue

            buf.append(ch)
            i += 1

        flush(combinator)
        return parts

    # ── Simple selector matching ──────────────────────────────────────────────

    def _match_simple(self, node: HTMLNode, simple: str) -> bool:
        """
        Determine if *node* matches a **simple** selector string
        (no combinators).

        A simple selector may be a compound of e.g.:
        ``div#app.card[data-active]:not(.hidden)``
        """
        if not simple or simple == "*":
            return True

        working = simple

        # ── Pseudo-elements ─────────────────────────────────────────────────
        # ::before / ::after etc. never match real DOM nodes
        if RE_PSEUDO_ELEMENT.search(working):
            return False

        # ── Functional pseudo-classes ────────────────────────────────────────
        # Process before stripping because they may contain nested selectors.
        working, ok = self._process_functional_pseudos(node, working)
        if not ok:
            return False

        # ── Attribute selectors ──────────────────────────────────────────────
        for attr_expr in RE_ATTRIBUTE.findall(working):
            if not self._match_attribute(node, attr_expr):
                return False
        working = RE_ATTRIBUTE.sub("", working)

        # ── Pseudo-classes (non-functional) ──────────────────────────────────
        for pc_match in re.finditer(r'(?<!:):([\w-]+)', working):
            pc = pc_match.group(1).lower()
            if not self._match_pseudo_class(node, pc):
                return False
        working = re.sub(r'(?<!:):([\w-]+)', '', working)

        # ── ID selector ──────────────────────────────────────────────────────
        for id_val in RE_ID_TOKEN.findall(working):
            if node.id != id_val:
                return False
        working = RE_ID_TOKEN.sub("", working)

        # ── Class selectors ──────────────────────────────────────────────────
        for cls in RE_CLASS_TOKEN.findall(working):
            if not node.has_class(cls):
                return False
        working = RE_CLASS_TOKEN.sub("", working)

        # ── Type selector ────────────────────────────────────────────────────
        working = working.strip()
        if working and working != "*":
            if node.tag != working.lower():
                return False

        return True

    def _process_functional_pseudos(
        self, node: HTMLNode, selector: str
    ) -> Tuple[str, bool]:
        """
        Handle functional pseudo-classes such as ``:not(…)``, ``:is(…)``,
        ``:has(…)``, ``:nth-child(…)``, ``:nth-of-type(…)``.

        Returns the selector with functional pseudos stripped, plus a bool
        indicating whether all functional pseudos matched.
        """
        pattern = re.compile(r':?([\w-]+)\(([^()]*(?:\([^()]*\)[^()]*)*)\)')

        # We'll replace each match in-place
        remaining = selector
        for m in list(pattern.finditer(selector)):
            fn_name = m.group(1).lower()
            fn_arg  = m.group(2).strip()

            # Validate match
            matched = self._match_functional_pseudo(node, fn_name, fn_arg)
            if not matched:
                return selector, False

            # Remove this functional pseudo from remaining
            remaining = remaining.replace(m.group(0), "", 1)

        return remaining, True

    def _match_functional_pseudo(
        self, node: HTMLNode, fn_name: str, arg: str
    ) -> bool:
        """Dispatch to the correct handler for each functional pseudo-class."""
        if fn_name == "not":
            groups = self._split_groups(arg)
            return not any(self.match(node, g) for g in groups)

        elif fn_name in ("is", "matches", "any"):
            groups = self._split_groups(arg)
            return any(self.match(node, g) for g in groups)

        elif fn_name == "has":
            # :has(selector) – matches if any descendant matches the selector
            return self._node_has_descendant_matching(node, arg)

        elif fn_name == "where":
            groups = self._split_groups(arg)
            return any(self.match(node, g) for g in groups)

        elif fn_name == "nth-child":
            return self._match_nth(node, arg, of_type=False)

        elif fn_name == "nth-last-child":
            return self._match_nth_last(node, arg, of_type=False)

        elif fn_name == "nth-of-type":
            return self._match_nth(node, arg, of_type=True)

        elif fn_name == "nth-last-of-type":
            return self._match_nth_last(node, arg, of_type=True)

        elif fn_name == "nth-col":
            # Not broadly supported; return False
            return False

        elif fn_name == "lang":
            lang_attr = node.get_attribute("lang")
            if lang_attr is None:
                return False
            return lang_attr.lower().startswith(arg.strip('"\'').lower())

        elif fn_name == "dir":
            return node.get_attribute("dir") == arg.strip()

        else:
            # Unknown functional pseudo → conservatively return True
            # (don't block matching for unrecognised pseudos)
            logger.debug("Unknown functional pseudo-class :%s(%s)", fn_name, arg)
            return True

    def _node_has_descendant_matching(
        self, node: HTMLNode, selector: str
    ) -> bool:
        """Return True if any descendant of *node* matches *selector*."""
        stack = list(node.children)
        while stack:
            child = stack.pop()
            if self.match(child, selector):
                return True
            stack.extend(child.children)
        return False

    # ── Pseudo-class helpers ──────────────────────────────────────────────────

    def _match_pseudo_class(self, node: HTMLNode, pc: str) -> bool:
        """Match simple (non-functional) pseudo-classes."""
        if pc in ("hover", "focus", "active", "visited", "checked",
                  "disabled", "enabled", "placeholder", "focus-within",
                  "focus-visible", "target"):
            # Dynamic pseudo-classes – always False in static analysis
            return False

        if pc == "root":
            return node.parent is None

        if pc == "empty":
            return len(node.children) == 0

        if pc == "first-child":
            return self._is_nth_child(node, 1, of_type=False)

        if pc == "last-child":
            return self._is_nth_last_child(node, 1, of_type=False)

        if pc == "only-child":
            siblings = node.parent.children if node.parent else [node]
            return len(siblings) == 1

        if pc == "first-of-type":
            return self._is_nth_child(node, 1, of_type=True)

        if pc == "last-of-type":
            return self._is_nth_last_child(node, 1, of_type=True)

        if pc == "only-of-type":
            if node.parent is None:
                return True
            same_type = [c for c in node.parent.children if c.tag == node.tag]
            return len(same_type) == 1

        if pc in ("link", "any-link"):
            return node.tag in ("a", "area", "link") and \
                   "href" in node.attributes

        if pc == "local-link":
            return False

        if pc == "scope":
            return node.parent is None

        # Unknown pseudo – don't block
        logger.debug("Unknown pseudo-class :%s – treating as match", pc)
        return True

    # ── nth helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_nth_arg(arg: str) -> Tuple[int, int]:
        """
        Parse an ``nth-child`` argument into (step, offset) where the formula
        is ``step*n + offset``.

        Supported formats:
        - ``odd``     → (2, 1)
        - ``even``    → (2, 0)
        - ``3``       → (0, 3)
        - ``2n``      → (2, 0)
        - ``2n+1``    → (2, 1)
        - ``-n+3``    → (-1, 3)
        - ``n``       → (1, 0)
        """
        arg = arg.strip().lower()
        if arg == "odd":
            return 2, 1
        if arg == "even":
            return 2, 0

        # Try plain integer
        if re.fullmatch(r'-?\d+', arg):
            return 0, int(arg)

        # an+b pattern
        m = re.fullmatch(r'(-?\d*n)\s*([+-]\s*\d+)?', arg)
        if not m:
            logger.debug("Cannot parse nth arg: %r", arg)
            return 0, 0

        a_str = m.group(1)   # e.g. "2n", "-n", "n"
        b_str = m.group(2)   # e.g. "+1", "-3", None

        if a_str in ("n", "+n"):
            a = 1
        elif a_str == "-n":
            a = -1
        else:
            a = int(a_str.replace("n", ""))

        b = 0
        if b_str:
            b = int(b_str.replace(" ", ""))

        return a, b

    def _get_child_index(
        self, node: HTMLNode, of_type: bool, reverse: bool = False
    ) -> int:
        """
        Return the 1-based position of *node* among its siblings.

        If *of_type* is True, only siblings with the same tag are counted.
        If *reverse* is True, count from the end.
        """
        if node.parent is None:
            return 1
        siblings = node.parent.children
        if of_type:
            siblings = [c for c in siblings if c.tag == node.tag]
        if reverse:
            siblings = list(reversed(siblings))
        try:
            return siblings.index(node) + 1
        except ValueError:
            return -1

    def _is_nth_child(
        self, node: HTMLNode, position: int, of_type: bool
    ) -> bool:
        return self._get_child_index(node, of_type) == position

    def _is_nth_last_child(
        self, node: HTMLNode, position: int, of_type: bool
    ) -> bool:
        return self._get_child_index(node, of_type, reverse=True) == position

    def _match_nth(
        self, node: HTMLNode, arg: str, of_type: bool
    ) -> bool:
        a, b   = self._parse_nth_arg(arg)
        index  = self._get_child_index(node, of_type)
        if index < 0:
            return False
        return self._nth_matches(a, b, index)

    def _match_nth_last(
        self, node: HTMLNode, arg: str, of_type: bool
    ) -> bool:
        a, b   = self._parse_nth_arg(arg)
        index  = self._get_child_index(node, of_type, reverse=True)
        if index < 0:
            return False
        return self._nth_matches(a, b, index)

    @staticmethod
    def _nth_matches(a: int, b: int, index: int) -> bool:
        """Return True if ``index`` satisfies ``a*n + b`` for some non-negative n."""
        if a == 0:
            return index == b
        n = (index - b)
        if n % a != 0:
            return False
        return n // a >= 0

    # ── Attribute selector matching ───────────────────────────────────────────

    def _match_attribute(self, node: HTMLNode, attr_expr: str) -> bool:
        """
        Match a single attribute selector expression (the content between
        ``[`` and ``]``).

        Supports:
        - ``[attr]``        presence
        - ``[attr=val]``    exact
        - ``[attr~=val]``   word in space-separated list
        - ``[attr|=val]``   exact or starts with ``val-``
        - ``[attr^=val]``   starts with
        - ``[attr$=val]``   ends with
        - ``[attr*=val]``   contains
        """
        m = RE_ATTR_INTERNAL.match(attr_expr.strip())
        if not m:
            logger.debug("Cannot parse attribute expression: %r", attr_expr)
            return False

        attr_name = m.group(1).strip().lower()
        operator  = m.group(2)
        expected  = m.group(3) if m.group(3) else ""

        # Case-insensitive flag  [attr=val i]
        case_insensitive = attr_expr.strip().endswith(" i") or \
                           attr_expr.strip().endswith("\ti")

        actual = node.get_attribute(attr_name)

        # Presence check
        if operator is None:
            return actual is not None

        if actual is None:
            return False

        if case_insensitive:
            actual   = actual.lower()
            expected = expected.lower()

        if operator == "=":
            return actual == expected
        elif operator == "~=":
            return expected in actual.split()
        elif operator == "|=":
            return actual == expected or actual.startswith(expected + "-")
        elif operator == "^=":
            return actual.startswith(expected)
        elif operator == "$=":
            return actual.endswith(expected)
        elif operator == "*=":
            return expected in actual
        else:
            logger.debug("Unknown attribute operator: %r", operator)
            return False


# ──────────────────────────────────────────────────────────────────────────────
# CSS Parser (builds CSSRule instances from Token stream)
# ──────────────────────────────────────────────────────────────────────────────

class CSSParser:
    """
    Converts the flat :class:`Token` stream produced by :class:`CSSLexer` into
    a list of :class:`CSSRule` objects.

    Handles:
    - Group selectors (comma-separated): split into individual rules.
    - ``!important`` declarations.
    - Graceful recovery from malformed input.
    """

    def __init__(self) -> None:
        self._tokens:   List[Token] = []
        self._pos:      int         = 0
        self._rules:    List[CSSRule] = []
        self._order:    int           = 0   # source-order counter

    # ── public ────────────────────────────────────────────────────────────────

    def parse(self, css_text: str) -> List[CSSRule]:
        """
        Parse *css_text* and return a list of :class:`CSSRule` instances.
        """
        lexer         = CSSLexer(css_text)
        self._tokens  = lexer.tokenise()
        self._pos     = 0
        self._rules   = []
        self._order   = 0
        self._parse_rules()
        return self._rules

    # ── private ───────────────────────────────────────────────────────────────

    def _peek(self) -> Token:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return Token(TokenType.EOF, "")

    def _consume(self) -> Token:
        tok      = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect(self, token_type: str) -> Optional[Token]:
        tok = self._peek()
        if tok.type == token_type:
            return self._consume()
        logger.debug(
            "Expected %s but got %s (%r) at line %d",
            token_type, tok.type, tok.value, tok.line
        )
        return None

    def _parse_rules(self) -> None:
        """Main parse loop: consume SELECTOR…CLOSE_BRACE blocks."""
        while self._peek().type != TokenType.EOF:
            tok = self._peek()

            if tok.type == TokenType.AT_RULE:
                # Already handled by the lexer (inner rules yielded inline).
                # Just consume the AT_RULE marker and continue.
                self._consume()
                continue

            if tok.type in (TokenType.CLOSE_BRACE, TokenType.OPEN_BRACE):
                self._consume()
                continue

            if tok.type == TokenType.SELECTOR:
                self._parse_rule_set()
                continue

            # Unknown / stray token – skip
            self._consume()

    def _parse_rule_set(self) -> None:
        """Parse one rule-set: SELECTOR OPEN_BRACE DECL* CLOSE_BRACE."""
        selector_tok = self._consume()   # SELECTOR
        if self._peek().type != TokenType.OPEN_BRACE:
            # Malformed; try to recover by skipping until CLOSE_BRACE or EOF
            logger.debug("Missing '{' after selector %r", selector_tok.value)
            while self._peek().type not in (
                TokenType.CLOSE_BRACE, TokenType.EOF
            ):
                self._consume()
            if self._peek().type == TokenType.CLOSE_BRACE:
                self._consume()
            return

        self._consume()  # OPEN_BRACE

        # Accumulate declarations
        properties: Dict[str, str]  = {}
        important:  Dict[str, bool] = {}

        while self._peek().type not in (TokenType.CLOSE_BRACE, TokenType.EOF):
            if self._peek().type == TokenType.PROPERTY:
                prop_tok = self._consume()   # PROPERTY
                if self._peek().type == TokenType.VALUE:
                    val_tok  = self._consume()   # VALUE
                    name = prop_tok.value.strip().lower()
                    raw_val  = val_tok.value.strip()

                    # Detect !important
                    is_important = bool(RE_IMPORTANT.search(raw_val))
                    clean_val    = RE_IMPORTANT.sub("", raw_val).strip()

                    if name:
                        properties[name] = clean_val
                        if is_important:
                            important[name] = True
                else:
                    logger.debug(
                        "Property %r has no value token", prop_tok.value
                    )
            else:
                self._consume()  # skip stray tokens inside block

        if self._peek().type == TokenType.CLOSE_BRACE:
            self._consume()

        if not properties:
            return

        # Split group selectors into individual CSSRule instances
        raw_selector = selector_tok.value
        groups = SelectorMatcher._split_groups(raw_selector)

        for group in groups:
            group = RE_WHITESPACE_COLLAPSE.sub(" ", group).strip()
            if not group:
                continue
            rule = CSSRule(
                selector     = group,
                properties   = dict(properties),
                important    = dict(important),
                source_order = self._order,
            )
            self._rules.append(rule)
            self._order += 1
            logger.debug("Parsed rule: %r  specificity=%d", group, rule.specificity)


# ──────────────────────────────────────────────────────────────────────────────
# CSS Compiler (Public API)
# ──────────────────────────────────────────────────────────────────────────────

class CSSCompiler:
    """
    High-level CSS Compiler that orchestrates parsing, matching, and cascade
    resolution.

    Usage
    -----
    ::

        compiler = CSSCompiler()
        compiler.parse_stylesheet(raw_css)

        node = HTMLNode(tag="h2", classes=["title"], ...)
        styles = compiler.resolve_styles(node)
        print(styles)  # {"color": "#333", "font-size": "2rem", ...}

    """

    def __init__(self) -> None:
        self.rules:    List[CSSRule]   = []
        self._parser:  CSSParser       = CSSParser()
        self._matcher: SelectorMatcher = SelectorMatcher()

    # ── Parsing ───────────────────────────────────────────────────────────────

    def parse_stylesheet(self, css_text: str) -> None:
        """
        Tokenise and compile *css_text* appending all discovered
        :class:`CSSRule` instances to :attr:`rules`.

        Multiple calls to this method accumulate rules (simulating multiple
        linked stylesheets). Call :meth:`reset` first if you want a clean slate.
        """
        new_rules = self._parser.parse(css_text)
        # Re-index source_order to preserve global ordering across multiple
        # stylesheet loads.
        offset = len(self.rules)
        for i, rule in enumerate(new_rules):
            rule.source_order = offset + i
        self.rules.extend(new_rules)
        logger.info("Parsed %d rules (%d total)", len(new_rules), len(self.rules))

    def reset(self) -> None:
        """Clear all parsed rules."""
        self.rules.clear()

    # ── Selector matching ─────────────────────────────────────────────────────

    def match_element(self, element_node: HTMLNode, selector: str) -> bool:
        """
        Return ``True`` if *element_node* is matched by *selector*.

        Supports the full range of selectors described in
        :class:`SelectorMatcher`.
        """
        return self._matcher.match(element_node, selector)

    # ── Style resolution ──────────────────────────────────────────────────────

    def resolve_styles(
        self,
        element_node:   HTMLNode,
        default_styles: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Compute the fully cascaded style for *element_node*.

        Steps
        -----
        1. Collect every :class:`CSSRule` whose selector matches *element_node*.
        2. Sort matching rules by **(specificity ASC, source_order ASC)** so
           higher-specificity / later-declared rules naturally override earlier,
           lower-specificity rules when we merge left-to-right.
        3. Merge *default_styles* (lowest priority) → matched rules →
           *element_node.inline_style* (highest priority, except ``!important``
           which always wins).
        4. Apply ``!important`` overrides on top.

        Returns
        -------
        Dict[str, str]
            Mapping of CSS property names to their final resolved values.
        """
        resolved:   Dict[str, str]  = {}
        important:  Dict[str, str]  = {}   # property → value for !important decls

        # ── 1. Default / browser stylesheet baseline ─────────────────────────
        if default_styles:
            resolved.update(default_styles)

        # ── 2. Find matching rules & sort ────────────────────────────────────
        matching = [
            rule for rule in self.rules
            if self._matcher.match(element_node, rule.selector)
        ]
        matching.sort(key=lambda r: (r.specificity, r.source_order))

        # ── 3. Merge stylesheet rules ─────────────────────────────────────────
        for rule in matching:
            for prop, value in rule.properties.items():
                is_imp = rule.important.get(prop, False)
                if is_imp:
                    # !important always wins; stash separately and apply last
                    # (later !important still overrides earlier !important)
                    important[prop] = value
                else:
                    resolved[prop] = value

        # ── 4. Inline styles (highest author-level priority) ──────────────────
        if element_node.inline_style:
            for prop, raw_value in element_node.inline_style.items():
                prop      = prop.strip().lower()
                is_imp    = bool(RE_IMPORTANT.search(raw_value))
                clean_val = RE_IMPORTANT.sub("", raw_value).strip()
                if is_imp:
                    important[prop] = clean_val
                else:
                    resolved[prop] = clean_val

        # ── 5. Apply !important overrides ────────────────────────────────────
        resolved.update(important)

        logger.debug(
            "Resolved %d properties for %r (%d matching rules)",
            len(resolved), element_node, len(matching)
        )
        return resolved

    # ── Introspection helpers ─────────────────────────────────────────────────

    def matching_rules(
        self, element_node: HTMLNode
    ) -> List[CSSRule]:
        """
        Return all :class:`CSSRule` instances that match *element_node*,
        sorted by (specificity ASC, source_order ASC).
        """
        matching = [
            rule for rule in self.rules
            if self._matcher.match(element_node, rule.selector)
        ]
        matching.sort(key=lambda r: (r.specificity, r.source_order))
        return matching

    def rules_for_selector(self, selector: str) -> List[CSSRule]:
        """Return all rules whose selector exactly matches *selector*."""
        return [r for r in self.rules if r.selector == selector.strip()]

    def all_properties(self) -> Set[str]:
        """Return the set of every CSS property name seen across all rules."""
        props: Set[str] = set()
        for rule in self.rules:
            props.update(rule.properties.keys())
        return props

    def to_json(self, indent: int = 2) -> str:
        """Serialise the compiled rule set to a JSON string."""
        return json.dumps(
            [r.to_dict() for r in self.rules],
            indent=indent,
            ensure_ascii=False,
        )

    def from_json(self, json_str: str) -> None:
        """
        Restore rules from a JSON string previously produced by :meth:`to_json`.
        Existing rules are **replaced**.
        """
        data = json.loads(json_str)
        self.rules = []
        for d in data:
            rule = CSSRule(
                selector     = d["selector"],
                properties   = d["properties"],
                important    = d.get("important", {}),
                source_order = d.get("source_order", 0),
            )
            self.rules.append(rule)

    def specificity_report(self) -> str:
        """
        Return a human-readable table of every rule and its specificity
        breakdown, sorted by specificity descending.
        """
        sorted_rules = sorted(
            self.rules,
            key=lambda r: (r.specificity, r.source_order),
            reverse=True,
        )
        lines = [
            f"{'Selector':<50} {'IDs':>4} {'Cls':>4} {'Elm':>4} {'Total':>6}",
            "-" * 72,
        ]
        for rule in sorted_rules:
            sv = rule.specificity_vector
            lines.append(
                f"{rule.selector:<50} {sv.ids:>4} {sv.classes:>4} "
                f"{sv.elements:>4} {rule.specificity:>6}"
            )
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"CSSCompiler(rules={len(self.rules)})"


# ──────────────────────────────────────────────────────────────────────────────
# Utility: build a small DOM tree from a Python dict description
# ──────────────────────────────────────────────────────────────────────────────

def build_dom(spec: Dict[str, Any], parent: Optional[HTMLNode] = None) -> HTMLNode:
    """
    Recursively build an :class:`HTMLNode` tree from a nested dictionary.

    Dictionary format
    -----------------
    ::

        {
            "tag": "div",
            "id": "app",
            "classes": ["container"],
            "attrs": {"data-theme": "dark"},
            "inline_style": {"font-size": "16px"},
            "children": [
                {"tag": "h1", "classes": ["title"]},
                ...
            ]
        }

    Parameters
    ----------
    spec   : dict   Node specification.
    parent : HTMLNode | None   Parent node (or None for root).

    Returns
    -------
    HTMLNode
        The constructed node with its full subtree attached.
    """
    attrs: Dict[str, str] = dict(spec.get("attrs", {}))
    node = HTMLNode(
        tag          = spec.get("tag", "div"),
        id           = spec.get("id", ""),
        classes      = list(spec.get("classes", [])),
        attributes   = attrs,
        parent       = parent,
        inline_style = dict(spec.get("inline_style", {})),
    )
    for child_spec in spec.get("children", []):
        child = build_dom(child_spec, parent=node)
        node.children.append(child)
    return node


# ──────────────────────────────────────────────────────────────────────────────
# SELF-TEST  (python css_compiler.py)
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ══════════════════════════════════════════════════════════════════════════
    # 0.  Helper: pretty-print section headers
    # ══════════════════════════════════════════════════════════════════════════
    def banner(text: str) -> None:
        width = 78
        print("\n" + "═" * width)
        print(f"  {text}")
        print("═" * width)

    def sub_banner(text: str) -> None:
        print(f"\n── {text} " + "─" * max(0, 74 - len(text)))

    # ══════════════════════════════════════════════════════════════════════════
    # 1.  Mock Stylesheet
    # ══════════════════════════════════════════════════════════════════════════
    banner("1. RAW CSS STYLESHEET (input)")

    MOCK_CSS = textwrap.dedent("""\
        /* ── Reset ───────────────────────────────────────────────────── */
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        /* ── Base typography ──────────────────────────────────────────── */
        body {
            font-family: 'Inter', sans-serif;
            font-size: 16px;
            line-height: 1.6;
            color: #212121;
            background-color: #fafafa;
        }

        h1, h2, h3, h4, h5, h6 {
            font-weight: 700;
            color: #111;
            margin-bottom: 0.5em;
        }

        h1 { font-size: 2.5rem; }
        h2 { font-size: 2rem;   }
        h3 { font-size: 1.75rem; }

        p {
            color: #444;
            margin-bottom: 1em;
        }

        a {
            color: #1565c0;
            text-decoration: none;
        }

        a:hover {
            text-decoration: underline;
        }

        /* ── Layout ───────────────────────────────────────────────────── */
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 1rem;
        }

        .row {
            display: flex;
            flex-wrap: wrap;
            margin: 0 -0.5rem;
        }

        .col-md-6 {
            flex: 0 0 50%;
            padding: 0 0.5rem;
        }

        .col-md-4 {
            flex: 0 0 33.333%;
            padding: 0 0.5rem;
        }

        /* ── Cards ────────────────────────────────────────────────────── */
        .card {
            background: #fff;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            padding: 1.5rem;
            margin-bottom: 1.5rem;
        }

        .card h2 {
            color: #ff5722;
            font-size: 1.5rem;
        }

        .card p {
            color: #555;
        }

        .card .card-footer {
            border-top: 1px solid #e0e0e0;
            padding-top: 1rem;
            margin-top: 1rem;
        }

        /* ── Buttons ──────────────────────────────────────────────────── */
        .btn {
            display: inline-block;
            padding: 0.5rem 1.25rem;
            border-radius: 4px;
            font-size: 0.875rem;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s ease;
        }

        .btn-primary {
            background-color: #1565c0;
            color: #fff;
        }

        .btn-primary:hover {
            background-color: #0d47a1;
        }

        .btn-secondary {
            background-color: #e0e0e0;
            color: #333;
        }

        /* ── Navigation ───────────────────────────────────────────────── */
        #navbar {
            background-color: #1565c0;
            padding: 1rem 2rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        #navbar a {
            color: #fff;
            font-weight: 500;
            margin-left: 1.5rem;
        }

        #navbar a:hover {
            color: #bbdefb;
        }

        #navbar .logo {
            font-size: 1.25rem;
            font-weight: 700;
            color: #fff;
        }

        /* ── Hero section ─────────────────────────────────────────────── */
        #hero {
            background: linear-gradient(135deg, #1565c0, #283593);
            color: #fff;
            padding: 5rem 2rem;
            text-align: center;
        }

        #hero h1 {
            font-size: 3rem;
            margin-bottom: 1rem;
        }

        #hero p {
            font-size: 1.25rem;
            color: rgba(255,255,255,0.85);
            max-width: 640px;
            margin: 0 auto 2rem;
        }

        /* ── Form elements ────────────────────────────────────────────── */
        input[type="text"],
        input[type="email"],
        textarea {
            width: 100%;
            padding: 0.75rem 1rem;
            border: 1px solid #ccc;
            border-radius: 4px;
            font-size: 1rem;
        }

        input[type="text"]:focus,
        input[type="email"]:focus {
            outline: none;
            border-color: #1565c0;
            box-shadow: 0 0 0 3px rgba(21,101,192,0.2);
        }

        /* ── Search box ───────────────────────────────────────────────── */
        #search-box {
            display: flex;
            align-items: center;
            background: #fff;
            border: 1px solid #ccc;
            border-radius: 24px;
            padding: 0.5rem 1rem;
        }

        #search-box input {
            border: none;
            outline: none;
            flex: 1;
        }

        /* ── Welcome section ──────────────────────────────────────────── */
        #welcome {
            padding: 3rem 2rem;
            text-align: center;
        }

        #welcome h2 {
            color: #1565c0;
            font-size: 2rem;
        }

        #welcome p {
            color: #666;
            max-width: 580px;
            margin: 0 auto;
        }

        /* ── Specificity stress tests ─────────────────────────────────── */
        /* specificity = 1 (just element) */
        p {
            color: black;
        }

        /* specificity = 11 (.card + p) */
        .card p {
            color: green;
        }

        /* specificity = 111 (#welcome + .card + p) */
        #welcome .card p {
            color: blue !important;
        }

        /* ── Media-query-like nested (should be parsed) ───────────────── */
        @media (max-width: 768px) {
            .col-md-6 {
                flex: 0 0 100%;
            }
            .col-md-4 {
                flex: 0 0 100%;
            }
            #navbar {
                flex-direction: column;
            }
        }

        /* ── Attribute-selector rules ─────────────────────────────────── */
        a[href^="https"] {
            color: #2e7d32;
        }

        a[href$=".pdf"] {
            color: #c62828;
        }

        img[alt] {
            border: 2px solid transparent;
        }

        /* ── nth-child / structural ───────────────────────────────────── */
        li:first-child  { font-weight: bold; }
        li:last-child   { font-style: italic; }
        li:nth-child(2) { color: #777; }
        li:nth-child(odd)  { background-color: #f5f5f5; }
        li:nth-child(even) { background-color: #e0e0e0; }

        /* ── :not() pseudo-class ──────────────────────────────────────── */
        a:not(.btn) {
            color: #1565c0;
        }

        /* ── Adjacent & general sibling ───────────────────────────────── */
        h2 + p {
            margin-top: 0.25rem;
        }

        h2 ~ p {
            color: #555;
        }

        /* Malformed rules (should be recovered gracefully) */
        .broken { color }
        .also-broken color: red
    """)

    print(MOCK_CSS[:500], "\n  ... (truncated for display) ...\n")

    # ══════════════════════════════════════════════════════════════════════════
    # 2.  Parse the stylesheet
    # ══════════════════════════════════════════════════════════════════════════
    banner("2. PARSING STYLESHEET")

    compiler = CSSCompiler()
    compiler.parse_stylesheet(MOCK_CSS)

    print(f"\n  Total rules parsed: {len(compiler.rules)}")
    sub_banner("First 10 rules")
    for rule in compiler.rules[:10]:
        print(f"  {rule}")

    # ══════════════════════════════════════════════════════════════════════════
    # 3.  Specificity Report
    # ══════════════════════════════════════════════════════════════════════════
    banner("3. SPECIFICITY REPORT (all rules, sorted by specificity DESC)")
    print()
    print(compiler.specificity_report())

    # ══════════════════════════════════════════════════════════════════════════
    # 4.  Spot-check specificity calculation
    # ══════════════════════════════════════════════════════════════════════════
    banner("4. SPECIFICITY SPOT-CHECKS")

    checks = [
        ("*",                   0),
        ("p",                   1),
        ("body",                1),
        ("h2",                  1),
        ("div",                 1),
        ("body p",              2),
        (".card",               10),
        ("a:hover",             11),    # a(1) + :hover(10)
        (".card p",             11),    # .card(10) + p(1)
        (".btn-primary",        10),
        (".btn-primary:hover",  20),    # .btn-primary(10) + :hover(10)
        ("#navbar",             100),
        ("#navbar a",           101),   # #navbar(100) + a(1)
        ("#welcome",            100),
        ("#welcome h2",         101),
        ("body .card h2",       12),    # body(1) + .card(10) + h2(1)
        ("#welcome .card p",    111),   # #welcome(100)+.card(10)+p(1)
        ("a[href^='https']",    11),    # a(1) + [attr](10)
        ("li:first-child",      11),    # li(1) + :first-child(10)
        ("a:not(.btn)",         11),    # a(1) + specificity of .btn(10)
    ]

    all_ok = True
    for selector, expected in checks:
        dummy_rule = CSSRule(selector=selector, properties={})
        got        = dummy_rule.calculate_specificity()
        status     = "✓" if got == expected else "✗"
        if got != expected:
            all_ok = False
        vec = dummy_rule.specificity_vector
        print(
            f"  {status}  {selector:<35} "
            f"IDs={vec.ids} Cls={vec.classes} Elm={vec.elements}  "
            f"= {got:>4}  (expected {expected})"
        )

    if all_ok:
        print("\n  ✅ All specificity checks passed!")
    else:
        print("\n  ⚠ Some specificity checks failed – review logic above.")

    # ══════════════════════════════════════════════════════════════════════════
    # 5.  Build a mock DOM tree
    # ══════════════════════════════════════════════════════════════════════════
    banner("5. MOCK DOM TREE")

    dom_spec = {
        "tag": "body",
        "children": [
            {
                "tag": "nav",
                "id":  "navbar",
                "children": [
                    {"tag": "span", "classes": ["logo"]},
                    {"tag": "a", "attrs": {"href": "https://example.com"},
                     "children": []},
                    {"tag": "a", "attrs": {"href": "/about"}},
                ]
            },
            {
                "tag": "section",
                "id":  "hero",
                "children": [
                    {"tag": "h1"},
                    {"tag": "p"},
                    {"tag": "a", "classes": ["btn", "btn-primary"],
                     "attrs": {"href": "https://start.com"}},
                ]
            },
            {
                "tag": "section",
                "id":  "welcome",
                "classes": ["container"],
                "children": [
                    {"tag": "h2"},
                    {"tag": "p"},
                    {
                        "tag": "div",
                        "classes": ["card"],
                        "children": [
                            {"tag": "h2"},
                            {"tag": "p",
                             "inline_style": {"color": "purple",
                                              "font-style": "italic"}},
                            {
                                "tag": "div",
                                "classes": ["card-footer"],
                                "children": [
                                    {"tag": "a", "classes": ["btn", "btn-secondary"],
                                     "attrs": {"href": "/read-more"}},
                                ]
                            }
                        ]
                    },
                ]
            },
            {
                "tag": "section",
                "classes": ["container"],
                "children": [
                    {
                        "tag": "div",
                        "classes": ["row"],
                        "children": [
                            {
                                "tag": "div",
                                "classes": ["col-md-6", "card"],
                                "children": [
                                    {"tag": "h2"},
                                    {"tag": "p"},
                                ]
                            },
                            {
                                "tag": "div",
                                "classes": ["col-md-6", "card"],
                                "children": [
                                    {"tag": "h2"},
                                    {"tag": "p"},
                                ]
                            },
                        ]
                    }
                ]
            },
            {
                "tag": "ul",
                "children": [
                    {"tag": "li"},   # 1st
                    {"tag": "li"},   # 2nd
                    {"tag": "li"},   # 3rd
                    {"tag": "li"},   # 4th (last)
                ]
            },
            {
                "tag": "div",
                "id": "search-box",
                "children": [
                    {"tag": "input",
                     "attrs": {"type": "text", "placeholder": "Search…"}}
                ]
            },
        ]
    }

    root = build_dom(dom_spec)

    def print_tree(node: HTMLNode, indent: int = 0) -> None:
        prefix = "  " * indent + "└─ "
        print(f"{prefix}{node!r}")
        for child in node.children:
            print_tree(child, indent + 1)

    print_tree(root)

    # ══════════════════════════════════════════════════════════════════════════
    # 6.  Selector matching tests
    # ══════════════════════════════════════════════════════════════════════════
    banner("6. SELECTOR MATCHING TESTS")

    def find_nodes(node: HTMLNode, tag: str = "", id_: str = "",
                   cls: str = "") -> List[HTMLNode]:
        """BFS collector."""
        results = []
        stack   = [node]
        while stack:
            n = stack.pop(0)
            match_tag = (not tag) or (n.tag == tag)
            match_id  = (not id_) or (n.id == id_)
            match_cls = (not cls) or (n.has_class(cls))
            if match_tag and match_id and match_cls:
                results.append(n)
            stack.extend(n.children)
        return results

    navbar_node    = find_nodes(root, id_="navbar")[0]
    hero_node      = find_nodes(root, id_="hero")[0]
    hero_h1        = find_nodes(hero_node, tag="h1")[0]
    hero_p         = find_nodes(hero_node, tag="p")[0]
    welcome_node   = find_nodes(root, id_="welcome")[0]
    welcome_h2     = find_nodes(welcome_node, tag="h2")[0]
    welcome_p      = find_nodes(welcome_node, tag="p")[0]
    card_node      = find_nodes(welcome_node, cls="card")[0]
    card_h2        = find_nodes(card_node, tag="h2")[0]
    card_p         = find_nodes(card_node, tag="p")[0]
    card_footer    = find_nodes(card_node, cls="card-footer")[0]
    btn_primary    = find_nodes(hero_node, cls="btn-primary")[0]
    logo_node      = find_nodes(navbar_node, cls="logo")[0]
    navbar_link    = find_nodes(navbar_node, tag="a")[0]
    search_box     = find_nodes(root, id_="search-box")[0]
    search_input   = find_nodes(search_box, tag="input")[0]
    ul_node        = find_nodes(root, tag="ul")[0]
    li_nodes       = find_nodes(ul_node, tag="li")

    match_tests = [
        # (element,          selector,                    expected)
        (root,              "body",                        True),
        (root,              "*",                           True),
        (navbar_node,       "#navbar",                     True),
        (navbar_node,       "nav#navbar",                  True),
        (navbar_node,       ".container",                  False),
        (logo_node,         "#navbar .logo",               True),
        (logo_node,         ".logo",                       True),
        (navbar_link,       "#navbar a",                   True),
        (navbar_link,       "a[href^='https']",            True),
        (hero_node,         "#hero",                       True),
        (hero_h1,           "#hero h1",                    True),
        (hero_h1,           "h1",                          True),
        (hero_p,            "#hero p",                     True),
        (hero_p,            "p",                           True),
        (btn_primary,       ".btn",                        True),
        (btn_primary,       ".btn-primary",                True),
        (btn_primary,       "a.btn-primary",               True),
        (btn_primary,       ".btn-secondary",              False),
        (btn_primary,       "a:not(.btn)",                 False),
        (welcome_node,      "#welcome",                    True),
        (welcome_h2,        "#welcome h2",                 True),
        (welcome_h2,        ".card h2",                    False),  # not inside .card
        (welcome_p,         "#welcome p",                  True),
        (card_node,         ".card",                       True),
        (card_h2,           ".card h2",                    True),
        (card_h2,           "#welcome .card h2",           True),
        (card_h2,           "body .card h2",               True),
        (card_p,            ".card p",                     True),
        (card_p,            "#welcome .card p",            True),
        (card_footer,       ".card .card-footer",          True),
        (card_footer,       ".card > .card-footer",        True),
        (card_footer,       "body > .card-footer",         False),  # not direct child
        (search_box,        "#search-box",                 True),
        (search_input,      "#search-box input",           True),
        (search_input,      "input[type='text']",          True),
        (search_input,      "input[type='email']",         False),
        (li_nodes[0],       "li:first-child",              True),
        (li_nodes[0],       "li:last-child",               False),
        (li_nodes[-1],      "li:last-child",               True),
        (li_nodes[-1],      "li:first-child",              False),
        (li_nodes[1],       "li:nth-child(2)",             True),
        (li_nodes[0],       "li:nth-child(odd)",           True),
        (li_nodes[1],       "li:nth-child(even)",          True),
        (li_nodes[2],       "li:nth-child(even)",          False),
        (ul_node,           "li:first-child",              False),  # ul ≠ li
    ]

    sub_banner("Running match tests")
    passed  = 0
    failed  = 0
    for element, selector, expected in match_tests:
        result  = compiler.match_element(element, selector)
        status  = "✓" if result == expected else "✗"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(
            f"  {status}  {str(element):<35} | "
            f"{selector:<35} → {result}  (expected {expected})"
        )

    print(f"\n  Results: {passed} passed, {failed} failed out of {len(match_tests)} tests.")

    # ══════════════════════════════════════════════════════════════════════════
    # 7.  Cascade & style resolution demo
    # ══════════════════════════════════════════════════════════════════════════
    banner("7. CASCADE & STYLE RESOLUTION")

    # ── 7a. Resolve styles for <p> inside #welcome > .card
    sub_banner("7a. card_p (#welcome .card p) – cascaded with !important")
    browser_defaults = {
        "display":     "block",
        "font-family": "serif",
        "color":       "black",
    }
    card_p_styles = compiler.resolve_styles(card_p, default_styles=browser_defaults)

    print(f"\n  Element : {card_p!r}")
    print(f"  Inline  : {card_p.inline_style}")
    print("\n  Matching rules (sorted by specificity ASC):")
    for r in compiler.matching_rules(card_p):
        imp_props = {k for k, v in r.important.items() if v}
        print(
            f"    [{r.specificity:>4}] {r.selector!r:<40} "
            f"props={list(r.properties.keys())}  !important={imp_props}"
        )

    print("\n  Resolved style:")
    for prop, val in sorted(card_p_styles.items()):
        print(f"    {prop}: {val};")

    # Verify: color should be "blue" (from #welcome .card p with !important)
    # but the inline style sets it to "purple" and inline beats !important
    # unless... actually inline style is author-level; user-agent !important
    # beats everything, but author !important beats author normal.
    # Our inline style below is normal (no !important), so stylesheet
    # !important (#welcome .card p { color: blue !important }) wins the cascade,
    # then inline overrides everything except !important.
    # So final color = purple (inline) because inline is the last merge step
    # BEFORE we apply !important. Wait – per our algorithm:
    # 1. resolved = default_styles
    # 2. for each matching rule (asc specificity): resolved[prop] = value
    #    (if !important → stash in `important` dict)
    # 3. resolved.update(inline_style)   ← inline overwrites cascade
    # 4. resolved.update(important)      ← !important always wins last
    # So: #welcome .card p sets color=blue !important → important["color"]="blue"
    #     inline sets color=purple (normal)
    # After step 4: color = "blue" (because !important overrides inline normal)
    expected_color = "blue"
    actual_color   = card_p_styles.get("color")
    icon = "✓" if actual_color == expected_color else "✗"
    print(
        f"\n  {icon} color = {actual_color!r}  "
        f"(expected {expected_color!r} — !important overrides inline normal)"
    )

    # ── 7b. Resolve styles for hero <h1>
    sub_banner("7b. hero_h1 (#hero h1)")
    hero_h1_styles = compiler.resolve_styles(hero_h1)

    print(f"\n  Element : {hero_h1!r}")
    print("\n  Matching rules:")
    for r in compiler.matching_rules(hero_h1):
        print(f"    [{r.specificity:>4}] {r.selector!r}")

    print("\n  Resolved style:")
    for prop, val in sorted(hero_h1_styles.items()):
        print(f"    {prop}: {val};")

    assert hero_h1_styles.get("font-size") == "3rem", \
        f"Expected 3rem, got {hero_h1_styles.get('font-size')}"
    print("  ✓ font-size = '3rem' (overridden by #hero h1 over h1 rule)")

    # ── 7c. Resolve styles for .btn-primary
    sub_banner("7c. btn_primary (.btn .btn-primary <a>)")
    btn_styles = compiler.resolve_styles(btn_primary)

    print(f"\n  Element : {btn_primary!r}")
    print("\n  Matching rules:")
    for r in compiler.matching_rules(btn_primary):
        print(f"    [{r.specificity:>4}] {r.selector!r}")

    print("\n  Resolved style:")
    for prop, val in sorted(btn_styles.items()):
        print(f"    {prop}: {val};")

    assert btn_styles.get("background-color") == "#1565c0", \
        f"Expected #1565c0, got {btn_styles.get('background-color')}"
    assert btn_styles.get("color") == "#fff", \
        f"Expected #fff, got {btn_styles.get('color')}"
    print("  ✓ background-color = '#1565c0'  (from .btn-primary)")
    print("  ✓ color = '#fff'  (from .btn-primary)")

    # ── 7d. Specificity cascade: p element colour
    sub_banner("7d. Specificity cascade for standalone <p> vs .card p vs #welcome .card p")
    standalone_p = HTMLNode(tag="p")                         # no ancestors
    outer_p      = welcome_p                                 # inside #welcome, not .card
    inner_p      = card_p                                    # inside #welcome > .card

    for label, p_node in [
        ("standalone <p>       (no ancestors)", standalone_p),
        ("#welcome > <p>       (not in .card)", outer_p),
        ("#welcome > .card > p (in card)",      inner_p),
    ]:
        resolved = compiler.resolve_styles(p_node)
        print(f"\n  {label}")
        print(f"    color = {resolved.get('color')!r}")

    # ══════════════════════════════════════════════════════════════════════════
    # 8.  JSON serialisation round-trip
    # ══════════════════════════════════════════════════════════════════════════
    banner("8. JSON SERIALISATION ROUND-TRIP")

    json_output   = compiler.to_json(indent=2)
    print(f"  Serialised {len(compiler.rules)} rules ({len(json_output)} bytes).")
    print("  First 600 chars of JSON:\n")
    print(json_output[:600])
    print("  ...")

    # Round-trip: restore from JSON into a fresh compiler
    compiler2 = CSSCompiler()
    compiler2.from_json(json_output)
    print(f"\n  Restored compiler has {len(compiler2.rules)} rules.")
    assert len(compiler2.rules) == len(compiler.rules), "Round-trip rule count mismatch!"
    print("  ✓ Rule count matches — round-trip successful.")

    # Verify a rule from compiler2 still resolves the same styles
    card_p_styles2 = compiler2.resolve_styles(card_p, default_styles=browser_defaults)
    assert card_p_styles2.get("color") == "blue", \
        f"Round-trip color mismatch: {card_p_styles2.get('color')}"
    print("  ✓ Resolved styles match after round-trip.")

    # ══════════════════════════════════════════════════════════════════════════
    # 9.  Edge-case stress tests
    # ══════════════════════════════════════════════════════════════════════════
    banner("9. EDGE-CASE & STRESS TESTS")

    sub_banner("9a. Malformed CSS recovery")
    bad_css = """
        /* unclosed comment
        .no-close { color: red;
        .valid { margin: 0; padding: 0; }
        #empty { }
        .missing-value { font-size: }
        { orphan-block: yes; }
        trailing garbage
    """
    ec = CSSCompiler()
    ec.parse_stylesheet(bad_css)
    print(f"  Rules recovered from malformed CSS: {len(ec.rules)}")
    for r in ec.rules:
        print(f"    {r}")

    sub_banner("9b. Deeply nested descendant selector")
    deep_css = """
        body section .container .row .col-md-6 .card h2 {
            color: deeppink;
            font-size: 1.1rem;
        }
    """
    dc = CSSCompiler()
    dc.parse_stylesheet(deep_css)
    assert len(dc.rules) == 1
    r = dc.rules[0]
    # body(1) section(1) .container(10) .row(10) .col-md-6(10) .card(10) h2(1) = 43
    print(f"  Selector : {r.selector!r}")
    print(f"  Specificity vector : {r.specificity_vector}")
    print(f"  Specificity (scalar): {r.specificity}  (expected 43)")
    assert r.specificity == 43, f"Got {r.specificity}"
    print("  ✓ Deep descendant specificity correct.")

    sub_banner("9c. :not() specificity (takes specificity of argument)")
    not_css = "a:not(#special) { color: teal; }"
    nc = CSSCompiler()
    nc.parse_stylesheet(not_css)
    # a(1) + specificity_of(#special)(100) = 101
    assert nc.rules[0].specificity == 101, \
        f"Expected 101 got {nc.rules[0].specificity}"
    print(f"  a:not(#special) specificity = {nc.rules[0].specificity}  ✓")

    sub_banner("9d. Group selector expansion")
    grp_css = "h1, h2, h3 { font-weight: bold; margin: 0; }"
    gc = CSSCompiler()
    gc.parse_stylesheet(grp_css)
    assert len(gc.rules) == 3, f"Expected 3 rules, got {len(gc.rules)}"
    selectors = [r.selector for r in gc.rules]
    print(f"  Expanded rules: {selectors}")
    assert "h1" in selectors and "h2" in selectors and "h3" in selectors
    print("  ✓ Group selector split into 3 individual rules.")

    sub_banner("9e. @media inner rules are parsed")
    media_css = """
        @media (max-width: 768px) {
            .sidebar { display: none; }
            .main    { flex: 0 0 100%; }
        }
    """
    mc = CSSCompiler()
    mc.parse_stylesheet(media_css)
    print(f"  Rules inside @media: {len(mc.rules)}")
    for r in mc.rules:
        print(f"    {r}")
    assert any(r.selector == ".sidebar" for r in mc.rules)
    print("  ✓ @media inner rules parsed successfully.")

    sub_banner("9f. Attribute selector matching (all operators)")
    attr_node = HTMLNode(
        tag="a",
        attributes={
            "href":     "https://example.com/doc.pdf",
            "lang":     "en-US",
            "class":    "link external",
            "data-id":  "42",
            "title":    "Hello World",
        }
    )
    attr_node.classes = ["link", "external"]

    attr_tests = [
        ("[href]",                True),
        ("[href='https://example.com/doc.pdf']", True),
        ("[href^='https']",       True),
        ("[href$='.pdf']",        True),
        ("[href*='example']",     True),
        ("[lang|='en']",          True),
        ("[class~='external']",   True),
        ("[class~='internal']",   False),
        ("[data-id]",             True),
        ("[title*='World']",      True),
        ("[nonexistent]",         False),
    ]

    for expr, exp in attr_tests:
        got = compiler.match_element(attr_node, f"a{expr}")
        icon = "✓" if got == exp else "✗"
        print(f"  {icon}  a{expr:<40} → {got}  (expected {exp})")

    sub_banner("9g. Sibling combinators")
    # Build:  div > h2 + p ~ p
    sib_parent = HTMLNode(tag="div")
    sib_h2     = HTMLNode(tag="h2")
    sib_p1     = HTMLNode(tag="p")
    sib_p2     = HTMLNode(tag="p")
    sib_parent.add_child(sib_h2)
    sib_parent.add_child(sib_p1)
    sib_parent.add_child(sib_p2)

    print(f"  h2 + p  matches sib_p1? {compiler.match_element(sib_p1, 'h2 + p')} (expected True)")
    print(f"  h2 + p  matches sib_p2? {compiler.match_element(sib_p2, 'h2 + p')} (expected False)")
    print(f"  h2 ~ p  matches sib_p1? {compiler.match_element(sib_p1, 'h2 ~ p')} (expected True)")
    print(f"  h2 ~ p  matches sib_p2? {compiler.match_element(sib_p2, 'h2 ~ p')} (expected True)")
    print(f"  div > h2 matches sib_h2? {compiler.match_element(sib_h2, 'div > h2')} (expected True)")
    print(f"  body > h2 matches sib_h2? {compiler.match_element(sib_h2, 'body > h2')} (expected False)")

    assert compiler.match_element(sib_p1, "h2 + p")  is True
    assert compiler.match_element(sib_p2, "h2 + p")  is False
    assert compiler.match_element(sib_p1, "h2 ~ p")  is True
    assert compiler.match_element(sib_p2, "h2 ~ p")  is True
    assert compiler.match_element(sib_h2, "div > h2") is True
    assert compiler.match_element(sib_h2, "body > h2") is False
    print("  ✓ All sibling combinator tests passed.")

    sub_banner("9h. :nth-child edge cases")
    parent = HTMLNode(tag="ul")
    items  = [HTMLNode(tag="li") for _ in range(10)]
    for item in items:
        parent.add_child(item)

    nth_tests = [
        (items[0],  "li:nth-child(1)",     True),
        (items[1],  "li:nth-child(2)",     True),
        (items[2],  "li:nth-child(3)",     True),
        (items[0],  "li:nth-child(odd)",   True),
        (items[1],  "li:nth-child(even)",  True),
        (items[2],  "li:nth-child(odd)",   True),
        (items[3],  "li:nth-child(even)",  True),
        (items[0],  "li:nth-child(2n+1)",  True),
        (items[1],  "li:nth-child(2n)",    True),
        (items[4],  "li:nth-child(3n+2)",  False),  # 5th: 3*1+2=5 → True!
        (items[4],  "li:nth-child(5)",     True),
        (items[9],  "li:last-child",       True),
        (items[0],  "li:first-child",      True),
    ]
    # fix the one test above: 5th item is index 4 → position 5; 3n+2: n=1→5 ✓
    nth_tests[9] = (items[4], "li:nth-child(3n+2)", True)

    all_nth_ok = True
    for item, sel, exp in nth_tests:
        got = compiler.match_element(item, sel)
        ok  = got == exp
        if not ok:
            all_nth_ok = False
        icon = "✓" if ok else "✗"
        pos  = items.index(item) + 1
        print(f"  {icon}  li[pos={pos}] {sel:<30} → {got}  (expected {exp})")

    print("  ✓ All :nth-child tests passed." if all_nth_ok else "  ⚠ Some nth tests failed.")

    # ══════════════════════════════════════════════════════════════════════════
    # 10.  Performance benchmark (parse a large synthesised stylesheet)
    # ══════════════════════════════════════════════════════════════════════════
    banner("10. PERFORMANCE BENCHMARK")

    import time

    def generate_large_stylesheet(n_rules: int = 1000) -> str:
        """Generate a synthetic stylesheet with n_rules rules."""
        lines: List[str] = []
        tags    = ["div", "p", "span", "section", "article", "header",
                   "footer", "main", "aside", "nav"]
        classes = [f"cls-{i}" for i in range(50)]
        ids_    = [f"id-{i}" for i in range(20)]
        props   = [
            ("color",            "#333"),
            ("background-color", "#fff"),
            ("font-size",        "1rem"),
            ("margin",           "0"),
            ("padding",          "0.5rem"),
            ("border-radius",    "4px"),
            ("display",          "block"),
            ("flex",             "1"),
            ("width",            "100%"),
            ("height",           "auto"),
        ]
        import random
        random.seed(42)
        for i in range(n_rules):
            r = random.random()
            if r < 0.2:
                sel = f"#{ids_[i % len(ids_)]} .{classes[i % len(classes)]}"
            elif r < 0.5:
                sel = f".{classes[i % len(classes)]} {tags[i % len(tags)]}"
            elif r < 0.7:
                tag = tags[i % len(tags)]
                cls = classes[(i * 3) % len(classes)]
                sel = f"{tag}.{cls}"
            else:
                sel = f".{classes[i % len(classes)]}"
            prop_name, prop_val = props[i % len(props)]
            lines.append(f"{sel} {{ {prop_name}: {prop_val}; }}")
        return "\n".join(lines)

    BENCH_RULES = 2000
    large_css   = generate_large_stylesheet(BENCH_RULES)

    t0 = time.perf_counter()
    bench_compiler = CSSCompiler()
    bench_compiler.parse_stylesheet(large_css)
    t1 = time.perf_counter()

    parse_ms = (t1 - t0) * 1000
    print(f"  Parsed  {len(bench_compiler.rules):>5} rules from {BENCH_RULES} raw rules "
          f"in {parse_ms:.2f} ms")

    # Create a representative element and time matching + resolution
    bench_node = HTMLNode(
        tag="div",
        id="id-5",
        classes=["cls-3", "cls-7", "cls-15"],
        attributes={"id": "id-5", "class": "cls-3 cls-7 cls-15"},
    )

    t2 = time.perf_counter()
    for _ in range(50):
        bench_compiler.resolve_styles(bench_node)
    t3 = time.perf_counter()

    resolve_ms = (t3 - t2) / 50 * 1000
    matching_n = len(bench_compiler.matching_rules(bench_node))
    print(f"  Matched {matching_n:>5} rules for test element")
    print(f"  Resolved styles in {resolve_ms:.3f} ms (avg over 50 runs)")
    print(f"  Throughput: ~{1000 / resolve_ms:.0f} resolutions/sec")

    # ══════════════════════════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════════════════════════
    banner("✅  ALL TESTS COMPLETE")
    print(textwrap.dedent(f"""
        Summary
        -------
        • Stylesheet compiled     : {len(compiler.rules)} rules
        • Selector match tests    : {passed}/{len(match_tests)} passed
        • Specificity checks      : {sum(1 for s,e in checks if CSSRule(s,{}).specificity==e)}/{len(checks)} passed
        • JSON round-trip         : ✓
        • Edge-cases              : malformed CSS, deep selectors, @media,
                                    attribute operators, sibling combinators,
                                    :nth-child, :not(), group selectors
        • Benchmark               : {len(bench_compiler.rules)} rules parsed in {parse_ms:.1f} ms,
                                    resolve in {resolve_ms:.3f} ms avg
    """))