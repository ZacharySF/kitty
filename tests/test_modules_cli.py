"""The CLI shows and runs only the commands of the enabled modules.

Synthetic temporary workspaces only: KITTY_HOME points at tmp_path, so no runtime
config, database or credentials are touched.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kitty import cli
from kitty.modules import load_modules, save_modules

REPO_ROOT = Path(__file__).resolve().parents[1]
CAREERS_COMMANDS = (
    "add", "discover", "apply", "autofill", "triage", "coach", "today", "finder", "schedule",
    "resumes", "import", "demo", "gmail", "proposals", "questions",
)
CORE_COMMANDS = ("init", "doctor", "modules", "tui", "mcp")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """An empty workspace root that every CLI invocation resolves to."""
    monkeypatch.setenv("KITTY_HOME", str(tmp_path))
    monkeypatch.delenv("KITTY_DB", raising=False)
    return tmp_path


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(cli.app, list(args), input=input)


def flat(result) -> str:
    """Output with rich's line wrapping collapsed."""
    return " ".join(result.output.split())


def listed(result) -> set[str]:
    """Command names in the help's command table."""
    return {name for name in (*CAREERS_COMMANDS, *CORE_COMMANDS)
            if re.search(rf"(?m)^[│| ]*{name}\s", result.output)}


def with_examples(home: Path) -> Path:
    """Copy the committed generic .example files so `init` can create a careers config."""
    config = home / "config"
    config.mkdir(exist_ok=True)
    for example in (REPO_ROOT / "config").glob("*.example"):
        shutil.copy(example, config / example.name)
    return config


# ------------------------------------------------------------------ help


@pytest.mark.parametrize("selection", [[], ["workspace"]])
def test_help_hides_careers_commands_without_applyai(home, selection):
    save_modules(home / "config", selection)
    result = run("--help")
    assert result.exit_code == 0
    assert listed(result) == set(CORE_COMMANDS)


@pytest.mark.parametrize("selection", [["applyai"], ["applyai", "workspace"]])
def test_help_lists_everything_with_applyai(home, selection):
    save_modules(home / "config", selection)
    result = run("--help")
    assert result.exit_code == 0
    assert listed(result) == {*CAREERS_COMMANDS, *CORE_COMMANDS}


def test_a_workspace_without_modules_yaml_keeps_every_command(home):
    (home / "config").mkdir()
    result = run("--help")
    assert listed(result) == {*CAREERS_COMMANDS, *CORE_COMMANDS}


def test_unreadable_modules_file_leaves_only_core_commands_in_help(home):
    config = home / "config"
    config.mkdir()
    (config / "modules.yaml").write_text("enabled: [nope]\n", encoding="utf-8")
    assert listed(run("--help")) == set(CORE_COMMANDS)


def test_version_and_bare_invocation_work_without_any_careers_yaml(home):
    save_modules(home / "config", [])
    assert run("--version").exit_code == 0
    bare = run()
    assert "modules" in bare.output and "discover" not in bare.output


# ---------------------------------------------------- direct invocations


@pytest.mark.parametrize("selection", [[], ["workspace"]])
@pytest.mark.parametrize("command", [
    ["discover"], ["add", "https://example.invalid/job"], ["apply", "1"], ["today"], ["list"],
    ["resumes", "list"], ["gmail", "status"], ["questions", "list"], ["demo", "reset"],
    ["schedule", "print"], ["import", "csv", "nothing.csv"], ["proposals"], ["apply", "--help"],
])
def test_disabled_careers_commands_are_rejected_before_feature_work(home, selection, command):
    save_modules(home / "config", selection)
    result = run(*command)
    assert result.exit_code == 1
    text = flat(result)
    assert "applyai module" in text and "kitty modules enable applyai" in text
    assert not (home / "data").exists()          # nothing opened or created a database


def test_a_disabled_command_does_not_run_even_with_bad_arguments(home):
    save_modules(home / "config", [])
    result = run("fav", "not-a-number")
    assert result.exit_code == 1 and "applyai module" in flat(result)


def test_unreadable_modules_file_rejects_careers_commands_readably(home):
    config = home / "config"
    config.mkdir()
    (config / "modules.yaml").write_text("enabled: [applyai, applyai]\n", encoding="utf-8")
    result = run("discover")
    assert result.exit_code == 1
    assert "Cannot read config/modules.yaml" in flat(result)
    assert "duplicate" in flat(result)


def test_unknown_commands_still_fail_normally(home):
    save_modules(home / "config", ["applyai"])
    assert run("no-such-command").exit_code != 0


def test_enabled_careers_commands_are_reachable(home):
    save_modules(home / "config", ["applyai"])
    assert run("apply", "--help").exit_code == 0
    assert run("resumes", "--help").exit_code == 0


# ------------------------------------------------------------------ init


def test_init_is_core_only_and_asks_nothing_about_a_profile(home):
    with_examples(home)
    result = run("init", input="")
    assert result.exit_code == 0, result.output
    text = flat(result)
    assert "none (core only)" in text and "Graduation" not in text and "profile.yaml" not in text
    assert load_modules(home / "config") == ()
    assert (home / "config" / "modules.yaml").read_text(encoding="utf-8") == "enabled: []\n"
    for name in ("tracks", "sources", "profile", "answers", "watchlist"):
        assert not (home / "config" / f"{name}.yaml").exists()
    assert (home / "data" / "apply.db").exists()


def test_init_with_a_module_creates_that_modules_config(home):
    with_examples(home)
    result = run("init", "--yes", "--module", "applyai")
    assert result.exit_code == 0, result.output
    assert load_modules(home / "config") == ("applyai",)
    assert (home / "config" / "tracks.yaml").exists()
    assert "Database:" in flat(result)


def test_init_in_a_pre_module_workspace_keeps_every_module(home):
    config = with_examples(home)
    shutil.copy(config / "tracks.yaml.example", config / "tracks.yaml")
    result = run("init", "--yes")
    assert result.exit_code == 0, result.output
    assert not (config / "modules.yaml").exists()
    assert "applyai, workspace" in flat(result)


def test_init_rejects_an_unknown_module_without_writing_anything(home):
    (home / "config").mkdir()
    result = run("init", "--yes", "--module", "nope")
    assert result.exit_code == 2
    assert not (home / "config" / "modules.yaml").exists()


def test_core_only_doctor_does_not_need_careers_config(home):
    assert run("init", "--yes").exit_code == 0
    result = run("doctor")
    assert result.exit_code == 0, result.output
    assert "tracks.yaml" not in result.output and "profile.yaml" not in result.output


# --------------------------------------------------------- modules command


def test_modules_list_shows_the_persisted_selection(home):
    save_modules(home / "config", ["workspace"])
    text = flat(run("modules", "list"))
    assert re.search(r"applyai\s+disabled", text) and re.search(r"workspace\s+enabled", text)


def test_enable_and_disable_persist_and_say_when_they_apply(home):
    save_modules(home / "config", [])
    result = run("modules", "enable", "workspace")
    assert result.exit_code == 0
    assert load_modules(home / "config") == ("workspace",)

    assert run("modules", "enable", "applyai", "workspace").exit_code == 0
    assert load_modules(home / "config") == ("applyai", "workspace")

    assert run("modules", "disable", "applyai").exit_code == 0
    assert load_modules(home / "config") == ("workspace",)
    # Each CLI invocation is a new process, so the change is live for the next command.
    assert run("discover").exit_code == 1
    assert run("modules", "enable", "applyai").exit_code == 0
    assert run("apply", "--help").exit_code == 0



def test_first_time_applyai_activation_provisions_config_without_inventing_facts(home):
    assert run("init", "--yes").exit_code == 0
    assert not (home / "config" / "profile.yaml").exists()
    assert run("modules", "enable", "applyai").exit_code == 0
    assert run("list").exit_code == 0
    from kitty.config import load_config

    config = load_config(home / "config")
    assert config.profile.name == "" and config.profile.email == ""
    profile = home / "config" / "profile.yaml"
    profile.write_text(profile.read_text().replace('name: ""', 'name: "Synthetic Preserved Applicant"', 1))
    assert run("modules", "disable", "applyai").exit_code == 0
    assert run("modules", "enable", "applyai").exit_code == 0
    assert load_config(home / "config").profile.name == "Synthetic Preserved Applicant"

def test_changing_a_pre_module_workspace_starts_from_every_module(home):
    (home / "config").mkdir()
    assert run("modules", "disable", "applyai").exit_code == 0
    assert load_modules(home / "config") == ("workspace",)


def test_changing_nothing_does_not_rewrite_the_file(home):
    save_modules(home / "config", ["applyai"])
    before = (home / "config" / "modules.yaml").stat().st_mtime_ns
    result = run("modules", "enable", "applyai")
    assert result.exit_code == 0 and "Nothing to change" in flat(result)
    assert (home / "config" / "modules.yaml").stat().st_mtime_ns == before


def test_unknown_module_ids_are_rejected_and_nothing_changes(home):
    save_modules(home / "config", ["applyai"])
    result = run("modules", "enable", "workspace", "nope")
    assert result.exit_code == 2 and "Unknown module nope" in flat(result)
    assert load_modules(home / "config") == ("applyai",)


def test_a_malformed_modules_file_is_reported_and_never_overwritten(home):
    config = home / "config"
    config.mkdir()
    (config / "modules.yaml").write_text("enabled: [nope]\n", encoding="utf-8")
    for args in (("list",), ("enable", "applyai"), ("disable", "applyai")):
        result = run("modules", *args)
        assert result.exit_code == 1 and "Config problems" in flat(result)
    assert (config / "modules.yaml").read_text(encoding="utf-8") == "enabled: [nope]\n"


# ------------------------------------------------------- tui and mcp entry


def test_mcp_in_a_core_workspace_starts_with_the_frozen_empty_selection(home, monkeypatch):
    save_modules(home / "config", [])
    seen = {}
    monkeypatch.setattr("kitty.mcp_server.run_stdio", lambda settings, modules: seen.update(modules=modules))
    result = run("mcp")
    assert result.exit_code == 0, result.output
    assert tuple(seen["modules"]) == ()


def test_tui_in_a_core_workspace_passes_the_selection_to_the_app_factory(home, monkeypatch):
    save_modules(home / "config", ["workspace"])
    seen = {}

    class Stub:
        def run(self):
            seen["ran"] = True

    def fake(**kwargs):
        seen.update(kwargs)
        return Stub()

    monkeypatch.setattr("kitty.tui.shell.create_app", fake)
    result = run("tui")
    assert result.exit_code == 0, result.output
    assert tuple(seen["modules"]) == ("workspace",) and seen["ran"]
