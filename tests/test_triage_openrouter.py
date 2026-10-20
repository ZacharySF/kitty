"""Isolated OpenRouter triage tests; all inference uses synthetic HTTP responses."""
from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from kitty.config import TriageFile
from kitty.core import jobs as jobs_core
from kitty.core import resumes as resumes_core
from kitty.core.triage import openrouter
from kitty.core.triage.run import input_hash, plan_triage


@pytest.fixture
def router_space(workspace, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-openrouter-test-key")
    workspace.config = replace(workspace.config, triage=TriageFile(
        provider="openrouter", model="typesafe/jev-router"))
    track = next(t for t in workspace.config.tracks.tracks if t.triage_depth == "llm" and t.default_resume)
    path = workspace.settings.root / "resumes" / "synthetic.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text("Pat Example | synthetic@example.invalid | (555) 010-1234\nProgramming: Python\nBuilt firmware")
    resumes_core.add_resume(workspace.conn, track.default_resume, path)
    workspace.track = track
    workspace.label = track.default_resume
    job, _ = jobs_core.upsert_job(workspace.conn, company="Synthetic Robotics", title="Firmware Intern",
        url="https://example.invalid/jobs/one", jd_text="Python required. Build firmware.",
        tracks=[track.name], eligibility="eligible")
    workspace.job_id = job.id
    workspace.conn.commit()
    return workspace


def _answer(space):
    return {"tier": "A", "tier_reason": "Firmware role.", "resume": space.label,
            "resume_reason": "Lists Python.", "edit_notes": [
                {"jd_phrase": "Python required", "resume_line": "Programming: Python", "note": "Keep Python visible."},
                {"jd_phrase": "Invented qualification", "resume_line": "", "note": "Discard this ungrounded advice."}]}


def _response(space, *, cost=0.001, text=None, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": text if text is not None else json.dumps(_answer(space))}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": cost}}


def _mock(monkeypatch, handler):
    sent = []
    real_client = httpx.Client

    def route(request):
        sent.append(request)
        return handler(request)

    monkeypatch.setattr(openrouter.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(route), **kwargs))
    return sent


def _plan(space):
    return plan_triage(space.conn, space.config, space.settings)


def _run(space, plan=None):
    return openrouter.run_now(space.conn, space.config, plan or _plan(space), space.settings)


def _stats(space, result):
    return json.loads(space.conn.execute("SELECT stats_json FROM runs WHERE id = ?", (result.run_id,)).fetchone()[0])


def _second_job(space):
    job, _ = jobs_core.upsert_job(space.conn, company="Synthetic Controls", title="Embedded Intern",
        url="https://example.invalid/jobs/two", jd_text="Python required. Build firmware.",
        tracks=[space.track.name], eligibility="eligible")
    space.conn.commit()
    return job


def test_router_request_redaction_validation_and_replay(router_space, monkeypatch):
    space = router_space
    sent = _mock(monkeypatch, lambda request: httpx.Response(200, json=_response(space)))
    plan = _plan(space)
    result = _run(space, plan)
    assert (result.sent, result.applied, result.notes_dropped) == (1, 1, 1)
    assert result.cost_usd == pytest.approx(0.001)
    job = jobs_core.get_job(space.conn, space.job_id)
    assert (job.tier, job.tier_source, job.resume_label) == ("A", "llm", space.label)
    assert len(job.edit_notes) == 1
    payload = json.loads(sent[0].content)
    assert payload["model"] == "typesafe/jev-router"
    assert payload["max_tokens"] == space.config.triage.max_output_tokens
    assert payload["stream"] is False and "tools" not in payload
    assert payload["provider"] == {
        "data_collection": "deny", "require_parameters": True, "allow_fallbacks": False,
        "max_price": {"prompt": 2.0, "completion": 10.0, "request": 0, "image": 0}}
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "synthetic@example.invalid" not in payload["messages"][0]["content"]
    assert "555" not in payload["messages"][0]["content"]
    assert not result.errors
    # Even the same stale plan cannot repeat the charge.
    replay = _run(space, plan)
    assert replay.sent == 0 and len(sent) == 1
    assert _plan(space).to_send == []
    # A reset automatic tier reuses its cached answer, without reading a key.
    jobs_core.set_tier(space.conn, space.job_id, "B", reason="automatic", tier_source="rules")
    monkeypatch.setattr(openrouter, "_key", lambda settings: pytest.fail("cached run must not read a key"))
    cached = _run(space)
    assert cached.cached_applied == 1 and cached.sent == 0


def test_provider_identity_separates_the_same_model_cache(router_space, monkeypatch):
    space = router_space
    plan = _plan(space)
    anthropic_config = replace(space.config, triage=space.config.triage.model_copy(update={"provider": "anthropic"}))
    assert input_hash(anthropic_config, plan.system, plan.users[space.job_id]) != plan.hashes[space.job_id]
    _mock(monkeypatch, lambda request: httpx.Response(200, json=_response(space)))
    _run(space)
    space.config = anthropic_config
    assert [job.id for job in _plan(space).to_send] == [space.job_id]


def test_estimate_bounds_utf8_schema_and_full_output_limit(router_space):
    space = router_space
    plan = _plan(space)
    plan.users[space.job_id] += "\n" + "é🦊" * 100
    params = openrouter.request_params(space.config, plan, plan.users[space.job_id])
    estimate = openrouter.plan_estimate(plan, space.config)
    assert estimate.jobs == 1
    assert estimate.input_tokens >= len(json.dumps(params, ensure_ascii=False).encode("utf-8")) + 1024
    assert estimate.output_tokens == space.config.triage.max_output_tokens
    assert estimate.output_tokens > space.config.triage.est_output_tokens
    expected = (estimate.input_tokens * 2 + estimate.output_tokens * 10) / 1_000_000
    assert estimate.usd == pytest.approx(expected)


@pytest.mark.parametrize("cost", [0, None])
def test_cap_never_recycles_a_cheaper_or_unknown_charge(router_space, monkeypatch, cost):
    space = router_space
    _second_job(space)
    plan = _plan(space)
    bounds = [openrouter._bound(openrouter.request_params(space.config, plan, plan.users[j.id]), space.config)[1]
              for j in plan.to_send]
    cap = float(max(bounds) * Decimal("1.1"))
    space.config = replace(space.config, triage=space.config.triage.model_copy(update={"max_cost_usd_per_run": cap}))
    sent = _mock(monkeypatch, lambda request: httpx.Response(200, json=_response(space, cost=cost)))
    result = _run(space, plan)
    assert len(sent) == result.sent == 1
    assert result.cap_hit and result.skipped_for_cap == 1
    assert _stats(space, result)["reserved_usd"] == pytest.approx(float(bounds[0]))
    assert result.cost_usd == pytest.approx(float(bounds[0]) if cost is None else 0)
    assert _stats(space, result)["cost_is_upper_bound"] is (cost is None)


def test_zero_cap_does_not_read_a_key_or_create_client(router_space, monkeypatch):
    space = router_space
    space.config = replace(space.config, triage=space.config.triage.model_copy(update={"max_cost_usd_per_run": 0}))
    monkeypatch.setattr(openrouter, "_key", lambda settings: pytest.fail("no key needed before cap"))
    monkeypatch.setattr(openrouter.httpx, "Client", lambda **kwargs: pytest.fail("no network before cap"))
    result = _run(space)
    assert result.cap_hit and result.sent == 0 and result.cost_usd == 0


@pytest.mark.parametrize("failure", ["timeout", "http", "api_error"])
def test_failed_paid_attempt_is_retained_and_not_retried(router_space, monkeypatch, failure):
    space = router_space
    _second_job(space)
    plan = _plan(space)
    expected = openrouter._bound(openrouter.request_params(space.config, plan, plan.users[plan.to_send[0].id]), space.config)[1]

    def handler(request):
        # The possible charge is already durable before the outbound request.
        row = space.conn.execute("SELECT stats_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        assert json.loads(row[0])["reserved_usd"] == pytest.approx(float(expected))
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-openrouter-test-key", request=request)
        if failure == "http":
            return httpx.Response(429, text="synthetic-openrouter-test-key")
        return httpx.Response(200, json={"error": {"message": "synthetic-openrouter-test-key"}})

    sent = _mock(monkeypatch, handler)
    result = _run(space, plan)
    assert result.sent == len(sent) == 1 and result.applied == 0
    assert result.cost_usd == pytest.approx(float(expected))
    stats = _stats(space, result)
    assert stats["cost_is_upper_bound"] is True
    assert stats["reserved_usd"] == pytest.approx(float(expected))
    assert "synthetic-openrouter-test-key" not in json.dumps(stats)
    assert space.conn.execute("SELECT finished_at FROM runs WHERE id = ?", (result.run_id,)).fetchone()[0]


@pytest.mark.parametrize("body", [None, [], {"choices": []}, {"choices": "bad"}])
def test_malformed_envelopes_keep_unknown_charge(router_space, monkeypatch, body):
    space = router_space
    sent = _mock(monkeypatch, lambda request: httpx.Response(200, content=b"not JSON") if body is None
                 else httpx.Response(200, json=body))
    result = _run(space)
    assert len(sent) == 1 and result.applied == 0 and result.errors
    assert result.cost_usd > 0 and _stats(space, result)["cost_is_upper_bound"]
    assert not space.conn.execute("SELECT 1 FROM llm_cache").fetchone()


@pytest.mark.parametrize("text", ["not JSON", "[]", "null", '{"tier": "A"}',
    '{"tier":"A","tier_reason":"x","resume":[],"resume_reason":"x","edit_notes":[]}',
    '{"tier":"A","tier_reason":"x","resume":"x","resume_reason":"x","edit_notes":{}}'])
def test_malformed_answer_json_never_changes_a_job(router_space, monkeypatch, text):
    space = router_space
    _mock(monkeypatch, lambda request: httpx.Response(200, json=_response(space, text=text)))
    result = _run(space)
    assert result.applied == 0 and result.errors and result.cost_usd == pytest.approx(0.001)
    assert jobs_core.get_job(space.conn, space.job_id).tier_source != "llm"
    assert not space.conn.execute("SELECT 1 FROM llm_cache").fetchone()


def test_manual_tier_and_chosen_resume_survive_stale_plans(router_space, monkeypatch):
    space = router_space
    plan = _plan(space)
    jobs_core.set_tier(space.conn, space.job_id, "C", reason="my decision", tier_source="manual")
    monkeypatch.setattr(openrouter, "_key", lambda settings: pytest.fail("manual job must not be sent"))
    result = _run(space, plan)
    assert result.sent == result.applied == 0
    assert jobs_core.get_job(space.conn, space.job_id).tier == "C"


def test_existing_resume_choice_is_not_replaced(router_space, monkeypatch):
    space = router_space
    path = space.settings.root / "resumes" / "other.txt"
    path.write_text("Synthetic alternative resume")
    chosen = resumes_core.add_resume(space.conn, "other", path)
    jobs_core.set_resume(space.conn, space.job_id, chosen.id)
    _mock(monkeypatch, lambda request: httpx.Response(200, json=_response(space)))
    result = _run(space)
    assert result.applied == 1
    assert jobs_core.get_job(space.conn, space.job_id).resume_id == chosen.id


@pytest.mark.parametrize("cost", [-1, "NaN", "Infinity", True, "bad"])
def test_invalid_reported_cost_stays_reserved(router_space, monkeypatch, cost):
    space = router_space
    _mock(monkeypatch, lambda request: httpx.Response(200, json=_response(space, cost=cost)))
    result = _run(space)
    assert result.applied == 1 and result.cost_usd > 0
    assert _stats(space, result)["cost_is_upper_bound"] is True


def test_over_ceiling_charge_is_recorded_and_stops_run(router_space, monkeypatch):
    space = router_space
    _second_job(space)
    sent = _mock(monkeypatch, lambda request: httpx.Response(200, json=_response(space, cost=1)))
    result = _run(space)
    assert len(sent) == 1 and result.applied == 0 and result.cost_usd == 1
    assert any("above the reserved ceiling" in error for error in result.errors)


@pytest.mark.parametrize("field", ["openrouter_max_input_price", "openrouter_max_output_price", "max_cost_usd_per_run"])
@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_price_and_cap_configuration_are_finite_and_nonnegative(field, value):
    with pytest.raises(ValidationError):
        TriageFile(**{field: value})


def test_private_key_storage_is_owner_only_and_rejects_symlinks(router_space):
    space = router_space
    directory = space.settings.root / ".private"
    directory.mkdir(mode=0o700)
    path = directory / "openrouter-key"
    path.write_text("synthetic-file-key")
    path.chmod(0o644)
    with pytest.raises(openrouter.TriageError, match="owner-only"):
        openrouter._key(space.settings)
    path.chmod(0o600)
    assert openrouter._key(space.settings) == "synthetic-file-key"
    path.unlink()
    path.symlink_to(space.settings.root / "resumes" / "synthetic.txt")
    with pytest.raises(openrouter.TriageError, match="symbolic link"):
        openrouter._key(space.settings)


def _daily_cap(space, plan, *, factor="1.1"):
    maximum = openrouter._bound(openrouter.request_params(
        space.config, plan, plan.users[plan.to_send[0].id]), space.config)[1]
    space.config = replace(space.config, triage=space.config.triage.model_copy(update={
        "openrouter_daily_budget_usd": float(maximum * Decimal(factor))}))
    return maximum


@pytest.mark.parametrize("failed", [False, True])
def test_daily_cap_survives_new_connection_and_failed_requests(router_space, monkeypatch, failed):
    from kitty.db import connect

    space = router_space
    plan = _plan(space)
    maximum = _daily_cap(space, plan)

    def handler(request):
        if failed:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        # A malformed paid answer leaves the same job eligible in the next run.
        return httpx.Response(200, json=_response(space, cost=0, text="not JSON"))

    sent = _mock(monkeypatch, handler)
    first = _run(space, plan)
    assert first.sent == 1
    space.conn.close()
    space.conn = connect(space.settings.db_path)
    second = _run(space)
    assert second.sent == 0 and second.cap_hit and len(sent) == 1
    assert sum(Decimal(value) for value in _stats(space, first)["reserved_by_utc_day"].values()) == maximum


def test_daily_cap_counts_incomplete_requests_while_another_connection_runs(router_space, monkeypatch):
    from kitty.db import connect

    space = router_space
    plan = _plan(space)
    _daily_cap(space, plan)
    overlapping = []

    def handler(request):
        # The first run has not received a response and remains unfinished.
        other = connect(space.settings.db_path)
        try:
            unfinished = other.execute("SELECT finished_at FROM runs WHERE id = 1").fetchone()
            assert unfinished[0] is None
            second = openrouter.run_now(other, space.config, plan, space.settings)
            overlapping.append(second)
            assert second.sent == 0 and second.cap_hit
        finally:
            other.close()
        return httpx.Response(200, json=_response(space))

    sent = _mock(monkeypatch, handler)
    first = _run(space, plan)
    assert first.sent == 1 and len(sent) == 1 and len(overlapping) == 1


def test_daily_cap_rolls_over_at_utc_midnight(router_space, monkeypatch):
    space = router_space
    plan = _plan(space)
    _daily_cap(space, plan)
    day = ["2026-10-15"]
    monkeypatch.setattr(openrouter, "now_iso", lambda: day[0] + "T12:00:00Z")
    sent = _mock(monkeypatch, lambda request: httpx.Response(
        200, json=_response(space, cost=0, text="not JSON")))
    first = _run(space)
    blocked = _run(space)
    assert first.sent == 1 and blocked.sent == 0 and blocked.cap_hit
    day[0] = "2026-10-16"
    tomorrow = _run(space)
    assert tomorrow.sent == 1 and len(sent) == 2
    assert "2026-10-15" in _stats(space, first)["reserved_by_utc_day"]
    assert "2026-10-16" in _stats(space, tomorrow)["reserved_by_utc_day"]


def test_zero_daily_budget_blocks_before_key_access(router_space, monkeypatch):
    space = router_space
    space.config = replace(space.config, triage=space.config.triage.model_copy(update={
        "openrouter_daily_budget_usd": 0}))
    monkeypatch.setattr(openrouter, "_key", lambda settings: pytest.fail("daily cap must precede key access"))
    result = _run(space)
    assert result.sent == 0 and result.cap_hit


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_daily_budget_is_finite_nonnegative(value):
    with pytest.raises(ValidationError):
        TriageFile(openrouter_daily_budget_usd=value)


def test_exact_daily_boundary_allows_only_one_reservation(router_space, monkeypatch):
    space = router_space
    plan = _plan(space)
    maximum = _daily_cap(space, plan, factor="1")
    sent = _mock(monkeypatch, lambda request: httpx.Response(
        200, json=_response(space, cost=0, text="not JSON")))
    first = _run(space, plan)
    second = _run(space, plan)
    assert first.sent == 1 and second.sent == 0 and second.cap_hit and len(sent) == 1
    assert _stats(space, first)["reserved_usd"] == pytest.approx(float(maximum))


def test_no_daily_budget_preserves_per_run_behavior(router_space, monkeypatch):
    space = router_space
    assert space.config.triage.openrouter_daily_budget_usd is None
    sent = _mock(monkeypatch, lambda request: httpx.Response(
        200, json=_response(space, cost=0, text="not JSON")))
    assert _run(space).sent == 1
    assert _run(space).sent == 1
    assert len(sent) == 2
