"""Pipeline: the kanban board, one column per status.

Each card shows how many days it has been sitting in its column, which is the
number that tells me when to follow up or write something off. Gmail (Stage 6)
only proposes status changes: `v` reviews them, `G` checks Gmail in a worker
thread with its own database connection.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from applyai.core import pipeline as pipeline_core
from applyai.core.gmail import auth as gmail_auth
from applyai.core.gmail.check import check_gmail
from applyai.core.gmail.proposals import pending_proposals
from applyai.db import connect
from applyai.models import PIPELINE_STATUSES
from applyai.tui.actions import JobActions
from applyai.tui.helpers import STATUS_COLOUR, due_label
from applyai.tui.navigation import ScreenHeader
from applyai.tui.screens.proposals import ProposalsModal
from applyai.tui.theme import BONE, CRIMSON, EMBER, GOLD, MOSS


class PipelineScreen(JobActions, Screen):
    """Applications as a board: applied / OA / interview / offer / rejected / ghosted."""

    BINDINGS = [
        ("enter", "open_detail", "Open"),
        ("s", "change_status", "Status"),
        ("u", "undo_status", "Undo status"),
        ("n", "set_next_action", "Next action"),
        ("o", "open_url", "Open in browser"),
        # priority: the focused DataTable also binds the arrows, and moving
        # between columns is what left/right should do on a board.
        Binding("left", "prev_column", "Column left", priority=True),
        Binding("right", "next_column", "Column right", priority=True),
        ("t", "cycle_track", "Filter by track"),
        ("v", "review_proposals", "Review proposals"),
        ("G", "check_gmail", "Check Gmail"),
        ("r", "refresh", "Refresh"),
    ]
    EXTRA_HELP = (
        ("v", "Gmail proposals: y confirms (the status moves), n declines"),
        ("G", "Read new recruiting mail and propose status changes (read-only)"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.track_filter: str | None = None
        self.column_index = 0
        self.gmail_running = False

    def compose(self) -> ComposeResult:
        yield ScreenHeader("pipeline")
        yield Static(id="board-summary")
        with Horizontal(id="board"):
            for status in PIPELINE_STATUSES:
                with Vertical(classes="board-column", id=f"col-{status}"):
                    yield Static(status, classes="board-column-title", id=f"title-{status}")
                    yield DataTable(
                        classes="board-cards", id=f"cards-{status}",
                        cursor_type="row", show_header=False,
                    )
        yield Footer()

    def on_mount(self) -> None:
        for status in PIPELINE_STATUSES:
            table = self.query_one(f"#cards-{status}", DataTable)
            table.add_column("card", width=26)
            table.add_column("age", width=6)
        self.refresh_data()
        self._focus_column(0)

    def on_screen_resume(self) -> None:
        self.refresh_data()

    # ------------------------------------------------------------- render

    def refresh_data(self) -> None:
        """Re-read the board and repaint every column."""
        board = pipeline_core.board(self.app.conn, track=self.track_filter)  # type: ignore[attr-defined]
        total = sum(len(cards) for cards in board.values())

        for status in PIPELINE_STATUSES:
            cards = board.get(status, [])
            table = self.query_one(f"#cards-{status}", DataTable)
            previous = table.cursor_row
            table.clear()
            for card in cards:
                label = f"{card.company_name}\n[dim]{card.title}[/dim]"
                if card.has_proposals:
                    label += f"\n[{GOLD}]proposal waiting[/]"
                if card.next_action_at:
                    label += f"\n[{EMBER}]next: {due_label(card.next_action_at)}[/]"
                age_colour = CRIMSON if card.days_since_change >= 21 else (
                    GOLD if card.days_since_change >= 7 else MOSS
                )
                table.add_row(
                    label, f"[{age_colour}]{card.days_since_change}d[/]", key=str(card.job_id),
                )
            colour = STATUS_COLOUR.get(status, BONE)
            self.query_one(f"#title-{status}", Static).update(
                f"[{colour}]{status}[/] [dim]{len(cards)}[/dim]"
            )
            if cards:
                table.move_cursor(row=min(previous, len(cards) - 1))

        track_label = self.track_filter or "all tracks"
        waiting = len(pending_proposals(self.app.conn))                          # type: ignore[attr-defined]
        if self.gmail_running:
            proposals_note = "   [$accent]checking Gmail...[/]"
        elif waiting:
            proposals_note = f"   [{GOLD}]{waiting} proposal(s) waiting: v reviews[/]"
        else:
            proposals_note = ""
        self.query_one("#board-summary", Static).update(
            f"[bold]{total}[/bold] applications   track: [bold]{track_label}[/bold]{proposals_note}   "
            "[dim]t cycles tracks, ←/→ changes column. Card age: days since the last change.[/dim]"
        )

    def _focus_column(self, index: int) -> None:
        """Move focus to one column, highlighting its border."""
        self.column_index = index % len(PIPELINE_STATUSES)
        for position, status in enumerate(PIPELINE_STATUSES):
            column = self.query_one(f"#col-{status}")
            column.set_class(position == self.column_index, "focused")
        self.query_one(f"#cards-{PIPELINE_STATUSES[self.column_index]}", DataTable).focus()

    def current_job_id(self) -> int | None:
        """The job behind the selected card."""
        status = PIPELINE_STATUSES[self.column_index]
        table = self.query_one(f"#cards-{status}", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        return int(row_key.value) if row_key.value is not None else None

    # ------------------------------------------------------------ actions

    def action_prev_column(self) -> None:
        self._focus_column(self.column_index - 1)

    def action_next_column(self) -> None:
        self._focus_column(self.column_index + 1)

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_open_detail(self) -> None:
        job_id = self.current_job_id()
        if job_id is not None:
            self.app.open_job(job_id)               # type: ignore[attr-defined]

    def action_cycle_track(self) -> None:
        """Cycle the board through 'all tracks' and each track in tracks.yaml."""
        names = [None, *self.app.config.track_names]        # type: ignore[attr-defined]
        position = names.index(self.track_filter) if self.track_filter in names else 0
        self.track_filter = names[(position + 1) % len(names)]
        self.refresh_data()

    def action_review_proposals(self) -> None:
        """Open the proposals list; the board repaints once it closes."""
        if not pending_proposals(self.app.conn):                               # type: ignore[attr-defined]
            self.notify("No proposals waiting. G checks Gmail.")
            return
        self.app.push_screen(ProposalsModal(), lambda _decided: self.refresh_data())

    def action_check_gmail(self) -> None:
        """Run a Gmail check in a worker thread; it only ever proposes."""
        settings = self.app.workspace.settings                                 # type: ignore[attr-defined]
        if not gmail_auth.setup_state(settings).ready:
            self.notify("Gmail is not set up. In a terminal: applyai gmail status (shows the steps), "
                        "then applyai gmail auth.", severity="warning", timeout=12)
            return
        if self.gmail_running:
            self.notify("Already checking Gmail.", severity="warning")
            return
        self.gmail_running = True
        self.refresh_data()

        def work() -> None:
            conn = connect(settings.db_path)
            try:
                result = check_gmail(conn, settings)
                message = (f"Gmail: {result.fetched} new messages, {result.classified} status mail, "
                           f"{result.proposed} proposed" + ("; v reviews" if result.proposed else ""))
                severity = "information"
                if result.error:
                    message, severity = f"{message}. {result.status}: {result.error}", "error"
            except gmail_auth.GmailSetupError as exc:
                message, severity = str(exc), "warning"
            except Exception as exc:                                      # shown, not swallowed
                message, severity = f"Gmail check failed: {type(exc).__name__}: {exc}", "error"
            finally:
                conn.close()
            self.app.call_from_thread(self._gmail_finished, message, severity)

        self.run_worker(work, thread=True, exclusive=True, name="gmail-check")

    def _gmail_finished(self, message: str, severity: str) -> None:
        self.gmail_running = False
        self.refresh_data()
        self.notify(message, severity=severity, timeout=12)

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self.app.open_job(int(event.row_key.value))     # type: ignore[attr-defined]
