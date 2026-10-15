"""Local opportunity extension with opt-in persisted background monitoring.

Register get/post in WebApplication.extensions, call start(app) after migrations,
and stop(app) before draining app.tasks. Monitoring runs while the app is open;
a due run missed during shutdown starts on the next launch. No paid providers.
"""
from __future__ import annotations

import re
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone

from kitty.core import opportunities as core, opportunity_sources as providers
from kitty.dates import now_iso
from kitty.db import connect
from kitty.db.connection import transaction


def get(app, conn, path, query):
    if path == '/api/opportunities':
        items = core.list_items(conn, query)
        config = core.settings(conn)
        return {'items': items, 'total': len(items), 'sources': providers.sources(conn),
                'profile': config['profile'], 'schedule': {k: v for k, v in config.items() if k != 'profile'},
                'statuses': list(core.STATUSES)}
    match = re.fullmatch(r'/api/opportunities/([1-9][0-9]*)', path)
    if match:
        if query: core.fail('Detail does not accept query parameters')
        return core.detail(conn, int(match[1]))
    return None


def _run(app, selected=None):
    def work():
        with closing(connect(app.settings.db_path)) as conn:
            try:
                result = providers.discover(conn, app.settings.cache_dir, selected)
                error = 'Some public sources are partial, failed, or blocked; inspect source health.' if result['partial'] else None
            except Exception:
                with transaction(conn):
                    config = core.settings(conn)
                    conn.execute('UPDATE opportunity_settings SET last_run=?,last_error=?,next_run=? WHERE id=1',
                                 (now_iso(), 'Monitoring run failed; inspect task status.',
                                  (datetime.now(timezone.utc) + timedelta(hours=config['interval_hours'])).isoformat() if config['enabled'] else None))
                raise
            with transaction(conn):
                config = core.settings(conn)
                conn.execute('UPDATE opportunity_settings SET last_run=?,last_error=?,next_run=? WHERE id=1',
                             (now_iso(), error, (datetime.now(timezone.utc) + timedelta(hours=config['interval_hours'])).isoformat() if config['enabled'] else None))
            return result
    return app.tasks.start('opportunity-monitor', work)


def post(app, conn, path, body):
    if not path.startswith('/api/opportunities'): return None
    if path == '/api/opportunities': return core.create(conn, body)
    if path == '/api/opportunities/import':
        if set(body) != {'items'}:
            core.fail('Import requires an items array')
        return core.import_items(conn, body['items'])
    if path == '/api/opportunities/favorites':
        if set(body) != {'selections', 'favorite'}: core.fail('Expected selections and favorite')
        return core.favorites(conn, body['selections'], body['favorite'])
    if path == '/api/opportunities/settings':
        if app.settings.demo and body.get('enabled'): core.fail('External monitoring is disabled in demo', 403)
        result = core.save_settings(conn, body)
        if result['enabled']:
            start(app)
        else:
            stop(app)
        return result
    if path == '/api/opportunities/discover':
        if app.settings.demo: core.fail('External provider requests are disabled in demo', 403)
        if set(body) - {'confirmed', 'sources'} or body.get('confirmed') is not True:
            core.fail('Explicit confirmation is required to fetch free public sources')
        selected = body.get('sources')
        if selected is not None:
            known = {s['id'] for s in providers.sources(conn)}
            if not isinstance(selected, list) or not 1 <= len(selected) <= len(known) or any(not isinstance(s, str) or s not in known for s in selected): core.fail('Select known public sources')
        return {'task': _run(app, selected)}
    match = re.fullmatch(r'/api/opportunities/sources/([a-z0-9-]+)', path)
    if match: return providers.save_source(conn, match[1], body)
    match = re.fullmatch(r'/api/opportunities/([1-9][0-9]*)(?:/(delete|project|items|project-details|calendar))?', path)
    if not match: return None
    ident, action = int(match[1]), match[2]
    if action == 'calendar':
        if body: core.fail('Calendar export expects an empty object')
        return {'event': core.export_deadline(conn, ident)}
    if action == 'delete':
        if set(body) != {'revision'}: core.fail('Deletion requires revision')
        return core.remove(conn, ident, body['revision'])
    if action == 'project':
        if set(body) != {'revision'}: core.fail('Project creation requires opportunity revision')
        return core.project(conn, ident, body)
    if action == 'items': return core.project_item(conn, ident, body)
    if action == 'project-details': return core.update_project(conn, ident, body)
    changes = {k: v for k, v in body.items() if k != 'revision'}
    return core.update(conn, ident, changes, body.get('revision'))


def start(app):
    """Start exactly one process-local scheduler; disabled until explicit enable."""
    if app.settings.demo:
        return
    with closing(connect(app.settings.db_path)) as conn:
        if not core.settings(conn)['enabled']:
            return
    if getattr(app, 'opportunity_monitor', None): return
    halted = threading.Event()
    def loop():
        while not halted.wait(30):
            if app.settings.demo: continue
            try:
                with closing(connect(app.settings.db_path)) as conn:
                    config = core.settings(conn)
                if config['enabled'] and (not config['next_run'] or datetime.fromisoformat(config['next_run']) <= datetime.now(timezone.utc)):
                    _run(app)
            except Exception as exc:
                from kitty.web_server import APIError
                # An already-running monitor is normal; do not dispatch duplicates.
                if isinstance(exc, APIError) and exc.status == 409: continue
                with closing(connect(app.settings.db_path)) as conn:
                    conn.execute('UPDATE opportunity_settings SET last_error=? WHERE id=1',
                                 (f'Scheduler could not start monitoring ({type(exc).__name__}).',))
    thread = threading.Thread(target=loop, name='kitty-opportunity-schedule', daemon=True)
    app.opportunity_monitor = (halted, thread)
    thread.start()


def stop(app):
    monitor = getattr(app, 'opportunity_monitor', None)
    if monitor:
        monitor[0].set()
        monitor[1].join()
        app.opportunity_monitor = None
