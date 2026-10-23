"""Revision-checked local schema graph API; no AI or network side effects."""
from __future__ import annotations

import json

from kitty.core import backlinks, schema_maps
from kitty.modules import MODULE_IDS, ModuleDisabledError


def _applyai(app):
    """Whether ApplyAI is active for this app; a bare stub app (tests) means every module."""
    return 'applyai' in getattr(app, 'modules', MODULE_IDS)


def _call(function, *args, **kwargs):
    from kitty.web_server import APIError
    try:
        return function(*args, **kwargs)
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
        with schema_maps.applyai_scope(_applyai(app)):
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
        with schema_maps.applyai_scope(_applyai(app)):
            return _call(schema_maps.network, conn, query.get('mode') or None, map_ids, flag in {'true', '1'})
    if path == '/api/schema/backlinks':
        if set(query) == {'map_id'}:
            try:
                map_id = int(query['map_id'])
            except (ValueError, TypeError):
                raise APIError(400, 'Map ID must be an integer') from None
            return _call(backlinks.suggestions, conn, map_id, applyai=_applyai(app))
        if 'term' in query and not set(query) - {'term', 'exclude'}:
            return _call(backlinks.mentions, conn, query['term'], query.get('exclude'), applyai=_applyai(app))
        raise APIError(400, 'Provide map_id, or term with an optional exclude=<kind>:<id>')
    return None


def _applyai_sources(nodes):
    return {(node['source'].get('kind'), str(node['source'].get('id'))) for node in nodes
            if isinstance(node, dict) and isinstance(node.get('source'), dict)
            and node['source'].get('kind') in schema_maps.APPLYAI_KINDS}


def _reject_new_applyai_sources(conn, path, body):
    """Without ApplyAI no job/company link may be created; existing links are kept."""
    from kitty.web_server import APIError
    if path == '/api/schema/add-node':
        found = _applyai_sources([body])
        existing = set()
    elif path == '/api/schema/save':
        graph = body.get('graph') if isinstance(body.get('graph'), dict) else {}
        nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
        found = _applyai_sources(nodes)
        row = conn.execute('SELECT graph_json FROM schema_maps WHERE id=?', (body.get('id'),)).fetchone() \
            if isinstance(body.get('id'), int) else None
        existing = _applyai_sources(json.loads(row['graph_json']).get('nodes', [])) if row else set()
    elif path in {'/api/schema/create', '/api/schema/import'}:
        document = body.get('document') if isinstance(body.get('document'), dict) else {}
        graph = document.get('graph') if isinstance(document.get('graph'), dict) else {}
        found = _applyai_sources(graph.get('nodes') if isinstance(graph.get('nodes'), list) else [])
        existing = set()
    else:
        return
    if found - existing:
        raise APIError(403, str(ModuleDisabledError('applyai')))


def post(app, conn, path, body):
    routes = {'/api/schema/create': schema_maps.create, '/api/schema/import': schema_maps.create,
              '/api/schema/add-node': schema_maps.add_node,
              '/api/schema/save': schema_maps.save, '/api/schema/delete': schema_maps.delete,
              '/api/schema/undo': schema_maps.undo, '/api/schema/import-jobs': schema_maps.import_jobs,
              '/api/schema/backlinks/apply': lambda c, b: backlinks.apply(c, b, applyai=_applyai(app))}
    handler = routes.get(path)
    if handler is None:
        return None
    applyai = _applyai(app)
    if not applyai:
        _reject_new_applyai_sources(conn, path, body)
    with schema_maps.applyai_scope(applyai):
        return _call(handler, conn, body)
