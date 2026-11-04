"""The morning brief (design Q15): the sweep matches synthetic jobs, opportunities, mail
and events to goals, marks urgent ones, writes one brief a day, classifies unclear
items in one budgeted fast-tier request in live mode only, and the scheduler runs it
at launch once a day from 05:00. Never a real network call."""
from __future__ import annotations

import dataclasses
import json
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from kitty import web_ai
from kitty.core import calendar, goals
from kitty.core import jobs as jobs_core
from kitty.core.rika import brief, llm, store
from kitty.core.rika.loop import Hub

AT = datetime.now(timezone.utc).replace(microsecond=0)
MODULES = ("applyai", "workspace")


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
    """The mail slice's check() (kitty.core.rika.mail), stubbed: it records the brief's message id."""
    from kitty.core.rika import mail as module

    stub = SimpleNamespace(calls=[], error=None, MailError=module.MailError,
                           result={"new": [], "drafted": [], "scheduled": [], "notes": []})

    def check(app, conn, *, message_id=None):
        stub.calls.append(message_id)
        if stub.error:
            raise stub.error
        return stub.result

    monkeypatch.setattr(module, "check", check)
    return stub


@pytest.fixture
def make_app(workspace, mail, no_network):
    def make(*, demo=True, modules=MODULES):
        settings = dataclasses.replace(workspace.settings, demo=demo)
        app = SimpleNamespace(settings=settings, modules=modules, mutation_lock=threading.Lock(),
                              rika=SimpleNamespace(hub=Hub()))
        app.events = app.rika.hub.subscribe()
        return app
    return make


@pytest.fixture
def openrouter(workspace, monkeypatch):
    web_ai._write_key(SimpleNamespace(settings=workspace.settings), "synthetic-openrouter-test-key")
    for name in ("fast", "standard", "deep"):
        workspace.conn.execute("INSERT INTO ai_models VALUES(?,?,?,?,?,?)", (f"synthetic/{name}", name, "0.000001",
                               "0.000002", 200_000, web_ai._now().isoformat()))
    workspace.conn.execute("UPDATE ai_settings SET rika_fast_model='synthetic/fast',rika_standard_model='synthetic/standard',"
                           "rika_deep_model='synthetic/deep',daily_budget_usd='1.00' WHERE id=1")
    script = SimpleNamespace(replies=[], requests=[])

    def handle(request: httpx.Request) -> httpx.Response:
        script.requests.append(json.loads(request.content))
        return httpx.Response(200, content=script.replies.pop(0), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handle))
    return script


def _sse_text(text: str) -> bytes:
    chunks = [{"choices": [{"delta": {"content": text}}]},
              {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"cost": 0.0002}}]
    return b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks) + b"data: [DONE]\n\n"


def drain(events: queue.Queue) -> list[dict]:
    seen = []
    while True:
        try:
            seen.append(events.get_nowait())
        except queue.Empty:
            return seen


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_job(conn, title, *, seen=AT - timedelta(hours=3), deadline=None, company="Synthetic Labs", season="Summer 2027"):
    job, _ = jobs_core.upsert_job(conn, company=company, title=title, url=f"https://example.invalid/{uuid.uuid4().hex}",
                                  source_job_id=uuid.uuid4().hex, season=season,
                                  deadline_at=iso(deadline) if deadline else None)
    conn.execute("UPDATE jobs SET first_seen_at=? WHERE id=?", (iso(seen), job.id))
    return str(job.id)


def make_mail(conn, subject, body, received=AT - timedelta(hours=1)):
    ident = uuid.uuid4().hex
    conn.execute("INSERT INTO rika_mail_messages(id,thread_id,sender,sender_email,subject,body,received_at,kind,needs_reply,"
                 "fetched_at) VALUES(?,?,'Synthetic Recruiter','recruiter@example.invalid',?,?,?,'scheduling',1,?)",
                 (ident, ident, subject, body, received.isoformat(), received.isoformat()))
    return ident


def make_event(conn, title, start):
    return calendar.create_event(conn, {"title": title, "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
                                        "timezone": "UTC"})["id"]


def make_opportunity(conn, title, *, created=AT - timedelta(hours=2), deadline=None):
    cursor = conn.execute("INSERT INTO opportunities(identity,title,source,category,deadline,created_at,updated_at)"
                          " VALUES(?,?,'manual','workshop',?,?,?)",
                          (uuid.uuid4().hex, title, deadline, iso(created), iso(created)))
    return str(cursor.lastrowid)


def two_goals(conn):
    internship = goals.create(conn, {"title": "Land a summer 2027 internship",
                                     "measures": [{"label": "Applications sent", "target": 40, "auto": "applications"}]})
    deck = goals.create(conn, {"title": "Finish the frontend interview deck"})
    return internship, deck


def test_the_sweep_matches_new_items_to_goals_and_marks_urgent_ones(workspace, make_app, mail):
    conn = workspace.conn
    internship, deck = two_goals(conn)
    soon_job = make_job(conn, "Software Intern", deadline=AT + timedelta(hours=30))
    later_job = make_job(conn, "Hardware Intern", deadline=AT + timedelta(days=20))
    make_job(conn, "Barista - Part Time", season="part-time")
    make_job(conn, "Graphics Intern", seen=AT - timedelta(days=5))  # before the lookback
    asking = make_mail(conn, "Frontend interview deck", "Could you send the deck tomorrow?")
    event = make_event(conn, "Frontend interview deck review", AT + timedelta(hours=20))
    make_event(conn, "Frontend interview deck retro", AT + timedelta(days=5))  # past the horizon
    workshop = make_opportunity(conn, "Frontend interview workshop", deadline=(AT + timedelta(days=30)).date().isoformat())
    mail.result = {"new": [], "drafted": [{"id": "d1"}, {"id": "d2"}], "scheduled": [{"id": "d2"}], "notes": []}
    app = make_app()

    made = brief.sweep(app, conn, AT)

    found = {(item["goal_id"], item["kind"], item["ref"]): item for item in made["items"]}
    assert set(found) == {(internship["id"], "job", soon_job), (internship["id"], "job", later_job),
                          (deck["id"], "mail", asking), (deck["id"], "event", event), (deck["id"], "opportunity", workshop)}
    assert {key[2] for key, item in found.items() if item["urgent"]} == {soon_job, asking, event}
    assert all(item["classified_by"] == "keywords" and item["status"] == "new" for item in made["items"])
    assert made["items"][0]["urgent"] and found[(deck["id"], "event", event)]["view"] == "calendar"
    assert found[(internship["id"], "job", soon_job)]["due_at"].startswith((AT + timedelta(hours=30)).date().isoformat())
    assert made["day"] == AT.date().isoformat() and made["seen"] is False
    assert made["mail"] == {"drafted": 2, "scheduled": 1, "sent": 0} and made["notes"] == []
    message = store.message(conn, made["message_id"])
    assert message["role"] == "rika" and message["model"] == "brief" and message["status"] == "done"
    assert message["text"].startswith("Good morning. I found 5 things for your goals:")
    assert "Email: 2 replies drafted, 1 scheduled to send." in message["text"]
    assert mail.calls == [made["message_id"]]
    events = drain(app.events)
    assert [event["type"] for event in events] == ["message", "brief"]
    assert events[0]["message"]["id"] == made["message_id"] and events[1]["brief"] == made
    alerts = brief.alerts(conn, AT)
    assert {alert["key"] for alert in alerts} == {f"rika:{found[(internship['id'], 'job', soon_job)]['id']}",
                                                  f"rika:{found[(deck['id'], 'mail', asking)]['id']}",
                                                  f"rika:{found[(deck['id'], 'event', event)]['id']}"}
    assert all(set(alert) == {"id", "key", "title", "body", "due_at", "goal_id", "view", "created_at"} for alert in alerts)
    assert brief.alerts(conn, AT + timedelta(days=3)) == []  # their moments have passed
    assert goals.get(conn, deck["id"])["suggestions_count"] == 3


def test_one_brief_a_day_and_dismissed_matches_never_return(workspace, make_app):
    conn = workspace.conn
    internship, _deck = two_goals(conn)
    first_job = make_job(conn, "Software Intern")
    app = make_app()
    first = brief.sweep(app, conn, AT)
    [item] = first["items"]
    goals.decide_match(conn, item["id"], "dismiss")
    again = brief.sweep(app, conn, AT + timedelta(minutes=5))
    assert again["message_id"] == first["message_id"] and again["items"] == []
    assert conn.execute("SELECT count(*) FROM rika_briefs").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM rika_messages WHERE model='brief'").fetchone()[0] == 1
    assert brief.mark_seen(conn, AT)["seen"] is True
    # The next day looks only at what arrived since the previous brief.
    later_job = make_job(conn, "Hardware Intern", seen=AT + timedelta(hours=2))
    tomorrow = brief.sweep(app, conn, AT + timedelta(days=1))
    assert tomorrow["day"] == (AT + timedelta(days=1)).date().isoformat()
    assert tomorrow["message_id"] != first["message_id"] and tomorrow["seen"] is False
    assert [(item["ref"], item["goal_id"]) for item in tomorrow["items"]] == [(later_job, internship["id"])]
    assert first_job not in {item["ref"] for item in tomorrow["items"]}
    assert brief.get(conn, AT)["items"] == []  # the first day's only match was dismissed


def test_the_brief_without_goals_says_so_and_notes_mail_errors(workspace, make_app, mail):
    mail.error = mail.MailError("auth", "Gmail asked to sign in again")
    made = brief.sweep(make_app(), workspace.conn, AT)
    assert made["items"] == [] and made["notes"] == ["Gmail asked to sign in again"]
    text = store.message(workspace.conn, made["message_id"])["text"]
    assert text.startswith("Good morning. You have no active goals yet") and text.endswith("Gmail asked to sign in again")


def _unclear(conn):
    """A goal and a job that only shares one goal-title word: an unclear keyword match."""
    goal = goals.create(conn, {"title": "Finish the frontend interview deck"})
    job = make_job(conn, "Frontend Developer")
    return goal, job


def test_live_mode_classifies_unclear_items_in_one_fast_request(workspace, make_app, openrouter):
    conn = workspace.conn
    goal, job = _unclear(conn)
    openrouter.replies = [_sse_text('```json\n{"matches": [{"candidate": "c0", "score": 0.9}]}\n```')]
    made = brief.sweep(make_app(demo=False), conn, AT)
    [request] = openrouter.requests
    assert request["model"] == "synthetic/fast" and "tools" not in request
    assert "untrusted" in request["messages"][0]["content"]
    payload = json.loads(request["messages"][1]["content"].split("\n", 1)[1])
    assert payload["candidates"] == [{"candidate": "c0", "goal": goal["id"], "kind": "job", "title": "Frontend Developer",
                                      "text": payload["candidates"][0]["text"]}]
    [item] = made["items"]
    assert (item["ref"], item["classified_by"], item["score"]) == (job, "model", 0.9)
    assert made["notes"] == []
    message = store.message(conn, made["message_id"])
    assert message["tier"] == "fast" and message["cost_usd"] == pytest.approx(0.0002)
    ledger = conn.execute("SELECT state,actual_usd FROM ai_reviews WHERE purpose='rika'").fetchall()
    assert [tuple(row) for row in ledger] == [("completed", "0.0002")]


def test_the_model_check_is_skipped_over_budget_without_a_key_and_in_the_demo(workspace, make_app, openrouter,
                                                                              monkeypatch):
    conn = workspace.conn
    _unclear(conn)
    conn.execute("UPDATE ai_settings SET daily_budget_usd='0' WHERE id=1")
    over = brief.sweep(make_app(demo=False), conn, AT)
    assert openrouter.requests == [] and over["items"] == []
    assert over["notes"] == ["Skipped the model check of 1 unclear item: it would go over today's AI budget"]
    conn.execute("UPDATE ai_settings SET daily_budget_usd='1.00' WHERE id=1")
    demo = brief.sweep(make_app(demo=True), conn, AT + timedelta(minutes=1))
    assert openrouter.requests == [] and demo["notes"] == []
    (workspace.settings.root / ".private" / "openrouter-key").unlink()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    keyless = brief.sweep(make_app(demo=False), conn, AT + timedelta(minutes=2))
    assert openrouter.requests == [] and keyless["notes"][0].startswith("Skipped the model check of 1 unclear item: ")


def test_the_scheduler_runs_at_launch_once_a_day_from_five(workspace, make_app):
    conn = workspace.conn
    conn.execute("UPDATE calendar_calendars SET timezone='America/Chicago' WHERE id='personal'")
    chicago = ZoneInfo("America/Chicago")
    early = datetime(2026, 11, 3, 4, 30, tzinfo=chicago)
    morning = datetime(2026, 11, 3, 6, 0, tzinfo=chicago)
    app = make_app(demo=False)
    assert not brief.Scheduler(app).due(conn, morning)  # no active goal yet
    two_goals(conn)
    assert not brief.Scheduler(app, clock=lambda: early).due(conn, early)
    assert brief.Scheduler(app, clock=lambda: early).tick() is None
    assert brief.Scheduler(app).due(conn, morning)
    scheduler = brief.Scheduler(app, clock=lambda: morning, interval=3600)
    scheduler.start()
    try:
        deadline = time.monotonic() + 10
        while not conn.execute("SELECT 1 FROM rika_briefs").fetchone():
            assert time.monotonic() < deadline, "the launch sweep did not run"
            time.sleep(0.05)
    finally:
        scheduler.stop()
    assert [row["day"] for row in conn.execute("SELECT day FROM rika_briefs")] == ["2026-11-03"]
    assert not brief.Scheduler(app).due(conn, morning + timedelta(hours=8))
    assert brief.Scheduler(app, clock=lambda: morning + timedelta(hours=8)).tick() is None
    assert brief.Scheduler(app).due(conn, morning + timedelta(days=1))
    # The demo shows its brief whenever it starts.
    assert brief.Scheduler(make_app(demo=True)).due(conn, early + timedelta(days=1))


def test_the_scheduler_also_runs_without_goals_once_gmail_is_connected(workspace, make_app, monkeypatch):
    from kitty.core.rika import mail as mail_module

    conn = workspace.conn
    morning = datetime.now(goals.zone(conn)).replace(hour=8, minute=0, second=0, microsecond=0)
    app = make_app(demo=False)
    assert not brief.Scheduler(app).due(conn, morning)
    monkeypatch.setattr(mail_module, "connected", lambda _app: True)
    assert brief.Scheduler(app).due(conn, morning)


def test_an_all_day_event_tomorrow_is_due_by_the_end_of_its_day(workspace, make_app):
    conn = workspace.conn
    _internship, deck = two_goals(conn)
    tomorrow = (AT + timedelta(days=1)).date()
    event = calendar.create_event(conn, {"title": "Frontend interview deck review", "start": tomorrow.isoformat(),
                                         "end": (tomorrow + timedelta(days=1)).isoformat(), "all_day": True,
                                         "timezone": "UTC"})["id"]
    [item] = brief.sweep(make_app(), conn, AT)["items"]
    assert (item["goal_id"], item["ref"], item["urgent"]) == (deck["id"], event, True)
    assert item["due_at"] == f"{tomorrow.isoformat()}T23:59:00.000000+00:00"
    assert item["detail"] == datetime.combine(tomorrow, datetime.min.time()).strftime("%a %d %b")
