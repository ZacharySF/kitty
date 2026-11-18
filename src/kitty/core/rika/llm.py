"""Tool-calling for Rika, under rika!'s shared AI controls, on the brain chosen for this
computer (:mod:`kitty.core.rika.brain`): OpenRouter, streamed, or omp's own sign-in.

OpenRouter.

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

omp. The same Chat Completions request, without OpenRouter's ``provider`` and ``usage``
fields, goes through ``omp auth-gateway stdio`` (:mod:`kitty.core.rika.omp`) on the tier's
omp model, billed by whatever omp is signed in with (a Claude subscription, for
example). No OpenRouter key, catalog or price is involved, so the request reserves
nothing and is outside the daily budget; it is still recorded in ``ai_reviews`` before
it is sent (``provider: "omp"`` in its plan), and its tokens are reported. The gateway
answers whole, so the text arrives as one token. The demo never gets here either.
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

from kitty.core.rika import brain, omp, router
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
    provider: str = "openrouter"


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


def model_for(app, conn: sqlite3.Connection, tier: str) -> str:
    """The model `tier` runs on with this computer's brain."""
    chosen = brain.load(app.settings)
    return brain.models(chosen)[tier] if chosen.provider == "omp" else router.models(conn)[tier]


def prepare(app, conn: sqlite3.Connection, model_id: str, messages: list[dict], tools: list[dict]) -> Call:
    """A bounded request for `model_id`, or ModelUnavailable before anything is sent."""
    from kitty.web_server import APIError

    web_ai = _web_ai()
    if app.settings.demo:
        raise ModelUnavailable("The demo never calls a model")
    if brain.load(app.settings).provider == "omp":
        return _prepare_omp(conn, model_id, messages, tools)
    try:
        key = web_ai._key(app)
    except APIError as exc:
        raise ModelUnavailable(str(exc)) from None
    if not key:
        raise ModelUnavailable("Add your OpenRouter API key in Settings → AI, or choose omp there as Rika's brain, "
                               "so Rika can think beyond simple commands")
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
                     # Text only, so no image cap (it would refuse image-capable providers); see web_ai._ceilings.
                     "max_price": {"prompt": float(prompt * 1_000_000), "completion": float(completion * 1_000_000),
                                   "request": 0}},
        "usage": {"include": True},
    }
    if tools:
        body.update(tools=tools, tool_choice="auto")
    input_bound = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + FRAMING
    if input_bound + settings["max_output_tokens"] > model["context_length"]:
        raise ModelUnavailable("This conversation is too long for the tier's model; start a new one or choose a larger model")
    cost = Decimal(input_bound) * prompt + Decimal(settings["max_output_tokens"]) * completion
    return Call(model_id, body, key, cost, web_ai._fingerprint(app, settings))


def _prepare_omp(conn: sqlite3.Connection, model_id: str, messages: list[dict], tools: list[dict]) -> Call:
    if omp.program() is None:
        raise ModelUnavailable("omp is not installed (not found on PATH); install it, or choose OpenRouter as "
                               "Rika's brain in Settings → AI")
    web_ai = _web_ai()
    settings = web_ai._settings(conn)
    body = {"model": model_id, "messages": messages, "max_tokens": settings["max_output_tokens"], "stream": False}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    return Call(model_id, body, "", Decimal(0), web_ai._binding(settings, "omp"), provider="omp")


def fits_budget(conn: sqlite3.Connection, call: Call) -> bool:
    """omp requests are billed by omp's sign-in, not counted against the daily budget."""
    web_ai = _web_ai()
    return call.provider == "omp" or web_ai._spent(conn) + call.maximum_cost <= Decimal(web_ai._settings(conn)["daily_budget_usd"])


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
                     (identifier, PURPOSE, json.dumps({"model": call.model, "provider": call.provider,
                                                       "maximum_cost_usd": str(call.maximum_cost)}),
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
    if call.provider == "omp":
        return _ask_omp(call, on_token)
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


def _ask_omp(call: Call, on_token: Callable[[str], None]) -> Reply:
    """The gateway's whole answer as a Reply; its text is passed on as one token."""
    try:
        answer = omp.complete(call.body)
        [choice] = answer["choices"][:1]
        message = choice["message"]
        reply = Reply(text=message.get("content") or "", finish_reason=choice.get("finish_reason"))
        for index, item in enumerate(message.get("tool_calls") or []):
            function = item["function"]
            reply.tool_calls.append({"id": item.get("id") or f"call_{index}", "name": function["name"],
                                     "arguments": function.get("arguments") or ""})
        if not isinstance(reply.text, str):
            raise TypeError
    except omp.OmpMissing as exc:
        raise ModelFailed(str(exc)) from None
    except omp.OmpFailed as exc:
        raise ModelFailed(f"omp could not answer: {exc}. It was not retried.") from None
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        raise ModelFailed("omp's answer was not a chat completion; it was not retried") from None
    _tokens(reply, answer.get("usage"))
    if reply.text:
        on_token(reply.text)
    return reply
