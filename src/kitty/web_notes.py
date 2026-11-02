"""Local-only notes API; only registered normalized PNGs are downloadable.

GET/POST /api/notes/pages/:id/blocks reads/replaces a bounded UUID outline.
POST requires the shared page revision; optional title/document save canvas and
outline atomically. Responses include revision, blocks, live references and
citation_counts. GET /api/notes/blocks/:uuid/contexts returns direct citations;
GET /api/notes/topics?kind=page|tag&topic=... aggregates local topic blocks.
Both endpoints accept offset and return next_offset to page through all contexts.
GET /api/notes/pages/:id/mentions returns unlinked local title mentions.
GET /api/notes/pages/:id/map returns {"map_id": id | null}, the page's mind map;
POST it with {} to create that map (the page as its root topic) when it has none.
"""
from __future__ import annotations

import re
from contextlib import closing
from kitty.core import concept_maps, notes, note_blocks
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


def _offset(query):
    value = query.get("offset", "0")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,8}", value):
        raise notes.NotesError("Offset must be a nonnegative integer")
    return int(value)


def get(app, conn, path, query):
    if not path.startswith("/api/notes"):
        return None
    try:
        blocks = re.fullmatch(r"/api/notes/pages/([1-9][0-9]{0,15})/(blocks|mentions)", path)
        if blocks:
            notes.keys(query, set())
            return note_blocks.get_blocks(conn, int(blocks[1])) if blocks[2] == "blocks" else note_blocks.mentions(conn, int(blocks[1]))
        page_map = re.fullmatch(r"/api/notes/pages/([1-9][0-9]{0,15})/map", path)
        if page_map:
            notes.keys(query, set())
            return concept_maps.page_map(conn, int(page_map[1]))
        reference = re.fullmatch(r"/api/notes/blocks/([0-9a-f-]{36})/contexts", path)
        if reference:
            notes.keys(query, {"offset"})
            return note_blocks.contexts(conn, reference[1], _offset(query))
        if path == "/api/notes/topics":
            notes.keys(query, {"kind", "topic", "offset"}, {"kind", "topic"})
            return note_blocks.topics(conn, query["kind"], query["topic"], _offset(query))
        if path == "/api/notes/tags":
            notes.keys(query, set())
            return notes.tags_summary(conn)
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
        blocks = re.fullmatch(r"/api/notes/pages/([1-9][0-9]{0,15})/blocks", path)
        if blocks:
            notes.keys(body, {"revision", "blocks", "title", "document"}, {"revision", "blocks"})
            return note_blocks.save_blocks(conn, int(blocks[1]), body["revision"], body["blocks"], body.get("title"), body.get("document"))
        page_map = re.fullmatch(r"/api/notes/pages/([1-9][0-9]{0,15})/map", path)
        if page_map:
            notes.keys(body, set())
            try:
                return concept_maps.create_page_map(conn, int(page_map[1]))
            except notes.NotesError:
                raise
            except ValueError as exc:
                raise notes.NotesError(str(exc)) from None
        if path == "/api/notes/daily":
            notes.keys(body, {"date"})
            return notes.daily_page(conn, body.get("date"))
        if path == "/api/notes/resolve":
            notes.keys(body, {"title", "notebook_id"}, {"title"})
            return notes.resolve_page(conn, body["title"], body.get("notebook_id"))
        if path == "/api/notes/reorder":
            notes.keys(body, {"kind", "ids"}, {"kind", "ids"})
            return notes.reorder(conn, body["kind"], body["ids"])
        organize = re.fullmatch(r"/api/notes/pages/([1-9][0-9]{0,15})/organize", path)
        if organize:
            return notes.organize_page(conn, int(organize[1]), body)
        if path == "/api/notes/folders":
            notes.keys(body, {"title", "parent_id"}, {"title"})
            return notes.create_folder(conn, body["title"], body.get("parent_id"))
        match = re.fullmatch(r"/api/notes/folders/([1-9][0-9]{0,15})(/delete)?", path)
        if match:
            if match[2]:
                notes.keys(body, {"revision", "confirm"}, {"revision", "confirm"})
                if body["confirm"] is not True:
                    raise notes.NotesError("Confirm folder deletion")
                return notes.delete_folder(conn, int(match[1]), body["revision"])
            notes.keys(body, {"revision", "title", "parent_id"}, {"revision", "title", "parent_id"})
            return notes.save_folder(conn, int(match[1]), body["revision"], body["title"], body["parent_id"])
        if path == "/api/notes/notebooks":
            notes.keys(body, {"title", "subject", "project", "folder_id"}, {"title"})
            return notes.create_notebook(conn, body["title"], body.get("subject", ""), body.get("project", ""), body.get("folder_id"))
        match = re.fullmatch(r"/api/notes/notebooks/([1-9][0-9]{0,15})", path)
        if match:
            notes.keys(body, {"revision", "title", "subject", "project", "folder_id"}, {"revision", "title", "subject", "project"})
            folder = {"folder_id": body["folder_id"]} if "folder_id" in body else {}
            return notes.save_notebook(conn, int(match[1]), body["revision"], body["title"], body["subject"], body["project"], **folder)
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
