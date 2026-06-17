import tkinter as tk
from tkinter import scrolledtext
from network import fetch_url


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

        # Browser canvas (for rendering)
        self.canvas = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=80, height=30)
        self.canvas.pack(expand=True, fill=tk.BOTH, padx=5, pady=5)

    def load_url(self):
        url = self.address_entry.get()
        if not url.startswith("http"):
            url = "https://" + url
        self.canvas.delete(1.0, tk.END)
        self.canvas.insert(tk.END, f"Loading {url}...\n\n")
        self.root.update()  # Force UI update
        try:
            headers, body = fetch_url(url)
            self.canvas.delete(1.0, tk.END)
            self.canvas.insert(tk.END, f"=== HEADERS ===\n{headers}\n\n=== BODY ===\n{body[:2000]}")
        except Exception as e:
            self.canvas.insert(tk.END, f"Error: {str(e)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = SurfGambit(root)
    root.mainloop()