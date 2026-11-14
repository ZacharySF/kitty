"""The live build over HTTP and the live channel: every window sees every valid save at once."""
from __future__ import annotations

import copy
import http.client
import json
import re
import socket
import time
from contextlib import closing
from pathlib import Path

import pytest

from kitty import web_auth
from kitty.core.builds import document, project, share, store
from kitty.core.workspace import open_workspace
from kitty.db import connect
from kitty.settings import Settings
from terminal_support import FrameReader, front_site, send_frame, site, websocket

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
    with front_site(workspace(tmp_path, ["workspace"])) as server:
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
    assert ui["appearance"]["density"] == "comfortable" and ui["home"]["look"] == "2d"
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
    with front_site(workspace(tmp_path, [])) as running:
        window = Window(running)
        try:
            window.send({"type": "txn", "txn_id": "t1", "ops": []})
            reply = window.receive()
            assert reply["type"] == "reject" and reply["status"] == 403
            edit_file(running, lambda data: data.update(theme={"base": "nord"}))
            assert window.build()["build"]["theme"]["base"] == "nord"
        finally:
            window.close()


def test_safe_mode_is_announced_to_every_window(tmp_path):
    with front_site(workspace(tmp_path, ["workspace"]), args=("--safe",)) as running:
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
    deadline = time.time() + 5  # the build watcher projects the edit within a quarter second
    while True:
        status, answer = running.request(path, body, headers={"Origin": None, "X-Kitty-Request": None})
        if status != 202 or time.time() > deadline:
            break
        time.sleep(.1)
    assert status == 403 and "Tern plugin is turned off" in answer["error"]


def test_the_page_opens_with_safe_in_its_address_and_nothing_else(running):
    assert running.request("/?safe")[0] == 200
    assert running.request("/?safe=1")[0] == 200
    assert running.request("/?theme=x")[0] == 400


def test_export_reached_through_a_link_ticket_is_an_attachment_once(running):
    url = running.request(web_auth.LINK_PATH, {"path": "/api/build/export"})[1]["url"]
    client = http.client.HTTPConnection("127.0.0.1", running.port, timeout=10)
    try:
        client.request("GET", url, headers={"Origin": running.server.origin})
        response = client.getresponse()
        exported = response.read()
    finally:
        client.close()
    assert response.status == 200 and response.getheader("Content-Disposition") == 'attachment; filename="kitty-build.json"'
    active = document.require((running.app.settings.root / "config" / "build.json").read_text())
    assert exported.decode() == share.export_text(active)
    assert running.request(url, headers={web_auth.HEADER: None})[0] == 401, "single use"
    assert running.request("/api/build/export?x=1")[0] == 400


def test_the_worker_s_tasks_and_internal_routes_stay_where_they_are(running):
    assert running.request(f"/api/tasks/{'a' * 32}") == (404, {"error": "Background operation not found"})
    assert running.request("/api/internal/build/applied", {"build": {}}) == (404, {"error": "API route not found"})
    assert running.request("/api/build/nothing") == (404, {"error": "API route not found"})
    assert running.request("/api/build/nothing", {}) == (404, {"error": "API route not found"})


# Parity: the front's answers and rows against Python's kitty.core.builds -----------------------

#: The projected rows (build_state's clock, and next_run, compared by shape).
TABLES = ("ai_settings", "note_preferences", "study_settings", "study_timer", "opportunity_settings", "tag_settings",
          "build_state")
CLOCK = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00")


def rows(settings: Settings) -> dict:
    with closing(connect(settings.db_path)) as conn:
        found = {table: dict(conn.execute(f"SELECT * FROM {table} WHERE id=1").fetchone()) for table in TABLES}
    for table, column in (("build_state", "applied_at"), ("opportunity_settings", "next_run")):
        value = found[table][column]
        found[table][column] = None if value is None else bool(CLOCK.fullmatch(value))
    return found


def merge(target: dict, patch: dict, *, missing_only: bool = False) -> None:
    """What `POST /api/build/preferences` (and `/local`) merge into the document."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value, missing_only=missing_only)
        elif not missing_only or key not in target:
            target[key] = copy.deepcopy(value)


def history(root: Path) -> list[dict]:
    return [{key: entry[key] for key in ("reason", "name", "valid", "revision")} for entry in store.list_history(root)]


def test_the_front_projects_builds_with_python_s_writes(tmp_path):
    """Every write path, through the front and through Python's kitty.core.builds on a twin
    workspace, leaves the same file, history and settings rows."""
    initial = {"settings": {"study": {"focus_seconds": 1500}}}
    front_settings = workspace(tmp_path / "front", ["workspace"], **initial)
    twin = workspace(tmp_path / "python", ["workspace"], **initial)
    with open_workspace(twin):
        pass
    file = lambda settings: (settings.root / "config" / "build.json").read_text()  # noqa: E731

    with front_site(front_settings) as running, closing(connect(twin.db_path)) as conn:
        def same(step: str) -> None:
            assert file(front_settings) == file(twin), step
            assert rows(front_settings) == rows(twin), step
            assert history(front_settings.root) == history(twin.root), step

        same("start")
        # replace: the build editor's save (budget rounding, the idle timer, monitoring's next run)
        saved = json.dumps({"schema": 1, "modules": ["workspace"], "name": "Desk \u00e9\U0001F600",
                            "settings": {"ai": {"model": "synthetic/model", "daily_budget_usd": 2.675, "routing": "quality"},
                                         "study": {"focus_seconds": 1800, "sound": False},
                                         "opportunities": {"monitoring": True, "interval_hours": 12},
                                         "tags": {"mode": "always"}, "history": {"retention_days": 7},
                                         "tern": {"plugin": False}, "notes": {"allow_ai": True}}}, indent=1)
        base = running.request("/api/build")[1]["revision"]
        assert running.request("/api/build/save", {"text": saved, "base": base}) == (200, {"ok": True})
        project.replace(conn, twin.root, saved)
        same("save")
        # editing: a Settings screen's preferences
        patch = {"ui": {"graph": {"look": "3d"}}, "theme": {"base": "nord"}, "author": "Someone"}
        assert running.request("/api/build/preferences", patch) == (200, {"ok": True})
        with project.editing(conn, twin.root) as build:
            merge(build, patch)
        same("preferences")
        # sync: an external editor
        edited = json.loads(file(twin))
        edited["settings"]["study"]["short_break_seconds"] = 600
        edited["settings"]["opportunities"]["monitoring"] = False
        for settings in (front_settings, twin):
            (settings.root / "config" / "build.json").write_text(json.dumps(edited, indent=2))
        running.request("/api/build")
        project.sync(conn, twin.root)
        same("external edit")
        # replace with history: an install holding back the sensitive changes
        incoming = json.dumps({"schema": 1, "name": "Shared", "modules": ["workspace", "terminal"],
                               "settings": {"ai": {"model": "expensive/model", "daily_budget_usd": 50},
                                            "opportunities": {"monitoring": True}, "tags": {"mode": "suggest"}}})
        preview = running.request("/api/build/preview", {"text": incoming})[1]
        status, done = running.request("/api/build/install", {"text": incoming, "base": preview["base"],
                                                              "accept_sensitive": False, "source": "file"})
        plan = share.resolve(project.sync(conn, twin.root).build, incoming, accept_sensitive=False)
        project.replace(conn, twin.root, document.dump(plan["build"]), history=("install", "file"))
        assert status == 200 and done == {"ok": True, "restart": plan["restart"], "held": plan["held"], "changes": plan["changes"]}
        same("install")
        # rollback
        status, rolled = running.request("/api/build/rollback", {})
        entry = store.list_history(twin.root)[0]["id"]
        project.replace(conn, twin.root, store.history_text(twin.root, entry), history=("rollback", f"replaced by {entry}"))
        assert status == 200 and rolled["ok"] and rolled["restored"] == store.list_history(front_settings.root)[1]["id"]
        same("rollback")
        # sync's one-time move of settings rows into a build that does not mention them
        for settings in (front_settings, twin):
            with closing(connect(settings.db_path)) as other:
                other.execute("UPDATE build_state SET applied_hash=NULL WHERE id=1")
                other.execute("UPDATE ai_settings SET model='moved/model', daily_budget_usd='3.5' WHERE id=1")
                other.execute("UPDATE tag_settings SET mode='always' WHERE id=1")
            (settings.root / "config" / "build.json").write_text('{"schema": 1, "modules": ["workspace"]}\n')
        running.request("/api/build")
        project.sync(conn, twin.root)
        assert json.loads(file(twin))["settings"]["ai"]["model"] == "moved/model"
        same("one-time move")


INCOMING = (
    {"schema": 1, "name": "Stranger", "author": "someone", "modules": ["workspace", "terminal"],
     "theme": {"base": "nord", "tokens": {"ink": "#ffeeff"}, "css": ".tabs { gap: 2px }"},
     "settings": {"notes": {"allow_ai": True}, "ai": {"model": "expensive/model", "daily_budget_usd": 50},
                  "opportunities": {"monitoring": True}, "tags": {"mode": "always"}}},
    {"schema": 1, "settings": {"ai": {"daily_budget_usd": 0.5, "rika_fast_model": ""}}},
    {"schema": 1, "name": "\ud800 lone", "description": "\U0001F600", "x-unknown": 1, "theme": {"base": "aurora"},
     "ui": {"graph": {"look": "3d"}, "planner": {"zoom": 2}}},
    {"schema": 1, "theme": {"css": "@import 'x';"}},
    {"schema": 2},
)


@pytest.mark.parametrize("incoming", INCOMING)
def test_previews_and_checks_answer_as_share_and_document_do(running, incoming):
    text = json.dumps(incoming, indent=2)
    current = running.request("/api/build")[1]
    checked = document.check(text)
    assert running.request("/api/build/check", {"text": text}) == (200, {
        "ok": checked.ok, "build": checked.build, "problems": [problem.as_dict() for problem in checked.problems]})
    status, preview = running.request("/api/build/preview", {"text": text})
    try:
        plan = share.resolve(current["build"], text, accept_sensitive=True)
    except share.ShareError as refused:
        assert (status, preview) == (refused.status, {"error": str(refused),
                                                      "problems": [problem.as_dict() for problem in refused.problems]})
        return
    assert status == 200 and preview == {"base": current["active_revision"], "about": plan["about"], "changes": plan["changes"],
                                         "restart": plan["restart"], "warnings": plan["warnings"]}


def test_the_worker_runs_its_build_applied_hooks_when_the_front_says_so(tmp_path, monkeypatch):
    """The internal route the front calls when the active build changes (kitty.web_build)."""
    from kitty import web_opportunities

    applied = []
    monkeypatch.setattr(web_opportunities, "build_applied", lambda app, build: applied.append(build))
    with site(workspace(tmp_path, ["workspace"])) as python:
        build = document.normalize({"schema": 1})
        assert python.request("/api/internal/build/applied", {"build": build}) == (200, {"ok": True})
        assert applied == [build]
        assert python.request("/api/internal/build/applied", {"build": 1})[0] == 400
        assert python.request("/api/build")[0] == 404, "the page routes are the front's"
