"""Search in rika-server (server/src/search) against kitty.core.search, the reference.

`GET /api/search` is answered by the front; every answer must be the one ``search.search``
gives on the same database: the same hits in the same order (ranking ties included), titles,
marks, snippets, totals and refusals. `rika-server search-sync` must fold the
``search_pending`` queue into exactly the rows (and rowids) ``search.sync`` writes, and
`rika-server search` must answer exactly as ``search.search`` over a corpus of every source
kind and a battery of queries: word prefixes, substrings, diacritics, case folding beyond
ASCII, CJK, HTML entities, quotes and FTS5 syntax, limits, kinds and module sets.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import uuid

import pytest

from kitty.core import blocks, jobs, notes, quick_notes, search
from kitty.core.blocks import tags
from kitty.db import connect
from kitty.modules import DEFAULT_MODULES
from search_tables import TABLES, tables
from terminal_support import RIKA_SERVER, front_site

STAMP = "2026-01-01T00:00:00+00:00"
WORDS = ["alpha", "Alphabet", "beta", "Gamma", "délta", "Café", "cafe", "STRASSE", "straße", "ﬁnance", "neural-networks",
         "東京都庁", "へ行く", "ATP", "studies", "[[Alpha]]", "#tag", "x²", "naïve", "İstanbul", "ǅemal", "Ωmega", "ὈΔΥΣΣΕΎΣ",
         "quote\"d", "under_score", "o'clock", "AND", "NEAR", "*", "(paren", "x" * 40]
QUERIES = ["alpha", "alp", "a", "al", "ALPHA", "Alpha beta", "beta alpha", "delta", "délta", "cafe", "CAFÉ", "strasse",
           "STRASSE", "ß", "straße", "fin", "finance", "ﬁ", "東京", "都庁へ", "へ", "neural-net", "networks", "x²", "x2",
           "naive", "istanbul", "İst", "dž", "ǅ", "ωmega", "οδυσσευς", "quote\"d", "\"", "\"\"\"", "under_score", "under",
           "o'clock", "clock", "AND", "and alpha", "NEAR", "*", "alpha*", "(paren", "paren)", "-beta", "^alpha", "{title}",
           "title: alpha", "a b c d e f g h i j k l m n o p q r s", "!!!", "", "   ", "  alpha   beta ", "lph", "mma",
           "x" * 40, "x" * 300, "x" * 301, "a\x00b", "½", "frac", "&amp;", "h-bar", "spin", "quantum", "Renamed",
           "interview", "homework", "remote", "ab", "phabet", "studies alpha"]


@pytest.fixture(autouse=True)
def _built():
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER}); run cargo build --release in server/")


def test_the_rust_text_tables_match_this_python():
    assert TABLES.read_text(encoding="utf-8") == tables(), \
        "server/src/search/tables.rs is stale: run `uv run python tests/search_tables.py`"


def _text(rng: random.Random) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(0, 6)))


def _notebook(conn) -> int:
    row = conn.execute("SELECT id FROM note_notebooks ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else notes.create_notebook(conn, "Synthetic")["id"]


def populate(conn, rng: random.Random, size: int = 30) -> None:
    """Every source kind, with text drawn from WORDS (HTML and entities where Python cleans them)."""
    book = _notebook(conn)
    for index in range(size):
        root = str(uuid.uuid4())
        ops = [{"op": "insert", "id": root, "type": "page", "parent": None, "text": _text(rng) or "Untitled",
                "props": {"number": notes.next_number(conn, "page"), "notebook_id": book, "revision": 1}}]
        for _ in range(rng.randint(0, 4)):
            ops.append({"op": "insert", "id": str(uuid.uuid4()), "type": rng.choice(["text", "heading_1", "code", "image", "to_do"]),
                        "parent": root, "text": _text(rng)})
        blocks.transact(conn, ops)
        if index % 3 == 0:
            quick_notes.create(conn, f"{_text(rng)}\n{_text(rng)}")
        if index % 5 == 0:
            tags.create(conn, {"name": f"Tag {index} {rng.choice(WORDS)}"[:60], "keywords": [rng.choice(WORDS)[:30]],
                               "aliases": [f"alias{index}"]})
        conn.execute("INSERT INTO study_subjects(name) VALUES(?)", (_text(rng) or "Subject",))
        conn.execute("INSERT INTO study_tasks(title,checklist_json) VALUES(?,?)", (_text(rng) or "Task", json.dumps(
            [{"id": "a", "text": _text(rng), "done": False}, "loose", {"text": 3}] if index % 2 else {"text": "not a list"})))
        conn.execute("""INSERT INTO calendar_events(id,calendar_id,title,start,end,timezone,data,updated_at,source_key)
            VALUES(?,'personal',?,'2026-02-01','2026-02-02','UTC',?,?,?)""",
                     (f"evt{index}", _text(rng) or "Event", json.dumps({"description": _text(rng)}) if index % 4 else "{bad",
                      STAMP, rng.choice([None, f"application:{index}", f"job:{index}", f"google:{index}"])))
        conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES(?,?,?)",
                     (f"tsk{index}", json.dumps({"title": _text(rng) or "Todo", "description": _text(rng)} if index % 3 else [1]), STAMP))
        deck = conn.execute("INSERT INTO flash_decks(name,settings,created_at) VALUES(?,'{}',?)", (f"{_text(rng)} {index}", STAMP)).lastrowid
        kind = conn.execute("INSERT INTO flash_types(name,kind,fields,templates) VALUES(?,'basic',?,'[]')",
                            (f"Synthetic {index}", json.dumps(["Front", "Back", "Extra"]),)).lastrowid
        conn.execute("INSERT INTO flash_notes(guid,deck_id,type_id,fields,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                     (f"g{index}", deck, kind, json.dumps({"Back": f"&frac12; <i>{_text(rng)}</i> &amp &ampx &#x41;",
                                                           "Front": f"<b>{_text(rng)}</b> spin" if index % 2 else "   ",
                                                           "Extra": 4}), STAMP, STAMP))
        jobs.upsert_job(conn, company=rng.choice(["Example Labs", "Synthetic Co", "Café Corp"]), title=_text(rng) or "Job",
                        url=f"https://example.invalid/{index}", location=rng.choice(["Remote", "", "Zürich"]),
                        jd_text=f"<p>{_text(rng)}</p> &lt;tag&gt; &notit; {_text(rng)}")
    conn.execute("UPDATE flash_decks SET archived=1 WHERE id IN (SELECT id FROM flash_decks ORDER BY id LIMIT 2)")
    conn.execute("UPDATE calendar_events SET deleted=1 WHERE id='evt1'")


def rust(*args: str, stdin: str | None = None):
    done = subprocess.run([str(RIKA_SERVER), *args], capture_output=True, text=True, timeout=300, input=stdin)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout) if stdin is None else [json.loads(line) for line in done.stdout.splitlines()]


def python_answer(conn, text, limit=20, kinds=None, modules=("applyai", "workspace")):
    try:
        return search.search(conn, text, limit=limit, kinds=kinds, modules=modules)
    except ValueError as exc:
        return {"error": str(exc)}


def rows(conn) -> list[tuple]:
    return [tuple(row) for row in conn.execute("SELECT * FROM search_docs ORDER BY id")]


@pytest.mark.parametrize("seed", range(3))
def test_sync_writes_the_rows_python_writes(workspace, tmp_path, seed):
    conn = workspace.conn
    populate(conn, random.Random(seed))
    pending = conn.execute("SELECT COUNT(*) FROM search_pending").fetchone()[0]
    assert pending > 100
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    copy = tmp_path / "copy.db"
    shutil.copy(workspace.settings.db_path, copy)
    assert rust("search-sync", str(copy)) == pending
    assert search.sync(conn) == pending
    other = connect(copy)
    try:
        assert rows(other) == rows(conn)
        assert other.execute("SELECT COUNT(*) FROM search_pending").fetchone()[0] == 0
        for table in ("search_fts", "search_grams"):
            other.execute(f"INSERT INTO {table}({table}, rank) VALUES('integrity-check', 1)")
        # A second change set: updates and deletes queue the same records again.
        for target in (conn, other):
            target.execute("UPDATE study_subjects SET name=name||' renamed' WHERE id % 2 = 0")
            target.execute("DELETE FROM study_tasks WHERE id % 3 = 0")
            target.execute("UPDATE companies SET display_name='Renamed Optics' WHERE id=(SELECT MIN(id) FROM companies)")
            target.execute("UPDATE flash_types SET fields=? WHERE id % 2 = 1", (json.dumps(["Back", "Front"]),))
            target.execute("UPDATE jobs SET jd_text='&lt;b&gt; plain' WHERE id % 4 = 1")
        search.sync(conn)
        rust("search-sync", str(copy))
        assert rows(other) == rows(conn)
    finally:
        other.close()


def test_every_query_is_answered_as_python_answers_it(workspace):
    conn = workspace.conn
    populate(conn, random.Random(7), size=40)
    search.sync(conn)
    db = str(workspace.settings.db_path)
    cases = [(text, 20, None, ("applyai", "workspace")) for text in QUERIES]
    cases += [(text, limit, None, ("applyai", "workspace")) for text in ("alpha", "a", "beta", "caf") for limit in (1, 2, 3, 7, 200, 0, 201)]
    cases += [(text, 20, kinds, modules) for text in ("alpha", "spin", "studies", "naive")
              for kinds in (None, ["study_subject"], ["job", "calendar_event"], ["note_page", "tag", "quick_note"], ["nope"], [""])
              for modules in (("applyai", "workspace"), ("workspace",), ("applyai",), ())]
    requests = "".join(json.dumps({"q": text, "limit": limit, "kinds": kinds, "modules": list(modules)}) + "\n"
                       for text, limit, kinds, modules in cases)
    answers = rust("search", db, stdin=requests)
    assert len(answers) == len(cases)
    for (text, limit, kinds, modules), answer in zip(cases, answers):
        expected = python_answer(conn, text, limit, kinds, modules)
        assert answer == expected, (text, limit, kinds, modules)
        assert json.dumps(answer) == json.dumps(expected), "the same key order"


def test_the_front_answers_search_as_python_does(workspace):
    conn = workspace.conn
    populate(conn, random.Random(11))
    with front_site(workspace.settings) as running:
        # The front folds the queue the Python writes above left behind.
        assert conn.execute("SELECT COUNT(*) FROM search_pending").fetchone()[0] > 0
        status, answer = running.request("/api/search?q=alpha&limit=5")
        assert status == 200
        assert conn.execute("SELECT COUNT(*) FROM search_pending").fetchone()[0] == 0
        assert answer == search.search(conn, "alpha", limit=5, modules=DEFAULT_MODULES)
        assert answer["truncated"] and answer["total"] > 5
        # A write the worker's process did not make is searchable at once.
        conn.execute("INSERT INTO study_subjects(name) VALUES('Zymurgy studies')")
        status, answer = running.request("/api/search?q=zymur&kinds=study_subject,job")
        assert status == 200 and [hit["title"] for hit in answer["hits"]] == ["Zymurgy studies"]
        assert answer == search.search(conn, "zymur", kinds=["study_subject", "job"], modules=DEFAULT_MODULES)
        for target, error in (
            ("/api/search", "Search takes q and optional limit and kinds"),
            ("/api/search?q=a&page=2", "Search takes q and optional limit and kinds"),
            ("/api/search?q=a&limit=0", "Limit must be 1-200"),
            ("/api/search?q=a&limit=01", "Limit must be 1-200"),
            ("/api/search?q=a&limit=999", "Limit must be 1-200"),
            ("/api/search?q=a&limit=1000", "Limit must be 1-200"),
            ("/api/search?q=a&kinds=page", "Kinds are " + ", ".join(search.KINDS)),
            ("/api/search?q=" + "x" * 301, "Search text must be a string of at most 300 characters"),
            ("/api/search?q=a%00b", "Search text must be a string of at most 300 characters"),
        ):
            assert running.request(target) == (400, {"error": error}), target
        status, head = running.request("/api/search?q=alpha", method="HEAD")
        assert status == 200 and not head
        # A POST is no route of its own: the worker answers it, as before.
        assert running.request("/api/search", {"q": "alpha"}) == (404, {"error": "API route not found"})


def test_without_the_workspace_module_only_jobs_are_searched(tmp_path):
    from test_web_modules import make_root

    settings = make_root(tmp_path, ("applyai",))
    with front_site(settings) as running:
        conn = connect(settings.db_path)
        try:
            conn.execute("INSERT INTO study_subjects(name) VALUES('Quantum optics')")
            job = jobs.upsert_job(conn, company="Example Labs", title="Quantum Engineer", url="https://example.invalid/q")[0].id
            status, answer = running.request("/api/search?q=quantum")
            assert status == 200 and [(hit["kind"], hit["id"]) for hit in answer["hits"]] == [("job", job)]
            assert answer == search.search(conn, "quantum", modules=("applyai",))
        finally:
            conn.close()
