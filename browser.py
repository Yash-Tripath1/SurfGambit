import tkinter as tk
from network import fetch_url
from parser import parse_html
from layout import layout_tree


class SurfGambit:
    def __init__(self, root):
        self.root = root
        self.root.title("SurfGambit: My Window to the Broken Web")
        self.root.geometry("800x600")

        # Address bar
        self.address_frame = tk.Frame(root)
        self.address_frame.pack(fill=tk.X, padx=5, pady=5)

        self.address_label = tk.Label(self.address_frame, text="URL:")
        self.address_label.pack(side=tk.LEFT)

        self.address_entry = tk.Entry(self.address_frame, width=60)
        self.address_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.address_entry.insert(0, "https://example.com")

        self.go_button = tk.Button(self.address_frame, text="Go", command=self.load_url)
        self.go_button.pack(side=tk.LEFT, padx=5)

        # Browser canvas
        self.canvas = tk.Canvas(root, width=800, height=550, bg="white")
        self.canvas.pack(expand=True, fill=tk.BOTH, padx=5, pady=5)
        self.canvas.bind("<Button-1>", self.on_click)

        # Store link positions
        self.links = {}

    def on_click(self, event):
        """Handle clicks on links."""
        x, y = event.x, event.y
        for (x1, y1, x2, y2), url in self.links.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                self.address_entry.delete(0, tk.END)
                self.address_entry.insert(0, url)
                self.load_url()
                return

    def render_box(self, box, x_offset=0, y_offset=0):
        """Render a layout box and its children."""
        node = box.node
        x = box.x + x_offset
        y = box.y + y_offset

        if node.tag == "a":
            href = node.attrs.get("href", "")
            text = "".join(child.text for child in node.children if child.text)
            if text:
                text_id = self.canvas.create_text(x, y, text=text, fill="blue", anchor=tk.NW)
                bbox = self.canvas.bbox(text_id)
                self.links[(bbox[0], bbox[1], bbox[2], bbox[3])] = href
        elif node.tag in ["h1", "h2", "h3"]:
            text = "".join(child.text for child in node.children if child.text)
            size = 20 if node.tag == "h1" else 16 if node.tag == "h2" else 14
            self.canvas.create_text(x, y, text=text, font=("Arial", size, "bold"), anchor=tk.NW)
        elif node.tag in ["b", "i"]:
            text = "".join(child.text for child in node.children if child.text)
            weight = "bold" if node.tag == "b" else "italic"
            self.canvas.create_text(x, y, text=text, font=("Arial", 10, weight), anchor=tk.NW)
        elif node.text:
            self.canvas.create_text(x, y, text=node.text, font=("Arial", 10), anchor=tk.NW)
        elif node.tag in ["p", "div", "body", "html"]:
            y_offset += 5

        for child_box in box.children:
            self.render_box(child_box, x_offset, y_offset)

    def load_url(self):
        url = self.address_entry.get()
        if not url.startswith("http"):
            url = "https://" + url
        self.canvas.delete("all")
        self.canvas.create_text(400, 300, text=f"Loading {url}...")
        self.root.update()
        self.links = {}
        try:
            headers, body = fetch_url(url)
            tree = parse_html(body)
            root_box = layout_tree(tree)
            self.canvas.delete("all")
            self.render_box(root_box)
        except Exception as e:
            self.canvas.delete("all")
            self.canvas.create_text(400, 300, text=f"Error: {str(e)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = SurfGambit(root)
    root.mainloop()