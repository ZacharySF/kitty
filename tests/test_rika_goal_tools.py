"""Rika's goal tools and phrases: list_goals, create_goal, link_to_goal, update_measure
and morning_brief, each change undoable only while nothing changed since."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from kitty.core import goals, notes
from kitty.core.rika import commands, tools
from kitty.core.rika.tools import Context, ToolError, validate
from kitty.core.rika.workspace_tools import view_for

REGISTRY = tools.registry()


@pytest.fixture
def ctx(workspace):
    app = SimpleNamespace(settings=workspace.settings, modules=("workspace",), mutation_lock=threading.Lock())
    return Context(app, workspace.conn)


def run(ctx, name, /, **args):
    tool = REGISTRY[name]
    validate(tool.parameters, args)
    return tool.run(ctx, args)


def test_goal_tools_declare_honest_effects():
    effects = {name: (REGISTRY[name].effect, REGISTRY[name].reversible)
               for name in ("list_goals", "create_goal", "link_to_goal", "update_measure", "morning_brief")}
    assert effects == {"list_goals": ("read", False), "create_goal": ("change", True), "link_to_goal": ("change", True),
                       "update_measure": ("change", True), "morning_brief": ("read", False)}
    validate(REGISTRY["create_goal"].parameters, {"title": "Ship it", "measures": [
        {"label": "Pages", "target": 3, "auto": "tasks_done"}]})
    for args in ({"title": "x", "measures": [{"label": "Pages"}]}, {"title": "x", "measures": [{"label": "P", "target": 0}]},
                 {"title": "x", "measures": [{"label": "P", "target": 1, "auto": "laps"}]}):
        with pytest.raises(ToolError):
            validate(REGISTRY["create_goal"].parameters, args)
    validate(REGISTRY["open_view"].parameters, {"view": "goals"})


def test_create_goal_and_its_undo_only_while_unchanged(ctx):
    made = run(ctx, "create_goal", title="Run a 10k", why="Health",
               measures=[{"label": "Runs", "target": 12, "current": 1}])
    goal = goals.get(ctx.conn, made.data["goal_id"])
    assert made.summary == "Added the goal Run a 10k" and goal["measures"][0]["current"] == 1
    REGISTRY["create_goal"].undo(ctx, made.undo)
    assert goals.listing(ctx.conn) == []
    REGISTRY["create_goal"].undo(ctx, made.undo)  # already gone: nothing to do
    kept = run(ctx, "create_goal", title="Read 12 books")
    goals.update(ctx.conn, kept.data["goal_id"], {"revision": 1, "why": "Edited since"})
    with pytest.raises(ToolError, match="kept"):
        REGISTRY["create_goal"].undo(ctx, kept.undo)
    assert [goal["title"] for goal in goals.listing(ctx.conn)] == ["Read 12 books"]
    with pytest.raises(ToolError):
        run(ctx, "create_goal", title="Bad", target_date="tomorrow")


def test_link_to_goal_by_title_and_undo_unlinks(ctx):
    goal = goals.create(ctx.conn, {"title": "Interview prep"})
    page = notes.create_page(ctx.conn, notes.create_notebook(ctx.conn, "Synthetic")["id"], "STAR answers")
    linked = run(ctx, "link_to_goal", goal="interview prep", kind="note", ref=str(page["id"]))
    assert linked.summary == "Linked STAR answers to Interview prep" and linked.data["links_count"] == 1
    again = run(ctx, "link_to_goal", goal_id=goal["id"], kind="note", ref=str(page["id"]))
    assert again.undo is None and "already linked" in again.summary
    REGISTRY["link_to_goal"].undo(ctx, linked.undo)
    assert goals.get(ctx.conn, goal["id"])["links"] == []
    for args in ({"goal": "Nope", "kind": "note", "ref": "1"}, {"goal_id": goal["id"], "kind": "note", "ref": "999"},
                 {"goal_id": goal["id"], "kind": "job", "ref": "1"}, {"kind": "note", "ref": "1"}):
        with pytest.raises(ToolError):
            run(ctx, "link_to_goal", **args)


def test_update_measure_and_its_conditional_undo(ctx):
    goal = goals.create(ctx.conn, {"title": "Deck", "measures": [
        {"id": "pct", "label": "Deck done", "target": 100, "unit": "%", "current": 35},
        {"id": "apps", "label": "Applications", "target": 10, "auto": "applications"}]})
    set_ = run(ctx, "update_measure", goal="Deck", measure="deck done", current=60)
    assert set_.summary == "Set Deck done to 60% of 100 for Deck"
    REGISTRY["update_measure"].undo(ctx, set_.undo)
    assert goals.get(ctx.conn, goal["id"])["measures"][0]["current"] == 35
    later = run(ctx, "update_measure", goal_id=goal["id"], measure="pct", current=70)
    goals.set_measure(ctx.conn, goal["id"], "pct", 80)
    with pytest.raises(ToolError, match="kept"):
        REGISTRY["update_measure"].undo(ctx, later.undo)
    for args in ({"measure": "apps", "current": 3}, {"measure": "nothing", "current": 3}):
        with pytest.raises(ToolError):
            run(ctx, "update_measure", goal_id=goal["id"], **args)


def test_list_goals_and_the_morning_brief(ctx):
    assert run(ctx, "list_goals").summary == "You have no goals yet"
    goals.create(ctx.conn, {"title": "Deck", "measures": [{"label": "Done", "target": 4, "current": 1}]})
    listed = run(ctx, "list_goals")
    assert listed.summary == "You have 1 active goal: Deck (25%)"
    assert listed.data["goals"][0]["measures"][0]["label"] == "Done"
    assert run(ctx, "morning_brief").summary.startswith("There's no morning brief yet today")
    message = ctx.conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,model,created_at)"
                               " VALUES('m1','c1','rika','Good morning. Nothing new for your goals since the last brief.',"
                               "'brief','2026-11-03T06:00:00+00:00')")
    assert message
    from kitty.core.rika import brief

    ctx.conn.execute("INSERT INTO rika_briefs(day,created_at,message_id,summary_json) VALUES(?,?,?,?)",
                     (brief.day_of(ctx.conn), "2026-11-03T06:00:00+00:00", "m1", '{"mail":{"drafted":1,"scheduled":0,"sent":0},"notes":[]}'))
    today = run(ctx, "morning_brief")
    assert today.summary == "Good morning. Nothing new for your goals since the last brief."
    assert today.data["mail"] == {"drafted": 1, "scheduled": 0, "sent": 0} and today.data["items"] == []


def test_goal_phrases_run_with_no_model(workspace):
    available = tools.registry()
    for text, call in (("New goal Land a summer internship", ("create_goal", {"title": "Land a summer internship"})),
                       ("create a goal: Finish the deck.", ("create_goal", {"title": "Finish the deck"})),
                       ("add a goal Read more", ("create_goal", {"title": "Read more"})),
                       ("morning brief", ("morning_brief", {})), ("Show me my morning brief", ("morning_brief", {})),
                       ("What's new for my goals?", ("morning_brief", {})),
                       ("open goals", ("open_view", {"view": "goals"})), ("show my goal", ("open_view", {"view": "goals"}))):
        assert commands.parse(text, workspace.conn, available).calls == [call], text
    assert view_for("goal") == "goals"
