"""HTTP module activation, enforcement, frozen selection and data preservation.

Every workspace is a synthetic temporary directory; careers YAML exists only for
the combinations that enable applyai.
"""
from __future__ import annotations

import http.client
import json
import shutil
import threading
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from kitty import web_atlas
from kitty.config import load_config
from kitty.core import applications, backlinks, calendar, jobs, notes, opportunities, schema_maps
from kitty.core.workspace import open_workspace
from kitty.db import connect
from kitty.modules import MODULE_IDS, load_modules
from kitty.settings import Settings
from kitty.web_server import LocalHTTPServer, WebApplication

REPO_ROOT = Path(__file__).resolve().parents[1]
CAREERS = ("profile", "tracks", "sources", "watchlist", "answers", "views")
COMBINATIONS = [(), ("applyai",), ("workspace",), ("applyai", "workspace")]
APPLYAI_GETS = ("/api/dashboard", "/api/jobs", "/api/resumes", "/api/profile", "/api/sources",
                "/api/reminders", "/api/pipeline", "/api/mail", "/api/triage", "/api/automation/status")
APPLYAI_POSTS = ("/api/jobs/add", "/api/automation/start", "/api/resumes/register",
                 "/api/profile/basics", "/api/reminders")
WORKSPACE_GETS = ("/api/calendar", "/api/notes", "/api/schema", "/api/study", "/api/flashcards",
                  "/api/opportunities", "/api/documents", "/api/atlas/sections/notes")
WORKSPACE_POSTS = ("/api/notes/pages", "/api/calendar/events", "/api/schema/create",
                   "/api/ai/preview", "/api/ai/mind-map/preview", "/api/ai/run",
                   "/api/opportunities", "/api/documents/prepare")


def make_root(tmp_path: Path, modules: tuple[str, ...] | None, *, careers: bool | None = None) -> Settings:
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True, exist_ok=True)
    if modules is not None:
        (root / "config" / "modules.yaml").write_text(
            "enabled: [" + ", ".join(modules) + "]\n", encoding="utf-8")
    if careers if careers is not None else modules is None or "applyai" in modules:
        for name in CAREERS:
            shutil.copy(REPO_ROOT / "config" / f"{name}.yaml.example", root / "config" / f"{name}.yaml")
    return Settings.load(root=root, db_path=root / "data" / "web.db")


@contextmanager
def db(settings: Settings):
    with closing(connect(settings.db_path)) as conn:
        yield conn
        conn.commit()


def refused(status: int, body) -> bool:
    """True only for the readable 'module is disabled' refusal, not other 403s."""
    return status == 403 and isinstance(body, dict) and "module is disabled" in body.get("error", "")


@contextmanager
def site(settings: Settings, modules: tuple[str, ...] | None = None):
    """One 'process': the active snapshot is read once, like kitty-web start-up."""
    static = settings.root / "dist"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>Synthetic</title>")
    with open_workspace(settings, modules=modules) as workspace:
        config = workspace.config
    app = WebApplication(settings, config, static)
    server = LocalHTTPServer(app, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None):
        headers = {"Origin": server.origin}
        payload = None
        method = "GET"
        if body is not None:
            method = "POST"
            headers.update({"Content-Type": "application/json", "X-Kitty-Request": "1"})
            payload = json.dumps(body).encode()
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        try:
            client.request(method, path, body=payload, headers=headers)
            response = client.getresponse()
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(data) if data else None
            return response.status, data
        finally:
            client.close()

    try:
        yield app, request
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        app.close()


@pytest.mark.parametrize("modules", COMBINATIONS)
def test_bootstrap_and_modules_endpoint_for_every_combination(tmp_path, modules):
    settings = make_root(tmp_path, modules)
    assert (settings.config_dir / "tracks.yaml").exists() == ("applyai" in modules)
    with site(settings) as (app, request):
        status, boot = request("/api/bootstrap")
        assert status == 200
        state = boot["modules"]
        assert state["enabled"] == list(modules) and state["configured"] == list(modules)
        assert state["restart_required"] is False
        assert [item["id"] for item in state["available"]] == list(MODULE_IDS)
        assert ("tracks" in boot and "counts" in boot and "today" in boot) == ("applyai" in modules)
        assert boot["demo"] is False
        status, listed = request("/api/modules")
        assert (status, listed) == (200, state)
        assert request("/api/ai/status")[0] == 200
        status, atlas = request("/api/atlas")
        assert status == 200
        expected = {kind for kind in web_atlas.KINDS if web_atlas.KIND_MODULES[kind] in modules}
        assert {section["kind"] for section in atlas["sections"]} == expected


@pytest.mark.parametrize("modules", COMBINATIONS)
def test_disabled_routes_cannot_run_and_enabled_routes_are_reachable(tmp_path, modules):
    settings = make_root(tmp_path, modules)
    with site(settings) as (app, request):
        for module, gets, posts in (("applyai", APPLYAI_GETS, APPLYAI_POSTS),
                                    ("workspace", WORKSPACE_GETS, WORKSPACE_POSTS)):
            for path in gets:
                status, body = request(path)
                assert refused(status, body) == (module not in modules), path
            for path in posts:
                status, body = request(path, {})
                assert refused(status, body) == (module not in modules), path


@pytest.mark.parametrize("modules", COMBINATIONS)
def test_workers_and_credentials_belong_only_to_enabled_modules(tmp_path, modules):
    settings = make_root(tmp_path, modules)
    with open_workspace(settings) as workspace:
        opportunities.settings(workspace.conn)
        workspace.conn.execute("UPDATE opportunity_settings SET enabled=1 WHERE id=1")
        workspace.conn.commit()
    with site(settings) as (app, request):
        names = {extension.__name__.removeprefix("kitty.") for extension in app.extensions}
        assert {"web_ai", "web_atlas"} <= names
        assert ("web_jobs" in names) == ("applyai" in modules)
        assert ("web_automation" in names) == ("applyai" in modules)
        assert ("web_calendar" in names) == ("workspace" in modules)
        assert (app.automation is not None) == ("applyai" in modules)
        scheduler = [t for t in threading.enumerate() if t.name == "kitty-opportunity-schedule"]
        assert bool(scheduler) == ("workspace" in modules)
        assert (getattr(app, "opportunity_monitor", None) is not None) == ("workspace" in modules)
    assert not [t for t in threading.enumerate() if t.name == "kitty-opportunity-schedule"]


def test_legacy_workspace_without_modules_file_is_unchanged(tmp_path):
    settings = make_root(tmp_path, None)
    assert not (settings.config_dir / "modules.yaml").exists()
    with site(settings) as (app, request):
        assert app.modules == MODULE_IDS
        status, boot = request("/api/bootstrap")
        assert status == 200 and boot["modules"]["enabled"] == list(MODULE_IDS)
        assert boot["modules"]["restart_required"] is False
        assert "tracks" in boot and request("/api/dashboard")[0] == 200
        assert request("/api/calendar")[0] == 200


@pytest.mark.parametrize("modules", [(), ("workspace",)])
def test_malformed_careers_yaml_is_ignored_while_applyai_is_disabled(tmp_path, modules):
    settings = make_root(tmp_path, modules, careers=False)
    for name in ("profile", "tracks", "sources", "answers", "apply", "triage"):
        (settings.config_dir / f"{name}.yaml").write_text("{{{ broken: [", encoding="utf-8")
    with site(settings) as (app, request):
        assert request("/api/bootstrap")[0] == 200
        assert request("/api/reload", {})[0] == 200
        assert request("/api/profile")[0] == 403


def test_modules_post_persists_pending_selection_without_activating_it(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as (app, request):
        status, state = request("/api/modules", {"enabled": ["workspace", "applyai"]})
        assert status == 200
        assert state["enabled"] == ["workspace"]
        assert state["configured"] == ["applyai", "workspace"]
        assert state["restart_required"] is True
        assert load_modules(settings.config_dir) == ("applyai", "workspace")
        # Nothing activates in this process, not even after an explicit reload.
        assert request("/api/reload", {})[0] == 200
        assert app.modules == ("workspace",) and app.config.modules == ("workspace",)
        status, boot = request("/api/bootstrap")
        assert boot["modules"]["enabled"] == ["workspace"] and "tracks" not in boot
        assert request("/api/dashboard")[0] == 403 and request("/api/jobs")[0] == 403
        assert app.automation is None
        status, state = request("/api/modules", {"enabled": ["workspace"]})
        assert state["restart_required"] is False
        assert request("/api/modules")[1] == state


def test_pending_enable_of_applyai_is_neither_read_nor_validated_by_reload(tmp_path):
    settings = make_root(tmp_path, ("workspace",), careers=False)
    with site(settings) as (app, request):
        assert request("/api/modules", {"enabled": ["applyai", "workspace"]})[0] == 200
        (settings.config_dir / "tracks.yaml").write_text("invalid: [unterminated", encoding="utf-8")
        assert request("/api/reload", {})[0] == 200
        assert request("/api/bootstrap")[0] == 200
        assert app.config.modules == ("workspace",)


@pytest.mark.parametrize("body", [
    {}, {"enabled": "applyai"}, {"enabled": ["nope"]}, {"enabled": ["applyai", "applyai"]},
    {"enabled": [1]}, {"enabled": ["applyai"], "extra": True}, {"enabled": None},
    {"enabled": ["../../etc/passwd"]},
])
def test_modules_post_rejects_invalid_bodies_and_leaves_the_file_alone(tmp_path, body):
    settings = make_root(tmp_path, ("workspace",))
    path = settings.config_dir / "modules.yaml"
    before = path.read_text(encoding="utf-8")
    with site(settings) as (app, request):
        status, payload = request("/api/modules", body)
        assert status == 400 and "error" in payload
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in settings.config_dir.glob("*modules*")) == ["modules.yaml"]


def test_modules_post_never_writes_outside_the_modules_file(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    before = {p.name: p.read_bytes() for p in settings.config_dir.iterdir()}
    with site(settings) as (app, request):
        assert request("/api/modules", {"enabled": []})[0] == 200
    after = {p.name: p.read_bytes() for p in settings.config_dir.iterdir()}
    assert {k for k in after if after[k] != before.get(k)} == {"modules.yaml"} and set(after) == set(before)


def test_restart_activates_the_saved_selection_and_preserves_data(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as (app, request):
        with db(settings) as conn:
            book = notes.create_notebook(conn, "Preserved notebook")
            notes.create_page(conn, book["id"], "Preserved page")
        status, listing = request("/api/notes")
        assert status == 200 and "Preserved notebook" in json.dumps(listing)
        assert request("/api/modules", {"enabled": ["applyai"]})[0] == 200
        assert request("/api/notes")[0] == 200  # Still active until restart.

    # New process: only applyai, so careers files are now required.
    for name in CAREERS:
        shutil.copy(REPO_ROOT / "config" / f"{name}.yaml.example", settings.config_dir / f"{name}.yaml")
    with site(settings) as (app, request):
        assert app.modules == ("applyai",)
        assert request("/api/notes")[0] == 403 and request("/api/dashboard")[0] == 200
        assert request("/api/modules", {"enabled": ["workspace", "applyai"]})[0] == 200
    with site(settings) as (app, request):
        assert app.modules == ("applyai", "workspace")
        status, listing = request("/api/notes")
        assert status == 200 and "Preserved notebook" in json.dumps(listing)
        assert "Preserved page" in json.dumps(request("/api/atlas/sections/notes")[1])


def test_disabling_applyai_preserves_careers_rows_and_tracks(tmp_path):
    settings = make_root(tmp_path, ("applyai", "workspace"))
    with site(settings) as (app, request):
        with db(settings) as conn:
            job, _ = jobs.upsert_job(conn, company="Synthetic Co", title="Kept role", url="https://example.invalid/kept",
                                     source="manual", source_job_id="kept-1")
            tracks_before = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        assert tracks_before > 0 and request(f"/api/jobs/{job.id}")[0] == 200
    (settings.config_dir / "modules.yaml").write_text("enabled: [workspace]\n", encoding="utf-8")
    with site(settings) as (app, request):
        assert request(f"/api/jobs/{job.id}")[0] == 403
        assert request("/api/atlas/sections/jobs")[0] == 403
    with db(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == tracks_before
    (settings.config_dir / "modules.yaml").write_text("enabled: [applyai]\n", encoding="utf-8")
    with site(settings) as (app, request):
        status, detail = request(f"/api/jobs/{job.id}")
        assert status == 200 and detail["title"] == "Kept role"


def test_atlas_home_does_not_query_disabled_modules(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with open_workspace(settings) as workspace:
        statements: list[str] = []
        workspace.conn.set_trace_callback(statements.append)
        result = web_atlas.atlas(workspace.conn, modules=("workspace",))
        kinds = {section["kind"] for section in result["sections"]}
        assert kinds.isdisjoint({"job", "application"}) and "notebook" in kinds
        assert not any(" jobs" in text or " applications" in text for text in statements)
        empty = web_atlas.atlas(workspace.conn, modules=())
        assert empty == {"sections": []}


def event(conn, **changes):
    return calendar.create_event(conn, {"title": "Planning", "start": "2030-01-02T09:00:00Z",
                                        "end": "2030-01-02T10:00:00Z", "timezone": "UTC",
                                        "all_day": False, **changes})


def test_calendar_omits_applyai_contributions_but_keeps_them(workspace):
    conn = workspace.conn
    mine = event(conn)
    copied = event(conn, title="Deadline · Synthetic Co", source_key="job:7:deadline")
    window = ("2030-01-01T00:00:00Z", "2030-02-01T00:00:00Z")
    titles = lambda **kw: {item["title"] for item in calendar.occurrences(conn, *window, **kw)}
    assert titles() == {"Planning", "Deadline · Synthetic Co"}
    assert titles(applyai=False) == {"Planning"}
    assert "Deadline" not in calendar.export_ics(conn, applyai=False)["content"]
    assert "Deadline" in calendar.export_ics(conn)["content"]
    with pytest.raises(Exception) as hidden:
        calendar.get_event(conn, copied["id"], applyai=False)
    assert getattr(hidden.value, "status", None) == 404
    assert calendar.get_event(conn, mine["id"], applyai=False)["title"] == "Planning"
    # Refreshing without applyai neither lists job dates nor retires existing copies.
    jobs.upsert_job(conn, company="Synthetic Co", title="Role", url="https://example.invalid/r",
                    source="manual", source_job_id="cal-1", deadline_at="2030-01-09")
    calendar.import_jobs(conn, applyai=False)
    assert calendar.get_event(conn, copied["id"])["title"] == "Deadline · Synthetic Co"
    assert not any(i["title"].startswith("Deadline") and "Role" in i["title"]
                   for i in calendar.occurrences(conn, *window))
    assert calendar.import_jobs(conn)["source_count"] >= 1


def test_calendar_activity_and_reminders_split_by_module(workspace):
    conn = workspace.conn
    job, _ = jobs.upsert_job(conn, company="Synthetic Co", title="Role", url="https://example.invalid/a",
                             source="manual", source_job_id="act-1")
    applications.record_application(conn, job.id, applied_at="2030-01-03T12:00:00+00:00")
    conn.commit()
    both = calendar.activity(conn, "2030-01-01T00:00:00Z", "2030-02-01T00:00:00Z")
    only = calendar.activity(conn, "2030-01-01T00:00:00Z", "2030-02-01T00:00:00Z", applyai=False)
    assert any("applications" in day for day in both)
    assert not any("applications" in day or "status_updates" in day for day in only)
    # The careers reminder list is separate from the workspace calendar's own reminders.
    assert calendar.due_reminders(conn, applyai=False) == calendar.due_reminders(conn) == []


def test_backlinks_omit_jobs_and_applyai_calendar_copies_without_applyai(workspace):
    conn = workspace.conn
    jobs.upsert_job(conn, company="Zephyr Labs", title="Quokka controls engineer", url="https://example.invalid/q",
                    source="manual", source_job_id="bl-1", jd_text="quokka systems")
    event(conn, title="Quokka sync", source_key="job:1:deadline")
    event(conn, title="Quokka offsite")
    terms = {"quokka": backlinks.phrase("quokka")}
    kinds = lambda applyai: {ref["kind"] for ref in backlinks.scan(conn, terms, applyai=applyai)[0]["quokka"]}
    assert kinds(True) == {"job", "calendar_event"}
    assert kinds(False) == {"calendar_event"}
    titles = [ref["title"] for ref in backlinks.scan(conn, terms, applyai=False)[0]["quokka"]]
    assert titles == ["Quokka offsite"]


def test_schema_job_sources_keep_links_but_hide_live_data_without_applyai(workspace):
    conn = workspace.conn
    job, _ = jobs.upsert_job(conn, company="Synthetic Co", title="Visible only with applyai",
                             url="https://example.invalid/s", source="manual", source_job_id="sch-1")
    created = schema_maps.create(conn, {"title": "Career map", "mode": "jobs"})
    saved = schema_maps.save(conn, {
        "id": created["id"], "revision": created["revision"], "title": "Career map",
        "graph": {"nodes": [{"id": "role", "kind": "job", "label": "Role", "notes": "", "x": 0, "y": 0, "url": "",
                             "source": {"kind": "job", "id": job.id}}], "edges": []},
        "viewport": {"x": 0, "y": 0, "zoom": 1}})
    assert saved["live"]["role"]["label"] == "Visible only with applyai"
    with schema_maps.applyai_scope(False):
        hidden = schema_maps.load(conn, created["id"])
        assert hidden["live"]["role"] == {"unavailable": True}
        assert hidden["graph"]["nodes"][0]["source"] == {"kind": "job", "id": job.id}
        assert "Visible only" not in json.dumps(schema_maps.network(conn))
        assert "Visible only" not in json.dumps(schema_maps.export(conn, created["id"]))
    assert schema_maps.load(conn, created["id"])["live"]["role"]["label"] == "Visible only with applyai"


def test_schema_web_rejects_new_job_links_without_applyai(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as (app, request):
        status, created = request("/api/schema/create", {"title": "Plain map", "mode": "general"})
        assert status == 200
        body = {"map_id": created["id"], "revision": created["revision"], "label": "Job", "kind": "job",
                "source": {"kind": "job", "id": 1}}
        status, payload = request("/api/schema/add-node", body)
        assert status == 403 and "disabled" in payload["error"]
        assert request("/api/schema/jobs")[0] == 403 and request("/api/schema/import-jobs", {})[0] == 403
        assert request(f"/api/schema/map?id={created['id']}")[0] == 200


def test_ai_endpoints_split_between_core_and_workspace(tmp_path):
    settings = make_root(tmp_path, ("applyai",))
    with site(settings) as (app, request):
        assert request("/api/ai/status")[0] == 200
        assert request("/api/ai/preview", {"purpose": "summarize", "text": "note"})[0] == 403
        assert request("/api/ai/run", {})[0] == 403
        assert request("/api/ai/settings", {})[0] != 403
    settings = make_root(tmp_path / "ws", ("workspace",))
    with site(settings) as (app, request):
        assert request("/api/ai/preview", {"purpose": "summarize", "text": "note"})[0] != 403


def test_config_snapshot_matches_the_loader(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    assert load_config(settings.config_dir).modules == ("workspace",)
    with site(settings) as (app, _request):
        assert app.config.modules == app.modules == ("workspace",)
