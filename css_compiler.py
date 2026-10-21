"""
css_compiler.py: compatibility surface for the legacy CSS helper.

The cascade moved to `core.style` (tokenizer, selector engine with real
specificity, inheritance, shorthand expansion, @media support). This module
keeps the old `CSSRule` / `CSSCompiler` API, but backed by that engine instead
of the regex `([^{]+)\\{([^}]+)\\}` scan that dropped every rule after an
@media block.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from core.style import parse_selector, parse_stylesheet

__all__ = ["CSSRule", "CSSCompiler"]


class CSSRule:
    def __init__(self, selector: str, properties: Dict[str, str],
                 source_order: int = 0) -> None:
        self.selector = selector.strip()
        self.properties = properties
        self.source_order = source_order
        self.specificity = self.calculate_specificity(self.selector)

    @staticmethod
    def calculate_specificity(selector: str) -> int:
        parsed = parse_selector(selector)
        if parsed is None:
            return 0
        a, b, c = parsed.specificity
        return a * 100 + b * 10 + c


class CSSCompiler:
    def __init__(self) -> None:
        self.rules: List[CSSRule] = []

    def parse_stylesheet(self, css_text: str) -> None:
        sheet = parse_stylesheet(css_text or "")
        for index, rule in enumerate(sheet.rules):
            if rule.selector is None:
                continue
            self.rules.append(CSSRule(rule.selector.text, dict(rule.declarations),
                                      rule.order or index))

    def match_element(self, node, selector: str) -> bool:
        parsed = parse_selector(selector)
        return bool(parsed and parsed.matches(node))

    def _match_simple(self, node, selector: str) -> bool:
        return self.match_element(node, selector)

    def resolve_styles(self, node,
                       default_styles: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        result = dict(default_styles or {})
        for rule in sorted(self.rules, key=lambda r: (r.specificity, r.source_order)):
            if self.match_element(node, rule.selector):
                result.update(rule.properties)
        return result
