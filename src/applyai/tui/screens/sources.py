"""Sources: every discovery adapter's health, and a key to run one.

A source that failed or was rate limited shows that status in colour with its
error, so it can never be mistaken for "0 jobs". Running a source happens in a
worker thread with its own database connection; the screen stays usable.
"""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from applyai.core import sources as sources_core
from applyai.core.discovery import pipeline
from applyai.tui.helpers import muted, short_date
from applyai.tui.navigation import ScreenHeader
from applyai.tui.theme import CRIMSON, GOLD, MOSS

_COLUMNS = (
    ("source", 16), ("kind", 10), ("on", 3), ("fragile", 7), ("last run", 10),
    ("last success", 12), ("status", 12), ("count", 6), ("last error", 60),
)
_STATUS_COLOUR = {"ok": MOSS, "partial": GOLD, "error": CRIMSON, "rate_limited": CRIMSON}


class SourcesScreen(Screen):
    """Adapter health, from the sources table."""

    BINDINGS = [
        ("enter", "run_source", "Run source"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.running: str | None = None

    def compose(self) -> ComposeResult:
        yield ScreenHeader("sources")
        yield Static(id="sources-line")
        yield DataTable(id="sources-table", cursor_type="row", zebra_stripes=True)
        yield Static(id="source-guidance", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        for name, width in _COLUMNS:
            table.add_column(name, width=width, key=name)
        self.refresh_data()
        table.focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        table.clear()
        for source in sources_core.list_sources(self.app.conn):              # type: ignore[attr-defined]
            status = source.last_status or "never run"
            built = source.name in pipeline.ADAPTERS or source.name == "manual"
            table.add_row(
                source.name if built else muted(source.name),
                source.kind,
                "yes" if source.enabled else "no",
                f"[{GOLD}]fragile[/]" if source.fragile else "",
                short_date(source.last_run_at),
                short_date(source.last_success_at),
                f"[{_STATUS_COLOUR[source.last_status]}]{status}[/]"
                if source.last_status in _STATUS_COLOUR else muted(status),
                "-" if source.last_count is None else str(source.last_count),
                Text(sources_core.safe_error(source.last_error)) if source.last_error else "",
                key=source.name,
            )
        line = (f"[$accent]running {self.running}...[/]" if self.running
                else "[dim]grey rows have no adapter yet[/dim]")
        self.query_one("#sources-line", Static).update(line)
        self._update_guidance()

    def _update_guidance(self) -> None:
        name = self.selected_source()
        source = next((s for s in sources_core.list_sources(self.app.conn)  # type: ignore[attr-defined]
                       if s.name == name), None)
        text = (sources_core.source_warning(source)
                if source and source.last_status in sources_core.PROBLEM_STATUSES else "")
        self.query_one("#source-guidance", Static).update(text)

    @on(DataTable.RowHighlighted, "#sources-table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_guidance()

    def selected_source(self) -> str | None:
        table = self.query_one("#sources-table", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_run_source(self) -> None:
        name = self.selected_source()
        if name is None:
            return
        if name not in pipeline.ADAPTERS:
            self.notify(f"{name} has no adapter: add those jobs by hand (a on the Jobs screen).",
                        severity="warning")
            return
        if self.running:
            self.notify(f"Already running {self.running}.", severity="warning")
            return
        self.running = name
        self.refresh_data()
        workspace = self.app.workspace                                        # type: ignore[attr-defined]

        def work() -> None:
            try:
                result = pipeline.run_discover(workspace.settings, workspace.config, only=[name])
                tally = result.sources.get(name)
                message = (f"{name}: {tally.status}, {tally.new} new, {tally.merged} merged"
                           if tally else f"{name}: nothing ran")
                severity = "information" if tally and tally.status == "ok" else "warning"
            except Exception as exc:                                      # shown, not swallowed
                message, severity = f"{name} failed: {type(exc).__name__}: {sources_core.safe_error(str(exc))}", "error"
            self.app.call_from_thread(self._finished, message, severity)

        self.run_worker(work, thread=True, exclusive=True, name=f"discover-{name}")

    @on(DataTable.RowSelected, "#sources-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        """The table swallows enter, so its row-selected event runs the source."""
        self.action_run_source()

    def _finished(self, message: str, severity: str) -> None:
        self.running = None
        self.refresh_data()
        self.notify(message, severity=severity, timeout=10)
