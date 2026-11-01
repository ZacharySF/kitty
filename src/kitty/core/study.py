"""Local study records and an observed, continuously alternating Pomodoro timer.

Only completed focus and explicit manual study count. Each observed completion
starts the next phase at observation time, never backfilling unattended cycles.
Priming, pauses and breaks never become study time. Revisions and request IDs protect
cross-window mutations and retried commands.
"""
from __future__ import annotations

import hashlib
import json
import re
from kitty.db.connection import transaction
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc
TABLES = {"subjects": "study_subjects", "projects": "study_projects", "targets": "study_targets",
          "tasks": "study_tasks", "exams": "study_exams", "plan": "study_plan_blocks"}
PRIMING_SECONDS = 300


class StudyConflict(ValueError):
    """The caller must reload rather than overwrite another window's change."""


def clock(now: datetime | None = None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware server clock is required")
    return value.astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def text(value, name: str, limit: int = 20000, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"{name} must be text of at most {limit} characters")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    return value


def integer(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def boolean(value, name: str) -> int:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return int(value)


def zone(value) -> str:
    value = text(value, "Timezone", 100, required=True)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Use an IANA timezone such as Europe/London or UTC") from None
    return value


def day(value, name="Date") -> str:
    value = text(value, name, 10, required=True)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must use YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return value


def timestamp(value, name="Timestamp") -> datetime:
    value = text(value, name, 100, required=True)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{name} must be an ISO8601 timestamp with an explicit offset") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} needs an explicit timezone offset")
    return parsed.astimezone(UTC)


def _fields(body: dict, allowed: set[str]):
    if not isinstance(body, dict) or set(body) - allowed:
        raise ValueError("Unknown study field")


def _reference(conn, value, table):
    if value in (None, ""):
        return None
    value = integer(value, "Reference", 1, 2**63 - 1)
    if not conn.execute(f"SELECT id FROM {table} WHERE id=?", (value,)).fetchone():
        raise ValueError("The selected study item no longer exists")
    return value


def _revision(row, value):
    if not isinstance(value, int) or isinstance(value, bool) or value != row["revision"]:
        raise StudyConflict("Study state changed in another window. Reload before editing.")


def _row(conn, table, item_id):
    item_id = integer(item_id, "ID", 1, 2**63 - 1)
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise ValueError("Study item not found")
    return dict(row)


def _settings(conn):
    result = dict(conn.execute("SELECT * FROM study_settings WHERE id=1").fetchone())
    result.pop("id")
    result["sound"] = bool(result["sound"])
    result["notifications"] = bool(result["notifications"])
    return result


def _timer(conn):
    return dict(conn.execute("SELECT * FROM study_timer WHERE id=1").fetchone())


def _save_timer(conn, value):
    value["revision"] += 1
    columns = [key for key in value if key != "id"]
    conn.execute("UPDATE study_timer SET " + ",".join(f"{key}=?" for key in columns) + " WHERE id=1",
                 [value[key] for key in columns])


def _remaining(timer, now):
    elapsed = timer["elapsed_seconds"]
    if timer["status"] == "running":
        elapsed += max(0.0, (now - timestamp(timer["running_since"])).total_seconds())
    return max(0.0, timer["duration_seconds"] - elapsed)


def _public_timer(timer, now):
    result = {key: value for key, value in timer.items() if key != "segments_json"}
    result["remaining_seconds"] = _remaining(timer, now)
    return result


def _guide_state(value):
    if value is None:
        return {"checked": [False] * 14, "responses": [""] * 14, "reflection": "", "next_review": ""}
    _fields(value, {"checked", "responses", "reflection", "next_review"})
    checked, responses = value.get("checked"), value.get("responses")
    if not isinstance(checked, list) or len(checked) != 14:
        raise ValueError("The study guide needs all 14 checklist items")
    if not isinstance(responses, list) or len(responses) != 14:
        raise ValueError("The study guide needs all 14 responses")
    next_review = value.get("next_review", "")
    return {"checked": [bool(boolean(item, "Checklist item")) for item in checked],
            "responses": [text(item, "Guide response", 4000) for item in responses],
            "reflection": text(value.get("reflection", ""), "Reflection", 10000),
            "next_review": day(next_review, "Next review") if next_review else ""}


def _guide(conn, session_id):
    row = conn.execute("SELECT * FROM study_guides WHERE id=?", (session_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["state"] = json.loads(result.pop("state_json"))
    return result


def save_guide(conn, body):
    _fields(body, {"session_id", "revision", "state"})
    session_id = text(body.get("session_id"), "Study session", 100, required=True)
    state = _guide_state(body.get("state"))
    with transaction(conn, immediate=True):
        current = _guide(conn, session_id)
        if current is None:
            raise ValueError("Study guide not found")
        _revision(current, body.get("revision"))
        conn.execute("UPDATE study_guides SET state_json=?,revision=revision+1 WHERE id=?",
                     (json.dumps(state), session_id))
        return {"guide": _guide(conn, session_id)}


def _begin_phase(timer, now, *, running=True):
    timer.update(status="running" if running else "paused", phase_id=str(uuid.uuid4()),
                 started_at=iso(now), running_since=iso(now) if running else None,
                 deadline_at=iso(now + timedelta(seconds=timer["duration_seconds"])) if running else None,
                 elapsed_seconds=0, segments_json="[]", notes="", distractions="")


def _reconcile(conn, now):
    timer = _timer(conn)
    if timer["status"] != "running" or now < timestamp(timer["deadline_at"]):
        return timer
    ended = timestamp(timer["deadline_at"])
    segments = json.loads(timer["segments_json"])
    segments.append([timer["running_since"], iso(ended)])
    completed_phase = timer["phase"]
    if timer["phase"] == "focus":
        conn.execute("""INSERT OR IGNORE INTO study_sessions
            (source,source_key,subject_id,project_id,target_id,started_at,ended_at,timezone,
             duration_seconds,segments_json,notes,distractions,created_at)
            VALUES('focus',?,?,?,?,?,?,?,?,?,?,?,?)""",
            (timer["phase_id"], timer["subject_id"], timer["project_id"], timer["target_id"],
             timer["started_at"], iso(ended), timer["timezone"], timer["duration_seconds"],
             json.dumps(segments), timer["notes"], timer["distractions"], iso(now)))
        timer["cycle"] += 1
        timer["phase"] = "short_break"
        timer["duration_seconds"] = _settings(conn)["short_break_seconds"]
    else:
        timer["phase"] = "focus"
        timer["duration_seconds"] = _settings(conn)["focus_seconds"]
    timer.update(completed_at=iso(ended), completed_phase_id=timer["phase_id"],
                 completed_phase=completed_phase)
    # Deliberately reconcile only one phase. A sleeping/offline browser must not
    # invent hours of study; the next phase's clock begins when it is observed.
    _begin_phase(timer, now)
    _save_timer(conn, timer)
    return timer


def timer_state(conn, *, now=None):
    now = clock(now)
    with transaction(conn, immediate=True):
        timer = _reconcile(conn, now)
    return {"timer": _public_timer(timer, now), "server_now": iso(now), "settings": _settings(conn),
            "guide": _guide(conn, timer["session_id"])}


def _request(conn, body, endpoint):
    request_id = text(body.get("request_id"), "Request ID", 100, required=True)
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    row = conn.execute("SELECT * FROM study_requests WHERE request_id=?", (request_id,)).fetchone()
    if row:
        if row["endpoint"] != endpoint or row["fingerprint"] != fingerprint:
            raise StudyConflict("A request ID cannot be reused for different changes")
        return request_id, fingerprint, json.loads(row["response_json"])
    return request_id, fingerprint, None


def _remember(conn, request_id, fingerprint, endpoint, response, now):
    conn.execute("INSERT INTO study_requests VALUES(?,?,?,?,?)",
                 (request_id, endpoint, fingerprint, json.dumps(response), iso(now)))


def timer_command(conn, body, *, now=None):
    _fields(body, {"action", "revision", "request_id", "subject_id", "project_id", "target_id", "timezone", "notes", "distractions", "guide"})
    now = clock(now)
    with transaction(conn, immediate=True):
        request_id, fingerprint, replay = _request(conn, body, "timer")
        if replay is not None:
            return replay
        timer = _reconcile(conn, now)
        _revision(timer, body.get("revision"))
        action = body.get("action")
        if action in {"pause", "resume"}:
            timer["notes"] = text(body.get("notes", timer["notes"]), "Notes")
            timer["distractions"] = text(body.get("distractions", timer["distractions"]), "Distractions")
        if action == "start":
            if timer["status"] != "idle":
                raise StudyConflict("Pause or stop the active timer before starting another")
            settings = _settings(conn)
            for key, table in (("subject_id", "study_subjects"), ("project_id", "study_projects"), ("target_id", "study_targets")):
                if key in body:
                    timer[key] = _reference(conn, body[key], table)
            timer.update(phase="priming", duration_seconds=PRIMING_SECONDS, cycle=0,
                         session_id=str(uuid.uuid4()), completed_at=None,
                         completed_phase_id=None, completed_phase=None,
                         timezone=zone(body.get("timezone", settings["timezone"])))
            conn.execute("INSERT INTO study_guides(id,created_at,state_json) VALUES(?,?,?)",
                         (timer["session_id"], iso(now), json.dumps(_guide_state(body.get("guide")))))
            _begin_phase(timer, now)
            timer.update(notes=text(body.get("notes", ""), "Notes"),
                         distractions=text(body.get("distractions", ""), "Distractions"))
        elif action == "pause":
            if timer["status"] != "running":
                raise StudyConflict("Only a running timer can be paused")
            segments = json.loads(timer["segments_json"])
            segments.append([timer["running_since"], iso(now)])
            timer["elapsed_seconds"] += max(0.0, (now - timestamp(timer["running_since"])).total_seconds())
            timer.update(status="paused", running_since=None, deadline_at=None, segments_json=json.dumps(segments))
        elif action == "resume":
            if timer["status"] != "paused":
                raise StudyConflict("Only a paused timer can be resumed")
            timer.update(status="running", running_since=iso(now),
                         deadline_at=iso(now + timedelta(seconds=_remaining(timer, now))))
        elif action in {"stop", "reset"}:
            conn.execute("UPDATE study_guides SET ended_at=COALESCE(ended_at,?) WHERE id=?",
                         (iso(now), timer["session_id"]))
            timer.update(status="idle", phase="priming", phase_id=None, cycle=0, started_at=None, running_since=None,
                         deadline_at=None, elapsed_seconds=0, segments_json="[]", notes="", distractions="",
                         duration_seconds=PRIMING_SECONDS)
        elif action == "skip_break":
            if timer["phase"] != "short_break" or timer["status"] == "idle":
                raise StudyConflict("There is no break to skip")
            running = timer["status"] == "running"
            timer.update(phase="focus", duration_seconds=_settings(conn)["focus_seconds"])
            _begin_phase(timer, now, running=running)
        elif action == "annotate":
            if timer["status"] not in {"running", "paused"}:
                raise StudyConflict("Add notes to the completed session instead")
            timer["notes"] = text(body.get("notes", timer["notes"]), "Notes")
            timer["distractions"] = text(body.get("distractions", timer["distractions"]), "Distractions")
        else:
            raise ValueError("Unknown timer action")
        _save_timer(conn, timer)
        response = {"timer": _public_timer(timer, now), "server_now": iso(now)}
        _remember(conn, request_id, fingerprint, "timer", response, now)
        return response


def save_settings(conn, body):
    allowed = {"revision", "timezone", "focus_seconds", "short_break_seconds", "sound", "notifications"}
    _fields(body, allowed)
    with transaction(conn, immediate=True):
        current = _settings(conn)
        _revision(current, body.get("revision"))
        values = {"timezone": zone(body.get("timezone", current["timezone"]))}
        for key, low, high in (("focus_seconds", 60, 14400), ("short_break_seconds", 60, 7200)):
            values[key] = integer(body.get(key, current[key]), key, low, high)
        for key in ("sound", "notifications"):
            values[key] = boolean(body.get(key, current[key]), key)
        conn.execute("UPDATE study_settings SET " + ",".join(f"{key}=?" for key in values) + ",revision=revision+1 WHERE id=1", list(values.values()))
        timer = _timer(conn)
        if timer["status"] == "idle":
            timer["duration_seconds"] = PRIMING_SECONDS if timer["phase"] == "priming" else values[timer["phase"] + "_seconds"]
            _save_timer(conn, timer)
        return {"settings": _settings(conn)}


def _entity_values(conn, kind, body):
    values = {}
    if kind in {"subjects", "projects"}:
        values["name"] = text(body.get("name"), "Name", 250, required=True)
    else:
        values["title"] = text(body.get("title"), "Title", 500, required=True)
    if kind == "subjects":
        color = text(body.get("color", "#7c6fdb"), "Color", 7)
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("Color must use #RRGGBB")
        values["color"] = color
    else:
        values["subject_id"] = _reference(conn, body.get("subject_id"), "study_subjects")
    if kind in {"targets", "tasks"}:
        values["project_id"] = _reference(conn, body.get("project_id"), "study_projects")
    if kind == "targets":
        if body.get("period") not in {"daily", "weekly"}:
            raise ValueError("Goal period must be daily or weekly")
        values.update(period=body["period"], minutes=integer(body.get("minutes"), "Goal minutes", 1, 10080))
    if kind == "tasks":
        if body.get("priority", "normal") not in {"low", "normal", "high"}:
            raise ValueError("Unknown task priority")
        checklist = body.get("checklist", [])
        if not isinstance(checklist, list) or len(checklist) > 100:
            raise ValueError("Checklist must contain at most 100 items")
        cleaned = []
        ids = set()
        for item in checklist:
            _fields(item, {"id", "text", "done"})
            item_id = text(item.get("id"), "Checklist ID", 100, required=True)
            if item_id in ids:
                raise ValueError("Checklist IDs must be unique")
            ids.add(item_id)
            cleaned.append({"id": item_id, "text": text(item.get("text"), "Checklist item", 500, required=True), "done": bool(boolean(item.get("done", False), "Checklist done"))})
        values.update(priority=body.get("priority", "normal"), due_date=day(body["due_date"], "Due date") if body.get("due_date") else None,
                      done=boolean(body.get("done", False), "Task done"), checklist_json=json.dumps(cleaned))
    if kind == "exams":
        values.update(exam_date=day(body.get("exam_date"), "Exam date"), timezone=zone(body.get("timezone")), notes=text(body.get("notes", ""), "Notes"))
    if kind == "plan":
        start, end = timestamp(body.get("start"), "Start"), timestamp(body.get("end"), "End")
        if end <= start or (end - start).total_seconds() > 86400:
            raise ValueError("Study blocks need a positive duration of at most one day")
        values.update(exam_id=_reference(conn, body.get("exam_id"), "study_exams"), start=iso(start), end=iso(end),
                      timezone=zone(body.get("timezone")), done=boolean(body.get("done", False), "Block done"))
    return values


def _public_entity(row):
    result = dict(row)
    if "checklist_json" in result:
        result["checklist"] = json.loads(result.pop("checklist_json"))
    if "done" in result:
        result["done"] = bool(result["done"])
    return result


def save_entity(conn, kind, body):
    if kind not in TABLES:
        raise ValueError("Unknown study collection")
    columns = {"subjects": {"name", "color"}, "projects": {"name", "subject_id"},
               "targets": {"title", "subject_id", "project_id", "period", "minutes"},
               "tasks": {"title", "subject_id", "project_id", "priority", "due_date", "done", "checklist"},
               "exams": {"title", "subject_id", "exam_date", "timezone", "notes"},
               "plan": {"title", "subject_id", "exam_id", "start", "end", "timezone", "done"}}
    _fields(body, columns[kind] | {"action", "id", "revision"})
    table = TABLES[kind]
    with transaction(conn, immediate=True):
        existing = _row(conn, table, body["id"]) if body.get("id") is not None else None
        if existing:
            _revision(existing, body.get("revision"))
        if body.get("action", "save") == "delete":
            if not existing:
                raise ValueError("An existing item is required for deletion")
            conn.execute(f"DELETE FROM {table} WHERE id=?", (existing["id"],))
            return {"deleted": existing["id"]}
        if body.get("action", "save") != "save":
            raise ValueError("Unknown collection action")
        merged = {**(_public_entity(existing) if existing else {}), **body}
        values = _entity_values(conn, kind, merged)
        if existing:
            conn.execute(f"UPDATE {table} SET " + ",".join(f"{key}=?" for key in values) + ",revision=revision+1 WHERE id=?",
                         [*values.values(), existing["id"]])
            item_id = existing["id"]
        else:
            item_id = conn.execute(f"INSERT INTO {table} (" + ",".join(values) + ") VALUES (" + ",".join("?" for _ in values) + ")", list(values.values())).lastrowid
        return {"item": _public_entity(_row(conn, table, item_id))}


def save_session(conn, body, *, now=None):
    _fields(body, {"action", "id", "revision", "request_id", "subject_id", "project_id", "target_id", "started_at", "timezone", "duration_seconds", "notes", "distractions"})
    now = clock(now)
    with transaction(conn, immediate=True):
        request_id, fingerprint, replay = _request(conn, body, "sessions")
        if replay is not None:
            return replay
        existing = _row(conn, "study_sessions", body["id"]) if body.get("id") is not None else None
        if existing:
            _revision(existing, body.get("revision"))
        if body.get("action", "save") == "delete":
            if not existing:
                raise ValueError("An existing session is required for deletion")
            conn.execute("DELETE FROM study_sessions WHERE id=?", (existing["id"],))
            response = {"deleted": existing["id"]}
        else:
            if body.get("action", "save") != "save":
                raise ValueError("Unknown session action")
            merged = {**(existing or {}), **body}
            started = timestamp(merged.get("started_at"), "Session start")
            duration = integer(merged.get("duration_seconds"), "Duration seconds", 1, 604800)
            ended = started + timedelta(seconds=duration)
            if ended > now:
                raise ValueError("A completed session cannot end in the future")
            values = {"subject_id": _reference(conn, merged.get("subject_id"), "study_subjects"),
                      "project_id": _reference(conn, merged.get("project_id"), "study_projects"),
                      "target_id": _reference(conn, merged.get("target_id"), "study_targets"),
                      "started_at": iso(started), "ended_at": iso(ended), "timezone": zone(merged.get("timezone")),
                      "duration_seconds": duration, "notes": text(merged.get("notes", ""), "Notes"),
                      "distractions": text(merged.get("distractions", ""), "Distractions")}
            # Note-only changes retain the focus's actual paused intervals.
            if existing and started == timestamp(existing["started_at"]) and duration == existing["duration_seconds"]:
                values["ended_at"] = existing["ended_at"]
                values["segments_json"] = existing["segments_json"]
            else:
                values["segments_json"] = json.dumps([[iso(started), iso(ended)]])
            if existing:
                conn.execute("UPDATE study_sessions SET " + ",".join(f"{key}=?" for key in values) + ",revision=revision+1 WHERE id=?", [*values.values(), existing["id"]])
                item_id = existing["id"]
            else:
                values.update(source="manual", source_key=request_id, created_at=iso(now))
                item_id = conn.execute("INSERT INTO study_sessions (" + ",".join(values) + ") VALUES (" + ",".join("?" for _ in values) + ")", list(values.values())).lastrowid
            response = {"item": _public_session(_row(conn, "study_sessions", item_id))}
        _remember(conn, request_id, fingerprint, "sessions", response, now)
        return response


def _public_session(row):
    return {key: value for key, value in dict(row).items() if key != "segments_json"}


def _split_segment(start, end, tz):
    while start < end:
        local = start.astimezone(tz)
        tomorrow = datetime.combine(local.date() + timedelta(days=1), time.min, tz).astimezone(UTC)
        boundary = min(end, tomorrow)
        yield local.date().isoformat(), (boundary - start).total_seconds()
        start = boundary


def _streak(values, current, step):
    cursor = current if current in values else current - step
    count = 0
    while cursor in values:
        count += 1
        cursor -= step
    return count


def _history_range(today, period, anchor):
    if period not in {"week", "month"}:
        raise ValueError("History period must be week or month")
    anchor = date.fromisoformat(day(anchor, "History date")) if anchor else today
    if not 2 <= anchor.year <= 9998:
        raise ValueError("History date must allow previous and next periods")
    if period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
        previous, following = start - timedelta(days=7), end
    else:
        start = anchor.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        previous, following = (start - timedelta(days=1)).replace(day=1), end
    return start, end, previous, following


def statistics(conn, *, timezone_name=None, now=None, history_period="week", history_date=None):
    now = clock(now)
    timezone_name = zone(timezone_name or _settings(conn)["timezone"])
    tz = ZoneInfo(timezone_name)
    today = now.astimezone(tz).date()
    monday = today - timedelta(days=today.weekday())
    start, end, previous, following = _history_range(today, history_period, history_date)
    start_key, end_key = start.isoformat(), end.isoformat()
    history_subjects, history_sessions = {}, []
    daily, subjects, scopes, daily_subjects = {}, {}, {}, {}
    completed_days, completed_weeks = set(), set()
    total = 0
    sessions = conn.execute("SELECT * FROM study_sessions ORDER BY ended_at,id").fetchall()
    for row in sessions:
        period_seconds = 0
        completed = timestamp(row["ended_at"]).astimezone(tz).date()
        completed_days.add(completed)
        completed_weeks.add(completed - timedelta(days=completed.weekday()))
        total += row["duration_seconds"]
        subjects[row["subject_id"]] = subjects.get(row["subject_id"], 0) + row["duration_seconds"]
        for raw_start, raw_end in json.loads(row["segments_json"]):
            for local_day, seconds in _split_segment(timestamp(raw_start), timestamp(raw_end), tz):
                daily[local_day] = daily.get(local_day, 0) + seconds
                by_subject = daily_subjects.setdefault(local_day, {})
                by_subject[row["subject_id"]] = by_subject.get(row["subject_id"], 0) + seconds
                scope = (row["subject_id"], row["project_id"], local_day)
                scopes[scope] = scopes.get(scope, 0) + seconds
                if start_key <= local_day < end_key:
                    period_seconds += seconds
        if period_seconds:
            history_sessions.append({**_public_session(row), "period_seconds": period_seconds})
            history_subjects[row["subject_id"]] = history_subjects.get(row["subject_id"], 0) + period_seconds
    today_seconds = daily.get(today.isoformat(), 0)
    week_seconds = sum(seconds for d, seconds in daily.items() if monday.isoformat() <= d <= today.isoformat())
    month_seconds = sum(seconds for d, seconds in daily.items() if today.replace(day=1).isoformat() <= d <= today.isoformat())
    goals = []
    for target in conn.execute("SELECT * FROM study_targets ORDER BY id"):
        start_day = today if target["period"] == "daily" else monday
        seconds = sum(duration for (subject_id, project_id, local_day), duration in scopes.items()
                      if start_day.isoformat() <= local_day <= today.isoformat()
                      and (target["subject_id"] is None or subject_id == target["subject_id"])
                      and (target["project_id"] is None or project_id == target["project_id"]))
        goals.append({"id": target["id"], "seconds": seconds, "goal_seconds": target["minutes"] * 60})
    subject_names = {row["id"]: row["name"] for row in conn.execute("SELECT id,name FROM study_subjects")}
    colors = {row["id"]: row["color"] for row in conn.execute("SELECT id,color FROM study_subjects")}

    def day_entry(key):
        # Each day's study split by subject, largest first, so views can tint a
        # day by its main subject or stack it in subject colours.
        split = sorted(daily_subjects.get(key, {}).items(), key=lambda item: (-item[1], item[0] is None, item[0] or 0))
        return {"date": key, "seconds": daily.get(key, 0),
                "subjects": [{"id": subject_id, "seconds": seconds} for subject_id, seconds in split]}

    history_days = [day_entry((start + timedelta(days=i)).isoformat()) for i in range((end - start).days)]
    history = {"period": history_period, "start": start_key, "end": end_key,
               "previous": previous.isoformat(), "next": following.isoformat(),
               "seconds": sum(item["seconds"] for item in history_days), "days": history_days,
               "session_count": len(history_sessions), "sessions": list(reversed(history_sessions)),
               "subjects": [{"id": key, "name": subject_names.get(key, "Unassigned"),
                             "color": colors.get(key, "#7c6fdb"), "seconds": value}
                            for key, value in history_subjects.items()]}
    return {"timezone": timezone_name, "today": today.isoformat(), "week_start": monday.isoformat(),
            "today_seconds": today_seconds, "week_seconds": week_seconds, "month_seconds": month_seconds, "total_seconds": total,
            "daily_streak": _streak(completed_days, today, timedelta(days=1)),
            "weekly_streak": _streak(completed_weeks, monday, timedelta(days=7)),
            "session_count": len(sessions), "history": history,
            "days": [day_entry(key) for key in sorted(daily)],
            "subjects": [{"id": key, "name": subject_names.get(key, "Unassigned"), "color": colors.get(key, "#7c6fdb"), "seconds": value} for key, value in subjects.items()],
            "goals": goals}


def workspace(conn, *, now=None, timezone_name=None, history_period="week", history_date=None):
    now = clock(now)
    with transaction(conn, immediate=True):
        timer = _reconcile(conn, now)
        result = {"timer": _public_timer(timer, now), "settings": _settings(conn), "server_now": iso(now),
                  "guide": _guide(conn, timer["session_id"]),
                  "guides": [_guide(conn, row["id"]) for row in conn.execute(
                      "SELECT id FROM study_guides ORDER BY created_at DESC LIMIT 50")]}
        for kind, table in TABLES.items():
            result[kind if kind != "plan" else "plans"] = [_public_entity(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        result["stats"] = statistics(conn, timezone_name=timezone_name, now=now,
                                     history_period=history_period, history_date=history_date)
        result["sessions"] = result["stats"]["history"].pop("sessions")
        return result


def generate_plan(conn, body, *, now=None):
    """Distribute user-entered study time over real, pre-exam dates; no AI facts."""
    _fields(body, {"exam_id", "exam_revision", "start_date", "minutes", "block_minutes", "local_time", "replace"})
    now = clock(now)
    with transaction(conn, immediate=True):
        exam = _row(conn, "study_exams", body.get("exam_id"))
        _revision(exam, body.get("exam_revision"))
        start_date = date.fromisoformat(day(body.get("start_date"), "Plan start"))
        exam_date = date.fromisoformat(exam["exam_date"])
        tz = ZoneInfo(exam["timezone"])
        if start_date < now.astimezone(tz).date() or start_date >= exam_date:
            raise ValueError("Plan start must be today or later and before the exam date")
        minutes = integer(body.get("minutes"), "Total minutes", 1, 10080)
        block_minutes = integer(body.get("block_minutes"), "Block minutes", 1, 240)
        local_time = text(body.get("local_time", "17:00"), "Local time", 5)
        if not re.fullmatch(r"\d{2}:\d{2}", local_time):
            raise ValueError("Local time must use HH:MM")
        try:
            hour, minute = map(int, local_time.split(":"))
            start_time = time(hour, minute)
        except ValueError:
            raise ValueError("Invalid local time") from None
        count = (minutes + block_minutes - 1) // block_minutes
        days_available = (exam_date - start_date).days
        if count > days_available:
            raise ValueError("Not enough pre-exam days for one block per day; increase block minutes or reduce total time")
        existing = conn.execute("SELECT COUNT(*) FROM study_plan_blocks WHERE exam_id=?", (exam["id"],)).fetchone()[0]
        replace = bool(boolean(body.get("replace", False), "Replace plan"))
        if existing and not replace:
            raise StudyConflict("This exam already has a plan. Explicitly choose replacement.")
        if replace:
            conn.execute("DELETE FROM study_plan_blocks WHERE exam_id=?", (exam["id"],))
        items = []
        remaining = minutes
        for index in range(count):
            chosen_day = start_date + timedelta(days=index * days_available // count)
            local = datetime.combine(chosen_day, start_time, tz)
            start = local.astimezone(UTC)
            if start.astimezone(tz).replace(tzinfo=None) != local.replace(tzinfo=None):
                raise ValueError("A planned time falls in a daylight-saving gap; choose another local time")
            if start < now:
                raise ValueError("A planned block starts in the past; choose a later time or start date")
            duration = min(block_minutes, remaining)
            end = start + timedelta(minutes=duration)
            if end.astimezone(tz).date() >= exam_date:
                raise ValueError("A block would overlap the exam day; choose an earlier time")
            item_id = conn.execute("INSERT INTO study_plan_blocks(exam_id,subject_id,title,start,end,timezone) VALUES(?,?,?,?,?,?)",
                                   (exam["id"], exam["subject_id"], f"Study: {exam['title']}", iso(start), iso(end), exam["timezone"])).lastrowid
            items.append(_public_entity(_row(conn, "study_plan_blocks", item_id)))
            remaining -= duration
        return {"items": items}
