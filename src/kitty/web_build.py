"""Core: the worker's side of the live build (docs/platform-design.md, Q8/Q9).

rika-server owns the build routes (``/api/build*``), the file watcher and the push to every
window (server/src/builds/). Behind it, this module serves the one route only the front
reaches (it refuses ``/api/internal/...`` to pages):

POST /api/internal/build/applied {build}
    The active build changed (any window, Settings, an external editor or an install):
    every extension's ``build_applied(app, build)`` hook runs (web_opportunities turns
    scheduled monitoring on or off).

The Settings screens served here still write the build through
kitty.core.builds.project.editing; the front notices every committed mutation and syncs.
"""
from __future__ import annotations

APPLIED_PATH = "/api/internal/build/applied"


def _error(status: int, message: str):
    from kitty.web_server import APIError

    return APIError(status, message)


def get(app, conn, path, query):
    return None


def post(app, conn, path, body):
    if path != APPLIED_PATH:
        return None
    if set(body) != {"build"} or not isinstance(body["build"], dict):
        raise _error(400, 'Expected {"build": the active build}')
    for extension in app.extensions:
        hook = getattr(extension, "build_applied", None)
        if hook:
            hook(app, body["build"])
    return {"ok": True}
