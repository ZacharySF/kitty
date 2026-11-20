"""Rika's career_memory tool (kitty.core.rika.learning_tools): synthetic jobs and notes only."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kitty.core.rika import learning_tools, learnings
from kitty.core.rika.tools import Context, ToolError
from test_rika_learnings import VOCABULARY, _job

TOOL = {tool.name: tool for tool in learning_tools.TOOLS}["career_memory"]


@pytest.fixture
def ctx(workspace):
    app = SimpleNamespace(settings=workspace.settings, config=SimpleNamespace(resume_terms=VOCABULARY),
                          modules=("applyai",))
    return Context(app, workspace.conn)


def test_career_memory_is_an_applyai_read(ctx):
    assert TOOL.effect == "read" and TOOL.module == "applyai"
    assert TOOL.parameters["required"] == []


def test_it_refreshes_and_returns_learnings_and_the_summary(ctx):
    _job(ctx.conn, "a", "Python and SQL.", state="applied", applied="applied")
    _job(ctx.conn, "b", "Python.", state="queued")
    learnings.add_note(ctx.conn, "Prefer hardware teams")
    result = TOOL.run(ctx, {})
    assert [item["text"] for item in result.data["posting_skills"]] == ["Python", "SQL"]
    assert result.data["posting_skills"][0]["postings"] == 2
    assert result.data["notes"][0]["text"] == "Prefer hardware teams"
    assert "Prefer hardware teams" in result.data["summary"]
    assert "not applicant facts" in result.data["summary"]


def test_a_job_focuses_the_summary_and_an_unknown_job_is_refused(ctx):
    _job(ctx.conn, "a", "Python and Kubernetes.", state="applied", applied="applied")
    target = _job(ctx.conn, "t", "Kubernetes only.")
    line = next(line for line in TOOL.run(ctx, {"job_id": target}).data["summary"].splitlines()
                if line.startswith("- This posting"))
    assert "Kubernetes" in line and "Python" not in line
    with pytest.raises(ToolError):
        TOOL.run(ctx, {"job_id": 10**9})


def test_forgotten_learnings_stay_out(ctx):
    _job(ctx.conn, "a", "Python.", state="applied", applied="applied")
    TOOL.run(ctx, {})
    python = next(item for item in learnings.learnings(ctx.conn) if item["text"] == "Python")
    learnings.forget(ctx.conn, python["id"])
    data = TOOL.run(ctx, {}).data
    assert data["posting_skills"] == [] and "Python" not in data["summary"]
