"""The shell every kitty TUI shares, and the core-only app.

`ShellApp` holds what does not depend on any module: the workspace, the theme,
the help overlay, signal handling and the Modules screen. `KittyApp` (tui/app.py)
adds the applyai screens on top of it. When applyai is not enabled, `CoreApp`
launches instead and never imports a job screen, binding or palette provider.

`create_app` picks between them from the module selection the process started
with; edits to the build's modules (config/build.json) wait for the next start.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Sequence
from functools import partial

from textual.app import App
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider

from kitty.core.workspace import Workspace, open_workspace
from kitty.tui.screens.help_screen import HelpScreen
from kitty.tui.screens.modules import ModulesScreen
from kitty.tui.theme import DARK_FANTASY

APPLYAI = "applyai"


class ModulesCommands(Provider):
    """Command palette: open the Modules screen (core, always available)."""

    HELP = "Choose which modules kitty uses; changes apply on the next start"

    async def discover(self) -> Hits:
        yield DiscoveryHit("Modules", partial(self.app.action_goto, "modules"), help=self.HELP)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = matcher.match("Modules")
        if score > 0:
            yield Hit(score, matcher.highlight("Modules"), partial(self.app.action_goto, "modules"), help=self.HELP)


class ShellApp(App):
    """Workspace, theme, help and the Modules screen: the parts that need no module."""

    CSS_PATH = "styles.tcss"
    TITLE = "kitty"
    COMMANDS = App.COMMANDS | {ModulesCommands}

    #: The screen the app opens on, and where Esc on Modules goes back to.
    HOME = "modules"

    #: Main screens with an F-key each, for the help overlay; the core app has none.
    places: tuple = ()

    def __init__(self, *, workspace: Workspace) -> None:
        super().__init__()
        self.workspace = workspace
        self.sub_title = (
            "demo database" if workspace.settings.demo else str(workspace.settings.db_path.name)
        )

    # -------------------------------------------------------------- context

    @property
    def conn(self):
        """The open SQLite connection."""
        return self.workspace.conn

    @property
    def config(self):
        """The parsed yaml config."""
        return self.workspace.config

    @property
    def modules(self) -> tuple[str, ...]:
        """The modules this process started with (frozen; never changes while running)."""
        return tuple(self.workspace.config.modules)

    def on_mount(self) -> None:
        self.register_theme(DARK_FANTASY)
        self.theme = DARK_FANTASY.name
        self.push_screen(self.HOME)
        self._exit_on_hangup()

    def _exit_on_hangup(self) -> None:
        """Closing the terminal (SIGHUP) or SIGTERM exits normally instead of killing the app.

        A normal exit cancels workers, such as the form filler, so each ends its own
        session; a killed app would leave Firefox refusing remote control until it restarts.
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
        """Switch to one of the main screens."""
        if self.screen.name != name:
            self.switch_screen(name)

    def action_help(self) -> None:
        """Show the key list, built from the active screen's live bindings."""
        self.push_screen(HelpScreen(self._binding_rows(), self.screen.__class__.__name__, self.places))

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
        rows.append(("ctrl+p", "Command palette: screens and actions", "App"))
        if self.HOME != "modules":
            rows.append(("M", "Go to Modules", "App"))
        rows.extend((place.key.upper(), f"Go to {place.label}", "App") for place in self.places)
        return sorted(rows, key=lambda row: (row[2] != "App", row[0]))


class CoreApp(ShellApp):
    """The TUI of a workspace without applyai: the Modules screen and nothing else.

    Job screens, key bindings, palette commands and background workers belong to
    applyai and do not exist here.
    """

    BINDINGS = [
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("q", "quit", "Quit"),
    ]

    SCREENS = {"modules": ModulesScreen}


def create_app(
    *,
    demo: bool = False,
    modules: Sequence[str] | None = None,
    workspace: Workspace | None = None,
) -> ShellApp:
    """The TUI for the module selection this process started with.

    `modules` pins the selection (the CLI passes the one it already read); with
    neither it nor `workspace` the persisted selection is read once, here.
    """
    workspace = workspace or open_workspace(demo=demo, modules=modules)
    if APPLYAI in workspace.config.modules:
        from kitty.tui.app import KittyApp

        return KittyApp(workspace=workspace)
    return CoreApp(workspace=workspace)
