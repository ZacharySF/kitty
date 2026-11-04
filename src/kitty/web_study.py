"""Loopback study API; local study actions also work in demo mode."""
from __future__ import annotations

from kitty.core import study


def _call(function, *args, **kwargs):
    from kitty.web_server import APIError
    try:
        return function(*args, **kwargs)
    except study.StudyConflict as exc:
        raise APIError(409, str(exc)) from None
    except ValueError as exc:
        raise APIError(400, str(exc)) from None


def get(app, conn, path, query):
    from kitty.web_server import APIError
    if path == '/api/study':
        if set(query) - {'timezone', 'period', 'date'}:
            raise APIError(400, 'Unknown study query parameter')
        return _call(study.workspace, conn, timezone_name=query.get('timezone'),
                     history_period=query.get('period', 'week'), history_date=query.get('date'))
    if path == '/api/study/timer':
        app._no_query(query)
        return _call(study.timer_state, conn)
    return None


def post(app, conn, path, body):
    if not path.startswith('/api/study/'):
        return None
    route = path.removeprefix('/api/study/')
    if route == 'timer':
        return _call(study.timer_command, conn, body)
    if route == 'guide':
        return _call(study.save_guide, conn, body)
    if route == 'returns':
        return _call(study.review_return, conn, body)
    if route == 'settings':
        return _call(study.save_settings, conn, body, app.settings.root)
    if route == 'sessions':
        return _call(study.save_session, conn, body)
    if route == 'generate-plan':
        return _call(study.generate_plan, conn, body)
    if route in study.TABLES:
        return _call(study.save_entity, conn, route, body)
    return None
