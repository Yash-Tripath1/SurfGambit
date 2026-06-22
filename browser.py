import tkinter as tk
from tkinter import ttk
import threading
import urllib.parse
import json
import os
import time
import base64
from typing import Dict, List, Tuple, Optional

import network
import parser
import layout

HUB_FILE = "bookmarks.json"
DOWNLOADS_FILE = "downloads.json"

def load_hub_data() -> Dict[str, List[Dict[str, str]]]:
    if not os.path.exists(HUB_FILE):
        return {"bookmarks": [], "history": []}
    try:
        with open(HUB_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"bookmarks": [], "history": []}

def save_hub_data(data: Dict[str, List[Dict[str, str]]]):
    try:
        with open(HUB_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass

def load_downloads_data() -> List[Dict[str, str]]:
    if not os.path.exists(DOWNLOADS_FILE):
        return []
    try:
        with open(DOWNLOADS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_downloads_data(data: List[Dict[str, str]]):
    try:
        with open(DOWNLOADS_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass

WELCOME_HTML = """
<html>
<head>
    <style>
        body { 
            font-family: sans-serif; 
            background-color: #000000; 
            color: #e0e0e0; 
            margin: 40px; 
            text-align: center; 
        }
        .card { 
            background-color: #0d0d0d; 
            border: 1px solid #222; 
            padding: 25px; 
            margin: 20px auto; 
            max-width: 650px; 
            text-align: left; 
            border-radius: 8px;
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
        <alien></alien>
    </div>
    
    <div style="text-align: center; margin-bottom: 20px; margin-top: 15px;">
        <a href="surfgambit://bookmarks" style="margin-right: 15px; font-weight: bold;">⭐ Bookmarks</a> | 
        <a href="surfgambit://history" style="margin-right: 15px; font-weight: bold;">📜 History Log</a> |
        <a href="surfgambit://downloads" style="font-weight: bold;">⬇️ Downloads</a>
    </div>

    <div class="card">
        <h2>🚀 Deeply Custom Architecture</h2>
        <p>This browser is written from scratch without high-level rendering frameworks, implementing a modular engine model:</p>
        <div class="tech-list">
            - [network.py]: TCP Sockets, TLS, Gzip decompression, Chunked Transfer Decoding.<br>
            - [cookies.py]: Custom stateful Cookie Jar parsing and injecting active sessions.<br>
            - [accounts.py]: Secure salted SHA-256 cryptographies for primitive local accounts.<br>
            - [parser.py]: Custom state-machine HTML DOM Parser and Cascading CSS Styling Engine.<br>
            - [layout.py]: Vertical block and horizontal inline-wrap layout, plus alignment controls.
        </div>
    </div>
    
    <div class="card">
        <h2>🖼 PNG/GIF Image Support</h2>
        <p>This browser pulls actual image resources directly through raw socket streams, decodes them via standard library PhotoImage on the main loop, and reflows layouts on image dimensions detection.</p>
        <p>Try loading any standard image-heavy page. JPEGs fallback gracefully to text-based alt placeholders.</p>
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
        <p>Try entering these lightweight URLs in the address bar above, or type search terms to query DuckDuckGo directly:</p>
        <p>
            - <a href="http://example.com">http://example.com</a> (Standard HTTP test site)<br>
            - <a href="http://neverssl.com">http://neverssl.com</a> (Great raw no-SSL sandbox)<br>
            - <a href="https://text.npr.org">https://text.npr.org</a> (NPR News Text-Only)<br>
            - <a href="https://news.ycombinator.com">https://news.ycombinator.com</a> (Hacker News)
        </p>
    </div>
    
    <div style="color: #444; font-size: 12px; margin-top: 40px;">
        SurfGambit Client v1.3 | Standalone Sandbox Session
    </div>
</body>
</html>
"""

class RetroAlienInvader(tk.Canvas):
    def __init__(self, parent, size=24, color="#00adb5", bg="#151515", main_app=None):
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0)
        self.size = size
        self.color = color
        self.bg = bg
        self.main_app = main_app
        
        self.frames = [
            # Frame 1: Standard space invader (standing)
            [
                "  X  X  ",
                " X XXX X",
                "XXXXXXXX",
                "XX XXX XX",
                "XXXXXXXX",
                "  XXXX  ",
                " X    X ",
                "X      X"
            ],
            # Frame 2: Legs closed
            [
                "  X  X  ",
                " X XXX X",
                "XXXXXXXX",
                "XX XXX XX",
                "XXXXXXXX",
                " XXXXXX ",
                "X      X",
                " X    X "
            ],
            # Frame 3: Arms raised
            [
                "X X  X X",
                " X XXX X",
                "XXXXXXXX",
                "XX XXX XX",
                "XXXXXXXX",
                "  XXXX  ",
                " X    X ",
                "X      X"
            ],
            # Frame 4: Squished cheeks dancing!
            [
                "  X  X  ",
                "X XXXXXX",
                "XXXXXXXX",
                "X XXXX X",
                "XXXXXXXX",
                "  XXXX  ",
                " X    X ",
                "  X  X  "
            ]
        ]
        self.current_frame = 0
        
        self.bind("<Enter>", self.on_hover_enter)
        self.bind("<Leave>", self.on_hover_leave)
        
        self.animate()

    def animate(self):
        # Crucial Tkinter fix: stop animation loops if the widget has been destroyed!
        # This prevents "invalid command name" after-script crashes when navigating.
        if not self.winfo_exists():
            return
            
        self.delete("all")
        frame = self.frames[self.current_frame]
        pixel_size = self.size / 8
        
        for r, row in enumerate(frame):
            for c, char in enumerate(row):
                if char == "X":
                    x1 = c * pixel_size
                    y1 = r * pixel_size
                    x2 = x1 + pixel_size
                    y2 = y1 + pixel_size
                    self.create_rectangle(x1, y1, x2, y2, fill=self.color, outline="")
                    
        self.current_frame = (self.current_frame + 1) % len(self.frames)
        self.after(250, self.animate) # slightly faster dance speed!

    def on_hover_enter(self, event):
        if self.main_app:
            self.main_app.status_bar.config(text="👾: we wa wu!")

    def on_hover_leave(self, event):
        if self.main_app:
            self.main_app.status_bar.config(text="Done")


class BrowserTab(tk.Frame):
    def __init__(self, parent: tk.Frame, main_browser: 'SurfGambitApp'):
        super().__init__(parent, bg="#000000")
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
        
        # Live Forms and Widgets Tracking
        self.canvas_widgets: List[tk.Widget] = []
        self.form_entries: Dict[parser.HTMLNode, tk.Entry] = {}
        
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
        
        # Drag Text Selection State & Bindings
        self.select_start_x = None
        self.select_start_y = None
        self.select_end_x = None
        self.select_end_y = None
        self.selected_text_content = ""
        
        self.canvas.bind("<ButtonPress-1>", self.on_select_start)
        self.canvas.bind("<B1-Motion>", self.on_select_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_select_end)
        
        # Right-Click Context Copy Menu
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="📋 Copy Selection", command=self.copy_selection)
        
        self.canvas.bind("<Button-3>", self.show_context_menu)
        self.canvas.bind("<Button-2>", self.show_context_menu)
        
        # Resize detection
        self.canvas.bind("<Configure>", self.on_resize)
        self.last_width = 0
        
        # Initial Welcome Load
        self.load_welcome_page()

    def load_welcome_page(self):
        self.current_url = "surfgambit://welcome"
        
        # Check active session status via Cookies
        import cookies
        user = None
        cookie_header = cookies.jar.get_cookie_header("https://surfgambit.com")
        if cookie_header:
            parts = cookie_header.split(";")
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k.strip() == "session_user":
                        user = v.strip()
                        break
                        
        welcome_html = WELCOME_HTML
        if user:
            greeting = f"""
            <div style="text-align: center; margin-top: 10px; margin-bottom: 5px;">
                <p style="color: #00adb5; font-size: 16px; font-weight: bold;">👾 Welcome back, {user}! (Logged in via Secure Cookie)</p>
                <a href="surfgambit://logout" style="font-size: 13px; color: #ff5722; font-weight: bold;">Sign Out</a>
            </div>
            """
            welcome_html = welcome_html.replace('<h1>SurfGambit Browser</h1>', f'<h1>SurfGambit Browser</h1>{greeting}')
        else:
            login_link = """
            <div style="text-align: center; margin-top: 10px; margin-bottom: 5px;">
                <a href="surfgambit://login" style="font-size: 14px; color: #00adb5; font-weight: bold;">👤 Sign In / Register</a>
            </div>
            """
            welcome_html = welcome_html.replace('<h1>SurfGambit Browser</h1>', f'<h1>SurfGambit Browser</h1>{login_link}')
            
        self.raw_html = welcome_html
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

        # Intercept and process direct file download URLs
        parsed_path = urllib.parse.urlparse(url).path
        download_exts = {".zip", ".pdf", ".exe", ".dmg", ".pkg", ".tar.gz", ".rar", ".mp3", ".mp4", ".bin", ".iso"}
        is_download = any(parsed_path.endswith(ext) for ext in download_exts)
        
        if is_download:
            self.main_browser.status_bar.config(text=f"Streaming download for {os.path.basename(parsed_path)}...")
            threading.Thread(target=self._async_download_file, args=(url,), daemon=True).start()
            self.navigate_to("surfgambit://downloads", is_history_action=True)
            return

        # Handle Registration Form Submissions
        if url.startswith("surfgambit://register-submit"):
            parsed_query = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed_query.query)
            username = params.get("reg_username", [""])[0]
            password = params.get("reg_password", [""])[0]
            
            import accounts
            success, msg = accounts.register_user(username, password)
            if success:
                self.navigate_to(f"surfgambit://login?msg={urllib.parse.quote(msg)}", is_history_action=True)
            else:
                self.navigate_to(f"surfgambit://register?err={urllib.parse.quote(msg)}", is_history_action=True)
            return

        # Handle Login Form Submissions
        if url.startswith("surfgambit://login-submit"):
            parsed_query = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed_query.query)
            username = params.get("login_username", [""])[0]
            password = params.get("login_password", [""])[0]
            
            import accounts
            import cookies
            import secrets
            
            if accounts.verify_user(username, password):
                session_id = secrets.token_hex(32)
                # Set session cookies in custom cookies manager
                cookies.jar.extract_cookies("https://surfgambit.com", [
                    ("Set-Cookie", f"session_user={username}; Domain=.surfgambit.com; Path=/; Max-Age=3600"),
                    ("Set-Cookie", f"session_token={session_id}; Domain=.surfgambit.com; Path=/; Max-Age=3600")
                ])
                self.navigate_to("surfgambit://welcome", is_history_action=True)
            else:
                msg = "Invalid username or password!"
                self.navigate_to(f"surfgambit://login?err={urllib.parse.quote(msg)}", is_history_action=True)
            return

        # Handle Logout Actions
        if url == "surfgambit://logout":
            import cookies
            # Erase cookies for surfgambit.com
            cookies.jar.cookies = [c for c in cookies.jar.cookies if c.domain != ".surfgambit.com" and c.domain != "surfgambit.com"]
            cookies.jar.save()
            self.navigate_to("surfgambit://welcome", is_history_action=True)
            return

        # Render Registration page
        if url.startswith("surfgambit://register"):
            parsed_query = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed_query.query)
            err_msg = params.get("err", [""])[0]
            err_html = f'<p style="color: #ff5722; font-weight: bold; text-align: center;">🛑 {err_msg}</p>' if err_msg else ""
            
            self.current_url = url
            self.raw_html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: sans-serif; background-color: #000000; color: #e0e0e0; margin: 40px; text-align: center; }}
                    h1 {{ color: #00adb5; }}
                    .card {{ background-color: #0d0d0d; border: 1px solid #222; padding: 30px; margin: 20px auto; max-width: 450px; text-align: left; border-radius: 8px; }}
                    a {{ color: #00adb5; font-weight: bold; text-decoration: underline; }}
                    .nav {{ margin-bottom: 25px; }}
                    input, button {{ width: 100%; height: 28px; margin-bottom: 15px; border-radius: 4px; }}
                </style>
            </head>
            <body>
                <h1>SurfGambit Hub</h1>
                <div class="nav">
                    <a href="surfgambit://welcome">Home</a> | 
                    <a href="surfgambit://bookmarks">Bookmarks</a> | 
                    <a href="surfgambit://history">History</a>
                </div>
                
                <div class="card">
                    <h2 style="color: #00adb5; margin-top: 0px; margin-bottom: 20px; text-align: center;">👤 Create Secure Account</h2>
                    {err_html}
                    <form action="surfgambit://register-submit" method="GET">
                        <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">Username</p>
                        <input type="text" name="reg_username" placeholder="Enter username"><br>
                        
                        <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">Password</p>
                        <input type="password" name="reg_password" placeholder="Enter password"><br>
                        
                        <button type="submit" style="background-color: #00adb5; color: white; border: none; font-weight: bold; cursor: pointer; height: 32px; margin-top: 10px;">Create Account</button>
                    </form>
                    <p style="font-size: 13px; text-align: center; margin-top: 15px; color: #888;">Already have an account? <a href="surfgambit://login">Sign In</a></p>
                </div>
            </body>
            </html>
            """
            self.dom_tree = parser.HTMLParser(self.raw_html).parse()
            self.css_rules = parser.get_style_sheets(self.dom_tree)
            parser.resolve_styles(self.dom_tree, self.css_rules)
            self.trigger_layout()
            self.main_browser.update_ui_state(self)
            return

        # Render Login page
        if url.startswith("surfgambit://login"):
            parsed_query = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed_query.query)
            err_msg = params.get("err", [""])[0]
            success_msg = params.get("msg", [""])[0]
            
            banner_html = ""
            if err_msg:
                banner_html = f'<p style="color: #ff5722; font-weight: bold; text-align: center;">🛑 {err_msg}</p>'
            elif success_msg:
                banner_html = f'<p style="color: #4caf50; font-weight: bold; text-align: center;">💚 {success_msg}</p>'
                
            self.current_url = url
            self.raw_html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: sans-serif; background-color: #000000; color: #e0e0e0; margin: 40px; text-align: center; }}
                    h1 {{ color: #00adb5; }}
                    .card {{ background-color: #0d0d0d; border: 1px solid #222; padding: 30px; margin: 20px auto; max-width: 450px; text-align: left; border-radius: 8px; }}
                    a {{ color: #00adb5; font-weight: bold; text-decoration: underline; }}
                    .nav {{ margin-bottom: 25px; }}
                    input, button {{ width: 100%; height: 28px; margin-bottom: 15px; border-radius: 4px; }}
                </style>
            </head>
            <body>
                <h1>SurfGambit Hub</h1>
                <div class="nav">
                    <a href="surfgambit://welcome">Home</a> | 
                    <a href="surfgambit://bookmarks">Bookmarks</a> | 
                    <a href="surfgambit://history">History</a>
                </div>
                
                <div class="card">
                    <h2 style="color: #00adb5; margin-top: 0px; margin-bottom: 20px; text-align: center;">🔑 Sign In</h2>
                    {banner_html}
                    <form action="surfgambit://login-submit" method="GET">
                        <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">Username</p>
                        <input type="text" name="login_username" placeholder="Enter username"><br>
                        
                        <p style="font-size: 13px; color: #aaa; margin-bottom: 5px;">Password</p>
                        <input type="password" name="login_password" placeholder="Enter password"><br>
                        
                        <button type="submit" style="background-color: #00adb5; color: white; border: none; font-weight: bold; cursor: pointer; height: 32px; margin-top: 10px;">Sign In</button>
                    </form>
                    <p style="font-size: 13px; text-align: center; margin-top: 15px; color: #888;">Don't have an account? <a href="surfgambit://register">Create one</a></p>
                </div>
            </body>
            </html>
            """
            self.dom_tree = parser.HTMLParser(self.raw_html).parse()
            self.css_rules = parser.get_style_sheets(self.dom_tree)
            parser.resolve_styles(self.dom_tree, self.css_rules)
            self.trigger_layout()
            self.main_browser.update_ui_state(self)
            return

        # Handle Internal Command Actions
        if url == "surfgambit://clear-history":
            hub = load_hub_data()
            hub["history"] = []
            save_hub_data(hub)
            self.navigate_to("surfgambit://history", is_history_action=True)
            return

        if url == "surfgambit://clear-bookmarks":
            hub = load_hub_data()
            hub["bookmarks"] = []
            save_hub_data(hub)
            self.navigate_to("surfgambit://bookmarks", is_history_action=True)
            return

        # Render Bookmarks Hub
        if url == "surfgambit://bookmarks":
            if not is_history_action and self.current_url != url:
                self.back_stack.append(self.current_url)
                self.forward_stack.clear()
            self.current_url = url
            
            hub = load_hub_data()
            bookmark_items_html = ""
            if hub["bookmarks"]:
                for bm in hub["bookmarks"]:
                    title = bm.get("title", bm["url"])
                    b_url = bm["url"]
                    bookmark_items_html += f"""
                    <div class="item">
                        <a href="{b_url}">{title}</a><br>
                        <span class="url">{b_url}</span>
                    </div>
                    """
            else:
                bookmark_items_html = "<p style='color: #888;'>No bookmarks saved yet. Click the ⭐ button on any page to add one!</p>"
                
            self.raw_html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: sans-serif; background-color: #000000; color: #e0e0e0; margin: 40px; text-align: center; }}
                    h1 {{ color: #00adb5; }}
                    .card {{ background-color: #0d0d0d; border: 1px solid #222; padding: 25px; margin: 20px auto; max-width: 650px; text-align: left; border-radius: 8px; }}
                    .item {{ margin-bottom: 15px; border-bottom: 1px solid #222; padding-bottom: 10px; }}
                    a {{ color: #00adb5; font-weight: bold; text-decoration: underline; }}
                    .url {{ color: #888; font-size: 12px; font-family: monospace; }}
                    .nav {{ margin-bottom: 25px; }}
                    .clear-btn {{ color: #ff5722; font-weight: bold; }}
                </style>
            </head>
            <body>
                <h1>SurfGambit Bookmarks</h1>
                <div class="nav">
                    <a href="surfgambit://welcome">Home</a> | 
                    <a href="surfgambit://bookmarks">Bookmarks</a> | 
                    <a href="surfgambit://history">History</a> |
                    <a href="surfgambit://downloads">Downloads</a>
                </div>
                <div class="card">
                    <h2>⭐ Saved Bookmarks</h2>
                    {bookmark_items_html}
                    <br>
                    <a href="surfgambit://clear-bookmarks" class="clear-btn">❌ Clear All Bookmarks</a>
                </div>
            </body>
            </html>
            """
            self.dom_tree = parser.HTMLParser(self.raw_html).parse()
            self.css_rules = parser.get_style_sheets(self.dom_tree)
            parser.resolve_styles(self.dom_tree, self.css_rules)
            self.trigger_layout()
            self.main_browser.update_ui_state(self)
            return

        # Render History Hub
        if url == "surfgambit://history":
            if not is_history_action and self.current_url != url:
                self.back_stack.append(self.current_url)
                self.forward_stack.clear()
            self.current_url = url
            
            hub = load_hub_data()
            history_items_html = ""
            if hub["history"]:
                for h in hub["history"]:
                    h_url = h["url"]
                    timestamp = h.get("time", "")
                    history_items_html += f"""
                    <div class="item">
                        <span style="color: #666; font-size: 12px;">[{timestamp}]</span> <a href="{h_url}">{h_url}</a>
                    </div>
                    """
            else:
                history_items_html = "<p style='color: #888;'>No visited pages recorded yet.</p>"
                
            self.raw_html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: sans-serif; background-color: #000000; color: #e0e0e0; margin: 40px; text-align: center; }}
                    h1 {{ color: #00adb5; }}
                    .card {{ background-color: #0d0d0d; border: 1px solid #333; padding: 25px; margin: 20px auto; max-width: 650px; text-align: left; border-radius: 8px; }}
                    .item {{ margin-bottom: 12px; font-size: 14px; }}
                    a {{ color: #00adb5; font-weight: bold; text-decoration: underline; }}
                    .nav {{ margin-bottom: 25px; }}
                    .clear-btn {{ color: #ff5722; font-weight: bold; }}
                </style>
            </head>
            <body>
                <h1>SurfGambit Navigation Logs</h1>
                <div class="nav">
                    <a href="surfgambit://welcome">Home</a> | 
                    <a href="surfgambit://bookmarks">Bookmarks</a> | 
                    <a href="surfgambit://history">History</a> |
                    <a href="surfgambit://downloads">Downloads</a>
                </div>
                <div class="card">
                    <h2>📜 Browsing History</h2>
                    {history_items_html}
                    <br>
                    <a href="surfgambit://clear-history" class="clear-btn">❌ Clear History Logs</a>
                </div>
            </body>
            </html>
            """
            self.dom_tree = parser.HTMLParser(self.raw_html).parse()
            self.css_rules = parser.get_style_sheets(self.dom_tree)
            parser.resolve_styles(self.dom_tree, self.css_rules)
            self.trigger_layout()
            self.main_browser.update_ui_state(self)
            return

        # Render Downloads Manager Hub
        if url == "surfgambit://downloads":
            if not is_history_action and self.current_url != url:
                self.back_stack.append(self.current_url)
                self.forward_stack.clear()
            self.current_url = url
            
            dw_list = load_downloads_data()
            dw_items_html = ""
            if dw_list:
                for d in dw_list:
                    name = d["name"]
                    status = d["status"]
                    time_s = d["time"]
                    dw_items_html += f"""
                    <div class="item">
                        <span style="font-weight: bold; color: #00adb5;">{name}</span><br>
                        <span style="font-size: 12px; color: #888;">Time: {time_s} | Status: {status}</span>
                    </div>
                    """
            else:
                dw_items_html = "<p style='color: #888;'>No downloads recorded.</p>"
                
            self.raw_html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: sans-serif; background-color: #121212; color: #e0e0e0; margin: 40px; text-align: center; }}
                    h1 {{ color: #00adb5; }}
                    .card {{ background-color: #1e1e1e; border: 1px solid #333; padding: 25px; margin: 20px auto; max-width: 650px; text-align: left; border-radius: 8px; }}
                    .item {{ margin-bottom: 12px; font-size: 14px; border-bottom: 1px solid #222; padding-bottom: 10px; }}
                    a {{ color: #00adb5; font-weight: bold; text-decoration: underline; }}
                    .nav {{ margin-bottom: 25px; }}
                </style>
            </head>
            <body>
                <h1>SurfGambit Downloads</h1>
                <div class="nav">
                    <a href="surfgambit://welcome">Home</a> | 
                    <a href="surfgambit://bookmarks">Bookmarks</a> | 
                    <a href="surfgambit://history">History</a> |
                    <a href="surfgambit://downloads">Downloads</a>
                </div>
                <div class="card">
                    <h2>⬇️ Active &amp; Completed Downloads</h2>
                    {dw_items_html}
                </div>
            </body>
            </html>
            """
            self.dom_tree = parser.HTMLParser(self.raw_html).parse()
            self.css_rules = parser.get_style_sheets(self.dom_tree)
            parser.resolve_styles(self.dom_tree, self.css_rules)
            self.trigger_layout()
            self.main_browser.update_ui_state(self)
            return

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

    def _async_download_file(self, url: str):
        try:
            filename = os.path.basename(urllib.parse.urlparse(url).path) or "downloaded_file"
            time_s = time.strftime("%H:%M:%S")
            
            # Initial download registration
            dw_list = load_downloads_data()
            dw_list.insert(0, {"name": filename, "url": url, "status": "Downloading...", "time": time_s})
            save_downloads_data(dw_list)
            
            # Trigger download over raw socket
            resp = network.request(url)
            
            # Resolve actual Windows/system downloads path dynamically (e.g. C:\Users\tripa\Downloads)
            system_downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            os.makedirs(system_downloads, exist_ok=True)
            save_path = os.path.join(system_downloads, filename)
            
            with open(save_path, "wb") as f:
                f.write(resp.body)
                
            # Mark download complete
            dw_list = load_downloads_data()
            for d in dw_list:
                if d["url"] == url:
                    d["status"] = "Completed"
                    break
            save_downloads_data(dw_list)
            
            self.after(0, self._on_download_complete, filename)
        except Exception as e:
            self.after(0, self._on_download_error, url, str(e))

    def _on_download_complete(self, filename: str):
        self.main_browser.status_bar.config(text=f"Successfully downloaded {filename}! Saved to Downloads/")
        if self.current_url == "surfgambit://downloads":
            self.navigate_to("surfgambit://downloads", is_history_action=True)

    def _on_download_error(self, url: str, err: str):
        self.main_browser.status_bar.config(text=f"Download failed: {err}")
        dw_list = load_downloads_data()
        for d in dw_list:
            if d["url"] == url:
                d["status"] = f"Failed: {err}"
                break
        save_downloads_data(dw_list)
        if self.current_url == "surfgambit://downloads":
            self.navigate_to("surfgambit://downloads", is_history_action=True)

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
        if not self.winfo_exists() or not self.main_browser.root.winfo_exists():
            return
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
        
        # Save to local history log (excluding internal URLs)
        if not resolved_url.startswith("surfgambit://"):
            hub = load_hub_data()
            timestamp = time.strftime("%H:%M:%S")
            # Avoid duplicate consecutive logs
            if not hub["history"] or hub["history"][0]["url"] != resolved_url:
                hub["history"].insert(0, {"url": resolved_url, "time": timestamp})
                hub["history"] = hub["history"][:100] # limit size
                save_hub_data(hub)
        
        # Start async loading of image nodes
        self._start_image_downloads()
        
        # Render page
        self.trigger_layout()
        
        self.main_browser.status_bar.config(text="Done")
        self.main_browser.update_ui_state(self)

    def _on_load_error(self, url: str, err_msg: str):
        if not self.winfo_exists() or not self.main_browser.root.winfo_exists():
            return
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
        if not self.winfo_exists() or not self.main_browser.root.winfo_exists():
            return
        self.loading_images.discard(img_url)
        try:
            import base64
            # Encode raw binary data to Base64 (guarantees 100% universal Tcl/Tk image decoding on Windows!)
            b64_data = base64.b64encode(img_data).decode("utf-8")
            photo = tk.PhotoImage(data=b64_data)
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
        if not self.winfo_exists() or not self.main_browser.root.winfo_exists():
            return
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
        
        # Destroy and clear live form inputs from previous render
        for widget in getattr(self, "canvas_widgets", []):
            try:
                widget.destroy()
            except Exception:
                pass
        self.canvas_widgets = []
        self.form_entries = {}
        
        if not self.layout_tree:
            return
            
        # Dynamically propagate page background color to the canvas viewport.
        # This removes the ugly white boundaries around dark-themed web pages!
        is_welcome = self.current_url.startswith("surfgambit://")
        bg_color = "#000000" if is_welcome else "white"
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
            bg_color = "#000000" if is_welcome else "white"
            
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

    def _draw_rounded_rect(self, x1, y1, x2, y2, radius=8, fill="", outline="", width=1):
        if radius <= 0:
            self.canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=width)
            return
            
        # Draw filled shape
        if fill and fill.lower() != "transparent":
            self.canvas.create_arc(x1, y1, x1+2*radius, y1+2*radius, start=90, extent=90, style="pieslice", fill=fill, outline="")
            self.canvas.create_arc(x2-2*radius, y1, x2, y1+2*radius, start=0, extent=90, style="pieslice", fill=fill, outline="")
            self.canvas.create_arc(x2-2*radius, y2-2*radius, x2, y2, start=270, extent=90, style="pieslice", fill=fill, outline="")
            self.canvas.create_arc(x1, y2-2*radius, x1+2*radius, y2, start=180, extent=90, style="pieslice", fill=fill, outline="")
            
            self.canvas.create_rectangle(x1+radius, y1, x2-radius, y2, fill=fill, outline="")
            self.canvas.create_rectangle(x1, y1+radius, x2, y2-radius, fill=fill, outline="")
            
        # Draw border outline
        if outline and outline.lower() != "transparent" and width > 0:
            self.canvas.create_arc(x1, y1, x1+2*radius, y1+2*radius, start=90, extent=90, style="arc", outline=outline, width=width)
            self.canvas.create_arc(x2-2*radius, y1, x2, y1+2*radius, start=0, extent=90, style="arc", outline=outline, width=width)
            self.canvas.create_arc(x2-2*radius, y2-2*radius, x2, y2, start=270, extent=90, style="arc", outline=outline, width=width)
            self.canvas.create_arc(x1, y2-2*radius, x1+2*radius, y2, start=180, extent=90, style="arc", outline=outline, width=width)
            
            self.canvas.create_line(x1+radius, y1, x2-radius, y1, fill=outline, width=width)
            self.canvas.create_line(x1+radius, y2, x2-radius, y2, fill=outline, width=width)
            self.canvas.create_line(x1, y1+radius, x1, y2-radius, fill=outline, width=width)
            self.canvas.create_line(x2, y1+radius, x2, y2-radius, fill=outline, width=width)

    def _render_box(self, box: layout.LayoutBox):
        # Parse border radius
        radius_str = box.node.style.get("border-radius")
        radius = layout.parse_px_val(radius_str, 0) if radius_str else 0
        
        # 1. Render Background Color
        bg_color = box.node.style.get("background-color")
        if bg_color and bg_color.lower() != "transparent":
            if radius > 0:
                self._draw_rounded_rect(box.x, box.y, box.x + box.width, box.y + box.height, radius=radius, fill=bg_color)
            else:
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
            if radius > 0:
                self._draw_rounded_rect(box.x, box.y, box.x + box.width, box.y + box.height, radius=radius, outline=border_c, width=border_w)
            else:
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
                            # Smart contrast: if the canvas background is dark, use white. Otherwise, black!
                            is_bg_dark = (self.canvas["bg"].lower() in ("#121212", "#1e1e1e", "#151515", "black", "#000000", "#0d0d0d", "#2d2d2d", "#1a1a1a"))
                            color = "white" if is_bg_dark else "black"
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
                            is_welcome = self.current_url.startswith("surfgambit://")
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
                            
                    elif itype == "widget":
                        tag = node.tag
                        val = node.attributes.get("value", "")
                        placeholder = node.attributes.get("placeholder", "")
                        inp_type = node.attributes.get("type", "text").lower()
                        
                        if tag == "alien":
                            # Mount larger 64x64 central dancing alien mascot on welcome page!
                            alien = RetroAlienInvader(self.canvas, size=64, color="#00adb5", bg="#1e1e1e", main_app=self.main_browser)
                            self.canvas_widgets.append(alien)
                            self.canvas.create_window(ax, ay, window=alien, width=64, height=64, anchor="nw")
                            
                        elif tag == "input" and inp_type == "submit":
                            btn_text = val or "Submit"
                            btn = tk.Button(
                                self.canvas, text=btn_text, bg="#e1e1e1", fg="black",
                                activebackground="#cccccc", bd=1, relief="raised",
                                cursor="hand2", font=("Arial", max(8, int(9 * self.zoom_level)), "bold"),
                                command=lambda n=node: self._submit_form(n)
                            )
                            self.canvas_widgets.append(btn)
                            self.canvas.create_window(ax, ay, window=btn, width=rw, height=rh, anchor="nw")
                            
                        elif tag == "button":
                            btn_text = ""
                            if node.children:
                                btn_text = "".join(c.text for c in node.children if c.is_text())
                            if not btn_text:
                                btn_text = "Submit"
                            btn = tk.Button(
                                self.canvas, text=btn_text, bg="#e1e1e1", fg="black",
                                activebackground="#cccccc", bd=1, relief="raised",
                                cursor="hand2", font=("Arial", max(8, int(9 * self.zoom_level)), "bold"),
                                command=lambda n=node: self._submit_form(n)
                            )
                            self.canvas_widgets.append(btn)
                            self.canvas.create_window(ax, ay, window=btn, width=rw, height=rh, anchor="nw")
                            
                        elif tag == "input" and inp_type in ("text", "search", "password", "email", "url"):
                            show_char = "*" if inp_type == "password" else ""
                            entry = tk.Entry(
                                self.canvas, bg="white", fg="black", insertbackground="black",
                                bd=1, relief="solid", font=("Arial", max(8, int(10 * self.zoom_level))),
                                show=show_char
                            )
                            if val:
                                entry.insert(0, val)
                            elif placeholder:
                                pass
                                
                            self.canvas_widgets.append(entry)
                            self.form_entries[node] = entry
                            
                            # Bind pressing Enter key inside Entry to submit form!
                            entry.bind("<Return>", lambda e, n=node: self._submit_form(n))
                            self.canvas.create_window(ax, ay, window=entry, width=rw, height=rh, anchor="nw")
                            
                    elif itype == "inline-block":
                        child_box = node
                        # Re-run layout at the absolute canvas ax, ay offsets!
                        # This shifts all nested children's coordinates to render inside the column!
                        layout.compute_layout(child_box, ax, ay, rw, self.main_browser.measurer)
                        self._render_box(child_box)

                    elif itype == "hr":
                        # Render division line
                        color = style.get("color", "#ccc")
                        self.canvas.create_line(ax, ay, ax + rw, ay, fill=color, width=rh)
                        
        # Recursive pass
        for child in box.children:
            # Skip child elements laid out horizontally in lines to prevent double drawing
            if child.node.style.get("display") == "inline-block" or child.node.attributes.get("class", "") == "col":
                continue
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

    def _submit_form(self, trigger_node: parser.HTMLNode):
        # 1. Walk up to find enclosing <form>
        form_node = None
        curr = trigger_node
        while curr:
            if curr.tag == "form":
                form_node = curr
                break
            curr = curr.parent
            
        if not form_node:
            return
            
        # 2. Extract action and method
        action = form_node.attributes.get("action", "")
        method = form_node.attributes.get("method", "GET").upper()
        
        # 3. Recursively find all inputs inside this form
        form_inputs = []
        def find_inputs(node):
            if node.tag in ("input", "textarea", "select"):
                form_inputs.append(node)
            for child in node.children:
                find_inputs(child)
        find_inputs(form_node)
        
        # 4. Extract values from live Entries
        params = {}
        for inp in form_inputs:
            name = inp.attributes.get("name")
            if not name:
                continue
                
            inp_type = inp.attributes.get("type", "text").lower()
            if inp_type == "submit":
                continue
                
            val = ""
            if inp in self.form_entries:
                val = self.form_entries[inp].get()
            else:
                val = inp.attributes.get("value", "")
                
            params[name] = val
            
        # 5. Form query string
        query_string = urllib.parse.urlencode(params)
        
        # 6. Resolve target URL
        target_url = urllib.parse.urljoin(self.current_url, action)
        
        # 7. Format parameters based on GET/POST
        if query_string:
            if "?" in target_url:
                target_url += "&" + query_string
            else:
                target_url += "?" + query_string
                
        # 8. Trigger navigation
        self.navigate_to(target_url)

    def on_select_start(self, event):
        self.select_start_x = self.canvas.canvasx(event.x)
        self.select_start_y = self.canvas.canvasy(event.y)
        self.canvas.delete("selection")
        self.selected_text_content = ""

    def on_select_drag(self, event):
        self.select_end_x = self.canvas.canvasx(event.x)
        self.select_end_y = self.canvas.canvasy(event.y)
        self.canvas.delete("selection")
        self.canvas.create_rectangle(
            self.select_start_x, self.select_start_y,
            self.select_end_x, self.select_end_y,
            fill="", outline="#00adb5", width=2, dash=(2, 2), tags="selection"
        )

    def on_select_end(self, event):
        if self.select_start_x is None or self.select_end_x is None:
            return
        x1, x2 = min(self.select_start_x, self.select_end_x), max(self.select_start_x, self.select_end_x)
        y1, y2 = min(self.select_start_y, self.select_end_y), max(self.select_start_y, self.select_end_y)
        
        if (x2 - x1) < 5 and (y2 - y1) < 5:
            self.canvas.delete("selection")
            return
            
        words_found = []
        def collect_text(box):
            if box.lines:
                for line, line_h in box.lines:
                    for item in line:
                        itype, node, style, text, rx, ry, rw, rh = item
                        if itype == "word":
                            ax = box.x + rx + box.padding_left
                            ay = box.y + ry + box.padding_top
                            cx = ax + rw/2
                            cy = ay + rh/2
                            if x1 <= cx <= x2 and y1 <= cy <= y2:
                                words_found.append((ay, ax, text))
            for child in box.children:
                collect_text(child)
                
        if self.layout_tree:
            collect_text(self.layout_tree)
            
        words_found.sort(key=lambda w: (w[0], w[1]))
        self.selected_text_content = " ".join(w[2] for w in words_found)
        
        if self.selected_text_content.strip():
            self.main_browser.status_bar.config(text=f"Selected: {len(self.selected_text_content)} chars. Right-click to Copy.")

    def show_context_menu(self, event):
        self.context_menu.post(event.x_root, event.y_root)

    def copy_selection(self):
        if self.selected_text_content.strip():
            self.clipboard_clear()
            self.clipboard_append(self.selected_text_content)
            self.main_browser.status_bar.config(text="Selection copied to clipboard!")


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
        
        # Chrome Custom Tab Bar State
        self.tabs: List[BrowserTab] = []
        self.active_tab_idx: int = -1
        
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
        # 1. Custom Google Chrome-style Tab Bar Frame (Row 1)
        self.tab_bar_frame = tk.Frame(self.root, bg="#111111", height=32)
        self.tab_bar_frame.pack(side="top", fill="x")
        
        # 2. Primary Navigation Bar (Row 2)
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
        
        # Bookmark Quick-Save Button
        self.bookmark_btn = tk.Button(nav_row1, text="⭐", command=self.add_bookmark, **{**btn_opts, "padx": 8})
        self.bookmark_btn.pack(side="left", padx=2)
        
        # Search Engine Dropdown
        tk.Label(nav_row1, text="🔍:", fg="white", bg=self.chrome_bg, font=("Arial", 10)).pack(side="left", padx=2)
        self.search_engine_combo = ttk.Combobox(nav_row1, values=["Google", "DuckDuckGo", "Wikipedia"], width=11, state="readonly")
        self.search_engine_combo.set("DuckDuckGo")
        self.search_engine_combo.pack(side="left", padx=4)

        # 3. Secondary Utility Bar (Row 3) - Slightly darker background for visual layering
        nav_row2 = tk.Frame(self.root, bg="#151515", padx=6, pady=4)
        nav_row2.pack(side="top", fill="x")
        
        # Retro Pixel Alien Invader (Pulsing Mascot)
        self.alien = RetroAlienInvader(nav_row2, size=24, color="#00adb5", bg="#151515", main_app=self)
        self.alien.pack(side="left", padx=8)
        
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
        
        # Developer Console Button (packed on the right of Row 3)
        self.devtools_btn = tk.Button(nav_row2, text="🛠 DevTools Console", command=self.toggle_devtools, **{**btn_opts, "bg": "#2d2d2d", "padx": 12, "pady": 2, "font": ("Arial", 10, "bold")})
        self.devtools_btn.pack(side="right", padx=4)

        # Main split paned layout (Split Viewport Frame / DevTools Panel)
        self.paned_window = tk.PanedWindow(self.root, orient="horizontal", bd=0, sashwidth=4, bg="#2d2d2d")
        self.paned_window.pack(fill="both", expand=True)

        # 4. Viewport frame container (Holds active BrowserTab frames dynamically parented to PanedWindow)
        self.viewport_frame = tk.Frame(self.paned_window, bg="#000000")
        self.paned_window.add(self.viewport_frame)
        
        # Right pane: DevTools Panel
        self.devtools_panel = DevToolsFrame(self.paned_window)
        self.devtools_visible = False
        
        # 3. Status Bar
        self.status_bar = tk.Label(self.root, text="Done", bd=1, relief="sunken", anchor="w", bg="#151515", fg="#aaa", font=("Arial", 10), padx=4, pady=4)
        self.status_bar.pack(side="bottom", fill="x")
        
        # Create initial startup tab
        self.new_tab()

    def update_tab_bar(self):
        # Clear existing tab frames in tab bar
        for widget in self.tab_bar_frame.winfo_children():
            widget.destroy()
            
        # Draw active tabs
        for idx, tab in enumerate(self.tabs):
            tab_frame = tk.Frame(self.tab_bar_frame, bg="#2d2d2d" if idx == self.active_tab_idx else "#151515", padx=4, pady=2)
            tab_frame.pack(side="left", padx=2, fill="y")
            
            # Format title string
            title = tab.current_url
            if title.startswith("http://"):
                title = title[7:]
            elif title.startswith("https://"):
                title = title[8:]
            if len(title) > 16:
                title = title[:13] + "..."
                
            tab_lbl = tk.Label(
                tab_frame, text=title, bg="#2d2d2d" if idx == self.active_tab_idx else "#151515",
                fg="white", font=("Arial", 10, "bold" if idx == self.active_tab_idx else "normal"),
                cursor="hand2"
            )
            tab_lbl.pack(side="left", padx=4)
            tab_lbl.bind("<Button-1>", lambda e, i=idx: self.select_tab(i))
            
            # Tiny modern close 'x' button
            close_btn = tk.Button(
                tab_frame, text="×", bg="#2d2d2d" if idx == self.active_tab_idx else "#151515",
                fg="#ff5722", activebackground="#222222", bd=0, cursor="hand2",
                font=("Arial", 11, "bold"), padx=2, pady=0,
                command=lambda i=idx: self.close_tab_at(i)
            )
            close_btn.pack(side="left", padx=2)
            
        # Plus tab button packed adjacent to tabs
        add_tab_btn = tk.Button(
            self.tab_bar_frame, text=" ＋ ", bg="#111111", fg="white",
            activebackground="#333333", activeforeground="white", bd=0,
            font=("Arial", 11, "bold"), cursor="hand2", padx=6, pady=2,
            command=self.new_tab
        )
        add_tab_btn.pack(side="left", padx=4)

    def select_tab(self, idx: int):
        if not (0 <= idx < len(self.tabs)):
            return
            
        # Hide current active tab frame
        if self.active_tab_idx != -1 and self.active_tab_idx < len(self.tabs):
            self.tabs[self.active_tab_idx].pack_forget()
            
        self.active_tab_idx = idx
        active_tab = self.tabs[idx]
        
        # Display new active tab frame
        active_tab.pack(fill="both", expand=True)
        
        # Update UI bindings & states
        self.update_tab_bar()
        self.update_ui_state(active_tab)

    def close_tab_at(self, idx: int):
        if len(self.tabs) <= 1:
            self.status_bar.config(text="Cannot close the last remaining tab!")
            return
            
        tab = self.tabs[idx]
        tab.destroy() # safe Tkinter disposal
        self.tabs.pop(idx)
        
        # Calibrate active pointer limits
        if self.active_tab_idx >= len(self.tabs):
            self.active_tab_idx = len(self.tabs) - 1
        elif self.active_tab_idx == idx:
            self.active_tab_idx = min(idx, len(self.tabs) - 1)
            
        self.select_tab(self.active_tab_idx)

    def new_tab(self):
        tab = BrowserTab(self.viewport_frame, self)
        self.tabs.append(tab)
        self.select_tab(len(self.tabs) - 1)
        return tab

    def close_current_tab(self):
        self.close_tab_at(self.active_tab_idx)

    def add_bookmark(self):
        tab = self.get_active_tab()
        if tab:
            url = tab.current_url
            if url.startswith("surfgambit://"):
                self.status_bar.config(text="Cannot bookmark internal browser pages!")
                return
            hub = load_hub_data()
            # Prevent duplicates
            if any(bm["url"] == url for bm in hub["bookmarks"]):
                self.status_bar.config(text="Page already bookmarked!")
                return
                
            hub["bookmarks"].append({"title": url, "url": url})
            save_hub_data(hub)
            self.status_bar.config(text="Page bookmarked successfully! ⭐")

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
        
        # Refresh Developer Console side-view
        if self.devtools_visible:
            self.devtools_panel.refresh_devtools(tab)
            
        # Repaint tab bar names dynamically on titles arrival
        self.update_tab_bar()

    def show_loading_spinner(self, is_loading: bool):
        if not self.root.winfo_exists():
            return
        if is_loading:
            self.refresh_btn.config(text="⏳", state="disabled")
        else:
            self.refresh_btn.config(text="⟳", state="normal")

    def get_active_tab(self) -> Optional[BrowserTab]:
        if 0 <= self.active_tab_idx < len(self.tabs):
            return self.tabs[self.active_tab_idx]
        return None

    def start(self):
        self.root.mainloop()


def orig_escape(text: str) -> str:
    # Basic HTML tag string sanitizer for error rendering
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    app = SurfGambitApp()
    app.start()
