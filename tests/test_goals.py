"""Goals (design Q14): revision-checked CRUD, validated measures, links with existence
checks, measures computed from linked data, progress and the linkable search."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from kitty.core import applications as applications_core
from kitty.core import calendar, goals, notes
from kitty.core import jobs as jobs_core
from kitty.core.goals import GoalError

MODULES = ("applyai", "workspace", "terminal")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def make_job(conn, title="Frontend Intern", company="Synthetic Labs", **extra):
    job, _ = jobs_core.upsert_job(conn, company=company, title=title, url=f"https://example.invalid/{uuid.uuid4().hex}",
                                  source_job_id=uuid.uuid4().hex, **extra)
    return job.id


def make_session(conn, minutes, ended: datetime):
    cursor = conn.execute(
        "INSERT INTO study_sessions(source,source_key,started_at,ended_at,timezone,duration_seconds,segments_json,created_at)"
        " VALUES('manual',?,?,?,'UTC',?,'[]',?)",
        (uuid.uuid4().hex, _iso(ended - timedelta(minutes=minutes)), _iso(ended), minutes * 60, _iso(ended)))
    return cursor.lastrowid


def make_topic(conn, ident, mastered: datetime | None):
    conn.execute("INSERT INTO math_topics(id,strand,title,position) VALUES(?, 'Synthetic', ?, 1000)", (ident, ident.title()))
    conn.execute("INSERT INTO math_mastery(topic_id,mastered_at,fsrs,updated_at) VALUES(?,?,?,?)",
                 (ident, _iso(mastered) if mastered else None, "{}" if mastered else None, _iso(datetime.now(timezone.utc))))


def make_mail(conn, subject="Interview availability", body="Could you do tomorrow?", received=None):
    ident = uuid.uuid4().hex
    stamp = _iso(received or datetime.now(timezone.utc))
    conn.execute("INSERT INTO rika_mail_messages(id,thread_id,sender,sender_email,subject,body,received_at,kind,needs_reply,"
                 "fetched_at) VALUES(?,?,'Synthetic Recruiter','recruiter@example.invalid',?,?,?,'scheduling',1,?)",
                 (ident, ident, subject, body, stamp, stamp))
    return ident


def make_opportunity(conn, title="Frontend hackathon", deadline=None):
    stamp = _iso(datetime.now(timezone.utc))
    cursor = conn.execute("INSERT INTO opportunities(identity,title,source,category,deadline,created_at,updated_at)"
                          " VALUES(?,?,'manual','event',?,?,?)", (uuid.uuid4().hex, title, deadline, stamp, stamp))
    return cursor.lastrowid


def test_create_validates_measures_and_reports_progress(workspace):
    conn = workspace.conn
    goal = goals.create(conn, {"title": "  Ship the portfolio  ", "why": "Show real work",
                               "target_date": (goals.today(conn) + timedelta(days=30)).isoformat(),
                               "measures": [{"label": "Pages", "target": 4, "current": 1, "unit": "pages"},
                                            {"label": "Deck", "target": 100, "unit": "%", "current": 150}]})
    assert goal["title"] == "Ship the portfolio" and goal["status"] == "active" and goal["revision"] == 1
    assert [(m["label"], m["current"], m["computed"], m["auto"]) for m in goal["measures"]] == [
        ("Pages", 1, False, None), ("Deck", 150, False, None)]
    assert all(m["id"] for m in goal["measures"])
    assert goal["progress"] == pytest.approx((0.25 + 1) / 2)
    assert goal["days_left"] == 30 and goal["links"] == [] and goal["suggestions"] == []
    assert goals.create(conn, {"title": "No measures"})["progress"] == 0
    for body in ({"title": ""}, {"title": "x" * 201}, {"why": "no title"}, {"title": "a", "colour": "red"},
                 {"title": "a", "target_date": "2026-02-30"}, {"title": "a", "target_date": "next week"},
                 {"title": "a", "measures": [{"label": "x" * 121, "target": 1}]},
                 {"title": "a", "measures": [{"label": "L", "target": 0}]},
                 {"title": "a", "measures": [{"label": "L", "target": float("inf")}]},
                 {"title": "a", "measures": [{"label": "L", "target": True}]},
                 {"title": "a", "measures": [{"label": "L", "target": 1, "unit": "u" * 17}]},
                 {"title": "a", "measures": [{"label": "L", "target": 1, "auto": "pushups"}]},
                 {"title": "a", "measures": [{"label": "L", "target": 1, "current": -1}]},
                 {"title": "a", "measures": [{"label": "L", "target": 1, "extra": 1}]},
                 {"title": "a", "measures": [{"label": "L", "target": 1, "id": "m1"}, {"label": "M", "target": 1, "id": "m1"}]},
                 {"title": "a", "measures": [{"label": "L", "target": 1}] * 11}):
        with pytest.raises(GoalError) as refused:
            goals.create(conn, body)
        assert refused.value.status == 400


def test_updates_are_revision_checked_and_keep_measure_ids(workspace):
    conn = workspace.conn
    goal = goals.create(conn, {"title": "Run a 10k", "measures": [{"label": "Runs", "target": 20, "current": 3}]})
    measure = goal["measures"][0]["id"]
    updated = goals.update(conn, goal["id"], {"revision": 1, "title": "Run a fast 10k",
                                              "measures": [{"id": measure, "label": "Runs", "target": 10},
                                                           {"label": "Long runs", "target": 4}]})
    assert updated["revision"] == 2 and updated["title"] == "Run a fast 10k"
    assert updated["measures"][0] == {"id": measure, "label": "Runs", "target": 10, "unit": "", "current": 3,
                                      "auto": None, "computed": False}
    assert updated["measures"][1]["id"] != measure
    with pytest.raises(GoalError) as stale:
        goals.update(conn, goal["id"], {"revision": 1, "why": "late"})
    assert stale.value.status == 409
    with pytest.raises(GoalError):
        goals.update(conn, goal["id"], {"revision": 2, "status": "someday"})
    done = goals.update(conn, goal["id"], {"revision": 2, "status": "done"})
    assert done["progress"] == 1 and done["status"] == "done"
    with pytest.raises(GoalError) as stale:
        goals.delete(conn, goal["id"], 2)
    assert stale.value.status == 409
    assert goals.delete(conn, goal["id"], 3) == goal["id"]
    with pytest.raises(GoalError) as gone:
        goals.get(conn, goal["id"])
    assert gone.value.status == 404


def test_listing_puts_active_goals_first_then_by_target_date(workspace):
    conn = workspace.conn
    later = goals.create(conn, {"title": "Later", "target_date": "2027-06-01"})
    undated = goals.create(conn, {"title": "Undated"})
    sooner = goals.create(conn, {"title": "Sooner", "target_date": "2027-01-01"})
    paused = goals.create(conn, {"title": "Paused", "target_date": "2026-12-01"})
    goals.update(conn, paused["id"], {"revision": 1, "status": "paused"})
    assert [goal["title"] for goal in goals.listing(conn)] == ["Sooner", "Later", "Undated", "Paused"]
    assert "links" not in goals.listing(conn)[0]
    assert later["id"] and undated["id"] and sooner["id"]


def test_manual_measures_are_set_and_auto_measures_refuse(workspace):
    conn = workspace.conn
    goal = goals.create(conn, {"title": "Interviews", "measures": [
        {"id": "talks", "label": "Interviews", "target": 5, "current": 2},
        {"id": "apps", "label": "Applications", "target": 10, "auto": "applications"}]})
    assert goals.set_measure(conn, goal["id"], "talks", 3)["measures"][0]["current"] == 3
    for measure, value, status in (("apps", 4, 409), ("nope", 1, 404), ("talks", -1, 400), ("talks", float("nan"), 400)):
        with pytest.raises(GoalError) as refused:
            goals.set_measure(conn, goal["id"], measure, value)
        assert refused.value.status == status
    with pytest.raises(GoalError) as changed:
        goals.set_measure(conn, goal["id"], "talks", 2, expected=9)
    assert changed.value.status == 409


def test_links_check_existence_resolve_titles_and_show_deleted_targets(workspace):
    conn = workspace.conn
    goal = goals.create(conn, {"title": "Everything"})
    page = notes.create_page(conn, notes.create_notebook(conn, "Synthetic")["id"], "Interview prep")
    block = str(uuid.uuid4())
    from kitty.core import note_blocks

    # Outline items are blocks (note_blocks is frozen since the block migration).
    note_blocks.save_blocks(conn, page["id"], page["revision"], [
        {"id": block, "parent_id": None, "text": "Practise STAR answers\nmore", "ordering": 0, "collapsed": False}])
    task = calendar.save_task(conn, {"title": "Email the recruiter", "timezone": "UTC"})
    event = calendar.create_event(conn, {"title": "Mock interview", "start": "2026-11-05T10:00:00+00:00",
                                         "end": "2026-11-05T11:00:00+00:00", "timezone": "UTC"})
    refs = {"note": str(page["id"]), "block": block, "task": task["id"], "event": event["id"],
            "job": str(make_job(conn)), "study_session": str(make_session(conn, 45, datetime.now(timezone.utc))),
            "math_topic": "synthetic-limits", "opportunity": str(make_opportunity(conn)), "mail": make_mail(conn)}
    make_topic(conn, "synthetic-limits", None)
    for kind, ref in refs.items():
        goal = goals.link(conn, goal["id"], kind, ref)
    goals.link(conn, goal["id"], "note", refs["note"])  # twice is a no-op
    links = {link["kind"]: link for link in goals.get(conn, goal["id"])["links"]}
    assert set(links) == set(refs) and goal["links_count"] == 9
    assert links["note"] == {"kind": "note", "ref": refs["note"], "title": "Interview prep", "detail": "Page in Synthetic",
                             "view": "notes", "missing": False}
    assert links["block"]["title"] == "Practise STAR answers" and links["task"]["title"] == "Email the recruiter"
    assert links["job"]["detail"] == "Synthetic Labs" and links["study_session"]["title"] == "Study · 45 min"
    assert links["mail"]["view"] is None and links["math_topic"]["view"] == "math"
    for kind, ref in (("job", "999999"), ("note", "abc"), ("task", "missing"), ("mail", "0" * 32)):
        with pytest.raises(GoalError) as missing:
            goals.link(conn, goal["id"], kind, ref)
        assert missing.value.status == 404
    with pytest.raises(GoalError):
        goals.link(conn, goal["id"], "planet", "1")
    with pytest.raises(GoalError) as off:
        goals.link(conn, goal["id"], "job", refs["job"], modules=("workspace",))
    assert off.value.status == 403
    conn.execute("DELETE FROM rika_mail_messages WHERE id=?", (refs["mail"],))
    gone = {link["kind"]: link for link in goals.get(conn, goal["id"])["links"]}["mail"]
    assert gone == {"kind": "mail", "ref": refs["mail"], "title": "Deleted email", "detail": "", "view": None, "missing": True}
    assert goals.unlink(conn, goal["id"], "mail", refs["mail"])["links_count"] == 8


def test_auto_measures_count_linked_items_else_everything_since_the_goal(workspace):
    conn = workspace.conn
    now = datetime.now(timezone.utc)
    before = now - timedelta(days=3)
    old_job, new_job, linked_job = make_job(conn, "A"), make_job(conn, "B"), make_job(conn, "C")
    applications_core.record_application(conn, old_job, applied_at=_iso(before).replace("+00:00", "Z"))
    make_session(conn, 30, before)
    make_topic(conn, "synthetic-old", before)
    goal = goals.create(conn, {"title": "Measured", "measures": [
        {"label": "Applications", "target": 4, "auto": "applications"},
        {"label": "Minutes", "target": 100, "auto": "study_minutes"},
        {"label": "Tasks", "target": 2, "auto": "tasks_done"},
        {"label": "Topics", "target": 2, "auto": "math_mastered"}]})
    applications_core.record_application(conn, new_job)
    make_session(conn, 40, now + timedelta(seconds=1))
    task = calendar.save_task(conn, {"title": "Done task", "timezone": "UTC"})
    calendar.save_task(conn, {"id": task["id"], "revision": task["revision"], "status": "done"})
    open_task = calendar.save_task(conn, {"title": "Open task", "timezone": "UTC"})
    make_topic(conn, "synthetic-new", now + timedelta(seconds=1))
    make_topic(conn, "synthetic-unmastered", None)
    current = lambda: [m["current"] for m in goals.get(conn, goal["id"])["measures"]]
    assert current() == [1, 40, 1, 1]
    assert all(m["computed"] for m in goals.get(conn, goal["id"])["measures"])
    assert goals.get(conn, goal["id"])["progress"] == pytest.approx((0.25 + 0.4 + 0.5 + 0.5) / 4)
    # Once something of a kind is linked, only linked items count.
    goals.link(conn, goal["id"], "job", str(linked_job))
    goals.link(conn, goal["id"], "job", str(old_job))
    goals.link(conn, goal["id"], "study_session", str(make_session(conn, 15, before)))
    goals.link(conn, goal["id"], "task", open_task["id"])
    goals.link(conn, goal["id"], "math_topic", "synthetic-old")
    goals.link(conn, goal["id"], "math_topic", "synthetic-unmastered")
    assert current() == [1, 15, 0, 1]
    # An auto measure's stored value never matters, and cannot be set.
    with pytest.raises(GoalError):
        goals.set_measure(conn, goal["id"], goals.get(conn, goal["id"])["measures"][0]["id"], 3)


def test_linkable_searches_active_modules_kinds_round_robin(workspace):
    conn = workspace.conn
    make_job(conn, "Frontend Engineer Intern")
    make_opportunity(conn, "Frontend hackathon")
    make_mail(conn, "Frontend interview times")
    page = notes.create_page(conn, notes.create_notebook(conn, "Synthetic")["id"], "Frontend notes")
    items = goals.linkable(conn, "frontend", None, MODULES)
    assert {(item["kind"], item["title"]) for item in items} == {
        ("job", "Frontend Engineer Intern"), ("opportunity", "Frontend hackathon"),
        ("mail", "Frontend interview times"), ("note", "Frontend notes")}
    assert all(set(item) == {"kind", "ref", "title", "detail"} for item in items)
    assert {item["kind"] for item in goals.linkable(conn, "frontend", None, ("workspace",))} == {"opportunity", "mail", "note"}
    assert goals.linkable(conn, "frontend", "note", MODULES) == [
        {"kind": "note", "ref": str(page["id"]), "title": "Frontend notes", "detail": "Page in Synthetic"}]
    assert goals.linkable(conn, "frontend", "job", ("workspace",)) == []
    for index in range(30):
        make_job(conn, f"Frontend role {index}")
    assert len(goals.linkable(conn, "", None, MODULES)) == 20
    with pytest.raises(GoalError):
        goals.linkable(conn, "x", "planet", MODULES)


def test_a_match_links_its_item_or_is_dismissed(workspace):
    conn = workspace.conn
    goal = goals.create(conn, {"title": "Internship"})
    job = str(make_job(conn))
    stamp = _iso(datetime.now(timezone.utc))
    for kind, ref in (("job", job), ("mail", make_mail(conn))):
        conn.execute("INSERT INTO goal_matches(goal_id,kind,ref,title,score,created_at) VALUES(?,?,?,?,0.7,?)",
                     (goal["id"], kind, ref, "Synthetic", stamp))
    first, second = [row["id"] for row in conn.execute("SELECT id FROM goal_matches ORDER BY id")]
    assert goals.get(conn, goal["id"])["suggestions_count"] == 2
    linked = goals.decide_match(conn, first, "link")
    assert [(link["kind"], link["ref"]) for link in linked["links"]] == [("job", job)]
    assert linked["suggestions_count"] == 1 and linked["suggestions"][0]["id"] == second
    assert set(linked["suggestions"][0]) == {"id", "goal_id", "goal_title", "kind", "ref", "title", "detail", "score",
                                              "urgent", "due_at", "classified_by", "status", "view", "created_at"}
    dismissed = goals.decide_match(conn, second, "dismiss")
    assert dismissed["suggestions"] == [] and dismissed["links_count"] == 1
    for ident, decision, status in ((first, "link", 409), (second, "dismiss", 409), (999, "link", 404), (first, "maybe", 400)):
        with pytest.raises(GoalError) as refused:
            goals.decide_match(conn, ident, decision)
        assert refused.value.status == status


def test_demo_seed_makes_two_goals_and_links_demo_jobs(workspace):
    conn = workspace.conn
    job = make_job(conn)
    applications_core.record_application(conn, job)
    goals.demo_seed(conn)
    goals.demo_seed(conn)  # once
    internship, deck = goals.listing(conn)[1], goals.listing(conn)[0]
    assert {internship["title"], deck["title"]} == {"Land a summer 2027 internship", "Finish the frontend interview deck"}
    internship = goals.get(conn, next(g["id"] for g in goals.listing(conn) if g["title"].startswith("Land")))
    assert [link["kind"] for link in internship["links"]] == ["job"]
    assert [m["current"] for m in internship["measures"]] == [1, 2, 0]
    assert [row["all_day"] for row in conn.execute("SELECT all_day FROM calendar_events WHERE title='Frontend interview deck review'")] == [1]
