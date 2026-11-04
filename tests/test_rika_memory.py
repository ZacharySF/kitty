"""Rika's memory (design Q18): the local FTS5 index, model-bound retrieval that keeps note
text private, editable preferences, and the memory tools. Synthetic data only."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from kitty.core.rika import commands, memory, store, tools
from kitty.core.rika.tools import Context, ToolError, validate

REGISTRY = tools.registry()
MODULES = ("workspace", "applyai")


@pytest.fixture
def ctx(workspace):
    app = SimpleNamespace(settings=workspace.settings, modules=("workspace",), mutation_lock=threading.Lock())
    return Context(app, workspace.conn)


def run(ctx, name, /, **args):
    tool = REGISTRY[name]
    validate(tool.parameters, args)
    return tool.run(ctx, args)


def _filler(ctx, count=12):
    """Unrelated pages, so a rare word scores above the relevance floor."""
    for index in range(count):
        run(ctx, "create_page", title=f"Filler page {index}")


def _sources(conn):
    return {item["source"]: item for item in memory.index(conn)["sources"]}


def test_the_index_covers_every_source_and_rebuilds_only_what_changed(ctx):
    conn = ctx.conn
    page = run(ctx, "create_page", title="Ribosome lecture")
    run(ctx, "append_block", text="Ribosomes translate messenger RNA", page_id=page.data["page_id"])
    run(ctx, "create_event", title="Ribosome lab", start="2026-11-06T10:00")
    run(ctx, "create_task", title="Read the ribosome chapter")
    conn.execute("INSERT INTO companies(norm_name,display_name,created_at,updated_at) VALUES('acme','Acme Bio',?,?)",
                 (store.now(), store.now()))
    conn.execute("INSERT INTO jobs(company_id,title,location,url,dedupe_key,first_seen_at,last_seen_at,created_at,updated_at)"
                 " VALUES(1,'Ribosome research intern','Waco, TX','https://example.test/1','k',?,?,?,?)",
                 (store.now(),) * 4)
    conn.execute("INSERT INTO rika_mail_messages(id,thread_id,sender,sender_email,subject,body,received_at,kind,needs_reply,"
                 "fetched_at) VALUES('m1','t1','Dr Lee','lee@example.test','Ribosome seminar','See you Friday',?,"
                 "'scheduling',1,?)", (store.now(), store.now()))
    conn.execute("INSERT INTO goals(title,why,created_at,updated_at) VALUES('Master ribosomes','exam',?,?)",
                 (store.now(), store.now()))
    memory.add_preference(conn, "Explain ribosomes simply", source="added")
    conn.execute("INSERT INTO record_events(at,section,entity,entity_id,action,route) VALUES(?,'study','ribosome quiz',"
                 "'7','update','/api/study')", (store.now(),))
    reply = store.add_message(conn, conversation_id="c", role="rika", text="")
    store.add_action(conn, message_id=reply["id"], tool="add_card", args={}, summary="Added the card Ribosomes",
                     reversible=False, undo=None)
    memory.refresh(conn, modules=MODULES)
    sources = _sources(conn)
    assert [item["source"] for item in memory.index(conn)["sources"]] == list(memory.SOURCES)
    assert sources["page"]["label"] == "Pages" and sources["oplog"]["label"] == "Edit history"
    for name in ("page", "block", "event", "task", "job", "mail", "goal", "preference"):
        assert sources[name]["items"] >= 1, name
    assert sources["oplog"]["items"] == 2  # record_events and Rika's own action summaries
    assert memory.index(conn)["total"] == sum(item["items"] for item in sources.values())
    found = {item["source"] for item in memory.search(conn, "ribosome")}
    assert found >= {"page", "block", "event", "task", "job", "mail", "goal", "preference", "oplog"}
    stamp = sources["job"]["refreshed_at"]
    memory.refresh(conn, modules=MODULES)
    assert _sources(conn)["job"]["refreshed_at"] == stamp  # unchanged: not rebuilt
    run(ctx, "create_page", title="Golgi apparatus")
    memory.refresh(conn, modules=MODULES)
    assert _sources(conn)["page"]["refreshed_at"] != stamp and _sources(conn)["job"]["refreshed_at"] == stamp
    # Jobs belong to applyai: with it off the source empties.
    memory.refresh(conn, modules=("workspace",))
    assert _sources(conn)["job"]["items"] == 0 and not [item for item in memory.search(conn, "intern")]


def test_search_ranks_and_snippets_results_with_their_view(ctx):
    _filler(ctx)
    page = run(ctx, "create_page", title="Krebs cycle")
    run(ctx, "append_block", text="The citric acid cycle releases stored energy", page_id=page.data["page_id"])
    memory.refresh(ctx.conn, modules=MODULES)
    results = memory.search(ctx.conn, "citric acid")
    hit = next(item for item in results if item["source"] == "block")
    assert set(hit) == {"source", "ref", "title", "snippet", "view"}
    assert hit["title"] == "Krebs cycle" and "citric acid" in hit["snippet"]
    assert hit["view"] == "notes" and hit["ref"]
    assert memory.search(ctx.conn, "krebs")[0]["source"] == "page"
    assert memory.search(ctx.conn, "") == [] and memory.search(ctx.conn, '"*) OR (') == []
    assert len(memory.search(ctx.conn, "filler", limit=5)) == 5


def test_retrieval_for_the_model_withholds_note_text_until_notes_are_shared(ctx):
    conn = ctx.conn
    _filler(ctx)
    page = run(ctx, "create_page", title="Photosynthesis")
    run(ctx, "append_block", text="Chlorophyll absorbs red and blue light", page_id=page.data["page_id"])
    run(ctx, "create_event", title="Chlorophyll lab", start="2026-11-06T10:00")
    memory.refresh(conn, modules=MODULES)
    hidden = memory.retrieve(conn, "what did I write about chlorophyll?", allow_note_text=False)
    assert "untrusted" in hidden and "Chlorophyll lab" in hidden and "Photosynthesis" in hidden
    assert "absorbs red" not in hidden and "withheld" in hidden
    shared = memory.retrieve(conn, "what did I write about chlorophyll?", allow_note_text=True)
    assert "absorbs red" in shared
    assert memory.retrieve(conn, "tell me a joke about zebras", allow_note_text=True) is None
    # Words found almost everywhere fall below the relevance floor; at most six items come back.
    assert memory.retrieve(conn, "filler page", allow_note_text=True) is None
    _filler(ctx, 40)  # a word in a dozen items is still rare enough in a bigger index
    for index in range(10):
        run(ctx, "create_event", title=f"Chlorophyll review {index}", start="2026-11-07T10:00")
    memory.refresh(conn, modules=MODULES)
    assert memory.retrieve(conn, "chlorophyll", allow_note_text=True).count("\n- ") == memory.RETRIEVE_LIMIT == 6


def test_preferences_are_editable_capped_and_sent_as_one_block(ctx):
    conn = ctx.conn
    assert memory.preferences_prompt(conn) == ""
    first = memory.add_preference(conn, "  Keep replies short  ", source="added")
    assert set(first) == {"id", "text", "source", "created_at", "updated_at"}
    assert first["text"] == "Keep replies short" and first["source"] == "added"
    edited = memory.update_preference(conn, first["id"], "Keep replies very short")
    assert edited["text"] == "Keep replies very short" and edited["updated_at"] >= first["updated_at"]
    memory.add_preference(conn, "Use metric units", source="stated")
    assert [item["text"] for item in memory.preferences(conn)] == ["Keep replies very short", "Use metric units"]
    prompt = memory.preferences_prompt(conn)
    assert "- Keep replies very short" in prompt and "- Use metric units" in prompt
    for bad in ("", "   ", "x" * 501, 3):
        with pytest.raises(ValueError):
            memory.add_preference(conn, bad, source="added")
    with pytest.raises(LookupError):
        memory.update_preference(conn, 999, "x")
    assert memory.delete_preference(conn, first["id"]) == first["id"]
    with pytest.raises(LookupError):
        memory.delete_preference(conn, first["id"])
    for index in range(memory.PREFERENCES_MAX - 1):
        memory.add_preference(conn, f"Preference {index}", source="added")
    with pytest.raises(ValueError, match="at most"):
        memory.add_preference(conn, "One too many", source="added")


def test_preferences_never_exceed_the_character_budget(ctx):
    for index in range(8):
        memory.add_preference(ctx.conn, f"{index} " + "y" * 490, source="added")
    with pytest.raises(ValueError, match="characters"):
        memory.add_preference(ctx.conn, "z" * 500, source="added")
    assert len(memory.preferences_prompt(ctx.conn)) <= memory.PREFERENCES_CHARS + 400


def test_remember_preference_is_a_change_undone_only_while_unchanged(ctx):
    tool = REGISTRY["remember_preference"]
    assert tool.effect == "change" and tool.reversible and REGISTRY["search_memory"].effect == "read"
    result = run(ctx, "remember_preference", text="I study best in the mornings")
    assert result.summary == "Remembered: I study best in the mornings"
    [pref] = memory.preferences(ctx.conn)
    assert pref["source"] == "stated"
    tool.undo(ctx, result.undo)
    assert memory.preferences(ctx.conn) == []
    tool.undo(ctx, result.undo)  # already gone: nothing to do
    again = run(ctx, "remember_preference", text="Use metric units")
    memory.update_preference(ctx.conn, again.data["preference_id"], "Use SI units")
    with pytest.raises(ToolError):
        tool.undo(ctx, again.undo)
    assert [item["text"] for item in memory.preferences(ctx.conn)] == ["Use SI units"]


def test_search_memory_keeps_note_text_from_the_model_unless_shared(ctx):
    _filler(ctx)
    page = run(ctx, "create_page", title="Mitosis")
    run(ctx, "append_block", text="Prophase condenses chromosomes", page_id=page.data["page_id"])
    result = run(ctx, "search_memory", query="prophase")
    hits = result.data["results"]
    assert {hit["source"] for hit in hits} == {"page", "block"} and result.data["note_text_withheld"] is True
    assert all(hit["title"] == "Mitosis" and hit["snippet"] is None for hit in hits)
    assert result.summary == "Found 2 memory matches for prophase"
    ctx.conn.execute("UPDATE note_preferences SET allow_ai=1")
    assert "Prophase" in run(ctx, "search_memory", query="prophase").data["results"][0]["snippet"]


def test_the_remember_phrase_is_a_deterministic_command(ctx):
    plan = commands.parse("Remember that my exams are in May.", ctx.conn, REGISTRY)
    assert plan.calls == [("remember_preference", {"text": "My exams are in May"})]
    assert commands.parse("remember I like dark mode", ctx.conn, REGISTRY).calls == [
        ("remember_preference", {"text": "I like dark mode"})]
