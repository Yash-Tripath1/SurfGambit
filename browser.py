import tkinter as tk
from tkinter import ttk
import threading
import urllib.parse
from typing import Dict, List, Tuple, Optional

import network
import parser
import layout

WELCOME_HTML = """
<html>
<head>
    <style>
        body { 
            font-family: sans-serif; 
            background-color: #121212; 
            color: #e0e0e0; 
            margin: 40px; 
            text-align: center; 
        }
        .card { 
            background-color: #1e1e1e; 
            border: 1px solid #333; 
            padding: 25px; 
            margin: 20px auto; 
            max-width: 650px; 
            text-align: left; 
        }
        h1 { 
            color: #00adb5; 
            font-size: 38px; 
            margin-bottom: 5px; 
            text-align: center; 
        }
        h2 { 
            color: #00adb5; 
            font-size: 20px; 
            border-bottom: 1px solid #333; 
            padding-bottom: 8px; 
            margin-top: 0px;
        }
        p { 
            line-height: 1.6; 
            font-size: 14px;
        }
        a { 
            color: #00adb5; 
            font-weight: bold; 
            text-decoration: underline;
        }
        .tech-list { 
            background-color: #151515; 
            padding: 15px; 
            border-left: 4px solid #00adb5; 
            font-family: monospace; 
            font-size: 13px;
            line-height: 1.5;
        }
    </style>
</head>
<body>
    <h1>SurfGambit Browser</h1>
    <p style="color: #888; font-size: 16px; margin-top: 5px;">A custom-built Python web engine rendered on raw Canvas.</p>
    
    <div style="text-align: center; margin-top: 20px; margin-bottom: 10px;">
        <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/120px-Python-logo-notext.svg.png" alt="Python Logo" width="60" height="60" style="margin-right: 25px;">
        <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/d/d9/Tcl_Logo.svg/120px-Tcl_Logo.svg.png" alt="Tcl/Tk Logo" width="90" height="55">
    </div>
    
    <div class="card">
        <h2>🚀 Deeply Custom Architecture</h2>
        <p>This browser is written from scratch without high-level rendering frameworks, implementing a modular engine model:</p>
        <div class="tech-list">
            - [network.py]: TCP Sockets, TLS, Gzip decompression, Chunked Transfer Decoding.<br>
            - [parser.py]: State-machine HTML compiler & CSS Selector Cascadence specificity.<br>
            - [layout.py]: Geometry flow solvers, word wrapping, padding margins, alignment.<br>
            - [browser.py]: Multi-threaded UI shell, Canvas viewport painter, tabs management.
        </div>
    </div>
    
    <div class="card">
        <h2>🖼 PNG/GIF Image Support</h2>
        <p>This browser pulls actual image resources directly through raw socket streams, decodes them via standard library PhotoImage on the main loop, and reflows layouts on image dimensions detection.</p>
        <p>Try loading any standard image-heavy page, or see the Python and Tcl/Tk PNG banners rendered right above! JPEGs fallback gracefully to text-based alt placeholders.</p>
    </div>
    
    <div class="card">
        <h2>🌐 Interactive Features</h2>
        <p>This browser is equipped with power-user controls for testing:</p>
        <p>
            - <b>Multiple Tabs</b>: Browse multiple pages concurrently.<br>
            - <b>Precise Zoom</b>: Use ➕ / ➖ buttons to scale elements with responsive re-layout.<br>
            - <b>Developer Tools</b>: Toggle the side panel to view DOM, CSS, or Raw HTML source!<br>
            - <b>History Navigation</b>: Full back/forward page history tracking.<br>
            - <b>Word Wrapping &amp; Alignment</b>: Modern word flow and CSS style resolution.
        </p>
    </div>

    <div class="card">
        <h2>🔗 Standard Web Testbench</h2>
        <p>Try entering these lightweight URLs in the address bar above, or type search terms to query Google directly:</p>
        <p>
            - <a href="http://example.com">http://example.com</a> (Standard HTTP test site)<br>
            - <a href="http://neverssl.com">http://neverssl.com</a> (Great raw no-SSL sandbox)<br>
            - <a href="https://text.npr.org">https://text.npr.org</a> (NPR News Text-Only)<br>
            - <a href="https://news.ycombinator.com">https://news.ycombinator.com</a> (Hacker News)
        </p>
    </div>
    
    <div style="color: #444; font-size: 12px; margin-top: 40px;">
        SurfGambit Client v1.2 | Standalone Sandbox Session
    </div>
</body>
</html>
"""

class BrowserTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, main_browser: 'SurfGambitApp'):
        super().__init__(parent)
        self.parent = parent
        self.main_browser = main_browser
        
        # Navigation History
        self.back_stack: List[str] = []
        self.forward_stack: List[str] = []
        self.current_url: str = "surfgambit://welcome"
        
        # State & Parsed Data
        self.raw_html: str = ""
        self.dom_tree: Optional[parser.HTMLNode] = None
        self.css_rules: List[Tuple[str, Dict[str, str]]] = []
        self.layout_tree: Optional[layout.LayoutBox] = None
        self.zoom_level: float = 1.0
        
        # Async Images Cache & Tracking
        self.loaded_images: Dict[str, Optional[tk.PhotoImage]] = {}
        self.loading_images: set = set()
        
        # In-Page Search State (Ctrl+F)
        self.search_term = ""
        self.search_matches_count = 0
        
        # Thread lock and active loading control
        self.is_loading = False
        self.loading_thread: Optional[threading.Thread] = None
        
        # UI Setup: Canvas and scrollbar
        self.canvas = tk.Canvas(self, bg="white", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        
        # Link routing map (ID to URL)
        self.link_map: Dict[int, str] = {}
        
        # Mouse / Keyboard Scrolling Bindings
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-4>", self.on_mousewheel)
        self.canvas.bind("<Button-5>", self.on_mousewheel)
        
        # Canvas Link Event Bindings
        self.canvas.tag_bind("link", "<Enter>", self.on_link_enter)
        self.canvas.tag_bind("link", "<Leave>", self.on_link_leave)
        self.canvas.tag_bind("link", "<Button-1>", self.on_link_click)
        
        # Resize detection
        self.canvas.bind("<Configure>", self.on_resize)
        self.last_width = 0
        
        # Initial Welcome Load
        self.load_welcome_page()

    def load_welcome_page(self):
        self.current_url = "surfgambit://welcome"
        self.raw_html = WELCOME_HTML
        self.dom_tree = parser.HTMLParser(self.raw_html).parse()
        self.css_rules = parser.get_style_sheets(self.dom_tree)
        parser.resolve_styles(self.dom_tree, self.css_rules)
        self._start_image_downloads()
        self.trigger_layout()

    def navigate_to(self, url: str, is_history_action=False):
        url = url.strip()
        if not url:
            return
            
        # Standardize URL or route to selected Search Engine if it's a search term!
        is_schema = url.startswith("http://") or url.startswith("https://") or url.startswith("surfgambit://")
        
        if not is_schema:
            has_dot = "." in url
            has_space = " " in url
            if has_dot and not has_space:
                url = "https://" + url
            else:
                # Treat as search query!
                query_encoded = urllib.parse.quote(url)
                engine = self.main_browser.search_engine_combo.get()
                if engine == "Google":
                    url = f"https://www.google.com/search?q={query_encoded}&gbv=1"
                elif engine == "Wikipedia":
                    url = f"https://en.wikipedia.org/wiki/Special:Search?search={query_encoded}"
                else:
                    url = f"https://html.duckduckgo.com/html/?q={query_encoded}"

        if url == "surfgambit://welcome":
            if not is_history_action and self.current_url != url:
                self.back_stack.append(self.current_url)
                self.forward_stack.clear()
            self.load_welcome_page()
            self.main_browser.update_ui_state(self)
            return

        if self.is_loading:
            return # Prevent double loading
            
        self.is_loading = True
        self.main_browser.status_bar.config(text=f"Connecting to {url}...")
        self.main_browser.show_loading_spinner(True)
        
        # Clear images cache for the new session
        self.loaded_images.clear()
        self.loading_images.clear()
        
        # Launch non-blocking request background thread
        self.loading_thread = threading.Thread(
            target=self._async_fetch_and_parse,
            args=(url, is_history_action),
            daemon=True
        )
        self.loading_thread.start()

    def _async_fetch_and_parse(self, url: str, is_history_action: bool):
        try:
            response = network.request(url)
            # Use final resolved URL (supports redirects)
            resolved_url = response.url
            html_text = response.text
            
            # Parsing DOM and resolving stylesheets
            dom = parser.HTMLParser(html_text).parse()
            css = parser.get_style_sheets(dom)
            parser.resolve_styles(dom, css)
            
            # Post success callback on main thread
            self.after(0, self._on_load_success, url, resolved_url, html_text, dom, css, is_history_action)
        except Exception as e:
            self.after(0, self._on_load_error, url, str(e))

    def _on_load_success(self, orig_url: str, resolved_url: str, html_text: str, dom: parser.HTMLNode, css: list, is_history_action: bool):
        self.is_loading = False
        self.main_browser.show_loading_spinner(False)
        
        # Push history
        if not is_history_action:
            if self.current_url:
                self.back_stack.append(self.current_url)
            self.forward_stack.clear()
            
        self.current_url = resolved_url
        self.raw_html = html_text
        self.dom_tree = dom
        self.css_rules = css
        
        # Start async loading of image nodes
        self._start_image_downloads()
        
        # Render page
        self.trigger_layout()
        
        self.main_browser.status_bar.config(text="Done")
        self.main_browser.update_ui_state(self)

    def _on_load_error(self, url: str, err_msg: str):
        self.is_loading = False
        self.main_browser.show_loading_spinner(False)
        self.main_browser.status_bar.config(text=f"Failed to load: {err_msg}")
        
        error_html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: sans-serif; margin: 50px; background-color: #fff5f5; color: #c53030; }}
                h1 {{ font-size: 28px; margin-bottom: 10px; border-bottom: 2px solid #feb2b2; padding-bottom: 10px; }}
                p {{ font-size: 16px; line-height: 1.5; }}
                .url {{ font-family: monospace; background-color: #fff; padding: 4px; border: 1px solid #fed7d7; }}
            </style>
        </head>
        <body>
            <h1>Navigation Failed 🛑</h1>
            <p>SurfGambit was unable to load the requested page:</p>
            <p class="url">{orig_escape(url)}</p>
            <p><b>Error Details:</b> {orig_escape(err_msg)}</p>
            <p>Make sure you have an active internet connection and that the server address is spelled correctly.</p>
        </body>
        </html>
        """
        self.raw_html = error_html
        self.dom_tree = parser.HTMLParser(error_html).parse()
        self.css_rules = []
        parser.resolve_styles(self.dom_tree, self.css_rules)
        self.trigger_layout()
        self.main_browser.update_ui_state(self)

    def _start_image_downloads(self):
        self.loading_images.clear()
        img_nodes = []
        
        def find_images(node):
            if node.tag == "img":
                img_nodes.append(node)
            for child in node.children:
                find_images(child)
                
        if self.dom_tree:
            find_images(self.dom_tree)
            
        for node in img_nodes:
            src = node.attributes.get("src")
            if src:
                img_url = urllib.parse.urljoin(self.current_url, src)
                if img_url not in self.loaded_images and img_url not in self.loading_images:
                    self.loading_images.add(img_url)
                    threading.Thread(target=self._async_download_image, args=(img_url, node), daemon=True).start()

    def _async_download_image(self, img_url: str, node: parser.HTMLNode):
        try:
            response = network.request(img_url)
            self.after(0, self._on_image_download_success, img_url, response.body, node)
        except Exception:
            self.after(0, self._on_image_download_error, img_url)

    def _on_image_download_success(self, img_url: str, img_data: bytes, node: parser.HTMLNode):
        self.loading_images.discard(img_url)
        try:
            photo = tk.PhotoImage(data=img_data)
            self.loaded_images[img_url] = photo
            
            # Reflow: If sizing was implicit, update using actual PhotoImage width/height
            reflow = False
            if "width" not in node.attributes and "width" not in node.style:
                node.style["width"] = f"{photo.width()}px"
                reflow = True
            if "height" not in node.attributes and "height" not in node.style:
                node.style["height"] = f"{photo.height()}px"
                reflow = True
                
            self.trigger_layout()
        except Exception:
            # JPEG or parsing error - mark as None to draw alt fallback
            self.loaded_images[img_url] = None
            self.trigger_layout()

    def _on_image_download_error(self, img_url: str):
        self.loading_images.discard(img_url)
        self.loaded_images[img_url] = None
        self.trigger_layout()

    def trigger_layout(self):
        if not self.dom_tree:
            return
            
        measurer = self.main_browser.measurer
        measurer.zoom = self.zoom_level
        
        # Rebuild layout tree
        self.layout_tree = layout.build_layout_tree(self.dom_tree)
        if self.layout_tree:
            canvas_width = max(300, self.canvas.winfo_width())
            # Run layout calculations
            layout.compute_layout(self.layout_tree, 0, 0, canvas_width, measurer)
            # Paint to canvas
            self.render()
            
        # Update devtools views if active
        self.main_browser.devtools_panel.refresh_devtools(self)

    def render(self):
        self.canvas.delete("all")
        self.link_map.clear()
        self.search_matches_count = 0
        
        if not self.layout_tree:
            return
            
        # Dynamically propagate page background color to the canvas viewport.
        # This removes the ugly white boundaries around dark-themed web pages!
        bg_color = "white"
        if self.dom_tree:
            # Check for body background color first as it typically defines page branding
            body_bg = "transparent"
            for child in self.dom_tree.children:
                if child.tag == "body":
                    body_bg = child.style.get("background-color", "transparent")
                    break
            
            html_bg = self.dom_tree.style.get("background-color", "transparent")
            
            if body_bg and body_bg.lower() != "transparent":
                bg_color = body_bg
            elif html_bg and html_bg.lower() != "transparent":
                bg_color = html_bg
                
        if bg_color.lower() == "transparent":
            bg_color = "white"
            
        self.canvas.config(bg=bg_color)
            
        # Total heights and scrolling region bounds
        total_height = self.layout_tree.height
        self.canvas.config(scrollregion=(0, 0, self.canvas.winfo_width(), total_height + 100))
        
        # Recursively render the layout blocks
        self._render_box(self.layout_tree)
        
        # Display search matches in the status bar
        if self.search_term:
            self.main_browser.status_bar.config(
                text=f"Find: Highlighted {self.search_matches_count} occurrences of '{self.search_term}' on this page."
            )

    def _render_box(self, box: layout.LayoutBox):
        # 1. Render Background Color
        bg_color = box.node.style.get("background-color")
        if bg_color and bg_color.lower() != "transparent":
            self.canvas.create_rectangle(
                box.x, box.y, box.x + box.width, box.y + box.height,
                fill=bg_color, outline=""
            )
            
        # 1b. Render Borders (Outlines)
        border_str = box.node.style.get("border")
        border_w = 0
        border_c = None
        if border_str:
            # Simple parser for "1px solid #333" or similar
            parts = border_str.split()
            for p in parts:
                if "px" in p:
                    try:
                        border_w = max(1, int(float(p.replace("px", ""))))
                    except:
                        pass
                elif p.startswith("#") or p.lower() in ("black", "gray", "red", "blue", "white", "darkgray", "#333", "#555", "#ccc"):
                    border_c = p
        else:
            border_w_str = box.node.style.get("border-width")
            if border_w_str:
                border_w = layout.parse_px_val(border_w_str, 0)
                border_c = box.node.style.get("border-color", "black")
                
        if border_w > 0 and border_c:
            # Offset the rectangle slightly inward to keep it within the layout boundaries if desired,
            # or draw it normally. Normally is perfectly fine.
            self.canvas.create_rectangle(
                box.x, box.y, box.x + box.width, box.y + box.height,
                fill="", outline=border_c, width=border_w
            )
            
        # 2. Render list bullets for <li> elements
        if box.node.tag == "li":
            parent = box.node.parent
            if parent and parent.tag == "ol":
                # Render sequential numbering
                siblings = [c for c in parent.children if c.tag == "li"]
                try:
                    num = siblings.index(box.node) + 1
                except ValueError:
                    num = 1
                bx = box.x - 20
                by = box.y + box.padding_top + 2
                self.canvas.create_text(
                    bx, by, text=f"{num}.", anchor="nw", 
                    font=("Arial", int(14 * self.zoom_level), "bold"), fill=box.node.style.get("color", "black")
                )
            else:
                # Render modern circular bullets
                bx = box.x - 15
                by = box.y + box.padding_top + 6
                r = 3 * self.zoom_level
                self.canvas.create_oval(
                    bx - r, by - r, bx + r, by + r, 
                    fill=box.node.style.get("color", "black"), outline=""
                )

        # 3. Render inline formatting lines
        if box.lines:
            for line, line_h in box.lines:
                for item in line:
                    itype, node, style, text, rx, ry, rw, rh = item
                    # Absolute placement on canvas
                    ax = box.x + rx + box.padding_left
                    ay = box.y + ry + box.padding_top
                    
                    if itype == "word":
                        font_family = style.get("font-family", "sans-serif")
                        font_size = style.get("font-size", "16px")
                        font_weight = style.get("font-weight", "normal")
                        font_style = style.get("font-style", "normal")
                        
                        # Find link target (walk up parents)
                        is_link, href = self._find_link_ancestor(node)
                        
                        # Apply style values
                        color = style.get("color", "black")
                        if is_link:
                            color = style.get("color", "blue") # Fallback to standard blue for links
                        
                        # Sanitize colors for Tkinter compatibility (bypasses CSS 'inherit', 'unset', variables)
                        color_lower = color.lower().strip()
                        if color_lower in ("inherit", "initial", "unset", "currentcolor", "transparent", ""):
                            color = "white" if self.current_url == "surfgambit://welcome" else "black"
                        elif color_lower.startswith("rgba"):
                            color = "black" # basic solid color fallback for canvas text
                        elif color_lower.startswith("var("):
                            color = "black" # ignore CSS variable bindings
                        
                        # Format text decorations
                        font_dec = ""
                        if is_link or style.get("text-decoration") == "underline":
                            font_dec = "underline"
                            
                        # Retrieve correct Tk font
                        tk_font = self.main_browser.measurer.get_font(font_family, font_size, font_weight, font_style)
                        if font_dec == "underline" and not self.main_browser.measurer.headless:
                            # Apply underline if supported by the OS font engine
                            tk_font.configure(underline=True)
                        
                        # Link registration tagging
                        tags = ()
                        if is_link:
                            link_id = len(self.link_map) + 1
                            self.link_map[link_id] = href
                            tags = ("link", f"link_{link_id}")
                            
                        # IN-PAGE SEARCH HIGHLIGHTING (Ctrl+F)
                        if self.search_term and self.search_term.lower() in text.lower():
                            self.search_matches_count += 1
                            self.canvas.create_rectangle(
                                ax - 1, ay - 1, ax + rw + 1, ay + rh + 1,
                                fill="#ffeb3b", outline="#fbc02d", width=1
                            )
                            color = "black" # force high contrast black text on yellow
                            
                        self.canvas.create_text(
                            ax, ay, text=text, font=tk_font, fill=color, anchor="nw", tags=tags
                        )
                        
                    elif itype == "img":
                        src = node.attributes.get("src", "")
                        alt_text = node.attributes.get("alt", "Image")
                        resolved_url = urllib.parse.urljoin(self.current_url, src)
                        
                        # Render PhotoImage if successfully fetched and decoded
                        photo = self.loaded_images.get(resolved_url)
                        if photo:
                            self.canvas.create_image(ax, ay, image=photo, anchor="nw")
                        else:
                            # Draw beautiful textured placeholder
                            is_welcome = (self.current_url == "surfgambit://welcome")
                            fill_c = "#2d2d2d" if is_welcome else "#f5f5f5"
                            out_c = "#555555" if is_welcome else "#cccccc"
                            txt_c = "#888888" if is_welcome else "#666666"
                            
                            self.canvas.create_rectangle(
                                ax, ay, ax + rw, ay + rh,
                                fill=fill_c, outline=out_c, width=1, dash=(4, 4)
                            )
                            # Render compact placeholder details
                            lbl = f"📷 {alt_text}"
                            max_chars = max(5, int(rw / 6.5))
                            if len(lbl) > max_chars:
                                lbl = lbl[:int(max_chars) - 3] + "..."
                                
                            self.canvas.create_text(
                                ax + rw/2, ay + rh/2, text=lbl, fill=txt_c,
                                font=("Arial", max(8, int(10 * self.zoom_level)), "normal"), anchor="center"
                            )
                            
                    elif itype == "hr":
                        # Render division line
                        color = style.get("color", "#ccc")
                        self.canvas.create_line(ax, ay, ax + rw, ay, fill=color, width=rh)
                        
        # Recursive pass
        for child in box.children:
            self._render_box(child)

    def _find_link_ancestor(self, node: parser.HTMLNode) -> Tuple[bool, str]:
        curr = node
        while curr:
            if curr.tag == "a":
                return True, curr.attributes.get("href", "")
            curr = curr.parent
        return False, ""

    def on_mousewheel(self, event):
        # Universal wheel scroll (Windows/Linux/Mac fallback)
        if event.num == 5 or event.delta < 0:
            self.canvas.yview_scroll(1, "units")
        elif event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-1, "units")

    def on_resize(self, event):
        # Trigger layout on width resizing boundaries
        if abs(event.width - self.last_width) > 10:
            self.last_width = event.width
            # Debounce resize slightly or run immediately
            self.trigger_layout()

    def on_link_enter(self, event):
        item = self.canvas.find_withtag("current")
        tags = self.canvas.gettags(item)
        link_tag = [t for t in tags if t.startswith("link_")]
        if link_tag:
            try:
                link_id = int(link_tag[0].split("_")[1])
                href = self.link_map.get(link_id, "")
                # Resolve full path info
                full_url = urllib.parse.urljoin(self.current_url, href)
                self.main_browser.status_bar.config(text=f"Navigate to: {full_url}")
                self.canvas.config(cursor="hand2")
            except ValueError:
                pass

    def on_link_leave(self, event):
        self.main_browser.status_bar.config(text="Done")
        self.canvas.config(cursor="")

    def on_link_click(self, event):
        item = self.canvas.find_withtag("current")
        tags = self.canvas.gettags(item)
        link_tag = [t for t in tags if t.startswith("link_")]
        if link_tag:
            try:
                link_id = int(link_tag[0].split("_")[1])
                href = self.link_map.get(link_id, "")
                target_url = urllib.parse.urljoin(self.current_url, href)
                self.navigate_to(target_url)
            except ValueError:
                pass


class DevToolsFrame(ttk.Frame):
    def __init__(self, parent: tk.PanedWindow):
        super().__init__(parent)
        self.parent = parent
        
        # Split Tabbed Frame inside DevTools
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        
        # DOM Tree view tab
        self.dom_tab = ttk.Frame(self.notebook)
        self.dom_text = tk.Text(self.dom_tab, wrap="none", font=("Courier New", 11), bg="#1e1e1e", fg="#85e89d", insertbackground="white")
        self.dom_scroll_y = ttk.Scrollbar(self.dom_tab, orient="vertical", command=self.dom_text.yview)
        self.dom_scroll_x = ttk.Scrollbar(self.dom_tab, orient="horizontal", command=self.dom_text.xview)
        self.dom_text.configure(yscrollcommand=self.dom_scroll_y.set, xscrollcommand=self.dom_scroll_x.set)
        
        self.dom_scroll_y.pack(side="right", fill="y")
        self.dom_scroll_x.pack(side="bottom", fill="x")
        self.dom_text.pack(side="left", fill="both", expand=True)
        self.notebook.add(self.dom_tab, text="🔍 DOM Explorer")
        
        # CSS rules tab
        self.css_tab = ttk.Frame(self.notebook)
        self.css_text = tk.Text(self.css_tab, wrap="none", font=("Courier New", 11), bg="#1e1e1e", fg="#9ecbff", insertbackground="white")
        self.css_scroll_y = ttk.Scrollbar(self.css_tab, orient="vertical", command=self.css_text.yview)
        self.css_text.configure(yscrollcommand=self.css_scroll_y.set)
        
        self.css_scroll_y.pack(side="right", fill="y")
        self.css_text.pack(side="left", fill="both", expand=True)
        self.notebook.add(self.css_tab, text="🎨 Style Rules")
        
        # Raw source code tab
        self.source_tab = ttk.Frame(self.notebook)
        self.source_text = tk.Text(self.source_tab, wrap="none", font=("Courier New", 11), bg="#1e1e1e", fg="#ffab70", insertbackground="white")
        self.source_scroll_y = ttk.Scrollbar(self.source_tab, orient="vertical", command=self.source_text.yview)
        self.source_scroll_x = ttk.Scrollbar(self.source_tab, orient="horizontal", command=self.source_text.xview)
        self.source_text.configure(yscrollcommand=self.source_scroll_y.set, xscrollcommand=self.source_scroll_x.set)
        
        self.source_scroll_y.pack(side="right", fill="y")
        self.source_scroll_x.pack(side="bottom", fill="x")
        self.source_text.pack(side="left", fill="both", expand=True)
        self.notebook.add(self.source_tab, text="📝 Raw Source")

    def refresh_devtools(self, tab: BrowserTab):
        # 1. Update DOM Explorer
        self.dom_text.config(state="normal")
        self.dom_text.delete("1.0", "end")
        if tab.dom_tree:
            self.dom_text.insert("1.0", tab.dom_tree.dump())
        else:
            self.dom_text.insert("1.0", "No DOM Loaded")
        self.dom_text.config(state="disabled")
        
        # 2. Update Style rules
        self.css_text.config(state="normal")
        self.css_text.delete("1.0", "end")
        if tab.css_rules:
            formatted_css = ""
            for selector, rules in tab.css_rules:
                formatted_css += f"{selector} {{\n"
                for k, v in rules.items():
                    formatted_css += f"    {k}: {v};\n"
                formatted_css += "}\n\n"
            self.css_text.insert("1.0", formatted_css)
        else:
            self.css_text.insert("1.0", "No Stylesheets found")
        self.css_text.config(state="disabled")
        
        # 3. Update Raw source
        self.source_text.config(state="normal")
        self.source_text.delete("1.0", "end")
        if tab.raw_html:
            self.source_text.insert("1.0", tab.raw_html)
        else:
            self.source_text.insert("1.0", "Empty Document")
        self.source_text.config(state="disabled")


class SurfGambitApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SurfGambit Browser")
        self.root.geometry("1200x800")
        
        # Global text measurer
        self.measurer = layout.TextMeasurer()
        
        # Chrome styling colors
        self.chrome_bg = "#1e1e1e"
        self.button_bg = "#333333"
        self.button_fg = "#ffffff"
        self.entry_bg = "#2b2b2b"
        self.entry_fg = "#ffffff"
        
        self._setup_styles()
        self._build_ui()

    def _setup_styles(self):
        # ttk styling overrides
        style = ttk.Style()
        style.theme_use("clam")
        
        # Dark theme config for the tabs
        style.configure("TNotebook", background="#2d2d2d", borderwidth=0)
        style.configure("TNotebook.Tab", background="#3c3c3c", foreground="#ffffff", padding=[10, 4], borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", "#1e1e1e")])
        style.configure("TFrame", background="#1e1e1e")

    def _build_ui(self):
        # 1. Primary Navigation Bar (Row 1)
        nav_row1 = tk.Frame(self.root, bg=self.chrome_bg, padx=6, pady=4)
        nav_row1.pack(side="top", fill="x")
        
        # Styling parameters for custom button layout
        btn_opts = {
            "bg": self.button_bg, "fg": self.button_fg, "activebackground": "#555555",
            "activeforeground": "white", "bd": 0, "padx": 10, "pady": 5, "font": ("Arial", 11, "bold")
        }
        
        self.back_btn = tk.Button(nav_row1, text="◀", command=self.go_back, **btn_opts)
        self.back_btn.pack(side="left", padx=2)
        
        self.forward_btn = tk.Button(nav_row1, text="▶", command=self.go_forward, **btn_opts)
        self.forward_btn.pack(side="left", padx=2)
        
        self.refresh_btn = tk.Button(nav_row1, text="⟳", command=self.refresh_page, **btn_opts)
        self.refresh_btn.pack(side="left", padx=2)
        
        # URL input line
        self.url_entry = tk.Entry(nav_row1, bg=self.entry_bg, fg=self.entry_fg, insertbackground="white", bd=0, font=("Arial", 12))
        self.url_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        self.url_entry.bind("<Return>", lambda e: self.trigger_navigation())
        self.url_entry.bind("<FocusIn>", lambda e: self.url_entry.selection_range(0, "end"))
        
        self.go_btn = tk.Button(nav_row1, text="➔", command=self.trigger_navigation, **btn_opts)
        self.go_btn.pack(side="left", padx=2)
        
        # Search Engine Dropdown
        tk.Label(nav_row1, text="🔍:", fg="white", bg=self.chrome_bg, font=("Arial", 10)).pack(side="left", padx=2)
        self.search_engine_combo = ttk.Combobox(nav_row1, values=["Google", "DuckDuckGo", "Wikipedia"], width=11, state="readonly")
        self.search_engine_combo.set("DuckDuckGo")
        self.search_engine_combo.pack(side="left", padx=4)
        
        # Tab Actions packed on the right of Row 1 (standard modern browser style)
        self.add_tab_btn = tk.Button(nav_row1, text="➕ New Tab", command=self.new_tab, **btn_opts)
        self.add_tab_btn.pack(side="right", padx=2)
        
        self.close_tab_btn = tk.Button(nav_row1, text="❌ Close Tab", command=self.close_current_tab, **btn_opts)
        self.close_tab_btn.pack(side="right", padx=2)

        # 2. Secondary Utility Bar (Row 2) - Slightly darker background for visual layering
        nav_row2 = tk.Frame(self.root, bg="#151515", padx=6, pady=4)
        nav_row2.pack(side="top", fill="x")
        
        # Zoom controls
        tk.Label(nav_row2, text="🔍 Zoom:", fg="#aaa", bg="#151515", font=("Arial", 10)).pack(side="left", padx=4)
        self.zoom_out_btn = tk.Button(nav_row2, text="➖", command=self.zoom_out, **{**btn_opts, "bg": "#222222", "padx": 6, "pady": 2, "font": ("Arial", 9, "bold")})
        self.zoom_out_btn.pack(side="left", padx=1)
        
        self.zoom_lbl = tk.Label(nav_row2, text="100%", fg="white", bg="#151515", font=("Arial", 10, "bold"))
        self.zoom_lbl.pack(side="left", padx=6)
        
        self.zoom_in_btn = tk.Button(nav_row2, text="➕", command=self.zoom_in, **{**btn_opts, "bg": "#222222", "padx": 6, "pady": 2, "font": ("Arial", 9, "bold")})
        self.zoom_in_btn.pack(side="left", padx=1)
        
        # Separator
        tk.Label(nav_row2, text=" | ", fg="#444", bg="#151515").pack(side="left", padx=6)
        
        # In-Page Search Panel
        tk.Label(nav_row2, text="📄 Find on Page:", fg="#aaa", bg="#151515", font=("Arial", 10)).pack(side="left", padx=2)
        self.search_entry = tk.Entry(nav_row2, bg=self.entry_bg, fg=self.entry_fg, insertbackground="white", bd=0, font=("Arial", 11), width=18)
        self.search_entry.pack(side="left", padx=4, ipady=2)
        self.search_entry.bind("<Return>", lambda e: self.trigger_search())
        
        self.find_btn = tk.Button(nav_row2, text="Find", command=self.trigger_search, **{**btn_opts, "bg": "#2d2d2d", "padx": 10, "pady": 2, "font": ("Arial", 9, "bold")})
        self.find_btn.pack(side="left", padx=2)
        
        self.clear_btn = tk.Button(nav_row2, text="Clear", command=self.clear_search, **{**btn_opts, "bg": "#2d2d2d", "padx": 10, "pady": 2, "font": ("Arial", 9, "bold")})
        self.clear_btn.pack(side="left", padx=2)
        
        # Developer Console Button (packed on the right of Row 2)
        self.devtools_btn = tk.Button(nav_row2, text="🛠 DevTools Console", command=self.toggle_devtools, **{**btn_opts, "bg": "#2d2d2d", "padx": 12, "pady": 2, "font": ("Arial", 10, "bold")})
        self.devtools_btn.pack(side="right", padx=4)

        # 3. Main horizontal paned layout (Split Browser / DevTools)
        self.paned_window = tk.PanedWindow(self.root, orient="horizontal", bd=0, sashwidth=4, bg="#2d2d2d")
        self.paned_window.pack(fill="both", expand=True)
        
        # Left pane: Tab manager notebook
        self.notebook = ttk.Notebook(self.paned_window)
        self.paned_window.add(self.notebook)
        
        # Right pane: DevTools Panel
        self.devtools_panel = DevToolsFrame(self.paned_window)
        self.devtools_visible = False
        
        # Tab notebook navigation callbacks
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        
        # 3. Status Bar
        self.status_bar = tk.Label(self.root, text="Done", bd=1, relief="sunken", anchor="w", bg="#151515", fg="#aaa", font=("Arial", 10), padx=4, pady=4)
        self.status_bar.pack(side="bottom", fill="x")
        
        # Create initial startup tab
        self.new_tab()
        
        # Bind Ctrl+F for quick search focus
        self.root.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.root.bind("<Control-F>", lambda e: self.search_entry.focus_set())

    def new_tab(self):
        tab = BrowserTab(self.notebook, self)
        self.notebook.add(tab, text="New Tab")
        self.notebook.select(tab)
        return tab

    def close_current_tab(self):
        # Allow closing tabs if we have more than one tab open
        if self.notebook.index("end") > 1:
            current_idx = self.notebook.index("current")
            self.notebook.forget(current_idx)
        else:
            self.status_bar.config(text="Cannot close the last remaining tab!")

    def trigger_search(self):
        tab = self.get_active_tab()
        if tab:
            term = self.search_entry.get().strip()
            tab.search_term = term
            tab.render()

    def clear_search(self):
        self.search_entry.delete(0, "end")
        tab = self.get_active_tab()
        if tab:
            tab.search_term = ""
            tab.render()
            self.status_bar.config(text="Done")

    def trigger_navigation(self):
        tab = self.get_active_tab()
        if tab:
            url = self.url_entry.get().strip()
            tab.navigate_to(url)

    def refresh_page(self):
        tab = self.get_active_tab()
        if tab:
            tab.navigate_to(tab.current_url, is_history_action=True)

    def go_back(self):
        tab = self.get_active_tab()
        if tab and tab.back_stack:
            prev_url = tab.back_stack.pop()
            tab.forward_stack.append(tab.current_url)
            tab.navigate_to(prev_url, is_history_action=True)

    def go_forward(self):
        tab = self.get_active_tab()
        if tab and tab.forward_stack:
            next_url = tab.forward_stack.pop()
            tab.back_stack.append(tab.current_url)
            tab.navigate_to(next_url, is_history_action=True)

    def zoom_in(self):
        tab = self.get_active_tab()
        if tab and tab.zoom_level < 3.0:
            tab.zoom_level += 0.1
            self.zoom_lbl.config(text=f"{int(tab.zoom_level * 100)}%")
            tab.trigger_layout()

    def zoom_out(self):
        tab = self.get_active_tab()
        if tab and tab.zoom_level > 0.5:
            tab.zoom_level -= 0.1
            self.zoom_lbl.config(text=f"{int(tab.zoom_level * 100)}%")
            tab.trigger_layout()

    def toggle_devtools(self):
        if self.devtools_visible:
            self.paned_window.forget(self.devtools_panel)
            self.devtools_visible = False
            self.devtools_btn.config(bg=self.button_bg)
        else:
            self.paned_window.add(self.devtools_panel, width=450)
            self.devtools_visible = True
            self.devtools_btn.config(bg="#00adb5")
            tab = self.get_active_tab()
            if tab:
                self.devtools_panel.refresh_devtools(tab)

    def on_tab_changed(self, event):
        tab = self.get_active_tab()
        if tab:
            self.update_ui_state(tab)

    def update_ui_state(self, tab: BrowserTab):
        # Update URL Entry box
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, tab.current_url)
        
        # Update back/forward button states
        self.back_btn.config(state="normal" if tab.back_stack else "disabled")
        self.forward_btn.config(state="normal" if tab.forward_stack else "disabled")
        
        # Update Zoom displays
        self.zoom_lbl.config(text=f"{int(tab.zoom_level * 100)}%")
        
        # Update tab title header
        current_idx = self.notebook.index("current")
        title = tab.current_url
        if title.startswith("http://"):
            title = title[7:]
        elif title.startswith("https://"):
            title = title[8:]
        if len(title) > 20:
            title = title[:17] + "..."
            
        self.notebook.tab(current_idx, text=title)
        
        # Refresh Developer Console side-view
        if self.devtools_visible:
            self.devtools_panel.refresh_devtools(tab)

    def show_loading_spinner(self, is_loading: bool):
        if is_loading:
            self.refresh_btn.config(text="⏳", state="disabled")
        else:
            self.refresh_btn.config(text="⟳", state="normal")

    def get_active_tab(self) -> Optional[BrowserTab]:
        selected = self.notebook.select()
        if selected:
            return self.notebook.nametowidget(selected)
        return None

    def start(self):
        self.root.mainloop()


def orig_escape(text: str) -> str:
    # Basic HTML tag string sanitizer for error rendering
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    app = SurfGambitApp()
    app.start()
