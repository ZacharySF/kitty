"""Bounded recovery planning; the caller alone owns and executes browser tools.

Observations are structural, never page text. Signatures stay local and are not
included in model requests or events. Inspect means obtaining a *fresh*, richer
structural observation, not executing model-supplied selectors or code. Retry is
only an already-supported preparation path, never a credential resubmission.
No decision grants authorization or asserts that preparation succeeded.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from threading import Lock
from typing import Protocol

import httpx


class Action(StrEnum):
    INSPECT = "inspect"
    WAIT = "wait"
    FOCUS = "focus"
    RECOMMIT = "recommit"
    RETRY = "retry"
    RELOAD = "reload"
    HANDOFF = "handoff"


class Blocker(StrEnum):
    NONE = "none"
    UNKNOWN = "unknown"
    LOADING = "loading"
    TRANSIENT = "transient"
    HUMAN = "human"
    MISSING_FACT = "missing_fact"
    AUTH_REJECTED = "auth_rejected"
    VERIFICATION = "verification"
    CAPTCHA = "captcha"
    MFA = "mfa"
    BIOMETRIC = "biometric"
    CONSENT = "consent"
    CLOSED = "closed"
    APPROVAL_DRIFT = "approval_drift"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    REVIEW_BOUNDARY = "review_boundary"


_RECOVERABLE = frozenset({Blocker.NONE, Blocker.UNKNOWN, Blocker.LOADING, Blocker.TRANSIENT})


class Reason(StrEnum):
    DIAGNOSTIC = "diagnostic"
    BUSY = "busy"
    RESTORE_FOCUS = "restore_focus"
    COMMIT_SAVED_VALUE = "commit_saved_value"
    SUPPORTED_RETRY = "supported_retry"
    SAFE_RELOAD = "safe_reload"
    NO_SUPPORTED_PATH = "no_supported_path"
    BOUNDARY = "boundary"
    STEP_LIMIT = "step_limit"
    DEADLINE = "deadline"
    NO_PROGRESS = "no_progress"
    CYCLE = "cycle"
    ACTION_DENIED = "action_denied"
    REMOTE_DISABLED = "remote_disabled"
    REMOTE_BUDGET = "remote_budget"
    REMOTE_ERROR = "remote_error"
    REMOTE_MALFORMED = "remote_malformed"


class Result(StrEnum):
    PLANNED = "planned"
    OBSERVED = "observed"
    COMPLETED = "completed"
    NO_EFFECT = "no_effect"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"
    HANDED_OFF = "handed_off"


def _count(value: int, name: str) -> None:
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        raise ValueError(f"Invalid {name}")


def _seconds(value: float, name: str, *, positive: bool = False) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"Invalid {name}")


@dataclass(frozen=True)
class Attempt:
    action: Action
    result: Result

    def __post_init__(self) -> None:
        if not isinstance(self.action, Action) or not isinstance(self.result, Result):
            raise ValueError("Invalid recovery attempt")


@dataclass(frozen=True)
class Observation:
    signature: str
    required_count: int = 0
    invalid_count: int = 0
    field_count: int = 0
    busy: bool = False
    blocker: Blocker = Blocker.NONE
    can_reload: bool = False
    can_retry: bool = False
    can_focus: bool = False
    can_recommit: bool = False
    attempts: tuple[Attempt, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.signature, str) or not 1 <= len(self.signature) <= 256:
            raise ValueError("Invalid local signature")
        for name in ("required_count", "invalid_count", "field_count"):
            _count(getattr(self, name), name)
        for name in ("busy", "can_reload", "can_retry", "can_focus", "can_recommit"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Invalid {name}")
        if not isinstance(self.blocker, Blocker):
            raise ValueError("Invalid blocker category")
        if not isinstance(self.attempts, tuple) or len(self.attempts) > 64 or any(
            not isinstance(attempt, Attempt) for attempt in self.attempts
        ):
            raise ValueError("Invalid recovery history")

    def structural(self) -> dict:
        """Explicit allowlist: even an opaque signature is unnecessary remotely."""
        return {
            "required_count": self.required_count, "invalid_count": self.invalid_count,
            "field_count": self.field_count, "busy": self.busy,
            "blocker": self.blocker.value, "can_reload": self.can_reload,
            "can_retry": self.can_retry, "can_focus": self.can_focus,
            "can_recommit": self.can_recommit,
            "attempts": [{"action": attempt.action.value, "result": attempt.result.value}
                         for attempt in self.attempts],
        }


@dataclass(frozen=True)
class RecoveryPolicy:
    max_steps: int = 6
    max_seconds: float = 45
    max_no_progress: int = 4
    max_cycles: int = 2
    max_inspects: int = 2
    max_waits: int = 2
    max_retries: int = 1
    max_reloads: int = 1
    max_focuses: int = 1
    max_recommits: int = 1
    wait_seconds: float = 1

    def __post_init__(self) -> None:
        for name in ("max_steps", "max_no_progress", "max_cycles", "max_inspects",
                     "max_waits", "max_retries", "max_reloads", "max_focuses", "max_recommits"):
            _count(getattr(self, name), name)
        _seconds(self.max_seconds, "deadline")
        _seconds(self.wait_seconds, "wait")
        if self.wait_seconds > 30:
            raise ValueError("Wait must not exceed 30 seconds")


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: Reason
    wait_seconds: float = 0

    def __post_init__(self) -> None:
        if not isinstance(self.action, Action) or not isinstance(self.reason, Reason):
            raise ValueError("Invalid recovery decision")
        _seconds(self.wait_seconds, "wait")
        if self.wait_seconds > 30 or (self.action != Action.WAIT and self.wait_seconds != 0):
            raise ValueError("Invalid action arguments")


class Planner(Protocol):
    def plan(self, observation: Observation, allowed_actions: tuple[Action, ...],
             wait_seconds: float) -> Decision: ...


class LocalPlanner:
    """Finite diagnostic, loading, supported-path and safe-reload ordering."""

    def plan(self, observation: Observation, allowed_actions: tuple[Action, ...],
             wait_seconds: float) -> Decision:
        for action, reason in ((Action.INSPECT, Reason.DIAGNOSTIC),
                               (Action.WAIT, Reason.BUSY),
                               (Action.FOCUS, Reason.RESTORE_FOCUS),
                               (Action.RECOMMIT, Reason.COMMIT_SAVED_VALUE),
                               (Action.RETRY, Reason.SUPPORTED_RETRY),
                               (Action.RELOAD, Reason.SAFE_RELOAD)):
            if action in allowed_actions:
                return Decision(action, reason, wait_seconds if action == Action.WAIT else 0)
        return Decision(Action.HANDOFF, Reason.NO_SUPPORTED_PATH)


class PlannerUnavailable(Exception):
    """Only a bounded category crosses the planner boundary; never raw errors."""

    def __init__(self, reason: Reason):
        if reason not in (Reason.REMOTE_DISABLED, Reason.REMOTE_BUDGET,
                          Reason.REMOTE_ERROR, Reason.REMOTE_MALFORMED):
            raise ValueError("Invalid planner failure")
        self.reason = reason
        super().__init__(reason.value)


@dataclass
class _Reservations:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal(0)


@dataclass(frozen=True, kw_only=True)
class ModelBudget:
    """One shared whole-batch ledger; reservations are atomic and never refunded.

    Prices are maximum USD per million tokens, enforced in OpenRouter routing.
    Input is conservatively reserved at one token per serialized UTF-8 byte plus
    framing. An error, refused response or cancelled request still spends its
    reservation. No ledger is implicitly created or reset by an agent.
    """

    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    max_usd: Decimal
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    _usage: _Reservations = field(default_factory=_Reservations, init=False, repr=False, compare=False)
    _lock: object = field(default_factory=Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("max_requests", "max_input_tokens", "max_output_tokens"):
            _count(getattr(self, name), name)
        for name in ("max_usd", "input_price_per_million", "output_price_per_million"):
            try:
                value = Decimal(str(getattr(self, name)))
            except (InvalidOperation, ValueError):
                raise ValueError("Invalid model budget") from None
            if not value.is_finite() or value < 0:
                raise ValueError("Invalid model budget")
            if name != "max_usd" and not math.isfinite(float(value)):
                raise ValueError("Invalid model price ceiling")
            object.__setattr__(self, name, value)

    def reserve(self, input_tokens: int, output_tokens: int) -> bool:
        _count(input_tokens, "input reservation")
        _count(output_tokens, "output reservation")
        with self._lock, localcontext() as context:
            # JSON float conversion and Decimal arithmetic must never round the
            # reservation below the actual provider price ceilings.
            context.rounding = ROUND_CEILING
            input_price = max(self.input_price_per_million,
                              Decimal(str(float(self.input_price_per_million))))
            output_price = max(self.output_price_per_million,
                               Decimal(str(float(self.output_price_per_million))))
            cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
            usage = self._usage
            total_cost = usage.cost_usd + cost
            if (self.max_requests == 0 or self.max_input_tokens == 0
                    or self.max_output_tokens == 0 or self.max_usd == 0
                    or usage.requests + 1 > self.max_requests
                    or usage.input_tokens + input_tokens > self.max_input_tokens
                    or usage.output_tokens + output_tokens > self.max_output_tokens
                    or total_cost > self.max_usd):
                return False
            usage.requests += 1
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens
            usage.cost_usd = total_cost
            return True

    @property
    def reserved_usd(self) -> Decimal:
        with self._lock:
            return self._usage.cost_usd

    @property
    def requests(self) -> int:
        with self._lock:
            return self._usage.requests

    def snapshot(self) -> dict:
        with self._lock:
            return {"requests": self._usage.requests, "input_tokens": self._usage.input_tokens,
                    "output_tokens": self._usage.output_tokens, "cost_usd": str(self._usage.cost_usd)}


_SYSTEM = (
    "Choose exactly one supplied recovery function using the structural diagnosis and "
    "attempt outcomes. Do not repeat a failed or no-effect strategy on unchanged evidence. "
    "Focus restores the owned tab; recommit re-enters only an exact saved invalid value; "
    "retry re-reads supported blank fields. Tools cannot authorize actions, submit applications, "
    "invent answers, retry credentials, bypass human boundaries, or alter browser ownership. "
    "Never return prose or multiple calls. Use handoff when no safe supported path exists."
)
_ACTION_REASONS = {
    Action.INSPECT: Reason.DIAGNOSTIC, Action.WAIT: Reason.BUSY,
    Action.FOCUS: Reason.RESTORE_FOCUS, Action.RECOMMIT: Reason.COMMIT_SAVED_VALUE,
    Action.RETRY: Reason.SUPPORTED_RETRY, Action.RELOAD: Reason.SAFE_RELOAD,
    Action.HANDOFF: Reason.NO_SUPPORTED_PATH,
}


class OpenRouterPlanner:
    """Opt-in, function-tool planner; not a browser controller or credential reader."""

    def __init__(self, *, endpoint: str, api_key: str, model: str, approved: bool,
                 budget: ModelBudget, max_output_tokens: int = 128, timeout: float = 10):
        _count(max_output_tokens, "model output limit")
        _seconds(timeout, "model timeout", positive=True)
        if not 1 <= max_output_tokens <= 4096:
            raise ValueError("Invalid model output limit")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.approved = approved is True
        self.budget = budget
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout

    def plan(self, observation: Observation, allowed_actions: tuple[Action, ...],
             wait_seconds: float) -> Decision:
        if not isinstance(observation, Observation):
            raise PlannerUnavailable(Reason.REMOTE_DISABLED)
        if observation.blocker not in _RECOVERABLE:
            return Decision(Action.HANDOFF, Reason.BOUNDARY)
        if not self.approved or not isinstance(self.api_key, str) or not self.api_key.strip():
            raise PlannerUnavailable(Reason.REMOTE_DISABLED)
        # A reviewed endpoint must use HTTPS. Never follow redirects with the key.
        try:
            url = httpx.URL(self.endpoint)
            if url.scheme != "https" or not url.host or url.userinfo or url.fragment:
                raise ValueError
            if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 200:
                raise ValueError
            actions = tuple(dict.fromkeys(allowed_actions))
            if not actions or any(not isinstance(action, Action) for action in actions):
                raise ValueError
            _seconds(wait_seconds, "wait")
            if wait_seconds > 30:
                raise ValueError
        except (TypeError, ValueError, httpx.InvalidURL):
            raise PlannerUnavailable(Reason.REMOTE_DISABLED) from None
        tools = []
        for action in actions:
            parameters = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
            if action == Action.WAIT:
                parameters["properties"] = {"seconds": {"type": "number", "enum": [wait_seconds]}}
                parameters["required"] = ["seconds"]
            tools.append({"type": "function", "function": {
                "name": action.value, "description": _ACTION_REASONS[action].value,
                "strict": True, "parameters": parameters,
            }})
        params = {
            "model": self.model, "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(observation.structural(), sort_keys=True)},
            ], "tools": tools, "tool_choice": "required", "parallel_tool_calls": False,
            "max_tokens": self.max_output_tokens, "stream": False,
            "provider": {"data_collection": "deny", "require_parameters": True,
                         "allow_fallbacks": False, "max_price": {
                             "prompt": float(self.budget.input_price_per_million),
                             "completion": float(self.budget.output_price_per_million),
                             "request": 0, "image": 0}},
        }
        input_bound = len(json.dumps(params, ensure_ascii=False).encode("utf-8")) + 1024
        if not self.budget.reserve(input_bound, self.max_output_tokens):
            raise PlannerUnavailable(Reason.REMOTE_BUDGET)
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
                response = client.post(self.endpoint, json=params,
                                       headers={"Authorization": f"Bearer {self.api_key}"})
                response.raise_for_status()
                data = response.json()
        except Exception:
            # Remote response bodies, URLs, headers and exceptions never leave here.
            raise PlannerUnavailable(Reason.REMOTE_ERROR) from None
        return self._parse(data, actions, wait_seconds)

    @staticmethod
    def _parse(data: object, allowed_actions: tuple[Action, ...], wait_seconds: float) -> Decision:
        try:
            if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or len(data["choices"]) != 1:
                raise ValueError
            choice = data["choices"][0]
            if choice["finish_reason"] != "tool_calls":
                raise ValueError
            message = choice["message"]
            if message.get("role") != "assistant" or message.get("content") not in (None, "") or message.get("refusal"):
                raise ValueError
            if message.get("function_call") is not None:
                raise ValueError
            calls = message["tool_calls"]
            if not isinstance(calls, list) or len(calls) != 1:
                raise ValueError
            call = calls[0]
            if (call["type"] != "function" or not isinstance(call.get("id"), str)
                    or not 1 <= len(call["id"]) <= 200
                    or set(call) != {"id", "type", "function"}
                    or set(call["function"]) != {"name", "arguments"}):
                raise ValueError
            action = Action(call["function"]["name"])
            if action not in allowed_actions:
                raise ValueError
            raw = call["function"]["arguments"]
            if not isinstance(raw, str) or len(raw) > 256:
                raise ValueError
            # Duplicate keys are malformed, even if the last value would be safe.
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result
            args = json.loads(raw, object_pairs_hook=unique)
            if not isinstance(args, dict):
                raise ValueError
            if action == Action.WAIT:
                if set(args) != {"seconds"} or type(args["seconds"]) not in (int, float):
                    raise ValueError
                if not math.isfinite(args["seconds"]) or args["seconds"] != wait_seconds:
                    raise ValueError
            elif args:
                raise ValueError
            return Decision(action, _ACTION_REASONS[action], wait_seconds if action == Action.WAIT else 0)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError):
            raise PlannerUnavailable(Reason.REMOTE_MALFORMED) from None


class RecoveryAgent:
    """Synchronous decision state inside one caller-owned preparation session.

    Every decide call consumes a step, including diagnostic and model failures.
    Actual count improvements reset only the consecutive no-progress counter;
    deadlines, cycle history, per-action allowances and total steps never reset.
    Record only typed execution receipts; a failed tool cannot grant authority.
    """

    def __init__(self, policy: RecoveryPolicy | None = None, planner: Planner | None = None,
                 *, clock: Callable[[], float] = time.monotonic):
        self.policy = policy or RecoveryPolicy()
        self.planner = planner or LocalPlanner()
        self._local = LocalPlanner()
        self._clock = clock
        self._started: float | None = None
        self._steps = 0
        self._no_progress = 0
        self._visits: Counter[str] = Counter()
        self._previous_signature: str | None = None
        self._actions: Counter[Action] = Counter()
        self._best_required: int | None = None
        self._best_invalid: int | None = None
        self._last: Decision | None = None
        self._last_recorded = False
        self._terminal: Decision | None = None
        self._blocker = Blocker.NONE
        self._history: list[Attempt] = []
        self._ineffective: set[tuple[str, Action]] = set()
        self._action_signature: str | None = None

    def _stop(self, reason: Reason) -> Decision:
        self._terminal = Decision(Action.HANDOFF, reason)
        self._last = self._terminal
        self._last_recorded = False
        return self._terminal

    def _remaining(self) -> float:
        if self._started is None:
            return self.policy.max_seconds
        return max(0, self.policy.max_seconds - max(0, self._clock() - self._started))

    def decide(self, observation: Observation) -> Decision:
        if not isinstance(observation, Observation):
            raise ValueError("Recovery requires a structural observation")
        if self._terminal is not None:
            return self._terminal
        if self._started is None:
            self._started = self._clock()
        self._blocker = observation.blocker
        if observation.blocker not in _RECOVERABLE:
            return self._stop(Reason.BOUNDARY)
        if self._remaining() <= 0:
            return self._stop(Reason.DEADLINE)
        if self._steps >= self.policy.max_steps:
            return self._stop(Reason.STEP_LIMIT)
        self._steps += 1
        # Consecutive unchanged observations are stationarity, not a cycle.
        # Retain run-entry history so A-A-B-B-A still detects a real return.
        if observation.signature != self._previous_signature:
            self._visits[observation.signature] += 1
            self._previous_signature = observation.signature
            if self._visits[observation.signature] > self.policy.max_cycles:
                return self._stop(Reason.CYCLE)
        if self._best_required is None:
            self._best_required = observation.required_count
            self._best_invalid = observation.invalid_count
        elif (observation.required_count < self._best_required
              or observation.invalid_count < self._best_invalid):
            self._best_required = min(self._best_required, observation.required_count)
            self._best_invalid = min(self._best_invalid, observation.invalid_count)
            self._no_progress = 0
        else:
            self._no_progress += 1
        if self._no_progress >= self.policy.max_no_progress:
            return self._stop(Reason.NO_PROGRESS)
        allowed = []
        last_action = self._last.action if self._last else None
        # Each wait already produces a fresh observation. Let bounded loading
        # waits finish before spending another diagnostic allowance.
        if (self._actions[Action.INSPECT] < self.policy.max_inspects
                and last_action != Action.INSPECT
                and not (observation.busy and last_action == Action.WAIT)):
            allowed.append(Action.INSPECT)
        wait_seconds = min(self.policy.wait_seconds, self._remaining())
        if observation.busy and wait_seconds > 0 and self._actions[Action.WAIT] < self.policy.max_waits:
            allowed.append(Action.WAIT)
        if not observation.busy and observation.can_focus and self._actions[Action.FOCUS] < self.policy.max_focuses:
            allowed.append(Action.FOCUS)
        if (not observation.busy and not observation.can_focus and observation.can_recommit
                and self._actions[Action.RECOMMIT] < self.policy.max_recommits):
            allowed.append(Action.RECOMMIT)
        if (not observation.busy and not observation.can_focus and observation.can_retry
                and self._actions[Action.RETRY] < self.policy.max_retries):
            allowed.append(Action.RETRY)
        if observation.can_reload and self._actions[Action.RELOAD] < self.policy.max_reloads:
            allowed.append(Action.RELOAD)
        allowed = [action for action in allowed
                   if (observation.signature, action) not in self._ineffective]
        observation = replace(observation, attempts=tuple(self._history[-64:]))
        allowed.append(Action.HANDOFF)
        try:
            decision = self.planner.plan(observation, tuple(allowed), wait_seconds)
        except PlannerUnavailable as exc:
            if exc.reason == Reason.REMOTE_MALFORMED:
                return self._stop(Reason.REMOTE_MALFORMED)
            fallback = self._local.plan(observation, tuple(allowed), wait_seconds)
            decision = Decision(fallback.action, exc.reason, fallback.wait_seconds)
        except Exception:
            return self._stop(Reason.REMOTE_ERROR)
        if self._remaining() <= 0:
            return self._stop(Reason.DEADLINE)
        if (not isinstance(decision, Decision) or decision.action not in allowed
                or (decision.action == Action.WAIT and decision.wait_seconds > wait_seconds)):
            return self._stop(Reason.ACTION_DENIED)
        if decision.action == Action.HANDOFF:
            return self._stop(decision.reason)
        if decision.action == Action.WAIT:
            decision = Decision(Action.WAIT, decision.reason,
                                min(decision.wait_seconds, self._remaining()))
        self._actions[decision.action] += 1
        self._last = decision
        self._last_recorded = False
        self._action_signature = observation.signature
        return decision

    def event(self, decision: Decision, result: Result = Result.PLANNED) -> dict:
        """JSONL-safe fixed schema; no signatures, text, raw errors or model prose."""
        if not isinstance(decision, Decision) or not isinstance(result, Result):
            raise ValueError("Invalid recovery event category")
        return {
            "version": 1, "stage": "recovery", "cause": self._blocker.value,
            "reason": decision.reason.value, "action": decision.action.value,
            "result": result.value, "counters": {
                "steps": self._steps, "no_progress": self._no_progress,
                "max_visits": max(self._visits.values(), default=0),
                "inspect": self._actions[Action.INSPECT], "wait": self._actions[Action.WAIT],
                "retry": self._actions[Action.RETRY], "reload": self._actions[Action.RELOAD],
                "focus": self._actions[Action.FOCUS], "recommit": self._actions[Action.RECOMMIT],
            },
        }

    def record_result(self, decision: Decision, result: Result) -> dict:
        if decision is not self._last or self._last_recorded or result == Result.PLANNED:
            raise ValueError("Invalid or stale recovery receipt")
        event = self.event(decision, result)
        self._last_recorded = True
        self._history.append(Attempt(decision.action, result))
        if (result in (Result.FAILED, Result.NO_EFFECT) and self._action_signature is not None
                and decision.action in (Action.FOCUS, Action.RECOMMIT, Action.RETRY, Action.RELOAD)):
            self._ineffective.add((self._action_signature, decision.action))
        if result in (Result.DENIED, Result.CANCELLED):
            if result == Result.CANCELLED:
                self._blocker = Blocker.CANCELLED
            self._stop(Reason.ACTION_DENIED if result == Result.DENIED else Reason.BOUNDARY)
        return event
