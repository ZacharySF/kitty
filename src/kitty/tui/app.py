"""The applyai TUI: job screens, global keys and the job command palette.

`KittyApp` is only built when the applyai module is enabled (tui/shell.py's
`create_app` decides); the shared shell it extends owns the database connection,
parsed config and Modules screen. Everything it does is a call into `kitty.core`.
"""

from __future__ import annotations

import threading
from functools import partial

from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider

from kitty.config import ConfigError, load_config
from kitty.core import jobs as jobs_core
from kitty.core.workspace import Workspace, sync_config_to_db
from kitty.modules import ModuleDisabledError
from kitty.tui.helpers import job_label
from kitty.tui.navigation import PLACES
from kitty.tui.screens.apply import ApplyScreen
from kitty.tui.screens.dashboard import DashboardScreen
from kitty.tui.screens.finder import FinderScreen
from kitty.tui.screens.inbox import InboxScreen
from kitty.tui.screens.job_detail import JobDetailScreen
from kitty.tui.screens.jobs import JobsScreen
from kitty.tui.screens.modals import AddJobModal
from kitty.tui.screens.modules import ModulesScreen
from kitty.tui.screens.pipeline import PipelineScreen
from kitty.tui.screens.profile import ProfileScreen
from kitty.tui.screens.reports import ReportsScreen
from kitty.tui.screens.resume import ResumeScreen
from kitty.tui.screens.sources import SourcesScreen
from kitty.tui.shell import APPLYAI, ShellApp

#: Palette label -> screen name, in workflow order.
MAIN_SCREENS: dict[str, str] = {place.label: place.screen for place in PLACES}

#: Palette label -> (action name, help text). Actions run on the active screen.
PALETTE_ACTIONS: dict[str, tuple[str, str]] = {
    "Add job by URL": ("add_job", "Paste a posting URL and fill in the fields"),
    "Change status": ("change_status", "Set the status of the selected job"),
    "Undo last status change": ("undo_status", "Appends a reverting event"),
    "Set next action": ("set_next_action", "Date and note for the next follow-up"),
    "Set deadline": ("set_deadline", "Application deadline for the selected job"),
    "Open in browser": ("open_url", "Open the posting; the tool never submits"),
    "Reload config": ("reload_config", "Re-read the yaml files and sync them"),
    "Refresh": ("refresh", "Re-read this screen from the database"),
}


class ScreenCommands(Provider):
    """Command palette: jump to a screen."""

    async def discover(self) -> Hits:
        for place in PLACES:
            yield DiscoveryHit(place.label, partial(self.app.action_goto, place.screen),
                               help=f"Go to {place.label}: {place.purpose}")

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for place in PLACES:
            score = matcher.match(place.label)
            if score > 0:
                yield Hit(
                    score, matcher.highlight(place.label),
                    partial(self.app.action_goto, place.screen), help=f"Go to {place.label}: {place.purpose}",
                )


class ActionCommands(Provider):
    """Command palette: run an action on the current screen."""

    def _run(self, action: str):
        """A callable the palette can invoke, routed to the active screen."""
        return partial(self.app.run_action, action)

    async def discover(self) -> Hits:
        for label, (action, help_text) in PALETTE_ACTIONS.items():
            yield DiscoveryHit(label, self._run(action), help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, (action, help_text) in PALETTE_ACTIONS.items():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), self._run(action), help=help_text)


class JobCommands(Provider):
    """Command palette: find a job by company or title and open it."""

    async def search(self, query: str) -> Hits:
        if len(query.strip()) < 2:
            return
        matcher = self.matcher(query)
        for job in jobs_core.search_jobs(self.app.conn, query.strip(), limit=40):
            label = job_label(job)
            score = matcher.match(label)
            if score > 0:
                yield Hit(
                    score, matcher.highlight(label),
                    partial(self.app.open_job, job.id),
                    help=f"job {job.id}  {job.location or 'no location'}  "
                         f"{job.status or job.queue_state}",
                )


class KittyApp(ShellApp):
    """The applyai TUI: the job screens on top of the shared shell."""

    COMMANDS = ShellApp.COMMANDS | {ScreenCommands, ActionCommands, JobCommands}
    HOME = "dashboard"
    places = PLACES

    BINDINGS = [
        Binding("question_mark", "help", "Help", key_display="?"),
        # The tab bar at the top shows these, so they stay out of the footer.
        *[Binding(place.key, f"goto('{place.screen}')", place.label, show=False) for place in PLACES],
        Binding("M", "goto('modules')", "Modules", show=False),
        Binding("ctrl+r", "reload_config", "Reload config"),
        Binding("q", "quit", "Quit"),
    ]

    SCREENS = {
        "dashboard": DashboardScreen,
        "inbox": InboxScreen,
        "apply": ApplyScreen,
        "jobs": JobsScreen,
        "pipeline": PipelineScreen,
        "sources": SourcesScreen,
        "resume": ResumeScreen,
        "finder": FinderScreen,
        "profile": ProfileScreen,
        "reports": ReportsScreen,
        "modules": ModulesScreen,
    }

    def __init__(self, *, workspace: Workspace) -> None:
        if APPLYAI not in workspace.config.modules:
            raise ModuleDisabledError(APPLYAI)
        super().__init__(workspace=workspace)
        #: Held by the fill worker while it fills or watches: Firefox allows one
        #: remote-control session, so a new fill waits for the previous one to end.
        self.browser_lock = threading.Lock()
        #: Filled tabs still waiting for my submit; the next fill keeps watching them.
        self.watched_tabs: list = []

    @property
    def views(self):
        """Saved views from views.yaml, in file order."""
        return self.workspace.config.views.views

    # -------------------------------------------------------------- actions

    def action_goto(self, name: str) -> None:
        """Switch to one of the main screens, leaving Job Detail if it is open."""
        if isinstance(self.screen, JobDetailScreen):
            self.pop_screen()
        super().action_goto(name)

    def open_job(self, job_id: int) -> None:
        """Show Job Detail for a job, replacing any detail screen already open."""
        if isinstance(self.screen, JobDetailScreen):
            self.pop_screen()
        self.push_screen(JobDetailScreen(job_id))

    def action_add_job(self) -> None:
        """Open the add-a-job modal, then jump to the job it created."""
        def _opened(job_id: int | None) -> None:
            if job_id is not None:
                self.open_job(job_id)

        self.push_screen(AddJobModal(self.config.track_names), _opened)

    def action_reload_config(self) -> None:
        """Re-read the yaml files and sync them into the database."""
        try:
            self.workspace.config = load_config(
                self.workspace.settings.config_dir, modules=self.modules,
            )
        except ConfigError as exc:
            self.notify(
                f"Config not reloaded: {exc.problems[0]}", severity="error", timeout=10
            )
            return
        counts = sync_config_to_db(self.conn, self.workspace.config)
        self.notify(
            f"Reloaded config: {counts['tracks']} tracks, {counts['sources']} sources, "
            f"{counts['watchlist']} watchlist companies."
        )
        screen = self.screen
        if hasattr(screen, "refresh_data"):
            screen.refresh_data()
