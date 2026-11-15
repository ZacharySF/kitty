"""The worker side of the front's pending-approvals route (server/src/effects/).

rika-server answers ``/api/effects`` itself (list, approve, execute, deny) over the effect
tables, with the kernel protocol of kitty.core.effects. Two parts need Python, and the front
asks for them on these internal routes (pages never reach them: the front refuses every page
request under ``/api/internal/effects``)::

    POST /api/internal/effects/preconditions  {id} -> {preconditions}
    POST /api/internal/effects/execute        {id, digest} -> {id, result}

``preconditions`` reads a pending-approvals effect's current precondition values (for
``submit_application``: the job and the resume ``choose_resume`` picks with the workspace
config) while the front holds its claim transaction; it only reads, so SQLite's WAL lets it
run beside the front's write lock. ``execute`` is the whole of `effects.execute` for the
``prompt`` kinds as the person at this window session (`web_auth.window_user`, the front's
session): the final re-check, the ``executed`` event and the executor (``delete_block``
writes through the block store as the proposing actor) in one immediate transaction. An
`effects.EffectError` answers its status and message, with the changed preconditions as
``problems``.
"""
from __future__ import annotations

from kitty import web_auth
from kitty.core import effect_kinds, effects

PREFIX = "/api/internal/effects"


def _error(exc: effects.EffectError):
    from kitty.web_server import APIError

    return APIError(exc.status, str(exc), problems=[{"precondition": name} for name in exc.changed])


def _keys(body: dict, allowed: set[str]) -> None:
    from kitty.web_server import APIError

    if set(body) - allowed:
        raise APIError(400, "Unknown effect request field")


def get(app, conn, path: str, query: dict) -> dict | None:
    return None


def post(app, conn, path: str, body: dict) -> dict | None:
    if path not in {PREFIX + "/preconditions", PREFIX + "/execute"}:
        return None
    ctx = effect_kinds.Context(conn, app.config)
    kinds = effects.REGISTRY.reviewed_in("prompt")
    try:
        if path.endswith("/preconditions"):
            _keys(body, {"id"})
            effect = effects.get(conn, body.get("id"))
            if effect.kind not in kinds:
                raise effects.Forbidden("Review this effect in its own view")
            kind = effects.REGISTRY.get(effect.kind)
            return {"preconditions": dict(kind.preconditions(ctx, effect.payload))}
        _keys(body, {"id", "digest"})
        effect, result = effects.execute(conn, body.get("id"), body.get("digest"), kinds=kinds,
                                         actor=web_auth.window_user(app), ctx=ctx)
        return {"id": effect.id, "result": result}
    except effects.EffectError as exc:
        raise _error(exc) from None
