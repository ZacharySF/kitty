"""Optional synchronous OpenRouter triage, with routing ceilings and no retries.

Dynamic routers do not have a useful fixed catalog price. Reserve a UTF-8 byte
bound for the entire request plus framing, and the full output limit, at the
configured maximum unit prices. Never recycle a reservation within a run, even
when the actual charge is lower. Unknown charges remain reserved in run stats.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import ExitStack
from decimal import Decimal, InvalidOperation

import httpx

from kitty.config import Config
from kitty.core import jobs as jobs_core
from kitty.core import keys
from kitty.core import sources as sources_core
from kitty.core.triage.cost import Estimate, Usage
from kitty.core.triage.prompt import AnswerError
from kitty.core.triage.run import (
    KIND,
    RunResult,
    TriageError,
    TriagePlan,
    _apply_cached,
    _apply_rules,
    _cache,
    _cached_answer,
    _use_answer,
)
from kitty.dates import now_iso
from kitty.db.connection import transaction
from kitty.settings import Settings

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
_FRAMING_TOKENS = 1024


def _key(settings: Settings) -> str:
    """Read only the inference key; never include it or remote bodies in errors."""
    if settings.demo:
        raise TriageError("OpenRouter paid triage is unavailable in demo mode")
    try:
        key = keys.read(settings, "OPENROUTER")
    except keys.KeysError as exc:
        raise TriageError(str(exc)) from None
    if not key:
        raise TriageError("Set the OpenRouter key: kitty keys set OPENROUTER")
    return key


def request_params(config: Config, plan: TriagePlan, user: str) -> dict:
    """Text only: no tools, uploads, credentials, screening, or provider fallback."""
    return {
        "model": config.triage.model,
        "messages": [{"role": "system", "content": plan.system}, {"role": "user", "content": user}],
        "max_tokens": config.triage.max_output_tokens,
        "stream": False,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "job_triage", "strict": True, "schema": plan.schema,
        }},
        "provider": {
            "data_collection": "deny", "require_parameters": True, "allow_fallbacks": False,
            "max_price": {"prompt": config.triage.openrouter_max_input_price,
                          "completion": config.triage.openrouter_max_output_price,
                          "request": 0, "image": 0},
        },
        "usage": {"include": True},
    }


def _bound(params: dict, config: Config) -> tuple[Usage, Decimal]:
    # Counting the serialized schema, roles and controls as well as text is
    # deliberately conservative: one token per UTF-8 byte, plus model framing.
    usage = Usage(input_tokens=len(json.dumps(params, ensure_ascii=False).encode("utf-8")) + _FRAMING_TOKENS,
                  output_tokens=config.triage.max_output_tokens)
    cost = (Decimal(usage.input_tokens) * Decimal(str(config.triage.openrouter_max_input_price))
            + Decimal(usage.output_tokens) * Decimal(str(config.triage.openrouter_max_output_price))) / 1_000_000
    return usage, cost


def plan_estimate(plan: TriagePlan, config: Config) -> Estimate:
    """Maximum reserved dollars, not a nominal router/catalog price estimate."""
    total, cost = Usage(), Decimal(0)
    for job in plan.to_send:
        usage, reserved = _bound(request_params(config, plan, plan.users[job.id]), config)
        total.add(usage)
        cost += reserved
    return Estimate(len(plan.to_send), total.input_tokens, total.output_tokens, float(cost))


def _reported_cost(data: dict) -> Decimal | None:
    usage = data.get("usage")
    raw = usage.get("cost") if isinstance(usage, dict) else None
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value if value.is_finite() and value >= 0 else None


def _usage(data: dict) -> Usage:
    raw = data.get("usage")
    raw = raw if isinstance(raw, dict) else {}
    return Usage(**{field: raw.get(key) if type(raw.get(key)) is int and raw[key] >= 0 else 0
                    for field, key in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"))})


def _answer_text(data: dict) -> str:
    """Reject partial answers and malformed structure before semantic validation."""
    try:
        choice = data["choices"][0]
        if choice["finish_reason"] != "stop":
            raise AnswerError("OpenRouter returned an incomplete or refused answer")
        text = choice["message"]["content"]
        if not isinstance(text, str):
            raise ValueError
        answer = json.loads(text)
        if not isinstance(answer, dict) or not all(isinstance(answer.get(key), str) for key in
                ("tier", "tier_reason", "resume", "resume_reason")):
            raise ValueError
        notes = answer["edit_notes"]
        if not isinstance(notes, list) or not all(isinstance(note, dict) and all(
                isinstance(note.get(key), str) for key in ("jd_phrase", "resume_line", "note")) for note in notes):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise AnswerError("OpenRouter returned malformed triage JSON") from None
    return text


def _daily_reserved(conn: sqlite3.Connection, day: str) -> Decimal:
    """All committed reservations, including unfinished/failed requests.

    Call while holding BEGIN IMMEDIATE before authorizing another outbound call.
    The per-day ledger handles runs that cross UTC midnight. Older runs without
    a ledger retain their full reservation on their start day.
    """
    total = Decimal(0)
    for row in conn.execute("SELECT started_at, stats_json FROM runs WHERE kind = ? AND stats_json IS NOT NULL", (KIND,)):
        try:
            stats = json.loads(row["stats_json"])
            if not isinstance(stats, dict):
                raise ValueError
            if stats.get("provider") != "openrouter":
                continue
            ledger = stats.get("reserved_by_utc_day")
            if ledger is None:
                raw = 0
                if row["started_at"][:10] == day:
                    raw = max(Decimal(str(stats.get("reserved_usd", 0))),
                              Decimal(str(stats.get("cost_usd", 0))))
            elif isinstance(ledger, dict):
                raw = ledger.get(day, 0)
            else:
                raise ValueError
            amount = Decimal(str(raw))
            if not amount.is_finite() or amount < 0:
                raise ValueError
        except (json.JSONDecodeError, InvalidOperation, TypeError, ValueError):
            raise TriageError("Cannot verify the durable OpenRouter daily budget ledger") from None
        total += amount
    return total


def run_now(conn: sqlite3.Connection, config: Config, plan: TriagePlan, settings: Settings, *,
            progress: Callable[[str], None] | None = None) -> RunResult:
    """Tier/pick resumes only; reserve before each attempt, never retry a request.

    cost_usd is reported cost where known, otherwise the retained maximum charge.
    runs.stats_json also records reserved_usd and whether cost is an upper bound.
    The full reserved total, not cheaper actual charges, gates every next request.
    """
    if config.triage.provider != "openrouter":
        raise TriageError("OpenRouter triage requires provider: openrouter")
    say = progress or (lambda message: None)
    result = RunResult(mode="now")
    result.run_id = sources_core.start_run(conn, KIND)
    result.rules_set = _apply_rules(conn, plan)
    _apply_cached(conn, config, plan, result)
    cap, reserved, charged = Decimal(str(config.triage.max_cost_usd_per_run)), Decimal(0), Decimal(0)
    unknown_cost = False
    reserved_by_day: dict[str, Decimal] = {}
    daily_cap = config.triage.openrouter_daily_budget_usd
    daily_cap = Decimal(str(daily_cap)) if daily_cap is not None else None

    def stats() -> dict:
        result.cost_usd = float(charged)
        return result.stats() | {"provider": "openrouter", "reserved_usd": float(reserved),
                                 "reserved_by_utc_day": {day: str(cost) for day, cost in reserved_by_day.items()},
                                 "cost_is_upper_bound": unknown_cost}

    def save(*, finished: bool = False) -> None:
        with transaction(conn):
            if finished:
                sources_core.finish_run(conn, result.run_id, stats=stats())
            else:
                conn.execute("UPDATE runs SET stats_json = ? WHERE id = ?", (json.dumps(stats()), result.run_id))

    with ExitStack() as stack:
        client, key = None, None
        try:
            for index, original in enumerate(plan.to_send):
                job = jobs_core.get_job(conn, original.id)
                if job is None or job.tier_source == "manual":
                    continue
                # Replaying an already-executed plan must not repeat a paid call.
                cached = _cached_answer(conn, job.id, config.triage.model, plan.hashes[job.id])
                if cached is not None:
                    if job.tier_source != "llm":
                        with transaction(conn):
                            result.cached_applied += _use_answer(conn, config, plan, job, cached, result)
                    continue
                params = request_params(config, plan, plan.users[job.id])
                _, maximum = _bound(params, config)
                if reserved + maximum > cap:
                    result.cap_hit = True
                    result.skipped_for_cap = len(plan.to_send) - index
                    say(f"cost cap ${cap:.2f} reached; {result.skipped_for_cap} job(s) left for the next run")
                    break
                say(f"[{index + 1}/{len(plan.to_send)}] {job.company_name} - {job.title}")
                previous_unknown = unknown_cost
                # The read/compare/reserve is one writer transaction across all
                # processes, and is committed before the network call begins.
                with transaction(conn, immediate=True):
                    day = now_iso()[:10]
                    allowed = daily_cap is None or _daily_reserved(conn, day) + maximum <= daily_cap
                    if allowed:
                        if client is None:
                            key = _key(settings)
                            client = stack.enter_context(httpx.Client(timeout=90, follow_redirects=False))
                        reserved += maximum
                        charged += maximum
                        reserved_by_day[day] = reserved_by_day.get(day, Decimal(0)) + maximum
                        result.sent += 1
                        unknown_cost = True
                        conn.execute("UPDATE runs SET stats_json = ? WHERE id = ?",
                                     (json.dumps(stats()), result.run_id))
                if not allowed:
                    result.cap_hit = True
                    result.skipped_for_cap = len(plan.to_send) - index
                    say(f"daily cost cap ${daily_cap:.2f} reached; {result.skipped_for_cap} job(s) left")
                    break
                try:
                    response = client.post(BASE_URL, headers={"Authorization": "Bearer " + key,
                                           "X-OpenRouter-Title": "rika!"}, json=params)
                    response.raise_for_status()
                    data = response.json()
                except (httpx.HTTPError, ValueError):
                    result.errors.append(f"{job.id}: OpenRouter request failed; may have been billed; no retry")
                    save()
                    break
                if not isinstance(data, dict):
                    result.errors.append(f"{job.id}: OpenRouter returned malformed response JSON; charge remains reserved")
                    save()
                    continue
                actual = _reported_cost(data)
                if actual is not None:
                    charged += actual - maximum
                    unknown_cost = previous_unknown
                    if actual > maximum:
                        # A provider ceiling breach must also count against
                        # later runs, not just stop this run.
                        reserved_by_day[day] += actual - maximum
                else:
                    result.errors.append(f"{job.id}: OpenRouter omitted a valid cost; maximum charge remains reserved")
                if data.get("error"):
                    result.errors.append(f"{job.id}: OpenRouter declined the request; no retry")
                    save()
                    break
                if actual is not None and actual > maximum:
                    result.errors.append(f"{job.id}: OpenRouter reported cost above the reserved ceiling; stopping")
                    save()
                    break
                try:
                    answer = _answer_text(data)
                except AnswerError as exc:
                    result.errors.append(f"{job.id}: {exc}")
                    save()
                    continue
                with transaction(conn):
                    # Cache only checked answers, including their grounding checks.
                    applied = _use_answer(conn, config, plan, job, answer, result)
                    result.applied += applied
                    if applied:
                        _cache(conn, job.id, config.triage.model, plan.hashes[job.id], answer,
                               _usage(data), float(actual if actual is not None else maximum))
                save()
        finally:
            save(finished=True)
    return result
