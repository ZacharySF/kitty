"""Choosing Rika's model and effort beside her field: the pinned model in the brain choice,
the catalog of models this computer's brain can use (omp's `omp models --json`, OpenRouter's
Settings → AI list), thinking on both providers in `llm.prepare` and its reservation, the
loop on a pinned model, and the HTTP routes."""
from __future__ import annotations

import json
import os
import stat
import sys
from contextlib import closing
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_rika_loop import (  # noqa: F401 - fixtures and helpers
    _text,
    _tool_call,
    build,
    finished,
    no_network,
    of,
    openrouter,
    until,
)
from test_rika_omp import FAKE_OMP, FakeOmp
from test_rika_omp import _text as omp_text

from kitty import web_ai
from kitty.core.rika import brain, llm

#: What `omp models --json` lists (trimmed): levels rika lacks (minimal, xhigh) are dropped.
OMP_MODELS = {"models": [
    {"provider": "anthropic", "kind": "chat", "id": "claude-opus-5-5", "selector": "anthropic/claude-opus-5-5",
     "name": "Claude Opus 5.5", "reasoning": True, "thinking": ["low", "medium", "high", "xhigh", "max"]},
    {"provider": "anthropic", "kind": "chat", "id": "claude-haiku-5-5", "selector": "anthropic/claude-haiku-5-5",
     "name": "Claude Haiku 5.5", "reasoning": True, "thinking": ["low", "high"]},
    {"provider": "anthropic", "kind": "chat", "id": "claude-sonnet-5-5", "selector": "anthropic/claude-sonnet-5-5",
     "name": "Claude Sonnet 5.5", "reasoning": True, "thinking": ["minimal", "low", "medium", "high"]},
    {"provider": "openrouter", "kind": "chat", "id": "~unbiased/pareto", "selector": "openrouter/~unbiased/pareto",
     "name": "Pareto", "reasoning": False, "thinking": None},
    {"provider": "openai-codex", "kind": "chat", "id": "gpt-plain", "selector": "openai-codex/gpt-plain",
     "name": "GPT Plain", "reasoning": False, "thinking": None},
    {"provider": "x", "kind": "chat", "id": "bad", "selector": "--help", "name": "Not a selector", "reasoning": False},
]}

#: The fake omp, also answering `omp models --json` from models.json (or failing when it is absent).
CATALOG_OMP = """\
import json, os, sys
if sys.argv[1:2] == ["models"]:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "catalog-calls.jsonl"), "a") as log:
        log.write(json.dumps(sys.argv[1:]) + "\\n")
    path = os.path.join(here, "models.json")
    if not os.path.exists(path):
        sys.exit(1)
    print(open(path).read())
    sys.exit(0)
""" + FAKE_OMP


class CatalogOmp(FakeOmp):
    def catalog(self, listing) -> None:
        (self.folder / "models.json").write_text(listing if isinstance(listing, str) else json.dumps(listing))

    @property
    def catalog_calls(self) -> int:
        path = self.folder / "catalog-calls.jsonl"
        return len(path.read_text().splitlines()) if path.exists() else 0


@pytest.fixture
def omp_with_catalog(tmp_path, monkeypatch) -> CatalogOmp:
    folder = tmp_path / "omp-bin"
    folder.mkdir()
    script = folder / "omp"
    script.write_text(f"#!{sys.executable}\n" + CATALOG_OMP)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(brain, "_omp_cache", None)
    fake = CatalogOmp(folder)
    fake.catalog(OMP_MODELS)
    return fake


def _app(workspace):
    return SimpleNamespace(settings=workspace.settings)


def _reasoning(conn, *ids):
    conn.executemany("INSERT INTO ai_model_reasoning(id) VALUES(?)", [(identifier,) for identifier in ids])


# The pinned model in the brain choice --------------------------------------------------

def test_the_pinned_model_is_saved_kept_and_reset_with_the_provider(workspace):
    settings = workspace.settings
    assert brain.load(settings).model == ""
    saved = brain.save(settings, "omp", {}, model="anthropic/claude-opus-5-5")
    assert saved.model == "anthropic/claude-opus-5-5" and brain.load(settings) == saved
    path = settings.root / ".private" / "rika" / "brain.json"
    assert json.loads(path.read_text())["model"] == "anthropic/claude-opus-5-5"
    # Left out, it stays with the provider; another provider's catalog never holds it.
    assert brain.save(settings, "omp", {}, thinking="high").model == "anthropic/claude-opus-5-5"
    assert brain.save(settings, "omp", {}, model="").model == ""
    brain.save(settings, "omp", {}, model="anthropic/claude-opus-5-5")
    assert brain.save(settings, "openrouter", {}).model == ""


def test_an_openrouter_model_must_be_in_the_settings_catalog(workspace, openrouter):
    settings, conn = workspace.settings, workspace.conn
    with pytest.raises(ValueError):
        brain.save(settings, "openrouter", {}, model="synthetic/missing", conn=conn)
    with pytest.raises(ValueError):
        brain.save(settings, "openrouter", {}, model="synthetic/deep")  # nothing to check it against
    assert brain.save(settings, "openrouter", {}, model="synthetic/deep", conn=conn).model == "synthetic/deep"
    assert brain.load(settings).model == "synthetic/deep"


@pytest.mark.parametrize(("provider", "model"), [("omp", "--help"), ("omp", 3), ("omp", "a b"), ("openrouter", "a b"),
                                                 ("openrouter", 3), ("omp", "x" * 201)])
def test_a_bad_pinned_model_is_refused_and_nothing_is_saved(workspace, provider, model):
    with pytest.raises(ValueError):
        brain.save(workspace.settings, provider, {}, model=model, conn=workspace.conn)
    assert brain.load(workspace.settings) == brain.Brain("openrouter")


# The catalog -------------------------------------------------------------------------------

def test_the_omp_catalog_lists_omps_models_with_rikas_levels_they_support(workspace, omp_with_catalog):
    brain.save(workspace.settings, "omp", {})
    found = brain.catalog(_app(workspace), workspace.conn)
    assert found == [
        {"id": "anthropic/claude-opus-5-5", "label": "Claude Opus 5.5", "thinking": ["off", "low", "medium", "high", "max"]},
        {"id": "anthropic/claude-haiku-5-5", "label": "Claude Haiku 5.5", "thinking": ["off", "low", "high"]},
        {"id": "anthropic/claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "thinking": ["off", "low", "medium", "high"]},
        {"id": "openrouter/~unbiased/pareto", "label": "Pareto", "thinking": ["off"]},
        {"id": "openai-codex/gpt-plain", "label": "GPT Plain", "thinking": ["off"]},
    ]
    # Cached for the process: a second look does not start omp again.
    assert brain.catalog(_app(workspace), workspace.conn) == found
    assert omp_with_catalog.catalog_calls == 1
    # The tier defaults share off, low and high.
    assert brain.auto_thinking(_app(workspace), workspace.conn, found) == ["off", "low", "high"]


@pytest.mark.parametrize("listing", [None, "not json", json.dumps({"models": "nope"}), json.dumps([1, 2])])
def test_an_omp_catalog_that_fails_is_empty_without_a_crash(workspace, omp_with_catalog, listing):
    brain.save(workspace.settings, "omp", {})
    if listing is None:
        (omp_with_catalog.folder / "models.json").unlink()
    else:
        omp_with_catalog.catalog(listing)
    assert brain.catalog(_app(workspace), workspace.conn) == []
    assert brain.auto_thinking(_app(workspace), workspace.conn, []) == list(brain.THINKING)


def test_without_omp_installed_the_omp_catalog_is_empty(workspace, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(brain, "_omp_cache", None)
    brain.save(workspace.settings, "omp", {})
    assert brain.catalog(_app(workspace), workspace.conn) == []


def test_the_openrouter_catalog_is_settings_list_with_effort_for_reasoning_models(workspace, openrouter):
    _reasoning(workspace.conn, "synthetic/deep", "synthetic/standard")
    found = brain.catalog(_app(workspace), workspace.conn)
    assert found == [
        {"id": "synthetic/deep", "label": "deep", "thinking": ["off", "low", "medium", "high"]},
        {"id": "synthetic/fast", "label": "fast", "thinking": ["off"]},
        {"id": "synthetic/standard", "label": "standard", "thinking": ["off", "low", "medium", "high"]},
    ]
    # The fast tier cannot think, so Auto can only be off.
    assert brain.auto_thinking(_app(workspace), workspace.conn, found) == ["off"]
    workspace.conn.execute("UPDATE ai_settings SET rika_fast_model='synthetic/deep' WHERE id=1")
    assert brain.auto_thinking(_app(workspace), workspace.conn, found) == ["off", "low", "medium", "high"]


# Thinking in llm.prepare -------------------------------------------------------------------

def _bound(call, completion_price, prompt_price=Decimal("0.000001")):
    input_bound = len(json.dumps(call.body, ensure_ascii=False).encode("utf-8")) + llm.FRAMING
    return Decimal(input_bound) * prompt_price + Decimal(call.body["max_tokens"]) * completion_price


MESSAGES = [{"role": "user", "content": "hello"}]


def test_openrouter_sends_no_reasoning_when_thinking_is_off(workspace, openrouter):
    _reasoning(workspace.conn, "synthetic/deep")
    call = llm.prepare(_app(workspace), workspace.conn, "synthetic/deep", MESSAGES, [], thinking="off")
    assert "reasoning" not in call.body and call.body["max_tokens"] == 2048
    assert call.maximum_cost == _bound(call, Decimal("0.000002"))


@pytest.mark.parametrize("level", ["low", "medium", "high", "max"])
def test_openrouter_sends_the_effort_and_reserves_room_for_it(workspace, openrouter, level):
    _reasoning(workspace.conn, "synthetic/deep")
    call = llm.prepare(_app(workspace), workspace.conn, "synthetic/deep", MESSAGES, [], thinking=level)
    assert call.body["reasoning"] == {"effort": level}
    assert call.body["max_tokens"] == brain.THINKING_MAX_TOKENS
    assert call.maximum_cost == _bound(call, Decimal("0.000002"))
    reservation = llm.reserve(workspace.conn, call)
    row = workspace.conn.execute("SELECT reserved_usd,plan_json FROM ai_reviews WHERE id=?", (reservation,)).fetchone()
    assert Decimal(row["reserved_usd"]) == call.maximum_cost
    assert call.maximum_cost > Decimal(brain.THINKING_MAX_TOKENS) * Decimal("0.000002")


def test_openrouter_thinking_defaults_to_the_saved_level_and_skips_models_that_cannot_reason(workspace, openrouter):
    _reasoning(workspace.conn, "synthetic/deep")
    brain.save(workspace.settings, "openrouter", {}, thinking="medium")
    call = llm.prepare(_app(workspace), workspace.conn, "synthetic/deep", MESSAGES, [])
    assert call.body["reasoning"] == {"effort": "medium"}
    plain = llm.prepare(_app(workspace), workspace.conn, "synthetic/fast", MESSAGES, [])
    assert "reasoning" not in plain.body and plain.body["max_tokens"] == 2048


def test_a_larger_output_limit_is_kept_when_thinking(workspace, openrouter):
    _reasoning(workspace.conn, "synthetic/deep")
    workspace.conn.execute("UPDATE ai_settings SET max_output_tokens=32000 WHERE id=1")
    call = llm.prepare(_app(workspace), workspace.conn, "synthetic/deep", MESSAGES, [], thinking="high")
    assert call.body["max_tokens"] == 32000


def test_thinking_counts_against_the_models_context(workspace, openrouter):
    _reasoning(workspace.conn, "synthetic/deep")
    workspace.conn.execute("UPDATE ai_models SET context_length=8000 WHERE id='synthetic/deep'")
    llm.prepare(_app(workspace), workspace.conn, "synthetic/deep", MESSAGES, [], thinking="off")
    with pytest.raises(llm.ModelUnavailable):
        llm.prepare(_app(workspace), workspace.conn, "synthetic/deep", MESSAGES, [], thinking="low")


@pytest.mark.parametrize(("saved", "asked", "effort"), [("off", "low", "low"), ("high", "off", None), ("medium", None, "medium"),
                                                        ("off", "max", "max")])
def test_omp_takes_the_asked_level_over_the_saved_one(workspace, omp_with_catalog, saved, asked, effort):
    brain.save(workspace.settings, "omp", {}, thinking=saved)
    call = llm.prepare(_app(workspace), workspace.conn, "anthropic/claude-opus-5-5", MESSAGES, [], thinking=asked)
    assert call.provider == "omp" and call.body.get("reasoning_effort") == effort and "reasoning" not in call.body
    assert call.body["max_tokens"] == (2048 if effort is None else brain.THINKING_MAX_TOKENS)
    reservation = llm.reserve(workspace.conn, call)
    assert workspace.conn.execute("SELECT reserved_usd FROM ai_reviews WHERE id=?", (reservation,)).fetchone()[0] == "0"


@pytest.mark.parametrize("level", ["ultra", "", 3, "xhigh"])
def test_an_unknown_thinking_level_is_refused_before_anything_is_sent(workspace, openrouter, level):
    with pytest.raises(ValueError):
        llm.prepare(_app(workspace), workspace.conn, "synthetic/deep", MESSAGES, [], thinking=level)


# The loop on a pinned model ----------------------------------------------------------------

def test_a_pinned_openrouter_model_runs_every_tier_and_escalation(build, workspace, openrouter):  # noqa: F811
    workspace.conn.execute("INSERT INTO ai_models VALUES('synthetic/pinned','Pinned','0.000001','0.000002',200000,?)",
                           (web_ai._now().isoformat(),))
    brain.save(workspace.settings, "openrouter", {}, model="synthetic/pinned", conn=workspace.conn)
    openrouter.replies = [_tool_call("add_card", '{"front": "Mitosis"}'),
                          _tool_call("add_card", '{"front": "Mitosis", "back": "Cell division"}'),
                          _text("Added your Mitosis card.")]
    app, events = build()
    app.rika.submit(workspace.conn, "could you make me a flashcard about mitosis")
    seen = until(events, finished)
    assert [body["model"] for _headers, body in openrouter.requests] == ["synthetic/pinned"] * 3
    assert [(event["tier"], event["model"]) for event in of(seen, "tier")] == [
        ("fast", "synthetic/pinned"), ("standard", "synthetic/pinned")]
    assert of(seen, "message")[-1]["message"]["text"] == "Added your Mitosis card."


def test_a_pinned_omp_model_runs_every_tier(build, workspace, omp_with_catalog):  # noqa: F811
    brain.save(workspace.settings, "omp", {}, model="anthropic/claude-sonnet-5-5")
    omp_with_catalog.replies([omp_text("Start with kinematics.")])
    app, events = build()
    app.rika.submit(workspace.conn, "plan my week around the physics exam", tier="deep")
    seen = until(events, finished)
    assert [request["body"]["model"] for request in omp_with_catalog.requests] == ["anthropic/claude-sonnet-5-5"]
    assert [(event["tier"], event["model"]) for event in of(seen, "tier")] == [("deep", "anthropic/claude-sonnet-5-5")]


# Over HTTP -----------------------------------------------------------------------------------

@pytest.mark.parametrize("front", [False, True], ids=["python", "rust-front"])
def test_the_model_is_chosen_and_the_catalog_read_over_http(tmp_path, monkeypatch, front):
    from terminal_support import front_site, make_root, site

    from kitty.db import connect

    folder = tmp_path / "omp-bin"
    folder.mkdir()
    script = folder / "omp"
    script.write_text(f"#!{sys.executable}\n" + CATALOG_OMP)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (folder / "models.json").write_text(json.dumps(OMP_MODELS))
    monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(brain, "_omp_cache", None)
    with (front_site if front else site)(make_root(tmp_path, ("workspace",))) as running:
        with closing(connect(running.app.settings.db_path)) as conn:
            conn.execute("INSERT INTO ai_models VALUES('synthetic/deep','Deep','0.000001','0.000002',200000,?)",
                         (web_ai._now().isoformat(),))
            conn.execute("INSERT INTO ai_model_reasoning(id) VALUES('synthetic/deep')")
            conn.commit()
        status, answer = running.request("/api/rika/brain")
        assert status == 200 and answer["model"] == ""
        status, answer = running.request("/api/rika/models")
        assert status == 200 and answer == {
            "provider": "openrouter", "auto": list(brain.THINKING),
            "models": [{"id": "synthetic/deep", "label": "Deep", "thinking": ["off", "low", "medium", "high"]}]}
        status, answer = running.request("/api/rika/brain", {"provider": "openrouter", "model": "synthetic/deep", "thinking": "high"})
        assert status == 200 and (answer["model"], answer["thinking"]) == ("synthetic/deep", "high")
        assert brain.load(running.app.settings).model == "synthetic/deep"
        for body in ({"provider": "openrouter", "model": "synthetic/missing"}, {"provider": "openrouter", "model": 3},
                     {"provider": "omp", "model": "--help"}, {"provider": "omp", "model": "a b"},
                     {"provider": "omp", "model": None}):
            assert running.request("/api/rika/brain", body)[0] == 400, body
        assert brain.load(running.app.settings).model == "synthetic/deep"
        status, answer = running.request("/api/rika/brain", {"provider": "omp", "model": "anthropic/claude-opus-5-5"})
        assert status == 200 and answer["model"] == "anthropic/claude-opus-5-5"
        status, answer = running.request("/api/rika/models")
        assert status == 200 and answer["provider"] == "omp" and answer["auto"] == ["off", "low", "high"]
        assert answer["models"][0] == {"id": "anthropic/claude-opus-5-5", "label": "Claude Opus 5.5",
                                       "thinking": ["off", "low", "medium", "high", "max"]}
        assert running.request("/api/rika/models?x=1")[0] == 400
        status, answer = running.request("/api/rika/brain", {"provider": "omp", "model": ""})
        assert status == 200 and answer["model"] == ""
