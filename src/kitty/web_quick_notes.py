"""Quick notes API: POST create, save, delete. The reads are answered by rika-server (server/src/quick_notes)."""
from __future__ import annotations

from kitty.core import quick_notes

FIELDS = {
    "create": ({"body", "tint"}, set()),
    "save": ({"id", "revision", "body", "tint", "pinned"}, {"id", "revision"}),
    "delete": ({"id", "revision"}, {"id", "revision"}),
}


def _error(exc: quick_notes.QuickNoteError):
    from kitty.web_server import APIError
    return APIError(exc.status, str(exc))


def get(app, conn, path, query):
    """The front answers the reads; nothing reaches the worker."""
    return None


def post(app, conn, path, body):
    operation = path.removeprefix("/api/quick-notes/")
    if not path.startswith("/api/quick-notes/") or operation not in FIELDS:
        return None
    allowed, required = FIELDS[operation]
    try:
        if not isinstance(body, dict) or set(body) - allowed or required - set(body):
            raise quick_notes.QuickNoteError("Unexpected or missing quick note fields")
        if operation == "create":
            return quick_notes.create(conn, body.get("body", ""), body.get("tint", "rose"))
        if operation == "save":
            return quick_notes.save(conn, body["id"], body["revision"], body=body.get("body"),
                                    tint=body.get("tint"), pinned=body.get("pinned"))
        quick_notes.delete(conn, body["id"], body["revision"])
        return {"ok": True}
    except quick_notes.QuickNoteError as exc:
        raise _error(exc) from None
