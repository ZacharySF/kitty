"""The command line. A thin layer over `kitty.core`.

Printing and prompting live here; every decision and every write lives in the
core package, so the TUI and a future MCP server behave identically.

Each command group is its own module and registers itself on `kitty.cli.root.app`
when imported. `kitty --help` lists commands in registration order, so the
modules are imported from `_REGISTRATION_ORDER`, not a sortable import list.
"""

from __future__ import annotations

import importlib

from rich.markup import escape

from kitty.cli.common import error_console
from kitty.cli.root import app

#: Top-level commands first (init … mcp), then the command groups (resumes … policy).
_REGISTRATION_ORDER = (
    "onboarding",
    "jobs",
    "apply",
    "triage",
    "reports",
    "resumes",
    "imports",
    "demo",
    "schedule",
    "interfaces",
    "modules",
    "keys",
    "gmail",
    "questions",
    "index",
    "notes",
    "effects",
    "policy",
)
for _name in _REGISTRATION_ORDER:
    importlib.import_module(f"kitty.cli.{_name}")

__all__ = ["app", "main"]


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except PermissionError as exc:
        # Inside a confined rika! Agent tab the workspace's files are closed (kitty.core.confine).
        error_console.print(f"[bold red]Permission denied: {escape(str(exc.filename or exc))}[/bold red]\n"
                            "In a rika! Agent tab, rika!'s files are closed; `kitty notes` and `kitty mcp` "
                            "reach its notes with the tab's KITTY_AGENT_TOKEN.")
        raise SystemExit(1) from None
