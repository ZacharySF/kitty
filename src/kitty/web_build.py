"""Core: the live build (docs/platform-design.md, Q8/Q9).

The active build is config/build.json (kitty.core.builds). This extension watches it,
projects every valid save into SQLite and pushes it to every window over the live
channel (kitty.web_live), whoever saved it: the build editor, a Settings screen, an
install or rollback, another rika! process, or any text editor. A save with an error
changes nothing: the last good build stays active and every window shows the problem.

GET  /api/build                 the active build, the file's text and problems, history
GET  /api/build/export          the active build as a shareable kitty-build.json (download)
POST /api/build/check   {text}  problems with a text and the build it describes (no write)
POST /api/build/save    {text, base}            replace the file (the build editor); base is
                                                the file revision the editor started from
POST /api/build/preferences {ui?, theme?: {base}, name?, author?, description?}
                                                merge interface preferences (Settings)
POST /api/build/local   {ui?, theme?: {base}}   once per workspace: a browser's old local
                                                preferences, for keys the build does not set
POST /api/build/fetch   {url}                   fetch a build from an https link in the
                                                background: {"task"}; its result is {text, source}
POST /api/build/preview {text}                  what installing a build would change
POST /api/build/install {text, base, accept_sensitive, source}  install it in one step
POST /api/build/rollback {id?}                  restore a saved build (default: the newest)

Pushed to every window: ``{"type": "build", "revision", "active_revision", "build",
"text", "problems", "invalid", "restart_required", "safe"}``.
"""
from __future__ import annotations

import copy
import os
import threading
from contextlib import closing, nullcontext
from typing import Any

from kitty.core.builds import document, project, share, store
from kitty.db import connect

WATCH_SECONDS = 0.25
MAX_SOURCE = 500


def _error(status: int, message: str, problems: list[document.Problem] | None = None):
    from kitty.web_server import APIError

    return APIError(status, message, problems=[problem.as_dict() for problem in problems or []])


class BuildService:
    """The active build of this server and the file watcher behind it."""

    def __init__(self, app):
        self.app = app
        self.root = app.settings.root
        self._lock = threading.Lock()
        self._signature = self._stat()
        with closing(connect(app.settings.db_path)) as conn:
            self.state = project.sync(conn, self.root)
        self._thread = threading.Thread(target=self._watch, name="kitty-build-watch", daemon=True)
        self._thread.start()

    @property
    def active_revision(self) -> str:
        return self.state.active_revision

    def _stat(self) -> tuple[int, int, int] | None:
        try:
            info = os.stat(store.build_path(self.root), follow_symlinks=False)
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size, info.st_ino

    def _watch(self) -> None:
        # An editor may write a file in pieces: act once it has stood still for one tick.
        seen = self._signature
        while not self.app.streams_closed.wait(WATCH_SECONDS):
            current = self._stat()
            try:
                if current == seen:
                    self.refresh()
            except Exception:  # noqa: BLE001 - a bad save must never stop watching
                pass
            seen = current

    def refresh(self, *, locked: bool = False) -> None:
        """Pick up a changed file: project it when valid, push it (or its problems) to every window.

        `locked` says the caller already holds the server's mutation lock (a POST handler).
        """
        if self._stat() == self._signature:
            return
        with (nullcontext() if locked else self.app.mutation_lock), self._lock:
            signature = self._stat()
            if signature == self._signature:
                return
            self._signature = signature
            with closing(connect(self.app.settings.db_path)) as conn:
                synced = project.sync(conn, self.root)
            previous, self.state = self.state, synced
        if synced.revision == previous.revision and synced.active_revision == previous.active_revision:
            return
        self.app.live.broadcast({"type": "build", **self.public()})
        if synced.active_revision != previous.active_revision:
            for extension in self.app.extensions:
                hook = getattr(extension, "build_applied", None)
                if hook:
                    hook(self.app, synced.build)

    def public(self) -> dict[str, Any]:
        state = self.state
        modules = self.app.modules_state()
        return {
            "revision": state.revision, "active_revision": state.active_revision,
            "build": state.build, "text": state.text,
            "problems": [problem.as_dict() for problem in state.problems],
            "invalid": state.invalid,
            "restart_required": modules["restart_required"],
            "safe": self.app.safe,
        }


def start(app) -> None:
    app.builds = BuildService(app)


def stop(app) -> None:
    pass  # The watcher ends with the server's streams (app.streams_closed).


def mutated(app) -> None:
    """A Settings screen, the modules screen or a build route may have written the file."""
    app.builds.refresh()


def download(app, conn, path, query):
    if path != "/api/build/export":
        return None
    app._no_query(query)
    return share.export_text(app.builds.state.build).encode("utf-8"), "application/json; charset=utf-8", "kitty-build.json"


def get(app, conn, path, query):
    if path == "/api/build":
        app._no_query(query)
        app.builds.refresh()
        return {**app.builds.public(), "history": store.list_history(app.settings.root),
                "local_preferences_moved": project.local_preferences_moved(conn)}
    if path == "/api/build/history":
        app._no_query(query)
        return {"history": store.list_history(app.settings.root)}
    return None


def _text(body: dict, key: str = "text") -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise _error(400, f"Expected {{\"{key}\": the build file's text}}")
    if len(value.encode("utf-8", "surrogatepass")) > document.MAX_BYTES:
        raise _error(400, f"A build file is limited to {document.MAX_BYTES // 1024} KB")
    return value


def _fields(body: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    if set(body) - allowed or required - set(body):
        raise _error(400, f"Expected fields: {', '.join(sorted(allowed))}")


def _merge(target: dict[str, Any], patch: dict[str, Any], *, missing_only: bool = False) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value, missing_only=missing_only)
        elif not missing_only or key not in target:
            target[key] = copy.deepcopy(value)


def _preferences(body: dict) -> dict[str, Any]:
    """What a Settings screen may change directly: ``ui``, the theme name and the build's
    name, author and description (validated with the whole build before it is written)."""
    _fields(body, {"ui", "theme", "name", "author", "description"})
    patch: dict[str, Any] = {key: body[key] for key in ("name", "author", "description") if key in body}
    if "ui" in body:
        if not isinstance(body["ui"], dict):
            raise _error(400, "ui must be an object")
        patch["ui"] = body["ui"]
    if "theme" in body:
        if not isinstance(body["theme"], dict) or set(body["theme"]) - {"base"}:
            raise _error(400, "Only theme.base changes here; edit tokens and CSS in the build editor")
        patch["theme"] = body["theme"]
    return patch


def _fetch(url: Any) -> dict[str, str]:
    try:
        return {"text": share.fetch(url), "source": str(url)[:MAX_SOURCE]}
    except share.ShareError as exc:
        raise _error(exc.status, str(exc)) from None


def post(app, conn, path, body):
    if not path.startswith("/api/build/"):
        return None
    action = path.removeprefix("/api/build/")
    root = app.settings.root
    builds: BuildService = app.builds
    if action == "check":
        _fields(body, {"text"}, {"text"})
        checked = document.check(_text(body))
        return {"ok": checked.ok, "build": checked.build, "problems": [problem.as_dict() for problem in checked.problems]}
    if action == "save":
        _fields(body, {"text", "base"}, {"text", "base"})
        text = _text(body)
        builds.refresh(locked=True)
        if body["base"] != builds.state.revision:
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
        return {"task": app.tasks.start("build-fetch", lambda: _fetch(url))}
    if action == "preview":
        _fields(body, {"text"}, {"text"})
        builds.refresh(locked=True)
        try:
            plan = share.resolve(builds.state.build, _text(body), accept_sensitive=True)
        except share.ShareError as exc:
            raise _error(exc.status, str(exc), exc.problems) from None
        return {"base": builds.active_revision, "about": plan["about"], "changes": plan["changes"],
                "restart": plan["restart"], "warnings": plan["warnings"]}
    if action == "install":
        _fields(body, {"text", "base", "accept_sensitive", "source"}, {"text", "base", "accept_sensitive"})
        if type(body["accept_sensitive"]) is not bool:
            raise _error(400, "accept_sensitive must be true or false")
        source = body.get("source", "")
        if not isinstance(source, str):
            raise _error(400, "source must be text")
        builds.refresh(locked=True)
        if body["base"] != builds.active_revision:
            raise _error(409, "Your build changed since this preview; preview the install again")
        try:
            plan = share.resolve(builds.state.build, _text(body), accept_sensitive=body["accept_sensitive"])
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
    return None
