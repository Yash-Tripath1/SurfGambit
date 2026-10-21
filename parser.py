"""
parser.py — compatibility surface for the v1 HTML/CSS parser API.

The implementation now lives in `core.dom` (tokenizer + tree builder) and
`core.style` (CSS parsing and the cascade). This module keeps the old names
working so anything that imported `parser.HTMLParser` or `parser.CSSParser`
continues to run, and so there is exactly one parser in the tree instead of
two that disagree.
"""

from __future__ import annotations

import html as _html
from typing import Dict, List, Optional, Tuple

from core.dom import HTMLNode, TEXT_TAG, parse as _parse, parse_fragment
from core.dom.tokenizer import Tokenizer
from core.style import Rule, Stylesheet, parse_selector, parse_stylesheet
from core.style.cascade import compute_styles

__all__ = [
    "HTMLNode", "TEXT_TAG", "parse_fragment", "HTMLParser", "CSSParser", "parse_tag_content",
    "decode_html_entities", "get_style_sheets", "matches_selector",
    "matches_selector_simple", "resolve_styles",
]


class HTMLParser:
    """Drop-in replacement for the v1 parser (same constructor/parse shape)."""

    def __init__(self, html_text: str) -> None:
        self.html_text = html_text or ""

    def parse(self) -> HTMLNode:
        document = _parse(self.html_text)
        # v1 returned a synthetic root tagged "html" whose only element child
        # was the real <html>. Keep that shape so existing callers, which
        # index children[0], keep working.
        root = HTMLNode("html")
        root.append(document)
        return root


def decode_html_entities(text: str) -> str:
    return _html.unescape(text or "")


def parse_tag_content(content: str) -> Tuple[str, Dict[str, str]]:
    """Split `div class="a" id=b` into ('div', {'class': 'a', 'id': 'b'})."""
    tokens = Tokenizer("<" + content + ">").tokenize()
    for token in tokens:
        if token.kind == "start":
            return token.name, dict(token.attributes)
    return "", {}


class CSSParser:
    @staticmethod
    def parse(css_text: str) -> List[Tuple[str, Dict[str, str]]]:
        sheet = parse_stylesheet(css_text or "")
        return [(rule.selector.text if rule.selector else "", rule.declarations)
                for rule in sheet.rules]


def get_style_sheets(node: HTMLNode,
                     rules: Optional[List[Tuple[str, Dict[str, str]]]] = None
                     ) -> List[Tuple[str, Dict[str, str]]]:
    """Collect every <style> block under `node` as (selector, declarations)."""
    collected: List[Tuple[str, Dict[str, str]]] = list(rules or [])
    for element in node.walk():
        if element.tag == "style":
            collected.extend(CSSParser.parse(element.text_content()))
    return collected


def _sheet_from_pairs(pairs: List[Tuple[str, Dict[str, str]]]) -> Stylesheet:
    rules: List[Rule] = []
    order = 0
    for selector_text, declarations in pairs:
        parsed = parse_selector(selector_text)
        if parsed is None:
            continue
        rules.append(Rule(parsed, dict(declarations), order))
        order += 1
    return Stylesheet(rules)


def resolve_styles(node: HTMLNode, rules: List[Tuple[str, Dict[str, str]]],
                   parent_style: Optional[Dict[str, str]] = None,
                   viewport_width: int = 1024) -> None:
    """Run the cascade over `node`'s subtree (styles land in node.style)."""
    compute_styles(node, [_sheet_from_pairs(rules or [])],
                   viewport_width=viewport_width)


def matches_selector_simple(node: HTMLNode, selector: str) -> bool:
    parsed = parse_selector(selector)
    return bool(parsed and parsed.matches(node))


def matches_selector(node: HTMLNode, selector: str) -> bool:
    parsed = parse_selector(selector)
    return bool(parsed and parsed.matches(node))
