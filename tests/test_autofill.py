"""Which favorites one autofill run fills (no browser: only the selection)."""

from __future__ import annotations

from dataclasses import replace

from applyai.core import applications as applications_core
from applyai.core import favorites
from applyai.core import jobs as jobs_core
from applyai.core.apply.autofill import plan_run
from applyai.core.apply.state import record_attempt


def _favorite(workspace, name, *, url=None, tier="B"):
    """A starred job on the first track; Greenhouse unless `url` says otherwise."""
    job, _ = jobs_core.upsert_job(
        workspace.conn, company=f"{name} Co", title=f"{name} Intern",
        url=url or f"https://job-boards.greenhouse.io/{name.lower()}/jobs/{abs(hash(name)) % 10**9}",
        location="Austin, TX", tracks=[workspace.config.track_names[0]],
    )
    jobs_core.set_tier(workspace.conn, job.id, tier, reason="test", tier_source="manual")
    jobs_core.set_eligibility(workspace.conn, job.id, "eligible")
    favorites.favorite(workspace.conn, [job.id])
    return job.id


def _reasons(plan) -> dict[int, str]:
    return {left.job_id: left.reason for left in plan.left}


def test_plan_takes_open_unfilled_favorites_and_says_why_it_leaves_the_rest(workspace):
    conn = workspace.conn
    ready = _favorite(workspace, "Ready")
    applied = _favorite(workspace, "Applied")
    applications_core.record_application(conn, applied, track=workspace.config.track_names[0])
    skipped = _favorite(workspace, "Skipped")
    favorites.skip(conn, [skipped])
    not_eligible = _favorite(workspace, "Unfit")
    favorites.mark_not_eligible(conn, [not_eligible])
    filled = _favorite(workspace, "Filled")
    record_attempt(conn, filled, "ready")
    workday = _favorite(workspace, "Workday", url="https://acme.wd1.myworkdayjobs.com/External/job/X_1")
    closed = _favorite(workspace, "Closed")
    jobs_core.close_job(conn, closed, reason="test")
    _plain_job, _ = jobs_core.upsert_job(conn, company="Plain Co", title="Plain Intern",
                                         url="https://job-boards.greenhouse.io/plain/jobs/1")

    plan = plan_run(conn, workspace.config)

    assert set(plan.job_ids) == {ready, workday}         # Workday is inspected, not assumed gated
    reasons = _reasons(plan)
    assert set(reasons) == {applied, skipped, not_eligible, filled, closed}
    assert "applied" in reasons[applied]
    assert "skipped" in reasons[skipped]
    assert "not_eligible" in reasons[not_eligible]
    assert "verified ready for review" in reasons[filled]
    assert "closed" in reasons[closed]


def test_plan_respects_the_cap_and_the_limit_best_first(workspace):
    ids = [_favorite(workspace, f"Fav{index}", tier="B") for index in range(4)]
    tier_a = _favorite(workspace, "Best", tier="A")
    config = replace(workspace.config, apply=workspace.config.apply.model_copy(update={"autofill_cap": 3}))

    plan = plan_run(workspace.conn, config)
    assert len(plan.job_ids) == 3 and plan.job_ids[0] == tier_a     # best first: tier A leads
    over = [left for left in plan.left if "cap of 3" in left.reason]
    assert len(over) == 2 and {left.job_id for left in over} <= set(ids)

    assert len(plan_run(workspace.conn, config, limit=1).job_ids) == 1
    assert len(plan_run(workspace.conn, config, limit=10).job_ids) == 3   # limit never exceeds the cap
