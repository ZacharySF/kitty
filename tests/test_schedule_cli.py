"""Provider selection at CLI and schedule boundaries, without network requests."""
from pathlib import Path
from types import SimpleNamespace
import shlex

import pytest
from typer.testing import CliRunner

from kitty import cli
from kitty.core import schedule
from kitty.core.triage import run as triage_core


@pytest.mark.parametrize("provider,expected", [
    ("anthropic", ["triage --collect", "triage --batch --yes"]),
    ("openrouter", ["triage --now --yes"]),
])
def test_schedule_selects_provider_commands(monkeypatch, provider, expected):
    monkeypatch.setattr(schedule.shutil, "which", lambda _: "/usr/bin/uv")
    steps = schedule.commands(with_triage=True, provider=provider)
    assert steps == ["/usr/bin/uv run kitty discover",
                     *[f"/usr/bin/uv run kitty {step}" for step in expected],
                     "/usr/bin/uv run kitty today --notify"]


def test_crontab_quotes_working_directory(monkeypatch):
    monkeypatch.setattr(schedule.shutil, "which", lambda _: "/usr/bin/uv")
    root = Path("/tmp/job's work; touch unsafe")
    text = schedule.crontab(root, times=("07:00",))
    directory = text.split(" cd ", 1)[1].split(" && ", 1)[0]
    assert shlex.split(directory) == [str(root)]
    assert text.startswith("0 7 * * * ")


def _workspace(monkeypatch, provider="openrouter"):
    config = SimpleNamespace(triage=SimpleNamespace(provider=provider, model="typesafe/jev-router",
                                                    max_cost_usd_per_run=5))
    workspace = SimpleNamespace(config=config, settings=SimpleNamespace(root=Path("/tmp/public")),
                                conn=object(), close=lambda: None)
    monkeypatch.setattr(cli, "_open", lambda: workspace)
    return workspace


@pytest.mark.parametrize("mode", ["--batch", "--collect"])
def test_openrouter_rejects_anthropic_modes(monkeypatch, mode):
    _workspace(monkeypatch)
    monkeypatch.setattr(triage_core, "make_client", lambda *_: pytest.fail("Anthropic client accessed"))
    result = CliRunner().invoke(cli.app, ["triage", mode])
    assert result.exit_code == 1


