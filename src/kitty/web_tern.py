"""Terminal module: Stencil Tern beside kitty!'s herdr (kitty.core.tern).

GET  /api/terminal/tern  ->  status
    {install_url, installed, path, source, override, version, problem, workspace,
     socket, running, windows, sessions: [{id, name, shown, tabs: [{id, number, name,
     shown, blocks: [{id, title, cwd, program, cols, rows, focused, live, exited,
     working}]}]}], activity: [{event, pane, status, at}], quick_notes}
    Reading never starts Tern; ``running`` is false until kitty!'s daemon runs.
    ``activity`` lists recent pane starts and exits, newest first; ``quick_notes``
    says whether captures can be saved (the Workspace module is on).
POST /api/terminal/tern/start     {}  ->  status   (kitty!'s daemon and session)
POST /api/terminal/tern/open      {}  ->  status   (plus a native Tern window on it)
POST /api/terminal/tern/new       {}  ->  status   (a new pane in kitty!'s session)
POST /api/terminal/tern/run       {"block": ID, "text": "one line"}  ->  status
POST /api/terminal/tern/focus     {"block": ID}  ->  status   (opens a window when none is)
POST /api/terminal/tern/capture   {"block": ID}  ->  {note_id, block, truncated}
    Saves the pane's plain text (newest 60 KiB) as a quick note; needs the
    Workspace module (409 otherwise).
POST /api/terminal/tern/settings  {"path": "/abs/tern" | "" | null}  ->  status
    Header-protected like every mutation. Without a usable ``tern`` the actions
    answer 409 with the install pointer; run/focus/capture answer 409 while kitty!'s
    daemon is not running, 404 for a block that is not a pane of kitty!'s session
    and 400 for a non-integer block or text that is not one line. A daemon that
    cannot start or answer is 503.
"""
from __future__ import annotations

from datetime import UTC, datetime

from kitty.core import quick_notes
from kitty.core.tern import (
    SESSION,
    TernMissing,
    TernNoPane,
    TernNotRunning,
    TernRuntime,
    TernUnavailable,
    pane_id,
)

PREFIX = "/api/terminal/tern"
#: Request fields of the pane actions.
PANE_FIELDS = {PREFIX + "/run": {"block", "text"}, PREFIX + "/focus": {"block"}, PREFIX + "/capture": {"block"}}
CAPTURE_TINT = "sky"


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    app.tern = TernRuntime(app.settings.root, app.settings.root / ".private" / "tern")


def stop(app) -> None:
    app.tern.stop()


def _status(app) -> dict:
    try:
        return {**app.tern.status(), "quick_notes": "workspace" in app.modules}
    except TernUnavailable as exc:
        raise _error(503, str(exc)) from None


def _act(action):
    try:
        return action()
    except ValueError as exc:
        raise _error(400, str(exc)) from None
    except (TernMissing, TernNotRunning) as exc:
        raise _error(409, str(exc)) from None
    except TernNoPane as exc:
        raise _error(404, str(exc)) from None
    except TernUnavailable as exc:
        raise _error(503, str(exc)) from None
    except OSError:
        raise _error(503, "kitty!'s Tern daemon could not start") from None


def _capture(app, conn, block) -> dict:
    _act(lambda: pane_id(block))
    if "workspace" not in app.modules:
        raise _error(409, "Captures are saved as quick notes; turn on the Workspace module first")
    text, truncated = _act(lambda: app.tern.capture(block))
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    note = quick_notes.create(conn, f"Tern capture · {SESSION}/{block} · {stamp}\n\n{text}", CAPTURE_TINT)
    return {"note_id": note["id"], "block": block, "truncated": truncated}


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
    if path in PANE_FIELDS:
        if not isinstance(body, dict) or set(body) != PANE_FIELDS[path]:
            raise _error(400, f"This Tern request expects {{{', '.join(sorted(PANE_FIELDS[path]))}}}")
        if path == PREFIX + "/capture":
            return _capture(app, conn, body["block"])
        if path == PREFIX + "/run":
            _act(lambda: app.tern.run(body["block"], body["text"]))
        else:
            _act(lambda: app.tern.focus(body["block"]))
        return _status(app)
    actions = {PREFIX + "/start": app.tern.start, PREFIX + "/open": app.tern.open_window,
               PREFIX + "/new": app.tern.new_pane}
    if path not in actions:
        return None
    if body:
        raise _error(400, "This Tern request expects an empty object")
    _act(actions[path])
    return _status(app)
