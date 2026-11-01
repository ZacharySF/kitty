"""Settings → Devices routes and an end-to-end sync between two real kitty! web servers.

Each server is a full WebApplication on its own synthetic workspace with sync on
(ephemeral port on 127.0.0.1, no LAN announcement). Pairing goes through the HTTP
routes; a page written on one server must reach the other's open editor window as a
live push.
"""
from __future__ import annotations

import time
import uuid

import pytest

from kitty.core import blocks
from kitty.db.connection import connect
from terminal_support import make_root, site
from test_web_blocks import Client, page_ops


@pytest.fixture
def pair_of_sites(tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_SYNC", "on")
    monkeypatch.setenv("KITTY_SYNC_PORT", "0")
    monkeypatch.setenv("KITTY_SYNC_DISCOVERY", "off")
    with site(make_root(tmp_path / "a", ("workspace",))) as a, site(make_root(tmp_path / "b", ("workspace",))) as b:
        for running in (a, b):
            running.app.sync.host = "127.0.0.1"
        yield a, b


def wait_for(check, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.05)


def test_devices_state_and_validation(pair_of_sites):
    a, _b = pair_of_sites
    status, state = a.request("/api/devices")
    assert status == 200
    assert set(state) == {"device", "pairing", "discovered", "peers"}
    assert state["device"]["listening"] and state["device"]["port"] and state["peers"] == []
    status, state = a.request("/api/devices/name", {"name": "  Desk   kitty "})
    assert status == 200 and state["device"]["name"] == "Desk kitty"
    assert a.request("/api/devices/name", {"name": ""})[0] == 400
    assert a.request("/api/devices/name", {"name": "x", "extra": 1})[0] == 400
    assert a.request("/api/devices/unpair", {"device_id": "0" * 32})[0] == 404
    assert a.request("/api/devices/pause", {"paused": "yes"})[0] == 400
    status, state = a.request("/api/devices/pair/show", {})
    assert status == 200 and len(state["pairing"]["code"]) == 7 and state["pairing"]["attempts_left"] == 3
    status, state = a.request("/api/devices/pair/cancel", {})
    assert state["pairing"] is None
    assert a.request("/api/devices", headers={"Origin": "https://example.com"})[0] == 403


def test_two_servers_pair_and_a_page_reaches_the_other_window_live(pair_of_sites):
    a, b = pair_of_sites
    a.request("/api/devices/name", {"name": "Laptop"})
    b.request("/api/devices/name", {"name": "Desktop"})
    _status, shown = b.request("/api/devices/pair/show", {})
    b_port = shown["device"]["port"]
    status, body = a.request("/api/devices/pair/enter", {"code": "WRONG1", "address": f"127.0.0.1:{b_port}"})
    assert status == 400 and "Wrong code" in body["error"]
    status, state = a.request("/api/devices/pair/enter", {"code": shown["pairing"]["code"], "address": f"127.0.0.1:{b_port}"})
    assert status == 200, state
    assert [peer["name"] for peer in state["peers"]] == ["Desktop"]
    paired = wait_for(lambda: b.request("/api/devices")[1]["pairing"]["paired"])
    assert paired["name"] == "Laptop"

    window = Client(b)
    try:
        page, block = str(uuid.uuid4()), str(uuid.uuid4())
        with connect(a.app.settings.db_path) as conn:
            blocks.transact(conn, page_ops(page, block, "written on the laptop"))
        a.request("/api/devices/sync", {})
        change = window.expect("change", timeout=15)
        assert change["page_id"] == page and change["device_id"] == a.request("/api/devices")[1]["device"]["id"]
        assert any(op["op"] == "put" and op.get("text") == "written on the laptop" for op in change["ops"])
    finally:
        window.close()
    state = wait_for(lambda: (s := a.request("/api/devices")[1]) and s["peers"][0]["last_sync_at"] and s)
    assert state["peers"][0]["status"] in {"idle", "syncing"}
    status, state = b.request("/api/devices/unpair", {"device_id": state["device"]["id"]})
    assert status == 200 and state["peers"] == []
