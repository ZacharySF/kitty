"""Settings → Desktop: fixed commands forwarded to the desktop shell's owner pipe."""
from __future__ import annotations

import http.client
import json
import threading
from types import SimpleNamespace

import pytest

from kitty import web_auth
from kitty.web_desktop import COMMANDS, DesktopOwner
from kitty.web_server import LocalHTTPServer, WebApplication


class FakeShell:
    """Records owner-pipe lines; optionally answers keep-running like the real shell."""

    def __init__(self, *, tray: bool = True, keep_running: bool = False, answers: bool = True):
        self.lines: list[str] = []
        self.tray = tray
        self.keep_running = keep_running
        self.answers = answers
        self.owner: DesktopOwner | None = None

    def send(self, line: str) -> None:
        self.lines.append(line)
        command = json.loads(line)["command"]
        if self.answers and command in {"keep-running-on", "keep-running-off"}:
            if self.tray:
                self.keep_running = command == "keep-running-on"
            # The real shell answers from its own reader thread.
            threading.Thread(target=self.report).start()

    def report(self) -> None:
        self.owner.report(json.dumps({"keep_running": self.keep_running, "keep_running_available": self.tray}))


def serve(workspace, tmp_path, desktop):
    static = tmp_path / "dist"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>Synthetic website</title>")
    app = WebApplication(workspace.settings, workspace.config, static, desktop=desktop)
    server = LocalHTTPServer(app, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None, *, headers=None):
        method = "GET" if body is None else "POST"
        defaults = {"Host": server.authority, "Origin": server.origin, web_auth.HEADER: server.ui_auth.secret}
        payload = None
        if method == "POST":
            defaults.update({"Content-Type": "application/json", "X-Kitty-Request": "1"})
            payload = json.dumps(body).encode()
        for key, value in (headers or {}).items():
            if value is None:
                defaults.pop(key, None)
            else:
                defaults[key] = value
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        try:
            client.putrequest(method, path, skip_host=True)
            for key, value in defaults.items():
                client.putheader(key, value)
            if payload is not None:
                client.putheader("Content-Length", str(len(payload)))
            client.endheaders(payload)
            response = client.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            client.close()

    def close():
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    return request, close


@pytest.fixture
def desktop(workspace, tmp_path):
    shell = FakeShell()
    shell.owner = DesktopOwner(shell.send, timeout=2)
    shell.report()
    request, close = serve(workspace, tmp_path, shell.owner)
    yield SimpleNamespace(request=request, shell=shell)
    close()


@pytest.fixture
def website(workspace, tmp_path):
    request, close = serve(workspace, tmp_path, None)
    yield request
    close()


def test_plain_website_has_no_desktop_controls(website):
    assert website("/api/desktop") == (200, {"available": False})
    status, data = website("/api/desktop/command", {"command": "quit"})
    assert status == 409 and "desktop app" in data["error"]


def test_state_reflects_the_shells_last_report(desktop):
    assert desktop.request("/api/desktop") == (200, {
        "available": True, "keep_running": False, "keep_running_available": True})
    desktop.shell.keep_running = True
    desktop.shell.report()
    assert desktop.request("/api/desktop")[1]["keep_running"] is True


@pytest.mark.parametrize("command", ["check-updates", "new-quick-note", "quit"])
def test_fixed_commands_reach_the_owner_pipe_verbatim(desktop, command):
    assert desktop.request("/api/desktop/command", {"command": command}) == (200, {"ok": True})
    assert desktop.shell.lines == [json.dumps({"command": command})]


def test_keep_running_waits_for_the_shell_to_confirm(desktop):
    status, data = desktop.request("/api/desktop/command", {"command": "keep-running-on"})
    assert status == 200 and data == {"available": True, "keep_running": True, "keep_running_available": True}
    status, data = desktop.request("/api/desktop/command", {"command": "keep-running-off"})
    assert status == 200 and data["keep_running"] is False
    assert [json.loads(line)["command"] for line in desktop.shell.lines] == ["keep-running-on", "keep-running-off"]


def test_keep_running_is_refused_without_a_tray(desktop):
    desktop.shell.tray = False
    desktop.shell.report()
    status, data = desktop.request("/api/desktop/command", {"command": "keep-running-on"})
    assert status == 409 and "tray" in data["error"]
    assert desktop.shell.lines == []


def test_unconfirmed_keep_running_is_an_error_not_a_success(desktop):
    desktop.shell.answers = False
    status, data = desktop.request("/api/desktop/command", {"command": "keep-running-on"})
    assert status == 504 and "did not confirm" in data["error"]


@pytest.mark.parametrize("body", [
    {"command": "open-url"}, {"command": "quit\n{\"command\":\"check-updates\"}"},
    {"command": ["quit"]}, {"command": None}, {}, {"command": "quit", "url": "https://example.invalid"},
    {"command": "QUIT"}, {"command": " quit"},
])
def test_only_the_fixed_allowlist_passes(desktop, body):
    status, data = desktop.request("/api/desktop/command", body)
    assert status == 400 and "error" in data
    assert desktop.shell.lines == []


@pytest.mark.parametrize("headers", [
    {"Host": "attacker.invalid"}, {"Host": "127.0.0.1:1"},
    {"Host": None}, {"Origin": None},
    {"Origin": "https://attacker.invalid"}, {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
    {"X-Kitty-Request": None}, {"X-Kitty-Request": "0"},
])
def test_commands_require_the_same_local_request_checks(desktop, headers):
    for command in COMMANDS:
        status, data = desktop.request("/api/desktop/command", {"command": command}, headers=headers)
        assert status == 403 and "error" in data
    assert desktop.shell.lines == []


def test_commands_require_json_content_type(desktop):
    status, _ = desktop.request("/api/desktop/command", {"command": "quit"}, headers={"Content-Type": "text/plain"})
    assert status == 415 and desktop.shell.lines == []


@pytest.mark.parametrize("headers", [
    {"Host": "evil.invalid"}, {"Origin": "https://evil.invalid"}, {"Sec-Fetch-Site": "cross-site"},
])
def test_state_read_validates_origin_and_host(desktop, headers):
    assert desktop.request("/api/desktop", headers=headers)[0] == 403


def test_query_parameters_are_rejected(desktop):
    assert desktop.request("/api/desktop?x=1")[0] == 400


@pytest.mark.parametrize("line", [
    "", "not json", "[]", '{"keep_running": true}', '{"keep_running": 1, "keep_running_available": true}',
    '{"keep_running": true, "keep_running_available": true, "extra": 1}', '{"command": "quit"}',
])
def test_malformed_shell_reports_are_ignored(line):
    owner = DesktopOwner(lambda _line: None)
    assert owner.report(line) is False
    assert owner.state() == {"available": True, "keep_running": None, "keep_running_available": False}
