"""Rika's tools: thin wrappers over core functions with JSON-schema arguments.

Each tool declares its effect:

* ``read``: looks only; no action is recorded;
* ``change``: an in-app change that applies at once and is recorded as an action;
  it is reversible when the tool has an ``undo`` (design Q9), and the action keeps
  the undo payload ``run`` returned (reusing a core undo id where the core has one);
* ``outward``: outward or irreversible (deleting without Trash, settings and
  modules, starting a worker agent, shell commands): the loop asks for approval
  first, with the summary and detail from ``describe``.

Arguments are validated against the tool's schema before anything runs; a
validation failure is reported back to the model and escalates the tier.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

EFFECTS = ("read", "change", "outward")


class ToolError(ValueError):
    """A tool refused or failed; the message is safe to show and to give the model."""


@dataclass
class Context:
    """What a tool runs against: the web application and one open connection."""
    app: Any
    conn: sqlite3.Connection

    @property
    def demo(self) -> bool:
        return bool(self.app.settings.demo)


@dataclass
class Result:
    #: One line for the action feed and the deterministic reply.
    summary: str
    #: What the model sees as the tool result.
    data: dict = field(default_factory=dict)
    #: Private payload `Tool.undo` takes; None when nothing can be undone.
    undo: dict | None = None
    #: An event for the page, e.g. ``{"type": "navigate", "view": "calendar"}``.
    client: dict | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    effect: str
    run: Callable[[Context, dict], Result]
    undo: Callable[[Context, dict], None] | None = None
    #: The feature module that must be active, or None for the core.
    module: str | None = None
    #: (summary, detail) for an approval card; outward tools only.
    describe: Callable[[Context, dict], tuple[str, str]] | None = None

    @property
    def reversible(self) -> bool:
        return self.undo is not None

    def spec(self) -> dict:
        """The OpenAI-style function declaration OpenRouter forwards to the model."""
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


# Schemas ----------------------------------------------------------------------

def string(description: str, *, maximum: int = 500, minimum: int = 1, enum: tuple[str, ...] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "string", "description": description, "minLength": minimum, "maxLength": maximum}
    if enum:
        schema["enum"] = list(enum)
    return schema


def integer(description: str, *, minimum: int = 1, maximum: int = 2**53 - 1) -> dict:
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


def number(description: str, *, minimum: float = -100000, maximum: float = 100000) -> dict:
    return {"type": "number", "description": description, "minimum": minimum, "maximum": maximum}


def boolean(description: str) -> dict:
    return {"type": "boolean", "description": description}


def obj(required: tuple[str, ...] = (), **properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_TYPES = {"object": dict, "string": str, "array": list, "boolean": bool}


def validate(schema: dict, value: Any, where: str = "arguments") -> None:
    """Check `value` against the JSON-schema subset these tools use."""
    kind = schema.get("type")
    if kind == "integer":
        if type(value) is not int:
            raise ToolError(f"{where} must be an integer")
    elif kind == "number":
        if type(value) not in (int, float) or value != value or value in (float("inf"), float("-inf")):
            raise ToolError(f"{where} must be a finite number")
    elif not isinstance(value, _TYPES[kind]) or (kind != "boolean" and isinstance(value, bool)):
        raise ToolError(f"{where} must be a {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"{where} must be one of {', '.join(map(str, schema['enum']))}")
    if kind == "string":
        low, high = schema.get("minLength", 0), schema.get("maxLength", 10_000)
        if "\x00" in value or len(value) > high or (low and len(value.strip()) < low):
            raise ToolError(f"{where} must be {low}–{high} characters")
    if kind in ("integer", "number") and not schema.get("minimum", -float("inf")) <= value <= schema.get("maximum", float("inf")):
        raise ToolError(f"{where} must be between {schema.get('minimum')} and {schema.get('maximum')}")
    if kind == "array":
        if len(value) > schema.get("maxItems", 100):
            raise ToolError(f"{where} has too many items")
        for index, item in enumerate(value):
            validate(schema["items"], item, f"{where}[{index}]")
    if kind == "object":
        properties = schema.get("properties", {})
        unknown = set(value) - set(properties)
        if unknown and schema.get("additionalProperties") is False:
            raise ToolError(f"Unknown {where}: {', '.join(sorted(unknown))}")
        missing = [name for name in schema.get("required", ()) if name not in value]
        if missing:
            raise ToolError(f"Missing {where}: {', '.join(missing)}")
        for name, item in value.items():
            if name in properties:
                validate(properties[name], item, name)


# Registry ---------------------------------------------------------------------

def registry() -> dict[str, Tool]:
    """Every Rika tool by name."""
    from kitty.core.rika import goal_tools, mail_tools, memory_tools, outward, workspace_tools

    tools = [*workspace_tools.TOOLS, *outward.TOOLS, *goal_tools.TOOLS, *mail_tools.TOOLS, *memory_tools.TOOLS]
    found = {tool.name: tool for tool in tools}
    if len(found) != len(tools):
        raise RuntimeError("Duplicate Rika tool name")
    return found


def available(app) -> dict[str, Tool]:
    """The tools of this process's active modules."""
    return {name: tool for name, tool in registry().items() if tool.module is None or tool.module in app.modules}
