"""Rika thinking with omp: the brain choice, the `omp auth-gateway stdio` exchange (against
a scripted stand-in for omp on PATH) and a whole turn with no OpenRouter key."""
from __future__ import annotations

import dataclasses
import json
import os
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_rika_loop import (  # noqa: F401 - fixtures and helpers
    finished,
    no_network,
    of,
    until,
)

from kitty.core import keys
from kitty.core.rika import brain, llm, omp
from kitty.core.rika.loop import Rika

#: A stand-in for `omp auth-gateway stdio`: logs its argv and each request, answers each
#: request with the next scripted reply (`{"status", "body"}`), as the real gateway frames it.
FAKE_OMP = """\
import json, os, sys
folder = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(folder, "argv.jsonl"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({"ready": True, "version": "18.14.1"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    with open(os.path.join(folder, "requests.jsonl"), "a") as log:
        log.write(json.dumps(request) + "\\n")
    with open(os.path.join(folder, "replies.json")) as handle:
        replies = json.load(handle)
    reply = replies.pop(0)
    with open(os.path.join(folder, "replies.json"), "w") as handle:
        json.dump(replies, handle)
    print(json.dumps({"id": request["id"], **reply}), flush=True)
"""


class FakeOmp:
    def __init__(self, folder: Path):
        self.folder = folder
        self.replies([])

    def replies(self, replies: list[dict]) -> None:
        (self.folder / "replies.json").write_text(json.dumps(replies))

    @property
    def requests(self) -> list[dict]:
        path = self.folder / "requests.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    @property
    def argv(self) -> list[list[str]]:
        path = self.folder / "argv.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def fake_omp(tmp_path, monkeypatch) -> FakeOmp:
    folder = tmp_path / "omp-bin"
    folder.mkdir()
    script = folder / "omp"
    script.write_text(f"#!{sys.executable}\n" + FAKE_OMP)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ.get("PATH", ""))
    return FakeOmp(folder)


def _completion(message: dict, finish: str, usage: dict | None = None) -> dict:
    return {"status": 200, "body": {"object": "chat.completion", "model": "anthropic/claude-haiku-5-5",
                                    "choices": [{"index": 0, "message": {"role": "assistant", **message},
                                                 "finish_reason": finish}],
                                    "usage": usage or {"prompt_tokens": 120, "completion_tokens": 30,
                                                       "prompt_tokens_details": {"cached_tokens": 100}}}}


def _tool_call(name: str, arguments: dict) -> dict:
    return _completion({"content": None, "tool_calls": [{"id": "toolu_1", "type": "function",
                                                         "function": {"name": name, "arguments": json.dumps(arguments)}}]},
                       "tool_calls")


def _text(text: str) -> dict:
    return _completion({"content": text}, "stop")


@pytest.fixture
def build(workspace, no_network):  # noqa: F811 - the fixture imported above
    made = []

    def make(*, modules=("workspace",)):
        app = SimpleNamespace(settings=workspace.settings, modules=modules, mutation_lock=threading.Lock(),
                              streams_closed=threading.Event())
        app.rika = Rika(app)
        made.append(app.rika)
        return app, app.rika.hub.subscribe()

    yield make
    for rika in made:
        rika.close()


# The brain choice -------------------------------------------------------------------

def test_the_brain_is_openrouter_until_omp_is_chosen_and_the_choice_is_private(workspace):
    settings = workspace.settings
    assert brain.load(settings) == brain.Brain("openrouter", {"fast": "", "standard": "", "deep": ""}, "off")
    saved = brain.save(settings, "omp", {"fast": "", "standard": "anthropic/claude-sonnet-5-5", "deep": ""})
    assert saved == brain.load(settings)
    assert brain.models(saved) == {**brain.OMP_DEFAULTS, "standard": "anthropic/claude-sonnet-5-5"}
    path = settings.root / ".private" / "rika" / "brain.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(("provider", "models"), [
    ("anthropic", {}),
    ("omp", {"fast": "a model; rm -rf"}),
    ("omp", {"fast": "-x"}),
    ("omp", {"turbo": "anthropic/claude"}),
    ("omp", {"fast": "a" * 201}),
])
def test_a_bad_brain_choice_is_refused_and_nothing_is_saved(workspace, provider, models):
    with pytest.raises(ValueError):
        brain.save(workspace.settings, provider, models)
    assert brain.load(workspace.settings).provider == "openrouter"


def test_an_unreadable_brain_file_falls_back_to_openrouter(workspace):
    path = workspace.settings.root / ".private" / "rika" / "brain.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert brain.load(workspace.settings).provider == "openrouter"


# The gateway exchange ---------------------------------------------------------------

def test_a_request_goes_to_the_omp_gateway_and_its_answer_comes_back(fake_omp):
    fake_omp.replies([_text("Hello.")])
    answer = omp.complete({"model": "anthropic/claude-haiku-5-5", "messages": [{"role": "user", "content": "hi"}]})
    assert answer["choices"][0]["message"]["content"] == "Hello."
    assert fake_omp.argv == [["auth-gateway", "stdio"]]
    [request] = fake_omp.requests
    assert request["path"] == "/v1/chat/completions" and request["body"]["model"] == "anthropic/claude-haiku-5-5"


def test_a_gateway_refusal_is_reported_with_omps_reason(fake_omp):
    fake_omp.replies([{"status": 401, "body": {"error": {"message": "No credential available for provider anthropic",
                                                         "type": "authentication_error"}}}])
    with pytest.raises(omp.OmpFailed, match="No credential available for provider anthropic"):
        omp.complete({"model": "anthropic/claude-haiku-5-5", "messages": []})


def test_without_omp_on_path_the_gateway_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert omp.program() is None
    with pytest.raises(omp.OmpMissing):
        omp.complete({"model": "x", "messages": []})


# A whole turn ------------------------------------------------------------------------

def test_rika_thinks_with_omp_without_any_openrouter_key(build, workspace, fake_omp, no_network):  # noqa: F811
    assert not keys.for_settings(workspace.settings).read("OPENROUTER")
    brain.save(workspace.settings, "omp", {})
    fake_omp.replies([_tool_call("add_card", {"front": "Mitosis", "back": "Cell division"}), _text("Added your Mitosis card.")])
    app, events = build()
    app.rika.submit(workspace.conn, "could you make me a flashcard about mitosis")
    seen = until(events, finished)
    first, second = (request["body"] for request in fake_omp.requests)
    assert first["model"] == second["model"] == brain.OMP_DEFAULTS["fast"]
    assert first["stream"] is False and "provider" not in first and "usage" not in first
    assert {tool["function"]["name"] for tool in first["tools"]} >= {"add_card", "search", "open_view"}
    assert first["messages"][0]["role"] == "system" and "Rika" in json.dumps(first["messages"][0])
    assert second["messages"][-1]["role"] == "tool" and json.loads(second["messages"][-1]["content"])["status"] == "done"
    [action] = of(seen, "action")
    assert action["action"]["tool"] == "add_card" and action["action"]["status"] == "done"
    assert [(event["tier"], event["model"]) for event in of(seen, "tier")] == [("fast", brain.OMP_DEFAULTS["fast"])]
    reply = of(seen, "message")[-1]["message"]
    assert reply["status"] == "done" and reply["text"] == "Added your Mitosis card."
    assert reply["cost_usd"] == 0
    ledger = workspace.conn.execute("SELECT state,reserved_usd,actual_usd,plan_json FROM ai_reviews WHERE purpose='rika'").fetchall()
    assert [(row["state"], row["reserved_usd"], row["actual_usd"]) for row in ledger] == [("completed", "0", None)] * 2
    assert json.loads(ledger[0]["plan_json"])["provider"] == "omp"
    assert no_network == []


def test_omp_turns_ignore_the_openrouter_budget(build, workspace, fake_omp):
    workspace.conn.execute("UPDATE ai_settings SET daily_budget_usd='0' WHERE id=1")
    brain.save(workspace.settings, "omp", {})
    fake_omp.replies([_text("Start with kinematics.")])
    app, events = build()
    app.rika.submit(workspace.conn, "what should I study first?", tier="deep")
    seen = until(events, finished)
    assert of(seen, "approval") == []
    assert [request["body"]["model"] for request in fake_omp.requests] == [brain.OMP_DEFAULTS["deep"]]
    assert of(seen, "message")[-1]["message"]["text"] == "Start with kinematics."


def test_an_omp_failure_fails_the_turn_once_and_says_why(build, workspace, fake_omp):
    brain.save(workspace.settings, "omp", {})
    fake_omp.replies([{"status": 429, "body": {"error": {"message": "The usage limit has been reached",
                                                         "type": "rate_limit_error"}}}])
    app, events = build()
    app.rika.submit(workspace.conn, "what should I study first?")
    reply = of(until(events, finished), "message")[-1]["message"]
    assert reply["status"] == "failed" and "The usage limit has been reached" in reply["text"]
    assert len(fake_omp.requests) == 1
    assert [tuple(row) for row in workspace.conn.execute("SELECT state FROM ai_reviews WHERE purpose='rika'")] == [("failed",)]


def test_choosing_omp_without_omp_installed_says_what_to_do(build, workspace, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    brain.save(workspace.settings, "omp", {})
    app, events = build()
    app.rika.submit(workspace.conn, "what should I study first?")
    reply = of(until(events, finished), "message")[-1]["message"]
    assert reply["status"] == "failed" and "omp is not installed" in reply["text"]
    assert workspace.conn.execute("SELECT count(*) FROM ai_reviews").fetchone()[0] == 0


def test_without_a_key_the_openrouter_brain_points_at_omp_too(build, workspace):
    app, events = build()
    app.rika.submit(workspace.conn, "what should I study first?")
    reply = of(until(events, finished), "message")[-1]["message"]
    assert "OpenRouter API key" in reply["text"] and "omp" in reply["text"]


def test_the_demo_never_runs_omp(workspace, fake_omp):
    brain.save(workspace.settings, "omp", {})
    app = SimpleNamespace(settings=dataclasses.replace(workspace.settings, demo=True))
    with pytest.raises(llm.ModelUnavailable):
        llm.prepare(app, workspace.conn, brain.OMP_DEFAULTS["fast"], [], [])
    assert fake_omp.requests == []


# Settings → AI over HTTP -------------------------------------------------------------

def test_the_brain_is_read_and_chosen_over_http(tmp_path, fake_omp):
    from terminal_support import make_root, site

    with site(make_root(tmp_path, ("workspace",))) as running:
        status, answer = running.request("/api/rika/brain")
        assert status == 200 and answer == {"provider": "openrouter", "models": {"fast": "", "standard": "", "deep": ""},
                                            "thinking": "off", "thinking_levels": list(brain.THINKING),
                                            "defaults": brain.OMP_DEFAULTS, "omp_installed": True}
        status, answer = running.request("/api/rika/brain", {"provider": "omp", "models": {"deep": "anthropic/claude-opus-5"}})
        assert status == 200 and answer["provider"] == "omp" and answer["models"]["deep"] == "anthropic/claude-opus-5"
        assert brain.load(running.app.settings).provider == "omp"
        status, answer = running.request("/api/rika/brain", {"provider": "openrouter"})
        assert status == 200 and answer["provider"] == "openrouter" and answer["models"]["deep"] == ""
        status, answer = running.request("/api/rika/brain", {"provider": "omp", "thinking": "high"})
        assert status == 200 and answer["thinking"] == "high" and brain.load(running.app.settings).thinking == "high"
        for body in ({"provider": "anthropic"}, {"provider": "omp", "models": {"fast": "--help"}}, {"provider": "omp", "key": "x"},
                     {"models": {}}, {"provider": "omp", "thinking": "ultra"}, {"provider": "omp", "thinking": 3}):
            assert running.request("/api/rika/brain", body)[0] == 400
        running.request("/api/rika/brain", {"provider": "openrouter"})
        assert brain.load(running.app.settings).provider == "openrouter"
        assert running.request("/api/rika/brain", {"provider": "omp"}, headers={"X-Kitty-Request": None})[0] == 403


@pytest.mark.parametrize(("level", "effort"), [("off", None), ("low", "low"), ("high", "high"), ("max", "max")])
def test_the_thinking_level_rides_on_every_omp_request(build, workspace, fake_omp, level, effort):
    brain.save(workspace.settings, "omp", {}, thinking=level)
    fake_omp.replies([_text("Start with kinematics.")])
    app, events = build()
    app.rika.submit(workspace.conn, "what should I study first?")
    assert of(until(events, finished), "message")[-1]["message"]["text"] == "Start with kinematics."
    [request] = fake_omp.requests
    assert request["body"].get("reasoning_effort") == effort
    # Thinking spends output tokens first, so a thinking request gets room for both.
    assert request["body"]["max_tokens"] == (2048 if effort is None else brain.THINKING_MAX_TOKENS)


def test_saving_the_brain_without_a_thinking_level_keeps_the_saved_one(workspace):
    brain.save(workspace.settings, "omp", {}, thinking="medium")
    brain.save(workspace.settings, "omp", {"fast": "anthropic/claude-haiku-4-5"})
    assert brain.load(workspace.settings).thinking == "medium"
