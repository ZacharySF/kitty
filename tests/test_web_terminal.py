"""Terminal module over HTTP/WebSocket against a synthetic herdr; never a real session.

Every test runs with HOME/XDG_CONFIG_HOME in a throwaway directory, and with the
parent environment pretending kitty! itself runs inside herdr and tmux.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import stat
import threading
import time
from pathlib import Path

import pytest

from kitty import web_terminal
from kitty.web_server import LocalHTTPServer
from terminal_support import FakeHerdr, FrameReader, Site, make_root, send_frame, site

SESSION = "kitty-test"
STOP = ["--session", SESSION, "session", "stop", SESSION]


@pytest.fixture
def herdr(tmp_path, monkeypatch):
    fake = FakeHerdr(tmp_path)
    monkeypatch.setenv("HOME", str(fake.home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake.home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake.home / ".local" / "state"))
    monkeypatch.setenv("KITTY_HERDR", str(fake.binary))
    monkeypatch.setenv("KITTY_HERDR_SESSION", SESSION)
    # kitty! may itself run inside a herdr pane or tmux; none of that reaches its child.
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_SESSION", "default")
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(fake.home / "someone-elses-herdr.sock"))
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    try:
        yield fake
    finally:
        fake.cleanup(SESSION)
        shutil.rmtree(fake.home, ignore_errors=True)


def refused(status, body) -> bool:
    return status == 403 and isinstance(body, dict) and "module is disabled" in body.get("error", "")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_module_off_has_no_route_process_or_socket(tmp_path, herdr):
    settings = make_root(tmp_path, ())
    with site(settings) as running:
        assert refused(*running.request("/api/terminal/session", {}))
        status, _headers, connection, rest = running.connect("0" * 64)
        connection.close()
        assert status == 403 and b"module is disabled" in rest
    assert herdr.invocations() == []
    assert not (herdr.home / ".config" / "herdr").exists()
    assert not (settings.root / ".private" / "herdr").exists()


def test_enabled_terminal_starts_nothing_until_a_page_attaches(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        status, boot = running.request("/api/bootstrap")
        assert status == 200 and boot["modules"]["enabled"] == ["terminal"]
        running.token()
    # Issuing a token is not an attach; closing must not run herdr or create its socket.
    assert herdr.invocations() == []
    assert not (herdr.home / ".config" / "herdr").exists()


def test_token_is_issued_only_by_the_header_protected_api(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        assert running.request("/api/terminal/session", {}, headers={"X-Kitty-Request": None})[0] == 403
        assert running.request("/api/terminal/session", {}, headers={"Content-Type": "text/plain"})[0] == 415
        assert running.request("/api/terminal/session", {}, headers={"Origin": "http://127.0.0.1:1"})[0] == 403
        assert running.request("/api/terminal/session", {"cols": 80})[0] == 400
        assert running.request("/api/terminal/session")[0] in (404, 405)
        first, second = running.token(), running.token()
        assert first != second and len(first) == 64 and int(first, 16) >= 0
    assert herdr.invocations() == []


@pytest.mark.parametrize("fault", [
    "wrong-origin", "missing-origin", "other-port-origin", "wrong-host", "localhost-host",
    "missing-token", "unknown-token", "stale-token", "foreign-token",
])
def test_upgrade_is_refused_without_the_same_origin_proof(tmp_path, herdr, monkeypatch, fault):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        token = running.token()
        headers: dict[str, str | None] = {}
        if fault == "wrong-origin":
            headers["Origin"] = "https://attacker.example"
        elif fault == "missing-origin":
            headers["Origin"] = None
        elif fault == "other-port-origin":
            headers["Origin"] = f"http://127.0.0.1:{running.port + 1}"
        elif fault == "wrong-host":
            headers["Host"] = "attacker.example"
        elif fault == "localhost-host":
            headers["Host"] = f"localhost:{running.port}"
            headers["Origin"] = f"http://localhost:{running.port}"
        elif fault == "missing-token":
            token = None
        elif fault == "unknown-token":
            token = "f" * 64
        elif fault == "stale-token":
            monkeypatch.setattr(web_terminal, "TOKEN_TTL", 0.05)
            token = running.token()
            time.sleep(0.2)
        elif fault == "foreign-token":
            with site(make_root(tmp_path / "other", ("terminal",))) as other:
                token = other.token()
        status, _headers, connection, _rest = running.connect(token, headers=headers)
        connection.close()
        assert status == 403, fault
        # A refused proof never reached herdr.
        assert herdr.invocations() == []
        if fault in ("wrong-origin", "missing-origin", "wrong-host"):
            # The genuine token survives a refused forgery and still works once.
            good = running.connect(running.token())
            good[2].close()
            assert good[0] == 101


def test_a_token_authorises_exactly_one_upgrade(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        token = running.token()
        status, _headers, first, rest = running.connect(token)
        assert status == 101
        FrameReader(first, rest).until(b"FAKE-HERDR")
        status, _headers, second, _rest = running.connect(token)
        second.close()
        assert status == 403
        first.close()


def test_terminal_streams_kitty_private_herdr_session(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    loose = herdr.session_dir(SESSION)
    loose.mkdir(parents=True, mode=0o755)
    loose.chmod(0o755)
    with site(settings) as running:
        status, headers, connection, rest = running.connect(running.token(), query="&cols=100&rows=30")
        assert status == 101
        assert headers["upgrade"].lower() == "websocket"
        # WebKit (the desktop webview's engine) ignores a 101 sent as HTTP/1.0.
        assert headers["status-line"].startswith("HTTP/1.1 101 ")
        reader = FrameReader(connection, rest)
        reader.until(b"FAKE-HERDR session=kitty-test")
        reader.until(b"SIZE 100x30")
        send_frame(connection, 2, b"hi")
        reader.until(b"ECHO:hi")
        send_frame(connection, 1, json.dumps({"type": "resize", "cols": 120, "rows": 40}).encode())
        reader.until(b"SIZE 120x40")

        client = next(item for item in herdr.invocations() if item["argv"] == ["--session", SESSION])
        environment = client["env"]
        for inherited in ("HERDR_ENV", "HERDR_SESSION", "HERDR_SOCKET_PATH", "TMUX"):
            assert inherited not in environment, inherited
        private = settings.root / ".private" / "herdr"
        assert environment["HERDR_CONFIG_PATH"] == str(private / "config.toml")
        assert environment["TERM"] == "xterm-256color"
        assert client["cwd"] == str(herdr.home)

        assert mode(private) == 0o700 and mode(private / "config.toml") == 0o600
        theme = (private / "config.toml").read_text()
        assert 'accent = "#deb2c3"' in theme and "version_check = false" in theme
        folder = herdr.session_dir(SESSION)
        assert mode(folder) == 0o700
        for name in ("herdr.sock", "herdr-client.sock"):
            assert stat.S_ISSOCK((folder / name).lstat().st_mode) and mode(folder / name) == 0o600
        send_frame(connection, 8, b"\x03\xe8")
        assert reader.closed()[:2] == b"\x03\xe8"
        connection.close()


def test_detach_keeps_the_session_and_quit_stops_it(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    running = Site(settings)
    try:
        for _attempt in range(2):
            status, _headers, connection, rest = running.connect(running.token())
            assert status == 101
            FrameReader(connection, rest).until(b"FAKE-HERDR")
            connection.close()
            # Closing the page detaches its client; the session keeps running.
            deadline = time.time() + 5
            while time.time() < deadline and not herdr.listening(SESSION):
                time.sleep(0.05)
            assert herdr.listening(SESSION)
        servers = [item for item in herdr.invocations() if item["argv"][2:] == ["server"]]
        assert len(servers) == 1, "the second page re-attached instead of starting another server"
        assert STOP not in [item["argv"] for item in herdr.invocations()]
    finally:
        running.close()
    assert [item["argv"] for item in herdr.invocations()].count(STOP) == 1
    assert not herdr.listening(SESSION)


def test_widened_socket_permissions_refuse_the_terminal(tmp_path, herdr):
    herdr.mode_file.write_text("666")
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        status, _headers, connection, rest = running.connect(running.token())
        connection.close()
        assert status == 503 and b"permission" in rest
        assert STOP in [item["argv"] for item in herdr.invocations()]
        assert not herdr.listening(SESSION)


@pytest.mark.parametrize("name", ["default", "../escape", "has space", "x" * 65])
def test_default_or_invalid_session_name_never_launches_herdr(tmp_path, herdr, monkeypatch, name):
    monkeypatch.setenv("KITTY_HERDR_SESSION", name)
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        status, _headers, connection, rest = running.connect(running.token())
        connection.close()
        assert status == 503 and b"session name" in rest
    assert herdr.invocations() == []


def test_missing_bundled_runtime_is_reported(tmp_path, herdr, monkeypatch):
    monkeypatch.delenv("KITTY_HERDR")
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        status, _headers, connection, rest = running.connect(running.token())
        connection.close()
        assert status == 503 and b"herdr runtime" in rest
    assert herdr.invocations() == []


def test_unaddressable_socket_path_is_reported_before_launch(tmp_path, herdr, monkeypatch):
    # Linux limits a socket path to 107 bytes; herdr would otherwise exit silently.
    deep = herdr.home / ("d" * 90)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(deep))
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        status, _headers, connection, rest = running.connect(running.token())
        connection.close()
        assert status == 503 and b"socket path" in rest
    assert herdr.invocations() == []


def test_handshake_must_be_a_websocket_upgrade(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        assert running.request("/api/terminal/socket")[0] == 426
        status, _headers, connection, _rest = running.connect(running.token(), headers={"Sec-WebSocket-Version": "8"})
        connection.close()
        assert status == 400
        status, _headers, connection, _rest = running.connect(running.token())
        connection.close()
        assert status == 101
    key_check = base64.b64encode(hashlib.sha1(b"x" + web_terminal.WEBSOCKET_GUID).digest())
    assert web_terminal.accept_key("x") == key_check.decode()


def test_unmasked_client_frames_close_the_terminal(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        status, _headers, connection, rest = running.connect(running.token())
        assert status == 101
        reader = FrameReader(connection, rest)
        reader.until(b"FAKE-HERDR")
        send_frame(connection, 2, b"unmasked", mask=False)
        assert reader.closed()[:2] == b"\x03\xea"  # 1002 protocol error
        connection.close()


def test_server_close_ends_open_terminal_streams(tmp_path, herdr):
    class DrainingServer(LocalHTTPServer):
        # The desktop sidecar joins request threads on close.
        daemon_threads = False
        block_on_close = True

    settings = make_root(tmp_path, ("terminal",))
    running = Site(settings, DrainingServer)
    status, _headers, connection, rest = running.connect(running.token())
    assert status == 101
    reader = FrameReader(connection, rest)
    reader.until(b"FAKE-HERDR")
    closer = threading.Thread(target=running.close)
    closer.start()
    closer.join(timeout=15)
    assert not closer.is_alive(), "an open terminal blocked server shutdown"
    assert reader.closed()[:2] == b"\x03\xe9"  # 1001 going away
    connection.close()
    assert STOP in [item["argv"] for item in herdr.invocations()]
