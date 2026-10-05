"""Review status proposals (from Gmail): y confirms, n declines.

Like AddJobModal this modal writes to the database itself, because the list and
the decision on each row belong together. Confirming moves the application;
declining leaves it as it is and keeps the proposal in the history.
"""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from applyai.core import applications as applications_core
from applyai.core.gmail.proposals import PendingProposal, pending_proposals
from applyai.tui.helpers import STATUS_COLOUR
from applyai.tui.theme import BONE


class ProposalsModal(ModalScreen[int]):
    """Pending proposals, one per row. Returns how many I decided."""

    DEFAULT_CSS = """
    #proposals-modal { width: 90%; }
    #proposals-table { height: auto; max-height: 20; }
    #proposal-evidence { margin-top: 1; }
    """

    BINDINGS = [
        ("y", "confirm", "Confirm"),
        ("n", "decline", "Decline"),
        ("escape", "close", "Close"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.decided = 0
        self.proposals: dict[int, PendingProposal] = {}

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="proposals-modal"):
            yield Static("Status proposals from Gmail", classes="modal-title")
            yield DataTable(id="proposals-table", cursor_type="row")
            yield Static(id="proposal-evidence")
            yield Static("y confirm (the status moves)   n decline   esc close", classes="modal-hint")

    def on_mount(self) -> None:
        table = self.query_one("#proposals-table", DataTable)
        table.add_column("application", width=44)
        table.add_column("change", width=24)
        table.add_column("date", width=10)
        self.refresh_rows()
        table.focus()

    def refresh_rows(self) -> None:
        """Re-read pending proposals; close once none are left."""
        self.proposals = {p.event_id: p for p in pending_proposals(self.app.conn)}  # type: ignore[attr-defined]
        if not self.proposals:
            self.dismiss(self.decided)
            return
        table = self.query_one("#proposals-table", DataTable)
        previous = table.cursor_row
        table.clear()
        for proposal in self.proposals.values():
            colour = STATUS_COLOUR.get(proposal.to_status, BONE)
            table.add_row(
                f"{escape(proposal.company)} - {escape(proposal.title)}",
                f"{proposal.current_status} -> [{colour}]{proposal.to_status}[/]",
                proposal.occurred_at[:10],
                key=str(proposal.event_id),
            )
        table.move_cursor(row=min(previous, len(self.proposals) - 1))
        self._show_evidence()

    def selected(self) -> PendingProposal | None:
        table = self.query_one("#proposals-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return self.proposals.get(int(key)) if key else None

    def _show_evidence(self) -> None:
        proposal = self.selected()
        text = escape(proposal.evidence or "") if proposal else ""
        self.query_one("#proposal-evidence", Static).update(f"[dim]{text}[/dim]")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_evidence()

    def action_confirm(self) -> None:
        proposal = self.selected()
        if proposal is None:
            return
        applications_core.confirm_proposal(self.app.conn, proposal.event_id)  # type: ignore[attr-defined]
        self.decided += 1
        self.notify(f"{proposal.company}: now {proposal.to_status}.")
        self.refresh_rows()

    def action_decline(self) -> None:
        proposal = self.selected()
        if proposal is None:
            return
        applications_core.reject_proposal(self.app.conn, proposal.event_id)  # type: ignore[attr-defined]
        self.decided += 1
        self.notify(f"{proposal.company}: declined, stays {proposal.current_status}.")
        self.refresh_rows()

    def action_close(self) -> None:
        self.dismiss(self.decided)
