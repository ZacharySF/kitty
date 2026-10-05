"""Job Detail: everything known about one job, and the keys to change it."""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from applyai.core import applications as applications_core
from applyai.core import jobs as jobs_core
from applyai.core import resumes as resumes_core
from applyai.tui.actions import JobActions
from applyai.tui.helpers import (
    due_label,
    eligibility_markup,
    like_line,
    panel,
    short_date,
    status_markup,
    tier_markup,
)
from applyai.tui.navigation import ScreenHeader
from applyai.tui.screens.modals import PickModal, TextPromptModal
from applyai.tui.theme import ASH, CRIMSON, GOLD, STORM


class JobDetailScreen(JobActions, Screen):
    """One job: description, resume pick, edit notes, history and my notes."""

    BINDINGS = [
        ("escape", "back", "Back"),
        ("f", "fill_applications", "Fill & open"),
        ("s", "change_status", "Status"),
        ("u", "undo_status", "Undo status"),
        ("n", "set_next_action", "Next action"),
        ("d", "set_deadline", "Deadline"),
        ("k", "set_queue_state", "Queue state"),
        ("t", "set_tier", "Tier"),
        ("R", "pick_resume", "Resume"),
        ("e", "edit_notes", "My notes"),
        ("o", "open_url", "Open in browser"),
        ("r", "refresh", "Refresh"),
        Binding("asterisk", "toggle_favorite", "Favorite", key_display="*"),
    ]

    def __init__(self, job_id: int) -> None:
        super().__init__()
        self.job_id = job_id

    def compose(self) -> ComposeResult:
        yield ScreenHeader(None)
        with Horizontal(id="detail-body"):
            with VerticalScroll(id="detail-left"):
                with panel("Summary"):
                    yield Static(id="summary")
                with panel("Resume and edit notes"):
                    yield Static(id="resume-notes")
                with panel("Description"):
                    yield Static(id="jd-text")
            with VerticalScroll(id="detail-right"):
                with panel("Sources"):
                    yield Static(id="sources")
                with panel("History"):
                    yield Static(id="history")
                with panel("My notes"):
                    yield Static(id="my-notes")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def current_job_id(self) -> int | None:
        return self.job_id

    # ------------------------------------------------------------- render

    def refresh_data(self) -> None:
        """Re-read this job and repaint every panel."""
        conn = self.app.conn                        # type: ignore[attr-defined]
        job = jobs_core.get_job(conn, self.job_id)
        if job is None:
            self.notify(f"Job {self.job_id} is gone.", severity="error")
            self.app.pop_screen()
            return

        self.query_one(ScreenHeader).set_crumb(f"{job.company_name} — {job.title}")
        self.query_one("#summary", Static).update(self._summary(job))
        self.query_one("#resume-notes", Static).update(self._resume_notes(job))
        self.query_one("#jd-text", Static).update(
            escape(job.jd_text) if job.jd_text
            else "[dim]No description stored. Stage 2 fills this from the source.[/dim]"
        )
        self.query_one("#sources", Static).update(self._sources(job))
        self.query_one("#history", Static).update(self._history(conn, job))
        self.query_one("#my-notes", Static).update(
            job.my_notes or "[dim]none - press e to write some[/dim]"
        )

    def _summary(self, job) -> str:
        """The facts, with every verdict shown next to its reason."""
        lines = [
            f"[bold]url[/bold]        {job.url}",
            f"[bold]location[/bold]   {job.location or '-'}"
            + (f"  [{STORM}]remote[/]" if job.is_remote else ""),
            f"[bold]season[/bold]     {job.season or '-'}",
            f"[bold]tracks[/bold]     {', '.join(job.tracks) or '-'}",
            f"[bold]tier[/bold]       {tier_markup(job.tier)}"
            + (f"  [dim]({job.tier_source}) {job.tier_reason}[/dim]" if job.tier_reason else ""),
            f"[bold]eligible[/bold]   {eligibility_markup(job.eligibility)}"
            + (f"  [dim]{job.eligibility_reason}[/dim]" if job.eligibility_reason else ""),
            f"[bold]queue[/bold]      {job.queue_state}"
            + (f"   [{GOLD}]★ favorite[/]" if job.is_favorite else "   [dim]* makes it a favorite[/dim]"),
            f"[bold]status[/bold]     {status_markup(job.status) if job.status else '[dim]not applied[/dim]'}",
            f"[bold]posted[/bold]     {short_date(job.posted_at)}"
            f"   [bold]seen[/bold] {short_date(job.first_seen_at)}"
            f"   [bold]last seen[/bold] {short_date(job.last_seen_at)}",
            f"[bold]freshness[/bold]  {job.freshness}"
            f"   [bold]verified[/bold] {short_date(job.verified_at)}"
            f"   [bold]last check[/bold] {short_date(job.verification_checked_at)}",
            f"[bold]verification[/bold] {job.verification_status}"
            + (f" — {escape(job.verification_reason)}" if job.verification_reason else ""),
            f"[bold]form[/bold]       {job.fill_state}"
            + (f" — {escape(job.fill_blocker)}" if job.fill_blocker else ""),
            f"[bold]deadline[/bold]   {short_date(job.deadline_at)}   "
            f"[bold]next action[/bold] {due_label(job.next_action_at)}"
            + (f"  [dim]{job.next_action_note}[/dim]" if job.next_action_note else ""),
        ]
        if job.freshness in ("unverified", "stale"):
            lines.append(f"[dim]Check current employer evidence: applyai refresh --job {job.id}[/dim]")
        if job.school_selective:
            lines.append(
                f"[{GOLD}]school-selective[/] {job.school_selective_note or ''}"
            )
        like = like_line(job)
        if like:
            lines.append(f"[dim]{escape(like)}[/dim]")
        if not job.is_active:
            lines.append(f"[{CRIMSON}]closed[/] {short_date(job.closed_at)}  [{ASH}]still listed under All jobs[/]")
        return "\n".join(lines)

    def _resume_notes(self, job) -> str:
        """Which resume to send and what to check before sending it."""
        conn = self.app.conn                        # type: ignore[attr-defined]
        resume = resumes_core.get_resume(conn, job.resume_id) if job.resume_id else None
        lines = [
            f"[bold]resume[/bold] {resume.label if resume else '[dim]none picked - press R[/dim]'}"
        ]
        if resume:
            lines.append(f"[dim]{resume.file_path}[/dim]")
            if resume.description:
                lines.append(f"[dim]{resume.description}[/dim]")
        lines.append("")
        if job.edit_notes:
            lines.append("[bold]edit suggestions[/bold] [dim](you make the edits)[/dim]")
            lines.extend(f"  - {note}" for note in job.edit_notes)
        else:
            lines.append("[dim]No edit suggestions yet - Stage 3 fills these in.[/dim]")
        return "\n".join(lines)

    def _sources(self, job) -> str:
        """Every source this job was found in."""
        if not job.sources:
            return "[dim]none[/dim]"
        return "\n".join(
            f"[bold]{source.source}[/bold]  first {short_date(source.first_seen_at)}"
            f"  last {short_date(source.last_seen_at)}"
            for source in job.sources
        )

    def _history(self, conn, job) -> str:
        """The append-only status history, including undos and proposals."""
        application = applications_core.get_application_for_job(conn, job.id)
        if application is None:
            return "[dim]Not applied yet. Press s to record an application.[/dim]"

        lines = [
            f"[bold]applied[/bold] {short_date(application.applied_at)}"
            f"   [bold]track[/bold] {application.track or '-'}"
            f"   [bold]resume[/bold] {application.resume_label or '-'}",
            "",
        ]
        for event in applications_core.history(conn, application.id):
            marker = {
                "confirmed": "", "proposed": f" [{GOLD}](proposed - confirm with s)[/]",
                "rejected_by_me": " [dim](declined)[/dim]",
            }[event.state]
            undo = " [dim](undo)[/dim]" if event.undoes_event_id else ""
            lines.append(
                f"{short_date(event.occurred_at)}  {event.from_status or 'new'} -> "
                f"[bold]{event.to_status}[/bold]  [dim]{event.source}[/dim]{marker}{undo}"
            )
            if event.evidence:
                lines.append(f"    [dim]{event.evidence}[/dim]")
        return "\n".join(lines)

    # ------------------------------------------------------------ actions

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.refresh_data()
        self.notify("Refreshed.")

    def action_set_tier(self) -> None:
        """Set the tier by hand (tier_source becomes 'manual')."""
        options = [
            ("A - careful review", "A"),
            ("B - fast apply queue", "B"),
            ("C - filtered out, still visible", "C"),
            ("clear the tier", ""),
        ]

        def _save(value) -> None:
            if value is None:
                return
            jobs_core.set_tier(
                self.app.conn, self.job_id, value or None,  # type: ignore[attr-defined]
                reason="set by hand" if value else None, tier_source="manual",
            )
            self.refresh_data()

        self.app.push_screen(PickModal("Tier", options), _save)

    def action_pick_resume(self) -> None:
        """Choose which resume this job should use."""
        conn = self.app.conn                        # type: ignore[attr-defined]
        registered = resumes_core.list_resumes(conn)
        if not registered:
            self.notify("No resumes registered yet - `applyai resumes add <file>`.", severity="warning")
            return

        options = [
            (f"{resume.label} - {resume.description or 'no description'}", str(resume.id))
            for resume in registered
        ]
        options.append(("clear the pick", ""))

        def _save(value) -> None:
            if value is None:
                return
            resume_id = int(value) if value else None
            jobs_core.set_resume(conn, self.job_id, resume_id)
            application = applications_core.get_application_for_job(conn, self.job_id)
            if application is not None:
                applications_core.set_resume(conn, application.id, resume_id)
            self.refresh_data()

        self.app.push_screen(PickModal("Resume for this job", options), _save)

    def action_edit_notes(self) -> None:
        """Replace my free-text notes on this job."""
        job = jobs_core.get_job(self.app.conn, self.job_id)   # type: ignore[attr-defined]
        if job is None:
            return

        def _save(value) -> None:
            if value is None:
                return
            jobs_core.set_notes(self.app.conn, self.job_id, value.strip() or None)  # type: ignore[attr-defined]
            self.refresh_data()

        self.app.push_screen(
            TextPromptModal("My notes", initial=job.my_notes or ""), _save
        )
