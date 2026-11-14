"""Settings → Connected apps → Phone: the page routes that turn the surface on and add phones."""
from __future__ import annotations

import http.client
import json
import threading

import pytest

from kitty import web_auth, web_phone
from kitty.core import phone
from kitty.web_server import LocalHTTPServer, WebApplication


@pytest.fixture
def site(workspace, tmp_path, monkeypatch):
    # The "Wi-Fi" of the test is loopback, on a free port.
    monkeypatch.setattr(phone, "addresses", lambda: {"wifi": "127.0.0.1", "tailscale": None})
    monkeypatch.setattr(phone, "PORT", 0)
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
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        try:
            client.request("GET" if body is None else "POST", path, body=payload, headers=headers)
            response = client.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            client.close()

    yield request, app
    server.shutdown()
    server.server_close()
    app.close()
    worker.join(timeout=5)


def phone_request(address, method, path, body=None, token=None):
    host, port = address.removeprefix("http://").split(":")
    headers = {"Host": f"{host}:{port}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    client = http.client.HTTPConnection(host, int(port), timeout=15)
    try:
        client.request(method, path, body=payload, headers=headers)
        response = client.getresponse()
        raw = response.read()
        return response.status, raw.decode()
    finally:
        client.close()


def test_the_surface_is_off_until_a_network_is_chosen_and_a_phone_needs_it_on(site):
    request, app = site
    status, state = request("/api/phone")
    assert status == 200 and state["network"] == "off" and state["listening"] is False and state["phones"] == []
    status, data = request("/api/phone/devices", {"name": "iPhone", "scopes": ["capture"]})
    assert status == 409 and "network" in data["error"]
    status, state = request("/api/phone/network", {"network": "tailscale"})
    assert status == 200 and state["listening"] is False and "Tailscale" in state["error"]
    status, state = request("/api/phone/network", {"network": "wifi"})
    assert status == 200 and state["listening"] is True and state["address"].startswith("http://127.0.0.1:")
    assert request("/api/phone/network", {"network": "off"})[1]["listening"] is False
    assert request("/api/phone/network", {"network": "lan"})[0] == 400


def test_adding_a_phone_gives_a_setup_address_and_the_phone_then_captures_into_the_inbox(site, workspace):
    request, app = site
    request("/api/phone/network", {"network": "wifi"})
    status, added = request("/api/phone/devices", {"name": "Zach's iPhone", "scopes": ["capture", "read"]})
    assert status == 200 and [item["name"] for item in added["phones"]] == ["Zach's iPhone"]
    # The window never sees the token: only the address the phone opens.
    assert "kittyphone_" not in json.dumps(added)
    setup = added["setup"]
    assert setup["url"] == f"{added['address']}/phone/v1/setup/{setup['code']}" and setup["expires_in"] == 300
    status, page = phone_request(added["address"], "GET", f"/phone/v1/setup/{setup['code']}")
    token = page.split('id="token">')[1].split("<")[0]
    assert status == 200 and token.startswith("kittyphone_")
    status, said = phone_request(added["address"], "POST", "/phone/v1/capture", {"text": "Call the dentist"}, token)
    assert (status, json.loads(said)) == (200, {"say": "Added to your Inbox"})
    from contextlib import closing
    from kitty.core import ingress
    from kitty.db.connection import connect
    with closing(connect(workspace.settings.db_path)) as conn:
        assert ingress.recent(conn)["inbox"] == 1
        assert conn.execute("SELECT COUNT(*) FROM blocks WHERE text=?", ("Call the dentist",)).fetchone()[0] == 1
    status, today = phone_request(added["address"], "GET", "/phone/v1/today", token=token)
    assert status == 200 and set(json.loads(today)) == {"events", "tasks", "open_tasks"}
    # Removing the phone ends its token.
    assert request("/api/phone/devices/remove", {"id": added["phones"][0]["id"]})[1]["phones"] == []
    assert phone_request(added["address"], "POST", "/phone/v1/capture", {"text": "again"}, token)[0] == 401
