"""Isolated browser-adapter regressions: synthetic credentials, no browser or HTTP requests."""
from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kitty import web_automation as web
from kitty.core import applications, favorites, form_questions, jobs, resumes
from kitty.core.apply.filler import FillReport
from kitty.core.apply.watch import PageView, WatchedTab, watch_tabs
from kitty.db import connect
from kitty.web_server import APIError


@pytest.fixture
def app(workspace):
    controller = web.AutomationController(workspace.settings)
    result = SimpleNamespace(settings=workspace.settings, config=workspace.config, automation=controller)
    yield result
    controller.close()


@pytest.fixture
def posting(workspace):
    job, _ = jobs.upsert_job(
        workspace.conn, company="Synthetic Example", title="Synthetic internship",
        url="https://boards.greenhouse.io/synthetic/jobs/1234567", source="manual", source_job_id="synthetic-auto",
    )
    jobs.link_track(workspace.conn, job.id, workspace.config.track_names[0])
    jobs.set_tier(workspace.conn, job.id, "A", reason="Synthetic fixture")
    return job


@pytest.fixture
def saved_credentials(workspace):
    from kitty.core.apply.credentials import CREDENTIALS_FILE
    path = workspace.settings.root / CREDENTIALS_FILE
    path.parent.mkdir(exist_ok=True)
    path.write_text("APPLICATION_ACCOUNT_EMAIL=web.synthetic@example.invalid\n"
                    "APPLICATION_ACCOUNT_PASSWORD=Synthetic-Web-Pass#42\n")
    path.chmod(0o600)
    return path


def report(job_id, status="partial"):
    return FillReport(job_id, "Synthetic Example", "Synthetic internship", "https://example.invalid/form",
                      "greenhouse", None, ["first name"], ["Synthetic unanswered question"], status)


def finish(controller):
    controller._thread.join(timeout=3)
    assert not controller._thread.is_alive()
    return controller.snapshot()



def controller_plan(app, posting):
    conn = connect(app.settings.db_path)
    try:
        return web.preparation_plan(app, conn, {"mode": "selected", "job_ids": [posting.id]})
    finally:
        conn.close()


@pytest.fixture
def pdf_pair(workspace, posting):
    from pypdf import PdfWriter
    registered = []
    for index in range(2):
        path = workspace.settings.root / f"review-{index}.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612 + index, height=792)
        writer.write(path)
        registered.append(resumes.add_resume(workspace.conn, f"Review {index}", path))
    workspace.conn.execute(
        "INSERT INTO resume_default_overrides (track, resume_id) VALUES (?, ?)",
        (workspace.config.track_names[0], registered[0].id),
    )
    return registered

def test_local_plans_cap_and_exclusions_never_apply(app, workspace, posting):
    favorites.favorite(workspace.conn, [posting.id])
    for mode in ("selected", "queue", "top", "favorites"):
        body = {"mode": mode, "limit": 1}
        if mode == "selected":
            body["job_ids"] = [posting.id]
        plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
        assert plan["job_ids"] == [posting.id]
        assert plan["cap"] == 1
        assert plan["jobs"][0]["fill_state"] == "unattempted"
        assert "Historical" in plan["note"]
    favorites.set_state(workspace.conn, [posting.id], "skipped")
    plan = web.post(app, workspace.conn, "/api/automation/plan", {"mode": "favorites"})
    assert not plan["job_ids"]
    assert "skipped" in plan["left"][0]["reason"]
    assert applications.get_application_for_job(workspace.conn, posting.id) is None


@pytest.mark.parametrize("body", [
    {"mode": "selected", "job_ids": [True]}, {"limit": 0}, {"limit": True},
    {"track": False}, {"track": "unknown"}, {"mode": "queue", "job_ids": [1]},
    {"mode": "selected"}, {"submit": True}, {"mode": []}, {"include_ineligible": True},
])
def test_invalid_plan_parameters(app, workspace, body):
    with pytest.raises(APIError) as caught:
        web.post(app, workspace.conn, "/api/automation/plan", body)
    assert caught.value.status == 400


@pytest.mark.parametrize("confirmation", [None, False, 1, "true"])
def test_external_actions_require_exact_confirmation(app, workspace, posting, monkeypatch, confirmation):
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *a, **k: pytest.fail("Browser must not start"))
    for route in ("/api/automation/start", "/api/questions/collect"):
        body = {"confirmed": confirmation}
        if route.endswith("/start"):
            body.update(mode="selected", job_ids=[posting.id])
        with pytest.raises(APIError) as caught:
            web.post(app, workspace.conn, route, body)
        assert caught.value.status == 400
    assert applications.get_application_for_job(workspace.conn, posting.id) is None


def test_demo_blocks_external_actions_and_controller(app, workspace, posting, monkeypatch):
    app.settings = replace(app.settings, demo=True)
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *a, **k: pytest.fail("Browser must not start"))
    for route in ("/api/automation/start", "/api/questions/collect"):
        with pytest.raises(APIError) as caught:
            web.post(app, workspace.conn, route, {"confirmed": True})
        assert caught.value.status == 403
    controller = web.AutomationController(app.settings)
    with pytest.raises(APIError):
        controller.start(app.config, controller_plan(app, posting))
    controller.close()


def test_worker_serializes_filling_and_replaces_watcher_without_losing_tabs(app, workspace, posting, monkeypatch):
    entered, allow_watch, watching = threading.Event(), threading.Event(), threading.Event()
    watches = []
    tab = WatchedTab(posting.id, "synthetic-context", "greenhouse", posting.url, "Synthetic", None, None, marker="synthetic-context")

    def fill(settings, config, ids, *, watch, on_filled, **kwargs):
        if not watches:
            watch.tabs.append(tab)
        else:
            assert watch.tabs == [tab]
        watches.append(watch)
        entered.set()
        assert allow_watch.wait(3)
        on_filled([report(posting.id)])
        watch.on_start(len(watch.tabs))
        watching.set()
        assert watch.stop.wait(3)

    monkeypatch.setattr(web.filler, "fill_jobs", fill)
    app.automation.start(app.config, controller_plan(app, posting))
    assert entered.wait(3)
    assert app.automation.snapshot()["phase"] == "filling"
    with pytest.raises(APIError) as caught:
        app.automation.start(app.config, controller_plan(app, posting))
    assert caught.value.status == 409
    allow_watch.set()
    assert watching.wait(3)
    assert app.automation.snapshot()["phase"] == "watching"
    snapshot = app.automation.snapshot()
    snapshot["reports"].clear()
    assert app.automation.snapshot()["reports"]
    old_thread = app.automation._thread
    watching.clear()
    app.automation.start(app.config, controller_plan(app, posting))
    assert watching.wait(3)
    assert not old_thread.is_alive()
    assert watches[0].stop.is_set()
    assert len(watches) == 2
    app.automation.close()
    assert not app.automation.snapshot()["running"]
    assert applications.get_application_for_job(workspace.conn, posting.id) is None
    with pytest.raises(APIError):
        app.automation.start(app.config, controller_plan(app, posting))


def test_worker_exception_is_sanitized_and_releases_slot(app, posting, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("secret /private/person/password")
    monkeypatch.setattr(web.filler, "fill_jobs", broken)
    app.automation.start(app.config, controller_plan(app, posting))
    state = finish(app.automation)
    assert state["phase"] == "failed"
    assert state["error"] == "Browser worker failed (RuntimeError)"
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *args, **kwargs: kwargs["on_filled"]([report(posting.id)]))
    app.automation.start(app.config, controller_plan(app, posting))
    assert finish(app.automation)["phase"] == "completed"


def test_error_reports_are_not_success_and_do_not_leak_paths(app, posting, monkeypatch):
    failed = report(posting.id, "error")
    failed.error = "cannot open /private/resume.pdf"
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *args, **kwargs: kwargs["on_filled"]([failed]))
    app.automation.start(app.config, controller_plan(app, posting))
    state = finish(app.automation)
    assert state["phase"] == "failed"
    assert "/private" not in json.dumps(state)


@pytest.mark.parametrize("confirmed", [False, True])
def test_only_observed_confirmation_records_submission(app, workspace, posting, monkeypatch, confirmed):
    tab = WatchedTab(posting.id, "synthetic-context", "greenhouse", posting.url,
                     "Synthetic application", None, workspace.config.track_names[0], marker="synthetic-context")

    def fill(settings, config, ids, *, watch, on_filled, **kwargs):
        owned = connect(settings.db_path)
        try:
            watch.tabs.append(tab)
            on_filled([report(posting.id, "ready")])
            watch.on_start(1)
            views = iter([PageView([posting.url + "/confirmation" if confirmed else posting.url]), None])
            watch_tabs(lambda _: next(views), owned, config, watch, lambda _: None, poll_seconds=0)
        finally:
            owned.close()

    monkeypatch.setattr(web.filler, "fill_jobs", fill)
    app.automation.start(app.config, controller_plan(app, posting))
    state = finish(app.automation)
    application = applications.get_application_for_job(workspace.conn, posting.id)
    assert (application is not None) == confirmed
    assert bool(state["observed"]) == confirmed
    if application:
        assert application.status == "applied"
        assert len(applications.history(workspace.conn, application.id)) == 1


def test_stale_plan_is_rejected_before_browser(app, workspace, posting, monkeypatch):
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *a, **k: pytest.fail("Browser must not start"))
    with pytest.raises(APIError) as caught:
        web.post(app, workspace.conn, "/api/automation/start", {
            "mode": "selected", "job_ids": [posting.id], "confirmed": True, "fingerprint": "stale",
        })
    assert caught.value.status == 409


def test_questions_log_list_and_collection_counters(app, workspace, posting, monkeypatch):
    result = web.post(app, workspace.conn, "/api/questions", {"text": "Synthetic exact question?", "job_id": posting.id})
    listed = web.get(app, workspace.conn, "/api/questions", {"job_id": str(posting.id)})
    assert listed["questions"][0]["id"] == result["id"]
    assert listed["questions"][0]["text"] == "Synthetic exact question?"
    seen = []
    def collect(conn, settings, config, **kwargs):
        assert conn is not workspace.conn
        seen.append(kwargs)
        return form_questions.CollectResult(considered=2, skipped=Counter({"unsupported": 1}), failed=Counter({"HTTP 403": 1}))
    monkeypatch.setattr(form_questions, "collect", collect)
    app.tasks = SimpleNamespace(start=lambda kind, work: {"kind": kind, "result": work()})
    result = web.post(app, workspace.conn, "/api/questions/collect", {"confirmed": True, "limit": 2})
    assert result["result"]["failed"] == {"HTTP 403": 1}
    assert result["result"]["skipped"] == {"unsupported": 1}
    json.dumps(result)
    assert seen[0]["limit"] == 2
    assert applications.get_application_for_job(workspace.conn, posting.id) is None


def test_no_watch_stops_before_observation_but_retains_tabs(app, posting, monkeypatch):
    tab = WatchedTab(posting.id, "synthetic-context", "greenhouse", posting.url, "Synthetic", None, None, marker="synthetic-context")
    def fill(settings, config, ids, *, watch, on_filled, **kwargs):
        assert watch.hours > 0
        assert not watch.stop.is_set()
        watch.tabs.append(tab)
        on_filled([report(posting.id)])
        assert watch.stop.is_set()
    monkeypatch.setattr(web.filler, "fill_jobs", fill)
    app.automation.start(app.config, controller_plan(app, posting), watch=False)
    state = finish(app.automation)
    assert state["phase"] == "completed"
    assert state["observed"] == []
    assert app.automation._tabs == [tab]


def test_queued_mode_only_selects_manually_queued_jobs(app, workspace, posting):
    assert not web.preparation_plan(app, workspace.conn, {"mode": "queued"})["job_ids"]
    favorites.set_state(workspace.conn, [posting.id], "queued")
    assert web.preparation_plan(app, workspace.conn, {"mode": "queued"})["job_ids"] == [posting.id]


def test_authentication_requires_separate_explicit_authorization(app, workspace, posting, monkeypatch):
    policy = app.config.apply.authentication.model_copy(update={
        "enabled": True, "approved_origins": ["https://synthetic.example"],
    })
    app.config = replace(app.config, apply=app.config.apply.model_copy(update={"authentication": policy}))
    captured = []
    monkeypatch.setattr(app.automation, "start", lambda config, *a, **k: captured.append(config))
    body = {"mode": "selected", "job_ids": [posting.id]}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    web.post(app, workspace.conn, "/api/automation/start", {
        **body, "confirmed": True, "fingerprint": plan["fingerprint"],
    })
    assert not captured[0].apply.authentication.enabled
    assert app.config.apply.authentication.enabled


def test_changed_registered_resume_blocks_upload(app, workspace, posting, pdf_pair, monkeypatch):
    body = {"mode": "selected", "job_ids": [posting.id]}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    from pathlib import Path
    Path(pdf_pair[0].file_path).write_bytes(Path(pdf_pair[1].file_path).read_bytes())
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *a, **k: pytest.fail("Changed PDF must not be uploaded"))
    with pytest.raises(APIError, match="review the plan again") as caught:
        web.post(app, workspace.conn, "/api/automation/start", {
            **body, "confirmed": True, "fingerprint": plan["fingerprint"],
        })
    assert caught.value.status == 409


def test_watcher_disconnect_is_not_reported_as_success(app, posting, monkeypatch):
    def disconnected(*args, **kwargs):
        kwargs["on_filled"]([report(posting.id)])
        kwargs["progress"]("stopped watching: secret browser connection details")
    monkeypatch.setattr(web.filler, "fill_jobs", disconnected)
    app.automation.start(app.config, controller_plan(app, posting))
    state = finish(app.automation)
    assert state["phase"] == "failed"
    assert "disconnected" in state["error"]
    assert "secret" not in state["error"]


@pytest.mark.parametrize("fingerprint", [None, "", False, [], 42])
def test_start_requires_review_fingerprint(app, workspace, posting, monkeypatch, fingerprint):
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *a, **k: pytest.fail("Browser must not start"))
    body = {"mode": "selected", "job_ids": [posting.id], "confirmed": True}
    if fingerprint is not None:
        body["fingerprint"] = fingerprint
    with pytest.raises(APIError, match="review the plan again") as caught:
        web.post(app, workspace.conn, "/api/automation/start", body)
    assert caught.value.status == 409


@pytest.mark.parametrize("change", ["default", "registered", "posting_url", "apply_url", "profile", "answers", "apply"])
def test_review_binds_resume_urls_and_config(app, workspace, posting, pdf_pair, monkeypatch, change):
    body = {"mode": "selected", "job_ids": [posting.id]}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    assert plan["jobs"][0]["resume"]["available"]
    assert len(plan["fingerprint"]) == 64
    if change == "default":
        workspace.conn.execute("UPDATE resume_default_overrides SET resume_id=?", (pdf_pair[1].id,))
    elif change == "registered":
        from pathlib import Path
        resumes.add_resume(workspace.conn, pdf_pair[0].label, Path(pdf_pair[1].file_path))
    elif change in ("posting_url", "apply_url"):
        column = "url" if change == "posting_url" else "apply_url"
        workspace.conn.execute(f"UPDATE jobs SET {column}=? WHERE id=?",
                               ("https://example.invalid/new-form", posting.id))
    elif change == "profile":
        app.config = replace(app.config, profile=app.config.profile.model_copy(update={"first_name": "Changed"}))
    elif change == "answers":
        from kitty.config import AnswerEntry
        app.config = replace(app.config, answers=app.config.answers.model_copy(update={
            "answers": [AnswerEntry(question="Synthetic question?", answer="Reviewed fact")],
        }))
    else:
        app.config = replace(app.config, apply=app.config.apply.model_copy(update={"watch_hours": 7}))
    current = web.post(app, workspace.conn, "/api/automation/preflight", body)
    assert current["fingerprint"] != plan["fingerprint"]
    assert current["jobs"][0]["resume"]["available"]
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *a, **k: pytest.fail("Stale review must not start browser"))
    with pytest.raises(APIError, match="review the plan again") as caught:
        web.post(app, workspace.conn, "/api/automation/start", {
            **body, "confirmed": True, "fingerprint": plan["fingerprint"],
        })
    assert caught.value.status == 409


def test_default_changed_after_start_keeps_approved_resume(app, workspace, posting, pdf_pair, monkeypatch):
    entered, proceed = threading.Event(), threading.Event()
    seen = []
    def fill(settings, config, ids, *, approvals, on_filled, **kwargs):
        entered.set()
        assert proceed.wait(3)
        owned = connect(settings.db_path)
        try:
            job = jobs.get_job(owned, posting.id)
            assert web.filler.choose_resume(owned, config, job).id == pdf_pair[1].id
            approved = approvals[posting.id].resume(owned, settings, job)
            seen.append(approved.id)
            with pytest.raises(TypeError):
                approvals[posting.id] = approvals[posting.id]
            on_filled([report(posting.id)])
        finally:
            owned.close()
    monkeypatch.setattr(web.filler, "fill_jobs", fill)
    body = {"mode": "selected", "job_ids": [posting.id]}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    web.post(app, workspace.conn, "/api/automation/start", {
        **body, "confirmed": True, "fingerprint": plan["fingerprint"],
    })
    try:
        assert entered.wait(3)
        workspace.conn.execute("UPDATE resume_default_overrides SET resume_id=?", (pdf_pair[1].id,))
    finally:
        proceed.set()
    assert finish(app.automation)["phase"] == "completed"
    assert seen == [pdf_pair[0].id]


@pytest.mark.parametrize("mode", ["login", "create"])
def test_review_enables_only_chosen_run_account_action(
    app, workspace, posting, saved_credentials, monkeypatch, mode,
):
    captured = []
    monkeypatch.setattr(app.automation, "start", lambda config, *a, **k: captured.append(config))
    body = {"mode": "selected", "job_ids": [posting.id], "authentication_mode": mode}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    assert plan["authentication"]["credentials_available"]
    assert plan["authentication"]["approved_origins"] == ["https://boards.greenhouse.io"]
    assert plan["authentication"]["mode"] == mode
    serialized = json.dumps(plan)
    assert "Synthetic-Web-Pass" not in serialized and "web.synthetic@" not in serialized
    assert "credential_binding" not in serialized
    web.post(app, workspace.conn, "/api/automation/start", {
        **body, "confirmed": True, "authorize_authentication": True,
        "fingerprint": plan["fingerprint"],
    })
    policy = captured[0].apply.authentication
    assert policy.enabled and policy.account_mode == mode
    assert policy._credential_binding
    assert policy._credential_binding not in repr(policy)
    assert "_credential_binding" not in policy.model_dump()
    assert policy.allow_account_creation is (mode == "create")
    assert policy.approved_origins == ["https://boards.greenhouse.io"]
    assert not policy.approves("https://unreviewed.example/login")
    assert not app.config.apply.authentication.enabled


@pytest.mark.parametrize("change", ["password", "email", "mode", "origin"])
def test_account_authorization_is_bound_to_review(
    app, workspace, posting, saved_credentials, monkeypatch, change,
):
    body = {"mode": "selected", "job_ids": [posting.id], "authentication_mode": "create"}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    if change == "password":
        saved_credentials.write_text("APPLICATION_ACCOUNT_EMAIL=web.synthetic@example.invalid\n"
                                     "APPLICATION_ACCOUNT_PASSWORD=Changed-Synthetic-Password\n")
    elif change == "email":
        saved_credentials.write_text("APPLICATION_ACCOUNT_EMAIL=changed.synthetic@example.invalid\n"
                                     "APPLICATION_ACCOUNT_PASSWORD=Synthetic-Web-Pass#42\n")
    elif change == "mode":
        body["authentication_mode"] = "login"
    else:
        workspace.conn.execute("UPDATE jobs SET apply_url=? WHERE id=?",
                               ("https://different.example/register", posting.id))
    monkeypatch.setattr(app.automation, "start", lambda *a, **k: pytest.fail("Stale account review"))
    with pytest.raises(APIError, match="review the plan again") as caught:
        web.post(app, workspace.conn, "/api/automation/start", {
            **body, "confirmed": True, "authorize_authentication": True, "fingerprint": plan["fingerprint"],
        })
    assert caught.value.status == 409


def test_account_review_requires_saved_credentials_and_refuses_public_password(
    app, workspace, posting, monkeypatch,
):
    body = {"mode": "selected", "job_ids": [posting.id], "authentication_mode": "create"}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    assert not plan["authentication"]["credentials_available"]
    monkeypatch.setattr(app.automation, "start", lambda *a, **k: pytest.fail("No credential approval"))
    with pytest.raises(APIError, match="Saved credentials are unavailable"):
        web.post(app, workspace.conn, "/api/automation/start", {
            **body, "confirmed": True, "authorize_authentication": True, "fingerprint": plan["fingerprint"],
        })
    for extra in ({"password": "Synthetic"}, {"approved_origins": ["*"]}):
        with pytest.raises(APIError, match="Unknown parameter"):
            web.post(app, workspace.conn, "/api/automation/preflight", {**body, **extra})


def test_credentials_changed_after_start_are_rejected_before_browser_access(
    app, workspace, posting, saved_credentials, monkeypatch,
):
    from kitty.core.apply.auth import authenticate
    from kitty.core.apply.credentials import load_credentials
    captured = []
    monkeypatch.setattr(app.automation, "start", lambda config, *a, **k: captured.append(config))
    body = {"mode": "selected", "job_ids": [posting.id], "authentication_mode": "create"}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    web.post(app, workspace.conn, "/api/automation/start", {
        **body, "confirmed": True, "authorize_authentication": True, "fingerprint": plan["fingerprint"],
    })
    saved_credentials.write_text("APPLICATION_ACCOUNT_EMAIL=web.synthetic@example.invalid\n"
                                 "APPLICATION_ACCOUNT_PASSWORD=Changed-After-Start\n")
    # A sentinel with no browser methods ensures rejection precedes any DOM work.
    result = authenticate(object(), captured[0].apply.authentication, load_credentials(app.settings))
    assert not result.acted and "credentials changed after review" in result.blocker
    assert "Changed-After-Start" not in repr(result)


def test_google_review_uses_profile_identity_without_employer_password(
    app, workspace, posting, monkeypatch,
):
    captured = []
    app.config = replace(app.config, profile=app.config.profile.model_copy(
        update={"email": "job-account@example.invalid"}))
    monkeypatch.setattr(app.automation, "start", lambda config, *a, **k: captured.append(config))
    monkeypatch.setattr(web, "load_credentials", lambda *a: pytest.fail("Google must not load employer passwords"))
    body = {"mode": "selected", "job_ids": [posting.id], "authentication_mode": "google"}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    auth = plan["authentication"]
    assert auth["google_email"] == app.config.profile.email
    assert auth["google_available"] and not auth["credentials_available"]
    assert "https://accounts.google.com" not in auth["approved_origins"]
    web.post(app, workspace.conn, "/api/automation/start", {
        **body, "confirmed": True, "authorize_authentication": True, "fingerprint": plan["fingerprint"],
    })
    policy = captured[0].apply.authentication
    assert policy.account_mode == "google" and policy._google_email == app.config.profile.email
    assert not policy.allow_account_creation
    assert not policy.approves("https://accounts.google.com")
    assert not app.config.apply.authentication.enabled


@pytest.mark.parametrize("change", ["email", "origin", "mode"])
def test_google_account_action_identity_and_employer_are_review_bound(
    app, workspace, posting, monkeypatch, change,
):
    body = {"mode": "selected", "job_ids": [posting.id], "authentication_mode": "google"}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    if change == "email":
        app.config = replace(app.config, profile=app.config.profile.model_copy(
            update={"email": "different@example.invalid"}))
    elif change == "origin":
        workspace.conn.execute("UPDATE jobs SET apply_url=? WHERE id=?",
                               ("https://different.example/login", posting.id))
    else:
        body["authentication_mode"] = "login"
    monkeypatch.setattr(app.automation, "start", lambda *a, **k: pytest.fail("Stale Google approval"))
    with pytest.raises(APIError, match="review the plan again"):
        web.post(app, workspace.conn, "/api/automation/start", {
            **body, "confirmed": True, "authorize_authentication": True, "fingerprint": plan["fingerprint"],
        })


def test_google_action_without_checkbox_remains_manual(app, workspace, posting, monkeypatch):
    captured = []
    monkeypatch.setattr(app.automation, "start", lambda config, *a, **k: captured.append(config))
    body = {"mode": "selected", "job_ids": [posting.id], "authentication_mode": "google"}
    plan = web.post(app, workspace.conn, "/api/automation/preflight", body)
    web.post(app, workspace.conn, "/api/automation/start", {
        **body, "confirmed": True, "fingerprint": plan["fingerprint"],
    })
    assert not captured[0].apply.authentication.enabled
    assert captured[0].apply.authentication._google_email is None


@pytest.mark.parametrize("exclusion", ["queue", "eligibility", "closed"])
def test_selected_exclusion_precedes_credentials_and_keeps_actual_reason(
    app, workspace, posting, monkeypatch, exclusion,
):
    reason = "Applicant graduation does not meet this posting's requirement"
    if exclusion == "queue":
        workspace.conn.execute(
            "UPDATE jobs SET queue_state = 'not_eligible', eligibility_reason = ? WHERE id = ?",
            (reason, posting.id),
        )
    elif exclusion == "eligibility":
        jobs.set_eligibility(workspace.conn, posting.id, "ineligible", reason)
    else:
        workspace.conn.execute("UPDATE jobs SET is_active = 0 WHERE id = ?", (posting.id,))
    monkeypatch.setattr(web, "load_credentials", lambda *_: pytest.fail("Excluded jobs cannot load credentials"))
    plan = web.post(app, workspace.conn, "/api/automation/plan", {
        "mode": "selected", "job_ids": [posting.id],
    })
    assert plan["job_ids"] == [] and plan["jobs"] == []
    assert plan["left"] == [{
        "job_id": posting.id, "company": posting.company_name, "title": posting.title,
        "reason": ("closed: Posting unavailable; refresh availability before preparing it."
                   if exclusion == "closed" else f"ineligible: {reason}"),
    }]
    with pytest.raises(APIError) as caught:
        web.post(app, workspace.conn, "/api/automation/start", {
            "mode": "selected", "job_ids": [posting.id], "fingerprint": plan["fingerprint"],
            "confirmed": True,
        })
    assert caught.value.status == 409
    assert app.automation.snapshot()["reports"] == []
    assert applications.get_application_for_job(workspace.conn, posting.id) is None


def test_selected_mixed_plan_keeps_each_exclusion_without_replacements(app, workspace, posting):
    ineligible, _ = jobs.upsert_job(
        workspace.conn, company="Excluded Fixture", title="Doctoral internship",
        url="https://example.invalid/doctoral",
        eligibility="ineligible", eligibility_reason="Requires current PhD enrollment",
    )
    closed, _ = jobs.upsert_job(
        workspace.conn, company="Closed Fixture", title="Closed internship",
        url="https://example.invalid/closed",
    )
    workspace.conn.execute("UPDATE jobs SET is_active = 0 WHERE id = ?", (closed.id,))
    plan = web.post(app, workspace.conn, "/api/automation/plan", {
        "mode": "selected", "job_ids": [ineligible.id, posting.id, closed.id],
    })
    assert plan["job_ids"] == [posting.id]
    assert [item["job_id"] for item in plan["left"]] == [ineligible.id, closed.id]
    assert plan["left"][0]["reason"] == "ineligible: Requires current PhD enrollment"
    assert plan["left"][1]["reason"].startswith("closed:")


def test_new_ineligibility_invalidates_review_before_browser_or_credentials(
    app, workspace, posting, monkeypatch,
):
    body = {"mode": "selected", "job_ids": [posting.id]}
    plan = web.post(app, workspace.conn, "/api/automation/plan", body)
    jobs.set_eligibility(workspace.conn, posting.id, "ineligible", "UK-only posting")
    monkeypatch.setattr(web, "load_credentials", lambda *_: pytest.fail("Excluded job cannot load credentials"))
    monkeypatch.setattr(web.filler, "fill_jobs", lambda *a, **kw: pytest.fail("Stale review cannot start browser"))
    with pytest.raises(APIError) as caught:
        web.post(app, workspace.conn, "/api/automation/start", {
            **body, "confirmed": True, "fingerprint": plan["fingerprint"],
        })
    assert caught.value.status == 409
    assert app.automation.snapshot()["running"] is False


def test_structured_causes_survive_web_report_serialization(app, posting):
    ready = report(posting.id, "ready")
    controls = replace(
        report(posting.id), note="Selection interaction failed: saved choice did not commit",
        cause="unsupported_controls",
    )
    missing = replace(report(posting.id), cause="missing_applicant_fact")
    for outcome in (ready, controls, missing):
        app.automation._filled([outcome])
        assert app.automation.snapshot()["reports"][0]["cause"] == outcome.cause
        assert app.automation.snapshot()["reports"][0]["note"] == outcome.note
