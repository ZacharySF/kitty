"""Rika's routing memory, memory index and preferences over HTTP (synthetic, never a network call)."""
from __future__ import annotations

import dataclasses
from contextlib import closing
from decimal import Decimal

import httpx
import pytest

from kitty.core import notes
from kitty.core.rika import routes
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
def running(tmp_path, no_network):
    settings = dataclasses.replace(make_root(tmp_path, ("workspace",)), demo=True)
    with site(settings) as running:
        yield running
    assert no_network == []


def _db(running):
    return closing(connect(running.app.settings.db_path))


ROUTE_KEYS = {"signature", "label", "tier", "path", "uses", "escalations", "avg_tokens", "avg_cost_usd", "shortcut",
              "example", "updated_at"}


def test_routes_list_summarise_and_forget(running):
    status, body = running.request("/api/rika/routes")
    assert status == 200 and body["routes"] == []
    assert set(body["summary"]) == {"today_requests", "today_tokens", "today_cost_usd", "cached_share", "trend"}
    assert len(body["summary"]["trend"]) == 14
    with _db(running) as conn:
        for text in ("make a card about cells", "make a card about atoms"):
            routes.record(conn, text=text, path=["add_card"], start_tier="fast", final_tier="fast", escalated=0,
                          tokens=1200, cost=Decimal("0.0002"), deterministic=False,
                          call=("add_card", {"front": text.split()[-1], "back": "x"}))
    [route] = running.request("/api/rika/routes")[1]["routes"]
    assert set(route) == ROUTE_KEYS and route["signature"] == "add_card" and route["uses"] == 2
    assert route["avg_tokens"] == 1200 and route["avg_cost_usd"] == pytest.approx(0.0002)
    assert route["shortcut"] is None  # "x" never occurred in the request
    assert running.request("/api/rika/routes?x=1")[0] == 400
    assert running.request("/api/rika/routes/forget", {"signature": "nope"})[0] == 404
    for bad in ({}, {"signature": 3}, {"signature": "add_card", "extra": 1}):
        assert running.request("/api/rika/routes/forget", bad)[0] == 400
    status, body = running.request("/api/rika/routes/forget", {"signature": "add_card"})
    assert status == 200 and body["routes"] == [] and set(body["summary"]) >= {"trend"}


def test_memory_preferences_crud_and_header_protection(running):
    status, body = running.request("/api/rika/memory")
    assert status == 200 and set(body) == {"preferences", "index", "results", "notes_shared"}
    assert body["results"] == [] and body["notes_shared"] is False and body["preferences"] == []
    assert [item["source"] for item in body["index"]["sources"]] == [
        "page", "block", "oplog", "job", "event", "task", "mail", "goal", "preference"]
    assert all(set(item) == {"source", "label", "items", "refreshed_at"} for item in body["index"]["sources"])
    status, body = running.request("/api/rika/preferences", {"text": "Keep replies short"})
    assert status == 200 and body["preference"]["source"] == "added"
    pref = body["preference"]
    assert set(pref) == {"id", "text", "source", "created_at", "updated_at"}
    status, body = running.request(f"/api/rika/preferences/{pref['id']}", {"text": "Keep replies very short"})
    assert status == 200 and body["preference"]["text"] == "Keep replies very short"
    assert running.request("/api/rika/memory")[1]["preferences"][0]["text"] == "Keep replies very short"
    assert running.request("/api/rika/preferences", {"text": "hi"}, headers={"X-Kitty-Request": None})[0] == 403
    for path, bad in (("/api/rika/preferences", {}), ("/api/rika/preferences", {"text": ""}),
                      ("/api/rika/preferences", {"text": "x" * 501}), ("/api/rika/preferences", {"text": "a", "x": 1}),
                      (f"/api/rika/preferences/{pref['id']}", {"text": 4})):
        assert running.request(path, bad)[0] == 400, bad
    assert running.request("/api/rika/preferences/999", {"text": "x"})[0] == 404
    assert running.request("/api/rika/preferences/999/delete", {})[0] == 404
    status, body = running.request(f"/api/rika/preferences/{pref['id']}/delete", {})
    assert status == 200 and body == {"deleted": pref["id"]}
    assert running.request("/api/rika/memory")[1]["preferences"] == []


def test_memory_search_refreshes_the_index_and_rebuild_reports_it(running):
    with _db(running) as conn:
        notebook = notes.create_notebook(conn, "Biology")["id"]
        for index in range(8):
            notes.create_page(conn, notebook, f"Filler {index}")
        notes.create_page(conn, notebook, "Mitochondria")
    status, body = running.request("/api/rika/memory?q=mitochondria")
    assert status == 200 and body["results"][0]["title"] == "Mitochondria"
    assert set(body["results"][0]) == {"source", "ref", "title", "snippet", "view"}
    assert body["results"][0]["view"] == "notes"
    pages = next(item for item in body["index"]["sources"] if item["source"] == "page")
    assert pages["items"] >= 9 and pages["refreshed_at"] and body["index"]["total"] >= 9
    assert running.request("/api/rika/memory?q=" + "x" * 301)[0] == 400
    assert running.request("/api/rika/memory?other=1")[0] == 400
    status, body = running.request("/api/rika/memory/rebuild", {})
    assert status == 200 and set(body) == {"index"} and body["index"]["total"] >= 9
    assert running.request("/api/rika/memory/rebuild", {"x": 1})[0] == 400
