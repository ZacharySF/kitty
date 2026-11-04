"""Goals (/api/goals) and Rika's morning brief and alerts (/api/rika/brief, /api/rika/alerts).

GET  /api/goals  ->  {goals: [Goal]}  (active first, then by target date)
GET  /api/goals/<id>  ->  {goal}  (with links and suggestions)
GET  /api/goals/linkable?q=<text>[&kind=<kind>]  ->  {items: [{kind, ref, title, detail}]}
POST /api/goals {title, why?, target_date?, measures?}  ->  {goal}
POST /api/goals/<id> {revision, title?, why?, target_date?, status?, measures?}  ->  {goal}  (409 stale)
POST /api/goals/<id>/delete {revision}  ->  {deleted: id}
POST /api/goals/<id>/links {kind, ref}  ->  {goal};  POST /api/goals/<id>/unlink {kind, ref}  ->  {goal}
POST /api/goals/<id>/measures/<measure_id> {current}  ->  {goal}  (409 for an auto measure)
POST /api/goals/matches/<match_id> {decision: link|dismiss}  ->  {goal}
GET  /api/rika/brief  ->  {brief: Brief|null}  (today's, in the calendar's zone)
POST /api/rika/brief/seen {}  ->  {brief};  POST /api/rika/brief/run {}  ->  {brief}  (sweep now)
GET  /api/rika/alerts  ->  {alerts: [{id, key, title, body, due_at, goal_id, view, created_at}]}

Shapes are in :mod:`kitty.core.goals` and :mod:`kitty.core.rika.brief`. POSTs already
run under ``app.mutation_lock``; the brief's scheduler takes it around its own writes.
"""
from __future__ import annotations

import re

from kitty.core import goals
from kitty.core.goals import GoalError
from kitty.core.rika import brief

GOALS = "/api/goals"
_ID = r"([1-9][0-9]{0,17})"


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
    if path == GOALS:
        app._no_query(query)
        return {"goals": goals.listing(conn)}
    if path == GOALS + "/linkable":
        if set(query) - {"q", "kind"}:
            raise _error(400, "Search accepts only q and kind")
        return {"items": _call(goals.linkable, conn, query.get("q", ""), query.get("kind"), app.modules)}
    match = re.fullmatch(GOALS + "/" + _ID, path)
    if match:
        app._no_query(query)
        return {"goal": _call(goals.get, conn, int(match[1]))}
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
    if path != GOALS and not path.startswith(GOALS + "/"):
        return None
    if path == GOALS:
        return {"goal": _call(goals.create, conn, body)}
    match = re.fullmatch(GOALS + "/matches/" + _ID, path)
    if match:
        _keys(body, {"decision"}, {"decision"})
        return {"goal": _call(goals.decide_match, conn, int(match[1]), body["decision"])}
    match = re.fullmatch(GOALS + "/" + _ID + r"(?:/(delete|links|unlink)|/measures/([A-Za-z0-9_-]{1,40}))?", path)
    if match is None:
        return None
    goal_id, action, measure = int(match[1]), match[2], match[3]
    if measure is not None:
        _keys(body, {"current"}, {"current"})
        return {"goal": _call(goals.set_measure, conn, goal_id, measure, body["current"])}
    if action == "delete":
        _keys(body, {"revision"}, {"revision"})
        return {"deleted": _call(goals.delete, conn, goal_id, body["revision"])}
    if action == "links":
        _keys(body, {"kind", "ref"}, {"kind", "ref"})
        return {"goal": _call(goals.link, conn, goal_id, body["kind"], body["ref"], modules=app.modules)}
    if action == "unlink":
        _keys(body, {"kind", "ref"}, {"kind", "ref"})
        return {"goal": _call(goals.unlink, conn, goal_id, body["kind"], body["ref"])}
    _keys(body, {"revision", "title", "why", "target_date", "status", "measures"}, {"revision"})
    return {"goal": _call(goals.update, conn, goal_id, body)}
