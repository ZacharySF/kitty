"""Rika's local-state routes and the account's local half answered by rika-server
(server/src/rika, server/src/account.rs) against the retained Python core on two copies of
one seeded workspace.

The reference owns the deleted handlers' glue (`web_rika_memory.get/post`, the history branch
of `web_rika.get`, `web_account.get` and its onboarding write). A file clock makes the routing
summary's days, the index's `refreshed_at` and the preference stamps comparable without
masking anything. Chat, plans, approvals, undo, voice, speech, the agent roster, Google
sign-in and usage stay on the worker.
"""
from __future__ import annotations

import json
import re
import shutil
from contextlib import closing
from datetime import datetime as real_datetime
from datetime import timedelta
from urllib.parse import quote, urlencode

import pytest
from terminal_support import RIKA_SERVER, front_site
from test_blocks_parity import base_time

from kitty.core import block_api, notes, record_events
from kitty.core.rika import memory, routes, store
from kitty.core.rika import tools as toolset
from kitty.core.rika.workspace_tools import _allow_ai
from kitty.db import connect
from kitty.modules import MODULE_IDS
from kitty.web_server import APIError, WebApplication

PREFIX = "/api/rika"
QUERY_MAX = 300
_ID = r"(\d{1,18})"
GOOGLE = {"KITTY_GOOGLE_CLIENT_ID": "parity-client", "KITTY_GOOGLE_CLIENT_SECRET": "parity-secret"}


class Clock:
    value: real_datetime

    @classmethod
    def now(cls):
        return cls.value.isoformat(timespec="microseconds")


class FrozenDatetime(real_datetime):
    """`routes.summary` reads `datetime.now(zone)`."""

    @classmethod
    def now(cls, tz=None):
        return Clock.value.astimezone(tz)


def _keys(body, allowed, required=frozenset()):
    if set(body) - allowed or required - set(body):
        raise APIError(400, "Unexpected or missing Rika request fields")


def _no_query(query):
    if query:
        raise APIError(400, "This route does not accept query parameters")


def _preference(call, *args):
    try:
        return call(*args)
    except LookupError as exc:
        raise APIError(404, str(exc)) from None
    except ValueError as exc:
        raise APIError(400, str(exc)) from None


class Reference:
    """The deleted handlers as they were, over the retained core."""

    def __init__(self, db_path, modules, configured):
        self.conn, self.modules, self.configured = connect(db_path), modules, configured

    def _account(self):
        row = self.conn.execute("SELECT * FROM workspace_onboarding WHERE id=1").fetchone()
        return {
            "configured": self.configured,
            "identity": {"sub": row["google_sub"], "email": row["google_email"], "name": row["google_name"]} if row["google_sub"] else None,
            "onboarding": {"welcome_completed": bool(row["welcome_completed"]), "tutorial_completed": bool(row["tutorial_completed"])},
        }

    def _routes(self):
        return {"routes": routes.listing(self.conn), "summary": routes.summary(self.conn)}

    def _memory(self, query):
        conn, results = self.conn, []
        if query is not None and query.strip():
            memory.refresh(conn, modules=self.modules)
            results = memory.search(conn, query)
        return {"preferences": memory.preferences(conn), "index": memory.index(conn), "results": results,
                "notes_shared": _allow_ai(conn)}

    def get(self, path, query):
        conn = self.conn
        if path == "/api/account":
            _no_query(query)
            return self._account()
        if path == PREFIX + "/history":
            if set(query) - {"before"}:
                raise APIError(400, "History accepts only before")
            before = query.get("before")
            if before is not None and not re.fullmatch(r"[0-9T:.+-]{10,40}", before):
                raise APIError(400, "before must be an item's created_at")
            return store.history(conn, before)
        if path == PREFIX + "/routes":
            _no_query(query)
            return self._routes()
        if path == PREFIX + "/memory":
            if set(query) - {"q"}:
                raise APIError(400, "Memory accepts only q")
            text = query.get("q")
            if text is not None and (len(text) > QUERY_MAX or "\x00" in text):
                raise APIError(400, f"Search for at most {QUERY_MAX} characters")
            return self._memory(text)
        raise APIError(404, "API route not found")

    def post(self, path, body):
        conn = self.conn
        if path == "/api/account/onboarding":
            if (not body or not set(body) <= {"welcome_completed", "tutorial_completed"}
                    or any(type(value) is not bool for value in body.values())):
                raise APIError(400, "Expected welcome_completed or tutorial_completed booleans")
            for key, value in body.items():
                conn.execute(f"UPDATE workspace_onboarding SET {key}=? WHERE id=1", (int(value),))
            conn.commit()
            return self._account()
        if path == PREFIX + "/routes/forget":
            _keys(body, {"signature"}, {"signature"})
            if not isinstance(body["signature"], str) or len(body["signature"]) > 2000:
                raise APIError(400, "signature must be a route's signature")
            try:
                routes.forget(conn, body["signature"])
            except LookupError as exc:
                raise APIError(404, str(exc)) from None
            return self._routes()
        if path == PREFIX + "/preferences":
            _keys(body, {"text"}, {"text"})
            return {"preference": _preference(lambda text: memory.add_preference(conn, text, source="added"), body["text"])}
        match = re.fullmatch(PREFIX + "/preferences/" + _ID, path)
        if match:
            _keys(body, {"text"}, {"text"})
            return {"preference": _preference(memory.update_preference, conn, int(match[1]), body["text"])}
        match = re.fullmatch(PREFIX + "/preferences/" + _ID + "/delete", path)
        if match:
            _keys(body, set())
            return {"deleted": _preference(memory.delete_preference, conn, int(match[1]))}
        if path == PREFIX + "/memory/rebuild":
            _keys(body, set())
            return {"index": memory.rebuild(conn, modules=self.modules)}
        raise APIError(404, "API route not found")

    def __call__(self, path, body, now, query):
        Clock.value = now
        try:
            if body is not None and query:
                raise APIError(400, "This route does not accept query parameters")
            if body is None:
                answer = self.get(path, query)
            else:
                before = record_events.op_log_mark(self.conn)
                answer = self.post(path, body)
                record_events.capture(self.conn, path, body, answer, op_log_before=before)
        except APIError as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            return exc.status, {"error": str(exc)}
        return 200, json.loads(json.dumps(answer))


class Through:
    def __init__(self, site, clock):
        self.site, self.clock = site, clock

    def __call__(self, path, body, now, query):
        self.clock.write_text(now.isoformat(), encoding="utf-8")
        return self.site.request(quote(path, safe="/") + ("?" + urlencode(query) if query else ""), body)


def _stamp(at):
    return at.isoformat(timespec="microseconds")


def seed(workspace, base):
    """Conversations, actions, approvals, plans and mail drafts (ties across tables included),
    routes with exact and repeating costs, shortcuts, preferences, and one row of every
    source the memory index reads."""
    conn = workspace.conn
    conn.execute("UPDATE calendar_calendars SET timezone='America/Los_Angeles' WHERE id='personal'")
    conn.execute("UPDATE workspace_onboarding SET google_sub='sub-1', google_email='ada@example.com', google_name='Ada' WHERE id=1")
    book = notes.create_notebook(conn, "Biology")
    page = block_api.create_page(conn, "Enzymes", notebook=book["id"],
                                 blocks=["Enzymes lower activation energy.", "Catalase splits peroxide; ÇAFÉ straße."])
    block_api.create_page(conn, "Quasars", notebook=book["id"], blocks=["Quasar spectra and galaxies."])
    number = page["number"] if "number" in page else page.get("page", {}).get("number")

    tools = toolset.registry()
    outward = sorted(name for name, tool in tools.items() if tool.effect == "outward")
    change = sorted(name for name, tool in tools.items() if tool.effect != "outward")
    stamp = base - timedelta(days=20)
    messages = []

    def at(minutes):
        return _stamp(stamp + timedelta(minutes=minutes))

    # Forty exchanges, one minute apart, with replies costed and routed in every way history shows.
    costs = [None, "0", "0.0004", "1E-7", "0.00000012345", "12.5", "3"]
    for index in range(40):
        when = at(index * 2)
        user = store.new_id()
        reply = store.new_id()
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,reply_to,tier,model,status,source,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (user, "c1" if index < 20 else "c2", "user", f"Request {index} about enzymes", None, None, None,
                      "done", ("typed", "voice", "dump")[index % 3], when))
        route = [None, json.dumps({"path": ["search", "add_card"], "signature": "search>add_card", "remembered": 1,
                                   "shortcut": 0}),
                 json.dumps({}), json.dumps({"path": [], "signature": "", "remembered": "", "shortcut": "yes"})][index % 4]
        tokens = (None, None, None) if index % 5 == 0 else (100 + index, index * 3, 50 + index)
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,reply_to,tier,model,status,source,cost_usd,"
                     "tokens_in,tokens_cached,tokens_out,route_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (reply, "c1" if index < 20 else "c2", "rika", f"Reply {index}", user,
                      (None, "fast", "standard", "deep")[index % 4], "model-x" if index % 2 else None,
                      ("done", "failed", "done", "pending")[index % 4], "typed", costs[index % len(costs)],
                      *tokens, route, when if index % 6 == 0 else at(index * 2 + 1)))
        messages.append((user, reply, when))
    # Same instant in every table: the page keeps the tables' order, reversed on the way out.
    tie = messages[30][2]
    first_user, first_reply = messages[30][0], messages[30][1]
    for index in range(12):
        conn.execute("INSERT INTO rika_actions(id,message_id,tool,args_json,summary,reversible,undo_json,status,created_at,"
                     "undone_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (store.new_id(), messages[index * 3][1], change[index % len(change)], "{}", f"Did thing {index}",
                      index % 2, None, ("done", "undone", "failed")[index % 3], tie if index < 3 else at(index * 7 + 1),
                      at(index * 7 + 2) if index % 3 == 1 else None))
    for index in range(8):
        conn.execute("INSERT INTO rika_approvals(id,message_id,tool,args_json,summary,detail,cost_usd,status,created_at,"
                     "decided_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (store.new_id(), messages[index * 4][1], outward[index % len(outward)], "{}", f"Approve {index}",
                      f"Detail {index}", (None, "0.25", "1E-3")[index % 3], ("approved", "rejected", "expired")[index % 3],
                      tie if index < 2 else at(index * 9 + 3), at(index * 9 + 4)))
    calls = [{"tool": name, "args": {}} for name in [*outward, *change]]
    items = [
        {"id": "i1", "area": "notes", "title": "Plain", "detail": "", "source": "said", "calls": calls[:2],
         "question": None, "choice": None, "result": None},
        {"id": "i2", "area": "calendar", "title": "Asked", "detail": "d", "source": "", "calls": [],
         "question": {"text": "Which day?", "choices": [{"label": "Mon", "calls": [calls[-1]]},
                                                        {"label": "Tue", "calls": [calls[0], calls[-1]]}]},
         "choice": 1, "result": {"ok": True}, "picked": "Tue", "slot": {"tool": "x", "base": 1, "time": True}},
        {"id": "i3", "area": "tasks", "title": "Unknown tool", "detail": "", "source": "", "calls": [{"tool": "nope", "args": {}}],
         "question": None, "choice": None, "result": None, "slot": None},
        {"id": "i4", "area": "tasks", "title": "Negative choice", "detail": "", "source": "", "calls": [],
         "question": {"text": "Q", "choices": [{"label": "A", "calls": []}, {"label": "B", "calls": [calls[1]]}]},
         "choice": -1, "result": None},
        *({"id": f"t{index}", "area": "tools", "title": name, "detail": "", "source": "", "calls": [{"tool": name, "args": {}}],
           "question": None, "choice": None, "result": None} for index, name in enumerate(sorted(tools))),
    ]
    note_page = conn.execute("SELECT id FROM note_page_index WHERE title='Enzymes'").fetchone()["id"]
    conn.execute("PRAGMA foreign_keys=OFF")
    for index, (kind, status, note) in enumerate([("dump", "pending", note_page), ("confirm", "applied", None),
                                                  ("checkin", "dismissed", 999_999), ("dump", "failed", None)]):
        conn.execute("INSERT INTO rika_plans(id,message_id,request_id,items_json,segments_json,duration_s,note_page_id,"
                     "note_block_id,status,created_at,decided_at,kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (store.new_id(), messages[index * 5][1], messages[index * 5][0], json.dumps(items), "[]",
                      (0, 12.5, 3, 0.1)[index], note, None, status, tie if index == 0 else at(index * 11 + 5),
                      None if status == "pending" else at(index * 11 + 6), kind))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO rika_mail_messages(id,thread_id,rfc_id,sender,sender_email,subject,body,received_at,kind,"
                 "needs_reply,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 ("m1", "t1", "<r1>", "Grace", "grace@example.com", "Enzyme lab", "  Can we\nmeet\ttomorrow? " + "x" * 300,
                  at(3), "scheduling", 1, at(3)))
    for index, (status, mail_id) in enumerate([("draft", "m1"), ("sent", None), ("discarded", "m1"), ("cancelled", "m1")]):
        conn.execute("INSERT INTO rika_mail_drafts(id,mail_id,message_id,thread_id,to_address,subject,body,kind,gmail_draft_id,"
                     "status,auto,send_after,sent_at,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"d{index}", mail_id, first_reply if index == 0 else None, "t1", "grace@example.com", "Re: Enzyme lab",
                      "Sure", "scheduling", None, status, index % 2, None, at(50) if status == "sent" else None,
                      "boom" if status == "cancelled" else None, tie if index == 0 else at(index * 13 + 7), at(60)))
    assert first_user

    # Routing memory: exact, repeating and exponent costs, a route without uses, shortcuts.
    for signature, tier, path, uses, tokens, cost in [
            ("add_card", "fast", ["add_card"], 12, 14160, "0.0024"),
            ("search>add_card", "fast", ["search", "add_card"], 7, 16801, "0.0028"),
            ("", "direct", [], 3, 0, "0"),
            ("create_event", "standard", ["create_event"], 3, 18720, "1E-7"),
            ("open_view", "direct", ["open_view"], 0, 5, "123.456789012345678901234567"),
            ("delete_page", "deep", ["delete_page"], 9, 99, "0.000000045")]:
        conn.execute("INSERT INTO rika_routes(signature,label,tier,path_json,uses,escalations,tokens,cost_usd,example,"
                     "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (signature, routes.label(signature), tier, json.dumps(path), uses, uses // 3, tokens, cost,
                      f"example for {signature or 'talk'}", at(1), at(uses)))
        conn.execute("INSERT INTO rika_route_shapes(shape,signature,updated_at) VALUES(?,?,?)",
                     (f"shape {signature}", signature, at(2)))
    for template, signature, seen, uses in [("flashcard {front} means {back}", "add_card", 3, 2),
                                           ("card {front}", "add_card", 1, 9), ("make {x}", "search>add_card", 2, 0),
                                           ("open {view}", "open_view", 1, 0)]:
        conn.execute("INSERT INTO rika_shortcuts(template,signature,tool,constants_json,seen,uses,created_at,updated_at)"
                     " VALUES(?,?,?,?,?,?,?,?)", (template, signature, "add_card", "{}", seen, uses, at(3), at(4)))
    # The summary's fourteen local days: replies on both sides of Los Angeles midnight and the window's edge.
    user = store.new_id()
    conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,status,created_at) VALUES(?,?,?,?,?,?)",
                 (user, "c3", "user", "Recent", "done", _stamp(base - timedelta(days=15))))
    for index, (hours, cost, status, tokens) in enumerate([
            (0.5, "0.0012", "done", (1000, 400, 200)), (1, None, "failed", (10, None, 5)), (8.5, "1E-7", "done", (None, None, None)),
            (9.5, "0.0001", "done", (300, 300, 1)), (30, "0.3333333333", "done", (5, 5, 5)), (24 * 13 + 2, "1", "done", (1, 0, 1)),
            (24 * 13 + 10, "2", "done", (1, 1, 1)), (24 * 14 + 12, "5", "done", (9, 9, 9)), (2, "7", "pending", (9, 9, 9))]):
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,reply_to,status,cost_usd,tokens_in,tokens_cached,"
                     "tokens_out,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (store.new_id(), "c3", "rika", f"Recent reply {index}", user, status, cost, *tokens,
                      _stamp(base - timedelta(hours=hours))))
    # Preferences and the other sources.
    for text in ("Keep replies short", "Put new flashcards in the Biology deck"):
        conn.execute("INSERT INTO rika_preferences(text,source,created_at,updated_at) VALUES(?,?,?,?)",
                     (text, "added", at(5), at(5)))
    conn.execute("INSERT INTO goals(title,why,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                 ("Learn enzymes", "For the exam", "paused", at(6), at(6)))
    conn.execute("INSERT INTO calendar_events(id,calendar_id,title,start,end,timezone,data,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                 ("e1", "personal", "Enzyme exam", "2031-01-01T09:00:00+00:00", "2031-01-01T10:00:00+00:00", "UTC",
                  json.dumps({"description": "Bring a calculator", "location": "Hall 2"}), at(7)))
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES(?,?,?)",
                 ("k1", json.dumps({"title": "Review enzymes", "description": "Chapter 4"}), at(8)))
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES(?,?,?)", ("k2", json.dumps({"title": 7}), at(9)))
    conn.execute("INSERT INTO record_events(at,section,entity,entity_id,action,route) VALUES(?,?,?,?,?,?)",
                 (at(10), "notes", "page", str(number), "create", "/api/notes/pages"))
    store.recover(conn)
    conn.commit()


def scenario(call, base):
    answers = []
    now = base

    def do(path, body=None, expect=200, **query):
        nonlocal now
        now += timedelta(seconds=1)
        status, answer = call(path, body, now, query)
        assert status == expect, (len(answers), path, body, query, status, answer)
        answers.append((path, status, answer))
        return answer

    # The account's local half.
    do("/api/account")
    do("/api/account", expect=400, x="1")
    for bad in ({}, {"welcome_completed": 1}, {"welcome_completed": None}, {"x": True},
                {"welcome_completed": True, "other": False}):
        do("/api/account/onboarding", bad, 400)
    do("/api/account/onboarding", {"welcome_completed": True}, 400, x="1")
    do("/api/account/onboarding", {"welcome_completed": True})
    do("/api/account/onboarding", {"tutorial_completed": True, "welcome_completed": False})

    # History, paged back to the start.
    page = do(PREFIX + "/history")
    while page["has_more"]:
        page = do(PREFIX + "/history", before=page["next_before"])
    do(PREFIX + "/history", before="2000-01-01T00:00:00+00:00")
    do(PREFIX + "/history", before="9999-99-99T99")
    for bad in ("abc", "2026-01-01;x", "1" * 41, "123456789", "２０２６-01-01T00", ""):
        do(PREFIX + "/history", expect=400, before=bad)
    do(PREFIX + "/history", expect=400, after="2026-01-01T00:00:00")

    # Routing memory.
    do(PREFIX + "/routes")
    do(PREFIX + "/routes", expect=400, x="1")
    for bad in ({}, {"signature": 5}, {"signature": None}, {"signature": "x" * 2001}, {"signature": "add_card", "x": 1}):
        do(PREFIX + "/routes/forget", bad, 400)
    do(PREFIX + "/routes/forget", {"signature": "x" * 2000}, 404)
    do(PREFIX + "/routes/forget", {"signature": "nope"}, 404)
    do(PREFIX + "/routes/forget", {"signature": "add_card"}, 400, x="1")
    do(PREFIX + "/routes/forget", {"signature": "add_card"})
    do(PREFIX + "/routes/forget", {"signature": ""})
    do(PREFIX + "/routes/forget", {"signature": "add_card"}, 404)

    # Memory and preferences.
    do(PREFIX + "/memory")
    do(PREFIX + "/memory", q="")
    do(PREFIX + "/memory", q="  \t")
    do(PREFIX + "/memory", q="enzyme")
    do(PREFIX + "/memory", q="Enzymes lower")
    do(PREFIX + "/memory", q="café STRASSE çafé")
    do(PREFIX + "/memory", q="grace tomorrow")
    do(PREFIX + "/memory", q="thing pages")
    do(PREFIX + "/memory", q=" ".join(f"w{index}" for index in range(20)))
    do(PREFIX + "/memory", q="x" * 300)
    do(PREFIX + "/memory", expect=400, q="x" * 301)
    do(PREFIX + "/memory", expect=400, q="a\x00b")
    do(PREFIX + "/memory", expect=400, x="1")
    do(PREFIX + "/memory", expect=400, q="a", x="1")
    for bad in ({}, {"text": 5}, {"text": None}, {"text": ""}, {"text": " \t\n"}, {"text": "x\x00"}, {"text": "é" * 501},
                {"text": "ok", "x": 1}):
        do(PREFIX + "/preferences", bad, 400)
    do(PREFIX + "/preferences", {"text": "ok"}, 400, x="1")
    added = do(PREFIX + "/preferences", {"text": "  Answer   in\tFrench\u3000please "})["preference"]
    do(PREFIX + "/preferences", {"text": "é" * 500})
    for index in range(36):
        do(PREFIX + "/preferences", {"text": f"Preference {index} " + "y" * 70})
    do(PREFIX + "/preferences", {"text": "one too many"}, 400)
    do(PREFIX + f"/preferences/{added['id']}", {"text": "z" * 500}, 400)
    do(PREFIX + f"/preferences/{added['id']}", {"text": "Answer in German"})
    do(PREFIX + "/preferences/" + "١", {"text": "Arabic-Indic one"})
    do(PREFIX + "/preferences/999", {"text": "missing"}, 404)
    do(PREFIX + "/preferences/999", {"text": ""}, 400)
    do(PREFIX + f"/preferences/{added['id']}", {"text": "x", "y": 1}, 400)
    do(PREFIX + f"/preferences/{added['id']}", {}, 400)
    do(PREFIX + "/preferences/0", {"text": "zero"}, 404)
    do(PREFIX + "/preferences/" + "9" * 18, {"text": "large"}, 404)
    do(PREFIX + "/preferences/" + "9" * 19, {"text": "too long"}, 404)
    do(PREFIX + "/preferences/x", {"text": "letters"}, 404)
    do(PREFIX + f"/preferences/{added['id']}/delete", {"x": 1}, 400)
    do(PREFIX + f"/preferences/{added['id']}/delete", {})
    do(PREFIX + f"/preferences/{added['id']}/delete", {}, 404)
    do(PREFIX + "/preferences/٢/delete", {})
    do(PREFIX + "/preferences", {"text": "Now there is room"})
    do(PREFIX + "/memory", q="room german")
    do(PREFIX + "/memory/rebuild", {"x": 1}, 400)
    do(PREFIX + "/memory/rebuild", {}, 400, x="1")
    do(PREFIX + "/memory/rebuild", {})
    do(PREFIX + "/memory", q="preference")
    do(PREFIX + "/routes")
    return answers


TABLES = {
    "rika_preferences": "id", "rika_index_sources": "source", "rika_routes": "signature", "rika_route_shapes": "shape",
    "rika_shortcuts": "template", "workspace_onboarding": "id", "record_events": "seq",
    "rika_messages": "id", "rika_actions": "id", "rika_approvals": "id", "rika_plans": "id", "rika_mail_drafts": "id",
}


def stored(path):
    with closing(connect(path)) as conn:
        found = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
                 for table, order in TABLES.items()}
        for row in found["record_events"]:
            row.pop("at")
        found["rika_index"] = [tuple(row) for row in conn.execute(
            "SELECT rowid,title,body,source,ref,at FROM rika_index ORDER BY rowid")]
        return found


def prepare(workspace, tmp_path, modules):
    base = base_time()
    seed(workspace, base)
    if modules != MODULE_IDS:
        (workspace.settings.root / "config" / "build.json").write_text(
            json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    reference_path = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, reference_path)
    return base, reference_path


def reference_run(reference_path, base, monkeypatch, modules, configured):
    with monkeypatch.context() as patch:
        patch.setattr(store, "now", Clock.now)
        patch.setattr(routes, "datetime", FrozenDatetime)
        reference = Reference(reference_path, modules, configured)
        try:
            return scenario(reference, base)
        finally:
            reference.conn.close()


CASES = [(MODULE_IDS, True), (("applyai",), False)]


@pytest.mark.parametrize(("modules", "configured"), CASES)
def test_reference_scenario(workspace, tmp_path, monkeypatch, modules, configured, record_property):
    """Executable oracle sanity check, usable before the Rust implementation exists."""
    base, path = prepare(workspace, tmp_path, modules)
    answers = reference_run(path, base, monkeypatch, modules, configured)
    record_property("requests", len(answers))
    assert len(answers) > 100
    pages = [answer for route, status, answer in answers if route == PREFIX + "/history" and status == 200]
    assert len(pages) >= 3 and pages[0]["has_more"]
    statuses = {status for _, status, _ in answers}
    assert statuses == {200, 400, 404}
    sources = {result["source"] for _, status, answer in answers if status == 200 and isinstance(answer, dict)
               for result in answer.get("results", [])}
    assert {"preference", "mail", "goal", "oplog"} <= sources
    assert ({"page", "block", "event", "task"} <= sources) == ("workspace" in modules)


@pytest.mark.parametrize(("modules", "configured"), CASES)
def test_the_front_answers_rika_data_as_the_python_core_does(workspace, tmp_path, monkeypatch, modules, configured):
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER})")
    base, reference_path = prepare(workspace, tmp_path, modules)
    expected = reference_run(reference_path, base, monkeypatch, modules, configured)
    clock = tmp_path / "clock"
    clock.write_text(base.isoformat(), encoding="utf-8")
    env = {"KITTY_TEST_CLOCK": str(clock), **(GOOGLE if configured else dict.fromkeys(GOOGLE, ""))}
    with front_site(workspace.settings, env=env) as site:
        actual = scenario(Through(site, clock), base)
    assert len(actual) == len(expected)
    for index, (answer, wanted) in enumerate(zip(actual, expected, strict=True)):
        assert answer == wanted, (index, answer[0])
    left, right = stored(workspace.settings.db_path), stored(reference_path)
    for table in right:
        assert left[table] == right[table], table


def test_the_worker_answers_only_the_python_rika_routes(workspace, tmp_path):
    conn = workspace.conn
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        for path in ("/api/account", PREFIX + "/history", PREFIX + "/routes", PREFIX + "/memory"):
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(conn, path, {})
        for path in ("/api/account/onboarding", PREFIX + "/routes/forget", PREFIX + "/preferences", PREFIX + "/preferences/1",
                     PREFIX + "/preferences/1/delete", PREFIX + "/memory/rebuild"):
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(conn, path, {})
    finally:
        app.close()
    conn.close()
    with front_site(workspace.settings) as site:
        status, agents = site.request(PREFIX + "/agents")
        assert status == 200 and "agents" in agents
        status, usage = site.request("/api/usage")
        assert status == 200 and "kinds" in usage
        assert site.request("/api/account/google/start", {}) == (
            503, {"error": "Google connection is not configured. You can continue locally."})
        assert site.request(PREFIX + "/plans/" + "0" * 32, {"decision": "dismiss"})[0] == 404
