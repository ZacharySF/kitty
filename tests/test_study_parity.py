"""The study routes answered by rika-server (server/src/study) against `kitty.core.study`:
one scenario run through the front and through the Python core on two copies of the same
workspace, at the same clock readings. Every answer, the rows left behind and the content
events recorded must be the same (random ids aside)."""
from __future__ import annotations

import json
import re
import shutil
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from terminal_support import front_site

from kitty.core import record_events, study
from kitty.db import connect

UTC = timezone.utc
BASE = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
TABLES = ("study_timer", "study_sessions", "study_guides", "study_requests", "study_subjects", "study_projects",
          "study_targets", "study_tasks", "study_exams", "study_plan_blocks")


class Masked:
    """Random ids by order of first appearance, so two runs compare."""

    def __init__(self):
        self.seen: dict[str, str] = {}

    def __call__(self, value):
        if isinstance(value, dict):
            return {key: self(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, str):
            return UUID.sub(lambda found: self.seen.setdefault(found[0], f"uuid-{len(self.seen)}"), value)
        return value


def rows(db_path, mask):
    with closing(connect(db_path)) as conn:
        dump = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")] for table in TABLES}
        for request in dump["study_requests"]:
            request["response_json"] = json.loads(request["response_json"])
        for row in dump["study_sessions"] + dump["study_timer"]:
            row["segments_json"] = json.loads(row["segments_json"])
        for row in dump["study_guides"]:
            row["state_json"] = json.loads(row["state_json"])
        events = [tuple(row) for row in conn.execute("SELECT section,entity,entity_id,action,route FROM record_events ORDER BY seq")]
    # Guides are keyed by a random id: by creation instead.
    dump["study_guides"].sort(key=lambda row: row["created_at"])
    dump["study_sessions"].sort(key=lambda row: row["id"])
    return mask(json.loads(json.dumps(dump))), events


class Front:
    """The front on one workspace, restarted when the clock reading changes."""

    def __init__(self, settings):
        self.settings, self.now, self.context, self.site = settings, None, None, None

    def __call__(self, path, body, now, query):
        if now != self.now:
            self.close()
            self.context = front_site(self.settings, {"KITTY_TEST_NOW": now.isoformat()})
            self.site, self.now = self.context.__enter__(), now
        return self.site.request(path + ("?" + urlencode(query) if query else ""), body)

    def close(self):
        if self.context is not None:
            self.context.__exit__(None, None, None)
            self.context = None


class Reference:
    """The routes as `kitty.web_study` answered them, and the events `WebApplication.mutate` recorded."""

    def __init__(self, db_path):
        self.conn = connect(db_path)
        self.events = []

    def __call__(self, path, body, now, query):
        conn = self.conn
        try:
            if body is None:
                if path == "/api/study":
                    if set(query) - {"timezone", "period", "date"}:
                        return 400, {"error": "Unknown study query parameter"}
                    answer = study.workspace(conn, now=now, timezone_name=query.get("timezone"),
                                             history_period=query.get("period", "week"), history_date=query.get("date"))
                else:
                    answer = study.timer_state(conn, now=now)
            else:
                route = path.removeprefix("/api/study/")
                if route == "timer":
                    answer = study.timer_command(conn, body, now=now)
                elif route == "guide":
                    answer = study.save_guide(conn, body)
                elif route == "returns":
                    answer = study.review_return(conn, body, now=now)
                elif route == "sessions":
                    answer = study.save_session(conn, body, now=now)
                elif route == "generate-plan":
                    answer = study.generate_plan(conn, body, now=now)
                else:
                    answer = study.save_entity(conn, route, body)
                event = record_events.classify(path, body, answer, conn)
                if event is not None:
                    self.events.append((*event, path))
        except study.StudyConflict as refused:
            return 409, {"error": str(refused)}
        except ValueError as refused:
            return 400, {"error": str(refused)}
        return 200, json.loads(json.dumps(answer))


def scenario(call):
    """Every study route, with the refusals between: returns each (status, answer) in order."""
    answers = []

    def do(path, body=None, now=BASE, expect=200, **query):
        status, answer = call(path, body, now, query)
        assert status == expect, (path, body, answer)
        answers.append((path, status, answer))
        return answer

    do("/api/study")
    chem = do("/api/study/subjects", {"name": "  Chemistry ", "color": "#aAbBcC"})["item"]
    art = do("/api/study/subjects", {"name": "Art"})["item"]
    lab = do("/api/study/projects", {"name": "Lab report", "subject_id": chem["id"]})["item"]
    daily = do("/api/study/targets", {"title": "Daily chemistry", "subject_id": chem["id"], "period": "daily", "minutes": 30})["item"]
    do("/api/study/targets", {"title": "Anything, weekly", "period": "weekly", "minutes": 600})
    task = do("/api/study/tasks", {"title": "Read chapter 3", "subject_id": chem["id"], "project_id": lab["id"], "priority": "high",
                                   "due_date": "2026-10-20", "checklist": [{"id": "a", "text": " first ", "done": True}, {"id": "b", "text": "second"}]})["item"]
    task = do("/api/study/tasks", {"id": task["id"], "revision": task["revision"], "done": True, "due_date": ""})["item"]
    do("/api/study/tasks", {"id": task["id"], "revision": 1, "title": "stale"}, expect=409)
    exam = do("/api/study/exams", {"title": "Midterm \u00e9\u4e16", "subject_id": chem["id"], "exam_date": "2026-10-30",
                                   "timezone": "America/New_York", "notes": " bring a calculator "})["item"]
    plan = {"exam_id": exam["id"], "exam_revision": exam["revision"], "start_date": "2026-10-15", "minutes": 150, "block_minutes": 45, "local_time": "18:30"}
    do("/api/study/generate-plan", plan)
    do("/api/study/generate-plan", plan, expect=409)
    blocks = do("/api/study/generate-plan", {**plan, "replace": True, "minutes": 61, "block_minutes": 60})["items"]
    do("/api/study/plan", {"id": blocks[0]["id"], "revision": blocks[0]["revision"], "done": True})
    do("/api/study/plan", {"title": "Extra", "start": "2026-10-16T10:00:00-04:00", "end": "2026-10-16T11:30:00-04:00",
                           "timezone": "America/New_York", "exam_id": exam["id"]})
    for path, body in (
        ("/api/study/subjects", {"name": ""}), ("/api/study/subjects", {"name": "x", "color": "red"}), ("/api/study/subjects", {"name": "x", "extra": 1}),
        ("/api/study/subjects", {"id": 999, "revision": 1, "name": "x"}), ("/api/study/subjects", {"action": "delete"}),
        ("/api/study/subjects", {"action": "archive", "name": "x"}), ("/api/study/projects", {"name": "x", "subject_id": 999}),
        ("/api/study/targets", {"title": "x", "period": "yearly", "minutes": 5}), ("/api/study/targets", {"title": "x", "period": "daily", "minutes": 0}),
        ("/api/study/tasks", {"title": "x", "priority": "urgent"}), ("/api/study/tasks", {"title": "x", "checklist": [{"id": "a", "text": "t"}, {"id": "a", "text": "u"}]}),
        ("/api/study/tasks", {"title": "x", "checklist": [7]}), ("/api/study/tasks", {"title": "x", "due_date": "20261020"}),
        ("/api/study/tasks", {"title": "x", "done": 1}), ("/api/study/exams", {"title": "x", "exam_date": "2026-02-30", "timezone": "UTC"}),
        ("/api/study/exams", {"title": "x", "exam_date": "2026-03-01", "timezone": "utc"}),
        ("/api/study/plan", {"title": "x", "start": "2026-10-16T10:00:00", "end": "2026-10-16T11:00:00Z", "timezone": "UTC"}),
        ("/api/study/plan", {"title": "x", "start": "2026-10-16T10:00:00Z", "end": "2026-10-18T11:00:00Z", "timezone": "UTC"}),
        ("/api/study/generate-plan", {**plan, "replace": True, "local_time": "25:00"}), ("/api/study/generate-plan", {**plan, "replace": True, "local_time": "9:00"}),
        ("/api/study/generate-plan", {**plan, "replace": True, "start_date": "2026-10-13"}), ("/api/study/generate-plan", {**plan, "replace": True, "minutes": 10080, "block_minutes": 1}),
        ("/api/study/generate-plan", {"exam_id": None}),
        ("/api/study/guide", {"session_id": "nope", "revision": 1}), ("/api/study/returns", {"session_id": "nope", "revision": 1, "result": "great"}),
        ("/api/study/sessions", {"request_id": "bad-1", "started_at": "2026-10-14T08:59:00Z", "duration_seconds": 3600, "timezone": "UTC"}),
        ("/api/study/sessions", {"request_id": "bad-2", "started_at": "yesterday", "duration_seconds": 60, "timezone": "UTC"}),
        ("/api/study/sessions", {"started_at": "2026-10-13T08:00:00Z", "duration_seconds": 60, "timezone": "UTC"}),
    ):
        do(path, body, expect=400)

    # The timer: priming, focus with a pause, the break, and what a late window observes.
    revision = do("/api/study/timer")["timer"]["revision"]
    do("/api/study/timer", {"action": "dance", "revision": revision, "request_id": "bad-3"}, expect=400)
    do("/api/study/timer", {"action": "stop", "revision": revision + 1, "request_id": "bad-4"}, expect=409)
    guide = {"checked": [True] + [False] * 13, "responses": [" why "] + [""] * 13, "reflection": "", "next_review": "2026-10-15"}
    start = {"action": "start", "revision": revision, "request_id": "start-1", "subject_id": chem["id"], "project_id": lab["id"],
             "target_id": daily["id"], "timezone": "America/New_York", "notes": " focus \u00e9 ", "guide": guide}
    started = do("/api/study/timer", start)["timer"]
    do("/api/study/timer", start, now=BASE + timedelta(seconds=40))  # the same request again: the first answer
    do("/api/study/timer", {**start, "notes": "other"}, now=BASE + timedelta(seconds=40), expect=409)
    do("/api/study/timer", {"action": "start", "revision": started["revision"], "request_id": "start-2"}, now=BASE + timedelta(seconds=40), expect=409)
    primed = BASE + timedelta(seconds=305, milliseconds=250)
    focus = do("/api/study/timer", now=primed)["timer"]
    paused_at = primed + timedelta(seconds=600, milliseconds=100)
    paused = do("/api/study/timer", {"action": "pause", "revision": focus["revision"], "request_id": "pause-1", "distractions": " phone "}, now=paused_at)["timer"]
    do("/api/study/timer", {"action": "pause", "revision": paused["revision"], "request_id": "pause-2"}, now=paused_at, expect=409)
    resumed_at = paused_at + timedelta(minutes=3)
    do("/api/study/timer", {"action": "resume", "revision": paused["revision"], "request_id": "resume-1", "notes": "back"}, now=resumed_at)
    later = resumed_at + timedelta(minutes=20)
    observed = do("/api/study", now=later, timezone="America/New_York")  # the focus ended unobserved: one session, the break begins now
    session_id = observed["timer"]["session_id"]
    skipped = do("/api/study/timer", {"action": "skip_break", "revision": observed["timer"]["revision"], "request_id": "skip-1"}, now=later)["timer"]
    noted = do("/api/study/timer", {"action": "annotate", "revision": skipped["revision"], "request_id": "note-1", "notes": "second focus"}, now=later)["timer"]
    stopped = do("/api/study/timer", {"action": "stop", "revision": noted["revision"], "request_id": "stop-1"}, now=later)["timer"]
    do("/api/study/timer", {"action": "skip_break", "revision": stopped["revision"], "request_id": "skip-2"}, now=later, expect=409)

    # Guides and spaced returns.
    current = do("/api/study/timer", now=later)["guide"]
    saved = do("/api/study/guide", {"session_id": session_id, "revision": current["revision"],
                                    "state": {**current["state"], "reflection": " Mostly there. "}}, now=later)["guide"]
    do("/api/study/guide", {"session_id": session_id, "revision": current["revision"], "state": current["state"]}, now=later, expect=409)
    do("/api/study/guide", {"session_id": session_id, "revision": saved["revision"], "state": {**current["state"], "checked": [True]}}, now=later, expect=400)
    graded = do("/api/study/returns", {"session_id": session_id, "revision": saved["revision"], "result": "recalled"}, now=later)["guide"]
    graded = do("/api/study/returns", {"session_id": session_id, "revision": graded["revision"], "result": "recalled"}, now=later)["guide"]
    do("/api/study/returns", {"session_id": session_id, "revision": graded["revision"], "result": "shaky"}, now=later)

    # Sessions entered by hand, two days on: one crosses a local midnight.
    after = BASE + timedelta(days=2)
    night = do("/api/study/sessions", {"request_id": "manual-1", "started_at": "2026-10-13T23:30:00-04:00", "duration_seconds": 3600,
                                       "timezone": "America/New_York", "subject_id": art["id"]}, now=after)["item"]
    do("/api/study/sessions", {"request_id": "manual-1", "started_at": "2026-10-13T23:30:00-04:00", "duration_seconds": 3600,
                               "timezone": "America/New_York", "subject_id": art["id"]}, now=after)
    do("/api/study/sessions", {"request_id": "manual-2", "started_at": "2026-10-05T12:00:00+00:00", "duration_seconds": 1234, "timezone": "UTC"}, now=after)
    extra = do("/api/study/sessions", {"request_id": "manual-3", "started_at": "2026-09-30T22:00:00Z", "duration_seconds": 10800, "timezone": "UTC",
                                       "subject_id": chem["id"], "project_id": lab["id"]}, now=after)["item"]
    night = do("/api/study/sessions", {"request_id": "manual-4", "id": night["id"], "revision": night["revision"], "notes": " dense "}, now=after)["item"]
    do("/api/study/sessions", {"request_id": "manual-5", "id": night["id"], "revision": night["revision"], "duration_seconds": 5400}, now=after)
    do("/api/study/sessions", {"request_id": "manual-6", "id": extra["id"], "revision": 9, "action": "delete"}, now=after, expect=409)
    for query in ({}, {"timezone": "America/New_York"}, {"timezone": "Asia/Tokyo", "period": "month"}, {"period": "month", "date": "2026-09-15", "timezone": "UTC"},
                  {"period": "week", "date": "2026-10-01"}):
        do("/api/study", now=after, **query)
    for query in ({"period": "year"}, {"period": "month", "date": "2026-02-30"}, {"unknown": "1"}, {"timezone": "Mars/Base"}, {"date": "0001-01-01"}):
        do("/api/study", now=after, expect=400, **query)
    do("/api/study/sessions", {"request_id": "manual-7", "id": extra["id"], "revision": extra["revision"], "action": "delete"}, now=after)
    do("/api/study/subjects", {"id": art["id"], "revision": art["revision"], "action": "delete"}, now=after)
    do("/api/study", now=after + timedelta(days=9), timezone="America/New_York")
    return answers


def test_the_front_answers_study_as_the_python_core_does(workspace, tmp_path):
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    copy = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, copy)

    reference, front = Reference(copy), Front(workspace.settings)
    try:
        expected = scenario(reference)
        answered = scenario(front)
    finally:
        front.close()
        reference.conn.close()
    left, right = Masked(), Masked()
    assert len(answered) == len(expected)
    for (path, status, answer), (_, wanted_status, wanted) in zip(answered, expected, strict=True):
        assert (status, left(answer)) == (wanted_status, right(wanted)), path
    # A session logged by the timer and sums that are not whole seconds are in the run.
    final = expected[-1][2]
    assert final["stats"]["session_count"] == 3 and all(isinstance(day["seconds"], float) for day in final["stats"]["days"])
    stored, events = rows(workspace.settings.db_path, left)
    reference_rows, _ = rows(copy, right)
    assert stored == reference_rows
    assert left([list(event) for event in events]) == right([list(event) for event in reference.events])
    assert ("study", "subject", "1", "create", "/api/study/subjects") in events


def test_a_request_python_answered_replays_through_the_front(workspace):
    """The request fingerprint is the same in both, so an upgrade does not turn a retry into a conflict."""
    conn = workspace.conn
    revision = study.timer_state(conn, now=BASE)["timer"]["revision"]
    body = {"action": "start", "revision": revision, "request_id": "before-upgrade", "notes": "caf\u00e9 \u4e16 \"quoted\"", "timezone": "UTC",
            "guide": {"checked": [False] * 14, "responses": [""] * 14, "reflection": "x", "next_review": ""}}
    first = json.loads(json.dumps(study.timer_command(conn, body, now=BASE)))
    conn.close()
    with front_site(workspace.settings, {"KITTY_TEST_NOW": (BASE + timedelta(minutes=1)).isoformat()}) as site:
        assert site.request("/api/study/timer", body) == (200, first)
        assert site.request("/api/study/timer", {**body, "notes": "changed"})[0] == 409
        # Settings stay with the worker, behind the front.
        status, state = site.request("/api/study/timer")
        status, saved = site.request("/api/study/settings", {"revision": state["settings"]["revision"], "focus_seconds": 600})
        assert status == 200 and saved["settings"]["focus_seconds"] == 600
        assert site.request("/api/study/nope", {})[0] == 404
