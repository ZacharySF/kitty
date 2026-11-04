"""Paired client ingress over HTTP: pairing, scoped bearer tokens, revocation and CORS (Q7)."""
from __future__ import annotations

import http.client
import re
import json

import pytest
from terminal_support import make_root, site
from test_web_blocks import Client

from kitty.core import endpoint

EXTENSION = "chrome-extension://" + "b" * 32
OTHER_EXTENSION = "chrome-extension://" + "c" * 32


@pytest.fixture
def running(tmp_path):
    with site(make_root(tmp_path, ("workspace",))) as server:
        yield server


def call(running, method, path, body=None, *, token=None, origin=None, headers=None):
    """A request as an outside program sends it: no kitty page headers unless given."""
    fields = {}
    payload = None
    if body is not None:
        fields["Content-Type"] = "application/json"
        payload = json.dumps(body).encode()
    if token is not None:
        fields["Authorization"] = f"Bearer {token}"
    if origin is not None:
        fields["Origin"] = origin
    fields.update(headers or {})
    connection = http.client.HTTPConnection("127.0.0.1", running.port, timeout=10)
    try:
        connection.request(method, path, body=payload, headers=fields)
        response = connection.getresponse()
        data = response.read()
        if response.getheader("Content-Type", "").startswith("application/json"):
            data = json.loads(data) if data else None
        return response.status, data, {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()


def pair(running, *, scopes=("capture", "read"), kind="tern-plugin", origin=None, name="Tern plugin"):
    status, ticket, _ = call(running, "POST", "/api/ingress/pair", {"name": name, "kind": kind, "scopes": list(scopes)},
                             origin=origin)
    assert status == 202, ticket
    status, listed = running.request("/api/clients")
    assert status == 200
    [waiting] = [item for item in listed["requests"] if item["code"] == ticket["code"]]
    assert running.request("/api/clients/approve", {"id": waiting["id"]})[0] == 200
    status, result, _ = call(running, "POST", "/api/ingress/pair/status", {"request": ticket["request"]}, origin=origin)
    assert status == 200 and result["status"] == "approved", result
    return result["client"], result["token"]


def test_pairing_path_in_the_endpoint_file_is_the_pairing_route():
    assert endpoint.PAIRING_PATH == "/api/ingress/pair"


def test_a_client_pairs_after_the_user_approves_and_is_listed(running):
    status, ticket, _ = call(running, "POST", "/api/ingress/pair", {"name": "Tern plugin", "kind": "tern-plugin",
                                                                     "scopes": ["capture"]})
    assert status == 202 and ticket["status"] == "pending"
    status, pending, _ = call(running, "POST", "/api/ingress/pair/status", {"request": ticket["request"]})
    assert status == 200 and pending == {"status": "pending"}
    status, listed = running.request("/api/clients")
    assert status == 200 and listed["clients"] == []
    [waiting] = listed["requests"]
    assert waiting["code"] == ticket["code"] and waiting["kind"] == "tern-plugin" and "request" not in waiting
    status, after = running.request("/api/clients/approve", {"id": waiting["id"]})
    assert status == 200 and after["requests"] == []
    client, token = None, None
    status, result, _ = call(running, "POST", "/api/ingress/pair/status", {"request": ticket["request"]})
    assert status == 200 and result["status"] == "approved"
    client, token = result["client"], result["token"]
    status, listed = running.request("/api/clients")
    [row] = listed["clients"]
    assert row["id"] == client["id"] and row["name"] == "Tern plugin" and row["scopes"] == ["capture"]
    assert {"created_at", "last_used_at", "kind", "origin"} <= row.keys()
    assert token not in json.dumps(listed)
    status, me, _ = call(running, "GET", "/api/ingress/client", token=token)
    assert status == 200 and me["name"] == "Tern plugin" and me["scopes"] == ["capture"]


def test_a_denied_request_gets_no_token(running):
    status, ticket, _ = call(running, "POST", "/api/ingress/pair", {"name": "Probe", "kind": "tern-plugin", "scopes": ["read"]})
    assert status == 202
    [waiting] = running.request("/api/clients")[1]["requests"]
    assert running.request("/api/clients/deny", {"id": waiting["id"]})[0] == 200
    status, result, _ = call(running, "POST", "/api/ingress/pair/status", {"request": ticket["request"]})
    assert status == 200 and result == {"status": "denied"}
    assert running.request("/api/clients")[1] == {"clients": [], "requests": [], "address": running.server.origin}


def test_pairing_requests_are_rate_limited(running):
    for index in range(3):
        assert call(running, "POST", "/api/ingress/pair", {"name": f"App {index}", "kind": "tern-plugin", "scopes": ["read"]})[0] == 202
    status, _, headers = call(running, "POST", "/api/ingress/pair", {"name": "Flood", "kind": "tern-plugin", "scopes": ["read"]})
    assert status == 429 and "retry-after" in headers


def test_the_pairing_ui_routes_keep_the_browser_guard(running):
    call(running, "POST", "/api/ingress/pair", {"name": "Tern plugin", "kind": "tern-plugin", "scopes": ["read"]})
    [waiting] = running.request("/api/clients")[1]["requests"]
    # A local program cannot approve itself through kitty's page routes without the page's headers.
    status, _, _ = call(running, "POST", "/api/clients/approve", {"id": waiting["id"]})
    assert status == 403
    status, _, _ = call(running, "POST", "/api/clients/approve", {"id": waiting["id"]},
                        origin="https://attacker.example", headers={"X-Kitty-Request": "1"})
    assert status == 403
    # Nor with every page header forged exactly: only kitty's own window has the UI secret.
    status, body, headers = call(running, "POST", "/api/clients/approve", {"id": waiting["id"]},
                                 origin=running.server.origin, headers={"X-Kitty-Request": "1"})
    assert status == 401 and headers["www-authenticate"].startswith("Kitty")
    assert running.server.ui_auth.secret not in json.dumps(body)
    assert running.request("/api/clients")[1]["requests"][0]["id"] == waiting["id"]


def test_ingress_refuses_web_pages_wrong_hosts_and_non_json(running):
    body = {"name": "Page", "kind": "tern-plugin", "scopes": ["read"]}
    assert call(running, "POST", "/api/ingress/pair", body, origin="https://attacker.example")[0] == 403
    assert call(running, "POST", "/api/ingress/pair", body, origin="null")[0] == 403
    assert call(running, "POST", "/api/ingress/pair", body, headers={"Host": "attacker.example"})[0] == 403
    status, _, _ = call(running, "POST", "/api/ingress/pair", headers={"Content-Type": "text/plain", "Content-Length": "2"})
    assert status == 415
    assert call(running, "PUT", "/api/ingress/pair", body)[0] in {405, 501}


def test_a_token_reaches_only_its_scopes_and_never_the_rest_of_the_api(running):
    _, capture = pair(running, scopes=("capture",), name="Capture only")
    _, reader = pair(running, scopes=("read",), name="Read only")
    assert call(running, "POST", "/api/ingress/notes", {"text": "from the plugin"}, token=capture)[0] == 200
    assert call(running, "GET", "/api/ingress/recent", token=capture)[0] == 403
    assert call(running, "GET", "/api/ingress/recent", token=reader)[0] == 200
    assert call(running, "POST", "/api/ingress/notes", {"text": "nope"}, token=reader)[0] == 403
    assert call(running, "POST", "/api/ingress/inbox", {"blocks": [{"text": "nope"}]}, token=reader)[0] == 403
    assert call(running, "POST", "/api/ingress/mirror", {"url": "https://example.com/", "title": "x"}, token=reader)[0] == 403
    assert call(running, "GET", "/api/ingress/not-a-route", token=capture)[0] == 404
    # Without a valid token nothing past pairing answers.
    status, _, headers = call(running, "GET", "/api/ingress/recent")
    assert status == 401 and headers["www-authenticate"].startswith("Bearer")
    assert call(running, "GET", "/api/ingress/recent", token="kitty_" + "x" * 43)[0] == 401
    assert call(running, "GET", "/api/ingress/recent", headers={"Authorization": f"Basic {reader}"})[0] == 401
    # Every other route refuses a client token, even with the page headers forged.
    forged = {"Origin": running.server.origin, "X-Kitty-Request": "1"}
    for token in (capture, reader):
        assert call(running, "GET", "/api/quick-notes", token=token, headers=forged)[0] == 403
        assert call(running, "GET", "/api/clients", token=token, headers=forged)[0] == 403
        assert call(running, "POST", "/api/quick-notes/create", {"body": "x"}, token=token, headers=forged)[0] == 403
        assert call(running, "POST", "/api/clients/revoke", {"id": "x"}, token=token, headers=forged)[0] == 403
    assert running.request("/api/quick-notes")[1]["notes"][0]["body"] == "from the plugin"


def test_revoking_a_client_ends_its_token(running):
    client, token = pair(running, scopes=("read",))
    assert call(running, "GET", "/api/ingress/recent", token=token)[0] == 200
    status, listed = running.request("/api/clients/revoke", {"id": client["id"]})
    assert status == 200 and listed["clients"] == []
    assert call(running, "GET", "/api/ingress/recent", token=token)[0] == 401
    assert running.request("/api/clients/revoke", {"id": client["id"]})[0] == 404


def test_the_browser_origin_guard_is_unchanged_for_normal_routes(running):
    assert running.request("/api/quick-notes")[0] == 200
    assert running.request("/api/quick-notes/create", {"body": "page"})[0] == 200
    assert running.request("/api/quick-notes/create", {"body": "x"}, headers={"X-Kitty-Request": None})[0] == 403
    assert running.request("/api/quick-notes", headers={"Origin": "https://attacker.example"})[0] == 403
    assert running.request("/api/quick-notes", headers={"Origin": EXTENSION})[0] == 403
    assert call(running, "OPTIONS", "/api/quick-notes", origin=EXTENSION,
                headers={"Access-Control-Request-Method": "POST"})[0] == 405


def preflight(running, path, origin, method="POST", request_headers="authorization, content-type", extra=None):
    headers = {"Access-Control-Request-Method": method, "Access-Control-Request-Headers": request_headers, **(extra or {})}
    return call(running, "OPTIONS", path, origin=origin, headers=headers)


def test_cors_answers_only_the_recorded_extension_origin(running):
    # Any extension may ask to pair; web pages may not.
    status, _, headers = preflight(running, "/api/ingress/pair", EXTENSION, request_headers="content-type",
                                   extra={"Access-Control-Request-Private-Network": "true"})
    assert status == 204 and headers["access-control-allow-origin"] == EXTENSION
    assert "POST" in headers["access-control-allow-methods"]
    assert headers["access-control-allow-private-network"] == "true" and headers["vary"] == "Origin"
    assert preflight(running, "/api/ingress/pair", "https://attacker.example")[0] == 403
    # Before pairing, no extension origin may call the token routes.
    status, _, headers = preflight(running, "/api/ingress/notes", EXTENSION)
    assert status == 403 and "access-control-allow-origin" not in headers
    client, token = pair(running, kind="browser-extension", origin=EXTENSION, scopes=("capture",))
    assert client["origin"] == EXTENSION
    status, _, headers = preflight(running, "/api/ingress/notes", EXTENSION)
    assert status == 204 and headers["access-control-allow-origin"] == EXTENSION
    assert "authorization" in headers["access-control-allow-headers"].lower()
    assert preflight(running, "/api/ingress/notes", OTHER_EXTENSION)[0] == 403
    assert preflight(running, "/api/ingress/notes", EXTENSION, request_headers="x-other")[0] == 403
    assert preflight(running, "/api/ingress/notes", EXTENSION, method="DELETE")[0] == 403
    status, _, headers = call(running, "POST", "/api/ingress/notes", {"text": "from the side panel"}, token=token, origin=EXTENSION)
    assert status == 200 and headers["access-control-allow-origin"] == EXTENSION
    # The token is bound to the origin that paired: another extension, a page or no origin is refused.
    status, _, headers = call(running, "POST", "/api/ingress/notes", {"text": "stolen"}, token=token, origin=OTHER_EXTENSION)
    assert status == 403 and "access-control-allow-origin" not in headers
    assert call(running, "POST", "/api/ingress/notes", {"text": "stolen"}, token=token)[0] == 403
    assert call(running, "POST", "/api/ingress/notes", {"text": "stolen"}, token=token, origin="https://attacker.example")[0] == 403
    # A non-extension client's token is refused from a browser origin.
    _, plugin = pair(running, scopes=("capture",))
    assert call(running, "POST", "/api/ingress/notes", {"text": "x"}, token=plugin, origin=EXTENSION)[0] == 403
    # Revoking the extension closes its preflight too.
    running.request("/api/clients/revoke", {"id": client["id"]})
    assert preflight(running, "/api/ingress/notes", EXTENSION)[0] == 403


def test_captures_land_in_the_op_log_and_push_to_open_windows(running):
    client, token = pair(running, scopes=("capture", "read"))
    window = Client(running)
    try:
        status, note, _ = call(running, "POST", "/api/ingress/notes", {"text": "Quick from Tern"}, token=token)
        assert status == 200 and note["body"] == "Quick from Tern"
        change = window.expect("change")
        assert change["root"] == {"type": "quick_note", "number": note["id"]}
        # History records the capture under the paired client, by its pairing name.
        assert change["actor"] == {"kind": "client", "name": "Tern plugin", "session": client["id"]}

        status, inbox, _ = call(running, "POST", "/api/ingress/inbox",
                                {"blocks": [{"text": "first capture"}, {"type": "to_do", "text": "follow up"}]}, token=token)
        assert status == 200 and len(inbox["blocks"]) == 2
        change = window.expect("change")
        assert change["root"] == {"type": "page", "number": inbox["page"]}
        texts = [op.get("text") for op in change["ops"] if op["op"] == "insert"]
        assert "first capture" in texts and "follow up" in texts

        status, again, _ = call(running, "POST", "/api/ingress/inbox", {"blocks": [{"text": "second"}]}, token=token)
        assert status == 200 and again["page"] == inbox["page"]
        assert window.expect("change")["root"]["number"] == inbox["page"]

        status, mirror, _ = call(running, "POST", "/api/ingress/mirror",
                                 {"url": "https://example.com/article", "title": "An article", "selection": "quoted bit",
                                  "text": "First paragraph.\n\nSecond paragraph."}, token=token)
        assert status == 200 and mirror["page"] == inbox["page"]
        change = window.expect("change")
        inserted = [op for op in change["ops"] if op["op"] == "insert"]
        assert inserted[0]["text"] == "An article"
        assert inserted[0]["props"]["source_url"] == "https://example.com/article"
        assert {"quoted bit", "First paragraph.", "Second paragraph."} <= {op.get("text") for op in inserted}

        status, page = running.request(f"/api/blocks/page?type=page&number={inbox['page']}")
        assert status == 200
        texts = [block["text"] for block in page["blocks"] if block["type"] != "canvas"]
        assert texts[:3] == ["first capture", "follow up", "second"] and "An article" in texts

        status, recent, _ = call(running, "GET", "/api/ingress/recent", token=token)
        assert status == 200 and recent["inbox"] == inbox["page"]
        assert any(item["title"] == "Inbox" for item in recent["pages"])
        assert set(recent) == {"inbox", "pages", "tasks"}
        assert all(set(item) <= {"number", "title", "updated_at"} for item in recent["pages"])
    finally:
        window.close()


def test_mirror_and_capture_bodies_are_validated(running):
    _, token = pair(running, scopes=("capture",))
    assert call(running, "POST", "/api/ingress/notes", {"text": "x", "extra": 1}, token=token)[0] == 400
    assert call(running, "POST", "/api/ingress/inbox", {"blocks": []}, token=token)[0] == 400
    assert call(running, "POST", "/api/ingress/inbox", {"blocks": [{"type": "canvas", "text": "x"}]}, token=token)[0] == 400
    assert call(running, "POST", "/api/ingress/mirror", {"url": "javascript:alert(1)", "title": "x"}, token=token)[0] == 400
    assert call(running, "POST", "/api/ingress/mirror", {"url": "https://example.com/", "title": ""}, token=token)[0] == 400


def test_capture_and_read_need_the_workspace_module(tmp_path):
    with site(make_root(tmp_path, ("terminal",))) as running:
        _, token = pair(running, scopes=("capture", "read"))
        assert call(running, "POST", "/api/ingress/notes", {"text": "x"}, token=token)[0] == 403
        assert call(running, "GET", "/api/ingress/recent", token=token)[0] == 403


def test_the_kitty_tern_plugin_request_sequence(running):
    """The requests integrations/tern/lib/client.luau makes, with the shapes it reads back."""
    tern = {"Accept": "application/json", "User-Agent": "Tern"}
    # lib/core.luau accepts only a loopback origin with a port, as rika! publishes it.
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+", running.server.origin)
    status, ticket, _ = call(running, "POST", "/api/ingress/pair",
                             {"name": "Tern", "kind": "tern-plugin", "scopes": ["capture", "read"]}, headers=tern)
    assert status == 202 and {"request", "code", "expires_in"} <= set(ticket)
    poll = {"request": ticket["request"]}
    assert call(running, "POST", "/api/ingress/pair/status", poll, headers=tern)[1] == {"status": "pending"}
    [waiting] = running.request("/api/clients")[1]["requests"]
    assert waiting["code"] == ticket["code"] and waiting["name"] == "Tern"
    running.request("/api/clients/approve", {"id": waiting["id"]})
    approved = call(running, "POST", "/api/ingress/pair/status", poll, headers=tern)[1]
    token = approved["token"]
    assert approved["status"] == "approved" and approved["client"]["scopes"] == ["capture", "read"]

    status, me, _ = call(running, "GET", "/api/ingress/client", token=token, headers=tern)
    assert status == 200 and me["kind"] == "tern-plugin" and me["scopes"] == ["capture", "read"]
    blocks = [{"type": "quote", "text": "selected"}, {"type": "text", "text": "From Tern · bash · /tmp"},
              {"type": "code", "text": "$ make\nerror"}]
    status, saved, _ = call(running, "POST", "/api/ingress/inbox", {"blocks": blocks}, token=token, headers=tern)
    assert status == 200 and isinstance(saved["page"], int)
    status, note, _ = call(running, "POST", "/api/ingress/notes", {"text": "typed in Tern"}, token=token, headers=tern)
    assert status == 200 and isinstance(note["id"], int)
    assert running.request("/api/calendar/tasks", {"title": "Synthetic task", "due": "2026-11-05"})[0] == 200
    status, recent, _ = call(running, "GET", "/api/ingress/recent?limit=20", token=token, headers=tern)
    assert status == 200 and recent["inbox"] == saved["page"]
    assert all(isinstance(page["number"], int) and isinstance(page["title"], str) for page in recent["pages"])
    assert [(task["title"], task["due"]) for task in recent["tasks"]] == [("Synthetic task", "2026-11-05")]
    assert all(set(task) == {"id", "title", "due"} for task in recent["tasks"])

    # Revoked: 401 (lib/core.luau's "unauthorized"), then a fresh pairing works at once.
    running.request("/api/clients/revoke", {"id": approved["client"]["id"]})
    status, refused, headers = call(running, "POST", "/api/ingress/notes", {"text": "x"}, token=token, headers=tern)
    assert status == 401 and headers["www-authenticate"].startswith("Bearer")
    _, fresh = pair(running, name="Tern")
    assert call(running, "POST", "/api/ingress/notes", {"text": "again"}, token=fresh, headers=tern)[0] == 200
