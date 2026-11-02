"""Auto-tags (platform design Q13): the matcher, both modes, rejections, the projection,
tag pages, the backfill of existing #tags and the cost on the transaction path."""
from __future__ import annotations

import json
import shutil
import sqlite3
import statistics
import time
import uuid

import pytest

from kitty.core import blocks, note_blocks, notes
from kitty.core.blocks import tagging, tags
from kitty.core.blocks.autotag import Tag, Vocabulary, utf16
from kitty.db import connect, migrate
from kitty.db.migrations import available_migrations


def new_id():
    return str(uuid.uuid4())


def page_ops(page, *children, title="Page", number=None):
    props = {"revision": 1, **({"number": number} if number is not None else {})}
    ops = [{"op": "insert", "id": page, "type": "page", "parent": None, "text": title, "props": props}]
    for child, text in children:
        ops.append({"op": "insert", "id": child, "type": "text", "parent": page, "text": text})
    return ops


def spans(vocab, text, **kwargs):
    return [(match.tag, text[match.start:match.end]) for match in vocab.scan(text, **kwargs)]


def attached(conn, block=None):
    rows = conn.execute("SELECT block_id, tag_id, source FROM block_tags" + (" WHERE block_id=?" if block else ""),
                        (block,) if block else ()).fetchall()
    return {(row[0], row[1]): row[2] for row in rows}


def rejected(conn):
    return {(row[0], row[1]) for row in conn.execute("SELECT block_id, tag_id FROM tag_rejections")}


def type_(conn, block, text):
    """What the editor sends while typing: the block's new text, with suggestions back."""
    return blocks.transact(conn, [{"op": "text", "id": block, "text": text}], suggest=True)


def mode(workspace, value):
    tags.set_mode(workspace.conn, workspace.settings.root, value, tags.settings(workspace.conn)["revision"])


# The matcher ------------------------------------------------------------------------------

def test_matching_is_casefolded_whole_words_without_stemming():
    vocab = Vocabulary.of([Tag("py", "Python", ("CPython",)), Tag("ml", "ML", ("machine learning", "learning"))])
    assert spans(vocab, "I like PYTHON, cpython and pythonic code") == [("py", "PYTHON"), ("py", "cpython")]
    assert spans(vocab, "Pythons and tags") == []  # no stemming
    assert spans(vocab, "Machine Learning, learning; ML") == [("ml", "Machine Learning"), ("ml", "learning")]
    assert spans(vocab, "machine-learning") == [("ml", "learning")], "a hyphen is a word boundary, not a space"


def test_short_terms_need_the_tag_to_opt_in():
    assert spans(Vocabulary.of([Tag("a", "AI", ("go",))]), "AI and go") == []
    assert spans(Vocabulary.of([Tag("a", "AI", ("go",), short=True)]), "AI and go, not ago") == [("a", "AI"), ("a", "go")]


def test_code_urls_mail_and_existing_links_are_skipped():
    vocab = Vocabulary.of([Tag("py", "python")])
    text = ("`python` http://python.org/x www.python.org me@python.dev #python [[Python]] [[a|python]] "
            "```\npython\n``` python")
    assert spans(vocab, text) == [("py", "python")] and vocab.scan(text)[0].start == len(text) - 6
    # Inline code marks are UTF-16 ranges, as the editor writes them (an emoji is two units).
    marked = "🎉 python python"
    assert spans(vocab, marked, marks=[{"s": 3, "e": 9, "t": "c"}]) == [("py", "python")]
    assert vocab.scan(marked, marks=[{"s": 3, "e": 9, "t": "c"}])[0].start == 9


def test_folding_that_changes_length_maps_back_to_the_written_text():
    vocab = Vocabulary.of([Tag("s", "straße")])
    text = "Die STRASSE, die Straße."
    assert spans(vocab, text) == [("s", "STRASSE"), ("s", "Straße")]
    assert utf16("🎉 Straße", [2, 8]) == [3, 9]


def test_inline_tokens_name_tags_by_name_or_alias():
    vocab = Vocabulary.of([Tag("py", "Python", aliases=("py3",)), Tag("go", "golang")])
    assert vocab.inline("see #python and #PY3 but not #rust") == {"py"}


# Settings and modes --------------------------------------------------------------------------

def test_new_installs_suggest_and_the_mode_is_revision_checked(workspace):
    conn = workspace.conn
    assert tags.settings(conn) == {"mode": "suggest", "revision": 1}
    root = workspace.settings.root
    assert tags.set_mode(conn, root, "always", 1) == {"mode": "always", "revision": 2}
    with pytest.raises(blocks.BlockError) as stale:
        tags.set_mode(conn, root, "suggest", 1)
    assert stale.value.status == 409
    with pytest.raises(blocks.BlockError):
        tags.set_mode(conn, root, "sometimes", 2)


def test_suggest_mode_returns_matches_and_attaches_nothing(workspace):
    conn = workspace.conn
    tag = tags.create(conn, {"name": "Python", "keywords": ["pandas"]})
    assert tag["effective_mode"] == "suggest" and tag["mode"] is None
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "")))
    result = type_(conn, block, "🎉 pandas with Python")
    assert result["suggestions"] == {block: {"text": "🎉 pandas with Python", "items": [
        {"tag": tag["id"], "name": "Python", "start": 3, "end": 9},
        {"tag": tag["id"], "name": "Python", "start": 15, "end": 21}]}}
    assert result["tags"] == {tag["id"]: "Python"} and "tagged" not in result
    assert attached(conn) == {}


def test_tab_accepts_and_esc_rejects_for_good(workspace):
    conn = workspace.conn
    tag = tags.create(conn, {"name": "Python"})
    key = tagging.key_of(tag["id"])
    page, first, second = new_id(), new_id(), new_id()
    blocks.transact(conn, page_ops(page, (first, "python one"), (second, "python two")))
    before = conn.execute("SELECT properties, updated_at FROM blocks WHERE id=?", (page,)).fetchone()
    # Tab: the editor sets the block's state for the tag; tagging is not an edit (no revision bump).
    accept = blocks.transact(conn, [{"op": "set", "id": first, "props": {key: "accepted"}}], suggest=True)
    assert accept["revisions"] == {page: json.loads(before["properties"])["revision"]}
    assert accept["suggestions"][first]["items"] == []
    assert attached(conn) == {(first, tag["id"]): "accepted"}
    # Esc: rejected, remembered; later edits never suggest it again.
    blocks.transact(conn, [{"op": "set", "id": second, "props": {key: "rejected"}}])
    assert rejected(conn) == {(second, tag["id"])}
    assert type_(conn, second, "python two, python again")["suggestions"][second]["items"] == []
    mode(workspace, "always")
    assert "tagged" not in type_(conn, second, "python, still python")
    assert attached(conn) == {(first, tag["id"]): "accepted"}
    with pytest.raises(blocks.BlockError):
        blocks.transact(conn, [{"op": "set", "id": second, "props": {key: "maybe"}}])


def test_always_attach_marks_auto_and_removal_is_never_undone(workspace):
    conn = workspace.conn
    mode(workspace, "always")
    tag = tags.create(conn, {"name": "Rust", "keywords": ["cargo"]})
    key = tagging.key_of(tag["id"])
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "")))
    result = type_(conn, block, "cargo build")
    assert result["tagged"] == {page: [{"op": "set", "id": block, "props": {key: "auto"}}]}
    assert result["suggestions"][block]["items"] == [] and result["tags"] == {tag["id"]: "Rust"}
    assert attached(conn) == {(block, tag["id"]): "auto"}
    logged = json.loads(conn.execute("SELECT ops FROM op_log ORDER BY seq DESC LIMIT 1").fetchone()[0])["ops"]
    assert {"op": "set", "id": block, "props": {key: "auto"}} in logged, "the attachment is in the op log with the edit"
    assert blocks.replay(conn, page).blocks[block].properties[key] == "auto"
    # Removing an automatic tag is remembered: later edits never re-attach it.
    blocks.transact(conn, [{"op": "set", "id": block, "props": {key: "rejected"}}])
    assert attached(conn) == {} and rejected(conn) == {(block, tag["id"])}
    assert "tagged" not in type_(conn, block, "cargo build, cargo test, rust")
    assert attached(conn) == {}


def test_an_automatic_tag_follows_the_text(workspace):
    conn = workspace.conn
    mode(workspace, "always")
    tag = tags.create(conn, {"name": "python"})
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "")))
    type_(conn, block, "python")
    assert attached(conn) == {(block, tag["id"]): "auto"}
    # Still typing: "python" became "pythonic" — the automatic tag goes, without a rejection.
    result = type_(conn, block, "pythonic")
    assert result["tagged"] == {page: [{"op": "set", "id": block, "props": {tagging.key_of(tag["id"]): None}}]}
    assert attached(conn) == {} and rejected(conn) == set()
    type_(conn, block, "pythonic python")
    assert attached(conn) == {(block, tag["id"]): "auto"}


def test_each_tag_overrides_the_global_mode_either_way(workspace):
    conn = workspace.conn
    eager = tags.create(conn, {"name": "eager", "mode": "always"})
    shy = tags.create(conn, {"name": "shy"})
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "")))
    result = type_(conn, block, "eager and shy")
    assert attached(conn) == {(block, eager["id"]): "auto"}
    assert [item["tag"] for item in result["suggestions"][block]["items"]] == [shy["id"]]
    mode(workspace, "always")
    tags.update(conn, shy["id"], {"mode": "suggest"})
    other = new_id()
    result = blocks.transact(conn, [{"op": "insert", "id": other, "type": "text", "parent": page, "text": "eager, shy"}], suggest=True)
    assert attached(conn, other) == {(other, eager["id"]): "auto"}
    assert [item["tag"] for item in result["suggestions"][other]["items"]] == [shy["id"]]
    assert tags.get(conn, shy["id"])["effective_mode"] == "suggest" and tags.get(conn, eager["id"])["effective_mode"] == "always"


def test_only_prose_of_note_pages_is_tagged(workspace):
    conn = workspace.conn
    mode(workspace, "always")
    tag = tags.create(conn, {"name": "python"})
    page, code, heading, other_root = new_id(), new_id(), new_id(), new_id()
    blocks.transact(conn, page_ops(page) + [
        {"op": "insert", "id": code, "type": "code", "parent": page, "text": "python"},
        {"op": "insert", "id": heading, "type": "heading_2", "parent": page, "text": "Python"}])
    blocks.transact(conn, [{"op": "insert", "id": other_root, "type": "quick_note", "parent": None, "text": "python"}])
    canvas, sticky = new_id(), new_id()
    blocks.transact(conn, [{"op": "insert", "id": canvas, "type": "canvas", "parent": page},
                           {"op": "insert", "id": sticky, "type": "text", "parent": canvas, "text": "python"}])
    assert attached(conn) == {(heading, tag["id"]): "auto"}
    tag_child = new_id()
    blocks.transact(conn, [{"op": "insert", "id": tag_child, "type": "text", "parent": tag["id"], "text": "python notes"}])
    assert (tag_child, tag["id"]) not in attached(conn)


# #tag tokens and tag records --------------------------------------------------------------------

def test_hash_tokens_are_manual_attachments_and_follow_tag_records(workspace):
    conn = workspace.conn
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "notes #Py3 and #later")))
    assert attached(conn) == {}  # no tag record yet: still a topic, as before
    assert {tuple(row) for row in conn.execute("SELECT kind, topic FROM block_topics")} == {("tag", "py3"), ("tag", "later")}
    python = tags.create(conn, {"name": "Python", "aliases": ["py3"]})
    assert attached(conn) == {(block, python["id"]): "manual"}, "a new record picks up existing #alias tokens"
    tags.update(conn, python["id"], {"aliases": []})
    assert attached(conn) == {}
    tags.update(conn, python["id"], {"name": "later"})
    assert attached(conn) == {(block, python["id"]): "manual"}
    tags.delete(conn, python["id"])
    assert attached(conn) == {}
    with pytest.raises(blocks.BlockError) as missing:
        tags.get(conn, python["id"])
    assert missing.value.status == 404


def test_names_and_aliases_are_unique(workspace):
    conn = workspace.conn
    tags.create(conn, {"name": "Python", "aliases": ["py"]})
    for body in ({"name": "python"}, {"name": "Snake", "aliases": ["PY"]}):
        with pytest.raises(blocks.BlockError) as clash:
            tags.create(conn, body)
        assert clash.value.status == 409
    with pytest.raises(blocks.BlockError):
        tags.create(conn, {"name": "  "})
    with pytest.raises(blocks.BlockError):
        tags.create(conn, {"name": "x", "keywords": "not a list"})
    assert tags.create(conn, {"name": "Go", "keywords": ["golang", " GoLang ", ""], "short": True})["keywords"] == ["golang"]


def test_a_new_keyword_takes_effect_on_the_next_edit(workspace):
    conn = workspace.conn
    tag = tags.create(conn, {"name": "databases"})
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "")))
    assert type_(conn, block, "sqlite rocks")["suggestions"][block]["items"] == []
    tags.update(conn, tag["id"], {"keywords": ["SQLite"]})
    assert [item["tag"] for item in type_(conn, block, "sqlite rocks!")["suggestions"][block]["items"]] == [tag["id"]]


def test_whole_page_saves_keep_tag_states(workspace):
    conn = workspace.conn
    tag = tags.create(conn, {"name": "python"})
    book = notes.create_notebook(conn, "Tag tests")
    number = notes.create_page(conn, book["id"], "Page")["id"]
    block = new_id()
    note_blocks.save_blocks(conn, number, 1, [{"id": block, "parent_id": None, "text": "python", "ordering": 0, "collapsed": False}])
    blocks.transact(conn, [{"op": "set", "id": block, "props": {tagging.key_of(tag["id"]): "accepted"}}])
    saved = note_blocks.get_blocks(conn, number)
    note_blocks.save_blocks(conn, number, saved["revision"], [{"id": block, "parent_id": None, "text": "python, edited", "ordering": 0,
                                                               "collapsed": False}])
    assert attached(conn) == {(block, tag["id"]): "accepted"}


def test_trash_hides_and_restore_brings_back_attachments(workspace):
    conn = workspace.conn
    tag = tags.create(conn, {"name": "python"})
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "python")))
    blocks.transact(conn, [{"op": "set", "id": block, "props": {tagging.key_of(tag["id"]): "accepted"}}])
    blocks.transact(conn, [{"op": "delete", "id": block}])
    assert attached(conn) == {}
    blocks.transact(conn, [{"op": "restore", "id": block}])
    assert attached(conn) == {(block, tag["id"]): "accepted"}
    blocks.transact(conn, [{"op": "delete", "id": block}])
    blocks.transact(conn, [{"op": "purge", "id": block}])
    assert attached(conn) == {}


# Tag pages ---------------------------------------------------------------------------------

def test_tag_page_lists_backlinks_and_unlinked_mentions_and_links_all(workspace):
    conn = workspace.conn
    tag = tags.create(conn, {"name": "Kafka", "keywords": ["event streaming"]})
    page, tagged, hashed, loose, loose2, refused, code = (new_id() for _ in range(7))
    blocks.transact(conn, page_ops(page, (tagged, "kafka consumer"), (hashed, "see #kafka"), (loose, "Event  streaming? event streaming!"),
                                   (loose2, "Kafka topics"), (refused, "kafka again"), title="Streams", number=3)
                    + [{"op": "insert", "id": code, "type": "code", "parent": page, "text": "kafka"}])
    key = tagging.key_of(tag["id"])
    blocks.transact(conn, [{"op": "set", "id": tagged, "props": {key: "accepted"}},
                           {"op": "set", "id": refused, "props": {key: "rejected"}}])
    view = tags.page(conn, tag["id"])
    assert view["tag"]["name"] == "Kafka" and view["tag"]["count"] == 2 == view["count"]
    assert {(item["id"], item["source"], item["inline"]) for item in view["blocks"]} == {(tagged, "accepted", False), (hashed, "manual", True)}
    assert all(item["number"] == 3 and item["title"] == "Streams" and item["page_type"] == "page" for item in view["blocks"])
    assert {item["id"] for item in view["unlinked"]} == {loose, loose2} and view["unlinked_count"] == 2, \
        "code, rejected and linked blocks are not mentions"
    assert tags.link_all(conn, tag["id"]) == {"linked": 2, "tag": tag["id"]}
    view = tags.page(conn, tag["id"])
    assert view["unlinked"] == [] and {(item["id"], item["source"]) for item in view["blocks"]} >= {(loose2, "accepted")}
    assert tags.list_tags(conn)["tags"][0]["count"] == 4


def test_attach_and_remove_by_hand(workspace):
    conn = workspace.conn
    tag = tags.create(conn, {"name": "later"})
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "nothing to match")))
    tags.attach(conn, block, tag["id"])
    assert attached(conn) == {(block, tag["id"]): "manual"}
    tags.remove(conn, block, tag["id"])
    assert attached(conn) == {} and rejected(conn) == {(block, tag["id"])}
    with pytest.raises(blocks.BlockError):
        tags.attach(conn, page, tag["id"])


def test_page_state_carries_suggestions_and_tag_names(workspace):
    conn = workspace.conn
    suggest = tags.create(conn, {"name": "graphs"})
    done = tags.create(conn, {"name": "trees"})
    page, block = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (block, "graphs and trees")))
    blocks.transact(conn, [{"op": "set", "id": block, "props": {tagging.key_of(done["id"]): "accepted"}}])
    state = blocks.page_state(conn, page)
    extra = tagging.page_suggestions(conn, state)
    assert extra == {"suggestions": {block: {"text": "graphs and trees", "items": [{"tag": suggest["id"], "name": "graphs", "start": 0, "end": 6}]}},
                     "tags": {suggest["id"]: "graphs", done["id"]: "trees"}}


# The backfill -------------------------------------------------------------------------------

def test_migration_turns_existing_hash_tags_into_tag_records(tmp_path):
    early = tmp_path / "early"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 37:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "kitty.db")
    migrate(conn, early)
    page, a, b, c = new_id(), new_id(), new_id(), new_id()
    blocks.transact(conn, page_ops(page, (a, "Read #MachineLearning first"), (b, "more #machinelearning and #Bio/Energy"), (c, "python")))
    migrate(conn)
    names = {tag["name"]: tag for tag in tags.list_tags(conn)["tags"]}
    assert set(names) == {"MachineLearning", "Bio/Energy"}, "spelled as first written"
    assert attached(conn) == {(a, names["MachineLearning"]["id"]): "manual", (b, names["MachineLearning"]["id"]): "manual",
                              (b, names["Bio/Energy"]["id"]): "manual"}
    assert tags.settings(conn)["mode"] == "suggest"
    assert conn.execute("SELECT COUNT(*) FROM op_log WHERE page_id IN (SELECT id FROM blocks WHERE type='tag')").fetchone()[0] == 2
    conn.close()


def test_transactions_before_the_tag_tables_exist_still_work(tmp_path):
    early = tmp_path / "early"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 37:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "kitty.db")
    migrate(conn, early)
    page, block = new_id(), new_id()
    result = blocks.transact(conn, page_ops(page, (block, "#tag")), suggest=True)
    assert set(result) == {"txn_id", "seq", "revisions"}
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT * FROM block_tags")
    conn.close()


# Cost on the transaction path -----------------------------------------------------------------

WORDS = ["design", "review", "queue", "latency", "replica", "storage", "index", "cache", "vector", "graph", "parser", "compiler", "runtime", "kernel", "network", "socket", "thread", "memory", "schema", "migration", "backup", "restore", "editor"]


def test_perf_typing_on_a_2000_block_page_with_a_1000_tag_vocabulary(workspace, capsys):
    """Generous budget; the measured numbers are printed (pytest -s) and recorded in docs/platform-design.md."""
    conn = workspace.conn
    tag_ops = [{"op": "insert", "id": new_id(), "type": "tag", "parent": None, "text": f"topic{index:04d}",
                "props": {"revision": 1, "keywords": [f"{WORDS[index % len(WORDS)]} {index}", f"term{index}x", f"{WORDS[index % 7]}{index}"],
                          "aliases": []}} for index in range(1000)]
    tag_ops[0]["props"]["keywords"].append("latency")
    tag_ops[1]["props"]["keywords"].append("replica")
    blocks.transact(conn, tag_ops)
    page = new_id()
    children = [(new_id(), " ".join(WORDS[(index + offset) % len(WORDS)] for offset in range(16)) + f" term{index}x")
                for index in range(2000)]
    started = time.perf_counter()
    blocks.transact(conn, page_ops(page, *children))
    import_seconds = time.perf_counter() - started
    target = children[1000][0]
    text = children[1000][1]

    def typing(rounds: int, suggest: bool) -> list[float]:
        timings = []
        for index in range(rounds):
            started = time.perf_counter()
            blocks.transact(conn, [{"op": "text", "id": target, "at": len(text) + index, "del": 0, "ins": "y"}], suggest=suggest)
            timings.append(time.perf_counter() - started)
        return timings

    typing(3, True)  # warm the vocabulary cache
    with_tags = typing(40, True)
    started = time.perf_counter()
    state = blocks.page_state(conn, page)
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    found = tagging.page_suggestions(conn, state)
    scan_seconds = time.perf_counter() - started
    vocab = tagging.vocabulary(conn)
    started = time.perf_counter()
    for _ in range(100):
        vocab.scan(text)
    block_scan = (time.perf_counter() - started) / 100
    started = time.perf_counter()
    tagging.load_vocabulary(conn)
    build_seconds = time.perf_counter() - started
    conn.execute("UPDATE tag_settings SET vocabulary=vocabulary+1")
    without = []
    # Baseline: the same keystroke with the hooks bypassed (as on a database before migration 037).
    original = tagging._settings
    tagging._settings = lambda conn: None
    try:
        without = typing(40, True)
    finally:
        tagging._settings = original
    median, p95, base = statistics.median(with_tags), sorted(with_tags)[37], statistics.median(without)
    with capsys.disabled():
        print(f"\n[auto-tags perf] 2000 blocks x 1000 tags: keystroke txn median {median * 1000:.2f} ms "
              f"(p95 {p95 * 1000:.2f} ms) vs {base * 1000:.2f} ms without tagging; one block scan {block_scan * 1e6:.0f} us; "
              f"vocabulary build {build_seconds * 1000:.1f} ms; whole-page suggestions {scan_seconds * 1000:.1f} ms "
              f"(page load {load_seconds * 1000:.1f} ms); 2000-block import {import_seconds * 1000:.0f} ms")
    assert len(found["suggestions"]) > 1000
    assert median < 0.25 and median - base < 0.05, "tagging must not slow typing"
    assert scan_seconds < 3
