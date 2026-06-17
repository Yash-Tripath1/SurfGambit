from html import escape


class HTMLNode:
    """Represents an HTML node (element or text)."""
    def __init__(self, tag=None, attrs=None, children=None, text=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = children or []
        self.text = text

    def __repr__(self):
        if self.text:
            return f"HTMLNode(text={self.text!r})"
        return f"HTMLNode(tag={self.tag!r}, attrs={self.attrs}, children={len(self.children)})"


def parse_html(html):
    """Parse HTML string into a tree of HTMLNode objects."""
    root = HTMLNode(tag="html")
    stack = [root]
    i = 0
    n = len(html)

    while i < n:
        if html[i] == "<":
            # Opening or closing tag
            if html[i+1] == "/":
                # Closing tag
                i += 2
                tag_end = html.find(">", i)
                tag = html[i:tag_end].strip()
                if stack and stack[-1].tag == tag:
                    stack.pop()
                i = tag_end + 1
            else:
                # Opening tag
                tag_end = html.find(">", i)
                tag_part = html[i+1:tag_end]
                space_idx = tag_part.find(" ")
                if space_idx == -1:
                    tag = tag_part
                    attrs = {}
                else:
                    tag = tag_part[:space_idx]
                    attrs_str = tag_part[space_idx+1:]
                    attrs = parse_attributes(attrs_str)
                node = HTMLNode(tag=tag, attrs=attrs)
                stack[-1].children.append(node)
                stack.append(node)
                i = tag_end + 1
        else:
            # Text node
            text_end = html.find("<", i)
            if text_end == -1:
                text_end = n
            text = html[i:text_end].strip()
            if text:
                stack[-1].children.append(HTMLNode(text=text))
            i = text_end

    return root


def parse_attributes(attrs_str):
    """Parse HTML attributes string into a dict."""
    attrs = {}
    parts = attrs_str.split()
    for part in parts:
        if "=" in part:
            key, val = part.split("=", 1)
            val = val.strip('"\'')
            attrs[key] = val
        else:
            attrs[part] = True  # Boolean attribute (e.g., <input disabled>)
    return attrs


def render_node(node, indent=0):
    """Render an HTMLNode tree as a string (for debugging)."""
    if node.text:
        return "  " * indent + f"Text: {node.text!r}\n"
    result = "  " * indent + f"<{node.tag}" + (f" {node.attrs}" if node.attrs else "") + ">\n"
    for child in node.children:
        result += render_node(child, indent + 1)
    result += "  " * indent + f"</{node.tag}>\n"
    return result


if __name__ == "__main__":
    html = """
    <html>
        <head><title>Test</title></head>
        <body>
            <h1>Hello</h1>
            <p>World <b>bold</b>!</p>
        </body>
    </html>
    """
    tree = parse_html(html)
    print(render_node(tree))