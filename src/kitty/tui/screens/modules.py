"""Modules: which built-in modules this workspace uses.

The core screen. It works whether or not any module is enabled, so a core-only
workspace always has somewhere to start. Choices are written to the
build file (config/build.json) at once, but a module only switches on or off the next time
kitty starts: this process keeps the modules it started with, and the table shows
both so the difference is never a surprise.
"""

from __future__ import annotations

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from kitty.config import ConfigError
from kitty.core.bootstrap import configure_modules
from kitty.modules import available_modules, load_modules
from kitty.tui.helpers import muted
from kitty.tui.theme import GOLD, MOSS

_PURPOSE = (
    "Choose which modules rika! uses. enter or space switches one; changes apply the "
    "next time rika! starts. Your data is kept either way."
)


class ModulesHeader(Vertical):
    """The top of the Modules screen, styled like the tabbed header of the other screens."""

    def __init__(self) -> None:
        super().__init__(id="header")

    def compose(self) -> ComposeResult:
        yield Static("[bold $primary] ♜ [/][bold $accent]kitty[/]   "
                     "[bold $foreground on $primary] Modules [/]", id="nav-tabs")
        yield Static(f"[bold $accent]✦ MODULES[/] [italic dim]the foundations[/]   {_PURPOSE}", id="nav-purpose")


class ModulesScreen(Screen):
    """One row per module: running now, and what the next start will use."""

    BINDINGS = [
        ("enter", "toggle_module", "Enable / disable"),
        ("space", "toggle_module", "Enable / disable"),
        ("escape", "back", "Back"),
    ]

    #: Why the table cannot be changed right now (an unreadable build file), else "".
    problem: str = ""

    def compose(self) -> ComposeResult:
        yield ModulesHeader()
        yield DataTable(id="modules-table", cursor_type="row", zebra_stripes=True)
        yield Static(id="modules-note")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#modules-table", DataTable)
        table.add_column("module", width=12, key="module")
        table.add_column("running now", width=13, key="running")
        table.add_column("next start", width=12, key="next")
        table.add_column("what it adds", key="what")
        self.refresh_data()
        table.focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    @property
    def active(self) -> tuple[str, ...]:
        """The modules this process started with."""
        return tuple(self.app.workspace.config.modules)               # type: ignore[attr-defined]

    @property
    def config_dir(self):
        return self.app.workspace.settings.config_dir                 # type: ignore[attr-defined]

    def configured(self) -> tuple[str, ...]:
        """What config/build.json selects now; the running set when it cannot be read."""
        try:
            self.problem = ""
            return load_modules(self.config_dir)
        except ConfigError as exc:
            self.problem = str(exc)
            return self.active

    def refresh_data(self) -> None:
        configured = self.configured()
        table = self.query_one("#modules-table", DataTable)
        keep = table.cursor_row if table.row_count else 0
        table.clear()
        for item in available_modules():
            running = item["id"] in self.active
            pending = item["id"] in configured
            changed = running != pending
            table.add_row(
                f"{item['label']}",
                f"[{MOSS}]enabled[/]" if running else muted("disabled"),
                (f"[{GOLD}]{'enabled' if pending else 'disabled'} *[/]" if changed
                 else ("enabled" if pending else muted("disabled"))),
                item["description"],
                key=item["id"],
            )
        if table.row_count:
            table.move_cursor(row=min(keep, table.row_count - 1))
        if self.problem:
            note = f"[bold red]config/build.json cannot be read, so nothing can be changed here:[/] {escape(self.problem)}"
        elif configured != self.active:
            note = f"[{GOLD}]* Restart rika! to apply the change.[/]"
        else:
            note = muted("No pending change.")
        self.note = note
        self.query_one("#modules-note", Static).update(note)

    def selected_module(self) -> str | None:
        table = self.query_one("#modules-table", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_toggle_module(self) -> None:
        """Flip the highlighted module in config/build.json (applies on restart)."""
        module = self.selected_module()
        configured = self.configured()
        if module is None:
            return
        if self.problem:
            self.refresh_data()
            self.notify("config/build.json cannot be read; fix it first.", severity="error")
            return
        selection = [m for m in configured if m != module] if module in configured else [*configured, module]
        try:
            saved = configure_modules(self.config_dir, selection)
        except (ValueError, OSError) as exc:
            self.notify(f"Not saved: {exc}", severity="error", timeout=10)
            return
        self.refresh_data()
        state = "enabled" if module in saved else "disabled"
        if saved == self.active:
            self.notify(f"{module} {state}: same as what is running now.")
        else:
            self.notify(f"{module} {state} for the next start. Restart rika! to apply it.")

    @on(DataTable.RowSelected, "#modules-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        """The table swallows enter, so its row-selected event toggles the module."""
        self.action_toggle_module()

    def action_back(self) -> None:
        """Back to the home screen when there is one (core-only kitty is already home)."""
        home = self.app.HOME                                          # type: ignore[attr-defined]
        if home != "modules":
            self.app.action_goto(home)                                # type: ignore[attr-defined]
