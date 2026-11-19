"""External modules behind rika-server: the front adds them to ``/api/modules`` and
``/api/bootstrap``, forwards their routes and element scripts to the worker, and frames
their servers in its Content-Security-Policy (kitty.web_external_modules, server/src/modules.rs).

A loopback GitHub stand-in serves synthetic releases; nothing reaches the network.
"""
from __future__ import annotations

import http.client
import json
import sys
import time
from pathlib import Path

import pytest
from external_module_kit import FakeGitHub, process_files, python_files, tarball

from kitty import web_auth
from kitty.settings import Settings
from terminal_support import front_site


def make_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": []}), encoding="utf-8")
    return Settings.load(root=root, db_path=root / "data" / "web.db")


@pytest.fixture
def fake_github(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with FakeGitHub() as fake:
        # The front passes its environment to the worker, which installs from here.
        monkeypatch.setenv("KITTY_GITHUB_API", fake.origin)
        yield fake


def raw(running, path: str, *, auth: bool = True):
    """A page GET to the front: status, body and headers."""
    headers = {"Origin": running.server.origin}
    if auth:
        headers[web_auth.HEADER] = running.secret
    client = http.client.HTTPConnection("127.0.0.1", running.port, timeout=30)
    try:
        client.request("GET", path, headers=headers)
        response = client.getresponse()
        return response.status, response.read(), response.headers
    finally:
        client.close()


def install(running, repo: str) -> None:
    status, started = running.request("/api/external-modules/install", {"repo": repo})
    assert status == 200, started
    task = started["task"]
    deadline = time.monotonic() + 30
    while task["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        task = running.request(f"/api/tasks/{task['id']}")[1]
    assert task["status"] == "completed", task


def test_the_front_reports_installs_and_serves_an_active_modules_routes_and_script(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    fake_github.publish("synthetic/rika-synth", "rika-synth", "0.1.0",
                        tarball(python_files(package="rika_synth_front")))
    with front_site(settings) as running:
        status, state = running.request("/api/modules")
        assert status == 200 and state["restart_required"] is False
        assert state["external"] == {"installed": [], "views": [], "token": {"configured": False}}
        install(running, "synthetic/rika-synth")
        status, state = running.request("/api/modules")
        [entry] = state["external"]["installed"]
        assert entry["status"] == "restart-to-install" and state["restart_required"] is True
        assert running.request("/api/bootstrap")[1]["modules"] == state
        # Saving the built-in selection answers the same combined state.
        assert running.request("/api/modules", {"enabled": []}) == (200, state)

    with front_site(settings) as running:
        status, boot = running.request("/api/bootstrap")
        assert status == 200 and boot["modules"]["restart_required"] is False
        [view] = boot["modules"]["external"]["views"]
        assert view["script"] == "/modules/rika-synth/0.1.0/ui/view.js" and view["ui"] == "element"
        assert running.request("/api/modules/rika-synth/hello") == (200, {"hello": "synthetic", "count": 0, "started": 1})
        assert running.request("/api/modules/rika-synth/count", {"note": "synthetic"}) == (200, {"ok": True})
        assert running.request("/api/modules/rika-synth/hello")[1]["count"] == 1
        # The element script is same-origin static content: no UI secret, like the page itself.
        status, script, headers = raw(running, view["script"], auth=False)
        assert status == 200 and script.startswith(b"customElements.define")
        assert headers["Content-Type"].startswith("text/javascript")
        assert raw(running, view["script"] + "?retry=2", auth=False)[0] == 200
        assert raw(running, view["script"] + "?url=https://example.invalid", auth=False)[0] == 400
        assert raw(running, "/modules/rika-synth/0.1.0/rika-module.json", auth=False)[0] == 404


def test_the_front_frames_a_process_modules_server(tmp_path, fake_github):
    settings = make_settings(tmp_path)
    fake_github.publish("synthetic/rika-proc", "rika-proc", "0.1.0",
                        tarball(process_files(command=[sys.executable, "server.py"])))
    with front_site(settings) as running:
        install(running, "synthetic/rika-proc")
        assert "frame-src http://127.0.0.1:3210;" in raw(running, "/")[2]["Content-Security-Policy"]

    with front_site(settings) as running:
        status, answer = running.request("/api/modules/rika-proc")
        assert status == 200 and answer["available"] is False
        frames = f"frame-src http://127.0.0.1:3210 {answer['url']};"
        # On what the front answers itself (the page, the module state) and what it forwards.
        for path in ("/", "/api/modules", "/api/modules/rika-proc"):
            assert frames in raw(running, path)[2]["Content-Security-Policy"], path
