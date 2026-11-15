"""Settings → Devices → GitHub backup: a push-only copy of the notes in a private repository.

Page routes (behind the normal page guard; mutations behind the mutation guard)::

    GET  /api/reposync               {enabled, reason, repo, private, paused, problem, running,
                                      synced_at, pages, last_run, next_run}
    POST /api/reposync/repo          {repo}     owner/name or a GitHub address; refused when public
    POST /api/reposync/now           {}
    POST /api/reposync/pause         {paused}
    POST /api/reposync/disconnect    {}

Every mutation answers with the new state. The backup (kitty.core.reposync) and its
schedule never run for the demo workspace.
"""
from __future__ import annotations

from kitty.core import reposync

PREFIX = "/api/reposync"
DEMO = "GitHub backup is off in the demo workspace"
_BODIES = {"repo": {"repo"}, "now": set(), "pause": {"paused"}, "disconnect": set()}


def start(app) -> None:
    app.reposync = None
    if app.settings.demo:
        return
    app.reposync = reposync.RepoSync(app.settings, lock=app.mutation_lock)
    app.reposync.start()


def stop(app) -> None:
    backup = getattr(app, "reposync", None)
    if backup is not None:
        backup.stop()


def _state(app, conn) -> dict:
    if app.reposync is None:
        return {"enabled": False, "reason": DEMO, "repo": None, "private": None, "paused": False, "problem": None,
                "running": False, "synced_at": None, "pages": 0, "last_run": None, "next_run": None}
    return app.reposync.state(conn)


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return _state(app, conn)


def post(app, conn, path, body):
    from kitty.web_server import APIError

    action = path.removeprefix(PREFIX + "/")
    if not path.startswith(PREFIX + "/") or action not in _BODIES:
        return None
    if set(body) != _BODIES[action]:
        raise APIError(400, "Expected " + (", ".join(sorted(_BODIES[action])) or "an empty object"))
    backup = app.reposync
    if backup is None:
        raise APIError(409, DEMO)
    try:
        if action == "repo":
            backup.connect(conn, body["repo"])
        elif action == "now":
            backup.sync_now(conn)
        elif action == "pause":
            backup.pause(conn, body["paused"])
        else:
            backup.disconnect(conn)
    except reposync.RepoSyncError as error:
        raise APIError(error.status, str(error)) from None
    return backup.state(conn)
