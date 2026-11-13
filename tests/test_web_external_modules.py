"""External modules through HTTP: install from a fake GitHub, restart, route, frame, remove.

The GitHub API is a loopback stand-in (external_module_kit.FakeGitHub, reached through
KITTY_GITHUB_API); every module is a synthetic tarball built here. A "restart" is a new
WebApplication over the same workspace, exactly like kitty-web start-up.
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from external_module_kit import FakeGitHub, process_files, python_files, tarball

from kitty import web_auth
from kitty.core import keys
from kitty.core.external_modules import registry
from kitty.core.workspace import open_workspace
from kitty.settings import Settings
from kitty.web_desktop import DesktopOwner
from kitty.web_server import LocalHTTPServer, WebApplication


def make_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": []}), encoding="utf-8")
    return Settings.load(root=root, db_path=root / "data" / "web.db")


@contextmanager
def serve(settings: Settings):
    """One rika! process: the installed modules are read once, at start-up."""
    static = settings.root / "dist"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>Synthetic</title>")
    with open_workspace(settings, modules=()) as workspace:
        config = workspace.config
    app = WebApplication(settings, config, static)
    server = LocalHTTPServer(app, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None, *, auth=True):
        headers = {"Origin": server.origin}
        if auth:
            headers[web_auth.HEADER] = server.ui_auth.secret
        payload, method = None, "GET"
        if body is not None:
            method = "POST"
            headers.update({"Content-Type": "application/json", "X-Kitty-Request": "1"})
            payload = json.dumps(body).encode()
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
        try:
            client.request(method, path, body=payload, headers=headers)
            response = client.getresponse()
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(data) if data else None
            return response.status, data, dict(response.getheaders())
        finally:
            client.close()

    def finish(started):
        assert started[0] == 200, started
        task = started[1]["task"]
        deadline = time.monotonic() + 30
        while task["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
            task = request(f"/api/tasks/{task['id']}")[1]
        return task

    try:
        yield app, request, finish
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        app.close()


@pytest.fixture
def fake_github(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with FakeGitHub() as fake:
        monkeypatch.setenv("KITTY_GITHUB_API", fake.origin)
        yield fake


def external(request) -> dict:
    status, data, _ = request("/api/modules")
    assert status == 200
    return data["external"]


def test_python_module_installs_then_routes_and_serves_its_view_after_restart(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    fake_github.publish("synthetic/rika-synth", "rika-synth", "0.1.0",
                        tarball(python_files(package="rika_synth_web_a")))
    with serve(settings) as (_app, request, finish):
        before = external(request)
        assert before == {"installed": [], "views": [], "token": {"configured": False}}
        task = finish(request("/api/external-modules/install", {"repo": "synthetic/rika-synth"}))
        assert task["status"] == "completed", task
        state = task["result"]
        [entry] = state["external"]["installed"]
        assert entry["status"] == "restart-to-install" and entry["active_version"] is None
        assert entry["repo"] == "synthetic/rika-synth" and entry["version"] == "0.1.0" and entry["name"] == "Synthetic"
        assert "Restart rika!" in entry["message"]
        assert state["restart_required"] is True and state["external"]["views"] == []
        status, data, _ = request("/api/modules/rika-synth/hello")
        assert status == 409 and "Restart rika!" in data["error"]
        assert fake_github.requests[0]["path"] == "/repos/synthetic/rika-synth/releases/latest"
        assert fake_github.requests[0]["authorization"] is None

    with serve(settings) as (_app, request, _finish):
        status, boot, _ = request("/api/bootstrap")
        assert status == 200 and boot["modules"]["restart_required"] is False
        [entry] = boot["modules"]["external"]["installed"]
        assert entry["status"] == "active" and entry["active_version"] == "0.1.0"
        assert boot["modules"]["external"]["views"] == [{
            "module": "rika-synth", "version": "0.1.0", "id": "synth", "label": "Synthetic", "icon": "study",
            "group": "study", "first": True, "ui": "element",
            "script": "/modules/rika-synth/0.1.0/ui/view.js", "tag": "rika-synth-view",
        }]
        assert request("/api/modules/rika-synth/hello")[:2] == (200, {"hello": "synthetic", "count": 0, "started": 1})
        assert request("/api/modules/rika-synth/count", {"note": "synthetic"})[:2] == (200, {"ok": True})
        assert request("/api/modules/rika-synth/hello")[1]["count"] == 1
        assert request("/api/modules/rika-synth/elsewhere")[0] == 404
        assert request("/api/modules/rika-nobody/hello")[0] == 404
        # The element script is same-origin static content: no UI secret, like the SPA itself.
        status, script, headers = request("/modules/rika-synth/0.1.0/ui/view.js", auth=False)
        assert status == 200 and script.startswith(b"customElements.define")
        assert headers["Content-Type"].startswith("text/javascript")
        # The page re-imports a script after a failed load with ?retry=<n>; nothing else is accepted.
        assert request("/modules/rika-synth/0.1.0/ui/view.js?retry=2", auth=False)[0] == 200
        assert request("/modules/rika-synth/0.1.0/ui/view.js?url=https://example.invalid", auth=False)[0] == 400
        for hidden in ("/modules/rika-synth/0.1.0/rika_synth_web_a/__init__.py",
                       "/modules/rika-synth/0.1.0/rika-module.json",
                       "/modules/rika-synth/0.0.9/ui/view.js",
                       "/modules/rika-nobody/0.1.0/ui/view.js",
                       "/modules/rika-synth/0.1.0/migrations/001_counter.sql"):
            assert request(hidden, auth=False)[0] == 404, hidden
        assert request("/modules/rika-synth/0.1.0/../../data/web.db", auth=False)[0] == 400
        assert "rika_synth_web_a" in sys.modules
    assert "rika_synth_web_a" not in sys.modules


def test_process_module_starts_on_demand_in_a_clean_environment_on_a_framed_port(tmp_path, fake_github, monkeypatch):
    settings = make_settings(tmp_path)
    for name in ("OPENROUTER_API_KEY", "GITHUB_TOKEN", "KITTY_HOME"):
        monkeypatch.setenv(name, "synthetic-never-forward")
    fake_github.publish("synthetic/rika-proc", "rika-proc", "0.1.0",
                        tarball(process_files(command=[sys.executable, "server.py"])))
    with serve(settings) as (app, request, finish):
        assert finish(request("/api/external-modules/install", {"repo": "synthetic/rika-proc"}))["status"] == "completed"
        assert app.frame_ports() == ()

    with serve(settings) as (app, request, _finish):
        [port] = app.frame_ports()
        url = f"http://127.0.0.1:{port}"
        [view] = external(request)["views"]
        assert view["ui"] == "iframe" and "script" not in view and view["group"] == "study" and view["first"] is False
        status, data, headers = request("/api/modules/rika-proc")
        assert (status, data) == (200, {"url": url, "available": False})
        assert f"frame-src http://127.0.0.1:3210 {url};" in headers["Content-Security-Policy"]
        assert request("/api/modules/rika-proc/start", {"settings": "x"})[0] == 400
        assert request("/api/modules/rika-proc/start", {})[:2] == (200, {"url": url, "available": True})
        assert request("/api/modules/rika-proc")[1] == {"url": url, "available": True}
        probe = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        probe.request("GET", "/")
        served = json.loads(probe.getresponse().read())
        probe.close()
        data_dir = registry.data_dir(settings, "rika-proc")
        assert served == {"data": str(data_dir), "home": str(data_dir / "home"), "secrets": []}
        assert data_dir.stat().st_mode & 0o777 == 0o700
    probe = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    with pytest.raises(OSError):
        probe.request("GET", "/")
        probe.getresponse()
    probe.close()


def test_a_process_that_never_becomes_ready_is_a_plain_error(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    files = process_files(command=[sys.executable, "-c", "import time; time.sleep(30)"])
    data = json.loads(files["rika-module.json"])
    data["process"]["ready_timeout_s"] = 1
    files["rika-module.json"] = json.dumps(data).encode()
    fake_github.publish("synthetic/rika-proc", "rika-proc", "0.1.0", tarball(files))
    with serve(settings) as (_app, request, finish):
        assert finish(request("/api/external-modules/install", {"repo": "synthetic/rika-proc"}))["status"] == "completed"
    with serve(settings) as (_app, request, _finish):
        status, data, _ = request("/api/modules/rika-proc/start", {})
        assert status == 503 and "did not become ready" in data["error"]


def test_update_remove_and_check_through_the_settings_api(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    fake_github.publish("synthetic/rika-synth", "rika-synth", "0.1.0",
                        tarball(python_files(package="rika_synth_web_b")))
    with serve(settings) as (_app, request, finish):
        assert finish(request("/api/external-modules/install", {"repo": "synthetic/rika-synth"}))["status"] == "completed"

    with serve(settings) as (_app, request, finish):
        check = finish(request("/api/external-modules/check", {}))
        assert check["result"] == {"updates": {}, "errors": {}}
        fake_github.publish("synthetic/rika-synth", "rika-synth", "0.1.1",
                            tarball(python_files(version="0.1.1", package="rika_synth_web_b")))
        check = finish(request("/api/external-modules/check", {}))
        assert check["result"] == {"updates": {"rika-synth": "0.1.1"}, "errors": {}}
        task = finish(request("/api/external-modules/update", {"id": "rika-synth"}))
        assert task["status"] == "completed", task
        [entry] = task["result"]["external"]["installed"]
        assert (entry["status"], entry["version"], entry["active_version"]) == ("restart-to-update", "0.1.1", "0.1.0")
        assert "Restart rika!" in entry["message"]
        # The running process keeps serving the version it loaded.
        assert request("/api/modules/rika-synth/hello")[0] == 200
        status, state, _ = request("/api/external-modules/remove", {"id": "rika-synth"})
        assert status == 200
        [entry] = state["external"]["installed"]
        assert entry["status"] == "restart-to-remove" and "Restart rika!" in entry["message"]
        assert state["restart_required"] is True

    with serve(settings) as (_app, request, _finish):
        assert external(request)["installed"] == [] and external(request)["views"] == []
        assert request("/api/modules/rika-synth/hello")[0] == 404
        assert not (registry.modules_root(settings) / "rika-synth").exists()


def test_removing_a_module_that_never_ran_deletes_it_at_once(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    fake_github.publish("synthetic/rika-synth", "rika-synth", "0.1.0", tarball(python_files(package="rika_synth_web_c")))
    with serve(settings) as (_app, request, finish):
        assert finish(request("/api/external-modules/install", {"repo": "synthetic/rika-synth"}))["status"] == "completed"
        status, state, _ = request("/api/external-modules/remove", {"id": "rika-synth"})
        assert status == 200 and state["external"]["installed"] == [] and state["restart_required"] is False
        assert not (registry.modules_root(settings) / "rika-synth").exists()


@pytest.mark.parametrize("path,body,expected", [
    ("/api/external-modules/install", {}, 400),
    ("/api/external-modules/install", {"repo": "not a repository"}, 400),
    ("/api/external-modules/install", {"repo": "o/r", "extra": 1}, 400),
    ("/api/external-modules/update", {"id": "rika-nobody"}, 404),
    ("/api/external-modules/remove", {"id": "rika-nobody"}, 404),
    ("/api/external-modules/check", {"x": 1}, 400),
])
def test_settings_api_rejects_bad_requests_plainly(tmp_path, fake_github, path, body, expected):
    with serve(make_settings(tmp_path)) as (_app, request, _finish):
        status, data, _ = request(path, body)
        assert status == expected and data["error"]


def test_a_failed_download_check_is_reported_verbatim(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    fake_github.publish("synthetic/rika-synth", "rika-synth", "0.1.0", tarball(python_files()), digest="sha256:" + "0" * 64)
    with serve(settings) as (_app, request, finish):
        task = finish(request("/api/external-modules/install", {"repo": "synthetic/rika-synth"}))
        assert task["status"] == "failed" and "checksum" in task["error"]
        assert external(request)["installed"] == []


def test_github_token_comes_from_the_keystore(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    keys.for_settings(settings).set("GITHUB", "synthetic-github-token")
    fake_github.publish("synthetic/private", "rika-synth", "0.1.0", tarball(python_files(package="rika_synth_web_d")))
    with serve(settings) as (_app, request, finish):
        assert external(request)["token"] == {"configured": True}
        assert finish(request("/api/external-modules/install", {"repo": "synthetic/private"}))["status"] == "completed"
    assert fake_github.requests[0]["authorization"] == "Bearer synthetic-github-token"


def test_a_package_name_clash_is_refused(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    fake_github.publish("synthetic/rika-synth", "rika-synth", "0.1.0", tarball(python_files(package="json")))
    with serve(settings) as (_app, request, finish):
        task = finish(request("/api/external-modules/install", {"repo": "synthetic/rika-synth"}))
        assert task["status"] == "failed" and "json" in task["error"]


def test_a_module_that_fails_to_load_is_reported_and_rika_keeps_working(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    files = python_files(package="rika_synth_web_e")
    files["rika_synth_web_e/__init__.py"] = b"raise RuntimeError('synthetic load failure')\n"
    fake_github.publish("synthetic/rika-synth", "rika-synth", "0.1.0", tarball(files))
    with serve(settings) as (_app, request, finish):
        assert finish(request("/api/external-modules/install", {"repo": "synthetic/rika-synth"}))["status"] == "completed"
    with serve(settings) as (_app, request, _finish):
        status, boot, _ = request("/api/bootstrap")
        assert status == 200
        [entry] = boot["modules"]["external"]["installed"]
        assert entry["status"] == "failed" and "synthetic load failure" in entry["error"]
        assert boot["modules"]["external"]["views"] == [] and boot["modules"]["restart_required"] is False
        assert request("/api/modules/rika-synth/hello")[0] == 503
        assert request("/modules/rika-synth/0.1.0/ui/view.js", auth=False)[0] == 404
    assert "rika_synth_web_e" not in sys.modules


def test_the_desktop_shell_is_told_which_module_frames_to_allow():
    sent = []
    owner = DesktopOwner(sent.append)
    owner.allow_frame(43210)
    assert sent == ['{"command": "allow-frame", "port": 43210}']
    for bad in (0, 80, 70000, True, "43210"):
        with pytest.raises(ValueError):
            owner.allow_frame(bad)
