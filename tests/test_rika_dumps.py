"""Brain-dump plans: parsing a model's plan into validated items grouped by area, the
user's ticks and choices, the dump note's links, and the demo's scripted plan."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kitty.core import note_blocks
from kitty.core.rika import dumps, tools as toolset


@pytest.fixture
def tools():
    app = SimpleNamespace(modules=("workspace", "terminal"))
    return toolset.available(app)


def _item(area, title, *calls, **extra):
    return {"area": area, "title": title, "calls": [{"tool": tool, "args": args} for tool, args in calls], **extra}


def test_a_plan_keeps_valid_items_grouped_by_area_and_drops_the_rest(tools):
    text = "Here is the plan:\n" + json.dumps({"items": [
        _item("agents", "Fix login tests", ("start_worker", {"kind": "omp", "task": "Fix the login tests"})),
        _item("tasks", "Ask Priya", ("create_task", {"title": "Ask Priya"})),
        _item("notes", "Idea", ("create_page", {"title": "Idea"}), ("append_block", {"page": "Idea", "text": "Cards for code"})),
        _item("tasks", "Wrong area tool", ("add_card", {"front": "a", "back": "b"})),
        _item("cards", "Bad args", ("add_card", {"front": "a"})),
        _item("calendar", "Group", question={"text": "Which Tuesday?", "choices": [
            {"label": "This", "calls": [{"tool": "create_event", "args": {"title": "Group", "start": "2026-11-03T19:00"}}]},
            {"label": "Next", "calls": [{"tool": "create_event", "args": {"title": "Group", "start": "2026-11-10T19:00"}}]}]}),
        _item("calendar", "Second question", question={"text": "?", "choices": [
            {"label": "x", "calls": [{"tool": "create_event", "args": {"title": "x", "start": "2026-11-03"}}]},
            {"label": "y", "calls": [{"tool": "create_event", "args": {"title": "y", "start": "2026-11-04"}}]}]}),
    ]}) + "\nShall I go ahead?"
    items, dropped = dumps.parse(text, tools)
    assert [(item["area"], item["title"]) for item in items] == [
        ("notes", "Idea"), ("tasks", "Ask Priya"), ("calendar", "Group"), ("agents", "Fix login tests")]
    assert dropped == 3  # a tool outside its area, bad arguments, and a second question
    assert items[2]["question"]["text"] == "Which Tuesday?" and items[2]["choice"] == 0 and items[2]["calls"] == []
    with pytest.raises(dumps.PlanError):
        dumps.parse("I could not make a plan", tools)


def test_choosing_ticks_items_and_answers_the_question(tools):
    items, _ = dumps.parse(json.dumps({"items": [
        _item("tasks", "A", ("create_task", {"title": "A"})),
        _item("calendar", "G", question={"text": "Which?", "choices": [
            {"label": "1", "calls": [{"tool": "create_event", "args": {"title": "G", "start": "2026-11-03"}}]},
            {"label": "2", "calls": [{"tool": "create_event", "args": {"title": "G", "start": "2026-11-10"}}]}]}),
    ]}), tools)
    chosen = dumps.choose(items, [items[1]["id"]], {items[1]["id"]: 1})
    assert [item["picked"] for item in chosen] == [False, True]
    assert dumps.calls_of(chosen[1]) == [{"tool": "create_event", "args": {"title": "G", "start": "2026-11-10"}}]
    for picked, choices in (([], None), (["nope"], None), ([items[0]["id"]], {items[1]["id"]: 5}),
                            ([items[0]["id"]], {"nope": 0})):
        with pytest.raises(dumps.PlanError):
            dumps.choose(items, picked, choices)


def test_links_point_at_what_each_call_made():
    def done(summary, **data):
        return {"status": "done", "summary": summary, "data": data}

    assert dumps.link({"tool": "create_page", "args": {"title": "CS 220"}}, done("Created")) == "[[CS 220]]"
    assert dumps.link({"tool": "append_block", "args": {"page": "CS 220", "text": "x"}}, done("Added")) == "[[CS 220]]"
    assert dumps.link({"tool": "create_event", "args": {"title": "Midterm [orgo]"}},
                      done("Added", start="2026-11-06T10:00:00-06:00")) == "[Midterm (orgo) (Fri 6 Nov, 10:00)](#calendar)"
    assert dumps.link({"tool": "create_task", "args": {"title": "Ask Priya"}}, done("Added")) == "[Ask Priya](#calendar)"
    assert dumps.link({"tool": "add_card", "args": {"front": "SN1?", "back": "b"}}, done("Added")) == "[SN1?](#flashcards)"
    assert dumps.link({"tool": "start_worker", "args": {"kind": "omp", "task": "Fix tests"}},
                      done("Started", agent={"name": "omp"})) == "[omp: Fix tests](#herdr)"


def test_the_transcript_the_model_reads_is_timestamped():
    segments = [{"start": 0.0, "end": 4.0, "text": "Okay."}, {"start": 61.5, "end": 70.0, "text": "Midterm Friday."}]
    assert dumps.transcript_text(segments, "ignored") == "[0:00] Okay.\n[1:01] Midterm Friday."
    assert dumps.transcript_text([], "Whole text") == "Whole text"
    assert dumps.clock(3725) == "1:02:05"


def test_the_demo_plan_covers_every_area_and_the_dump_note_keeps_the_transcript(workspace, tools):
    items = dumps.demo_plan(workspace.conn, dumps.DEMO_DUMP, tools)
    assert [item["area"] for item in items] == [
        "notes", "tasks", "tasks", "calendar", "calendar", "calendar", "cards", "jobs", "agents"]
    assert sum(item["question"] is not None for item in items) == 1
    shown = dumps.public_items(items)
    assert all("calls" not in item for item in shown) and [item["outward"] for item in shown].count(True) == 1
    page, block = dumps.create_note(workspace.conn, transcript=dumps.DEMO_DUMP,
                                    segments=[{"start": 0.0, "end": 3.0, "text": dumps.DEMO_LINES[0]}], duration=3.0)
    texts = [item["text"] for item in note_blocks.get_blocks(workspace.conn, page["id"])["blocks"]]
    assert texts == ["Created from this dump: the plan is waiting in Rika history", "Transcript · 0:03 of talk",
                     "0:00 " + dumps.DEMO_LINES[0]]
    made = [dict(items[0], result={"status": "done", "links": ["[[CS 220 · Final project idea]]"], "summary": ""}),
            dict(items[1], result={"status": "failed", "links": [], "summary": "no"})]
    dumps.fill_note(workspace.conn, page["id"], block, made)
    blocks = note_blocks.get_blocks(workspace.conn, page["id"])["blocks"]
    head = next(item for item in blocks if item["id"] == block)
    group = next(item for item in blocks if item["parent_id"] == head["id"])
    assert (head["text"], group["text"]) == ("Created from this dump", "Notes")
    assert [item["text"] for item in blocks if item["parent_id"] == group["id"]] == ["[[CS 220 · Final project idea]]"]
