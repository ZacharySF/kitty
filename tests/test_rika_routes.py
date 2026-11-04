"""Rika's routing memory (design Q13): request shapes, the cheapest tier and shortest path
per kind of request, learned shortcuts and the cost readouts, on synthetic data only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kitty.core.rika import routes, store, tools as toolset


@pytest.fixture
def conn(workspace):
    return workspace.conn


def _record(conn, text, path, *, start="fast", final=None, escalated=0, tokens=100, cost="0.0001",
            deterministic=False, call=None, shortcut=None):
    routes.record(conn, text=text, path=path, start_tier=start, final_tier=final or start, escalated=escalated,
                  tokens=tokens, cost=Decimal(cost) if cost is not None else None, deterministic=deterministic,
                  call=call, shortcut=shortcut)


def _route(conn, signature):
    return next(item for item in routes.listing(conn) if item["signature"] == signature)


def test_a_shape_is_the_first_content_word_and_the_object_it_acts_on():
    assert routes.shape("Could you make me a flashcard about mitosis?") == "make card"
    assert routes.shape("please make some cards on the Krebs cycle") == "make card"
    assert routes.shape("add a node Photosynthesis") == "add node"
    assert routes.shape("Add the sticky Light reactions") == "add node"
    assert routes.shape("summarise yesterday's lecture") == "summarise yesterday"
    assert routes.shape("the a of") == ""


def test_recording_learns_the_route_and_recall_finds_it_by_shape(conn):
    assert routes.recall(conn, "make a card about osmosis") is None
    _record(conn, "could you make a card about mitosis", ["add_card"], start="fast", final="standard", escalated=1,
            tokens=300, cost="0.0003")
    recalled = routes.recall(conn, "make me a flashcard about osmosis")
    assert recalled == routes.Recall(tier="standard", signature="add_card", path=["add_card"])
    route = _route(conn, "add_card")
    assert route["label"] == "Add card" and route["tier"] == "standard" and route["escalations"] == 1
    assert route["uses"] == 1 and route["avg_tokens"] == 300 and route["avg_cost_usd"] == pytest.approx(0.0003)
    # A later success that did not escalate lowers the tier to the cheapest that worked.
    _record(conn, "make a card about osmosis", ["add_card"], start="fast", tokens=100, cost="0.0001")
    route = _route(conn, "add_card")
    assert route["tier"] == "fast" and route["uses"] == 2 and route["escalations"] == 1
    assert route["avg_tokens"] == 200 and route["avg_cost_usd"] == pytest.approx(0.0002)
    assert route["example"] == "make a card about osmosis"
    # A turn that had to escalate sets the tier it finished on.
    _record(conn, "make a card about meiosis", ["add_card"], start="fast", final="deep", escalated=2)
    assert _route(conn, "add_card")["tier"] == "deep" and _route(conn, "add_card")["escalations"] == 3


def test_deterministic_turns_are_direct_and_a_path_keeps_its_shortest_form(conn):
    _record(conn, "add a node Photosynthesis", ["add_canvas_node"], tokens=0, cost=None, deterministic=True)
    assert _route(conn, "add_canvas_node")["tier"] == "direct"
    _record(conn, "find biology then add a card", ["search", "search", "add_card"])
    _record(conn, "find chemistry then add a card", ["search", "add_card"])
    route = _route(conn, "search>add_card")
    assert route["path"] == ["search", "add_card"] and route["label"] == "Search → Add card" and route["uses"] == 2
    _record(conn, "hello there", [])
    assert _route(conn, "")["label"] == "Talk"


def test_two_matching_model_turns_teach_a_shortcut_that_runs_with_no_model(conn):
    available = toolset.registry()
    first = {"front": "Mitosis", "back": "Cell division"}
    _record(conn, "Quiz me later on Mitosis, answer Cell division.", ["add_card"], call=("add_card", first))
    assert routes.shortcut(conn, "quiz me later on Osmosis, answer Water moving", available) is None
    assert _route(conn, "add_card")["shortcut"] == {"template": "quiz me later on {front}, answer {back}", "uses": 0,
                                                    "active": False}
    _record(conn, "quiz me later on Meiosis, answer Halving", ["add_card"],
            call=("add_card", {"front": "Meiosis", "back": "Halving"}))
    plan = routes.shortcut(conn, "Quiz me later on Osmosis, answer Water moving!", available)
    assert plan.calls == [("add_card", {"front": "Osmosis", "back": "Water moving"})] and plan.reply is None
    assert plan.template == "quiz me later on {front}, answer {back}"
    assert routes.shortcut(conn, "quiz me later on Osmosis", available) is None
    assert routes.shortcut(conn, "quiz me later on Osmosis, answer Water", {}) is None  # tool not available
    _record(conn, "quiz me later on Osmosis, answer Water moving", ["add_card"], tokens=0, cost=None,
            deterministic=True, shortcut=plan.template)
    assert _route(conn, "add_card")["shortcut"] == {"template": plan.template, "uses": 1, "active": True}
    routes.forget_shortcut(conn, plan.template)
    assert routes.shortcut(conn, "quiz me later on Osmosis, answer Water moving", available) is None


def test_shortcuts_keep_constants_and_refuse_unsafe_or_wordless_templates(conn):
    available = toolset.registry()
    for text in ("sticky Light at the left", "sticky Dark at the left"):
        _record(conn, text, ["add_canvas_node"], call=("add_canvas_node", {"text": text.split()[1], "x": -200}))
    assert routes.shortcut(conn, "sticky Water at the left", available).calls == [
        ("add_canvas_node", {"text": "Water", "x": -200})]
    cases = [
        ("Mitosis", ("create_page", {"title": "Mitosis"})),                      # no literal word left
        ("ok Mitosis", ("create_page", {"title": "Mitosis"})),                   # only a two-letter word
        ("make a page about cells", ("create_page", {"title": "Cell biology"})),  # argument not in the text
        ("start omp to tidy notes", ("start_worker", {"kind": "omp", "task": "tidy notes"})),  # outward
        ("open the calendar", ("search", {"query": "calendar"})),               # read
        ("page {title} now", ("create_page", {"title": "now"})),                 # braces are not templates
    ]
    for text, call in cases:
        for _ in range(2):
            _record(conn, text, [call[0]], call=call)
    assert [row[0] for row in conn.execute("SELECT template FROM rika_shortcuts")] == ["sticky {text} at the left"]
    # Different constants restart the count instead of activating a mixed template.
    _record(conn, "sticky Air at the left", ["add_canvas_node"], call=("add_canvas_node", {"text": "Air", "x": 50}))
    assert routes.shortcut(conn, "sticky Water at the left", available) is None


def test_forget_removes_a_route_its_shapes_and_shortcuts(conn):
    for text in ("note Krebs means energy", "note Calvin means sugar"):
        _record(conn, text, ["create_page"], call=("create_page", {"title": text.split()[1]}))
    routes.forget(conn, "create_page")
    assert routes.listing(conn) == [] and routes.recall(conn, "note Krebs means energy") is None
    assert conn.execute("SELECT count(*) FROM rika_shortcuts").fetchone()[0] == 0
    with pytest.raises(LookupError):
        routes.forget(conn, "create_page")


def test_listing_is_most_used_first_with_the_contract_shape(conn):
    _record(conn, "open calendar", ["open_view"], deterministic=True, tokens=0, cost=None)
    for _ in range(2):
        _record(conn, "make a card about cells", ["add_card"])
    listed = routes.listing(conn)
    assert [item["signature"] for item in listed] == ["add_card", "open_view"]
    assert set(listed[0]) == {"signature", "label", "tier", "path", "uses", "escalations", "avg_tokens", "avg_cost_usd",
                              "shortcut", "example", "updated_at"}
    assert listed[1]["avg_cost_usd"] == 0 and listed[1]["shortcut"] is None


def _reply(conn, created_at, *, cost=None, tokens=(None, None, None), role="rika"):
    request = store.add_message(conn, conversation_id="c", role="user", text="synthetic")
    reply = store.add_message(conn, conversation_id="c", role=role, text="ok", reply_to=request["id"])
    conn.execute("UPDATE rika_messages SET created_at=?,cost_usd=?,tokens_in=?,tokens_cached=?,tokens_out=? WHERE id=?",
                 (created_at, cost, *tokens, reply["id"]))


def test_the_summary_counts_today_and_trends_over_fourteen_local_days(conn):
    conn.execute("UPDATE calendar_calendars SET timezone='UTC' WHERE id='personal'")
    now = datetime.now(timezone.utc)
    _reply(conn, now.isoformat(), cost="0.0004", tokens=(1000, 800, 100))
    _reply(conn, now.isoformat(), tokens=(0, 0, 0))  # a direct reply costs nothing
    _reply(conn, (now - timedelta(days=3)).isoformat(), cost="0.003", tokens=(2000, 0, 200))
    _reply(conn, (now - timedelta(days=20)).isoformat(), cost="9", tokens=(9, 9, 9))  # outside the window
    summary = routes.summary(conn)
    assert summary["today_requests"] == 2 and summary["today_tokens"] == 1100
    assert summary["today_cost_usd"] == pytest.approx(0.0004)
    assert summary["cached_share"] == pytest.approx(800 / 3000)
    trend = summary["trend"]
    assert len(trend) == 14 and trend[-1]["day"] == now.date().isoformat()
    assert trend[0]["day"] == (now.date() - timedelta(days=13)).isoformat()
    assert trend[-1] == {"day": now.date().isoformat(), "requests": 2, "avg_cost_usd": pytest.approx(0.0002)}
    assert trend[-4]["requests"] == 1 and trend[-4]["avg_cost_usd"] == pytest.approx(0.003)
    assert sum(day["requests"] for day in trend) == 3


def test_an_empty_memory_summarises_to_zeros(conn):
    summary = routes.summary(conn)
    assert summary["today_requests"] == 0 and summary["cached_share"] == 0 and summary["today_cost_usd"] == 0
    assert all(day["requests"] == 0 and day["avg_cost_usd"] == 0 for day in summary["trend"])


def test_the_demo_seed_fills_routes_a_shortcut_and_preferences_once(conn):
    routes.seed_demo(conn)
    routes.seed_demo(conn)
    listed = routes.listing(conn)
    assert {"add_card", "add_canvas_node", "search>add_card", "open_view"} <= {item["signature"] for item in listed}
    assert {item["tier"] for item in listed} >= {"direct", "fast"}
    assert sum(1 for item in listed if item["shortcut"] and item["shortcut"]["active"]) == 1
    assert conn.execute("SELECT count(*) FROM rika_preferences").fetchone()[0] == 2
    template = json.loads(json.dumps(next(item["shortcut"] for item in listed if item["shortcut"])))["template"]
    filled = template.replace("{front}", "Mitosis").replace("{back}", "Cell division")
    assert routes.shortcut(conn, filled, toolset.registry()).calls[0][0] == "add_card"
