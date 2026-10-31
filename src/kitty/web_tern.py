"""Terminal module: Stencil Tern beside kitty!'s herdr (kitty.core.tern).

GET  /api/terminal/tern  ->  status
    {install_url, installed, path, source, override, version, problem, workspace,
     socket, running, windows, sessions: [{id, name, shown, tabs: [{id, number, name,
     shown, blocks: [{id, title, cwd, program, cols, rows, focused, live, exited,
     working}]}]}]}
    Reading never starts Tern; ``running`` is false until kitty!'s daemon runs.
POST /api/terminal/tern/start     {}  ->  status   (kitty!'s daemon and session)
POST /api/terminal/tern/open      {}  ->  status   (plus a native Tern window on it)
POST /api/terminal/tern/settings  {"path": "/abs/tern" | "" | null}  ->  status
    Header-protected like every mutation. Without a usable ``tern`` start/open
    answer 409 with the install pointer; a daemon that cannot start answers 503.
"""
from __future__ import annotations

from kitty.core.tern import TernMissing, TernRuntime, TernUnavailable

PREFIX = "/api/terminal/tern"


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    app.tern = TernRuntime(app.settings.root, app.settings.root / ".private" / "tern")


def stop(app) -> None:
    app.tern.stop()


def _status(app) -> dict:
    try:
        return app.tern.status()
    except TernUnavailable as exc:
        raise _error(503, str(exc)) from None


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return _status(app)


def post(app, conn, path, body):
    if path == PREFIX + "/settings":
        if not isinstance(body, dict) or set(body) != {"path"} or not isinstance(body["path"], (str, type(None))):
            raise _error(400, 'Tern settings expect {"path": "/absolute/path/to/tern"} or an empty path')
        try:
            app.tern.save_override(body["path"].strip() if body["path"] else None)
        except ValueError as exc:
            raise _error(400, str(exc)) from None
        return _status(app)
    actions = {PREFIX + "/start": app.tern.start, PREFIX + "/open": app.tern.open_window}
    if path not in actions:
        return None
    if body:
        raise _error(400, "This Tern request expects an empty object")
    try:
        actions[path]()
    except TernMissing as exc:
        raise _error(409, str(exc)) from None
    except TernUnavailable as exc:
        raise _error(503, str(exc)) from None
    except OSError:
        raise _error(503, "kitty!'s Tern daemon could not start") from None
    return _status(app)
