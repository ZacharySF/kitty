"""HTTP module activation, enforcement, frozen selection and data preservation.

Every workspace is a synthetic temporary directory; careers YAML exists only for
the combinations that enable applyai. ``/api/modules`` is answered by rika-server
(server/src/modules.rs), so its tests run through the front, with the Python
selection functions (kitty.modules, kitty.core.builds.store) as the reference.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import stat
import threading
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from kitty import web_atlas, web_auth
from kitty.config import load_config
from kitty.core import applications, backlinks, calendar, jobs, notes, opportunities, schema_maps
from kitty.core.bootstrap import configure_modules
from kitty.core.builds import store
from kitty.core.workspace import open_workspace
from kitty.db import connect
from kitty.modules import DEFAULT_MODULES, MODULE_IDS, load_modules, modules_state, normalize_modules, save_modules
from kitty.settings import Settings
from kitty.web_server import LocalHTTPServer, WebApplication
from terminal_support import front_site

REPO_ROOT = Path(__file__).resolve().parents[1]
CAREERS = ("profile", "tracks", "sources", "watchlist", "answers", "views")
COMBINATIONS = [(), ("applyai",), ("workspace",), ("applyai", "workspace")]
APPLYAI_GETS = ("/api/dashboard", "/api/jobs", "/api/resumes", "/api/profile", "/api/sources",
                "/api/reminders", "/api/pipeline", "/api/mail", "/api/triage", "/api/automation/status")
APPLYAI_POSTS = ("/api/jobs/add", "/api/automation/start", "/api/resumes/register",
                 "/api/profile/basics", "/api/reminders")
# The calendar's local routes are rika-server's (tests/test_calendar.py); these two are Python's.
WORKSPACE_GETS = ("/api/calendar/google/status", "/api/notes", "/api/schema", "/api/study", "/api/flashcards",
                  "/api/opportunities", "/api/documents", "/api/atlas/sections/notes")
WORKSPACE_POSTS = ("/api/notes/pages", "/api/calendar/import/preview", "/api/schema/create",
                   "/api/ai/preview", "/api/ai/mind-map/preview", "/api/ai/run",
                   "/api/opportunities", "/api/documents/prepare")


def make_root(tmp_path: Path, modules: tuple[str, ...] | None, *, careers: bool | None = None) -> Settings:
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True, exist_ok=True)
    if modules is not None:
        (root / "config" / "build.json").write_text(
            json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
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
    app = WebApplication(settings, config, static, ui_session="ui-test")
    server = LocalHTTPServer(app, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None):
        headers = {"Origin": server.origin, web_auth.HEADER: server.ui_auth.secret}
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


@contextmanager
def front(settings: Settings):
    """rika-server with `kitty-web --worker` behind it: one process, as `kitty-web` runs.

    A synthetic $KITTY_HERDR makes Terminal's herdr pack present (kitty.packs), as the other
    packs are in a checkout, so turning Terminal on is not refused for a missing download; the
    Python reference answers computed here see the same variable."""
    herdr = settings.root / "synthetic-herdr"
    herdr.write_text("#!/bin/sh\nexit 0\n")
    herdr.chmod(0o755)
    previous = os.environ.get("KITTY_HERDR")
    os.environ["KITTY_HERDR"] = str(herdr)
    try:
        with front_site(settings) as running:
            yield running.request
    finally:
        if previous is None:
            os.environ.pop("KITTY_HERDR", None)
        else:
            os.environ["KITTY_HERDR"] = previous


@pytest.mark.parametrize("modules", COMBINATIONS)
def test_bootstrap_and_modules_endpoint_for_every_combination(tmp_path, modules):
    settings = make_root(tmp_path, modules)
    assert (settings.config_dir / "tracks.yaml").exists() == ("applyai" in modules)
    with front(settings) as request:
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


def test_legacy_workspace_without_a_build_file_is_unchanged(tmp_path):
    settings = make_root(tmp_path, None)
    assert not (settings.config_dir / "build.json").exists()
    with front(settings) as request:
        status, boot = request("/api/bootstrap")
        assert status == 200 and boot["modules"]["enabled"] == list(DEFAULT_MODULES)
        assert boot["modules"]["restart_required"] is False
        assert "tracks" in boot and request("/api/dashboard")[0] == 200
        assert request("/api/calendar")[0] == 200


@pytest.mark.parametrize("modules", [(), ("workspace",)])
def test_malformed_careers_yaml_is_ignored_while_applyai_is_disabled(tmp_path, modules):
    settings = make_root(tmp_path, modules, careers=False)
    for name in ("profile", "tracks", "sources", "answers", "apply", "triage"):
        (settings.config_dir / f"{name}.yaml").write_text("{{{ broken: [", encoding="utf-8")
    with front(settings) as request:
        assert request("/api/bootstrap")[0] == 200
        assert request("/api/reload", {})[0] == 200
        assert request("/api/profile")[0] == 403


def test_modules_post_persists_pending_selection_without_activating_it(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with front(settings) as request:
        status, state = request("/api/modules", {"enabled": ["workspace", "applyai"]})
        assert status == 200
        assert state["enabled"] == ["workspace"]
        assert state["configured"] == ["applyai", "workspace"]
        assert state["restart_required"] is True
        assert load_modules(settings.config_dir) == ("applyai", "workspace")
        # Nothing activates in this process, not even after an explicit reload.
        assert request("/api/reload", {})[0] == 200
        status, boot = request("/api/bootstrap")
        assert boot["modules"]["enabled"] == ["workspace"] and "tracks" not in boot
        assert request("/api/dashboard")[0] == 403 and request("/api/jobs")[0] == 403
        assert request("/api/automation/status")[0] == 403
        status, state = request("/api/modules", {"enabled": ["workspace"]})
        assert state["restart_required"] is False
        assert request("/api/modules")[1] == state
        assert request("/api/modules?x=1") == (400, {"error": "This route does not accept query parameters"})


def test_pending_enable_of_applyai_is_neither_read_nor_validated_by_reload(tmp_path):
    settings = make_root(tmp_path, ("workspace",), careers=False)
    with front(settings) as request:
        assert request("/api/modules", {"enabled": ["applyai", "workspace"]})[0] == 200
        (settings.config_dir / "tracks.yaml").write_text("invalid: [unterminated", encoding="utf-8")
        assert request("/api/reload", {})[0] == 200
        status, boot = request("/api/bootstrap")
        assert status == 200 and boot["modules"]["enabled"] == ["workspace"] and "tracks" not in boot


@pytest.mark.parametrize("body", [
    {}, {"enabled": "applyai"}, {"enabled": ["nope"]}, {"enabled": ["applyai", "applyai"]},
    {"enabled": [1]}, {"enabled": ["applyai"], "extra": True}, {"enabled": None},
    {"enabled": ["../../etc/passwd"]},
])
def test_modules_post_rejects_invalid_bodies_and_leaves_the_file_alone(tmp_path, body):
    settings = make_root(tmp_path, ("workspace",))
    path = settings.config_dir / "build.json"
    with front(settings) as request:
        before = path.read_text(encoding="utf-8")  # as the opened workspace settled it
        status, payload = request("/api/modules", body)
        assert status == 400 and "error" in payload
        enabled = body.get("enabled")
        if set(body) == {"enabled"} and isinstance(enabled, list):
            with pytest.raises(ValueError) as python:
                normalize_modules(enabled)
            assert payload == {"error": str(python.value)}
        else:
            assert payload == {"error": "Expected {\"enabled\": [module ids]}"}
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in settings.config_dir.glob("build*")) == ["build.json"]


def test_modules_post_never_writes_outside_the_build_file(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with front(settings) as request:
        before = {p.name: p.read_bytes() for p in settings.config_dir.iterdir()}
        assert request("/api/modules", {"enabled": []})[0] == 200
        after = {p.name: p.read_bytes() for p in settings.config_dir.iterdir()}
    assert {k for k in after if after[k] != before.get(k)} == {"build.json"} and set(after) == set(before)


def test_modules_post_writes_the_text_the_python_store_writes(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    path = settings.config_dir / "build.json"
    with front(settings) as request:
        written = json.dumps({"schema": 1, "name": "Zoë's \"build\"\t", "modules": ["workspace"],
                              "theme": {"base": "dracula"}, "author": "\u2028"}, ensure_ascii=True)
        path.write_text(written, encoding="utf-8")
        path.chmod(0o640)
        expected = store.changed(written, lambda build: build.__setitem__("modules", ["applyai", "terminal"]))
        assert request("/api/modules", {"enabled": ["terminal", "applyai"]})[0] == 200
        assert path.read_text(encoding="utf-8") == expected
        assert stat.S_IMODE(path.stat().st_mode) == 0o640, "the file keeps its mode, as the Python store does"
        # An unchanged selection keeps the exact text, however it is written.
        path.write_text('{"modules":["workspace"],"schema":1}', encoding="utf-8")
        assert request("/api/modules", {"enabled": ["workspace"]})[0] == 200
        assert path.read_text(encoding="utf-8") == '{"modules":["workspace"],"schema":1}'
        # A workspace whose build file went away gets a new one, as the Python store writes it.
        path.unlink()
        assert request("/api/modules", {"enabled": ["workspace"]})[0] == 200
        fresh = path.read_text(encoding="utf-8")
    assert fresh == store.changed(store.initial_text(), lambda build: build.__setitem__("modules", ["workspace"]))


def test_choosing_applyai_provisions_missing_careers_files_only(tmp_path):
    settings = make_root(tmp_path, ("workspace",), careers=False)
    (settings.config_dir / "profile.yaml").write_text("mine: true\n", encoding="utf-8")
    (settings.config_dir / "views.yaml.example").write_text("local: template\n", encoding="utf-8")
    with front(settings) as request:
        assert request("/api/modules", {"enabled": ["applyai"]})[0] == 200
    assert (settings.config_dir / "profile.yaml").read_text(encoding="utf-8") == "mine: true\n"
    assert (settings.config_dir / "views.yaml").read_text(encoding="utf-8") == "local: template\n"
    for name in ("tracks", "sources", "watchlist", "answers", "skills", "conferences", "resume_terms", "triage", "apply"):
        created = settings.config_dir / f"{name}.yaml"
        assert created.read_bytes() == (REPO_ROOT / "config" / f"{name}.yaml.example").read_bytes(), name
        assert stat.S_IMODE(created.stat().st_mode) == 0o600


BROKEN_BUILDS = {
    "not json": "{\"schema\": 1,",
    "schema error": json.dumps({"schema": 1, "modules": ["nope"], "theme": {"base": 3}}),
    "too large": json.dumps({"schema": 1, "name": "x" * (300 * 1024)}),
    "invalid utf-8": b"{\"schema\": 1, \"name\": \"\xff\"}",
    "empty": "",
}
INTERNAL = (500, {"error": "Local operation failed; inspect CLI diagnostics"})


def python_answer(config_dir: Path, enabled: list | None) -> tuple[int, dict]:
    """What the Python handlers answered: `modules_state` for a GET, `set_modules` for a POST."""
    try:
        if enabled is not None:
            configure_modules(config_dir, enabled)
        return 200, modules_state(("workspace",), config_dir)
    except ValueError as exc:
        return (400, {"error": str(exc)}) if enabled is not None else INTERNAL
    except Exception:  # noqa: BLE001 - the handler's catch-all
        return INTERNAL


@pytest.mark.parametrize("case", [*BROKEN_BUILDS, "symlink", "directory", "missing"])
def test_the_state_and_refusals_match_python_for_every_build_file(tmp_path, case):
    settings = make_root(tmp_path, ("workspace",))
    config_dir = settings.config_dir
    path = config_dir / "build.json"
    with front(settings) as request:
        path.unlink()
        if case in BROKEN_BUILDS:
            content = BROKEN_BUILDS[case]
            path.write_bytes(content if isinstance(content, bytes) else content.encode())
        elif case == "symlink":
            (config_dir / "elsewhere.json").write_text(json.dumps({"schema": 1, "modules": []}))
            path.symlink_to(config_dir / "elsewhere.json")
        elif case == "directory":
            path.mkdir()
        assert request("/api/modules") == python_answer(config_dir, None)
        # The Python store's answer to the same save, on a copy of the same workspace.
        copy = tmp_path / "copy"
        shutil.copytree(settings.root, copy, symlinks=True)
        expected = python_answer(copy / "config", ["workspace", "terminal"])
        assert request("/api/modules", {"enabled": ["workspace", "terminal"]}) == expected
        if path.is_file() and not path.is_symlink():
            assert path.read_bytes() == (copy / "config" / "build.json").read_bytes()


def test_a_legacy_selection_written_while_running_waits_for_the_next_start(tmp_path):
    """Opening the workspace moves modules.yaml into the build (store.ensure); the front never
    parses the pre-build YAML itself, so one written by hand later is left for the next start."""
    settings = make_root(tmp_path, ("workspace",))
    config_dir = settings.config_dir
    with front(settings) as request:
        (config_dir / "build.json").unlink()
        (config_dir / "modules.yaml").write_text("enabled: [workspace]\n", encoding="utf-8")
        status, state = request("/api/modules")
        assert status == 200 and state["configured"] == ["workspace"] and not state["restart_required"]
        assert "config/modules.yaml is read only when rika! starts" in state["error"]
        status, answer = request("/api/modules", {"enabled": ["terminal"]})
        assert status == 400 and "restart rika!" in answer["error"]
        assert not (config_dir / "build.json").exists()
    assert load_modules(config_dir) == ("workspace",)


def test_restart_activates_the_saved_selection_and_preserves_data(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with front(settings) as request:
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
    with front(settings) as request:
        assert request("/api/bootstrap")[1]["modules"]["enabled"] == ["applyai"]
        assert request("/api/notes")[0] == 403 and request("/api/dashboard")[0] == 200
        assert request("/api/modules", {"enabled": ["workspace", "applyai"]})[0] == 200
    with front(settings) as request:
        assert request("/api/bootstrap")[1]["modules"]["enabled"] == ["applyai", "workspace"]
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
    save_modules(settings.config_dir, ["workspace"])
    with site(settings) as (app, request):
        assert request(f"/api/jobs/{job.id}")[0] == 403
        assert request("/api/atlas/sections/jobs")[0] == 403
    with db(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == tracks_before
    save_modules(settings.config_dir, ["applyai"])
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
    """The maps' routes are rika-server's (server/src/schema)."""
    settings = make_root(tmp_path, ("workspace",))
    with front(settings) as request:
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
