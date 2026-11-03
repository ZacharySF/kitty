"""The live build over HTTP and the live channel: every window sees every valid save at once."""
from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

import terminal_support
from kitty.settings import Settings
from kitty.web_server import WebApplication
from terminal_support import FrameReader, send_frame, site, websocket

SOCKET = "/api/live/socket"


class Window:
    """One window's live socket."""

    def __init__(self, running):
        status, body = running.request("/api/live/session", {})
        assert status == 200 and body["socket"] == SOCKET, body
        status, _headers, self.connection, rest = websocket(running.port, f"{SOCKET}?token={body['token']}", {}, secret=running.secret)
        assert status == 101
        self.reader = FrameReader(self.connection, rest)
        self.hello = self.receive()
        assert self.hello["type"] == "hello"

    def send(self, message: dict) -> None:
        send_frame(self.connection, 1, json.dumps(message).encode())

    def receive(self, timeout: float = 5) -> dict:
        while True:
            opcode, payload = self.reader.frame(timeout)
            if opcode == 1:
                return json.loads(payload)

    def build(self, timeout: float = 5) -> dict:
        deadline = time.time() + timeout
        while True:
            message = self.receive(max(0.05, deadline - time.time()))
            if message["type"] == "build":
                return message

    def silent(self, timeout: float = .8) -> bool:
        try:
            self.receive(timeout)
        except (TimeoutError, socket.timeout):
            return True
        return False

    def close(self) -> None:
        self.connection.close()


def workspace(tmp_path: Path, modules: list[str], **extra) -> Settings:
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": modules, **extra}, indent=2))
    return Settings.load(root=root, db_path=root / "data" / "web.db")


@pytest.fixture
def running(tmp_path):
    with site(workspace(tmp_path, ["workspace"])) as server:
        yield server


def edit_file(running, change) -> None:
    """What an external editor does: rewrite config/build.json in place."""
    path = running.app.settings.root / "config" / "build.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2))


def test_get_build_answers_the_active_build_its_text_and_problems(running):
    status, body = running.request("/api/build")
    assert status == 200
    assert body["build"]["modules"] == ["workspace"] and body["invalid"] is False and body["problems"] == []
    assert body["revision"] == body["active_revision"] and json.loads(body["text"])["modules"] == ["workspace"]
    assert body["history"] == [] and body["safe"] is False and body["local_preferences_moved"] is False


def test_a_save_in_the_build_editor_reaches_every_window(running):
    first, second = Window(running), Window(running)
    try:
        state = running.request("/api/build")[1]
        assert first.hello["build"] == state["active_revision"]
        data = json.loads(state["text"])
        data["theme"] = {"tokens": {"ink": "#ff00ff"}, "css": ".tabs { gap: 3px }"}
        status, body = running.request("/api/build/save", {"text": json.dumps(data), "base": state["revision"]})
        assert status == 200, body
        for window in (first, second):
            pushed = window.build()
            assert pushed["build"]["theme"]["tokens"] == {"ink": "#ff00ff"}
            assert pushed["build"]["theme"]["css"] == ".tabs { gap: 3px }" and not pushed["invalid"]
        stale = running.request("/api/build/save", {"text": json.dumps(data), "base": state["revision"]})
        assert stale[0] == 409
    finally:
        first.close()
        second.close()


def test_an_external_editor_save_is_applied_live_and_an_invalid_one_keeps_the_last_good_build(running):
    first, second = Window(running), Window(running)
    try:
        edit_file(running, lambda data: data.update(theme={"tokens": {"void": "#101010"}}, settings={"tags": {"mode": "always"}}))
        for window in (first, second):
            assert window.build()["build"]["theme"]["tokens"] == {"void": "#101010"}
        assert running.request("/api/tags")[1]["mode"] == "always", "the projection follows the file"
        good = running.request("/api/build")[1]["active_revision"]
        path = running.app.settings.root / "config" / "build.json"
        path.write_text(path.read_text().replace('"#101010"', '"#101010",\n      "pad": "url(https://evil.example/x)"'))
        for window in (first, second):
            pushed = window.build()
            assert pushed["invalid"] and pushed["active_revision"] == good
            assert pushed["build"]["theme"]["tokens"] == {"void": "#101010"}
            (problem,) = pushed["problems"]
            assert problem["path"] == "theme.tokens.pad" and problem["line"] > 1
        refused = running.request("/api/build/preferences", {"ui": {"notes": {"pen_only": True}}})
        assert refused[0] == 409 and refused[1]["problems"][0]["path"] == "theme.tokens.pad"
        edit_file(running, lambda data: data["theme"]["tokens"].pop("pad"))
        assert not first.build()["invalid"]
    finally:
        first.close()
        second.close()


def test_settings_screens_and_the_editor_change_the_same_file(running):
    window = Window(running)
    try:
        assert running.request("/api/build/preferences", {"ui": {"graph": {"look": "3d"}}, "theme": {"base": "nord"},
                                                          "name": "Desk"})[0] == 200
        pushed = window.build()
        assert pushed["build"]["ui"]["graph"]["look"] == "3d" and pushed["build"]["theme"]["base"] == "nord"
        text = json.loads(pushed["text"])
        assert text["ui"]["graph"] == {"look": "3d"} and text["name"] == "Desk"
        assert running.request("/api/build/preferences", {"theme": {"css": "x"}})[0] == 400
        assert running.request("/api/build/preferences", {"ui": {"graph": {"look": "4d"}}})[0] == 400
        assert running.request("/api/build/preferences", {"ui": {"brain": {"speed": 9}}})[0] == 400
        assert running.request("/api/build/preferences", {"keys": {}})[0] == 400
        revision = running.request("/api/tags")[1]["revision"]
        assert running.request("/api/tags/settings", {"mode": "always", "revision": revision})[0] == 200
        assert json.loads(window.build()["text"])["settings"]["tags"]["mode"] == "always"
    finally:
        window.close()


def test_check_reports_problems_with_lines_without_writing(running):
    before = running.request("/api/build")[1]["revision"]
    status, body = running.request("/api/build/check", {"text": '{\n "schema": 1,\n "theme": {"css": "@import \\"x\\";"}\n}'})
    assert status == 200 and body["ok"] is False and body["problems"][0]["line"] == 3
    status, body = running.request("/api/build/save", {"text": '{"schema": 1,', "base": before})
    assert status == 400 and body["problems"][0]["line"] == 1
    assert running.request("/api/build")[1]["revision"] == before


def test_install_with_a_diff_then_roll_back(running):
    window = Window(running)
    try:
        incoming = json.dumps({"schema": 1, "name": "Shared", "modules": ["workspace", "terminal"],
                               "theme": {"base": "dracula", "tokens": {"ink": "#eeeeee"}},
                               "settings": {"notes": {"allow_ai": True}}})
        status, preview = running.request("/api/build/preview", {"text": incoming})
        assert status == 200 and preview["restart"] and preview["about"]["name"] == "Shared"
        flags = {change["path"]: (change["restart"], change["sensitive"]) for change in preview["changes"]}
        assert flags["modules"] == (True, False) and flags["settings.notes.allow_ai"] == (False, True)
        status, done = running.request("/api/build/install", {"text": incoming, "base": preview["base"],
                                                              "accept_sensitive": False, "source": "file"})
        assert status == 200 and done["restart"] and [held["path"] for held in done["held"]] == ["settings.notes.allow_ai"]
        pushed = window.build()
        assert pushed["build"]["theme"]["base"] == "dracula" and pushed["restart_required"]
        assert pushed["build"]["settings"]["notes"]["allow_ai"] is False
        again = running.request("/api/build/install", {"text": incoming, "base": preview["base"], "accept_sensitive": False})
        assert again[0] == 409, "a stale preview cannot install"
        history = running.request("/api/build")[1]["history"]
        assert [entry["reason"] for entry in history] == ["install"]
        assert running.request("/api/build/rollback", {})[0] == 200
        restored = window.build()
        assert restored["build"]["theme"]["base"] == "kitty" and not restored["restart_required"]
    finally:
        window.close()


def test_local_preferences_move_once_and_never_overwrite_the_build(running):
    running.request("/api/build/preferences", {"ui": {"study": {"guide_view": "rail"}}})
    first = running.request("/api/build/local", {"ui": {"graph": {"look": "3d"},
                                                        "appearance": {"density": "comfortable"}, "home": {"look": "2d"},
                                                        "brain": {"layout": "wheel", "colors": {"section": {"notes": "#aabbcc"}}},
                                                        "notifications": {"quiet": {"enabled": True, "from": 1380, "to": 420}},
                                                        "flashcards": {"look": "graph"},
                                                        "study": {"guide_view": "columns", "plans_view": "days"}},
                                                 "theme": {"base": "liquid-glass"}})
    assert first == (200, {"moved": True})
    build = running.request("/api/build")[1]
    assert build["local_preferences_moved"] is True
    ui = build["build"]["ui"]
    assert ui["study"] == {"guide_view": "rail", "plans_view": "days"}, "the build's own guide view wins"
    assert ui["appearance"]["density"] == "comfortable" and ui["home"] == {"look": "2d"}
    assert ui["brain"]["layout"] == "wheel" and ui["brain"]["colors"]["section"] == {"notes": "#aabbcc"}
    assert ui["notifications"]["quiet"] == {"enabled": True, "from": 1380, "to": 420, "let_calendar": True, "digest": True}
    assert ui["flashcards"] == {"look": "graph"}
    assert build["build"]["ui"]["graph"]["look"] == "3d" and build["build"]["theme"]["base"] == "liquid-glass"
    assert running.request("/api/build/local", {"theme": {"base": "dracula"}})[1] == {"moved": False}
    assert running.request("/api/build")[1]["build"]["theme"]["base"] == "liquid-glass"


def test_export_downloads_the_complete_active_build(running):
    running.request("/api/build/preferences", {"name": "Exported"})
    status, data = running.request("/api/build/export")
    assert status == 200 and data["name"] == "Exported" and data["schema"] == 1 and data["kitty_version"]
    assert set(data) == {"schema", "kitty_version", "name", "author", "description", "modules", "settings", "theme", "ui"}


def test_a_build_link_must_be_https(running):
    status, body = running.request("/api/build/fetch", {"url": "http://example.com/build.json"})
    assert status == 200
    task = body["task"]
    for _ in range(100):
        task = running.request(f"/api/tasks/{task['id']}")[1]
        if task["status"] != "running":
            break
        time.sleep(.05)
    assert task["status"] == "failed" and "https" in task["error"]


def test_block_transactions_need_the_workspace_module_but_the_build_is_live_without_it(tmp_path):
    with site(workspace(tmp_path, [])) as running:
        window = Window(running)
        try:
            window.send({"type": "txn", "txn_id": "t1", "ops": []})
            reply = window.receive()
            assert reply["type"] == "reject" and reply["status"] == 403
            edit_file(running, lambda data: data.update(theme={"base": "nord"}))
            assert window.build()["build"]["theme"]["base"] == "nord"
        finally:
            window.close()


def test_safe_mode_is_announced_to_every_window(tmp_path, monkeypatch):
    class Safe(WebApplication):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, safe=True)

    monkeypatch.setattr(terminal_support, "WebApplication", Safe)
    with site(workspace(tmp_path, ["workspace"])) as running:
        window = Window(running)
        try:
            assert running.request("/api/build")[1]["safe"] is True
            running.request("/api/build/preferences", {"name": "x"})
            assert window.build()["safe"] is True
        finally:
            window.close()


def test_the_tern_plugin_setting_gates_its_pairing(running):
    path = "/api/ingress/pair"
    body = {"name": "kitty Tern plugin", "kind": "tern-plugin", "scopes": ["capture"]}
    assert running.request(path, body, headers={"Origin": None, "X-Kitty-Request": None})[0] == 202
    edit_file(running, lambda data: data.update(settings={"tern": {"plugin": False}}))
    running.app.builds.refresh()  # the watcher would within a quarter second
    status, answer = running.request(path, body, headers={"Origin": None, "X-Kitty-Request": None})
    assert status == 403 and "Tern plugin is turned off" in answer["error"]


def test_the_page_opens_with_safe_in_its_address_and_nothing_else(running):
    assert running.request("/?safe")[0] == 200
    assert running.request("/?safe=1")[0] == 200
    assert running.request("/?theme=x")[0] == 400
