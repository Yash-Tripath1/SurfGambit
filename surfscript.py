"""
surfscript.py — compatibility surface for the legacy scripting API.

The interpreter now lives in `core.js.surfscript`, wired to the engine's real
DOM (`core.dom.tree.HTMLNode`) instead of a private copy of it. This module
keeps the documented legacy names so existing code keeps working:

    execute_script(source, dom_root, trigger_layout_callback)
    evaluate_event_handler(handler_code, dom_root, trigger_layout_callback)
    find_node_by_id(root, node_id)
    Lexer, JSParser, JSEvaluator, DOMElementWrapper, DOMStyleWrapper

The `trigger_layout_callback` argument is still accepted and is called on every
DOM mutation (it is the old name for `on_mutation`).
"""

from __future__ import annotations

from typing import Callable, Optional

from core.dom.tree import HTMLNode, TEXT_TAG
from core.js.surfscript import (DOMElementWrapper, DOMStyleWrapper, JSEvaluator,
                                JSParser, Lexer, ScriptError, Token,
                                evaluate_event_handler as _evaluate_handler,
                                execute_script as _execute,
                                find_node_by_id, run_document, run_handler)

__all__ = [
    "HTMLNode", "TEXT_TAG", "Token", "Lexer", "JSParser", "JSEvaluator",
    "DOMElementWrapper", "DOMStyleWrapper", "ScriptError",
    "execute_script", "evaluate_event_handler", "find_node_by_id",
    "run_document", "run_handler",
]


def execute_script(source: str, dom_root: HTMLNode,
                   trigger_layout_callback: Optional[Callable[[], None]] = None,
                   on_console: Optional[Callable[[str], None]] = None) -> JSEvaluator:
    return _execute(source, dom_root, trigger_layout_callback, on_console)


def evaluate_event_handler(handler_code: str, dom_root: HTMLNode,
                           trigger_layout_callback: Optional[Callable[[], None]] = None,
                           on_console: Optional[Callable[[str], None]] = None) -> JSEvaluator:
    return _evaluate_handler(handler_code, dom_root, trigger_layout_callback, on_console)
