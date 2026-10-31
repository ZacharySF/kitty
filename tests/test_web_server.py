"""HTTP behavior against isolated synthetic workspaces; no live source requests."""
from __future__ import annotations

import csv
import http.client
import io
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kitty.core import applications, jobs, reminders, resumes
from kitty.web_server import LocalHTTPServer, WebApplication


@pytest.fixture
def website(workspace, tmp_path):
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Synthetic website</title>")
    (static / "app.js").write_text("console.log('synthetic');")
    app = WebApplication(workspace.settings, workspace.config, static)
    server = LocalHTTPServer(app, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None, *, method=None, headers=None, raw=None):
        method = method or ("POST" if body is not None or raw is not None else "GET")
        defaults = {"Origin": server.origin}
        payload = raw
        if method == "POST":
            defaults.update({"Content-Type": "application/json", "X-Kitty-Request": "1"})
            if raw is None:
                payload = json.dumps(body).encode()
        for key, value in (headers or {}).items():
            if value is None:
                defaults.pop(key, None)
            else:
                defaults[key] = value
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            client.request(method, path, body=payload, headers=defaults)
            response = client.getresponse()
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(data) if data else None
            return response.status, data, dict(response.getheaders())
        finally:
            client.close()

    yield SimpleNamespace(request=request, server=server, app=app, static=static)
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)


@pytest.fixture
def posting(workspace):
    job, _ = jobs.upsert_job(
        workspace.conn, company="Synthetic Robotics", title="Controls Intern",
        location="Remote", url="https://example.invalid/controls",
        source="manual", source_job_id="web-controls", jd_text="Synthetic posting evidence",
    )
    jobs.link_track(workspace.conn, job.id, workspace.config.tracks.tracks[0].name)
    jobs.set_tier(workspace.conn, job.id, "A", reason="Synthetic fit")
    return job


def test_list_filters_pagination_detail_and_counts(website, workspace, posting):
    second, _ = jobs.upsert_job(
        workspace.conn, company="Another Synthetic Company", title="Design Intern",
        url="https://example.invalid/design", source="manual", source_job_id="web-design",
    )
    status, data, _ = website.request("/api/jobs?limit=1")
    assert status == 200 and len(data["jobs"]) == 1 and data["total"] == 2
    status, page, _ = website.request("/api/jobs?limit=1&offset=1")
    assert status == 200 and page["jobs"][0]["id"] != data["jobs"][0]["id"]
    assert website.request("/api/jobs?offset=2")[1] == {"jobs": [], "total": 2}
    track = workspace.config.tracks.tracks[0].name
    status, filtered, _ = website.request(f"/api/jobs?search=Robotics&tier=A&track={track}")
    assert status == 200 and [job["id"] for job in filtered["jobs"]] == [posting.id]
    status, detail, _ = website.request(f"/api/jobs/{posting.id}")
    assert status == 200 and detail["description"] == "Synthetic posting evidence"
    assert detail["application"] is None
    status, bootstrap, _ = website.request("/api/bootstrap")
    assert status == 200 and bootstrap["counts"]["jobs"] == 2
    assert bootstrap["counts"]["applications"] == 0
    assert second.id != posting.id


@pytest.mark.parametrize("query", [
    "limit=0", "limit=101", "offset=-1", "offset=100001", "limit=x",
    "view=unknown", "tier=Z", "track=not-a-track", "limit=1&limit=2", "unknown=x",
])
def test_rejects_invalid_filters(website, query):
    status, data, _ = website.request("/api/jobs?" + query)
    assert status == 400 and "error" in data


def test_queue_favorites_notes_and_restoration_do_not_apply(website, workspace, posting):
    endpoint = f"/api/jobs/{posting.id}"
    for action, state in [("queue", "queued"), ("skip", "skipped"), ("restore", "new")]:
        assert website.request(endpoint, {"action": action})[0] == 200
        assert jobs.get_job(workspace.conn, posting.id).queue_state == state
        assert applications.get_application_for_job(workspace.conn, posting.id) is None
    assert website.request(endpoint, {"action": "favorite", "value": True})[0] == 200
    assert website.request("/api/jobs?view=favorites")[1]["total"] == 1
    assert website.request(endpoint, {"action": "notes", "value": "User-authored synthetic note"})[0] == 200
    assert website.request(endpoint)[1]["my_notes"] == "User-authored synthetic note"
    assert website.request(endpoint, {"action": "favorite", "value": False})[0] == 200
    assert website.request("/api/jobs?view=favorites")[1]["total"] == 0


@pytest.mark.parametrize("body", [
    {"action": "applied"}, {"action": "applied", "value": False},
    {"action": "applied", "value": 1}, {"action": "applied", "value": "true"},
    {"action": "status", "value": "applied"}, {"action": "status", "value": []},
    {"action": "favorite", "value": 1}, {"action": "notes", "value": None},
    {"action": "notes", "value": "bad\x00note"}, {"action": "notes", "value": "n" * 20001},
    {"action": "notes", "value": "\ud800"},
    {"action": "resume", "value": True}, {"action": "resume", "value": -1},
    {"action": "resume", "value": "1"}, {"action": "queue", "value": "queued"},
    {"action": "queue", "unexpected": 1}, {"action": "submit"}, {"action": []}, {},
])
def test_invalid_mutations_never_record_applications(website, workspace, posting, body):
    status, data, _ = website.request(f"/api/jobs/{posting.id}", body)
    assert status == 400 and "error" in data
    assert applications.get_application_for_job(workspace.conn, posting.id) is None
    assert jobs.get_job(workspace.conn, posting.id).queue_state == "new"


def test_confirmation_and_status_append_history(website, workspace, posting):
    endpoint = f"/api/jobs/{posting.id}"
    assert website.request(endpoint, {"action": "status", "value": "interview"})[0] == 409
    assert website.request(endpoint, {"action": "applied", "value": True})[0] == 200
    assert website.request(endpoint, {"action": "applied", "value": True})[0] == 200
    application = applications.get_application_for_job(workspace.conn, posting.id)
    scheduled = reminders.followups_for(workspace.conn, application.id)
    assert len(scheduled) == len(workspace.config.apply.followup_days)
    assert {reminder.kind for reminder in scheduled} == {"followup"}
    assert len({reminder.due_at for reminder in scheduled}) == len(scheduled)
    assert website.request(endpoint, {"action": "status", "value": "interview"})[0] == 200
    detail = website.request(endpoint)[1]
    assert detail["queue_state"] == "applied"
    assert detail["application"]["status"] == "interview"
    assert [event["to_status"] for event in detail["application"]["history"]] == ["applied", "interview"]
    assert website.request(endpoint, {"action": "restore"})[0] == 409
    assert website.request("/api/jobs?view=applications")[1]["total"] == 1
    assert website.request("/api/jobs?view=queue")[1]["total"] == 0
    assert applications.get_application_for_job(workspace.conn, posting.id).status == "interview"


@pytest.mark.parametrize("headers", [
    {"Host": "attacker.invalid"}, {"Host": "127.0.0.1:1"},
    {"Origin": "https://attacker.invalid"}, {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
    {"X-Kitty-Request": None}, {"X-Kitty-Request": "0"},
])
def test_rejects_cross_origin_or_unmarked_mutations(website, workspace, posting, headers):
    status, data, response_headers = website.request(
        f"/api/jobs/{posting.id}", {"action": "applied", "value": True}, headers=headers,
    )
    assert status == 403 and "error" in data
    assert "Access-Control-Allow-Origin" not in response_headers
    assert applications.get_application_for_job(workspace.conn, posting.id) is None


@pytest.mark.parametrize("headers", [
    {"Host": "evil.invalid"}, {"Origin": "https://evil.invalid"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_private_reads_also_validate_origin_and_host(website, headers):
    assert website.request("/api/bootstrap", headers=headers)[0] == 403
    assert website.request("/api/reports/export?format=csv&dataset=jobs", headers=headers)[0] == 403


@pytest.mark.parametrize("raw", [
    b'[]', b'null', b'not-json', b'{"action":"notes","value":NaN}',
    b'{"action":"queue","action":"applied","value":true}',
])
def test_rejects_malformed_json(website, posting, raw):
    assert website.request(f"/api/jobs/{posting.id}", raw=raw)[0] == 400


def test_only_json_posts_and_no_cors_preflight(website, posting):
    endpoint = f"/api/jobs/{posting.id}"
    assert website.request(endpoint, {"action": "queue"}, headers={"Content-Type": "text/plain"})[0] == 415
    status, _, headers = website.request(endpoint, method="OPTIONS")
    assert status == 405 and "Access-Control-Allow-Origin" not in headers
    assert website.request(endpoint, raw=b"x" * 65537)[0] == 400
    assert website.request(endpoint, {"action": "queue"}, headers={"Transfer-Encoding": "chunked"})[0] == 400


def test_local_non_browser_client_still_requires_custom_header(website, posting):
    endpoint = f"/api/jobs/{posting.id}"
    assert website.request(endpoint, {"action": "queue"}, headers={"Origin": None})[0] == 200
    assert website.request(endpoint, {"action": "skip"}, headers={"Origin": None, "X-Kitty-Request": None})[0] == 403


def test_resume_pdf_selection_and_pinned_history(website, workspace, posting, tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% synthetic fixture, no personal resume\n%%EOF\n")
    resume = resumes.add_resume(workspace.conn, "Synthetic library entry", pdf)
    status, library, _ = website.request("/api/resumes")
    assert status == 200 and library["resumes"][0]["available"] is True
    assert library["versions"] == []
    assert "file_path" not in library["resumes"][0]
    endpoint = f"/api/jobs/{posting.id}"
    assert website.request(endpoint, {"action": "resume", "value": resume.id})[0] == 200
    assert website.request(endpoint)[1]["resume"] == {"id": resume.id, "label": resume.label}
    status, data, headers = website.request(f"/api/resumes/{resume.id}/pdf")
    assert status == 200 and data == pdf.read_bytes()
    assert headers["Content-Type"] == "application/pdf"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert website.request(endpoint, {"action": "applied", "value": True})[0] == 200
    assert website.request(endpoint, {"action": "resume", "value": resume.id})[0] == 409
    app = applications.get_application_for_job(workspace.conn, posting.id)
    assert app.resume_id == resume.id
    pdf.write_bytes(b"%PDF-1.4\nchanged\n%%EOF\n")
    assert website.request(f"/api/resumes/{resume.id}/pdf")[0] == 409
    assert website.request("/api/resumes")[1]["resumes"][0]["available"] is False


def test_non_pdf_registration_cannot_expose_other_files(website, workspace, posting, tmp_path):
    text = tmp_path / "synthetic.txt"
    text.write_text("Not a PDF")
    resume = resumes.add_resume(workspace.conn, "Not a PDF", text)
    assert website.request(f"/api/resumes/{resume.id}/pdf")[0] == 404
    assert website.request(f"/api/jobs/{posting.id}", {"action": "resume", "value": resume.id})[0] == 404
    assert website.request("/api/resumes/999999/pdf")[0] == 404
    assert website.request("/api/resumes?path=/etc/passwd")[0] == 400


def test_export_reuses_formula_escaping(website, workspace):
    jobs.upsert_job(workspace.conn, company="=Synthetic Formula", title="+Synthetic Title",
                    url="https://example.invalid/formula", source="manual", source_job_id="formula")
    status, data, headers = website.request("/api/reports/export?format=csv&dataset=jobs")
    assert status == 200 and "attachment" in headers["Content-Disposition"]
    row = next(csv.DictReader(io.StringIO(data.decode())))
    assert row["company"] == "'=Synthetic Formula"
    assert row["title"] == "'+Synthetic Title"


def test_static_spa_is_confined_and_has_private_headers(website, tmp_path):
    outside = tmp_path / "outside.html"
    outside.write_text("private synthetic file")
    (website.static / "escape.html").symlink_to(outside)
    (website.static / ".env").write_text("synthetic secret")
    status, html, headers = website.request("/jobs")
    assert status == 200 and b"Synthetic website" in html
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    for path in ("/escape.html", "/.env", "/missing.js", "/api/not-found"):
        assert website.request(path)[0] == 404
    assert website.request("/%2e%2e/outside.html")[0] == 400
    status, data, headers = website.request("/app.js", method="HEAD")
    assert status == 200 and data == b"" and int(headers["Content-Length"]) > 0




def test_discovery_only_runs_enabled_free_sources(website, monkeypatch):
    config = website.app.config.sources.sources
    for source in config.values():
        source.enabled = False
    config["greenhouse"].enabled = True
    config["websearch"].enabled = True
    started = threading.Event()
    release = threading.Event()
    captured = []

    def discover(settings, configuration, *, only):
        captured.extend(only)
        started.set()
        assert release.wait(5)
        return SimpleNamespace(sources={})

    monkeypatch.setattr("kitty.web_insights.run_discover", discover)
    try:
        assert website.request("/api/sources/run", {"confirm": True})[0] == 200
        assert started.wait(5)
        assert captured == ["greenhouse"]
        assert website.request("/api/sources")[1]["discovery"]["running"] is True
        assert website.request("/api/sources/run", {"confirm": True})[0] == 409
    finally:
        release.set()


def test_discovery_rejects_demo_empty_sources_and_custom_inputs(website):
    assert website.request("/api/sources/run", {"confirm": True, "only": ["websearch"]})[0] == 400
    for source in website.app.config.sources.sources.values():
        source.enabled = False
    assert website.request("/api/sources/run", {"confirm": True})[0] == 400
    website.app.settings = replace(website.app.settings, demo=True)
    assert website.request("/api/sources/run", {"confirm": True})[0] == 403
    assert website.request("/api/bootstrap")[1]["demo"] is True


@pytest.mark.parametrize("upstream_status,available", [(200, True), (302, False), (500, False)])
def test_learnharness_boundary_is_fixed_loopback_not_a_proxy(website, monkeypatch, upstream_status, available):
    calls = []

    class Probe:
        def __init__(self, host, port, *, timeout):
            calls.append((host, port, timeout))

        def request(self, method, path):
            calls.append((method, path))

        def getresponse(self):
            return SimpleNamespace(status=upstream_status)

        def close(self):
            calls.append("closed")

    # Patch only the extension's connection, never the test HTTP client.
    monkeypatch.setattr("kitty.web_learnharness.HTTPConnection", Probe)
    status, data, headers = website.request("/api/extensions/learnharness")
    assert status == 200
    assert data == {"url": "http://127.0.0.1:3210", "available": available}
    assert calls == [("127.0.0.1", 3210, 2), ("HEAD", "/"), "closed"]
    assert "frame-src http://127.0.0.1:3210;" in headers["Content-Security-Policy"]
    calls.clear()
    assert website.request("/api/extensions/learnharness?url=https://example.invalid")[0] == 400
    assert website.request("/api/extensions/learnharness", {"url": "https://example.invalid"})[0] == 404
    assert calls == []
    assert website.request("/api/extensions/learnharness/api/keys")[0] == 404


def test_learnharness_unavailable_is_not_a_kitty_failure(website, monkeypatch):
    class Offline:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            raise ConnectionRefusedError("synthetic offline service")

        def close(self):
            pass

    monkeypatch.setattr("kitty.web_learnharness.HTTPConnection", Offline)
    assert website.request("/api/extensions/learnharness")[:2] == (
        200, {"url": "http://127.0.0.1:3210", "available": False},
    )
    assert website.request("/api/bootstrap")[0] == 200


def test_study_start_reports_missing_packaged_runtime_not_unknown_route(website):
    website.app.learnharness.bundle = website.static / "missing-runtime"
    website.app.learnharness.port = 1
    status, data, _ = website.request("/api/extensions/learnharness/start", {})
    assert status == 503
    assert "reinstall" in data["error"]
    assert website.request("/api/extensions/learnharness/start", {"url": "https://example.invalid"})[0] == 400


def test_quick_notes_http_create_autosave_list_and_delete(website):
    status, created, _ = website.request("/api/quick-notes/create", {"body": "", "tint": "mint"})
    assert status == 200 and created["body"] == "" and created["tint"] == "mint"
    status, saved, _ = website.request("/api/quick-notes/save", {"id": created["id"], "revision": created["revision"], "body": "idea: glass notes"})
    assert status == 200 and saved["revision"] == created["revision"] + 1
    status, stale, _ = website.request("/api/quick-notes/save", {"id": created["id"], "revision": created["revision"], "body": "old"})
    assert status == 409 and "another window" in stale["error"]
    assert website.request(f"/api/quick-notes/{created['id']}")[1]["body"] == "idea: glass notes"
    assert [note["body"] for note in website.request("/api/quick-notes")[1]["notes"]] == ["idea: glass notes"]
    status, _, _ = website.request("/api/quick-notes/save", {"id": created["id"], "revision": saved["revision"], "colour": "rose"})
    assert status == 400
    status, _, _ = website.request("/api/quick-notes/delete", {"id": created["id"], "revision": saved["revision"]})
    assert status == 200 and website.request("/api/quick-notes")[1]["notes"] == []
    assert website.request(f"/api/quick-notes/{created['id']}")[0] == 404
