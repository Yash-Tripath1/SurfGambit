"""
core/js/dom_bridge.py — the DOM half of SurfScript.

`DocumentRuntime` owns one interpreter per document. It exposes `document`,
`window`, `Element` and `Event` objects to the language core and keeps the
listener registry (the layout nodes themselves have `__slots__` and carry no
script state).

Everything the page can touch goes through this file, so the security story is
simple: there is no fetch, no eval, no filesystem, and every mutation calls
back into the tab through `on_mutate`, which schedules a repaint.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.dom.tree import HTMLNode, TEXT_TAG
from core.js.surfscript import (
    Interpreter, JSObject, JSFunction, NativeFunction, Scope,
    ScriptError, JSRuntimeError, Lexer, JSParser, UNDEFINED,
    truthy, to_string, to_number,
)

__all__ = ["DocumentRuntime", "Element", "Document", "Event"]


_ELEMENT_CACHE_LIMIT = 4000


class Event(JSObject):
    """A DOM event object handed to listeners."""

    __slots__ = ("type", "target", "current_target", "fields",
                 "default_prevented", "propagation_stopped")

    def __init__(self, type_: str, target: Any, fields: Optional[Dict[str, Any]] = None) -> None:
        self.type = type_
        self.target = target
        self.current_target = target
        self.fields = dict(fields or {})
        self.default_prevented = False
        self.propagation_stopped = False

    def js_get(self, name: str) -> Any:
        if name == "type":
            return self.type
        if name == "target":
            return self.target
        if name == "currentTarget":
            return self.current_target
        if name in ("defaultPrevented", "returnValue"):
            return self.default_prevented if name == "defaultPrevented" else not self.default_prevented
        if name == "preventDefault":
            return NativeFunction("preventDefault", lambda this, args: self._prevent())
        if name == "stopPropagation":
            return NativeFunction("stopPropagation", lambda this, args: self._stop())
        if name in self.fields:
            return self.fields[name]
        return UNDEFINED

    def js_set(self, name: str, value: Any) -> None:
        if name == "returnValue":
            self.default_prevented = not truthy(value)
            return
        self.fields[name] = value

    def _prevent(self) -> Any:
        self.default_prevented = True
        return UNDEFINED

    def _stop(self) -> Any:
        self.propagation_stopped = True
        return UNDEFINED

    def js_to_string(self) -> str:
        return f"[object Event {self.type}]"


class StyleProxy(JSObject):
    """`element.style.color = "red"` and friends."""

    __slots__ = ("element",)

    def __init__(self, element: "Element") -> None:
        self.element = element

    def _property(self, name: str) -> str:
        if name.startswith("--"):
            return name
        out = []
        for ch in name:
            if ch.isupper():
                out.append("-" + ch.lower())
            else:
                out.append(ch)
        css = "".join(out)
        # Things JS spells differently from CSS.
        aliases = {"css-float": "float", "float": "float"}
        return aliases.get(css, css)

    def js_get(self, name: str) -> Any:
        if name == "cssText":
            style = self.element.node.style
            return "; ".join(f"{k}: {v}" for k, v in style.items())
        return to_string(self.element.node.style.get(self._property(name), ""))

    def js_set(self, name: str, value: Any) -> None:
        if name == "cssText":
            self.element.node.style.clear()
            for chunk in to_string(value).split(";"):
                if ":" in chunk:
                    key, _, val = chunk.partition(":")
                    self.element.node.style[key.strip().lower()] = val.strip()
            self.element.mutated()
            return
        text = to_string(value)
        prop = self._property(name)
        if text == "":
            self.element.node.style.pop(prop, None)
        else:
            self.element.node.style[prop] = text
        self.element.mutated()

    def js_keys(self) -> List[str]:
        return list(self.element.node.style.keys())

    def js_to_string(self) -> str:
        return "[object CSSStyleDeclaration]"


class ClassList(JSObject):
    __slots__ = ("element",)

    def __init__(self, element: "Element") -> None:
        self.element = element

    def _classes(self) -> List[str]:
        return (self.element.node.attributes.get("class") or "").split()

    def _write(self, values: List[str]) -> None:
        seen: List[str] = []
        for value in values:
            if value and value not in seen:
                seen.append(value)
        if seen:
            self.element.node.attributes["class"] = " ".join(seen)
        else:
            self.element.node.attributes.pop("class", None)
        self.element.mutated()

    def js_get(self, name: str) -> Any:
        if name == "length":
            return len(self._classes())
        if name == "contains":
            return NativeFunction("contains", lambda t, a:
                                  to_string(a[0]) in self._classes() if a else False)
        if name == "add":
            return NativeFunction("add", lambda t, a: self._add(a))
        if name == "remove":
            return NativeFunction("remove", lambda t, a: self._remove(a))
        if name == "toggle":
            return NativeFunction("toggle", lambda t, a: self._toggle(a))
        if name == "replace":
            return NativeFunction("replace", lambda t, a: self._replace(a))
        if name == "toString":
            return NativeFunction("toString", lambda t, a: " ".join(self._classes()))
        if name == "item":
            return NativeFunction("item", lambda t, a:
                                  self._classes()[int(to_number(a[0]))]
                                  if a and int(to_number(a[0])) < len(self._classes())
                                  else UNDEFINED)
        return UNDEFINED

    def _add(self, args: List[Any]) -> Any:
        classes = self._classes()
        for value in args:
            text = to_string(value)
            if text and text not in classes:
                classes.append(text)
        self._write(classes)
        return UNDEFINED

    def _remove(self, args: List[Any]) -> Any:
        drop = {to_string(a) for a in args}
        self._write([c for c in self._classes() if c not in drop])
        return UNDEFINED

    def _toggle(self, args: List[Any]) -> Any:
        if not args:
            return UNDEFINED
        name = to_string(args[0])
        classes = self._classes()
        if name in classes:
            classes.remove(name)
            self._write(classes)
            return False
        classes.append(name)
        self._write(classes)
        return True

    def _replace(self, args: List[Any]) -> Any:
        if len(args) < 2:
            return False
        old, new = to_string(args[0]), to_string(args[1])
        classes = self._classes()
        if old not in classes:
            return False
        classes[classes.index(old)] = new
        self._write(classes)
        return True

    def js_to_string(self) -> str:
        return " ".join(self._classes())


class Element(JSObject):
    """A DOM element exposed to script."""

    __slots__ = ("runtime", "node")

    def __init__(self, runtime: "DocumentRuntime", node: HTMLNode) -> None:
        self.runtime = runtime
        self.node = node

    # -- helpers ---------------------------------------------------------
    def mutated(self) -> None:
        self.runtime.note_mutation()

    def _wrap(self, node: Optional[HTMLNode]) -> Any:
        return self.runtime.wrap(node)

    def _elements(self) -> List[HTMLNode]:
        return [child for child in self.node.children if child.tag != TEXT_TAG]

    def _query(self, selector: str) -> List[HTMLNode]:
        return self.runtime.select(selector, root=self.node)

    # -- properties ------------------------------------------------------
    def js_get(self, name: str) -> Any:
        node = self.node
        if name in ("tagName", "nodeName"):
            return node.tag.upper()
        if name == "id":
            return node.attributes.get("id", "")
        if name == "className":
            return node.attributes.get("class", "")
        if name in ("textContent", "innerText"):
            return node.text_content()
        if name == "innerHTML":
            return self.runtime.serialize(node)
        if name == "outerHTML":
            return self.runtime.serialize(node, outer=True)
        if name in ("children", "childNodes"):
            items = self._elements() if name == "children" else list(node.children)
            return [self._wrap(child) for child in items]
        if name == "parentNode" or name == "parentElement":
            return self._wrap(node.parent)
        if name in ("firstChild", "firstElementChild"):
            items = list(node.children) if name == "firstChild" else self._elements()
            return self._wrap(items[0]) if items else None
        if name in ("lastChild", "lastElementChild"):
            items = list(node.children) if name == "lastChild" else self._elements()
            return self._wrap(items[-1]) if items else None
        if name in ("nextSibling", "nextElementSibling", "previousSibling",
                    "previousElementSibling"):
            return self._sibling(name)
        if name == "style":
            return StyleProxy(self)
        if name == "classList":
            return ClassList(self)
        if name == "dataset":
            return DatasetProxy(self)
        if name == "value":
            if node.tag == "textarea":
                return node.text_content()
            if node.tag == "select":
                chosen = next((o for o in node.children
                               if "selected" in o.attributes), None)
                if chosen is None:
                    options = [o for o in node.children if o.tag == "option"]
                    chosen = options[0] if options else None
                return chosen.text_content().strip() if chosen is not None else ""
            return node.attributes.get("value", "")
        if name == "checked":
            return "checked" in node.attributes
        if name == "selected":
            return "selected" in node.attributes
        if name in ("href", "src", "title", "type", "name", "placeholder", "alt", "action"):
            return node.attributes.get(name, "")
        if name in ("offsetWidth", "offsetHeight", "clientWidth", "clientHeight"):
            return self.runtime.size_of(node, name)
        if name in ("getAttribute", "setAttribute", "removeAttribute", "hasAttribute"):
            return NativeFunction(name, lambda t, a, n=name: self._attribute(n, a))
        if name in ("appendChild", "removeChild", "insertBefore", "replaceChild"):
            return NativeFunction(name, lambda t, a, n=name: self._tree(n, a))
        if name in ("querySelector", "querySelectorAll", "getElementsByTagName",
                    "getElementsByClassName", "getElementById"):
            return NativeFunction(name, lambda t, a, n=name: self._select(n, a))
        if name in ("addEventListener", "removeEventListener"):
            return NativeFunction(name, lambda t, a, n=name: self._listener(n, a))
        if name == "dispatchEvent":
            return NativeFunction("dispatchEvent", lambda t, a: self._dispatch(a))
        if name == "click":
            return NativeFunction("click", lambda t, a: self._click())
        if name in ("focus", "blur", "scrollIntoView"):
            return NativeFunction(name, lambda t, a: UNDEFINED)
        if name == "remove":
            return NativeFunction("remove", lambda t, a: self._remove())
        if name in ("getBoundingClientRect",):
            return NativeFunction(name, lambda t, a: self._rect())
        return UNDEFINED

    def js_set(self, name: str, value: Any) -> None:
        node = self.node
        if name == "id":
            node.attributes["id"] = to_string(value)
        elif name == "className":
            node.attributes["class"] = to_string(value)
        elif name in ("textContent", "innerText"):
            node.clear()
            text = to_string(value)
            if text:
                node.append(HTMLNode(TEXT_TAG, text=text))
        elif name == "innerHTML":
            node.clear()
            for child in self.runtime.parse_fragment(to_string(value)):
                node.append(child)
        elif name == "value":
            if node.tag == "textarea":
                node.clear()
                node.append(HTMLNode(TEXT_TAG, text=to_string(value)))
            else:
                node.attributes["value"] = to_string(value)
        elif name == "checked":
            if truthy(value):
                node.attributes["checked"] = ""
            else:
                node.attributes.pop("checked", None)
        elif name in ("href", "src", "title", "type", "name", "placeholder", "alt"):
            node.attributes[name] = to_string(value)
        elif name == "style":
            if isinstance(value, str):
                for chunk in value.split(";"):
                    if ":" in chunk:
                        key, _, val = chunk.partition(":")
                        node.style[key.strip().lower()] = val.strip()
            return
        else:
            node.attributes[name] = to_string(value)
        self.mutated()

    # -- operations ------------------------------------------------------
    def _attribute(self, op: str, args: List[Any]) -> Any:
        if not args:
            return UNDEFINED
        key = to_string(args[0])
        if op == "getAttribute":
            return self.node.attributes.get(key, None)
        if op == "hasAttribute":
            return key in self.node.attributes
        if op == "removeAttribute":
            self.node.attributes.pop(key, None)
            self.mutated()
            return UNDEFINED
        self.node.attributes[key] = to_string(args[1]) if len(args) > 1 else ""
        self.mutated()
        return UNDEFINED

    def _tree(self, op: str, args: List[Any]) -> Any:
        if not args:
            return UNDEFINED
        child = args[0]
        node = child.node if isinstance(child, Element) else None
        if op == "removeChild":
            if node is not None and node.parent is self.node:
                self.node.children.remove(node)
                node.parent = None
                self.mutated()
            return child
        if op == "insertBefore":
            if node is None:
                return child
            reference = args[1].node if len(args) > 1 and isinstance(args[1], Element) else None
            if node.parent is not None:
                node.parent.children.remove(node)
            index = 0
            if reference is not None and reference in self.node.children:
                index = self.node.children.index(reference)
            node.parent = self.node
            self.node.children.insert(index, node)
            self.mutated()
            return child
        if op == "replaceChild":
            if node is None:
                return child
            old = args[1].node if len(args) > 1 and isinstance(args[1], Element) else None
            if old is not None and old in self.node.children:
                index = self.node.children.index(old)
                self.node.children[index] = node
                node.parent = self.node
                old.parent = None
                self.mutated()
            return args[1] if len(args) > 1 else UNDEFINED
        # appendChild
        if node is not None:
            if node.parent is not None:
                node.parent.children.remove(node)
            self.node.append(node)
            if isinstance(child, Element):
                pass
            self.mutated()
        return child

    def _remove(self) -> Any:
        if self.node.parent is not None:
            self.node.parent.children.remove(self.node)
            self.node.parent = None
            self.mutated()
        return UNDEFINED

    def _select(self, op: str, args: List[Any]) -> Any:
        if op == "querySelector":
            found = self._query(to_string(args[0]) if args else "")
            return self._wrap(found[0]) if found else None
        if op == "querySelectorAll":
            return [self._wrap(node) for node in self._query(to_string(args[0]) if args else "")]
        if op == "getElementById":
            target = to_string(args[0]) if args else ""
            for node in self.node.walk():
                if node.attributes.get("id") == target:
                    return self._wrap(node)
            return None
        if op == "getElementsByTagName":
            tag = (to_string(args[0]) if args else "*").lower()
            return [self._wrap(node) for node in self.node.walk()
                    if node.tag != TEXT_TAG and (tag == "*" or node.tag == tag)]
        classes = set((to_string(args[0]) if args else "").split())
        return [self._wrap(node) for node in self.node.walk()
                if node.tag != TEXT_TAG and classes.issubset(set(node.classes))]

    def _listener(self, op: str, args: List[Any]) -> Any:
        if len(args) < 2:
            return UNDEFINED
        event_type = to_string(args[0])
        handler = args[1]
        if not isinstance(handler, (JSFunction, NativeFunction)):
            return UNDEFINED
        if op == "addEventListener":
            self.runtime.add_listener(self.node, event_type, handler)
        else:
            self.runtime.remove_listener(self.node, event_type, handler)
        return UNDEFINED

    def _dispatch(self, args: List[Any]) -> Any:
        if not args:
            return False
        event = args[0]
        if isinstance(event, Event):
            self.runtime.dispatch(self.node, event)
            return not event.default_prevented
        return True

    def _click(self) -> Any:
        event = Event("click", self)
        self.runtime.dispatch(self.node, event)
        return UNDEFINED

    def _sibling(self, name: str) -> Any:
        parent = self.node.parent
        if parent is None:
            return None
        items = ([c for c in parent.children if c.tag != TEXT_TAG]
                 if "Element" in name else list(parent.children))
        try:
            index = items.index(self.node)
        except ValueError:
            return None
        step = -1 if "previous" in name else 1
        target = index + step
        if 0 <= target < len(items):
            return self._wrap(items[target])
        return None

    def _rect(self) -> Any:
        return {"x": 0, "y": 0, "width": self.runtime.size_of(self.node, "offsetWidth"),
                "height": self.runtime.size_of(self.node, "offsetHeight")}

    def js_keys(self) -> List[str]:
        return list(self.node.attributes.keys())

    def js_to_string(self) -> str:
        return f"[object HTML{self.node.tag.capitalize()}Element]"


class DatasetProxy(JSObject):
    __slots__ = ("element",)

    def __init__(self, element: Element) -> None:
        self.element = element

    @staticmethod
    def _attr(name: str) -> str:
        out = []
        for ch in name:
            if ch.isupper():
                out.append("-" + ch.lower())
            else:
                out.append(ch)
        return "data-" + "".join(out)

    def js_get(self, name: str) -> Any:
        return self.element.node.attributes.get(self._attr(name), UNDEFINED)

    def js_set(self, name: str, value: Any) -> None:
        self.element.node.attributes[self._attr(name)] = to_string(value)
        self.element.mutated()


class Document(JSObject):
    """`document`, plus the window-level globals a page usually touches."""

    __slots__ = ("runtime",)

    def __init__(self, runtime: "DocumentRuntime") -> None:
        self.runtime = runtime

    def js_get(self, name: str) -> Any:
        runtime = self.runtime
        root = runtime.dom
        if name == "title":
            title_node = next((n for n in root.walk() if n.tag == "title"), None)
            return title_node.text_content().strip() if title_node is not None else ""
        if name == "body":
            return runtime.wrap(root.find("body"))
        if name == "head":
            return runtime.wrap(root.find("head"))
        if name == "documentElement":
            return runtime.wrap(root)
        if name == "readyState":
            return "complete"
        if name == "location":
            return LocationProxy(runtime.base_url)
        if name == "cookie":
            return runtime.cookie_header
        if name == "URL":
            return runtime.base_url
        if name == "referrer":
            return ""
        if name == "characterSet" or name == "charset":
            return "utf-8"
        if name == "hidden":
            return False
        if name == "getElementById":
            return NativeFunction("getElementById", lambda t, a: runtime.by_id(
                to_string(a[0]) if a else ""))
        if name == "querySelector":
            return NativeFunction("querySelector", lambda t, a: runtime.query_one(
                to_string(a[0]) if a else ""))
        if name == "querySelectorAll":
            return NativeFunction("querySelectorAll", lambda t, a: runtime.query_all(
                to_string(a[0]) if a else ""))
        if name == "getElementsByTagName":
            return NativeFunction("getElementsByTagName", lambda t, a: runtime.by_tag(
                to_string(a[0]) if a else "*"))
        if name == "getElementsByClassName":
            return NativeFunction("getElementsByClassName", lambda t, a: runtime.by_class(
                to_string(a[0]) if a else ""))
        if name == "createElement":
            return NativeFunction("createElement", lambda t, a:
                                  runtime.wrap(runtime.create_element(
                                      to_string(a[0]) if a else "div")))
        if name == "createTextNode":
            return NativeFunction("createTextNode", lambda t, a:
                                  runtime.wrap(runtime.create_text(
                                      to_string(a[0]) if a else "")))
        if name in ("addEventListener", "removeEventListener"):
            return NativeFunction(name, lambda t, a, n=name: runtime.document_listener(n, a))
        if name == "write":
            return NativeFunction("write", lambda t, a: UNDEFINED)
        return UNDEFINED

    def js_set(self, name: str, value: Any) -> None:
        if name == "title":
            self.runtime.set_title(to_string(value))
            return
        if name == "cookie":
            self.runtime.cookie_header = to_string(value)
            return
        raise JSRuntimeError(f"cannot set document.{name}")


class LocationProxy(JSObject):
    __slots__ = ("href_value",)

    def __init__(self, href: str) -> None:
        self.href_value = href

    def js_get(self, name: str) -> Any:
        if name in ("href", "toString"):
            if name == "toString":
                return NativeFunction("toString", lambda t, a: self.href_value)
            return self.href_value
        if name == "protocol":
            return self.href_value.split(":", 1)[0] + ":"
        if name == "host":
            parts = self.href_value.split("/")
            return parts[2] if len(parts) > 2 else ""
        return UNDEFINED

    def js_set(self, name: str, value: Any) -> None:
        if name == "href":
            self.href_value = to_string(value)


class NavigatorProxy(JSObject):
    __slots__ = ("runtime",)

    def __init__(self, runtime: "DocumentRuntime") -> None:
        self.runtime = runtime

    def js_get(self, name: str) -> Any:
        if name == "userAgent":
            return ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SurfGambit/3.0")
        if name == "language":
            return "en-US"
        if name == "cookieEnabled":
            return True
        if name == "onLine":
            return True
        if name == "platform":
            return "Linux x86_64"
        return UNDEFINED


class WindowProxy(JSObject):
    __slots__ = ("runtime",)

    def __init__(self, runtime: "DocumentRuntime") -> None:
        self.runtime = runtime

    def js_get(self, name: str) -> Any:
        if name in ("document", "window", "self", "globalThis"):
            return self if name != "document" else self.runtime.document
        if name == "location":
            return LocationProxy(self.runtime.base_url)
        if name == "navigator":
            return NavigatorProxy(self.runtime)
        if name in ("innerWidth", "outerWidth"):
            return self.runtime.viewport[0]
        if name in ("innerHeight", "outerHeight"):
            return self.runtime.viewport[1]
        if name == "alert":
            return NativeFunction("alert", lambda t, a:
                                  self.runtime.console("log", "alert: " +
                                                       to_string(a[0] if a else "")))
        if name in ("addEventListener", "removeEventListener"):
            return NativeFunction(name, lambda t, a, n=name:
                                  self.runtime.document_listener(n, a))
        try:
            return self.runtime.interp.globals.get(name)
        except ScriptError:
            return UNDEFINED

    def js_set(self, name: str, value: Any) -> None:
        self.runtime.interp.globals.declare(name, value)


# ============================================================
# Runtime
# ============================================================

class DocumentRuntime:
    """Owns the interpreter, the listener registry and the mutation hook."""

    def __init__(self, dom: HTMLNode, base_url: str = "",
                 on_mutate: Optional[Callable[[], None]] = None,
                 on_console: Optional[Callable[[str, str], None]] = None,
                 max_steps: int = 300_000,
                 time_budget: float = 1.5) -> None:
        self.dom = dom
        self.base_url = base_url
        self.on_mutate = on_mutate
        self.on_console = on_console
        self.errors: List[str] = []
        self.viewport: Tuple[int, int] = (1024, 768)
        self._elements: Dict[int, Element] = {}
        self._nodes: Dict[int, HTMLNode] = {}
        self._listeners: Dict[int, Dict[str, List[Any]]] = {}
        self._compiled: Dict[str, Any] = {}
        self._mutation_pending = False
        self.cookie_header = ""
        self.interp = Interpreter(
            on_console=self._console_hook, max_steps=max_steps,
            time_budget=time_budget)
        self.document = Document(self)
        self.window = WindowProxy(self)
        self.interp.globals.declare("document", self.document, "const")
        self.interp.globals.declare("window", self.window, "const")
        self.interp.globals.declare("self", self.window, "const")

    # -- plumbing --------------------------------------------------------
    def _console_hook(self, level: str, text: str) -> None:
        if self.on_console is not None:
            try:
                self.on_console(level, text)
            except Exception:
                pass

    def console(self, level: str, text: str) -> Any:
        self.interp.log_console(level, text)
        return UNDEFINED

    def note_mutation(self) -> None:
        if self.on_mutate is not None:
            self.on_mutate()

    # -- wrapping --------------------------------------------------------
    def wrap(self, node: Optional[HTMLNode]) -> Any:
        if node is None:
            return None
        if node.tag == TEXT_TAG:
            return node.text
        key = id(node)
        cached = self._elements.get(key)
        if cached is not None:
            return cached
        if len(self._elements) > _ELEMENT_CACHE_LIMIT:
            self._elements.clear()
        element = Element(self, node)
        self._elements[key] = element
        self._nodes[key] = node
        return element

    def size_of(self, node: HTMLNode, _which: str) -> int:
        return 0

    # -- selection -------------------------------------------------------
    def select(self, selector: str, root: Optional[HTMLNode] = None) -> List[HTMLNode]:
        from core.style.selectors import parse_selector_list

        parsed = parse_selector_list(selector or "")
        if not parsed:
            return []
        base = root if root is not None else self.dom
        out: List[HTMLNode] = []
        for node in base.walk():
            if node.tag == TEXT_TAG:
                continue
            if any(sel.matches(node) for sel in parsed):
                out.append(node)
        return out

    def by_id(self, target: str) -> Any:
        for node in self.dom.walk():
            if node.attributes.get("id") == target:
                return self.wrap(node)
        return None

    def query_one(self, selector: str) -> Any:
        found = self.select(selector)
        return self.wrap(found[0]) if found else None

    def query_all(self, selector: str) -> List[Any]:
        return [self.wrap(node) for node in self.select(selector)]

    def by_tag(self, tag: str) -> List[Any]:
        tag = tag.lower()
        return [self.wrap(node) for node in self.dom.walk()
                if node.tag != TEXT_TAG and (tag == "*" or node.tag == tag)]

    def by_class(self, classes: str) -> List[Any]:
        wanted = set(classes.split())
        return [self.wrap(node) for node in self.dom.walk()
                if node.tag != TEXT_TAG and wanted.issubset(set(node.classes))]

    # -- construction ----------------------------------------------------
    def create_element(self, tag: str) -> HTMLNode:
        return HTMLNode(tag.strip().lower() or "div", {})

    def create_text(self, text: str) -> HTMLNode:
        return HTMLNode(TEXT_TAG, text=text)

    def parse_fragment(self, html_text: str) -> List[HTMLNode]:
        from core.dom.parser import parse

        try:
            holder = parse(f"<body>{html_text}</body>")
        except Exception:
            return []
        body = holder.find("body")
        if body is None:
            return []
        children = list(body.children)
        for child in children:
            child.parent = None
        return children

    def serialize(self, node: HTMLNode, outer: bool = False) -> str:
        def render(item: HTMLNode) -> str:
            if item.tag == TEXT_TAG:
                return _escape(item.text)
            attrs = "".join(f' {k}="{_escape(v)}"' for k, v in item.attributes.items())
            inner = "".join(render(child) for child in item.children)
            return f"<{item.tag}{attrs}>{inner}</{item.tag}>"

        if outer:
            return render(node)
        return "".join(render(child) for child in node.children)

    def set_title(self, text: str) -> None:
        title = next((n for n in self.dom.walk() if n.tag == "title"), None)
        if title is None:
            head = self.dom.find("head")
            if head is None:
                return
            title = head.append(HTMLNode("title"))
        title.clear()
        title.append(HTMLNode(TEXT_TAG, text=text))
        self.note_mutation()

    # -- listeners -------------------------------------------------------
    def add_listener(self, node: HTMLNode, event_type: str, handler: Any) -> None:
        key = id(node)
        self._nodes[key] = node
        bucket = self._listeners.setdefault(key, {})
        bucket.setdefault(event_type, []).append(handler)

    def remove_listener(self, node: HTMLNode, event_type: str, handler: Any) -> None:
        bucket = self._listeners.get(id(node), {})
        handlers = bucket.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def has_listeners(self, node: HTMLNode, event_type: str = "click") -> bool:
        return bool(self._listeners.get(id(node), {}).get(event_type))

    def document_listener(self, op: str, args: List[Any]) -> Any:
        if len(args) < 2:
            return UNDEFINED
        event_type = to_string(args[0])
        handler = args[1]
        if not isinstance(handler, (JSFunction, NativeFunction)):
            return UNDEFINED
        if op == "addEventListener":
            self.add_listener(self.dom, event_type, handler)
        else:
            self.remove_listener(self.dom, event_type, handler)
        return UNDEFINED

    def dispatch(self, target: Optional[HTMLNode], event: Event) -> Event:
        """Bubble `event` from `target` up to the document root."""
        node = target
        first = True
        while node is not None:
            event.current_target = node
            if first:
                event.target = node
                first = False
            handlers = self._listeners.get(id(node), {}).get(event.type, [])
            for handler in list(handlers):
                try:
                    handler.call(self.wrap(node), [event])
                except ScriptError as exc:
                    self.errors.append(str(exc))
                    self.console("error", str(exc))
            attribute = node.attributes.get("on" + event.type)
            if attribute:
                self.run_attribute_handler(node, event.type, attribute, event)
            if event.propagation_stopped:
                break
            node = node.parent
        return event

    def run_attribute_handler(self, node: HTMLNode, event_type: str, code: str,
                              event: Optional[Event] = None) -> None:
        try:
            program = self._compiled.get(code)
            if program is None:
                program = JSParser(Lexer(code).tokenize()).parse()
                self._compiled[code] = program
            scope = Scope(self.interp.globals)
            scope.this_value = self.wrap(node)
            scope.declare("event", event if event is not None else Event(event_type, node))
            self.interp.reset_budget(0.5)
            self.interp.run(program, scope)
        except ScriptError as exc:
            self.errors.append(str(exc))
            self.console("error", f"{event_type} handler on <{node.tag}>: {exc}")

    # -- script execution ------------------------------------------------
    def evaluate_script(self, source: str, scope: Optional[Scope] = None) -> List[str]:
        errors: List[str] = []
        try:
            program = JSParser(Lexer(source).tokenize()).parse()
            self.interp.reset_budget()
            self.interp.run(program, scope or self.interp.globals)
        except ScriptError as exc:
            errors.append(str(exc))
            self.console("error", str(exc))
        except RecursionError:
            errors.append("RangeError: maximum call stack size exceeded")
            self.console("error", "RangeError: maximum call stack size exceeded")
        self.errors.extend(errors)
        return errors

    def evaluate_handler(self, code: str, event_fields: Optional[Dict[str, Any]] = None,
                         node: Optional[HTMLNode] = None) -> List[str]:
        errors: List[str] = []
        try:
            program = JSParser(Lexer(code).tokenize()).parse()
            scope = Scope(self.interp.globals)
            scope.this_value = self.wrap(node or self.dom)
            scope.declare("event", Event("click", self.wrap(node or self.dom),
                                         event_fields or {}))
            self.interp.reset_budget(0.5)
            self.interp.run(program, scope)
        except ScriptError as exc:
            errors.append(str(exc))
            self.console("error", str(exc))
        self.errors.extend(errors)
        return errors

    def script_nodes(self, root: Optional[HTMLNode] = None) -> List[HTMLNode]:
        base = root if root is not None else self.dom
        return [node for node in base.walk() if node.tag == "script"]

    def run_scripts(self) -> List[str]:
        errors: List[str] = []
        for script in self.script_nodes():
            script_type = (script.attributes.get("type") or "").lower()
            if script_type and "javascript" not in script_type and script_type not in (
                    "text/javascript", "application/javascript", "module"):
                continue
            if script.attributes.get("src"):
                self.console("warn", f"skipped external script {script.attributes['src']}")
                continue
            source = "".join(child.text for child in script.children if child.tag == TEXT_TAG)
            if not source.strip():
                continue
            errors.extend(self.evaluate_script(source))
        # <body onload="...">
        body = self.dom.find("body")
        if body is not None and body.attributes.get("onload"):
            self.run_attribute_handler(body, "load", body.attributes["onload"])
        # DOMContentLoaded / load listeners registered by those scripts
        for event_type in ("DOMContentLoaded", "load"):
            event = Event(event_type, self.wrap(self.dom))
            self.dispatch(self.dom, event)
        if self.interp.timers:
            pass
        return errors

    # -- timers ----------------------------------------------------------
    def pending_timers(self) -> int:
        return len([t for t in self.interp.timers if not t.cancelled])

    def next_timer_delay(self) -> Optional[float]:
        live = [t for t in self.interp.timers if not t.cancelled]
        if not live:
            return None
        due = min(timer.due for timer in live)
        return max(0.0, min(5.0, due - time.monotonic()))

    def run_due_timers(self) -> int:
        now = time.monotonic()
        keep = []
        ran = 0
        for timer in self.interp.timers:
            if timer.cancelled:
                continue
            if timer.due <= now + 0.001:
                try:
                    timer.fn.call(UNDEFINED, timer.args)
                except ScriptError as exc:
                    self.errors.append(str(exc))
                    self.console("error", str(exc))
                ran += 1
                if timer.interval is not None and ran < 40:
                    timer.due = time.monotonic() + timer.interval
                    keep.append(timer)
            else:
                keep.append(timer)
        self.interp.timers = keep
        return ran


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
