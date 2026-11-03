"""Goals and the morning brief over HTTP, in the demo (never a network call)."""
from __future__ import annotations

import dataclasses
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from terminal_support import make_root, site

from kitty.core import calendar, goals, notes
from kitty.core.rika import brief
from kitty.db import connect, migrate

GOAL_KEYS = {"id", "title", "why", "target_date", "status", "measures", "progress", "days_left", "links_count",
             "suggestions_count", "revision", "created_at", "updated_at"}
BRIEF_KEYS = {"day", "created_at", "seen", "items", "mail", "notes", "message_id"}


@pytest.fixture
def no_network(monkeypatch):
    calls = []

    def refuse(self, request):
        calls.append(str(request.url))
        raise AssertionError(f"unexpected network request to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    return calls


@pytest.fixture
def mail(monkeypatch):
    """The mail slice's check(), stubbed to report nothing new."""
    from kitty.core.rika import mail as module

    monkeypatch.setattr(module, "check", lambda app, conn, *, message_id=None: {
        "new": [], "drafted": [], "scheduled": [], "notes": []})


@pytest.fixture
def settings(tmp_path, no_network, mail):
    return dataclasses.replace(make_root(tmp_path, ("workspace",)), demo=True)


@pytest.fixture
def demo(settings, no_network):
    with site(settings) as running:
        yield running
    assert no_network == []


def db(running):
    return closing(connect(running.app.settings.db_path))


def test_goal_routes_create_edit_link_measure_and_delete(demo):
    assert demo.request("/api/goals") == (200, {"goals": []})
    status, body = demo.request("/api/goals", {"title": "Finish the frontend interview deck", "target_date": "2027-01-15",
                                               "measures": [{"label": "Deck done", "target": 100, "unit": "%", "current": 35},
                                                            {"label": "Tasks", "target": 4, "auto": "tasks_done"}]})
    assert status == 200 and set(body["goal"]) == GOAL_KEYS | {"links", "suggestions"}
    goal = body["goal"]
    manual, auto = (item["id"] for item in goal["measures"])
    assert goal["progress"] == pytest.approx(0.175) and goal["measures"][1]["computed"] is True
    status, listed = demo.request("/api/goals")
    assert status == 200 and set(listed["goals"][0]) == GOAL_KEYS
    assert demo.request(f"/api/goals/{goal['id']}")[1]["goal"]["id"] == goal["id"]
    assert demo.request(f"/api/goals/{goal['id']}", {"revision": 9, "why": "x"})[0] == 409
    status, body = demo.request(f"/api/goals/{goal['id']}", {"revision": 1, "why": "Ready before the first interview"})
    assert status == 200 and body["goal"]["revision"] == 2
    with db(demo) as conn:
        page = notes.create_page(conn, notes.create_notebook(conn, "Synthetic")["id"], "Deck outline")
    status, body = demo.request(f"/api/goals/{goal['id']}/links", {"kind": "note", "ref": str(page["id"])})
    assert status == 200 and body["goal"]["links"] == [{"kind": "note", "ref": str(page["id"]), "title": "Deck outline",
                                                        "detail": "Page in Synthetic", "view": "notes", "missing": False}]
    assert demo.request(f"/api/goals/{goal['id']}/links", {"kind": "note", "ref": "999"})[0] == 404
    assert demo.request(f"/api/goals/{goal['id']}/links", {"kind": "job", "ref": "1"})[0] == 403  # applyai is off
    assert demo.request("/api/goals/linkable?q=deck") == (200, {"items": [
        {"kind": "note", "ref": str(page["id"]), "title": "Deck outline", "detail": "Page in Synthetic"}]})
    assert demo.request("/api/goals/linkable?q=deck&kind=job") == (200, {"items": []})
    assert demo.request("/api/goals/linkable?query=deck")[0] == 400
    status, body = demo.request(f"/api/goals/{goal['id']}/unlink", {"kind": "note", "ref": str(page["id"])})
    assert status == 200 and body["goal"]["links"] == []
    status, body = demo.request(f"/api/goals/{goal['id']}/measures/{manual}", {"current": 60})
    assert status == 200 and body["goal"]["measures"][0]["current"] == 60 and body["goal"]["revision"] == 3
    assert demo.request(f"/api/goals/{goal['id']}/measures/{auto}", {"current": 1})[0] == 409
    assert demo.request(f"/api/goals/{goal['id']}/measures/nope", {"current": 1})[0] == 404
    for path, payload in (("/api/goals", {"title": ""}), ("/api/goals", {"title": "x", "colour": 1}),
                          (f"/api/goals/{goal['id']}", {"why": "no revision"}),
                          (f"/api/goals/{goal['id']}/links", {"kind": "note"}),
                          (f"/api/goals/{goal['id']}/delete", {})):
        assert demo.request(path, payload)[0] == 400, path
    assert demo.request(f"/api/goals/{goal['id']}/delete", {"revision": 1})[0] == 409
    assert demo.request(f"/api/goals/{goal['id']}/delete", {"revision": 3}) == (200, {"deleted": goal["id"]})
    assert demo.request(f"/api/goals/{goal['id']}")[0] == 404
    assert demo.request("/api/goals", {"title": "Unprotected"}, headers={"X-Kitty-Request": None})[0] == 403


def test_brief_routes_run_see_alert_and_link_a_match(demo):
    assert demo.request("/api/rika/brief") == (200, {"brief": None})
    assert demo.request("/api/rika/brief/seen", {})[0] == 404
    goal = demo.request("/api/goals", {"title": "Finish the frontend interview deck"})[1]["goal"]
    start = datetime.now(timezone.utc) + timedelta(hours=6)
    with db(demo) as conn:
        event = calendar.create_event(conn, {"title": "Frontend interview deck review", "start": start.isoformat(),
                                             "end": (start + timedelta(hours=1)).isoformat(), "timezone": "UTC"})
    status, body = demo.request("/api/rika/brief/run", {})
    assert status == 200 and set(body["brief"]) == BRIEF_KEYS
    made = body["brief"]
    [item] = made["items"]
    assert (item["kind"], item["ref"], item["urgent"], item["goal_title"]) == ("event", event["id"], True, goal["title"])
    assert demo.request("/api/rika/brief") == (200, {"brief": made})
    [alert] = demo.request("/api/rika/alerts")[1]["alerts"]
    assert alert["key"] == f"rika:{item['id']}" and alert["goal_id"] == goal["id"] and alert["view"] == "calendar"
    assert demo.request("/api/rika/brief/seen", {})[1]["brief"]["seen"] is True
    assert demo.request("/api/rika/brief/run", {"now": 1})[0] == 400
    status, body = demo.request(f"/api/goals/matches/{item['id']}", {"decision": "link"})
    assert status == 200 and [link["ref"] for link in body["goal"]["links"]] == [event["id"]]
    assert demo.request(f"/api/goals/matches/{item['id']}", {"decision": "dismiss"})[0] == 409
    assert demo.request("/api/goals/matches/999", {"decision": "link"})[0] == 404
    assert demo.request("/api/rika/alerts") == (200, {"alerts": []})
    history = [entry for entry in demo.request("/api/rika/history")[1]["items"] if entry["kind"] == "message"]
    assert [(entry["role"], entry["model"]) for entry in history] == [("rika", "brief")]


def test_the_scheduler_writes_the_brief_at_launch_and_stops_with_the_app(settings):
    with closing(connect(settings.db_path)) as conn:
        migrate(conn)
        goals.create(conn, {"title": "Land a summer 2027 internship"})
    with site(settings) as running:
        assert isinstance(running.app.brief_scheduler, brief.Scheduler)
        deadline = time.monotonic() + 10
        while running.request("/api/rika/brief")[1]["brief"] is None:
            assert time.monotonic() < deadline, "no brief at launch"
            time.sleep(0.05)
    assert running.app.brief_scheduler is None
