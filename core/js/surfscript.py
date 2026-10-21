"""
core/js/surfscript.py — a deliberately small JavaScript subset.

JavaScript is a declared non-goal for this browser (see README). What this
module provides is the honest middle ground: a real lexer, parser and
evaluator for a documented subset of the language, wired to the *actual* DOM
used by the engine, so pages that use it degrade predictably instead of
silently doing nothing.

Supported
---------
  let/const/var declarations, assignment, arithmetic, string concatenation,
  member access, function calls, parentheses.

DOM surface
-----------
  document.title                  (get/set)
  document.getElementById(id)     -> element or null
  document.querySelector(sel)     -> element or null (single simple selector)
  document.querySelectorAll(sel)  -> array-like list
  element.textContent             (get/set)
  element.innerHTML               (get/set - parsed as HTML when set)
  element.style.<camelCase>       (get/set, translated to CSS properties)
  element.getAttribute(n) / setAttribute(n, v)
  element.classList.contains/add/remove/toggle
  console.log(...)                (routed to the browser console panel)

Anything outside the subset raises a soft error that the tab reports in its
status bar and the console panel; it never takes the page down.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.dom.tree import HTMLNode, TEXT_TAG

__all__ = [
    "Token", "Lexer", "JSParser", "JSEvaluator", "DOMElementWrapper",
    "execute_script", "evaluate_event_handler", "run_document", "run_handler",
    "find_node_by_id", "ScriptError",
]


class ScriptError(RuntimeError):
    """A scripting error that should be shown to the user, not raised."""


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




# ============================================================
# DOM bridge
# ============================================================
_CAMEL_RE = re.compile(r"[A-Z]")


def _camel_to_kebab(name: str) -> str:
    return _CAMEL_RE.sub(lambda m: "-" + m.group(0).lower(), name)


def find_node_by_id(root: Optional[HTMLNode], target_id: str) -> Optional[HTMLNode]:
    if root is None:
        return None
    for node in root.walk():
        if node.attributes.get("id") == target_id:
            return node
    return None


def _matches_simple(node: HTMLNode, selector: str) -> bool:
    """Support tag, .class, #id and tag.class combos (one compound selector)."""
    selector = selector.strip()
    if not selector or node.tag == TEXT_TAG:
        return False
    tag = ""
    ident = ""
    classes: List[str] = []
    buf = ""
    mode = "tag"
    for ch in selector:
        if ch in ".#":
            if mode == "tag":
                tag = buf
            elif mode == "id":
                ident = buf
            else:
                classes.append(buf)
            buf = ""
            mode = "id" if ch == "#" else "class"
        else:
            buf += ch
    if mode == "tag":
        tag = buf
    elif mode == "id":
        ident = buf
    else:
        classes.append(buf)

    if tag and tag != "*" and node.tag != tag.lower():
        return False
    if ident and node.attributes.get("id", "") != ident:
        return False
    node_classes = (node.attributes.get("class") or "").split()
    return all(c in node_classes for c in classes if c)


class DOMStyleWrapper:
    def __init__(self, node: HTMLNode, evaluator: "JSEvaluator") -> None:
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_evaluator", evaluator)

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        return str(self._node.style.get(_camel_to_kebab(name), ""))

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self._node.style[_camel_to_kebab(name)] = str(value)
        if self._evaluator.on_mutation is not None:
            self._evaluator.on_mutation()

    def __repr__(self) -> str:
        return f"DOMStyleWrapper({self._node.style!r})"


class _ClassList:
    def __init__(self, node: HTMLNode, evaluator: "JSEvaluator") -> None:
        self._node = node
        self._evaluator = evaluator

    def _classes(self) -> List[str]:
        return (self._node.attributes.get("class") or "").split()

    def _write(self, values: List[str]) -> None:
        self._node.attributes["class"] = " ".join(values)
        if self._evaluator.on_mutation is not None:
            self._evaluator.on_mutation()

    def contains(self, name: str) -> bool:
        return name in self._classes()

    def add(self, *names: str) -> None:
        current = self._classes()
        for name in names:
            if name and name not in current:
                current.append(name)
        self._write(current)

    def remove(self, *names: str) -> None:
        current = [c for c in self._classes() if c not in names]
        self._write(current)

    def toggle(self, name: str) -> bool:
        if self.contains(name):
            self.remove(name)
            return False
        self.add(name)
        return True


class DOMElementWrapper:
    def __init__(self, node: HTMLNode, evaluator: "JSEvaluator") -> None:
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_evaluator", evaluator)

    @property
    def style(self) -> DOMStyleWrapper:
        return DOMStyleWrapper(self._node, self._evaluator)

    @property
    def classList(self) -> _ClassList:
        return _ClassList(self._node, self._evaluator)

    @property
    def tagName(self) -> str:
        return self._node.tag.upper()

    @property
    def textContent(self) -> str:
        return self._node.text_content()

    @textContent.setter
    def textContent(self, value: Any) -> None:
        self._node.clear()
        if value not in (None, ""):
            self._node.append(HTMLNode(TEXT_TAG, text=str(value)))
        self._mutated()

    @property
    def innerHTML(self) -> str:
        parts: List[str] = []
        for child in self._node.children:
            if child.tag == TEXT_TAG:
                parts.append(child.text)
            else:
                attrs = "".join(f' {k}="{v}"' for k, v in child.attributes.items())
                parts.append(f"<{child.tag}{attrs}>{child.text_content()}</{child.tag}>")
        return "".join(parts)

    @innerHTML.setter
    def innerHTML(self, value: Any) -> None:
        from core.dom.parser import parse_fragment

        self._node.clear()
        fragment = parse_fragment(str(value))
        for child in list(fragment.children):
            self._node.append(child)
        self._mutated()

    def getAttribute(self, name: str) -> Optional[str]:
        return self._node.attributes.get(name)

    def setAttribute(self, name: str, value: Any) -> None:
        self._node.attributes[str(name)] = str(value)
        self._mutated()

    def appendChild(self, other: Any) -> None:
        if isinstance(other, DOMElementWrapper):
            other._node.remove()
            self._node.append(other._node)
            self._mutated()

    def _mutated(self) -> None:
        if self._evaluator.on_mutation is not None:
            self._evaluator.on_mutation()

    def __repr__(self) -> str:
        return f"DOMElementWrapper(<{self._node.tag}>)"


class _ConsoleProxy:
    def __init__(self, evaluator: "JSEvaluator") -> None:
        self._evaluator = evaluator

    def log(self, *args: Any) -> None:
        message = " ".join(str(a) for a in args)
        self._evaluator.on_console(message)


class _DocumentProxy:
    def __init__(self, evaluator: "JSEvaluator") -> None:
        self._evaluator = evaluator

    @property
    def title(self) -> str:
        node = self._evaluator.dom_root.find("title")
        return node.text_content().strip() if node else ""

    @title.setter
    def title(self, value: Any) -> None:
        node = self._evaluator.dom_root.find("title")
        if node is None:
            head = self._evaluator.dom_root.find("head")
            target = head if head is not None else self._evaluator.dom_root
            node = target.append(HTMLNode("title"))
        node.clear()
        node.append(HTMLNode(TEXT_TAG, text=str(value)))
        self._evaluator.title_changed = str(value)

    def getElementById(self, target_id: str) -> Optional[DOMElementWrapper]:
        node = find_node_by_id(self._evaluator.dom_root, str(target_id))
        return DOMElementWrapper(node, self._evaluator) if node is not None else None

    def querySelector(self, selector: str) -> Optional[DOMElementWrapper]:
        for node in self._evaluator.dom_root.walk():
            if _matches_simple(node, str(selector)):
                return DOMElementWrapper(node, self._evaluator)
        return None

    def querySelectorAll(self, selector: str) -> List[DOMElementWrapper]:
        return [DOMElementWrapper(n, self._evaluator)
                for n in self._evaluator.dom_root.walk()
                if _matches_simple(n, str(selector))]


# ============================================================
# Evaluator
# ============================================================
class JSEvaluator:
    def __init__(self, dom_root: HTMLNode,
                 on_mutation: Optional[Callable[[], None]] = None,
                 on_console: Optional[Callable[[str], None]] = None) -> None:
        self.dom_root = dom_root
        self.on_mutation = on_mutation
        self.on_console = on_console or (lambda message: None)
        self.title_changed: Optional[str] = None
        self.scopes: List[Dict[str, Dict[str, Any]]] = [{}]
        self.global_scope: Dict[str, Any] = {
            "console": _ConsoleProxy(self),
            "document": _DocumentProxy(self),
            "true": True,
            "false": False,
            "null": None,
        }

    def evaluate(self, ast: ASTNode) -> Any:
        if isinstance(ast, Program):
            for stmt in ast.body:
                self._eval_stmt(stmt)
            return None
        return self._eval_stmt(ast)

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
        raise ScriptError(f"Unknown statement type: {type(stmt).__name__}")

    def _assign(self, target: ASTNode, value: Any) -> None:
        if isinstance(target, Identifier):
            for scope in reversed(self.scopes):
                if target.name in scope:
                    if scope[target.name].get("const", False):
                        raise ScriptError(
                            f"TypeError: cannot reassign const '{target.name}'")
                    scope[target.name]["value"] = value
                    return
            self.scopes[-1][target.name] = {"value": value, "const": False}
            return
        if isinstance(target, MemberExpr):
            obj = self._eval_expr(target.obj)
            name = self._prop_name(target.prop)
            if isinstance(obj, DOMStyleWrapper):
                setattr(obj, name, value)
                return
            if isinstance(obj, (DOMElementWrapper, _DocumentProxy)):
                try:
                    setattr(obj, name, value)
                except AttributeError as exc:
                    raise ScriptError(str(exc))
                return
            if isinstance(obj, dict):
                obj[name] = value
                return
            raise ScriptError(
                f"TypeError: cannot set property '{name}' on {type(obj).__name__}")
        raise ScriptError(f"Invalid assignment target: {type(target).__name__}")

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
            raise ScriptError(f"ReferenceError: '{expr.name}' is not defined")
        if isinstance(expr, BinaryOp):
            left = self._eval_expr(expr.left)
            right = self._eval_expr(expr.right)
            return self._apply_op(expr.op, left, right)
        if isinstance(expr, MemberExpr):
            obj = self._eval_expr(expr.obj)
            return self._get_member(obj, self._prop_name(expr.prop))
        if isinstance(expr, CallExpr):
            callee = self._eval_expr(expr.callee)
            args = [self._eval_expr(a) for a in expr.args]
            if not callable(callee):
                raise ScriptError(f"TypeError: {callee!r} is not a function")
            return callee(*args)
        raise ScriptError(f"Unknown expression type: {type(expr).__name__}")

    def _apply_op(self, op: str, left: Any, right: Any) -> Any:
        if op == "+":
            if isinstance(left, str) or isinstance(right, str):
                return f"{left}{right}"
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return left + right
            raise ScriptError("Operator '+' needs numbers or strings")
        if op in ("-", "*", "/"):
            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                raise ScriptError(f"Operator '{op}' needs numeric operands")
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if right == 0:
                raise ScriptError("Division by zero")
            return left / right
        raise ScriptError(f"Unknown operator: {op!r}")

    @staticmethod
    def _prop_name(prop: ASTNode) -> str:
        if isinstance(prop, Identifier):
            return prop.name
        return str(prop)

    def _get_member(self, obj: Any, name: str) -> Any:
        if isinstance(obj, (DOMStyleWrapper, DOMElementWrapper, _DocumentProxy,
                            _ConsoleProxy, _ClassList)):
            try:
                return getattr(obj, name)
            except AttributeError:
                raise ScriptError(
                    f"TypeError: cannot read property '{name}' of DOM object")
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            raise ScriptError(f"Property '{name}' not found on object")
        raise ScriptError(
            f"TypeError: cannot read property '{name}' of {type(obj).__name__}")


# ============================================================
# Entry points
# ============================================================
def execute_script(source: str, dom_root: HTMLNode,
                   on_mutation: Optional[Callable[[], None]] = None,
                   on_console: Optional[Callable[[str], None]] = None) -> JSEvaluator:
    tokens = Lexer(source).tokenize()
    ast = JSParser(tokens).parse()
    evaluator = JSEvaluator(dom_root, on_mutation, on_console)
    evaluator.evaluate(ast)
    return evaluator


def evaluate_event_handler(handler_code: str, dom_root: HTMLNode,
                           on_mutation: Optional[Callable[[], None]] = None,
                           on_console: Optional[Callable[[str], None]] = None) -> JSEvaluator:
    return execute_script(handler_code, dom_root, on_mutation, on_console)


def run_document(dom: HTMLNode, base_url: str = "",
                 on_mutation: Optional[Callable[[], None]] = None,
                 on_console: Optional[Callable[[str], None]] = None) -> List[str]:
    """Execute every inline <script> in a document. Returns error strings."""
    errors: List[str] = []
    sources = [n for n in dom.walk() if n.tag == "script"]
    for node in sources:
        if node.attributes.get("src"):
            # External scripts are not fetched: a silent network dependency in
            # the scripting engine is worse than an explicit skip.
            continue
        script_type = (node.attributes.get("type") or "text/javascript").lower()
        if "javascript" not in script_type and script_type not in ("", "module"):
            continue
        code = "".join(c.text for c in node.children)
        if not code.strip():
            continue
        try:
            execute_script(code, dom, on_mutation, on_console)
        except (ScriptError, SyntaxError, ValueError, TypeError, ZeroDivisionError) as exc:
            errors.append(f"Script error: {exc}")
        except RecursionError:
            errors.append("Script error: expression nested too deeply")
    return errors


def run_handler(node: HTMLNode, code: str, base_url: str = "",
                on_mutation: Optional[Callable[[], None]] = None,
                on_console: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Run an inline event handler (e.g. onclick). Returns an error, or None."""
    root = node
    while root.parent is not None:
        root = root.parent
    try:
        execute_script(code, root, on_mutation, on_console)
    except (ScriptError, SyntaxError, ValueError, TypeError, ZeroDivisionError) as exc:
        return str(exc)
    return None
