"""`kitty add` and `kitty status` write all of their rows or none of them."""
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from kitty import cli
from kitty.cli import common as cli_common
from kitty.core import jobs as jobs_core


@pytest.fixture
def run(workspace, monkeypatch):
    """Invoke the CLI against the fixture workspace (every module enabled: no build file)."""
    monkeypatch.setenv("KITTY_HOME", str(workspace.settings.root))
    monkeypatch.setattr(cli_common, "open_workspace", lambda _settings: SimpleNamespace(
        settings=workspace.settings, config=workspace.config, conn=workspace.conn, close=lambda: None))
    return lambda *args: CliRunner().invoke(cli.app, list(args))


def _counts(conn) -> tuple[int, int, int]:
    return tuple(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                 for table in ("jobs", "applications", "status_events"))


def test_a_rejected_status_records_no_application_and_no_event(workspace, run):
    job, _ = jobs_core.upsert_job(workspace.conn, company="Synth Co", title="Intern",
                                  url="https://example.test/jobs/1", source="manual")
    before = _counts(workspace.conn)

    assert run("status", str(job.id), "intervew").exit_code != 0

    # status_events is append-only: a half-applied change could never be removed.
    assert _counts(workspace.conn) == before


def test_a_rejected_tier_does_not_leave_the_job_behind(workspace, run):
    result = run("add", "https://example.test/jobs/2", "-c", "Synth Co", "-t", "Intern", "-l", "Remote",
                 "--tier", "D", "--applied")

    assert result.exit_code != 0
    assert _counts(workspace.conn) == (0, 0, 0)
