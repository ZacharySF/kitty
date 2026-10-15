"""The Textual app: screen registry, global keys and the command palette.

The app owns the database connection and the parsed config; screens read them
through `self.app`. Everything it does is a call into `kitty.core`.
"""

from __future__ import annotations

import asyncio
import signal
import threading
from functools import partial

from textual.app import App
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider

from kitty.core import jobs as jobs_core
from kitty.core.workspace import Workspace, open_workspace, sync_config_to_db
from kitty.tui.helpers import job_label
from kitty.tui.navigation import PLACES
from kitty.tui.screens.apply import ApplyScreen
from kitty.tui.screens.dashboard import DashboardScreen
from kitty.tui.screens.finder import FinderScreen
from kitty.tui.screens.help_screen import HelpScreen
from kitty.tui.screens.inbox import InboxScreen
from kitty.tui.screens.job_detail import JobDetailScreen
from kitty.tui.screens.jobs import JobsScreen
from kitty.tui.screens.modals import AddJobModal
from kitty.tui.screens.pipeline import PipelineScreen
from kitty.tui.screens.profile import ProfileScreen
from kitty.tui.screens.reports import ReportsScreen
from kitty.tui.screens.resume import ResumeScreen
from kitty.tui.screens.sources import SourcesScreen
from kitty.tui.theme import DARK_FANTASY

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


class KittyApp(App):
    """The kitty TUI."""

    CSS_PATH = "styles.tcss"
    TITLE = "kitty"
    COMMANDS = App.COMMANDS | {ScreenCommands, ActionCommands, JobCommands}

    BINDINGS = [
        Binding("question_mark", "help", "Help", key_display="?"),
        # The tab bar at the top shows these, so they stay out of the footer.
        *[Binding(place.key, f"goto('{place.screen}')", place.label, show=False) for place in PLACES],
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
    }

    def __init__(self, *, demo: bool = False, workspace: Workspace | None = None) -> None:
        super().__init__()
        self.workspace = workspace or open_workspace(demo=demo)
        self.sub_title = (
            "demo database" if self.workspace.settings.demo else str(self.workspace.settings.db_path.name)
        )
        #: Held by the fill worker while it fills or watches: Firefox allows one
        #: remote-control session, so a new fill waits for the previous one to end.
        self.browser_lock = threading.Lock()
        #: Filled tabs still waiting for my submit; the next fill keeps watching them.
        self.watched_tabs: list = []

    # -------------------------------------------------------------- context

    @property
    def conn(self):
        """The open SQLite connection every screen reads from."""
        return self.workspace.conn

    @property
    def config(self):
        """The parsed yaml config."""
        return self.workspace.config

    @property
    def views(self):
        """Saved views from views.yaml, in file order."""
        return self.workspace.config.views.views

    def on_mount(self) -> None:
        self.register_theme(DARK_FANTASY)
        self.theme = DARK_FANTASY.name
        self.push_screen("dashboard")
        self._exit_on_hangup()

    def _exit_on_hangup(self) -> None:
        """Closing the terminal (SIGHUP) or SIGTERM exits normally instead of killing the app.

        A normal exit cancels the fill worker, which ends its Firefox session; a
        killed app would leave Firefox refusing remote control until it restarts.
        """
        loop = asyncio.get_running_loop()
        for name in ("SIGHUP", "SIGTERM"):
            try:
                loop.add_signal_handler(getattr(signal, name), self.exit)
            except (AttributeError, NotImplementedError, RuntimeError, ValueError):
                pass                      # no such signal here, or not the main thread

    def on_unmount(self) -> None:
        self.workspace.close()

    # -------------------------------------------------------------- actions

    def action_goto(self, name: str) -> None:
        """Switch to one of the main screens, leaving Job Detail if it is open."""
        if isinstance(self.screen, JobDetailScreen):
            self.pop_screen()
        if self.screen.name != name:
            self.switch_screen(name)

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
        from kitty.config import ConfigError, load_config

        try:
            self.workspace.config = load_config(self.workspace.settings.config_dir)
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

    def action_help(self) -> None:
        """Show the key list, built from the active screen's live bindings."""
        self.push_screen(HelpScreen(self._binding_rows(), self.screen.__class__.__name__))

    def _binding_rows(self) -> list[tuple[str, str, str]]:
        """Collect (key, description, where) from the active bindings."""
        rows: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for key, active in self.screen.active_bindings.items():
            binding = active.binding
            if not binding.show or not binding.description:
                continue
            where = type(active.node).__name__.removesuffix("Screen") or "App"
            entry = (binding.key_display or key, binding.description)
            if entry in seen:
                continue
            seen.add(entry)
            rows.append((entry[0], entry[1], where))
        where = self.screen.__class__.__name__.removesuffix("Screen")
        for key, description in getattr(self.screen, "EXTRA_HELP", ()):
            rows.append((key, description, where))
        rows.append(("ctrl+p", "Command palette: screens, actions, jobs", "App"))
        rows.extend((place.key.upper(), f"Go to {place.label}", "App") for place in PLACES)
        return sorted(rows, key=lambda row: (row[2] != "App", row[0]))


def run(demo: bool = False) -> None:
    """Entry point used by `kitty tui`."""
    KittyApp(demo=demo).run()
