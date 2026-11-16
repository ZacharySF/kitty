"""The backlink routes answered by rika-server (server/src/backlinks) against `kitty.core.backlinks`:
one scenario over `GET /api/schema/backlinks`, `POST /api/schema/backlinks/apply` and
`POST /api/backlinks/related`, with the refusals between, run through the front and through the
Python core on two copies of the same workspace at the same clock reading. Every answer (the
references, their order, snippets and redaction, the ranked related records and their scores),
the maps left behind (byte for byte) and the content events recorded must be the same; again
with the workspace module alone (no jobs, no ApplyAI calendar copies) and without the workspace
module (every route refused)."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from contextlib import closing

import pytest
from terminal_support import RIKA_SERVER, front_site
from test_schema_parity import BASE, Clock, Front, Masked, Refused, rows

from kitty.core import backlinks, calendar, flashcards, jobs, notes, record_events, schema_maps
from kitty.db import connect
from kitty.modules import MODULE_IDS, ModuleDisabledError
from kitty.web_server import APIError, WebApplication

SCHEMA = "/api/schema/backlinks"
APPLY = "/api/schema/backlinks/apply"
RELATED = "/api/backlinks/related"
#: Text a snippet must redact, or must keep, next to a match.
FRAGMENTS = [
    "https://example.invalid/a?b=c", "HTTPS://Example.invalid", "f\u0130le://x/y", "http\u017f://z", "ftp://h/p", "file:/x", "http://",
    "www.Example.org/path", "wwwx.example", "see:www.x", "mail me@example.invalid,", "a.b+c@sub-d.example.co.uk", "x@y", "@example.com",
    "user@host.", "a@b.c@d.e", "C:\\Users\\someone\\file.txt", "c:/x/y", "x:/a", "/home/someone/private/file.txt", "/a", "/a/b", "/a/ b",
    "~/notes/secret", "~\\x", "~/", "a/b/c/", "abcdefabcdef0123456789abcdef", "ABCDEFGHIJKLMNOPQRSTUVWX1", "abcdefghijklmnopqrstuvwx-\u0663",
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "012345678901234567890123456", "key-ABCDEF0123456789abcdef_ghij-", "-abcdefghijklmnopqrstuvwxy1z",
    "na\u00efve caf\u00e9", "Stra\u00dfe", "\u65e5\u672c\u8a9e\u306e\u30c6\u30ad\u30b9\u30c8", "\U0001f600 emoji", "\u00a0nbsp\u00a0",
    "tab\there", "line\u2028sep", "x\u3000y", "[[Wiki|link]]", "under_score_word", "\u0130stanbul", "\ufb01le", "\u00b2\u00b3",
]
WORDS = ["lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing", "elit", "sed", "do"]


def page(conn, title, *texts, kind="text"):
    notebook = conn.execute("SELECT id FROM note_notebooks ORDER BY id LIMIT 1").fetchone()
    notebook_id = notebook[0] if notebook else notes.create_notebook(conn, "Synthetic notebook")["id"]
    created = notes.create_page(conn, notebook_id, title)
    document = notes.empty_document()
    document["elements"] = [{"id": f"e{index}", "kind": kind, "layer": "default", "x": 0, "y": 0, "w": 10, "h": 10,
                             "color": "#000000", "width": 1, **({"text": text} if kind in {"text", "sticky"} else {})}
                            for index, text in enumerate(texts)]
    notes.save_page(conn, created["id"], created["revision"], document, title)
    return created["id"]


def node(key, label, kind="topic", source=None, **extra):
    return {"id": key, "kind": kind, "label": label, "notes": "", "x": 0, "y": 0, "url": "", "source": source, **extra}


def edge(key, start, end, label=""):
    return {"id": key, "from": start, "to": end, "label": label}


def mapped(conn, title, nodes, edges=(), mode="study"):
    item = schema_maps.create(conn, {"title": title, "mode": mode})
    return schema_maps.save(conn, {"id": item["id"], "revision": item["revision"], "viewport": item["viewport"],
                                   "graph": {"nodes": list(nodes), "edges": list(edges)}})


def seed(conn):
    """Records of every kind the backlinks search, and the maps they link into."""
    rng = random.Random(20261116)
    seeded = {"markers": []}
    # Snippets: each marker sits among fragments a snippet must redact or keep.
    for index in range(len(FRAGMENTS)):
        marker = f"marker{index}"
        around = lambda: " ".join(rng.choice(WORDS + FRAGMENTS) for _ in range(rng.randint(0, 9)))
        page(conn, f"Fragments {index}", f"{around()} {FRAGMENTS[index]} {marker} {FRAGMENTS[(index * 7) % len(FRAGMENTS)]} {around()}")
        seeded["markers"].append(marker)
    page(conn, "Edge case", f"{FRAGMENTS[0]}marker0", "marker0" + "x" * 30, "z" * 200 + " marker0 " + "w " * 100)
    seeded["week"] = page(conn, "Week 2", "Reviewed neural-networks and a matrix; Thermodynamics too")
    page(conn, "Week 3", "Unneural networking matrixes")
    seeded["linked"] = page(conn, "Linked", "See [[Thermodynamics|heat]] for detail", kind="sticky")
    page(conn, "Shape", "thermodynamics", kind="rect")
    for index in range(30):
        page(conn, f"Lecture {index}", f"Topology notes {index}")
    seeded["secret"] = page(conn, "Paths and https://example.invalid/title", "Saved at /home/someone/private/file.txt and https://example.invalid/x?token=1 "
                            "mail someone@example.invalid key abcdefabcdef0123456789abcdef \u2014 topology")
    page(conn, "Long", "x " * 8000 + "topology")
    page(conn, "Turkish", "\u0130stanbul trip and the stra\u00dfe; studies of classes and boxes")
    seeded["intro"] = page(conn, "Graph theory intro", "Graph theory and a calculus refresher")
    # Related records: links both ways, mentions, and many records sharing a term.
    seeded["cell"] = page(conn, "Cell energy", "ATP powers cells")
    page(conn, "Mitochondria", "Respiration produces ATP")
    page(conn, "Respiration", "See [[Cell energy]]")
    page(conn, "Membrane transport", "ATP pumps")
    page(conn, "Exact concepts", "mitochondrial respiration ATP")
    page(conn, "Broad concepts", "mitochondrial respiration ATP chlorophyll photosynthesis sunlight chloroplast")
    for index in range(60):
        page(conn, f"Example {index}", "mitochondrial biology" + " cell" * (index % 3))
    seeded["subject"] = conn.execute("INSERT INTO study_subjects(name) VALUES('Calculus')").lastrowid
    seeded["algebra"] = conn.execute("INSERT INTO study_subjects(name) VALUES('Linear Algebra')").lastrowid
    conn.execute("INSERT INTO study_projects(name) VALUES('Linear algebra problem set')")
    conn.execute("INSERT INTO study_tasks(title,checklist_json) VALUES('Homework',?)",
                 (json.dumps([{"id": "a", "text": "Eigenvalues for linear algebra", "done": False}]),))
    conn.execute("""INSERT INTO calendar_events(id,calendar_id,title,start,end,timezone,data,updated_at)
        VALUES('evt1','personal','Exam','2026-02-01','2026-02-02','UTC',?,'2026-01-01T00:00:00+00:00')""",
                 (json.dumps({"description": "Linear algebra midterm, topology"}),))
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES('tsk1',?,'2026-01-01')",
                 (json.dumps({"title": "Read", "description": "linear algebras chapter"}),))
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES('untitled',?,'2026-01-02')",
                 (json.dumps({"title": "", "description": "topology homework"}),))
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES('odd id/\u00e9',?,'2026-01-03')",
                 (json.dumps({"title": "Topology reading"}),))
    calendar.create_event(conn, {"title": "Quokka offsite", "start": "2030-01-02T09:00:00Z", "end": "2030-01-02T10:00:00Z",
                                 "timezone": "UTC", "all_day": False})
    calendar.create_event(conn, {"title": "Quokka deadline", "start": "2030-01-03T09:00:00Z", "end": "2030-01-03T10:00:00Z",
                                 "timezone": "UTC", "all_day": False, "source_key": "job:1:deadline"})
    flashcards.bootstrap(conn)
    deck = flashcards.save_deck(conn, {"name": "Synthetic biology"})
    basic = conn.execute("SELECT id FROM flash_types WHERE kind='basic' ORDER BY id LIMIT 1").fetchone()[0]
    seeded["card"] = flashcards.save_note(conn, {"deck_id": deck["id"], "type_id": basic,
                                                 "fields": {"Front": "Respiration", "Back": "Mitochondrial ATP synthesis"}})["id"]
    flashcards.save_note(conn, {"deck_id": deck["id"], "type_id": basic, "fields": {"Front": "<b>Linear</b> algebra basis", "Back": "span"}})
    flashcards.save_note(conn, {"deck_id": deck["id"], "type_id": basic, "fields": {"Front": "Topology card", "Back": "open sets"}})
    job, _ = jobs.upsert_job(conn, company="Zephyr Labs", title="Quokka controls engineer", url="https://example.invalid/q",
                             source="manual", source_job_id="bl-1", jd_text="quokka systems and linear algebra")
    compiler, _ = jobs.upsert_job(conn, company="Example Company", title="Compiler Engineer", url="https://example.invalid/c")
    conn.commit()

    seeded["study"] = mapped(conn, "Study", [
        node("nn", "Neural network", x=10.5, y=-3), node("thermo", "Thermodynamics", x=-400), node("topo", "Topology", x=99950, y=99990.25),
        node("calc", "Calculus", "class", {"kind": "subject", "id": seeded["subject"]}),
        node("algebra", "Linear algebra", "class", {"kind": "subject", "id": seeded["algebra"]}), node("short", "AI"), node("stop", "the"),
        node("ref-note_page-5", "Topology"), node("empty", "zzqq nothing"), node("istanbul", "\u0130stanbul"), node("strasse", "STRASSE"),
        node("studies", "Study"), node("classes", "Class"), node("quokka", "Quokka"), node("marker", "marker3"),
        node("task", "Review", "task", {"kind": "calendar_task", "id": "tsk1"})],
        [edge("seen", "topo", "ref-note_page-5", "seen")])
    seeded["career"] = schema_maps.create(conn, {"title": "Career", "mode": "jobs"})
    career = schema_maps.import_jobs(conn, {"id": seeded["career"]["id"], "revision": seeded["career"]["revision"], "job_ids": [compiler.id, job.id]})
    seeded["career"] = schema_maps.save(conn, {"id": career["id"], "revision": career["revision"], "viewport": career["viewport"],
                                               "graph": {"nodes": [*career["graph"]["nodes"], node("compilers", "Compilers"), node("quokkas", "quokkas")],
                                                         "edges": career["graph"]["edges"]}})
    seeded["crowded"] = mapped(conn, "Crowded", [node("topo", "Topology"), *(node(f"n{index}", f"n{index}") for index in range(298))])
    seeded["wired"] = mapped(conn, "Wired", [node("topo", "Topology"), node("b", "zz")], [edge(f"e{index}", "topo", "b") for index in range(600)])
    seeded["bare"] = mapped(conn, "Bare", [node("a", "AI"), node("b", "of the")])
    conn.commit()
    return seeded


class Reference:
    """The routes as `kitty.web_schema` and `kitty.web_backlinks` answered them over the core (the
    handlers are deleted), the module guard before them and the events `WebApplication.mutate`
    recorded after."""

    def __init__(self, db_path, modules=MODULE_IDS):
        self.conn, self.modules, self.events = connect(db_path), modules, []

    def get(self, query, applyai):
        if set(query) == {"map_id"}:
            try:
                map_id = int(query["map_id"])
            except (ValueError, TypeError):
                raise Refused(400, "Map ID must be an integer") from None
            return backlinks.suggestions(self.conn, map_id, applyai=applyai)
        if "term" in query and not set(query) - {"term", "exclude"}:
            return backlinks.mentions(self.conn, query["term"], query.get("exclude"), applyai=applyai)
        raise Refused(400, "Provide map_id, or term with an optional exclude=<kind>:<id>")

    def post(self, path, body, applyai):
        if path == APPLY:
            with schema_maps.applyai_scope(applyai):
                answer = backlinks.apply(self.conn, body, applyai=applyai)
        else:
            if set(body) - {"text", "exclude"} or "text" not in body:
                raise Refused(400, "Expected text and optional exclude")
            answer = backlinks.related(self.conn, body["text"], body.get("exclude"))
        event = record_events.classify(path, body, answer, self.conn)
        if event is not None:
            self.events.append([*event, path])
        return answer

    def __call__(self, path, body, now, query):
        if path == "@sql":
            self.conn.execute(body)
            self.conn.commit()
            return 200, None
        Clock.value = now
        try:
            if body is not None and query:
                raise Refused(400, "This route does not accept query parameters")
            if "workspace" not in self.modules:
                raise Refused(403, str(ModuleDisabledError("workspace")))
            applyai = "applyai" in self.modules
            answer = self.get(query, applyai) if body is None else self.post(path, body, applyai)
        except Refused as refused:
            return refused.status, {"error": refused.message}
        except schema_maps.SchemaConflict as refused:
            return 409, {"error": str(refused)}
        except ValueError as refused:
            return 400, {"error": str(refused)}
        except Exception:  # what `RequestHandler._dispatch` answers for anything else
            return 500, {"error": "Local operation failed; inspect CLI diagnostics"}
        return 200, json.loads(json.dumps(answer))


class Through(Front):
    """The front, and a direct write to its workspace for "@sql"."""

    def __call__(self, path, body, now, query):
        if path == "@sql":
            with closing(connect(self.settings.db_path)) as conn:
                conn.execute(body)
                conn.commit()
            return 200, None
        return super().__call__(path, body, now, query)


def scenario(call, seeded, modules=MODULE_IDS):
    """Every backlink route, with the refusals between: returns each (path, status, answer) in order."""
    answers = []
    allowed = "workspace" in modules

    def do(path, body=None, expect=200, **query):
        status, answer = call(path, body, BASE, query)
        # Without the workspace module every route is the 403 of a disabled module, after the
        # query refusal every mutation gets first.
        if not allowed and path != "@sql" and not (body is not None and query):
            expect = 403
        assert status == expect, (path, body, query, answer)
        answers.append((path, status, answer))
        return answer

    study, career = seeded["study"], seeded["career"]
    # Suggestions for every node of a map, and the refusals of the query.
    do(SCHEMA, map_id=str(study["id"]))
    do(SCHEMA, map_id=f" +{study['id']} ")
    do(SCHEMA, map_id=str(career["id"]))
    do(SCHEMA, map_id=str(seeded["bare"]["id"]))
    for query in ({}, {"map_id": "x"}, {"map_id": ""}, {"map_id": "1.0"}, {"map_id": "0"}, {"map_id": "-1"}, {"map_id": "999"},
                  {"map_id": "99999999999999999999"}, {"map_id": "1", "term": "a"}, {"exclude": "note_page:1"}, {"term": "a", "x": "1"},
                  {"term": "x" * 201}, {"term": "a\x00"}, {"term": "optics", "exclude": "bogus"}, {"term": "optics", "exclude": "note_page:"},
                  {"term": "optics", "exclude": ""}, {"term": "optics", "exclude": "schema_node:x"}):
        do(SCHEMA, expect=400, **query)
    # Mentions of free terms: plurals, case folding, stopwords, exclusions and every kind.
    for term in ("linear algebra", "  Linear Algebras ", "topology", "Topology", "thermodynamics", "neural network", "of", "", "  ", "ai",
                 "quokka", "\u0130stanbul", "strasse", "Stra\u00dfe", "study", "class", "box", "marker0", "zzqq", "lecture 3",
                 "graph theory", "atp", "x" * 200):
        do(SCHEMA, term=term)
    for exclude in (f"study_subject:{seeded['algebra']}", f"note_page:{seeded['week']}", "calendar_event:evt1", "calendar_task:tsk1",
                    "job:1", "flashcard_deck:1", "note_page:abc", "note_page:1:2"):
        do(SCHEMA, term="linear algebra", exclude=exclude)
    for marker in seeded["markers"]:
        do(SCHEMA, term=marker)
    # A record written after the index was last read is found: the queue is folded first.
    do("@sql", "INSERT INTO calendar_tasks(id,data,updated_at) VALUES('late','{\"title\":\"Late topology task\"}','2026-01-04')")
    do(SCHEMA, term="topology")

    # Related records.
    for body in ({"text": "mitochondrial respiration ATP"}, {"text": "[[Mitochondria]] and membrane transport", "exclude": f"note_page:{seeded['cell']}"},
                 {"text": "mitochondrial ATP synthesis", "exclude": f"flashcard_note:{seeded['card']}"}, {"text": "mitochondrial ATP synthesis", "exclude": None},
                 {"text": "Linear algebra and [[Week 2]] with [[Graph theory intro|intro]]"}, {"text": "the and of"}, {"text": ""},
                 {"text": "mitochondrial " * 1000}, {"text": "Stra\u00dfe \u0130stanbul caf\u00e9 na\u00efve studies"}, {"text": "cell energy", "exclude": "schema_node:nn"},
                 {"text": "cell energy", "exclude": "study_subject:1"}, {"text": "cell energy", "exclude": "flashcard_deck:1"},
                 {"text": "ATP", "exclude": "note_page:\u0663"}, {"text": "ATP", "exclude": "note_page:abc"}, {"text": "ATP", "exclude": "flashcard_note:999"},
                 {"text": "Topology lecture notes and the mitochondrial cell"}, {"text": "Membrane transport of ATP in the Cell energy page"}):
        do(RELATED, body)
    for body in ({}, {"text": None}, {"text": 42}, {"text": ["a"]}, {"text": "x" * 20001}, {"text": "a\x00"}, {"text": "a", "unknown": 1},
                 {"exclude": "note_page:1"}, {"text": "a", "exclude": "bogus"}, {"text": "a", "exclude": 5}, {"text": "a", "exclude": "note_page:"},
                 {"text": "a", "exclude": "note_page:" + "1" * 81}, {"text": "a", "exclude": "note_page:a:b"}, {"text": "a", "exclude": "job:1"},
                 {"text": "a", "exclude": "note_page:\u00b2"}, {"text": "a", "exclude": "note_page:1\u00b2"}):
        do(RELATED, body, expect=400)
    do(RELATED, {"text": "a", "exclude": "note_page:" + "9" * 30}, expect=500)
    do(RELATED, {"text": "a"}, expect=400, x="1")

    # Linking: refusals, then the accepted nodes, then the same again (nothing changes).
    revision = study["revision"]
    for body in ({}, {"map_id": study["id"], "revision": revision, "node_ids": ["nn"], "x": 1}, {"map_id": study["id"], "revision": revision},
                 {"map_id": study["id"], "revision": revision, "node_ids": []}, {"map_id": study["id"], "revision": revision, "node_ids": "nn"},
                 {"map_id": study["id"], "revision": revision, "node_ids": ["nn"] * 301}, {"map_id": study["id"], "revision": revision, "node_ids": [5]},
                 {"map_id": study["id"], "revision": revision, "node_ids": ["bad id"]}, {"map_id": study["id"], "revision": revision, "node_ids": [""]},
                 {"map_id": study["id"], "revision": revision, "node_ids": ["missing"]}, {"map_id": "1", "revision": revision, "node_ids": ["nn"]},
                 {"map_id": 999, "revision": 1, "node_ids": ["nn"]}, {"revision": revision, "node_ids": ["nn"]}):
        do(APPLY, body, expect=400)
    do(APPLY, {"map_id": study["id"], "revision": revision + 1, "node_ids": ["nn"]}, expect=409)
    do(APPLY, {"map_id": study["id"], "revision": True, "node_ids": ["nn"]}, expect=409)
    do(APPLY, {"map_id": study["id"], "revision": revision, "node_ids": ["nn"]}, expect=400, q="1")
    linked = do(APPLY, {"map_id": study["id"], "revision": revision, "node_ids": ["nn", "calc", "topo", "nn", "task", "strasse", "short", "algebra"]})
    revision = linked["revision"] if allowed else revision
    do(APPLY, {"map_id": study["id"], "revision": revision, "node_ids": ["nn", "topo"]})
    do(SCHEMA, map_id=str(study["id"]))
    linked = do(APPLY, {"map_id": study["id"], "revision": revision, "node_ids": ["quokka", "thermo", "studies", "classes", "marker"]})
    do(SCHEMA, map_id=str(study["id"]))
    do(APPLY, {"map_id": career["id"], "revision": career["revision"], "node_ids": ["compilers", "quokkas"]})
    do(APPLY, {"map_id": seeded["crowded"]["id"], "revision": seeded["crowded"]["revision"], "node_ids": ["topo"]}, expect=400)
    do(APPLY, {"map_id": seeded["wired"]["id"], "revision": seeded["wired"]["revision"], "node_ids": ["topo"]}, expect=400)
    do(APPLY, {"map_id": seeded["bare"]["id"], "revision": seeded["bare"]["revision"], "node_ids": ["a", "b"]})
    do(SCHEMA, map_id=str(career["id"]))
    return answers


def compare(workspace, tmp_path, monkeypatch, seeded, modules=MODULE_IDS):
    if modules != MODULE_IDS:
        (workspace.settings.root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    copy = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, copy)
    monkeypatch.setattr(schema_maps, "datetime", Clock)

    reference, front = Reference(copy, modules), Through(workspace.settings)
    try:
        expected = scenario(reference, seeded, modules)
        answered = scenario(front, seeded, modules)
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


def test_the_front_answers_backlinks_as_the_python_core_does(workspace, tmp_path, monkeypatch):
    seeded = seed(workspace.conn)
    expected, stored, events = compare(workspace, tmp_path, monkeypatch, seeded)
    answers = {(path, json.dumps(answer, sort_keys=True)) for path, status, answer in expected if status == 200}
    # The scenario reached what it is about: truncation, wikilinks first, linking and every group.
    suggested = next(answer for path, status, answer in expected if path == SCHEMA and status == 200 and "suggestions" in answer)
    assert any(item["truncated"] for item in suggested["suggestions"]) and suggested["scanned"]["job"] == 2
    assert any(ref["match"] == "wikilink" for item in suggested["suggestions"] for ref in item["references"])
    snippets = " ".join(ref["snippet"] for path, status, answer in expected if status == 200 and "references" in (answer or {})
                        for ref in answer["references"])
    assert "[link]" in snippets and "[email]" in snippets and "[path]" in snippets and "[redacted]" in snippets
    related = [answer for path, status, answer in expected if path == RELATED and status == 200]
    assert all(related[0][group] for group in ("related",)) and related[1]["linked"] and related[1]["unlinked"]
    assert len(related[0]["related"]) == 12 and any(answer["related"] and answer["related"][-1]["score"] < 1 for answer in related)
    assert any("mentioned in" in row["graph_json"] and "Linked from" in row["graph_json"] for row in stored)
    assert ["schema", "map", str(seeded["study"]["id"]), "link", APPLY] in events
    assert len(answers) > 90


def test_without_applyai_jobs_and_their_calendar_copies_are_not_searched(workspace, tmp_path, monkeypatch):
    seeded = seed(workspace.conn)
    expected, _, _ = compare(workspace, tmp_path, monkeypatch, seeded, ("workspace",))
    quokka = next(answer for path, status, answer in expected if path == SCHEMA and status == 200 and answer.get("term") == "quokka")
    assert [ref["title"] for ref in quokka["references"]] == ["Quokka offsite"] and "job" not in quokka["scanned"]
    career = expected[-1][2]
    assert not any(ref["kind"] == "job" for item in career["suggestions"] for ref in item["references"])
    linked = next(answer for path, status, answer in reversed(expected) if path == APPLY and status == 200 and answer["id"] == seeded["career"]["id"])
    assert {json.dumps(view) for view in linked["live"].values()} == {'{"unavailable": true}'}


def test_without_the_workspace_module_every_route_is_refused(workspace, tmp_path, monkeypatch):
    seeded = seed(workspace.conn)
    expected, _, events = compare(workspace, tmp_path, monkeypatch, seeded, ("applyai",))
    assert {status for path, status, _ in expected if path != "@sql"} == {403, 400} and events == []
    assert "disabled" in next(answer for _, status, answer in expected if status == 403)["error"]


#: Pieces of generated text: what the tokens, the wikilinks and each redaction pattern turn on.
PIECES = [
    "a", "Z", "x1", "_", "-", ".", "+", "@", "/", "\\", "~", ":", " ", "  ", "\n", "\t", "[", "]", "[[", "]]", "|", "http", "HTTPS", "s",
    "://", "ftp", "file", "fİle", "httpſ", "www", "WWW.", "C:", "c:", "~/", "x" * 24, "ab12" * 7, "Z9" * 13, "-a1" * 9, "\u0663",
    "\u00b2", "\u00a0", "\u2028", "\u3000", "\u00e9", "\u00df", "\u0130", "\u0131", "\u017f", "\ufb01", "\u65e5\u672c", "\U0001f600",
    "\u0301", "\u2160", "studies", "Classes", "boxes", "is", "the", "of", "Neural", "networks", "ies", "ses",
    "me@ex", "@ex.am", ".co", "-x.", "a.b+c@", "@\u00e9.\u00e9", "@-", "..",
]


def test_the_text_rules_match_python():
    """Tokens, phrases, wikilinks, redaction and snippets over generated text, against the core."""
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER}); run cargo build --release in server/")
    rng = random.Random(4711)
    cases = []
    for _ in range(6000):
        text = "".join(rng.choice(PIECES) for _ in range(rng.randint(0, 60)))
        start = rng.randint(0, len(text))
        cases.append({"text": text, "start": start, "end": rng.randint(start, min(len(text), start + 30))})
    cases += [{"text": fragment, "start": 0, "end": len(fragment)} for fragment in FRAGMENTS + PIECES]
    done = subprocess.run([str(RIKA_SERVER), "backlinks-text"], input="\n".join(json.dumps(case) for case in cases) + "\n",
                          capture_output=True, text=True, check=True, timeout=120)
    answered = [json.loads(line) for line in done.stdout.split("\n") if line]
    assert len(answered) == len(cases)
    for case, answer in zip(cases, answered, strict=True):
        text = case["text"]
        phrase = backlinks.phrase(text)
        wanted = {"redact": backlinks.redact(text), "phrase": list(phrase) if phrase else None,
                  "tokens": [list(token) for token in backlinks._tokens(text)],
                  "wikilinks": [found.group(1) for found in backlinks.WIKILINK.finditer(text)],
                  "snippet": backlinks.snippet(text, case["start"], case["end"])}
        assert answer == wanted, text



def test_the_worker_answers_no_backlink_route_and_block_backlinks_stay_with_it(workspace, tmp_path):
    """No Python handler is left for a route the front answers; `/api/blocks/backlinks` (the
    block API's read snapshot) is still the worker's, behind the front."""
    conn = workspace.conn
    created = mapped(conn, "Physics", [node("optics", "Optics")])
    page(conn, "Optics", "Optics lab")
    conn.commit()
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        for query in ({"map_id": str(created["id"])}, {"term": "optics"}):
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(conn, SCHEMA, query)
        for path, body in ((APPLY, {"map_id": created["id"], "revision": created["revision"], "node_ids": ["optics"]}), (RELATED, {"text": "optics"})):
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(conn, path, body)
    finally:
        app.close()
    root = conn.execute("SELECT block_id FROM note_page_index ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    with front_site(workspace.settings) as site:
        status, found = site.request(f"{SCHEMA}?term=optics")
        assert status == 200 and found["references"][0]["title"] == "Optics"
        status, blocks = site.request(f"/api/blocks/backlinks?id={root}")
        assert status == 200 and blocks["target"]["id"] == root
        assert site.request("/api/backlinks/nope", {})[0] == 404 and site.request(f"{SCHEMA}/nope")[0] == 404
