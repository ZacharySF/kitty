"""Rika follow-ups: asking before a dated item whose date is not clear, and check-ins on tasks whose day passed."""
from __future__ import annotations

import dataclasses
import json
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from kitty.core import calendar, study
from kitty.core.rika import dumps, followups, slots, tools
from kitty.core.rika.loop import Hub, Rika, RikaError
from kitty.core.rika.tools import Context, ToolError, validate

REGISTRY = tools.registry()
ZONE = ZoneInfo("America/New_York")
#: Saturday 14 November 2026, 10:00 in New York.
NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


def check(tool, **args):
    return slots.check(tool, args, now=NOW, zone=ZONE)


def labels(ask):
    return [choice["label"] for choice in ask["choices"]]


# Confirm before act ---------------------------------------------------------------

@pytest.mark.parametrize(("tool", "args"), [
    ("create_task", {"title": "Lab", "due": "2026-11-20", "said": "friday"}),
    ("create_task", {"title": "Lab", "due": "2026-11-20", "said": "friday at 4pm"}),  # a task may keep the day only
    ("create_task", {"title": "Lab", "due": "2026-11-20T16:00", "said": "friday at 4pm"}),
    ("create_task", {"title": "Lab"}),  # undated
    ("create_task", {"title": "Lab", "due": "2026-11-20"}),  # no words to read, and the date is ahead
    ("create_task", {"title": "Lab", "due": "2026-11-20", "said": "sometime soon"}),
    ("create_task", {"title": "Lab", "due": "not a date", "said": "friday"}),  # the tool refuses it itself
    ("create_study_task", {"title": "Lab", "due_date": "2026-11-15", "said": "tomorrow at 4"}),  # due on a day
    ("create_event", {"title": "Group", "start": "2026-11-20T16:00", "said": "friday at 4pm"}),
    ("create_event", {"title": "Group", "start": "2026-11-20T21:00:00+00:00", "said": "friday at 4pm"}),  # 16:00 in New York
    ("create_event", {"title": "Group", "start": "2026-11-20T16:00", "said": "friday"}),  # the time was said elsewhere
    ("create_event", {"title": "Group", "start": "2026-11-20", "all_day": True, "said": "all day friday"}),
    ("create_event", {"title": "Group", "start": "2026-11-14", "all_day": True}),
    ("create_page", {"title": "Friday"}),
])
def test_a_clear_date_is_not_asked_about(tool, args):
    assert slots.check(tool, args, now=NOW, zone=ZONE) is None


def test_words_with_two_readings_ask_with_each_as_a_whole_call():
    ask = check("create_task", title="Lab report", due="2026-11-27", said="next friday", priority="high")
    assert labels(ask) == ["Fri 20 Nov", "Fri 27 Nov"]
    assert ask["text"] == "Is “Lab report” on Fri 20 Nov or Fri 27 Nov?"
    assert ask["choice"] == 1  # what the model worked out is preselected
    assert [choice["args"] for choice in ask["choices"]] == [
        {"title": "Lab report", "priority": "high", "due": "2026-11-20"},
        {"title": "Lab report", "priority": "high", "due": "2026-11-27"}]


def test_a_date_that_disagrees_with_the_words_is_offered_beside_their_reading():
    ask = check("create_task", title="Lab", due="2026-11-21", said="friday")
    assert labels(ask) == ["Fri 20 Nov", "Sat 21 Nov"] and ask["choice"] == 0
    missing = check("create_task", title="Lab", said="friday")
    assert labels(missing) == ["Fri 20 Nov"] and missing["text"] == "So you want “Lab” added on Fri 20 Nov?"
    timed = check("create_event", title="Group", start="2026-11-20", all_day=True, said="friday at 4pm")
    assert labels(timed) == ["Fri 20 Nov, 16:00", "Fri 20 Nov"]
    assert timed["choices"][0]["args"] == {"title": "Group", "start": "2026-11-20T16:00"}


def test_a_date_that_has_passed_asks_with_today_and_tomorrow():
    ask = check("create_task", title="Lab", due="2026-11-10")
    assert labels(ask) == ["Tue 10 Nov", "Sat 14 Nov", "Sun 15 Nov"]
    assert ask["text"] == "Tue 10 Nov has passed. When should “Lab” go?"
    said = check("create_task", title="Lab", due="2026-11-13", said="yesterday")
    assert labels(said) == ["Fri 13 Nov", "Sat 14 Nov", "Sun 15 Nov"]
    # 08:00 today has gone too, so only tomorrow's is offered beside it.
    event = check("create_event", title="Run", start="2026-11-14T08:00", said="today at 8am")
    assert labels(event) == ["Sat 14 Nov, 08:00", "Sun 15 Nov, 08:00"]


def test_an_event_with_a_day_but_no_time_asks_for_the_part_of_the_day():
    ask = check("create_event", title="Study group", start="2026-11-20", all_day=True, said="friday")
    assert labels(ask) == ["Morning", "Afternoon", "All day"]
    assert ask["text"] == "“Study group” on Fri 20 Nov: what time?"
    assert ask["choice"] == 2  # the all-day event the caller had in mind
    assert [choice["args"] for choice in ask["choices"]] == [
        {"title": "Study group", "start": "2026-11-20T09:00"},
        {"title": "Study group", "start": "2026-11-20T14:00"},
        {"title": "Study group", "start": "2026-11-20", "all_day": True}]


def test_an_event_moved_to_another_reading_keeps_its_length():
    ask = check("create_event", title="Lab", start="2026-11-20T15:00", end="2026-11-20T17:00", said="next friday at 3pm")
    assert [choice["args"] for choice in ask["choices"]] == [
        {"title": "Lab", "start": "2026-11-20T15:00", "end": "2026-11-20T17:00"},
        {"title": "Lab", "start": "2026-11-27T15:00", "end": "2026-11-27T17:00"}]


def _items(*asks):
    return slots.items(list(asks), REGISTRY)


def test_another_date_becomes_a_further_validated_choice():
    items = _items(check("create_task", title="Lab", due="2026-11-27", said="next friday"),
                   check("create_study_task", title="Quiz", due_date="2026-11-27", said="next friday"))
    assert [entry["id"] for entry in items] == ["i1", "i2"] and all(entry["question"] for entry in items)
    today = NOW.date()
    chosen = dumps.choose(items, ["i1", "i2"], {"i1": {"date": "2026-12-01", "time": "10:30"}, "i2": 0},
                          tools=REGISTRY, today=today)
    assert chosen[0]["question"]["choices"][2]["label"] == "Tue 1 Dec, 10:30"
    assert dumps.calls_of(chosen[0]) == [{"tool": "create_task", "args": {"title": "Lab", "due": "2026-12-01T10:30"}}]
    assert dumps.calls_of(chosen[1]) == [{"tool": "create_study_task", "args": {"title": "Quiz", "due_date": "2026-11-20"}}]
    whole_day = dumps.choose(items, ["i2"], {"i2": {"date": "2026-12-01"}}, tools=REGISTRY, today=today)
    assert dumps.calls_of(whole_day[1]) == [{"tool": "create_study_task", "args": {"title": "Quiz", "due_date": "2026-12-01"}}]
    for bad in ({"date": "1 Dec"}, {"date": "2026-02-30"}, {"date": "2026-12-01", "time": "25:00"},
                {"date": "2026-12-01", "time": "4pm"}, {"time": "10:00"}, {"date": "2026-12-01", "zone": "UTC"}):
        with pytest.raises(dumps.PlanError):
            dumps.choose(items, ["i1"], {"i1": bad}, tools=REGISTRY, today=today)
    with pytest.raises(dumps.PlanError):  # a study task is due on a day
        dumps.choose(items, ["i2"], {"i2": {"date": "2026-12-01", "time": "10:30"}}, tools=REGISTRY, today=today)
    public = dumps.public_items(items)
    assert [entry["date"] for entry in public] == [{"time": True}, {"time": False}]
    assert "calls" not in public[0] and public[0]["question"]["choices"] == ["Fri 20 Nov", "Fri 27 Nov"]


# The task tools a check-in answers with --------------------------------------------

@pytest.fixture
def ctx(workspace):
    app = SimpleNamespace(settings=workspace.settings, modules=("workspace",), mutation_lock=threading.Lock())
    return Context(app, workspace.conn)


def run(ctx, name, /, **args):
    tool = REGISTRY[name]
    validate(tool.parameters, args)
    return tool.run(ctx, args)


def task(conn, ident):
    return json.loads(conn.execute("SELECT data FROM calendar_tasks WHERE id=?", (ident,)).fetchone()["data"])


def zone_of(conn) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"])


def test_a_calendar_task_is_completed_moved_and_dropped_with_undo(ctx, workspace):
    conn = workspace.conn
    made = run(ctx, "create_task", title="Send the report", due="2026-11-13")
    ident = made.data["task_id"]
    done = run(ctx, "complete_task", task_id=ident)
    assert done.summary == "Marked Send the report done" and task(conn, ident)["status"] == "done"
    REGISTRY["complete_task"].undo(ctx, done.undo)
    assert task(conn, ident)["status"] == "todo"
    moved = run(ctx, "reschedule_task", task_id=ident, due="tomorrow")
    tomorrow = (datetime.now(zone_of(conn)).date() + timedelta(days=1)).isoformat()
    assert task(conn, ident)["due"] == tomorrow and moved.data == {"kind": "task", "task_id": ident, "due": tomorrow}
    REGISTRY["reschedule_task"].undo(ctx, moved.undo)
    assert task(conn, ident)["due"] == "2026-11-13"
    dropped = run(ctx, "drop_task", task_id=ident)
    assert task(conn, ident)["status"] == "cancelled"
    REGISTRY["drop_task"].undo(ctx, dropped.undo)
    assert task(conn, ident)["status"] == "todo"
    with pytest.raises(ToolError):
        run(ctx, "complete_task", task_id="missing")
    with pytest.raises(ToolError):
        run(ctx, "reschedule_task", task_id=ident, due="someday")


def test_a_study_task_is_completed_moved_and_dropped_with_undo(ctx, workspace):
    conn = workspace.conn
    made = run(ctx, "create_study_task", title="Problem set 4", due_date="2026-11-13")
    ident = str(made.data["task_id"])
    row = lambda: conn.execute("SELECT * FROM study_tasks WHERE id=?", (int(ident),)).fetchone()
    done = run(ctx, "complete_task", task_id=ident, kind="study_task")
    assert row()["done"] == 1
    REGISTRY["complete_task"].undo(ctx, done.undo)
    assert row()["done"] == 0
    moved = run(ctx, "reschedule_task", task_id=ident, kind="study_task", due="2026-11-20T09:00")
    assert row()["due_date"] == "2026-11-20"
    # A task edited since keeps the edit.
    study.save_entity(conn, "tasks", {"id": int(ident), "revision": row()["revision"], "title": "Problem set 4b"})
    with pytest.raises(ToolError):
        REGISTRY["reschedule_task"].undo(ctx, moved.undo)
    dropped = run(ctx, "drop_task", task_id=ident, kind="study_task")
    assert row() is None
    REGISTRY["drop_task"].undo(ctx, dropped.undo)
    again = conn.execute("SELECT title,due_date,done FROM study_tasks").fetchall()
    assert [tuple(item) for item in again] == [("Problem set 4b", "2026-11-20", 0)]


# Check-ins -------------------------------------------------------------------------

@pytest.fixture
def app(workspace):
    made = SimpleNamespace(settings=workspace.settings, modules=("workspace",), mutation_lock=threading.Lock(),
                           rika=SimpleNamespace(hub=Hub()))
    made.events = made.rika.hub.subscribe()
    return made


def followup(conn, ident):
    return conn.execute("SELECT * FROM rika_followups WHERE subject_id=? ORDER BY created_at DESC, rowid DESC", (ident,)).fetchone()


def test_a_check_in_is_booked_for_the_evening_of_the_due_day_or_the_due_time_if_later():
    assert followups.ask_at("2026-11-13", ZONE) == datetime(2026, 11, 13, 18, 0, tzinfo=ZONE)
    assert followups.ask_at("2026-11-13T09:00:00-05:00", ZONE) == datetime(2026, 11, 13, 18, 0, tzinfo=ZONE)
    assert followups.ask_at("2026-11-13T21:30", ZONE) == datetime(2026, 11, 13, 21, 30, tzinfo=ZONE)
    assert followups.ask_at("2026-11-14T02:30:00+00:00", ZONE) == datetime(2026, 11, 13, 21, 30, tzinfo=ZONE)


def test_a_due_check_in_asks_once_in_the_conversation(ctx, app, workspace):
    conn = workspace.conn
    zone = zone_of(conn)
    ident = run(ctx, "create_task", title="Send the report", due="2026-11-13").data["task_id"]
    undated = run(ctx, "create_task", title="Someday").data["task_id"]
    assert followups.schedule(conn, "task", ident) == followups.ask_at("2026-11-13", zone).astimezone(timezone.utc).isoformat(
        timespec="microseconds")
    assert followups.schedule(conn, "task", undated) is None and followup(conn, undated) is None
    due = followups.ask_at("2026-11-13", zone)
    assert followups.sweep(app, conn, due - timedelta(minutes=1)) == []
    [plan] = followups.sweep(app, conn, due + timedelta(minutes=1))
    assert plan["plan_kind"] == "checkin" and plan["status"] == "pending"
    [item] = plan["items"]
    assert item["question"] == {"text": "Did you finish “Send the report”?", "choices": ["Done", "Tomorrow", "Drop"]}
    assert item["detail"] == "Due Fri 13 Nov" and item["date"] == {"time": False}
    message, event = app.events.get_nowait(), app.events.get_nowait()
    assert message["message"]["text"] == "Did you finish “Send the report”?" and message["message"]["role"] == "rika"
    assert event == {"type": "plan", "plan": plan}
    row = followup(conn, ident)
    assert (row["status"], row["plan_id"]) == ("asked", plan["id"])
    assert followups.sweep(app, conn, due + timedelta(days=3)) == []  # asked once


def test_a_check_in_follows_the_task_as_it_is_now(ctx, app, workspace):
    conn = workspace.conn
    zone = zone_of(conn)
    asked_at = followups.ask_at("2026-11-13", zone) + timedelta(minutes=1)
    done, moved, gone = (run(ctx, "create_task", title=title, due="2026-11-13").data["task_id"]
                         for title in ("Done", "Moved", "Gone"))
    for ident in (done, moved, gone):
        followups.schedule(conn, "task", ident)
    run(ctx, "complete_task", task_id=done)
    revision = conn.execute("SELECT revision FROM calendar_tasks WHERE id=?", (moved,)).fetchone()["revision"]
    calendar.save_task(conn, {"id": moved, "revision": revision, "due": "2026-11-16"})
    revision = conn.execute("SELECT revision FROM calendar_tasks WHERE id=?", (gone,)).fetchone()["revision"]
    calendar.save_task(conn, {"id": gone, "revision": revision, "delete": True})
    assert followups.sweep(app, conn, asked_at) == []
    assert followup(conn, done)["status"] == "expired" and followup(conn, gone)["status"] == "expired"
    later = followup(conn, moved)
    assert later["status"] == "scheduled"
    assert later["ask_at"] == followups.ask_at("2026-11-16", zone).astimezone(timezone.utc).isoformat(timespec="microseconds")
    [plan] = followups.sweep(app, conn, followups.ask_at("2026-11-16", zone) + timedelta(minutes=1))
    assert plan["items"][0]["title"] == "Moved"


def test_a_sweep_asks_a_few_at_a_time(ctx, app, workspace):
    conn = workspace.conn
    for index in range(followups.MAX_ASKED + 2):
        made = run(ctx, "create_study_task", title=f"Set {index}", due_date="2026-11-13")
        followups.schedule(conn, "study_task", str(made.data["task_id"]))
    at = followups.ask_at("2026-11-13", zone_of(conn)) + timedelta(hours=1)
    assert len(followups.sweep(app, conn, at)) == followups.MAX_ASKED
    assert len(followups.sweep(app, conn, at)) == 2
    assert followups.sweep(app, conn, at) == []


# Through the loop ------------------------------------------------------------------

@pytest.fixture
def build(workspace, monkeypatch):
    import httpx

    def refuse(self, request):
        raise AssertionError(f"unexpected network request to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    settings = dataclasses.replace(workspace.settings, demo=True)
    made = SimpleNamespace(settings=settings, modules=("workspace",), mutation_lock=threading.Lock(),
                           streams_closed=threading.Event())
    made.rika = Rika(made)
    yield made, made.rika.hub.subscribe()
    made.rika.close()


def until(events: queue.Queue, timeout=10.0) -> list[dict]:
    seen, deadline = [], time.monotonic() + timeout
    while True:
        event = events.get(timeout=max(0.01, deadline - time.monotonic()))
        seen.append(event)
        if event["type"] == "message" and event["message"]["role"] == "rika" and event["message"]["status"] != "pending":
            return seen


def of(events, kind):
    return [event[kind] for event in events if event["type"] == kind]


def tasks(conn):
    return {item["title"]: item for item in calendar.tasks(conn)}


def test_a_clear_date_is_added_at_once_and_books_its_check_in(build, workspace):
    app, events = build
    conn = workspace.conn
    app.rika.submit(conn, "add a task Read chapter 3 in 3 days")
    seen = until(events)
    assert not of(seen, "plan")
    [action] = of(seen, "action")
    assert action["tool"] == "create_task" and action["reversible"]
    due = (datetime.now(zone_of(conn)).date() + timedelta(days=3)).isoformat()
    made = tasks(conn)["Read chapter 3"]
    assert made["due"] == due
    assert followup(conn, made["id"])["status"] == "scheduled"


def test_an_unclear_date_asks_first_and_the_choice_adds_the_item(build, workspace):
    app, events = build
    conn = workspace.conn
    today = datetime.now(zone_of(conn)).date()
    weekday = (today + timedelta(days=1)).strftime("%A")
    app.rika.submit(conn, f"add a task Lab report next {weekday}")
    seen = until(events)
    assert not of(seen, "action") and tasks(conn) == {}
    [plan] = of(seen, "plan")
    assert (plan["plan_kind"], plan["status"]) == ("confirm", "pending")
    [item] = plan["items"]
    assert len(item["question"]["choices"]) == 2 and item["title"] == "Lab report"
    reply = of(seen, "message")[-1]
    assert reply["status"] == "done" and reply["text"] == item["question"]["text"]

    app.rika.decide_plan(conn, plan["id"], "approve", [item["id"]], {item["id"]: 1})
    seen = until(events)
    [action] = of(seen, "action")
    assert action["tool"] == "create_task" and action["reversible"]
    assert tasks(conn)["Lab report"]["due"] == (today + timedelta(days=8)).isoformat()
    assert of(seen, "plan")[-1]["status"] == "applied"
    assert of(seen, "message")[-1]["text"] == "Added the task Lab report."
    with pytest.raises(RikaError) as again:
        app.rika.decide_plan(conn, plan["id"], "approve", [item["id"]], {item["id"]: 0})
    assert again.value.status == 409


def test_another_date_or_not_now_answers_the_question(build, workspace):
    app, events = build
    conn = workspace.conn
    weekday = (datetime.now(zone_of(conn)).date() + timedelta(days=1)).strftime("%A")
    app.rika.submit(conn, f"add an event Study group next {weekday} at 4pm")
    [plan] = of(until(events), "plan")
    with pytest.raises(RikaError) as bad:
        app.rika.decide_plan(conn, plan["id"], "approve", ["i1"], {"i1": {"date": "soon"}})
    assert bad.value.status == 400
    app.rika.decide_plan(conn, plan["id"], "approve", ["i1"], {"i1": {"date": "2031-03-04", "time": "10:30"}})
    seen = until(events)
    assert of(seen, "action")[0]["tool"] == "create_event"
    start = conn.execute("SELECT start FROM calendar_events WHERE title='Study group' AND deleted=0").fetchone()["start"]
    assert start.startswith("2031-03-04T10:30")
    assert of(seen, "plan")[-1]["items"][0]["question"]["choices"][-1] == "Tue 4 Mar 2031, 10:30"

    app.rika.submit(conn, f"add a task Essay next {weekday}")
    [plan] = of(until(events), "plan")
    assert app.rika.decide_plan(conn, plan["id"], "dismiss")["status"] == "dismissed"
    assert "Essay" not in tasks(conn)


def test_answering_a_check_in_changes_the_task_and_books_the_next_one(build, workspace):
    app, events = build
    conn = workspace.conn
    yesterday = (datetime.now(zone_of(conn)).date() - timedelta(days=1)).isoformat()
    first = calendar.save_task(conn, {"title": "Send the report", "due": yesterday})["id"]
    second = calendar.save_task(conn, {"title": "Book a room", "due": yesterday})["id"]
    third = calendar.save_task(conn, {"title": "Old idea", "due": yesterday})["id"]
    for ident in (first, second, third):
        followups.schedule(conn, "task", ident)
    plans = {plan["items"][0]["title"]: plan for plan in followups.sweep(app, conn, datetime.now(timezone.utc) + timedelta(days=1))}
    assert len(plans) == 3
    while not events.empty():  # the questions themselves
        events.get_nowait()

    app.rika.decide_plan(conn, plans["Send the report"]["id"], "approve", ["i1"], {"i1": 0})
    seen = until(events)
    assert of(seen, "message")[-1]["text"] == "Marked Send the report done."
    assert tasks(conn)["Send the report"]["status"] == "done" and followup(conn, first)["status"] == "answered"
    assert of(seen, "action")[-1]["reversible"]

    app.rika.decide_plan(conn, plans["Book a room"]["id"], "approve", ["i1"], {"i1": {"date": "2031-03-04"}})
    until(events)
    assert tasks(conn)["Book a room"]["due"] == "2031-03-04"
    rows = conn.execute("SELECT status FROM rika_followups WHERE subject_id=? ORDER BY ask_at", (second,)).fetchall()
    assert [row["status"] for row in rows] == ["answered", "scheduled"]

    assert app.rika.decide_plan(conn, plans["Old idea"]["id"], "dismiss")["status"] == "dismissed"
    assert followup(conn, third)["status"] == "dismissed" and tasks(conn)["Old idea"]["status"] == "todo"
