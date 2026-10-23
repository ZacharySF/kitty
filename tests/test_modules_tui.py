"""The TUI follows the module selection: core-only kitty has no job screens at all.

Everything runs on synthetic temporary workspaces through Textual's pilot.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from rich.text import Text
from textual.widgets import DataTable

from kitty.core.workspace import open_workspace
from kitty.modules import ModuleDisabledError, load_modules, save_modules
from kitty.settings import Settings
from kitty.tui.app import KittyApp
from kitty.tui.navigation import PLACES
from kitty.tui.screens.modules import ModulesScreen
from kitty.tui.shell import CoreApp, create_app

SIZE = (160, 40)


@pytest.fixture
def core_workspace(tmp_path):
    """A core-only workspace: modules.yaml selects nothing and there is no careers yaml."""
    config_dir = tmp_path / "config"
    save_modules(config_dir, [])
    settings = Settings.load(root=tmp_path, db_path=tmp_path / "data" / "core.db")
    space = open_workspace(settings)
    yield space
    space.close()


def _states(app) -> dict[str, tuple[str, ...]]:
    """Module id -> (running now, next start) as the table shows them, markup removed."""
    table = app.screen.query_one("#modules-table", DataTable)
    return {
        key: tuple(Text.from_markup(str(cell)).plain for cell in table.get_row(key)[1:3])
        for key in ("applyai", "workspace")
    }


def test_create_app_picks_the_core_app_without_applyai(core_workspace):
    app = create_app(workspace=core_workspace)
    assert type(app) is CoreApp
    assert app.modules == ()
    assert set(app.SCREENS) == {"modules"}


def test_create_app_picks_the_job_app_with_applyai(workspace):
    app = create_app(workspace=workspace)
    assert type(app) is KittyApp
    assert app.modules == ("applyai", "workspace")


def test_job_app_refuses_a_workspace_without_applyai(core_workspace):
    with pytest.raises(ModuleDisabledError, match="applyai"):
        KittyApp(workspace=core_workspace)


def test_core_app_never_imports_a_job_screen(tmp_path):
    """A fresh interpreter: building the core app must not load any career screen."""
    save_modules(tmp_path / "config", [])
    script = (
        "import sys\n"
        "from kitty.tui.shell import create_app\n"
        "app = create_app()\n"
        "print(type(app).__name__)\n"
        "print(sorted(m for m in sys.modules if m.startswith('kitty.tui.screens.')))\n"
        "app.workspace.close()\n"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("KITTY_")}
    env["KITTY_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    name, screens = result.stdout.strip().splitlines()
    assert name == "CoreApp"
    assert "dashboard" not in screens and "jobs" not in screens and "apply" not in screens
    assert "modules" in screens


async def test_core_app_lands_on_modules_without_job_keys(core_workspace):
    app = create_app(workspace=core_workspace)
    async with app.run_test(size=SIZE) as pilot:
        assert isinstance(app.screen, ModulesScreen)
        assert _states(app) == {"applyai": ("disabled", "disabled"), "workspace": ("disabled", "disabled")}
        # The job app's F-keys, add-job and config reload do not exist here.
        for place in PLACES:
            await pilot.press(place.key)
            await pilot.pause()
            assert isinstance(app.screen, ModulesScreen)
        keys = {binding.key for binding in app.BINDINGS}
        assert not keys & {place.key for place in PLACES} and "ctrl+r" not in keys
        provider_names = {provider.__name__ for provider in app.COMMANDS}
        assert not provider_names & {"JobCommands", "ScreenCommands", "ActionCommands"}


async def test_toggling_persists_a_pending_selection_that_waits_for_restart(core_workspace):
    app = create_app(workspace=core_workspace)
    config_dir = core_workspace.settings.config_dir
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("space")                      # first row: applyai
        await pilot.pause()
        assert load_modules(config_dir) == ("applyai",)
        assert app.modules == ()                        # this process keeps what it started with
        assert _states(app)["applyai"] == ("disabled", "enabled *")
        assert "Restart" in app.screen.note

        await pilot.press("space")                      # toggling back matches what is running
        await pilot.pause()
        assert load_modules(config_dir) == ()
        assert _states(app)["applyai"] == ("disabled", "disabled")

        await pilot.press("down", "enter")              # enter toggles too: workspace
        await pilot.pause()
        assert load_modules(config_dir) == ("workspace",)
        assert app.modules == ()


async def test_malformed_modules_file_blocks_changes_and_stays_readable(core_workspace):
    config_dir = core_workspace.settings.config_dir
    app = create_app(workspace=core_workspace)
    async with app.run_test(size=SIZE) as pilot:
        (config_dir / "modules.yaml").write_text("enabled: [nope]\n", encoding="utf-8")
        await pilot.press("space")
        await pilot.pause()
        assert (config_dir / "modules.yaml").read_text(encoding="utf-8") == "enabled: [nope]\n"
        assert "cannot be read" in app.screen.note


async def test_job_app_opens_modules_with_M_and_goes_back(workspace):
    app = create_app(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        assert app.screen.__class__.__name__ == "DashboardScreen"
        await pilot.press("M")
        await pilot.pause()
        assert isinstance(app.screen, ModulesScreen)
        assert _states(app) == {"applyai": ("enabled", "enabled"), "workspace": ("enabled", "enabled")}
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DashboardScreen"


async def test_job_app_reload_keeps_the_frozen_selection(workspace):
    """A pending modules.yaml edit must not change what the running app uses."""
    tracks = workspace.config.track_names
    app = create_app(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        save_modules(workspace.settings.config_dir, ["workspace"])      # pending: applyai off
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert app.modules == ("applyai", "workspace")
        assert app.config.track_names == tracks
        assert app.screen.__class__.__name__ == "DashboardScreen"
