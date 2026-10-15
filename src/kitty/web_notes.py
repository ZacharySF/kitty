"""Local-only notes API; only registered normalized PNGs are downloadable."""
from __future__ import annotations

import re
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
    if not path.startswith("/api/notes"):
        return None
    try:
        if path == "/api/notes":
            notes.keys(query, {"search"})
            return notes.listing(conn, query.get("search", ""))
        match = re.fullmatch(r"/api/notes/pages/([1-9][0-9]{0,15})(/export)?", path)
        if match:
            notes.keys(query, set())
            if match[2]:
                return notes.export_page(conn, app.settings.data_dir, int(match[1]))
            return notes.page(conn, int(match[1]))
        return None
    except notes.NotesError as exc:
        raise _error(exc) from None


def post(app, conn, path, body):
    if not path.startswith("/api/notes"):
        return None
    try:
        if path == "/api/notes/notebooks":
            notes.keys(body, {"title", "subject", "project"}, {"title"})
            return notes.create_notebook(conn, body["title"], body.get("subject", ""), body.get("project", ""))
        match = re.fullmatch(r"/api/notes/notebooks/([1-9][0-9]{0,15})", path)
        if match:
            notes.keys(body, {"revision", "title", "subject", "project"}, {"revision", "title", "subject", "project"})
            return notes.save_notebook(conn, int(match[1]), body["revision"], body["title"], body["subject"], body["project"])
        if path == "/api/notes/pages":
            notes.keys(body, {"notebook_id", "title"}, {"notebook_id"})
            return notes.create_page(conn, body["notebook_id"], body.get("title", "Untitled page"))
        match = re.fullmatch(r"/api/notes/pages/([1-9][0-9]{0,15})(/viewport|/delete)?", path)
        if match:
            pid = int(match[1])
            if match[2] == "/viewport":
                notes.keys(body, {"revision", "viewport"}, {"revision", "viewport"})
                return notes.save_viewport(conn, pid, body["revision"], body["viewport"])
            if match[2] == "/delete":
                notes.keys(body, {"revision", "confirm"}, {"revision", "confirm"})
                if body["confirm"] is not True:
                    raise notes.NotesError("Confirm page deletion")
                return notes.delete_page(conn, pid, body["revision"])
            notes.keys(body, {"revision", "title", "document"}, {"revision", "title", "document"})
            return notes.save_page(conn, pid, body["revision"], body["document"], body["title"])
        if path == "/api/notes/attachments":
            notes.keys(body, {"data", "name"}, {"data", "name"})
            return _local_import(app, "notes-reference-import", lambda local: {"attachments": notes.register(local, app.settings.data_dir, body["data"], body["name"])})
        if path == "/api/notes/import":
            notes.keys(body, {"notebook_id", "page"}, {"notebook_id", "page"})
            return _local_import(app, "notes-page-restore", lambda local: notes.restore(local, app.settings.data_dir, body["notebook_id"], body["page"]))
        if path == "/api/notes/preferences":
            notes.keys(body, {"revision", "allow_ai", "retention_days"}, {"revision", "allow_ai", "retention_days"})
            return notes.preferences(conn, body["revision"], body["allow_ai"], body["retention_days"])
        if path == "/api/notes/cleanup":
            notes.keys(body, {"confirm"}, {"confirm"})
            if body["confirm"] is not True:
                raise notes.NotesError("Confirm unused reference cleanup")
            return notes.cleanup(conn, app.settings.data_dir)
        return None
    except notes.NotesError as exc:
        raise _error(exc) from None


def download(app, conn, path, query):
    match = re.fullmatch(r"/api/notes/attachments/([a-f0-9]{32})", path)
    if not match:
        return None
    try:
        notes.keys(query, set())
        return notes.attachment_bytes(conn, app.settings.data_dir, match[1]), "image/png", None
    except notes.NotesError as exc:
        raise _error(exc) from None
