"""Notes writes that stay on the worker: registering references and importing portable pages.

POST /api/notes/attachments {data, name}       decode an image or PDF locally (Pillow,
                                                PyMuPDF) and register normalized PNGs: {"task"}
POST /api/notes/import      {notebook_id, page} restore a portable page with its references: {"task"}

Both run as background tasks on the worker's runner. Every other Notes route is answered
by rika-server (server/src/notes/).
"""
from __future__ import annotations

from contextlib import closing

from kitty.core import notes
from kitty.db import connect


def _error(exc: notes.NotesError):
    from kitty.web_server import APIError
    return APIError(exc.status, str(exc))


def _local_import(app, kind, operation):
    def work():
        try:
            with app.mutation_lock, closing(connect(app.settings.db_path)) as conn:
                return operation(conn)
        except notes.NotesError as exc:
            raise _error(exc) from None
    return {"task": app.tasks.start(kind, work)}


def get(app, conn, path, query):
    """The front answers every Notes read; nothing reaches the worker."""
    return None


def post(app, conn, path, body):
    try:
        if path == "/api/notes/attachments":
            notes.keys(body, {"data", "name"}, {"data", "name"})
            return _local_import(app, "notes-reference-import", lambda local: {"attachments": notes.register(local, app.settings.data_dir, body["data"], body["name"])})
        if path == "/api/notes/import":
            notes.keys(body, {"notebook_id", "page"}, {"notebook_id", "page"})
            return _local_import(app, "notes-page-restore", lambda local: notes.restore(local, app.settings.data_dir, body["notebook_id"], body["page"]))
        return None
    except notes.NotesError as exc:
        raise _error(exc) from None
