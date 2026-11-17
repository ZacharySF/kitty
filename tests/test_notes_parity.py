"""Notes and tag routes: the Rust front against the retained Python core.

The reference owns the deleted handlers' glue (web_notes.py's moved routes, web_tags.py),
not imports of those handlers. One front, two copies of one seeded database, a file clock
and deterministic UUIDs make answers, block rows, Loro bytes, the build file and the
reference files comparable. The registered references live under the workspace's data
folder (their rows hold absolute paths): it is copied before the reference runs and put
back before the front runs. No transaction auto-tags several blocks or mentions one target
several times (Python's set order is not a portable contract).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from terminal_support import RIKA_SERVER, front_site
from test_blocks_parity import Clock, base_time, deterministic_uuids, loro_view

from kitty.core import block_api, concept_maps, note_blocks, notes, record_events
from kitty.core.blocks import crdt, store, tags
from kitty.core.builds import project
from kitty.db import connect
from kitty.modules import MODULE_IDS, ModuleDisabledError
from kitty.web_server import APIError, WebApplication

MISSING = "ffffffff-ffff-4fff-bfff-ffffffffffff"
NOTES_GETS = ("/api/notes", "/api/notes/tags", "/api/notes/topics")
TAG_POSTS = ("settings", "create", "update", "delete", "link-all", "attach", "remove")


class FrozenDatetime(datetime):
    """`datetime` on the scenario's clock (`now()` local and naive, `now(tz)` aware)."""

    @classmethod
    def now(cls, tz=None):
        value = Clock.value
        return value.astimezone(tz) if tz is not None else value.astimezone().replace(tzinfo=None)


def _offset(query):
    value = query.get("offset", "0")
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 8:
        raise notes.NotesError("Offset must be a nonnegative integer")
    return int(value)


def _tag_offset(value):
    if value is None:
        return 0
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 8:
        raise store.BlockError("Offset must be a whole number")
    return int(value)


def _expect(body, keys):
    if set(body) != keys:
        raise store.BlockError("Expected {" + ", ".join(f'"{key}"' for key in sorted(keys)) + "}")


def _number(path, prefix, suffix=""):
    """The `[1-9][0-9]{0,15}` page, folder or notebook number of a path, or None."""
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    text = path[len(prefix):len(path) - len(suffix)]
    if not (1 <= len(text) <= 16 and text.isascii() and text.isdigit() and text[0] != "0"):
        return None
    return int(text)


class Reference:
    def __init__(self, db_path, data_dir, root, modules=MODULE_IDS):
        self.conn, self.data_dir, self.root, self.modules = connect(db_path), data_dir, root, modules

    def notes_get(self, path, query):
        conn = self.conn
        for suffix, reader in (("/blocks", note_blocks.get_blocks), ("/mentions", note_blocks.mentions),
                               ("/map", concept_maps.page_map)):
            number = _number(path, "/api/notes/pages/", suffix)
            if number is not None:
                notes.keys(query, set())
                return reader(conn, number)
        if path.startswith("/api/notes/blocks/") and path.endswith("/contexts"):
            bid = path.removeprefix("/api/notes/blocks/").removesuffix("/contexts")
            if len(bid) == 36 and all(c in "0123456789abcdef-" for c in bid):
                notes.keys(query, {"offset"})
                return note_blocks.contexts(conn, bid, _offset(query))
        if path == "/api/notes/topics":
            notes.keys(query, {"kind", "topic", "offset"}, {"kind", "topic"})
            return note_blocks.topics(conn, query["kind"], query["topic"], _offset(query))
        if path == "/api/notes/tags":
            notes.keys(query, set())
            return notes.tags_summary(conn)
        if path == "/api/notes":
            notes.keys(query, {"search"})
            return notes.listing(conn, query.get("search", ""))
        for suffix in ("/export", ""):
            number = _number(path, "/api/notes/pages/", suffix)
            if number is not None:
                notes.keys(query, set())
                return notes.export_page(conn, self.data_dir, number) if suffix else notes.page(conn, number)
        raise APIError(404, "API route not found")

    def notes_post(self, path, body):
        conn = self.conn
        number = _number(path, "/api/notes/pages/", "/map")
        if number is not None:
            notes.keys(body, set())
            try:
                return concept_maps.create_page_map(conn, number)
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
        number = _number(path, "/api/notes/pages/", "/organize")
        if number is not None:
            return notes.organize_page(conn, number, body)
        if path == "/api/notes/folders":
            notes.keys(body, {"title", "parent_id"}, {"title"})
            return notes.create_folder(conn, body["title"], body.get("parent_id"))
        for suffix in ("/delete", ""):
            number = _number(path, "/api/notes/folders/", suffix)
            if number is not None:
                if suffix:
                    notes.keys(body, {"revision", "confirm"}, {"revision", "confirm"})
                    if body["confirm"] is not True:
                        raise notes.NotesError("Confirm folder deletion")
                    return notes.delete_folder(conn, number, body["revision"])
                notes.keys(body, {"revision", "title", "parent_id"}, {"revision", "title", "parent_id"})
                return notes.save_folder(conn, number, body["revision"], body["title"], body["parent_id"])
        if path == "/api/notes/notebooks":
            notes.keys(body, {"title", "subject", "project", "folder_id"}, {"title"})
            return notes.create_notebook(conn, body["title"], body.get("subject", ""), body.get("project", ""), body.get("folder_id"))
        number = _number(path, "/api/notes/notebooks/")
        if number is not None:
            notes.keys(body, {"revision", "title", "subject", "project", "folder_id"}, {"revision", "title", "subject", "project"})
            folder = {"folder_id": body["folder_id"]} if "folder_id" in body else {}
            return notes.save_notebook(conn, number, body["revision"], body["title"], body["subject"], body["project"], **folder)
        if path == "/api/notes/pages":
            notes.keys(body, {"notebook_id", "title"}, {"notebook_id"})
            return notes.create_page(conn, body["notebook_id"], body.get("title", "Untitled page"))
        for suffix in ("/viewport", "/delete", ""):
            number = _number(path, "/api/notes/pages/", suffix)
            if number is None:
                continue
            if suffix == "/viewport":
                notes.keys(body, {"revision", "viewport"}, {"revision", "viewport"})
                return notes.save_viewport(conn, number, body["revision"], body["viewport"])
            if suffix == "/delete":
                notes.keys(body, {"revision", "confirm"}, {"revision", "confirm"})
                if body["confirm"] is not True:
                    raise notes.NotesError("Confirm page deletion")
                return notes.delete_page(conn, number, body["revision"])
            notes.keys(body, {"revision", "title", "document"}, {"revision", "title", "document"})
            return notes.save_page(conn, number, body["revision"], body["document"], body["title"])
        if path == "/api/notes/preferences":
            notes.keys(body, {"revision", "allow_ai", "retention_days"}, {"revision", "allow_ai", "retention_days"})
            return notes.preferences(conn, self.root, body["revision"], body["allow_ai"], body["retention_days"])
        if path == "/api/notes/cleanup":
            notes.keys(body, {"confirm"}, {"confirm"})
            if body["confirm"] is not True:
                raise notes.NotesError("Confirm unused reference cleanup")
            return notes.cleanup(conn, self.data_dir)
        raise APIError(404, "API route not found")

    def tags_get(self, path, query):
        conn = self.conn
        if path == "/api/tags":
            if query:
                raise APIError(400, "This route does not accept query parameters")
            return tags.list_tags(conn)
        if path == "/api/tags/page":
            if set(query) - {"id", "offset"} or "id" not in query:
                raise store.BlockError("Name the tag page with id")
            return tags.page(conn, query["id"], _tag_offset(query.get("offset")))
        if path == "/api/tags/find":
            if set(query) != {"name"}:
                raise store.BlockError("Name the tag with name")
            return {"id": tags.find(conn, query["name"])}
        raise APIError(404, "API route not found")

    def tags_post(self, path, body):
        conn = self.conn
        action = path.removeprefix("/api/tags/")
        if action == "settings":
            _expect(body, {"mode", "revision"})
            return tags.set_mode(conn, self.root, body["mode"], body["revision"])
        if action == "create":
            return tags.create(conn, body)
        if action == "update":
            if "id" not in body:
                raise store.BlockError("Name the tag with id")
            fields = dict(body)
            return tags.update(conn, fields.pop("id"), fields)
        if action in {"delete", "link-all"}:
            _expect(body, {"id"})
            return (tags.delete if action == "delete" else tags.link_all)(conn, body["id"])
        if action in {"attach", "remove"}:
            _expect(body, {"block", "tag"})
            return (tags.attach if action == "attach" else tags.remove)(conn, body["block"], body["tag"])
        raise APIError(404, "API route not found")

    def __call__(self, path, body, now, query):
        Clock.value = now
        try:
            if body is not None and query:
                raise APIError(400, "This route does not accept query parameters")
            if "workspace" not in self.modules:
                raise APIError(403, str(ModuleDisabledError("workspace")))
            if body is None and re.fullmatch(r"/api/notes/attachments/[a-f0-9]{32}", path):
                notes.keys(query, set())
                return 200, notes.attachment_bytes(self.conn, self.data_dir, path.rsplit("/", 1)[1])
            tagging = path == "/api/tags" or path.startswith("/api/tags/")
            if body is None:
                answer = (self.tags_get if tagging else self.notes_get)(path, query)
            else:
                before = record_events.op_log_mark(self.conn)
                answer = (self.tags_post if tagging else self.notes_post)(path, body)
                record_events.capture(self.conn, path, body, answer, op_log_before=before)
        except (notes.NotesError, store.BlockError, APIError) as exc:
            return exc.status, {"error": str(exc)}
        except Exception:
            return 500, {"error": "Local operation failed; inspect CLI diagnostics"}
        return 200, json.loads(json.dumps(answer))


class Through:
    def __init__(self, site, clock):
        self.site, self.clock = site, clock

    def __call__(self, path, body, now, query):
        self.clock.write_text(now.isoformat(), encoding="utf-8")
        return self.site.request(path + ("?" + urlencode(query) if query else ""), body)


def png(width, height, color):
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


def element(eid, kind, layer="default", **extra):
    item = {"id": eid, "kind": kind, "layer": layer, "x": 10, "y": 20.5, "w": 30, "h": 40, "color": "#112233", "width": 2}
    item.update(extra)
    return item


def canvas(elements, **extra):
    return {"version": 1, "grid": True, "snap": False, "background": "lined",
            "layers": [{"id": "default", "name": "Drawing", "visible": True, "locked": False},
                       {"id": "ink-2", "name": "Überlay ✏️", "visible": True, "locked": True}],
            "elements": elements, **extra}


def seed(conn, base, data_dir, root, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(store, "now", Clock.now)
        patch.setattr(notes, "now", Clock.now)
        project.sync(conn, root)
        Clock.value = base - timedelta(days=40)
        old = notes.register(conn, data_dir, png(3, 2, (255, 0, 0, 255)), "Old unused")
        kept = notes.register(conn, data_dir, png(4, 4, (0, 0, 255, 128)), "Kept reference")
        Clock.value = base
        recent = notes.register(conn, data_dir, png(2, 2, (0, 255, 0, 255)), "Recent unused")
        folder = notes.create_folder(conn, "Science")
        child = notes.create_folder(conn, "Physics", folder["id"])
        book = notes.create_notebook(conn, "Parity notebook", "Optics", "Quasar project", folder["id"])
        other = notes.create_notebook(conn, "Second book")
        first = block_api.create_page(conn, "Quasar target", notebook=book["id"], blocks=["A😀B café Straße", "movable leaf"])
        leaf = first["blocks"][0]["id"]
        second = block_api.create_page(conn, "Destination", notebook=book["id"])
        linked = block_api.insert_blocks(conn, [f"[[Quasar target|alias]] (({leaf})) {{{{embed:{leaf}}}}} #cosmos"], page=second["page_id"])
        block_api.insert_blocks(conn, ["Unlinked Quasar target mention with 𐐀 and İ", "a nebula photon here",
                                       "Straße ODER strasse photonics"], page=second["page_id"])
        block_api.insert_blocks(conn, [f"cycle (({leaf}))", "#cosmos tagged twice"], page=first["page_id"])
        always = tags.create(conn, {"name": "Cosmos", "mode": "always", "keywords": ["nebula"], "aliases": ["cosmic"]})
        suggest = tags.create(conn, {"name": "Optics", "mode": "suggest", "keywords": ["photon"], "aliases": ["lightwave"]})
        unicode_tag = tags.create(conn, {"name": "Straße", "keywords": ["ǅungla"], "short": True})
        tags.attach(conn, linked["blocks"][0]["id"], always["id"])
        drawing = notes.create_page(conn, book["id"], "Drawing page")
        saved = notes.save_page(conn, drawing["id"], drawing["revision"], canvas([
            element("pen-1", "pen", points=[[0, 0, 0.5], [1, 2, 1]]),
            element("pen-2", "highlighter", points=[[3, 4, 0.25]], x=-5, w=1),
            element("card", "sticky", text="Sticky card", tags=["todo", "todo", "question"], checked=True, fontSize=18),
            element("img", "image", attachment=kept[0]["id"]),
        ]), "Drawing page")
        notes.save_viewport(conn, drawing["id"], 1, {"x": 5, "y": -3.5, "zoom": 1.25})
        conn.commit()
    return {"first": first, "second": second, "leaf": leaf, "folder": folder, "child": child, "book": book, "other": other,
            "always": always["id"], "suggest": suggest["id"], "unicode": unicode_tag["id"], "drawing": drawing["id"],
            "drawing_revision": saved["revision"], "old": old[0]["id"], "kept": kept[0]["id"], "recent": recent[0]["id"],
            "linked": linked["blocks"][0]["id"]}


def scenario(call, seeded, base, modules=MODULE_IDS):
    answers = []
    allowed = "workspace" in modules
    now = base

    def do(path, body=None, expect=200, **query):
        nonlocal now
        now += timedelta(seconds=1)
        status, answer = call(path, body, now, query)
        if not allowed and not (body is not None and query):
            expect = 403
        assert status == expect, (len(answers), path, body, query, status, answer)
        answers.append((path, status, answer))
        return answer

    first, second = seeded["first"]["number"], seeded["second"]["number"]
    drawing, leaf = seeded["drawing"], seeded["leaf"]
    page_path = f"/api/notes/pages/{drawing}"

    # Reads and their refusals.
    do("/api/notes")
    for search in ("", " ", "quasar", "Parity", "optics", "%_\\", "Straße"):
        do("/api/notes", search=search)
    do("/api/notes", expect=400, search="x" * 301)
    do("/api/notes", expect=400, extra="1")
    do("/api/notes/tags")
    do("/api/notes/tags", expect=400, q="1")
    for number in (first, second, drawing):
        for suffix in ("", "/blocks", "/mentions", "/map", "/export"):
            do(f"/api/notes/pages/{number}{suffix}")
    for suffix in ("", "/blocks", "/mentions", "/map", "/export"):
        do(f"/api/notes/pages/999999{suffix}", expect=404)
        do(f"/api/notes/pages/9999999999999999{suffix}", expect=400)
        do(f"/api/notes/pages/{first}{suffix}", expect=400, q="1")
    for path in ("/api/notes/pages/99999999999999999", "/api/notes/pages/0", f"/api/notes/pages/{first}/viewport",
                 "/api/notes/pages", "/api/notes/unknown", "/api/notes/blocks/x/contexts"):
        do(path, expect=404)
    for bid, status in ((leaf, 200), (MISSING, 200), ("0123456789abcdef0123456789abcdef----", 400),
                        ("01234567-89ab-cdef-0123-456789abcd--", 400)):
        do(f"/api/notes/blocks/{bid}/contexts", expect=status)
    do(f"/api/notes/blocks/{leaf}/contexts", offset="1")
    for offset, status in (("abc", 400), ("99999999", 400), ("-1", 400), ("1000001", 400), ("0", 200)):
        do(f"/api/notes/blocks/{leaf}/contexts", expect=status, offset=offset)
    do(f"/api/notes/blocks/{leaf}/contexts", expect=400, other="1")
    for kind, topic in (("page", "Quasar target"), ("page", " QUASAR TARGET "), ("tag", "cosmos"), ("tag", "COSMOS"), ("tag", "none")):
        do("/api/notes/topics", kind=kind, topic=topic)
    do("/api/notes/topics", kind="tag", topic="cosmos", offset="1")
    for query in ({"kind": "bad", "topic": "x"}, {"kind": "tag"}, {"topic": "x"}, {"kind": "tag", "topic": "x" * 201},
                  {"kind": "tag", "topic": "x", "offset": "x"}, {"kind": "tag", "topic": "x", "extra": "1"}):
        do("/api/notes/topics", expect=400, **query)
    for attachment, status in ((seeded["kept"], 200), (seeded["old"], 200), ("0" * 32, 404)):
        do(f"/api/notes/attachments/{attachment}", expect=status)
    do(f"/api/notes/attachments/{seeded['kept']}", expect=400, q="1")
    do("/api/notes/attachments/NOTHEX", expect=404)

    # Tag reads.
    do("/api/tags")
    do("/api/tags", expect=400, q="1")
    for tag in (seeded["always"], seeded["suggest"], seeded["unicode"]):
        do("/api/tags/page", id=tag)
        do("/api/tags/page", id=tag, offset="1")
    for query, status in (({}, 400), ({"id": "bad"}, 400), ({"id": MISSING}, 404), ({"id": seeded["always"], "x": "1"}, 400),
                          ({"id": seeded["always"], "offset": "x"}, 400), ({"offset": "1"}, 400)):
        do("/api/tags/page", expect=status, **query)
    for name in ("cosmos", "COSMIC", "  Cosmos ", "nebula", "strasse", "x" * 101, ""):
        do("/api/tags/find", expect=400 if len(name) > 100 else 200, name=name)
    do("/api/tags/find", expect=400)
    do("/api/tags/find", expect=400, name="x", other="1")
    do("/api/tags/unknown", expect=404)

    # Folders and notebooks.
    folder, child = seeded["folder"], seeded["child"]
    made = do("/api/notes/folders", {"title": "Ünicode folder 📚", "parent_id": folder["id"]})
    for body, status in (({"title": ""}, 400), ({"title": "  "}, 400), ({"title": "x" * 201}, 400), ({"title": "é" * 101}, 400),
                         ({"title": 5}, 400), ({"title": "nul\x00"}, 400), ({"title": "x", "parent_id": 999999}, 404),
                         ({"title": "x", "parent_id": True}, 400), ({"title": "x", "parent_id": "1"}, 400),
                         ({"title": "x", "extra": 1}, 400), ({}, 400)):
        do("/api/notes/folders", body, expect=status)
    folder_id = made["id"] if allowed else 999
    path = f"/api/notes/folders/{folder_id}"
    for body, status in (({"revision": 1, "title": "Moved", "parent_id": None}, 200),
                         ({"revision": 1, "title": "Stale", "parent_id": None}, 409),
                         ({"revision": 2, "title": "", "parent_id": None}, 400),
                         ({"revision": 2, "title": "x"}, 400),
                         ({"revision": True, "title": "x", "parent_id": None}, 400),
                         ({"revision": 2, "title": "x", "parent_id": 999999}, 404)):
        do(path, body, expect=status)
    do(f"/api/notes/folders/{folder['id']}", {"revision": 1, "title": "Loop", "parent_id": child["id"]}, expect=400)
    do(f"/api/notes/folders/{folder['id']}", {"revision": 1, "title": "Self", "parent_id": folder["id"]}, expect=400)
    do("/api/notes/folders/999999", {"revision": 1, "title": "x", "parent_id": None}, expect=404)
    do("/api/notes/folders/9999999999999999", {"revision": 1, "title": "x", "parent_id": None}, expect=400)
    for body, status in (({"revision": 2, "confirm": False}, 400), ({"revision": 2}, 400), ({"revision": 1, "confirm": True}, 409),
                         ({"revision": 2, "confirm": True}, 200), ({"revision": 2, "confirm": True}, 404)):
        do(path + "/delete", body, expect=status)
    do(f"/api/notes/folders/{folder['id']}/delete", {"revision": 2, "confirm": True}, expect=409)
    book = do("/api/notes/notebooks", {"title": "Third book", "subject": "Maths", "project": "", "folder_id": child["id"]})
    for body, status in (({"title": ""}, 400), ({"title": "x", "subject": None}, 400), ({"title": "x", "folder_id": 999999}, 404),
                         ({"title": "x", "project": "é" * 101}, 400), ({"subject": "x"}, 400)):
        do("/api/notes/notebooks", body, expect=status)
    book_id = book["id"] if allowed else 999
    for body, status in (({"revision": 1, "title": "Renamed", "subject": "S", "project": "P"}, 200),
                         ({"revision": 1, "title": "Stale", "subject": "", "project": ""}, 409),
                         ({"revision": 2, "title": "Moved", "subject": "", "project": "", "folder_id": None}, 200),
                         ({"revision": 3, "title": "Into", "subject": "", "project": "", "folder_id": folder["id"]}, 200),
                         ({"revision": 4, "title": "x", "subject": "", "project": "", "folder_id": 999999}, 404),
                         ({"revision": 4, "title": " ", "subject": "", "project": ""}, 400),
                         ({"revision": 4, "title": "x", "subject": ""}, 400),
                         ({"revision": "4", "title": "x", "subject": "", "project": ""}, 400)):
        do(f"/api/notes/notebooks/{book_id}", body, expect=status)
    do("/api/notes/notebooks/999999", {"revision": 1, "title": "x", "subject": "", "project": ""}, expect=409)

    # Pages: create, canvas saves and their refusals, viewport, organize, order.
    created = do("/api/notes/pages", {"notebook_id": book_id, "title": "Fresh canvas"})
    do("/api/notes/pages", {"notebook_id": seeded["other"]["id"]})
    for body, status in (({"notebook_id": 999999}, 404), ({"notebook_id": "1"}, 400), ({"notebook_id": book_id, "title": ""}, 400),
                         ({"title": "x"}, 400), ({"notebook_id": book_id, "title": "x" * 201}, 400)):
        do("/api/notes/pages", body, expect=status)
    fresh = created["id"] if allowed else 999
    document = canvas([
        element("s1", "pen", points=[[0, 0, 0.5], [10, 10, 1]]),
        element("s2", "pen", points=[[5, 5, 0.1]], x=-20, y=-1, w=5, h=1),
        element("s3", "highlighter", layer="ink-2", points=[[1, 1, 1]]),
        element("t1", "text", text="Hello 🌍 #notatag", fontSize=24, tags=["definition"]),
        element("st", "sticky", text="", checked=False, group="g-1"),
        element("r1", "rect", fill="#ffffff", text="ignored", fontSize=99),
        element("e1", "ellipse", group=""),
        element("l1", "line", points=[[0, 0, 0], [1, 1, 1]]),
        element("a1", "arrow", points=[[0, 0, 0.5], [2.5, 3, 0]]),
        element("c1", "connector", points=[[0, 0, 0], [1, 1, 0]], **{"from": "t1", "to": "st"}),
        element("im", "image", attachment=seeded["kept"]),
        element("s4", "pen", points=[[7, 7, 0.7]]),
    ])
    saved = do(f"/api/notes/pages/{fresh}", {"revision": 1, "title": "Fresh canvas", "document": document})
    do(f"/api/notes/pages/{fresh}")
    revision = saved["revision"] if allowed else 2
    reordered = canvas([
        element("t1", "text", text="Hello again", fontSize=24),
        element("s1", "pen", points=[[0, 0, 0.5], [10, 10, 1]]),
        element("new", "sticky", text="Added card 😀", tags=["important"]),
        element("r1", "rect", fill="none", x=11),
        element("s3", "highlighter", layer="ink-2", points=[[1, 1, 1]]),
        element("l1", "line", points=[[0, 0, 0], [1, 1, 1]]),
    ], grid=False, snap=True, background="grid")
    saved = do(f"/api/notes/pages/{fresh}", {"revision": revision, "title": "Renamed canvas", "document": reordered})
    revision = saved["revision"] if allowed else revision + 1
    do(f"/api/notes/pages/{fresh}")
    do(f"/api/notes/pages/{fresh}", {"revision": revision, "title": "Restored", "document": document})
    revision += 1
    do(f"/api/notes/pages/{fresh}", {"revision": revision, "title": "Restored", "document": document})
    revision += 1
    do(f"/api/notes/pages/{fresh}")
    do(f"/api/notes/pages/{fresh}/export")
    stale = {"revision": 1, "title": "Stale", "document": canvas([])}
    do(f"/api/notes/pages/{fresh}", stale, expect=409)
    good = canvas([element("ok", "rect")])
    bad_documents = [
        [], {"version": 1}, {**good, "version": 2}, {**good, "version": True}, {**good, "version": 1.0}, {**good, "extra": 1},
        {**good, "layers": []}, {**good, "layers": "x"}, {**good, "elements": [element(f"e{i}", "rect") for i in range(2001)]},
        {**good, "layers": [{"id": "a", "name": "A", "visible": 1, "locked": False}]},
        {**good, "layers": [{"id": "a b", "name": "A", "visible": True, "locked": False}]},
        {**good, "layers": [{"id": "a", "name": "A" * 101, "visible": True, "locked": False}]},
        {**good, "layers": [{"id": "a", "name": "A", "visible": True, "locked": False}] * 2},
        {**good, "layers": [{"id": "a", "name": "A", "visible": True}]},
        canvas([element("x", "rect"), element("x", "rect")]), canvas([element("x" * 81, "rect")]),
        canvas([element("x", "blob")]), canvas([element("x", "rect", layer="nope")]), canvas([element("x", ["rect"])]),
        canvas([element("x", "rect", color="#12345")]), canvas([element("x", "rect", fill="red")]), canvas([element("x", "rect", fill=None)]),
        canvas([element("x", "rect", x=True)]), canvas([element("x", "rect", x="1")]), canvas([element("x", "rect", y=1e7)]),
        canvas([element("x", "rect", w=-1)]), canvas([element("x", "rect", width=0)]), canvas([element("x", "rect", width=101)]),
        canvas([element("x", "rect", group="bad id")]), canvas([element("x", "rect", unknown=1)]), canvas([{"id": "x", "kind": "rect"}]),
        canvas([element("x", "pen")]), canvas([element("x", "pen", points=[])]), canvas([element("x", "pen", points=[[1, 2]])]),
        canvas([element("x", "pen", points=[[1, 2, 2]])]), canvas([element("x", "pen", points=[[0, 0, 0]] * 20001)]),
        canvas([element(f"p{i}", "pen", points=[[0, 0, 0]] * 20000) for i in range(6)]),
        canvas([element("x", "text", text=5)]), canvas([element("x", "text", text="é" * 10001)]),
        canvas([element("x", "text", fontSize=7)]), canvas([element("x", "text", tags=["nope"])]),
        canvas([element("x", "text", tags=["todo"] * 5)]), canvas([element("x", "text", checked=1)]),
        canvas([element("x", "image")]), canvas([element("x", "image", attachment="f" * 32)]),
        canvas([element("x", "line")]), canvas([element("x", "line", points=[[0, 0, 0]])]),
        canvas([element("x", "line", points=[[0, 0, 0], "bad"])]), canvas([element("x", "arrow", points=[[0, 0, 2], [0, 0, 0]])]),
        canvas([element("x", "connector", points=[[0, 0, 0], [1, 1, 1]])]),
        canvas([element("x", "connector", points=[[0, 0, 0], [1, 1, 1]], **{"from": "x", "to": "y"})]),
        canvas([element("a", "rect"), element("x", "connector", points=[[0, 0, 0], [1, 1, 1]], **{"from": "a", "to": "a"})]),
        {**good, "grid": 1}, {**good, "snap": None}, {**good, "background": "stars"}, {**good, "background": None},
        canvas([element(f"t{i}", "text", text="é" * 9000) for i in range(130)]),
    ]
    for bad in bad_documents:
        do(f"/api/notes/pages/{fresh}", {"revision": revision, "title": "Bad", "document": bad}, expect=400)
    for body, status in (({"revision": revision, "title": "", "document": good}, 400),
                         ({"revision": 0, "title": "x", "document": good}, 400),
                         ({"revision": revision, "title": "x"}, 400),
                         ({"revision": revision, "title": 1, "document": good}, 400)):
        do(f"/api/notes/pages/{fresh}", body, expect=status)
    do("/api/notes/pages/999999", {"revision": 1, "title": "x", "document": good}, expect=404)
    do("/api/notes/pages/9999999999999999", {"revision": 1, "title": "x", "document": good}, expect=400)
    do(f"/api/notes/pages/{fresh}/blocks", {"revision": 1, "blocks": []}, expect=404)
    do(f"{page_path}/viewport", {"revision": 2, "viewport": {"x": 1, "y": 2.5, "zoom": 0.05}})
    for body, status in (({"revision": 2, "viewport": {"x": 1, "y": 2, "zoom": 1}}, 409),
                         ({"revision": 3, "viewport": {"x": 1, "y": 2, "zoom": 21}}, 400),
                         ({"revision": 3, "viewport": {"x": 1, "y": 2}}, 400),
                         ({"revision": 3, "viewport": [1, 2, 3]}, 400),
                         ({"revision": True, "viewport": {"x": 1, "y": 2, "zoom": 1}}, 400),
                         ({"revision": 3}, 400)):
        do(f"{page_path}/viewport", body, expect=status)
    do("/api/notes/pages/999999/viewport", {"revision": 1, "viewport": {"x": 1, "y": 2, "zoom": 1}}, expect=404)
    for body in ({"favorite": True, "visited": True}, {"favorite": False}, {"visited": False}, {}):
        do(f"{page_path}/organize", body)
    for body, status in (({"favorite": 1}, 400), ({"pinned": True}, 400)):
        do(f"{page_path}/organize", body, expect=status)
    do("/api/notes/pages/999999/organize", {"favorite": True}, expect=404)
    do("/api/notes/pages/9999999999999999/organize", {"pinned": True}, expect=400)
    do("/api/notes/reorder", {"kind": "pages", "ids": [drawing, first, second]})
    do("/api/notes/reorder", {"kind": "notebooks", "ids": [seeded["other"]["id"], seeded["book"]["id"]]})
    do("/api/notes/reorder", {"kind": "folders", "ids": [child["id"]]})
    do("/api/notes/reorder", {"kind": "pages", "ids": []})
    for body, status in (({"kind": "pages", "ids": [first, first]}, 400), ({"kind": "pages", "ids": [999999]}, 404),
                         ({"kind": "notebooks", "ids": [999999]}, 404), ({"kind": "tags", "ids": []}, 400),
                         ({"kind": "pages", "ids": "1"}, 400), ({"kind": "pages", "ids": [True]}, 400),
                         ({"kind": "pages", "ids": list(range(1, 502))}, 400), ({"kind": "pages"}, 400)):
        do("/api/notes/reorder", body, expect=status)
    do("/api/notes")

    # Daily and resolve.
    do("/api/notes/daily", {"date": "2026-03-04"})
    do("/api/notes/daily", {"date": "2026-03-04"})
    do("/api/notes/daily", {})
    do("/api/notes/daily", {"date": None})
    for date in ("2026-3-04", "2026-02-30", "0000-01-01", "2026-13-01", 20260304, "2026-03-04T00:00", "２０２６-03-0４", "２０２６-03-1５"):
        do("/api/notes/daily", {"date": date}, expect=200 if date == "２０２６-03-1５" else 400)
    do("/api/notes/daily", {"day": "2026-03-04"}, expect=400)
    do("/api/notes/resolve", {"title": "  quasar TARGET "})
    do("/api/notes/resolve", {"title": "Brand new resolved page"})
    do("/api/notes/resolve", {"title": "Into another book", "notebook_id": seeded["other"]["id"]})
    for body, status in (({"title": " "}, 400), ({"title": "x", "notebook_id": 999999}, 404), ({"title": "y", "notebook_id": "1"}, 400),
                         ({}, 400)):
        do("/api/notes/resolve", body, expect=status)

    # Mind maps of pages.
    do(f"/api/notes/pages/{first}/map")
    made_map = do(f"/api/notes/pages/{first}/map", {})
    again = do(f"/api/notes/pages/{first}/map", {})
    if allowed:
        assert made_map == again and made_map["map_id"]
    do(f"/api/notes/pages/{first}/map")
    do("/api/notes/pages/999999/map", {}, expect=404)
    do(f"/api/notes/pages/{first}/map", {"x": 1}, expect=400)

    # Tag writes.
    tag = do("/api/tags/create", {"name": "  New   tag ", "keywords": ["Alpha", "alpha", " beta  gamma ", ""], "aliases": ["Nouveau"],
                                  "mode": "always", "short": True})
    tag_id = tag["id"] if allowed else MISSING
    for body, status in (({"name": "cosmic"}, 409), ({"name": "It's \"quoted\""}, 200), ({"name": "It's \"quoted\""}, 409),
                         ({"name": "x", "aliases": ["NEW TAG"]}, 409), ({"name": ""}, 400), ({"name": "x" * 101}, 400),
                         ({"name": "two\nlines"}, 400), ({"name": "x", "keywords": "a"}, 400), ({"name": "x", "keywords": [1]}, 400),
                         ({"name": "x", "keywords": ["k" * 101]}, 400), ({"name": "x", "aliases": ["a\nb"]}, 400),
                         ({"name": "x", "mode": "never"}, 400), ({"name": "x", "short": 1}, 400), ({"name": "x", "bogus": 1}, 400),
                         ({"keywords": []}, 500), ({"name": "x", "keywords": ["k"] * 201}, 400)):
        do("/api/tags/create", body, expect=status)
    # One removed property per operation: Python's Loro mirror deletes keys in set order.
    for body, status in (({"id": tag_id, "name": "Renamed tag", "mode": None, "aliases": []}, 200), ({"id": tag_id, "short": False}, 200),
                         ({"id": tag_id, "keywords": ["photon"]}, 200), ({"id": tag_id}, 200),
                         ({"id": tag_id, "name": "Optics"}, 409), ({"id": tag_id, "aliases": ["Lightwave"]}, 409),
                         ({"id": MISSING, "name": "x"}, 404), ({"id": "bad", "name": "x"}, 400), ({"name": "x"}, 400),
                         ({"id": tag_id, "colour": "red"}, 400), ({"id": MISSING, "name": ""}, 400)):
        do("/api/tags/update", body, expect=status)
    do("/api/tags/page", id=tag_id)
    do("/api/tags/link-all", {"id": seeded["suggest"]})
    do("/api/tags/page", id=seeded["suggest"])
    do("/api/tags/link-all", {"id": seeded["unicode"]})
    for body, status in (({"block": seeded["linked"], "tag": seeded["suggest"]}, 200), ({"block": leaf, "tag": seeded["unicode"]}, 200),
                         ({"block": seeded["second"]["page_id"], "tag": seeded["suggest"]}, 400), ({"block": MISSING, "tag": seeded["suggest"]}, 404),
                         ({"block": leaf, "tag": MISSING}, 404), ({"block": "bad", "tag": seeded["suggest"]}, 400), ({"block": leaf}, 400)):
        do("/api/tags/attach", body, expect=status)
    do("/api/tags/remove", {"block": seeded["linked"], "tag": seeded["always"]})
    do("/api/tags/page", id=seeded["always"])
    do("/api/tags/remove", {"block": seeded["linked"], "tag": seeded["suggest"], "x": 1}, expect=400)
    do("/api/tags/delete", {"id": tag_id})
    do("/api/tags/delete", {"id": tag_id}, expect=404)
    do("/api/tags/link-all", {"id": MISSING}, expect=404)
    do("/api/tags/link-all", {}, expect=400)
    do("/api/tags/unknown", {}, expect=404)
    do("/api/tags", {}, expect=404)
    for route in TAG_POSTS:
        do("/api/tags/" + route, {}, expect=400, q="1")
    mode_revision = do("/api/tags").get("revision", 1)
    for body, status in (({"mode": "always", "revision": mode_revision}, 200), ({"mode": "suggest", "revision": mode_revision}, 409),
                         ({"mode": "never", "revision": mode_revision}, 400), ({"mode": "suggest", "revision": "1"}, 400),
                         ({"mode": "suggest"}, 400), ({"mode": ["suggest"], "revision": mode_revision}, 400)):
        do("/api/tags/settings", body, expect=status)
    do("/api/tags")

    # Preferences, cleanup, page deletion.
    prefs = do("/api/notes").get("preferences", {"revision": 1})
    for body, status in (({"revision": prefs["revision"], "allow_ai": True, "retention_days": 0}, 200),
                         ({"revision": prefs["revision"], "allow_ai": False, "retention_days": 30}, 409),
                         ({"revision": prefs["revision"], "allow_ai": 1, "retention_days": 30}, 400),
                         ({"revision": prefs["revision"], "allow_ai": True, "retention_days": 3651}, 400),
                         ({"revision": prefs["revision"], "allow_ai": True}, 400)):
        do("/api/notes/preferences", body, expect=status)
    do("/api/notes")
    do("/api/notes/cleanup", {"confirm": False}, expect=400)
    do("/api/notes/cleanup", {}, expect=400)
    do("/api/notes/cleanup", {"confirm": True})
    do("/api/notes/cleanup", {"confirm": True})
    do(f"/api/notes/attachments/{seeded['old']}", expect=404)
    do(f"/api/notes/attachments/{seeded['kept']}")
    for body, status in (({"revision": 1, "confirm": True}, 409), ({"revision": 99, "confirm": False}, 400), ({"revision": 1}, 400)):
        do(f"/api/notes/pages/{fresh}/delete", body, expect=status)
    do("/api/notes/pages/999999/delete", {"revision": "x", "confirm": True}, expect=404)
    do(f"/api/notes/pages/{fresh}/delete", {"revision": "x", "confirm": True}, expect=400)
    # A page and its canvas only: Python's Loro mirror trashes a branching subtree in set order.
    brief = do("/api/notes/pages", {"notebook_id": book_id, "title": "Short-lived"})
    brief_id = brief["id"] if allowed else 999
    do(f"/api/notes/pages/{brief_id}/delete", {"revision": 1, "confirm": True})
    do(f"/api/notes/pages/{brief_id}", expect=404)
    do(f"/api/notes/pages/{fresh}/delete", {"revision": revision, "confirm": False}, expect=400)
    do("/api/notes/unknown", {}, expect=404)
    for path in ("/api/notes/folders", "/api/notes/daily", f"{page_path}/viewport"):
        do(path, {}, expect=400, q="1")
    do("/api/notes")
    do("/api/notes/tags")
    do(f"/api/notes/pages/{second}/mentions")
    do(f"/api/notes/pages/{first}/blocks")
    return answers


TABLES = ("blocks", "op_log", "page_crdt", "page_snapshots", "block_refs", "block_topics", "block_tags", "tag_rejections",
          "tag_settings", "search_docs", "search_pending", "note_notebooks", "note_folders", "note_attachments",
          "note_preferences", "note_page_maps", "schema_maps", "record_events")


def stored_rows(db_path):
    with closing(connect(db_path)) as conn:
        result = {}
        for table in TABLES:
            info = list(conn.execute(f"PRAGMA table_info({table})"))
            excluded = {"id"} if table == "search_docs" else {"at"} if table == "record_events" else set()
            columns = [column["name"] for column in info if column["name"] not in excluded]
            primary = [column["name"] for column in sorted(info, key=lambda column: column["pk"]) if column["pk"]]
            order = {"blocks": ["id"], "op_log": ["seq"], "page_crdt": ["page_id"], "search_docs": ["kind", "ref"]}.get(table, primary)
            assert columns and order, table
            result[table] = [dict(row) for row in conn.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY {','.join(order)}")]
        result["build_state"] = [dict(row) for row in conn.execute("SELECT applied_hash, applied_text FROM build_state")]
        return result


def assert_stored_equal(actual, expected):
    for table in expected:
        if table in {"op_log", "page_crdt"} and actual[table] != expected[table]:
            for index, (left, right) in enumerate(zip(actual[table], expected[table])):
                for column in left:
                    if left[column] != right[column] and isinstance(left[column], bytes):
                        assert left[column] == right[column], (table, index, column, loro_view(left[column]), loro_view(right[column]))
        assert actual[table] == expected[table], table


def files_of(folder: Path):
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())} if folder.is_dir() else {}


def prepare(workspace, tmp_path, monkeypatch, modules):
    base = base_time()
    settings = workspace.settings
    if modules != MODULE_IDS:
        (settings.root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
    seeded = seed(workspace.conn, base, settings.data_dir, settings.root, monkeypatch)
    # An orphaned raster with no row, old enough for the cleanup, and one too recent.
    folder = settings.data_dir / "notes-attachments"
    for name, age in (("a" * 32 + ".png", 41), ("b" * 32 + ".png", 0)):
        (folder / name).write_bytes(b"\x89PNG\r\n\x1a\norphan")
        stamp = (base - timedelta(days=age)).timestamp()
        os.utime(folder / name, (stamp, stamp))
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    reference_path = tmp_path / "reference.db"
    shutil.copy(settings.db_path, reference_path)
    reference_root = tmp_path / "reference-root"
    shutil.copytree(settings.root / "config", reference_root / "config")
    saved_files = tmp_path / "attachments-before"
    shutil.copytree(folder, saved_files)
    return base, seeded, reference_path, reference_root, saved_files


def reference_run(reference_path, reference_root, data_dir, seeded, base, monkeypatch, modules):
    with monkeypatch.context() as patch:
        deterministic_uuids(patch)
        patch.setattr(notes, "datetime", FrozenDatetime)
        patch.setattr(concept_maps, "datetime", FrozenDatetime)
        reference = Reference(reference_path, data_dir, reference_root, modules)
        try:
            answers = scenario(reference, seeded, base, modules)
        finally:
            reference.conn.close()
            crdt._CACHE.clear()
    return answers


def comparable(answers):
    return [(path, status, base64.b64encode(answer).decode() if isinstance(answer, bytes) else answer) for path, status, answer in answers]


@pytest.mark.parametrize("modules", [MODULE_IDS, ("applyai",)])
def test_reference_scenario(workspace, tmp_path, monkeypatch, modules):
    """Executable oracle sanity check, usable before the Rust implementation exists."""
    base, seeded, path, root, _ = prepare(workspace, tmp_path, monkeypatch, modules)
    answers = reference_run(path, root, workspace.settings.data_dir, seeded, base, monkeypatch, modules)
    assert len(answers) > 350
    if "workspace" not in modules:
        assert {status for _, status, _ in answers} == {400, 403}
        return
    # With 0 days' retention: the two unused references and the two orphaned files; then nothing.
    assert [answer["removed"] for path, status, answer in answers if path == "/api/notes/cleanup" and status == 200] == [4, 0]
    assert [answer["linked"] for path, status, answer in answers if path == "/api/tags/link-all" and status == 200] == [1, 1]


@pytest.mark.parametrize("modules", [MODULE_IDS, ("applyai",)])
def test_the_front_answers_notes_and_tags_as_the_python_core_does(workspace, tmp_path, monkeypatch, modules):
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER})")
    base, seeded, reference_path, reference_root, saved_files = prepare(workspace, tmp_path, monkeypatch, modules)
    data_dir = workspace.settings.data_dir
    folder = data_dir / "notes-attachments"
    expected = reference_run(reference_path, reference_root, data_dir, seeded, base, monkeypatch, modules)
    expected_files = files_of(folder)
    shutil.rmtree(folder)
    shutil.copytree(saved_files, folder)
    clock, counter = tmp_path / "clock", tmp_path / "uuids"
    clock.write_text(base.isoformat(), encoding="utf-8")
    counter.write_text("0", encoding="utf-8")
    with front_site(workspace.settings, env={"KITTY_TEST_CLOCK": str(clock), "KITTY_TEST_UUIDS": str(counter)}) as site:
        actual = scenario(Through(site, clock), seeded, base, modules)
    assert len(actual) == len(expected)
    for index, (answer, wanted) in enumerate(zip(comparable(actual), comparable(expected), strict=True)):
        assert answer == wanted, (index, answer[0])
    assert files_of(folder) == expected_files
    assert (workspace.settings.root / "config" / "build.json").read_text(encoding="utf-8") == \
        (reference_root / "config" / "build.json").read_text(encoding="utf-8")
    assert_stored_equal(stored_rows(workspace.settings.db_path), stored_rows(reference_path))


def test_the_worker_answers_no_moved_notes_or_tag_route(workspace, tmp_path):
    book = notes.create_notebook(workspace.conn, "Forwarded")
    page = notes.create_page(workspace.conn, book["id"], "Still Python")
    conn = workspace.conn
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        for path in ("/api/notes", "/api/notes/tags", f"/api/notes/pages/{page['id']}", f"/api/notes/pages/{page['id']}/blocks",
                     "/api/tags", "/api/tags/find"):
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(conn, path, {})
        for path in ("/api/notes/notebooks", "/api/notes/pages", "/api/notes/daily", "/api/notes/preferences", "/api/notes/cleanup",
                     "/api/tags/create", "/api/tags/settings"):
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(conn, path, {})
    finally:
        app.close()
    conn.close()
    with front_site(workspace.settings) as site:
        # Registering a reference decodes the image with Pillow on the worker, as a task.
        status, answer = site.request("/api/notes/attachments", {"data": png(2, 2, (1, 2, 3, 255)), "name": "Forwarded"})
        assert status == 200 and "task" in answer
        status, answer = site.request("/api/notes/import", {"notebook_id": book["id"], "page": {}})
        assert status == 200 and "task" in answer
