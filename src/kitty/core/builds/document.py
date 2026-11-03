"""Reading a build file: JSON with line numbers, the published schema, and rika!'s own checks.

``check(text)`` never raises for bad input: it returns every problem with the line and
column of the offending value (or key), so the build editor can mark the line and an
external editor's save can be reported the same way. Errors make the build unusable
(the last good one stays active); warnings (an unknown key, an unknown theme) do not.

``normalize`` turns a valid document into the complete build rika! applies: every known
key present, defaults filled in, unknown keys dropped, CSS as one string.
"""
from __future__ import annotations

import bisect
import copy
import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from importlib import resources
from typing import Any

from kitty.core.builds import css as css_check

SCHEMA_VERSION = 1
MAX_BYTES = 256 * 1024
#: The roles rika!'s themes (web/src/lib/themes/catalog.ts) define; their tokens must be hex colours.
COLOR_TOKENS = (
    "void", "panel", "inset", "raised", "line", "lineHot", "ink", "mute", "dim", "structure",
    "signal", "go", "reference", "caution", "alternate", "danger",
    "graph1", "graph2", "graph3", "graph4", "graph5", "graph6", "shadow",
)
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")

Path = tuple[str | int, ...]


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a build file, located for the editor."""

    message: str
    line: int
    column: int
    path: str = ""
    severity: str = "error"

    def as_dict(self) -> dict[str, Any]:
        return {"message": self.message, "line": self.line, "column": self.column,
                "path": self.path, "severity": self.severity}

    def __str__(self) -> str:
        where = f" ({self.path})" if self.path else ""
        return f"line {self.line}, column {self.column}{where}: {self.message}"


@dataclass
class Checked:
    """The outcome of reading a build file."""

    #: The parsed document as written (None when it is not JSON).
    raw: dict[str, Any] | None = None
    #: The complete build to apply (None when there is any error).
    build: dict[str, Any] | None = None
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.build is not None

    @property
    def errors(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.severity == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.severity == "warning"]


class BuildInvalid(ValueError):
    """A build that cannot be used; carries every located problem."""

    def __init__(self, problems: list[Problem]):
        self.problems = problems
        first = next((problem for problem in problems if problem.severity == "error"), problems[0])
        more = len([problem for problem in problems if problem.severity == "error"]) - 1
        super().__init__(f"{first}" + (f" (and {more} more)" if more > 0 else ""))


@cache
def schema() -> dict[str, Any]:
    """The published JSON Schema (kitty-build.schema.json next to this module)."""
    return json.loads(resources.files(__package__).joinpath("kitty-build.schema.json").read_text(encoding="utf-8"))


@cache
def _validator():
    from jsonschema import Draft202012Validator

    return Draft202012Validator(schema())


def _defaults(node: dict[str, Any]) -> Any:
    if "default" in node:
        return copy.deepcopy(node["default"])
    if node.get("type") == "object" and "properties" in node:
        return {key: _defaults(child) for key, child in node["properties"].items()
                if key != "$schema" and ("default" in child or "properties" in child or "const" in child)}
    if "const" in node:
        return node["const"]
    return None


def defaults() -> dict[str, Any]:
    """The complete default build (what an empty ``{"schema": 1}`` means)."""
    return _defaults(schema())


def theme_ids() -> tuple[str, ...]:
    return tuple(schema()["properties"]["theme"]["properties"]["base"]["examples"])


class _Positions:
    """Line and column of every value (and object key) in a JSON text, by path."""

    def __init__(self, text: str):
        self.text = text
        self.starts = [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]
        self.values: dict[Path, int] = {}
        self.keys: dict[Path, int] = {}
        self.problems: list[Problem] = []

    def where(self, offset: int) -> tuple[int, int]:
        line = bisect.bisect_right(self.starts, offset)
        return line, offset - self.starts[line - 1] + 1

    def of(self, path: Path, *, key: bool = False) -> tuple[int, int]:
        """The position of a path's key (when asked and known) or value, else its nearest parent."""
        while True:
            if key and path in self.keys:
                return self.where(self.keys[path])
            if path in self.values:
                return self.where(self.values[path])
            if not path:
                return 1, 1
            path = path[:-1]
            key = False

    def scan(self) -> None:
        text, n = self.text, len(self.text)
        whitespace = " \t\n\r"
        i = 0

        def skip(i: int) -> int:
            while i < n and text[i] in whitespace:
                i += 1
            return i

        # An explicit stack keeps deep (valid) nesting from exhausting Python's recursion.
        def value(i: int, path: Path) -> int:
            stack: list[tuple[str, Path, set[str], int]] = []
            while True:
                i = skip(i)
                self.values[path] = i
                char = text[i]
                if char == "{":
                    i = skip(i + 1)
                    if text[i] == "}":
                        i += 1
                    else:
                        stack.append(("object", path, set(), 0))
                        key, i = self._key(i, path, stack[-1][2])
                        path = path + (key,)
                        continue
                elif char == "[":
                    i = skip(i + 1)
                    if text[i] == "]":
                        i += 1
                    else:
                        stack.append(("array", path, set(), 0))
                        path = path + (0,)
                        continue
                elif char == '"':
                    _value, i = json.decoder.scanstring(text, i + 1)
                else:
                    start = i
                    while i < n and text[i] not in ",]} \t\n\r":
                        i += 1
                    literal = text[start:i]
                    if literal in ("NaN", "Infinity", "-Infinity"):
                        line, column = self.where(start)
                        self.problems.append(Problem(f"{literal} is not allowed in JSON", line, column,
                                                     _dotted(path)))
                # Close finished containers; open the next member of the innermost one.
                while stack:
                    i = skip(i)
                    kind, parent, seen, index = stack[-1]
                    if text[i] == ",":
                        i = skip(i + 1)
                        if kind == "object":
                            key, i = self._key(i, parent, seen)
                            path = parent + (key,)
                        else:
                            stack[-1] = (kind, parent, seen, index + 1)
                            path = parent + (index + 1,)
                        break
                    i += 1  # the closing bracket
                    stack.pop()
                    path = parent
                else:
                    return i

        value(skip(i), ())

    def _key(self, i: int, parent: Path, seen: set[str]) -> tuple[str, int]:
        start = i
        key, i = json.decoder.scanstring(self.text, i + 1)
        if key in seen:
            line, column = self.where(start)
            self.problems.append(Problem(f"duplicate key '{key}'", line, column, _dotted(parent + (key,))))
        seen.add(key)
        self.keys[parent + (key,)] = start
        while self.text[i] in " \t\n\r":
            i += 1
        return key, i + 1  # past the colon


def _dotted(path: Path) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _schema_problem(error, positions: _Positions) -> list[Problem]:
    path: Path = tuple(error.absolute_path)
    where = _dotted(path) or "build"
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        allowed = set(error.schema.get("properties", {}))
        patterns = error.schema.get("patternProperties", {})
        problems = []
        for key in error.instance:
            if key in allowed or any(re.search(pattern, key) for pattern in patterns):
                continue
            line, column = positions.of(path + (key,), key=True)
            problems.append(Problem(f"unknown key '{key}' is ignored", line, column, _dotted(path + (key,)),
                                    "warning"))
        return problems
    line, column = positions.of(path)
    if path == ("schema",) or (not path and error.validator == "required"):
        version = error.instance if path else None
        if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
            message = (f"this build uses build format {version}, made by a newer rika!; "
                       f"this rika! reads format {SCHEMA_VERSION}. Update rika! to use it")
        else:
            message = f'a build starts with "schema": {SCHEMA_VERSION}'
        return [Problem(message, line, column, "schema")]
    if path and path[-1] == "css" and error.validator == "anyOf":
        return [Problem("css must be a string or a list of strings (lines)", line, column, where)]
    if error.validator == "additionalProperties":
        return [Problem(f"{where} must be an object", line, column, where)]
    return [Problem(f"{where}: {error.message}", line, column, where)]


def _semantic(data: dict[str, Any], positions: _Positions) -> list[Problem]:
    """rika!'s checks beyond the schema: theme names, token values and the CSS policy."""
    problems: list[Problem] = []
    theme = data.get("theme")
    if not isinstance(theme, dict):
        return problems
    base = theme.get("base")
    if isinstance(base, str) and base not in theme_ids():
        line, column = positions.of(("theme", "base"))
        problems.append(Problem(f"theme '{base}' is not built into this rika!; using '{defaults()['theme']['base']}'",
                                line, column, "theme.base", "warning"))
    tokens = theme.get("tokens")
    if isinstance(tokens, dict):
        for name, value in tokens.items():
            if not isinstance(value, str):
                continue
            line, column = positions.of(("theme", "tokens", name))
            if name in COLOR_TOKENS:
                if not _HEX.fullmatch(value):
                    problems.append(Problem(f"theme.tokens.{name} must be a hex colour such as #1c1c1c",
                                            line, column, f"theme.tokens.{name}"))
            else:
                for issue in css_check.check_value(value):
                    problems.append(Problem(f"theme.tokens.{name}: {issue.message}", line, column,
                                            f"theme.tokens.{name}"))
    stylesheet = theme.get("css")
    if isinstance(stylesheet, list) and all(isinstance(item, str) for item in stylesheet):
        for issue in css_check.check_css("\n".join(stylesheet)):
            # One array item per CSS line: point at that item.
            line, column = positions.of(("theme", "css", issue.line - 1))
            problems.append(Problem(f"CSS: {issue.message}", line, column + issue.column,
                                    f"theme.css[{issue.line - 1}]"))
    elif isinstance(stylesheet, str):
        for issue in css_check.check_css(stylesheet):
            line, column = positions.of(("theme", "css"))
            problems.append(Problem(f"CSS line {issue.line}, column {issue.column}: {issue.message}",
                                    line, column, "theme.css"))
    return problems


def check(text: str) -> Checked:
    """Parse, validate and normalize a build file's text."""
    if len(text.encode("utf-8", "surrogatepass")) > MAX_BYTES:
        return Checked(problems=[Problem(f"a build file is limited to {MAX_BYTES // 1024} KB", 1, 1)])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return Checked(problems=[Problem(f"not valid JSON: {exc.msg}", exc.lineno, exc.colno)])
    except RecursionError:
        return Checked(problems=[Problem("nested too deeply", 1, 1)])
    positions = _Positions(text)
    positions.scan()
    problems = list(positions.problems)
    if not isinstance(data, dict):
        return Checked(problems=[Problem("a build is a JSON object: { \"schema\": 1, ... }", 1, 1)])
    for error in sorted(_validator().iter_errors(data), key=lambda item: list(map(str, item.absolute_path))):
        problems.extend(_schema_problem(error, positions))
    problems.extend(_semantic(data, positions))
    problems.sort(key=lambda item: (item.line, item.column))
    if any(problem.severity == "error" for problem in problems):
        return Checked(raw=data, problems=problems)
    return Checked(raw=data, build=normalize(data), problems=problems)


def _merge(base: Any, over: Any) -> Any:
    """Known keys of `base` (the defaults), each taken from `over` when given there."""
    if isinstance(base, dict) and isinstance(over, dict) and base:
        return {key: _merge(value, over[key]) if key in over else value for key, value in base.items()}
    return copy.deepcopy(over)


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """The complete build a valid document describes (defaults filled in, unknown keys dropped)."""
    build = _merge(defaults(), data)
    build["schema"] = SCHEMA_VERSION
    order = schema()["properties"]["modules"]["items"]["enum"]
    build["modules"] = [module for module in order if module in build["modules"]]
    theme = build["theme"]
    if isinstance(theme["css"], list):
        theme["css"] = "\n".join(theme["css"])
    if theme["base"] not in theme_ids():
        theme["base"] = defaults()["theme"]["base"]
    theme["tokens"] = dict(theme["tokens"])
    budget = Decimal(str(build["settings"]["ai"]["daily_budget_usd"])).quantize(Decimal("0.01"), ROUND_HALF_UP)
    build["settings"]["ai"]["daily_budget_usd"] = float(budget)
    return build


def require(text: str) -> dict[str, Any]:
    """The normalized build, or BuildInvalid with every problem."""
    checked = check(text)
    if not checked.ok:
        raise BuildInvalid(checked.problems)
    return checked.build  # type: ignore[return-value]


def dump(data: dict[str, Any]) -> str:
    """The canonical text rika! writes: two-space JSON, keys in the order given, final newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
