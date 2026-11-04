"""Real desktop sidecar lifecycle against isolated public-example workspaces."""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from kitty import web_auth
from terminal_support import FakeHerdr, FrameReader, websocket


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kitty_desktop_backend", PROJECT / "desktop/backend.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
prepare_workspace = MODULE.prepare_workspace


def test_selected_workspace_is_reused_without_overwriting(tmp_path):
    root = tmp_path / "existing"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "resumes").mkdir()
    profile = root / "config/profile.yaml"
    profile.write_text("name: Existing person\n")
    database = root / "data/apply.db"
    database.write_bytes(b"existing database must never be copied or replaced")
    resume = root / "resumes/registered.pdf"
    resume.write_bytes(b"%PDF-existing")
    prepare_workspace(root, PROJECT / "config", "kitty")
    assert profile.read_text() == "name: Existing person\n"
    assert database.read_bytes() == b"existing database must never be copied or replaced"
    assert resume.read_bytes() == b"%PDF-existing"
    assert (root / "config/tracks.yaml").is_file()
    assert (root / ".private").is_dir()


@pytest.fixture
def backend(tmp_path, request):
    """The frozen entrypoint; indirect parametrization passes extra arguments (e.g. --safe)."""
    extra = list(getattr(request, "param", ()))
    root = tmp_path / "workspace"
    bundle = tmp_path / "bundle"
    assets = bundle / "website"
    assets.mkdir(parents=True)
    shutil.copytree(PROJECT / "config", bundle / "examples", ignore=lambda _path, names: [name for name in names if not name.endswith(".yaml.example")])
    (bundle / "backend-package.txt").write_text("kitty\n")
    (assets / "index.html").write_text("<!doctype html><title>rika!</title><p>Private website</p>")
    # Run the actual frozen entrypoint and owner channel with a synthetic bundle.
    code = (
        "import runpy, sys; "
        "entry=sys.argv[3]; sys._MEIPASS=sys.argv[1]; "
        "sys.argv=['kitty-web', '--workspace', sys.argv[2], *sys.argv[4:]]; "
        "runpy.run_path(entry, run_name='__main__')"
    )
    environment = os.environ.copy()
    for key in ("KITTY_HOME", "KITTY_DB", "KITTY_HOME", "KITTY_DB", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        environment.pop(key, None)
    environment["PYTHONPATH"] = os.pathsep.join((str(PROJECT), str(PROJECT / "src")))
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(bundle), str(root), str(PROJECT / "desktop/backend.py"), *extra],
        cwd=tmp_path, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 40)[0], "Sidecar did not announce readiness"
        message = json.loads(process.stdout.readline())
        # The readiness line is the only place the UI secret leaves the sidecar (to the shell).
        assert set(message) == {"origin", "secret"} and re.fullmatch(r"[0-9a-f]{64}", message["secret"])
        yield process, urlsplit(message["origin"]), root, message["secret"]
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                pytest.fail("Sidecar failed to drain and exit after its owner pipe closed")
        assert process.returncode == 0, process.stderr.read()


def request(origin, method="GET", path="/", headers=None, body=None, *, secret=None):
    connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=10)
    try:
        fields = {**({web_auth.HEADER: secret} if secret else {}), **(headers or {})}
        connection.request(method, path, body=body, headers=fields)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_random_loopback_origin_preserves_same_origin_csrf(backend):
    process, origin, root, secret = backend
    assert origin.scheme == "http" and origin.hostname == "127.0.0.1"
    assert origin.port and origin.port != 8765
    status, headers, body = request(origin)
    assert status == 200 and b"Private website" in body
    assert "connect-src 'self'" in headers["Content-Security-Policy"]
    assert request(origin, headers={"Host": "attacker.example"})[0] == 403
    assert request(origin, headers={"Origin": "https://attacker.example"})[0] == 403
    local_headers = {
        "Origin": origin.geturl(), "Content-Type": "application/json",
        "X-Kitty-Request": "1",
    }
    # Every page header forged exactly is still not enough without the UI secret.
    assert request(origin, "POST", "/api/not-a-route", local_headers, "{}")[0] == 401
    assert request(origin, path="/api/bootstrap", headers={"Origin": origin.geturl().rstrip("/")})[0] == 401
    assert request(origin, "POST", "/api/not-a-route", local_headers, "{}", secret=secret)[0] == 404
    local_headers.pop("X-Kitty-Request")
    assert request(origin, "POST", "/api/not-a-route", local_headers, "{}", secret=secret)[0] == 403
    assert (root / "data/apply.db").is_file()


@pytest.mark.parametrize("backend", [(), ("--safe",)], indirect=True, ids=["normal", "safe"])
def test_safe_flag_reaches_the_web_application(backend):
    process, origin, root, secret = backend
    status, _headers, body = request(origin, path="/api/build", headers={"X-Kitty-Request": "1"}, secret=secret)
    assert status == 200
    assert json.loads(body)["safe"] is ("--safe" in process.args)


def test_owner_quit_leaves_unrelated_process_alive(backend):
    process, origin, root, _secret = backend
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        process.stdin.write("quit\n")
        process.stdin.flush()
        assert process.wait(timeout=30) == 0
        assert unrelated.poll() is None
        assert (root / "data/apply.db").is_file()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=10)


def test_the_sidecar_publishes_its_endpoint_owner_only_and_removes_it_on_quit(backend):
    process, origin, root, secret = backend
    path = root / ".private" / "endpoint.json"
    assert json.loads(path.read_text()) == {"origin": origin.geturl().rstrip("/"), "pairing": "/api/ingress/pair"}
    assert secret not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    process.stdin.write("quit\n")
    process.stdin.flush()
    assert process.wait(timeout=30) == 0
    assert not path.exists()


def owner_line(process, timeout=10):
    assert select.select([process.stdout], [], [], timeout)[0], "No owner-pipe message"
    return json.loads(process.stdout.readline())


def test_settings_desktop_commands_travel_the_owner_pipe(backend):
    process, origin, _root, secret = backend
    local = {"Origin": origin.geturl().rstrip("/"), "Content-Type": "application/json", "X-Kitty-Request": "1"}
    process.stdin.write(json.dumps({"keep_running": False, "keep_running_available": True}) + "\n")
    process.stdin.flush()
    for _ in range(50):
        status, _headers, body = request(origin, path="/api/desktop", secret=secret)
        if json.loads(body)["keep_running"] is False:
            break
    assert status == 200 and json.loads(body) == {"available": True, "keep_running": False, "keep_running_available": True}
    assert request(origin, "POST", "/api/desktop/command", local, '{"command":"new-quick-note"}', secret=secret)[0] == 200
    assert owner_line(process) == {"command": "new-quick-note"}
    # A request without the local header never reaches the shell.
    unmarked = {key: value for key, value in local.items() if key != "X-Kitty-Request"}
    assert request(origin, "POST", "/api/desktop/command", unmarked, '{"command":"quit"}', secret=secret)[0] == 403
    # Nor does a request with every page header but without the UI secret.
    assert request(origin, "POST", "/api/desktop/command", local, '{"command":"quit"}')[0] == 401
    assert not select.select([process.stdout], [], [], 0.5)[0]

    results = []
    worker = threading.Thread(target=lambda: results.append(
        request(origin, "POST", "/api/desktop/command", local, '{"command":"keep-running-on"}', secret=secret)))
    worker.start()
    assert owner_line(process) == {"command": "keep-running-on"}
    process.stdin.write(json.dumps({"keep_running": True, "keep_running_available": True}) + "\n")
    process.stdin.flush()
    worker.join(timeout=10)
    status, _headers, body = results[0]
    assert status == 200 and json.loads(body)["keep_running"] is True

    assert request(origin, "POST", "/api/desktop/command", local, '{"command":"quit"}', secret=secret)[0] == 200
    assert owner_line(process) == {"command": "quit"}
    # Only the shell decides to stop: the backend keeps serving until its owner says quit.
    assert request(origin, path="/api/desktop", secret=secret)[0] == 200
    process.stdin.write("quit\n")
    process.stdin.flush()
    assert process.wait(timeout=30) == 0


@pytest.fixture
def terminal_workspace(tmp_path, monkeypatch):
    """A workspace selecting only the Terminal module, with a synthetic herdr."""
    fake = FakeHerdr(tmp_path)
    for key, value in {"HOME": str(fake.home), "XDG_CONFIG_HOME": str(fake.home / ".config"),
                       "KITTY_HERDR": str(fake.binary), "KITTY_HERDR_SESSION": "kitty-desktop-test",
                       "HERDR_ENV": "1"}.items():
        monkeypatch.setenv(key, value)
    config = tmp_path / "workspace" / "config"
    config.mkdir(parents=True)
    (config / "build.json").write_text('{"schema": 1, "modules": ["terminal"]}')
    try:
        yield fake
    finally:
        fake.cleanup("kitty-desktop-test")
        shutil.rmtree(fake.home, ignore_errors=True)


def test_owner_quit_stops_kitty_herdr_session_but_detaching_does_not(terminal_workspace, backend):
    fake, session = terminal_workspace, "kitty-desktop-test"
    process, origin, _root, secret = backend
    headers = {"Origin": origin.geturl(), "Content-Type": "application/json", "X-Kitty-Request": "1"}
    pages = []
    for _page in range(2):
        status, _headers, body = request(origin, "POST", "/api/terminal/session", headers, "{}", secret=secret)
        assert status == 200
        status, _headers, connection, rest = websocket(
            origin.port, f"/api/terminal/socket?token={json.loads(body)['token']}", {}, secret=secret)
        assert status == 101
        FrameReader(connection, rest).until(b"FAKE-HERDR")
        pages.append(connection)
    # A page going away (or a hidden window) only detaches its client.
    pages[0].close()
    time.sleep(0.3)
    assert fake.listening(session)
    stop = ["--session", session, "session", "stop", session]
    assert stop not in [item["argv"] for item in fake.invocations()]
    # Quit rika! (save and stop workers) also stops rika!'s herdr server, even with a page attached.
    process.stdin.write("quit\n")
    process.stdin.flush()
    assert process.wait(timeout=30) == 0
    pages[1].close()
    assert [item["argv"] for item in fake.invocations()].count(stop) == 1
    assert not fake.listening(session)
