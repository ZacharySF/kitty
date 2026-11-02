"""Workspace: the live page mirror (platform design Q11) for kitty!'s windows.

The paired browser extension sends frames to the client ingress (kitty.web_clients):

    POST /api/ingress/mirror/frame  capture: {tab, url, title, text?, selection?, image?}
                                    -> {mirror, frame, expires_in}
    POST /api/ingress/mirror/stop   capture: {tab} -> {stopped}

Each accepted frame is pushed to every open window over the live channel as
``{"type": "mirror", "id", "client", "tab", "url", "title", "selection", "frame",
"text_rev", "text"? (only when it changed), "image": {frame, type, width, height, bytes}
| null, "at", "started_at", "expires_in"}``; a dropped mirror as ``{"type":
"mirror_end", "id", "reason": stopped|expired|revoked|limit}``.

Page routes (behind the normal page guard)::

    GET /api/mirror                         {mirrors: [... with text], retention}
    GET /api/mirror/<id>/image?frame=<n>    the screenshot of frame n while it is the latest

Frames live in memory only (kitty.core.mirror states the retention).
"""
from __future__ import annotations

import re
import threading

from kitty.core import mirror

#: How often expired mirrors are looked for once a frame has arrived.
SWEEP_SECONDS = 5.0


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    app.mirrors = mirror.Mirrors()
    app.mirror_sweeper = None


def stop(app) -> None:
    app.mirrors.clear()
    sweeper = app.mirror_sweeper
    if sweeper is not None:
        sweeper.join(timeout=5)


def _push(app, messages: list[dict]) -> None:
    for message in messages:
        app.live.broadcast(message)


def _sweep(app) -> None:
    while not app.streams_closed.wait(SWEEP_SECONDS):
        _push(app, app.mirrors.expire())


def frame(app, client: dict, body) -> dict:
    """One frame from a paired client (kitty.core.mirror validates it), pushed to every window."""
    reply, pushes = app.mirrors.accept(client, body)
    if app.mirror_sweeper is None:
        app.mirror_sweeper = threading.Thread(target=_sweep, args=(app,), name="kitty-mirror-sweep", daemon=True)
        app.mirror_sweeper.start()
    _push(app, pushes)
    return reply


def stop_tab(app, client: dict, body) -> dict:
    pushes = app.mirrors.stop(client, body)
    _push(app, pushes)
    return {"stopped": bool(pushes)}


def revoked(app, client_id: str) -> None:
    """A revoked client's mirrors go at once."""
    if getattr(app, "mirrors", None) is not None:
        _push(app, app.mirrors.drop_client(client_id))


def get(app, conn, path, query):
    if path != "/api/mirror":
        return None
    app._no_query(query)
    return {"mirrors": app.mirrors.listing(), "retention": mirror.RETENTION}


def post(app, conn, path, body):
    return None


def download(app, conn, path, query):
    match = re.fullmatch(r"/api/mirror/([0-9a-f]{32})/image", path)
    if not match:
        return None
    if set(query) != {"frame"} or not query["frame"].isdigit() or len(query["frame"]) > 12:
        raise _error(400, "Expected ?frame=<number>")
    image = app.mirrors.image(match[1], int(query["frame"]))
    if image is None:
        raise _error(404, "That mirror frame is gone; a newer one replaced it or mirroring stopped")
    return image.data, image.type, None
