"""The phone surface: a second listener that serves only /phone/v1/* to scoped phone tokens."""
from __future__ import annotations

import http.client
import json
from contextlib import closing

import pytest

from kitty.core import phone
from kitty.db.connection import connect


class Recorder:
    def __init__(self):
        self.captured: list[dict] = []
        self.asked: list[str] = []

    def capture(self, conn, body):
        self.captured.append({"line": phone.capture_line(body)})
        return "Added to your Inbox"

    def ask(self, conn, text):
        self.asked.append(text)
        return f"Task added: {text}"

    def today(self, conn):
        return {"events": [], "tasks": [{"title": "Read chapter 3", "due": None}], "open_tasks": 1}


@pytest.fixture
def surface(workspace):
    db = workspace.settings.db_path
    recorder = Recorder()
    setup = phone.Setup()
    services = phone.Services(connect=lambda: connect(db), capture=recorder.capture, ask=recorder.ask, today=recorder.today)
    listener = phone.PhoneListener("127.0.0.1", services, setup, port=0)
    listener.start()

    def add(name="Zach's iPhone", scopes=("capture",)):
        with closing(connect(db)) as conn:
            return phone.create(conn, name, list(scopes))

    def request(method, path, body=None, *, token=None, headers=None):
        sent = {"Host": listener.authority}
        if token:
            sent["Authorization"] = f"Bearer {token}"
        payload = None
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        sent = {key: value for key, value in sent.items() if value is not None}
        client = http.client.HTTPConnection("127.0.0.1", listener.port, timeout=10)
        try:
            client.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in sent.items():
                client.putheader(key, value)
            if payload is not None:
                client.putheader("Content-Length", str(len(payload)))
            client.endheaders(payload)
            response = client.getresponse()
            raw = response.read()
            kind = response.getheader("Content-Type", "")
            return response.status, (json.loads(raw) if kind.startswith("application/json") else raw.decode()), response
        finally:
            client.close()

    yield type("Surface", (), {"request": staticmethod(request), "add": staticmethod(add), "recorder": recorder, "setup": setup, "db": db, "listener": listener})
    listener.stop()


def test_a_phone_adds_to_the_inbox_with_its_token_and_siri_gets_a_line_to_say(surface):
    _, token = surface.add()
    status, data, _ = surface.request("POST", "/phone/v1/capture", {"text": "  Buy oat milk "}, token=token)
    assert (status, data) == (200, {"say": "Added to your Inbox"})
    surface.request("POST", "/phone/v1/capture", {"url": "https://example.com/a", "title": "An article"}, token=token)
    assert [item["line"] for item in surface.recorder.captured] == ["Buy oat milk", "[An article](https://example.com/a)"]


@pytest.mark.parametrize("body", [{}, {"text": "   "}, {"text": 7}, {"url": "javascript:alert(1)"}, {"text": "x", "more": 1}, {"text": "x" * 4001}])
def test_a_capture_must_say_or_share_something_plain(surface, body):
    _, token = surface.add()
    assert surface.request("POST", "/phone/v1/capture", body, token=token)[0] == 400
    assert surface.recorder.captured == []


def test_each_route_needs_its_own_scope(surface):
    _, capture_only = surface.add()
    _, everything = surface.add("iPad", ("capture", "rika", "read"))
    assert surface.request("POST", "/phone/v1/rika", {"text": "add a task"}, token=capture_only)[0] == 403
    assert surface.request("GET", "/phone/v1/today", token=capture_only)[0] == 403
    assert surface.recorder.asked == []
    assert surface.request("POST", "/phone/v1/rika", {"text": "add a task read chapter 3"}, token=everything)[1] == {"say": "Task added: add a task read chapter 3"}
    assert surface.request("GET", "/phone/v1/today", token=everything)[1]["open_tasks"] == 1


def test_only_a_live_phone_token_is_accepted_and_a_removed_phone_stops_working(surface):
    added, token = surface.add()
    for bad in [None, "kittyphone_" + "A" * 43, token[:-1] + ("A" if token[-1] != "A" else "B"), "kitty_" + "A" * 43]:
        status, data, response = surface.request("POST", "/phone/v1/capture", {"text": "x"}, token=bad)
        assert status == 401 and response.getheader("WWW-Authenticate")
    with closing(connect(surface.db)) as conn:
        # The stored value is a hash: the token itself is nowhere in the database.
        assert token not in json.dumps([dict(row) for row in conn.execute("SELECT * FROM phones")])
        phone.revoke(conn, added["id"])
    assert surface.request("POST", "/phone/v1/capture", {"text": "x"}, token=token)[0] == 401
    assert surface.recorder.captured == []


def test_repeated_wrong_tokens_make_that_address_wait(surface):
    _, token = surface.add()
    for _ in range(phone.AUTH_FAILURES):
        assert surface.request("POST", "/phone/v1/capture", {"text": "x"}, token="kittyphone_" + "B" * 43)[0] == 401
    status, _, response = surface.request("POST", "/phone/v1/capture", {"text": "x"}, token=token)
    assert status == 429 and int(response.getheader("Retry-After")) > 0


def test_a_token_is_limited_to_thirty_requests_a_minute(surface):
    _, token = surface.add(scopes=("read",))
    statuses = [surface.request("GET", "/phone/v1/today", token=token)[0] for _ in range(phone.RATE_LIMIT + 1)]
    assert statuses == [200] * phone.RATE_LIMIT + [429]


def test_no_web_page_and_no_other_host_can_use_the_surface(surface):
    _, token = surface.add()
    for headers in [{"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}]:
        assert surface.request("POST", "/phone/v1/capture", {"text": "x"}, token=token, headers=headers)[0] == 403
    assert surface.request("POST", "/phone/v1/capture", {"text": "x"}, token=token, headers={"Host": "rebound.example:8771"})[0] == 404
    assert surface.recorder.captured == []


@pytest.mark.parametrize(("method", "path"), [("GET", "/"), ("GET", "/api/bootstrap"), ("GET", "/phone/v1/capture"), ("POST", "/phone/v1/today"), ("GET", "/phone/v2/today"), ("GET", "/index.html")])
def test_nothing_else_is_served(surface, method, path):
    _, token = surface.add(scopes=("capture", "rika", "read"))
    assert surface.request(method, path, {"text": "x"} if method == "POST" else None, token=token)[0] == 404


def test_bodies_must_be_small_json_objects(surface):
    _, token = surface.add()
    assert surface.request("POST", "/phone/v1/capture", b"[1]", token=token)[0] == 400
    assert surface.request("POST", "/phone/v1/capture", b"{not json", token=token)[0] == 400
    assert surface.request("POST", "/phone/v1/capture", {"text": "x"}, token=token, headers={"Content-Type": "text/plain"})[0] == 415
    assert surface.request("POST", "/phone/v1/capture", b'{"text":"' + b"x" * (phone.MAX_BODY) + b'"}', token=token)[0] == 413


def test_the_setup_code_shows_the_token_once(surface):
    _, token = surface.add()
    code = surface.setup.issue(token, "Zach's <iPhone>")["code"]
    # Safari opens the page: a browser's fetch headers are fine here, and only here.
    status, page, _ = surface.request("GET", f"/phone/v1/setup/{code.lower()}", headers={"Sec-Fetch-Mode": "navigate"})
    assert status == 200 and token in page and "Zach&#x27;s &lt;iPhone&gt;" in page and f"http://{surface.listener.authority}" in page
    assert surface.request("GET", f"/phone/v1/setup/{code}")[0] == 404


def test_wrong_setup_codes_retire_the_waiting_ones_and_codes_expire():
    clock = [0.0]
    setup = phone.Setup(clock=lambda: clock[0])
    code = setup.issue("kittyphone_token", "Phone")["code"]
    wrong = "".join("A" if char != "A" else "B" for char in code)
    for _ in range(phone.CODE_TRIES):
        assert setup.redeem(wrong) is None
    assert setup.redeem(code) is None
    later = setup.issue("kittyphone_token", "Phone")["code"]
    clock[0] = phone.CODE_TTL + 1
    assert setup.redeem(later) is None


@pytest.mark.parametrize(("name", "scopes"), [("", ["capture"]), ("x" * 81, ["capture"]), ("Phone", []), ("Phone", ["capture", "capture"]), ("Phone", ["admin"]), ("Pho\x00ne", ["capture"]), ("Phone", "capture")])
def test_a_phone_needs_a_plain_name_and_known_scopes(workspace, name, scopes):
    with closing(connect(workspace.settings.db_path)) as conn, pytest.raises(phone.PhoneError):
        phone.create(conn, name, scopes)


def test_the_network_is_off_until_chosen(workspace):
    with closing(connect(workspace.settings.db_path)) as conn:
        assert phone.network(conn) == "off"
        assert phone.set_network(conn, "tailscale") == "tailscale" and phone.network(conn) == "tailscale"
        with pytest.raises(phone.PhoneError):
            phone.set_network(conn, "everywhere")
