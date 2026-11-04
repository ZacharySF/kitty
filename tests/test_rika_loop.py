"""Rika's loop: deterministic phrases, approvals that pause the turn, undo, and the
OpenRouter tool-calling loop (streaming, escalation, budget) against a mock transport."""
from __future__ import annotations

import dataclasses
import json
import queue
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from kitty import web_ai
from kitty.core import notes
from kitty.core.rika import llm, store
from kitty.core.rika.loop import Rika, RikaError


@pytest.fixture
def no_network(monkeypatch):
    """Every real HTTP request fails the test; a mock transport may still be installed."""
    calls = []

    def refuse(self, request):
        calls.append(str(request.url))
        raise AssertionError(f"unexpected network request to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    return calls


@pytest.fixture
def build(workspace, no_network):
    made = []

    def make(*, demo=False, modules=("workspace",)):
        settings = dataclasses.replace(workspace.settings, demo=demo)
        app = SimpleNamespace(settings=settings, modules=modules, mutation_lock=threading.Lock(),
                              streams_closed=threading.Event())
        app.rika = Rika(app)
        made.append(app.rika)
        return app, app.rika.hub.subscribe()

    yield make
    for rika in made:
        rika.close()


def until(events: queue.Queue, done, timeout=10.0) -> list[dict]:
    seen, deadline = [], time.monotonic() + timeout
    while True:
        event = events.get(timeout=max(0.01, deadline - time.monotonic()))
        seen.append(event)
        if done(event):
            return seen


def finished(event):
    return event["type"] == "message" and event["message"]["role"] == "rika" and event["message"]["status"] != "pending"


def of(events, kind):
    return [event for event in events if event["type"] == kind]


def test_a_deterministic_phrase_runs_its_tool_with_no_model(build, workspace, no_network):
    app, events = build()
    sent = app.rika.submit(workspace.conn, "Add a node Photosynthesis.")
    seen = until(events, finished)
    assert of(seen, "message")[0]["message"]["id"] == sent["message_id"]
    [tier] = of(seen, "tier")
    reply = of(seen, "message")[-1]["message"]
    assert tier == {"type": "tier", "message_id": reply["id"], "reply_to": sent["message_id"], "tier": "fast",
                    "model": "deterministic"}
    [action] = of(seen, "action")
    assert action["action"]["tool"] == "add_canvas_node" and action["action"]["reversible"] is True
    assert action["action"]["message_id"] == reply["id"]
    assert reply["status"] == "done" and reply["reply_to"] == sent["message_id"]
    assert reply["text"] == "".join(event["text"] for event in of(seen, "token")) == "Added the node Photosynthesis to Canvas."
    assert no_network == []


def test_an_outward_tool_pauses_for_approval_and_runs_once_approved(build, workspace):
    page = notes.create_page(workspace.conn, notes.create_notebook(workspace.conn, "Notes")["id"], "Draft")
    app, events = build()
    app.rika.submit(workspace.conn, "delete page draft")
    seen = until(events, lambda event: event["type"] == "approval")
    approval = seen[-1]["approval"]
    assert approval["tool"] == "delete_page" and approval["status"] == "pending"
    assert "move to Trash" in approval["detail"]
    time.sleep(0.3)
    assert notes.page(workspace.conn, page["id"])  # paused: nothing deleted yet
    pending = [item for item in store.history(workspace.conn)["items"] if item["kind"] == "message"][-1]
    assert pending["role"] == "rika" and pending["status"] == "pending"
    decided = app.rika.decide(workspace.conn, approval["id"], "approve")
    assert decided["status"] == "approved"
    with pytest.raises(RikaError) as twice:
        app.rika.decide(workspace.conn, approval["id"], "reject")
    assert twice.value.status == 409
    seen = until(events, finished)
    [action] = of(seen, "action")
    assert action["action"]["reversible"] is False and action["action"]["status"] == "done"
    assert of(seen, "message")[-1]["message"]["text"] == "Deleted the page Draft."
    with pytest.raises(notes.NotesError):
        notes.page(workspace.conn, page["id"])


def test_a_rejected_approval_changes_nothing(build, workspace):
    page = notes.create_page(workspace.conn, notes.create_notebook(workspace.conn, "Notes")["id"], "Keep me")
    app, events = build()
    app.rika.submit(workspace.conn, "delete page Keep me")
    approval = until(events, lambda event: event["type"] == "approval")[-1]["approval"]
    with pytest.raises(RikaError):
        app.rika.decide(workspace.conn, approval["id"], "maybe")
    app.rika.decide(workspace.conn, approval["id"], "reject")
    seen = until(events, finished)
    assert of(seen, "action") == []
    assert of(seen, "message")[-1]["message"]["text"] == "Not done: Delete the page Keep me (you declined)."
    assert notes.page(workspace.conn, page["id"])["title"] == "Keep me"


def test_closing_expires_a_waiting_approval_and_a_restart_recovers_stale_rows(build, workspace):
    notes.create_page(workspace.conn, notes.create_notebook(workspace.conn, "Notes")["id"], "Draft")
    app, events = build()
    app.rika.submit(workspace.conn, "delete page Draft")
    approval = until(events, lambda event: event["type"] == "approval")[-1]["approval"]
    app.rika.close()
    assert store.approval(workspace.conn, approval["id"])["status"] == "expired"
    # A crash leaves rows pending; the next start settles them.
    reply = store.add_message(workspace.conn, conversation_id="c", role="rika", text="", status="pending")
    stale = store.add_approval(workspace.conn, message_id=reply["id"], tool="delete_page", args={}, summary="s", detail="d")
    build()
    assert store.approval(workspace.conn, stale["id"])["status"] == "expired"
    assert store.message(workspace.conn, reply["id"])["status"] == "failed"


def test_undo_reverts_a_reversible_action_once(build, workspace):
    app, events = build()
    app.rika.submit(workspace.conn, "new card Mitosis / Cell division")
    action = of(until(events, finished), "action")[0]["action"]
    assert workspace.conn.execute("SELECT count(*) FROM flash_notes").fetchone()[0] == 1
    undone = app.rika.undo(workspace.conn, action["id"])
    assert undone["status"] == "undone"
    assert until(events, lambda event: event["type"] == "action")[-1]["action"]["status"] == "undone"
    assert workspace.conn.execute("SELECT count(*) FROM flash_notes").fetchone()[0] == 0
    for ident, status in ((action["id"], 409), ("0" * 32, 404)):
        with pytest.raises(RikaError) as refused:
            app.rika.undo(workspace.conn, ident)
        assert refused.value.status == status


# OpenRouter -------------------------------------------------------------------

def _sse(*chunks: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks) + b": keepalive\n\ndata: [DONE]\n\n"


def _tool_call(name: str, arguments: str) -> bytes:
    return _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                        "function": {"name": name, "arguments": arguments[:10]}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": arguments[10:]}}]},
                              "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0001}})


def _text(*parts: str) -> bytes:
    return _sse(*({"choices": [{"delta": {"content": part}}]} for part in parts),
                {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"cost": 0.0001}})


@pytest.fixture
def openrouter(workspace, monkeypatch):
    """A key, three priced tier models and a scripted OpenRouter behind a mock transport."""
    web_ai._write_key(SimpleNamespace(settings=workspace.settings), "synthetic-openrouter-test-key")
    for name in ("fast", "standard", "deep"):
        workspace.conn.execute("INSERT INTO ai_models VALUES(?,?,?,?,?,?)", (f"synthetic/{name}", name, "0.000001",
                               "0.000002", 200_000, web_ai._now().isoformat()))
    workspace.conn.execute("UPDATE ai_settings SET rika_fast_model='synthetic/fast',rika_standard_model='synthetic/standard',"
                           "rika_deep_model='synthetic/deep',daily_budget_usd='1.00' WHERE id=1")
    script = SimpleNamespace(replies=[], requests=[])

    def handle(request: httpx.Request) -> httpx.Response:
        script.requests.append((dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, content=script.replies.pop(0), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handle))
    return script


def test_the_model_loop_streams_calls_tools_and_escalates_on_a_validation_failure(build, workspace, openrouter):
    openrouter.replies = [_tool_call("add_card", '{"front": "Mitosis"}'),
                          _tool_call("add_card", '{"front": "Mitosis", "back": "Cell division"}'),
                          _text("Added your ", "Mitosis card.")]
    app, events = build()
    app.rika.submit(workspace.conn, "could you make me a flashcard about mitosis")
    seen = until(events, finished)
    models = [body["model"] for _headers, body in openrouter.requests]
    assert models == ["synthetic/fast", "synthetic/standard", "synthetic/standard"]
    assert [(event["tier"], event["model"]) for event in of(seen, "tier")] == [
        ("fast", "synthetic/fast"), ("standard", "synthetic/standard")]
    headers, first = openrouter.requests[0]
    assert headers["authorization"] == "Bearer synthetic-openrouter-test-key"
    assert first["stream"] is True and first["provider"]["allow_fallbacks"] is False
    assert first["provider"]["data_collection"] == "deny" and first["provider"]["max_price"]["prompt"] == pytest.approx(1.0)
    assert {tool["function"]["name"] for tool in first["tools"]} >= {"add_card", "create_event", "search", "open_view"}
    assert "start_worker" not in {tool["function"]["name"] for tool in first["tools"]}  # terminal module is off
    rejected = json.loads(openrouter.requests[1][1]["messages"][-1]["content"])
    assert rejected["status"] == "invalid" and "back" in rejected["error"]
    [action] = of(seen, "action")
    assert action["action"]["tool"] == "add_card"
    reply = of(seen, "message")[-1]["message"]
    assert reply["text"] == "Added your Mitosis card." and [event["text"] for event in of(seen, "token")] == ["Added your ", "Mitosis card."]
    assert reply["tier"] == "standard" and reply["cost_usd"] == pytest.approx(0.0003)
    ledger = workspace.conn.execute("SELECT state,actual_usd FROM ai_reviews WHERE purpose='rika'").fetchall()
    assert [tuple(row) for row in ledger] == [("completed", "0.0001")] * 3


def test_planning_starts_on_standard_and_an_incomplete_reply_escalates(build, workspace, openrouter):
    cut = _sse({"choices": [{"delta": {"content": "First, "}, "finish_reason": "length"}]})
    openrouter.replies = [cut, _text("the full plan.")]
    app, events = build()
    app.rika.submit(workspace.conn, "plan my week around the physics exam")
    seen = until(events, finished)
    assert [event["tier"] for event in of(seen, "tier")] == ["standard", "deep"]
    assert of(seen, "message")[-1]["message"]["text"] == "First, \n\nthe full plan."


def test_a_request_over_the_daily_budget_waits_for_approval(build, workspace, openrouter):
    workspace.conn.execute("UPDATE ai_settings SET daily_budget_usd='0' WHERE id=1")
    app, events = build()
    app.rika.submit(workspace.conn, "what should I study first?", tier="deep")
    approval = until(events, lambda event: event["type"] == "approval")[-1]["approval"]
    assert approval["tool"] == "openrouter" and approval["cost_usd"] > 0 and "synthetic/deep" in approval["summary"]
    app.rika.decide(workspace.conn, approval["id"], "reject")
    reply = of(until(events, finished), "message")[-1]["message"]
    assert "budget" in reply["text"] and openrouter.requests == []
    openrouter.replies = [_text("Start with kinematics.")]
    app.rika.submit(workspace.conn, "what should I study first?")
    approval = until(events, lambda event: event["type"] == "approval")[-1]["approval"]
    app.rika.decide(workspace.conn, approval["id"], "approve")
    assert of(until(events, finished), "message")[-1]["message"]["text"] == "Start with kinematics."
    assert len(openrouter.requests) == 1


def test_without_a_key_rika_still_does_commands_and_asks_for_one_otherwise(build, workspace, no_network):
    app, events = build()
    app.rika.submit(workspace.conn, "write me a poem about cells")
    reply = of(until(events, finished), "message")[-1]["message"]
    assert reply["status"] == "failed" and "OpenRouter API key" in reply["text"]
    app.rika.submit(workspace.conn, "add a task Read chapter 3 tomorrow")
    assert of(until(events, finished), "message")[-1]["message"]["status"] == "done"
    assert no_network == []


def test_the_demo_never_calls_openrouter_even_with_a_key_and_models(build, workspace, openrouter, no_network):
    app, events = build(demo=True)
    app.rika.submit(workspace.conn, "write me a poem about cells")
    seen = until(events, finished)
    assert of(seen, "tier")[0]["model"] == "demo"
    assert len(of(seen, "token")) > 3 and "demo" in of(seen, "message")[-1]["message"]["text"]
    app.rika.submit(workspace.conn, "new card Mitosis / Cell division", tier="deep")
    assert of(until(events, finished), "action")[0]["action"]["tool"] == "add_card"
    assert openrouter.requests == [] and no_network == []


# Voice and brain dumps ----------------------------------------------------------

def test_a_spoken_request_goes_to_the_fast_tier_even_when_it_plans(build, workspace, openrouter):
    openrouter.replies = [_text("Monday: kinematics.")]
    app, events = build()
    app.rika.submit(workspace.conn, "plan my week around the physics exam", spoken=True)
    seen = until(events, finished)
    assert of(seen, "message")[0]["message"]["source"] == "voice"
    assert [event["tier"] for event in of(seen, "tier")] == ["fast"]
    assert openrouter.requests[0][1]["model"] == "synthetic/fast"
    reply = of(seen, "message")[-1]["message"]
    assert app.rika.speak(workspace.conn, reply["id"])["text"] == "Monday: kinematics."


def _plan_reply(items: list[dict]) -> bytes:
    return _text(json.dumps({"items": items}))


DUMP = "um so the physics exam is friday at nine and make me a card on Newton's second law"


def test_a_brain_dump_is_planned_on_the_standard_tier_and_bad_items_are_dropped(build, workspace, openrouter):
    openrouter.replies = [_plan_reply([
        {"area": "cards", "title": "Newton's second law", "detail": "1 card", "source": "make me a card",
         "calls": [{"tool": "add_card", "args": {"front": "Newton's second law?", "back": "F = ma"}}]},
        {"area": "calendar", "title": "Physics exam", "detail": "Fri 09:00", "source": "physics exam is friday",
         "calls": [{"tool": "create_event", "args": {"title": "Physics exam", "start": "2026-11-06T09:00"}}]},
        {"area": "calendar", "title": "Wrong tool", "calls": [{"tool": "add_card", "args": {"front": "x", "back": "y"}}]},
        {"area": "gossip", "title": "Not an area", "calls": []},
    ])]
    app, events = build()
    sent = app.rika.dump(workspace.conn, DUMP, [{"start": 0, "end": 6.5, "text": DUMP}], 6.5)
    seen = until(events, finished)
    [tier] = of(seen, "tier")
    assert (tier["tier"], tier["model"]) == ("standard", "synthetic/standard")
    _headers, body = openrouter.requests[0]
    assert "tools" not in body and body["messages"][1]["content"] == f"[0:00] {DUMP}"
    assert "untrusted data" in body["messages"][0]["content"] and "add_card" in body["messages"][0]["content"]
    plan = of(seen, "plan")[0]["plan"]
    assert [(item["area"], item["title"]) for item in plan["items"]] == [
        ("calendar", "Physics exam"), ("cards", "Newton's second law")]
    assert plan["request_id"] == sent["message_id"] and plan["note"] is not None
    assert not of(seen, "action")  # nothing happens before the plan is approved
    reply = of(seen, "message")[-1]["message"]
    assert reply["status"] == "done" and reply["cost_usd"] == pytest.approx(0.0001)


def test_an_unreadable_plan_escalates_once_then_gives_up_keeping_the_note(build, workspace, openrouter):
    openrouter.replies = [_text("Sure! Here's what I'd do."), _text("still not json"), _text("nope")]
    app, events = build()
    app.rika.dump(workspace.conn, DUMP, [], 6.5)
    seen = until(events, finished)
    assert [event["tier"] for event in of(seen, "tier")] == ["standard", "deep"]
    reply = of(seen, "message")[-1]["message"]
    assert reply["status"] == "failed" and "dump note" in reply["text"] and not of(seen, "plan")
    assert workspace.conn.execute("SELECT count(*) FROM note_page_index WHERE title LIKE 'Brain dump · %'").fetchone()[0] == 1


def test_approving_a_plan_is_the_approval_for_its_ticked_outward_items(build, workspace, monkeypatch):
    app, events = build(demo=True, modules=("workspace", "terminal"))
    from kitty.core.rika.dumps import DEMO_DUMP

    app.rika.dump(workspace.conn, DEMO_DUMP, [], 40)
    plan = of(until(events, finished), "plan")[0]["plan"]
    agent = next(item for item in plan["items"] if item["area"] == "agents")
    app.rika.decide_plan(workspace.conn, plan["id"], "approve", [agent["id"]])
    seen = until(events, finished)
    assert not of(seen, "approval")
    [action] = of(seen, "action")
    assert action["action"]["tool"] == "start_worker" and action["action"]["summary"].startswith("Started omp")
    done = of(seen, "plan")[-1]["plan"]
    assert [item["result"]["status"] for item in done["items"]].count("done") == 1
    assert app.rika.agents.list()[-1]["task"].startswith("Fix the failing login tests")
    with pytest.raises(RikaError) as again:
        app.rika.decide_plan(workspace.conn, plan["id"], "approve", [agent["id"]])
    assert again.value.status == 409


def test_a_plan_item_that_fails_is_reported_and_the_rest_still_run(build, workspace):
    app, events = build(demo=True)
    app.rika.dump(workspace.conn, "Add a task Read chapter 3 tomorrow. Add a topic Cells.", [], 5)
    plan = of(until(events, finished), "plan")[0]["plan"]
    assert [item["area"] for item in plan["items"]] == ["notes", "tasks"]
    workspace.conn.execute("UPDATE calendar_calendars SET timezone='Not/AZone' WHERE id='personal'")
    app.rika.decide_plan(workspace.conn, plan["id"], "approve", [item["id"] for item in plan["items"]])
    seen = until(events, finished)
    done = of(seen, "plan")[-1]["plan"]
    statuses = {item["area"]: item["result"]["status"] for item in done["items"]}
    assert statuses == {"notes": "done", "tasks": "failed"}
    reply = of(seen, "message")[-1]["message"]
    assert reply["status"] == "done" and "1 failed" in reply["text"]


# Routing memory, prompt caching and memory (Q13, Q18) --------------------------

def _counted(name: str, args: dict, *, prompt=1000, cached=600, completion=40, cost=0.0002) -> bytes:
    """One tool call with OpenRouter's usage, including cached prompt tokens."""
    return _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                        "function": {"name": name, "arguments": json.dumps(args)}}]},
                              "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "cost": cost,
                                          "prompt_tokens_details": {"cached_tokens": cached}}})


def _said(text: str, *, prompt=1200, cached=1000, completion=10, cost=0.0001) -> bytes:
    return _sse({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "cost": cost,
                                          "prompt_tokens_details": {"cached_tokens": cached}}})


def test_a_model_turn_records_tokens_cost_and_path_on_the_reply_and_a_route(build, workspace, openrouter):
    from kitty.core.rika import routes

    openrouter.replies = [_counted("add_card", {"front": "Mitosis", "back": "Cell division"}), _said("Added it.")]
    app, events = build()
    sent = app.rika.submit(workspace.conn, "could you make me a flashcard about mitosis")
    seen = until(events, finished)
    assert of(seen, "message")[0]["message"]["usage"] is None  # the user's own message
    reply = of(seen, "message")[-1]["message"]
    assert reply["usage"] == {"tokens_in": 2200, "tokens_cached": 1600, "tokens_out": 50, "path": ["add_card"],
                              "signature": "add_card", "remembered": False, "shortcut": False}
    assert reply["cost_usd"] == pytest.approx(0.0003) and reply["reply_to"] == sent["message_id"]
    assert [item for item in store.history(workspace.conn)["items"] if item["kind"] == "message"][-1]["usage"] == reply["usage"]
    [route] = routes.listing(workspace.conn)
    assert (route["signature"], route["tier"], route["uses"], route["avg_tokens"]) == ("add_card", "fast", 1, 2250)
    assert route["avg_cost_usd"] == pytest.approx(0.0003)
    app.rika.submit(workspace.conn, "add a node Photosynthesis")
    direct = of(until(events, finished), "message")[-1]["message"]
    assert direct["model"] == "deterministic" and direct["tier"] == "fast" and direct["cost_usd"] is None
    assert direct["usage"] == {"tokens_in": 0, "tokens_cached": 0, "tokens_out": 0, "path": ["add_canvas_node"],
                               "signature": "add_canvas_node", "remembered": False, "shortcut": False}
    assert {item["signature"]: item["tier"] for item in routes.listing(workspace.conn)}["add_canvas_node"] == "direct"


def test_the_next_request_of_the_same_kind_starts_on_the_remembered_tier(build, workspace, openrouter):
    openrouter.replies = [_tool_call("add_card", '{"front": "Mitosis"}'),
                          _counted("add_card", {"front": "Mitosis", "back": "Cell division"}), _said("Done.")]
    app, events = build()
    app.rika.submit(workspace.conn, "please make me a flashcard about mitosis")
    assert of(until(events, finished), "message")[-1]["message"]["tier"] == "standard"
    openrouter.replies = [_counted("add_card", {"front": "Osmosis", "back": "Water"}), _said("Done.")]
    app.rika.submit(workspace.conn, "make a card about osmosis")
    seen = until(events, finished)
    assert [body["model"] for _headers, body in openrouter.requests[3:]] == ["synthetic/standard"] * 2
    assert [event["tier"] for event in of(seen, "tier")] == ["standard"]
    assert of(seen, "message")[-1]["message"]["usage"]["remembered"] is True
    # An override still wins over the memory.
    openrouter.replies = [_said("Sure.")]
    app.rika.submit(workspace.conn, "make a card about diffusion", tier="fast")
    assert of(until(events, finished), "message")[-1]["message"]["usage"]["remembered"] is False
    assert openrouter.requests[-1][1]["model"] == "synthetic/fast"


def test_a_shortcut_seen_twice_runs_with_no_model_and_undoing_it_forgets_it(build, workspace, openrouter):
    from kitty.core.rika import routes, tools

    app, events = build()
    for front, back in (("Mitosis", "Cell division"), ("Meiosis", "Halving")):
        openrouter.replies = [_counted("add_card", {"front": front, "back": back}), _said("Added.")]
        app.rika.submit(workspace.conn, f"quiz me later on {front}, answer {back}")
        assert of(until(events, finished), "message")[-1]["message"]["usage"]["shortcut"] is False
    sent = len(openrouter.requests)
    app.rika.submit(workspace.conn, "Quiz me later on Osmosis, answer Water moving.")
    seen = until(events, finished)
    assert len(openrouter.requests) == sent  # the mock transport was not hit
    assert [(event["tier"], event["model"]) for event in of(seen, "tier")] == [("fast", "shortcut")]
    [action] = of(seen, "action")
    reply = of(seen, "message")[-1]["message"]
    assert reply["model"] == "shortcut" and reply["usage"]["shortcut"] is True and reply["usage"]["tokens_in"] == 0
    assert reply["text"] == action["action"]["summary"] + "."
    assert workspace.conn.execute("SELECT count(*) FROM flash_notes").fetchone()[0] == 3
    app.rika.undo(workspace.conn, action["action"]["id"])
    assert routes.shortcut(workspace.conn, "quiz me later on Osmosis, answer Water moving", tools.registry()) is None


def test_anthropic_requests_cache_the_stable_prompt_and_carry_preferences_and_memory(build, workspace, openrouter):
    from kitty.core.rika import memory

    workspace.conn.execute("INSERT INTO ai_models VALUES('anthropic/claude-haiku-4.5','haiku','0.000001','0.000002',"
                           "200000,?)", (web_ai._now().isoformat(),))
    workspace.conn.execute("UPDATE ai_settings SET rika_fast_model='anthropic/claude-haiku-4.5' WHERE id=1")
    memory.add_preference(workspace.conn, "Keep replies short", source="added")
    notebook = notes.create_notebook(workspace.conn, "Notes")["id"]
    for index in range(12):
        notes.create_page(workspace.conn, notebook, f"Filler {index}")
    notes.create_page(workspace.conn, notebook, "Chlorophyll")
    openrouter.replies = [_said("It absorbs light.")]
    app, events = build()
    app.rika.submit(workspace.conn, "what did I note about chlorophyll")
    until(events, finished)
    messages = openrouter.requests[0][1]["messages"]
    stable, volatile = messages[0]["content"]
    assert messages[0]["role"] == "system" and stable["cache_control"] == {"type": "ephemeral"}
    assert "You are Rika" in stable["text"] and "- Keep replies short" in stable["text"]
    assert "remember_preference" in stable["text"] and "Today is" not in stable["text"]
    assert "Today is" in volatile["text"] and "cache_control" not in volatile
    assert messages[-1] == {"role": "user", "content": "what did I note about chlorophyll"}
    recalled = messages[-2]
    assert recalled["role"] == "user" and "untrusted" in recalled["content"] and "Chlorophyll" in recalled["content"]
    # Other providers cache on their own: the system prompt stays one string.
    workspace.conn.execute("UPDATE ai_settings SET rika_fast_model='synthetic/fast' WHERE id=1")
    openrouter.replies = [_said("Hello.")]
    app.rika.submit(workspace.conn, "hello")
    until(events, finished)
    system = openrouter.requests[1][1]["messages"][0]
    assert isinstance(system["content"], str) and "Keep replies short" in system["content"] and "Today is" in system["content"]


def test_remember_is_a_deterministic_phrase_and_its_undo_forgets_the_preference(build, workspace, no_network):
    from kitty.core.rika import memory

    app, events = build()
    app.rika.submit(workspace.conn, "remember that I study best in the mornings")
    seen = until(events, finished)
    [action] = of(seen, "action")
    assert action["action"]["tool"] == "remember_preference" and action["action"]["reversible"] is True
    assert of(seen, "message")[-1]["message"]["text"] == "Remembered: I study best in the mornings."
    assert [(item["text"], item["source"]) for item in memory.preferences(workspace.conn)] == [
        ("I study best in the mornings", "stated")]
    app.rika.undo(workspace.conn, action["action"]["id"])
    assert memory.preferences(workspace.conn) == [] and no_network == []
