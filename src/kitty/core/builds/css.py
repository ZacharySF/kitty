"""Custom CSS and theme token values in a build (Q9): styling only, never code or remote loads.

A build from a stranger may restyle rika!, but its CSS must not load anything (which
could leak that the build is in use, or exfiltrate attribute values), run anything, or
escape the scope rika! wraps it in. The checks work on CSS tokens (CSS Syntax Level 3),
not on text, so escapes such as ``u\\72l(`` are decoded before names are compared:

* no ``url()`` except an inline ``data:image`` (PNG, JPEG, GIF, WebP, AVIF) in base64;
* no function that loads or renders another resource or runs code: ``image()``,
  ``image-set()``, ``cross-fade()``, ``element()``, ``src()``, ``paint()``, ``expression()``;
* no at-rule except ``@media``, ``@supports`` and ``@container`` (so no ``@import``,
  ``@font-face`` or ``@namespace``, and no ``@scope`` that could widen rika!'s own);
* no legacy code-running properties (``behavior``, ``-moz-binding``);
* balanced blocks and brackets, terminated strings and comments: the stylesheet is
  wrapped in ``@scope (#app) { ... }`` by the window, and a stray ``}`` or an open
  comment would otherwise close that scope early.

Problems carry the CSS line and column; nothing is rewritten, so what the user wrote is
exactly what runs once it passes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CSS = 65536
MAX_DEPTH = 32
ALLOWED_AT_RULES = frozenset({"media", "supports", "container"})
FORBIDDEN_FUNCTIONS = frozenset({
    "url", "src", "image", "image-set", "-webkit-image-set", "cross-fade", "-webkit-cross-fade",
    "element", "-moz-element", "expression", "paint",
})
FORBIDDEN_PROPERTIES = frozenset({"behavior", "-moz-binding", "-o-link", "-o-link-source"})
DATA_IMAGE = re.compile(r"data:image/(png|jpeg|gif|webp|avif);base64,[A-Za-z0-9+/=\s]*", re.I)


@dataclass(frozen=True)
class CssProblem:
    message: str
    line: int
    column: int


@dataclass(frozen=True)
class Token:
    kind: str  # ws comment string ident function at hash number delim url open close semicolon colon comma cdx
    value: str
    line: int
    column: int


def _hex(char: str) -> bool:
    return char in "0123456789abcdefABCDEF"


def _name_char(char: str) -> bool:
    return char.isalnum() or char in "-_" or ord(char) >= 0x80


def _starts_name(text: str, i: int) -> bool:
    """Whether an identifier starts at text[i] (CSS Syntax 4.3.9)."""
    def valid_escape(j: int) -> bool:
        return j + 1 < len(text) and text[j] == "\\" and text[j + 1] != "\n"
    if i >= len(text):
        return False
    first = text[i]
    if first == "-":
        second = text[i + 1] if i + 1 < len(text) else ""
        return bool(second) and (second.isalpha() or second in "-_" or ord(second) >= 0x80 or valid_escape(i + 1))
    return first.isalpha() or first == "_" or ord(first) >= 0x80 or valid_escape(i)


class _Tokenizer:
    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.line = 1
        self.column = 1
        self.problems: list[CssProblem] = []

    def advance(self, count: int = 1) -> str:
        chunk = self.text[self.i:self.i + count]
        for char in chunk:
            if char == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
        self.i += len(chunk)
        return chunk

    def peek(self, offset: int = 0) -> str:
        j = self.i + offset
        return self.text[j] if j < len(self.text) else ""

    def problem(self, message: str, line: int, column: int) -> None:
        self.problems.append(CssProblem(message, line, column))

    def escape(self) -> str:
        """Consume an escape after the backslash; return the character it stands for."""
        char = self.peek()
        if not char:
            return "\ufffd"
        if _hex(char):
            digits = ""
            while len(digits) < 6 and _hex(self.peek()):
                digits += self.advance()
            if self.peek() in " \t\n":
                self.advance()
            code = int(digits, 16)
            return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "\ufffd"
        return self.advance()

    def name(self) -> str:
        result = ""
        while self.i < len(self.text):
            char = self.peek()
            if _name_char(char):
                result += self.advance()
            elif char == "\\" and self.peek(1) not in ("", "\n"):
                self.advance()
                result += self.escape()
            else:
                break
        return result

    def string(self, quote: str, line: int, column: int) -> Token:
        self.advance()
        value = ""
        while True:
            char = self.peek()
            if not char:
                self.problem("unterminated string", line, column)
                return Token("string", value, line, column)
            if char == quote:
                self.advance()
                return Token("string", value, line, column)
            if char == "\n":
                self.problem("unterminated string (a line break inside quotes)", line, column)
                return Token("string", value, line, column)
            if char == "\\":
                self.advance()
                if self.peek() == "\n":
                    self.advance()
                else:
                    value += self.escape()
                continue
            value += self.advance()

    def url(self, line: int, column: int) -> Token:
        """An unquoted url( token; the caller consumed ``url(``."""
        while self.peek() in " \t\n" and self.peek():
            self.advance()
        value = ""
        while True:
            char = self.peek()
            if not char:
                self.problem("unterminated url(", line, column)
                break
            if char == ")":
                self.advance()
                break
            if char == "\\" and self.peek(1) not in ("", "\n"):
                self.advance()
                value += self.escape()
                continue
            value += self.advance()
        return Token("url", value.strip(), line, column)

    def number(self, line: int, column: int) -> Token:
        start = self.i
        if self.peek() in "+-":
            self.advance()
        while self.peek().isdigit():
            self.advance()
        if self.peek() == "." and self.peek(1).isdigit():
            self.advance()
            while self.peek().isdigit():
                self.advance()
        if self.peek() in "eE" and (self.peek(1).isdigit() or (self.peek(1) in "+-" and self.peek(2).isdigit())):
            self.advance(2)
            while self.peek().isdigit():
                self.advance()
        value = self.text[start:self.i]
        if _starts_name(self.text, self.i):
            value += self.name()
        elif self.peek() == "%":
            value += self.advance()
        return Token("number", value, line, column)

    def tokens(self) -> list[Token]:
        out: list[Token] = []
        text = self.text
        while self.i < len(text):
            line, column = self.line, self.column
            char = self.peek()
            if char in " \t\n\r\f":
                while self.peek() and self.peek() in " \t\n\r\f":
                    self.advance()
                out.append(Token("ws", " ", line, column))
            elif char == "/" and self.peek(1) == "*":
                end = text.find("*/", self.i + 2)
                if end < 0:
                    self.problem("unterminated comment (/* without */)", line, column)
                    self.advance(len(text) - self.i)
                else:
                    self.advance(end + 2 - self.i)
                out.append(Token("comment", "", line, column))
            elif char in "\"'":
                out.append(self.string(char, line, column))
            elif char == "#":
                self.advance()
                out.append(Token("hash", self.name(), line, column))
            elif char == "@":
                self.advance()
                if _starts_name(text, self.i):
                    out.append(Token("at", self.name().lower(), line, column))
                else:
                    out.append(Token("delim", "@", line, column))
            elif char.isdigit() or (char in "+-." and (self.peek(1).isdigit() or (self.peek(1) == "." and self.peek(2).isdigit()))):
                out.append(self.number(line, column))
            elif char == "<" and text.startswith("<!--", self.i):
                self.advance(4)
                out.append(Token("cdx", "<!--", line, column))
            elif char == "-" and text.startswith("-->", self.i):
                self.advance(3)
                out.append(Token("cdx", "-->", line, column))
            elif _starts_name(text, self.i):
                name = self.name()
                if self.peek() == "(":
                    self.advance()
                    lowered = name.lower()
                    if lowered == "url":
                        # url( followed by a quote is a function holding a string token.
                        while self.peek() in " \t\n" and self.peek():
                            self.advance()
                        if self.peek() in "\"'":
                            out.append(Token("function", "url", line, column))
                        else:
                            out.append(self.url(line, column))
                    else:
                        out.append(Token("function", lowered, line, column))
                else:
                    out.append(Token("ident", name, line, column))
            elif char in "{[(":
                self.advance()
                out.append(Token("open", char, line, column))
            elif char in "}])":
                self.advance()
                out.append(Token("close", char, line, column))
            elif char == ";":
                self.advance()
                out.append(Token("semicolon", ";", line, column))
            elif char == ":":
                self.advance()
                out.append(Token("colon", ":", line, column))
            elif char == ",":
                self.advance()
                out.append(Token("comma", ",", line, column))
            elif char == "\\" and self.peek(1) == "\n":
                self.problem("invalid escape (backslash before a line break)", line, column)
                self.advance()
            elif char == "\x00":
                self.problem("NUL characters are not allowed", line, column)
                self.advance()
            else:
                out.append(Token("delim", self.advance(), line, column))
        return out


def _check(tokens: list[Token], problems: list[CssProblem], *, value: bool) -> None:
    """The policy over a token stream; `value` is a single property value (theme token)."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    stack: list[Token] = []
    significant = [token for token in tokens if token.kind not in ("ws", "comment")]
    for index, token in enumerate(significant):
        following = significant[index + 1] if index + 1 < len(significant) else None
        if token.kind == "url":
            if not DATA_IMAGE.fullmatch(token.value):
                problems.append(CssProblem(
                    "url() may only hold an inline data:image (PNG, JPEG, GIF, WebP or AVIF, base64); "
                    "remote and local addresses are not allowed", token.line, token.column))
        elif token.kind == "function":
            if token.value == "url":
                inner = following.value if following is not None and following.kind == "string" else ""
                if not DATA_IMAGE.fullmatch(inner):
                    problems.append(CssProblem(
                        "url() may only hold an inline data:image (PNG, JPEG, GIF, WebP or AVIF, base64); "
                        "remote and local addresses are not allowed", token.line, token.column))
            elif token.value in FORBIDDEN_FUNCTIONS:
                problems.append(CssProblem(f"{token.value}() can load or run outside resources and is not allowed",
                                           token.line, token.column))
            stack.append(Token("open", "(", token.line, token.column))
            if len(stack) > MAX_DEPTH:
                problems.append(CssProblem("nested too deeply", token.line, token.column))
                return
        elif token.kind == "at":
            if value:
                problems.append(CssProblem("at-rules are not allowed in a token value", token.line, token.column))
            elif token.value == "import":
                problems.append(CssProblem("@import is not allowed: a build cannot load other stylesheets",
                                           token.line, token.column))
            elif token.value not in ALLOWED_AT_RULES:
                problems.append(CssProblem(f"@{token.value} is not allowed; use @media, @supports or @container",
                                           token.line, token.column))
        elif token.kind == "ident" and token.value.lower() in FORBIDDEN_PROPERTIES and following is not None \
                and following.kind == "colon":
            problems.append(CssProblem(f"the {token.value} property is not allowed", token.line, token.column))
        elif token.kind == "cdx":
            problems.append(CssProblem("HTML comment markers are not allowed", token.line, token.column))
        elif token.kind == "open":
            if value and token.value == "{":
                problems.append(CssProblem("braces are not allowed in a token value", token.line, token.column))
            stack.append(token)
            if len(stack) > MAX_DEPTH:
                problems.append(CssProblem("nested too deeply", token.line, token.column))
                return
        elif token.kind == "close":
            if value and token.value == "}":
                problems.append(CssProblem("braces are not allowed in a token value", token.line, token.column))
                return
            if not stack or pairs[stack[-1].value] != token.value:
                problems.append(CssProblem(f"unexpected '{token.value}'", token.line, token.column))
                return
            stack.pop()
        elif value and token.kind == "semicolon":
            problems.append(CssProblem("semicolons are not allowed in a token value", token.line, token.column))
        elif value and token.kind == "delim" and token.value == "!":
            problems.append(CssProblem("!important is not allowed in a token value", token.line, token.column))
    for opened in stack:
        problems.append(CssProblem(f"'{opened.value}' is never closed", opened.line, opened.column))


def check_css(text: str) -> list[CssProblem]:
    """Problems with a custom stylesheet; empty when it may be applied as written."""
    if len(text) > MAX_CSS:
        return [CssProblem(f"custom CSS is limited to {MAX_CSS} characters", 1, 1)]
    tokenizer = _Tokenizer(text)
    tokens = tokenizer.tokens()
    problems = list(tokenizer.problems)
    _check(tokens, problems, value=False)
    return sorted(problems, key=lambda item: (item.line, item.column))


def check_value(text: str) -> list[CssProblem]:
    """Problems with one theme token's CSS value (a plain value: no blocks, rules or loads)."""
    tokenizer = _Tokenizer(text)
    tokens = tokenizer.tokens()
    problems = list(tokenizer.problems)
    _check(tokens, problems, value=True)
    if not text.strip():
        problems.append(CssProblem("a token value cannot be empty", 1, 1))
    return sorted(problems, key=lambda item: (item.line, item.column))
