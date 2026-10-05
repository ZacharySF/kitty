"""Apply: walk one track's queue one job at a time, A tier first, then B.

For the job in front of me: what it is, the resume to send, the edit notes, my
saved answers, and a warning when the board is Workday (the filler can only
open those). f fills the form in the applyai browser and moves on to the next
job; on a starred job (or with v on), f fills every waiting favorite in the
track I am on (t), the same as F. o opens it in my normal browser. Ready jobs
wait at the end of the queue for my review. After f the prepared tabs are
watched: when a tab shows its board's confirmation page (I clicked submit
myself), the application is recorded with follow-ups and the queue refreshes;
w stops watching. a records it by hand (resume and time, plus follow-up
reminders) and moves to the next job. s skips, l puts it off, x marks it not
eligible by hand, q logs a screening question I could not answer. * stars the
job as a favorite, v walks only favorites, and F autofills favorites (best
first, capped, only the current track's) in background tabs of the applyai
browser while I do something else. The tool never submits anything.
"""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from applyai.core import favorites as favorites_core
from applyai.core import followups
from applyai.core import jobs as jobs_core
from applyai.core import questions as questions_core
from applyai.core.apply.filler import choose_resume
from applyai.core.apply.urls import detect_ats
from applyai.models import Job
from applyai.tui.actions import JobActions
from applyai.tui.helpers import eligibility_markup, favorite_mark, like_line, muted, panel, short_date, tier_markup
from applyai.tui.navigation import ScreenHeader, key_of
from applyai.tui.screens.modals import TextPromptModal
from applyai.tui.theme import CRIMSON, EMBER, GOLD, MOSS

#: Boards the filler handles well; anything else is opened for me to fill.
_FILLABLE = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday"}


class ApplyScreen(JobActions, Screen):
    """The apply session: one job, everything needed to apply, one key to move on."""

    BINDINGS = [
        ("f", "fill_applications", "Fill & open"),
        ("o", "open_url", "Open in browser"),
        ("a", "applied", "Applied"),
        ("s", "skip", "Skip"),
        ("l", "later", "Later"),
        ("x", "not_eligible", "Not eligible"),
        ("q", "log_question", "Log question"),
        ("n", "next_job", "Next"),
        ("p", "prev_job", "Previous"),
        ("t", "cycle_track", "Track"),
        ("enter", "open_detail", "Details"),
        ("r", "refresh", "Refresh"),
        ("w", "stop_watching", "Stop watching"),
        Binding("asterisk", "toggle_favorite", "Favorite", key_display="*"),
        ("v", "toggle_favorites_only", "Favorites only"),
        ("F", "autofill", "Autofill favorites"),
    ]
    EXTRA_HELP = (
        ("a", "Only after you clicked submit yourself: records the application"),
        ("f", "Fills this job and moves on; on a ★ job (or with v) fills every waiting favorite "
              "in this track. Watches the tabs and records each confirmation page"),
        ("F", "Prepares this track's favorites in background tabs; blocked/partial attempts can resume"),
    )
    f_fills_favorites = True

    def __init__(self) -> None:
        super().__init__()
        self.track: str | None = None
        self.favorites_only = False
        self.queue: list[Job] = []
        self.index = 0

    def compose(self) -> ComposeResult:
        yield ScreenHeader("apply")
        yield Static(id="apply-line")
        with Horizontal(id="apply-body"):
            with VerticalScroll(id="apply-left"):
                with panel("The posting"):
                    yield Static(id="apply-job")
                with panel("Description"):
                    yield Static(id="apply-jd")
            with VerticalScroll(id="apply-right"):
                with panel("Send"):
                    yield Static(id="apply-send")
                with panel("Before you apply"):
                    yield Static(id="apply-notes")
                with panel("Your saved answers"):
                    yield Static(id="apply-answers")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    # ----------------------------------------------------------------- queue

    def active_track(self) -> str | None:
        """The track t picked; f and F only fill favorites in it."""
        return self.track

    def fills_favorites_now(self) -> bool:
        """f fills all waiting favorites when the job in front of me is one, or with v on."""
        if self.favorites_only:
            return True
        return bool(self.queue) and self.queue[self.index].is_favorite

    def after_fill_started(self) -> None:
        """The filled job waits in the browser for my submit; show the next one."""
        self.action_next_job()

    def load_queue(self) -> list[Job]:
        """Not-applied A and B jobs (on one track, or all), A first, best first within a tier.

        Jobs verified ready for review go last. Partial/blocked attempts stay
        retryable; with v on, only my favorites are shown.
        """
        config = self.app.config                                             # type: ignore[attr-defined]
        jobs = jobs_core.list_jobs(self.app.conn, jobs_core.JobFilter(       # type: ignore[attr-defined]
            queue_states=["new", "queued"], tiers=["A", "B"], eligibility=jobs_core.NOT_FILTERED_OUT,
            not_applied=True, tracks=[self.track] if self.track else None, order="best",
            prefer_cities=config.prefer_cities, prefer_remote=config.profile.open_to_remote,
            favorites_only=self.favorites_only or None,
        ))
        return sorted(jobs, key=lambda job: (job.fill_state == "ready", job.tier != "A"))  # stable

    def current_job_id(self) -> int | None:
        return self.queue[self.index].id if self.queue else None

    def refresh_data(self) -> None:
        """Reload the queue; stay on the same job when it is still there (starring
        reorders the queue), else on the same position (the handled job left it)."""
        current = self.current_job_id()
        self.queue = self.load_queue()
        ids = [job.id for job in self.queue]
        if current in ids:
            self.index = ids.index(current)
        self.index = min(self.index, max(len(self.queue) - 1, 0))
        self.show()

    # ---------------------------------------------------------------- render

    def show(self) -> None:
        a_count = sum(job.tier == "A" for job in self.queue)
        where = self.track or "all tracks"
        if self.favorites_only:
            where += f" [{GOLD}]★ favorites[/]"
        if not self.queue:
            self.query_one("#apply-line", Static).update(
                f"[bold]{where}[/bold]: nothing left to apply to. [dim]t switches track; v favorites / all; "
                f"the Inbox ({key_of('inbox')}) has new jobs to tier.[/dim]")
            for widget in ("#apply-job", "#apply-jd", "#apply-send", "#apply-notes", "#apply-answers"):
                self.query_one(widget, Static).update("")
            return
        job = self.queue[self.index]
        favorites = sum(job.is_favorite for job in self.queue)
        self.query_one("#apply-line", Static).update(
            f"[bold]{where}[/bold]   job {self.index + 1} of {len(self.queue)}   "
            f"[{GOLD}]{a_count} A[/] · {len(self.queue) - a_count} B · [{GOLD}]{favorites} ★[/]   "
            f"[dim]f fill · F autofill ★ · submit it yourself · a applied · s skip · l later · "
            f"x not eligible · * favorite[/dim]")
        self.query_one("#apply-job", Static).update(self._job_text(job))
        self.query_one("#apply-jd", Static).update(
            escape(job.jd_text) if job.jd_text else muted("No description stored; open the posting (o)."))
        self.query_one("#apply-send", Static).update(self._send_text(job))
        self.query_one("#apply-notes", Static).update(self._notes_text(job))
        self.query_one("#apply-answers", Static).update(self._answers_text())

    def _job_text(self, job: Job) -> str:
        lines = [
            f"{favorite_mark(job)} [bold]{escape(job.company_name)}[/bold]",
            f"{escape(job.title)}",
            f"{escape(job.location or '-')}{'  remote' if job.is_remote else ''}   "
            f"[dim]tracks[/dim] {', '.join(job.tracks)}",
            f"tier {tier_markup(job.tier)}  [dim]{escape(job.tier_reason or '')}[/dim]",
            f"eligibility {eligibility_markup(job.eligibility)}  [dim]{escape(job.eligibility_reason or '')}[/dim]",
            f"[dim]posted {short_date(job.posted_at)} · seen {short_date(job.first_seen_at)}[/dim]",
        ]
        if job.school_selective:
            lines.append(f"[{GOLD}]school-selective[/] {escape(job.school_selective_note or '')}")
        if job.deadline_at:
            lines.append(f"[{CRIMSON}]deadline {short_date(job.deadline_at)}[/]")
        if job.fill_state != "unattempted":
            lines.append(f"[{GOLD}]preparation: {escape(job.fill_state)}; nothing submitted[/]")
            if job.fill_blocker:
                lines.append(f"[dim]{escape(job.fill_blocker)}[/dim]")
        lines.append(f"[dim]freshness: {escape(job.freshness)}[/dim]")
        if job.verification_reason:
            lines.append(f"[dim]{escape(job.verification_reason)}[/dim]")
        like = like_line(job)
        if like:
            lines.append(f"[dim]{escape(like)}[/dim]")
        return "\n".join(lines)

    def _resume_for(self, job: Job):
        """Use the same pinned-or-default selection as the application filler."""
        return choose_resume(self.app.conn, self.app.config, job)              # type: ignore[attr-defined]

    def _send_text(self, job: Job) -> str:
        resume = self._resume_for(job)
        ats = detect_ats(job.apply_url or job.url)
        if resume:
            name = escape(Path(resume.file_path).name)
            lines = [f"[bold]resume[/bold] [{EMBER}]{escape(resume.label)}[/] [dim]{name}[/dim]"]
        else:
            lines = [f"[{CRIMSON}]no resume for this track - add one on the Profile tab ({key_of('profile')})[/]"]
        if ats == "workday":
            lines.append(f"[{GOLD}]Workday: f inspects guest/session steps; any login or verification stays manual.[/]")
        elif ats in _FILLABLE:
            lines.append(f"[{MOSS}]{ats}[/]: f fills it in the applyai browser")
        else:
            lines.append(f"[{GOLD}]{ats} board: f opens it; fill it by hand[/]")
        lines.append(f"[dim]{escape(job.apply_url or job.url)}[/dim]")
        return "\n".join(lines)

    def _notes_text(self, job: Job) -> str:
        if not job.edit_notes:
            return muted("No edit notes yet (applyai triage adds them).")
        return "\n".join(f"• {escape(note)}" for note in job.edit_notes)

    def _answers_text(self) -> str:
        answers = self.app.config.answers.answers                            # type: ignore[attr-defined]
        if not answers:
            return muted(f"None yet - add them on the Profile tab ({key_of('profile')}).")
        return "\n".join(f"[dim]{escape(a.question)}[/dim]\n  {escape(a.answer)}" for a in answers)

    # --------------------------------------------------------------- actions

    def _after(self, message: str) -> None:
        """Reload the queue (the job just handled leaves it) and say what happened."""
        self.notify(message)
        self.refresh_data()

    def action_applied(self) -> None:
        """Record the application I just submitted: resume, time, follow-up reminders."""
        if not self.queue:
            return
        job = self.queue[self.index]
        resume = self._resume_for(job)
        followups.mark_applied(self.app.conn, self.app.config, job.id,       # type: ignore[attr-defined]
                               resume_id=resume.id if resume else None,
                               track=job.tracks[0] if job.tracks else None)
        days = ", ".join(f"+{d}" for d in self.app.config.apply.followup_days)  # type: ignore[attr-defined]
        self._after(f"Applied: {job.company_name}. Follow-ups at {days} days.")

    def _set_state(self, state: str, message: str) -> None:
        if not self.queue:
            return
        job = self.queue[self.index]
        favorites_core.set_state(self.app.conn, [job.id], state)             # type: ignore[attr-defined]
        self._after(f"{message}: {job.company_name}")

    def action_skip(self) -> None:
        self._set_state("skipped", "Skipped")

    def action_later(self) -> None:
        self._set_state("later", "Later")

    def action_not_eligible(self) -> None:
        if not self.queue:
            return
        job = self.queue[self.index]
        favorites_core.mark_not_eligible(self.app.conn, [job.id])            # type: ignore[attr-defined]
        self._after(f"Not eligible: {job.company_name}")

    def action_log_question(self) -> None:
        """Log a screening question I could not answer, so I answer it once on the Profile tab."""
        job_id = self.current_job_id()

        def _save(text: str | None) -> None:
            if text and text.strip():
                questions_core.log_question(self.app.conn, text.strip(), job_id=job_id)  # type: ignore[attr-defined]
                self.notify(f"Logged. Answer it once on the Profile tab ({key_of('profile')}).")

        self.app.push_screen(TextPromptModal("Screening question you could not answer",
                                             label="Paste the question exactly"), _save)

    def action_next_job(self) -> None:
        if self.queue:
            self.index = (self.index + 1) % len(self.queue)
            self.show()

    def action_prev_job(self) -> None:
        if self.queue:
            self.index = (self.index - 1) % len(self.queue)
            self.show()

    def action_cycle_track(self) -> None:
        names = [None, *self.app.config.track_names]                         # type: ignore[attr-defined]
        self.track = names[(names.index(self.track) + 1) % len(names)]
        self._restart()

    def action_toggle_favorites_only(self) -> None:
        """Walk only my favorites, or the whole A/B queue again."""
        self.favorites_only = not self.favorites_only
        self._restart()

    def _restart(self) -> None:
        """Show the first job of a new queue."""
        self.queue, self.index = [], 0
        self.refresh_data()

    def action_open_detail(self) -> None:
        job_id = self.current_job_id()
        if job_id is not None:
            self.app.open_job(job_id)                                        # type: ignore[attr-defined]

    def action_refresh(self) -> None:
        self.refresh_data()
