"""Stencil Tern in the Terminal module, against a synthetic ``tern``; never the real one.

Every test runs with PATH, XDG_RUNTIME_DIR and the Tern daemon socket in throwaway
directories, and with the parent environment pretending rika! itself runs inside
the user's own Tern pane (whose daemon must never be touched), herdr and a
PyInstaller bundle.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from tern_support import FakeTern, alive, listening
from terminal_support import make_root, site

from kitty.core.tern import MAX_CAPTURE_BYTES, TRIMMED, parse_event, plain_text

STATUS_KEYS = {"install_url", "installed", "path", "source", "override", "version", "problem",
               "workspace", "socket", "running", "windows", "sessions", "activity", "quick_notes"}
PANE_ACTIONS = (("/api/terminal/tern/new", {}), ("/api/terminal/tern/run", {"block": 3, "text": "ls"}),
                ("/api/terminal/tern/focus", {"block": 3}), ("/api/terminal/tern/capture", {"block": 3}))
OUTER_TERN = {"TERN_PANE": "5", "TERN_PANE_SOCKET": "", "TERN_WINDOW_KEY": "", "TERN_WINDOW_SOCKET": "",
              "TERN_EXE": "/somewhere/else/tern", "TERN_IDENTITY": "{}"}
#: What the AppImage runtime adds to rika!'s environment, and a secret from the user's shell.
#: (Not PYTHONHOME: the fake tern is Python, the real one is not.)
APPIMAGE = {"APPDIR": "/tmp/.mount_kitty", "APPIMAGE": "/apps/kitty.AppImage",
            "GIO_MODULE_DIR": "/tmp/.mount_kitty/usr/lib/gio/modules", "GTK_PATH": "/tmp/.mount_kitty/usr/lib/gtk-3.0",
            "KITTY_TEST_SECRET": "s3cret-value"}


@pytest.fixture
def tern(tmp_path, monkeypatch):
    fake = FakeTern(tmp_path)
    owner = fake.runtime / "owner"
    owner.mkdir(mode=0o700)
    fake.owner_socket = owner / "daemon.sock"
    monkeypatch.setenv("PATH", f"{fake.bin}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(fake.runtime))
    # Not inside an FHS sandbox unless a test says so, whatever runs the tests.
    monkeypatch.setattr("kitty.core.tern.FHS_HOST_ETC", tmp_path / "no-host-etc")
    # rika! may run inside the user's own Tern pane: its daemon is someone else's.
    monkeypatch.setenv("TERN_DAEMON_SOCKET", str(fake.owner_socket))
    for key, value in OUTER_TERN.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TERN_CONFIG_DIR", str(tmp_path / "user-tern-config"))
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIbundled")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/nix/store/incompatible/lib")
    try:
        yield fake
    finally:
        fake.cleanup()
        shutil.rmtree(fake.runtime, ignore_errors=True)


@pytest.fixture
def sandbox(tmp_path, tern, monkeypatch):
    """rika! inside appimage-run's FHS sandbox, as NixOS runs its AppImage."""
    host_etc = tmp_path / "host-etc"
    host_etc.mkdir()
    monkeypatch.setattr("kitty.core.tern.FHS_HOST_ETC", host_etc)
    for key, value in APPIMAGE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-kitty-test")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    return tern


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def wait_for(predicate, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while not (value := predicate()):
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.05)
    return value


def status(running) -> dict:
    code, body = running.request("/api/terminal/tern")
    assert code == 200, body
    assert set(body) == STATUS_KEYS
    return body


def test_module_off_has_no_tern_route_or_process(tmp_path, tern):
    settings = make_root(tmp_path, ())
    with site(settings) as running:
        for path, body in (("/api/terminal/tern", None), ("/api/terminal/tern/open", {}),
                           ("/api/terminal/tern/start", {}), ("/api/terminal/tern/settings", {"path": ""}),
                           *PANE_ACTIONS):
            code, answer = running.request(path, body)
            assert code == 403 and "module is disabled" in answer["error"]
    assert tern.calls() == []
    assert not (tern.runtime / "kitty").exists()


def test_absent_tern_reports_the_install_pointer_and_starts_nothing(tmp_path, tern, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    settings = make_root(tmp_path, ("workspace", "terminal"))
    with site(settings) as running:
        body = status(running)
        assert body["installed"] is False and body["path"] is None and body["source"] is None
        assert body["version"] is None and body["problem"] is None
        assert body["install_url"] == "https://stencil.so/tern"
        assert body["running"] is False and body["windows"] == 0 and body["sessions"] == []
        for path, payload in (("/api/terminal/tern/open", {}), ("/api/terminal/tern/start", {}), *PANE_ACTIONS):
            code, answer = running.request(path, payload)
            assert code == 409 and "https://stencil.so/tern" in answer["error"], path
    assert tern.calls() == []
    assert not (tern.runtime / "kitty").exists()


def test_tern_on_path_is_detected_without_starting_anything(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        first, second = status(running), status(running)
    assert first == second
    assert first["installed"] is True and first["path"] == str(tern.binary) and first["source"] == "path"
    assert first["version"] == "0.9.8" and first["override"] is None and first["problem"] is None
    assert first["workspace"] == str(settings.root) and first["running"] is False
    assert Path(first["socket"]).is_relative_to(tern.runtime / "kitty") and first["socket"].endswith("/daemon.sock")
    # Detection asks for the version once; it never starts a daemon or a window.
    assert tern.calls() == [["--version"]]
    assert not Path(first["socket"]).parent.exists()


def test_settings_override_wins_is_saved_owner_only_and_validated(tmp_path, tern):
    custom = tmp_path / "beta" / "tern-beta"
    custom.parent.mkdir()
    shutil.copy2(tern.binary, custom)
    settings = make_root(tmp_path, ("terminal",))
    saved = settings.root / ".private" / "tern" / "settings.json"
    with site(settings) as running:
        assert running.request("/api/terminal/tern/settings", {"path": "relative/tern"})[0] == 400
        assert running.request("/api/terminal/tern/settings", {"path": str(custom), "extra": 1})[0] == 400
        code, body = running.request("/api/terminal/tern/settings", {"path": str(custom)})
        assert code == 200 and set(body) == STATUS_KEYS
        assert body["installed"] is True and body["source"] == "settings" and body["path"] == str(custom)
        assert body["override"] == str(custom)
    assert json.loads(saved.read_text()) == {"path": str(custom)}
    assert mode(saved) == 0o600 and mode(saved.parent) == 0o700
    with site(settings) as running:
        assert status(running)["path"] == str(custom)
        # A broken override is reported, never silently replaced by the one on PATH.
        custom.chmod(0o644)
        body = status(running)
        assert body["installed"] is False and body["source"] == "settings" and body["path"] is None
        assert str(custom) in body["problem"]
        assert running.request("/api/terminal/tern/open", {})[0] == 409
        custom.chmod(0o755)
        tern.mode("impostor")
        body = status(running)
        assert body["installed"] is False and "not Stencil Tern" in body["problem"]
        tern.mode("")
        code, body = running.request("/api/terminal/tern/settings", {"path": ""})
        assert code == 200 and body["source"] == "path" and body["override"] is None
    assert json.loads(saved.read_text()) == {"path": None}
    assert not any(call[:1] in (["daemon"], ["--dir"]) for call in tern.calls())


def test_open_in_tern_runs_a_window_on_kittys_own_daemon_and_quit_leaves_nothing(tmp_path, tern):
    owner = tern.start_daemon(tern.owner_socket)
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/open", {})
        assert code == 200 and set(body) == STATUS_KEYS and body["running"] is True
        socket_path = Path(body["socket"])
        assert socket_path != tern.owner_socket and listening(socket_path)
        assert mode(socket_path.parent) == 0o700 and mode(socket_path) == 0o600
        body = wait_for(lambda: (state := status(running))["windows"] == 1 and state)
        [session] = body["sessions"]
        assert session["name"] == "kitty"
        [tab] = session["tabs"]
        [block] = tab["blocks"]
        assert block["cwd"] == str(settings.root) and block["working"] is True
        assert set(block) == {"id", "title", "cwd", "program", "command", "cols", "rows", "focused", "live", "exited",
                              "working"}
        # A second Open hands over to the open window, which Tern raises: one window, daemon and session.
        assert running.request("/api/terminal/tern/open", {})[0] == 200
        assert wait_for(lambda: len(windows := [item for item in tern.invocations() if item["argv"][:1] == ["--dir"]]) == 2
                        and not alive(windows[1]["pid"]))
        assert status(running)["windows"] == 1
        assert len(status(running)["sessions"]) == 1
    invocations = tern.invocations()
    calls = [item["argv"] for item in invocations]
    root = str(settings.root)
    assert calls.count(["daemon", "--socket", str(socket_path)]) == 1
    assert calls.count(["new", "session", "kitty", "--cwd", root, "--json"]) == 1
    assert calls.count(["--dir", root]) == 2
    # Outside a sandbox Tern runs as rika!'s child, never through systemd.
    assert not any(item["argv"][0] in ("systemd-run", "systemctl") for item in invocations)
    for item in invocations:
        if item["argv"] == ["daemon", "--socket", str(tern.owner_socket)]:
            continue  # the user's own daemon, started by this test
        env = item["env"]
        assert env["TERN_DAEMON_SOCKET"] == str(socket_path), item["argv"]
        assert not set(OUTER_TERN) & set(env), item["argv"]
        # (The fake's interpreter may get an empty LD_LIBRARY_PATH from nix-ld; never the bundle's.)
        assert not env.get("LD_LIBRARY_PATH") and "LD_LIBRARY_PATH_ORIG" not in env and "HERDR_ENV" not in env
        assert env["TERN_CONFIG_DIR"] == str(tmp_path / "user-tern-config")
        if item["argv"][:1] in (["--dir"], ["daemon"]):
            assert item["cwd"] == root
    # Quit stopped every window and rika!'s daemon; nothing rika! started outlives it.
    assert not listening(socket_path)
    for item in invocations:
        if item["argv"] != ["daemon", "--socket", str(tern.owner_socket)]:
            assert not alive(item["pid"]), item["argv"]
    # The user's own daemon kept running, untouched.
    assert owner.poll() is None and listening(tern.owner_socket)
    assert json.loads(tern.run("inspect", "--json", socket_path=tern.owner_socket).stdout)["sessions"] == []


def test_in_an_fhs_sandbox_tern_runs_outside_it_as_user_services_and_quit_stops_them(tmp_path, sandbox):
    """appimage-run's /etc/profile hides the user's programs from Tern's login-shell panes."""
    owner = sandbox.start_daemon(sandbox.owner_socket)
    settings = make_root(tmp_path, ("terminal",))
    root = str(settings.root)
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/open", {})
        assert code == 200 and body["running"] is True
        socket_path = Path(body["socket"])
        assert mode(socket_path.parent) == 0o700 and mode(socket_path) == 0o600
        body = wait_for(lambda: (state := status(running))["windows"] == 1 and state)
        assert [session["name"] for session in body["sessions"]] == ["kitty"]
        assert len(list(sandbox.units.iterdir())) == 2
    invocations = sandbox.invocations()
    unit = f"kitty-{socket_path.parent.name}"
    launches = {}
    for item in invocations:
        if item["argv"][0] == "systemd-run":
            split = item["argv"].index("--")
            launches[tuple(item["argv"][split + 1:])] = item["argv"][1:split]
            assert item["cwd"] == root
    daemon = (str(sandbox.binary), "daemon", "--socket", str(socket_path))
    window = (str(sandbox.binary), "--dir", root)
    assert set(launches) == {daemon, window}
    for command, options in launches.items():
        assert options[:5] == ["--user", "--wait", "--quiet", "--collect", "--service-type=exec"]
        assert f"--working-directory={root}" in options
        [name] = [option.removeprefix("--unit=") for option in options if option.startswith("--unit=")]
        assert name == unit if command == daemon else name.startswith(f"{unit}-window-")
        # Names only: systemd-run reads the values from its own environment, off the command line.
        assert sorted(option for option in options if option.startswith("--setenv=")) == [
            "--setenv=TERN_CONFIG_DIR", "--setenv=TERN_DAEMON_SOCKET", "--setenv=WAYLAND_DISPLAY"]
    assert "s3cret-value" not in json.dumps([item["argv"] for item in invocations])
    # The daemon and the window run in the user's session environment, aimed at rika!'s daemon.
    outside = [item for item in invocations if (str(sandbox.binary), *item["argv"]) in (daemon, window)]
    assert len(outside) == 2
    for item in outside:
        env = item["env"]
        assert env["SYSTEMD_MANAGER_ENV"] == "1" and env["TERN_DAEMON_SOCKET"] == str(socket_path)
        assert env["TERN_CONFIG_DIR"] == str(tmp_path / "user-tern-config")
        assert env["WAYLAND_DISPLAY"] == "wayland-kitty-test"
        assert not (set(APPIMAGE) | set(OUTER_TERN) | {"LD_LIBRARY_PATH_ORIG", "HERDR_ENV"}) & set(env)
        assert item["cwd"] == root
    # Status and session commands are clients of the socket; they run as rika!'s children.
    assert [item["argv"] for item in invocations].count(["new", "session", "kitty", "--cwd", root, "--json"]) == 1
    # Quit stopped the window's and the daemon's services; nothing rika! started outlives it.
    assert any(item["argv"][:3] == ["systemctl", "--user", "stop"] for item in invocations)
    assert not listening(socket_path) and not list(sandbox.units.iterdir())
    for item in sandbox.invocations():
        if item["argv"][:1] != ["systemctl"] and item["argv"] != ["daemon", "--socket", str(sandbox.owner_socket)]:
            assert not alive(item["pid"]), item["argv"]
    assert owner.poll() is None and listening(sandbox.owner_socket)


def test_in_an_fhs_sandbox_a_daemon_that_never_listens_has_its_service_stopped(tmp_path, sandbox, monkeypatch):
    monkeypatch.setattr("kitty.core.tern.READY_TIMEOUT", 1.0)
    sandbox.mode("hang")
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/open", {})
        assert code == 503 and "did not start" in body["error"]
        [daemon] = [item for item in sandbox.invocations() if item["argv"][:1] == ["daemon"]]
        assert not alive(daemon["pid"]) and not list(sandbox.units.iterdir())
        assert not any(item["argv"][:1] == ["--dir"] for item in sandbox.invocations())


def test_in_an_fhs_sandbox_without_systemd_tern_still_runs_as_kittys_child(tmp_path, sandbox, monkeypatch):
    alone = tmp_path / "tern-alone"
    alone.mkdir()
    shutil.copy2(sandbox.binary, alone / "tern")
    monkeypatch.setenv("PATH", str(alone))
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/start", {})
        assert code == 200 and body["running"] is True
    [daemon] = [item for item in sandbox.invocations() if item["argv"][:1] == ["daemon"]]
    assert daemon["env"]["TERN_DAEMON_SOCKET"] == body["socket"] and "SYSTEMD_MANAGER_ENV" not in daemon["env"]
    assert not listening(Path(body["socket"])) and not alive(daemon["pid"])


def test_start_makes_one_kitty_session_without_a_window(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        for _ in range(2):
            code, body = running.request("/api/terminal/tern/start", {})
            assert code == 200 and body["running"] is True and body["windows"] == 0
            assert [session["name"] for session in body["sessions"]] == ["kitty"]
        assert running.request("/api/terminal/tern/start", {"cwd": "/"})[0] == 400
    calls = tern.calls()
    assert sum(call[:1] == ["daemon"] for call in calls) == 1
    assert sum(call[:2] == ["new", "session"] for call in calls) == 1
    assert not any(call[:1] == ["--dir"] for call in calls)
    assert not listening(Path(body["socket"]))


def test_a_daemon_left_on_kittys_socket_is_adopted_and_stopped_at_quit(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        socket_path = Path(status(running)["socket"])
        socket_path.parent.mkdir(mode=0o700, parents=True)
        # A previous rika! process's daemon, still running on rika!'s private socket.
        previous = tern.start_daemon(socket_path)
        code, body = running.request("/api/terminal/tern/start", {})
        assert code == 200 and body["running"] is True
        assert sum(call[:1] == ["daemon"] for call in tern.calls()) == 1  # only the test's
    assert previous.wait(timeout=10) == 0
    assert not listening(socket_path)


def test_quit_without_using_tern_leaves_kittys_daemon_alone(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        socket_path = Path(status(running)["socket"])
        socket_path.parent.mkdir(mode=0o700, parents=True)
        other = tern.start_daemon(socket_path)
        assert status(running)["running"] is True
    assert other.poll() is None and listening(socket_path)


def test_a_daemon_that_never_listens_is_reported_and_stopped(tmp_path, tern, monkeypatch):
    monkeypatch.setattr("kitty.core.tern.READY_TIMEOUT", 1.0)
    tern.mode("hang")
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/open", {})
        assert code == 503 and "did not start" in body["error"]
        [daemon] = [item for item in tern.invocations() if item["argv"][:1] == ["daemon"]]
        assert not alive(daemon["pid"])
        assert not any(item["argv"][:1] == ["--dir"] for item in tern.invocations())


def test_a_daemon_socket_others_can_reach_is_refused_and_stopped(tmp_path, tern):
    tern.mode("unsafe")
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/start", {})
        assert code == 503 and "unsafe permissions" in body["error"]
        socket_path = Path(status(running)["socket"])
        assert not listening(socket_path)
        assert all(not alive(item["pid"]) for item in tern.invocations())


def test_unreadable_sessions_are_an_error_not_an_empty_session(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        assert running.request("/api/terminal/tern/start", {})[0] == 200
        tern.mode("garbled")
        code, body = running.request("/api/terminal/tern")
        assert code == 503 and "did not report its sessions" in body["error"]
        tern.mode("")


def test_a_socket_path_too_long_for_unix_sockets_is_refused(tmp_path, tern, monkeypatch):
    deep = tern.runtime / ("d" * 100)
    deep.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(deep))
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/start", {})
        assert code == 503 and "longer than 107 bytes" in body["error"]
    assert not any(call[:1] == ["daemon"] for call in tern.calls())


# Panes of rika!'s session: new, run, focus, capture and their live state ----------

def kitty_blocks(body: dict) -> list[dict]:
    return [block for session in body["sessions"] if session["name"] == "kitty"
            for tab in session["tabs"] for block in tab["blocks"]]


def test_a_new_pane_starts_kittys_session_once_then_adds_tabs(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    root = str(settings.root)
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/new", {})
        assert code == 200 and set(body) == STATUS_KEYS and body["running"] is True
        # The session rika! just created is the new pane; no extra tab.
        [first] = kitty_blocks(body)
        code, body = running.request("/api/terminal/tern/new", {})
        assert code == 200
        first_again, second = kitty_blocks(body)
        assert first_again["id"] == first["id"] and second["id"] != first["id"]
        assert second["cwd"] == root and body["windows"] == 0
        assert running.request("/api/terminal/tern/new", {"cwd": "/"})[0] == 400
    calls = tern.calls()
    assert calls.count(["new", "session", "kitty", "--cwd", root, "--json"]) == 1
    assert calls.count(["new", "tab", "kitty", "--cwd", root, "--json"]) == 1
    assert not any(call[:1] == ["--dir"] for call in calls)


def test_run_and_capture_save_a_kitty_pane_as_a_quick_note_on_kittys_daemon_only(tmp_path, tern):
    owner = tern.start_daemon(tern.owner_socket)
    settings = make_root(tmp_path, ("workspace", "terminal"))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/new", {})
        assert code == 200 and body["quick_notes"] is True
        [block] = kitty_blocks(body)
        socket_path = Path(body["socket"])
        code, body = running.request("/api/terminal/tern/run", {"block": block["id"], "text": "echo kitty-42"})
        assert code == 200 and set(body) == STATUS_KEYS
        [pane] = kitty_blocks(body)
        assert pane["title"] == "echo kitty-42" and pane["exited"] is None
        code, saved = running.request("/api/terminal/tern/capture", {"block": block["id"]})
        assert code == 200 and saved == {"note_id": saved["note_id"], "block": block["id"], "truncated": False}
        code, note = running.request(f"/api/quick-notes/{saved['note_id']}")
    assert code == 200 and note["tint"] == "sky"
    header, blank, *text = note["body"].split("\n")
    assert header.startswith(f"Tern capture · kitty/{block['id']} · ") and blank == ""
    # Terminal controls stripped, trailing blank lines dropped.
    assert text == ["$ echo kitty-42", "kitty-42"]
    calls = tern.calls()
    assert ["run", f"kitty/{block['id']}", "--", "echo kitty-42"] in calls
    assert ["capture", f"kitty/{block['id']}", "--scrollback"] in calls
    for item in tern.invocations():
        if item["argv"] != ["daemon", "--socket", str(tern.owner_socket)]:
            assert item["env"]["TERN_DAEMON_SOCKET"] == str(socket_path), item["argv"]
    # The user's own daemon kept running with none of rika!'s panes.
    assert owner.poll() is None
    assert json.loads(tern.run("inspect", "--json", socket_path=tern.owner_socket).stdout)["sessions"] == []


@pytest.mark.parametrize("path, body", [
    ("/api/terminal/tern/run", {"block": 3}),
    ("/api/terminal/tern/run", {"block": 3, "text": "ls\nrm -rf /"}),
    ("/api/terminal/tern/run", {"block": 3, "text": "ls\r"}),
    ("/api/terminal/tern/run", {"block": 3, "text": "  "}),
    ("/api/terminal/tern/run", {"block": 3, "text": "clear\x1b[2J"}),
    ("/api/terminal/tern/run", {"block": 3, "text": "x" * 4097}),
    ("/api/terminal/tern/run", {"block": 3, "text": 5}),
    ("/api/terminal/tern/run", {"block": "3", "text": "ls"}),
    ("/api/terminal/tern/run", {"block": "Default/3", "text": "ls"}),
    ("/api/terminal/tern/focus", {"block": True}),
    ("/api/terminal/tern/focus", {"block": -3}),
    ("/api/terminal/tern/focus", {"block": 2 ** 64}),
    ("/api/terminal/tern/focus", {"block": 3, "extra": 1}),
    ("/api/terminal/tern/capture", {}),
    ("/api/terminal/tern/capture", []),
])
def test_invalid_pane_requests_never_reach_tern(tmp_path, tern, path, body):
    settings = make_root(tmp_path, ("workspace", "terminal"))
    with site(settings) as running:
        assert running.request("/api/terminal/tern/start", {})[0] == 200
        code, answer = running.request(path, body)
    assert code == 400, answer
    assert not any(call[:1] in (["run"], ["focus"], ["capture"]) for call in tern.calls())


def test_a_pane_outside_kittys_session_is_not_found(tmp_path, tern):
    settings = make_root(tmp_path, ("workspace", "terminal"))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/start", {})
        socket_path = Path(body["socket"])
        # Another session on rika!'s daemon (a script using the shown socket) is not rika!'s.
        other = json.loads(tern.run("new", "session", "other", "--cwd", "/", "--json", socket_path=socket_path).stdout)
        for block in (999, other["block"]):
            for path, payload in (("run", {"block": block, "text": "ls"}), ("focus", {"block": block}),
                                  ("capture", {"block": block})):
                code, answer = running.request(f"/api/terminal/tern/{path}", payload)
                assert code == 404 and "no pane" in answer["error"], (path, block)
    assert not any(call[:1] in (["run"], ["focus"], ["capture"]) for call in tern.calls())


def test_pane_actions_need_kittys_session_running(tmp_path, tern):
    settings = make_root(tmp_path, ("workspace", "terminal"))
    with site(settings) as running:
        for path, payload in PANE_ACTIONS[1:]:
            code, answer = running.request(path, payload)
            assert code == 409 and "not running" in answer["error"], path
    assert tern.calls() == [["--version"]]


def test_focus_raises_kittys_tern_window_on_the_pane_opening_one_when_none_is_open(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    root = str(settings.root)
    with site(settings) as running:
        running.request("/api/terminal/tern/new", {})
        code, body = running.request("/api/terminal/tern/new", {})
        first, second = kitty_blocks(body)
        assert second["focused"] is True and first["focused"] is False
        code, body = running.request("/api/terminal/tern/focus", {"block": first["id"]})
        assert code == 200
        assert [block["focused"] for block in kitty_blocks(body)] == [True, False]
        assert wait_for(lambda: status(running)["windows"] == 1)
        # With rika!'s window open, `tern --dir` hands over to it (Tern raises it): no second window.
        assert running.request("/api/terminal/tern/focus", {"block": second["id"]})[0] == 200
        assert [block["focused"] for block in kitty_blocks(status(running))] == [False, True]
        windows = wait_for(lambda: len(found := [item for item in tern.invocations() if item["argv"] == ["--dir", root]]) == 2
                           and not alive(found[1]["pid"]) and found)
        assert status(running)["windows"] == 1 and alive(windows[0]["pid"])
    calls = tern.calls()
    assert calls.index(["focus", f"kitty/{first['id']}"]) < calls.index(["--dir", root])
    assert ["focus", f"kitty/{second['id']}"] in calls
    assert all(not alive(item["pid"]) for item in tern.invocations())


def test_capture_needs_the_workspace_module_for_quick_notes(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request("/api/terminal/tern/new", {})
        assert body["quick_notes"] is False
        [block] = kitty_blocks(body)
        code, answer = running.request("/api/terminal/tern/capture", {"block": block["id"]})
    assert code == 409 and "Workspace" in answer["error"]
    assert not any(call[:1] == ["capture"] for call in tern.calls())


def test_a_long_capture_keeps_the_newest_output_within_a_quick_note(tmp_path, tern):
    settings = make_root(tmp_path, ("workspace", "terminal"))
    with site(settings) as running:
        [block] = kitty_blocks(running.request("/api/terminal/tern/new", {})[1])
        tern.mode("big")
        code, saved = running.request("/api/terminal/tern/capture", {"block": block["id"]})
        tern.mode("")
        assert code == 200 and saved["truncated"] is True
        body = running.request(f"/api/quick-notes/{saved['note_id']}")[1]["body"]
    text = body.split("\n", 2)[2]
    assert len(text.encode()) <= MAX_CAPTURE_BYTES
    head, *kept = text.split("\n")
    assert head == TRIMMED and kept[-1] == "line 003999 " + "é" * 40
    assert all(line.startswith("line ") for line in kept)


def test_exited_panes_stay_listed_with_their_status_and_events_and_quit_stops_following(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        [block] = kitty_blocks(running.request("/api/terminal/tern/new", {})[1])
        events = wait_for(lambda: [item["argv"] for item in tern.invocations() if item["argv"][:1] == ["events"]])
        assert events == [["events", "--filter", "pane_spawned,pane_exited"]]
        assert running.request("/api/terminal/tern/run", {"block": block["id"], "text": "exit 3"})[0] == 200
        [pane] = kitty_blocks(status(running))
        assert pane["exited"] == 3
        activity = wait_for(lambda: (body := status(running))["activity"] and body["activity"])
        assert [(item["event"], item["pane"], item["status"]) for item in activity][:1] == [
            ("pane_exited", block["id"], 3)]
        assert all(isinstance(item["at"], str) and item["at"] for item in activity)
        [follower] = [item["pid"] for item in tern.invocations() if item["argv"][:1] == ["events"]]
        assert alive(follower)
    assert wait_for(lambda: not alive(follower), timeout=5)


def test_plain_text_strips_terminal_controls():
    raw = ("\x1b[31mred\x1b[0m line  \r\n\x1b]0;title\x07next\x00\x07 bell \x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\\n"
           "\x9b2Jc1 \x1b(Bcharset\ttab\x7f\n\n\n")
    assert plain_text(raw) == "red line\nnext bell link\nc1 charset\ttab"


def test_parse_event_accepts_only_pane_events():
    assert parse_event(b'{"event":"pane_spawned","pane":5,"by":3}')["pane"] == 5
    exited = parse_event(b'{"event":"pane_exited","pane":18446744073709551615,"status":3}\n')
    assert exited["event"] == "pane_exited" and exited["pane"] == 18446744073709551615 and exited["status"] == 3
    assert parse_event(b'{"event":"pane_exited","pane":5}')["status"] is None
    for line in (b"not json", b"[1]", b'{"event":"layout_changed","by":3}', b'{"event":"pane_spawned"}',
                 b'{"event":"pane_spawned","pane":"5"}', b'{"event":"pane_spawned","pane":true}',
                 b'{"event":"pane_spawned","pane":-1}', b'{"event":"pane_exited","pane":5,"status":"0"}',
                 b"\xff\xfe", b""):
        assert parse_event(line) is None, line
