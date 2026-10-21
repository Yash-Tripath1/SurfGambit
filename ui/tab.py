"""
ui/tab.py — one browser tab: navigation, document loading, painting.

The concurrency model, which fixes a whole class of legacy bugs:

  * every navigation bumps a `generation` counter; any callback whose
    generation is stale is dropped, so a slow response can never overwrite the
    page you have since navigated to
  * worker threads never touch Tk — they hand closures to `app.ui()`, which
    runs them on the main thread through a queue pump
  * `is_loading` is derived from a counter, so closing a tab mid-load cannot
    leave the toolbar stuck
"""

from __future__ import annotations

import base64
import os
import tkinter as tk
import traceback
import urllib.parse
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

from core.dom import parse
from core.dom.tree import HTMLNode
from core.layout.block import apply_list_markers, compute_layout
from core.layout.box import LayoutBox, build_layout_tree
from core.style import parse_stylesheet
from core.style.cascade import compute_styles
from ui import pages
from ui.renderer import Renderer

MAX_STYLESHEETS = 8
INTERNAL_PREFIX = "surfgambit://"


def collect_style_text(dom: HTMLNode) -> str:
    chunks: List[str] = []
    for node in dom.walk():
        if node.tag == "style":
            chunks.append("".join(c.text for c in node.children))
    return "\n".join(chunks)


def document_title(dom: Optional[HTMLNode]) -> str:
    if dom is None:
        return ""
    for node in dom.walk():
        if node.tag == "title":
            text = "".join(c.text for c in node.children).strip()
            return " ".join(text.split())
    return ""


def stylesheet_links(dom: HTMLNode, base_url: str) -> List[str]:
    out: List[str] = []
    for node in dom.walk():
        if node.tag != "link":
            continue
        rel = (node.attributes.get("rel") or "").lower()
        if "stylesheet" not in rel:
            continue
        href = node.attributes.get("href")
        if not href:
            continue
        if href.lower().startswith("data:"):
            continue
        out.append(urllib.parse.urljoin(base_url, href))
        if len(out) >= MAX_STYLESHEETS:
            break
    return out


def image_urls(dom: HTMLNode, base_url: str) -> List[Tuple[str, HTMLNode]]:
    out: List[Tuple[str, HTMLNode]] = []
    seen = set()
    for node in dom.walk():
        if node.tag != "img":
            continue
        src = node.attributes.get("src")
        if not src:
            continue
        absolute = urllib.parse.urljoin(base_url, src)
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append((absolute, node))
    return out


class BrowserTab(tk.Frame):
    def __init__(self, parent: tk.Widget, app) -> None:
        super().__init__(parent, bg="#ffffff")
        self.app = app
        self.canvas = tk.Canvas(self, bg="#ffffff", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical",
                                       command=self._on_scrollbar)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.current_url: str = "surfgambit://welcome"
        self.title: str = "New tab"
        self.dom: Optional[HTMLNode] = None
        self.layout: Optional[LayoutBox] = None
        self.css_sheets = []
        self.zoom: float = 1.0
        self.search_term: str = ""
        self.search_hits: int = 0

        self.back_stack: List[str] = []
        self.forward_stack: List[str] = []
        self.generation: int = 0
        self.is_loading: bool = False
        self.status_note: str = ""

        self.images: Dict[str, Optional[tk.PhotoImage]] = {}
        self.loading_images: set = set()
        self.form_widgets: List[tk.Widget] = []
        self.form_entries: Dict[HTMLNode, tk.Widget] = {}

        self._render_scheduled = False
        self._viewport_job = None
        self._drawn_range = (0.0, 0.0)
        self.script_runtime = None
        self.script_errors = 0
        self._widget_pos = {}
        self._widget_for_node = {}
        self._timer_job = None
        self._last_width = 0
        self._resize_job = None
        self._game = None
        self.selected_text = ""
        self._sel_start = None
        self._sel_rect = None

        self.renderer = Renderer(self.canvas, app.measurer,
                                 image_provider=self.get_image,
                                 base_url=self.current_url)
        self.renderer.form_factory = self._make_form_widget

        self._bind_events()
        self.load_page("surfgambit://welcome", push_history=False)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _bind_events(self) -> None:
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(sequence, self._on_wheel)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_context)
        self.canvas.tag_bind("link", "<Enter>", self._on_link_enter)
        self.canvas.tag_bind("link", "<Leave>", self._on_link_leave)
        for sequence in ("<Prior>", "<Next>", "<Home>", "<End>"):
            self.canvas.bind(sequence, self._on_page_key)

    def _on_wheel(self, event) -> str:
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(3, "units")
        elif getattr(event, "delta", 0) > 0:
            self.canvas.yview_scroll(-3, "units")
        else:
            self.canvas.yview_scroll(3, "units")
        self._after_scroll()
        return "break"

    def _on_page_key(self, event) -> str:
        height = max(1, self.canvas.winfo_height() or 600)
        if event.keysym == "Prior":
            self.canvas.yview_scroll(-height, "units")
        elif event.keysym == "Next":
            self.canvas.yview_scroll(height, "units")
        elif event.keysym == "Home":
            self.canvas.yview_moveto(0)
        else:
            self.canvas.yview_moveto(1)
        self._after_scroll()
        return "break"

    def _on_scrollbar(self, *args) -> None:
        self.canvas.yview(*args)
        self._after_scroll()

    def _after_scroll(self) -> None:
        """Keep Tk widgets glued to the page, and re-cull if we left the band."""
        self._sync_form_widgets()
        if self._viewport_job is not None:
            return
        self._viewport_job = self.after(30, self._viewport_check)

    def _viewport_check(self) -> None:
        self._viewport_job = None
        if self.layout is None:
            return
        height = max(1, self.canvas.winfo_height() or 600)
        top = self.canvas.canvasy(0)
        bottom = top + height
        band_top, band_bottom = self._drawn_range
        if top < band_top + 60 or bottom > band_bottom - 60:
            self._repaint_viewport()

    def _on_configure(self, event) -> None:
        if abs(event.width - self._last_width) < 8:
            return
        self._last_width = event.width
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(120, self.relayout)

    def _on_link_enter(self, event) -> None:
        item = self.canvas.find_withtag("current")
        if not item:
            return
        tags = self.canvas.gettags(item[0])
        for tag in tags:
            if tag.startswith("link_"):
                try:
                    link_id = int(tag.split("_", 1)[1])
                except ValueError:
                    return
                href = self.renderer.link_map.get(link_id, "")
                if href:
                    self.app.set_status(f"{urllib.parse.urljoin(self.current_url, href)}")
                self.canvas.config(cursor="hand2")
                return

    def _on_link_leave(self, event) -> None:
        self.canvas.config(cursor="")
        self.app.set_status("Done")

    # -- selection ------------------------------------------------------
    def _on_press(self, event) -> None:
        self._sel_start = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        self.selected_text = ""

    def _on_drag(self, event) -> None:
        if self._sel_start is None:
            return
        x0, y0 = self._sel_start
        x1 = self.canvas.canvasx(event.x)
        y1 = self.canvas.canvasy(event.y)
        if self._sel_rect is not None:
            self.canvas.delete(self._sel_rect)
        self._sel_rect = self.canvas.create_rectangle(
            x0, y0, x1, y1, outline="#00a7b0", dash=(3, 3), width=1
        )

    def _on_release(self, event) -> None:
        if self._sel_start is None:
            return
        x0, y0 = self._sel_start
        x1 = self.canvas.canvasx(event.x)
        y1 = self.canvas.canvasy(event.y)
        if self._sel_rect is not None:
            self.canvas.delete(self._sel_rect)
            self._sel_rect = None

        if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:
            self._sel_start = None
            self.app.ctrl_down = bool(event.state & 0x4)
            self._click_at(x1, y1)
            return

        left, right = min(x0, x1), max(x0, x1)
        top, bottom = min(y0, y1), max(y0, y1)
        words = self._words_in(left, top, right, bottom)
        self.selected_text = " ".join(words)
        if self.selected_text:
            self.app.set_status(f"{len(self.selected_text)} characters selected - Ctrl+C to copy")
        self._sel_start = None

    def _words_in(self, left: float, top: float, right: float, bottom: float) -> List[str]:
        out: List[Tuple[float, float, str]] = []
        if self.layout is None:
            return []
        for box in self.layout.walk():
            for line in box.lines:
                for item in line.items:
                    if item.kind != "word" or not item.text:
                        continue
                    cx = item.x + item.width / 2
                    cy = item.y + item.height / 2
                    if left <= cx <= right and top <= cy <= bottom:
                        out.append((item.y, item.x, item.text))
        out.sort(key=lambda w: (round(w[0]), w[1]))
        return [w[2] for w in out]

    def _node_at(self, x: float, y: float):
        """The DOM node painted under a canvas point, if any."""
        for canvas_id in reversed(self.canvas.find_overlapping(x, y, x, y)):
            for tag in self.canvas.gettags(canvas_id):
                if len(tag) > 1 and tag[0] == "n" and tag[1:].isdigit():
                    node = self.renderer.node_map.get(int(tag[1:]))
                    if node is not None:
                        return node
        return None

    def _node_has_script_click(self, node: HTMLNode) -> bool:
        runtime = self.script_runtime
        if runtime is None or not self.scripts_enabled():
            return False
        probe = node
        while probe is not None:
            if "onclick" in probe.attributes:
                return True
            if runtime.has_listeners(probe, "click"):
                return True
            probe = probe.parent
        return False

    def _dispatch_on(self, node: HTMLNode, fields: Dict[str, object]) -> bool:
        runtime = self.script_runtime
        if runtime is None or not self.scripts_enabled():
            return False
        try:
            from core.js.dom_bridge import Event

            event = Event("click", runtime.wrap(node), fields)
            runtime.dispatch(node, event)
            if runtime.errors:
                for error in runtime.errors[-2:]:
                    self.app.log_console(error, "error")
                runtime.errors.clear()
            return bool(event.default_prevented)
        except Exception as exc:
            self.app.log_console(f"click handler failed: {exc}", "error")
            return False

    def _dispatch_click(self, x: float, y: float) -> bool:
        """Send a click to script listeners. Returns True if it was consumed."""
        runtime = self.script_runtime
        if runtime is None or not self.scripts_enabled():
            return False
        node = self._node_at(x, y)
        if node is None:
            return False
        return self._dispatch_on(node, {
            "clientX": x, "clientY": y, "pageX": x, "pageY": y, "button": 0,
        })

    def _click_at(self, x: float, y: float) -> None:
        if self._dispatch_click(x, y):
            return
        item = self.canvas.find_overlapping(x, y, x, y)
        for canvas_id in reversed(item):
            tags = self.canvas.gettags(canvas_id)
            for tag in tags:
                if tag.startswith("link_"):
                    try:
                        link_id = int(tag.split("_", 1)[1])
                    except ValueError:
                        continue
                    href = self.renderer.link_map.get(link_id, "")
                    if href:
                        target = urllib.parse.urljoin(self.current_url, href)
                        if tag == "link" and self.app.ctrl_down:
                            self.app.open_in_new_tab(target)
                        else:
                            self.navigate(target)
                    return

    # -- context menu ---------------------------------------------------
    def _on_context(self, event) -> None:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        href = ""
        for canvas_id in reversed(self.canvas.find_overlapping(x, y, x, y)):
            for tag in self.canvas.gettags(canvas_id):
                if tag.startswith("link_"):
                    try:
                        href = self.renderer.link_map.get(int(tag.split("_", 1)[1]), "")
                    except (ValueError, KeyError):
                        href = ""

        node = self._node_at(x, y)
        image_url = ""
        probe = node
        while probe is not None:
            if probe.tag == "img" and probe.attributes.get("src"):
                image_url = urllib.parse.urljoin(self.current_url,
                                                 probe.attributes["src"])
                break
            probe = probe.parent

        menu = tk.Menu(self, tearoff=0)
        if image_url:
            menu.add_command(label="Open image in new tab",
                             command=lambda: self.app.open_in_new_tab(image_url))
            menu.add_command(label="Save image as\u2026",
                             command=lambda: self.app.offer_download(
                                 image_url, os.path.basename(
                                     urllib.parse.urlsplit(image_url).path) or "image"))
            menu.add_separator()
        if node is not None and self.script_runtime is not None:
            menu.add_command(label="Inspect element",
                             command=lambda n=node: self.app.inspect_node(n))
            menu.add_separator()
        if href:
            target = urllib.parse.urljoin(self.current_url, href)
            menu.add_command(label="Open link",
                             command=lambda: self.navigate(target))
            menu.add_command(label="Open in new tab",
                             command=lambda: self.app.open_in_new_tab(target))
            menu.add_command(label="Copy link address",
                             command=lambda: self._to_clipboard(target))
            menu.add_separator()
        if self.selected_text:
            menu.add_command(label="Copy selection",
                             command=lambda: self._to_clipboard(self.selected_text))
            menu.add_separator()
        menu.add_command(label="Back", command=self.go_back)
        menu.add_command(label="Forward", command=self.go_forward)
        menu.add_command(label="Reload", command=self.reload)
        menu.add_separator()
        menu.add_command(label="Copy page address",
                         command=lambda: self._to_clipboard(self.current_url))
        menu.add_command(label="View source", command=self.app.view_source)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _to_clipboard(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.app.set_status("Copied to clipboard")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def navigate(self, url: str, push_history: bool = True) -> None:
        url = (url or "").strip()
        if not url:
            return
        resolved = self.app.resolve_input(url)
        if resolved is None:
            return

        self.stop_game()
        self.selected_text = ""

        if resolved.startswith(INTERNAL_PREFIX):
            if push_history and self.current_url and self.current_url != resolved:
                self.back_stack.append(self.current_url)
                self.forward_stack.clear()
            self._open_internal(resolved)
            return

        self.generation += 1
        generation = self.generation
        if push_history and self.current_url and self.current_url != resolved:
            self.back_stack.append(self.current_url)
            self.forward_stack.clear()

        self.current_url = resolved
        self.title = "Loading..."
        self.set_loading(True)
        self.app.set_status(f"Loading {resolved} ...")
        self.app.submit(lambda: self._fetch(generation, resolved))

    def reload(self, without_scripts: bool = False) -> None:
        self._skip_scripts = bool(without_scripts)
        if without_scripts:
            self.app.set_status("Reloading without scripts\u2026")
        self.load_page(self.current_url, push_history=False, force=True)

    def go_back(self) -> None:
        if not self.back_stack:
            return
        target = self.back_stack.pop()
        self.forward_stack.append(self.current_url)
        self.load_page(target, push_history=False)

    def go_forward(self) -> None:
        if not self.forward_stack:
            return
        target = self.forward_stack.pop()
        self.back_stack.append(self.current_url)
        self.load_page(target, push_history=False)

    def load_page(self, url: str, push_history: bool = True,
                  force: bool = False) -> None:
        """Load without touching the stacks (used by back/forward/reload)."""
        if url.startswith(INTERNAL_PREFIX):
            self.current_url = url
            self._open_internal(url)
            return
        self.generation += 1
        generation = self.generation
        self.current_url = url
        self.set_loading(True)
        self.app.set_status(f"Loading {url} ...")
        self.app.submit(lambda: self._fetch(generation, url, force))

    # -- worker ---------------------------------------------------------
    def _fetch(self, generation: int, url: str, force: bool = False) -> None:
        client = self.app.client
        try:
            response = client.get(url, force_refresh=force)
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status} {response.reason}")
            if not response.is_html:
                raise RuntimeError(
                    f"Unsupported content type: {response.content_type or 'unknown'}"
                )
            html_text = response.text
            final_url = response.url

            # Fetch linked stylesheets on the same thread, so the first paint
            # already has the page's CSS.
            dom = parse(html_text)
            sheet_sources: List[str] = [collect_style_text(dom)]
            for sheet_url in stylesheet_links(dom, final_url):
                try:
                    sheet_response = client.get(sheet_url, referer=final_url)
                    if sheet_response.ok:
                        sheet_sources.append(sheet_response.text)
                except Exception:
                    continue

            self.app.ui(lambda: self._on_loaded(
                generation, final_url, html_text, sheet_sources
            ))
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            self.app.ui(lambda: self._on_failed(generation, url, message))

    def _on_loaded(self, generation: int, final_url: str, html_text: str,
                   sheet_sources: List[str]) -> None:
        if generation != self.generation or not self.winfo_exists():
            return
        self.current_url = final_url
        self.set_loading(False)
        self._install_document(html_text, extra_sources=sheet_sources[1:])
        self.app.record_history(final_url, self.title)
        self.app.set_status("Done")

    def _on_failed(self, generation: int, url: str, message: str) -> None:
        if generation != self.generation or not self.winfo_exists():
            return
        self.set_loading(False)
        self.app.set_status(f"Failed: {message}")
        html_text = pages.error_page(url, message)
        self._install_document(html_text)
        self.title = "Error"

    # ------------------------------------------------------------------
    # Internal pages
    # ------------------------------------------------------------------
    def _open_internal(self, url: str) -> None:
        self.current_url = url
        parsed = urllib.parse.urlsplit(url)
        path = parsed.netloc or parsed.path
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        settings = self.app.settings
        self.css_sheets = []

        if path == "demo":
            html_text = pages.demo_page(self.scripts_enabled())
            self.title = "SurfScript demo"
        elif path == "welcome":
            html_text = pages.welcome_page(
                self.app.profile_name, self.app.store.load_bookmarks()
            )
            self.title = "SurfGambit"
        elif path == "bookmarks":
            html_text = pages.bookmarks_page(self.app.store.load_bookmarks(),
                                             params.get("msg", ""))
            self.title = "Bookmarks"
        elif path == "history":
            html_text = pages.history_page(self.app.store.load_history())
            self.title = "History"
        elif path == "downloads":
            html_text = pages.downloads_page(self.app.store.load_downloads())
            self.title = "Downloads"
        elif path == "settings":
            html_text = pages.settings_page(
                settings, params.get("msg", ""), params.get("err", "")
            )
            self.title = "Settings"
        elif path == "login":
            html_text = pages.login_page(params.get("err", ""), params.get("msg", ""))
            self.title = "Sign in"
        elif path == "register":
            html_text = pages.register_page(params.get("err", ""))
            self.title = "Create profile"
        elif path == "game":
            self._start_game()
            return
        elif path == "clear-history":
            self.app.store.clear_history()
            self._open_internal("surfgambit://history?msg=History+cleared")
            return
        elif path == "clear-bookmarks":
            self.app.store.save_bookmarks([])
            self._open_internal("surfgambit://bookmarks?msg=Bookmarks+cleared")
            return
        elif path == "clear-cookies":
            self.app.client.cookies.clear()
            self._open_internal("surfgambit://settings?msg=Cookies+cleared")
            return
        elif path == "logout":
            self.app.profile_name = None
            self._open_internal("surfgambit://welcome?msg=Signed+out")
            return
        elif path == "remove-bookmark":
            target = params.get("url", "")
            self.app.store.remove_bookmark(target)
            self._open_internal("surfgambit://bookmarks?msg=Bookmark+removed")
            return
        elif path == "login-submit":
            self._handle_login(params)
            return
        elif path == "register-submit":
            self._handle_register(params)
            return
        elif path == "settings-save":
            self._handle_settings_save(params)
            return
        elif path == "source":
            source = ""
            if self.dom is not None:
                source = self.app.last_source or ""
            html_text = pages.source_page("View source", source or "(no source)")
            self.title = "View source"
        elif path == "images":
            query = params.get("q", "")
            self._start_image_search(query)
            return
        else:
            html_text = pages.error_page(url, f"Unknown internal page: {path}", suggestions=False)
            self.title = "Not found"

        self._install_document(html_text)
        self.app.update_chrome(self)

    def _handle_login(self, params: Dict[str, str]) -> None:
        ok, result = self.app.accounts.login(
            params.get("username", ""), params.get("password", "")
        )
        if ok:
            self.app.profile_name = params.get("username", "").strip()
            self._open_internal("surfgambit://welcome?msg=Signed+in")
        else:
            self._open_internal(
                "surfgambit://login?err=" + urllib.parse.quote(result)
            )

    def _handle_register(self, params: Dict[str, str]) -> None:
        username = params.get("username", "")
        password = params.get("password", "")
        confirm = params.get("confirm", "")
        if password != confirm:
            self._open_internal(
                "surfgambit://register?err=" + urllib.parse.quote("Passwords do not match.")
            )
            return
        ok, message = self.app.accounts.register(username, password)
        if ok:
            self.app.profile_name = username.strip()
            self._open_internal("surfgambit://welcome?msg=Profile+created")
        else:
            self._open_internal(
                "surfgambit://register?err=" + urllib.parse.quote(message)
            )

    def _handle_settings_save(self, params: Dict[str, str]) -> None:
        updated = dict(self.app.settings)
        updated["search_engine"] = params.get("se", "DuckDuckGo")
        updated["user_agent_name"] = params.get("ua", "Chrome")
        updated["verify_tls"] = params.get("verify", "on")
        updated["javascript"] = params.get("js", "off")
        updated["mascot_active"] = params.get("mascot", "on")
        self.app.apply_settings(updated)
        self._open_internal(
            "surfgambit://settings?msg=" + urllib.parse.quote("Settings saved.")
        )

    def _start_image_search(self, query: str) -> None:
        self.set_loading(True)
        self.app.set_status(f"Searching images for {query!r} ...")

        def work() -> None:
            try:
                urls = self.app.search_images(query)
                self.app.ui(lambda: self._finish_image_search(query, urls, ""))
            except Exception as exc:
                message = str(exc)
                self.app.ui(lambda: self._finish_image_search(query, [], message))

        self.app.submit(work)

    def _finish_image_search(self, query: str, urls: List[str], error: str) -> None:
        if not self.winfo_exists():
            return
        self.set_loading(False)
        self.title = f"Images: {query}"
        html_text = pages.image_search_page(query, urls, error)
        self._install_document(html_text)

    # ------------------------------------------------------------------
    # Document installation
    # ------------------------------------------------------------------
    def _install_document(self, html_text: str,
                          extra_sources: Optional[List[str]] = None) -> None:
        self.app.last_source = html_text
        try:
            dom = parse(html_text)
        except Exception as exc:
            self.app.set_status(f"Parse error: {exc}")
            return
        # The page's own <style> blocks plus anything fetched from
        # <link rel=stylesheet>; both go through the same cascade.
        sources: List[str] = []
        inline = collect_style_text(dom)
        if inline.strip():
            sources.append(inline)
        sources.extend(src for src in (extra_sources or []) if src)
        self.css_sheets = [parse_stylesheet(src) for src in sources]
        self.dom = dom
        self.images.clear()
        self.loading_images.clear()
        self.search_term = ""
        self.title = document_title(dom) or self.title
        self._run_scripts_if_enabled(dom)
        self.relayout()
        self._schedule_timers()
        self._load_images()
        self.app.update_chrome(self)
        self.app.refresh_devtools(self)

    def scripts_enabled(self) -> bool:
        return str(self.app.settings.get("javascript", "off")).lower() == "on"

    def _run_scripts_if_enabled(self, dom: HTMLNode) -> None:
        """Create the per-document JS runtime and execute <script> blocks."""
        self._cancel_timers()
        self.script_runtime = None
        self.script_errors = 0
        if getattr(self, "_skip_scripts", False):
            self._skip_scripts = False
            self.app.set_status("Scripts skipped for this load")
            return
        if not self.scripts_enabled():
            return
        try:
            from core.js.dom_bridge import DocumentRuntime

            runtime = DocumentRuntime(
                dom, base_url=self.current_url,
                on_mutate=self._on_script_mutation,
                on_console=self._on_script_console,
            )
            runtime.viewport = (max(320, self.canvas.winfo_width() or 1024),
                                max(240, self.canvas.winfo_height() or 768))
            self.script_runtime = runtime
            errors = runtime.run_scripts()
            self.script_errors = len(errors)
            for error in errors[:4]:
                self.app.log_console(error, "error")
            if errors:
                self.app.set_status(
                    f"{len(errors)} script error(s) — press F12 for details")
        except Exception as exc:
            traceback.print_exc()
            self.app.log_console(f"script runtime failure: {exc}", "error")

    def _on_script_mutation(self) -> None:
        """A script changed the DOM: relayout once, after the batch settles."""
        if getattr(self, "_mutation_job", None) is not None:
            return
        self._mutation_job = self.after(60, self._apply_script_mutation)

    def _apply_script_mutation(self) -> None:
        self._mutation_job = None
        if self.winfo_exists() and self.dom is not None:
            self.schedule_paint()

    def _on_script_console(self, level: str, text: str) -> None:
        self.app.log_console(text, level)

    def _cancel_timers(self) -> None:
        if self._timer_job is not None:
            try:
                self.after_cancel(self._timer_job)
            except Exception:
                pass
            self._timer_job = None

    def _schedule_timers(self) -> None:
        runtime = self.script_runtime
        if runtime is None or not self.winfo_exists():
            return
        delay = runtime.next_timer_delay()
        if delay is None:
            return
        self._timer_job = self.after(int(delay * 1000) + 5, self._run_timers)

    def _run_timers(self) -> None:
        self._timer_job = None
        runtime = self.script_runtime
        if runtime is None or not self.winfo_exists():
            return
        if runtime.run_due_timers():
            self.schedule_paint()
        self._schedule_timers()

    # ------------------------------------------------------------------
    # Layout + paint
    # ------------------------------------------------------------------
    def relayout(self) -> None:
        if self.dom is None:
            return
        width = max(320, self.canvas.winfo_width() or 0)
        if width <= 1:
            width = max(320, self.app.root.winfo_width() - 40)
        try:
            page_bg = self._page_background()
            compute_styles(
                self.dom, self.css_sheets,
                viewport_width=width,
                viewport_height=max(300, self.canvas.winfo_height() or 720),
                zoom=self.zoom,
                page_background=page_bg,
            )
            self.layout = build_layout_tree(self.dom)
            if self.layout is None:
                return
            apply_list_markers(self.layout)
            compute_layout(self.layout, 0, 0, width, self.app.measurer)
            self._ensure_root_covers_content()
            self._paint(width, keep_widgets=False)
        except RecursionError:
            self.app.set_status("This page is nested too deeply to lay out.")
        except Exception:
            traceback.print_exc()
            self.app.set_status("Layout error (see console)")

    def _ensure_root_covers_content(self) -> None:
        """
        Make the page at least as tall as everything on it.

        CSS lets a fixed-height body overflow its box; a canvas scroll region
        does not, so the scrollable area is extended to the real content
        extent (this is also what makes `height: 100%` pages scroll).
        """
        if self.layout is None:
            return
        bottom = self.layout.y + self.layout.height
        for box in self.layout.walk():
            bottom = max(bottom, box.y + box.height)
        if bottom > self.layout.height:
            self.layout.height = bottom - self.layout.y

    def _page_background(self) -> Optional[str]:
        if self.dom is None:
            return None
        body = self.dom.find("body")
        for node in (body, self.dom):
            if node is None:
                continue
            colour = node.style.get("background-color")
            if colour:
                return str(colour)
        return None

    def _viewport_band(self):
        """The slice of the page we keep as canvas items (1.75 screens)."""
        height = max(1, self.canvas.winfo_height() or 800)
        top = self.canvas.canvasy(0)
        return (top - height * 0.75, top + height * 1.75)

    def _paint(self, width: int, keep_widgets: bool = False) -> None:
        if not keep_widgets:
            for widget in self.form_widgets:
                try:
                    widget.destroy()
                except Exception:
                    pass
            self.form_widgets = []
            self.form_entries.clear()
            self._widget_pos.clear()
            self._widget_for_node.clear()

        if self.layout is None:
            return
        background = self._page_background() or "#ffffff"
        try:
            self.canvas.config(bg=background)
        except tk.TclError:
            pass

        band = self._viewport_band()
        self._drawn_range = band
        self.renderer.measurer = self.app.measurer
        self.renderer.render(self.layout, self.current_url, self.search_term,
                             viewport=band)
        self.search_hits = self.renderer.search_hits
        total = int(self.layout.height) + 40
        self.canvas.config(scrollregion=(0, 0, width, total))
        self._sync_form_widgets()
        self._sync_mascot_boxes()

        if self.search_term:
            self.app.set_status(
                f"{self.search_hits} match(es) for {self.search_term!r}"
            )

    def _repaint_viewport(self) -> None:
        """Re-render the visible band without redoing layout."""
        if self.layout is None or not self.winfo_exists():
            return
        width = max(320, self.canvas.winfo_width() or 0) or 320
        try:
            self._paint(width, keep_widgets=True)
        except Exception:
            traceback.print_exc()

    def _sync_form_widgets(self) -> None:
        """Place live Tk form widgets at their page position minus scroll."""
        if not self._widget_pos:
            return
        offset = self.canvas.canvasy(0)
        height = self.canvas.winfo_height() or 600
        for widget, (x, y, w, h) in list(self._widget_pos.items()):
            try:
                if not widget.winfo_exists():
                    self._widget_pos.pop(widget, None)
                    continue
                # Off-screen widgets stay alive (so typing is never lost)
                # but are moved out of the viewport.
                y_view = y - offset
                if y_view + h < -80 or y_view > height + 80:
                    widget.place_forget()
                else:
                    widget.place(x=x, y=y_view, width=max(1, int(w)),
                                 height=max(1, int(h)))
            except tk.TclError:
                self._widget_pos.pop(widget, None)

    def _sync_mascot_boxes(self) -> None:
        """`<alien>` blocks get a live pixel mascot drawn on top of them."""
        if self.layout is None:
            return
        for box in self.layout.walk():
            node = box.node
            if node.tag != "alien":
                continue
            widget = self._widget_for_node.get(node)
            size = int(min(max(box.height or 64, 40), 96))
            if widget is None or not widget.winfo_exists():
                from ui.mascot import Mascot
                widget = Mascot(self.canvas, size=size, bg="#000000",
                                on_hover=lambda msg: self.app.set_status(msg or "Done"))
                self._widget_for_node[node] = widget
            self._position_widget(widget, box.x, box.y, box.width or 64, box.height or 64)

    def schedule_paint(self) -> None:
        """Coalesce repeated repaint requests (e.g. many images finishing)."""
        if self._render_scheduled:
            return
        self._render_scheduled = True

        def run() -> None:
            self._render_scheduled = False
            if self.winfo_exists():
                self.relayout()

        self.after(40, run)

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------
    def _load_images(self) -> None:
        if self.dom is None:
            return
        generation = self.generation
        for url, node in image_urls(self.dom, self.current_url):
            if url in self.images or url in self.loading_images:
                continue
            self.loading_images.add(url)
            self.app.submit(lambda u=url: self._fetch_image(u, generation))

    def _fetch_image(self, url: str, generation: int) -> None:
        data = None
        try:
            response = self.app.client.get(url, referer=self.current_url)
            if response.ok:
                data = response.body
        except Exception:
            data = None
        self.app.ui(lambda: self._store_image(url, data, generation))

    def _store_image(self, url: str, data: Optional[bytes], generation: int) -> None:
        self.loading_images.discard(url)
        if not self.winfo_exists() or generation != self.generation:
            return
        photo = None
        if data:
            try:
                photo = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
            except Exception:
                photo = None
        self.images[url] = photo
        if photo is not None and self.dom is not None:
            for node in self.dom.walk():
                if node.tag != "img":
                    continue
                src = node.attributes.get("src")
                if not src:
                    continue
                if urllib.parse.urljoin(self.current_url, src) == url:
                    if "width" not in node.attributes:
                        node.replaced_width = float(photo.width())
                    if "height" not in node.attributes:
                        node.replaced_height = float(photo.height())
        self.schedule_paint()

    def get_image(self, url: str) -> Optional[tk.PhotoImage]:
        return self.images.get(url)

    # ------------------------------------------------------------------
    # Form widgets
    # ------------------------------------------------------------------
    def _make_form_widget(self, node: HTMLNode, x: float, y: float,
                          w: float, h: float, style: Dict[str, object]) -> bool:
        """Create a live Tk widget for a form control. Returns True if handled."""
        tag = node.tag
        itype = (node.attributes.get("type") or "text").lower()
        if tag == "input" and itype in ("hidden",):
            return True

        # Scrolling repaints the page many times a second: never rebuild a
        # widget that already exists, or typed text would be lost mid-scroll.
        existing = self._widget_for_node.get(node)
        if existing is not None and existing.winfo_exists():
            self._position_widget(existing, x, y, w, h)
            return True

        widget: Optional[tk.Widget] = None
        if tag == "input" and itype in ("text", "search", "email", "url", "tel", "number", "password"):
            widget = tk.Entry(self.canvas, relief="solid", bd=1, bg="#ffffff",
                              fg="#000000", insertbackground="#000000")
            value = node.attributes.get("value", "")
            if value:
                widget.insert(0, value)
            if itype == "password":
                widget.config(show="\u2022")
        elif tag == "textarea":
            widget = tk.Text(self.canvas, relief="solid", bd=1, bg="#ffffff",
                             fg="#000000", wrap="word", insertbackground="#000000")
            text = "".join(c.text for c in node.children)
            if text.strip():
                widget.insert("1.0", text.strip())
        elif tag == "input" and itype in ("checkbox", "radio"):
            variable = tk.BooleanVar(value="checked" in node.attributes)
            widget = tk.Checkbutton(self.canvas, variable=variable, bg="#ffffff",
                                    activebackground="#ffffff", bd=0,
                                    highlightthickness=0)
            self.form_entries[node] = variable  # type: ignore[assignment]
        elif tag == "select":
            options = [o.text_content().strip() for o in node.children if o.tag == "option"]
            variable = tk.StringVar(value=options[0] if options else "")
            widget = ttk.Combobox(self.canvas, values=options, textvariable=variable,
                                  state="readonly")
            self.form_entries[node] = variable  # type: ignore[assignment]
        elif tag in ("button", "input") and (tag == "button" or itype in ("submit", "button", "reset")):
            if self._node_has_script_click(node):
                # Painted button: the canvas click will reach the script.
                return False
            label = (node.attributes.get("value")
                     or "".join(c.text for c in node.children).strip()
                     or "Submit")
            widget = tk.Button(self.canvas, text=label, relief="raised", bd=1,
                               bg="#e8e8e8", fg="#111111", activebackground="#d5d5d5",
                               cursor="hand2",
                               command=lambda n=node: self._activate_button(n))
        else:
            return False

        if widget is None:
            return False
        self._widget_for_node[node] = widget
        self._position_widget(widget, x, y, w, h)
        if node not in self.form_entries:
            self.form_entries[node] = widget
        if isinstance(widget, (tk.Entry, tk.Text)):
            widget.bind("<Return>", lambda e, n=node: self.submit_form(n))
        if isinstance(widget, ttk.Combobox):
            widget.bind("<<ComboboxSelected>>", lambda e, n=node: None)
        return True

    def _activate_button(self, node: HTMLNode) -> None:
        if self._dispatch_on(node, {"button": 0}):
            return
        self.submit_form(node)

    def _position_widget(self, widget, x: float, y: float, w: float, h: float) -> None:
        self._widget_pos[widget] = (x, y, w, h)
        if widget not in self.form_widgets:
            self.form_widgets.append(widget)
        offset = self.canvas.canvasy(0)
        height = self.canvas.winfo_height() or 600
        try:
            y_view = y - offset
            if y_view + h < -80 or y_view > height + 80:
                widget.place_forget()
            else:
                widget.place(x=x, y=y_view, width=max(1, int(w)),
                             height=max(1, int(h)))
        except tk.TclError:
            pass

    def node_summary(self, node: HTMLNode) -> str:
        attrs = " ".join(f'{k}="{v}"' for k, v in list(node.attributes.items())[:6])
        return f"<{node.tag}{(' ' + attrs) if attrs else ''}>"

    def submit_form(self, trigger: HTMLNode) -> None:
        form = trigger
        while form is not None and form.tag != "form":
            form = form.parent
        if form is None:
            return

        fields: List[Tuple[str, str]] = []
        for node in form.walk():
            if node.tag not in ("input", "textarea", "select"):
                continue
            name = node.attributes.get("name")
            if not name:
                continue
            itype = (node.attributes.get("type") or "text").lower()
            if itype in ("submit", "button", "reset", "image", "file"):
                continue

            value = ""
            widget = self.form_entries.get(node)
            if isinstance(widget, tk.Entry):
                value = widget.get()
            elif isinstance(widget, tk.Text):
                value = widget.get("1.0", "end-1c")
            elif isinstance(widget, tk.Variable):
                current = widget.get()
                if isinstance(current, bool):
                    if not current:
                        continue
                    value = node.attributes.get("value", "on")
                else:
                    value = str(current)
            else:
                value = node.attributes.get("value", "")

            if itype in ("checkbox", "radio") and value == "":
                continue
            fields.append((name, value))

        action = form.attributes.get("action", "")
        target = urllib.parse.urljoin(self.current_url, action or self.current_url)
        method = (form.attributes.get("method") or "GET").upper()

        if method == "POST" and target.startswith(INTERNAL_PREFIX):
            # Internal pages use POST-style submission for credentials: never
            # put a password in a URL.
            query = urllib.parse.urlencode(fields)
            self._open_internal(target + ("&" if "?" in target else "?") + query)
            return

        if method == "POST":
            self._post_form(target, fields)
            return

        query = urllib.parse.urlencode(fields)
        separator = "&" if "?" in target else "?"
        self.navigate(target + (separator + query if query else ""))

    def _post_form(self, target: str, fields: List[Tuple[str, str]]) -> None:
        self.generation += 1
        generation = self.generation
        if self.current_url:
            self.back_stack.append(self.current_url)
            self.forward_stack.clear()
        self.current_url = target
        self.set_loading(True)
        self.app.set_status(f"Submitting to {target} ...")

        def work() -> None:
            try:
                response = self.app.client.post(target, data=dict(fields),
                                                referer=self.current_url)
                html_text = response.text
                final_url = response.url
                dom = parse(html_text)
                sources = [collect_style_text(dom)]
                for sheet_url in stylesheet_links(dom, final_url):
                    try:
                        sheet = self.app.client.get(sheet_url, referer=final_url)
                        if sheet.ok:
                            sources.append(sheet.text)
                    except Exception:
                        continue
                self.app.ui(lambda: self._on_loaded(generation, final_url, html_text, sources))
            except Exception as exc:
                message = str(exc)
                self.app.ui(lambda: self._on_failed(generation, target, message))

        self.app.submit(work)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def set_loading(self, loading: bool) -> None:
        self.is_loading = loading
        self.app.update_loading_state()

    def find(self, term: str) -> int:
        self.search_term = term
        self.relayout()
        return self.search_hits

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.4, min(3.0, round(zoom, 2)))
        self.app.measurer.clear()
        self.relayout()

    def _start_game(self) -> None:
        try:
            from ui.game import SpaceInvaders
            self.stop_game()
            self._game = SpaceInvaders(self.canvas, self.app)
            self._game.start()
            self.title = "Alien Defender"
            self.app.update_chrome(self)
        except Exception as exc:
            self.app.set_status(f"Could not start game: {exc}")

    def stop_game(self) -> None:
        if self._game is not None:
            try:
                self._game.stop()
            except Exception:
                pass
            self._game = None

    def destroy(self) -> None:
        self._cancel_timers()
        self.script_runtime = None
        self.stop_game()
        self.generation += 1
        super().destroy()
