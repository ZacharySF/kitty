"""The mind maps' backlink routes; no AI or network side effects.

The maps themselves (list, one map, export, network, tracked jobs, create, import, save,
add a node, delete, undo, import tracked jobs, transfer) are answered by rika-server
(server/src/schema). These stay here: they scan the workspace through `kitty.core.backlinks`.
"""
from __future__ import annotations

from kitty.core import backlinks, schema_maps
from kitty.modules import MODULE_IDS


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
    if path != '/api/schema/backlinks':
        return None
    if set(query) == {'map_id'}:
        try:
            map_id = int(query['map_id'])
        except (ValueError, TypeError):
            raise APIError(400, 'Map ID must be an integer') from None
        return _call(backlinks.suggestions, conn, map_id, applyai=_applyai(app))
    if 'term' in query and not set(query) - {'term', 'exclude'}:
        return _call(backlinks.mentions, conn, query['term'], query.get('exclude'), applyai=_applyai(app))
    raise APIError(400, 'Provide map_id, or term with an optional exclude=<kind>:<id>')


def post(app, conn, path, body):
    if path != '/api/schema/backlinks/apply':
        return None
    applyai = _applyai(app)
    with schema_maps.applyai_scope(applyai):
        return _call(backlinks.apply, conn, body, applyai=applyai)
