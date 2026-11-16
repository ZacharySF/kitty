"""The mind-map routes answered by rika-server (server/src/schema) against `kitty.core.schema_maps`:
one scenario over every route, with the refusals between, run through the front and through
the Python core on two copies of the same workspace, at the same clock readings. Every
answer, the rows left behind (the stored documents byte for byte) and the content events
recorded must be the same (random ids aside)."""
from __future__ import annotations

import json
import re
import shutil
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from terminal_support import front_site

from kitty.core import jobs, notes, record_events, schema_maps
from kitty.db import connect
from kitty.modules import ModuleDisabledError

UTC = timezone.utc
BASE = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
LATER = BASE + timedelta(hours=3, microseconds=250)
HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
JOB_ROUTES = {"/api/schema/jobs", "/api/schema/import-jobs"}


class Masked:
    """Random ids by order of first appearance, so two runs compare."""

    def __init__(self):
        self.seen: dict[str, str] = {}

    def __call__(self, value):
        if isinstance(value, dict):
            return {self(key): self(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, str):
            return HEX.sub(lambda found: self.seen.setdefault(found[0], f"id-{len(self.seen)}"), value)
        return value


def rows(db_path, mask):
    """The stored maps as text (so the documents compare byte for byte) and the events."""
    with closing(connect(db_path)) as conn:
        maps = [dict(row) for row in conn.execute("SELECT * FROM schema_maps ORDER BY id")]
        events = [list(row) for row in conn.execute("SELECT section,entity,entity_id,action,route FROM record_events ORDER BY seq")]
    return mask(maps), events


class Front:
    """The front on one workspace, restarted when the clock reading changes."""

    def __init__(self, settings):
        self.settings, self.now, self.context, self.site = settings, None, None, None

    def __call__(self, path, body, now, query):
        if now != self.now:
            self.close()
            self.context = front_site(self.settings, {"KITTY_TEST_NOW": now.isoformat()})
            self.site, self.now = self.context.__enter__(), now
        return self.site.request(path + ("?" + urlencode(query) if query else ""), body)

    def close(self):
        if self.context is not None:
            self.context.__exit__(None, None, None)
            self.context = None


class Refused(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Clock:
    """`schema_maps.datetime` for the reference: the reading the front is given."""
    value = BASE

    @classmethod
    def now(cls, zone=None):
        return cls.value


def integer(text, message):
    try:
        return int(text)
    except (ValueError, TypeError):
        raise Refused(400, message) from None


def applyai_sources(nodes):
    return {(node["source"].get("kind"), str(node["source"].get("id"))) for node in nodes
            if isinstance(node, dict) and isinstance(node.get("source"), dict)
            and node["source"].get("kind") in schema_maps.APPLYAI_KINDS}


class Reference:
    """The routes as `kitty.web_schema` answered them over the core (the handlers are deleted),
    the module guard before them and the events `WebApplication.mutate` recorded after."""

    def __init__(self, db_path, applyai=True):
        self.conn, self.applyai, self.events = connect(db_path), applyai, []

    def get(self, path, query):
        conn = self.conn
        if path == "/api/schema":
            if query:
                raise Refused(400, "This route does not accept query parameters")
            return schema_maps.workspace(conn)
        if path in {"/api/schema/map", "/api/schema/export"}:
            if set(query) != {"id"}:
                raise Refused(400, "Provide a map ID only")
            item_id = integer(query["id"], "Map ID must be an integer")
            return (schema_maps.load if path.endswith("/map") else schema_maps.export)(conn, item_id)
        if path == "/api/schema/jobs":
            if set(query) - {"search", "offset"}:
                raise Refused(400, "Unknown jobs query parameter")
            offset = integer(query.get("offset", "0"), "Offset must be an integer")
            return schema_maps.job_options(conn, query.get("search", ""), offset)
        assert path == "/api/schema/network", path
        if set(query) - {"mode", "map_ids", "merge_labels"}:
            raise Refused(400, "Unknown network query parameter")
        flag = query.get("merge_labels", "false")
        if flag not in {"true", "false", "1", "0"}:
            raise Refused(400, "merge_labels must be true or false")
        map_ids = None
        if query.get("map_ids"):
            map_ids = [integer(value, "Map IDs must be comma-separated integers") for value in query["map_ids"].split(",")]
        return schema_maps.network(conn, query.get("mode") or None, map_ids, flag in {"true", "1"})

    def reject_new_applyai_sources(self, path, body):
        """Without ApplyAI no job or company link may be created; existing links are kept."""
        existing = set()
        if path == "/api/schema/add-node":
            found = applyai_sources([body])
        elif path == "/api/schema/save":
            graph = body.get("graph") if isinstance(body.get("graph"), dict) else {}
            found = applyai_sources(graph.get("nodes") if isinstance(graph.get("nodes"), list) else [])
            row = self.conn.execute("SELECT graph_json FROM schema_maps WHERE id=?", (body.get("id"),)).fetchone() \
                if isinstance(body.get("id"), int) else None
            existing = applyai_sources(json.loads(row["graph_json"]).get("nodes", [])) if row else set()
        elif path in {"/api/schema/create", "/api/schema/import"}:
            document = body.get("document") if isinstance(body.get("document"), dict) else {}
            graph = document.get("graph") if isinstance(document.get("graph"), dict) else {}
            found = applyai_sources(graph.get("nodes") if isinstance(graph.get("nodes"), list) else [])
        else:
            return
        if found - existing:
            raise Refused(403, str(ModuleDisabledError("applyai")))

    def post(self, path, body, query):
        if query:
            raise Refused(400, "This route does not accept query parameters")
        handler = {"create": schema_maps.create, "import": schema_maps.create, "add-node": schema_maps.add_node,
                   "save": schema_maps.save, "delete": schema_maps.delete, "undo": schema_maps.undo,
                   "import-jobs": schema_maps.import_jobs, "transfer": schema_maps.transfer}[path.removeprefix("/api/schema/")]
        if not self.applyai:
            self.reject_new_applyai_sources(path, body)
        answer = handler(self.conn, body)
        event = record_events.classify(path, body, answer, self.conn)
        if event is not None:
            self.events.append([*event, path])
        return answer

    def __call__(self, path, body, now, query):
        Clock.value = now
        try:
            if path in JOB_ROUTES and not self.applyai:
                raise Refused(403, str(ModuleDisabledError("applyai")))
            with schema_maps.applyai_scope(self.applyai):
                answer = self.get(path, query) if body is None else self.post(path, body, query)
        except Refused as refused:
            return refused.status, {"error": refused.message}
        except schema_maps.SchemaConflict as refused:
            return 409, {"error": str(refused)}
        except ValueError as refused:
            return 400, {"error": str(refused)}
        except Exception:  # what `RequestHandler._dispatch` answers for anything else
            return 500, {"error": "Local operation failed; inspect CLI diagnostics"}
        return 200, json.loads(json.dumps(answer))


def node(key, kind="topic", source=None, **extra):
    return {"id": key, "kind": kind, "label": key, "notes": "", "x": 0, "y": 0, "url": "", "source": source, **extra}


def edge(key, start, end, label="", **extra):
    return {"id": key, "from": start, "to": end, "label": label, **extra}


def seed(conn):
    """Records the maps link to, written before the workspace is copied."""
    subject = conn.execute("INSERT INTO study_subjects(name) VALUES('Linear Algebra')").lastrowid
    gone = conn.execute("INSERT INTO study_subjects(name) VALUES('Dropped later')").lastrowid
    notebook = notes.create_notebook(conn, "Synthetic notebook")["id"]
    page = notes.create_page(conn, notebook, "Synthetic note card")["id"]
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES(?,?,?)",
                 ("synthetic-task", '{"title":"Review synthetic chapter"}', "2026-01-01"))
    first, _ = jobs.upsert_job(conn, company="Example Company", title="Developer", url="https://example.invalid/a")
    second, _ = jobs.upsert_job(conn, company="Example Company", title="Data Analyst", url="javascript:alert(1)")
    third, _ = jobs.upsert_job(conn, company="Other \u00e9 Co", title="Developer advocate", url="https://user:pw@example.invalid/c")
    conn.execute("INSERT INTO applications(job_id,status,applied_at,last_activity_at,created_at,updated_at)"
                 " VALUES(?,'interview','now','now','now','now')", (first.id,))
    conn.execute("UPDATE companies SET website='https://example.invalid/company' WHERE id=?", (first.company_id,))
    conn.commit()
    return {"subject": subject, "gone": gone, "page": page, "jobs": [first.id, second.id, third.id], "company": first.company_id}


def scenario(call, seeded):
    """Every mind-map route, with the refusals between: returns each (path, status, answer) in order."""
    answers = []

    def do(path, body=None, now=BASE, expect=200, **query):
        status, answer = call(path, body, now, query)
        assert status == expect, (path, body, query, answer)
        answers.append((path, status, answer))
        return answer

    def save(item, graph=..., expect=200, now=BASE, **extra):
        return do("/api/schema/save", {"id": item["id"], "revision": item["revision"], "graph": item["graph"] if graph is ... else graph,
                                       "viewport": item["viewport"], **extra}, expect=expect, now=now)

    subject = {"kind": "subject", "id": seeded["subject"]}
    do("/api/schema")
    do("/api/schema", expect=400, x="1")
    study = do("/api/schema/create", {"title": "  Study map ", "mode": "study"})
    career = do("/api/schema/create", {"title": "Career", "mode": "jobs"})
    ideas = do("/api/schema/create", {"title": "Ideas \u00e9\u4e16 \U0001f600"})
    portable = {"format": "kitty-schema", "version": 1, "title": "x", "mode": "general", "graph": {"nodes": [], "edges": []},
                "viewport": {"x": 0, "y": 0, "zoom": 1}}
    for body in ({"title": ""}, {"title": "x", "mode": "other"}, {"title": "x", "mode": ["study"]}, {"title": "x", "extra": 1}, {"title": "x" * 201},
                 {"title": 7}, {"title": "x\u0000"}, {"title": "x", "document": None}, {"title": "x", "document": {**portable, "format": "other"}},
                 {"title": "x", "document": {**portable, "version": 1.0}}, {"title": "x", "document": {**portable, "version": True}},
                 {"title": "x", "document": {**portable, "title": " "}}, {"title": "x", "document": {**portable, "mode": "x"}},
                 {"title": "x", "document": {**portable, "graph": {"nodes": [node("a", "class", subject)], "edges": []}}},
                 {"title": "x", "document": {**portable, "graph": {"nodes": [node("a", map_id=1)], "edges": []}}},
                 {"title": "x", "document": {**portable, "viewport": {"x": 0, "y": 0}}}, {"title": "x", "document": {**portable, "more": 1}}):
        do("/api/schema/create", body, expect=400)
    do("/api/schema/create", {"title": "x"}, expect=400, q="1")

    # A document with every optional field, floats as Python writes them and ink.
    full = {"nodes": [node("algebra", "class", subject, label="  Algebra  ", x=0.30000000000000004, y=-450.5, collapsed=False),
                      node("vectors", notes="caf\u00e9 \u4e16 \"quoted\"\n\U0001f600", x=1e-7, y=100000.0, free=True, color="c4"),
                      node("proofs", "skill", url=" https://Example.invalid:443/a?b#c ", x=-100000, y=1e5, collapsed=True, free=False),
                      node("essay", "idea", {"kind": "note_page", "id": seeded["page"]}, x=123456.5e-3, y=2, map_id=ideas["id"]),
                      node("review", "task", {"kind": "calendar_task", "id": "synthetic-task"})],
            "edges": [edge("part", "vectors", "algebra", " Part  Of "), edge("needs", "proofs", "vectors", "needs", kind="cross"),
                      edge("tree", "algebra", "essay", kind="tree"), edge("self", "review", "review")],
            "strokes": [{"id": "tick", "node": "algebra", "points": [[0, 0, 0.4], [12.5, -3, 0.9], [1e-5, 1.0, 1]]},
                        {"id": "note", "node": "essay", "points": [[-4, 50.25, 0]]}]}
    saved = save(study, full, title="Study map II", viewport={"x": -30.25, "y": 65, "zoom": 0.7})
    save(study, full, expect=409)
    save({**saved, "revision": True}, expect=409)
    save({**saved, "id": 0}, expect=400)
    save({**saved, "id": "1"}, expect=400)
    save({**saved, "id": 999}, expect=400)
    save(saved, extra=1, expect=400)
    save(saved, title=" ", expect=400)

    def broken(nodes=None, edges=None, strokes=None, **top):
        return {"nodes": nodes if nodes is not None else [node("a"), node("b")], "edges": edges if edges is not None else [],
                **({"strokes": strokes} if strokes is not None else {}), **top}

    for graph in (
        None, [], {"nodes": []}, broken(nodes="x"), broken(strokes=None, remote="https://example.invalid/"), {"nodes": [], "edges": [], "strokes": None},
        broken([node(str(index)) for index in range(301)]), broken([node("same"), node("same")]), broken(["a"]), broken([node("n", extra=1)]),
        broken([node("")]), broken([node("a b")]), broken([node("x" * 81)]), broken([node(7)]), broken([node("n", kind=[])]), broken([node("n", kind="unknown")]),
        broken([node("n", x=True)]), broken([node("n", x=100000.5)]), broken([node("n", y="1")]), broken([node("n", y=None)]),
        broken([node("n", url="javascript:alert(1)")]), broken([node("n", url="https://user:pass@example.invalid/")]), broken([node("n", url="//example.invalid/")]),
        broken([node("n", url="http://h:99999/")]), broken([node("n", url="http://[::1/")]), broken([node("n", url="http://h/a b")]), broken([node("n", url=5)]),
        broken([node("n", url="x" * 4001)]), broken([node("n", label="a" * 201)]), broken([node("n", label="  ")]), broken([node("n", notes=None)]),
        broken([node("n", notes="x" * 10001)]), broken([node("n", collapsed=1)]), broken([node("n", free="yes")]), broken([node("n", color="c7")]),
        broken([node("n", color=4)]), broken([node("n", map_id=999)]), broken([node("n", map_id="1")]), broken([node("n", map_id=0)]),
        broken([node("n", "class", {"kind": "subject", "id": True})]), broken([node("n", "class", {"kind": "job", "id": seeded["subject"]})]),
        broken([node("n", "class", {"kind": "subject", "id": 999999})]), broken([node("n", "topic", subject)]), broken([node("n", "class", {"kind": "subject"})]),
        broken([node("n", "class", {"kind": "subject", "id": 1, "more": 1})]), broken([node("n", "class", "subject")]),
        broken([node("n", "topic", {"kind": "calendar_task", "id": "synthetic-task"})]), broken([node("n", "task", {"kind": "calendar_task", "id": "missing"})]),
        broken([node("n", "task", {"kind": "calendar_task", "id": 5})]), broken([node("n", "topic", {"kind": "note_page", "id": 999999})]),
        broken([node("n", "topic", {"kind": "note_page", "id": "1"})]), broken([node("a", "class", subject), node("b", "class", subject)]),
        broken(edges=[edge("e", "a", "missing")]), broken(edges=[edge("e", 1, "a")]), broken(edges=[edge("e", "a", "b"), edge("e", "b", "a")]),
        broken(edges=[edge("e", "a", "b", kind="bogus")]), broken(edges=[edge("e", "a", "b", kind=None)]), broken(edges=[edge("e", "a", "b", label=None)]),
        broken(edges=[edge("e", "a", "b", more=1)]), broken(edges=["e"]), broken(edges=[edge("bad id", "a", "b")]),
        broken(strokes=[{"id": "s", "node": "missing", "points": [[0, 0, 0.5]]}]), broken(strokes=[{"id": "s", "node": 5, "points": [[0, 0, 0.5]]}]),
        broken(strokes=[{"id": "s", "node": "a", "points": []}]), broken(strokes=[{"id": "s", "node": "a", "points": [[0, 0]]}]),
        broken(strokes=[{"id": "s", "node": "a", "points": [[0, 0, 1.5]]}]), broken(strokes=[{"id": "s", "node": "a", "points": [[0, "x", 0.5]]}]),
        broken(strokes=[{"id": "s", "node": "a", "points": [[0, 0, 0.5]] * 1001}]), broken(strokes=[{"id": "s", "node": "a", "points": [[0, 0, 0.5]], "colour": "red"}]),
        broken(strokes=[{"id": "s", "node": "a", "points": [[0, 0, 0.5]]}, {"id": "s", "node": "b", "points": [[0, 0, 0.5]]}]),
        broken(strokes=[{"id": f"s{index}", "node": "a", "points": [[0, 0, 0.5]]} for index in range(401)]), broken(strokes="ink"),
        broken([node(str(index), notes="x" * 10000) for index in range(110)]),
    ):
        save(saved, graph, expect=400)
    # An unhashable value where Python looks a string up is its TypeError: the generic 500.
    save(saved, broken(strokes=[{"id": "s", "node": ["a"], "points": [[0, 0, 0.5]]}]), expect=500)
    for view in ({"x": 0, "y": 0, "zoom": 0}, {"x": 0, "y": 0, "zoom": True}, {"x": 0, "y": 0, "zoom": 1, "w": 1}, {"x": 1e7 + 1, "y": 0, "zoom": 1},
                 {"x": 0, "y": "0", "zoom": 1}, None, [0, 0, 1]):
        save(saved, viewport=view, expect=400)

    # Reads of one map.
    do("/api/schema/map", id=str(study["id"]))
    do("/api/schema/map", id=f" +{study['id']} ")
    do("/api/schema/export", id=str(study["id"]))
    for query in ({}, {"id": "bad"}, {"id": "1.0"}, {"id": ""}, {"id": "999"}, {"id": "0"}, {"id": "-1"}, {"id": "99999999999999999999"},
                  {"id": str(study["id"]), "x": "2"}, {"map": str(study["id"])}):
        do("/api/schema/map", expect=400, **query)
        do("/api/schema/export", expect=400, **query)

    # Nodes added one at a time: free, under a parent, linked to a record, the same record again.
    blank = do("/api/schema/add-node", {"map_id": ideas["id"], "revision": ideas["revision"], "label": " First idea "})
    child = do("/api/schema/add-node", {"map_id": ideas["id"], "revision": blank["revision"], "label": "Child", "kind": "idea",
                                        "parent_id": blank["graph"]["nodes"][0]["id"]})
    task = {"map_id": ideas["id"], "revision": child["revision"], "label": "Review chapter", "kind": "task", "parent_id": child["graph"]["nodes"][1]["id"],
            "source": {"kind": "calendar_task", "id": "synthetic-task"}}
    linked = do("/api/schema/add-node", task)
    do("/api/schema/add-node", task, expect=409)
    do("/api/schema/add-node", {**task, "revision": linked["revision"], "label": "Again"})
    carded = do("/api/schema/add-node", {"map_id": ideas["id"], "revision": linked["revision"], "label": "Card", "source": {"kind": "note_page", "id": seeded["page"]}})
    for change in ({"parent_id": "missing"}, {"label": None}, {"label": ""}, {"kind": "planet"}, {"map_id": 999}, {"map_id": None}, {"extra": 1},
                   {"source": {"kind": "subject", "id": seeded["subject"]}}, {"kind": "class", "source": {"kind": "subject", "id": 999999}},
                   {"kind": "class", "source": {"kind": "note_page", "id": 999999}}, {"kind": 5, "source": {"kind": "subject", "id": 1}}, {"parent_id": 5}):
        do("/api/schema/add-node", {"map_id": ideas["id"], "revision": carded["revision"], "label": "x", **change}, expect=400)
    do("/api/schema/add-node", {"map_id": ideas["id"], "revision": carded["revision"], "label": "x", "kind": ["class"], "source": {"kind": "subject", "id": 1}}, expect=500)

    # Tracked jobs: the options, the import, the import again.
    first, second, third = seeded["jobs"]
    for query in ({}, {"search": "dev"}, {"search": " ANALYST "}, {"offset": "1"}, {"search": "nothing"}, {"offset": "1_0"}):
        do("/api/schema/jobs", **query)
    for query in ({"offset": "x"}, {"offset": "-1"}, {"offset": "1000001"}, {"unknown": "1"}, {"search": "x" * 201}):
        do("/api/schema/jobs", expect=400, **query)
    imported = do("/api/schema/import-jobs", {"id": career["id"], "revision": career["revision"], "job_ids": [first, second, first]})
    do("/api/schema/import-jobs", {"id": career["id"], "revision": imported["revision"], "job_ids": [second, first]})
    imported = do("/api/schema/import-jobs", {"id": career["id"], "revision": imported["revision"], "job_ids": [third]})
    do("/api/schema/import-jobs", {"id": career["id"], "revision": career["revision"], "job_ids": [first]}, expect=409)
    for body in ({"id": career["id"], "revision": imported["revision"], "job_ids": [first, 999999]}, {"id": career["id"], "revision": imported["revision"], "job_ids": []},
                 {"id": career["id"], "revision": imported["revision"], "job_ids": list(range(1, 52))}, {"id": career["id"], "revision": imported["revision"], "job_ids": ["1"]},
                 {"id": career["id"], "revision": imported["revision"], "job_ids": [True]}, {"id": career["id"], "revision": imported["revision"], "job_ids": "1"},
                 {"id": study["id"], "revision": saved["revision"], "job_ids": [first]}, {"id": career["id"], "revision": imported["revision"], "job_ids": [first], "x": 1}):
        do("/api/schema/import-jobs", body, expect=400)
    do("/api/schema/map", id=str(career["id"]))
    exported = do("/api/schema/export", id=str(career["id"]))
    copy = do("/api/schema/import", {"title": "Imported", "mode": exported["mode"], "document": exported})
    do("/api/schema/import", {"title": "Rebound", "document": {**exported, "graph": {"nodes": [node("a", "job", {"kind": "job", "id": first})], "edges": []}}}, expect=400)

    # Undo and redo of a plain save.
    undone = do("/api/schema/undo", {"id": study["id"], "revision": saved["revision"]}, now=LATER)
    do("/api/schema/undo", {"id": study["id"], "revision": saved["revision"]}, now=LATER, expect=409)
    do("/api/schema/undo", {"id": study["id"], "revision": undone["revision"]}, now=LATER, expect=400)
    do("/api/schema/undo", {"id": study["id"], "revision": undone["revision"], "redo": True}, now=LATER, expect=400)
    do("/api/schema/undo", {"id": study["id"], "revision": undone["revision"], "more": 1}, now=LATER, expect=400)
    do("/api/schema/undo", {"id": copy["id"], "revision": copy["revision"], "redo": 1}, now=LATER, expect=400)
    saved = save(undone, full, now=LATER)

    # A branch split into its own map, the pair undone and redone together, then merged back.
    tree = do("/api/schema/create", {"title": "Tree", "mode": "general"}, now=LATER)
    tree = save(tree, {"nodes": [node("root", x=10.5, y=20), node("branch", x=310.25, y=-40, map_id=ideas["id"]), node("leaf", x=600, y=0.1, color="c2"),
                                 node("twig", x=99990, y=-99990.5), node("aside", x=5, y=5)],
                       "edges": [edge("a", "root", "branch"), edge("b", "branch", "leaf", "has"), edge("c", "leaf", "twig"), edge("x", "aside", "leaf", "see", kind="cross"),
                                 edge("y", "leaf", "branch", "back", kind="cross"), edge("loop", "leaf", "leaf")],
                       "strokes": [{"id": "on-root", "node": "root", "points": [[0, 0, 0.5]]}, {"id": "on-leaf", "node": "leaf", "points": [[1.5, 2, 1]]},
                                   {"id": "on-branch", "node": "branch", "points": [[3, 4, 0]]}]}, now=LATER)
    for body in ({"id": tree["id"], "revision": tree["revision"], "node_id": "missing"}, {"id": tree["id"], "revision": tree["revision"]},
                 {"id": tree["id"], "revision": tree["revision"], "node_id": 5}, {"id": tree["id"], "revision": tree["revision"], "node_id": "branch", "x": 1},
                 {"id": tree["id"], "revision": tree["revision"], "node_id": "branch", "target_id": tree["id"], "target_revision": tree["revision"]},
                 {"id": tree["id"], "revision": tree["revision"], "node_id": "branch", "target_id": 999, "target_revision": 1},
                 {"id": tree["id"], "revision": tree["revision"], "node_id": "branch", "target_id": ideas["id"], "target_revision": carded["revision"]}):
        do("/api/schema/transfer", body, now=LATER, expect=400)
    do("/api/schema/transfer", {"id": tree["id"], "revision": tree["revision"], "node_id": ["branch"]}, now=LATER, expect=500)
    do("/api/schema/transfer", {"id": tree["id"], "revision": tree["revision"] + 1, "node_id": "branch"}, now=LATER, expect=409)
    do("/api/schema/transfer", {"id": tree["id"], "revision": tree["revision"], "node_id": "root", "target_id": ideas["id"], "target_revision": 1}, now=LATER, expect=409)
    split = do("/api/schema/transfer", {"id": tree["id"], "revision": tree["revision"], "node_id": "branch"}, now=LATER)
    left = do("/api/schema/map", now=LATER, id=str(tree["id"]))
    undone = do("/api/schema/undo", {"id": split["id"], "revision": split["revision"]}, now=LATER)
    do("/api/schema/undo", {"id": undone["id"], "revision": undone["revision"]}, now=LATER, expect=400)
    redone = do("/api/schema/undo", {"id": undone["id"], "revision": undone["revision"], "redo": True}, now=LATER)
    left = do("/api/schema/map", now=LATER, id=str(tree["id"]))
    merged = do("/api/schema/transfer", {"id": redone["id"], "revision": redone["revision"], "node_id": redone["graph"]["nodes"][0]["id"],
                                         "target_id": left["id"], "target_revision": left["revision"]}, now=LATER)
    emptied = do("/api/schema/map", now=LATER, id=str(split["id"]))
    # A root dropped into a map that has no place kept for it: laid out beside what is there.
    ideas_now = do("/api/schema/map", now=LATER, id=str(ideas["id"]))
    dropped = do("/api/schema/transfer", {"id": merged["id"], "revision": merged["revision"], "node_id": "aside",
                                          "target_id": ideas["id"], "target_revision": ideas_now["revision"]}, now=LATER)
    tree_now = do("/api/schema/map", now=LATER, id=str(tree["id"]))
    # Far from the origin, the moved nodes stop at the edge of the canvas.
    far = save(tree_now, {**tree_now["graph"], "nodes": [{**item, "x": -99999.5, "y": 99999.5} if item["id"] == "root" else item for item in tree_now["graph"]["nodes"]]}, now=LATER)
    crowded = do("/api/schema/transfer", {"id": far["id"], "revision": far["revision"], "node_id": "root", "target_id": ideas["id"], "target_revision": dropped["revision"]}, now=LATER)
    assert {100000, -100000} <= {value for item in crowded["graph"]["nodes"] for value in (item["x"], item["y"])}
    do("/api/schema/transfer", {"id": study["id"], "revision": saved["revision"], "node_id": "algebra",
                                "target_id": ideas["id"], "target_revision": crowded["revision"]}, now=LATER, expect=400)
    do("/api/schema/transfer", {"id": study["id"], "revision": saved["revision"], "node_id": "proofs",
                                "target_id": ideas["id"], "target_revision": crowded["revision"]}, now=LATER)
    # The other window changed the partner: the pair is not undone.
    ideas_now = do("/api/schema/map", now=LATER, id=str(ideas["id"]))
    save(ideas_now, title="Ideas, renamed", now=LATER)
    study_now = do("/api/schema/map", now=LATER, id=str(study["id"]))
    do("/api/schema/undo", {"id": study["id"], "revision": study_now["revision"]}, now=LATER, expect=409)

    # The merged network. Unsourced nodes are keyed by their ids, which are random where the
    # core made them, so maps holding those are read with labels merged (keyed by label).
    end = LATER + timedelta(days=1)
    shared = do("/api/schema/create", {"title": "Shared", "mode": "general"}, now=end)
    save(shared, {"nodes": [node("class", "class", subject, label="Algebra (saved)", url="https://example.invalid/class"),
                            node("role", "job", {"kind": "job", "id": first}), node("firm", "company", {"kind": "company", "id": seeded["company"]}),
                            node("vectors", label="VECTORS"), node("proofs", "skill"), node("lonely"), node("sharp", label="Stra\u00dfe  \u0130"),
                            node("plain", label="STRASSE i\u0307")],
                  "edges": [edge("needs", "role", "class", "Needs"), edge("needs2", "role", "class", " needs "), edge("part", "vectors", "class", "part of"),
                            edge("at", "role", "firm", "at"), edge("loop", "lonely", "lonely", "self")]}, now=end)
    fixed = f"{study['id']},{career['id']}, {shared['id']},{career['id']}"
    for query in ({"map_ids": fixed}, {"map_ids": fixed, "merge_labels": "true"}, {"merge_labels": "true"}, {"merge_labels": "1", "mode": "jobs"},
                  {"mode": "study"}, {"mode": "", "merge_labels": "true"}, {"map_ids": "", "merge_labels": "true"},
                  {"map_ids": f"{career['id']},{copy['id']}", "merge_labels": "true"}, {"map_ids": str(ideas["id"]), "mode": "jobs", "merge_labels": "0"}):
        do("/api/schema/network", now=end, **query)
    for query in ({"map_ids": "x"}, {"merge_labels": "maybe"}, {"mode": "other"}, {"map_ids": "999999"}, {"map_ids": ",".join(str(index) for index in range(1, 102))},
                  {"map_ids": "0"}, {"map_ids": "1,,2"}, {"map_ids": "99999999999999999999"}, {"unknown": "1"}):
        do("/api/schema/network", now=end, expect=400, **query)

    # Deleting: the confirmation, the revision, and a pair whose partner is gone.
    do("/api/schema/delete", {"id": copy["id"], "revision": copy["revision"]}, now=end, expect=400)
    do("/api/schema/delete", {"id": copy["id"], "revision": copy["revision"], "confirm": 1}, now=end, expect=400)
    do("/api/schema/delete", {"id": copy["id"], "revision": copy["revision"] + 1, "confirm": True}, now=end, expect=409)
    do("/api/schema/delete", {"id": copy["id"], "revision": copy["revision"], "confirm": True, "x": 1}, now=end, expect=400)
    do("/api/schema/delete", {"id": copy["id"], "revision": copy["revision"], "confirm": True}, now=end)
    do("/api/schema/delete", {"id": copy["id"], "revision": copy["revision"], "confirm": True}, now=end, expect=400)
    tree_now = do("/api/schema/map", now=end, id=str(tree["id"]))
    do("/api/schema/delete", {"id": tree["id"], "revision": tree_now["revision"], "confirm": True}, now=end)
    emptied = do("/api/schema/map", now=end, id=str(emptied["id"]))
    do("/api/schema/undo", {"id": emptied["id"], "revision": emptied["revision"]}, now=end, expect=400)
    do("/api/schema/map", now=end, expect=400, id=str(copy["id"]))
    do("/api/schema", now=end)
    do("/api/schema/network", now=end, merge_labels="true")
    return answers


def compare(workspace, tmp_path, monkeypatch, run, applyai=True):
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    copy = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, copy)
    monkeypatch.setattr(schema_maps, "datetime", Clock)

    reference, front = Reference(copy, applyai), Front(workspace.settings)
    try:
        expected = run(reference)
        answered = run(front)
    finally:
        front.close()
        reference.conn.close()
    left, right = Masked(), Masked()
    assert len(answered) == len(expected)
    for (path, status, answer), (_, wanted_status, wanted) in zip(answered, expected, strict=True):
        assert (status, left(answer)) == (wanted_status, right(wanted)), path
    stored, events = rows(workspace.settings.db_path, left)
    reference_rows, _ = rows(copy, right)
    assert stored == reference_rows
    assert left(events) == right(reference.events)
    return expected, stored, events


def test_the_front_answers_mind_maps_as_the_python_core_does(workspace, tmp_path, monkeypatch):
    seeded = seed(workspace.conn)
    expected, stored, events = compare(workspace, tmp_path, monkeypatch, lambda call: scenario(call, seeded))
    # Floats are stored as Python writes them, and the undo history is in the rows compared.
    assert any('"x": 0.30000000000000004' in row["graph_json"] and '"x": 1e-07' in row["graph_json"] for row in stored)
    assert any(row["previous_json"] and '"partner"' in row["previous_json"] for row in stored)
    assert ["schema", "map", "1", "create", "/api/schema/create"] in events
    assert {event[3] for event in events} >= {"create", "import", "add-node", "save", "delete", "undo", "import-jobs"}
    assert any(event[4] == "/api/schema/transfer" for event in events)
    final = expected[-1][2]
    assert final["unified"] >= 1 and len(final["clusters"]) > 1


def without_applyai(call, seeded):
    """With the workspace alone: job and company links stay but show nothing, and none is made."""
    answers = []

    def do(path, body=None, expect=200, **query):
        status, answer = call(path, body, BASE, query)
        assert status == expect, (path, body, query, answer)
        answers.append((path, status, answer))
        return answer

    first, second, _ = seeded["jobs"]
    career = do("/api/schema/map", id=str(seeded["career"]))
    assert {tuple(live) for live in career["live"].values()} == {("unavailable",)}
    do("/api/schema/export", id=str(seeded["career"]))
    do("/api/schema/network")
    do("/api/schema")
    do("/api/schema/jobs", expect=403)
    do("/api/schema/import-jobs", {"id": seeded["career"], "revision": career["revision"], "job_ids": [first]}, expect=403)
    graph = career["graph"]
    kept = do("/api/schema/save", {"id": career["id"], "revision": career["revision"], "viewport": career["viewport"],
                                   "graph": {**graph, "nodes": [*graph["nodes"], node("plain")]}})
    new_job = node("other", "job", {"kind": "job", "id": second})
    do("/api/schema/save", {"id": career["id"], "revision": kept["revision"], "viewport": career["viewport"],
                            "graph": {**kept["graph"], "nodes": [*kept["graph"]["nodes"], new_job]}}, expect=403)
    # A link the map does not hold is new whatever map it is saved to; a string id names the stored one.
    do("/api/schema/save", {"id": 999, "revision": 1, "viewport": career["viewport"], "graph": graph}, expect=403)
    do("/api/schema/save", {"id": career["id"], "revision": kept["revision"], "viewport": career["viewport"],
                            "graph": {"nodes": [node("j", "job", {"kind": "job", "id": str(first)})], "edges": []}}, expect=400)
    plain = do("/api/schema/create", {"title": "Plain map", "mode": "general"})
    for source in ({"kind": "job", "id": first}, {"kind": "company", "id": seeded["company"]}, {"kind": "job", "id": None}):
        do("/api/schema/add-node", {"map_id": plain["id"], "revision": plain["revision"], "label": "Job", "kind": "job", "source": source}, expect=403)
    do("/api/schema/add-node", {"map_id": plain["id"], "revision": plain["revision"], "label": "Job", "kind": "job", "source": {"kind": ["job"], "id": 1}}, expect=500)
    do("/api/schema/add-node", {"map_id": plain["id"], "revision": plain["revision"], "label": "Subject", "kind": "class",
                                "source": {"kind": "subject", "id": seeded["subject"]}})
    document = {"format": "kitty-schema", "version": 1, "title": "x", "mode": "jobs", "viewport": {"x": 0, "y": 0, "zoom": 1},
                "graph": {"nodes": [new_job], "edges": []}}
    do("/api/schema/create", {"title": "x", "document": document}, expect=403)
    do("/api/schema/import", {"title": "x", "document": document}, expect=403)
    do("/api/schema/import", {"title": "x", "document": {**document, "graph": {"nodes": [node("free")], "edges": []}}})
    do("/api/schema/map", id=str(career["id"]))
    return answers


def test_job_links_without_applyai_are_kept_hidden_and_not_made(workspace, tmp_path, monkeypatch):
    conn = workspace.conn
    seeded = seed(conn)
    created = schema_maps.create(conn, {"title": "Career map", "mode": "jobs"})
    schema_maps.import_jobs(conn, {"id": created["id"], "revision": created["revision"], "job_ids": seeded["jobs"][:1]})
    seeded["career"] = created["id"]
    (workspace.settings.root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": ["workspace"]}), encoding="utf-8")
    expected, _, _ = compare(workspace, tmp_path, monkeypatch, lambda call: without_applyai(call, seeded), applyai=False)
    assert "disabled" in next(answer for path, status, answer in expected if status == 403)["error"]
