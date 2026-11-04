"""The full-text search index: exact after every write, checked against a rebuild, built by its migration."""
from __future__ import annotations

import json
import random
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from kitty.core import backlinks, blocks, jobs, note_blocks, notes, search
from kitty.core.blocks import history
from kitty.db import connect, migrate
from kitty.db.migrations import available_migrations

STAMP = "2026-01-01T00:00:00+00:00"


def new_id():
    return str(uuid.uuid4())


def notebook(conn):
    row = conn.execute("SELECT id FROM note_notebooks ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else notes.create_notebook(conn, "Synthetic")["id"]


def page_ops(conn, title, *texts, number=None, book=None):
    """A note page with one text block per entry of `texts`; returns (root id, child ids, ops)."""
    root = new_id()
    number = number or notes.next_number(conn, "page")
    children = [new_id() for _ in texts]
    ops = [{"op": "insert", "id": root, "type": "page", "parent": None, "text": title,
            "props": {"number": number, "notebook_id": book or notebook(conn), "revision": 1}}]
    ops += [{"op": "insert", "id": child, "type": "text", "parent": root, "text": text} for child, text in zip(children, texts)]
    return root, children, ops


def make_page(conn, title, *texts):
    root, children, ops = page_ops(conn, title, *texts)
    blocks.transact(conn, ops)
    return root, children


def number_of(conn, root):
    return conn.execute("SELECT json_extract(properties,'$.number') FROM blocks WHERE id=?", (root,)).fetchone()[0]


def stored(conn):
    return {(row["kind"], row["ref"]): tuple(row) for row in conn.execute(
        f"SELECT {','.join(search.COLUMNS)} FROM search_docs")}


def rebuilt(conn, blocks_from="projection"):
    return {(doc.kind, doc.ref): tuple(doc) for doc in search.documents(conn, blocks=blocks_from)}


def assert_exact(conn):
    """The incremental index equals a rebuild from the projection and from the log, and FTS5 agrees."""
    search.sync(conn)
    assert stored(conn) == rebuilt(conn)
    report = search.check(conn, repair=False)
    assert report["ok"], report
    assert report["docs"] == len(stored(conn))


def hits(result):
    return [(hit["kind"], hit["id"]) for hit in result["hits"]]


def test_block_transactions_keep_the_index_exact_inside_the_same_transaction(workspace):
    conn = workspace.conn
    root, (a, b) = make_page(conn, "Cell biology", "Mitochondria make ATP", "Ribosomes make proteins")
    number = number_of(conn, root)
    assert hits(search.search(conn, "mitochondria")) == [("note_page", number)]
    blocks.transact(conn, [{"op": "text", "id": a, "at": 0, "del": 12, "ins": "Chloroplasts"}])
    assert search.search(conn, "mitochondria")["hits"] == []
    assert hits(search.search(conn, "chloro")) == [("note_page", number)]
    blocks.transact(conn, [{"op": "set", "id": b, "type": "image"}])  # not a text block any more
    assert search.search(conn, "ribosomes")["hits"] == []
    blocks.transact(conn, [{"op": "delete", "id": a}])
    assert search.search(conn, "chloroplasts")["hits"] == []
    blocks.transact(conn, [{"op": "restore", "id": a}])
    assert hits(search.search(conn, "chloroplasts")) == [("note_page", number)]
    other, _ = make_page(conn, "Plants")
    blocks.transact(conn, [{"op": "move", "id": a, "parent": other}])
    assert hits(search.search(conn, "chloroplasts")) == [("note_page", number_of(conn, other))]
    # A refused transaction writes nothing, the index included.
    with pytest.raises(blocks.BlockError):
        blocks.transact(conn, [{"op": "text", "id": a, "text": "never stored"}], base={other: 99})
    assert search.search(conn, "stored")["hits"] == []
    blocks.transact(conn, [{"op": "purge", "id": a}])
    assert search.search(conn, "chloroplasts")["hits"] == []
    blocks.transact(conn, [{"op": "delete", "id": root}])
    assert search.search(conn, "cell")["hits"] == []
    assert_exact(conn)


def test_prefixes_diacritics_substrings_cjk_ranking_and_highlights(workspace):
    conn = workspace.conn
    titled, _ = make_page(conn, "Thermodynamics", "Entropy rises")
    body, _ = make_page(conn, "Week 3", "We reviewed thermodynamics and the Café de Flore", "東京都庁へ行く")
    result = search.search(conn, "thermo")
    assert hits(result) == [("note_page", number_of(conn, titled)), ("note_page", number_of(conn, body))]
    top = result["hits"][0]
    assert top["title_marks"] == [["Thermodynamics", True]] and top["view"] == "notes" and top["match"] == "word"
    assert ["thermodynamics", True] in result["hits"][1]["snippet"]
    assert ["Café", True] in search.search(conn, "cafe")["hits"][0]["snippet"]
    inside = search.search(conn, "modynam")  # inside a word: the trigram index
    assert {hit["match"] for hit in inside["hits"]} == {"substring"} and len(inside["hits"]) == 2
    assert ["都庁へ", True] in search.search(conn, "都庁へ")["hits"][0]["snippet"]
    assert search.search(conn, "the café")["hits"][0]["id"] == number_of(conn, body)
    for _ in range(4):
        make_page(conn, "Thermal notes")
    limited = search.search(conn, "therm", limit=3)
    assert len(limited["hits"]) == 3 and limited["truncated"] and limited["total"] == 6
    assert search.search(conn, "!!!")["hits"] == [] and search.search(conn, "")["hits"] == []
    for bad in ("x" * 301, "a\x00b"):
        with pytest.raises(ValueError):
            search.search(conn, bad)
    with pytest.raises(ValueError):
        search.search(conn, "x", limit=0)


def test_other_sources_follow_their_tables_and_modules(workspace):
    conn = workspace.conn
    subject = conn.execute("INSERT INTO study_subjects(name) VALUES('Quantum optics')").lastrowid
    task = conn.execute("INSERT INTO study_tasks(title,checklist_json) VALUES('Homework',?)",
                        (json.dumps([{"id": "a", "text": "Quantum tunnelling", "done": False}]),)).lastrowid
    conn.execute("""INSERT INTO calendar_events(id,calendar_id,title,start,end,timezone,data,updated_at,source_key)
        VALUES('evt1','personal','Interview','2026-02-01','2026-02-02','UTC',?,?,'application:1')""",
                 (json.dumps({"description": "quantum computing team"}), STAMP))
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES('tsk1',?,?)",
                 (json.dumps({"title": "Read quantum chapter"}), STAMP))
    deck = conn.execute("INSERT INTO flash_decks(name,settings,created_at) VALUES('Physics','{}',?)", (STAMP,)).lastrowid
    kind = conn.execute("INSERT INTO flash_types(name,kind,fields,templates) VALUES('Synthetic basic','basic',?,'[]')",
                        (json.dumps(["Front", "Back"]),)).lastrowid
    card = conn.execute("INSERT INTO flash_notes(guid,deck_id,type_id,fields,created_at,updated_at) VALUES('g',?,?,?,?,?)",
                        (deck, kind, json.dumps({"Front": "<b>Quantum</b> spin", "Back": "&frac12; <i>h-bar</i>"}), STAMP, STAMP)).lastrowid
    job = jobs.upsert_job(conn, company="Example Labs", title="Quantum Engineer", url="https://example.invalid/q",
                          location="Remote")[0].id
    found = set(hits(search.search(conn, "quantum")))
    assert found == {("study_subject", subject), ("study_task", task), ("calendar_event", "evt1"),
                     ("calendar_task", "tsk1"), ("flashcard_note", card), ("job", job)}
    card_hit = next(hit for hit in search.search(conn, "spin")["hits"] if hit["kind"] == "flashcard_note")
    assert card_hit["title"] == "Quantum spin"  # HTML is cleaned before it is indexed
    assert search.search(conn, "frac")["hits"] == [] and hits(search.search(conn, "½"))
    # Without ApplyAI, jobs and ApplyAI-sourced calendar copies are not searched; without the workspace, only jobs.
    assert {kind for kind, _ in hits(search.search(conn, "quantum", modules=("workspace",)))} == {
        "study_subject", "study_task", "calendar_task", "flashcard_note"}
    assert hits(search.search(conn, "quantum", modules=("applyai",))) == [("job", job)]
    assert hits(search.search(conn, "quantum", kinds=["study_subject"])) == [("study_subject", subject)]
    conn.execute("UPDATE companies SET display_name='Renamed Optics' WHERE id=(SELECT company_id FROM jobs WHERE id=?)", (job,))
    assert hits(search.search(conn, "renamed")) == [("job", job)]
    assert search.search(conn, "renamed")["hits"][0]["title"] == "Quantum Engineer · Renamed Optics"
    conn.execute("UPDATE flash_decks SET archived=1 WHERE id=?", (deck,))
    conn.execute("UPDATE calendar_events SET deleted=1")
    conn.execute("DELETE FROM study_subjects")
    assert {kind for kind, _ in hits(search.search(conn, "quantum"))} == {"study_task", "calendar_task", "job"}
    assert_exact(conn)


def test_tags_quick_notes_and_documents_are_indexed(workspace):
    from kitty.core import documents, quick_notes
    from kitty.core.blocks import tags

    conn = workspace.conn
    tag = tags.create(conn, {"name": "Photosynthesis", "keywords": ["chlorophyll"]})
    note = quick_notes.create(conn, "Buy chlorophyll supplements\nsecond line")
    document = documents.save(conn, {"title": "Plant paper", "content": "# Intro\n\nChlorophyll absorbs light.",
                                     "format": "markdown", "notebook_id": None})
    kinds = {hit["kind"] for hit in search.search(conn, "chlorophyll")["hits"]}
    assert kinds == {"tag", "quick_note", "document"}, (tag, note, document)
    assert_exact(conn)


def test_the_oracle_finds_and_repairs_every_kind_of_drift(workspace):
    conn = workspace.conn
    root, (child,) = make_page(conn, "Oracle", "first text")
    conn.execute("INSERT INTO study_subjects(name) VALUES('Geology')")
    assert search.check(conn)["ok"]
    # A lost document, a wrong one, a ghost and an FTS row with no document.
    conn.execute("DELETE FROM search_docs WHERE ref=?", (child,))
    conn.execute("UPDATE search_docs SET title='Wrong' WHERE ref=?", (root,))
    conn.execute("INSERT INTO search_docs(kind,ref,title) VALUES('study_subject','999','Ghost')")
    conn.execute("INSERT INTO search_fts(rowid,title,body) VALUES(424242,'phantom','phantom')")
    report = search.check(conn)
    assert not report["ok"] and report["repaired"] == 3 and report["drift"] == []
    assert {(item["ref"], item["problem"]) for item in report["mismatches"]} == {(child, "missing"), (root, "changed"), ("999", "extra")}
    assert report["fts"] == {"search_fts": False, "search_grams": True}
    assert search.search(conn, "phantom")["hits"] == []
    assert search.check(conn)["ok"]
    assert search.last_check(conn)["mismatches"] == 0
    conn.execute("DROP TRIGGER search_study_subjects_insert")
    assert search.check(conn)["missing_triggers"] == ["search_study_subjects_insert"]


def test_queued_changes_commit_with_the_write_that_made_them(workspace):
    conn = workspace.conn
    conn.execute("BEGIN")
    conn.execute("INSERT INTO study_subjects(name) VALUES('Rolled back')")
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT COUNT(*) FROM search_pending").fetchone()[0] == 0
    conn.execute("INSERT INTO study_subjects(name) VALUES('Kept')")
    assert [tuple(row) for row in conn.execute("SELECT kind FROM search_pending")] == [("study_subject",)]
    assert search.sync(conn) == 1 and search.sync(conn) == 0
    assert hits(search.search(conn, "kept"))


def _random_text(rng, words):
    return " ".join(rng.choice(words) for _ in range(rng.randint(0, 6)))


def _step(conn, rng, words, state, at):
    pages, items = state["pages"], state["items"]
    choice = rng.random()
    live = [bid for bid in items if conn.execute("SELECT 1 FROM blocks WHERE id=? AND deleted_at IS NULL", (bid,)).fetchone()]
    deleted = [bid for bid in items if conn.execute("SELECT 1 FROM blocks WHERE id=? AND deleted_at IS NOT NULL", (bid,)).fetchone()]
    if choice < 0.08 or not pages:
        root, children, ops = page_ops(conn, _random_text(rng, words) or "Untitled", *[_random_text(rng, words) for _ in range(rng.randint(0, 3))])
        blocks.transact(conn, ops, at=at)
        pages.append(root)
        items.extend(children)
    elif choice < 0.3:
        bid = new_id()
        parent = rng.choice(pages + live) if live else rng.choice(pages)
        kind = rng.choice(["text", "heading_1", "to_do", "code", "quote", "image", "divider"])
        blocks.transact(conn, [{"op": "insert", "id": bid, "type": kind, "parent": parent, "text": _random_text(rng, words)}], at=at)
        items.append(bid)
    elif choice < 0.55 and live:
        bid = rng.choice(live)
        text = conn.execute("SELECT text FROM blocks WHERE id=?", (bid,)).fetchone()[0]
        position = rng.randint(0, len(text))
        blocks.transact(conn, [{"op": "text", "id": bid, "at": position, "del": rng.randint(0, len(text) - position),
                                "ins": rng.choice(["", " ", *words])}], at=at)
    elif choice < 0.62 and live:
        blocks.transact(conn, [{"op": "set", "id": rng.choice(live), "type": rng.choice(["text", "callout", "image", "toggle"])}], at=at)
    elif choice < 0.7 and live:
        blocks.transact(conn, [{"op": "delete", "id": rng.choice(live)}], at=at)
    elif choice < 0.75 and deleted:
        try:
            blocks.transact(conn, [{"op": "restore", "id": rng.choice(deleted)}], at=at)
        except blocks.BlockError:
            pass  # Restoring under a deleted parent is refused.
    elif choice < 0.8 and live:
        try:
            blocks.transact(conn, [{"op": "move", "id": rng.choice(live), "parent": rng.choice(pages)}], at=at)
        except blocks.BlockError:
            pass  # A move into a deleted page or a cycle is refused.
    elif choice < 0.83 and live:
        blocks.transact(conn, [{"op": "purge", "id": rng.choice(live)}], at=at)
    elif choice < 0.86:
        live_pages = [root for root in pages if conn.execute("SELECT 1 FROM blocks WHERE id=? AND deleted_at IS NULL", (root,)).fetchone()]
        if live_pages:
            blocks.transact(conn, [{"op": "text", "id": rng.choice(live_pages), "text": _random_text(rng, words)}], at=at)
    elif choice < 0.9:
        conn.execute("INSERT INTO study_subjects(name) VALUES(?)", (_random_text(rng, words),))
    elif choice < 0.93:
        row = conn.execute("SELECT id FROM study_subjects ORDER BY random() LIMIT 1").fetchone()
        if row:
            conn.execute("UPDATE study_subjects SET name=? WHERE id=?", (_random_text(rng, words), row[0]))
    elif choice < 0.96:
        jobs.upsert_job(conn, company=rng.choice(["Example Labs", "Synthetic Co"]), title=_random_text(rng, words) or "Job",
                        url=f"https://example.invalid/{new_id()}", jd_text=_random_text(rng, words))
    else:
        conn.execute("DELETE FROM study_subjects WHERE id IN (SELECT id FROM study_subjects ORDER BY random() LIMIT 1)")


@pytest.mark.parametrize("seed", range(12))
def test_random_transactions_then_rebuild_equals_incremental(workspace, seed):
    """Property: after any sequence of block and source-table writes, a rebuild equals the incremental index.

    Half the runs write their first transactions 40 days in the past and compact, so the
    oracle's rebuild replays daily snapshots plus the log tail.
    """
    conn = workspace.conn
    rng = random.Random(seed)
    words = ["alpha", "beta", "Gamma", "délta", "epsilon", "neural-networks", "東京", "ATP", "studies", "[[Alpha]]", "#tag"]
    state = {"pages": [], "items": []}
    old = datetime.now(timezone.utc) - timedelta(days=40)
    for index in range(70):
        at = (old + timedelta(minutes=index)).isoformat() if seed % 2 and index < 35 else None
        _step(conn, rng, words, state, at)
        if index % 25 == 24:
            assert_exact(conn)
    if seed % 2:
        history.compact(conn)
        assert conn.execute("SELECT COUNT(*) FROM page_snapshots").fetchone()[0] > 0
    search.sync(conn)
    assert stored(conn) == rebuilt(conn, "log")
    assert_exact(conn)


def test_the_migration_builds_the_index_for_existing_data(tmp_path):
    before = tmp_path / "before"
    before.mkdir()
    for migration in available_migrations():
        if migration.version < 44:
            shutil.copy(migration.path, before / migration.path.name)
    conn = connect(tmp_path / "data" / "kitty.db")
    migrate(conn, before)
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='search_docs'").fetchone() is None
    root, _ = make_page(conn, "Existing page", "written before the index")
    conn.execute("INSERT INTO study_subjects(name) VALUES('Existing subject')")
    assert [m.version for m in migrate(conn)] == [m.version for m in available_migrations() if m.version >= 44]
    assert {kind for kind, _ in hits(search.search(conn, "existing"))} == {"note_page", "study_subject"}
    assert search.check(conn)["ok"]
    conn.close()


def test_unlinked_mentions_and_backlinks_cover_every_page(workspace):
    conn = workspace.conn
    book = notebook(conn)
    ops = []
    for index in range(620):
        ops += page_ops(conn, f"Distinct topic number {index:04d}", f"body {index}", number=10_000 + index, book=book)[2]
    blocks.transact(conn, ops)
    # The old matcher compiled only the 500 longest titles; "Oz" is the shortest of 621.
    target, _ = make_page(conn, "Oz", "a short title")
    source, (child,) = make_page(conn, "Reading list", "Travel to Oz and Distinct topic number 0003 next week")
    mentioned = note_blocks.mentions(conn, number_of(conn, source))
    assert {item["title"] for item in mentioned["mentions"]} == {"Oz", "Distinct topic number 0003"}
    assert all(item["block_id"] == child for item in mentioned["mentions"]) and not mentioned["truncated"]
    # The old scan read the 1,000 most recent pages; the oldest is found now.
    blocks.transact(conn, page_ops(conn, "Oldest", "zebrafish regeneration", number=9_999, book=book)[2],
                    at="2020-01-01T00:00:00+00:00")
    for index in range(2):
        ops = []
        for item in range(600):
            ops += page_ops(conn, f"Filler {index}-{item}", "unrelated words here", number=20_000 + index * 600 + item, book=book)[2]
        blocks.transact(conn, ops)
    assert search.search(conn, "zebrafish")["hits"][0]["title"] == "Oldest"
    assert [ref["title"] for ref in backlinks.mentions(conn, "zebrafish regeneration")["references"]] == ["Oldest"]
    assert [ref["title"] for ref in backlinks.related(conn, "zebrafish regeneration study")["related"]] == ["Oldest"]


def test_notes_find_reads_the_index_and_reports_truncation(workspace):
    conn = workspace.conn
    root, _ = make_page(conn, "Cell biology", "The mitochondrion is the powerhouse")
    make_page(conn, "Unrelated", "Nothing here")
    number = number_of(conn, root)
    assert [page["id"] for page in notes.listing(conn, "powerhouse")["pages"]] == [number]
    assert [page["id"] for page in notes.listing(conn, "owerhou")["pages"]] == [number]  # substring, as before
    assert [page["id"] for page in notes.listing(conn, "mito")["pages"]] == [number]
    assert notes.listing(conn, "Synthetic")["pages"] and not notes.listing(conn)["truncated"]  # notebook title


def test_a_database_without_the_index_reports_it():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(search.SearchUnavailable):
        search.search(conn, "anything")
    with pytest.raises(search.SearchUnavailable):
        search.check(conn)


def test_kitty_index_check_reports_repairs_and_exits_nonzero_on_a_difference(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from kitty.cli import app
    from kitty.settings import Settings

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "build.json").write_text('{"schema": 1, "modules": []}')
    monkeypatch.setenv("KITTY_HOME", str(tmp_path))
    runner = CliRunner()
    first = runner.invoke(app, ["index", "check"])
    assert first.exit_code == 0, first.output
    assert "equals the rebuild" in first.output
    conn = connect(Settings.load().db_path)
    make_page(conn, "Synthetic page", "indexed text")
    conn.execute("DELETE FROM search_docs WHERE type='text'")
    conn.close()
    broken = runner.invoke(app, ["index", "check", "--no-repair"])
    assert broken.exit_code == 1 and "1 mismatched, 0 repaired" in broken.output and "missing: block" in broken.output
    repaired = runner.invoke(app, ["index", "check"])
    assert repaired.exit_code == 1 and "1 repaired" in repaired.output
    assert runner.invoke(app, ["index", "check", "--from", "projection"]).exit_code == 0
    rebuilt_run = runner.invoke(app, ["index", "rebuild"])
    assert rebuilt_run.exit_code == 0 and "Indexed 2 documents" in rebuilt_run.output
