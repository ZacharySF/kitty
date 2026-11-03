"""Rika's tools: closed JSON schemas, the core change each makes, and its undo."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from kitty.core import note_blocks, notes, schema_maps
from kitty.core.rika import tools
from kitty.core.rika.tools import Context, ToolError, validate

REGISTRY = tools.registry()


@pytest.fixture
def ctx(workspace):
    app = SimpleNamespace(settings=workspace.settings, modules=("workspace", "terminal"), mutation_lock=threading.Lock())
    return Context(app, workspace.conn)


def run(ctx, name, /, **args):
    tool = REGISTRY[name]
    validate(tool.parameters, args)
    return tool.run(ctx, args)


def undo(ctx, name, result):
    REGISTRY[name].undo(ctx, result.undo)


def test_every_tool_declares_a_closed_documented_schema_and_an_honest_effect():
    assert {"search", "create_page", "append_block", "edit_block", "find_blocks", "add_canvas_node", "move_canvas_node",
            "connect_canvas_nodes", "add_map_topic", "connect_map_topics", "rename_map_topic", "add_card", "create_event",
            "create_task", "create_subject", "create_study_task", "open_view", "delete_page", "set_modules",
            "start_worker", "run_command"} <= set(REGISTRY)
    for tool in REGISTRY.values():
        schema = tool.parameters
        assert schema["type"] == "object" and schema["additionalProperties"] is False, tool.name
        assert set(schema["required"]) <= set(schema["properties"]), tool.name
        assert all(item.get("description") or item["type"] == "array" for item in schema["properties"].values()), tool.name
        assert tool.spec()["function"]["name"] == tool.name and tool.description
        assert tool.effect in tools.EFFECTS
        if tool.reversible:
            assert tool.effect == "change", tool.name
        if tool.effect == "outward":
            assert tool.describe is not None and not tool.reversible, tool.name
        if schema["required"]:
            with pytest.raises(ToolError):
                validate(schema, {})
    # The spec's approval list (Q9): deleting without Trash, settings and modules,
    # starting a worker agent and shell commands all wait for a tap.
    assert {name for name, tool in REGISTRY.items() if tool.effect == "outward"} == {
        "delete_page", "set_modules", "start_worker", "run_command"}


@pytest.mark.parametrize("args", [
    {"front": 3, "back": "x"}, {"front": "a"}, {"front": "a", "back": "b", "colour": "red"},
    {"front": "   ", "back": "b"}, {"front": "a\x00", "back": "b"}, {"front": "a" * 10001, "back": "b"},
])
def test_validation_rejects_wrong_missing_unknown_and_oversized_arguments(args):
    with pytest.raises(ToolError):
        validate(REGISTRY["add_card"].parameters, args)


def test_validation_checks_enums_integers_numbers_and_arrays():
    validate(REGISTRY["open_view"].parameters, {"view": "calendar"})
    for schema, value in ((REGISTRY["open_view"].parameters, {"view": "nowhere"}),
                          (REGISTRY["read_page"].parameters, {"page_id": True}),
                          (REGISTRY["read_page"].parameters, {"page_id": 0}),
                          (REGISTRY["move_canvas_node"].parameters, {"node": "n", "x": float("nan"), "y": 1}),
                          (REGISTRY["set_modules"].parameters, {"enabled": ["workspace", "games"]})):
        with pytest.raises(ToolError):
            validate(schema, value)


def test_pages_and_blocks_apply_and_undo_without_overwriting_later_edits(ctx):
    page = run(ctx, "create_page", title="Lecture 7")
    page_id = page.data["page_id"]
    assert notes.page(ctx.conn, page_id)["title"] == "Lecture 7"
    added = run(ctx, "append_block", text="Mitochondria make ATP", page="lecture 7")
    edited = run(ctx, "edit_block", block_id=added.data["block_id"], text="Mitochondria make most ATP")
    assert [block["text"] for block in note_blocks.get_blocks(ctx.conn, page_id)["blocks"]] == ["Mitochondria make most ATP"]
    undo(ctx, "edit_block", edited)
    assert note_blocks.get_blocks(ctx.conn, page_id)["blocks"][0]["text"] == "Mitochondria make ATP"
    undo(ctx, "append_block", added)
    assert note_blocks.get_blocks(ctx.conn, page_id)["blocks"] == []
    # A page edited after Rika created it is kept.
    blocks = run(ctx, "append_block", text="Written later", page_id=page_id)
    with pytest.raises(ToolError):
        undo(ctx, "create_page", page)
    undo(ctx, "append_block", blocks)
    assert run(ctx, "find_pages", query="Lecture").data["pages"][0]["id"] == page_id


def test_canvas_nodes_add_move_connect_and_undo(ctx):
    first = run(ctx, "add_canvas_node", text="Photosynthesis")
    assert first.undo["created_page"] is True
    page_id = first.data["page_id"]
    second = run(ctx, "add_canvas_node", text="Glucose", page_id=page_id)
    moved = run(ctx, "move_canvas_node", page_id=page_id, node="Glucose", x=600, y=300)
    linked = run(ctx, "connect_canvas_nodes", page_id=page_id, **{"from": "Photosynthesis", "to": second.data["node_id"]})
    elements = notes.page(ctx.conn, page_id)["document"]["elements"]
    assert [item["kind"] for item in elements] == ["sticky", "sticky", "connector"]
    assert (elements[1]["x"], elements[1]["y"]) == (600, 300)
    assert elements[0]["fill"] == "#fff2a8" and elements[0]["text"] == "Photosynthesis"
    with pytest.raises(ToolError):
        undo(ctx, "add_canvas_node", second)  # still connected
    undo(ctx, "connect_canvas_nodes", linked)
    undo(ctx, "move_canvas_node", moved)
    elements = notes.page(ctx.conn, page_id)["document"]["elements"]
    assert len(elements) == 2 and (elements[1]["x"], elements[1]["y"]) != (600, 300)
    undo(ctx, "add_canvas_node", second)
    assert [item["text"] for item in notes.page(ctx.conn, page_id)["document"]["elements"]] == ["Photosynthesis"]
    # The page Rika created for the first node is kept once something else changed on it.
    with pytest.raises(ToolError):
        run(ctx, "connect_canvas_nodes", page_id=page_id, **{"from": "Photosynthesis", "to": "Photosynthesis"})
    undo(ctx, "add_canvas_node", first)
    assert notes.page(ctx.conn, page_id)["document"]["elements"] == []


def test_a_node_on_a_canvas_rika_created_is_undone_with_its_page(ctx):
    first = run(ctx, "add_canvas_node", text="Alone")
    undo(ctx, "add_canvas_node", first)
    with pytest.raises(notes.NotesError):
        notes.page(ctx.conn, first.data["page_id"])


def test_mind_map_topics_reuse_the_maps_own_revision_checked_undo(ctx):
    root = run(ctx, "add_map_topic", label="Biology")
    assert root.undo["created_map"] is True
    map_id = root.data["map_id"]
    child = run(ctx, "add_map_topic", label="Cells", parent="biology")
    renamed = run(ctx, "rename_map_topic", topic="Cells", label="Cell biology")
    other = run(ctx, "add_map_topic", label="Genetics")
    linked = run(ctx, "connect_map_topics", map_id=map_id, **{"from": "Cell biology", "to": "Genetics"}, label="shapes")
    graph = schema_maps.load(ctx.conn, map_id)["graph"]
    assert [node["label"] for node in graph["nodes"]] == ["Biology", "Cell biology", "Genetics"]
    undo(ctx, "connect_map_topics", linked)
    assert len(schema_maps.load(ctx.conn, map_id)["graph"]["edges"]) == 1
    # Only the latest change is undoable, as in the map editor.
    for result, name in ((renamed, "rename_map_topic"), (child, "add_map_topic"), (other, "add_map_topic")):
        with pytest.raises(ToolError):
            undo(ctx, name, result)
    undo(ctx, "add_map_topic", root) if False else None
    assert run(ctx, "find_maps", map_id=map_id).data["topics"][1]["label"] == "Cell biology"


def test_cards_are_added_to_a_deck_and_undo_deletes_only_an_unreviewed_card(ctx):
    card = run(ctx, "add_card", front="Mitosis", back="Cell division", deck="Biology")
    row = ctx.conn.execute("SELECT n.fields,d.name FROM flash_notes n JOIN flash_decks d ON d.id=n.deck_id").fetchone()
    assert json.loads(row["fields"]) == {"Front": "Mitosis", "Back": "Cell division"} and row["name"] == "Biology"
    undo(ctx, "add_card", card)
    assert ctx.conn.execute("SELECT count(*) FROM flash_notes").fetchone()[0] == 0
    reviewed = run(ctx, "add_card", front="Meiosis", back="Gametes")
    ctx.conn.execute("UPDATE flash_cards SET reviews=1")
    with pytest.raises(ToolError):
        undo(ctx, "add_card", reviewed)


def test_calendar_events_and_tasks_undo_through_calendars_own_history(ctx):
    event = run(ctx, "create_event", title="Study group", start="2026-11-03T16:00")
    assert event.undo == {"undo_id": ctx.conn.execute("SELECT max(id) FROM calendar_history").fetchone()[0]}
    row = ctx.conn.execute("SELECT start,end,deleted FROM calendar_events").fetchone()
    assert row["start"].startswith("2026-11-03T16:00") and row["end"].startswith("2026-11-03T17:00")
    undo(ctx, "create_event", event)
    assert ctx.conn.execute("SELECT deleted FROM calendar_events").fetchone()[0] == 1
    task = run(ctx, "create_task", title="Read chapter 3", due="2026-11-04")
    undo(ctx, "create_task", task)
    assert ctx.conn.execute("SELECT deleted FROM calendar_tasks").fetchone()[0] == 1
    with pytest.raises(ToolError):
        undo(ctx, "create_task", task)
    whole_day = run(ctx, "create_event", title="Exam", start="2026-12-01", all_day=True)
    assert whole_day.data["start"] == "2026-12-01"


def test_study_subjects_and_tasks_and_their_undo(ctx):
    subject = run(ctx, "create_subject", name="Physics")
    task = run(ctx, "create_study_task", title="Problem set 4", subject="physics", due_date="2026-11-05")
    assert ctx.conn.execute("SELECT subject_id FROM study_tasks").fetchone()[0] == subject.data["subject_id"]
    with pytest.raises(ToolError):
        run(ctx, "create_study_task", title="x", subject="Latin")
    undo(ctx, "create_study_task", task)
    undo(ctx, "create_subject", subject)
    assert ctx.conn.execute("SELECT count(*) FROM study_tasks").fetchone()[0] == 0
    assert ctx.conn.execute("SELECT count(*) FROM study_subjects").fetchone()[0] == 0


def test_open_view_is_a_client_event_for_active_modules_only(ctx):
    result = run(ctx, "open_view", view="calendar")
    assert result.client == {"type": "navigate", "view": "calendar"} and not REGISTRY["open_view"].reversible
    with pytest.raises(ToolError):
        run(ctx, "open_view", view="jobs")  # applyai is off


def test_search_spans_the_workspace_and_keeps_note_text_private_unless_allowed(ctx):
    page = run(ctx, "create_page", title="Cell notes")
    run(ctx, "append_block", text="ribosome function", page_id=page.data["page_id"])
    run(ctx, "add_card", front="What does a ribosome do?", back="Makes proteins")
    run(ctx, "create_event", title="Ribosome lab", start="2026-11-06T10:00")
    run(ctx, "add_map_topic", label="Ribosome")
    result = run(ctx, "search", query="ribosome")
    found = result.data
    assert len(found["blocks"]) == len(found["cards"]) == len(found["events"]) == len(found["maps"]) == 1
    assert result.summary == ("Found 5 matches for ribosome: Cell notes (page), Ribosome lab (event), "
                              "What does a ribosome do? (card), Mind map (mind map) and 1 more")
    assert run(ctx, "search", query="zebra").summary == "Nothing matches zebra"
    assert found["blocks"][0]["text"] is None
    ctx.conn.execute("UPDATE note_preferences SET allow_ai=1")
    assert run(ctx, "search", query="ribosome").data["blocks"][0]["text"] == "ribosome function"
