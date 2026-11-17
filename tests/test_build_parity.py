"""The build routes, the build file watcher and its live push answered by rika-server
(server/src/builds) against the retained `kitty.core.builds` on two copies of one workspace.

The reference owns the deleted handlers' glue (`web_build.BuildService`, `get`, `post`,
`download` and `share.fetch` as they were). A file clock (`KITTY_TEST_CLOCK`) makes the
answers, every push, the `build_applied` hooks the worker runs, the build file, the history
files and the projected settings rows comparable without masking anything but a background
task's random id.
"""
from __future__ import annotations

import copy
import http.client
import ipaddress
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
import pytest
from terminal_support import RIKA_SERVER, front_site
from test_web_build import Window

from kitty import web_auth, web_tasks
from kitty.core.builds import document, project, share, store
from kitty.core.workspace import open_workspace
from kitty.db import connect
from kitty.modules import modules_state
from kitty.settings import Settings
from kitty.web_server import APIError

MAX_SOURCE = 500
TASK_ID = "<task>"
#: The rows `project.apply` and `project._record` write.
TABLES = ("ai_settings", "note_preferences", "study_settings", "study_timer", "opportunity_settings", "tag_settings",
          "build_state")
#: A worker that writes down every build the front announces (`POST /api/internal/build/applied`,
#: where every extension's `build_applied` hook runs) before running the hooks.
HOOK_WORKER = """
import json, os
from kitty import web_build

post = web_build.post

def recording(app, conn, path, body):
    if path == web_build.APPLIED_PATH:
        with open(os.environ["KITTY_TEST_HOOKS"], "a", encoding="utf-8") as out:
            out.write(json.dumps(body.get("build")) + "\\n")
    return post(app, conn, path, body)

web_build.post = recording
from kitty.web_server import main
main()
"""


class Clock:
    value: datetime

    @classmethod
    def iso_seconds(cls) -> str:
        return cls.value.strftime("%Y-%m-%dT%H:%M:%SZ")


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return Clock.value.astimezone(tz) if tz else Clock.value.replace(tzinfo=None)


# share.fetch as it was (rika-server's fetch.rs now) --------------------------------------

def _public_host(host, resolver):
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise share.ShareError(f"Could not find {host}") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global:
            raise share.ShareError("Build links must point to a public https address, not this machine or a local network")


def _check_url(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        raise share.ShareError("That is not a valid link") from None
    if parts.scheme != "https":
        raise share.ShareError("Build links must use https (for example a GitHub gist or raw file link)")
    if not parts.hostname or parts.username or parts.password:
        raise share.ShareError("Build links need a host and cannot carry a user name or password")
    return parts.hostname


def reference_fetch(url, resolver=socket.getaddrinfo):
    if not isinstance(url, str) or len(url) > 2048:
        raise share.ShareError("That is not a valid link")
    with httpx.Client(timeout=10, follow_redirects=False, headers={"Accept": "application/json, text/plain;q=0.9"}) as client:
        for _hop in range(4):
            host = _check_url(url)
            _public_host(host, resolver)
            try:
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        if urlsplit(url).scheme != "https":
                            raise share.ShareError("That link redirects to a non-https address; refusing it")
                        continue
                    if response.status_code != 200:
                        raise share.ShareError(f"The link answered {response.status_code}", 502)
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > document.MAX_BYTES:
                            raise share.ShareError(f"Builds are limited to {document.MAX_BYTES // 1024} KB")
            except httpx.HTTPError as exc:
                raise share.ShareError(f"Could not fetch the link ({type(exc).__name__})", 502) from None
            try:
                return body.decode("utf-8")
            except UnicodeDecodeError:
                raise share.ShareError("The link did not return UTF-8 text") from None
    raise share.ShareError("That link redirects more than 3 times")


# The reference: web_build as it was -------------------------------------------------------

def _error(status, message, problems=None):
    return APIError(status, message, problems=[problem.as_dict() for problem in problems or []])


def _text(body, key="text"):
    value = body.get(key)
    if not isinstance(value, str):
        raise _error(400, f"Expected {{\"{key}\": the build file's text}}")
    if len(value.encode("utf-8", "surrogatepass")) > document.MAX_BYTES:
        raise _error(400, f"A build file is limited to {document.MAX_BYTES // 1024} KB")
    return value


def _fields(body, allowed, required=frozenset()):
    if set(body) - allowed or required - set(body):
        raise _error(400, f"Expected fields: {', '.join(sorted(allowed))}")


def _merge(target, patch, *, missing_only=False):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value, missing_only=missing_only)
        elif not missing_only or key not in target:
            target[key] = copy.deepcopy(value)


def _preferences(body):
    _fields(body, {"ui", "theme", "name", "author", "description"})
    patch = {key: body[key] for key in ("name", "author", "description") if key in body}
    if "ui" in body:
        if not isinstance(body["ui"], dict):
            raise _error(400, "ui must be an object")
        patch["ui"] = body["ui"]
    if "theme" in body:
        if not isinstance(body["theme"], dict) or set(body["theme"]) - {"base"}:
            raise _error(400, "Only theme.base changes here; edit tokens and CSS in the build editor")
        patch["theme"] = body["theme"]
    return patch


def _fetch(url):
    try:
        return {"text": reference_fetch(url), "source": str(url)[:MAX_SOURCE]}
    except share.ShareError as exc:
        raise _error(exc.status, str(exc)) from None


class Reference:
    """`web_build.BuildService` and its routes over the retained core; the watcher's tick is
    `edit`'s explicit refresh."""

    def __init__(self, settings: Settings, frozen, safe):
        self.settings, self.root, self.frozen, self.safe = settings, settings.root, list(frozen), safe
        self.conn = connect(settings.db_path)
        self.tasks = web_tasks.TaskRunner()
        self._pushes, self.hooks = [], []
        self._signature = self._stat()
        self.state = project.sync(self.conn, self.root)
        self.hello = self.state.active_revision

    def close(self):
        self.tasks.close()
        self.conn.close()

    def _stat(self):
        try:
            info = os.stat(store.build_path(self.root), follow_symlinks=False)
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size, info.st_ino

    def refresh(self):
        if self._stat() == self._signature:
            return
        self._signature = self._stat()
        previous, self.state = self.state, project.sync(self.conn, self.root)
        if self.state.revision == previous.revision and self.state.active_revision == previous.active_revision:
            return
        self._pushes.append(json.loads(json.dumps({"type": "build", **self.public()})))
        if self.state.active_revision != previous.active_revision:
            self.hooks.append(json.loads(json.dumps(self.state.build)))

    def public(self):
        state = self.state
        return {
            "revision": state.revision, "active_revision": state.active_revision,
            "build": state.build, "text": state.text,
            "problems": [problem.as_dict() for problem in state.problems],
            "invalid": state.invalid,
            "restart_required": modules_state(self.frozen, self.settings.config_dir)["restart_required"],
            "safe": self.safe,
        }

    def pushes(self, _count):
        found, self._pushes = self._pushes, []
        return found

    def get(self, path, query):
        if path == "/api/build":
            self._no_query(query)
            self.refresh()
            return {**self.public(), "history": store.list_history(self.root),
                    "local_preferences_moved": project.local_preferences_moved(self.conn)}
        if path == "/api/build/history":
            self._no_query(query)
            return {"history": store.list_history(self.root)}
        if path.startswith("/api/tasks/"):
            self._no_query(query)
            return self.tasks.snapshot(path.removeprefix("/api/tasks/"))
        raise APIError(404, "API route not found")

    @staticmethod
    def _no_query(query):
        if query:
            raise APIError(400, "This route does not accept query parameters")

    def post(self, path, body):
        conn, root = self.conn, self.root
        action = path.removeprefix("/api/build/") if path.startswith("/api/build/") else None
        if action == "check":
            _fields(body, {"text"}, {"text"})
            checked = document.check(_text(body))
            return {"ok": checked.ok, "build": checked.build, "problems": [problem.as_dict() for problem in checked.problems]}
        if action == "save":
            _fields(body, {"text", "base"}, {"text", "base"})
            text = _text(body)
            self.refresh()
            if body["base"] != self.state.revision:
                raise _error(409, "The build file changed since you opened it (another window or editor saved it); "
                                  "reload it, or copy your edits first")
            checked = document.check(text)
            if not checked.ok:
                raise _error(400, f"Not saved: {checked.errors[0]}", checked.problems)
            project.replace(conn, root, text)
            return {"ok": True}
        if action == "preferences":
            patch = _preferences(body)
            with project.editing(conn, root) as build:
                _merge(build, patch)
            return {"ok": True}
        if action == "local":
            patch = _preferences(body)
            if project.local_preferences_moved(conn):
                return {"moved": False}
            with project.editing(conn, root) as build:
                if project.claim_local_preferences(conn):
                    _merge(build, patch, missing_only=True)
            return {"moved": True}
        if action == "fetch":
            _fields(body, {"url"}, {"url"})
            url = body["url"]
            return {"task": self.tasks.start("build-fetch", lambda: _fetch(url))}
        if action == "preview":
            _fields(body, {"text"}, {"text"})
            self.refresh()
            try:
                plan = share.resolve(self.state.build, _text(body), accept_sensitive=True)
            except share.ShareError as exc:
                raise _error(exc.status, str(exc), exc.problems) from None
            return {"base": self.state.active_revision, "about": plan["about"], "changes": plan["changes"],
                    "restart": plan["restart"], "warnings": plan["warnings"]}
        if action == "install":
            _fields(body, {"text", "base", "accept_sensitive", "source"}, {"text", "base", "accept_sensitive"})
            if type(body["accept_sensitive"]) is not bool:
                raise _error(400, "accept_sensitive must be true or false")
            source = body.get("source", "")
            if not isinstance(source, str):
                raise _error(400, "source must be text")
            self.refresh()
            if body["base"] != self.state.active_revision:
                raise _error(409, "Your build changed since this preview; preview the install again")
            try:
                plan = share.resolve(self.state.build, _text(body), accept_sensitive=body["accept_sensitive"])
            except share.ShareError as exc:
                raise _error(exc.status, str(exc), exc.problems) from None
            project.replace(conn, root, document.dump(plan["build"]), history=("install", source[:MAX_SOURCE]))
            return {"ok": True, "restart": plan["restart"], "held": plan["held"], "changes": plan["changes"]}
        if action == "rollback":
            _fields(body, {"id"})
            entries = store.list_history(root)
            entry_id = body.get("id", entries[0]["id"] if entries else None)
            if entry_id is None:
                raise _error(404, "There is no earlier build to go back to")
            text = store.history_text(root, entry_id)
            checked = document.check(text)
            if not checked.ok:
                raise _error(400, f"That saved build cannot be restored: {checked.errors[0]}", checked.problems)
            project.replace(conn, root, text, history=("rollback", f"replaced by {entry_id}"))
            return {"ok": True, "restored": entry_id}
        raise APIError(404, "API route not found")

    def __call__(self, path, body, now, query, download=False):
        Clock.value = now
        try:
            if download:
                self._no_query(query)
                assert path == "/api/build/export"
                data = share.export_text(self.state.build).encode("utf-8")
                return 200, {"data": data.hex(), "type": "application/json; charset=utf-8",
                             "disposition": 'attachment; filename="kitty-build.json"'}
            if body is None:
                answer = self.get(path, query)
            else:
                self._no_query(query)
                answer = self.post(path, body)
                self.refresh()  # web_build.mutated
        except APIError as exc:
            return exc.status, {"error": str(exc), **({"problems": exc.problems} if exc.problems else {})}
        except store.BuildStoreError as exc:
            return exc.status, {"error": str(exc), **({"problems": [p.as_dict() for p in exc.problems]} if exc.problems else {})}
        except (TimeoutError, sqlite3.OperationalError):
            return 503, {"error": "Local workspace is busy or unavailable; try again"}
        except Exception:  # noqa: BLE001 - RequestHandler._dispatch's last resort
            return 500, {"error": "Local operation failed; inspect CLI diagnostics"}
        return 200, json.loads(json.dumps(answer))

    def task(self, task_id, now):
        """The finished snapshot of a background fetch."""
        Clock.value = now
        with self.tasks._lock:
            thread = self.tasks._threads.get(task_id)
        if thread:
            thread.join()
        return json.loads(json.dumps(self.tasks.snapshot(task_id)))

    def edit(self, now, change):
        """An external editor's save, picked up at the watcher's next tick."""
        Clock.value = now
        change(self.settings)
        self.refresh()


class Through:
    def __init__(self, site, clock, window):
        self.site, self.clock, self.window = site, clock, window
        self.hello = window.hello["build"]

    def tick(self, now):
        # Renamed into place: a fetch task finishing while the clock moves must never read a
        # truncated file (the front falls back to the real clock).
        moving = self.clock.with_name(self.clock.name + ".next")
        moving.write_text(now.isoformat(), encoding="utf-8")
        os.replace(moving, self.clock)

    def __call__(self, path, body, now, query, download=False):
        self.tick(now)
        target = path + ("?" + urlencode(query) if query else "")
        if not download:
            return self.site.request(target, body)
        client = http.client.HTTPConnection("127.0.0.1", self.site.port, timeout=10)
        try:
            client.request("GET", target, headers={"Origin": self.site.server.origin, web_auth.HEADER: self.site.secret})
            response = client.getresponse()
            data = response.read()
            if response.status != 200:
                return response.status, json.loads(data)
            return response.status, {"data": data.hex(), "type": response.getheader("Content-Type"),
                                     "disposition": response.getheader("Content-Disposition")}
        finally:
            client.close()

    def pushes(self, count):
        return [self.window.build() for _ in range(count)]

    def task(self, task_id, now):
        self.tick(now)
        deadline = time.time() + 20
        while True:
            status, snapshot = self.site.request(f"/api/tasks/{task_id}")
            assert status == 200, snapshot
            if snapshot["status"] != "running" or time.time() > deadline:
                return snapshot
            time.sleep(.02)

    def edit(self, now, change):
        self.tick(now)
        change(self.site.app.settings)


# The scenario -----------------------------------------------------------------------------

def write_build(settings, text):
    """An editor's atomic save (a temporary file renamed over the build)."""
    path = settings.root / "config" / "build.json"
    temporary = path.with_name(".build.json.edit")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def edited(change):
    def apply(settings):
        path = settings.root / "config" / "build.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        write_build(settings, json.dumps(data, indent=2, ensure_ascii=False))
    return apply


def state(settings):
    """The build file, every history file and the projected rows."""
    folder = settings.root / "data" / "build-history"
    history = {}
    for path in sorted(folder.iterdir()) if folder.is_dir() else ():
        mode = path.lstat().st_mode & 0o777
        history[path.name] = (mode, os.readlink(path) if path.is_symlink() else path.read_bytes().decode("utf-8", "surrogateescape"))
    build = settings.root / "config" / "build.json"
    with closing(connect(settings.db_path)) as conn:
        rows = {table: dict(conn.execute(f"SELECT * FROM {table} WHERE id=1").fetchone()) for table in TABLES}
    return {"file": (build.stat().st_mode & 0o777, build.read_text(encoding="utf-8")),
            "history_dir": (folder.stat().st_mode & 0o777) if folder.is_dir() else None, "history": history, "rows": rows}


SAVED = json.dumps({
    "schema": 1, "modules": ["workspace", "terminal"], "name": "Desk \u00e9\U0001F600", "description": "Main desk",
    "settings": {"ai": {"model": "synthetic/model", "daily_budget_usd": 2.675, "routing": "latency", "max_output_tokens": 3000},
                 "study": {"focus_seconds": 1800, "sound": False},
                 "opportunities": {"monitoring": True, "interval_hours": 12},
                 "tags": {"mode": "always"}, "history": {"retention_days": 7},
                 "tern": {"plugin": False}, "notes": {"allow_ai": True, "attachment_retention_days": 14}},
    "theme": {"base": "dracula", "tokens": {"ink": "#ff00ff"}, "css": ".tabs { gap: 3px }"},
    "ui": {"graph": {"look": "2d"}}}, indent=1, ensure_ascii=False)
INCOMING = json.dumps({
    "schema": 1, "name": "Shared", "author": "someone", "modules": ["workspace"],
    "theme": {"base": "nord", "tokens": {"void": "#101010"}, "css": ".tabs { gap: 2px }"},
    "settings": {"ai": {"model": "expensive/model", "daily_budget_usd": 50, "rika_fast_model": ""},
                 "notes": {"allow_ai": False}, "opportunities": {"monitoring": False}, "tags": {"mode": "suggest"},
                 "study": {"short_break_seconds": 420}},
    "ui": {"planner": {"zoom": 2}}, "x-unknown": 1}, indent=2)
LONE = json.dumps({"schema": 1, "name": "\ud800 lone"})
SECOND = json.dumps({"schema": 1, "name": "Second \u00e9", "description": "\U0001F600", "theme": {"base": "aurora"},
                     "settings": {"ai": {"daily_budget_usd": 0.5}, "opportunities": {"monitoring": True, "interval_hours": 6}}})
INVALID_TOKEN = '"#101010",\n      "pad": "url(https://evil.example/x)"'
LINKS = ("http://example.com/build.json", "file:///etc/passwd", "https://user:pw@example.com/build.json", "https:///path",
         "https://[::1", "https://localhost/build.json", "https://127.0.0.1/x", "https://10.1.2.3/x", "https://[::1]/x",
         "https://[fe80::1%25eth0]/x", "https://[fe80::1%lo]/x", "https://100.64.0.1/x", "https://rika-test.invalid/build.json",
         "x" * 2049, 5, None,
         "https://192.168.1.1:8443/x", "HTTPS://127.0.0.1/x")


def scenario(call, base):
    answers = []
    now = base

    def record(label, status, answer):
        answers.append((label, status, answer, call.pushes(expected[len(answers)] if expected else None)))

    def do(path, body=None, expect=200, download=False, tick=True, **query):
        nonlocal now
        if tick:
            now += timedelta(seconds=1)
        status, answer = call(path, body, now, query, download)
        assert status == expect, (len(answers), path, body, query, status, answer)
        if isinstance(answer, dict) and isinstance(answer.get("task"), dict):
            task_id = answer["task"]["id"]
            answer = {**answer, "task": {**answer["task"], "id": TASK_ID}}
            finished = call.task(task_id, now)
            answer["finished"] = {**finished, "id": TASK_ID}
            do(f"/api/tasks/{task_id}", expect=400, tick=False, x="1")
        record(path.replace(path.removeprefix("/api/tasks/"), TASK_ID) if path.startswith("/api/tasks/") else path, status, answer)
        return answer

    def edit(change):
        nonlocal now
        now += timedelta(seconds=1)
        call.edit(now, change)
        record("edit", 200, None)

    def checkpoint():
        record("state", 200, state(call.settings if isinstance(call, Reference) else call.site.app.settings))

    expected = getattr(call, "expected", None)
    record("hello", 200, call.hello)
    checkpoint()
    # Reads.
    current = do("/api/build")
    do("/api/build", expect=400, x="1")
    do("/api/build/history")
    do("/api/build/history", expect=400, x="1")
    do("/api/build/nothing", expect=404)
    do("/api/build/check", expect=404)
    do("/api/build/export", download=True)
    do("/api/build/export", expect=400, download=True, x="1")
    # Checks never write.
    do("/api/build/check", {}, 400)
    do("/api/build/check", {"text": 5}, 400)
    do("/api/build/check", {"text": "{}", "base": "x"}, 400)
    do("/api/build/check", {"text": "x" * (document.MAX_BYTES + 1)}, 400)
    do("/api/build/check", {"text": "\u00e9" * (document.MAX_BYTES // 2 + 1)}, 400)
    do("/api/build/check", {"text": "{}"}, 400, x="1")
    for text in (SAVED, INCOMING, SECOND, LONE, '{"schema": 1,', '{\n "schema": 1,\n "theme": {"css": "@import \\"x\\";"}\n}',
                 '{"schema": 2}', '{"schema": 1, "schema": 1}', "[]", '{"schema": 1, "theme": {"base": "nope"}, "zzz": 1}',
                 '{"schema": 1, "settings": {"ai": {"daily_budget_usd": 1e400}}}', '\ufeff{"schema": 1}', ""):
        do("/api/build/check", {"text": text})
    do("/api/build/nothing", {}, 404)
    do("/api/build", {}, 404)
    # The build editor's save.
    revision = current["revision"]
    do("/api/build/save", {"text": SAVED}, 400)
    do("/api/build/save", {"text": SAVED, "base": "0" * 64}, 409)
    do("/api/build/save", {"text": SAVED, "base": 5}, 409)
    do("/api/build/save", {"text": '{"schema": 1, "theme": {"css": "@import \'x\';"}}', "base": revision}, 400)
    do("/api/build/save", {"text": SAVED, "base": revision})
    do("/api/build/save", {"text": SAVED, "base": revision}, 409)
    current = do("/api/build")
    do("/api/build/save", {"text": SAVED, "base": current["revision"]})  # unchanged: no push
    checkpoint()
    # Settings screens' preferences.
    for refused in ({"keys": {}}, {"ui": 5}, {"ui": []}, {"theme": {"css": "x"}}, {"theme": "nord"},
                    {"ui": {"graph": {"look": "4d"}}}, {"ui": {"brain": {"speed": 9}}}, {"name": 5}, {"name": "x" * 201}):
        do("/api/build/preferences", refused, 400)
    do("/api/build/preferences", {"theme": {"base": "nope"}})  # an unknown theme is a warning
    do("/api/build/preferences", {"author": "Someone", "ui": {"graph": {"look": "3d"}}, "theme": {"base": "nord"}, "name": "Desk"})
    do("/api/build/preferences", {})
    # A lone surrogate cannot be written as UTF-8: Python's last-resort 500, nothing written.
    do("/api/build/preferences", {"description": "\ud83d lone"}, 500)
    do("/api/build/preferences", {"description": "Main desk \U0001F600", "ui": {"study": {"guide_view": "rail"}}})
    # The one-time move of a browser's local preferences.
    do("/api/build/local", {"ui": 5}, 400)
    do("/api/build/local", {"ui": {"graph": {"look": "2d"}, "appearance": {"density": "comfortable"}, "home": {"look": "2d"},
                                   "study": {"guide_view": "columns", "plans_view": "days"},
                                   "notifications": {"quiet": {"enabled": True, "from": 1380, "to": 420}}},
                            "theme": {"base": "liquid-glass"}})
    do("/api/build/local", {"theme": {"base": "dracula"}})
    do("/api/build/local", {"keys": 1}, 400)
    checkpoint()
    # External editors: a valid save, an invalid one (the last good build stays), its fix.
    edit(edited(lambda data: data.update(theme={"tokens": {"void": "#101010"}},
                                         settings={**data["settings"], "tags": {"mode": "suggest"}})))
    good = do("/api/build")
    edit(lambda settings: write_build(settings, (settings.root / "config" / "build.json").read_text(encoding="utf-8")
                                      .replace('"#101010"', INVALID_TOKEN)))
    invalid = do("/api/build")
    do("/api/build/preferences", {"name": "Refused"}, 409)
    do("/api/build/local", {"name": "Refused"})
    do("/api/build/preview", {"text": INCOMING})
    do("/api/build/save", {"text": SAVED, "base": good["revision"]}, 409)
    # A lone surrogate cannot be written as UTF-8 (Python's 500).
    do("/api/build/install", {"text": LONE, "base": invalid["active_revision"], "accept_sensitive": True}, 500)
    # An install over the invalid file keeps the invalid text in the history.
    do("/api/build/install", {"text": SECOND, "base": invalid["active_revision"], "accept_sensitive": True, "source": "file"})
    checkpoint()
    edit(lambda settings: write_build(settings, "{\n  \"schema\": 1,\n"))
    broken = do("/api/build")
    do("/api/build/save", {"text": SAVED, "base": broken["revision"]})
    # Previews and installs.
    do("/api/build/preview", {}, 400)
    do("/api/build/preview", {"text": "{"}, 400)
    do("/api/build/preview", {"text": '{"schema": 2}'}, 400)
    do("/api/build/preview", {"text": '{"schema": 1, "theme": {"css": "@import \'x\';"}}'}, 400)
    preview = do("/api/build/preview", {"text": INCOMING})
    install = {"text": INCOMING, "base": preview["base"], "accept_sensitive": False}
    do("/api/build/install", {"text": INCOMING, "base": preview["base"]}, 400)
    do("/api/build/install", {**install, "accept_sensitive": 1}, 400)
    do("/api/build/install", {**install, "accept_sensitive": "yes"}, 400)
    do("/api/build/install", {**install, "source": 5}, 400)
    do("/api/build/install", {**install, "extra": 1}, 400)
    do("/api/build/install", {**install, "base": "0" * 64}, 409)
    do("/api/build/install", {**install, "text": '{"schema": 2}'}, 400)
    do("/api/build/install", {**install, "source": "file \U0001F600"})
    do("/api/build/install", install, 409)
    preview = do("/api/build/preview", {"text": SECOND})
    do("/api/build/install", {"text": SECOND, "base": preview["base"], "accept_sensitive": True, "source": "\U0001F600" * 600},
       tick=False)
    preview = do("/api/build/preview", {"text": SAVED}, tick=False)
    # The same instant twice: the history id moves by a microsecond.
    do("/api/build/install", {"text": SAVED, "base": preview["base"], "accept_sensitive": False}, tick=False)
    history = do("/api/build/history")["history"]
    checkpoint()
    # Roll back.
    do("/api/build/rollback", {"x": 1}, 400)
    do("/api/build/rollback", {"id": 5}, 400)
    do("/api/build/rollback", {"id": "../../config/build"}, 400)
    do("/api/build/rollback", {"id": "20200101T000000000000Z"}, 404)
    do("/api/build/rollback", {"id": None}, 404)
    invalid_entry = next(entry["id"] for entry in history if not entry["valid"])
    do("/api/build/rollback", {"id": invalid_entry}, 400)
    do("/api/build/rollback", {})
    do("/api/build/rollback", {"id": history[-2]["id"]})
    # The history keeps the newest ten.
    for _ in range(11):
        do("/api/build/rollback", {})
    checkpoint()
    # Entries that are not history: a link, a stray file, unreadable JSON, text that is not a string.
    def strays(settings):
        folder = settings.root / "data" / "build-history"
        (folder / "20990101T000000000000Z.json").symlink_to("../../config/build.json")
        (folder / "notes.json").write_text('{"text": "{}"}', encoding="utf-8")
        (folder / "20980101T000000000000Z.json").write_text("{", encoding="utf-8")
        (folder / "20970101T000000000000Z.json").write_text('{"text": 5}', encoding="utf-8")
        (folder / "20960101T000000000000Z.json").write_text('{"text": "{\\"schema\\": 1, \\"name\\": \\"Old\\"}", "reason": 3}',
                                                             encoding="utf-8")
    edit(strays)
    do("/api/build/history")
    do("/api/build/rollback", {"id": "20990101T000000000000Z"}, 404)
    do("/api/build/rollback", {"id": "20970101T000000000000Z"}, 404)
    do("/api/build/rollback", {"id": "20960101T000000000000Z"})
    for _ in range(3):
        do("/api/build/rollback", {})
    do("/api/build")
    checkpoint()
    # The one-time move of settings rows into a build that does not mention them.
    def rows_moved(settings):
        with closing(connect(settings.db_path)) as conn:
            conn.execute("UPDATE build_state SET applied_hash=NULL WHERE id=1")
            conn.execute("UPDATE ai_settings SET model='moved/model', daily_budget_usd='3.5' WHERE id=1")
            conn.execute("UPDATE tag_settings SET mode='always' WHERE id=1")
            conn.commit()
        write_build(settings, '{"schema": 1, "modules": ["workspace"]}\n')
    edit(rows_moved)
    do("/api/build")
    checkpoint()
    # Links are fetched in the background; every refusal is the task's error.
    do("/api/build/fetch", {}, 400)
    do("/api/build/fetch", {"url": "https://example.com", "x": 1}, 400)
    do("/api/build/fetch", {"url": "https://example.com"}, 400, x="1")
    for link in LINKS:
        do("/api/build/fetch", {"url": link})
    do("/api/build/export", download=True)
    checkpoint()
    return answers


def differences(actual, wanted, at="") -> list:
    """Where two answers differ (a readable assertion message)."""
    if isinstance(actual, dict) and isinstance(wanted, dict):
        found = [f"{at}.{key}: only one side" for key in actual.keys() ^ wanted.keys()]
        return found + [item for key in actual.keys() & wanted.keys() for item in differences(actual[key], wanted[key], f"{at}.{key}")]
    if isinstance(actual, (list, tuple)) and isinstance(wanted, (list, tuple)) and len(actual) == len(wanted):
        return [item for index, pair in enumerate(zip(actual, wanted)) for item in differences(*pair, f"{at}[{index}]")]
    return [] if actual == wanted else [f"{at}: {actual!r:.300} != {wanted!r:.300}"]


def seed(tmp_path, modules):
    root = tmp_path / "seed"
    (root / "config").mkdir(parents=True)
    (root / "config" / "build.json").write_text(json.dumps(
        {"schema": 1, "modules": list(modules), "settings": {"study": {"focus_seconds": 1500}}}, indent=2))
    settings = Settings.load(root=root, db_path=root / "data" / "web.db")
    with open_workspace(settings):
        pass
    return settings


def copy_of(seeded: Settings, path: Path) -> Settings:
    shutil.copytree(seeded.root, path, symlinks=True)
    return Settings.load(root=path, db_path=path / "data" / "web.db")


def base_time():
    return datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=250_000) + timedelta(days=1)


def reference_run(settings, modules, safe, base, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(store, "datetime", FixedDatetime)
        patch.setattr(project, "datetime", FixedDatetime)
        patch.setattr(web_tasks, "now_iso", Clock.iso_seconds)
        Clock.value = base
        reference = Reference(settings, modules, safe)
        try:
            answers = scenario(reference, base)
        finally:
            reference.close()
        return answers, reference.hooks


CASES = [(("workspace", "terminal"), False), ((), True)]


@pytest.mark.parametrize(("modules", "safe"), CASES)
def test_reference_scenario(tmp_path, monkeypatch, modules, safe, record_property):
    """Executable oracle sanity check, usable before the Rust implementation exists."""
    seeded = seed(tmp_path, modules)
    answers, hooks = reference_run(copy_of(seeded, tmp_path / "reference"), modules, safe, base_time(), monkeypatch)
    record_property("requests", len(answers))
    assert len(answers) > 120
    assert sum(len(pushes) for *_, pushes in answers) > 25 and len(hooks) > 15
    assert {status for _, status, _, _ in answers} == {200, 400, 404, 409, 500}


@pytest.mark.parametrize(("modules", "safe"), CASES)
def test_the_front_answers_and_pushes_builds_as_python_did(tmp_path, monkeypatch, modules, safe):
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER})")
    seeded = seed(tmp_path, modules)
    reference_settings, front_settings = copy_of(seeded, tmp_path / "reference"), copy_of(seeded, tmp_path / "front")
    base = base_time()
    expected, hooks = reference_run(reference_settings, modules, safe, base, monkeypatch)
    clock, hook_file = tmp_path / "clock", tmp_path / "hooks"
    clock.write_text(base.isoformat(), encoding="utf-8")
    with front_site(front_settings, env={"KITTY_TEST_CLOCK": str(clock), "KITTY_TEST_HOOKS": str(hook_file)},
                    worker=[sys.executable, "-c", HOOK_WORKER], args=("--safe",) if safe else ()) as site:
        window = Window(site)
        try:
            through = Through(site, clock, window)
            through.expected = [len(pushes) for *_, pushes in expected]
            actual = scenario(through, base)
            assert window.silent(), "no push beyond Python's"
        finally:
            window.close()
        deadline = time.time() + 10
        while len(hook_file.read_text().splitlines() if hook_file.exists() else []) < len(hooks) and time.time() < deadline:
            time.sleep(.05)
    assert len(actual) == len(expected)
    for index, (answer, wanted) in enumerate(zip(actual, expected, strict=True)):
        assert answer == wanted, (index, answer[0], differences(answer, wanted)[:6])
    assert [json.loads(line) for line in hook_file.read_text().splitlines()] == hooks
