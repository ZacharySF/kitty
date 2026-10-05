"""Track-level resume statistics and local, explicitly reviewed candidate versions.

Generation and counting run in separate worker connections. Missing qualifications
are review items, never invented facts; only explicit promotion changes a default.
"""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from applyai.core import resume_coach, resume_versions
from applyai.db import connect
from applyai.tui.helpers import bar, muted, panel
from applyai.tui.screens.modals import PickModal, TextPromptModal
from applyai.tui.screens.resume_review import ResumeReviewModal
from applyai.tui.navigation import ScreenHeader
from applyai.tui.theme import CRIMSON, EMBER, GOLD, MOSS


class ResumeScreen(Screen):
    """Per track: terms postings ask for that the resume lacks, and the ones it has."""

    BINDINGS = [
        ("t", "next_track", "Next track"),
        ("T", "prev_track", "Previous track"),
        ("enter", "open_example", "Open example posting"),
        ("r", "refresh", "Recount"),
        ("g", "generate", "Generate candidate"),
        ("v", "versions", "Versions / review"),
        ("b", "rollback", "Rollback default"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.track_index = 0
        self.reports: dict[str, resume_coach.TrackReport] = {}
        self.version_busy = False

    def compose(self) -> ComposeResult:
        yield ScreenHeader("resume")
        yield Static(id="resume-line")
        with Vertical(id="resume-body"):
            with panel("Postings ask for this; your resume doesn't mention it"):
                yield DataTable(id="missing-table", cursor_type="row", zebra_stripes=True)
            with panel("Already on your resume"):
                yield DataTable(id="covered-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        missing = self.query_one("#missing-table", DataTable)
        missing.add_column("term", width=22)
        missing.add_column("postings", width=26)
        missing.add_column("for example", width=90)
        covered = self.query_one("#covered-table", DataTable)
        covered.add_column("term", width=22)
        covered.add_column("postings", width=26)
        covered.add_column("your resume line", width=90)
        self.action_refresh()
        missing.focus()

    @property
    def track_names(self) -> list[str]:
        return self.app.config.track_names                                    # type: ignore[attr-defined]

    @property
    def track(self) -> str:
        return self.track_names[self.track_index % len(self.track_names)] if self.track_names else ""

    # ------------------------------------------------------------- counting

    def action_refresh(self) -> None:
        """Recount every track in a worker thread (PDF text + every posting)."""
        self.query_one("#resume-line", Static).update(f"[{EMBER}]counting terms across postings...[/]")
        workspace = self.app.workspace                                        # type: ignore[attr-defined]

        def work() -> None:
            try:
                conn = connect(workspace.settings.db_path)
                try:
                    reports = resume_coach.coach(conn, workspace.config, workspace.settings)
                finally:
                    conn.close()
            except Exception as exc:
                self.app.call_from_thread(self._count_failed, str(exc))
            else:
                self.app.call_from_thread(self._counted, reports)

        self.run_worker(work, thread=True, exclusive=True, group="resume-coach", name="resume-coach")

    def _count_failed(self, message: str) -> None:
        self.query_one("#resume-line", Static).update(escape(message))
        self.notify(message, severity="error", timeout=15)

    def _counted(self, reports: list[resume_coach.TrackReport]) -> None:
        self.reports = {report.track: report for report in reports}
        self.show_track()

    # --------------------------------------------------------------- render

    def show_track(self) -> None:
        report = self.reports.get(self.track)
        tabs = "  ".join(
            f"[bold $foreground on $primary] {name} [/]" if name == self.track else f"[dim]{name}[/dim]"
            for name in self.track_names
        )
        missing = self.query_one("#missing-table", DataTable)
        covered = self.query_one("#covered-table", DataTable)
        missing.clear()
        covered.clear()
        if report is None:
            self.query_one("#resume-line", Static).update(f"{tabs}\n{muted('not counted yet')}")
            return
        if report.error:
            summary = f"[{CRIMSON}]{escape(report.error)}[/]"
        else:
            summary = (f"[bold]{report.postings}[/bold] {report.track} postings counted against the "
                       f"[bold {EMBER}]{escape(report.resume_label or '-')}[/] resume "
                       f"[dim]({escape(report.resume_file or '-')})[/dim]. Add a term only if it is true; "
                       "g creates a local candidate; v reviews exact edits. No upload / $0. "
                       "Nothing becomes a default without explicit approval.")
        self.query_one("#resume-line", Static).update(f"{tabs}   [dim]t / T switch[/dim]\n{summary}")

        for index, stat in enumerate(report.missing):
            example = (f"{escape(stat.example_company or '')}: "
                       f"[dim]\"{escape(stat.example_snippet or '')}\"[/dim]")
            missing.add_row(f"[{GOLD}]{escape(stat.term)}[/]", self._share(stat), example, key=str(index))
        for index, stat in enumerate(report.covered):
            covered.add_row(f"[{MOSS}]{escape(stat.term)}[/]", self._share(stat),
                            escape(stat.resume_line or ""), key=str(index))

    @staticmethod
    def _share(stat: resume_coach.TermStat) -> str:
        return f"{bar(stat.share, 10)} {stat.share:>4.0%} [dim]({stat.postings})[/dim]"

    # -------------------------------------------------------------- actions

    def action_next_track(self) -> None:
        self.track_index = (self.track_index + 1) % max(1, len(self.track_names))
        self.show_track()

    def action_prev_track(self) -> None:
        self.track_index = (self.track_index - 1) % max(1, len(self.track_names))
        self.show_track()

    def action_open_example(self) -> None:
        """Open the posting a missing term was quoted from."""
        report = self.reports.get(self.track)
        table = self.query_one("#missing-table", DataTable)
        if report is None or table.row_count == 0:
            return
        stat = report.missing[table.cursor_row]
        if stat.example_job_id is not None:
            self.app.open_job(stat.example_job_id)                            # type: ignore[attr-defined]

    @on(DataTable.RowSelected, "#missing-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open_example()

    def action_generate(self) -> None:
        if self.version_busy or not self.track:
            self.notify("Choose a track and wait for any current version action.", severity="warning")
            return
        track = self.track

        def selected(source: str | None) -> None:
            if source is None:
                return
            self._version_worker("generate", track, source.strip())

        self.app.push_screen(
            TextPromptModal(
                f"Generate candidate for {track} (local only / $0)",
                label="Local HTML source path; blank infers an unambiguous registered source. "
                      "At least 5 eligible postings required. Original files/default stay unchanged.",
                placeholder="~/resumes/source.html",
            ),
            selected,
        )

    def action_versions(self) -> None:
        if not self.version_busy and self.track:
            self._version_worker("versions", self.track)

    def action_rollback(self) -> None:
        if self.version_busy or not self.track:
            return
        track = self.track
        self.app.push_screen(
            PickModal(
                f"Restore the prior default for {track}?",
                [("Cancel; keep current default", "cancel"), ("Restore previous default", "rollback")],
                hint="Existing jobs/applications and all audit artifacts remain unchanged.",
            ),
            lambda choice: self._version_worker("rollback", track) if choice == "rollback" else None,
        )

    def _version_worker(self, action: str, track: str, value: str = "") -> None:
        if self.version_busy:
            return
        self.version_busy = True
        workspace = self.app.workspace  # type: ignore[attr-defined]
        app = self.app
        self.notify(f"Resume {action}: working locally; no upload / $0.")

        def work() -> None:
            try:
                conn = connect(workspace.settings.db_path)
                try:
                    if action == "generate":
                        result = resume_versions.create_candidate(
                            conn, workspace.config, workspace.settings, track,
                            Path(value).expanduser() if value else None,
                        )
                    elif action == "versions":
                        result = resume_versions.list_versions(conn, workspace.settings, track)
                    elif action == "review":
                        result = resume_versions.get_version(conn, workspace.settings, value)
                    else:
                        result = resume_versions.rollback(conn, workspace.config, workspace.settings, track)
                finally:
                    conn.close()
            except Exception as exc:
                app.call_from_thread(self._version_failed, str(exc))
            else:
                app.call_from_thread(self._version_done, action, track, result)

        self.run_worker(work, thread=True, group="resume-versions", name=f"resume-{action}", exit_on_error=False)

    def _version_failed(self, message: str) -> None:
        self.version_busy = False
        self.notify(message, severity="error", timeout=20)

    def _version_done(self, action: str, track: str, result) -> None:
        self.version_busy = False
        if action == "versions":
            if not result:
                self.notify(f"No versions for {track}. Press g to generate a candidate.")
                return
            options = [
                (f"{row.get('created_at', '')}  {row['status']}  "
                 f"{'DEFAULT  ' if row.get('is_default') else ''}"
                 f"{'valid' if row.get('validation', {}).get('valid') else 'BLOCKED'}  {row['id']}",
                 row["id"])
                for row in result
            ]
            self.app.push_screen(
                PickModal(f"{track}: immutable version history", options,
                          hint="Enter: exact diff, evidence, validation and decision history. Esc cancels."),
                lambda version: self._version_worker("review", track, version) if version else None,
            )
        elif action in {"generate", "review"}:
            self.app.push_screen(ResumeReviewModal(result), self._review_closed)
        else:
            self.notify(f"Default restored: {result.get('label')} (resume {result.get('id')}).")
            self.action_refresh()

    def _review_closed(self, changed: bool) -> None:
        if changed:
            self.action_refresh()
