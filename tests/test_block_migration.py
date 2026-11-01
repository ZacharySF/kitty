"""One-way migration of every existing note into blocks: export first, lose nothing."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3

import pytest

from kitty.core import backlinks, blocks, documents, note_blocks, notes, quick_notes
from kitty.db import connect, migrate
from kitty.db.migrations import available_migrations

BLOCKS_MIGRATION = min(m.version for m in available_migrations() if m.name == "blocks")
REF = re.compile(r'\(\(([0-9a-fA-F-]{36})\)\)|\{\{embed:([0-9a-fA-F-]{36})\}\}')
WIKI = re.compile(r'\[\[([^\[\]\n|]{1,200})(?:\|[^\[\]\n]{0,200})?\]\]')
TAG = re.compile(r'(?<![\w#])#([\w][\w/-]{0,99})', re.UNICODE)
LEGACY_TABLES = ("note_folders", "note_notebooks", "note_pages", "note_blocks", "note_block_refs", "note_block_topics",
                 "note_daily", "note_attachments", "documents", "document_originals", "quick_notes", "note_preferences")

B = {name: f"00000000-0000-4000-8000-{index:012d}" for index, name in enumerate(
    ("root", "child", "grandchild", "second", "hidden", "chemi", "cites", "unlinked", "daily"), start=1)}


def element(kind, eid, **extra):
    value = {"id": eid, "kind": kind, "layer": "default", "x": 10.0, "y": 20.0, "w": 120.0, "h": 60.0,
             "color": "#344054", "fill": "none", "width": 3.0}
    if kind in {"pen", "highlighter"}:
        value["points"] = [[0.0, 0.0, .2], [40.0, 25.0, .7], [80.0, 10.0, .9]]
    if kind in {"line", "arrow", "connector"}:
        value["points"] = [[0.0, 0.0, .5], [90.0, 40.0, .5]]
    if kind in {"text", "sticky"}:
        value.update(text="", fontSize=18.0)
    value.update(extra)
    return value


ENERGY = {"version": 1, "grid": True, "snap": False, "background": "grid",
          "layers": [{"id": "default", "name": "Drawing", "visible": True, "locked": False},
                     {"id": "refs", "name": "References", "visible": False, "locked": True}],
          "elements": [element("pen", "s1", group="g1"), element("pen", "s2", group="g1"), element("highlighter", "s3", color="#fde047"),
                       element("sticky", "st1", text="ATP is the energy currency #exam", tags=["important"], fill="#fef3c7", x=300.0),
                       element("text", "t1", text="Krebs cycle → NADH", checked=True, tags=["todo", "question"], x=-200.5, fontSize=24.0),
                       element("rect", "r1", fill="#dbeafe", x=500.0, y=500.0), element("ellipse", "e1", x=650.0, y=520.0),
                       element("arrow", "a1"), element("image", "img1", attachment="a" * 32, layer="refs", w=320.0, h=160.0),
                       element("pen", "s4", color="#ef4444")]}
MIND = {"version": 1, "grid": False, "snap": True, "layers": [{"id": "default", "name": "Drawing", "visible": True, "locked": False}],
        "elements": [element("sticky", "c1", text="Photosynthesis", fill="#292438", h=96.0),
                     element("sticky", "c2", text="Glucose", fill="#292438", h=96.0, x=300.0),
                     element("sticky", "c3", text="Respiration", fill="#292438", h=96.0, x=600.0),
                     element("connector", "k1", **{"from": "c1", "to": "c2"}), element("connector", "k2", **{"from": "c2", "to": "c3"}),
                     element("pen", "doodle")]}
EMPTY = {"version": 1, "grid": True, "snap": False, "layers": [{"id": "default", "name": "Drawing", "visible": True, "locked": False}], "elements": []}

PAGES = [  # id, notebook, title, document, viewport, revision, viewport_revision, created, updated, favorite, last_opened, position
    (1, 1, "Cell energy", ENERGY, {"x": -120.5, "y": 44, "zoom": 1.5}, 3, 2, "2026-09-01T10:00:00+00:00", "2026-10-02T10:00:00+00:00", 1, "2026-10-29T08:00:00+00:00", 1),
    (2, 1, "ATP synthesis", EMPTY, {"x": 0, "y": 0, "zoom": 1}, 2, 1, "2026-09-02T10:00:00+00:00", "2026-10-03T10:00:00+00:00", 0, None, 0),
    (3, 2, "Concept map", MIND, {"x": 5, "y": 6, "zoom": .5}, 2, 1, "2026-09-03T10:00:00+00:00", "2026-10-01T10:00:00+00:00", 0, None, 0),
    (7, 3, "2026-10-30", EMPTY, {"x": 0, "y": 0, "zoom": 1}, 2, 1, "2026-10-30T07:00:00+00:00", "2026-10-30T07:05:00+00:00", 0, None, 0),
]
OUTLINE = [  # id, page, parent, text, ordering, collapsed
    (B["root"], 1, None, "Mitochondria make [[ATP synthesis]] #biology", 0, 0),
    (B["child"], 1, B["root"], "Inner membrane folds (cristae)", 1, 0),
    (B["grandchild"], 1, B["child"], "Surface area → more ATP #exam", 2, 0),
    (B["second"], 1, None, f"See (({B['root']})) for the overview", 3, 1),
    (B["hidden"], 1, B["second"], "Collapsed detail", 4, 0),
    (B["chemi"], 2, None, "Chemiosmosis drives ATP synthase #biology #Bio/Energy", 0, 0),
    (B["cites"], 2, None, f"Cites {{{{embed:{B['grandchild']}}}}} and (({B['root']}))", 1, 0),
    (B["unlinked"], 2, None, "Unlinked mention: Cell energy matters", 2, 0),
    (B["daily"], 7, None, "Review [[Cell energy|energy]] #exam", 0, 0),
]
DOCUMENTS = [  # id, title, content, format, notebook, revision, created, updated
    (1, "Lab report", "# Lab report\n\nWe measured **ATP** output.\n\n\n```python\nx = 1\n\nprint(x)\n```\n\n- step one\n- step two\n\n> quoted [[ATP synthesis]]\n",
     "markdown", 1, 4, "2026-09-05T10:00:00+00:00", "2026-10-05T10:00:00+00:00"),
    (2, "Plain", "\n\nline one\r\nline two\n\n\nafter gap", "text", None, 1, "2026-09-06T10:00:00+00:00", "2026-10-06T10:00:00+00:00"),
    (3, "Empty", "", "markdown", 2, 1, "2026-09-07T10:00:00+00:00", "2026-09-07T10:00:00+00:00"),
]
QUICK = [  # id, body, tint, pinned, revision, created, updated
    (1, "Buy lab gloves", "mint", 0, 1, "2026-10-01T10:00:00+00:00", "2026-10-01T10:00:00+00:00"),
    (2, "Call advisor about #exam\nsecond line", "sky", 1, 2, "2026-10-02T10:00:00+00:00", "2026-10-02T11:00:00+00:00"),
    (5, "", "rose", 0, 1, "2026-10-03T10:00:00+00:00", "2026-10-03T10:00:00+00:00"),
]


def legacy_database(tmp_path):
    """A workspace database at the last pre-block schema, filled the way the old code wrote it."""
    early = tmp_path / "early"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < BLOCKS_MIGRATION:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "data" / "legacy.sqlite")
    migrate(conn, early)
    conn.execute("INSERT INTO note_folders(id,title,parent_id,created_at,updated_at) VALUES (1,'Studies',NULL,'t','t'),(2,'Biology',1,'t','t')")
    conn.execute("INSERT INTO note_notebooks(id,title,subject,project,folder_id,created_at,updated_at) VALUES "
                 "(1,'Cells','Biology','Exam prep',2,'t','t'),(2,'Ideas','','',NULL,'t','t'),(3,'Daily notes','','',NULL,'t','t')")
    conn.execute("INSERT INTO note_attachments VALUES (?,?,?,?,?,?,?)", ("a" * 32, "Diagram", "/nowhere/a.png", 10, 64, 32, "t"))
    for pid, nid, title, doc, view, rev, vrev, created, updated, favorite, opened, position in PAGES:
        conn.execute("INSERT INTO note_pages(id,notebook_id,title,document,search_text,viewport,revision,viewport_revision,created_at,updated_at,favorite,last_opened,position) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (pid, nid, title, json.dumps(doc), "stale search text", json.dumps(view), rev, vrev, created, updated, favorite, opened, position))
    for bid, pid, parent, text, ordering, collapsed in OUTLINE:
        conn.execute("INSERT INTO note_blocks(id,page_id,parent_id,text,ordering,collapsed) VALUES (?,?,?,?,?,?)", (bid, pid, parent, text, ordering, collapsed))
        conn.executemany("INSERT INTO note_block_refs VALUES (?,?)", [(bid, t) for t in {(m[1] or m[2]).lower() for m in REF.finditer(text)}])
        topics = {("page", m[1].strip().casefold()) for m in WIKI.finditer(text)} | {("tag", m[1].casefold()) for m in TAG.finditer(text)}
        conn.executemany("INSERT INTO note_block_topics VALUES (?,?,?)", [(bid, kind, topic) for kind, topic in topics])
    conn.execute("INSERT INTO note_daily VALUES ('2026-10-30',7)")
    for row in DOCUMENTS:
        conn.execute("INSERT INTO documents(id,title,content,format,notebook_id,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", row)
    conn.execute("INSERT INTO document_originals(id,name,extension,byte_size,page_id,document_id,created_at) VALUES "
                 "(?,?,?,?,?,?,?),(?,?,?,?,?,?,?)", ("b" * 32, "source.md", "md", 5, 2, None, "t1", "c" * 32, "lab.pdf", "pdf", 9, None, 1, "t2"))
    for row in QUICK:
        conn.execute("INSERT INTO quick_notes(id,body,tint,pinned,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", row)
    return conn


def legacy_rows(conn):
    return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in LEGACY_TABLES}


@pytest.fixture
def migrated(tmp_path):
    conn = legacy_database(tmp_path)
    before = legacy_rows(conn)
    migrate(conn)
    yield conn, before, tmp_path
    conn.close()


def test_full_json_export_is_written_before_migrating(migrated):
    conn, before, tmp_path = migrated
    exports = list((tmp_path / "data" / "exports").glob("notes-before-blocks-*.json"))
    assert len(exports) == 1
    assert exports[0].stat().st_mode & 0o077 == 0
    exported = json.loads(exports[0].read_text(encoding="utf-8"))
    assert exported["format"] == "kitty-notes-export" and exported["tables"] == before
    migrate(conn)
    assert len(list((tmp_path / "data" / "exports").iterdir())) == 1


def test_canvas_pages_round_trip_geometry_layers_ink_and_view_state(migrated):
    conn = migrated[0]
    for pid, nid, title, doc, view, rev, vrev, created, updated, favorite, opened, position in PAGES:
        page = notes.page(conn, pid)
        assert page["document"] == doc
        assert (page["title"], page["notebook_id"], page["viewport"], page["revision"], page["viewport_revision"]) == (title, nid, view, rev, vrev)
        assert (page["created_at"], page["updated_at"], page["favorite"], page["last_opened"], page["position"]) == (created, updated, favorite, opened, position)
    root = conn.execute("SELECT id FROM blocks WHERE type='page' AND json_extract(properties,'$.number')=1").fetchone()["id"]
    kinds = [row["type"] for row in conn.execute(
        "SELECT b.type FROM blocks b JOIN blocks c ON c.id=b.parent_id WHERE c.type='canvas' AND c.page_id=? ORDER BY b.order_key", (root,))]
    assert kinds == ["ink", "sticky", "text_box", "shape", "shape", "shape", "image", "ink"]
    sticky = conn.execute("SELECT properties,text FROM blocks WHERE page_id=? AND type='sticky'", (root,)).fetchone()
    geometry = json.loads(sticky["properties"])
    assert {key: geometry[key] for key in ("x", "y", "w", "h")} == {"x": 300.0, "y": 20.0, "w": 120.0, "h": 60.0}
    assert sticky["text"] == "ATP is the energy currency #exam"
    ink = json.loads(conn.execute("SELECT properties FROM blocks WHERE page_id=? AND type='ink' ORDER BY order_key", (root,)).fetchone()[0])
    assert [stroke["id"] for stroke in ink["strokes"]] == ["s1", "s2", "s3"]


def test_outlines_links_tags_backlinks_and_ordering_survive(migrated):
    conn = migrated[0]
    for pid in {row[1] for row in OUTLINE}:
        expected = [{"id": bid, "page_id": page, "parent_id": parent, "text": text, "ordering": ordering, "collapsed": bool(collapsed),
                     "type": "text", "props": {}}
                    for bid, page, parent, text, ordering, collapsed in OUTLINE if page == pid]
        assert note_blocks.get_blocks(conn, pid)["blocks"] == expected
    cites = note_blocks.get_blocks(conn, 2)
    assert cites["references"] == {B["grandchild"]: "Surface area → more ATP #exam", B["root"]: OUTLINE[0][3]}
    assert note_blocks.get_blocks(conn, 1)["citation_counts"] == {B["root"]: 2, B["grandchild"]: 1}
    assert {row["id"] for row in note_blocks.topics(conn, "tag", "exam")["blocks"]} == {B["grandchild"], B["daily"]}
    assert {row["id"] for row in note_blocks.topics(conn, "tag", "bio/energy")["blocks"]} == {B["chemi"]}
    assert [row["id"] for row in note_blocks.topics(conn, "page", "cell energy")["blocks"]] == [B["daily"]]
    assert note_blocks.topics(conn, "page", "ATP synthesis")["count"] == 1
    contexts = note_blocks.contexts(conn, B["root"])
    assert contexts["count"] == 2 and {row["page_id"] for row in contexts["contexts"]} == {1, 2} and contexts["page_id"] == 1
    assert [(row["block_id"], row["page_id"]) for row in note_blocks.mentions(conn, 2)["mentions"]] == [(B["unlinked"], 1)]
    tagged = {item.get("element_id") or item.get("block_id") for item in notes.tags_summary(conn)["tags"]}
    assert tagged == {"st1", "t1", B["root"], B["grandchild"], B["chemi"], B["daily"]}
    related = backlinks.related(conn, "Chemiosmosis synthase energy")
    assert any(item["kind"] == "note_page" and item["id"] == 2 for bucket in related.values() for item in bucket)


def test_library_order_search_daily_pages_and_originals_survive(migrated):
    conn = migrated[0]
    listing = notes.listing(conn)
    assert [page["id"] for page in listing["pages"]] == [7, 2, 3, 1]
    assert [page["id"] for page in notes.listing(conn, "cristae")["pages"]] == [1]
    assert [page["id"] for page in notes.listing(conn, "Respiration")["pages"]] == [3]
    assert [page["id"] for page in notes.listing(conn, "Exam prep")["pages"]] == [2, 1]
    assert notes.daily_page(conn, "2026-10-30")["id"] == 7
    assert len(notes.listing(conn)["pages"]) == 4
    assert [row["id"] for row in documents.originals(conn, page_id=2)] == ["b" * 32]
    assert [row["id"] for row in documents.originals(conn, document_id=1)] == ["c" * 32]


def test_documents_and_quick_notes_round_trip_exactly(migrated):
    conn = migrated[0]
    for did, title, content, fmt, nid, rev, created, updated in DOCUMENTS:
        assert documents.document(conn, did) == {"id": did, "title": title, "content": content, "format": fmt, "notebook_id": nid,
                                                 "revision": rev, "created_at": created, "updated_at": updated}
    assert [doc["id"] for doc in documents.listing(conn)["documents"]] == [2, 1, 3]
    chunks = [row["type"] for row in conn.execute(
        "SELECT c.type FROM blocks c JOIN blocks d ON d.id=c.parent_id WHERE d.type='document' AND json_extract(d.properties,'$.number')=1 ORDER BY c.order_key")]
    assert chunks == ["heading", "text", "code", "list", "quote"]
    by_id = {row[0]: row for row in QUICK}
    assert quick_notes.list_notes(conn) == [
        {"id": nid, "body": body, "tint": tint, "pinned": bool(pinned), "revision": rev, "created_at": created, "updated_at": updated}
        for nid, body, tint, pinned, rev, created, updated in (by_id[2], by_id[5], by_id[1])]


def test_old_tables_stay_readable_but_refuse_writes(migrated):
    conn, before = migrated[0], migrated[1]
    assert legacy_rows(conn) == before
    for statement in ("INSERT INTO note_pages(notebook_id,title,document,created_at,updated_at) VALUES (1,'x','{}','t','t')",
                      "UPDATE note_blocks SET text='changed'", "DELETE FROM documents", "UPDATE quick_notes SET body='x'",
                      "DELETE FROM note_daily", "INSERT INTO note_block_topics VALUES ('x','tag','y')"):
        with pytest.raises(sqlite3.DatabaseError, match="read-only"):
            conn.execute(statement)


def test_migrated_pages_keep_their_revision_guards(migrated):
    conn = migrated[0]
    saved = notes.save_page(conn, 1, 3, ENERGY | {"snap": True}, "Cell energy")
    assert saved == {"revision": 4}
    with pytest.raises(notes.NotesError) as stale:
        notes.save_page(conn, 1, 3, ENERGY, "Stale")
    assert stale.value.status == 409
    assert quick_notes.save(conn, 2, 2, body="edited")["revision"] == 3
    with pytest.raises(notes.NotesError) as stale_doc:
        documents.save(conn, {"title": "Lab report", "content": "x", "format": "markdown", "notebook_id": 1, "revision": 3}, 1)
    assert stale_doc.value.status == 409


def test_one_log_transaction_per_migrated_root(migrated):
    conn = migrated[0]
    roots = conn.execute("SELECT COUNT(*) FROM blocks WHERE parent_id IS NULL AND type != 'tag'").fetchone()[0]
    assert roots == len(PAGES) + len(DOCUMENTS) + len(QUICK)
    # Migration 037 then made each #tag a tag record, one transaction for all of them.
    tag_names = {row[0] for row in conn.execute("SELECT text FROM blocks WHERE parent_id IS NULL AND type = 'tag'")}
    assert tag_names == {"biology", "exam", "Bio/Energy"}
    tag_roots = len(tag_names)
    assert conn.execute("SELECT COUNT(DISTINCT page_id) FROM op_log").fetchone()[0] == roots + tag_roots
    for page_id in [row[0] for row in conn.execute("SELECT id FROM blocks WHERE parent_id IS NULL")]:
        assert blocks.replay(conn, page_id).rows() == blocks.load_page(conn, page_id).rows()


def test_a_failed_conversion_rolls_back_and_can_be_retried(tmp_path, monkeypatch):
    from kitty.core.blocks import legacy

    conn = legacy_database(tmp_path)
    before = legacy_rows(conn)
    original = legacy.convert_quick_notes
    monkeypatch.setattr(legacy, "convert_quick_notes", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version>=?", (BLOCKS_MIGRATION + 1,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0
    conn.execute("UPDATE quick_notes SET body=body")
    assert legacy_rows(conn) == before
    monkeypatch.setattr(legacy, "convert_quick_notes", original)
    migrate(conn)
    assert len(quick_notes.list_notes(conn)) == len(QUICK)


def test_a_fresh_workspace_writes_no_export(tmp_path):
    conn = connect(tmp_path / "data" / "fresh.sqlite")
    migrate(conn)
    assert not (tmp_path / "data" / "exports").exists()
    assert conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0
