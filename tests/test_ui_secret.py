"""The per-launch UI secret (kitty.web_auth): local programs that forge every browser
header still reach no /api route, upgrade or terminal token without it."""
from __future__ import annotations

import http.client
import json
import threading
import time
from types import SimpleNamespace

import pytest

from kitty import web_auth
from kitty.web_server import ROUTE_MODULES, _announce_links, _link
from terminal_support import make_root, site, websocket

#: One path per route family: every module prefix plus the always-on core routes.
CORE_ROUTES = (
    "/api", "/api/bootstrap", "/api/tasks/1", "/api/modules", "/api/brain", "/api/reload",
    "/api/ai/status", "/api/atlas", "/api/desktop", "/api/desktop/command", "/api/live/session",
    "/api/build", "/api/build/export", "/api/clients", "/api/clients/approve", "/api/keys",
    "/api/devices", "/api/search", "/api/session/link", "/api/not-a-route",
)
ROUTES = tuple(sorted({prefix for prefix, _module in ROUTE_MODULES} | set(CORE_ROUTES)))


@pytest.fixture
def running(tmp_path):
    with site(make_root(tmp_path, ("workspace", "terminal"))) as server:
        yield server


def forged(running) -> dict[str, str | None]:
    """Everything rika!'s page sends except the secret: exact Host and Origin, X-Kitty-Request."""
    return {"Origin": running.server.origin, web_auth.HEADER: None}


def refused(response) -> bool:
    status, body = response
    return status == 401 and body == {"error": web_auth.REFUSAL}


@pytest.mark.parametrize("path", ROUTES)
def test_every_route_family_refuses_perfect_page_headers_without_the_secret(running, path):
    assert refused(running.request(path, headers=forged(running)))
    assert refused(running.request(path, {}, headers=forged(running)))
    assert running.request(path, method="HEAD", headers=forged(running))[0] == 401


def test_a_wrong_repeated_or_reshaped_secret_is_refused_alike(running):
    secret = running.secret
    for value in ("", "0" * 64, secret.upper(), secret[:-1], secret + "0", f"{secret} x", "é" * 64):
        assert refused(running.request("/api/bootstrap", headers={web_auth.HEADER: value})), value
        assert refused(running.request("/api/quick-notes/create", {"body": "x"}, headers={web_auth.HEADER: value})), value
    # Two copies, even both right, are not one secret.
    client = http.client.HTTPConnection("127.0.0.1", running.port, timeout=5)
    try:
        client.putrequest("GET", "/api/bootstrap")
        client.putheader("Origin", running.server.origin)
        client.putheader(web_auth.HEADER, secret)
        client.putheader(web_auth.HEADER, secret)
        client.endheaders()
        response = client.getresponse()
        assert response.status == 401 and response.getheader("WWW-Authenticate") == 'Kitty realm="rika!"'
        assert secret not in response.read().decode()
    finally:
        client.close()


def test_the_right_secret_works_and_the_page_itself_needs_none(running):
    assert running.request("/api/bootstrap")[0] == 200
    assert running.request("/api/quick-notes/create", {"body": "with the secret"})[0] == 200
    assert running.request("/", headers={web_auth.HEADER: None})[0] == 200
    # The browser-mode link's code is the page's to read; the server does not check it there.
    assert running.request("/?code=abc", headers={web_auth.HEADER: None})[0] == 200
    # The older guards still apply first: a token, a foreign Origin or a missing page header.
    assert running.request("/api/bootstrap", headers={"Origin": "https://attacker.example"})[0] == 403
    assert running.request("/api/bootstrap", headers={"Authorization": "Bearer x"})[0] == 403
    assert running.request("/api/quick-notes/create", {"body": "x"}, headers={"X-Kitty-Request": None})[0] == 403


def test_each_launch_has_its_own_secret(running, tmp_path):
    with site(make_root(tmp_path / "other", ("workspace",))) as other:
        assert other.secret != running.secret and len(other.secret) == 64
        assert refused(other.request("/api/bootstrap", headers={web_auth.HEADER: running.secret}))


def test_ingress_keeps_its_bearer_tokens_and_needs_no_secret(running):
    body = {"name": "Tern plugin", "kind": "tern-plugin", "scopes": ["read"]}
    status, answer = running.request("/api/ingress/pair", body, headers={"Origin": None, "X-Kitty-Request": None,
                                                                          web_auth.HEADER: None})
    assert status == 202, answer
    status, _answer = running.request("/api/ingress/recent", headers={"Origin": None, web_auth.HEADER: None})
    assert status == 401, "the ingress answers with its own bearer refusal"


def test_the_terminal_token_cannot_be_minted_without_the_secret(running):
    assert refused(running.request("/api/terminal/session", {}, headers=forged(running)))
    assert refused(running.request("/api/live/session", {}, headers=forged(running)))
    status, body = running.request("/api/terminal/session", {})
    assert status == 200 and body["token"]


@pytest.mark.parametrize("session, socket", [("/api/live/session", "/api/live/socket"),
                                             ("/api/terminal/session", "/api/terminal/socket")])
def test_websocket_upgrades_need_the_secret_as_a_subprotocol(running, session, socket):
    token = running.request(session, {})[1]["token"]
    path = f"{socket}?token={token}"
    protocol = "Sec-WebSocket-Protocol"
    for offered in (None, "kitty", f"kitty, kitty-secret.{'0' * 64}", f"kitty-secret.{running.secret}",
                    f"kitty, kitty, kitty-secret.{running.secret}", f"chat, kitty-secret.{running.secret}",
                    f"kitty, kitty-secret.{running.secret}, kitty-secret.{running.secret}"):
        status, headers, connection, rest = websocket(running.port, path, {protocol: offered})
        connection.close()
        assert status == 401, offered
        assert json.loads(rest) == {"error": web_auth.REFUSAL}
    if socket == "/api/live/socket":
        # The refused upgrades never spent the token; the page's own offer opens it.
        status, headers, connection, _rest = websocket(running.port, path, {}, secret=running.secret)
        connection.close()
        assert status == 101 and headers["sec-websocket-protocol"] == web_auth.PROTOCOL


def test_the_bootstrap_code_is_single_use_and_short_lived(running):
    page = {web_auth.HEADER: None}
    code = running.server.ui_auth.issue_code()
    status, body = running.request(web_auth.SESSION_PATH, {"code": code}, headers=page)
    assert status == 200 and body == {"secret": running.secret}
    assert refused(running.request(web_auth.SESSION_PATH, {"code": code}, headers=page))
    # A new code replaces the old one; neither is valid once it lapses.
    first = running.server.ui_auth.issue_code()
    second = running.server.ui_auth.issue_code()
    assert refused(running.request(web_auth.SESSION_PATH, {"code": first}, headers=page))
    clock = running.server.ui_auth._clock
    running.server.ui_auth._clock = lambda: clock() + web_auth.CODE_TTL + 1
    assert refused(running.request(web_auth.SESSION_PATH, {"code": second}, headers=page))
    running.server.ui_auth._clock = clock
    # The exchange is a page mutation like any other: exact Origin and the page header.
    code = running.server.ui_auth.issue_code()
    assert running.request(web_auth.SESSION_PATH, {"code": code},
                           headers={**page, "Origin": "https://attacker.example"})[0] == 403
    assert running.request(web_auth.SESSION_PATH, {"code": code}, headers={**page, "X-Kitty-Request": None})[0] == 403
    assert running.request(web_auth.SESSION_PATH, {"code": code, "x": 1}, headers=page)[0] == 400
    assert running.request(web_auth.SESSION_PATH, {"code": code}, headers=page)[0] == 200
    assert refused(running.request(web_auth.SESSION_PATH, {"code": 7}, headers=page))


def test_a_link_ticket_opens_one_exact_get_once(running):
    page = {web_auth.HEADER: None}
    running.request("/api/quick-notes/create", {"body": "linked"})
    status, body = running.request(web_auth.LINK_PATH, {"path": "/api/quick-notes"})
    assert status == 200 and body["url"].startswith("/api/quick-notes?ticket=")
    assert running.secret not in body["url"]
    status, listed = running.request(body["url"], headers=page)
    assert status == 200 and listed["notes"][0]["body"] == "linked"
    assert refused(running.request(body["url"], headers=page)), "single use"
    # Bound to the exact path and query.
    url = running.request(web_auth.LINK_PATH, {"path": "/api/build/export"})[1]["url"]
    ticket = url.rsplit("=", 1)[1]
    assert refused(running.request(f"/api/quick-notes?ticket={ticket}", headers=page))
    url = running.request(web_auth.LINK_PATH, {"path": "/api/notes/pages?notebook_id=1"})[1]["url"]
    assert url.startswith("/api/notes/pages?notebook_id=1&ticket=")
    assert refused(running.request(url.replace("notebook_id=1", "notebook_id=2"), headers=page))
    # Short-lived.
    url = running.request(web_auth.LINK_PATH, {"path": "/api/quick-notes"})[1]["url"]
    clock = running.server.ui_auth._clock
    running.server.ui_auth._clock = lambda: clock() + web_auth.LINK_TTL + 1
    assert refused(running.request(url, headers=page))
    running.server.ui_auth._clock = clock
    # Minting needs the secret; links reach only rika!'s own API downloads.
    assert refused(running.request(web_auth.LINK_PATH, {"path": "/api/quick-notes"}, headers=forged(running)))
    for bad in ("/", "/index.html", "/api/ingress/recent", web_auth.SESSION_PATH, web_auth.LINK_PATH,
                "/api/quick-notes?ticket=x", "https://example.com/api/x", "/api/../x"):
        assert running.request(web_auth.LINK_PATH, {"path": bad})[0] == 400, bad
    assert running.request(web_auth.LINK_PATH, {"path": 1})[0] == 400


def test_the_secret_object_never_shows_its_value():
    auth = web_auth.UiAuth()
    assert auth.secret not in repr(auth) and auth.secret not in str(auth)
    assert len(auth.secret) == 64 and int(auth.secret, 16) >= 0
    assert web_auth.UiAuth().secret != auth.secret
    assert not auth.valid(None) and not auth.valid(b"x") and not auth.valid(auth.secret + " ")
    assert auth.protocol_secret([f"kitty, kitty-secret.{auth.secret}"])
    assert auth.protocol_secret(["kitty", f"kitty-secret.{auth.secret}"])
    assert not auth.protocol_secret([]) and not auth.protocol_secret([f"kitty-secret.{auth.secret}"])


def test_codes_and_tickets_follow_their_clock():
    now = [100.0]
    auth = web_auth.UiAuth(clock=lambda: now[0])
    code = auth.issue_code()
    now[0] += web_auth.CODE_TTL - 1
    assert auth.redeem(code) == auth.secret and auth.redeem(code) is None
    code = auth.issue_code()
    now[0] += web_auth.CODE_TTL
    assert auth.redeem(code) is None
    ticket = auth.issue_link("a")
    assert not auth.follow(ticket, "b") and not auth.follow(ticket, "a"), "spent by the wrong target"
    tickets = [auth.issue_link(str(index)) for index in range(web_auth.MAX_LINKS + 5)]
    assert not auth.follow(tickets[0], "0") and auth.follow(tickets[-1], str(web_auth.MAX_LINKS + 4))


def test_kitty_web_prints_a_fresh_link_once_one_is_used_and_never_the_secret(capsys):
    server = SimpleNamespace(origin="http://127.0.0.1:8765", ui_auth=web_auth.UiAuth())
    link = _link(server)
    assert link.startswith("http://127.0.0.1:8765/?code=")
    stop = threading.Event()
    worker = threading.Thread(target=_announce_links, args=(server, link, stop), daemon=True)
    worker.start()
    assert server.ui_auth.redeem(link.rsplit("=", 1)[1]) == server.ui_auth.secret
    printed, deadline = "", time.monotonic() + 5
    while "next link" not in printed and time.monotonic() < deadline:
        time.sleep(0.05)
        printed += capsys.readouterr().out
    stop.set()
    worker.join(timeout=5)
    assert "rika! next link" in printed and "?code=" in printed
    assert server.ui_auth.secret not in printed
    fresh = printed.strip().rsplit("=", 1)[1]
    assert server.ui_auth.redeem(fresh) == server.ui_auth.secret
