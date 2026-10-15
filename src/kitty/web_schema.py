"""Revision-checked local schema graph API; no AI or network side effects."""
from __future__ import annotations

from kitty.core import schema_maps


def _call(function, *args):
    from kitty.web_server import APIError
    try:
        return function(*args)
    except schema_maps.SchemaConflict as exc:
        raise APIError(409, str(exc)) from None
    except ValueError as exc:
        raise APIError(400, str(exc)) from None


def get(app, conn, path, query):
    from kitty.web_server import APIError
    if path == '/api/schema':
        app._no_query(query)
        return _call(schema_maps.workspace, conn)
    if path in {'/api/schema/map', '/api/schema/export'}:
        if set(query) != {'id'}:
            raise APIError(400, 'Provide a map ID only')
        try:
            item_id = int(query['id'])
        except (ValueError, TypeError):
            raise APIError(400, 'Map ID must be an integer') from None
        return _call(schema_maps.load if path.endswith('/map') else schema_maps.export, conn, item_id)
    if path == '/api/schema/jobs':
        if set(query) - {'search', 'offset'}:
            raise APIError(400, 'Unknown jobs query parameter')
        try:
            offset = int(query.get('offset', '0'))
        except (ValueError, TypeError):
            raise APIError(400, 'Offset must be an integer') from None
        return _call(schema_maps.job_options, conn, query.get('search', ''), offset)
    if path == '/api/schema/network':
        if set(query) - {'mode', 'map_ids', 'merge_labels'}:
            raise APIError(400, 'Unknown network query parameter')
        flag = query.get('merge_labels', 'false')
        if flag not in {'true', 'false', '1', '0'}:
            raise APIError(400, 'merge_labels must be true or false')
        map_ids = None
        if query.get('map_ids'):
            try:
                map_ids = [int(value) for value in query['map_ids'].split(',')]
            except ValueError:
                raise APIError(400, 'Map IDs must be comma-separated integers') from None
        return _call(schema_maps.network, conn, query.get('mode') or None, map_ids, flag in {'true', '1'})
    return None


def post(app, conn, path, body):
    routes = {'/api/schema/create': schema_maps.create, '/api/schema/import': schema_maps.create,
              '/api/schema/save': schema_maps.save, '/api/schema/delete': schema_maps.delete,
              '/api/schema/undo': schema_maps.undo, '/api/schema/import-jobs': schema_maps.import_jobs}
    handler = routes.get(path)
    return _call(handler, conn, body) if handler else None
