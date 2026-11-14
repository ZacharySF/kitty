"""The calendar's local routes, answered by rika-server (server/src/calendar/), on isolated SQLite.

Every request goes through the front as a window sends it; setup that has no route (jobs,
study subjects) writes the shared database directly. ``KITTY_TEST_NOW`` fixes the front's
clock where the Python tests monkeypatched ``calendar.now``. No real account or submission
dates.
"""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pytest

from kitty.core import jobs
from terminal_support import front_site


class Calendar:
    """The calendar routes of one front, restartable with another clock."""

    def __init__(self, settings):
        self.settings = settings
        self._site = None
        self._context = None

    def start(self, now: str | None = None):
        self.stop()
        self._context = front_site(self.settings, {"KITTY_TEST_NOW": now} if now else None)
        self._site = self._context.__enter__()
        return self

    def stop(self):
        if self._context is not None:
            self._context.__exit__(None, None, None)
            self._context = self._site = None

    @property
    def site(self):
        if self._site is None:
            self.start()
        return self._site

    def call(self, path, body=None, **query):
        target = path + ("?" + urlencode(query) if query else "")
        return self.site.request(target, body)

    def get(self, path, **query):
        status, answer = self.call(path, **query)
        assert status == 200, answer
        return answer

    def post(self, path, body):
        status, answer = self.call(path, body)
        assert status == 200, answer
        return answer

    def refused(self, path, body=None, *, status=None, match=None, **query):
        code, answer = self.call(path, body, **query)
        assert code >= 400, answer
        if status is not None:
            assert code == status, answer
        if match is not None:
            assert match in answer["error"], answer
        return code, answer["error"]

    # The former core functions, as routes.
    def event(self, **changes):
        return self.post("/api/calendar/events", {"title": "Study block", "start": "2026-10-26T09:00:00-05:00",
                                                  "end": "2026-10-26T10:00:00-05:00", "timezone": "America/Chicago",
                                                  "all_day": False, **changes})

    def occurrences(self, start="2026-10-01T00:00:00Z", end="2026-12-01T00:00:00Z", **query):
        return self.get("/api/calendar", start=start, end=end, **query)["events"]

    def edit(self, ident, body):
        return self.post(f"/api/calendar/events/{ident}", body)

    def undo(self, ident):
        return self.post("/api/calendar/undo", {"id": ident})

    def task(self, body):
        return self.post("/api/calendar/tasks", body)

    def tasks(self):
        return self.get("/api/calendar", start="2026-10-01T00:00:00Z", end="2026-11-01T00:00:00Z")["tasks"]

    def save_calendar(self, body):
        return self.post("/api/calendar/calendars", body)

    def reminders(self):
        return self.get("/api/calendar/reminders")["items"]


@pytest.fixture
def api(workspace):
    running = Calendar(workspace.settings)
    try:
        yield running
    finally:
        running.stop()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_multiple_calendars_visibility_search_and_revision_conflict(api):
    cal = api.save_calendar({"name": "School", "color": "#123abc", "timezone": "America/Chicago"})
    item = api.event(calendar_id=cal["id"], description="Read chapter about graph theory")
    assert len(api.occurrences(end="2026-11-01T00:00:00Z", search="graph theory")) == 1
    hidden = api.save_calendar({"id": cal["id"], "revision": cal["revision"], "visible": False})
    assert api.occurrences() == []
    assert len(api.occurrences(end="2026-11-01T00:00:00Z", hidden="1")) == 1
    api.refused("/api/calendar/calendars", {"id": cal["id"], "revision": cal["revision"], "name": "stale"}, status=409)
    api.refused("/api/calendar/calendars/delete", {"id": cal["id"], "revision": hidden["revision"]})
    assert api.get(f"/api/calendar/events/{item['id']}")["title"] == "Study block"


def test_rrule_preserves_wall_clock_across_dst_and_exclusions(api):
    item = api.event(rrule="FREQ=WEEKLY;COUNT=4", exdates=["2026-11-09T09:00:00-06:00"])
    expanded = api.occurrences()
    assert [v["start"] for v in expanded] == ["2026-10-26T09:00:00-05:00", "2026-11-02T09:00:00-06:00", "2026-11-16T09:00:00-06:00"]
    assert all(v["id"] == item["id"] for v in expanded)
    assert all(v["end"][11:16] == "10:00" for v in expanded)


def test_dst_gap_is_rejected_and_count_does_not_consume_nonexistent_time(api):
    api.refused("/api/calendar/time", {"value": "2027-03-14T02:30", "timezone": "America/Chicago"}, match="does not exist")
    assert api.post("/api/calendar/time", {"value": "2026-11-01T01:30", "timezone": "America/Chicago", "fold": 0})["value"].endswith("-05:00")
    assert api.post("/api/calendar/time", {"value": "2026-11-01T01:30", "timezone": "America/Chicago", "fold": 1})["value"].endswith("-06:00")
    api.event(start="2027-03-13T02:30:00-06:00", end="2027-03-13T03:00:00-06:00", rrule="FREQ=DAILY;COUNT=3")
    result = api.occurrences("2027-03-12T00:00:00Z", "2027-03-18T00:00:00Z")
    assert [v["start"][:10] for v in result] == ["2027-03-13", "2027-03-15", "2027-03-16"]


def test_future_split_keeps_count_boundary_exceptions_and_undo(api):
    original = api.event(rrule="FREQ=WEEKLY;COUNT=5")
    edited = api.edit(original["id"], {"revision": 1, "scope": "this", "occurrence": "2026-11-16T09:00:00-06:00", "changes": {"title": "Special session"}})
    split = api.edit(original["id"], {"revision": edited["revision"], "scope": "future",
                                      "occurrence": "2026-11-09T09:00:00-06:00", "changes": {"description": "New plan"}})
    values = api.occurrences()
    assert len(values) == 5
    assert len([v for v in values if v["id"] == original["id"]]) == 2
    assert split["future_event"]["rrule"] == "FREQ=WEEKLY;COUNT=3"
    assert next(v for v in values if v["occurrence"].startswith("2026-11-16"))["title"] == "Special session"
    api.undo(split["undo_id"])
    restored = api.occurrences()
    assert len(restored) == 5 and all(v["id"] == original["id"] for v in restored)
    assert next(v for v in restored if v["occurrence"].startswith("2026-11-16"))["title"] == "Special session"


def test_this_occurrence_move_delete_and_all_shift_preserves_override(api):
    original = api.event(rrule="FREQ=WEEKLY;COUNT=3")
    moved = api.edit(original["id"], {"revision": 1, "scope": "this", "occurrence": "2026-11-02T09:00:00-06:00",
                                      "changes": {"start": "2026-11-03T11:00:00-06:00", "end": "2026-11-03T12:00:00-06:00", "title": "Moved session"}})
    assert [v["start"][:10] for v in api.occurrences()] == ["2026-10-26", "2026-11-03", "2026-11-09"]
    shifted = api.edit(original["id"], {"revision": moved["revision"], "scope": "all",
                                        "changes": {"start": "2026-10-26T10:00:00-05:00", "end": "2026-10-26T11:00:00-05:00"}})
    exception = next(v for v in api.occurrences() if v["title"] == "Moved session")
    assert exception["start"] == "2026-11-03T12:00:00-06:00"
    deleted = api.edit(original["id"], {"revision": shifted["revision"], "scope": "this", "occurrence": exception["occurrence"], "delete": True})
    assert len(api.occurrences()) == 2
    api.undo(deleted["undo_id"])
    assert len(api.occurrences()) == 3


def test_all_day_multiday_until_date_and_half_open_range(api):
    api.event(start="2026-10-20", end="2026-10-23", all_day=True, rrule="FREQ=WEEKLY;UNTIL=20261103")
    assert len(api.occurrences("2026-10-01T00:00:00Z", "2026-11-10T00:00:00Z")) == 3
    assert api.occurrences("2026-10-23T05:00:00Z", "2026-10-24T05:00:00Z") == []
    assert len(api.occurrences("2026-10-22T05:00:00Z", "2026-10-23T05:00:00Z")) == 1


@pytest.mark.parametrize("changes", [
    {"start": "2026-10-26T09:00:00"}, {"timezone": "Not/AZone"}, {"end": "2026-10-26T08:00:00-05:00"},
    {"all_day": True}, {"rrule": "FREQ=SECONDLY"}, {"rrule": "FREQ=DAILY;COUNT=3;UNTIL=20261030T000000Z"},
    {"url": "javascript:alert(1)"}, {"reminders": [-1]}, {"attendees": [{"email": "invalid"}]},
])
def test_event_validation_errors_do_not_mutate(api, workspace, changes):
    api.refused("/api/calendar/events", {"title": "Study block", "start": "2026-10-26T09:00:00-05:00",
                                         "end": "2026-10-26T10:00:00-05:00", "timezone": "America/Chicago", "all_day": False, **changes},
                status=400)
    assert count(workspace.conn, "calendar_events") == 0


def test_optimistic_edits_and_undo_never_overwrite_later_change(api):
    original = api.event()
    first = api.edit(original["id"], {"revision": 1, "changes": {"title": "First"}})
    api.refused(f"/api/calendar/events/{original['id']}", {"revision": 1, "changes": {"title": "Stale"}}, status=409)
    api.edit(original["id"], {"revision": first["revision"], "changes": {"title": "Later"}})
    api.refused("/api/calendar/undo", {"id": first["undo_id"]}, status=409)
    assert api.get(f"/api/calendar/events/{original['id']}")["title"] == "Later"


def test_task_workflows_and_completion_activity_preserve_actual_completion(api):
    item = api.task({"title": "Review trees", "due": "2026-10-28", "timezone": "America/Chicago", "tags": ["DSA", "DSA"], "project": "Interview", "priority": "high"})
    assert item["tags"] == ["DSA"]
    completed = api.task({"id": item["id"], "revision": item["revision"], "status": "done"})
    edited = api.task({"id": item["id"], "revision": completed["revision"], "title": "Reviewed trees"})
    assert edited["completed_at"] == completed["completed_at"]
    api.refused("/api/calendar/tasks", {"id": item["id"], "revision": 1, "status": "doing"})
    api.undo(edited["undo_id"])
    assert api.tasks()[0]["title"] == "Review trees"


def test_availability_conflicts_and_free_events(api):
    first = api.event()
    second = api.event(title="Overlapping", start="2026-10-26T09:30:00-05:00", end="2026-10-26T10:30:00-05:00")
    api.event(title="Free note", start="2026-10-26T11:00:00-05:00", end="2026-10-26T12:00:00-05:00", busy=False)
    result = api.post("/api/calendar/availability", {"start": "2026-10-26T13:00:00Z", "end": "2026-10-26T18:00:00Z"})
    assert len(result["busy"]) == 1 and len(result["free"]) == 2
    assert result["conflicts"][0]["event_id"] == second["id"] and first["id"] in result["conflicts"][0]["with"]
    excluded = api.post("/api/calendar/availability", {"start": "2026-10-26T13:00:00Z", "end": "2026-10-26T18:00:00Z", "exclude_id": second["id"]})
    assert excluded["conflicts"] == []
    snapshot = api.get("/api/calendar", start="2026-10-26T13:00:00Z", end="2026-10-26T18:00:00Z")
    assert snapshot["busy"] == result["busy"] and snapshot["free"] == result["free"]


def test_reminders_dismissal_is_occurrence_specific(api):
    api.start("2026-10-26T08:55:00-05:00")
    item = api.event(rrule="FREQ=DAILY;COUNT=2", reminders=[10])
    reminder = api.reminders()[0]
    api.post("/api/calendar/reminders/dismiss", {key: reminder[key] for key in ("event_id", "occurrence", "minutes")})
    assert api.reminders() == []
    api.start("2026-10-27T08:55:00-05:00")
    following = api.reminders()
    assert following[0]["event_id"] == item["id"] and following[0]["occurrence"] != reminder["occurrence"]


def test_source_refresh_idempotent_manual_override_and_no_source_writes(api, workspace):
    conn = workspace.conn
    job, _ = jobs.upsert_job(conn, company="Synthetic calendar company", title="Intern", url="https://example.invalid/job/calendar", source="manual", source_job_id="calendar")
    conn.execute("UPDATE jobs SET deadline_at=? WHERE id=?", ("2026-11-01", job.id))
    assert api.post("/api/calendar/import-jobs", {})["changed"] == 1
    assert api.post("/api/calendar/import-jobs", {})["changed"] == 0
    linked = api.occurrences()[0]
    assert linked["source_key"] == f"job:{job.id}:deadline"
    same = api.post("/api/calendar/events", {"title": "Would overwrite", "start": "2026-11-02", "end": "2026-11-03", "timezone": "UTC", "all_day": True, "source_key": linked["source_key"]})
    assert same["id"] == linked["id"] and same["title"] == linked["title"]
    api.edit(linked["id"], {"revision": linked["revision"], "changes": {"title": "My copy"}})
    conn.execute("UPDATE jobs SET deadline_at=? WHERE id=?", ("2026-11-03", job.id))
    assert api.post("/api/calendar/import-jobs", {})["changed"] == 0
    assert api.get(f"/api/calendar/events/{linked['id']}")["title"] == "My copy"
    assert conn.execute("SELECT deadline_at FROM jobs WHERE id=?", (job.id,)).fetchone()[0] == "2026-11-03"


def test_csv_preview_review_and_atomic_validation(api, workspace):
    content = "title,date,timezone\nApply,2026-11-01,UTC\nInvalid,not-a-date,UTC\n"
    preview = api.post("/api/calendar/import/preview", {"format": "csv", "content": content})
    assert preview["items"][0]["event"]["all_day"] is True
    assert "error" in preview["items"][1]
    api.refused("/api/calendar/import", {"confirmed": True, "items": [preview["items"][0], {"event": {"title": "Invalid"}}]})
    assert count(workspace.conn, "calendar_events") == 0
    result = api.post("/api/calendar/import", {"confirmed": True, "items": [preview["items"][0]]})
    assert len(result["created"]) == 1
    again = api.post("/api/calendar/import/preview", {"format": "csv", "content": "title,date,timezone\nApply,2026-11-01,UTC\n"})
    assert again["items"][0]["duplicate"] is True


def test_nullable_source_key_and_empty_optional_fields_roundtrip_on_edit(api):
    original = api.event(source_key=None, source_url="", attendees=[], attachments=[], google_options={})
    edited = api.edit(original["id"], {"revision": original["revision"], "changes": {"title": "Renamed"}})
    assert edited["source_key"] is None and edited["source_url"] == ""
    assert edited["attendees"] == [] and edited["attachments"] == [] and edited["google_options"] == {}
    api.undo(edited["undo_id"])
    assert api.get(f"/api/calendar/events/{original['id']}")["title"] == "Study block"
    api.refused("/api/calendar/events", {"title": "x", "start": "2026-10-26T09:00:00-05:00", "end": "2026-10-26T10:00:00-05:00",
                                         "source_key": 0}, match="Source key must be text")


@pytest.mark.parametrize(("start", "end", "at", "due"), [
    ("2026-11-01T01:30:00-06:00", "2026-11-01T02:30:00-06:00", "2026-11-01T01:35:00-05:00", "2026-11-01T01:30:00-05:00"),
    ("2027-03-14T03:30:00-05:00", "2027-03-14T04:30:00-05:00", "2027-03-14T01:35:00-06:00", "2027-03-14T01:30:00-06:00"),
])
def test_reminder_offsets_use_elapsed_minutes_across_dst(api, start, end, at, due):
    api.start(at)
    api.event(start=start, end=end, reminders=[60])
    result = api.reminders()
    assert len(result) == 1 and result[0]["due"] == due


def test_calendar_cannot_be_deleted_while_a_task_references_it(api):
    cal = api.save_calendar({"name": "Task calendar"})
    task = api.task({"title": "Keep this task", "calendar_id": cal["id"]})
    api.refused("/api/calendar/calendars/delete", {"id": cal["id"], "revision": cal["revision"]}, match="tasks")
    api.refused("/api/calendar/tasks", {"id": task["id"], "revision": task["revision"], "delete": "false"}, match="Delete must be boolean")
    api.task({"id": task["id"], "revision": task["revision"], "calendar_id": "personal"})
    assert api.post("/api/calendar/calendars/delete", {"id": cal["id"], "revision": cal["revision"]})["ok"]
    assert api.tasks()[0]["calendar_id"] == "personal"


def test_removed_source_milestone_disappears_and_restores_without_overwriting_manual_copy(api, workspace):
    conn = workspace.conn
    job, _ = jobs.upsert_job(conn, company="Synthetic source calendar", title="Intern",
                            url="https://example.invalid/job/source", source="manual", source_job_id="source")
    conn.execute("UPDATE jobs SET deadline_at=? WHERE id=?", ("2026-11-01", job.id))
    assert api.post("/api/calendar/import-jobs", {})["changed"] == 1
    original = api.occurrences()[0]
    conn.execute("UPDATE jobs SET deadline_at=NULL WHERE id=?", (job.id,))
    assert api.post("/api/calendar/import-jobs", {})["changed"] == 1 and api.occurrences() == []
    conn.execute("UPDATE jobs SET deadline_at=? WHERE id=?", ("2026-11-01", job.id))
    assert api.post("/api/calendar/import-jobs", {})["changed"] == 1
    restored = api.occurrences()[0]
    assert restored["id"] == original["id"]
    api.edit(restored["id"], {"revision": restored["revision"], "changes": {"title": "Protected manual copy"}})
    conn.execute("UPDATE jobs SET deadline_at=NULL WHERE id=?", (job.id,))
    assert api.post("/api/calendar/import-jobs", {})["changed"] == 0
    assert api.occurrences()[0]["title"] == "Protected manual copy"


def test_this_scope_does_not_silently_discard_series_or_calendar_edits(api):
    original = api.event(rrule="FREQ=WEEKLY;COUNT=3")
    cal = api.save_calendar({"name": "Another calendar"})
    for changes in ({"rrule": "FREQ=DAILY;COUNT=3"}, {"exdates": [original["start"]]}, {"calendar_id": cal["id"]}):
        api.refused(f"/api/calendar/events/{original['id']}", {"revision": original["revision"], "scope": "this",
                                                               "occurrence": original["start"], "changes": changes}, match="individual occurrence")
    assert len(api.occurrences()) == 3


def test_timed_duration_and_fold_survive_dst_expansion_and_occurrence_edits(api):
    item = api.event(start="2026-11-01T01:30:00-06:00", end="2026-11-01T02:30:00-06:00", rrule="FREQ=DAILY;COUNT=2")
    values = api.occurrences()
    assert values[0]["start"] == "2026-11-01T01:30:00-06:00"
    assert values[0]["end"] == "2026-11-01T02:30:00-06:00"
    api.refused(f"/api/calendar/events/{item['id']}", {"revision": item["revision"], "scope": "this",
                                                       "occurrence": "2026-11-01T01:30:00-05:00", "changes": {"title": "Wrong fold"}}, match="not in this series")
    edited = api.edit(item["id"], {"revision": item["revision"], "scope": "this",
                                   "occurrence": values[0]["occurrence"], "changes": {"title": "Correct fold"}})
    api.edit(item["id"], {"revision": edited["revision"], "scope": "all", "changes": {"description": "Unrelated edit"}})
    assert api.occurrences()[0]["start"] == "2026-11-01T01:30:00-06:00"


def test_recurrence_end_uses_elapsed_duration_and_never_lands_in_spring_gap(api):
    original = api.event(start="2027-03-13T01:30:00-06:00", end="2027-03-13T02:30:00-06:00", rrule="FREQ=DAILY;COUNT=3")
    values = api.occurrences("2027-03-12T00:00:00Z", "2027-03-17T00:00:00Z")
    assert values[1]["start"] == "2027-03-14T01:30:00-06:00"
    assert values[1]["end"] == "2027-03-14T03:30:00-05:00"
    assert all(datetime.fromisoformat(v["end"]) - datetime.fromisoformat(v["start"]) == timedelta(hours=1) for v in values)
    edited = api.edit(original["id"], {"revision": original["revision"], "scope": "this",
                                       "occurrence": values[1]["occurrence"], "changes": {"title": "Spring occurrence"}})
    assert next(v for v in api.occurrences("2027-03-12T00:00:00Z", "2027-03-17T00:00:00Z")
                if v["title"] == "Spring occurrence")["end"] == "2027-03-14T03:30:00-05:00"
    assert edited["revision"] == original["revision"] + 1


def test_future_scope_at_spring_transition_keeps_exact_count_boundary(api):
    original = api.event(start="2027-03-13T03:00:00-06:00", end="2027-03-13T04:00:00-06:00", rrule="FREQ=DAILY;COUNT=3")
    split = api.edit(original["id"], {"revision": original["revision"], "scope": "future",
                                      "occurrence": "2027-03-14T03:00:00-05:00", "changes": {"title": "Future"}})
    assert split["future_event"]["rrule"] == "FREQ=DAILY;COUNT=2"
    values = api.occurrences("2027-03-12T00:00:00Z", "2027-03-17T00:00:00Z")
    assert [v["title"] for v in values] == ["Study block", "Future", "Future"]


def repeating_task(api, **changes):
    return api.task({"title": "Recurring review", "due": "2026-10-01", "timezone": "America/Chicago", "repeat_frequency": "DAILY", **changes})


def complete_task(api, item):
    return api.task({"id": item["id"], "revision": item["revision"], "status": "done"})


def test_recurring_tasks_skip_missed_dates_and_preserve_real_history(api):
    api.start("2026-10-14T17:00:00+00:00")
    item = repeating_task(api, tags=["DSA"], description="Keep notes", priority="high", project="Practice")
    completed = complete_task(api, item)
    following = completed["next_task"]
    assert completed["completed_at"] == "2026-10-14T17:00:00+00:00"
    assert completed["due"] == "2026-10-01"
    assert following["due"] == "2026-10-15"
    assert following["status"] == "todo" and following["completed_at"] is None
    assert all(following[key] == completed[key] for key in
               ("title", "tags", "description", "priority", "project", "timezone", "repeat_frequency", "repeat_interval"))
    values = api.tasks()
    assert len(values) == 2 and sum(v["status"] == "done" for v in values) == 1
    days = api.get("/api/calendar", start="2026-10-01T00:00:00Z", end="2026-10-20T00:00:00Z", timezone="America/Chicago")["activity"]
    assert [(v["date"], v.get("tasks_completed")) for v in days if "tasks_completed" in v] == [("2026-10-14", 1)]
    repeated = complete_task(api, completed)
    assert repeated["next_task"] is None and repeated["completed_at"] == completed["completed_at"]
    assert len(api.tasks()) == 2
    api.refused("/api/calendar/tasks", {"id": item["id"], "revision": item["revision"], "status": "done"}, status=409)
    assert len(api.tasks()) == 2


@pytest.mark.parametrize(("frequency", "interval", "due", "expected"), [
    ("DAILY", 3, "2026-10-01", "2026-10-16"),
    ("WEEKLY", 2, "2026-09-30", "2026-10-28"),
    ("MONTHLY", 2, "2026-08-31", "2026-10-31"),
    ("YEARLY", 2, "2024-02-29", "2028-02-29"),
])
def test_recurring_task_intervals_skip_to_future_anchor_boundary(api, frequency, interval, due, expected):
    api.start("2026-10-14T17:00:00+00:00")
    item = repeating_task(api, due=due, repeat_frequency=frequency, repeat_interval=interval)
    assert complete_task(api, item)["next_task"]["due"] == expected


@pytest.mark.parametrize(("frequency", "due", "first_due", "later_clock", "second_due"), [
    ("MONTHLY", "2027-01-31", "2027-02-28", "2027-02-28T17:00:00+00:00", "2027-03-31"),
    ("YEARLY", "2024-02-29", "2025-02-28", "2027-02-28T17:00:00+00:00", "2028-02-29"),
])
def test_recurring_task_month_end_and_leap_anchor_never_drift(api, workspace, frequency, due, first_due, later_clock, second_due):
    api.start(due + "T17:00:00+00:00")
    original = repeating_task(api, due=due, repeat_frequency=frequency)
    first = complete_task(api, original)["next_task"]
    assert first["due"] == first_due
    # Ordinary edits of an instance must not turn a clamped date into its anchor.
    first = api.task({"id": first["id"], "revision": first["revision"], "title": "Updated next title"})
    api.start(later_clock)
    second = complete_task(api, first)["next_task"]
    assert second["due"] == second_due
    rows = workspace.conn.execute("SELECT repeat_anchor,task_series_id FROM calendar_tasks WHERE deleted=0").fetchall()
    assert {row["repeat_anchor"] for row in rows} == {due}
    assert {row["task_series_id"] for row in rows} == {original["id"]}


@pytest.mark.parametrize(("due", "clock", "expected"), [
    ("2026-10-31T09:00:00-05:00", "2026-10-31T15:00:00+00:00", "2026-11-01T09:00:00-06:00"),
    ("2027-03-13T02:30:00-06:00", "2027-03-13T09:00:00+00:00", "2027-03-15T02:30:00-05:00"),
    ("2026-11-01T01:30:00-06:00", "2026-11-01T07:00:00+00:00", "2026-11-02T01:30:00-06:00"),
    ("2026-10-14T18:00:00Z", "2026-10-14T17:00:00+00:00", "2026-10-15T13:00:00-05:00"),
])
def test_recurring_task_times_normalize_and_preserve_wall_clock_across_dst(api, due, clock, expected):
    from kitty.core import calendar
    api.start(clock)
    item = repeating_task(api, due=due)
    assert item["due"] == calendar.instant(due, "America/Chicago").isoformat()
    assert complete_task(api, item)["next_task"]["due"] == expected
    assert len(api.tasks()) == 2


@pytest.mark.parametrize(("due", "clock", "until", "expected"), [
    ("2026-10-14", "2026-10-14T17:00:00+00:00", "2026-10-15", "2026-10-15"),
    ("2026-10-14", "2026-10-15T17:00:00+00:00", "2026-10-15", None),
    ("2026-10-14T23:30:00-05:00", "2026-10-15T05:00:00+00:00", "2026-10-15", "2026-10-15T23:30:00-05:00"),
    ("9999-12-31", "2026-10-14T17:00:00+00:00", "", None),
])
def test_recurring_task_stop_date_is_inclusive_in_task_timezone(api, due, clock, until, expected):
    api.start(clock)
    result = complete_task(api, repeating_task(api, due=due, repeat_until=until))
    assert (result["next_task"]["due"] if result["next_task"] else None) == expected
    if result["next_task"]:
        api.start(expected[:10] + "T17:00:00+00:00")
        assert complete_task(api, result["next_task"])["next_task"] is None
    done = sum(v["status"] == "done" for v in api.get("/api/calendar", start="2026-10-01T00:00:00Z", end="2026-11-01T00:00:00Z")["tasks"])
    assert done == (2 if expected else 1)


def test_recurring_task_completion_undo_tombstones_successor_and_can_complete_again(api, workspace):
    conn = workspace.conn
    api.start("2026-10-14T17:00:00+00:00")
    item = repeating_task(api)
    completed = complete_task(api, item)
    next_id = completed["next_task"]["id"]
    api.undo(completed["undo_id"])
    restored = api.tasks()
    assert len(restored) == 1 and restored[0]["status"] == "todo" and restored[0]["completed_at"] is None
    assert conn.execute("SELECT deleted FROM calendar_tasks WHERE id=?", (next_id,)).fetchone()["deleted"] == 1
    repeated = complete_task(api, restored[0])
    assert repeated["next_task"]["id"] != next_id and repeated["next_task"]["due"] == "2026-10-15"
    assert len(api.tasks()) == 2
    assert conn.execute("SELECT COUNT(*) FROM calendar_tasks WHERE previous_task_id=? AND deleted=0", (item["id"],)).fetchone()[0] == 1
    api.refused("/api/calendar/undo", {"id": completed["undo_id"]}, status=404)


@pytest.mark.parametrize("change", [{"title": "Edited next"}, {"status": "done"}, {"delete": True}])
def test_recurring_task_undo_rejects_changed_successor_atomically(api, workspace, change):
    conn = workspace.conn
    api.start("2026-10-14T17:00:00+00:00")
    completed = complete_task(api, repeating_task(api))
    following = completed["next_task"]
    api.task({"id": following["id"], "revision": following["revision"], **change})
    before = [dict(row) for row in conn.execute("SELECT * FROM calendar_tasks ORDER BY id")]
    api.refused("/api/calendar/undo", {"id": completed["undo_id"]}, status=409)
    assert [dict(row) for row in conn.execute("SELECT * FROM calendar_tasks ORDER BY id")] == before
    assert conn.execute("SELECT undone FROM calendar_history WHERE id=?", (completed["undo_id"],)).fetchone()[0] == 0


def test_recurring_task_history_edits_do_not_rewrite_recurrence_or_generate(api):
    api.start("2026-10-14T17:00:00+00:00")
    completed = complete_task(api, repeating_task(api))
    edited = api.task({"id": completed["id"], "revision": completed["revision"], "title": "History note"})
    assert edited["completed_at"] == completed["completed_at"] and edited["next_task"] is None
    following = completed["next_task"]
    for changes in ({"status": "todo"}, {"repeat_frequency": "WEEKLY"}, {"repeat_interval": 2}, {"repeat_until": "2026-12-31"}):
        api.refused("/api/calendar/tasks", {"id": edited["id"], "revision": edited["revision"], **changes}, status=409)
    assert next(v for v in api.tasks() if v["id"] == following["id"])["repeat_frequency"] == "DAILY"
    changed_next = api.task({"id": following["id"], "revision": following["revision"], "repeat_frequency": "WEEKLY", "repeat_interval": 2})
    assert complete_task(api, changed_next)["next_task"]["due"] == "2026-10-29"
    assert next(v for v in api.tasks() if v["id"] == completed["id"])["repeat_frequency"] == "DAILY"


def test_recurring_task_deletion_cancellation_and_creation_undo_keep_crud_behavior(api):
    api.start("2026-10-14T17:00:00+00:00")
    plain = api.task({"title": "Plain task"})
    assert (plain["repeat_frequency"], plain["repeat_interval"], plain["repeat_until"]) == ("", 1, "")
    api.undo(plain["undo_id"])
    assert api.tasks() == []
    recurring = repeating_task(api)
    cancelled = api.task({"id": recurring["id"], "revision": recurring["revision"], "status": "cancelled"})
    assert cancelled["next_task"] is None and cancelled["completed_at"] is None
    deleted = api.task({"id": recurring["id"], "revision": cancelled["revision"], "delete": True, "status": "done"})
    assert deleted["next_task"] is None and api.tasks() == []
    api.undo(deleted["undo_id"])
    assert api.tasks()[0]["status"] == "cancelled"
    created_done = repeating_task(api, status="done")
    assert created_done["next_task"] is not None
    api.undo(created_done["undo_id"])
    assert len(api.tasks()) == 1


@pytest.mark.parametrize("changes", [
    {"repeat_frequency": "HOURLY"}, {"repeat_frequency": None}, {"repeat_interval": 0},
    {"repeat_interval": -1}, {"repeat_interval": True}, {"repeat_interval": 1.5}, {"repeat_interval": "2"},
    {"due": ""}, {"due": None}, {"due": "2026-10-14T09:00:00"}, {"timezone": "Invalid/Zone"},
    {"repeat_until": "2026-02-30"}, {"repeat_until": "2026-10-15T00:00:00Z"}, {"repeat_until": "2026-09-30"},
])
def test_recurring_task_validation_is_atomic(api, workspace, changes):
    api.start("2026-10-14T17:00:00+00:00")
    api.refused("/api/calendar/tasks", {"title": "Recurring review", "due": "2026-10-01", "timezone": "America/Chicago",
                                        "repeat_frequency": "DAILY", **changes}, status=400)
    assert api.tasks() == []
    assert count(workspace.conn, "calendar_history") == 0


def study_subject(conn, name="Physics", color="#31a2bc"):
    from kitty.core import study
    return study.save_entity(conn, "subjects", {"name": name, "color": color})["item"]["id"]


def test_new_calendars_take_theme_palette_slots_and_keep_existing_hex(api, workspace):
    conn = workspace.conn
    assert api.get("/api/calendar")["calendars"][0]["color"] == "c1"
    school = api.save_calendar({"name": "School"})
    assert school["color"] == "c2", "the next free palette slot, not a fixed hex"
    picked = api.save_calendar({"id": school["id"], "revision": school["revision"], "color": "c5"})
    assert picked["color"] == "c5"
    assert api.save_calendar({"name": "Legacy", "color": "#123abc"})["color"] == "#123abc"
    for bad in ("c0", "c7", "var(--c1)", "red", "#12345", None):
        api.refused("/api/calendar/calendars", {"name": "Bad", "color": bad}, match="palette")
    default = conn.execute("SELECT dflt_value FROM pragma_table_info('calendar_calendars') WHERE name='color'").fetchone()[0]
    assert default == "'c1'"


def test_events_tasks_and_calendars_link_study_subjects_or_pick_a_palette_slot(api, workspace):
    from kitty.core import study
    conn = workspace.conn
    physics = study_subject(conn)
    linked = api.event(subject_id=physics)
    picked = api.event(title="Lunch", color="c3")
    plain = api.event(title="Walk")
    shown = {e["id"]: e for e in api.occurrences()}
    assert (shown[linked["id"]]["subject_id"], shown[linked["id"]]["color"]) == (physics, None)
    assert (shown[picked["id"]]["subject_id"], shown[picked["id"]]["color"]) == (None, "c3")
    assert (shown[plain["id"]]["subject_id"], shown[plain["id"]]["color"]) == (None, None)
    moved = api.edit(linked["id"], {"revision": linked["revision"], "changes": {"location": "Lab"}})
    assert moved["subject_id"] == physics, "other edits keep the subject link"
    unlinked = api.edit(linked["id"], {"revision": moved["revision"], "changes": {"subject_id": None, "color": "c6"}})
    assert (unlinked["subject_id"], unlinked["color"]) == (None, "c6")
    lab = api.save_calendar({"name": "Physics lab", "subject_id": physics})
    assert lab["subject_id"] == physics
    task = api.task({"title": "Problem set", "subject_id": physics})
    assert task["subject_id"] == physics
    base = {"title": "Study block", "start": "2026-10-26T09:00:00-05:00", "end": "2026-10-26T10:00:00-05:00"}
    for bad in (999, "1", True, 1.0):
        api.refused("/api/calendar/events", {**base, "subject_id": bad}, match="subject")
        api.refused("/api/calendar/tasks", {"title": "Bad", "subject_id": bad}, match="subject")
        api.refused("/api/calendar/calendars", {"name": "Bad", "subject_id": bad}, match="subject")
    api.refused("/api/calendar/events", {**base, "color": "#6d5dfc80"}, match="palette")
    snapshot = api.get("/api/calendar", start="2026-10-01T00:00:00Z", end="2026-11-01T00:00:00Z")
    assert snapshot["subjects"] == [{"id": physics, "name": "Physics", "color": "#31a2bc"}], "Study’s own subject colours"
    revision = conn.execute("SELECT revision FROM study_subjects WHERE id=?", (physics,)).fetchone()[0]
    linked_again = api.event(title="Revision", subject_id=physics)
    study.save_entity(conn, "subjects", {"id": physics, "revision": revision, "action": "delete"})
    assert api.get(f"/api/calendar/events/{linked_again['id']}")["subject_id"] is None
    assert next(c for c in api.get("/api/calendar")["calendars"] if c["id"] == lab["id"])["subject_id"] is None
    assert [t["subject_id"] for t in api.tasks()] == [None]


def test_routes_refuse_like_the_python_handlers(api):
    api.refused("/api/calendar", status=400, match="Unknown calendar query", other="1")
    api.refused("/api/calendar/reminders", status=400, match="does not accept query", x="1")
    api.refused("/api/calendar/undo", {"id": "1"}, status=400, match="Undo ID must be an integer")
    api.refused("/api/calendar/import-jobs", {"x": 1}, status=400, match="empty object")
    api.refused("/api/calendar/import", {"items": []}, status=400, match="confirm")
    api.refused("/api/calendar/nowhere", {}, status=404, match="API route not found")
    api.refused("/api/calendar/events/missing", status=404, match="Event not found")
    api.refused("/api/calendar", status=400, match="Timezone must be an IANA zone", timezone="Mars/Base")
    api.refused("/api/calendar", status=400, match="two years", start="2026-01-01T00:00:00Z", end="2029-01-01T00:00:00Z")


def test_mutations_are_recorded_as_record_events(api, workspace):
    """The rows `WebApplication.mutate` appended for the same requests (kitty.core.record_events)."""
    created = api.event()
    edited = api.edit(created["id"], {"revision": created["revision"], "changes": {"title": "Renamed"}})
    api.post("/api/calendar/availability", {"start": "2026-10-26T13:00:00Z", "end": "2026-10-26T18:00:00Z"})
    api.post("/api/calendar/time", {"value": "2030-01-01T09:00", "timezone": "UTC"})
    api.refused("/api/calendar/events", {"title": ""}, status=400)
    task = api.task({"title": "Recorded"})
    renamed = api.task({"id": task["id"], "revision": task["revision"], "title": "Recorded twice"})
    api.undo(renamed["undo_id"])
    api.undo(edited["undo_id"])
    cal = api.save_calendar({"name": "Short-lived"})
    api.post("/api/calendar/calendars/delete", {"id": cal["id"], "revision": cal["revision"]})
    api.post("/api/calendar/import-jobs", {})
    rows = [tuple(row) for row in workspace.conn.execute("SELECT section, entity, entity_id, action, route FROM record_events ORDER BY seq")]
    assert rows == [
        ("calendar", "event", created["id"], "create", "/api/calendar/events"),
        ("calendar", "event", created["id"], "save", f"/api/calendar/events/{created['id']}"),
        ("calendar", "task", task["id"], "create", "/api/calendar/tasks"),
        ("calendar", "task", task["id"], "save", "/api/calendar/tasks"),
        ("calendar", "task", task["id"], "undo", "/api/calendar/undo"),
        ("calendar", "event", created["id"], "undo", "/api/calendar/undo"),
        ("calendar", "calendar", cal["id"], "create", "/api/calendar/calendars"),
        ("calendar", "calendar", cal["id"], "delete", "/api/calendar/calendars/delete"),
        ("calendar", "event", None, "import", "/api/calendar/import-jobs"),
    ]
    stamps = [row[0] for row in workspace.conn.execute("SELECT at FROM record_events")]
    assert all(datetime.fromisoformat(at).isoformat(timespec="milliseconds") == at for at in stamps)


def test_calendar_routes_need_the_workspace_module(workspace):
    from kitty.core.builds import store

    store.edit(workspace.settings.root, lambda build: build.update(modules=["applyai"]))
    with front_site(workspace.settings) as running:
        status, body = running.request("/api/calendar")
        assert status == 403 and "'workspace' module is disabled" in body["error"]
        status, body = running.request("/api/calendar/events", {"title": "x"})
        assert status == 403


# ------------------------------------------------------------------------------ parity

#: Rules, zones and starts whose expansion the front must compute exactly as
#: `kitty.core.calendar.occurrences` (dateutil's rrule and zoneinfo) does.
RULES = [
    ("FREQ=DAILY;COUNT=10", "2026-10-30T01:30:00-05:00", "America/Chicago", False),
    ("FREQ=DAILY;INTERVAL=3", "2026-10-01T09:00:00-05:00", "America/Chicago", False),
    ("FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=20", "2026-10-05T18:00:00+01:00", "Europe/London", False),
    ("FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH;WKST=SU", "2026-10-06T07:15:00+02:00", "Europe/Berlin", False),
    ("FREQ=MONTHLY;BYDAY=-1FR", "2026-10-30T12:00:00Z", "UTC", False),
    ("FREQ=MONTHLY;BYDAY=2TU,4TU", "2026-10-13T08:00:00-04:00", "America/New_York", False),
    ("FREQ=MONTHLY;BYMONTHDAY=-1", "2026-10-31", "Australia/Sydney", True),
    ("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1", "2026-10-30T17:00:00+11:00", "Australia/Sydney", False),
    ("FREQ=MONTHLY;COUNT=6", "2026-08-31T10:00:00-07:00", "America/Los_Angeles", False),
    ("FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", "2026-03-08T02:30:00-08:00", "America/Los_Angeles", False),
    ("FREQ=YEARLY;BYWEEKNO=1,53;BYDAY=MO", "2026-01-01", "UTC", True),
    ("FREQ=YEARLY;BYYEARDAY=1,100,-1", "2026-01-01T06:00:00Z", "UTC", False),
    ("FREQ=YEARLY;BYEASTER=0,-2", "2026-04-05", "Europe/Paris", True),
    ("FREQ=YEARLY;COUNT=3", "2024-02-29", "UTC", True),
    ("FREQ=WEEKLY;UNTIL=20261201", "2026-10-03", "America/Santiago", True),
    ("FREQ=DAILY;BYHOUR=9,17;BYMINUTE=0,30", "2026-10-30T09:00:00+05:30", "Asia/Kolkata", False),
    ("FREQ=DAILY;UNTIL=20261110T000000Z", "2026-11-01T01:30:00-06:00", "America/Chicago", False),
    ("FREQ=WEEKLY;COUNT=5", "2027-03-07T02:30:00-06:00", "America/Chicago", False),
]


def _strip(event):
    return {k: v for k, v in event.items() if k not in {"id", "updated_at", "revision", "undo_id"}}


@pytest.mark.parametrize(("rule", "start", "zone", "all_day"), RULES)
def test_expansion_and_normalization_match_the_python_reference(api, workspace, tmp_path, rule, start, zone, all_day):
    from kitty.core import calendar
    from kitty.db import connect
    from kitty.db.migrations import migrate

    if all_day:
        end = (datetime.fromisoformat(start) + timedelta(days=1)).date().isoformat()
    else:
        end = (datetime.fromisoformat(start) + timedelta(minutes=45)).isoformat()
    body = {"title": "Parity", "start": start, "end": end, "timezone": zone, "all_day": all_day, "rrule": rule,
            "exdates": [], "reminders": [5, 60]}
    made = api.post("/api/calendar/events", body)
    reference = connect(tmp_path / "reference.db")
    migrate(reference)
    expected = calendar.create_event(reference, body)
    assert _strip(made) == _strip(expected)
    stored = workspace.conn.execute("SELECT data FROM calendar_events WHERE id=?", (made["id"],)).fetchone()[0]
    assert stored == reference.execute("SELECT data FROM calendar_events").fetchone()[0], "the same json.dumps text"
    windows = (("2026-01-01T00:00:00Z", "2027-06-01T00:00:00Z"), ("2027-02-01T00:00:00Z", "2028-03-01T00:00:00Z"))
    seen = []
    for lower, upper in windows:
        front = api.occurrences(lower, upper)
        assert front == plain(calendar.occurrences(workspace.conn, lower, upper))
        seen += front
    assert seen, "the rule has occurrences in the compared range"
    exdate = seen[min(1, len(seen) - 1)]["occurrence"]
    edited = api.edit(made["id"], {"revision": made["revision"], "changes": {"exdates": [exdate]}})
    assert exdate in edited["exdates"]
    for lower, upper in windows:
        front = api.occurrences(lower, upper)
        assert front == plain(calendar.occurrences(workspace.conn, lower, upper))
        assert exdate not in [event["occurrence"] for event in front]


@pytest.mark.parametrize("value", [
    "2026-11-01T01:30", "2026-11-01 01:30:15", "20261101T0130", "2026-11-01T01:30:00.25", "2026-W44-7T01:30",
    "2026-11-01", "2026-11-01T01", "2026-11-01T01:30:00+05:00", "2026-11-01T01:30Z", "2026-11-01X01:30", "2026-13-01T01:30",
    "2026-11-01T1:30", "2026-11-01T01:30:", "2026-11-01T24:00", "not a date", "2026-11-01T01:30:00,123456789",
])
@pytest.mark.parametrize("fold", [0, 1])
def test_wall_time_resolution_matches_python_datetime(api, value, fold):
    from zoneinfo import ZoneInfo

    from dateutil.tz import datetime_exists

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        api.refused("/api/calendar/time", {"value": value, "timezone": "America/Chicago", "fold": fold}, match="Invalid local date/time")
        return
    if parsed.tzinfo is not None:
        expected = parsed.astimezone(ZoneInfo("America/Chicago")).isoformat()
    else:
        local = parsed.replace(tzinfo=ZoneInfo("America/Chicago"), fold=fold)
        if not datetime_exists(local):
            api.refused("/api/calendar/time", {"value": value, "timezone": "America/Chicago", "fold": fold}, match="does not exist")
            return
        expected = local.isoformat()
    assert api.post("/api/calendar/time", {"value": value, "timezone": "America/Chicago", "fold": fold})["value"] == expected


# ------------------------------------------------------------------------------ parity with the Python core

def plain(value):
    """A Python answer as the front sends it."""
    return json.loads(json.dumps(value))


def reference(conn, tmp_path):
    """A copy of the workspace database for the Python functions: the same rows and ids."""
    import sqlite3

    from kitty.db import connect

    path = tmp_path / "reference.db"
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as copy:
        conn.backup(copy)
    return connect(path)


def masked(value):
    """An answer or a stored snapshot without what differs between two runs: clock values, and
    the random id of a series a split makes."""
    if isinstance(value, dict):
        return {key: "*" if key in {"updated_at", "created_at"} or (key in {"id", "event_id"} and item in masked.ids) else masked(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [masked(item) for item in value]
    if isinstance(value, str) and value[:1] in "{[":
        try:
            return masked(json.loads(value))
        except ValueError:
            return value
    return value


masked.ids = set()


def stored(conn):
    """Every calendar row, in an order both sides share."""
    tables = {
        "calendar_events": "SELECT * FROM calendar_events ORDER BY start, title, revision",
        "calendar_exceptions": "SELECT * FROM calendar_exceptions ORDER BY occurrence, data",
        "calendar_history": "SELECT entity, before_json, after_json, undone FROM calendar_history ORDER BY id",
        "calendar_tasks": "SELECT * FROM calendar_tasks ORDER BY json_extract(data, '$.due'), json_extract(data, '$.status')",
        "calendar_calendars": "SELECT * FROM calendar_calendars ORDER BY name",
    }
    return {table: masked([dict(row) for row in conn.execute(sql)]) for table, sql in tables.items()}


def both(api, python, path, body, call):
    """One mutation sent to the front and run by the Python function on the reference copy:
    the same answer, or the same refusal."""
    from kitty.web_server import APIError

    status, answer = api.call(path, body)
    try:
        expected = plain(call(python))
    except APIError as refusal:
        assert (status, answer) == (refusal.status, {"error": str(refusal)}), (path, body)
        return None
    assert status == 200, (path, body, answer)
    for made, wanted in ((answer, expected), (answer.get("future_event"), expected.get("future_event")),
                         (answer.get("next_task"), expected.get("next_task"))):
        if isinstance(made, dict) and isinstance(wanted, dict) and made.get("id") != wanted.get("id"):
            masked.ids |= {made["id"], wanted["id"]}
    assert masked(answer) == masked(expected), (path, body)
    return answer


WINDOW = ("2026-10-01T00:00:00Z", "2027-02-01T00:00:00Z")

#: Edits applied one after another to a weekly series with an exclusion: answered or refused
#: exactly as `kitty.core.calendar.edit_event` answers or refuses them.
EDITS = [
    {"scope": "this", "occurrence": "2026-11-16T09:00:00-06:00",
     "changes": {"title": "Special", "start": "2026-11-17T11:00:00-06:00", "end": "2026-11-17T12:30:00-06:00"}},
    {"scope": "this", "occurrence": "2026-11-09T09:00:00-06:00", "changes": {"title": "Excluded"}},
    {"scope": "this", "occurrence": "2026-11-23T09:00:00-06:00", "changes": {"rrule": "FREQ=DAILY"}},
    {"scope": "all", "changes": {"start": "2026-10-26T10:00:00-05:00", "end": "2026-10-26T11:15:00-05:00", "location": "Room 2"}},
    {"scope": "this", "occurrence": "2026-11-02T10:00:00-06:00", "delete": True},
    {"scope": "everything"},
    {"scope": "all", "changes": {"source_key": "other"}},
    {"scope": "all", "changes": {"rrule": "FREQ=WEEKLY;COUNT=2"}},
    {"scope": "future", "occurrence": "2026-11-23T10:00:00-06:00", "changes": {"description": "New plan", "reminders": [5]}},
    {"scope": "future", "occurrence": "2026-10-26T10:00:00-05:00", "changes": {"timezone": "America/New_York"}},
    {"scope": "all", "changes": {"all_day": True, "start": "2026-10-26", "end": "2026-10-27"}},
    {"scope": "future", "occurrence": "2026-11-09", "delete": True},
    {"scope": "all", "delete": True},
]


def test_edit_sequences_and_their_undo_match_the_python_reference(api, workspace, tmp_path):
    from kitty.core import calendar

    masked.ids = set()
    base = api.event(rrule="FREQ=WEEKLY;COUNT=8", exdates=["2026-11-09T09:00:00-06:00"], reminders=[15],
                     attendees=[{"email": "guest@example.invalid", "name": " Guest "}], description="Séance ☕")
    python = reference(workspace.conn, tmp_path)
    ident, undo_ids = base["id"], []
    for step in EDITS:
        revision = api.get(f"/api/calendar/events/{ident}")["revision"]
        for body in ({"revision": revision - 1, **step}, {"revision": revision, **step}):
            answer = both(api, python, f"/api/calendar/events/{ident}", body, lambda conn: calendar.edit_event(conn, ident, body))
            if answer is not None:
                undo_ids.append(answer["undo_id"])
            assert stored(workspace.conn) == stored(python), step
            assert masked(api.occurrences(*WINDOW)) == masked(plain(calendar.occurrences(python, *WINDOW))), step
    assert len(undo_ids) >= 6
    for undo_id in reversed(undo_ids):
        both(api, python, "/api/calendar/undo", {"id": undo_id}, lambda conn: calendar.undo(conn, undo_id))
        assert stored(workspace.conn) == stored(python), undo_id
    both(api, python, "/api/calendar/undo", {"id": undo_ids[0]}, lambda conn: calendar.undo(conn, undo_ids[0]))
    assert masked(api.occurrences(*WINDOW)) == masked(plain(calendar.occurrences(python, *WINDOW)))


def test_task_and_calendar_saves_match_the_python_reference(api, workspace, tmp_path, monkeypatch):
    from kitty.core import calendar

    masked.ids = set()
    clock = "2026-10-14T17:00:00+00:00"
    monkeypatch.setattr(calendar, "now", lambda: clock)
    api.start(clock)
    physics = study_subject(workspace.conn)
    python = reference(workspace.conn, tmp_path)
    task = lambda body: both(api, python, "/api/calendar/tasks", body, lambda conn: calendar.save_task(conn, body))
    made = task({"title": " Review ", "due": "2026-08-31", "timezone": "America/Chicago", "repeat_frequency": "MONTHLY",
                 "repeat_interval": 2, "tags": ["a", "", "a", " b "], "priority": "urgent", "subject_id": physics})
    # A new task's id is random on each side: the two rows are told apart by what they hold.
    twin = python.execute("SELECT id FROM calendar_tasks").fetchone()[0]
    masked.ids |= {made["id"], twin}
    python.execute("UPDATE calendar_tasks SET id=?, task_series_id=? WHERE id=?", (made["id"], made["id"], twin))
    python.execute("UPDATE calendar_history SET entity_id=?, after_json=replace(after_json, ?, ?)", (made["id"], twin, made["id"]))
    done = task({"id": made["id"], "revision": made["revision"], "status": "done"})
    assert done["next_task"]["due"] == "2026-10-31"
    for body in ({"id": made["id"], "revision": done["revision"], "status": "todo"},
                 {"id": made["id"], "revision": done["revision"], "repeat_interval": 3},
                 {"id": made["id"], "revision": 1, "title": "Stale"},
                 {"id": "missing", "revision": 1}, {"title": "x", "extra": 1}, {"title": "x", "delete": 1},
                 {"title": "x", "due": "2026-10-14T09:00:00"}, {"title": "x", "tags": "a"}, {"title": "x", "calendar_id": "nowhere"},
                 {"title": "x" * 501}, {"title": "x", "priority": "soon"}, {"title": "x", "repeat_frequency": "DAILY"},
                 {"id": made["id"], "revision": done["revision"], "title": "Reviewed", "delete": True}):
        task(body)
        assert stored(workspace.conn) == stored(python), body
    saved = lambda body: both(api, python, "/api/calendar/calendars", body, lambda conn: calendar.save_calendar(conn, body))
    for body in ({"id": "personal", "revision": 1, "name": " Mine ", "color": "c4", "timezone": "Europe/Paris", "visible": False, "subject_id": physics},
                 {"id": "personal", "revision": 1, "name": "Stale"}, {"id": "personal", "revision": 2, "name": ""},
                 {"id": "personal", "revision": 2, "visible": 1}, {"id": "personal", "revision": 2, "timezone": "Paris"},
                 {"id": "personal", "revision": 2, "color": "blue"}, {"id": "nowhere", "revision": 1}, {"name": "x" * 201},
                 {"id": "personal", "revision": 2.0, "visible": True}):
        saved(body)
        assert stored(workspace.conn) == stored(python), body


def python_snapshot(conn, start, end, tz="UTC", *, search="", calendar_id=None, hidden=False, applyai=True):
    """What the Python handler of `GET /api/calendar` composed, but for the availability keys."""
    from kitty.core import calendar, study

    events = calendar.occurrences(conn, start, end, search=search, calendar_id=calendar_id, include_hidden=hidden, applyai=applyai)
    cals = [{k: v for k, v in c.items() if k not in {"sync_token", "google_etag"}} for c in calendar.calendars(conn)]
    return plain({"calendars": cals, "events": events, "tasks": calendar.tasks(conn, search), "subjects": study.subject_colors(conn),
                  "activity": calendar.activity(conn, start, end, tz, applyai=applyai)})


def seed_careers(conn):
    """Synthetic ApplyAI rows with every date the calendar copies, and an opportunity deadline."""
    from kitty.core import applications, opportunities, reminders

    job, _ = jobs.upsert_job(conn, company="Synthetic Co", title="Role", url="https://example.invalid/r",
                             source="manual", source_job_id="cal-1", deadline_at="2026-11-09")
    other, _ = jobs.upsert_job(conn, company="Other Co", title="Ünïcode role", url="https://example.invalid/o",
                               source="manual", source_job_id="cal-2", deadline_at="2026-11-12T17:00:00-05:00")
    application = applications.record_application(conn, job.id, applied_at="2026-10-20T12:00:00+00:00")
    conn.execute("UPDATE applications SET next_action_at=?, next_action_note=? WHERE id=?", ("2026-10-27", "Send thanks", application.id))
    applications.set_status(conn, application.id, "interview", occurred_at="2026-10-22T15:30:00+00:00")
    reminders.create_reminder(conn, kind="followup", due_at="2026-10-29", note=None, job_id=other.id)
    reminders.create_reminder(conn, kind="custom", due_at="not a date", note="Unreadable date")
    opportunities.create(conn, {"title": "Hackathon", "deadline": "2026-11-20", "timezone": "America/Chicago",
                                "url": "https://example.invalid/hack", "description": "Bring a laptop"})
    return job


SOURCE_ROWS = ("SELECT title,start,end,timezone,all_day,data,source_key,source_fingerprint,source_overridden,deleted,revision"
               " FROM calendar_events ORDER BY source_key, title")


@pytest.mark.parametrize("modules", [("applyai", "workspace"), ("workspace",)])
def test_snapshot_reminders_and_source_dates_match_the_python_reference(api, workspace, tmp_path, modules):
    from kitty.core import calendar
    from kitty.core.builds import store

    conn = workspace.conn
    applyai = "applyai" in modules
    clock = "2026-10-26T14:05:00+00:00"
    store.edit(workspace.settings.root, lambda build: build.update(modules=list(modules)))
    api.start(clock)
    job = seed_careers(conn)
    physics = study_subject(conn)
    school = api.save_calendar({"name": "School", "subject_id": physics})
    hidden = api.save_calendar({"name": "Hidden", "visible": False})
    api.event(rrule="FREQ=DAILY;COUNT=5", reminders=[10, 60], calendar_id=school["id"], location="Graph theory room")
    api.event(title="All day", start="2026-10-27", end="2026-10-29", all_day=True, busy=False, reminders=[0, 1440])
    api.event(title="Out of sight", calendar_id=hidden["id"], start="2026-10-26T09:30:00-05:00", end="2026-10-26T11:00:00-05:00")
    done = api.task({"title": "Graph homework", "due": "2026-10-25", "tags": ["graph"]})
    api.task({"id": done["id"], "revision": done["revision"], "status": "done"})
    api.task({"title": "Later", "project": "Graph project"})
    # A copy from ApplyAI data that predates turning the module off.
    copied = calendar.create_event(conn, {"title": "Deadline · Kept", "start": "2026-10-26T08:50:00-05:00", "end": "2026-10-26T09:50:00-05:00",
                                          "timezone": "America/Chicago", "source_key": "job:7:deadline", "reminders": [15]})

    python = reference(conn, tmp_path)
    for attempt in range(3):
        if attempt == 1:
            conn.execute("UPDATE jobs SET deadline_at='2026-11-10' WHERE id=?", (job.id,))
            python.execute("UPDATE jobs SET deadline_at='2026-11-10' WHERE id=?", (job.id,))
        if attempt == 2:
            for side in (conn, python):
                side.execute("UPDATE jobs SET deadline_at=NULL WHERE id=?", (job.id,))
                side.execute("UPDATE opportunities SET deadline=NULL")
        assert api.post("/api/calendar/import-jobs", {}) == plain(calendar.import_jobs(python, applyai=applyai)), attempt
        assert [tuple(row) for row in conn.execute(SOURCE_ROWS)] == [tuple(row) for row in python.execute(SOURCE_ROWS)], attempt

    for query in ({}, {"start": "2026-10-20T00:00:00Z", "end": "2026-11-25T00:00:00Z", "timezone": "America/Chicago"},
                  {"search": "GRAPH"}, {"calendar_id": school["id"]}, {"hidden": "1", "timezone": "Asia/Kolkata"},
                  {"calendar_id": hidden["id"], "hidden": "1"}, {"calendar_id": ""}):
        at = datetime.fromisoformat(clock)
        start = query.get("start", (at - timedelta(days=7)).isoformat())
        end = query.get("end", (at + timedelta(days=35)).isoformat())
        answer = api.get("/api/calendar", **query)
        expected = python_snapshot(conn, start, end, query.get("timezone", "UTC"), search=query.get("search", ""),
                                   calendar_id=query.get("calendar_id"), hidden=query.get("hidden") == "1", applyai=applyai)
        assert {key: answer[key] for key in expected} == expected, query
        assert set(answer) == set(expected) | {"busy", "free", "conflicts"}
        assert all("sync_token" not in cal and "google_etag" not in cal for cal in answer["calendars"])
    assert ("Deadline · Kept" in {event["title"] for event in api.occurrences(*WINDOW)}) == applyai

    due = plain(calendar.due_reminders(conn, at=datetime.fromisoformat(clock), applyai=applyai))
    assert api.reminders() == due and len(due) >= 2
    assert any(item["event_id"] == copied["id"] for item in due) == applyai
    first = due[0]
    api.post("/api/calendar/reminders/dismiss", {key: first[key] for key in ("event_id", "occurrence", "minutes")})
    assert api.reminders() == plain(calendar.due_reminders(conn, at=datetime.fromisoformat(clock), applyai=applyai)) == due[1:]

    # A copy of ApplyAI data is not found while that module is off, as `calendar.get_event` refuses it.
    status, answer = api.call(f"/api/calendar/events/{copied['id']}")
    if applyai:
        assert (status, answer) == (200, plain(calendar.get_event(conn, copied["id"])))
    else:
        with pytest.raises(Exception) as hidden_copy:
            calendar.get_event(conn, copied["id"], applyai=False)
        assert (status, answer) == (hidden_copy.value.status, {"error": str(hidden_copy.value)}) == (404, {"error": "Event not found"})
        api.refused(f"/api/calendar/events/{copied['id']}", {"revision": copied["revision"], "changes": {"title": "Mine"}}, status=404)
        api.refused("/api/calendar/reminders/dismiss", {"event_id": copied["id"], "occurrence": copied["start"], "minutes": 15}, status=404)
        assert calendar.get_event(conn, copied["id"])["title"] == "Deadline · Kept", "kept, only hidden"


def test_outlook_calendars_are_read_only_with_pythons_refusals(api, workspace):
    """`calendar_remote` (kitty.core.connect.microsoft): no create, edit, delete, move or import."""
    from kitty.core import calendar
    from kitty.web_server import APIError

    masked.ids = set()
    conn = workspace.conn
    outlook = api.save_calendar({"name": "Calendar (Outlook)"})
    mine = api.event()
    theirs = api.event(calendar_id=outlook["id"], title="Design review", rrule="FREQ=DAILY;COUNT=3")
    conn.execute("INSERT INTO calendar_remote(calendar_id,provider,remote_id) VALUES(?,?,?)", (outlook["id"], "microsoft", "remote-1"))
    new = {"title": "Local", "start": theirs["start"], "end": theirs["end"], "calendar_id": outlook["id"]}
    edits = [(theirs, {"changes": {"title": "Mine"}}), (theirs, {"delete": True}),
             (theirs, {"scope": "this", "occurrence": theirs["start"], "changes": {"title": "Once"}}),
             (theirs, {"changes": {"calendar_id": "personal"}}), (mine, {"changes": {"calendar_id": outlook["id"]}})]
    before = stored(conn)
    with pytest.raises(APIError) as refused:
        calendar.create_event(conn, new)
    read_only = (409, {"error": str(refused.value)})
    assert refused.value.status == 409 and "read-only" in str(refused.value)
    assert api.call("/api/calendar/events", new) == read_only
    for event, change in edits:
        body = {"revision": event["revision"], **change}
        with pytest.raises(APIError) as refused:
            calendar.edit_event(conn, event["id"], body)
        assert api.call(f"/api/calendar/events/{event['id']}", body) == (refused.value.status, {"error": str(refused.value)}) == read_only
    assert api.call("/api/calendar/calendars/delete", {"id": outlook["id"], "revision": outlook["revision"]}) == read_only
    assert api.call("/api/calendar/import", {"confirmed": True, "items": [{"event": {**new, "timezone": "UTC"}}]}) == read_only
    assert api.call("/api/calendar/import", {"confirmed": True, "items": [
        {"event": {"title": "Fine", "start": "2026-11-02", "end": "2026-11-03", "all_day": True, "timezone": "UTC"}},
        {"event": {**new, "timezone": "UTC"}}]}) == read_only, "nothing of a refused import is written"
    assert stored(conn) == before
    # Reading is unchanged, and a stale revision is refused before anything else, as in Python.
    assert [event["title"] for event in api.occurrences(calendar_id=outlook["id"])] == ["Design review"] * 3
    assert api.get(f"/api/calendar/events/{theirs['id']}")["title"] == "Design review"
    api.refused(f"/api/calendar/events/{theirs['id']}", {"revision": 99, "changes": {"title": "Stale"}}, status=409, match="Event changed")
    # The calendar row itself is rika!'s: hiding it is allowed, as `save_calendar` allows it.
    assert api.save_calendar({"id": outlook["id"], "revision": outlook["revision"], "visible": False})["visible"] == 0
    # Once the account is disconnected the calendar is an ordinary one again.
    conn.execute("DELETE FROM calendar_remote")
    assert api.edit(theirs["id"], {"revision": theirs["revision"], "changes": {"title": "Mine"}})["title"] == "Mine"
