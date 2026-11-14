"""Microsoft (Outlook) and Slack connections against fake provider servers on loopback: no network."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import stat
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from kitty import web_auth
from kitty.core import calendar, connect
from kitty.core.connect import microsoft, slack
from kitty.core.rika import mail
from kitty.db.connection import connect as db_connect
from kitty.web_server import APIError, LocalHTTPServer, WebApplication

MS_CLIENT = "11111111-2222-3333-4444-555555555555"
SLACK_CLIENT = "1234567890.9876543210"
ME = "U0ME"


def _soon(days: int, hour: int) -> str:
    day = datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return day.strftime("%Y-%m-%dT%H:%M:%S.0000000")


def _event(ident: str, subject: str, days: int = 1, **extra) -> dict:
    return {"id": ident, "subject": subject, "start": {"dateTime": _soon(days, 15), "timeZone": "UTC"},
            "end": {"dateTime": _soon(days, 16), "timeZone": "UTC"}, "isAllDay": False, "showAs": "busy",
            "location": {"displayName": "Room 4"}, "bodyPreview": "Agenda", "webLink": "https://outlook.example/e/" + ident,
            "isCancelled": False, **extra}


def _mail(ident: str, name: str, address: str, subject: str, body: str, **extra) -> dict:
    return {"id": ident, "conversationId": "conv-" + ident, "internetMessageId": f"<{ident}@mail.example>",
            "from": {"emailAddress": {"name": name, "address": address}}, "replyTo": [], "subject": subject,
            "body": {"contentType": "text", "content": body},
            "receivedDateTime": (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), **extra}


def _reacted(channel: str, ts: str, text: str, name: str = "pushpin", user: str = ME) -> dict:
    return {"type": "message", "channel": channel,
            "message": {"type": "message", "text": text, "ts": ts, "permalink": f"https://team.slack.example/archives/{channel}/p{ts}",
                        "reactions": [{"name": name, "users": [user], "count": 1}]}}


class Providers:
    """The state of both fake services; the tests change it between syncs."""

    def __init__(self) -> None:
        self.codes: dict[str, str] = {}     # authorization code -> the challenge it was issued for
        self.forms: list[dict] = []         # every token request, as sent
        self.serial = 0
        self.access = self.refresh = None
        self.expires_in = 3600
        self.refuse_refresh = False
        self.calendars = [{"id": "cal/1", "name": "Calendar"}]
        self.events = {"cal/1": [_event("ev-1", "Design review"), _event("ev-2", "Dentist", days=2)]}
        self.mail = [_mail("m-1", "Maya Chen", "maya@northwind.example", "Onsite interview: pick a time",
                           "Could you share a few windows next Tuesday?"),
                     _mail("m-2", "Campus Weekly", "newsletter@campus.example", "This week", "Top stories.",
                           internetMessageHeaders=[{"name": "List-Unsubscribe", "value": "<mailto:x@campus.example>"}])]
        self.prefer: list[str] = []
        self.reactions: list[dict] = []

    def issue(self) -> dict:
        self.serial += 1
        self.access, self.refresh = f"access-{self.serial}", f"refresh-{self.serial}"
        return {"access_token": self.access, "refresh_token": self.refresh, "expires_in": self.expires_in}

    def redeem(self, form: dict) -> bool:
        """An authorization-code grant is good only with the verifier of its challenge (S256)."""
        expected = self.codes.pop(form.get("code", ""), None)
        digest = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
        return expected is not None and digest == expected


def _handler(world: Providers):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def answer(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            form = {key: values[0] for key, values in
                    parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
            world.forms.append(form)
            refresh = form.get("grant_type") == "refresh_token"
            good = (not world.refuse_refresh and form.get("refresh_token") == world.refresh) if refresh else world.redeem(form)
            if self.path == "/ms/token":
                if not good:
                    return self.answer(400, {"error": "invalid_grant"})
                return self.answer(200, {**world.issue(), "scope": "User.Read Calendars.Read Mail.Read", "token_type": "Bearer"})
            if self.path == "/slack/oauth.v2.access":
                if not good:
                    return self.answer(200, {"ok": False, "error": "invalid_refresh_token" if refresh else "invalid_code"})
                token = {**world.issue(), "token_type": "user"}
                if refresh:
                    return self.answer(200, {"ok": True, **token})
                return self.answer(200, {"ok": True, "team": {"id": "T0TEAM", "name": "Northwind"},
                                         "authed_user": {"id": ME, "scope": "reactions:read", **token}})
            self.answer(404, {})

        def do_GET(self):
            target = urlsplit(self.path)
            if self.headers.get("Authorization") != f"Bearer {world.access}":
                return self.answer(401, {"ok": False, "error": "invalid_auth"})
            world.prefer.append(self.headers.get("Prefer", ""))
            if target.path == "/graph/me":
                return self.answer(200, {"mail": "alex@outlook.example", "userPrincipalName": "alex@outlook.example"})
            if target.path == "/graph/me/calendars":
                return self.answer(200, {"value": world.calendars})
            if target.path.startswith("/graph/me/calendars/") and target.path.endswith("/calendarView"):
                ident = target.path.removeprefix("/graph/me/calendars/").removesuffix("/calendarView").replace("%2F", "/")
                return self.answer(200, {"value": world.events.get(ident, [])})
            if target.path == "/graph/me/mailFolders/inbox/messages":
                return self.answer(200, {"value": world.mail})
            if target.path == "/slack/reactions.list":
                return self.answer(200, {"ok": True, "items": world.reactions, "response_metadata": {"next_cursor": ""}})
            self.answer(404, {})

    return Handler


@pytest.fixture
def world(monkeypatch):
    state = Providers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(microsoft, "AUTHORITY", base + "/ms")
    monkeypatch.setattr(microsoft, "GRAPH", base + "/graph")
    monkeypatch.setattr(slack, "AUTHORIZE", base + "/slack/authorize")
    monkeypatch.setattr(slack, "API", base + "/slack")
    monkeypatch.setattr(slack, "PORT", 0)
    yield state
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)


@pytest.fixture
def site(workspace, tmp_path, world):
    static = tmp_path / "dist"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>Synthetic website</title>")
    app = WebApplication(workspace.settings, workspace.config, static, ui_session="ui-test")
    server = LocalHTTPServer(app, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None):
        headers = {"Host": server.authority, "Origin": server.origin, web_auth.HEADER: server.ui_auth.secret}
        payload = None
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-Kitty-Request": "1"})
            payload = json.dumps(body).encode()
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=20)
        try:
            client.request("GET" if body is None else "POST", path, body=payload, headers=headers)
            response = client.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            client.close()

    yield request
    server.shutdown()
    server.server_close()
    app.close()
    worker.join(timeout=5)


def redirect_back(url: str, **query) -> int:
    """What the browser does after sign-in: follow the provider's redirect to the loopback listener."""
    redirect = urlsplit(parse_qs(urlsplit(url).query)["redirect_uri"][0])
    client = http.client.HTTPConnection("127.0.0.1", redirect.port, timeout=20)
    try:
        client.request("GET", f"{redirect.path or '/'}?{urlencode(query)}", headers={"Host": redirect.netloc})
        response = client.getresponse()
        response.read()
        return response.status
    finally:
        client.close()


def sign_in(request, world, provider: str) -> dict:
    client_id = MS_CLIENT if provider == "microsoft" else SLACK_CLIENT
    assert request(f"/api/connect/{provider}/config", {"client_id": client_id})[0] == 200
    status, started = request(f"/api/connect/{provider}/start", {})
    assert status == 200
    query = {key: values[0] for key, values in parse_qs(urlsplit(started["url"]).query).items()}
    world.codes["code-1"] = query["code_challenge"]
    assert redirect_back(started["url"], code="code-1", state=query["state"]) == 200
    assert request(f"/api/connect/{provider}/poll", {"ticket": started["ticket"]}) == (200, {"status": "connected"})
    return query


def test_microsoft_connects_with_pkce_on_loopback_and_no_secret(site, world, workspace):
    request = site
    assert request("/api/connect/microsoft/start", {})[0] == 409  # no client ID yet
    assert request("/api/connect/microsoft/config", {"client_id": "not-a-guid"})[0] == 400
    assert request("/api/connect/microsoft/config", {"client_id": MS_CLIENT})[0] == 200
    status, started = request("/api/connect/microsoft/start", {})
    query = {key: values[0] for key, values in parse_qs(urlsplit(started["url"]).query).items()}
    redirect = urlsplit(query["redirect_uri"])
    assert status == 200 and query["client_id"] == MS_CLIENT and query["code_challenge_method"] == "S256"
    assert redirect.scheme == "http" and redirect.hostname == "localhost" and redirect.port
    assert set(query["scope"].split()) == {"User.Read", "offline_access", "Calendars.Read", "Mail.Read"}
    world.codes["good"] = query["code_challenge"]

    # A redirect with another state is refused and uses nothing up.
    assert redirect_back(started["url"], code="good", state="forged") == 400
    assert world.forms == []
    assert request("/api/connect/microsoft/poll", {"ticket": "someone-else"})[0] == 400
    assert request("/api/connect/microsoft/poll", {"ticket": started["ticket"]})[1] == {"status": "pending"}

    assert redirect_back(started["url"], code="good", state=query["state"]) == 200
    assert request("/api/connect/microsoft/poll", {"ticket": started["ticket"]})[1] == {"status": "connected"}
    # The fake accepted the code only because the verifier hashes to the challenge; no secret was sent.
    form = world.forms[0]
    assert form["grant_type"] == "authorization_code" and form["redirect_uri"] == query["redirect_uri"]
    assert len(form["code_verifier"]) >= 43 and "client_secret" not in form
    # The attempt is over: replaying the redirect finds no listener (or is refused by one closing).
    try:
        assert redirect_back(started["url"], code="good", state=query["state"]) == 400
    except ConnectionError:
        pass
    assert len(world.forms) == 1

    token = connect.token_path(workspace.settings, "microsoft")
    assert stat.S_IMODE(token.stat().st_mode) == 0o600 and json.loads(token.read_text())["refresh_token"] == "refresh-1"
    state = request("/api/connect")[1]["microsoft"]
    assert state["connected"] is True and state["account"] == "alex@outlook.example" and state["error"] is None
    assert "access-1" not in json.dumps(request("/api/connect")[1])
    assert request("/api/connect/microsoft/config", {"client_id": MS_CLIENT})[0] == 409  # connected: disconnect first


def test_a_code_exchanged_with_the_wrong_verifier_connects_nothing(site, world, workspace):
    request = site
    request("/api/connect/microsoft/config", {"client_id": MS_CLIENT})
    started = request("/api/connect/microsoft/start", {})[1]
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    world.codes["stolen"] = "a-challenge-from-another-attempt"
    assert redirect_back(started["url"], code="stolen", state=state) == 200
    result = request("/api/connect/microsoft/poll", {"ticket": started["ticket"]})[1]
    assert result["status"] == "error" and "refused" in result["error"]
    assert not connect.token_path(workspace.settings, "microsoft").exists()
    assert request("/api/connect")[1]["microsoft"]["connected"] is False


def test_outlook_events_are_imported_read_only_then_updated_and_removed(site, world, workspace):
    request = site
    sign_in(request, world, "microsoft")  # connecting runs the first sync
    with closing(db_connect(workspace.settings.db_path)) as conn:
        outlook = next(item for item in calendar.calendars(conn) if item["name"] == "Calendar (Outlook)")
        window = ((datetime.now(UTC) - timedelta(days=2)).isoformat(), (datetime.now(UTC) + timedelta(days=9)).isoformat())
        found = {item["title"]: item for item in calendar.occurrences(conn, *window, calendar_id=outlook["id"])}
        assert set(found) == {"Design review", "Dentist"}
        review = calendar.get_event(conn, found["Design review"]["id"])
        assert review["location"] == "Room 4" and review["url"] == "https://outlook.example/e/ev-1"
        assert datetime.fromisoformat(review["start"]) == datetime.fromisoformat(_soon(1, 15)[:19] + "+00:00")

        # Read-only: no edit, no delete, no new event in that calendar.
        for attempt in (lambda: calendar.edit_event(conn, review["id"], {"revision": review["revision"], "changes": {"title": "Mine"}}),
                        lambda: calendar.edit_event(conn, review["id"], {"revision": review["revision"], "delete": True}),
                        lambda: calendar.create_event(conn, {"calendar_id": outlook["id"], "title": "Local", "start": review["start"], "end": review["end"]})):
            with pytest.raises(APIError) as refused:
                attempt()
            assert refused.value.status == 409

        world.events["cal/1"] = [_event("ev-1", "Design review (moved)", days=3),
                                 _event("ev-3", "Cancelled one", isCancelled=True)]
        status, synced = request("/api/connect/microsoft/sync", {})
        assert status == 200 and synced["result"]["calendar"] == {"calendars": 1, "added": 0, "updated": 1, "removed": 1, "skipped": 0}
        found = calendar.occurrences(conn, *window, calendar_id=outlook["id"])
        assert [(item["id"], item["title"]) for item in found] == [(review["id"], "Design review (moved)")]
        assert datetime.fromisoformat(found[0]["start"]) == datetime.fromisoformat(_soon(3, 15)[:19] + "+00:00")
        # An unchanged calendar syncs to no change.
        assert request("/api/connect/microsoft/sync", {})[1]["result"]["calendar"]["updated"] == 0


def test_outlook_mail_reaches_rikas_triage_once_and_is_never_drafted(site, world, workspace):
    request = site
    sign_in(request, world, "microsoft")
    assert any("outlook.body-content-type" in value and "ImmutableId" in value for value in world.prefer)
    with closing(db_connect(workspace.settings.db_path)) as conn:
        rows = {row["id"]: row for row in conn.execute("SELECT * FROM rika_mail_messages")}
        assert set(rows) == {"outlook:m-1", "outlook:m-2"}
        maya, weekly = rows["outlook:m-1"], rows["outlook:m-2"]
        assert (maya["provider"], maya["kind"], maya["needs_reply"], maya["sender_email"]) == ("outlook", "scheduling", 1, "maya@northwind.example")
        assert (weekly["kind"], weekly["needs_reply"]) == ("automated", 0)

        world.mail.append(_mail("m-3", "Jordan Lee", "jordan@study.example", "Notes", "Thanks for the walkthrough!"))
        assert request("/api/connect/microsoft/sync", {})[1]["result"]["mail"] == {"new": 1}
        assert request("/api/connect/microsoft/sync", {})[1]["result"]["mail"] == {"new": 0}
        assert conn.execute("SELECT count(*) FROM rika_mail_messages").fetchone()[0] == 3
        # Read-only in this phase: Rika refuses to draft a reply to Outlook mail.
        with pytest.raises(mail.MailRefused) as refused:
            mail.create_reply(None, conn, "outlook:m-1", body="Tuesday works")
        assert refused.value.status == 409
        assert conn.execute("SELECT count(*) FROM rika_mail_drafts").fetchone()[0] == 0


def test_an_expiring_token_is_refreshed_and_a_refused_refresh_disconnects(site, world, workspace):
    request = site
    world.expires_in = 30  # inside the refresh margin: the next call refreshes first
    sign_in(request, world, "microsoft")
    token = connect.token_path(workspace.settings, "microsoft")
    grants = [form["grant_type"] for form in world.forms]
    assert grants[0] == "authorization_code" and set(grants[1:]) == {"refresh_token"}
    assert all("client_secret" not in form for form in world.forms)
    # Every refresh replaces the refresh token, and the newest one is the one on disk.
    assert json.loads(token.read_text())["refresh_token"] == world.refresh != "refresh-1"

    world.expires_in = 3600
    assert request("/api/connect/microsoft/sync", {})[0] == 200
    used = len(world.forms)
    assert request("/api/connect/microsoft/sync", {})[0] == 200
    assert len(world.forms) == used  # a token with time left is not refreshed

    saved = json.loads(token.read_text())
    token.write_text(json.dumps({**saved, "expires_at": 0}))
    world.refuse_refresh = True
    status, data = request("/api/connect/microsoft/sync", {})
    assert status == 409 and "connect again" in data["error"]
    state = request("/api/connect")[1]["microsoft"]
    assert state["connected"] is False and "connect again" in state["error"] and not token.exists()


def test_a_slack_reaction_becomes_one_task_exactly_once(site, world, workspace):
    request = site
    world.reactions = [_reacted("C1", "1700000000.000100", "Old pin from last year")]
    query = sign_in(request, world, "slack")
    assert query["user_scope"] == "reactions:read" and "scope" not in query and query["code_challenge_method"] == "S256"
    assert urlsplit(query["redirect_uri"]).hostname == "localhost" and urlsplit(query["redirect_uri"]).path == "/slack/callback"
    assert "client_secret" not in world.forms[0] and world.forms[0]["code_verifier"]
    state = request("/api/connect")[1]["slack"]
    assert state["connected"] is True and state["account"] == "Northwind" and state["reaction"] == "pushpin"
    manifest = json.loads(state["manifest"])
    assert manifest["oauth_config"] == {"redirect_urls": [slack.REDIRECT], "scopes": {"user": ["reactions:read"]}, "pkce_enabled": True}
    assert "bot_user" not in manifest.get("features", {})

    with closing(db_connect(workspace.settings.db_path)) as conn:
        assert calendar.tasks(conn) == []  # what was already pinned when connecting makes no task

        world.reactions = [
            _reacted("C1", "1700000500.000200", "Send Priya the lab handout\nbefore Thursday"),
            _reacted("C1", "1700000600.000300", "Someone else pinned this", user="U0OTHER"),
            _reacted("C2", "1700000700.000400", "Mine, but another emoji", name="eyes"),
            *world.reactions,
        ]
        status, synced = request("/api/connect/slack/sync", {})
        assert status == 200 and synced["result"] == {"tasks": 1}
        tasks = calendar.tasks(conn)
        assert [(task["title"], task["description"]) for task in tasks] == [
            ("Send Priya the lab handout", "https://team.slack.example/archives/C1/p1700000500.000200")]

        assert request("/api/connect/slack/sync", {})[1]["result"] == {"tasks": 0}
        assert len(calendar.tasks(conn)) == 1
        capture = conn.execute("SELECT * FROM slack_captures WHERE task_id IS NOT NULL").fetchone()
        assert (capture["team"], capture["channel"], capture["task_id"]) == ("T0TEAM", "C1", tasks[0]["id"])


def test_disconnect_removes_the_tokens_and_the_outlook_calendars(site, world, workspace):
    request = site
    sign_in(request, world, "microsoft")
    sign_in(request, world, "slack")
    paths = [connect.token_path(workspace.settings, provider) for provider in connect.PROVIDERS]
    assert all(path.exists() for path in paths)
    for provider in connect.PROVIDERS:
        status, state = request(f"/api/connect/{provider}/disconnect", {})
        assert status == 200 and state[provider]["connected"] is False and state[provider]["account"] is None
        assert request(f"/api/connect/{provider}/sync", {})[0] == 409
    assert not any(path.exists() for path in paths)
    with closing(db_connect(workspace.settings.db_path)) as conn:
        assert not any("Outlook" in item["name"] for item in calendar.calendars(conn))
        assert conn.execute("SELECT count(*) FROM calendar_remote").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM connect_accounts").fetchone()[0] == 0
        # Nothing left behind shows on the calendar.
        window = ((datetime.now(UTC) - timedelta(days=2)).isoformat(), (datetime.now(UTC) + timedelta(days=9)).isoformat())
        assert calendar.occurrences(conn, *window) == []
