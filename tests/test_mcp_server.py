"""Stage 7: the MCP server's tools against a temporary workspace (no network)."""

from __future__ import annotations

import asyncio

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from applyai.core import applications as applications_core
from applyai.core import jobs as jobs_core
from applyai.core import reminders as reminders_core
from applyai.mcp_server import ApplyAITools, build_server

EXPECTED_TOOLS = {
    "today", "list_jobs", "get_job", "weekly_progress", "list_questions", "finder_candidates",
    "list_proposals", "mark_applied", "set_status", "skip_job", "mark_not_eligible",
    "confirm_proposal", "reject_proposal", "run_discover",
    "list_resume_versions", "get_resume_version",
}


def _job(conn, slug: str, tracks: list[str], **extra):
    job, _created = jobs_core.upsert_job(
        conn, company="Acme", title=f"Intern {slug}", url=f"https://acme.example/{slug}",
        tracks=tracks, **extra,
    )
    return job


def test_the_server_offers_exactly_the_expected_tools(workspace):
    """Nothing that submits, answers or writes resumes can slip in unnoticed."""
    tools = asyncio.run(build_server(workspace.settings).list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


def test_list_jobs_filters_by_track_tier_and_queue_state(workspace):
    conn = workspace.conn
    first, second = workspace.config.track_names[:2]
    a = _job(conn, "a", [first])
    b = _job(conn, "b", [second])
    c = _job(conn, "c", [first])
    jobs_core.set_tier(conn, a.id, "A")
    jobs_core.set_tier(conn, b.id, "B")
    jobs_core.set_queue_state(conn, c.id, "later")
    tools = ApplyAITools(workspace.settings)

    def ids(**filters) -> set[int]:
        return {job["id"] for job in tools.list_jobs(**filters)["jobs"]}

    assert ids(track=first) == {a.id, c.id}
    assert ids(tier="B") == {b.id}
    assert ids(queue_state="later") == {c.id}
    assert ids(track=first, queue_state="new") == {a.id}
    limited = tools.list_jobs(limit=1)
    assert len(limited["jobs"]) == 1 and limited["total"] == 3
    with pytest.raises(ToolError, match="unknown track"):
        tools.list_jobs(track="no-such-track")


def test_mark_applied_records_the_application_and_its_followups_once(workspace):
    track = workspace.config.track_names[0]
    job = _job(workspace.conn, "a", [track])
    tools = ApplyAITools(workspace.settings)

    with pytest.raises(ToolError, match="mark_applied first"):
        tools.set_status(job.id, "interview")

    result = tools.mark_applied(job.id, applied_on="2026-09-01")
    days = workspace.config.apply.followup_days
    assert result["application"]["status"] == "applied"
    assert result["application"]["track"] == track
    assert len(result["followups"]) == len(days)

    again = tools.mark_applied(job.id)
    assert again["application"]["id"] == result["application"]["id"]
    application_id = result["application"]["id"]
    assert len(reminders_core.followups_for(workspace.conn, application_id)) == len(days)
    assert jobs_core.get_job(workspace.conn, job.id).queue_state == "applied"

    tools.set_status(job.id, "interview", note="phone screen email")
    assert applications_core.get_application(workspace.conn, application_id).status == "interview"


def test_confirming_a_proposal_moves_the_status_and_rejecting_does_not(workspace):
    conn = workspace.conn
    tools = ApplyAITools(workspace.settings)
    track = workspace.config.track_names[0]
    first = applications_core.record_application(conn, _job(conn, "a", [track]).id)
    second = applications_core.record_application(conn, _job(conn, "b", [track]).id)
    to_confirm = applications_core.set_status(conn, first.id, "oa", source="gmail", state="proposed")
    to_reject = applications_core.set_status(conn, second.id, "rejected", source="gmail", state="proposed")

    listed = {p["id"]: p for p in tools.list_proposals()["proposals"]}
    assert set(listed) == {to_confirm.id, to_reject.id}
    assert listed[to_confirm.id]["company"] == "Acme"

    assert tools.confirm_proposal(to_confirm.id)["application_status"] == "oa"
    tools.reject_proposal(to_reject.id)
    assert applications_core.get_application(conn, first.id).status == "oa"
    assert applications_core.get_application(conn, second.id).status == "applied"
    assert tools.list_proposals()["proposals"] == []
    with pytest.raises(ToolError, match="no pending proposal"):
        tools.confirm_proposal(to_reject.id)


def test_tool_errors_reach_the_client_as_error_results(workspace):
    """An agent asking for a missing job gets a readable error, not a crashed server."""
    async def call():
        async with Client(build_server(workspace.settings)) as client:
            return await client.call_tool("get_job", {"job_id": 999})

    result = asyncio.run(call())
    assert result.is_error
    assert "no job with id 999" in result.content[0].text
