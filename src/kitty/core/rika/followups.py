"""Check-ins: once the day of a dated task Rika added has passed, "Did you finish it?".

A follow-up (``rika_followups``, migration 057) is one question Rika owes for a task or
study task. :func:`schedule` books it when Rika's tools add or move a dated task: for
the due day at :data:`CHECK_IN` local time, or the due time when that is later.

:func:`sweep` runs on every tick of the morning brief's scheduler. For each follow-up
now due it looks at the task as it is today:

* gone, done or dropped, or no longer dated: the follow-up expires silently;
* moved to a later day: the follow-up moves with it;
* still open: Rika posts the question in the conversation with a ``checkin`` plan of one
  item, whose choices are Done, Tomorrow and Drop, and whose other date is "Another day".

The answer runs through the ordinary plan runner and Rika's task tools, so it applies at
once with Undo; moving the task books the next check-in. Each task is asked about once
per due date, and at most :data:`MAX_ASKED` questions are posted per sweep.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from kitty.core.rika import store, when
from kitty.db.connection import transaction

#: The local time Rika asks about a task due that day.
CHECK_IN = time(18)
MAX_ASKED = 3
KINDS = ("task", "study_task")


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _zone(conn: sqlite3.Connection) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"])


def subject(conn: sqlite3.Connection, kind: str, ident: str) -> dict[str, Any] | None:
    """A task as a check-in sees it: ``{title, due, open}``; None when it is gone."""
    if kind == "task":
        row = conn.execute("SELECT data FROM calendar_tasks WHERE id=? AND deleted=0", (ident,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["data"])
        return {"title": data["title"], "due": data.get("due") or "", "open": data.get("status", "todo") in ("todo", "doing")}
    if kind == "study_task" and ident.isdigit():
        row = conn.execute("SELECT title,due_date,done FROM study_tasks WHERE id=?", (int(ident),)).fetchone()
        if row is None:
            return None
        return {"title": row["title"], "due": row["due_date"] or "", "open": not row["done"]}
    return None


def ask_at(due: str, zone: ZoneInfo) -> datetime:
    """When to ask about a task due `due` (a date or an ISO time): the check-in hour of its day, or the due time if later."""
    if len(due) == 10:
        return datetime.combine(datetime.fromisoformat(due).date(), CHECK_IN, zone)
    moment = datetime.fromisoformat(due.replace("Z", "+00:00"))
    moment = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)
    return max(moment, datetime.combine(moment.date(), CHECK_IN, zone))


def schedule(conn: sqlite3.Connection, kind: str, ident: str, *, origin: str = "rika") -> str | None:
    """Book the check-in for a task's current due date, replacing one still waiting; its ``ask_at`` or None."""
    found = subject(conn, kind, ident)
    with transaction(conn):
        conn.execute("DELETE FROM rika_followups WHERE subject_kind=? AND subject_id=? AND status='scheduled'", (kind, ident))
        if found is None or not found["open"] or not found["due"]:
            return None
        moment = _utc(ask_at(found["due"], _zone(conn)))
        conn.execute("INSERT OR IGNORE INTO rika_followups(id,subject_kind,subject_id,ask_at,origin,created_at)"
                     " VALUES(?,?,?,?,?,?)", (store.new_id(), kind, ident, moment, origin, store.now()))
    return moment


def after_call(conn: sqlite3.Connection, tool: str, data: dict) -> None:
    """Book the check-in a finished tool call earned: a dated task added or moved."""
    if tool == "create_task":
        schedule(conn, "task", str(data["task_id"]))
    elif tool == "create_study_task":
        schedule(conn, "study_task", str(data["task_id"]))
    elif tool == "reschedule_task":
        schedule(conn, data["kind"], str(data["task_id"]))


def item(kind: str, ident: str, found: dict, today) -> dict:
    """The one item of a check-in plan: the question and what each answer calls."""
    ref = {"task_id": ident, "kind": kind}
    due = datetime.fromisoformat(found["due"][:10]).date()
    choices = [("Done", "complete_task", ref), ("Tomorrow", "reschedule_task", {**ref, "due": "tomorrow"}),
               ("Drop", "drop_task", ref)]
    return {"id": "i1", "area": "tasks", "title": found["title"][:200], "detail": f"Due {when.label(due, None, today)}",
            "source": "", "calls": [],
            "question": {"text": question(found["title"]),
                         "choices": [{"label": label, "calls": [{"tool": tool, "args": args}]} for label, tool, args in choices]},
            "choice": 0, "result": None, "slot": {"tool": "reschedule_task", "base": 1, "time": False}}


def question(title: str) -> str:
    return f"Did you finish “{title}”?"


def sweep(app, conn: sqlite3.Connection, now: datetime | None = None, *, lock=None) -> list[dict]:
    """Ask every check-in that is due; the plans posted. `lock` guards the writes when the caller does not hold it."""
    at = now or datetime.now(timezone.utc)
    lock = lock or nullcontext()
    zone = _zone(conn)
    today = at.astimezone(zone).date()
    posted = []
    rows = conn.execute("SELECT * FROM rika_followups WHERE status='scheduled' AND ask_at<=? ORDER BY ask_at,id",
                        (_utc(at),)).fetchall()
    for row in rows:
        kind, ident = row["subject_kind"], row["subject_id"]
        found = subject(conn, kind, ident)
        if found is None or not found["open"] or not found["due"]:
            with lock, transaction(conn):
                conn.execute("UPDATE rika_followups SET status='expired' WHERE id=?", (row["id"],))
            continue
        moved = ask_at(found["due"], zone)
        if moved > at:
            with lock, transaction(conn):
                conn.execute("UPDATE OR REPLACE rika_followups SET ask_at=? WHERE id=?", (_utc(moved), row["id"]))
            continue
        if len(posted) >= MAX_ASKED:
            break
        with lock:
            message = store.add_message(conn, conversation_id=store.conversation_for(conn), role="rika",
                                        text=question(found["title"]), model="checkin")
            plan = store.add_plan(conn, kind="checkin", message_id=message["id"], request_id=message["id"],
                                  items=[item(kind, ident, found, today)], segments=[], duration=0,
                                  note_page_id=None, note_block_id=None)
            with transaction(conn):
                conn.execute("UPDATE rika_followups SET status='asked',asked_at=?,plan_id=? WHERE id=?",
                             (store.now(), plan["id"], row["id"]))
        app.rika.hub.publish({"type": "message", "message": message})
        app.rika.hub.publish({"type": "plan", "plan": plan})
        posted.append(plan)
    return posted


def settle(conn: sqlite3.Connection, plan_id: str, status: str) -> None:
    """The check-in a plan asked was answered or dismissed."""
    with transaction(conn):
        conn.execute("UPDATE rika_followups SET status=?,answered_at=? WHERE plan_id=? AND status='asked'",
                     (status, store.now(), plan_id))
