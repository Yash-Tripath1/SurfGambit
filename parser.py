import re
from typing import Dict, List, Tuple, Optional

class HTMLNode:
    def __init__(self, tag: str, attributes: Optional[Dict[str, str]] = None, parent: Optional['HTMLNode'] = None, text: str = ""):
        self.tag = tag.lower()
        self.attributes = attributes or {}
        self.parent = parent
        self.text = text
        self.children: List['HTMLNode'] = []
        self.style: Dict[str, str] = {} 

    def is_text(self) -> bool:
        return self.tag == "text"

    def __repr__(self) -> str:
        if self.is_text():
            return f"TextNode({repr(self.text[:25])})"
        return f"HTMLNode(<{self.tag}>, children={len(self.children)})"

    def dump(self, indent: int = 0) -> str:
        prefix = "  " * indent
        if self.is_text():
            res = f"{prefix}Text: {repr(self.text.strip())}\n"
        else:
            attrs = " ".join(f'{k}="{v}"' for k, v in self.attributes.items())
            res = f"{prefix}<{self.tag}{' ' + attrs if attrs else ''}>\n"
            for c in self.children:
                res += c.dump(indent + 1)
        return res

class CSSParser:
    @staticmethod
    def parse(css_text: str) -> List[Tuple[str, Dict[str, str]]]:
        # Remove CSS comments /* ... */
        css_clean = re.sub(r'/\*.*?\*/', '', css_text, flags=re.DOTALL)
        rules = []
        # Find all selectors and block rules: selector { key: val; }
        pattern = re.compile(r'([^{]+)\{([^}]+)\}')
        for match in pattern.finditer(css_clean):
            selector_raw = match.group(1).strip()
            block = match.group(2).strip()
            
            # Parse property:value declarations
            properties = {}
            for decl in block.split(";"):
                decl = decl.strip()
                if not decl or ":" not in decl:
                    continue
                p, v = decl.split(":", 1)
                properties[p.strip().lower()] = v.strip()
            
            # Split comma-separated selectors (e.g. h1, h2, h3)
            for sel in selector_raw.split(","):
                sel = sel.strip()
                if sel:
                    rules.append((sel, properties))
        return rules

def parse_tag_content(content: str) -> Tuple[str, Dict[str, str]]:
    content = content.strip()
    if not content:
        return "", {}
    parts = content.split(None, 1)
    tag_name = parts[0].lower()
    if len(parts) == 1:
        return tag_name, {}
    
    attr_str = parts[1]
    attributes = {}
    pattern = re.compile(r'([a-zA-Z0-9\-:_]+)(?:\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+)))?')
    for match in pattern.finditer(attr_str):
        name = match.group(1).lower()
        val = match.group(2) or match.group(3) or match.group(4) or ""
        # Decodes HTML entities (like &amp; -> &) inside attribute values to ensure URLs resolve correctly
        attributes[name] = decode_html_entities(val)
        
    return tag_name, attributes

def decode_html_entities(text: str) -> str:
    entities = {
        "&nbsp;": " ",
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&apos;": "'",
        "&cent;": "¢",
        "&pound;": "£",
        "&yen;": "¥",
        "&euro;": "€",
        "&copy;": "©",
        "&reg;": "®",
    }
    for ent, char in entities.items():
        text = text.replace(ent, char)
        text = text.replace(ent.upper(), char)
    
    # Numeric / Hexadecimal entities (e.g. &#8211; or &#x2013;)
    def replace_num_entity(match):
        val = match.group(1)
        try:
            if val.lower().startswith('x'):
                code = int(val[1:], 16)
            else:
                code = int(val)
            return chr(code)
        except Exception:
            return match.group(0)
            
    text = re.sub(r'&#([xX]?[0-9a-fA-F]+);', replace_num_entity, text)
    return text

class HTMLParser:
    def __init__(self, html: str):
        self.html = html
        self.idx = 0
        self.length = len(html)

    def parse(self) -> HTMLNode:
        root = HTMLNode("html")
        stack = [root]
        
        while self.idx < self.length:
            tag_start = self.html.find("<", self.idx)
            if tag_start == -1:
                # Remaining text
                text = self.html[self.idx:]
                if text.strip():
                    decoded = decode_html_entities(text)
                    node = HTMLNode("text", parent=stack[-1], text=decoded)
                    stack[-1].children.append(node)
                break
            
            # Text node preceding the tag
            if tag_start > self.idx:
                text = self.html[self.idx:tag_start]
                # Keep whitespace but decode entities
                if text:
                    decoded = decode_html_entities(text)
                    node = HTMLNode("text", parent=stack[-1], text=decoded)
                    stack[-1].children.append(node)
            
            self.idx = tag_start
            
            # Check for HTML comments
            if self.html.startswith("<!--", self.idx):
                comment_end = self.html.find("-->", self.idx)
                if comment_end == -1:
                    self.idx = self.length
                else:
                    self.idx = comment_end + 3
                continue
                
            # Check for special doctype or meta tags
            if self.html.startswith("<!", self.idx):
                gt = self.html.find(">", self.idx)
                if gt == -1:
                    self.idx = self.length
                else:
                    self.idx = gt + 1
                continue
            
            # Check for XML processing instructions
            if self.html.startswith("<?", self.idx):
                pi_end = self.html.find("?>", self.idx)
                if pi_end == -1:
                    self.idx = self.length
                else:
                    self.idx = pi_end + 2
                continue
            
            # Tag boundaries
            tag_end = self.html.find(">", self.idx)
            if tag_end == -1:
                text = self.html[self.idx:]
                decoded = decode_html_entities(text)
                node = HTMLNode("text", parent=stack[-1], text=decoded)
                stack[-1].children.append(node)
                break
            
            tag_content = self.html[self.idx+1:tag_end].strip()
            self.idx = tag_end + 1
            
            if not tag_content:
                continue
                
            if tag_content.startswith("/"):
                # Closing Tag
                tag_name = tag_content[1:].strip().lower()
                # Pop robustly from stack to match tag_name
                found_idx = -1
                for i in range(len(stack)-1, -1, -1):
                    if stack[i].tag == tag_name:
                        found_idx = i
                        break
                if found_idx != -1 and found_idx > 0:
                    stack = stack[:found_idx]
            else:
                # Opening or self-closing tag
                is_self_closing = tag_content.endswith("/")
                if is_self_closing:
                    tag_content = tag_content[:-1].strip()
                
                tag_name, attributes = parse_tag_content(tag_content)
                
                # HTML5 implicit self-closers
                self_closing_tags = {
                    "area", "base", "br", "col", "embed", "hr", "img", "input", 
                    "link", "meta", "param", "source", "track", "wbr"
                }
                if tag_name in self_closing_tags:
                    is_self_closing = True
                
                node = HTMLNode(tag_name, attributes=attributes, parent=stack[-1])
                stack[-1].children.append(node)
                
                if not is_self_closing:
                    # Capture unparsed text content for style and script tags
                    if tag_name in ("script", "style"):
                        close_tag = f"</{tag_name}>"
                        close_start = self.html.lower().find(close_tag, self.idx)
                        if close_start == -1:
                            content = self.html[self.idx:]
                            self.idx = self.length
                        else:
                            content = self.html[self.idx:close_start]
                            self.idx = close_start + len(close_tag)
                        
                        if content.strip():
                            text_node = HTMLNode("text", parent=node, text=content)
                            node.children.append(text_node)
                    else:
                        stack.append(node)
        
        return root

def get_style_sheets(node: HTMLNode, rules: Optional[List[Tuple[str, Dict[str, str]]]] = None) -> List[Tuple[str, Dict[str, str]]]:
    if rules is None:
        rules = []
    if node.tag == "style" and node.children:
        style_text = "".join(c.text for c in node.children if c.is_text())
        rules.extend(CSSParser.parse(style_text))
    for child in node.children:
        get_style_sheets(child, rules)
    return rules

def matches_selector_simple(node: HTMLNode, selector: str) -> bool:
    selector = selector.strip()
    if not selector:
        return False
    if selector == "*":
        return True
    if selector.startswith("#"):
        return node.attributes.get("id", "") == selector[1:]
    if selector.startswith("."):
        classes = node.attributes.get("class", "").split()
        return selector[1:] in classes
    return node.tag == selector.lower()

def matches_selector(node: HTMLNode, selector: str) -> bool:
    parts = selector.strip().split()
    if not parts:
        return False
    if len(parts) == 1:
        return matches_selector_simple(node, parts[0])
    
    # Descendant selector: body .content p
    if not matches_selector_simple(node, parts[-1]):
        return False
    
    curr = node.parent
    part_idx = len(parts) - 2
    while curr and part_idx >= 0:
        if matches_selector_simple(curr, parts[part_idx]):
            part_idx -= 1
        curr = curr.parent
    return part_idx < 0

DEFAULT_STYLES: Dict[str, Dict[str, str]] = {
    "html": {"display": "block", "font-family": "sans-serif", "color": "black", "background-color": "transparent"},
    "body": {"display": "block", "margin": "12px"},
    "div": {"display": "block"},
    "p": {"display": "block", "margin-top": "12px", "margin-bottom": "12px"},
    "h1": {"display": "block", "font-size": "32px", "font-weight": "bold", "margin-top": "20px", "margin-bottom": "20px"},
    "h2": {"display": "block", "font-size": "24px", "font-weight": "bold", "margin-top": "18px", "margin-bottom": "18px"},
    "h3": {"display": "block", "font-size": "18px", "font-weight": "bold", "margin-top": "16px", "margin-bottom": "16px"},
    "h4": {"display": "block", "font-size": "16px", "font-weight": "bold", "margin-top": "14px", "margin-bottom": "14px"},
    "h5": {"display": "block", "font-size": "14px", "font-weight": "bold", "margin-top": "12px", "margin-bottom": "12px"},
    "h6": {"display": "block", "font-size": "12px", "font-weight": "bold", "margin-top": "10px", "margin-bottom": "10px"},
    "b": {"display": "inline", "font-weight": "bold"},
    "strong": {"display": "inline", "font-weight": "bold"},
    "i": {"display": "inline", "font-style": "italic"},
    "em": {"display": "inline", "font-style": "italic"},
    "u": {"display": "inline", "text-decoration": "underline"},
    "a": {"display": "inline", "color": "blue", "text-decoration": "underline"},
    "ul": {"display": "block", "margin-top": "12px", "margin-bottom": "12px", "padding-left": "30px"},
    "ol": {"display": "block", "margin-top": "12px", "margin-bottom": "12px", "padding-left": "30px"},
    "li": {"display": "block"},
    "br": {"display": "block"},
    "hr": {"display": "block", "margin-top": "12px", "margin-bottom": "12px", "border-style": "solid", "border-width": "1px", "color": "#ccc"},
    "pre": {"display": "block", "font-family": "monospace", "white-space": "pre", "margin": "12px 0", "background-color": "#f8f9fa"},
    "code": {"display": "inline", "font-family": "monospace", "background-color": "#f1f3f5"},
}

def resolve_styles(node: HTMLNode, rules: List[Tuple[str, Dict[str, str]]], parent_style: Optional[Dict[str, str]] = None):
    if node.is_text():
        # Only inherit valid text properties. Never copy parent borders, paddings, backgrounds!
        style = {}
        inheritable_props = ["color", "font-family", "font-size", "font-weight", "font-style", "text-align", "text-decoration"]
        if parent_style:
            for prop in inheritable_props:
                if prop in parent_style:
                    style[prop] = parent_style[prop]
        style["display"] = "inline"
        node.style = style
        return

    style = {}
    inheritable_props = ["color", "font-family", "font-size", "font-weight", "font-style", "text-align", "text-decoration"]
    if parent_style:
        for prop in inheritable_props:
            if prop in parent_style:
                style[prop] = parent_style[prop]

    default_node_style = DEFAULT_STYLES.get(node.tag, {"display": "inline"})
    style.update(default_node_style)

    for selector, rule_styles in rules:
        if matches_selector(node, selector):
            style.update(rule_styles)

    inline_style_str = node.attributes.get("style", "")
    if inline_style_str:
        inline_styles = {}
        for decl in inline_style_str.split(";"):
            decl = decl.strip()
            if not decl or ":" not in decl:
                continue
            p, v = decl.split(":", 1)
            inline_styles[p.strip().lower()] = v.strip()
        style.update(inline_styles)

    node.style = style

    for child in node.children:
        resolve_styles(child, rules, style)

if __name__ == "__main__":
    html_content = """
    <html>
        <head>
            <style>
                body { color: #333; font-family: Arial; }
                .main p { font-size: 16px; color: green; }
                #title { font-weight: bold; font-size: 28px; color: navy; }
            </style>
        </head>
        <body>
            <h1 id="title">Welcome &amp; Hello &lt;World&gt;!</h1>
            <div class="main">
                <p>This is a custom HTML &amp; CSS parser in action.</p>
                <p style="color: red; font-style: italic;">This inline style overrides stylesheet!</p>
            </div>
        </body>
    </html>
    """
    print("Parsing HTML...")
    parser = HTMLParser(html_content)
    doc = parser.parse()
    
    print("\nExtracting stylesheets...")
    rules = get_style_sheets(doc)
    print("Rules parsed:", rules)
    
    print("\nResolving styles...")
    resolve_styles(doc, rules)
    
    print("\nDOM Tree:")
    print(doc.dump())
import css_compiler
compiler = css_compiler.CSSCompiler()

