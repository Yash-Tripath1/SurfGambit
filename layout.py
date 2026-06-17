class LayoutBox:
    """Represents a laid-out box with position and dimensions."""
    def __init__(self, node, x, y, width, height):
        self.node = node
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.children = []


class LayoutEngine:
    """Simple layout engine for block elements."""
    def __init__(self, width=800):
        self.width = width
        self.y = 0

    def layout(self, node):
        """Recursively lay out a node and its children."""
        if node.text:
            # Text node: create a leaf box
            return LayoutBox(node, 0, self.y, self.width, 20)

        # Block elements: div, p, h1, h2, h3
        if node.tag in ["div", "p", "h1", "h2", "h3", "body", "html"]:
            box = LayoutBox(node, 0, self.y, self.width, 0)
            for child in node.children:
                child_box = self.layout(child)
                child_box.x = 10  # Left margin
                box.children.append(child_box)
                box.height += child_box.height
            self.y += box.height
            return box

        # Inline elements: span, a, b, i
        if node.tag in ["span", "a", "b", "i"]:
            box = LayoutBox(node, 0, self.y, 0, 20)
            for child in node.children:
                child_box = self.layout(child)
                box.children.append(child_box)
                box.width += child_box.width
            return box

        # Default: treat as block
        box = LayoutBox(node, 0, self.y, self.width, 20)
        for child in node.children:
            child_box = self.layout(child)
            box.children.append(child_box)
            box.height += child_box.height
        self.y += box.height
        return box


def layout_tree(root_node):
    """Layout the entire HTML tree."""
    engine = LayoutEngine()
    return engine.layout(root_node)