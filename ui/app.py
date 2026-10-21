"""
ui/app.py — the application shell: window, menus, toolbar, tabs, find bar,
downloads, devtools and the thread plumbing.

Threading contract
------------------
Only the main thread touches Tk. Worker threads call `app.ui(callable)`,
which enqueues the callable; a single `after(30, ...)` pump drains the queue.
That replaces v1's habit of calling `self.after(0, ...)` from a worker thread,
which is not thread-safe and produced the "main thread is not in main loop"
traceback whenever a page finished loading.
"""

from __future__ import annotations

import os
import queue
import tkinter as tk
import traceback
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from core.accounts import AccountStore
from core.downloads import DownloadManager
from core.layout.text import TextMeasurer
from core.net.cache import HTTPCache
from core.net.client import HTTPClient
from core.net.cookies import CookieJar
from core.search import USER_AGENTS, fetch_image_urls, normalise_input
from core.storage import data_path
from core.store import Store
from ui import pages
from ui.mascot import Mascot
from ui.tab import BrowserTab

APP_NAME = "SurfGambit"
VERSION = "3.0"

BG = "#1b1b1f"
BG_ALT = "#24242a"
FG = "#e6e6e6"
FG_DIM = "#9aa0a6"
ACCENT = "#00adb5"
BORDER = "#34343c"


class BrowserApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.root = self
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry("1280x860")
        self.minsize(720, 480)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        # A bounded pool beats one-thread-per-request: a page with 60 images
        # used to spawn 60 threads and hammer the network.
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="surf")
        self._closing = False
        self.ctrl_down = False
        self.last_source = ""
        self.profile_name: Optional[str] = None
        self._status_override: Optional[str] = None
        self._devtools: Optional[tk.Toplevel] = None
        self._devtools_text: Dict[str, tk.Text] = {}
        self._console_entry: Optional[ttk.Entry] = None
        self._downloads_window: Optional[tk.Toplevel] = None
        self._downloads_tree = None

        self.store = Store()
        self.cache = HTTPCache(data_path(os.path.join("cache", "http")))
        self.settings: Dict[str, Any] = self.store.load_settings()
        self.profile_name = self.settings.get("profile") or None

        self.cookie_jar = CookieJar(data_path("cookies.json"))
        self.client = HTTPClient(
            cookie_jar=self.cookie_jar,
            user_agent=USER_AGENTS.get(self.settings.get("user_agent_name", "Chrome"),
                                       USER_AGENTS["Chrome"]),
            verify_tls=self.settings.get("verify_tls", "on") == "on",
        )
        self.client.cache = self.cache
        self.accounts = AccountStore(data_path("accounts.json"))
        self.measurer = TextMeasurer()
        self.downloads = DownloadManager(self.store, self.client,
                                         on_update=lambda d: self.ui(
                                             lambda: self._on_download_update(d)))

        self._build_style()
        self._build_menu()
        self._build_toolbar()
        self._build_notebook()
        self._build_findbar()
        self._build_statusbar()
        self._bind_shortcuts()

        self.after(30, self._pump)
        self._open_first_tab()

    # ------------------------------------------------------------------
    # Styling / layout
    # ------------------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG_ALT,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TFrame", background=BG)
        style.configure("Toolbar.TFrame", background=BG_ALT)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Status.TLabel", background=BG_ALT, foreground=FG_DIM)
        style.configure("TButton", background=BG_ALT, foreground=FG, borderwidth=1,
                        focusthickness=0, padding=(8, 4))
        style.map("TButton",
                  background=[("active", "#33333c"), ("disabled", BG_ALT)],
                  foreground=[("disabled", "#5c5c66")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#04201f")
        style.map("Accent.TButton", background=[("active", "#00c4cc")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_ALT, foreground=FG_DIM,
                        padding=(14, 6), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", BG)],
                  foreground=[("selected", FG)])
        style.configure("TEntry", fieldbackground="#ffffff", foreground="#111111",
                        insertcolor="#111111", bordercolor="#5c5c66",
                        lightcolor="#5c5c66", darkcolor="#5c5c66", padding=5)
        style.configure("TCombobox", fieldbackground="#ffffff", background="#e9e9e9",
                        foreground="#111111", arrowcolor="#111111",
                        bordercolor="#5c5c66", lightcolor="#5c5c66",
                        darkcolor="#5c5c66", padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#ffffff"), ("disabled", "#d5d5d5")],
                  foreground=[("readonly", "#111111")],
                  selectbackground=[("readonly", "#d6f2f4")],
                  selectforeground=[("readonly", "#111111")])
        # The dropdown list is a plain Tk listbox under the hood.
        self.option_add("*TCombobox*Listbox.background", "#ffffff")
        self.option_add("*TCombobox*Listbox.foreground", "#111111")
        self.option_add("*TCombobox*Listbox.selectBackground", "#00a7b0")
        self.option_add("*TCombobox*Listbox.selectForeground", "#04141a")
        style.configure("TSeparator", background=BORDER)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self, tearoff=0, bg=BG_ALT, fg=FG,
                          activebackground=ACCENT, activeforeground="#04201f")

        file_menu = tk.Menu(menubar, tearoff=0, bg=BG_ALT, fg=FG,
                            activebackground=ACCENT, activeforeground="#04201f")
        file_menu.add_command(label="New tab", accelerator="Ctrl+T",
                              command=lambda: self.add_tab())
        file_menu.add_command(label="Close tab", accelerator="Ctrl+W",
                              command=self.close_current_tab)
        file_menu.add_separator()
        file_menu.add_command(label="Open location...", accelerator="Ctrl+L",
                              command=self.focus_address)
        file_menu.add_command(label="Save page as...", accelerator="Ctrl+S",
                              command=self.save_page)
        file_menu.add_command(label="Open file...", command=self.open_local_file)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0, bg=BG_ALT, fg=FG,
                            activebackground=ACCENT, activeforeground="#04201f")
        edit_menu.add_command(label="Find in page", accelerator="Ctrl+F",
                              command=self.focus_find)
        edit_menu.add_command(label="Copy selection", accelerator="Ctrl+C",
                              command=self.copy_selection)
        edit_menu.add_command(label="Select page address", command=self.copy_address)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0, bg=BG_ALT, fg=FG,
                            activebackground=ACCENT, activeforeground="#04201f")
        view_menu.add_command(label="Reload", accelerator="Ctrl+R",
                              command=lambda: self.current_tab().reload())
        view_menu.add_command(label="Zoom in", accelerator="Ctrl++",
                              command=lambda: self.zoom_by(0.1))
        view_menu.add_command(label="Zoom out", accelerator="Ctrl+-",
                              command=lambda: self.zoom_by(-0.1))
        view_menu.add_command(label="Reset zoom", accelerator="Ctrl+0",
                              command=lambda: self.set_zoom(1.0))
        view_menu.add_separator()
        view_menu.add_command(label="Reload without scripts",
                              accelerator="Ctrl+Shift+R",
                              command=lambda: self.current_tab().reload(without_scripts=True))
        view_menu.add_command(label="View source", accelerator="Ctrl+U",
                              command=self.view_source)
        view_menu.add_command(label="Developer tools", accelerator="F12",
                              command=self.toggle_devtools)
        view_menu.add_command(label="Full screen", accelerator="F11",
                              command=self.toggle_fullscreen)
        menubar.add_cascade(label="View", menu=view_menu)

        book_menu = tk.Menu(menubar, tearoff=0, bg=BG_ALT, fg=FG,
                            activebackground=ACCENT, activeforeground="#04201f")
        book_menu.add_command(label="Bookmark this page", accelerator="Ctrl+D",
                              command=self.bookmark_current)
        book_menu.add_command(label="Bookmark manager",
                              command=lambda: self.current_tab().navigate("surfgambit://bookmarks"))
        book_menu.add_command(label="History",
                              command=lambda: self.current_tab().navigate("surfgambit://history"))
        book_menu.add_command(label="Downloads",
                              command=lambda: self.current_tab().navigate("surfgambit://downloads"))
        book_menu.add_command(label="Download manager...", accelerator="Ctrl+J",
                              command=self.show_downloads)
        menubar.add_cascade(label="Bookmarks", menu=book_menu)

        tools_menu = tk.Menu(menubar, tearoff=0, bg=BG_ALT, fg=FG,
                             activebackground=ACCENT, activeforeground="#04201f")
        tools_menu.add_command(label="Settings",
                               command=lambda: self.current_tab().navigate("surfgambit://settings"))
        tools_menu.add_command(label="Image search",
                               command=lambda: self.current_tab().navigate("surfgambit://images"))
        tools_menu.add_command(label="Alien Defender",
                               command=lambda: self.current_tab().navigate("surfgambit://game"))
        tools_menu.add_separator()
        tools_menu.add_command(label="Clear cookies", command=self.clear_cookies)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0, bg=BG_ALT, fg=FG,
                            activebackground=ACCENT, activeforeground="#04201f")
        help_menu.add_command(label="Keyboard shortcuts", command=self.show_shortcuts)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="Toolbar.TFrame", padding=(6, 5))
        bar.pack(side="top", fill="x")
        self.toolbar = bar

        self.back_btn = ttk.Button(bar, text="\u2190", width=3,
                                   command=lambda: self.current_tab().go_back())
        self.forward_btn = ttk.Button(bar, text="\u2192", width=3,
                                      command=lambda: self.current_tab().go_forward())
        self.reload_btn = ttk.Button(bar, text="\u27f3", width=3,
                                     command=self.reload_or_stop)
        home_btn = ttk.Button(bar, text="\u2302", width=3,
                              command=lambda: self.current_tab().navigate(
                                  self.settings.get("homepage", "surfgambit://welcome")))
        for widget in (self.back_btn, self.forward_btn, self.reload_btn, home_btn):
            widget.pack(side="left", padx=1)

        self.mascot = Mascot(bar, size=24, bg=BG_ALT,
                             is_busy=lambda: bool(self.current_tab()
                                                  and self.current_tab().is_loading),
                             is_enabled=lambda: self.settings.get("mascot_active", "on") == "on",
                             on_hover=self._mascot_status)
        self.mascot.pack(side="left", padx=(8, 6))

        self.address_var = tk.StringVar()
        self.address = ttk.Entry(bar, textvariable=self.address_var)
        self.address.pack(side="left", fill="x", expand=True, padx=6)
        self.address.bind("<Return>", self._on_address_enter)
        self.address.bind("<FocusIn>", self._on_address_focus)
        self.address.bind("<Escape>", lambda e: (self.current_tab().canvas.focus_set()))

        self.star_btn = ttk.Button(bar, text="\u2606", width=3,
                                   command=self.bookmark_current)
        self.star_btn.pack(side="left", padx=1)
        ttk.Button(bar, text="Go", style="Accent.TButton",
                   command=self._on_address_enter).pack(side="left", padx=(4, 2))
        ttk.Button(bar, text="+", width=3,
                   command=lambda: self.add_tab()).pack(side="left", padx=(2, 0))

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(side="top", fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.notebook.bind("<Button-2>", self._on_tab_middle_click)

    def _build_findbar(self) -> None:
        self.find_frame = ttk.Frame(self, style="Toolbar.TFrame", padding=(8, 4))
        ttk.Label(self.find_frame, text="Find:", style="Status.TLabel").pack(side="left")
        self.find_var = tk.StringVar()
        self.find_entry = ttk.Entry(self.find_frame, textvariable=self.find_var, width=32)
        self.find_entry.pack(side="left", padx=6)
        self.find_var.trace_add("write", lambda *a: self._run_find())
        self.find_entry.bind("<Return>", lambda e: self._run_find())
        self.find_entry.bind("<Escape>", lambda e: self.close_find())
        self.find_label = ttk.Label(self.find_frame, text="", style="Status.TLabel")
        self.find_label.pack(side="left", padx=6)
        ttk.Button(self.find_frame, text="\u2715", width=3,
                   command=self.close_find).pack(side="right")
        self._find_visible = False

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, style="Toolbar.TFrame", padding=(8, 3))
        bar.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(bar, text="Ready", style="Status.TLabel")
        self.status_label.pack(side="left")
        self.loading_bar = ttk.Progressbar(bar, mode="indeterminate", length=110)
        self._loading_shown = False
        self.right_label = ttk.Label(bar, text="", style="Status.TLabel")
        self.right_label.pack(side="right")

    def _bind_shortcuts(self) -> None:
        bindings = {
            "<Control-t>": lambda e: self.add_tab(),
            "<Control-w>": lambda e: self.close_current_tab(),
            "<Control-l>": lambda e: self.focus_address(),
            "<Control-j>": lambda e: self.show_downloads(),
            "<Control-f>": lambda e: self.focus_find(),
            "<Control-r>": lambda e: self.current_tab().reload(),
            "<Control-Shift-r>": lambda e: self.current_tab().reload(without_scripts=True),
            "<F5>": lambda e: self.current_tab().reload(),
            "<Control-u>": lambda e: self.view_source(),
            "<Control-d>": lambda e: self.bookmark_current(),
            "<Control-s>": lambda e: self.save_page(),
            "<Control-q>": lambda e: self.on_close(),
            "<F12>": lambda e: self.toggle_devtools(),
            "<F11>": lambda e: self.toggle_fullscreen(),
            "<Control-plus>": lambda e: self.zoom_by(0.1),
            "<Control-equal>": lambda e: self.zoom_by(0.1),
            "<Control-minus>": lambda e: self.zoom_by(-0.1),
            "<Control-Key-0>": lambda e: self.set_zoom(1.0),
            "<Alt-Left>": lambda e: self.current_tab().go_back(),
            "<Alt-Right>": lambda e: self.current_tab().go_forward(),
            "<Escape>": lambda e: self.close_find(),
        }
        for sequence, handler in bindings.items():
            self.bind_all(sequence, self._wrap_shortcut(handler), add="+")

    def _wrap_shortcut(self, handler: Callable[[Any], Any]):
        def dispatch(event):
            widget = event.widget
            if isinstance(widget, (tk.Text, tk.Entry, ttk.Entry)):
                # Let the widget own clipboard/undo keys; browser shortcuts
                # (Ctrl+T, Ctrl+F, ...) still win everywhere else.
                if event.keysym.lower() in ("c", "v", "x", "a", "z", "y") and \
                        (event.state & 0x4):
                    return None
            try:
                handler(event)
            except Exception:
                traceback.print_exc()
            return "break"
        return dispatch

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------
    def _open_first_tab(self) -> None:
        homepage = self.settings.get("homepage") or "surfgambit://welcome"
        self.add_tab(homepage)

    def add_tab(self, url: Optional[str] = None) -> BrowserTab:
        tab = BrowserTab(self.notebook, self)
        self.notebook.add(tab, text="New tab")
        self.notebook.select(tab)
        try:
            tab.set_zoom(float(self.settings.get("zoom", 1.0)))
        except Exception:
            pass
        if url:
            tab.navigate(url, push_history=False)
        tab.canvas.focus_set()
        return tab

    def current_tab(self) -> Optional[BrowserTab]:
        notebook = getattr(self, "notebook", None)
        if notebook is None:
            return None
        try:
            selected = notebook.select()
        except tk.TclError:
            return None
        if not selected:
            return None
        widget = self.nametowidget(selected)
        return widget if isinstance(widget, BrowserTab) else None

    def close_current_tab(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        if len(self.notebook.tabs()) == 1:
            tab.navigate("surfgambit://welcome", push_history=False)
            return
        tab.stop_game()
        tab.destroy()
        self.update_chrome(self.current_tab())

    def open_in_new_tab(self, url: str) -> BrowserTab:
        return self.add_tab(url)

    def _on_tab_changed(self, _event=None) -> None:
        tab = self.current_tab()
        if tab is not None:
            self.update_chrome(tab)

    def _on_tab_middle_click(self, event) -> None:
        try:
            index = self.notebook.index(f"@{event.x},{event.y}")
            widget = self.nametowidget(self.notebook.tabs()[index])
        except Exception:
            return
        if len(self.notebook.tabs()) == 1:
            return
        widget.destroy()

    # ------------------------------------------------------------------
    # Chrome updates
    # ------------------------------------------------------------------
    def update_chrome(self, tab: Optional[BrowserTab]) -> None:
        if tab is None:
            return
        label = tab.title or "New tab"
        try:
            if str(tab) in self.notebook.tabs():
                self.notebook.tab(
                    tab, text=(label[:28] + "\u2026") if len(label) > 29 else label)
        except tk.TclError:
            pass
        self.title(f"{label} - {APP_NAME} {VERSION}")
        if self.focus_get() is not self.address:
            self.address_var.set(tab.current_url)
        self.back_btn.state(["!disabled"] if tab.back_stack else ["disabled"])
        self.forward_btn.state(["!disabled"] if tab.forward_stack else ["disabled"])
        starred = self.store.is_bookmarked(tab.current_url)
        self.star_btn.configure(text="\u2605" if starred else "\u2606")
        self.right_label.configure(
            text=f"zoom {int(round(tab.zoom * 100))}%"
            + ("  \u00b7  scripts on" if self.settings.get("javascript") == "on" else ""))

    def update_loading_state(self) -> None:
        tab = self.current_tab()
        loading = bool(tab and tab.is_loading)
        self.reload_btn.configure(text="\u2715" if loading else "\u27f3")
        if loading and not self._loading_shown:
            self._loading_shown = True
            self.loading_bar.pack(side="left", padx=10)
            try:
                self.loading_bar.start(14)
            except tk.TclError:
                pass
            self.status_label.configure(text="Loading\u2026")
        elif not loading and self._loading_shown:
            self._loading_shown = False
            try:
                self.loading_bar.stop()
            except tk.TclError:
                pass
            self.loading_bar.pack_forget()

    def set_status(self, text: str, timeout_ms: int = 0) -> None:
        self.status_label.configure(text=text)
        if timeout_ms:
            self.after(timeout_ms, lambda: self.status_label.configure(text="Done"))

    def _mascot_status(self, message: Optional[str]) -> None:
        if message:
            self.status_label.configure(text=message)
        else:
            tab = self.current_tab()
            self.status_label.configure(text=tab.status_note if tab else "Ready")

    def reload_or_stop(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        if tab.is_loading:
            tab.generation += 1
            tab.set_loading(False)
            self.set_status("Stopped")
        else:
            tab.reload()

    # ------------------------------------------------------------------
    # Address bar
    # ------------------------------------------------------------------
    def resolve_input(self, text: str) -> Optional[str]:
        return normalise_input(text, self.settings.get("search_engine", "Bing"))

    def _on_address_enter(self, _event=None) -> Any:
        tab = self.current_tab()
        if tab is None:
            return "break"
        text = self.address_var.get().strip()
        if not text:
            return "break"
        target = self.resolve_input(text)
        if target:
            tab.navigate(target)
        tab.canvas.focus_set()
        return "break"

    def _on_address_focus(self, _event=None) -> None:
        self.after(10, lambda: self.address.selection_range(0, "end"))

    def focus_address(self) -> None:
        self.address.focus_set()
        self.address.selection_range(0, "end")

    def copy_address(self) -> None:
        tab = self.current_tab()
        if tab:
            self.clipboard_clear()
            self.clipboard_append(tab.current_url)

    def copy_selection(self) -> None:
        tab = self.current_tab()
        if tab and tab.selected_text:
            self.clipboard_clear()
            self.clipboard_append(tab.selected_text)

    # ------------------------------------------------------------------
    # Find
    # ------------------------------------------------------------------
    def focus_find(self) -> None:
        if not self._find_visible:
            self.find_frame.pack(side="bottom", fill="x", before=self.status_label.master)
            self._find_visible = True
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")

    def close_find(self) -> None:
        if not self._find_visible:
            return
        self.find_frame.pack_forget()
        self._find_visible = False
        tab = self.current_tab()
        if tab is not None:
            tab.search_term = ""
            tab.relayout()
            tab.canvas.focus_set()

    def _run_find(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        term = self.find_var.get()
        hits = tab.find(term)
        self.find_label.configure(
            text=f"{hits} match{'es' if hits != 1 else ''}" if term else "")

    # ------------------------------------------------------------------
    # Zoom / fullscreen / navigation helpers
    # ------------------------------------------------------------------
    def zoom_by(self, delta: float) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        self.set_zoom(tab.zoom + delta)

    def set_zoom(self, zoom: float) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        tab.set_zoom(zoom)
        self.settings["zoom"] = tab.zoom
        self.store.save_settings(self.settings)
        self.update_chrome(tab)

    def toggle_fullscreen(self) -> None:
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def record_history(self, url: str, title: str) -> None:
        if self.settings.get("save_history", "on") == "on":
            self.store.record_visit(url, title)

    def bookmark_current(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        starred, message = self.store.toggle_bookmark(tab.current_url, tab.title)
        self.update_chrome(tab)
        self.set_status(message, 2500)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def apply_settings(self, updated: Dict[str, Any]) -> None:
        self.settings = self.store.load_settings()
        self.settings.update({k: v for k, v in updated.items() if k in self.settings})
        self.store.save_settings(self.settings)
        self.client.user_agent = USER_AGENTS.get(
            self.settings.get("user_agent_name", "Chrome"), USER_AGENTS["Chrome"])
        self.client.verify_tls = self.settings.get("verify_tls", "on") == "on"
        self.client._ssl_context = None
        self.settings["profile"] = self.profile_name or ""
        self.store.save_settings(self.settings)
        tab = self.current_tab()
        if tab is not None:
            self.update_chrome(tab)

    def clear_cookies(self) -> None:
        self.cookie_jar.clear()
        self.set_status("Cookies cleared", 2500)

    # ------------------------------------------------------------------
    # Images / downloads / source
    # ------------------------------------------------------------------
    def search_images(self, query: str) -> List[str]:
        limit = 30 if query.strip() else 8
        return fetch_image_urls(self.client, query, limit=min(limit, 12))

    def offer_download(self, url: str, suggested: str = "") -> None:
        tab = self.current_tab()
        referer = tab.current_url if tab else ""
        name = suggested or os.path.basename(url.split("?")[0]) or "download"
        path = filedialog.asksaveasfilename(
            parent=self, title="Save file", initialfile=name,
            initialdir=os.path.join(os.path.expanduser("~"), "Downloads"),
        )
        if not path:
            return
        self.downloads.start(url, os.path.dirname(path) or ".", referer=referer,
                             exact_path=path)
        self.set_status(f"Downloading {os.path.basename(path)}\u2026")
        if tab is not None:
            tab._open_internal("surfgambit://downloads")

    def show_downloads(self) -> None:
        """A live view of the queue: progress, size and a cancel button."""
        if self._downloads_window is not None and self._downloads_window.winfo_exists():
            self._downloads_window.lift()
            self._refresh_downloads_window()
            return
        window = tk.Toplevel(self)
        window.title("Downloads")
        window.geometry("680x300")
        window.configure(bg=BG)
        self._downloads_window = window

        columns = ("name", "size", "progress", "status")
        tree = ttk.Treeview(window, columns=columns, show="headings", height=9)
        for column, title, width in (("name", "File", 240), ("size", "Size", 90),
                                     ("progress", "Progress", 90),
                                     ("status", "Status", 220)):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        self._downloads_tree = tree

        row = ttk.Frame(window, padding=(8, 0, 8, 8))
        row.pack(fill="x")
        ttk.Button(row, text="Cancel selected",
                   command=self._cancel_selected_download).pack(side="left")
        ttk.Button(row, text="Open folder",
                   command=self._open_downloads_folder).pack(side="left", padx=6)
        ttk.Button(row, text="Close", command=window.destroy).pack(side="right")
        self._refresh_downloads_window()

    def _refresh_downloads_window(self) -> None:
        tree = self._downloads_tree
        window = self._downloads_window
        if tree is None or window is None or not window.winfo_exists():
            return
        active = self.downloads.active()
        existing = {tree.item(item, "values")[0]: item for item in tree.get_children("")}
        for download in active:
            percent = download.progress
            progress_text = f"{percent * 100:.0f}%" if percent >= 0 else "?"
            values = (download.filename, format_bytes(download.bytes_done),
                      progress_text, download.status)
            item = existing.get(download.filename)
            if item:
                tree.item(item, values=values)
            else:
                tree.insert("", "end", values=values, iid=download.path)
        for filename, item in existing.items():
            if all(d.filename != filename for d in active):
                tree.delete(item)
        window.after(500, self._refresh_downloads_window)

    def _cancel_selected_download(self) -> None:
        tree = self._downloads_tree
        if tree is None:
            return
        for item in tree.selection():
            for download in self.downloads.active():
                if download.path == item:
                    self.downloads.cancel(download)
                    self.set_status(f"Cancelling {download.filename}\u2026")

    def _open_downloads_folder(self) -> None:
        active = self.downloads.active()
        folder = os.path.dirname(active[0].path) if active else ""
        if folder and hasattr(os, "startfile"):
            os.startfile(folder)          # type: ignore[attr-defined]
        else:
            self.set_status(folder or "No active downloads")

    def _on_download_update(self, download) -> None:
        if not self.winfo_exists():
            return
        if download.status == "complete":
            self.set_status(f"Saved {download.filename} ({download.size} bytes)",
                            4000)
        elif download.status == "failed":
            self.set_status(f"Download failed: {download.error}", 6000)
        elif download.status == "cancelled":
            self.set_status(f"Cancelled {download.filename}", 4000)
        else:
            percent = download.progress
            suffix = f" ({percent * 100:.0f}%)" if percent >= 0 else ""
            self.right_label.configure(
                text=f"{download.filename}: {format_bytes(download.bytes_done)}{suffix}")
        tab = self.current_tab()
        if tab is not None and tab.current_url.startswith("surfgambit://downloads"):
            tab._open_internal("surfgambit://downloads")

    def save_page(self) -> None:
        tab = self.current_tab()
        if tab is None or not self.last_source:
            self.set_status("Nothing to save")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Save page", defaultextension=".html",
            initialfile="page.html", filetypes=[("HTML", "*.html"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(self.last_source)
            self.set_status(f"Saved {path}", 3000)
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)

    def open_local_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Open file",
            filetypes=[("HTML", "*.html *.htm"), ("All files", "*.*")],
        )
        if not path:
            return
        url = "file://" + os.path.abspath(path).replace(os.sep, "/")
        self.current_tab().navigate(url)

    def view_source(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        source = self.last_source or ""
        tab.title = "View source"
        tab.css_sheets = []
        tab._install_document(pages.source_page(tab.current_url, source))

    # ------------------------------------------------------------------
    # Devtools
    # ------------------------------------------------------------------
    def log_console(self, message: str, level: str = "log") -> None:
        widget = self._devtools_text.get("console")
        if widget is None:
            return
        widget.configure(state="normal")
        widget.insert("end", f"[{level}] {message}\n")
        widget.see("end")
        widget.configure(state="disabled")

    def toggle_devtools(self) -> None:
        if self._devtools is not None and self._devtools.winfo_exists():
            self._devtools.destroy()
            self._devtools = None
            self._devtools_text.clear()
            self._console_entry = None
            return
        window = tk.Toplevel(self)
        window.title("SurfGambit developer tools")
        window.geometry("820x460")
        window.configure(bg=BG)
        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True)
        self._devtools_notebook = notebook

        for name in ("console", "network", "dom", "layout"):
            frame = ttk.Frame(notebook)
            text = tk.Text(frame, bg="#111114", fg=FG, insertbackground=FG,
                           wrap="none", font=("Courier", 10), relief="flat")
            scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            text.pack(side="left", fill="both", expand=True)
            notebook.add(frame, text=name.capitalize())
            self._devtools_text[name] = text
            if name == "console":
                entry_row = ttk.Frame(frame)
                entry_row.pack(side="bottom", fill="x")
                ttk.Label(entry_row, text="\u203a", style="Status.TLabel").pack(side="left")
                self._console_entry = ttk.Entry(entry_row)
                self._console_entry.pack(side="left", fill="x", expand=True, padx=4, pady=4)
                self._console_entry.bind("<Return>", self._eval_console)

        self._devtools = window
        tab = self.current_tab()
        if tab is not None:
            self.refresh_devtools(tab)
        self._refresh_network_panel()

    def _eval_console(self, _event=None) -> None:
        """Evaluate an expression in the page's own JS context."""
        entry = getattr(self, "_console_entry", None)
        if entry is None:
            return
        code = entry.get().strip()
        if not code:
            return
        entry.delete(0, "end")
        widget = self._devtools_text.get("console")
        if widget is None:
            return
        widget.configure(state="normal")
        widget.insert("end", f"\u203a {code}\n")
        tab = self.current_tab()
        try:
            from core.js.surfscript import Interpreter, to_string

            interp = tab.script_runtime.interp if (tab and tab.script_runtime) \
                else Interpreter()
            widget.insert("end", to_string(interp.evaluate_source(code)) + "\n")
        except Exception as exc:
            widget.insert("end", f"{type(exc).__name__}: {exc}\n")
        widget.see("end")
        widget.configure(state="disabled")

    def inspect_node(self, node) -> None:
        """Open devtools and highlight a node's ancestry."""
        if self._devtools is None or not self._devtools.winfo_exists():
            self.toggle_devtools()
        widget = self._devtools_text.get("dom")
        if widget is None or node is None:
            return
        chain = []
        probe = node
        while probe is not None:
            chain.append(probe)
            probe = probe.parent
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for item in reversed(chain):
            widget.insert("end", f"{'  ' * min(item.depth(), 14)}{item.dump(0, 1)}\n")
        widget.configure(state="disabled")
        try:
            self._devtools_notebook.select(2)
        except Exception:
            pass
        self.set_status("Inspecting element in developer tools")

    def _refresh_network_panel(self) -> None:
        widget = self._devtools_text.get("network")
        if widget is None or not widget.winfo_exists():
            return
        stats = self.cache.stats()
        summary = (f"HTTP cache: {stats['entries']} entries, "
                   f"{format_bytes(int(stats['bytes']))}, "
                   f"{stats['hits']} hits / {stats['misses']} misses / "
                   f"{stats['revalidated']} revalidated\n"
                   + "-" * 78 + "\n")
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", summary + self.client.log_text())
        widget.configure(state="disabled")
        if self._devtools is not None and self._devtools.winfo_exists():
            self._devtools.after(1500, self._refresh_network_panel)

    def refresh_devtools(self, tab: Optional[BrowserTab]) -> None:
        if self._devtools is None or not self._devtools.winfo_exists() or tab is None:
            return
        self._refresh_network_panel()
        dom_text = self._devtools_text.get("dom")
        if dom_text is not None:
            dom_text.configure(state="normal")
            dom_text.delete("1.0", "end")
            if tab.dom is not None:
                dom_text.insert("end", tab.dom.dump())
            dom_text.configure(state="disabled")
        layout_text = self._devtools_text.get("layout")
        if layout_text is not None:
            layout_text.configure(state="normal")
            layout_text.delete("1.0", "end")
            boxes = list(tab.layout.walk()) if tab.layout is not None else []
            drawn = getattr(tab.renderer, "drawn_boxes", 0)
            items = getattr(tab.renderer, "drawn_items", 0)
            layout_text.insert("end", f"boxes: {len(boxes)} (painted: {drawn})\n"
                                      f"canvas items: {items}\n"
                                      f"page height: {int(tab.layout.height) if tab.layout else 0}px\n\n")
            for box in boxes[:400]:
                layout_text.insert(
                    "end", f"{'  ' * min(box.node.depth(), 12)}<{box.node.tag}> "
                           f"{box.display} x={box.x:.0f} y={box.y:.0f} "
                           f"w={box.width:.0f} h={box.height:.0f}\n")
            layout_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------
    def show_shortcuts(self) -> None:
        messagebox.showinfo(
            "Keyboard shortcuts",
            "Ctrl+T   new tab\nCtrl+W   close tab\nCtrl+L   focus address bar\n"
            "Ctrl+R   reload\nCtrl+F   find in page\nCtrl+D   bookmark page\n"
            "Ctrl+U   view source\nCtrl+S   save page\nCtrl+ +/-   zoom\n"
            "Ctrl+0   reset zoom\nAlt+Left/Right   back / forward\n"
            "F11 full screen    F12 developer tools\nCtrl+Q   quit",
            parent=self)

    def show_about(self) -> None:
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {VERSION}\n\nA browser built from scratch in Python: raw "
            "sockets and TLS, a hand-written tokenizer, CSS cascade, layout "
            "engine and canvas renderer.\n\nNo requests, no BeautifulSoup, no "
            "WebKit. Standard library only.",
            parent=self)

    def prompt_credentials(self, url: str) -> Optional[tuple]:
        dialog = tk.Toplevel(self)
        dialog.title("Authentication required")
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text=f"{url}\nrequires a username and password.",
                  style="TLabel", wraplength=340).pack(padx=16, pady=(16, 8))
        frame = ttk.Frame(dialog)
        frame.pack(padx=16, fill="x")
        ttk.Label(frame, text="Username", style="TLabel").grid(row=0, column=0, sticky="w")
        user_var = tk.StringVar()
        ttk.Entry(frame, textvariable=user_var, width=28).grid(row=0, column=1, pady=3)
        ttk.Label(frame, text="Password", style="TLabel").grid(row=1, column=0, sticky="w")
        pass_var = tk.StringVar()
        ttk.Entry(frame, textvariable=pass_var, show="\u2022", width=28).grid(row=1, column=1, pady=3)
        result: Dict[str, Optional[tuple]] = {"value": None}

        def accept() -> None:
            result["value"] = (user_var.get(), pass_var.get())
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(pady=12)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Sign in", style="Accent.TButton",
                   command=accept).pack(side="left", padx=4)
        dialog.bind("<Return>", lambda e: accept())
        dialog.wait_window()
        return result["value"]

    # ------------------------------------------------------------------
    # Thread plumbing
    # ------------------------------------------------------------------
    def submit(self, fn: Callable[[], None]) -> None:
        """Queue work on the shared pool (8 workers, daemon threads)."""
        def guarded() -> None:
            try:
                fn()
            except Exception:
                traceback.print_exc()
        try:
            self._pool.submit(guarded)
        except RuntimeError:            # pool already shut down
            pass

    def ui(self, fn: Callable[[], None]) -> None:
        self._queue.put(fn)

    def _pump(self) -> None:
        if self._closing:
            return
        drained = 0
        while drained < 60:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            try:
                fn()
            except Exception:
                traceback.print_exc()
        try:
            self.after(25, self._pump)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:               # Python < 3.9
            self._pool.shutdown(wait=False)
        tab = self.current_tab()
        try:
            if tab is not None:
                self.settings["zoom"] = tab.zoom
            self.settings["profile"] = self.profile_name or ""
            self.store.save_settings(self.settings)
            self.store.flush()
            self.cookie_jar.save()
        except Exception:
            pass
        try:
            self.mascot.stop()
            for child in list(self.notebook.tabs()):
                widget = self.nametowidget(child)
                widget.stop_game()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


def format_bytes(count: int) -> str:
    value = float(max(0, count))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
