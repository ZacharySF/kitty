"""Opportunity monitoring: its settings, the discovery runs and the public sources.

The list, one opportunity, manual entries, imports, edits, favorites, projects and the
deadline's calendar event are answered by rika-server (server/src/opportunities). What
stays here edits the build file (settings), runs a background task that fetches public
sources (discover) or changes where one is fetched from (sources/<id>).
:mod:`kitty.core.opportunities` stays: discovery and the settings here call it, and it is
the reference rika-server is compared with (tests/test_opportunities_parity.py).

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
    if not path.startswith('/api/opportunities/'): return None
    if path == '/api/opportunities/settings':
        if app.settings.demo and body.get('enabled'): core.fail('External monitoring is disabled in demo', 403)
        result = core.save_settings(conn, body, app.settings.root)
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
    return providers.save_source(conn, match[1], body) if match else None


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


def build_applied(app, build):
    """A new active build (any window, Settings, an external editor or an install) may turn
    scheduled monitoring on or off."""
    if build["settings"]["opportunities"]["monitoring"]:
        start(app)
    else:
        stop(app)
