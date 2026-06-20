import re
from typing import List, Tuple, Dict, Optional, Any
from parser import HTMLNode

class TextMeasurer:
    def __init__(self):
        self._fonts = {}
        self._tk_font_module = None
        self._root = None
        self.zoom = 1.0

    def _init_tk(self):
        if self._root is None:
            import tkinter as tk
            import tkinter.font as tkfont
            try:
                self._root = tk.Tk()
                self._root.withdraw() # Hide root window
                self._tk_font_module = tkfont
                self.headless = False
            except Exception:
                self.headless = True

    def get_font(self, family: str, size: str, weight: str, slant: str):
        self._init_tk()
        if self.headless:
            raise RuntimeError("Running headlessly, Tkinter not available")
        
        # Default font mappings
        if not family:
            family = "sans-serif"
        family_map = {
            "sans-serif": "Arial",
            "serif": "Times New Roman",
            "monospace": "Courier New",
            "cursive": "Comic Sans MS"
        }
        fam = family_map.get(family.lower(), family)
        
        # Parse font size
        try:
            if isinstance(size, str):
                if size.endswith("px"):
                    size_int = int(float(size[:-2]))
                elif size.endswith("em"):
                    size_int = int(float(size[:-2]) * 16)
                elif size.endswith("pt"):
                    size_int = int(float(size[:-2]) * 1.33)
                elif size.endswith("%"):
                    size_int = int(float(size[:-1]) / 100 * 16)
                else:
                    size_int = int(float(size))
            else:
                size_int = int(size)
        except Exception:
            size_int = 16
            
        # Scale by zoom level
        size_int = int(size_int * self.zoom)
        
        # Clamp font size to reasonable bounds
        size_int = max(4, min(size_int, 144))
            
        weight_str = "bold" if "bold" in str(weight).lower() else "normal"
        slant_str = "italic" if "italic" in str(slant).lower() else "roman"
        
        key = (fam, size_int, weight_str, slant_str)
        if key not in self._fonts:
            try:
                self._fonts[key] = self._tk_font_module.Font(
                    family=fam, size=size_int, weight=weight_str, slant=slant_str
                )
            except Exception:
                # Fallback to default sans-serif font
                self._fonts[key] = self._tk_font_module.Font(
                    family="Arial", size=size_int, weight=weight_str, slant=slant_str
                )
        return self._fonts[key]

    def measure_text(self, text: str, family: str, size: str, weight: str, slant: str) -> Tuple[int, int]:
        try:
            font = self.get_font(family, size, weight, slant)
            return font.measure(text), font.metrics("linespace")
        except Exception:
            # Headless estimate fallback
            try:
                if size.endswith("px"):
                    sz = int(float(size[:-2]))
                elif size.endswith("em"):
                    sz = int(float(size[:-2]) * 16)
                elif size.endswith("pt"):
                    sz = int(float(size[:-2]) * 1.33)
                else:
                    sz = int(float(size))
            except Exception:
                sz = 16
            
            # Scale by zoom level
            sz = int(sz * self.zoom)
            
            char_w = sz * 0.55
            if "bold" in str(weight).lower():
                char_w *= 1.15
            
            w = int(len(text) * char_w)
            h = int(sz * 1.2)
            return w, h

class LayoutBox:
    def __init__(self, node: HTMLNode, box_type: str):
        self.node = node
        self.box_type = box_type # "block" or "inline"
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0
        self.children: List['LayoutBox'] = []
        
        # Resolved margins, padding
        self.margin_top = 0
        self.margin_bottom = 0
        self.margin_left = 0
        self.margin_right = 0
        
        self.padding_top = 0
        self.padding_bottom = 0
        self.padding_left = 0
        self.padding_right = 0
        
        # Line records for block elements with inline/text content
        # Each line: (list_of_items, line_height)
        # where item is: ("word"|"hr", node, style, text, rel_x, rel_y, width, height)
        self.lines: List[Tuple[List[Tuple[str, HTMLNode, Dict[str, str], str, float, float, int, int]], int]] = []

    def __repr__(self) -> str:
        return f"LayoutBox({self.box_type}, tag={self.node.tag}, x={self.x}, y={self.y}, w={self.width}, h={self.height})"

def build_layout_tree(node: HTMLNode) -> Optional[LayoutBox]:
    # Skip styling, metadata, and scripts
    if node.tag in {"head", "style", "script", "link", "meta", "title"}:
        return None
    if node.style.get("display") == "none":
        return None
        
    display = node.style.get("display", "inline")
    box_type = "block" if display == "block" else "inline"
    
    if node.is_text():
        box_type = "inline"
        
    box = LayoutBox(node, box_type)
    
    for child in node.children:
        child_box = build_layout_tree(child)
        if child_box:
            box.children.append(child_box)
            
    # Anonymous Block Box simulation:
    # If this container is a block, and has ANY block-level child, then force ALL child layout boxes 
    # to be block-level. This prevents mixed-mode horizontal text flow overlaps (fundamental CSS rule).
    has_block_child = any(c.box_type == "block" for c in box.children)
    if box_type == "block" and has_block_child:
        for child_box in box.children:
            if child_box.box_type == "inline":
                child_box.box_type = "block"
            
    return box

def parse_px_val(val_str: str, default: int = 0) -> int:
    if not val_str:
        return default
    try:
        val_str = val_str.strip().lower()
        if val_str.endswith("px"):
            return int(float(val_str[:-2]))
        if val_str.endswith("em"):
            return int(float(val_str[:-2]) * 16)
        if val_str.endswith("%"):
            return default # Keep it simple, don't do complex % height calculations
        return int(float(val_str))
    except Exception:
        return default

def compute_layout(box: LayoutBox, x: int, y: int, max_width: int, measurer: TextMeasurer):
    # 1. Parse Margins and Padding
    box.margin_top = parse_px_val(box.node.style.get("margin-top"), parse_px_val(box.node.style.get("margin"), 0))
    box.margin_bottom = parse_px_val(box.node.style.get("margin-bottom"), parse_px_val(box.node.style.get("margin"), 0))
    box.margin_left = parse_px_val(box.node.style.get("margin-left"), parse_px_val(box.node.style.get("margin"), 0))
    box.margin_right = parse_px_val(box.node.style.get("margin-right"), parse_px_val(box.node.style.get("margin"), 0))
    
    box.padding_top = parse_px_val(box.node.style.get("padding-top"), parse_px_val(box.node.style.get("padding"), 0))
    box.padding_bottom = parse_px_val(box.node.style.get("padding-bottom"), parse_px_val(box.node.style.get("padding"), 0))
    box.padding_left = parse_px_val(box.node.style.get("padding-left"), parse_px_val(box.node.style.get("padding"), 0))
    box.padding_right = parse_px_val(box.node.style.get("padding-right"), parse_px_val(box.node.style.get("padding"), 0))

    if box.box_type == "block":
        # Support max-width for block styling
        target_width = max_width - box.margin_left - box.margin_right
        max_w_val = box.node.style.get("max-width")
        if max_w_val:
            limit_w = parse_px_val(max_w_val, target_width)
            if limit_w < target_width:
                target_width = limit_w

        # Support margin: auto centering
        margin_left_str = box.node.style.get("margin-left", "").strip().lower()
        margin_right_str = box.node.style.get("margin-right", "").strip().lower()
        margin_str = box.node.style.get("margin", "").strip().lower()
        
        is_auto_left = (margin_left_str == "auto") or ("auto" in margin_str and not margin_left_str)
        is_auto_right = (margin_right_str == "auto") or ("auto" in margin_str and not margin_right_str)
        
        if is_auto_left and is_auto_right:
            remaining_space = max_width - target_width
            if remaining_space > 0:
                box.margin_left = int(remaining_space / 2)
                box.margin_right = int(remaining_space / 2)

        box.x = x + box.margin_left
        box.y = y + box.margin_top
        box.width = target_width
        
        # Check if the block has block-level children
        has_block_children = any(c.box_type == "block" for c in box.children)
        
        if has_block_children:
            current_y = box.y + box.padding_top
            for child in box.children:
                compute_layout(child, box.x + box.padding_left, current_y, box.width - box.padding_left - box.padding_right, measurer)
                # Mathematical fix: update current_y using absolute child bottom + margin bottom
                current_y = child.y + child.height + child.margin_bottom
            box.height = current_y - box.y + box.padding_bottom
        else:
            # Inline Formatting Context inside this Block
            # Flow horizontal inline content (word wrap)
            _layout_inline_content(box, measurer, box.width - box.padding_left - box.padding_right)
            box.height = box.height + box.padding_top + box.padding_bottom
    else:
        # Inline boxes should not be directly layed out by compute_layout if they are in BFC.
        # However, as a safety fallback, if an inline box is a root or styled directly, we handle it.
        box.x = x
        box.y = y
        box.width = max_width
        box.height = 20 # stub height

def _layout_inline_content(box: LayoutBox, measurer: TextMeasurer, available_width: int):
    # Step A: Recursively flatten all inline/text elements into word tokens
    items = []
    
    def collect_items(b: LayoutBox):
        if b.node.tag == "br":
            items.append(("br", b.node, b.node.style, ""))
            return
        if b.node.tag == "hr":
            items.append(("hr", b.node, b.node.style, ""))
            return
        if b.node.tag == "img":
            # Image dimensions
            w_attr = b.node.attributes.get("width")
            h_attr = b.node.attributes.get("height")
            w_val = b.node.style.get("width", w_attr)
            h_val = b.node.style.get("height", h_attr)
            
            w = parse_px_val(w_val, 120)
            h = parse_px_val(h_val, 80)
            items.append(("img", b.node, b.node.style, f"{w},{h}"))
            return
        if b.node.tag in ("input", "button", "textarea", "alien"):
            items.append(("widget", b.node, b.node.style, ""))
            return
        if b.node.is_text():
            text = b.node.text
            # Standard HTML: collapse all whitespaces to single spaces
            text_collapsed = re.sub(r'\s+', ' ', text)
            if not text_collapsed:
                return
            
            # Preserve leading/trailing space if original text had it
            leading_space = text.startswith(" ") or text.startswith("\n") or text.startswith("\r") or text.startswith("\t")
            trailing_space = text.endswith(" ") or text.endswith("\n") or text.endswith("\r") or text.endswith("\t")
            
            words = [w for w in text_collapsed.split(' ') if w]
            for idx, word in enumerate(words):
                # Put spacing back between words
                word_str = word
                if idx < len(words) - 1 or trailing_space:
                    word_str += " "
                if idx == 0 and leading_space and not word_str.startswith(" "):
                    word_str = " " + word_str
                
                if word_str:
                    items.append(("word", b.node, b.node.style, word_str))
            return
            
        # Recursive collect
        for child in b.children:
            collect_items(child)

    collect_items(box)

    # Step B: Flow items into lines with word wrapping
    lines = []
    current_line = []
    cx = 0
    cy = 0
    max_line_height = 0
    line_spacing = 4

    for item in items:
        item_type, node, style, text = item
        
        if item_type == "br":
            lines.append((current_line, max_line_height or 16))
            current_line = []
            cy += (max_line_height or 16) + line_spacing
            cx = 0
            max_line_height = 0
            continue
            
        if item_type == "hr":
            if current_line:
                lines.append((current_line, max_line_height))
                current_line = []
                cy += max_line_height + line_spacing
                
            # hr block takes full width, 8px high
            hr_item = ("hr", node, style, "", 0, cy + 4, available_width, 2)
            lines.append(([hr_item], 10))
            cy += 10 + line_spacing
            cx = 0
            max_line_height = 0
            continue

        if item_type == "img":
            try:
                w_str, h_str = text.split(",")
                w = int(w_str)
                h = int(h_str)
            except Exception:
                w, h = 120, 80
                
            scaled_w = int(w * measurer.zoom)
            scaled_h = int(h * measurer.zoom)
            
            if cx + scaled_w > available_width and cx > 0:
                lines.append((current_line, max_line_height))
                current_line = []
                cy += max_line_height + line_spacing
                cx = 0
                max_line_height = 0
                
            max_line_height = max(max_line_height, scaled_h)
            current_line.append(("img", node, style, text, cx, cy, scaled_w, scaled_h))
            cx += scaled_w
            continue

        if item_type == "widget":
            tag = node.tag
            # Default sizes
            w, h = 150, 24
            if tag == "button" or node.attributes.get("type") == "submit":
                w, h = 80, 24
                
            w_val = style.get("width", node.attributes.get("width"))
            h_val = style.get("height", node.attributes.get("height"))
            w = parse_px_val(w_val, w)
            h = parse_px_val(h_val, h)
            
            scaled_w = int(w * measurer.zoom)
            scaled_h = int(h * measurer.zoom)
            
            if cx + scaled_w > available_width and cx > 0:
                lines.append((current_line, max_line_height))
                current_line = []
                cy += max_line_height + line_spacing
                cx = 0
                max_line_height = 0
                
            max_line_height = max(max_line_height, scaled_h)
            # pack width and height as text 'w,h' for convenience
            current_line.append(("widget", node, style, f"{scaled_w},{scaled_h}", cx, cy, scaled_w, scaled_h))
            cx += scaled_w
            continue

        # Measure text word
        font_family = style.get("font-family", "sans-serif")
        font_size = style.get("font-size", "16px")
        font_weight = style.get("font-weight", "normal")
        font_style = style.get("font-style", "normal")
        
        w, h = measurer.measure_text(text, font_family, font_size, font_weight, font_style)
        
        # Word wrapping condition
        if cx + w > available_width and cx > 0:
            lines.append((current_line, max_line_height))
            current_line = []
            cy += max_line_height + line_spacing
            cx = 0
            max_line_height = 0
            
        max_line_height = max(max_line_height, h)
        current_line.append(("word", node, style, text, cx, cy, w, h))
        cx += w

    if current_line:
        lines.append((current_line, max_line_height))
        cy += max_line_height
    else:
        # Avoid empty content height of 0
        cy = max(cy, 16)

    # Step C: Align text based on text-align property
    align = box.node.style.get("text-align", "left").lower()
    if align in ("center", "right"):
        adjusted_lines = []
        for line, line_h in lines:
            if not line:
                adjusted_lines.append((line, line_h))
                continue
            last_item = line[-1]
            line_width = last_item[4] + last_item[6] # rx + rw of last item
            remaining_width = available_width - line_width
            if remaining_width > 0:
                shift = remaining_width / 2 if align == "center" else remaining_width
                shifted_line = []
                for item in line:
                    itype, inode, istyle, itext, rx, ry, rw, rh = item
                    shifted_line.append((itype, inode, istyle, itext, rx + shift, ry, rw, rh))
                adjusted_lines.append((shifted_line, line_h))
            else:
                adjusted_lines.append((line, line_h))
        lines = adjusted_lines

    box.lines = lines
    box.height = cy

if __name__ == "__main__":
    from parser import HTMLParser, get_style_sheets, resolve_styles
    html_content = """
    <html>
        <body>
            <div style="text-align: center; color: purple;">
                <h1>SurfGambit Layout Engine</h1>
                <p>Testing horizontal alignment and wrapping features inside a centralized block container.</p>
            </div>
            <hr>
            <div style="margin-left: 50px; background-color: #eee;">
                <p>This paragraph has left margin! Let's see if <b>bold words</b> and <i>italic fonts</i> wrap gracefully when the text line exceeds the limit!</p>
            </div>
        </body>
    </html>
    """
    print("Parsing...")
    doc = HTMLParser(html_content).parse()
    rules = get_style_sheets(doc)
    resolve_styles(doc, rules)
    
    print("Building layout...")
    measurer = TextMeasurer()
    layout_tree = build_layout_tree(doc)
    if layout_tree:
        compute_layout(layout_tree, 0, 0, 600, measurer)
        print("Root height:", layout_tree.height)
        print("Layout complete!")
