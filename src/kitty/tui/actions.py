"""Actions shared by the screens that show jobs.

Jobs, Pipeline and Job Detail all need "change status", "undo" and "set next
action". They mix this in and say which job is selected; the writes themselves
are core calls.
"""

from __future__ import annotations

import webbrowser

from textual.worker import get_current_worker

from kitty.core import applications as applications_core
from kitty.core import favorites as favorites_core
from kitty.core import jobs as jobs_core
from kitty.dates import parse_user_date
from kitty.tui.screens.modals import PickModal, StatusModal, TextPromptModal


def fill_summary(reports) -> str:
    """One toast for a fill run: per job what was filled and what still needs me."""
    lines = []
    for report in reports:
        waiting = f", {len(report.unanswered)} question(s) for you" if report.unanswered else ""
        problem = f" - {report.error or report.note}" if report.error or report.note else ""
        lines.append(f"{report.company}: {report.status}, {len(report.filled)} fields{waiting}{problem}")
    lines.append("Nothing was submitted. Check each tab and click submit yourself; "
                 "kitty records it when the board shows its confirmation page.")
    return "\n".join(lines)


class JobActions:
    """Mixin for screens with a selected job.

    Subclasses implement `current_job_id` and `refresh_data`.
    """

    def current_job_id(self) -> int | None:
        """The job the user has selected, or None."""
        raise NotImplementedError

    def refresh_data(self) -> None:
        """Re-read the screen's data after a change."""
        raise NotImplementedError

    #: List screens show only this track's jobs when set (c picks it); None = every track.
    track_filter: str | None = None

    def action_pick_track(self) -> None:
        """Pick one track to list (quant, parttime, ...), or every track again."""
        names = self.app.config.track_names                 # type: ignore[attr-defined]
        every = "*every*"
        options = [("every track", every), *[(name, name) for name in names]]

        def _apply(track: str | None) -> None:
            if track is None:                               # escape: keep the current one
                return
            self.track_filter = None if track == every else track
            self.refresh_data()
            self.notify(f"Showing {self.track_filter or 'every track'}.")

        current = self.track_filter or "every track"
        self.app.push_screen(PickModal(f"Show which track? (now: {current})", options), _apply)  # type: ignore[attr-defined]

    # ---------------------------------------------------------------- helpers

    def _job_or_warn(self):
        """The selected job, or None with a message."""
        job_id = self.current_job_id()
        if job_id is None:
            self.notify("Select a job first.", severity="warning")
            return None
        job = jobs_core.get_job(self.app.conn, job_id)      # type: ignore[attr-defined]
        if job is None:
            self.notify(f"Job {job_id} is gone.", severity="error")
        return job

    def _application_for(self, job, *, create: bool):
        """The job's application; optionally record one so a status can be set."""
        conn = self.app.conn                                # type: ignore[attr-defined]
        application = applications_core.get_application_for_job(conn, job.id)
        if application is None and create:
            application = applications_core.record_application(
                conn, job.id,
                track=job.tracks[0] if job.tracks else None,
                resume_id=job.resume_id,
            )
        return application

    def fill_targets(self) -> list[int]:
        """Jobs the fill key acts on: the selected one. The Inbox uses its marked rows."""
        job_id = self.current_job_id()
        return [job_id] if job_id is not None else []

    def favorite_targets(self) -> list[int]:
        """Jobs the favorite key acts on: the selected one. The Inbox uses its marked rows."""
        job_id = self.current_job_id()
        return [job_id] if job_id is not None else []

    def action_toggle_favorite(self) -> None:
        """Star the target jobs, or unstar them when every one is already a favorite.

        Favorites steer best-first order ("more like these") and are what
        autofill (F on the Apply tab) fills.
        """
        ids = self.favorite_targets()
        if not ids:
            self.notify("Select a job first.", severity="warning")       # type: ignore[attr-defined]
            return
        conn = self.app.conn                                            # type: ignore[attr-defined]
        starred = favorites_core.favorite(conn, ids)
        if starred:
            message = f"★ {len(starred)} favorite(s) added; similar jobs now rank higher."
        else:
            favorites_core.unfavorite(conn, ids)
            message = f"{len(ids)} favorite(s) removed."
        self.notify(message)                                            # type: ignore[attr-defined]
        self.refresh_data()

    #: Inbox, Jobs and Apply set this: f may fill every starred job still waiting.
    f_fills_favorites: bool = False

    def active_track(self) -> str | None:
        """The track this screen is filtered on (c), or None for every track."""
        return self.track_filter

    def fills_favorites_now(self) -> bool:
        """Whether f fills the starred jobs instead of its target: list screens with nothing marked."""
        return self.f_fills_favorites and not getattr(self, "marked", None)

    def after_fill_started(self) -> None:
        """Hook: the Apply tab moves on to the next job once a fill has started."""

    def _favorites_waiting(self) -> bool:
        """True when this track has favorites that have not been verified ready."""
        from kitty.core.apply.autofill import plan_run

        return bool(plan_run(self.app.conn, self.app.config,          # type: ignore[attr-defined]
                             track=self.active_track()).job_ids)

    def action_fill_applications(self) -> None:
        """Open each target job's application in the kitty browser, fill it, then watch it.

        When `fills_favorites_now` and starred jobs are waiting in the track I am
        filtered on, f fills every one of them (best first, autofill_cap per
        press) instead; favorites in other tracks are left alone. Nothing is
        submitted: I check each tab and click submit; when a tab shows its
        board's confirmation page the watcher records the application and the
        screen refreshes.
        """
        if self.fills_favorites_now() and self._favorites_waiting():
            self.action_autofill()
            return
        ids = self.fill_targets()
        if not ids:
            self.notify("Select a job first.", severity="warning")
            return
        self.notify(f"Filling {len(ids)} application(s) in the rika! browser...")  # type: ignore[attr-defined]

        def fill(workspace, watch, to_ui) -> None:
            from kitty.core.apply.filler import fill_jobs

            fill_jobs(workspace.settings, workspace.config, ids, watch=watch,
                      on_filled=lambda reports: to_ui(self._filled, fill_summary(reports), "information"))

        self._run_browser_worker(fill)
        self.after_fill_started()

    def action_autofill(self) -> None:
        """Fill my favorites in this screen's track (best first, up to autofill_cap) in
        background tabs, then watch them.

        The tabs open behind the one in front, so I can keep working in other
        windows; applied, skipped and verified-ready favorites are
        left out and named in the summary. Nothing is submitted.
        """
        track = self.active_track()
        where = f"{track} " if track else ""
        self.notify(f"Autofill: filling your {where}favorites in background tabs of the rika! browser...")  # type: ignore[attr-defined]

        def fill(workspace, watch, to_ui) -> None:
            from kitty.core.apply.autofill import run_autofill, summary

            run_autofill(workspace.settings, workspace.config, track=track, watch=watch,
                         on_filled=lambda result: to_ui(
                             self._filled, summary(result), "error" if result.failure else "information"))

        self._run_browser_worker(fill)
        self.after_fill_started()

    def _run_browser_worker(self, fill) -> None:
        """Run `fill(workspace, watch, to_ui)` in the app-level browser worker.

        An app-level worker thread with its own database connection, so
        watching goes on when I switch tabs. Firefox takes one remote-control
        session, so a new run cancels the running one (which ends its session
        within a few seconds) and keeps watching its tabs. Quitting the app
        cancels it too.
        """
        app = self.app                                              # type: ignore[attr-defined]
        workspace = app.workspace

        def work() -> None:
            # Imported here: only these keys need the browser code.
            from kitty.core.apply.watch import Watch

            worker = get_current_worker()

            def to_ui(callback, *args) -> None:
                """Run on the UI thread unless the worker was cancelled (the app may be gone)."""
                if not worker.is_cancelled:
                    try:
                        app.call_from_thread(callback, *args)
                    except RuntimeError:                            # the app stopped meanwhile
                        pass

            with app.browser_lock:                                  # the previous run ends first
                if worker.is_cancelled:
                    return
                watch = Watch(
                    stop=worker.cancelled_event, hours=workspace.config.apply.watch_hours,
                    tabs=app.watched_tabs,
                    on_start=lambda count: to_ui(self._watching, count),
                    on_applied=lambda tab: to_ui(self._recorded, tab.label),
                )
                try:
                    fill(workspace, watch, to_ui)
                except Exception as exc:                            # shown, not swallowed
                    to_ui(self._filled, f"Filling failed: {type(exc).__name__}: {exc}", "error")

        app.run_worker(work, thread=True, exclusive=True, group="fill-applications",
                       name="fill-applications")

    def _filled(self, message: str, severity: str) -> None:
        app = self.app                                              # type: ignore[attr-defined]
        app.notify(message, severity=severity, timeout=30)
        if hasattr(app.screen, "refresh_data"):
            app.screen.refresh_data()

    def _watching(self, count: int) -> None:
        self.app.notify(f"Watching {count} tab(s) for submissions; w on the Apply tab stops.")  # type: ignore[attr-defined]

    def _recorded(self, label: str) -> None:
        """A watched tab showed its confirmation page: say so and refresh the screen I am on.

        Goes through the app, not this screen: I may have left it (or closed Job Detail).
        """
        app = self.app                                              # type: ignore[attr-defined]
        app.notify(f"Applied: {label} (confirmation page seen). Follow-ups scheduled.", timeout=15)
        if hasattr(app.screen, "refresh_data"):
            app.screen.refresh_data()

    def action_stop_watching(self) -> None:
        """Stop the fill worker: it ends its Firefox session; the tabs stay open."""
        app = self.app                                              # type: ignore[attr-defined]
        if app.workers.cancel_group(app, "fill-applications"):
            self.notify("Stopped watching the rika! browser tabs.")  # type: ignore[attr-defined]
        else:
            self.notify("Not watching anything.")                    # type: ignore[attr-defined]

    # ---------------------------------------------------------------- actions

    def action_change_status(self) -> None:
        """Pick a new status for the selected job (recording the application if new)."""
        job = self._job_or_warn()
        if job is None:
            return

        def _apply(choice) -> None:
            if choice is None:
                return
            conn = self.app.conn                            # type: ignore[attr-defined]
            application = self._application_for(job, create=True)
            if choice.status != "applied":
                applications_core.set_status(
                    conn, application.id, choice.status,
                    occurred_at=choice.occurred_at, evidence=choice.note,
                )
            self.notify(f"{job.company_name} - {job.title}: {choice.status}")
            self.refresh_data()

        self.app.push_screen(
            StatusModal(f"{job.company_name} - {job.title}", current=job.status), _apply
        )

    def action_undo_status(self) -> None:
        """Undo the last status change on the selected job.

        Undo covers status changes only, and works by appending a reverting event
        so the history keeps both steps.
        """
        job = self._job_or_warn()
        if job is None:
            return
        application = self._application_for(job, create=False)
        if application is None:
            self.notify("No application on this job yet.", severity="warning")
            return

        event = applications_core.undo_last_status(self.app.conn, application.id)  # type: ignore[attr-defined]
        if event is None:
            self.notify("Nothing to undo (the first 'applied' event stays).", severity="warning")
        else:
            self.notify(f"Undone: back to {event.to_status}.")
        self.refresh_data()

    def action_set_next_action(self) -> None:
        """Set the next-action date and note on the selected job's application."""
        job = self._job_or_warn()
        if job is None:
            return
        application = self._application_for(job, create=False)
        if application is None:
            self.notify("No application on this job yet.", severity="warning")
            return

        def _save(value) -> None:
            if value is None:
                return
            text = value.strip()
            if not text:
                applications_core.set_next_action(
                    self.app.conn, application.id, due_at=None, note=None  # type: ignore[attr-defined]
                )
                self.notify("Next action cleared.")
                self.refresh_data()
                return

            date_part, _, note = text.partition(" ")
            due_at = parse_user_date(date_part)
            if due_at is None:
                self.notify(f"Could not read the date '{date_part}'.", severity="error")
                return
            applications_core.set_next_action(
                self.app.conn, application.id,                # type: ignore[attr-defined]
                due_at=due_at, note=note.strip() or None,
            )
            self.notify(f"Next action {due_at[:10]}.")
            self.refresh_data()

        self.app.push_screen(
            TextPromptModal(
                "Next action",
                label="Date then note, e.g. '+7 email the recruiter'. Blank clears it.",
                initial=(application.next_action_at or "")[:10]
                + (f" {application.next_action_note}" if application.next_action_note else ""),
                placeholder="+7 follow up",
            ),
            _save,
        )

    def action_set_deadline(self) -> None:
        """Set or clear the application deadline on the selected job."""
        job = self._job_or_warn()
        if job is None:
            return

        def _save(value) -> None:
            if value is None:
                return
            text = value.strip()
            if not text:
                jobs_core.set_deadline(self.app.conn, job.id, None)   # type: ignore[attr-defined]
                self.notify("Deadline cleared.")
            else:
                parsed = parse_user_date(text)
                if parsed is None:
                    self.notify(f"Could not read the date '{text}'.", severity="error")
                    return
                jobs_core.set_deadline(self.app.conn, job.id, parsed)  # type: ignore[attr-defined]
                self.notify(f"Deadline {parsed[:10]}.")
            self.refresh_data()

        self.app.push_screen(
            TextPromptModal(
                "Deadline",
                label="2026-10-01, 'today', '+14'. Blank clears it.",
                initial=(job.deadline_at or "")[:10],
            ),
            _save,
        )

    def action_set_queue_state(self) -> None:
        """Move the selected job between my workflow states."""
        job = self._job_or_warn()
        if job is None:
            return

        options = [
            ("new - not looked at", "new"),
            ("queued - in the apply queue", "queued"),
            ("later - maybe later", "later"),
            ("skipped - not applying", "skipped"),
            ("not_eligible - I do not qualify", "not_eligible"),
        ]

        def _save(value) -> None:
            if value is None:
                return
            favorites_core.set_state(self.app.conn, [job.id], value)   # type: ignore[attr-defined]
            self.notify(f"Queue state: {value}")
            self.refresh_data()

        self.app.push_screen(PickModal(f"Queue state ({job.queue_state})", options), _save)

    def action_open_url(self) -> None:
        """Open the posting in my normal browser. It never submits anything."""
        job = self._job_or_warn()
        if job is None:
            return
        target = job.apply_url or job.url
        try:
            webbrowser.open(target)
        except Exception as exc:                      # a headless box has no browser
            self.notify(f"Could not open a browser: {exc}", severity="error")
            return
        self.notify(f"Opened {target}")
