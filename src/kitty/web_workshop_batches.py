"""Explicit, local batch consent with durable state and a bounded background run."""
from __future__ import annotations

from contextlib import closing
import re

from kitty.core.workshop import ai, batches, store
from kitty.db import connect

_PREFIX = '/api/workshop-batches'
_ID = r'([0-9a-f-]{36})'


def _keys(body, allowed, required=()):
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
        raise store.WorkshopError('Invalid batch request fields')


def _confirm(body):
    if body.get('confirm') is not True:
        raise store.WorkshopError('Explicit confirmation is required')


def _error(exc):
    from kitty.web_server import APIError
    return APIError(exc.status, str(exc))


def start(app):
    with closing(connect(app.settings.db_path)) as conn:
        batches.recover(conn)


def get(app, conn, path, query):
    if path != _PREFIX and not path.startswith(_PREFIX + '/'):
        return None
    try:
        _keys(query, ())
        if path == _PREFIX:
            return {'batches': batches.listing(conn), 'jobs': batches.job_choices(conn),
                    'insights': batches.insights(conn)}
        match = re.fullmatch(_PREFIX + '/' + _ID, path)
        if match:
            return {'batch': batches.get(conn, match[1])}
        return None
    except store.WorkshopError as exc:
        raise _error(exc) from None


def post(app, conn, path, body):
    if path != _PREFIX and not path.startswith(_PREFIX + '/'):
        return None
    try:
        if path == _PREFIX:
            _keys(body, ('master_id', 'base_revision', 'job_ids', 'research', 'max_cost_usd', 'confirm'),
                  ('master_id', 'base_revision', 'job_ids', 'max_cost_usd', 'confirm'))
            _confirm(body)
            return {'batch': batches.create(conn, master_id=body['master_id'], base_revision=body['base_revision'],
                                            job_ids=body['job_ids'], research=body.get('research', False),
                                            max_cost_usd=body['max_cost_usd'])}
        match = re.fullmatch(_PREFIX + '/' + _ID + '/(run|cancel)', path)
        if not match:
            return None
        _keys(body, ('confirm',), ('confirm',))
        _confirm(body)
        identifier, operation = match.groups()
        if operation == 'cancel':
            return {'batch': batches.cancel(conn, identifier)}
        batch = batches.get(conn, identifier)
        if batch['status'] != 'queued':
            raise store.WorkshopError('This batch is already running or finished', 409)
        if not ai.get_settings(conn)['enabled']:
            raise store.WorkshopError('Enable hosted editing and review its privacy notice first', 403)

        def work():
            try:
                with closing(connect(app.settings.db_path)) as own:
                    return {'batch': batches.run(app, own, identifier)}
            except store.WorkshopError as exc:
                raise _error(exc) from None
            finally:
                app.mutated()

        return {'task': app.tasks.start('workshop-batch-' + identifier, work)}
    except store.WorkshopError as exc:
        raise _error(exc) from None
