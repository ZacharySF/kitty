"""Block HTTP and live transactions: the Rust front against the retained Python core.

The reference owns the deleted handlers' glue, not imports of those handlers. One
front, two copies of one seeded database, a file clock and deterministic UUIDs make
responses, operation payloads and Loro bytes comparable without masking anything.
Cross-page moves use leaves/single chains; no transaction creates several searchable
mentions of the same target or auto-tags several blocks (Python set order is not a
portable contract). Undo gives each conflicting block only one conflict reason.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from terminal_support import RIKA_SERVER, FrameReader, front_site, send_frame, websocket

from kitty.core import block_api, blocks, notes, quick_notes, record_events
from kitty.core.blocks import actor, crdt, history, sessions, store, tagging, tags
from kitty.db import connect
from kitty.db.connection import transaction
from kitty.modules import MODULE_IDS, ModuleDisabledError
from kitty.web_server import APIError, WebApplication

PREFIX = "/api/blocks/"
SOCKET = "/api/live/socket"
GETS = ("page", "history", "history/preview", "sessions", "citations", "trash", "pages", "search", "backlinks")
POSTS = ("history/restore", "sessions/undo", "trash/restore", "pages/create", "insert", "update", "move", "delete", "inbox")
QUICK = ("create", "save", "delete")
PLACE = {"page", "parent", "after", "before", "revision"}
MISSING = "ffffffff-ffff-4fff-bfff-ffffffffffff"
LIVE_PAGE = "eeeeeeee-eeee-4eee-beee-eeeeeeeeeeee"
LIVE_BLOCK = "dddddddd-dddd-4ddd-bddd-dddddddddddd"
LIVE_CHILD = "cccccccc-cccc-4ccc-bccc-cccccccccccc"
LIVE_TXN = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"


class Clock:
    value: datetime

    @classmethod
    def now(cls):
        return cls.value.isoformat()


def base_time():
    # Compaction in the worker uses real time: none of the scenario's new rows
    # may cross its 30-day retention horizon, even years after this test was written.
    return datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)


def deterministic_uuids(monkeypatch):
    counter = 0

    def uuid4():
        nonlocal counter
        data = hashlib.sha256(f"kitty-test-uuid:{counter}".encode()).digest()[:16]
        counter += 1
        return uuid.UUID(bytes=data, version=4)

    monkeypatch.setattr(uuid, "uuid4", uuid4)
    monkeypatch.setattr(store, "now", Clock.now)
    monkeypatch.setattr(notes, "now", Clock.now)
    crdt._CACHE.clear()


def _page(conn, values):
    if "page" in values:
        return blocks.block_id(values["page"])
    kind, number = values.get("type"), values.get("number")
    if isinstance(number, str) and re.fullmatch(r"[1-9][0-9]{0,15}", number):
        number = int(number)
    if kind not in {"page", "document", "quick_note"} or type(number) is not int:
        raise blocks.BlockError("Name the page as page=<block id> or type=page|document|quick_note with its number")
    row = conn.execute("SELECT id FROM blocks WHERE type=? AND parent_id IS NULL AND json_extract(properties,'$.number')=?",
                       (kind, number)).fetchone()
    if row is None:
        raise blocks.BlockError("Page not found", 404)
    return row["id"]


def _sequence(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,18}", value):
        value = int(value)
    if type(value) is not int:
        raise blocks.BlockError("Choose a sequence number from the page's history")
    return value


def _limit(query, default):
    value = query.get("limit", str(default))
    if not re.fullmatch(r"[1-9][0-9]{0,2}", value):
        raise blocks.BlockError(f"Limit must be 1-{block_api.MAX_LIST}")
    return int(value)


def _fields(body, allowed, required=frozenset()):
    if set(body) - allowed or required - set(body):
        raise blocks.BlockError(f"Expected fields: {', '.join(sorted(allowed))}")
    return body


class Reference:
    def __init__(self, db_path, modules=MODULE_IDS):
        self.conn, self.modules = connect(db_path), modules

    def get(self, path, query):
        conn = self.conn
        if path == PREFIX + "page":
            if set(query) - {"page", "type", "number"}:
                raise blocks.BlockError("Unexpected page parameters")
            with transaction(conn):
                state = blocks.page_state(conn, _page(conn, query))
                state.update(tagging.page_suggestions(conn, state))
            return state
        if path == PREFIX + "history":
            if set(query) - {"page", "type", "number", "actor", "session"}:
                raise blocks.BlockError("Unexpected history parameters")
            return history.page_history(conn, _page(conn, query), actor=query.get("actor"), session=query.get("session"))
        if path == PREFIX + "sessions":
            _fields(query, {"kind", "limit"})
            kind = query.get("kind", "agent")
            return sessions.list_sessions(conn, kind=None if kind == "all" else kind, limit=_limit(query, 50))
        if path == PREFIX + "citations":
            _fields(query, {"cites"}, {"cites"})
            return history.citations(conn, [item for item in query["cites"].split(",") if item])
        if path == PREFIX + "history/preview":
            if set(query) - {"page", "type", "number", "seq"}:
                raise blocks.BlockError("Unexpected preview parameters")
            return history.preview(conn, _page(conn, query), _sequence(query.get("seq")))
        if path == PREFIX + "trash":
            if query:
                raise blocks.BlockError("Trash takes no parameters")
            return history.trash(conn)
        if path == PREFIX + "pages":
            _fields(query, {"q", "kind", "limit"})
            return block_api.list_pages(conn, query.get("q", ""), kind=query.get("kind"), limit=_limit(query, 50))
        if path == PREFIX + "search":
            _fields(query, {"q", "limit"}, {"q"})
            return block_api.search_blocks(conn, query["q"], limit=_limit(query, 20))
        if path == PREFIX + "backlinks":
            _fields(query, {"id"}, {"id"})
            return block_api.backlinks(conn, query["id"])
        raise APIError(404, "API route not found")

    def post(self, path, body):
        conn = self.conn
        if path == PREFIX + "history/restore":
            if set(body) - {"page", "type", "number", "seq"}:
                raise blocks.BlockError("Unexpected restore fields")
            return history.restore(conn, _page(conn, body), _sequence(body.get("seq")))
        if path == PREFIX + "sessions/undo":
            _fields(body, {"session", "dry_run"}, {"session"})
            return sessions.undo_session(conn, body["session"], dry_run=body.get("dry_run", False))
        if path == PREFIX + "trash/restore":
            if set(body) != {"id"}:
                raise blocks.BlockError("Restore needs the block id from Trash")
            return history.restore_from_trash(conn, body["id"])
        if path == PREFIX + "pages/create":
            _fields(body, {"title", "notebook", "blocks"}, {"title"})
            return block_api.create_page(conn, body["title"], notebook=body.get("notebook"), blocks=body.get("blocks"))
        if path == PREFIX + "insert":
            _fields(body, {"blocks"} | PLACE, {"blocks"})
            return block_api.insert_blocks(conn, body["blocks"], **{key: body.get(key) for key in PLACE})
        if path == PREFIX + "update":
            _fields(body, {"id", "text", "type", "props", "revision"}, {"id"})
            return block_api.update_block(conn, body["id"], text=body.get("text"), kind=body.get("type"),
                                          props=body.get("props"), revision=body.get("revision"))
        if path == PREFIX + "move":
            _fields(body, {"id"} | PLACE, {"id"})
            return block_api.move_block(conn, body["id"], **{key: body.get(key) for key in PLACE})
        if path == PREFIX + "delete":
            _fields(body, {"id", "revision"}, {"id"})
            return block_api.delete_block(conn, body["id"], revision=body.get("revision"))
        if path == PREFIX + "inbox":
            _fields(body, {"blocks"}, {"blocks"})
            return block_api.add_to_inbox(conn, body["blocks"])
        fields = {"create": ({"body", "tint"}, set()),
                  "save": ({"id", "revision", "body", "tint", "pinned"}, {"id", "revision"}),
                  "delete": ({"id", "revision"}, {"id", "revision"})}
        operation = path.removeprefix("/api/quick-notes/")
        if path.startswith("/api/quick-notes/") and operation in fields:
            allowed, required = fields[operation]
            if not isinstance(body, dict) or set(body) - allowed or required - set(body):
                raise quick_notes.QuickNoteError("Unexpected or missing quick note fields")
            if operation == "create":
                return quick_notes.create(conn, body.get("body", ""), body.get("tint", "rose"))
            if operation == "save":
                return quick_notes.save(conn, body["id"], body["revision"], body=body.get("body"),
                                        tint=body.get("tint"), pinned=body.get("pinned"))
            quick_notes.delete(conn, body["id"], body["revision"])
            return {"ok": True}
        raise APIError(404, "API route not found")

    def transact(self, message):
        txn_id = message.get("txn_id") or str(uuid.uuid4())
        if "workspace" not in self.modules:
            return {"type": "reject", "txn_id": txn_id, "status": 403, "error": "Block edits need the workspace module"}
        try:
            if set(message) - {"type", "txn_id", "ops", "base"}:
                raise blocks.BlockError("Unexpected transaction fields")
            result = blocks.transact(self.conn, message.get("ops"), base=message.get("base"), txn_id=txn_id,
                                     suggest=True, actor=actor.USER)
        except blocks.BlockError as exc:
            return {"type": "reject", "txn_id": txn_id, "status": exc.status, "error": str(exc)}
        except sqlite3.OperationalError:
            return {"type": "reject", "txn_id": txn_id, "status": 503,
                    "error": "Local workspace is busy; send the transaction again"}
        return {"type": "ack", **result}

    def __call__(self, path, body, now, query):
        Clock.value = now
        try:
            if path == SOCKET:
                answer = self.transact(body)
                return answer.get("status", 200), json.loads(json.dumps(answer))
            if body is not None and query:
                raise APIError(400, "This route does not accept query parameters")
            if "workspace" not in self.modules:
                raise APIError(403, str(ModuleDisabledError("workspace")))
            if body is None:
                answer = self.get(path, query)
            else:
                before = record_events.op_log_mark(self.conn)
                answer = self.post(path, body)
                record_events.capture(self.conn, path, body, answer, op_log_before=before)
        except (blocks.BlockError, quick_notes.QuickNoteError, APIError) as exc:
            return exc.status, {"error": str(exc)}
        except Exception:
            return 500, {"error": "Local operation failed; inspect CLI diagnostics"}
        return 200, json.loads(json.dumps(answer))


class Through:
    def __init__(self, site, clock):
        self.site, self.clock, self.connection = site, clock, None

    def receive(self):
        while True:
            opcode, payload = self.reader.frame(10)
            if opcode == 1:
                return json.loads(payload)
            assert opcode in (9, 10), (opcode, payload)
            if opcode == 9:
                send_frame(self.connection, 10, payload)

    def __call__(self, path, body, now, query):
        self.clock.write_text(now.isoformat(), encoding="utf-8")
        if path != SOCKET:
            return self.site.request(path + ("?" + urlencode(query) if query else ""), body)
        if self.connection is None:
            status, token = self.site.request("/api/live/session", {})
            assert status == 200
            status, _, self.connection, rest = websocket(self.site.port, f"{SOCKET}?token={token['token']}", {}, secret=self.site.secret)
            assert status == 101
            self.reader = FrameReader(self.connection, rest)
            assert self.receive()["type"] == "hello"
        send_frame(self.connection, 1, json.dumps(body).encode())
        while True:
            answer = self.receive()
            if answer["type"] in {"ack", "reject"}:
                return answer.get("status", 200), answer
            assert answer["type"] == "change", answer

    def close(self):
        if self.connection is not None:
            self.connection.close()


def seed(conn, base, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(store, "now", Clock.now)
        patch.setattr(notes, "now", Clock.now)
        Clock.value = base - timedelta(days=40)
        old = block_api.create_page(conn, "Archived history", blocks=["old version"])
        old_block = old["blocks"][0]["id"]
        block_api.update_block(conn, old_block, text="daily snapshot")
        history.compact(conn, now=base)
        Clock.value = base
        book = notes.create_notebook(conn, "Parity notebook")
        first = block_api.create_page(conn, "Quasar target", notebook=book["id"], blocks=["A😀B café Straße", "movable leaf"])
        p, leaf = first["page_id"], first["blocks"][0]["id"]
        second = block_api.create_page(conn, "Destination", notebook=book["id"])
        linked = block_api.insert_blocks(conn, [f"[[Quasar target|alias]] (({leaf})) (({leaf}@{first['seq']})) {{{{embed:{leaf}}}}} #cosmos"], page=second["page_id"])
        block_api.insert_blocks(conn, ["Unlinked Quasar target mention with 𐐀 and İ"], page=second["page_id"])
        always = tags.create(conn, {"name": "Cosmos", "mode": "always", "keywords": ["nebula"], "aliases": ["cosmic"]})
        suggest = tags.create(conn, {"name": "Optics", "mode": "suggest", "keywords": ["photon"], "aliases": ["lightwave"]})
        tags.attach(conn, linked["blocks"][0]["id"], always["id"])
        quick = quick_notes.create(conn, "seed scratch 😀", "mint")
        document = str(uuid.uuid4())
        blocks.transact(conn, [{"op": "insert", "id": document, "type": "document", "parent": None,
                               "text": "Document root", "props": {"number": 90, "revision": 1}}])
        with actor.acting(actor.Actor("agent", "Claude", "sess-1")):
            block_api.update_block(conn, leaf, text="agent edit")
            block_api.insert_blocks(conn, ["agent addition"], page=p)
        with actor.acting(actor.Actor("agent", "Claude", "sess-2")):
            block_api.insert_blocks(conn, ["second session addition"], page=second["page_id"])
        block_api.update_block(conn, leaf, text="user conflict: preserve this")
        conn.commit()
    return {"first": first, "second": second, "leaf": leaf, "movable": first["blocks"][1]["id"],
            "always": always["id"], "suggest": suggest["id"], "quick": quick, "document": document,
            "old": old, "old_block": old_block}


def scenario(call, seeded, base, modules=MODULE_IDS):
    answers = []
    allowed = "workspace" in modules
    now = base

    def do(route, body=None, expect=200, **query):
        nonlocal now
        now += timedelta(seconds=1)
        path = route if route.startswith("/") else PREFIX + route
        status, answer = call(path, body, now, query)
        if not allowed and not (body is not None and query):
            expect = 403
        assert status == expect, (len(answers), path, body, query, status, answer)
        answers.append((path, status, answer))
        return answer

    def txn(ops, expect=200, **extra):
        return do(SOCKET, {"type": "txn", "ops": ops, **extra}, expect=expect)

    p, dest = seeded["first"]["page_id"], seeded["second"]["page_id"]
    leaf, movable = seeded["leaf"], seeded["movable"]
    seq = seeded["first"]["seq"]
    for query in ({"page": p}, {"type": "page", "number": str(seeded["first"]["number"])},
                  {"page": seeded["document"]}, {"type": "quick_note", "number": str(seeded["quick"]["id"])}):
        do("page", **query)
    for route in ("page", "history", "history/preview"):
        do(route, expect=400, page=p, unexpected="1")
        do(route, expect=400, page="bad", **({"seq": str(seq)} if route.endswith("preview") else {}))
        do(route, expect=404, type="page", number="999999")
        do(route, expect=400, type="page", number="01")
        do(route, expect=400)
    do("page", expect=404, page=MISSING)
    do("history", page=p)
    do("history", page=p, actor="agent", session="sess-1")
    do("history", page=p, session="absent")
    do("history", expect=400, page=p, actor="robot")
    do("history", expect=404, page=MISSING)
    do("history/preview", page=p, seq=str(seq))
    for value in ("-1", "x", "1.0", "1" * 19):
        do("history/preview", expect=400, page=p, seq=value)
    do("history/preview", expect=410, page=p, seq="0")
    do("history/preview", expect=410, page=seeded["old"]["page_id"], seq=str(seeded["old"]["seq"]))
    cited = do("citations", cites=f"(({leaf}@{seq})),{MISSING}@{seq},{seeded['old_block']}@{seeded['old']['seq']},,")
    if allowed:
        assert [item["status"] for item in cited["citations"]] == ["changed", "unknown", "compacted"]
    for query in ({}, {"cites": ""}, {"cites": "bad"}, {"cites": f"{leaf}@-1"},
                  {"cites": ",".join([f"{leaf}@{seq}"] * 51)}, {"cites": f"{leaf}@{seq}", "x": "1"}):
        do("citations", expect=400, **query)
    for route in ("sessions", "pages", "search"):
        query = {"q": "Quasar"} if route == "search" else {}
        do(route, **query)
        for limit in ("1", "200"):
            do(route, limit=limit, **query)
        for limit in ("0", "-1", "01", "201", "999", "1000", "a", "١", " 2"):
            do(route, expect=400, limit=limit, **query)
        do(route, expect=400, extra="x", **query)
    do("sessions", kind="all")
    do("sessions", kind="user")
    do("sessions", expect=400, kind="nope")
    for kind in ("page", "document", "quick_note"):
        do("pages", kind=kind)
    do("pages", q="quasar")
    do("pages", expect=400, kind="tag")
    do("pages", expect=400, q="x" * 201)
    do("search", expect=400)
    for text in ("", "😀", "Straße", "Quasar target", "[[Quasar target]]"):
        do("search", q=text)
    do("search", q="x" * 201)  # Unlike pages, search does not use MAX_QUERY.
    for target in (p, leaf, seeded["always"], seeded["suggest"]):
        do("backlinks", id=target)
    do("backlinks", expect=400)
    do("backlinks", expect=400, id="bad")
    do("backlinks", expect=404, id=MISSING)
    do("backlinks", expect=400, id=p, extra="x")
    do("trash")
    do("trash", expect=400, limit="1")

    # Field refusals for every mutation, and query refusal before the module guard.
    for route in POSTS:
        do(route, {"unexpected": 1}, expect=400)
        do(route, {}, expect=400, q="1")
    for route, body, status in (
        ("history/restore", {"page": p, "seq": True}, 400),
        ("history/restore", {"page": p, "seq": -1}, 400),
        ("history/restore", {"page": MISSING, "seq": seq}, 404),
        ("sessions/undo", {"session": "bad session"}, 400),
        ("sessions/undo", {"session": "absent"}, 404),
        ("sessions/undo", {"session": "sess-1", "dry_run": 1}, 400),
        ("trash/restore", {"id": "bad"}, 400),
        ("trash/restore", {"id": MISSING}, 404),
        ("trash/restore", {"id": leaf}, 409),
        ("pages/create", {"title": ""}, 400),
        ("pages/create", {"title": "x" * 201}, 400),
        ("pages/create", {"title": "x", "notebook": 999999}, 404),
        ("insert", {"page": p, "blocks": []}, 400),
        ("insert", {"blocks": ["no destination"]}, 400),
        ("insert", {"page": p, "blocks": "not a list"}, 400),
        ("insert", {"page": p, "blocks": [{"children": "bad"}]}, 400),
        ("insert", {"page": p, "blocks": [{"extra": 1}]}, 400),
        ("insert", {"page": p, "blocks": [{"type": "invalid"}]}, 400),
        ("insert", {"page": p, "blocks": [{"type": []}]}, 500),
        ("insert", {"page": p, "blocks": [{"props": {"checked": 1}}]}, 400),
        ("insert", {"page": p, "blocks": ["x"], "revision": 999}, 409),
        ("update", {"id": leaf}, 400),
        ("update", {"id": "bad", "text": "x"}, 400),
        ("update", {"id": MISSING, "text": "x"}, 404),
        ("update", {"id": leaf, "text": "x", "revision": True}, 400),
        ("update", {"id": leaf, "text": "x", "revision": 999}, 409),
        ("update", {"id": p, "type": "text"}, 400),
        ("update", {"id": leaf, "props": {"unknown": 1}}, 400),
        ("update", {"id": leaf, "props": {"language": "x" * 41}}, 400),
        ("move", {"id": p, "page": dest}, 400),
        ("move", {"id": movable, "after": movable}, 400),
        ("move", {"id": movable, "after": leaf, "before": leaf}, 400),
        ("move", {"id": movable, "parent": movable}, 400),
        ("move", {"id": movable, "page": dest, "revision": 999}, 409),
        ("delete", {"id": p}, 400),
        ("delete", {"id": MISSING}, 404),
        ("delete", {"id": leaf, "revision": 999}, 409),
        ("inbox", {"blocks": []}, 400),
    ):
        do(route, body, expect=status)

    dry = do("sessions/undo", {"session": "sess-1", "dry_run": True})
    applied = do("sessions/undo", {"session": "sess-1"})
    if allowed:
        assert dry["conflicts"] and applied["conflicts"]
        assert not dry["applied"] and applied["applied"]
    do("sessions/undo", {"session": "sess-2", "dry_run": True})
    do("sessions/undo", {"session": "sess-2"})
    do("sessions")
    do("update", {"id": leaf, "text": "edited after agent undo", "type": "code", "props": {"language": "rust"}})
    do("update", {"id": leaf, "props": {"language": None}})
    do("history/restore", {"page": p, "seq": seq})
    do("history/restore", {"page": p, "seq": str(seq)}, expect=409)
    do("citations", cites=f"{leaf}@{seq}")
    do("insert", {"page": p, "blocks": ["newly appended"]})
    do("insert", {"after": leaf, "blocks": ["inserted after"]})
    do("insert", {"before": leaf, "blocks": ["inserted before"]})
    do("move", {"id": movable, "before": leaf})
    do("move", {"id": movable, "page": dest})
    do("move", {"id": movable, "page": p})
    do("delete", {"id": movable})
    do("citations", cites=f"{movable}@{seq}")
    do("trash")
    do("delete", {"id": movable}, expect=404)
    do("trash/restore", {"id": movable})
    do("trash/restore", {"id": movable}, expect=409)
    do("pages/create", {"title": "Nested outline", "notebook": "New notebook", "blocks": [
        {"type": "heading_1", "text": "Outline", "children": [{"text": "one chain", "children": ["deepest"]}]},
        {"type": "to_do", "text": "task", "props": {"checked": False}}]})
    do("pages/create", {"title": "Empty", "blocks": []})
    do("inbox", {"blocks": ["first capture 😀"]})
    do("inbox", {"blocks": [{"text": "second capture", "children": ["nested capture"]}]})

    # Quick-note validation: all field, id, UTF-8/size, tint, pin and revision branches.
    note = do("/api/quick-notes/create", {"body": "scratch 😀", "tint": "sky"})
    note_id = note["id"] if allowed else seeded["quick"]["id"]
    revision = note["revision"] if allowed else 1
    for operation in QUICK:
        do("/api/quick-notes/" + operation, {"unknown": 1}, expect=400)
        do("/api/quick-notes/" + operation, {}, expect=400, q="1")
    for operation in ("save", "delete"):
        do("/api/quick-notes/" + operation, {}, expect=400)
        for bad_id in (None, True, 0, -1, "1", 2**53):
            do("/api/quick-notes/" + operation, {"id": bad_id, "revision": 1}, expect=400)
        do("/api/quick-notes/" + operation, {"id": 999999, "revision": 1}, expect=404)
        for stale in (0, 999, True, "1", None):
            do("/api/quick-notes/" + operation, {"id": note_id, "revision": stale}, expect=409)
    # A lone surrogate: Python reads it and refuses it (or the disabled module first); the front's
    # JSON reader refuses the body before either (a known difference, see the comparison).
    for body in (None, 1, [], "nul\x00", *(("\ud800",) if allowed else ()), "é" * 32769):
        do("/api/quick-notes/create", {"body": body}, expect=400)
        if body is not None:  # save's null means leave unchanged, unlike create.
            do("/api/quick-notes/save", {"id": note_id, "revision": revision, "body": body}, expect=400)
    for tint in ("bogus", 1, [], None):
        do("/api/quick-notes/create", {"tint": tint}, expect=400)
        if tint is not None:
            do("/api/quick-notes/save", {"id": note_id, "revision": revision, "tint": tint}, expect=400)
    for pinned in (1, "true", []):
        do("/api/quick-notes/save", {"id": note_id, "revision": revision, "pinned": pinned}, expect=400)
    for changes in ({"body": "saved 🐈", "tint": "honey", "pinned": True}, {"pinned": False},
                    {"body": None, "tint": None, "pinned": None}, {}):
        saved = do("/api/quick-notes/save", {"id": note_id, "revision": revision, **changes})
        revision = saved["revision"] if allowed else revision + 1
    do("/api/quick-notes/delete", {"id": note_id, "revision": revision})
    do("/api/quick-notes/delete", {"id": note_id, "revision": revision}, expect=404)
    do("/api/quick-notes/save", {"id": note_id, "revision": revision}, expect=404)
    do("/api/quick-notes/nope", {}, expect=404)
    do("/api/quick-notes/create", {})
    do("/api/quick-notes/create", {"body": "é" * 32768, "tint": "frost"})

    # One new matching block only: auto attachment and suggestion order are stable.
    inserted = txn([
        {"op": "insert", "id": LIVE_PAGE, "type": "page", "parent": None, "text": "Live editor", "props": {"revision": 1}},
        {"op": "insert", "id": LIVE_BLOCK, "type": "text", "parent": LIVE_PAGE, "text": "A😀B nebula photon cosmic lightwave"},
        {"op": "insert", "id": LIVE_CHILD, "type": "text", "parent": LIVE_BLOCK, "text": "single chain"},
    ], txn_id=LIVE_TXN)
    if allowed:
        assert inserted["tagged"] and inserted["suggestions"] and inserted["tags"]
    txn([{"op": "text", "id": LIVE_BLOCK, "text": "duplicate"}], expect=409, txn_id=LIVE_TXN)
    txn([{"op": "text", "id": LIVE_BLOCK, "at": 1, "del": 1, "ins": "🦊é"}])
    txn([{"op": "text", "id": LIVE_BLOCK, "at": 3, "del": 1, "ins": "Z"}])
    revision = do("page", page=LIVE_PAGE).get("revision", 1)
    tag_key = "tag_" + seeded["suggest"].replace("-", "")
    view = txn([{"op": "set", "id": LIVE_BLOCK, "props": {"viewport": {"x": 1.25}, "position": [2, 3],
                  "favorite": True, "last_opened": "today", "viewport_revision": 7, tag_key: "accepted"}}])
    if allowed:
        assert view["revisions"][LIVE_PAGE] == revision
    txn([{"op": "set", "id": LIVE_BLOCK, "props": {tag_key: "rejected"}}])
    do("page", page=LIVE_PAGE)
    do("backlinks", id=seeded["always"])
    do("backlinks", id=seeded["suggest"])
    txn([{"op": "text", "id": LIVE_BLOCK, "text": "stale"}], expect=409, base={LIVE_PAGE: 999})
    txn([{"op": "text", "id": LIVE_BLOCK, "text": "stale"}], expect=409, base={LIVE_PAGE: True})
    txn([{"op": "text", "id": LIVE_BLOCK, "text": "bad base"}], expect=400, base=[])
    for ops in ("not a list", {}, [], [None], [{"op": "unknown", "id": LIVE_BLOCK}],
                [{"op": "release", "id": LIVE_BLOCK}], [{"op": "adopt", "parent": LIVE_PAGE}],
                [{"op": "put", "id": LIVE_BLOCK}],
                [{"op": "move", "id": LIVE_BLOCK, "parent": LIVE_CHILD}],
                [{"op": "set", "id": LIVE_BLOCK, "props": {tag_key: "bad"}}],
                [{"op": "text", "id": LIVE_BLOCK, "at": True, "del": 0, "ins": "x"}],
                [{"op": "text", "id": LIVE_BLOCK, "at": -1}],
                [{"op": "text", "id": LIVE_BLOCK, "at": 999}],
                [{"op": "text", "id": LIVE_BLOCK, "text": "bad\x00"}]):
        txn(ops, expect=400)
    txn([{"op": "set", "id": LIVE_BLOCK, "props": {}}], expect=400, unexpected=True)
    txn([{"op": "set", "id": LIVE_BLOCK, "props": {}}], expect=400, txn_id="BAD")
    txn([{"op": "insert", "id": LIVE_BLOCK, "type": "text", "parent": LIVE_PAGE}], expect=409)
    txn([{"op": "text", "id": MISSING, "text": "missing"}], expect=404)
    # A cross-page move of a single chain tests release/adopt without set-order branches.
    txn([{"op": "move", "id": LIVE_BLOCK, "parent": p}])
    txn([{"op": "move", "id": LIVE_BLOCK, "parent": LIVE_PAGE}])
    txn([{"op": "delete", "id": LIVE_BLOCK}])
    txn([{"op": "delete", "id": LIVE_BLOCK}], expect=409)
    txn([{"op": "restore", "id": LIVE_CHILD}], expect=409)
    txn([{"op": "insert", "id": MISSING, "type": "text", "parent": LIVE_BLOCK}], expect=409)
    txn([{"op": "restore", "id": LIVE_BLOCK}])
    txn([{"op": "restore", "id": LIVE_BLOCK}], expect=409)
    now += timedelta(minutes=11)
    txn([{"op": "text", "id": LIVE_CHILD, "text": "later session"}])
    history_answer = do("history", page=LIVE_PAGE)
    if allowed:
        assert len(history_answer["sessions"]) >= 2
    do("page", page=LIVE_PAGE)
    do("page", page=p)
    do("page", page=dest)
    do("pages")
    do("search", q="photon")
    do("backlinks", id=p)
    do("trash")
    return answers


TABLES = ("blocks", "op_log", "page_crdt", "page_snapshots", "block_history", "block_refs",
          "block_topics", "block_tags", "tag_rejections", "tag_settings", "search_docs",
          "search_pending", "note_notebooks", "record_events")


def stored_rows(db_path):
    with closing(connect(db_path)) as conn:
        result = {}
        for table in TABLES:
            info = list(conn.execute(f"PRAGMA table_info({table})"))
            excluded = {"id"} if table == "search_docs" else {"at"} if table == "record_events" else set()
            columns = [column["name"] for column in info if column["name"] not in excluded]
            primary = [column["name"] for column in sorted(info, key=lambda column: column["pk"]) if column["pk"]]
            order = {"blocks": ["id"], "op_log": ["seq"], "page_crdt": ["page_id"],
                     "search_docs": ["kind", "ref"]}.get(table, primary)
            assert columns and order, table
            result[table] = [dict(row) for row in conn.execute(
                f"SELECT {','.join(columns)} FROM {table} ORDER BY {','.join(order)}")]
        return result


def loro_view(blob):
    """Diagnostic only: never normalize the bytes used by the equality assertion."""
    if blob is None:
        return None
    from loro import LoroDoc
    try:
        doc = LoroDoc()
        doc.import_(bytes(blob))
        return doc.get_deep_value()
    except Exception as exc:
        return {"decode_error": str(exc), "bytes": repr(blob)}


def assert_stored_equal(actual, expected):
    for table in TABLES:
        if table in {"op_log", "page_crdt"} and actual[table] != expected[table]:
            for index, (left, right) in enumerate(zip(actual[table], expected[table])):
                for column in left:
                    if left[column] != right[column] and isinstance(left[column], bytes):
                        assert left[column] == right[column], (
                            table, index, column, loro_view(left[column]), loro_view(right[column]))
        assert actual[table] == expected[table], table


def prepare(workspace, tmp_path, monkeypatch, modules):
    base = base_time()
    seeded = seed(workspace.conn, base, monkeypatch)
    if modules != MODULE_IDS:
        (workspace.settings.root / "config" / "build.json").write_text(
            json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    reference_path = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, reference_path)
    return base, seeded, reference_path


def reference_run(reference_path, seeded, base, monkeypatch, modules):
    with monkeypatch.context() as patch:
        deterministic_uuids(patch)
        reference = Reference(reference_path, modules)
        try:
            answers = scenario(reference, seeded, base, modules)
        finally:
            reference.conn.close()
            crdt._CACHE.clear()
    return answers


@pytest.mark.parametrize("modules", [MODULE_IDS, ("applyai",)])
def test_reference_scenario(workspace, tmp_path, monkeypatch, modules, record_property):
    """Executable oracle sanity check, usable before the Rust implementation exists."""
    base, seeded, path = prepare(workspace, tmp_path, monkeypatch, modules)
    answers = reference_run(path, seeded, base, monkeypatch, modules)
    assert len(answers) > 250
    record_property("http_requests", sum(path != SOCKET for path, _, _ in answers))
    record_property("live_messages", sum(path == SOCKET for path, _, _ in answers))
    assert {path for path, _, _ in answers} >= {
        *(PREFIX + route for route in GETS + POSTS), *("/api/quick-notes/" + route for route in QUICK), SOCKET}
    assert stored_rows(path)["record_events"] == []
    if "workspace" not in modules:
        assert {status for _, status, _ in answers} == {400, 403}
        assert all(answer["error"] == "This route does not accept query parameters"
                   for _, status, answer in answers if status == 400)


@pytest.mark.parametrize("modules", [MODULE_IDS, ("applyai",)])
def test_the_front_answers_blocks_as_the_python_core_does(workspace, tmp_path, monkeypatch, modules):
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER})")
    base, seeded, reference_path = prepare(workspace, tmp_path, monkeypatch, modules)
    expected = reference_run(reference_path, seeded, base, monkeypatch, modules)
    clock, counter = tmp_path / "clock", tmp_path / "uuids"
    clock.write_text(base.isoformat(), encoding="utf-8")
    counter.write_text("0", encoding="utf-8")
    with front_site(workspace.settings, env={"KITTY_TEST_CLOCK": str(clock), "KITTY_TEST_UUIDS": str(counter)}) as site:
        front = Through(site, clock)
        try:
            actual = scenario(front, seeded, base, modules)
        finally:
            front.close()
    assert len(actual) == len(expected)
    differences = 0
    for index, (answer, wanted) in enumerate(zip(actual, expected, strict=True)):
        if wanted[1:] == (400, {"error": "Quick note text must be valid Unicode"}):
            # Known difference: the front's JSON reader refuses a lone surrogate escape in any
            # body before the route sees it (400 both).
            assert answer == (wanted[0], 400, {"error": "Malformed JSON body"}), index
            differences += 1
            continue
        assert answer == wanted, (index, answer[0])
    assert differences == (2 if "workspace" in modules else 0)
    actual_rows, expected_rows = stored_rows(workspace.settings.db_path), stored_rows(reference_path)
    assert_stored_equal(actual_rows, expected_rows)
    assert actual_rows["record_events"] == []


def test_reference_busy_transaction(workspace, monkeypatch):
    """The live glue maps SQLite OperationalError to a retryable reject, not HTTP 500."""
    reference = Reference(workspace.settings.db_path)
    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(blocks, "transact", busy)
    try:
        assert reference.transact({"type": "txn", "txn_id": LIVE_TXN, "ops": []}) == {
            "type": "reject", "txn_id": LIVE_TXN, "status": 503,
            "error": "Local workspace is busy; send the transaction again"}
    finally:
        reference.conn.close()


def test_the_worker_answers_no_block_route(workspace, tmp_path):
    created = block_api.create_page(workspace.conn, "Forwarded notes", blocks=["still Python"])
    conn = workspace.conn
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        for route in GETS:
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(conn, PREFIX + route, {})
        for path in [*(PREFIX + route for route in POSTS), *("/api/quick-notes/" + route for route in QUICK),
                     "/api/internal/live/txn"]:
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(conn, path, {})
    finally:
        app.close()
    conn.close()
    with front_site(workspace.settings) as site:
        status, page = site.request(f"/api/notes/pages/{created['number']}/blocks")
        assert status == 200 and page["blocks"][0]["text"] == "still Python"
        status, tag = site.request("/api/tags/create", {"name": "Forwarded tag", "mode": "suggest"})
        assert status == 200 and tag["name"] == "Forwarded tag"
