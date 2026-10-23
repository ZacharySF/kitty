"""Loopback calendar extension. OAuth runs outside the request mutation lock."""
from __future__ import annotations

import re
from contextlib import closing
from datetime import datetime, timedelta, timezone

from kitty.core import calendar, calendar_google as google
from kitty.db import connect


def get(app, conn, path, query):
    if path == '/api/calendar':
        if set(query) - {'start', 'end', 'timezone', 'search', 'calendar_id', 'hidden'}:
            calendar.fail('Unknown calendar query')
        at = datetime.now(timezone.utc)
        start = query.get('start', (at - timedelta(days=7)).isoformat())
        end = query.get('end', (at + timedelta(days=35)).isoformat())
        tz = query.get('timezone', 'UTC')
        calendar.zone(tz)
        search = calendar.text(query.get('search', ''), 'Search', 500)
        applyai = 'applyai' in app.modules
        events = calendar.occurrences(conn, start, end, search=search, calendar_id=query.get('calendar_id'), include_hidden=query.get('hidden') == '1', applyai=applyai)
        available = calendar.availability(events, start, end)
        # Do not expose sync tokens or provider ETags in the local collection.
        cals = [{k: v for k, v in c.items() if k not in {'sync_token', 'google_etag'}} for c in calendar.calendars(conn)]
        return {'calendars': cals, 'events': events, 'tasks': calendar.tasks(conn, search),
                'activity': calendar.activity(conn, start, end, tz, applyai=applyai),
                'reminders': calendar.due_reminders(conn, applyai=applyai), **available}
    match = re.fullmatch(r'/api/calendar/events/([a-zA-Z0-9_-]{1,100})', path)
    if match:
        app._no_query(query)
        return calendar.get_event(conn, match[1], applyai='applyai' in app.modules)
    if path == '/api/calendar/export':
        if set(query) - {'calendar_id'}:
            calendar.fail('Unknown export query')
        return calendar.export_ics(conn, query.get('calendar_id'), applyai='applyai' in app.modules)
    if path == '/api/calendar/reminders':
        app._no_query(query)
        return {'items': calendar.due_reminders(conn, applyai='applyai' in app.modules)}
    if path == '/api/calendar/google/status':
        app._no_query(query)
        return google.status(app.settings, conn)
    if path == '/api/calendar/google/conflicts':
        app._no_query(query)
        return {'items': google.conflicts(conn)}
    return None


def post(app, conn, path, body):
    if path == '/api/calendar/calendars':
        return calendar.save_calendar(conn, body)
    if path == '/api/calendar/calendars/delete':
        return calendar.delete_calendar(conn, body)
    if path == '/api/calendar/events':
        return calendar.create_event(conn, body)
    match = re.fullmatch(r'/api/calendar/events/([a-zA-Z0-9_-]{1,100})', path)
    if match:
        calendar.ensure_visible(conn, match[1], applyai='applyai' in app.modules)
        return calendar.edit_event(conn, match[1], body)
    if path == '/api/calendar/tasks':
        return calendar.save_task(conn, body)
    if path == '/api/calendar/undo':
        if type(body.get('id')) is not int:
            calendar.fail('Undo ID must be an integer')
        return calendar.undo(conn, body['id'])
    if path == '/api/calendar/reminders/dismiss':
        calendar.ensure_visible(conn, body.get('event_id'), applyai='applyai' in app.modules)
        return calendar.dismiss_reminder(conn, body)
    if path == '/api/calendar/time':
        return {'value': calendar.wall_instant(body.get('value', ''), body.get('timezone', 'UTC'), body.get('fold', 0))}
    if path == '/api/calendar/import-jobs':
        if body:
            calendar.fail('Job-date refresh expects an empty object')
        return calendar.import_jobs(conn, applyai='applyai' in app.modules)
    if path == '/api/calendar/import/preview':
        if body.get('format') == 'ics':
            return calendar.preview_ics(conn, body.get('content', ''), body.get('timezone', 'UTC'), body.get('calendar_id', 'personal'))
        if body.get('format') == 'csv':
            return calendar.preview_csv(conn, body.get('content', ''), body.get('timezone', 'UTC'), applyai='applyai' in app.modules)
        calendar.fail('Choose ICS or CSV')
    if path == '/api/calendar/import':
        if body.get('confirmed') is not True:
            calendar.fail('Review and confirm selected imported items')
        return calendar.import_reviewed(conn, body.get('items'))
    if path == '/api/calendar/availability':
        start, end = body.get('start', ''), body.get('end', '')
        events = calendar.occurrences(conn, start, end, include_hidden=True, applyai='applyai' in app.modules)
        return calendar.availability(events, start, end, exclude_id=body.get('exclude_id'))
    if path == '/api/calendar/google/client':
        return google.upload_client(app.settings, body)
    if path == '/api/calendar/google/disconnect':
        if body.get('confirmed') is not True:
            calendar.fail('Confirm disconnecting the separate Calendar token')
        return google.disconnect(app.settings, body.get('revoke') is True)
    if path == '/api/calendar/google/preview':
        return google.preview_sync(conn, body, applyai='applyai' in app.modules)
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

        def work():
            with closing(connect(app.settings.db_path)) as owned:
                if path.endswith('/operation/preview'):
                    return google.preview_operation(app.settings, body)
                if path.endswith('/sync'):
                    return google.sync(app.settings, owned, body, applyai=applyai)
                if path.endswith('/resolve-calendar'):
                    return google.resolve_calendar(app.settings, owned, body)
                if path.endswith('/resolve'):
                    return google.resolve_conflict(app.settings, owned, body)
                if path.endswith('/select'):
                    return google.select_calendar(app.settings, owned, body)
                return google.operation(app.settings, body)
        return {'task': app.tasks.start('calendar-google', work)}
    return None
