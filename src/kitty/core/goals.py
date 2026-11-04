"""Goals (design Q14): first-class records with measures, linked to what moves them.

A goal has a title, a why, an optional target date and measures
``[{id, label, target, unit, current, auto}]``. A manual measure keeps the
``current`` you set; an ``auto`` measure computes it from linked data (linked jobs
with an application, linked study sessions' minutes, linked calendar tasks done,
linked math topics mastered), falling back, when nothing of that kind is linked,
to everything since the goal was created.

Links point at notes, blocks, calendar tasks and events, jobs, study sessions,
math topics, opportunities and mail; a deleted target still shows, as missing.
The morning brief (:mod:`kitty.core.rika.brief`) stores what it found for a goal
in ``goal_matches``; linking a match adds a goal link, dismissing one keeps it from
coming back.

Every change to a goal's own fields (title, why, target date, status, measures)
is revision-checked and bumps the revision; links do not.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from kitty.dates import parse as parse_instant
from kitty.db.connection import transaction

STATUSES = ("active", "paused", "done", "dropped")
#: Sources an auto measure computes `current` from.
AUTO = ("applications", "study_minutes", "tasks_done", "math_mastered")
#: Link kinds and the feature module each needs (None: the core).
LINK_KINDS: dict[str, str | None] = {
    "note": "workspace", "block": "workspace", "task": "workspace", "job": "applyai",
    "study_session": "workspace", "math_topic": "workspace", "opportunity": "workspace",
    "event": "workspace", "mail": None,
}
MATCH_KINDS = ("job", "opportunity", "mail", "event")
#: The view that shows each kind of item, or None.
VIEWS = {"note": "notes", "block": "notes", "task": "calendar", "job": "jobs", "study_session": "study",
         "math_topic": "math", "opportunity": "opportunities", "event": "calendar", "mail": None}
LABELS = {"note": "note", "block": "block", "task": "task", "job": "job", "study_session": "study session",
          "math_topic": "math topic", "opportunity": "opportunity", "event": "event", "mail": "email"}
MAX_MEASURES = 10
MAX_LINKS = 200
LINKABLE_LIMIT = 20
_MEASURE_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
_MEASURE_FIELDS = {"id", "label", "target", "unit", "current", "auto", "computed"}
_GOAL_FIELDS = {"title", "why", "target_date", "measures"}


class GoalError(ValueError):
    """A refused goal change; `status` is the HTTP status the web layer answers with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def zone(conn: sqlite3.Connection) -> ZoneInfo:
    """The calendar's local zone, which decides 'today' for goals and the brief."""
    return ZoneInfo(conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"])


def today(conn: sqlite3.Connection, at: datetime | None = None) -> date:
    return (at or datetime.now(timezone.utc)).astimezone(zone(conn)).date()


def _like(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _since(value: str | None, start: datetime) -> bool:
    parsed = parse_instant(value)
    return parsed is not None and parsed >= start


# Validation -------------------------------------------------------------------

def _text(value: Any, name: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise GoalError(f"{name} must be text")
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise GoalError(f"{name} must be {'1' if required else '0'}–{maximum} characters")
    return value


def _number(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise GoalError(f"{name} must be a finite number")
    return float(value) if isinstance(value, float) else value


def _target_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise GoalError("The target date must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise GoalError("The target date does not exist") from None
    return value


def _measures(value: Any, previous: list[dict] | None = None) -> list[dict]:
    """Validated measures; an item with a known `id` keeps it (and its current, unless given)."""
    if not isinstance(value, list) or len(value) > MAX_MEASURES:
        raise GoalError(f"Measures must be a list of at most {MAX_MEASURES}")
    before = {item["id"]: item for item in previous or []}
    output, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) - _MEASURE_FIELDS or not {"label", "target"} <= set(item):
            raise GoalError("A measure needs a label and a target, and only id, unit, current and auto besides")
        ident = item.get("id")
        if ident is None:
            ident = "m" + secrets.token_hex(4)
        elif not isinstance(ident, str) or not _MEASURE_ID.fullmatch(ident):
            raise GoalError("A measure id is 1–40 letters, digits, - or _")
        if ident in seen:
            raise GoalError("Two measures share an id")
        seen.add(ident)
        target = _number(item["target"], "A measure's target")
        if target <= 0:
            raise GoalError("A measure's target must be above 0")
        auto = item.get("auto")
        if auto is not None and auto not in AUTO:
            raise GoalError(f"A measure's auto source must be one of {', '.join(AUTO)}")
        if "current" in item:
            current = _number(item["current"], "A measure's current value")
            if current < 0:
                raise GoalError("A measure's current value cannot be negative")
        else:
            current = before.get(ident, {}).get("current", 0)
        output.append({"id": ident, "label": _text(item["label"], "A measure's label", 120, required=True),
                       "target": target, "unit": _text(item.get("unit", ""), "A measure's unit", 16),
                       "current": 0 if auto else current, "auto": auto})
    return output


def _revision(row: sqlite3.Row, revision: Any) -> None:
    if type(revision) is not int:
        raise GoalError("Send the goal's revision")
    if revision != row["revision"]:
        raise GoalError("This goal changed since you opened it; reload it and try again", 409)


def _row(conn: sqlite3.Connection, goal_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if row is None:
        raise GoalError("Goal not found", 404)
    return row


# Links ------------------------------------------------------------------------

def _int_ref(ref: str) -> int | None:
    return int(ref) if re.fullmatch(r"[1-9][0-9]{0,17}", ref) else None


def _resolve(conn: sqlite3.Connection, kind: str, ref: str) -> tuple[str, str] | None:
    """(title, detail) of a link target, or None when it does not exist."""
    number = _int_ref(ref)
    if kind == "note":
        # Pages and outline items come from the block projection (Notes numbers); note_pages is frozen.
        row = conn.execute("SELECT p.title,n.title AS notebook FROM note_page_index p JOIN note_notebooks n ON n.id=p.notebook_id"
                           " WHERE p.id=?", (number,)).fetchone() if number else None
        return (row["title"] or "Untitled", f"Page in {row['notebook']}") if row else None
    if kind == "block":
        row = conn.execute("SELECT text,title FROM note_outline WHERE id=?",
                           (ref,)).fetchone()
        if row is None:
            return None
        line = (row["text"].strip().splitlines() or [""])[0]
        return (line[:120] or "Empty block", f"Block on {row['title']}")
    if kind == "task":
        row = conn.execute("SELECT data FROM calendar_tasks WHERE id=? AND deleted=0", (ref,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["data"])
        status = {"todo": "To do", "doing": "Doing", "done": "Done", "cancelled": "Cancelled"}.get(data.get("status"), "")
        return data["title"], " · ".join(part for part in (status, f"due {data['due'][:10]}" if data.get("due") else "") if part)
    if kind == "event":
        row = conn.execute("SELECT title,start,all_day FROM calendar_events WHERE id=? AND deleted=0", (ref,)).fetchone()
        return (row["title"], row["start"][:10] if row["all_day"] else row["start"][:16].replace("T", " ")) if row else None
    if kind == "job":
        row = conn.execute("SELECT j.title,c.display_name,a.status FROM jobs j JOIN companies c ON c.id=j.company_id"
                           " LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=?", (number,)).fetchone() if number else None
        if row is None:
            return None
        return row["title"], row["display_name"] + (f" · {row['status']}" if row["status"] else "")
    if kind == "study_session":
        row = conn.execute("SELECT s.started_at,s.duration_seconds,sub.name FROM study_sessions s"
                           " LEFT JOIN study_subjects sub ON sub.id=s.subject_id WHERE s.id=?",
                           (number,)).fetchone() if number else None
        if row is None:
            return None
        return f"{row['name'] or 'Study'} · {round(row['duration_seconds'] / 60)} min", row["started_at"][:10]
    if kind == "math_topic":
        row = conn.execute("SELECT t.title,t.strand,m.mastered_at FROM math_topics t"
                           " LEFT JOIN math_mastery m ON m.topic_id=t.id WHERE t.id=?", (ref,)).fetchone()
        return (row["title"], row["strand"] + (" · mastered" if row["mastered_at"] else "")) if row else None
    if kind == "opportunity":
        row = conn.execute("SELECT title,category,deadline FROM opportunities WHERE id=?", (number,)).fetchone() if number else None
        if row is None:
            return None
        return row["title"], row["category"] + (f" · deadline {row['deadline'][:10]}" if row["deadline"] else "")
    if kind == "mail":
        row = conn.execute("SELECT subject,sender,sender_email FROM rika_mail_messages WHERE id=?", (ref,)).fetchone()
        return (row["subject"] or "(no subject)", f"From {row['sender'] or row['sender_email']}") if row else None
    raise GoalError(f"Links are to one of {', '.join(LINK_KINDS)}")


def _link(conn: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any]:
    found = _resolve(conn, kind, ref)
    if found is None:
        return {"kind": kind, "ref": ref, "title": f"Deleted {LABELS[kind]}", "detail": "", "view": None, "missing": True}
    return {"kind": kind, "ref": ref, "title": found[0], "detail": found[1], "view": VIEWS[kind], "missing": False}


def _link_args(kind: Any, ref: Any) -> tuple[str, str]:
    if kind not in LINK_KINDS:
        raise GoalError(f"Links are to one of {', '.join(LINK_KINDS)}")
    if isinstance(ref, int) and not isinstance(ref, bool):
        ref = str(ref)
    if not isinstance(ref, str) or not 1 <= len(ref) <= 200 or "\x00" in ref:
        raise GoalError("A link needs the item's id as ref")
    return kind, ref


def _refs(conn: sqlite3.Connection, goal_id: int, kind: str) -> list[str]:
    return [row["ref"] for row in conn.execute("SELECT ref FROM goal_links WHERE goal_id=? AND kind=?", (goal_id, kind))]


# Measures computed from data --------------------------------------------------

def _auto_current(conn: sqlite3.Connection, row: sqlite3.Row, source: str) -> float:
    # Most core timestamps have whole seconds; the goal's own has microseconds.
    created = parse_instant(row["created_at"]).replace(microsecond=0)
    if source == "applications":
        refs = [ref for ref in _refs(conn, row["id"], "job") if _int_ref(ref)]
        if refs:
            return conn.execute(f"SELECT count(*) FROM applications WHERE job_id IN ({','.join('?' * len(refs))})",
                                [int(ref) for ref in refs]).fetchone()[0]
        return sum(_since(item["applied_at"], created) for item in conn.execute("SELECT applied_at FROM applications"))
    if source == "study_minutes":
        refs = [int(ref) for ref in _refs(conn, row["id"], "study_session") if _int_ref(ref)]
        if refs:
            seconds = conn.execute(f"SELECT coalesce(sum(duration_seconds),0) FROM study_sessions"
                                   f" WHERE id IN ({','.join('?' * len(refs))})", refs).fetchone()[0]
        else:
            seconds = sum(item["duration_seconds"] for item in conn.execute(
                "SELECT ended_at,duration_seconds FROM study_sessions") if _since(item["ended_at"], created))
        return round(seconds / 60)
    if source == "tasks_done":
        refs = _refs(conn, row["id"], "task")
        if refs:
            tasks = conn.execute(f"SELECT data FROM calendar_tasks WHERE deleted=0 AND id IN ({','.join('?' * len(refs))})", refs)
            return sum(json.loads(item["data"]).get("status") == "done" for item in tasks)
        done = (json.loads(item["data"]) for item in conn.execute("SELECT data FROM calendar_tasks WHERE deleted=0"))
        return sum(data.get("status") == "done" and _since(data.get("completed_at"), created) for data in done)
    refs = _refs(conn, row["id"], "math_topic")
    if refs:
        return conn.execute(f"SELECT count(*) FROM math_mastery WHERE mastered_at IS NOT NULL"
                            f" AND topic_id IN ({','.join('?' * len(refs))})", refs).fetchone()[0]
    return sum(_since(item["mastered_at"], created)
               for item in conn.execute("SELECT mastered_at FROM math_mastery WHERE mastered_at IS NOT NULL"))


def _computed(conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict]:
    measures = []
    for item in json.loads(row["measures_json"]):
        computed = item.get("auto") in AUTO
        current = _auto_current(conn, row, item["auto"]) if computed else item["current"]
        measures.append({**item, "current": current, "computed": computed})
    return measures


def progress(status: str, measures: list[dict]) -> float:
    if status == "done":
        return 1.0
    if not measures:
        return 0.0
    return round(sum(min(1.0, max(0.0, item["current"] / item["target"])) for item in measures) / len(measures), 4)


# Output -----------------------------------------------------------------------

def match_json(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "goal_id": row["goal_id"], "goal_title": row["goal_title"], "kind": row["kind"],
            "ref": row["ref"], "title": row["title"], "detail": row["detail"], "score": round(row["score"], 3),
            "urgent": bool(row["urgent"]), "due_at": row["due_at"], "classified_by": row["classified_by"],
            "status": row["status"], "view": VIEWS[row["kind"]], "created_at": row["created_at"]}


MATCH_SELECT = "SELECT m.*,g.title AS goal_title FROM goal_matches m JOIN goals g ON g.id=m.goal_id"


def _goal(conn: sqlite3.Connection, row: sqlite3.Row, *, detail: bool) -> dict[str, Any]:
    measures = _computed(conn, row)
    days_left = (date.fromisoformat(row["target_date"]) - today(conn)).days if row["target_date"] else None
    result = {
        "id": row["id"], "title": row["title"], "why": row["why"], "target_date": row["target_date"],
        "status": row["status"], "measures": measures, "progress": progress(row["status"], measures),
        "days_left": days_left,
        "links_count": conn.execute("SELECT count(*) FROM goal_links WHERE goal_id=?", (row["id"],)).fetchone()[0],
        "suggestions_count": conn.execute("SELECT count(*) FROM goal_matches WHERE goal_id=? AND status='new'",
                                          (row["id"],)).fetchone()[0],
        "revision": row["revision"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if detail:
        result["links"] = [_link(conn, link["kind"], link["ref"]) for link in conn.execute(
            "SELECT kind,ref FROM goal_links WHERE goal_id=? ORDER BY created_at,kind,ref", (row["id"],))]
        result["suggestions"] = [match_json(item) for item in conn.execute(
            MATCH_SELECT + " WHERE m.goal_id=? AND m.status='new' ORDER BY m.urgent DESC,m.score DESC,m.id DESC",
            (row["id"],))]
    return result


def listing(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every goal: active first, then paused, done and dropped; each by target date (undated last)."""
    rows = conn.execute(
        "SELECT * FROM goals ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'done' THEN 2 ELSE 3 END,"
        " target_date IS NULL,target_date,id")
    return [_goal(conn, row, detail=False) for row in rows]


def get(conn: sqlite3.Connection, goal_id: int) -> dict[str, Any]:
    """One goal with its links and the brief's open suggestions."""
    return _goal(conn, _row(conn, goal_id), detail=True)


def active(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_goal(conn, row, detail=True) for row in conn.execute("SELECT * FROM goals WHERE status='active' ORDER BY id")]


# Changes ----------------------------------------------------------------------

def create(conn: sqlite3.Connection, body: dict) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - _GOAL_FIELDS or "title" not in body:
        raise GoalError("A goal needs a title, and only why, target_date and measures besides")
    title = _text(body["title"], "The title", 200, required=True)
    why = _text(body.get("why", ""), "The why", 2000)
    target = _target_date(body.get("target_date"))
    measures = _measures(body.get("measures", []))
    stamp = now()
    with transaction(conn):
        cursor = conn.execute("INSERT INTO goals(title,why,target_date,measures_json,created_at,updated_at)"
                              " VALUES(?,?,?,?,?,?)", (title, why, target, json.dumps(measures), stamp, stamp))
    return get(conn, cursor.lastrowid)


def update(conn: sqlite3.Connection, goal_id: int, body: dict) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - _GOAL_FIELDS - {"revision", "status"}:
        raise GoalError("A goal has only title, why, target_date, status and measures")
    with transaction(conn, immediate=True):
        row = _row(conn, goal_id)
        _revision(row, body.get("revision"))
        fields = {
            "title": _text(body["title"], "The title", 200, required=True) if "title" in body else row["title"],
            "why": _text(body["why"], "The why", 2000) if "why" in body else row["why"],
            "target_date": _target_date(body["target_date"]) if "target_date" in body else row["target_date"],
            "status": body.get("status", row["status"]),
            "measures_json": json.dumps(_measures(body["measures"], json.loads(row["measures_json"])))
            if "measures" in body else row["measures_json"],
        }
        if fields["status"] not in STATUSES:
            raise GoalError(f"The status must be one of {', '.join(STATUSES)}")
        conn.execute("UPDATE goals SET title=?,why=?,target_date=?,status=?,measures_json=?,revision=revision+1,"
                     "updated_at=? WHERE id=?", (*fields.values(), now(), goal_id))
    return get(conn, goal_id)


def delete(conn: sqlite3.Connection, goal_id: int, revision: Any) -> int:
    with transaction(conn, immediate=True):
        _revision(_row(conn, goal_id), revision)
        conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
    return goal_id


def link(conn: sqlite3.Connection, goal_id: int, kind: Any, ref: Any, *, modules=None) -> dict[str, Any]:
    """Link an existing item; linking it twice is a no-op."""
    kind, ref = _link_args(kind, ref)
    if modules is not None and LINK_KINDS[kind] is not None and LINK_KINDS[kind] not in modules:
        raise GoalError(f"Linking a {LABELS[kind]} needs the {LINK_KINDS[kind]} module, which is off", 403)
    with transaction(conn, immediate=True):
        _row(conn, goal_id)
        if _resolve(conn, kind, ref) is None:
            raise GoalError(f"No such {LABELS[kind]}", 404)
        if conn.execute("SELECT count(*) FROM goal_links WHERE goal_id=?", (goal_id,)).fetchone()[0] >= MAX_LINKS:
            raise GoalError(f"A goal holds at most {MAX_LINKS} links")
        conn.execute("INSERT OR IGNORE INTO goal_links(goal_id,kind,ref,created_at) VALUES(?,?,?,?)",
                     (goal_id, kind, ref, now()))
        conn.execute("UPDATE goal_matches SET status='linked' WHERE goal_id=? AND kind=? AND ref=? AND status='new'",
                     (goal_id, kind, ref))
        conn.execute("UPDATE goals SET updated_at=? WHERE id=?", (now(), goal_id))
    return get(conn, goal_id)


def linked(conn: sqlite3.Connection, goal_id: int, kind: str, ref: str) -> bool:
    return conn.execute("SELECT 1 FROM goal_links WHERE goal_id=? AND kind=? AND ref=?", (goal_id, kind, ref)).fetchone() is not None


def unlink(conn: sqlite3.Connection, goal_id: int, kind: Any, ref: Any) -> dict[str, Any]:
    kind, ref = _link_args(kind, ref)
    with transaction(conn, immediate=True):
        _row(conn, goal_id)
        if conn.execute("DELETE FROM goal_links WHERE goal_id=? AND kind=? AND ref=?", (goal_id, kind, ref)).rowcount:
            conn.execute("UPDATE goals SET updated_at=? WHERE id=?", (now(), goal_id))
    return get(conn, goal_id)


def set_measure(conn: sqlite3.Connection, goal_id: int, measure_id: str, current: Any,
                *, expected: float | None = None) -> dict[str, Any]:
    """Set a manual measure's current value; `expected` makes it conditional (undo)."""
    value = _number(current, "The current value")
    if value < 0:
        raise GoalError("The current value cannot be negative")
    with transaction(conn, immediate=True):
        row = _row(conn, goal_id)
        measures = json.loads(row["measures_json"])
        item = next((entry for entry in measures if entry["id"] == measure_id), None)
        if item is None:
            raise GoalError("Measure not found", 404)
        if item.get("auto"):
            raise GoalError("This measure is computed from linked data, so it cannot be set by hand", 409)
        if expected is not None and item["current"] != expected:
            raise GoalError("The measure changed since, so it was kept", 409)
        item["current"] = value
        conn.execute("UPDATE goals SET measures_json=?,revision=revision+1,updated_at=? WHERE id=?",
                     (json.dumps(measures), now(), goal_id))
    return get(conn, goal_id)


def decide_match(conn: sqlite3.Connection, match_id: int, decision: Any) -> dict[str, Any]:
    """Link a brief's match to its goal, or dismiss it for good."""
    if decision not in ("link", "dismiss"):
        raise GoalError("Decide link or dismiss")
    row = conn.execute("SELECT * FROM goal_matches WHERE id=?", (match_id,)).fetchone()
    if row is None:
        raise GoalError("Suggestion not found", 404)
    if row["status"] != "new":
        raise GoalError(f"This suggestion was already {row['status']}", 409)
    if decision == "dismiss":
        with transaction(conn):
            conn.execute("UPDATE goal_matches SET status='dismissed' WHERE id=?", (match_id,))
        return get(conn, row["goal_id"])
    if _resolve(conn, row["kind"], row["ref"]) is None:
        raise GoalError(f"That {LABELS[row['kind']]} no longer exists", 404)
    return link(conn, row["goal_id"], row["kind"], row["ref"])


# Search for things to link ----------------------------------------------------

def _search(conn: sqlite3.Connection, kind: str, query: str, limit: int) -> list[dict[str, Any]]:
    pattern = _like(query)
    if kind == "note":
        rows = conn.execute("SELECT id AS ref FROM note_page_index WHERE title LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT ?",
                            (pattern, limit))
    elif kind == "block":
        rows = conn.execute("SELECT id AS ref FROM note_outline WHERE text<>'' AND text LIKE ? ESCAPE '\\' LIMIT ?",
                            (pattern, limit))
    elif kind == "task":
        rows = conn.execute("SELECT id AS ref FROM calendar_tasks WHERE deleted=0 AND json_extract(data,'$.title') LIKE ?"
                            " ESCAPE '\\' ORDER BY updated_at DESC LIMIT ?", (pattern, limit))
    elif kind == "event":
        rows = conn.execute("SELECT id AS ref FROM calendar_events WHERE deleted=0 AND title LIKE ? ESCAPE '\\'"
                            " ORDER BY start DESC LIMIT ?", (pattern, limit))
    elif kind == "job":
        rows = conn.execute("SELECT j.id AS ref FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.title LIKE ?"
                            " ESCAPE '\\' OR c.display_name LIKE ? ESCAPE '\\' ORDER BY j.last_seen_at DESC LIMIT ?",
                            (pattern, pattern, limit))
    elif kind == "study_session":
        rows = conn.execute("SELECT s.id AS ref FROM study_sessions s LEFT JOIN study_subjects sub ON sub.id=s.subject_id"
                            " WHERE coalesce(sub.name,'Study') LIKE ? ESCAPE '\\' OR s.notes LIKE ? ESCAPE '\\'"
                            " ORDER BY s.started_at DESC LIMIT ?", (pattern, pattern, limit))
    elif kind == "math_topic":
        rows = conn.execute("SELECT id AS ref FROM math_topics WHERE title LIKE ? ESCAPE '\\' OR strand LIKE ? ESCAPE '\\'"
                            " ORDER BY position LIMIT ?", (pattern, pattern, limit))
    elif kind == "opportunity":
        rows = conn.execute("SELECT id AS ref FROM opportunities WHERE title LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT ?",
                            (pattern, limit))
    else:
        rows = conn.execute("SELECT id AS ref FROM rika_mail_messages WHERE subject LIKE ? ESCAPE '\\' OR sender LIKE ?"
                            " ESCAPE '\\' ORDER BY received_at DESC LIMIT ?", (pattern, pattern, limit))
    items = []
    for row in rows:
        ref = str(row["ref"])
        title, detail = _resolve(conn, kind, ref) or ("", "")
        items.append({"kind": kind, "ref": ref, "title": title, "detail": detail})
    return items


def linkable(conn: sqlite3.Connection, query: str, kind: str | None, modules) -> list[dict[str, Any]]:
    """Up to 20 items of the active modules' kinds whose title matches `query` (recent ones when empty)."""
    query = _text(query, "The search", 200)
    if kind is not None and kind not in LINK_KINDS:
        raise GoalError(f"Search one of {', '.join(LINK_KINDS)}")
    kinds = [name for name, module in LINK_KINDS.items()
             if (kind is None or name == kind) and (module is None or module in modules)]
    found = [_search(conn, name, query, LINKABLE_LIMIT) for name in kinds]
    # Round-robin, so one busy kind does not crowd out the others.
    items = [group[index] for index in range(LINKABLE_LIMIT) for group in found if index < len(group)]
    return items[:LINKABLE_LIMIT]


# Demo -------------------------------------------------------------------------

def demo_seed(conn: sqlite3.Connection) -> None:
    """Two synthetic goals for `kitty-web --demo`, linked to demo jobs when there are any,
    plus a calendar event tomorrow that the launch brief raises as urgent."""
    if conn.execute("SELECT 1 FROM goals LIMIT 1").fetchone():
        return
    internship = create(conn, {
        "title": "Land a summer 2027 internship",
        "why": "A summer of real engineering work before junior year.",
        "target_date": (today(conn) + timedelta(days=120)).isoformat(),
        "measures": [{"label": "Applications sent", "target": 40, "auto": "applications"},
                     {"label": "Interviews", "target": 5, "current": 2},
                     {"label": "Study minutes", "target": 600, "unit": "min", "auto": "study_minutes"}]})
    jobs = conn.execute("SELECT j.id FROM jobs j JOIN applications a ON a.job_id=j.id ORDER BY j.id LIMIT 2").fetchall()
    for row in jobs:
        link(conn, internship["id"], "job", str(row["id"]))
    create(conn, {
        "title": "Finish the frontend interview deck",
        "why": "Practice answers for frontend interviews, ready before the first one.",
        "target_date": (today(conn) + timedelta(days=14)).isoformat(),
        "measures": [{"label": "Deck done", "target": 100, "unit": "%", "current": 35}]})
    # All day, so it books no time in the calendar's views.
    tomorrow = today(conn) + timedelta(days=1)
    from kitty.core import calendar

    calendar.create_event(conn, {"title": "Frontend interview deck review", "start": tomorrow.isoformat(),
                                 "end": (tomorrow + timedelta(days=1)).isoformat(), "all_day": True,
                                 "timezone": zone(conn).key, "location": "Library room 2",
                                 "description": "Synthetic demo event: walk through the interview deck."})
