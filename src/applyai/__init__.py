"""applyai - a personal job-search tracker.

Layout rule: everything under `core/` (plus `config`, `db`, `models`, `normalize`,
`settings`) is pure library code with no UI imports, so it can be driven by the
CLI, the TUI, or later an MCP server.
"""

__version__ = "0.1.0"
