"""The goal routes answered by rika-server (server/src/goals) against `kitty.core.goals`
on the same workspace: the same answers for the reads, and after each write through the
front the same goal as Python reads back, with rows Python would have written."""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from terminal_support import front_site
from test_goals import make_job, make_mail, make_opportunity, make_session, make_topic

from kitty.core import applications as applications_core
from kitty.core import calendar, goals, notes
from kitty.db import connect

MODULES = ("applyai", "workspace")


@pytest.fixture
def settings(workspace):
    workspace.conn.close()
    return workspace.settings


def plain(value):
    """What the wire carries: JSON types only."""
    return json.loads(json.dumps(value))


def seed(conn):
    """Goals with every kind of link and every auto source, linked and unlinked."""
    now = datetime.now(timezone.utc)
    linked = goals.create(conn, {"title": "Land an internship", "why": "Real work", "target_date": (now + timedelta(days=40)).date().isoformat(),
                                 "measures": [{"label": "Applications", "target": 3, "auto": "applications"},
                                              {"label": "Minutes", "target": 90, "unit": "min", "auto": "study_minutes"},
                                              {"label": "Tasks", "target": 4, "auto": "tasks_done"},
                                              {"label": "Topics", "target": 3, "auto": "math_mastered"},
                                              {"label": "Interviews", "target": 7, "current": 2.5}]})
    free = goals.create(conn, {"title": "Everything since", "measures": [
        {"label": "Applications", "target": 2, "auto": "applications"}, {"label": "Minutes", "target": 45, "auto": "study_minutes"},
        {"label": "Tasks", "target": 3, "auto": "tasks_done"}, {"label": "Topics", "target": 6, "auto": "math_mastered"}]})
    goals.create(conn, {"title": "No measures"})
    done = goals.create(conn, {"title": "Done already", "measures": [{"label": "Pages", "target": 3, "current": 1}]})
    goals.update(conn, done["id"], {"revision": 1, "status": "done"})

    job = make_job(conn)
    applications_core.record_application(conn, job)
    applications_core.record_application(conn, make_job(conn, title="Old one"), applied_at=(now - timedelta(days=9)).isoformat().replace("+00:00", "Z"))
    applications_core.record_application(conn, make_job(conn, title="New one"), applied_at=(now + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"))
    page = notes.create_page(conn, notes.create_notebook(conn, "Synthetic")["id"], "Interview prep")
    task = calendar.save_task(conn, {"title": "Email the recruiter", "timezone": "UTC", "due": "2026-12-01T09:00:00+00:00"})
    finished = calendar.save_task(conn, {"title": "Done task", "timezone": "UTC"})
    calendar.save_task(conn, {"id": finished["id"], "revision": finished["revision"], "status": "done"})
    event = calendar.create_event(conn, {"title": "Mock interview", "start": "2026-11-05T10:00:00+00:00",
                                         "end": "2026-11-05T11:00:00+00:00", "timezone": "UTC"})
    all_day = calendar.create_event(conn, {"title": "Career fair", "start": "2026-11-06", "end": "2026-11-07", "all_day": True, "timezone": "UTC"})
    # 25 and 35 minutes: 12.5 and 17.5 would round to even, were they one session.
    sessions = [make_session(conn, minutes, now + timedelta(seconds=5)) for minutes in (25, 35)]
    odd = make_session(conn, 1, now + timedelta(seconds=5))
    conn.execute("UPDATE study_sessions SET duration_seconds=150 WHERE id=?", (odd,))  # 2.5 minutes
    make_topic(conn, "synthetic-limits", now + timedelta(seconds=5))
    make_topic(conn, "synthetic-old", now - timedelta(days=30))
    make_topic(conn, "synthetic-unmastered", None)
    opportunity = make_opportunity(conn, deadline="2026-12-24T00:00:00Z")
    mail = make_mail(conn)
    for kind, ref in (("job", str(job)), ("note", str(page["id"])), ("task", task["id"]), ("task", finished["id"]), ("event", event["id"]),
                      ("event", all_day["id"]), ("study_session", str(sessions[0])), ("study_session", str(odd)),
                      ("math_topic", "synthetic-limits"), ("math_topic", "synthetic-unmastered"), ("opportunity", str(opportunity)), ("mail", mail)):
        goals.link(conn, linked["id"], kind, ref)
    # A link whose target is gone still shows, as missing.
    conn.execute("INSERT INTO goal_links(goal_id,kind,ref,created_at) VALUES(?, 'task', 'gone', ?)", (linked["id"], goals.now()))
    conn.execute("INSERT INTO goal_matches(goal_id,kind,ref,title,detail,score,urgent,created_at) VALUES(?, 'event', ?, 'Career fair', 'soon', 0.61249, 1, ?)",
                 (free["id"], all_day["id"], goals.now()))
    conn.commit()
    return linked["id"], free["id"]


def test_the_front_reads_goals_as_the_python_core_does(settings):
    with closing(connect(settings.db_path)) as conn:
        linked, free = seed(conn)
        listing = plain(goals.listing(conn))
        details = {ident: plain(goals.get(conn, ident)) for ident in (linked, free)}
        searches = {(query, kind): plain(goals.linkable(conn, query, kind, MODULES))
                    for query, kind in (("", None), ("interview", None), ("%", None), ("synth", "math_topic"), ("Email", "task"), ("fair", "event"))}
    assert len(details[linked]["links"]) == 13 and details[free]["suggestions"][0]["score"] == 0.612
    with front_site(settings) as site:
        assert site.request("/api/goals") == (200, {"goals": listing})
        for ident, expected in details.items():
            assert site.request(f"/api/goals/{ident}") == (200, {"goal": expected})
        for (query, kind), expected in searches.items():
            target = f"/api/goals/linkable?q={quote(query)}" + (f"&kind={kind}" if kind else "")
            assert site.request(target) == (200, {"items": expected}), target
        assert site.request("/api/goals/linkable?kind=nope") == (400, {"error": "Search one of note, block, task, job, study_session, math_topic, opportunity, event, mail"})
        assert site.request("/api/goals/999") == (404, {"error": "Goal not found"})
        assert site.request("/api/goals?x=1")[0] == 400


def test_writes_through_the_front_leave_what_python_reads_and_would_have_written(settings):
    with front_site(settings) as site:
        def same(answer):
            """The front's answer is what Python reads from the same rows."""
            status, body = answer
            assert status == 200, body
            with closing(connect(settings.db_path)) as conn:
                assert body["goal"] == plain(goals.get(conn, body["goal"]["id"]))
                row = conn.execute("SELECT * FROM goals WHERE id=?", (body["goal"]["id"],)).fetchone()
                # Stored exactly as `json.dumps` writes it.
                assert row["measures_json"] == json.dumps(json.loads(row["measures_json"]))
                return body["goal"], row

        goal, row = same(site.request("/api/goals", {"title": "  Ship it  ", "why": " because ", "target_date": "2027-02-28", "measures": [
            {"label": " Pages ", "target": 12.5, "unit": "pp", "current": 3}, {"id": "apps", "label": "Applications", "target": 4, "auto": "applications", "current": 9}]}))
        assert (goal["title"], goal["why"], row["revision"]) == ("Ship it", "because", 1)
        assert json.loads(row["measures_json"])[1] == {"id": "apps", "label": "Applications", "target": 4, "unit": "", "current": 0, "auto": "applications"}
        assert row["created_at"] == row["updated_at"] and len(row["created_at"]) == 32 and row["created_at"].endswith("+00:00")
        manual = goal["measures"][0]["id"]
        assert manual.startswith("m") and len(manual) == 9
        goal, row = same(site.request(f"/api/goals/{goal['id']}/measures/{manual}", {"current": 6.25}))
        assert goal["progress"] == 0.25 and row["revision"] == 2
        # A known id keeps its current value when the edit leaves it out.
        goal, row = same(site.request(f"/api/goals/{goal['id']}", {"revision": 2, "status": "paused", "target_date": "", "measures": [
            {"id": manual, "label": "Pages", "target": 25}]}))
        assert (goal["measures"][0]["current"], goal["target_date"], goal["status"], row["revision"]) == (6.25, None, "paused", 3)
        with closing(connect(settings.db_path)) as conn:
            task = calendar.save_task(conn, {"title": "Outline", "timezone": "UTC"})
        goal, _ = same(site.request(f"/api/goals/{goal['id']}/links", {"kind": "task", "ref": task["id"]}))
        goal, _ = same(site.request(f"/api/goals/{goal['id']}/links", {"kind": "task", "ref": task["id"]}))
        assert goal["links_count"] == 1 and goal["revision"] == 3
        goal, _ = same(site.request(f"/api/goals/{goal['id']}/unlink", {"kind": "task", "ref": task["id"]}))
        assert goal["links"] == []

        refusals = [
            ("/api/goals", {"title": "x", "measures": [{"label": "a", "target": 0}]}, 400, "A measure's target must be above 0"),
            ("/api/goals", {"title": "x", "measures": [{"label": "a", "target": True}]}, 400, "A measure's target must be a finite number"),
            ("/api/goals", {"title": "x", "measures": [{"label": "a", "target": 1, "auto": "magic"}]}, 400,
             "A measure's auto source must be one of applications, study_minutes, tasks_done, math_mastered"),
            ("/api/goals", {"title": "x", "measures": [{"id": "a", "label": "a", "target": 1}, {"id": "a", "label": "b", "target": 1}]}, 400, "Two measures share an id"),
            ("/api/goals", {"title": "x", "target_date": "2027-02-30"}, 400, "The target date does not exist"),
            ("/api/goals", {"title": "x", "target_date": "soon"}, 400, "The target date must be YYYY-MM-DD"),
            ("/api/goals", {"title": "x" * 201}, 400, "The title must be 1–200 characters"),
            ("/api/goals", {"title": 7}, 400, "The title must be text"),
            (f"/api/goals/{goal['id']}", {"revision": 3, "status": "later"}, 400, "The status must be one of active, paused, done, dropped"),
            (f"/api/goals/{goal['id']}", {"revision": "3"}, 400, "Send the goal's revision"),
            (f"/api/goals/{goal['id']}", {"revision": 1}, 409, "This goal changed since you opened it; reload it and try again"),
            (f"/api/goals/{goal['id']}/links", {"kind": "page", "ref": "1"}, 400, "Links are to one of note, block, task, job, study_session, math_topic, opportunity, event, mail"),
            (f"/api/goals/{goal['id']}/links", {"kind": "task", "ref": ""}, 400, "A link needs the item's id as ref"),
            (f"/api/goals/{goal['id']}/links", {"kind": "task", "ref": "nope"}, 404, "No such task"),
            (f"/api/goals/{goal['id']}/measures/{manual}", {"current": -1}, 400, "The current value cannot be negative"),
            ("/api/goals/999/links", {"kind": "task", "ref": task["id"]}, 404, "Goal not found"),
            ("/api/goals/matches/5", {"decision": "maybe"}, 400, "Decide link or dismiss"),
        ]
        for path, body, status, message in refusals:
            assert site.request(path, body) == (status, {"error": message}), (path, body)
        with closing(connect(settings.db_path)) as conn:
            for path, body, status, message in refusals:
                if path == "/api/goals":
                    with pytest.raises(goals.GoalError, match=message.replace("(", r"\(").replace(")", r"\)")) as refused:
                        goals.create(conn, body)
                    assert refused.value.status == status
