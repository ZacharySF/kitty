"""The ? help overlay.

The table is built from the live BINDINGS of whatever screen was showing, so it
cannot drift out of date when a key is added or renamed.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from kitty.tui.navigation import Place


class HelpScreen(ModalScreen[None]):
    """Keys for the current screen, then the global keys."""

    BINDINGS = [
        ("escape", "dismiss_help", "Close"),
        ("question_mark", "dismiss_help", "Close"),
        ("q", "dismiss_help", "Close"),
    ]

    def __init__(
        self, rows: list[tuple[str, str, str]], screen_name: str, places: tuple[Place, ...] = (),
    ) -> None:
        super().__init__()
        self.rows = rows
        self.screen_name = screen_name
        self.places = places

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal help-modal"):
            yield Static(f"Keys - {self.screen_name}", classes="modal-title")
            yield DataTable(id="help-table", cursor_type="row", zebra_stripes=True)
            if self.places:
                screens = ", ".join(f"{place.label} {place.key.upper()}" for place in self.places)
                hint = (
                    f"Screens, in workflow order: {screens} (or click the tabs). "
                    "enter on a job opens Job Detail; esc goes back. "
                    "ctrl+p opens the command palette. u undoes the last status change only."
                )
            else:
                hint = "ctrl+p opens the command palette. enter or space switches a module."
            yield Static(hint, classes="modal-hint")

    def on_mount(self) -> None:
        table = self.query_one("#help-table", DataTable)
        table.add_columns("key", "does", "where")
        for key, description, where in self.rows:
            table.add_row(key, description, where)
        table.focus()

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

    @on(DataTable.RowSelected)
    def _close(self) -> None:
        self.dismiss(None)
