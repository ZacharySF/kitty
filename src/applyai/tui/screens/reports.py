"""Reports: which resumes, tracks, sources and tiers hear back, and the export.

Response rate = OA, interview, offer or rejection over applications; OA+ =
OA, interview or offer. `e` writes the CSV export and `j` the JSON export to
data/export/, the same files as `applyai export`.
"""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from applyai.core import export as export_core
from applyai.core import stats as stats_core
from applyai.core.stats import ResponseRate
from applyai.dates import now_iso
from applyai.tui.helpers import bar, muted, panel
from applyai.tui.navigation import ScreenHeader, key_of

#: Two columns of panels: (left, right) groupings from stats.REPORT_GROUPS.
_LAYOUT = (("resume", "track"), ("source", "tier"))


class ReportsScreen(Screen):
    """Response rates by resume, track, source and tier, plus export keys."""

    DEFAULT_CSS = """
    #reports-line { padding: 0 2; color: $text-muted; }
    #reports-body { height: 1fr; }
    """

    BINDINGS = [
        ("e", "export_csv", "Export CSV"),
        ("j", "export_json", "Export JSON"),
        ("r", "refresh", "Refresh"),
    ]
    EXTRA_HELP = (
        ("e / j", "Write every job, application and status change to data/export/ (CSV or JSON)"),
    )

    def compose(self) -> ComposeResult:
        yield ScreenHeader("reports")
        yield Static(id="reports-line")
        with Horizontal(id="reports-body"):
            for column in _LAYOUT:
                with VerticalScroll(classes="dash-column"):
                    for name in column:
                        with panel(f"By {name}"):
                            yield Static(id=f"report-{name}")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        sections = stats_core.response_report(self.app.conn)                 # type: ignore[attr-defined]
        total = sum(rate.applications for rate in sections["track"])
        self.query_one("#reports-line", Static).update(
            f"{total} application(s). heard back = OA, interview, offer or rejection; "
            f"OA+ = OA or further. e exports CSV, j JSON."
            if total else
            f"No applications yet: mark jobs applied on the Apply tab ({key_of('apply')}) and this fills in."
        )
        for name, rates in sections.items():
            self.query_one(f"#report-{name}", Static).update(self._table(rates))

    @staticmethod
    def _table(rates: list[ResponseRate]) -> str:
        """One grouping as aligned text rows: key, applied, heard back, OA+, offers."""
        if not rates:
            return muted("nothing yet")
        width = max(12, *(len(rate.key) for rate in rates))
        lines = [muted(f"{'':<{width}}  applied  heard back       OA+          offers")]
        for rate in rates:
            lines.append(
                f"{escape(rate.key):<{width}}  {rate.applications:>7}  "
                f"{rate.responses:>3} {rate.response_rate:>4.0%} {bar(rate.response_rate, 6)}  "
                f"{rate.advanced:>3} {rate.advance_rate:>4.0%}  {rate.offers:>6}"
            )
        return "\n".join(lines)

    def _export(self, fmt: str) -> None:
        workspace = self.app.workspace                                       # type: ignore[attr-defined]
        base = workspace.settings.data_dir / "export"
        stamp = now_iso()[:10]
        if fmt == "csv":
            result = export_core.export_csv(workspace.conn, base / stamp)
        else:
            result = export_core.export_json(workspace.conn, base / f"applyai-{stamp}.json")
        counts = ", ".join(f"{name} {count}" for name, count in result.counts.items())
        self.notify(f"Wrote {', '.join(str(p) for p in result.paths)} ({counts})", timeout=10)

    def action_export_csv(self) -> None:
        self._export("csv")

    def action_export_json(self) -> None:
        self._export("json")
