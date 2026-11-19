"""Rika's morning brief and alerts (/api/rika/brief, /api/rika/alerts).

The goal routes (/api/goals...) are answered by rika-server (server/src/goals);
:mod:`kitty.core.goals` stays for Rika's tools, the brief and the demo.

GET  /api/rika/brief  ->  {brief: Brief|null}  (today's, in the calendar's zone)
POST /api/rika/brief/seen {}  ->  {brief};  POST /api/rika/brief/run {}  ->  {brief}  (sweep now)
GET  /api/rika/alerts  ->  {alerts: [{id, key, title, body, due_at, goal_id, view, created_at}]}

Shapes are in :mod:`kitty.core.rika.brief`. POSTs already
run under ``app.mutation_lock``; the brief's scheduler takes it around its own writes.
"""
from __future__ import annotations

from kitty.core.goals import GoalError
from kitty.core.rika import brief


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def _keys(body: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    if set(body) - allowed or required - set(body):
        raise _error(400, "Unexpected or missing goal request fields")


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except GoalError as exc:
        raise _error(exc.status, str(exc)) from None


def start(app) -> None:
    app.brief_scheduler = brief.Scheduler(app)
    app.brief_scheduler.start()


def stop(app) -> None:
    scheduler = getattr(app, "brief_scheduler", None)
    if scheduler is not None:
        scheduler.stop()
        app.brief_scheduler = None


def get(app, conn, path, query):
    if path == "/api/rika/brief":
        app._no_query(query)
        return {"brief": brief.get(conn)}
    if path == "/api/rika/alerts":
        app._no_query(query)
        return {"alerts": brief.alerts(conn)}
    return None


def post(app, conn, path, body):
    if path == "/api/rika/brief/seen":
        _keys(body, set())
        return {"brief": _call(brief.mark_seen, conn)}
    if path == "/api/rika/brief/run":
        _keys(body, set())
        return {"brief": brief.sweep(app, conn)}
    return None
