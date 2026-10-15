"""Finder: firms the Stage 5 finder proposes for the watchlist, to approve or reject.

Approving appends the firm to config/watchlist.yaml and the companies table,
then reloads the config so discovery polls its board from the next run.
Rejecting is permanent: the firm is never proposed again. Running the finder
happens in a worker thread with its own database connection; the screen stays
usable meanwhile.
"""

from __future__ import annotations

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from kitty.config import ConfigError
from kitty.core import finder as finder_core
from kitty.tui.helpers import muted, panel, short_date
from kitty.tui.navigation import ScreenHeader
from kitty.tui.theme import CRIMSON, GOLD, MOSS

_COLUMNS = (("firm", 26), ("area", 18), ("score", 5), ("board", 26), ("reason", 90))
_STATUS_COLOUR = {"ok": MOSS, "partial": GOLD, "error": CRIMSON, "rate_limited": CRIMSON}


class FinderScreen(Screen):
    """Pending firm candidates, best score first."""

    DEFAULT_CSS = """
    #finder-line { padding: 0 2; color: $text-muted; }
    #finder-evidence-panel { height: 10; }
    """

    BINDINGS = [
        ("a", "approve", "Approve"),
        ("enter", "approve", "Approve"),
        ("r", "reject", "Reject"),
        ("R", "run_finder", "Run finder"),
        ("u", "refresh", "Refresh"),
    ]
    EXTRA_HELP = (
        ("a / enter", "Approve: append the firm to config/watchlist.yaml and the companies table"),
        ("r", "Reject: the firm is never proposed again"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.running = False

    def compose(self) -> ComposeResult:
        yield ScreenHeader("finder")
        yield Static(id="finder-line")
        yield DataTable(id="finder-table", cursor_type="row", zebra_stripes=True)
        box = panel("why it was proposed")
        box.id = "finder-evidence-panel"
        with box:
            yield Static(id="finder-evidence")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#finder-table", DataTable)
        for name, width in _COLUMNS:
            table.add_column(name, width=width, key=name)
        self.refresh_data()
        table.focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    # ------------------------------------------------------------ reading

    def refresh_data(self) -> None:
        conn = self.app.conn                                                  # type: ignore[attr-defined]
        self.candidates = {c.id: c for c in finder_core.list_candidates(conn)}
        table = self.query_one("#finder-table", DataTable)
        table.clear()
        for candidate in self.candidates.values():
            board = (f"{candidate.ats_kind}:{candidate.ats_slug}" if candidate.ats_kind
                     else muted("none found"))
            table.add_row(
                escape(candidate.display_name),
                candidate.area or "-",
                f"{candidate.score:g}" if candidate.score is not None else "-",
                board,
                escape(candidate.reason or ""),
                key=str(candidate.id),
            )
        self.query_one("#finder-line", Static).update(self._summary_line(conn))
        self._show_evidence()

    def _summary_line(self, conn) -> str:
        """Counts by decision, then the last run's date and any source that did not answer cleanly."""
        counts = finder_core.decision_counts(conn)
        line = (f"[bold]{counts['pending']}[/bold] pending   [{MOSS}]{counts['approved']} approved[/]   "
                f"{muted(str(counts['rejected']) + ' rejected')}   ")
        if self.running:
            return line + "[$accent]finder running...[/]"
        last = finder_core.last_run(conn)
        if last is None:
            return line + muted("never run: press R")
        started, stats = last
        problems = [f"[{_STATUS_COLOUR.get(report['status'], CRIMSON)}]{name} {report['status']}[/]"
                    for name, report in (stats.get("adapters") or {}).items() if report["status"] != "ok"]
        return (line + f"last run {short_date(started)}: {stats.get('proposed', 0)} proposed"
                + (f"   {'  '.join(problems)}" if problems else ""))

    def selected(self) -> finder_core.Candidate | None:
        table = self.query_one("#finder-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return self.candidates.get(int(key)) if key else None

    def _show_evidence(self) -> None:
        candidate = self.selected()
        widget = self.query_one("#finder-evidence", Static)
        if candidate is None:
            widget.update(muted("Nothing pending. R runs the finder (it proposes at most 15 firms)."))
            return
        lines = [f"[bold]{escape(candidate.display_name)}[/bold]  "
                 + (escape(candidate.website) if candidate.website else muted("no website known")),
                 escape(candidate.reason or "")]
        lines += [muted(escape(line)) for line in candidate.evidence]
        widget.update("\n".join(lines))

    @on(DataTable.RowHighlighted, "#finder-table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_evidence()

    @on(DataTable.RowSelected, "#finder-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        """The table swallows enter, so its row-selected event approves."""
        self.action_approve()

    # ------------------------------------------------------------ actions

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_approve(self) -> None:
        candidate = self.selected()
        if candidate is None:
            return
        workspace = self.app.workspace                                        # type: ignore[attr-defined]
        try:
            finder_core.approve(workspace.conn, workspace.settings, workspace.config, candidate.id)
        except (ValueError, ConfigError) as exc:
            self.notify(f"Not approved: {exc}", severity="error", timeout=10)
            return
        self.notify(f"{candidate.display_name} joined the watchlist.")
        self.app.action_reload_config()                                       # type: ignore[attr-defined]
        self.refresh_data()

    def action_reject(self) -> None:
        candidate = self.selected()
        if candidate is None:
            return
        try:
            finder_core.reject(self.app.conn, candidate.id)                   # type: ignore[attr-defined]
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Rejected {candidate.display_name}; it will not be proposed again.")
        self.refresh_data()

    def action_run_finder(self) -> None:
        if self.running:
            self.notify("The finder is already running.", severity="warning")
            return
        self.running = True
        self.refresh_data()
        workspace = self.app.workspace                                        # type: ignore[attr-defined]

        def work() -> None:
            try:
                result = finder_core.run_finder(workspace.settings, workspace.config)
                trouble = [f"{name} {report.status}" for name, report in result.adapters.items()
                           if report.status != "ok"]
                message = f"Finder proposed {len(result.proposed)} firm(s)" + (
                    f"; {', '.join(trouble)}" if trouble else "")
                severity = "warning" if trouble else "information"
            except Exception as exc:                                      # shown, not swallowed
                message, severity = f"Finder failed: {type(exc).__name__}: {exc}", "error"
            self.app.call_from_thread(self._finished, message, severity)

        self.run_worker(work, thread=True, exclusive=True, name="finder")

    def _finished(self, message: str, severity: str) -> None:
        self.running = False
        self.refresh_data()
        self.notify(message, severity=severity, timeout=10)
