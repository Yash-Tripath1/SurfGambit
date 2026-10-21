"""
core/js/surfscript.py — SurfScript, a real (small) JavaScript engine.

This is the language core: lexer, parser, interpreter, values and built-ins.
The DOM bindings live in `core/js/dom_bridge.py`, so the language can also be
used against plain objects (the devtools console does exactly that).

What is supported (documented, tested subset)
---------------------------------------------
  * `let` / `const` / `var`, block scoping, function declarations and
    expressions, closures, `return`, recursion
  * `if` / `else`, `while`, `for (;;)`, `for (x of iterable)`, `break`,
    `continue`, blocks
  * operators: `+ - * / %`, `== != === !==`, `< <= > >=`, `&& || !`,
    ternary `?:`, compound assignment `+= -= *= /= %=`, `++` / `--`
  * arrays and object literals, indexing, member access, string and array
    methods, `Math`, `JSON`, `parseInt` / `parseFloat` / `Number` / `String`,
    `isNaN`, `typeof`
  * `//` and `/* */` comments, single and double quoted strings with escapes

Hard limits
-----------
  Every script runs inside a budget: 300 000 evaluation steps and 1.5 s of
  wall-clock time by default. Exceeding either raises `ScriptError`. Scripts
  never touch the network, the filesystem, or Python's `eval`.

The old legacy API (`execute_script`, `evaluate_event_handler`, `find_node_by_id`)
still works; see the compatibility surface in `surfscript.py` at the repo root.
"""

from __future__ import annotations

import json
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "ScriptError", "JSSyntaxError", "JSRuntimeError", "Undefined", "UNDEFINED",
    "Token", "Lexer", "JSParser", "Interpreter", "JSEvaluator",
    "JSFunction", "NativeFunction", "to_string", "to_number", "truthy",
    "execute_script", "evaluate_expression", "run_document", "run_handler",
    "evaluate_event_handler", "find_node_by_id",
]


class ScriptError(RuntimeError):
    """Base class for anything a script does wrong."""


class JSSyntaxError(ScriptError):
    def __init__(self, message: str, position: int = -1) -> None:
        where = f" (at {position})" if position >= 0 else ""
        super().__init__(f"SyntaxError: {message}{where}")


class JSRuntimeError(ScriptError):
    def __init__(self, message: str, kind: str = "TypeError") -> None:
        super().__init__(f"{kind}: {message}")


# ============================================================
# Values
# ============================================================

class _Undefined:
    __slots__ = ()

    def __repr__(self) -> str:
        return "undefined"

    def __bool__(self) -> bool:
        return False


UNDEFINED = _Undefined()
Undefined = _Undefined


def truthy(value: Any) -> bool:
    """JavaScript truthiness."""
    if value is UNDEFINED or value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value == value and value != 0      # NaN is falsy
    if isinstance(value, str):
        return value != ""
    return True


def to_number(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if value is UNDEFINED:
        return float("nan")
    if value is None:
        return 0.0
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return float("nan")
    return float("nan")


def _num_out(value: float) -> Any:
    """Render an integral float as int so `1.0` prints as `1`."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return int(value)
    return value


def to_string(value: Any) -> str:
    if value is UNDEFINED:
        return "undefined"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
        return str(_num_out(value))
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, list):
        return ",".join("" if item is None or item is UNDEFINED else to_string(item)
                        for item in value)
    if isinstance(value, dict):
        return "[object Object]"
    if isinstance(value, (JSFunction, NativeFunction)):
        return f"function {value.name or ''}() {{ [native or script] }}"
    if isinstance(value, (RegexValue, DateValue)):
        return value.js_to_string()
    if hasattr(value, "js_to_string"):
        return str(value.js_to_string())
    return str(value)


def _to_primitive(value: Any) -> Any:
    if isinstance(value, list):
        return to_string(value)
    if isinstance(value, dict) and not hasattr(value, "js_get"):
        return to_string(value)
    return value


def loose_equals(left: Any, right: Any) -> bool:
    if left is right:
        return True
    if (left is None or left is UNDEFINED) and (right is None or right is UNDEFINED):
        return True
    if isinstance(left, bool):
        return loose_equals(_to_primitive(to_number(left)), right)
    if isinstance(right, bool):
        return loose_equals(left, _to_primitive(to_number(right)))
    if isinstance(left, (int, float)) and isinstance(right, str):
        return to_number(right) == float(left)
    if isinstance(right, (int, float)) and isinstance(left, str):
        return to_number(left) == float(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def strict_equals(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
            and not isinstance(left, bool) and not isinstance(right, bool):
        return float(left) == float(right)
    if type(left) is not type(right):
        return False
    return left == right


# ============================================================
# Lexer
# ============================================================

KEYWORDS = {
    "let", "const", "var", "if", "else", "while", "for", "of", "in",
    "function", "return", "true", "false", "null", "undefined",
    "break", "continue", "typeof", "new", "do",
}

# Longest first: the lexer tries these in order.
_OPERATORS = [
    "===", "!==", "==", "!=", "<=", ">=", "&&", "||", "++", "--",
    "+=", "-=", "*=", "/=", "%=", "<<", ">>", "=>",
    "+", "-", "*", "/", "%", "<", ">", "=", "!", "?", ":", ";", ",",
    ".", "(", ")", "[", "]", "{", "}", "&", "|", "^", "~",
]

_PUNCT_NAMES = {"(", ")", "[", "]", "{", "}", ";", ",", ".", ":", "?"}


class Token:
    __slots__ = ("type", "value", "pos")

    def __init__(self, type_: str, value: str, pos: int = 0) -> None:
        self.type = type_          # NUM STR ID KW OP EOF
        self.value = value
        self.pos = pos

    def __repr__(self) -> str:
        return f"Token({self.type!r}, {self.value!r})"


class Lexer:
    """Hand-written scanner: comments, escapes, numbers, identifiers."""

    def __init__(self, source: str) -> None:
        self.source = source or ""
        self.pos = 0
        self.length = len(self.source)
        self._last: Optional[Token] = None

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        src = self.source
        while self.pos < self.length:
            ch = src[self.pos]

            if ch in " \t\r\n\f\v":
                self.pos += 1
                continue

            # comments
            if ch == "/" and self.pos + 1 < self.length:
                nxt = src[self.pos + 1]
                if nxt == "/":
                    end = src.find("\n", self.pos)
                    self.pos = self.length if end < 0 else end + 1
                    continue
                if nxt == "*":
                    end = src.find("*/", self.pos + 2)
                    if end < 0:
                        raise JSSyntaxError("unterminated comment", self.pos)
                    self.pos = end + 2
                    continue

            if ch in "'\"":
                token = self._string(ch)
                tokens.append(token)
                self._last = token
                continue

            if ch == "/" and self._regex_allowed():
                token = self._regex()
                tokens.append(token)
                self._last = token
                continue

            if ch.isdigit() or (ch == "." and self.pos + 1 < self.length
                                and src[self.pos + 1].isdigit()):
                token = self._number()
                tokens.append(token)
                self._last = token
                continue

            if ch.isalpha() or ch in "_$":
                start = self.pos
                while self.pos < self.length and (src[self.pos].isalnum()
                                                  or src[self.pos] in "_$"):
                    self.pos += 1
                word = src[start:self.pos]
                token = Token("KW" if word in KEYWORDS else "ID", word, start)
                tokens.append(token)
                self._last = token
                continue

            for op in _OPERATORS:
                if src.startswith(op, self.pos):
                    token = Token("OP", op, self.pos)
                    tokens.append(token)
                    self._last = token
                    self.pos += len(op)
                    break
            else:
                raise JSSyntaxError(f"unexpected character {ch!r}", self.pos)

        tokens.append(Token("EOF", "", self.length))
        return tokens

    def _regex_allowed(self) -> bool:
        """A `/` starts a regex only where an expression may begin."""
        last = self._last
        if last is None:
            return True
        if last.type == "OP":
            return last.value not in (")", "]", "}")
        if last.type == "KW":
            return last.value in ("return", "typeof", "case", "in", "of", "new")
        return False

    def _regex(self) -> Token:
        start = self.pos
        self.pos += 1
        pattern: List[str] = []
        in_class = False
        while self.pos < self.length:
            ch = self.source[self.pos]
            if ch == "\\" and self.pos + 1 < self.length:
                pattern.append(ch)
                pattern.append(self.source[self.pos + 1])
                self.pos += 2
                continue
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                self.pos += 1
                break
            elif ch == "\n":
                raise JSSyntaxError("unterminated regular expression", start)
            pattern.append(ch)
            self.pos += 1
        flags = ""
        while self.pos < self.length and self.source[self.pos].isalpha():
            flags += self.source[self.pos]
            self.pos += 1
        return Token("REGEX", "".join(pattern) + "\x00" + flags, start)

    def _string(self, quote: str) -> Token:
        start = self.pos
        self.pos += 1
        out: List[str] = []
        src = self.source
        while self.pos < self.length:
            ch = src[self.pos]
            if ch == "\\":
                self.pos += 1
                if self.pos >= self.length:
                    break
                esc = src[self.pos]
                mapping = {"n": "\n", "t": "\t", "r": "\r", "b": "\b",
                           "f": "\f", "0": "\0", "\\": "\\", "'": "'",
                           '"': '"', "/": "/", "`": "`"}
                if esc == "u":
                    hex_digits = src[self.pos + 1:self.pos + 5]
                    try:
                        out.append(chr(int(hex_digits, 16)))
                        self.pos += 5
                        continue
                    except ValueError:
                        out.append("u")
                elif esc == "x":
                    hex_digits = src[self.pos + 1:self.pos + 3]
                    try:
                        out.append(chr(int(hex_digits, 16)))
                        self.pos += 3
                        continue
                    except ValueError:
                        out.append("x")
                else:
                    out.append(mapping.get(esc, esc))
                self.pos += 1
                continue
            if ch == quote:
                self.pos += 1
                return Token("STR", "".join(out), start)
            if ch == "\n":
                raise JSSyntaxError("unterminated string", start)
            out.append(ch)
            self.pos += 1
        raise JSSyntaxError("unterminated string", start)

    def _number(self) -> Token:
        start = self.pos
        src = self.source
        seen_dot = False
        while self.pos < self.length:
            ch = src[self.pos]
            if ch.isdigit():
                self.pos += 1
            elif ch == "." and not seen_dot:
                seen_dot = True
                self.pos += 1
            elif ch in "eE" and self.pos + 1 < self.length and \
                    (src[self.pos + 1].isdigit() or src[self.pos + 1] in "+-"):
                self.pos += 2 if src[self.pos + 1] in "+-" else 1
            else:
                break
        raw = src[start:self.pos]
        try:
            float(raw)
        except ValueError:
            raise JSSyntaxError(f"bad number {raw!r}", start)
        return Token("NUM", raw, start)


# ============================================================
# AST
# ============================================================

class Node:
    __slots__ = ()


class Program(Node):
    __slots__ = ("body",)

    def __init__(self, body: List[Node]) -> None:
        self.body = body


class Block(Node):
    __slots__ = ("body",)

    def __init__(self, body: List[Node]) -> None:
        self.body = body


class VarDecl(Node):
    __slots__ = ("kind", "name", "init")

    def __init__(self, kind: str, name: str, init: Optional[Node]) -> None:
        self.kind = kind
        self.name = name
        self.init = init


class If(Node):
    __slots__ = ("cond", "then", "otherwise")

    def __init__(self, cond: Node, then: Node, otherwise: Optional[Node]) -> None:
        self.cond = cond
        self.then = then
        self.otherwise = otherwise


class While(Node):
    __slots__ = ("cond", "body")

    def __init__(self, cond: Node, body: Node) -> None:
        self.cond = cond
        self.body = body


class For(Node):
    __slots__ = ("init", "cond", "update", "body")

    def __init__(self, init: Optional[Node], cond: Optional[Node],
                 update: Optional[Node], body: Node) -> None:
        self.init = init
        self.cond = cond
        self.update = update
        self.body = body


class ForOf(Node):
    __slots__ = ("name", "iterable", "body")

    def __init__(self, name: str, iterable: Node, body: Node) -> None:
        self.name = name
        self.iterable = iterable
        self.body = body


class FuncDecl(Node):
    __slots__ = ("name", "params", "body")

    def __init__(self, name: str, params: List[str], body: List[Node]) -> None:
        self.name = name
        self.params = params
        self.body = body


class Return(Node):
    __slots__ = ("value",)

    def __init__(self, value: Optional[Node]) -> None:
        self.value = value


class Break(Node):
    __slots__ = ()


class Continue(Node):
    __slots__ = ()


class ExprStmt(Node):
    __slots__ = ("expr",)

    def __init__(self, expr: Node) -> None:
        self.expr = expr


class Lit(Node):
    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


class RegexLit(Node):
    __slots__ = ("pattern", "flags")

    def __init__(self, pattern: str, flags: str) -> None:
        self.pattern = pattern
        self.flags = flags


class Ident(Node):
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class ArrayLit(Node):
    __slots__ = ("items",)

    def __init__(self, items: List[Node]) -> None:
        self.items = items


class ObjectLit(Node):
    __slots__ = ("pairs",)

    def __init__(self, pairs: List[Tuple[str, Node]]) -> None:
        self.pairs = pairs


class FuncExpr(Node):
    __slots__ = ("name", "params", "body")

    def __init__(self, name: str, params: List[str], body: List[Node]) -> None:
        self.name = name
        self.params = params
        self.body = body


class New(Node):
    __slots__ = ("target",)

    def __init__(self, target: Node) -> None:
        self.target = target


class Unary(Node):
    __slots__ = ("op", "operand")

    def __init__(self, op: str, operand: Node) -> None:
        self.op = op
        self.operand = operand


class Update(Node):
    __slots__ = ("op", "target", "prefix")

    def __init__(self, op: str, target: Node, prefix: bool) -> None:
        self.op = op
        self.target = target
        self.prefix = prefix


class Binary(Node):
    __slots__ = ("op", "left", "right")

    def __init__(self, op: str, left: Node, right: Node) -> None:
        self.op = op
        self.left = left
        self.right = right


class Logical(Node):
    __slots__ = ("op", "left", "right")

    def __init__(self, op: str, left: Node, right: Node) -> None:
        self.op = op
        self.left = left
        self.right = right


class Ternary(Node):
    __slots__ = ("cond", "then", "otherwise")

    def __init__(self, cond: Node, then: Node, otherwise: Node) -> None:
        self.cond = cond
        self.then = then
        self.otherwise = otherwise


class Assign(Node):
    __slots__ = ("op", "target", "value")

    def __init__(self, op: str, target: Node, value: Node) -> None:
        self.op = op
        self.target = target
        self.value = value


class Member(Node):
    __slots__ = ("obj", "name")

    def __init__(self, obj: Node, name: str) -> None:
        self.obj = obj
        self.name = name


class Index(Node):
    __slots__ = ("obj", "index")

    def __init__(self, obj: Node, index: Node) -> None:
        self.obj = obj
        self.index = index


class Call(Node):
    __slots__ = ("callee", "args")

    def __init__(self, callee: Node, args: List[Node]) -> None:
        self.callee = callee
        self.args = args


# ============================================================
# Parser
# ============================================================

class JSParser:
    """Recursive-descent parser producing the AST above."""

    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens = list(tokens)
        self.i = 0

    # -- token helpers ---------------------------------------------------
    @property
    def current(self) -> Token:
        return self.tokens[self.i]

    def peek(self, offset: int = 0) -> Token:
        index = min(self.i + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def at(self, value: str) -> bool:
        tok = self.current
        return tok.type in ("OP", "KW") and tok.value == value

    def at_type(self, type_: str) -> bool:
        return self.current.type == type_

    def advance(self) -> Token:
        tok = self.current
        if self.i < len(self.tokens) - 1:
            self.i += 1
        return tok

    def expect(self, value: str) -> Token:
        if not self.at(value):
            raise JSSyntaxError(
                f"expected {value!r} but found {self.current.value or 'EOF'!r}",
                self.current.pos)
        return self.advance()

    def expect_ident(self) -> str:
        if self.current.type != "ID":
            raise JSSyntaxError(
                f"expected an identifier but found {self.current.value or 'EOF'!r}",
                self.current.pos)
        return self.advance().value

    def skip_semi(self) -> None:
        while self.at(";"):
            self.advance()

    # -- entry point -----------------------------------------------------
    def parse(self) -> Program:
        body: List[Node] = []
        while not self.at_type("EOF"):
            body.append(self.parse_statement())
            self.skip_semi()
        return Program(body)

    # -- statements ------------------------------------------------------
    def parse_statement(self) -> Node:
        tok = self.current
        if tok.type == "OP" and tok.value == "{":
            return self.parse_block()
        if tok.type == "KW":
            word = tok.value
            if word in ("let", "const", "var"):
                return self.parse_var_decl()
            if word == "if":
                return self.parse_if()
            if word == "while":
                self.advance()
                self.expect("(")
                cond = self.parse_expression()
                self.expect(")")
                return While(cond, self.parse_statement())
            if word == "do":
                self.advance()
                body = self.parse_statement()
                self.expect("while")
                self.expect("(")
                cond = self.parse_expression()
                self.expect(")")
                return While(cond, body)      # do/while behaves like while here
            if word == "for":
                return self.parse_for()
            if word == "function":
                return self.parse_function_decl()
            if word == "return":
                self.advance()
                if self.at(";") or self.at("}") or self.at_type("EOF"):
                    return Return(None)
                return Return(self.parse_expression())
            if word == "break":
                self.advance()
                return Break()
            if word == "continue":
                self.advance()
                return Continue()
        expr = self.parse_expression()
        return ExprStmt(expr)

    def parse_block(self) -> Block:
        self.expect("{")
        body: List[Node] = []
        while not self.at("}"):
            if self.at_type("EOF"):
                raise JSSyntaxError("unexpected end of script (missing '}')",
                                    self.current.pos)
            body.append(self.parse_statement())
            self.skip_semi()
        self.expect("}")
        return Block(body)

    def parse_var_decl(self) -> VarDecl:
        kind = self.advance().value
        name = self.expect_ident()
        init = None
        if self.at("="):
            self.advance()
            init = self.parse_expression()
        return VarDecl(kind, name, init)

    def parse_if(self) -> If:
        self.expect("if")
        self.expect("(")
        cond = self.parse_expression()
        self.expect(")")
        then = self.parse_statement()
        otherwise = None
        self.skip_semi()
        if self.at("else"):
            self.advance()
            otherwise = self.parse_statement()
        return If(cond, then, otherwise)

    def parse_for(self) -> Node:
        self.expect("for")
        self.expect("(")
        # for (x of y)  /  for (const x of y)
        offset = 1 if (self.current.type == "KW"
                       and self.current.value in ("let", "const", "var")) else 0
        if (self.peek(offset).type in ("ID", "KW")
                and self.peek(offset + 1).value == "of"):
            for _ in range(offset):
                self.advance()
            name = self.advance().value
            self.advance()                      # 'of'
            iterable = self.parse_expression()
            self.expect(")")
            return ForOf(name, iterable, self.parse_statement())

        init: Optional[Node] = None
        if not self.at(";"):
            if self.current.type == "KW" and self.current.value in ("let", "const", "var"):
                init = self.parse_var_decl()
            else:
                init = ExprStmt(self.parse_expression())
        self.expect(";")
        cond: Optional[Node] = None
        if not self.at(";"):
            cond = self.parse_expression()
        self.expect(";")
        update: Optional[Node] = None
        if not self.at(")"):
            update = self.parse_expression()
        self.expect(")")
        return For(init, cond, update, self.parse_statement())

    def parse_function_decl(self) -> FuncDecl:
        self.expect("function")
        name = self.expect_ident()
        params = self.parse_params()
        body = self.parse_block().body
        return FuncDecl(name, params, body)

    def parse_params(self) -> List[str]:
        self.expect("(")
        params: List[str] = []
        while not self.at(")"):
            params.append(self.expect_ident())
            if self.at(","):
                self.advance()
        self.expect(")")
        return params

    # -- expressions -----------------------------------------------------
    def parse_expression(self) -> Node:
        return self.parse_assignment()

    def parse_assignment(self) -> Node:
        left = self.parse_ternary()
        if self.current.type == "OP" and self.current.value in (
                "=", "+=", "-=", "*=", "/=", "%="):
            op = self.advance().value
            right = self.parse_assignment()
            return Assign(op, left, right)
        return left

    def parse_ternary(self) -> Node:
        cond = self.parse_or()
        if self.at("?"):
            self.advance()
            then = self.parse_assignment()
            self.expect(":")
            otherwise = self.parse_assignment()
            return Ternary(cond, then, otherwise)
        return cond

    def parse_or(self) -> Node:
        node = self.parse_and()
        while self.at("||"):
            self.advance()
            node = Logical("||", node, self.parse_and())
        return node

    def parse_and(self) -> Node:
        node = self.parse_bit_or()
        while self.at("&&"):
            self.advance()
            node = Logical("&&", node, self.parse_bit_or())
        return node

    def parse_bit_or(self) -> Node:
        node = self.parse_bit_xor()
        while self.at("|"):
            self.advance()
            node = Binary("|", node, self.parse_bit_xor())
        return node

    def parse_bit_xor(self) -> Node:
        node = self.parse_bit_and()
        while self.at("^"):
            self.advance()
            node = Binary("^", node, self.parse_bit_and())
        return node

    def parse_bit_and(self) -> Node:
        node = self.parse_equality()
        while self.at("&"):
            self.advance()
            node = Binary("&", node, self.parse_equality())
        return node

    def parse_equality(self) -> Node:
        node = self.parse_relational()
        while self.current.type == "OP" and self.current.value in (
                "==", "!=", "===", "!=="):
            op = self.advance().value
            node = Binary(op, node, self.parse_relational())
        return node

    def parse_relational(self) -> Node:
        node = self.parse_shift()
        while self.current.type == "OP" and self.current.value in (
                "<", ">", "<=", ">="):
            op = self.advance().value
            node = Binary(op, node, self.parse_shift())
        return node

    def parse_shift(self) -> Node:
        node = self.parse_additive()
        while self.current.type == "OP" and self.current.value in ("<<", ">>"):
            op = self.advance().value
            node = Binary(op, node, self.parse_additive())
        return node

    def parse_additive(self) -> Node:
        node = self.parse_multiplicative()
        while self.current.type == "OP" and self.current.value in ("+", "-"):
            op = self.advance().value
            node = Binary(op, node, self.parse_multiplicative())
        return node

    def parse_multiplicative(self) -> Node:
        node = self.parse_unary()
        while self.current.type == "OP" and self.current.value in ("*", "/", "%"):
            op = self.advance().value
            node = Binary(op, node, self.parse_unary())
        return node

    def parse_unary(self) -> Node:
        tok = self.current
        if tok.type == "OP" and tok.value in ("!", "-", "+", "~"):
            self.advance()
            return Unary(tok.value, self.parse_unary())
        if tok.type == "KW" and tok.value == "new":
            self.advance()
            return self.parse_new()
        if tok.type == "KW" and tok.value == "typeof":
            self.advance()
            return Unary("typeof", self.parse_unary())
        if tok.type == "OP" and tok.value in ("++", "--"):
            self.advance()
            return Update(tok.value, self.parse_unary(), prefix=True)
        return self.parse_postfix()

    def parse_postfix(self) -> Node:
        node = self.parse_call()
        if self.current.type == "OP" and self.current.value in ("++", "--"):
            op = self.advance().value
            return Update(op, node, prefix=False)
        return node

    def parse_new(self) -> Node:
        """`new Foo(...)`: binds to the constructor call only, then the chain."""
        ctor = self.parse_primary()
        while True:
            if self.at("."):
                self.advance()
                name = self.current
                if name.type not in ("ID", "KW"):
                    raise JSSyntaxError("expected a property name", name.pos)
                self.advance()
                ctor = Member(ctor, name.value)
            elif self.at("["):
                self.advance()
                index = self.parse_expression()
                self.expect("]")
                ctor = Index(ctor, index)
            else:
                break
        args: List[Node] = []
        if self.at("("):
            self.advance()
            while not self.at(")"):
                args.append(self.parse_assignment())
                if self.at(","):
                    self.advance()
            self.expect(")")
        return self.parse_call(New(Call(ctor, args)))

    def parse_call(self, node: Optional[Node] = None) -> Node:
        node = self.parse_primary() if node is None else node
        while True:
            if self.at("."):
                self.advance()
                name = self.current
                if name.type not in ("ID", "KW"):
                    raise JSSyntaxError("expected a property name", name.pos)
                self.advance()
                node = Member(node, name.value)
            elif self.at("["):
                self.advance()
                index = self.parse_expression()
                self.expect("]")
                node = Index(node, index)
            elif self.at("("):
                self.advance()
                args: List[Node] = []
                while not self.at(")"):
                    args.append(self.parse_assignment())
                    if self.at(","):
                        self.advance()
                self.expect(")")
                node = Call(node, args)
            else:
                return node

    def parse_primary(self) -> Node:
        tok = self.current
        if tok.type == "NUM":
            self.advance()
            value = float(tok.value)
            return Lit(_num_out(value))
        if tok.type == "STR":
            self.advance()
            return Lit(tok.value)
        if tok.type == "REGEX":
            self.advance()
            pattern, _, flags = tok.value.partition("\x00")
            return RegexLit(pattern, flags)
        if tok.type == "ID":
            self.advance()
            return Ident(tok.value)
        if tok.type == "KW":
            if tok.value == "true":
                self.advance()
                return Lit(True)
            if tok.value == "false":
                self.advance()
                return Lit(False)
            if tok.value == "null":
                self.advance()
                return Lit(None)
            if tok.value == "undefined":
                self.advance()
                return Lit(UNDEFINED)
            if tok.value == "function":
                self.advance()
                name = ""
                if self.current.type == "ID":
                    name = self.advance().value
                params = self.parse_params()
                body = self.parse_block().body
                return FuncExpr(name, params, body)
            raise JSSyntaxError(f"unexpected keyword {tok.value!r}", tok.pos)
        if tok.type == "OP" and tok.value == "(":
            self.advance()
            node = self.parse_expression()
            self.expect(")")
            return node
        if tok.type == "OP" and tok.value == "[":
            self.advance()
            items: List[Node] = []
            while not self.at("]"):
                items.append(self.parse_assignment())
                if self.at(","):
                    self.advance()
            self.expect("]")
            return ArrayLit(items)
        if tok.type == "OP" and tok.value == "{":
            self.advance()
            pairs: List[Tuple[str, Node]] = []
            while not self.at("}"):
                key_tok = self.current
                if key_tok.type in ("ID", "KW", "STR", "NUM"):
                    key = key_tok.value
                    self.advance()
                else:
                    raise JSSyntaxError("expected an object key", key_tok.pos)
                if self.at(":"):
                    self.advance()
                    pairs.append((key, self.parse_assignment()))
                else:
                    pairs.append((key, Ident(key)))
                if self.at(","):
                    self.advance()
            self.expect("}")
            return ObjectLit(pairs)
        raise JSSyntaxError(
            f"unexpected token {tok.value or 'EOF'!r}", tok.pos)


# ============================================================
# Runtime values
# ============================================================

class JSObject:
    """Anything with custom property semantics (DOM nodes, console, Math…)."""

    __slots__ = ()

    def js_get(self, name: str) -> Any:
        raise JSRuntimeError(f"{name} is not defined")

    def js_set(self, name: str, value: Any) -> None:
        raise JSRuntimeError(f"cannot set {name}")

    def js_keys(self) -> List[str]:
        return []

    def js_to_string(self) -> str:
        return "[object Object]"


class NativeFunction:
    __slots__ = ("name", "fn")

    def __init__(self, name: str, fn: Callable[[Any, List[Any]], Any]) -> None:
        self.name = name
        self.fn = fn

    def call(self, this: Any, args: List[Any]) -> Any:
        return self.fn(this, args)

    def __repr__(self) -> str:
        return f"<native {self.name}>"


class JSFunction:
    __slots__ = ("name", "params", "body", "closure", "interp")

    def __init__(self, name: str, params: List[str], body: List[Node],
                 closure: "Scope", interp: "Interpreter") -> None:
        self.name = name
        self.params = params
        self.body = body
        self.closure = closure
        self.interp = interp

    def call(self, this: Any, args: List[Any]) -> Any:
        return self.interp.call_function(self, this, args)

    def __repr__(self) -> str:
        return f"<function {self.name or 'anonymous'}>"


class BoundMethod:
    """A method looked up on an object: keeps the receiver for `this`."""

    __slots__ = ("receiver", "func")

    def __init__(self, receiver: Any, func: Any) -> None:
        self.receiver = receiver
        self.func = func

    def call(self, this: Any, args: List[Any]) -> Any:
        return self.func.call(self.receiver, args)


class Scope:
    __slots__ = ("values", "parent", "consts", "this_value")

    def __init__(self, parent: Optional["Scope"] = None) -> None:
        self.values: Dict[str, Any] = {}
        self.parent = parent
        self.consts: set = set()
        self.this_value: Any = UNDEFINED

    def declare(self, name: str, value: Any, kind: str = "let") -> None:
        self.values[name] = value
        if kind == "const":
            self.consts.add(name)

    def get(self, name: str) -> Any:
        scope: Optional[Scope] = self
        while scope is not None:
            if name in scope.values:
                return scope.values[name]
            scope = scope.parent
        raise JSRuntimeError(f"{name} is not defined", "ReferenceError")

    def find(self, name: str) -> Optional["Scope"]:
        scope: Optional[Scope] = self
        while scope is not None:
            if name in scope.values:
                return scope
            scope = scope.parent
        return None

    def set(self, name: str, value: Any) -> None:
        scope = self.find(name)
        if scope is None:
            self.values[name] = value          # implicit global
            return
        if name in scope.consts:
            raise JSRuntimeError(f"assignment to constant variable {name!r}")
        scope.values[name] = value

    def this_of(self) -> Any:
        scope: Optional[Scope] = self
        while scope is not None:
            if scope.this_value is not UNDEFINED:
                return scope.this_value
            scope = scope.parent
        return UNDEFINED


# ============================================================
# Interpreter
# ============================================================

class _ReturnSignal(Exception):
    def __init__(self, value: Any) -> None:
        self.value = value


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


class Timer:
    """A pending setTimeout / setInterval callback."""

    __slots__ = ("id", "due", "fn", "args", "interval", "cancelled")

    def __init__(self, timer_id: int, due: float, fn: Any, args: List[Any],
                 interval: Optional[float]) -> None:
        self.id = timer_id
        self.due = due
        self.fn = fn
        self.args = args
        self.interval = interval
        self.cancelled = False


class Interpreter:
    """Tree-walking evaluator with an instruction and time budget."""

    def __init__(self, on_console: Optional[Callable[[str, str], None]] = None,
                 max_steps: int = 300_000,
                 time_budget: float = 1.5) -> None:
        self.on_console = on_console
        self.max_steps = max_steps
        self.time_budget = time_budget
        self.steps = 0
        self.deadline = time.monotonic() + time_budget
        self.globals = Scope()
        self.timers: List[Timer] = []
        self._timer_seq = 0
        self.console_lines: List[Tuple[str, str]] = []
        self._install_builtins()

    # -- budget ----------------------------------------------------------
    def tick(self) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise ScriptError(
                f"Script exceeded its instruction budget ({self.max_steps} steps)")
        if self.steps % 512 == 0 and time.monotonic() > self.deadline:
            raise ScriptError("Script exceeded its time budget")

    def reset_budget(self, seconds: Optional[float] = None) -> None:
        self.steps = 0
        self.deadline = time.monotonic() + (seconds or self.time_budget)

    # -- built-ins -------------------------------------------------------
    def _install_builtins(self) -> None:
        g = self.globals
        g.declare("undefined", UNDEFINED, "const")
        g.declare("NaN", float("nan"), "const")
        g.declare("Infinity", float("inf"), "const")

        g.declare("parseInt", NativeFunction("parseInt", _builtin_parse_int))
        g.declare("parseFloat", NativeFunction("parseFloat", _builtin_parse_float))
        g.declare("isNaN", NativeFunction("isNaN", _builtin_is_nan))
        g.declare("Number", NativeFunction("Number", lambda t, a: to_number(a[0]) if a else 0))
        g.declare("String", NativeFunction("String", lambda t, a: to_string(a[0]) if a else ""))
        g.declare("Boolean", NativeFunction("Boolean", lambda t, a: truthy(a[0]) if a else False))
        g.declare("Math", MathObject(self))
        g.declare("JSON", JsonObject(self))
        g.declare("console", ConsoleObject(self))
        g.declare("Array", ArrayObject(self))
        g.declare("Date", DateCtor())
        g.declare("RegExp", NativeFunction("RegExp", lambda this, args: RegexValue(
            to_string(args[0]) if args else "", to_string(args[1]) if len(args) > 1 else "")))
        g.declare("Object", ObjectObject(self))
        g.declare("alert", NativeFunction(
            "alert", lambda t, a: self.log_console("log", "alert: " + to_string(a[0]) if a else "alert")))
        g.declare("setTimeout", NativeFunction(
            "setTimeout", lambda this, args: self._set_timer(this, args, False)))
        g.declare("setInterval", NativeFunction(
            "setInterval", lambda this, args: self._set_timer(this, args, True)))
        g.declare("clearTimeout", NativeFunction("clearTimeout", self._clear_timer))
        g.declare("clearInterval", NativeFunction("clearInterval", self._clear_timer))

    def _set_timer(self, _this: Any, args: List[Any], repeat: bool) -> Any:
        if not args or not isinstance(args[0], (JSFunction, NativeFunction)):
            return UNDEFINED
        delay = to_number(args[1]) if len(args) > 1 else 0.0
        delay = 0.0 if delay != delay else max(0.0, min(delay, 10000.0))
        self._timer_seq += 1
        seconds = max(0.016, delay / 1000.0)
        self.timers.append(Timer(self._timer_seq, time.monotonic() + seconds,
                                 args[0], args[2:], seconds if repeat else None))
        return self._timer_seq

    def _clear_timer(self, _this: Any, args: List[Any]) -> Any:
        if args:
            target = args[0]
            for timer in self.timers:
                if timer.id == target:
                    timer.cancelled = True
        return UNDEFINED

    def log_console(self, level: str, message: str) -> Any:
        self.console_lines.append((level, message))
        if self.on_console is not None:
            try:
                self.on_console(level, message)
            except Exception:
                pass
        return UNDEFINED

    # -- program entry ---------------------------------------------------
    def run(self, program: Program, scope: Optional[Scope] = None) -> Any:
        scope = scope or self.globals
        try:
            self.run_block(program.body, scope, hoist=True)
        except _ReturnSignal as signal:
            return signal.value
        return UNDEFINED

    def evaluate_source(self, source: str) -> Any:
        """Evaluate an expression or statement list; returns the last value."""
        tokens = Lexer(source).tokenize()
        program = JSParser(tokens).parse()
        self.reset_budget(1.0)
        body = program.body
        if not body:
            return UNDEFINED
        if len(body) == 1 and isinstance(body[0], ExprStmt):
            return self.eval(body[0].expr, self.globals)
        head = Program(body[:-1])
        try:
            self.run(head)
        except _ReturnSignal:
            pass
        last = body[-1]
        if isinstance(last, ExprStmt):
            return self.eval(last.expr, self.globals)
        self.exec_statement(last, self.globals)
        return UNDEFINED

    # -- statements ------------------------------------------------------
    def run_block(self, body: List[Node], scope: Scope, hoist: bool = False) -> None:
        if hoist:
            for stmt in body:
                if isinstance(stmt, FuncDecl):
                    scope.declare(stmt.name,
                                  JSFunction(stmt.name, stmt.params, stmt.body,
                                             scope, self), "var")
        for stmt in body:
            self.tick()
            self.exec_statement(stmt, scope)

    def exec_statement(self, stmt: Node, scope: Scope) -> None:
        if isinstance(stmt, ExprStmt):
            self.eval(stmt.expr, scope)
        elif isinstance(stmt, VarDecl):
            value = self.eval(stmt.init, scope) if stmt.init is not None else UNDEFINED
            scope.declare(stmt.name, value, stmt.kind)
        elif isinstance(stmt, FuncDecl):
            scope.declare(stmt.name,
                          JSFunction(stmt.name, stmt.params, stmt.body, scope, self),
                          "var")
        elif isinstance(stmt, Block):
            self.run_block(stmt.body, Scope(scope))
        elif isinstance(stmt, If):
            if truthy(self.eval(stmt.cond, scope)):
                self.exec_statement(stmt.then, scope)
            elif stmt.otherwise is not None:
                self.exec_statement(stmt.otherwise, scope)
        elif isinstance(stmt, While):
            while truthy(self.eval(stmt.cond, scope)):
                self.tick()
                try:
                    self.exec_statement(stmt.body, scope)
                except _BreakSignal:
                    break
                except _ContinueSignal:
                    continue
        elif isinstance(stmt, For):
            loop_scope = Scope(scope)
            if stmt.init is not None:
                self.exec_statement(stmt.init, loop_scope)
            while stmt.cond is None or truthy(self.eval(stmt.cond, loop_scope)):
                self.tick()
                try:
                    self.exec_statement(stmt.body, loop_scope)
                except _BreakSignal:
                    break
                except _ContinueSignal:
                    pass
                if stmt.update is not None:
                    self.eval(stmt.update, loop_scope)
        elif isinstance(stmt, ForOf):
            iterable = self.eval(stmt.iterable, scope)
            items = _iterable_values(iterable)
            for item in items:
                self.tick()
                loop_scope = Scope(scope)
                loop_scope.declare(stmt.name, item, "let")
                try:
                    self.exec_statement(stmt.body, loop_scope)
                except _BreakSignal:
                    break
                except _ContinueSignal:
                    continue
        elif isinstance(stmt, Return):
            raise _ReturnSignal(self.eval(stmt.value, scope)
                                if stmt.value is not None else UNDEFINED)
        elif isinstance(stmt, Break):
            raise _BreakSignal()
        elif isinstance(stmt, Continue):
            raise _ContinueSignal()
        else:
            raise JSRuntimeError(f"cannot execute {type(stmt).__name__}")

    # -- functions -------------------------------------------------------
    def call_function(self, func: JSFunction, this: Any, args: List[Any]) -> Any:
        scope = Scope(func.closure)
        scope.this_value = this
        scope.declare("arguments", list(args), "var")
        for index, name in enumerate(func.params):
            scope.declare(name, args[index] if index < len(args) else UNDEFINED, "var")
        try:
            self.run_block(func.body, scope, hoist=True)
        except _ReturnSignal as signal:
            return signal.value
        except (_BreakSignal, _ContinueSignal):
            pass
        return UNDEFINED

    def call_value(self, callee: Any, args: List[Any], this: Any = UNDEFINED) -> Any:
        self.tick()
        if isinstance(callee, BoundMethod):
            return callee.call(this, args)
        if isinstance(callee, (JSFunction, NativeFunction)):
            return callee.call(this, args)
        raise JSRuntimeError(f"{to_string(callee)} is not a function")

    # -- expressions -----------------------------------------------------
    def eval(self, node: Optional[Node], scope: Scope) -> Any:
        if node is None:
            return UNDEFINED
        self.tick()
        kind = type(node)

        if kind is Lit:
            return node.value                                   # type: ignore[attr-defined]
        if kind is RegexLit:
            return RegexValue(node.pattern, node.flags)         # type: ignore[attr-defined]
        if kind is Ident:
            if node.name == "this":                             # type: ignore[attr-defined]
                return scope.this_of()
            return scope.get(node.name)                         # type: ignore[attr-defined]
        if kind is ArrayLit:
            return [self.eval(item, scope) for item in node.items]   # type: ignore[attr-defined]
        if kind is ObjectLit:
            return {key: self.eval(value, scope) for key, value in node.pairs}  # type: ignore[attr-defined]
        if kind is FuncExpr:
            return JSFunction(node.name, node.params, node.body, scope, self)  # type: ignore[attr-defined]
        if kind is New:
            return self._eval_new(node, scope)          # type: ignore[arg-type]
        if kind is Unary:
            return self._eval_unary(node, scope)
        if kind is Update:
            return self._eval_update(node, scope)
        if kind is Binary:
            return self._eval_binary(node, scope)
        if kind is Logical:
            left = self.eval(node.left, scope)                  # type: ignore[attr-defined]
            if node.op == "&&":                                 # type: ignore[attr-defined]
                return self.eval(node.right, scope) if truthy(left) else left  # type: ignore[attr-defined]
            return left if truthy(left) else self.eval(node.right, scope)      # type: ignore[attr-defined]
        if kind is Ternary:
            return (self.eval(node.then, scope) if truthy(self.eval(node.cond, scope))  # type: ignore[attr-defined]
                    else self.eval(node.otherwise, scope))      # type: ignore[attr-defined]
        if kind is Assign:
            return self._eval_assign(node, scope)
        if kind is Member:
            obj = self.eval(node.obj, scope)                    # type: ignore[attr-defined]
            return self.get_property(obj, node.name)            # type: ignore[attr-defined]
        if kind is Index:
            obj = self.eval(node.obj, scope)                    # type: ignore[attr-defined]
            index = self.eval(node.index, scope)                # type: ignore[attr-defined]
            return self.get_property(obj, to_string(index))
        if kind is Call:
            return self._eval_call(node, scope)
        raise JSRuntimeError(f"cannot evaluate {kind.__name__}")

    def _eval_new(self, node: New, scope: Scope) -> Any:
        target = node.target
        if isinstance(target, Call):
            callee = self.eval(target.callee, scope)
            args = [self.eval(arg, scope) for arg in target.args]
        else:
            callee = self.eval(target, scope)
            args = []
        return self.construct(callee, args)

    def construct(self, callee: Any, args: List[Any]) -> Any:
        """`new Foo(...)`: no prototypes, but constructor bodies work."""
        if isinstance(callee, DateCtor):
            return DateValue(to_number(args[0]) if args else None)
        if isinstance(callee, JSFunction):
            this: Any = {}
            result = callee.call(this, args)
            if isinstance(result, (dict, list, JSObject)) and result is not UNDEFINED:
                return result
            return this
        if isinstance(callee, NativeFunction):
            result = callee.call(UNDEFINED, args)
            return result if result is not UNDEFINED else {}
        raise JSRuntimeError(f"{to_string(callee)} is not a constructor")

    def _eval_unary(self, node: Unary, scope: Scope) -> Any:
        if node.op == "typeof":
            try:
                value = self.eval(node.operand, scope)
            except JSRuntimeError:
                return "undefined"
            return _typeof(value)
        value = self.eval(node.operand, scope)
        if node.op == "!":
            return not truthy(value)
        if node.op == "-":
            return _num_out(-to_number(value))
        if node.op == "+":
            return _num_out(to_number(value))
        if node.op == "~":
            return _num_out(float(~int(to_number(value))))
        raise JSRuntimeError(f"unknown unary operator {node.op}")

    def _eval_update(self, node: Update, scope: Scope) -> Any:
        old = self.eval(node.target, scope)
        old_number = to_number(old)
        new = old_number + (1 if node.op == "++" else -1)
        self.assign_to(node.target, _num_out(new), scope)
        return _num_out(new) if node.prefix else _num_out(old_number)

    def _eval_binary(self, node: Binary, scope: Scope) -> Any:
        left = self.eval(node.left, scope)
        right = self.eval(node.right, scope)
        op = node.op
        if op == "+":
            if isinstance(left, str) or isinstance(right, str) \
                    or isinstance(left, list) or isinstance(right, list) \
                    or isinstance(left, dict) or isinstance(right, dict):
                return to_string(left) + to_string(right)
            return _num_out(to_number(left) + to_number(right))
        if op in ("-", "*", "/", "%"):
            a, b = to_number(left), to_number(right)
            if op == "-":
                return _num_out(a - b)
            if op == "*":
                return _num_out(a * b)
            if op == "/":
                if b == 0:
                    return float("inf") if a > 0 else (float("-inf") if a < 0
                                                       else float("nan"))
                return _num_out(a / b)
            if b == 0:
                return float("nan")
            return _num_out(math.fmod(a, b))
        if op in ("&", "|", "^", "<<", ">>"):
            a = int(to_number(left))
            b = int(to_number(right))
            if op == "&":
                return a & b
            if op == "|":
                return a | b
            if op == "^":
                return a ^ b
            if op == "<<":
                return a << max(0, min(b, 31))
            return a >> max(0, min(b, 31))
        if op in ("==", "!="):
            result = loose_equals(left, right)
            return result if op == "==" else not result
        if op in ("===", "!=="):
            result = strict_equals(left, right)
            return result if op == "===" else not result
        if op in ("<", ">", "<=", ">="):
            if isinstance(left, str) and isinstance(right, str):
                a, b = left, right
            else:
                a, b = to_number(left), to_number(right)
            if op == "<":
                return a < b
            if op == ">":
                return a > b
            if op == "<=":
                return a <= b
            return a >= b
        raise JSRuntimeError(f"unknown operator {op}")

    def _eval_assign(self, node: Assign, scope: Scope) -> Any:
        if node.op == "=":
            value = self.eval(node.value, scope)
        else:
            current = self.eval(node.target, scope)
            right = self.eval(node.value, scope)
            value = self._eval_binary(
                Binary(node.op[0], Lit(current), Lit(right)), scope)
        self.assign_to(node.target, value, scope)
        return value

    def assign_to(self, target: Node, value: Any, scope: Scope) -> None:
        if isinstance(target, Ident):
            scope.set(target.name, value)
            return
        if isinstance(target, Member):
            obj = self.eval(target.obj, scope)
            self.set_property(obj, target.name, value)
            return
        if isinstance(target, Index):
            obj = self.eval(target.obj, scope)
            index = self.eval(target.index, scope)
            self.set_property(obj, to_string(index), value)
            return
        raise JSRuntimeError("invalid assignment target")

    def _eval_call(self, node: Call, scope: Scope) -> Any:
        # Method calls need their receiver as `this`.
        if isinstance(node.callee, Member):
            receiver = self.eval(node.callee.obj, scope)
            callee = self.get_property(receiver, node.callee.name)
            this = receiver
        elif isinstance(node.callee, Index):
            receiver = self.eval(node.callee.obj, scope)
            name = to_string(self.eval(node.callee.index, scope))
            callee = self.get_property(receiver, name)
            this = receiver
        else:
            callee = self.eval(node.callee, scope)
            this = scope.this_of()
        args = [self.eval(arg, scope) for arg in node.args]
        return self.call_value(callee, args, this)

    # -- properties ------------------------------------------------------
    def get_property(self, obj: Any, name: str) -> Any:
        if obj is UNDEFINED or obj is None:
            raise JSRuntimeError(f"cannot read {name!r} of {to_string(obj)}")
        if isinstance(obj, JSObject):
            return obj.js_get(name)
        if isinstance(obj, str):
            return _string_property(obj, name)
        if isinstance(obj, list):
            return _array_property(obj, name)
        if isinstance(obj, dict):
            return _object_property(obj, name)
        if isinstance(obj, bool):
            return _number_property(1.0 if obj else 0.0, name)
        if isinstance(obj, (int, float)):
            return _number_property(float(obj), name)
        return UNDEFINED

    def set_property(self, obj: Any, name: str, value: Any) -> None:
        if isinstance(obj, JSObject):
            obj.js_set(name, value)
            return
        if isinstance(obj, list):
            if name == "length":
                length = int(to_number(value))
                del obj[length:]
                while len(obj) < length:
                    obj.append(UNDEFINED)
                return
            try:
                index = int(name)
            except ValueError:
                raise JSRuntimeError(f"cannot set {name!r} on an array")
            while len(obj) <= index:
                obj.append(UNDEFINED)
            obj[index] = value
            return
        if isinstance(obj, dict):
            obj[name] = value
            return
        raise JSRuntimeError(f"cannot set {name!r} on {to_string(obj)}")


class RegexValue(JSObject):
    """A small RegExp: enough for `test`, `exec`, `source` and `global`."""

    __slots__ = ("pattern", "flags")

    def __init__(self, pattern: str, flags: str = "") -> None:
        self.pattern = pattern
        self.flags = flags

    def _compile(self):
        import re as _re

        flags = 0
        if "i" in self.flags:
            flags |= _re.IGNORECASE
        if "m" in self.flags:
            flags |= _re.MULTILINE
        if "s" in self.flags:
            flags |= _re.DOTALL
        source = self.pattern.replace("\\/", "/")
        return _re.compile(source, flags)

    def js_get(self, name: str) -> Any:
        if name == "source":
            return self.pattern
        if name == "flags":
            return self.flags
        if name == "global":
            return "g" in self.flags
        if name == "test":
            def test(_this: Any, args: List[Any]) -> Any:
                text = to_string(args[0]) if args else ""
                try:
                    return bool(self._compile().search(text))
                except Exception:
                    return False
            return NativeFunction("test", test)
        if name == "exec":
            def execute(_this: Any, args: List[Any]) -> Any:
                text = to_string(args[0]) if args else ""
                try:
                    match = self._compile().search(text)
                except Exception:
                    return None
                if match is None:
                    return None
                return [match.group(0)] + list(match.groups())
            return NativeFunction("exec", execute)
        if name == "toString":
            return NativeFunction("toString", lambda this, args: self.js_to_string())
        return UNDEFINED

    def js_to_string(self) -> str:
        return f"/{self.pattern}/{self.flags}"


def _typeof(value: Any) -> str:
    if value is UNDEFINED:
        return "undefined"
    if value is None:
        return "object"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (JSFunction, NativeFunction, BoundMethod)):
        return "function"
    return "object"


def _iterable_values(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return list(value)
    if hasattr(value, "js_items"):
        return list(value.js_items())
    if isinstance(value, dict):
        return list(value.values())
    return []


# ============================================================
# Prototypes (string / array / object / number methods)
# ============================================================

def _method(receiver: Any, name: str, fn: Callable[..., Any]) -> BoundMethod:
    return BoundMethod(receiver, NativeFunction(name, lambda this, args: fn(receiver, *args)))


def _string_property(text: str, name: str) -> Any:
    if name == "length":
        return len(text)
    if name in ("toUpperCase", "toLocaleUpperCase"):
        return _method(text, name, lambda s: s.upper())
    if name in ("toLowerCase", "toLocaleLowerCase"):
        return _method(text, name, lambda s: s.lower())
    if name == "trim":
        return _method(text, name, lambda s: s.strip())
    if name == "charAt":
        return _method(text, name,
                       lambda s, i=0: s[int(to_number(i))] if 0 <= int(to_number(i)) < len(s) else "")
    if name == "charCodeAt":
        return _method(text, name,
                       lambda s, i=0: ord(s[int(to_number(i))])
                       if 0 <= int(to_number(i)) < len(s) else float("nan"))
    if name == "indexOf":
        return _method(text, name, lambda s, sub="", start=0:
                       s.find(to_string(sub), int(to_number(start))))
    if name == "lastIndexOf":
        return _method(text, name, lambda s, sub="": s.rfind(to_string(sub)))
    if name == "includes":
        return _method(text, name, lambda s, sub="": to_string(sub) in s)
    if name == "startsWith":
        return _method(text, name, lambda s, sub="": s.startswith(to_string(sub)))
    if name == "endsWith":
        return _method(text, name, lambda s, sub="": s.endswith(to_string(sub)))
    if name == "slice":
        return _method(text, name, lambda s, start=0, end=None: _slice_string(s, start, end))
    if name == "substring":
        return _method(text, name, lambda s, start=0, end=None:
                       _slice_string(s, max(0, int(to_number(start))),
                                     len(s) if end is None else max(0, int(to_number(end)))))
    if name == "substr":
        return _method(text, name, lambda s, start=0, length=None:
                       s[int(to_number(start)):int(to_number(start)) +
                         (int(to_number(length)) if length is not None else len(s))])
    if name == "split":
        return _method(text, name, lambda s, sep=None: _string_split(s, sep))
    if name == "replace":
        return _method(text, name, lambda s, old="", new="":
                       _string_replace(s, old, new, 1))
    if name == "replaceAll":
        return _method(text, name, lambda s, old="", new="":
                       _string_replace(s, old, new, 0))
    if name == "match":
        return _method(text, name, lambda s, pattern=None: _string_match(s, pattern))
    if name == "search":
        return _method(text, name, lambda s, pattern=None: _string_search(s, pattern))
    if name == "repeat":
        return _method(text, name, lambda s, count=1:
                       s * max(0, min(1000, int(to_number(count)))))
    if name == "concat":
        return _method(text, name, lambda s, *parts: s + "".join(to_string(p) for p in parts))
    if name == "padStart":
        return _method(text, name, lambda s, width=0, pad=" ":
                       to_string(pad).rjust(int(to_number(width)), to_string(pad)[:1] or " ")
                       if len(s) < int(to_number(width)) else s)
    if name == "padEnd":
        return _method(text, name, lambda s, width=0, pad=" ":
                       to_string(pad).ljust(int(to_number(width)), to_string(pad)[:1] or " ")
                       if len(s) < int(to_number(width)) else s)
    if name == "toString":
        return _method(text, name, lambda s: s)
    if name == "toString":
        return _method(text, name, lambda s: s)
    return UNDEFINED


def _string_split(text: str, sep: Any) -> List[str]:
    if sep in (None, UNDEFINED):
        return [text]
    if isinstance(sep, RegexValue):
        try:
            return sep._compile().split(text)
        except Exception:
            return [text]
    return text.split(to_string(sep))


def _string_replace(text: str, old: Any, new: Any, count: int) -> str:
    replacement = to_string(new)
    if isinstance(old, RegexValue):
        try:
            return old._compile().sub(replacement.replace("\\", "\\\\"), text,
                                      count=count)
        except Exception:
            return text
    return text.replace(to_string(old), replacement, count)


def _string_match(text: str, pattern: Any) -> Any:
    if not isinstance(pattern, RegexValue):
        return None
    try:
        regex = pattern._compile()
    except Exception:
        return None
    if "g" in pattern.flags:
        return regex.findall(text)
    found = regex.search(text)
    return [found.group(0)] + list(found.groups()) if found else None


def _string_search(text: str, pattern: Any) -> Any:
    if not isinstance(pattern, RegexValue):
        return text.find(to_string(pattern))
    try:
        found = pattern._compile().search(text)
    except Exception:
        return -1
    return found.start() if found else -1


def _slice_string(text: str, start: Any, end: Any) -> str:
    length = len(text)
    begin = int(to_number(start))
    begin = max(0, length + begin) if begin < 0 else min(begin, length)
    if end in (None, UNDEFINED):
        finish = length
    else:
        finish = int(to_number(end))
        finish = max(0, length + finish) if finish < 0 else min(finish, length)
    return text[begin:finish]


def _array_property(items: List[Any], name: str) -> Any:
    if name == "length":
        return len(items)
    if name == "push":
        return _method(items, name, lambda a, *values: (a.extend(values), len(a))[1])
    if name == "pop":
        return _method(items, name, lambda a: a.pop() if a else UNDEFINED)
    if name == "shift":
        return _method(items, name, lambda a: a.pop(0) if a else UNDEFINED)
    if name == "unshift":
        return _method(items, name, lambda a, *values: (a.__setitem__(slice(0, 0), values), len(a))[1])
    if name == "join":
        return _method(items, name, lambda a, sep=",":
                       to_string(sep).join(to_string(x) for x in a))
    if name == "slice":
        return _method(items, name, lambda a, start=0, end=None:
                       a[int(to_number(start)):None if end in (None, UNDEFINED)
                         else int(to_number(end))])
    if name == "indexOf":
        return _method(items, name, lambda a, needle: _index_of(a, needle))
    if name == "includes":
        return _method(items, name, lambda a, needle: _index_of(a, needle) >= 0)
    if name == "reverse":
        return _method(items, name, lambda a: (a.reverse(), a)[1])
    if name == "concat":
        return _method(items, name, lambda a, *others:
                       a + [x for other in others
                            for x in (other if isinstance(other, list) else [other])])
    if name == "splice":
        return _method(items, name, lambda a, start=0, count=None, *values:
                       _splice(a, start, count, values))
    if name == "forEach":
        return _method(items, name, lambda a, fn:
                       [fn.call(UNDEFINED, [item, index, a]) for index, item in enumerate(list(a))]
                       and UNDEFINED)
    if name == "map":
        return _method(items, name, lambda a, fn:
                       [fn.call(UNDEFINED, [item, index, a]) for index, item in enumerate(list(a))])
    if name == "filter":
        return _method(items, name, lambda a, fn:
                       [item for index, item in enumerate(list(a))
                        if truthy(fn.call(UNDEFINED, [item, index, a]))])
    if name == "reduce":
        return _method(items, name, lambda a, fn, initial=None: _reduce(a, fn, initial))
    if name == "find":
        return _method(items, name, lambda a, fn:
                       next((item for item in a
                             if truthy(fn.call(UNDEFINED, [item]))), UNDEFINED))
    if name == "sort":
        return _method(items, name, lambda a: (a.sort(key=lambda v: to_string(v)), a)[1])
    if name == "toString":
        return _method(items, name, lambda a: to_string(a))
    try:
        index = int(name)
    except ValueError:
        return UNDEFINED
    return items[index] if 0 <= index < len(items) else UNDEFINED


def _index_of(items: List[Any], needle: Any) -> int:
    for index, item in enumerate(items):
        if strict_equals(item, needle) or loose_equals(item, needle):
            return index
    return -1


def _splice(items: List[Any], start: Any, count: Any, values: Sequence[Any]) -> List[Any]:
    begin = int(to_number(start))
    begin = max(0, len(items) + begin) if begin < 0 else begin
    if count in (None, UNDEFINED):
        removed = items[begin:]
        del items[begin:]
    else:
        amount = max(0, int(to_number(count)))
        removed = items[begin:begin + amount]
        del items[begin:begin + amount]
    items[begin:begin] = list(values)
    return removed


def _reduce(items: List[Any], fn: Any, initial: Any) -> Any:
    if initial in (None, UNDEFINED):
        if not items:
            raise JSRuntimeError("reduce of empty array with no initial value")
        accumulator = items[0]
        rest = items[1:]
    else:
        accumulator = initial
        rest = items
    for index, item in enumerate(rest):
        accumulator = fn.call(UNDEFINED, [accumulator, item, index, items])
    return accumulator


def _object_property(obj: Dict[str, Any], name: str) -> Any:
    if name in obj:
        return obj[name]
    if name == "hasOwnProperty":
        return _method(obj, name, lambda o, key: to_string(key) in o)
    if name == "toString":
        return _method(obj, name, lambda o: "[object Object]")
    return UNDEFINED


def _number_property(value: float, name: str) -> Any:
    if name == "toFixed":
        return _method(value, name, lambda v, digits=0: f"{v:.{int(to_number(digits))}f}")
    if name == "toString":
        return _method(value, name, lambda v, radix=None: to_string(_num_out(v)))
    return UNDEFINED


# ============================================================
# Built-in objects
# ============================================================

class DateValue(JSObject):
    """`new Date()` — a timestamp with the handful of methods pages use."""

    __slots__ = ("ms",)

    def __init__(self, ms: Optional[float] = None) -> None:
        self.ms = time.time() * 1000.0 if ms is None else float(ms)

    def js_get(self, name: str) -> Any:
        import datetime as _dt

        stamp = _dt.datetime.fromtimestamp(self.ms / 1000.0)
        if name in ("getTime", "valueOf"):
            return NativeFunction(name, lambda this, args: _num_out(self.ms))
        if name == "getFullYear":
            return NativeFunction(name, lambda this, args: stamp.year)
        if name == "getMonth":
            return NativeFunction(name, lambda this, args: stamp.month - 1)
        if name == "getDate":
            return NativeFunction(name, lambda this, args: stamp.day)
        if name == "getHours":
            return NativeFunction(name, lambda this, args: stamp.hour)
        if name == "getMinutes":
            return NativeFunction(name, lambda this, args: stamp.minute)
        if name == "getSeconds":
            return NativeFunction(name, lambda this, args: stamp.second)
        if name == "toISOString":
            return NativeFunction(name, lambda this, args:
                                  stamp.strftime("%Y-%m-%dT%H:%M:%S.") +
                                  f"{stamp.microsecond // 1000:03d}Z")
        if name in ("toLocaleDateString", "toDateString"):
            return NativeFunction(name, lambda this, args:
                                  stamp.strftime("%a %b %d %Y"))
        if name in ("toLocaleTimeString", "toTimeString"):
            return NativeFunction(name, lambda this, args:
                                  stamp.strftime("%H:%M:%S"))
        if name == "toString":
            return NativeFunction(name, lambda this, args: stamp.isoformat(" "))
        return UNDEFINED

    def js_to_string(self) -> str:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(self.ms / 1000.0).isoformat(" ")


class DateCtor(JSObject):
    """The `Date` global: callable-ish, with `Date.now()`."""

    __slots__ = ()

    def js_get(self, name: str) -> Any:
        if name == "now":
            return NativeFunction("now", lambda this, args: _num_out(time.time() * 1000.0))
        if name == "parse":
            return NativeFunction("parse", lambda this, args: float("nan"))
        return UNDEFINED


class MathObject(JSObject):
    __slots__ = ("interp",)
    _CONSTANTS = {"PI": math.pi, "E": math.e, "LN2": math.log(2), "LN10": math.log(10),
                  "SQRT2": math.sqrt(2)}

    def __init__(self, interp: Interpreter) -> None:
        self.interp = interp

    def js_get(self, name: str) -> Any:
        if name in self._CONSTANTS:
            return self._CONSTANTS[name]
        table = {
            "floor": lambda *a: math.floor(to_number(a[0])),
            "ceil": lambda *a: math.ceil(to_number(a[0])),
            "round": lambda *a: math.floor(to_number(a[0]) + 0.5),
            "abs": lambda *a: abs(to_number(a[0])),
            "sqrt": lambda *a: math.sqrt(max(0.0, to_number(a[0]))),
            "pow": lambda *a: to_number(a[0]) ** to_number(a[1]),
            "min": lambda *a: min(to_number(v) for v in a),
            "max": lambda *a: max(to_number(v) for v in a),
            "random": lambda *a: random.random(),
            "sign": lambda *a: (to_number(a[0]) > 0) - (to_number(a[0]) < 0),
            "trunc": lambda *a: math.trunc(to_number(a[0])),
        }
        if name in table:
            fn = table[name]
            return NativeFunction(name, lambda this, args, f=fn: _num_out(float(f(*args))))
        return UNDEFINED


class JsonObject(JSObject):
    __slots__ = ("interp",)

    def __init__(self, interp: Interpreter) -> None:
        self.interp = interp

    def js_get(self, name: str) -> Any:
        if name == "stringify":
            return NativeFunction("stringify", lambda this, args:
                                  json.dumps(_plain(args[0] if args else None),
                                             ensure_ascii=False))
        if name == "parse":
            def parse(this: Any, args: List[Any]) -> Any:
                try:
                    return json.loads(to_string(args[0] if args else "null"))
                except Exception as exc:
                    raise JSRuntimeError(f"JSON.parse failed: {exc}", "SyntaxError")
            return NativeFunction("parse", parse)
        return UNDEFINED


class ConsoleObject(JSObject):
    __slots__ = ("interp",)

    def __init__(self, interp: Interpreter) -> None:
        self.interp = interp

    def js_get(self, name: str) -> Any:
        if name in ("log", "info", "warn", "error", "debug"):
            level = "error" if name == "error" else ("warn" if name == "warn" else "log")
            return NativeFunction(name, lambda this, args, lv=level:
                                  self.interp.log_console(
                                      lv, " ".join(to_string(a) for a in args)))
        return UNDEFINED


class ArrayObject(JSObject):
    __slots__ = ("interp",)

    def __init__(self, interp: Interpreter) -> None:
        self.interp = interp

    def js_get(self, name: str) -> Any:
        if name == "isArray":
            return NativeFunction("isArray", lambda this, args:
                                  bool(args) and isinstance(args[0], list))
        if name == "from":
            return NativeFunction("from", lambda this, args:
                                  _iterable_values(args[0]) if args else [])
        return UNDEFINED


class ObjectObject(JSObject):
    __slots__ = ("interp",)

    def __init__(self, interp: Interpreter) -> None:
        self.interp = interp

    def js_get(self, name: str) -> Any:
        if name == "keys":
            return NativeFunction("keys", lambda this, args: _keys(args[0] if args else {}))
        if name == "values":
            return NativeFunction("values", lambda this, args: _values(args[0] if args else {}))
        return UNDEFINED


def _keys(value: Any) -> List[str]:
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, JSObject):
        return value.js_keys()
    if isinstance(value, list):
        return [str(i) for i in range(len(value))]
    return []


def _values(value: Any) -> List[Any]:
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, list):
        return list(value)
    return []


def _plain(value: Any, depth: int = 0) -> Any:
    """Convert interpreter values into something json.dumps understands."""
    if depth > 20:
        return None
    if value is UNDEFINED or isinstance(value, (JSFunction, NativeFunction, BoundMethod)):
        return None
    if isinstance(value, JSObject):
        try:
            return value.js_to_string()
        except Exception:
            return None
    if isinstance(value, list):
        return [_plain(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item, depth + 1) for key, item in value.items()}
    if isinstance(value, float) and value != value:
        return None
    return value


def _builtin_parse_int(_this: Any, args: List[Any]) -> Any:
    text = to_string(args[0] if args else "").strip()
    radix = int(to_number(args[1])) if len(args) > 1 else 10
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    digits = ""
    for ch in text:
        try:
            if int(ch, radix) < radix:
                digits += ch
            else:
                break
        except ValueError:
            break
    if not digits:
        return float("nan")
    try:
        return _num_out(float(sign * int(digits, radix)))
    except ValueError:
        return float("nan")


def _builtin_parse_float(_this: Any, args: List[Any]) -> Any:
    text = to_string(args[0] if args else "").strip()
    out = ""
    for index, ch in enumerate(text):
        if ch.isdigit() or (ch in "+-" and index == 0) or (ch == "." and "." not in out):
            out += ch
        elif ch in "eE":
            out += ch
        else:
            break
    try:
        return _num_out(float(out))
    except ValueError:
        return float("nan")


def _builtin_is_nan(_this: Any, args: List[Any]) -> Any:
    value = to_number(args[0] if args else float("nan"))
    return value != value


# ============================================================
# Entry points
# ============================================================

def _interpreter_for(on_console: Optional[Callable[[str, str], None]],
                     max_steps: int, time_budget: float) -> Interpreter:
    return Interpreter(on_console=on_console, max_steps=max_steps,
                       time_budget=time_budget)


def execute_script(source: str, dom_root: Any,
                   trigger_layout_callback: Optional[Callable[[], None]] = None,
                   on_console: Optional[Callable[[str], None]] = None,
                   max_steps: int = 300_000) -> "JSEvaluator":
    """legacy-compatible: run one script against `dom_root`, return the evaluator."""
    from core.js.dom_bridge import DocumentRuntime

    sink = (lambda level, text: on_console(text)) if on_console else None
    runtime = DocumentRuntime(dom_root, on_mutate=trigger_layout_callback,
                              on_console=sink)
    errors = runtime.evaluate_script(source)
    evaluator = JSEvaluator(runtime)
    evaluator.errors.extend(errors)
    return evaluator


def evaluate_event_handler(handler_code: str, dom_root: Any,
                           trigger_layout_callback: Optional[Callable[[], None]] = None,
                           on_console: Optional[Callable[[str], None]] = None,
                           event: Optional[Dict[str, Any]] = None) -> "JSEvaluator":
    from core.js.dom_bridge import DocumentRuntime

    sink = (lambda level, text: on_console(text)) if on_console else None
    runtime = DocumentRuntime(dom_root, on_mutate=trigger_layout_callback,
                              on_console=sink)
    errors = runtime.evaluate_handler(handler_code, event or {})
    evaluator = JSEvaluator(runtime)
    evaluator.errors.extend(errors)
    return evaluator


def evaluate_expression(source: str,
                        on_console: Optional[Callable[[str, str], None]] = None) -> Any:
    """Evaluate one expression with a throwaway interpreter (devtools REPL)."""
    interp = Interpreter(on_console=on_console)
    return interp.evaluate_source(source)


def run_document(dom: Any, base_url: str = "",
                 on_console: Optional[Callable[[str, str], None]] = None,
                 on_mutate: Optional[Callable[[], None]] = None) -> List[str]:
    """Execute every <script> in `dom`. Returns human-readable errors."""
    from core.js.dom_bridge import DocumentRuntime

    runtime = DocumentRuntime(dom, base_url=base_url, on_mutate=on_mutate,
                              on_console=on_console)
    return runtime.run_scripts()


def run_handler(node: Any, code: str, base_url: str = "",
                event: Optional[Dict[str, Any]] = None,
                on_console: Optional[Callable[[str, str], None]] = None) -> List[str]:
    from core.js.dom_bridge import DocumentRuntime

    runtime = DocumentRuntime(node, base_url=base_url, on_console=on_console)
    return runtime.evaluate_handler(code, event or {})


def __getattr__(name: str) -> Any:
    """legacy aliases, resolved lazily to avoid a circular import."""
    if name in ("DOMElementWrapper", "DOMStyleWrapper"):
        from core.js.dom_bridge import Element, StyleProxy

        return Element if name == "DOMElementWrapper" else StyleProxy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def find_node_by_id(root: Any, target_id: str) -> Any:
    if root is None:
        return None
    for node in root.walk():
        if getattr(node, "attributes", {}).get("id") == target_id:
            return node
    return None


class JSEvaluator:
    """Compatibility shim: the legacy object that 'ran a script'."""

    def __init__(self, runtime: Any = None) -> None:
        self.runtime = runtime
        self.errors: List[str] = []
        self.console: List[str] = []

    @property
    def variables(self) -> Dict[str, Any]:
        if self.runtime is None:
            return {}
        return dict(self.runtime.interp.globals.values)
