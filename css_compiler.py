import re
from typing import Dict, List, Tuple, Optional, Any

class CSSRule:
    def __init__(self, selector: str, properties: Dict[str, str], source_order: int = 0):
        self.selector = selector.strip()
        self.properties = properties
        self.source_order = source_order
        self.specificity = self.calculate_specificity(self.selector)

    def calculate_specificity(self, selector: str) -> int:
        # Calculates W3C selector specificity weights: ID=100, Class=10, Tag=1
        weight = 0
        parts = selector.split()
        for p in parts:
            if p.startswith("#"):
                weight += 100
            elif p.startswith("."):
                weight += 10
            elif p != "*":
                weight += 1
        return weight

class CSSCompiler:
    def __init__(self):
        self.rules: List[CSSRule] = []

    def parse_stylesheet(self, css_text: str):
        # Clean up CSS comments /* ... */
        css_clean = re.sub(r'/\*.*?\*/', '', css_text, flags=re.DOTALL)
        
        # Parse selectors and blocks
        pattern = re.compile(r'([^{]+)\{([^}]+)\}')
        source_order = len(self.rules)
        
        for match in pattern.finditer(css_clean):
            selector_raw = match.group(1).strip()
            block = match.group(2).strip()
            
            # Parse property key-value declarations
            properties = {}
            for decl in block.split(";"):
                decl = decl.strip()
                if not decl or ":" not in decl:
                    continue
                p, v = decl.split(":", 1)
                properties[p.strip().lower()] = v.strip()
                
            # Split grouped selectors (comma-separated)
            for sel in selector_raw.split(","):
                sel = sel.strip()
                if sel:
                    self.rules.append(CSSRule(sel, properties, source_order))
                    source_order += 1

    def match_element(self, node, selector: str) -> bool:
        # Resolves descendant selectors (e.g. body .card h2) right-to-left
        parts = selector.strip().split()
        if not parts:
            return False
            
        if len(parts) == 1:
            return self._match_simple(node, parts[0])
            
        # Descendant selector: body .card h2
        if not self._match_simple(node, parts[-1]):
            return False
            
        curr = node.parent
        part_idx = len(parts) - 2
        while curr and part_idx >= 0:
            if self._match_simple(curr, parts[part_idx]):
                part_idx -= 1
            curr = curr.parent
        return part_idx < 0

    def _match_simple(self, node, selector: str) -> bool:
        selector = selector.strip()
        if not selector or not node:
            return False
        if selector == "*":
            return True
        if selector.startswith("#"):
            return node.id == selector[1:]
        if selector.startswith("."):
            return selector[1:] in node.classes
        return node.tag == selector.lower()

    def resolve_styles(self, node, default_styles: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        resolved = {}
        if default_styles:
            resolved.update(default_styles)
            
        # Find all matching stylesheet rules
        matching = [r for r in self.rules if self.match_element(node, r.selector)]
        
        # Sort by specificity ascending and source order ascending
        matching.sort(key=lambda r: (r.specificity, r.source_order))
        
        # Merge sorted rules
        for rule in matching:
            resolved.update(rule.properties)
            
        # Merge inline style attribute on top (highest priority)
        inline_style_str = node.attributes.get("style", "")
        if inline_style_str:
            inline_styles = {}
            for decl in inline_style_str.split(";"):
                decl = decl.strip()
                if not decl or ":" not in decl:
                    continue
                p, v = decl.split(":", 1)
                inline_styles[p.strip().lower()] = v.strip()
            resolved.update(inline_styles)
            
        return resolved

if __name__ == "__main__":
    print("Testing custom standalone CSS compiler...")
    compiler = CSSCompiler()
    compiler.parse_stylesheet("body { color: #333; } .card p { color: green; } #welcome { color: red; }")
    
    class MockNode:
        def __init__(self, tag, id_val=None, classes=None, parent=None, style=None):
            self.tag = tag
            self.parent = parent
            self.attributes = {"id": id_val, "class": " ".join(classes or []), "style": style or ""}
            self.id = id_val
            self.classes = classes or []
            
    body = MockNode("body")
    card = MockNode("div", classes=["card"], parent=body)
    welcome = MockNode("p", id_val="welcome", classes=["text"], parent=card, style="font-weight: bold;")
    
    styles = compiler.resolve_styles(welcome, {"font-size": "16px"})
    print("Resolved Styles:", styles)
