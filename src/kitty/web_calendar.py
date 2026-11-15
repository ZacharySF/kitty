"""The calendar routes still answered by Python: Google Calendar, ICS export and import preview.

The calendar's local routes (calendars, events, tasks, undo, availability, reminders, wall
time, the source-date and reviewed imports) are answered by rika-server
(server/src/calendar/). OAuth runs outside the request mutation lock.

Google previews propose effects (kitty.core.effects) as the person at this rika! window
session; the matching sync/operation request approves one by ``effect_id`` and ``digest``.
"""
from __future__ import annotations

from contextlib import closing

from kitty import web_auth
from kitty.core import calendar, calendar_google as google
from kitty.db import connect


def get(app, conn, path, query):
    if path == '/api/calendar/export':
        if set(query) - {'calendar_id'}:
            calendar.fail('Unknown export query')
        return calendar.export_ics(conn, query.get('calendar_id'), applyai='applyai' in app.modules)
    if path == '/api/calendar/google/status':
        app._no_query(query)
        return google.status(app.settings, conn)
    if path == '/api/calendar/google/conflicts':
        app._no_query(query)
        return {'items': google.conflicts(conn)}
    return None


def post(app, conn, path, body):
    if path == '/api/calendar/import/preview':
        if body.get('format') == 'ics':
            return calendar.preview_ics(conn, body.get('content', ''), body.get('timezone', 'UTC'), body.get('calendar_id', 'personal'))
        if body.get('format') == 'csv':
            return calendar.preview_csv(conn, body.get('content', ''), body.get('timezone', 'UTC'), applyai='applyai' in app.modules)
        calendar.fail('Choose ICS or CSV')
    if path == '/api/calendar/google/client':
        return google.upload_client(app.settings, body)
    if path == '/api/calendar/google/disconnect':
        if body.get('confirmed') is not True:
            calendar.fail('Confirm disconnecting the separate Calendar token')
        return google.disconnect(app.settings, body.get('revoke') is True)
    if path == '/api/calendar/google/preview':
        return google.preview_sync(conn, body, actor=web_auth.window_user(app), applyai='applyai' in app.modules)
    if path == '/api/calendar/google/unselect':
        return google.unselect(conn, body)
    if path == '/api/calendar/google/authorize':
        if body.get('confirmed') is not True:
            calendar.fail('Confirm opening Google consent for separate Calendar permissions')
        if app.settings.demo:
            calendar.fail('Google authorization is disabled in demo mode', 403)
        return {'task': app.tasks.start('calendar-oauth', lambda: google.authorize(app.settings, body.get('include_tasks') is True))}
    if path in {'/api/calendar/google/sync', '/api/calendar/google/resolve', '/api/calendar/google/select', '/api/calendar/google/operation',
                '/api/calendar/google/operation/preview', '/api/calendar/google/resolve-calendar'}:
        if app.settings.demo:
            calendar.fail('External Google calls are disabled in demo mode', 403)
        # Each background operation owns its SQLite connection. Consent and network
        # IO never hold WebApplication.mutation_lock across browser authorization.
        applyai = 'applyai' in app.modules
        actor = web_auth.window_user(app)

        def work():
            with closing(connect(app.settings.db_path)) as owned:
                if path.endswith('/operation/preview'):
                    return google.preview_operation(app.settings, owned, body, actor=actor)
                if path.endswith('/sync'):
                    return google.sync(app.settings, owned, body, actor=actor, applyai=applyai)
                if path.endswith('/resolve-calendar'):
                    return google.resolve_calendar(app.settings, owned, body)
                if path.endswith('/resolve'):
                    return google.resolve_conflict(app.settings, owned, body)
                if path.endswith('/select'):
                    return google.select_calendar(app.settings, owned, body)
                return google.operation(app.settings, owned, body, actor=actor)
        return {'task': app.tasks.start('calendar-google', work)}
    return None
