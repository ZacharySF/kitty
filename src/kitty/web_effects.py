"""The pending-approvals list (kitty.core.effects): rika!'s one review UI for proposed effects.

Agents and paired programs propose (MCP ``propose_delete_block`` /
``propose_submit_application``, ``kitty effects propose ...``); only these page routes,
behind the per-launch UI secret (kitty.web_auth), approve, execute or deny, as the person
at this rika! window session (`user`)::

    GET  /api/effects                 {pending: [effect], recent: [effect]}
    POST /api/effects/approve         {id, digest} -> {effect, notice?}
    POST /api/effects/execute         {id, digest} -> {effect, result}; 400 for a kind with no executor
    POST /api/effects/deny            {id} -> {effect}

Approving re-checks every precondition; executing re-checks them again and recomputes
the digest, so anything that changed in between refuses the effect (409, with the
changed names under ``changed``). Approving ``submit_application`` only records your
decision: it has no executor, and executing it is refused as a type error.
Kinds reviewed in their own view (AI requests, Google pushes, application preparation)
are not listed here, and the kernel refuses them here: these routes own only the
``prompt`` kinds (`effects.Registry.reviewed_in`).
"""
from __future__ import annotations

from kitty.core import effect_kinds, effects
from kitty.core.blocks.actor import Actor

PREFIX = "/api/effects"


def _error(exc: effects.EffectError):
    from kitty.web_server import APIError

    problems = [{"precondition": name} for name in exc.changed]
    return APIError(exc.status, str(exc), problems=problems)


def user(app) -> Actor:
    """The person at this rika! window session (every /api request passed the UI secret)."""
    return Actor("user", None, app.ui_session)


def _keys(body: dict, allowed: set[str]) -> None:
    from kitty.web_server import APIError

    if not isinstance(body, dict) or set(body) - allowed:
        raise APIError(400, "Unknown effect request field")


def view(conn, effect: effects.Effect) -> dict:
    kind = effects.REGISTRY.get(effect.kind)
    return {**effect.as_dict(), "executable": isinstance(kind, effects.Executable),
            "handoff": getattr(kind, "handoff", None), "events": effects.events(conn, effect.id)}


def listing(conn) -> dict:
    prompt = effects.REGISTRY.reviewed_in("prompt")
    return {"pending": [view(conn, effect) for effect in effects.pending(conn)],
            "recent": [view(conn, effect) for effect in effects.recent(conn) if effect.kind in prompt]}


def get(app, conn, path: str, query: dict) -> dict | None:
    if path != PREFIX:
        return None
    app._no_query(query)
    return listing(conn)


def post(app, conn, path: str, body: dict) -> dict | None:
    if path not in {PREFIX + "/approve", PREFIX + "/execute", PREFIX + "/deny"}:
        return None
    ctx = effect_kinds.Context(conn, app.config)
    kinds = effects.REGISTRY.reviewed_in("prompt")
    try:
        if path.endswith("/deny"):
            _keys(body, {"id"})
            effect = effects.deny(conn, body.get("id"), kinds=kinds, actor=user(app))
            return {"effect": view(conn, effect), **listing(conn)}
        _keys(body, {"id", "digest"})
        if path.endswith("/approve"):
            effect = effects.approve(conn, body.get("id"), body.get("digest"), kinds=kinds, actor=user(app), ctx=ctx)
            kind = effects.REGISTRY.get(effect.kind)
            return {"effect": view(conn, effect), "notice": getattr(kind, "handoff", None), **listing(conn)}
        effect, result = effects.execute(conn, body.get("id"), body.get("digest"), kinds=kinds, actor=user(app), ctx=ctx)
        return {"effect": view(conn, effect), "result": result, **listing(conn)}
    except effects.EffectError as exc:
        raise _error(exc) from None
