"""Synthetic, network-isolated recovery contracts; never executes browser tools."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from kitty.core.apply import recovery
from kitty.core.apply.recovery import (
    Action,
    Blocker,
    Decision,
    ModelBudget,
    Observation,
    OpenRouterPlanner,
    PlannerUnavailable,
    Reason,
    RecoveryAgent,
    RecoveryPolicy,
    Result,
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Choose:
    def __init__(self, action):
        self.action = action
        self.calls = []

    def plan(self, observation, allowed_actions, wait_seconds):
        self.calls.append((observation, allowed_actions, wait_seconds))
        return Decision(self.action, Reason.DIAGNOSTIC,
                        wait_seconds if self.action == Action.WAIT else 0)


def policy(**changes):
    return replace(RecoveryPolicy(max_steps=20, max_no_progress=10, max_cycles=10,
                                  max_inspects=10, max_waits=10, max_retries=10), **changes)


def budget(**changes):
    params = dict(max_requests=5, max_input_tokens=100_000, max_output_tokens=10_000,
                  max_usd=Decimal("1"), input_price_per_million=Decimal("2"),
                  output_price_per_million=Decimal("10"))
    params.update(changes)
    return ModelBudget(**params)


def planner(ledger=None, **changes):
    params = dict(endpoint="https://example.invalid/api/v1/chat/completions",
                  api_key="synthetic-key", model="synthetic/model", approved=True,
                  budget=ledger if ledger is not None else budget())
    params.update(changes)
    return OpenRouterPlanner(**params)


def response(action="inspect", arguments="{}", **message_changes):
    message = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "synthetic-call", "type": "function", "function": {
            "name": action, "arguments": arguments}}]}
    message.update(message_changes)
    return {"choices": [{"finish_reason": "tool_calls", "message": message}]}


def mock_http(monkeypatch, handler):
    real_client = httpx.Client
    requests = []

    def route(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(recovery.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(route), **kwargs))
    return requests


def test_local_default_diagnoses_waits_retries_and_reloads_without_ai():
    agent = RecoveryAgent(policy=policy(max_waits=2, max_retries=1))
    observed = Observation("local", required_count=2, busy=True, can_retry=True, can_reload=True)
    actions = [agent.decide(observed).action for _ in range(4)]
    assert actions == [Action.INSPECT, Action.WAIT, Action.WAIT, Action.RELOAD]
    observed = replace(observed, busy=False)
    assert agent.decide(observed).action == Action.INSPECT
    assert agent.decide(observed).action == Action.RETRY
    assert agent.decide(observed).action == Action.INSPECT
    decision = agent.decide(replace(observed, can_retry=False, can_reload=False))
    assert decision == Decision(Action.HANDOFF, Reason.NO_SUPPORTED_PATH)


def test_retry_needs_supported_change_and_busy_wait_is_bounded():
    agent = RecoveryAgent(policy=policy(max_inspects=0, max_waits=2))
    assert agent.decide(Observation("unknown")).action == Action.HANDOFF
    agent = RecoveryAgent(policy=policy(max_inspects=0, max_waits=2))
    obs = Observation("busy", busy=True, can_retry=True)
    assert agent.decide(obs).action == Action.WAIT
    assert agent.decide(obs).action == Action.WAIT
    assert agent.decide(obs).action == Action.HANDOFF
    assert agent.event(agent.decide(obs))["counters"]["retry"] == 0


@pytest.mark.parametrize("blocker", [blocker for blocker in Blocker if blocker not in {
    Blocker.NONE, Blocker.UNKNOWN, Blocker.LOADING, Blocker.TRANSIENT}])
def test_every_protected_boundary_stops_before_planner(blocker):
    chosen = Choose(Action.RETRY)
    agent = RecoveryAgent(planner=chosen)
    decision = agent.decide(Observation("protected", blocker=blocker, can_retry=True, can_reload=True))
    assert decision == Decision(Action.HANDOFF, Reason.BOUNDARY)
    assert not chosen.calls
    # Later changing observations do not undo a terminal boundary.
    assert agent.decide(Observation("later", can_retry=True)) is decision
    assert agent.event(decision)["cause"] == blocker.value


def test_count_progress_resets_only_soft_counter_not_step_or_action_allowance():
    chosen = Choose(Action.RETRY)
    agent = RecoveryAgent(policy=policy(max_steps=4, max_no_progress=2, max_retries=4), planner=chosen)
    for signature, required, expected in (("a", 5, 0), ("b", 5, 1), ("c", 4, 0), ("d", 3, 0)):
        decision = agent.decide(Observation(signature, required_count=required, can_retry=True))
        assert decision.action == Action.RETRY
        assert agent.event(decision)["counters"]["no_progress"] == expected
    decision = agent.decide(Observation("e", required_count=2, can_retry=True))
    assert decision == Decision(Action.HANDOFF, Reason.STEP_LIMIT)
    assert len(chosen.calls) == 4
    assert agent.event(decision)["counters"]["retry"] == 4


def test_signature_churn_and_count_oscillation_are_not_progress():
    agent = RecoveryAgent(policy=policy(max_no_progress=2), planner=Choose(Action.RETRY))
    observations = [Observation(str(i), required_count=count, can_retry=True)
                    for i, count in enumerate((2, 3, 2))]
    assert agent.decide(observations[0]).action == Action.RETRY
    assert agent.decide(observations[1]).action == Action.RETRY
    assert agent.decide(observations[2]).reason == Reason.NO_PROGRESS


def test_alternating_cycle_remains_detected_even_with_real_count_progress():
    agent = RecoveryAgent(policy=policy(max_cycles=1), planner=Choose(Action.RETRY))
    assert agent.decide(Observation("a", required_count=5, can_retry=True)).action == Action.RETRY
    assert agent.decide(Observation("b", required_count=4, can_retry=True)).action == Action.RETRY
    assert agent.decide(Observation("a", required_count=3, can_retry=True)).reason == Reason.CYCLE


def test_stationary_loading_uses_bounded_waits_and_one_safe_reload_before_handoff():
    agent = RecoveryAgent()
    observation = Observation("stationary", busy=True, blocker=Blocker.LOADING, can_reload=True)
    decisions = [agent.decide(observation) for _ in range(5)]
    assert [decision.action for decision in decisions] == [
        Action.INSPECT, Action.WAIT, Action.WAIT, Action.RELOAD, Action.HANDOFF]
    assert decisions[-1].reason == Reason.NO_PROGRESS
    event = agent.event(decisions[-1])
    assert event["counters"]["max_visits"] == 1
    assert event["counters"]["wait"] == 2 and event["counters"]["reload"] == 1
    assert agent.decide(replace(observation, signature="later")) is decisions[-1]


def test_stationary_runs_do_not_erase_alternating_cycle_history():
    agent = RecoveryAgent(policy=policy(max_cycles=1), planner=Choose(Action.RETRY))
    for signature in ("a", "a", "b", "b"):
        assert agent.decide(Observation(signature, can_retry=True)).action == Action.RETRY
    assert agent.decide(Observation("a", can_retry=True)).reason == Reason.CYCLE


def test_first_decision_starts_deadline_and_progress_cannot_reset_it():
    clock = Clock()
    agent = RecoveryAgent(policy=policy(max_seconds=3), planner=Choose(Action.RETRY), clock=clock)
    clock.now = 500
    assert agent.decide(Observation("a", required_count=5, can_retry=True)).action == Action.RETRY
    clock.now = 502
    assert agent.decide(Observation("b", required_count=4, can_retry=True)).action == Action.RETRY
    clock.now = 503
    assert agent.decide(Observation("c", required_count=3, can_retry=True)).reason == Reason.DEADLINE


def test_wait_is_clamped_to_remaining_time_and_model_cannot_overrun_deadline():
    clock = Clock()
    chosen = Choose(Action.WAIT)
    agent = RecoveryAgent(policy=policy(max_seconds=0.25), planner=chosen, clock=clock)
    assert agent.decide(Observation("busy", busy=True)).wait_seconds == 0.25

    class Slow:
        def plan(self, observation, allowed_actions, wait_seconds):
            clock.now += 2
            return Decision(Action.RETRY, Reason.SUPPORTED_RETRY)

    agent = RecoveryAgent(policy=policy(max_seconds=1), planner=Slow(), clock=clock)
    assert agent.decide(Observation("a", can_retry=True)).reason == Reason.DEADLINE

    class SlowWait:
        def plan(self, observation, allowed_actions, wait_seconds):
            clock.now += 0.75
            return Decision(Action.WAIT, Reason.BUSY, wait_seconds)

    agent = RecoveryAgent(policy=policy(max_seconds=1), planner=SlowWait(), clock=clock)
    assert agent.decide(Observation("busy", busy=True)).wait_seconds == 0.25


@pytest.mark.parametrize("action,observation,limits", [
    (Action.RETRY, Observation("a", can_retry=False), {}),
    (Action.RETRY, Observation("a", can_retry=True, busy=True), {}),
    (Action.RELOAD, Observation("a", can_reload=False), {}),
    (Action.WAIT, Observation("a", busy=False), {}),
    (Action.INSPECT, Observation("a"), {"max_inspects": 0}),
    (Action.RETRY, Observation("a", can_retry=True), {"max_retries": 0}),
])
def test_planner_cannot_expand_action_authority(action, observation, limits):
    agent = RecoveryAgent(policy=policy(**limits), planner=Choose(action))
    decision = agent.decide(observation)
    assert decision == Decision(Action.HANDOFF, Reason.ACTION_DENIED)


def test_action_caps_survive_progress():
    agent = RecoveryAgent(policy=policy(max_retries=1), planner=Choose(Action.RETRY))
    assert agent.decide(Observation("a", required_count=4, can_retry=True)).action == Action.RETRY
    assert agent.decide(Observation("b", required_count=3, can_retry=True)).reason == Reason.ACTION_DENIED


@pytest.mark.parametrize("result", [Result.DENIED, Result.CANCELLED])
def test_denied_or_cancelled_receipt_is_terminal_and_does_not_grant_retry(result):
    agent = RecoveryAgent()
    decision = agent.decide(Observation("a", can_retry=True))
    event = agent.record_result(decision, result)
    assert event["result"] == result.value
    assert agent.decide(Observation("b", can_retry=True)).action == Action.HANDOFF


def test_receipts_are_typed_single_use_and_cannot_replay_stale_decisions():
    agent = RecoveryAgent(policy=policy())
    first = agent.decide(Observation("a", can_retry=True))
    agent.record_result(first, Result.FAILED)
    with pytest.raises(ValueError):
        agent.record_result(first, Result.COMPLETED)
    second = agent.decide(Observation("b", can_retry=True))
    with pytest.raises(ValueError):
        agent.record_result(first, Result.COMPLETED)
    with pytest.raises(ValueError):
        agent.record_result(second, "secret exception text")


def test_events_and_remote_observations_never_include_local_signature_or_text():
    signature = "synthetic-secret@example.invalid /private/path?password=sentinel"
    agent = RecoveryAgent()
    decision = agent.decide(Observation(signature, required_count=3, field_count=5))
    event = agent.record_result(decision, Result.OBSERVED)
    serialized = json.dumps(event, allow_nan=False)
    assert signature not in serialized
    assert set(event) == {"version", "stage", "cause", "reason", "action", "result", "counters"}
    assert event["version"] == 1 and event["stage"] == "recovery"
    assert all(type(value) is int for value in event["counters"].values())
    assert "signature" not in Observation(signature).structural()


@pytest.mark.parametrize("kwargs", [
    {"required_count": -1}, {"field_count": True}, {"invalid_count": "applicant text"},
    {"blocker": "unknown"}, {"can_retry": "true"}, {"signature": ""},
])
def test_observations_reject_untyped_or_unbounded_data(kwargs):
    values = {"signature": "a", **kwargs}
    with pytest.raises(ValueError):
        Observation(**values)


@pytest.mark.parametrize("kwargs", [{"max_steps": -1}, {"max_seconds": float("nan")},
                                     {"wait_seconds": 31}, {"max_retries": True}])
def test_policy_rejects_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        RecoveryPolicy(**kwargs)


@pytest.mark.parametrize("kwargs", [
    {"approved": False}, {"api_key": ""}, {"api_key": "   "},
    {"endpoint": "http://example.invalid/"},
    {"endpoint": "https://user:secret@example.invalid/"},
    {"budget": budget(max_requests=0)}, {"budget": budget(max_input_tokens=0)},
    {"budget": budget(max_output_tokens=0)}, {"budget": budget(max_usd=Decimal(0))},
    {"budget": budget(max_usd=Decimal("0.000000001"))},
])
def test_remote_requires_opt_in_key_and_real_shared_allowance(monkeypatch, kwargs):
    sent = mock_http(monkeypatch, lambda request: pytest.fail("No remote request authorized"))
    remote = planner(**kwargs)
    with pytest.raises(PlannerUnavailable) as raised:
        remote.plan(Observation("a"), (Action.INSPECT, Action.HANDOFF), 1)
    assert raised.value.reason in (Reason.REMOTE_DISABLED, Reason.REMOTE_BUDGET)
    assert sent == []
    assert remote.budget.requests == 0


def test_real_function_tool_request_is_structural_and_validated(monkeypatch):
    sent = mock_http(monkeypatch, lambda request: httpx.Response(200, json=response()))
    ledger = budget()
    remote = planner(ledger)
    decision = remote.plan(Observation("sensitive-local-only", required_count=3, field_count=8),
                           (Action.INSPECT, Action.HANDOFF), 1)
    assert decision == Decision(Action.INSPECT, Reason.DIAGNOSTIC)
    request = json.loads(sent[0].content)
    assert "sensitive-local-only" not in sent[0].content.decode()
    assert "synthetic-key" not in sent[0].content.decode()
    assert sent[0].headers["Authorization"] == "Bearer synthetic-key"
    assert request["tool_choice"] == "required" and request["parallel_tool_calls"] is False
    assert {tool["function"]["name"] for tool in request["tools"]} == {"inspect", "handoff"}
    assert all(tool["function"]["parameters"]["additionalProperties"] is False for tool in request["tools"])
    assert json.loads(request["messages"][1]["content"]) == Observation("ignored", required_count=3, field_count=8).structural()
    assert request["provider"] == {
        "data_collection": "deny", "require_parameters": True, "allow_fallbacks": False,
        "max_price": {"prompt": 2.0, "completion": 10.0, "request": 0, "image": 0}}
    snapshot = ledger.snapshot()
    bound = len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + 1024
    assert snapshot["input_tokens"] == bound
    assert snapshot["output_tokens"] == remote.max_output_tokens
    assert ledger.reserved_usd == (Decimal(bound) * 2 + remote.max_output_tokens * 10) / 1_000_000


@pytest.mark.parametrize("failure", ["http", "transport", "json", "malformed", "redirect"])
def test_errors_consume_shared_whole_batch_reservation(monkeypatch, failure):
    def route(request):
        if failure == "transport":
            raise httpx.ReadTimeout("synthetic-secret-must-not-escape")
        if failure == "json":
            return httpx.Response(200, content=b"not JSON synthetic-secret")
        if failure == "malformed":
            return httpx.Response(200, json=response(action="submit"))
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://other.invalid/secret"})
        return httpx.Response(500, content=b"synthetic-secret")

    sent = mock_http(monkeypatch, route)
    ledger = budget(max_requests=1)
    first, second = planner(ledger), planner(ledger)
    with pytest.raises(PlannerUnavailable) as raised:
        first.plan(Observation("a"), (Action.INSPECT,), 1)
    assert "synthetic-secret" not in str(raised.value)
    reserved = ledger.reserved_usd
    assert reserved > 0
    with pytest.raises(PlannerUnavailable) as raised:
        second.plan(Observation("b"), (Action.INSPECT,), 1)
    assert raised.value.reason == Reason.REMOTE_BUDGET
    assert ledger.requests == 1 and ledger.reserved_usd == reserved and len(sent) == 1


@pytest.mark.parametrize("limit", ["max_requests", "max_input_tokens", "max_output_tokens", "max_usd"])
def test_budget_caps_each_approved_dimension_without_partial_reservation(limit):
    limits = dict(max_requests=2, max_input_tokens=20, max_output_tokens=20,
                  max_usd=Decimal("0.00024"))
    limits[limit] = {"max_requests": 1, "max_input_tokens": 10, "max_output_tokens": 10,
                     "max_usd": Decimal("0.00012")}[limit]
    ledger = budget(**limits)
    assert ledger.reserve(10, 10)
    before = ledger.snapshot()
    assert not ledger.reserve(10, 10)
    assert ledger.snapshot() == before


def test_budget_reservations_are_atomic_for_shared_jobs():
    ledger = budget(max_requests=3)
    with ThreadPoolExecutor(max_workers=8) as executor:
        admitted = list(executor.map(lambda _: ledger.reserve(10, 10), range(30)))
    assert sum(admitted) == 3 and ledger.requests == 3
    assert ledger.snapshot()["input_tokens"] == 30


@pytest.mark.parametrize("kwargs", [{"max_usd": "NaN"}, {"max_usd": -1},
                                     {"max_requests": True}, {"input_price_per_million": "Infinity"}])
def test_invalid_budgets_fail_closed(kwargs):
    with pytest.raises(ValueError):
        budget(**kwargs)


@pytest.mark.parametrize("data", [
    {}, {"choices": []}, {"choices": [{}, {}]},
    response(action="submit"), response(action="retry"), response(arguments='{"selector":"#password"}'),
    response(arguments='{"code":"execute()"}'), response(arguments="[]"),
    response(arguments="not-json"), response(content="Use these credentials"),
    response(content=[]), response(refusal="no"), response(tool_calls=[]),
    response(role="user"),
    response(tool_calls=response()["choices"][0]["message"]["tool_calls"] * 2),
    response(function_call={"name": "inspect", "arguments": "{}"}),
    {"choices": [{"finish_reason": "length", "message": response()["choices"][0]["message"]}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "inspect"}}]},
])
def test_malformed_multiple_prose_or_unapproved_tool_calls_fail_closed(monkeypatch, data):
    sent = mock_http(monkeypatch, lambda request: httpx.Response(200, json=data))
    remote = planner()
    with pytest.raises(PlannerUnavailable) as raised:
        remote.plan(Observation("a"), (Action.INSPECT, Action.HANDOFF), 1)
    assert raised.value.reason == Reason.REMOTE_MALFORMED
    assert remote.budget.requests == 1 and len(sent) == 1


@pytest.mark.parametrize("arguments", ['{}', '{"seconds":true}', '{"seconds":-1}',
                                        '{"seconds":31}', '{"seconds":0.5}',
                                        '{"seconds":1,"seconds":1}', '{"seconds":NaN}',
                                        '{"seconds":1,"selector":"#x"}'])
def test_wait_tool_arguments_are_exact_and_bounded(monkeypatch, arguments):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=response("wait", arguments)))
    with pytest.raises(PlannerUnavailable) as raised:
        planner().plan(Observation("a", busy=True), (Action.WAIT,), 1)
    assert raised.value.reason == Reason.REMOTE_MALFORMED


@pytest.mark.parametrize("action,arguments,expected", [
    (Action.WAIT, '{"seconds":0.25}', Decision(Action.WAIT, Reason.BUSY, 0.25)),
    (Action.HANDOFF, '{}', Decision(Action.HANDOFF, Reason.NO_SUPPORTED_PATH)),
])
def test_valid_wait_and_handoff_tools(monkeypatch, action, arguments, expected):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=response(action.value, arguments)))
    assert planner().plan(Observation("a", busy=True), (action,), 0.25) == expected


def test_remote_failure_falls_back_locally_without_resetting_budget_or_steps(monkeypatch):
    sent = mock_http(monkeypatch, lambda request: httpx.Response(500))
    ledger = budget(max_requests=1)
    agent = RecoveryAgent(policy=policy(max_steps=3), planner=planner(ledger))
    observations = [Observation(str(i), required_count=3-i, can_retry=True) for i in range(4)]
    first = agent.decide(observations[0])
    assert first == Decision(Action.INSPECT, Reason.REMOTE_ERROR)
    second = agent.decide(observations[1])
    assert second == Decision(Action.RETRY, Reason.REMOTE_BUDGET)
    assert agent.decide(observations[2]).action == Action.INSPECT
    assert agent.decide(observations[3]).reason == Reason.STEP_LIMIT
    assert ledger.requests == 1 and len(sent) == 1
    assert "synthetic-key" not in json.dumps(agent.event(first))


def test_direct_remote_planner_cannot_recover_protected_boundaries(monkeypatch):
    sent = mock_http(monkeypatch, lambda request: pytest.fail("Protected boundary must remain local"))
    remote = planner()
    decision = remote.plan(Observation("a", blocker=Blocker.AUTH_REJECTED, can_retry=True),
                           (Action.RETRY, Action.HANDOFF), 1)
    assert decision == Decision(Action.HANDOFF, Reason.BOUNDARY)
    assert remote.budget.requests == 0 and sent == []


def test_zero_step_and_time_allowances_stop_before_any_planner():
    for limits, reason in (({"max_steps": 0}, Reason.STEP_LIMIT),
                           ({"max_seconds": 0}, Reason.DEADLINE)):
        chosen = Choose(Action.RETRY)
        agent = RecoveryAgent(policy=policy(**limits), planner=chosen)
        assert agent.decide(Observation("a", can_retry=True)).reason == reason
        assert not chosen.calls


def test_cost_reservation_covers_float_price_serialization_rounding():
    ledger = budget(input_price_per_million=Decimal("1.19999999999999998"),
                    output_price_per_million=Decimal(0), max_input_tokens=1_000_000)
    assert ledger.reserve(1_000_000, 0) is False  # The default one-dollar cap.
    ledger = budget(max_usd=Decimal("1.2"),
                    input_price_per_million=Decimal("1.19999999999999998"),
                    output_price_per_million=Decimal(0), max_input_tokens=1_000_000)
    assert ledger.reserve(1_000_000, 0)
    assert ledger.reserved_usd == Decimal("1.2")


def test_malformed_remote_proposal_hands_off_instead_of_executing_tools(monkeypatch):
    sent = mock_http(monkeypatch, lambda request: httpx.Response(
        200, json=response(arguments='{"selector":"#credential"}')))
    ledger = budget()
    agent = RecoveryAgent(planner=planner(ledger))
    decision = agent.decide(Observation("a", can_retry=True, can_reload=True))
    assert decision == Decision(Action.HANDOFF, Reason.REMOTE_MALFORMED)
    assert agent.decide(Observation("b", can_retry=True)) is decision
    assert ledger.requests == 1 and len(sent) == 1
    assert agent.event(decision)["counters"]["retry"] == 0

@pytest.mark.parametrize("receipt", [Result.FAILED, Result.NO_EFFECT])
def test_ineffective_strategy_is_removed_until_diagnostic_evidence_changes(receipt):
    class Adaptive:
        def __init__(self):
            self.seen = []

        def plan(self, observation, allowed_actions, wait_seconds):
            self.seen.append((observation, allowed_actions))
            for action in (Action.RECOMMIT, Action.RETRY):
                if action in allowed_actions:
                    return Decision(action, Reason.DIAGNOSTIC)
            return Decision(Action.HANDOFF, Reason.NO_SUPPORTED_PATH)

    planner = Adaptive()
    agent = RecoveryAgent(policy(max_inspects=0, max_recommits=3), planner)
    observed = Observation("unchanged", required_count=2, can_recommit=True, can_retry=True)
    first = agent.decide(observed)
    assert first.action == Action.RECOMMIT
    agent.record_result(first, receipt)
    second = agent.decide(observed)
    assert second.action == Action.RETRY
    received, allowed = planner.seen[-1]
    assert Action.RECOMMIT not in allowed
    assert [(attempt.action, attempt.result) for attempt in received.attempts] == [(Action.RECOMMIT, receipt)]
    agent.record_result(second, Result.NO_EFFECT)
    changed = replace(observed, signature="new-control", required_count=1)
    assert agent.decide(changed).action == Action.RECOMMIT


def test_hidden_owned_tab_must_be_focused_before_any_field_repair():
    agent = RecoveryAgent(policy(max_inspects=0))
    observed = Observation("hidden", can_focus=True, can_recommit=True, can_retry=True)
    decision = agent.decide(observed)
    assert decision.action == Action.FOCUS
    agent.record_result(decision, Result.COMPLETED)
    assert agent.decide(replace(observed, signature="visible", can_focus=False)).action == Action.RECOMMIT


def test_remote_replanning_receives_outcomes_but_not_local_state(monkeypatch):
    responses = iter(("recommit", "retry"))
    sent = mock_http(monkeypatch, lambda request: httpx.Response(200, json=response(next(responses))))
    agent = RecoveryAgent(policy(max_inspects=0), planner())
    observed = Observation("PRIVATE-LOCAL-FIELD", required_count=1, can_recommit=True, can_retry=True)
    first = agent.decide(observed)
    agent.record_result(first, Result.NO_EFFECT)
    assert agent.decide(observed).action == Action.RETRY
    request = json.loads(sent[-1].content)
    payload = json.loads(request["messages"][1]["content"])
    assert payload["attempts"] == [{"action": "recommit", "result": "no_effect"}]
    assert "PRIVATE-LOCAL-FIELD" not in sent[-1].content.decode()
    assert "recommit" not in {tool["function"]["name"] for tool in request["tools"]}
