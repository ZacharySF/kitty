"""Rika's email over HTTP in the demo: the synthetic mailbox, settings with confirmation,
held sends sent by the dispatcher, Undo-send, and held sends cancelled by a restart.
Never a network call (the demo's fake mailbox; real HTTP transports fail the test)."""
from __future__ import annotations

import dataclasses
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kitty.core.rika import demo_seed, mail
from kitty.core.workspace import open_workspace
from kitty.db import connect
from terminal_support import make_root, site


@pytest.fixture
def no_network(monkeypatch):
    calls = []

    def refuse(self, request):
        calls.append(str(request.url))
        raise AssertionError(f"unexpected network request to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    return calls


@pytest.fixture
def settings(tmp_path, no_network, monkeypatch):
    monkeypatch.setattr(mail, "DISPATCH_INTERVAL", 0.05)
    # The morning brief's sweep also checks mail; keep it out of these tests' counts.
    monkeypatch.setattr("kitty.web_goals.start", lambda app: None, raising=False)
    made = dataclasses.replace(make_root(tmp_path, ("workspace",)), demo=True)
    with open_workspace(made) as workspace:
        mail.seed_demo(workspace.conn)
    return made


@pytest.fixture
def demo(settings, no_network):
    with site(settings) as running:
        yield running
    assert no_network == []


def _due(settings, draft_id):
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="microseconds")
    with closing(connect(settings.db_path)) as conn:
        conn.execute("UPDATE rika_mail_drafts SET send_after=? WHERE id=?", (past, draft_id))


def _wait_for(demo, draft_id, status, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        drafts = {item["id"]: item for item in demo.request("/api/rika/mail")[1]["drafts"]}
        if drafts[draft_id]["status"] == status:
            return drafts[draft_id]
        time.sleep(0.05)
    raise AssertionError(f"draft never became {status}")


def test_the_demo_mailbox_is_connected_with_every_auto_send_off(demo):
    status, body = demo.request("/api/rika/mail")
    assert status == 200
    assert body["mailbox"] == {"connected": True, "demo": True, "account": mail.DEMO_ACCOUNT, "setup": None,
                               "can_delay": False, "hold_seconds": 30, "hold_choices": [10, 30, 60, 120, 300],
                               "last_check": None}
    assert [(kind["kind"], kind["auto"], kind["sent"]) for kind in body["kinds"]] == [
        ("scheduling", False, 0), ("confirmation", False, 0), ("thanks", False, 0)]
    assert body["drafts"] == []
    assert demo.request("/api/rika/mail?x=1")[0] == 400
    assert demo.request("/api/rika/mail/connect", {})[0] == 409


def test_auto_send_needs_confirm_and_the_hold_is_chosen(demo):
    status, refused = demo.request("/api/rika/mail/settings", {"kind": "scheduling", "auto": True})
    assert status == 409 and refused["error"] == "Confirm turning on auto-send"
    status, body = demo.request("/api/rika/mail/settings", {"kind": "scheduling", "auto": True, "confirm": True})
    assert status == 200 and body["kinds"][0]["auto"] is True
    status, body = demo.request("/api/rika/mail/settings", {"hold_seconds": 60})
    assert status == 200 and body["mailbox"]["hold_seconds"] == 60
    for bad in ({"hold_seconds": 45}, {"kind": "question", "auto": True, "confirm": True}, {"kind": "thanks"},
                {"auto": True}, {}, {"kind": "thanks", "auto": "yes"}, {"colour": 1}):
        assert demo.request("/api/rika/mail/settings", bad)[0] == 400, bad
    assert demo.request("/api/rika/mail/settings", {"hold_seconds": 30}, headers={"X-Kitty-Request": None})[0] == 403


def test_check_drafts_then_send_holds_and_the_dispatcher_sends(demo, settings):
    status, checked = demo.request("/api/rika/mail/check", {})
    assert status == 200 and checked["new"] == 0
    drafts = {draft["reply_kind"]: draft for draft in checked["drafts"]}
    assert set(drafts) == {"scheduling", "confirmation", "thanks"}
    assert demo.request("/api/rika/mail/check", {})[1]["drafts"] == []
    scheduling = drafts["scheduling"]
    path = f"/api/rika/mail/drafts/{scheduling['id']}"
    status, edited = demo.request(path, {"body": "Hi Maya,\n\nTuesday after 1pm works.\n\nAlex"})
    assert status == 200 and edited["draft"]["body"].endswith("Alex")
    status, held = demo.request(path + "/send", {})
    assert status == 200 and held["draft"]["status"] == "scheduled" and held["draft"]["send_after"]
    assert demo.request(path, {"body": "too late"})[0] == 409
    status, undone = demo.request(path + "/undo", {})
    assert status == 200 and undone["draft"]["status"] == "cancelled"
    demo.request(path + "/send", {})
    _due(settings, scheduling["id"])
    sent = _wait_for(demo, scheduling["id"], "sent")
    assert sent["sent_at"] and sent["error"] is None
    status, late = demo.request(path + "/undo", {})
    assert status == 409 and late["error"] == "Already sent: Gmail can't unsend mail"
    overview = demo.request("/api/rika/mail")[1]
    assert overview["kinds"][0]["sent"] == 1 and overview["mailbox"]["last_check"]
    thanks = drafts["thanks"]["id"]
    status, discarded = demo.request(f"/api/rika/mail/drafts/{thanks}/discard", {})
    assert status == 200 and discarded["draft"]["status"] == "discarded"
    assert thanks not in {draft["id"] for draft in demo.request("/api/rika/mail")[1]["drafts"]}
    assert demo.request("/api/rika/mail/drafts/" + "0" * 32 + "/send", {})[0] == 404
    assert demo.request(path + "/send", {"now": True})[0] == 400
    history = demo.request("/api/rika/history")[1]["items"]
    assert [item["kind"] for item in history] == ["mail", "mail"]
    assert {item["reply_kind"] for item in history} == {"scheduling", "confirmation"}


def test_auto_send_holds_the_enabled_kind_only(demo, settings):
    demo.request("/api/rika/mail/settings", {"kind": "confirmation", "auto": True, "confirm": True})
    drafts = demo.request("/api/rika/mail/check", {})[1]["drafts"]
    statuses = {draft["reply_kind"]: (draft["status"], draft["auto"]) for draft in drafts}
    assert statuses == {"scheduling": ("draft", False), "confirmation": ("scheduled", True), "thanks": ("draft", False)}


def test_a_restart_cancels_sends_still_held(settings):
    with site(settings) as first:
        draft = first.request("/api/rika/mail/check", {})[1]["drafts"][0]
        first.request(f"/api/rika/mail/drafts/{draft['id']}/send", {})
    with site(settings) as second:
        after = {item["id"]: item for item in second.request("/api/rika/mail")[1]["drafts"]}[draft["id"]]
        assert after["status"] == "cancelled" and after["error"] == "rika! closed during the hold, so it was not sent"


def test_the_demo_seeder_seeds_only_the_synthetic_mail(tmp_path, no_network):
    made = dataclasses.replace(make_root(tmp_path, ("workspace",)), demo=True)
    with open_workspace(made) as workspace:
        demo_seed.seed(workspace.conn)
        conn = workspace.conn
        assert conn.execute("SELECT count(*) FROM rika_mail_messages").fetchone()[0] == 6
        assert conn.execute("SELECT count(*) FROM rika_mail_drafts").fetchone()[0] == 0
        assert conn.execute("SELECT sum(auto_send) FROM rika_mail_settings").fetchone()[0] == 0
