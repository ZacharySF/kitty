"""Insights web adapters against isolated fixtures, without live auth or paid calls."""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace

import pytest

from applyai import web_insights as web
from applyai.core import applications, jobs
from applyai.core.finder import store
from applyai.core.triage.run import RunResult, TriagePlan
from applyai.web_server import APIError, WebApplication


class DeferredTasks:
    """Keep the worker separate from the request, as the production runner does."""

    def start(self, kind, work):
        self.work = work
        return {"id": "synthetic-task", "kind": kind, "status": "running",
                "started_at": "2026-01-01", "finished_at": None, "result": None, "error": None}


@pytest.fixture
def app(workspace, tmp_path):
    result = WebApplication(workspace.settings, workspace.config, tmp_path)
    result.tasks = DeferredTasks()
    return result


@pytest.fixture
def posting(workspace):
    job, _ = jobs.upsert_job(workspace.conn, company="Synthetic Company", title="=Synthetic formula",
                             url="https://example.invalid/job", source="manual", source_job_id="insight-job")
    jobs.link_track(workspace.conn, job.id, workspace.config.track_names[0])
    return job


def candidate(workspace, name):
    return store.insert_candidate(workspace.conn, name=name, area=workspace.config.track_names[0],
                                  score=3, reason="Synthetic public evidence", ats_kind=None, ats_slug=None,
                                  board_url=None, website="https://example.invalid", evidence=["Synthetic"], run_id=None)


def test_finder_decisions_persist_and_approval_updates_watchlist(app, workspace):
    approved = candidate(workspace, "Synthetic Approved Firm")
    rejected = candidate(workspace, "Synthetic Rejected Firm")
    with pytest.raises(APIError):
        web.post(app, workspace.conn, f"/api/finder/{approved}/approve", {})
    web.post(app, workspace.conn, f"/api/finder/{approved}/approve", {"confirm": True})
    web.post(app, workspace.conn, f"/api/finder/{rejected}/reject", {"confirm": True})
    assert store.get_candidate(workspace.conn, approved).decision == "approved"
    assert store.get_candidate(workspace.conn, rejected).decision == "rejected"
    assert any(item.name == "Synthetic Approved Firm" for item in app.config.watchlist.companies)
    assert not any(item.name == "Synthetic Rejected Firm" for item in app.config.watchlist.companies)
    with pytest.raises(APIError):
        web.post(app, workspace.conn, f"/api/finder/{approved}/reject", {"confirm": True})


def test_all_exports_include_history_and_escape_csv(app, workspace, posting):
    applied = applications.record_application(workspace.conn, posting.id)
    applications.set_status(workspace.conn, applied.id, "interview")
    content, content_type, filename = web.download(app, workspace.conn, "/api/reports/export", {"format": "json"})
    document = json.loads(content)
    assert document["jobs"][0]["title"] == "=Synthetic formula"
    assert len(document["applications"]) == 1 and len(document["status_events"]) == 2
    assert content_type == "application/json" and filename.endswith(".json")
    content, content_type, _ = web.download(app, workspace.conn, "/api/reports/export", {"format": "csv"})
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert set(archive.namelist()) == {"jobs.csv", "applications.csv", "status_events.csv"}
        assert "'=Synthetic formula" in archive.read("jobs.csv").decode()
    report = web.get(app, workspace.conn, "/api/reports", {})
    assert report["sections"] and report["datasets"] == ["jobs", "applications", "status_events"]


def test_mail_status_does_not_read_credentials_and_proposals_require_decision(app, workspace, posting):
    (workspace.settings.config_dir / "gmail_client.json").write_text("synthetic-client-secret")
    (workspace.settings.data_dir / "gmail_token.json").write_text("synthetic-token-secret")
    applied = applications.record_application(workspace.conn, posting.id)
    proposal = applications.set_status(workspace.conn, applied.id, "interview", state="proposed", source="gmail", evidence="Synthetic recruiting subject")
    state = web.get(app, workspace.conn, "/api/mail", {})
    assert state["ready"] and state["scope"] == "gmail.readonly"
    assert "synthetic-token-secret" not in json.dumps(state)
    assert "synthetic-client-secret" not in json.dumps(state)
    assert state["proposals"][0]["event_id"] == proposal.id
    assert applications.get_application(workspace.conn, applied.id).status == "applied"
    with pytest.raises(APIError):
        web.post(app, workspace.conn, f"/api/mail/proposals/{proposal.id}/confirm", {"confirm": 1})
    web.post(app, workspace.conn, f"/api/mail/proposals/{proposal.id}/confirm", {"confirm": True})
    assert applications.get_application(workspace.conn, applied.id).status == "interview"
    declined = applications.set_status(workspace.conn, applied.id, "rejected", state="proposed", source="gmail")
    web.post(app, workspace.conn, f"/api/mail/proposals/{declined.id}/reject", {"confirm": True})
    assert applications.get_application(workspace.conn, applied.id).status == "interview"


@pytest.mark.parametrize("path,body", [
    ("/api/finder/run", {"confirm": True}),
    ("/api/mail/auth", {"confirm": True}),
    ("/api/mail/check", {"confirm": True}),
    ("/api/triage/run", {"mode": "now", "confirm": True}),
    ("/api/triage/run", {"mode": "collect", "confirm": True}),
    ("/api/sources/run", {"confirm": True}),
    ("/api/postings/refresh", {"confirm": True}),
])
def test_demo_blocks_external_operations(app, workspace, path, body):
    app.settings = replace(app.settings, demo=True)
    with pytest.raises(APIError) as caught:
        web.post(app, workspace.conn, path, body)
    assert caught.value.status == 403


def test_posting_preview_is_local_and_rejects_invalid_ids(app, workspace, posting, monkeypatch):
    monkeypatch.setattr(web.refresh, "refresh_jobs", lambda *args, **kwargs: pytest.fail("No requests in preview"))
    result = web.post(app, workspace.conn, "/api/postings/refresh", {"dry_run": True, "job_ids": [posting.id], "limit": 1})
    assert [item["id"] for item in result["jobs"]] == [posting.id]
    for body in ({"dry_run": True, "job_ids": [True]}, {"dry_run": True, "job_ids": []},
                 {"dry_run": True, "limit": 101}, {"dry_run": True, "track": "not-a-track"}):
        with pytest.raises(APIError):
            web.post(app, workspace.conn, "/api/postings/refresh", body)


def test_triage_fresh_estimate_cap_and_replay_boundary(app, workspace, monkeypatch):
    plan = TriagePlan(system="synthetic", schema={}, resume_lines={}, resume_ids={})
    monkeypatch.setattr(web.triage, "plan_triage", lambda *args, **kwargs: plan)
    monkeypatch.setattr(web.triage, "make_client", lambda *args: pytest.fail("Empty plan must not create paid client"))
    web.post(app, workspace.conn, "/api/triage/estimate", {"limit": 1})
    estimate = app.tasks.work()
    assert estimate["to_send"] == 0 and estimate["now"]["usd"] == 0
    configured_cap = app.config.triage.max_cost_usd_per_run
    for cap in (True, 0, -1, float("nan"), configured_cap + 1):
        with pytest.raises(APIError):
            web.post(app, workspace.conn, "/api/triage/run", {"confirm": True, "mode": "now", "estimate_id": estimate["estimate_id"], "cap_usd": cap})
    observed = []
    def execute(conn, config, actual_plan, client):
        observed.append(config.triage.max_cost_usd_per_run)
        return RunResult(mode="now")
    monkeypatch.setattr(web.triage, "run_now", execute)
    body = {"confirm": True, "mode": "now", "estimate_id": estimate["estimate_id"], "cap_usd": configured_cap / 2}
    web.post(app, workspace.conn, "/api/triage/run", body)
    assert app.tasks.work()["status"] == "ok"
    assert observed == [configured_cap / 2]
    assert app.config.triage.max_cost_usd_per_run == configured_cap
    with pytest.raises(APIError):
        web.post(app, workspace.conn, "/api/triage/run", body)


def test_triage_changed_inputs_prevent_paid_calls(app, workspace, monkeypatch):
    plan = TriagePlan(system="synthetic", schema={}, resume_lines={}, resume_ids={})
    monkeypatch.setattr(web.triage, "plan_triage", lambda *args, **kwargs: plan)
    monkeypatch.setattr(web.triage, "run_now", lambda *args: pytest.fail("Stale estimate must not run"))
    web.post(app, workspace.conn, "/api/triage/estimate", {})
    estimate = app.tasks.work()
    plan.hashes[42] = "changed-synthetic-input"
    web.post(app, workspace.conn, "/api/triage/run", {"confirm": True, "mode": "now", "estimate_id": estimate["estimate_id"], "cap_usd": app.config.triage.max_cost_usd_per_run})
    assert app.tasks.work()["status"] == "stale"


def test_source_health_omits_config_and_error_secrets(app, workspace):
    name = next(iter(app.config.sources.sources))
    workspace.conn.execute("UPDATE sources SET last_error=?, last_status='error' WHERE name=?", ("credential synthetic-private-secret", name))
    data = web.get(app, workspace.conn, "/api/sources", {})
    assert "synthetic-private-secret" not in json.dumps(data)
    assert all("config_json" not in item for item in data["sources"])
    assert next(item for item in data["sources"] if item["name"] == name)["status"] == "error"


def test_background_errors_never_expose_authorization_response(app, workspace, monkeypatch):
    def failure(settings):
        raise ValueError("synthetic-secret /private/synthetic/path")
    monkeypatch.setattr(web.auth, "run_consent_flow", failure)
    web.post(app, workspace.conn, "/api/mail/auth", {"confirm": True})
    with pytest.raises(RuntimeError) as caught:
        app.tasks.work()
    assert "synthetic-secret" not in str(caught.value)
    assert "ValueError" in str(caught.value)


@pytest.mark.parametrize("path,body", [
    ("/api/finder/run", {"confirm": True, "cap": True}),
    ("/api/mail/check", {"confirm": True, "days": 0}),
    ("/api/triage/estimate", {"limit": -1}),
    ("/api/triage/run", {"confirm": True, "mode": []}),
    ("/api/mail/auth", {"confirm": True, "scope": "gmail.modify"}),
])
def test_invalid_parameters_rejected_before_task_start(app, workspace, path, body):
    with pytest.raises(APIError):
        web.post(app, workspace.conn, path, body)


def test_source_toggle_persists_and_selected_run_remains_bounded(app, workspace, monkeypatch):
    from applyai.config import load_config
    from applyai.core.discovery.pipeline import DiscoverResult, SourceTally
    from applyai.web_server import FREE_SOURCES

    name = next(name for name in app.config.sources.sources if name in FREE_SOURCES and name in web.ADAPTERS)
    path = f"/api/sources/{name}/toggle"
    with pytest.raises(APIError):
        web.post(app, workspace.conn, path, {"enabled": False})
    web.post(app, workspace.conn, path, {"confirm": True, "enabled": False})
    assert not load_config(workspace.settings.config_dir).sources.sources[name].enabled
    with pytest.raises(APIError):
        web.post(app, workspace.conn, f"/api/sources/{name}/run", {"confirm": True})
    web.post(app, workspace.conn, path, {"confirm": True, "enabled": True})
    selected = []
    def discover(settings, config, *, only):
        selected.extend(only)
        return DiscoverResult(1, "2026-01-01", {name: SourceTally(status="partial", error="synthetic-secret")})
    monkeypatch.setattr(web, "run_discover", discover)
    web.post(app, workspace.conn, f"/api/sources/{name}/run", {"confirm": True})
    result = app.tasks.work()
    assert selected == [name] and result["sources"][name]["status"] == "partial"
    assert "synthetic-secret" not in json.dumps(result)
    assert app.discovery["running"] is False


def test_batch_and_collect_reuse_core_without_new_requests_on_collect(app, workspace, monkeypatch):
    plan = TriagePlan(system="synthetic", schema={}, resume_lines={}, resume_ids={})
    monkeypatch.setattr(web.triage, "plan_triage", lambda *args, **kwargs: plan)
    monkeypatch.setattr(web.triage, "make_client", lambda *args: pytest.fail("No request is needed"))
    monkeypatch.setattr(web.triage, "submit_batch", lambda *args: RunResult(mode="batch", pending=False))
    web.post(app, workspace.conn, "/api/triage/estimate", {})
    estimate = app.tasks.work()
    web.post(app, workspace.conn, "/api/triage/run", {
        "confirm": True, "mode": "batch", "estimate_id": estimate["estimate_id"],
        "cap_usd": app.config.triage.max_cost_usd_per_run,
    })
    assert app.tasks.work()["mode"] == "batch"
    web.post(app, workspace.conn, "/api/triage/run", {"confirm": True, "mode": "collect"})
    assert app.tasks.work() == {"batches": []}
