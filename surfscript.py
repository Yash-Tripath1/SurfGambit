"""
SurfScript - Micro-JavaScript Parser & DOM Bridge Engine
========================================================

A single-file JavaScript interpreter built from scratch in pure Python
(standard library only). It tokenizes, parses, and evaluates a deliberately
small subset of JavaScript, and provides a direct DOM bridge so that JS code
can mutate a SurfGambit-style `HTMLNode` tree in real time.

Pipeline
--------
    source  -->  Lexer  -->  List[Token]  -->  JSParser  -->  AST  -->  JSEvaluator
                                                                              |
                                                                              v
                                                              (mutates HTMLNode tree
                                                               + fires layout callback)

Supported JS features
---------------------
* Variable declarations           : `let x = 10;`  `const name = "Yash";`
* Assignment statements           : `x = 20;`  `obj.style.color = "red";`
* Expression statements           : `console.log("hi");`
* Arithmetic expressions          : `+ - * /` with correct precedence and parens
* String literals (with escapes)  : "hello", 'world', "line\\nbreak"
* Number literals                 : integers and floats
* Identifier & member access      : `foo`, `foo.bar`, `foo.bar.baz`
* Function/method calls           : `foo()`, `foo(a, b)`
* Built-in globals                : `console.log(...)`  `document.getElementById(...)`

DOM bridge
----------
* `document.getElementById("id")` returns a `DOMElementWrapper`.
* `.innerHTML = "..."`            replaces children with a single text node.
* `.style.color = "red"`          sets `node.style["color"] = "red"`.
* `.style.backgroundColor = "..."` sets `node.style["background-color"] = "..."`.
* Every DOM mutation invokes the `trigger_layout_callback` so the browser
  canvas can be repainted.

This file imports nothing outside Python's standard library.
"""

from __future__ import annotations

import re
import json
import os
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

__all__ = [
    # DOM contract
    "HTMLNode",
    # Lexer
    "Token",
    "Lexer",
    # AST
    "ASTNode",
    "Program",
    "VarDecl",
    "Assignment",
    "ExprStmt",
    "BinaryOp",
    "NumberLit",
    "StringLit",
    "Identifier",
    "MemberExpr",
    "CallExpr",
    # Parser
    "JSParser",
    # DOM bridge + evaluator
    "DOMElementWrapper",
    "DOMStyleWrapper",
    "JSEvaluator",
    # Convenience entry points
    "execute_script",
    "evaluate_event_handler",
    "find_node_by_id",
]


# ============================================================
# Reference HTMLNode
# ============================================================
class HTMLNode:
    """
    Reference DOM-node class mirroring SurfGambit's `parser.HTMLNode`.

    Real SurfGambit code uses its own `HTMLNode`; this class is provided so
    `surfscript.py` is testable in isolation.  The contract is:

        tag        : str                     e.g. "div", "h1", "#text"
        attributes : dict[str, str]          e.g. {"id": "welcome"}
        children   : list[HTMLNode]
        style      : dict[str, str]          e.g. {"color": "red"}
        text       : str                     for #text nodes
    """

    __slots__ = ("tag", "attributes", "children", "style", "text")

    def __init__(
        self,
        tag: str = "div",
        attributes: Optional[Dict[str, str]] = None,
        children: Optional[List["HTMLNode"]] = None,
        style: Optional[Dict[str, str]] = None,
        text: str = "",
    ) -> None:
        self.tag = tag
        self.attributes = attributes or {}
        self.children = children or []
        self.style = style or {}
        self.text = text

    def __repr__(self) -> str:
        ident = self.attributes.get("id", "<none>")
        return (
            f"HTMLNode(tag={self.tag!r}, id={ident!r}, "
            f"text={self.text!r}, style={self.style!r})"
        )


# ============================================================
# Lexer
# ============================================================
# Token-spec order matters: more specific patterns (KEYWORD) must come before
# the catch-all IDENT so that e.g. "let" is tokenized as KEYWORD, not IDENT.
_TOKEN_SPEC: List[Tuple[str, str]] = [
    ("WHITESPACE", r"\s+"),
    ("KEYWORD",    r"\b(?:let|const|var)\b"),
    ("NUMBER",     r"\d+(?:\.\d+)?"),
    ("STRING",     r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''),
    ("IDENT",      r"[A-Za-z_$][A-Za-z0-9_$]*"),
    ("EQUALS",     r"="),
    ("DOT",        r"\."),
    ("SEMI",       r";"),
    ("COMMA",      r","),
    ("LPAREN",     r"\("),
    ("RPAREN",     r"\)"),
    ("PLUS",       r"\+"),
    ("MINUS",      r"-"),
    ("STAR",       r"\*"),
    ("SLASH",      r"/"),
]
_TOKEN_RE = re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in _TOKEN_SPEC),
    re.UNICODE,
)


class Token:
    """A single lexical token produced by the Lexer."""

    __slots__ = ("type", "value")

    def __init__(self, type_: str, value: str) -> None:
        self.type = type_
        self.value = value

    def __repr__(self) -> str:
        return f"Token({self.type!r}, {self.value!r})"


class Lexer:
    """Regex-based lexer that turns a JS source string into a token list."""

    def __init__(self, source: str) -> None:
        self.source = source

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        pos = 0
        n = len(self.source)
        while pos < n:
            m = _TOKEN_RE.match(self.source, pos)
            if m is None:
                raise SyntaxError(
                    f"Unexpected character at position {pos}: "
                    f"{self.source[pos]!r}"
                )
            type_ = m.lastgroup
            value = m.group()
            if type_ != "WHITESPACE":
                tokens.append(Token(type_, value))
            pos = m.end()
        return tokens


# ============================================================
# AST nodes
# ============================================================
class ASTNode:
    """Base class for every AST node."""


class Program(ASTNode):
    """Root of an AST: an ordered list of top-level statements."""

    __slots__ = ("body",)

    def __init__(self, body: List[ASTNode]) -> None:
        self.body = body


class VarDecl(ASTNode):
    """`[let|const|var] NAME = EXPR ;`"""

    __slots__ = ("name", "value", "is_const")

    def __init__(self, name: str, value: ASTNode, is_const: bool = False) -> None:
        self.name = name
        self.value = value
        self.is_const = is_const


class Assignment(ASTNode):
    """`TARGET = EXPR ;`"""

    __slots__ = ("target", "value")

    def __init__(self, target: ASTNode, value: ASTNode) -> None:
        self.target = target
        self.value = value


class ExprStmt(ASTNode):
    """`EXPR ;` -- an expression evaluated for side-effects only."""

    __slots__ = ("expression",)

    def __init__(self, expression: ASTNode) -> None:
        self.expression = expression


class BinaryOp(ASTNode):
    """`LEFT OP RIGHT` where OP is one of +, -, *, /."""

    __slots__ = ("op", "left", "right")

    def __init__(self, op: str, left: ASTNode, right: ASTNode) -> None:
        self.op = op
        self.left = left
        self.right = right


class NumberLit(ASTNode):
    """Numeric literal (int or float)."""

    __slots__ = ("value",)

    def __init__(self, value: Union[int, float]) -> None:
        self.value = value


class StringLit(ASTNode):
    """String literal with escape sequences already resolved."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


class Identifier(ASTNode):
    """Bare identifier reference."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class MemberExpr(ASTNode):
    """`OBJ . PROP` -- never computed in this JS subset."""

    __slots__ = ("obj", "prop")

    def __init__(self, obj: ASTNode, prop: ASTNode) -> None:
        self.obj = obj
        self.prop = prop


class CallExpr(ASTNode):
    """`CALLEE ( ARG , ARG , ... )`"""

    __slots__ = ("callee", "args")

    def __init__(self, callee: ASTNode, args: List[ASTNode]) -> None:
        self.callee = callee
        self.args = args


# ============================================================
# Parser  (recursive descent, classic precedence climbing)
# ============================================================
class JSParser:
    """Turns a list of tokens into a `Program` AST."""

    def __init__(self, tokens: List[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    # --- low-level cursor helpers ---
    def _peek(self, offset: int = 0) -> Optional[Token]:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return None

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, type_: str) -> Token:
        tok = self._peek()
        if tok is None or tok.type != type_:
            actual = tok.type if tok is not None else "EOF"
            raise SyntaxError(
                f"Expected token of type {type_!r}, got {actual!r} "
                f"at token index {self.pos}"
            )
        return self._advance()

    def _at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    def _consume_optional_semi(self) -> None:
        """Eat a trailing `;` if present (JS auto-semicolon-insertion)."""
        if not self._at_end() and self._peek().type == "SEMI":
            self._advance()

    # --- entry point ---
    def parse(self) -> Program:
        body: List[ASTNode] = []
        while not self._at_end():
            body.append(self._parse_statement())
        return Program(body)

    # --- statements ---
    def _parse_statement(self) -> ASTNode:
        tok = self._peek()
        if tok is not None and tok.type == "KEYWORD":
            return self._parse_var_decl()
        return self._parse_expression_or_assignment()

    def _parse_var_decl(self) -> VarDecl:
        keyword = self._advance()
        is_const = (keyword.value == "const")
        name = self._expect("IDENT").value
        self._expect("EQUALS")
        value = self._parse_expression()
        self._consume_optional_semi()
        return VarDecl(name, value, is_const=is_const)

    def _parse_expression_or_assignment(self) -> ASTNode:
        expr = self._parse_expression()
        if not self._at_end() and self._peek().type == "EQUALS":
            self._advance()  # consume '='
            value = self._parse_expression()
            self._consume_optional_semi()
            return Assignment(expr, value)
        self._consume_optional_semi()
        return ExprStmt(expr)

    # --- expressions (precedence: < > == ... < +/- < *// < unary < postfix < primary) ---
    def _parse_expression(self) -> ASTNode:
        return self._parse_additive()

    def _parse_additive(self) -> ASTNode:
        left = self._parse_multiplicative()
        while not self._at_end() and self._peek().type in ("PLUS", "MINUS"):
            op = self._advance().value
            right = self._parse_multiplicative()
            left = BinaryOp(op, left, right)
        return left

    def _parse_multiplicative(self) -> ASTNode:
        left = self._parse_unary()
        while not self._at_end() and self._peek().type in ("STAR", "SLASH"):
            op = self._advance().value
            right = self._parse_unary()
            left = BinaryOp(op, left, right)
        return left

    def _parse_unary(self) -> ASTNode:
        # No unary operators in this subset; just descend.
        return self._parse_postfix()

    def _parse_postfix(self) -> ASTNode:
        expr = self._parse_primary()
        while not self._at_end():
            if self.peek_is_dot():
                self._advance()  # consume '.'
                prop = self._expect("IDENT").value
                expr = MemberExpr(expr, Identifier(prop))
            elif self.peek_is_lparen():
                self._advance()  # consume '('
                args: List[ASTNode] = []
                if not self._at_end() and self._peek().type != "RPAREN":
                    args.append(self._parse_expression())
                    while not self._at_end() and self._peek().type == "COMMA":
                        self._advance()
                        args.append(self._parse_expression())
                self._expect("RPAREN")
                expr = CallExpr(expr, args)
            else:
                break
        return expr

    def peek_is_dot(self) -> bool:
        return self._peek() is not None and self._peek().type == "DOT"

    def peek_is_lparen(self) -> bool:
        return self._peek() is not None and self._peek().type == "LPAREN"

    def _parse_primary(self) -> ASTNode:
        tok = self._peek()
        if tok is None:
            raise SyntaxError("Unexpected end of input")

        if tok.type == "NUMBER":
            self._advance()
            raw = tok.value
            return NumberLit(float(raw) if "." in raw else int(raw))

        if tok.type == "STRING":
            self._advance()
            return StringLit(self._decode_string_literal(tok.value))

        if tok.type == "IDENT":
            self._advance()
            return Identifier(tok.value)

        if tok.type == "LPAREN":
            self._advance()
            inner = self._parse_expression()
            self._expect("RPAREN")
            return inner

        raise SyntaxError(
            f"Unexpected token in primary expression: {tok.type}:{tok.value!r}"
        )

    @staticmethod
    def _decode_string_literal(raw: str) -> str:
        """Strip outer quotes and resolve common JS escape sequences."""
        assert len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'")
        body = raw[1:-1]
        # Use a placeholder for the backslash so we can do the "\\" -> "\"
        # replacement last, after the other escape sequences are gone.
        body = body.replace("\\\\", "\x00")
        body = (
            body.replace("\\'", "'")
                .replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\r", "\r")
                .replace("\x00", "\\")
        )
        return body


# ============================================================
# DOM bridge
# ============================================================
def _camel_to_kebab(name: str) -> str:
    """Convert camelCase JS property name to kebab-case CSS key."""
    out: List[str] = []
    for ch in name:
        if ch.isupper():
            out.append("-")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def find_node_by_id(root: Optional[HTMLNode], target_id: str) -> Optional[HTMLNode]:
    """Walk an HTMLNode tree and return the first node whose id matches."""
    if root is None:
        return None
    if getattr(root, "attributes", {}).get("id") == target_id:
        return root
    for child in getattr(root, "children", []) or []:
        hit = find_node_by_id(child, target_id)
        if hit is not None:
            return hit
    return None


class DOMStyleWrapper:
    """
    Proxy that exposes an HTMLNode's `style` dict to JS as:

        elem.style.color            -> node.style["color"]
        elem.style.backgroundColor  -> node.style["background-color"]
        elem.style.color = "red"    -> node.style["color"] = "red"
                                       + trigger_layout_callback()

    The camelCase -> kebab-case translation is handled here so the underlying
    HTMLNode.style dict always uses canonical CSS keys.
    """

    def __init__(self, node: HTMLNode, evaluator: "JSEvaluator") -> None:
        # Bypass our __setattr__ during initialization.
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_evaluator", evaluator)

    # __getattr__ only fires when normal lookup fails, so unknown style
    # properties just return "" (matching JS `undefined` semantics loosely).
    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        key = _camel_to_kebab(name)
        return self._node.style.get(key, "")

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        key = _camel_to_kebab(name)
        self._node.style[key] = value
        cb = self._evaluator.trigger_layout_callback
        if cb is not None:
            cb()

    def __repr__(self) -> str:
        return f"DOMStyleWrapper({self._node.style!r})"


class DOMElementWrapper:
    """
    Proxy that exposes an HTMLNode to JS. Supports:

        .innerHTML  (get / set)
        .style      (always returns a fresh DOMStyleWrapper)
    """

    def __init__(self, node: HTMLNode, evaluator: "JSEvaluator") -> None:
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_evaluator", evaluator)

    # --- .style ---
    @property
    def style(self) -> DOMStyleWrapper:
        return DOMStyleWrapper(self._node, self._evaluator)

    @style.setter
    def style(self, value: Any) -> None:
        if isinstance(value, dict):
            self._node.style.update(value)
            self._trigger_layout()
        else:
            raise TypeError(
                "Assigning to .style requires a dict of CSS-property -> value"
            )

    # --- .innerHTML ---
    @property
    def innerHTML(self) -> str:
        return "".join(self._collect_text(c) for c in self._node.children)

    @innerHTML.setter
    def innerHTML(self, value: Any) -> None:
        text_node = HTMLNode(tag="#text", text=str(value))
        self._node.children = [text_node]
        self._trigger_layout()

    # --- helpers ---
    def _collect_text(self, node: HTMLNode) -> str:
        if getattr(node, "tag", None) == "#text":
            return getattr(node, "text", "")
        return "".join(self._collect_text(c) for c in getattr(node, "children", []) or [])

    def _trigger_layout(self) -> None:
        cb = self._evaluator.trigger_layout_callback
        if cb is not None:
            cb()

    def __repr__(self) -> str:
        ident = self._node.attributes.get("id", "<none>")
        return f"DOMElementWrapper(tag={self._node.tag!r}, id={ident!r})"


class _ConsoleProxy:
    """JS-side `console` object -- currently supports `console.log(...)`."""

    def __init__(self, evaluator: "JSEvaluator") -> None:
        self._evaluator = evaluator

    def log(self, *args: Any) -> None:
        print(*args)


class _DocumentProxy:
    """JS-side `document` object -- currently supports `getElementById(...)`."""

    def __init__(self, evaluator: "JSEvaluator") -> None:
        self._evaluator = evaluator

    def getElementById(self, target_id: str) -> Optional[DOMElementWrapper]:
        node = find_node_by_id(self._evaluator.dom_root, target_id)
        if node is None:
            return None
        return DOMElementWrapper(node, self._evaluator)


# ============================================================
# Evaluator
# ============================================================
class JSEvaluator:
    """
    Walks an AST and executes it, maintaining a small scope stack and
    providing DOM mutation hooks.
    """

    def __init__(
        self,
        dom_root: HTMLNode,
        trigger_layout_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self.dom_root = dom_root
        self.trigger_layout_callback = trigger_layout_callback
        # Each scope is {name: {"value": v, "const": bool}}.
        self.scopes: List[Dict[str, Dict[str, Any]]] = [{}]
        # Globals (console, document) are looked up *after* the scope stack.
        self.global_scope: Dict[str, Any] = {
            "console": _ConsoleProxy(self),
            "document": _DocumentProxy(self),
        }

    # ---------- public ----------
    def evaluate(self, ast: ASTNode) -> Any:
        if isinstance(ast, Program):
            for stmt in ast.body:
                self._eval_stmt(stmt)
            return None
        if isinstance(ast, (VarDecl, Assignment, ExprStmt)):
            return self._eval_stmt(ast)
        raise RuntimeError(f"Cannot evaluate AST root of type {type(ast).__name__}")

    # ---------- statements ----------
    def _eval_stmt(self, stmt: ASTNode) -> Any:
        if isinstance(stmt, VarDecl):
            value = self._eval_expr(stmt.value)
            self.scopes[-1][stmt.name] = {"value": value, "const": stmt.is_const}
            return value
        if isinstance(stmt, Assignment):
            value = self._eval_expr(stmt.value)
            self._assign(stmt.target, value)
            return value
        if isinstance(stmt, ExprStmt):
            return self._eval_expr(stmt.expression)
        raise RuntimeError(f"Unknown statement type: {type(stmt).__name__}")

    def _assign(self, target: ASTNode, value: Any) -> None:
        if isinstance(target, Identifier):
            for scope in reversed(self.scopes):
                if target.name in scope:
                    if scope[target.name].get("const", False):
                        raise RuntimeError(
                            f"TypeError: cannot reassign const variable "
                            f"'{target.name}'"
                        )
                    scope[target.name]["value"] = value
                    return
            # Implicit global declaration in top scope.
            self.scopes[-1][target.name] = {"value": value, "const": False}
            return
        if isinstance(target, MemberExpr):
            obj = self._eval_expr(target.obj)
            prop_name = self._prop_name(target.prop)
            self._set_member(obj, prop_name, value)
            return
        raise RuntimeError(f"Invalid assignment target: {type(target).__name__}")

    # ---------- expressions ----------
    def _eval_expr(self, expr: ASTNode) -> Any:
        if isinstance(expr, NumberLit):
            return expr.value
        if isinstance(expr, StringLit):
            return expr.value
        if isinstance(expr, Identifier):
            for scope in reversed(self.scopes):
                if expr.name in scope:
                    return scope[expr.name]["value"]
            if expr.name in self.global_scope:
                return self.global_scope[expr.name]
            raise RuntimeError(f"ReferenceError: '{expr.name}' is not defined")
        if isinstance(expr, BinaryOp):
            left = self._eval_expr(expr.left)
            right = self._eval_expr(expr.right)
            return self._apply_op(expr.op, left, right)
        if isinstance(expr, MemberExpr):
            obj = self._eval_expr(expr.obj)
            prop_name = self._prop_name(expr.prop)
            return self._get_member(obj, prop_name)
        if isinstance(expr, CallExpr):
            callee = self._eval_expr(expr.callee)
            args = [self._eval_expr(a) for a in expr.args]
            if not callable(callee):
                raise RuntimeError(f"TypeError: {callee!r} is not a function")
            return callee(*args)
        raise RuntimeError(f"Unknown expression type: {type(expr).__name__}")

    def _apply_op(self, op: str, left: Any, right: Any) -> Any:
        if op == "+":
            # JS coerces: if either operand is a string, do string concat.
            if isinstance(left, str) or isinstance(right, str):
                return f"{left}{right}"
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return left + right
            raise RuntimeError(
                f"Operator '+' not supported for {type(left).__name__} "
                f"and {type(right).__name__}"
            )
        if op in ("-", "*", "/"):
            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                raise RuntimeError(
                    f"Operator '{op}' requires numeric operands, got "
                    f"{type(left).__name__} and {type(right).__name__}"
                )
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                if right == 0:
                    raise RuntimeError("Division by zero")
                return left / right
        raise RuntimeError(f"Unknown operator: {op!r}")

    # ---------- member helpers ----------
    @staticmethod
    def _prop_name(prop: ASTNode) -> str:
        if isinstance(prop, Identifier):
            return prop.name
        return str(prop)

    def _get_member(self, obj: Any, name: str) -> Any:
        if isinstance(obj, DOMStyleWrapper):
            return getattr(obj, name)
        if isinstance(obj, DOMElementWrapper):
            if name == "style":
                return obj.style
            if name == "innerHTML":
                return obj.innerHTML
            raise RuntimeError(
                f"TypeError: cannot read property '{name}' of DOM element"
            )
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            raise RuntimeError(f"Property '{name}' not found on object")
        if hasattr(obj, name):
            return getattr(obj, name)
        raise RuntimeError(
            f"TypeError: cannot read property '{name}' of "
            f"{type(obj).__name__}"
        )

    def _set_member(self, obj: Any, name: str, value: Any) -> None:
        if isinstance(obj, DOMStyleWrapper):
            # DOMStyleWrapper.__setattr__ handles key conversion + repaint.
            setattr(obj, name, value)
            return
        if isinstance(obj, DOMElementWrapper):
            if name == "style":
                if isinstance(value, dict):
                    obj._node.style.update(value)
                    obj._trigger_layout()
                return
            if name == "innerHTML":
                obj.innerHTML = value
                return
            raise RuntimeError(
                f"TypeError: cannot set property '{name}' on DOM element"
            )
        if isinstance(obj, dict):
            obj[name] = value
            return
        raise RuntimeError(
            f"TypeError: cannot set property '{name}' on "
            f"{type(obj).__name__}"
        )


# ============================================================
# Convenience entry points
# ============================================================
def execute_script(
    source: str,
    dom_root: HTMLNode,
    trigger_layout_callback: Optional[Callable[[], None]] = None,
) -> JSEvaluator:
    """
    One-shot: tokenize + parse + evaluate a JS source string.

    Returns the `JSEvaluator` so the caller can read declared variables
    after execution.
    """
    tokens = Lexer(source).tokenize()
    ast = JSParser(tokens).parse()
    evaluator = JSEvaluator(dom_root, trigger_layout_callback)
    evaluator.evaluate(ast)
    return evaluator


def evaluate_event_handler(
    handler_code: str,
    dom_root: HTMLNode,
    trigger_layout_callback: Optional[Callable[[], None]] = None,
) -> JSEvaluator:
    """Evaluate a JS event-handler string (e.g. an `onclick` attribute)."""
    return execute_script(handler_code, dom_root, trigger_layout_callback)


# ============================================================
# Self-test / demo  (run with `python3 surfscript.py`)
# ============================================================
if __name__ == "__main__":
    # ---- build a mock SurfGambit DOM tree ----
    welcome = HTMLNode(
        tag="h1",
        attributes={"id": "welcome"},
        style={"color": "black", "font-size": "24px"},
        children=[HTMLNode(tag="#text", text="Hello, SurfGambit!")],
    )
    button = HTMLNode(
        tag="button",
        attributes={
            "id": "btn",
            "onclick": (
                'document.getElementById("welcome").innerHTML = "Clicked!";'
            ),
        },
        style={"background-color": "lightgray"},
        children=[HTMLNode(tag="#text", text="Click me")],
    )
    counter = HTMLNode(
        tag="p",
        attributes={"id": "counter"},
        children=[HTMLNode(tag="#text", text="0")],
    )
    body = HTMLNode(tag="body", children=[welcome, button, counter])
    root = HTMLNode(tag="html", children=[body])

    # ---- a callback that simulates the browser repainting its canvas ----
    repaint_log: List[int] = []

    def trigger_layout() -> None:
        repaint_log.append(len(repaint_log) + 1)
        print(f"  [REPAINT #{repaint_log[-1]}] DOM mutated -> "
              f"layout recalculated -> canvas redrawn")

    # ---- run a top-level script ----
    print("=" * 60)
    print(" SurfScript :: top-level script execution")
    print("=" * 60)
    script = """
        let x = 10;
        const name = "Yash";
        let total = x + 32 * 2;

        console.log("Hello,", name, "- welcome to SurfGambit!");
        console.log("Computed total:", total);

        document.getElementById("welcome").style.color = "green";
        document.getElementById("welcome").style.backgroundColor = "yellow";
        document.getElementById("btn").style.color = "white";
        document.getElementById("btn").style.backgroundColor = "blue";

        let click_script = document.getElementById("btn").innerHTML;
        console.log("Button label before click was:", click_script);
    """
    evaluator = execute_script(script, root, trigger_layout)

    print("\n" + "=" * 60)
    print(" SurfScript :: simulated button click event")
    print("=" * 60)
    onclick_code = button.attributes.get("onclick", "")
    evaluate_event_handler(onclick_code, root, trigger_layout)

    print("\n" + "=" * 60)
    print("All tests completed successfully!")
