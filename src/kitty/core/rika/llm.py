"""OpenRouter tool-calling for Rika, streamed, under rika!'s shared AI controls.

Reused from Settings → AI (:mod:`kitty.web_ai`): the private key, the model catalog
and its prices, the routing preference, the output limit and the daily budget. Like
:mod:`kitty.core.triage.openrouter`, each request reserves a conservative maximum
first (one token per UTF-8 byte of the whole request plus framing, and the full
output limit, at the catalog prices), committed in the shared ``ai_reviews`` ledger
before the network call, and is sent with those prices as the provider ceiling and
no fallbacks or retries. A request that would exceed today's remaining budget only
runs after the user approves it (the loop asks). Demo mode never gets here.

Prompt caching: the loop sends the system prompt as two text parts, the stable
instructions and preferences first and the volatile date line second (:func:`system`).
Anthropic and Gemini models need an explicit breakpoint, so the stable part carries
``cache_control: {type: 'ephemeral'}`` (OpenRouter's prompt-caching format); other
providers cache repeated prefixes on their own and get the parts joined as one string.
The reply reports prompt, cached and completion tokens from OpenRouter's ``usage``.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from kitty.db.connection import transaction

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
PURPOSE = "rika"
FRAMING = 1024
TIMEOUT = 120
#: Model prefixes whose providers cache only at explicit ``cache_control`` breakpoints.
EXPLICIT_CACHE = ("anthropic/", "google/gemini")
#: Tests swap in an ``httpx.MockTransport``; None is the real network.
transport: httpx.BaseTransport | None = None


class ModelUnavailable(RuntimeError):
    """No request was sent; the message says what to fix in Settings → AI."""


class ModelFailed(RuntimeError):
    """A request was sent and failed; it may have been billed and is never retried."""


class OverBudget(RuntimeError):
    """The request's maximum cost does not fit today's remaining AI budget."""


@dataclass
class Call:
    model: str
    body: dict
    key: str = field(repr=False)
    maximum_cost: Decimal
    fingerprint: str


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    cost: Decimal | None = None
    tokens_in: int | None = None
    tokens_cached: int | None = None
    tokens_out: int | None = None


def _web_ai():
    from kitty import web_ai
    return web_ai


def prepare(app, conn: sqlite3.Connection, model_id: str, messages: list[dict], tools: list[dict]) -> Call:
    """A bounded request for `model_id`, or ModelUnavailable before anything is sent."""
    from kitty.web_server import APIError

    web_ai = _web_ai()
    if app.settings.demo:
        raise ModelUnavailable("The demo never calls OpenRouter")
    try:
        key = web_ai._key(app)
    except APIError as exc:
        raise ModelUnavailable(str(exc)) from None
    if not key:
        raise ModelUnavailable("Add your OpenRouter API key in Settings → AI so Rika can think beyond simple commands")
    settings = web_ai._settings(conn)
    model = conn.execute("SELECT * FROM ai_models WHERE id=?", (model_id,)).fetchone()
    if model is None:
        raise ModelUnavailable(f"{model_id} is not in the model list; refresh it or choose Rika's tier models in Settings → AI")
    if web_ai._now() - datetime.fromisoformat(model["fetched_at"]) > timedelta(hours=24):
        raise ModelUnavailable("Model prices expired; refresh the model list in Settings → AI")
    prompt, completion = Decimal(model["prompt_price"]), Decimal(model["completion_price"])
    body = {
        "model": model_id, "messages": _cacheable(model_id, messages), "max_tokens": settings["max_output_tokens"],
        "stream": True,
        "provider": {"sort": settings["routing"], "data_collection": "deny", "require_parameters": True,
                     "allow_fallbacks": False,
                     "max_price": {"prompt": float(prompt * 1_000_000), "completion": float(completion * 1_000_000),
                                   "request": 0, "image": 0}},
        "usage": {"include": True},
    }
    if tools:
        body.update(tools=tools, tool_choice="auto")
    input_bound = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + FRAMING
    if input_bound + settings["max_output_tokens"] > model["context_length"]:
        raise ModelUnavailable("This conversation is too long for the tier's model; start a new one or choose a larger model")
    cost = Decimal(input_bound) * prompt + Decimal(settings["max_output_tokens"]) * completion
    return Call(model_id, body, key, cost, web_ai._fingerprint(app, settings))


def fits_budget(conn: sqlite3.Connection, call: Call) -> bool:
    web_ai = _web_ai()
    return web_ai._spent(conn) + call.maximum_cost <= Decimal(web_ai._settings(conn)["daily_budget_usd"])


def reserve(conn: sqlite3.Connection, call: Call, *, approved_over_budget: bool = False) -> str:
    """Commit the maximum charge to the shared ledger before the request is sent."""
    web_ai = _web_ai()
    identifier = secrets.token_hex(16)
    stamp = web_ai._now()
    with transaction(conn, immediate=True):
        if not approved_over_budget and not fits_budget(conn, call):
            raise OverBudget("This request exceeds today's remaining AI budget")
        conn.execute("INSERT INTO ai_reviews(id,purpose,plan_json,fingerprint,expires_at,created_at,state,reserved_usd)"
                     " VALUES(?,?,?,?,?,?,'running',?)",
                     (identifier, PURPOSE, json.dumps({"model": call.model, "maximum_cost_usd": str(call.maximum_cost)}),
                      call.fingerprint, (stamp + timedelta(minutes=10)).isoformat(), stamp.isoformat(),
                      str(call.maximum_cost)))
    return identifier


def settle(conn: sqlite3.Connection, reservation: str, reply: Reply | None) -> None:
    """Record the reported cost; a failed or unreported request keeps its full reservation."""
    web_ai = _web_ai()
    with transaction(conn):
        if reply is None:
            conn.execute("UPDATE ai_reviews SET state='failed',completed_at=? WHERE id=?", (web_ai._now().isoformat(), reservation))
        else:
            conn.execute("UPDATE ai_reviews SET state='completed',actual_usd=?,completed_at=? WHERE id=?",
                         (str(reply.cost) if reply.cost is not None else None, web_ai._now().isoformat(), reservation))


def _cost(usage) -> Decimal | None:
    raw = usage.get("cost") if isinstance(usage, dict) else None
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and value >= 0 else None


def system(stable: str, volatile: str) -> dict:
    """A system message whose first part stays the same between requests (cacheable)."""
    return {"role": "system", "content": [{"type": "text", "text": stable}, {"type": "text", "text": volatile}]}


def _cacheable(model_id: str, messages: list[dict]) -> list[dict]:
    """The messages as `model_id`'s provider caches them: a breakpoint after the stable
    system part where caching is explicit, else that system message as one string."""
    explicit = model_id.startswith(EXPLICIT_CACHE)
    shaped = []
    for message in messages:
        parts = message.get("content")
        if message.get("role") == "system" and isinstance(parts, list) and parts:
            if explicit:
                message = {**message, "content": [{**parts[0], "cache_control": {"type": "ephemeral"}}, *parts[1:]]}
            else:
                message = {**message, "content": "\n\n".join(part["text"] for part in parts)}
        shaped.append(message)
    return shaped


def _count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _tokens(reply: Reply, usage) -> None:
    if not isinstance(usage, dict):
        return
    details = usage.get("prompt_tokens_details")
    reply.tokens_in = _count(usage.get("prompt_tokens"))
    reply.tokens_cached = _count(details.get("cached_tokens")) if isinstance(details, dict) else None
    reply.tokens_out = _count(usage.get("completion_tokens"))


def stream(call: Call, on_token: Callable[[str], None]) -> Reply:
    """Send one request and stream its text; tool calls are assembled from their deltas."""
    reply, calls = Reply(), {}
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=transport) as client:
            with client.stream("POST", BASE_URL, json=call.body, headers={
                    "Authorization": "Bearer " + call.key, "X-OpenRouter-Title": "rika!"}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue  # SSE comments keep the connection alive
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if not isinstance(chunk, dict) or chunk.get("error"):
                        raise ModelFailed("OpenRouter declined the request; it may have been billed and was not retried")
                    if "usage" in chunk:
                        reply.cost = _cost(chunk["usage"])
                        _tokens(reply, chunk["usage"])
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if isinstance(delta.get("content"), str) and delta["content"]:
                            reply.text += delta["content"]
                            on_token(delta["content"])
                        for part in delta.get("tool_calls") or []:
                            slot = calls.setdefault(part.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            function = part.get("function") or {}
                            slot["id"] = part.get("id") or slot["id"]
                            slot["name"] += function.get("name") or ""
                            slot["arguments"] += function.get("arguments") or ""
                        if choice.get("finish_reason"):
                            reply.finish_reason = choice["finish_reason"]
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise ModelFailed("The model request failed or timed out; it may have been billed and was not retried") from None
    reply.tool_calls = [{**slot, "id": slot["id"] or f"call_{index}"} for index, slot in sorted(calls.items())]
    return reply
