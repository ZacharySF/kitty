"""Bounded local mental maps; source records remain authoritative and read-only.

A map is one revisioned graph/viewport snapshot. Saves retain one durable undo
snapshot; undo itself is revision-checked. Portable exports detach local IDs so
importing on another installation cannot accidentally link unrelated records.
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import urlsplit

from kitty.db.connection import transaction

MODES = {'study', 'general', 'jobs'}
KINDS = {'class', 'topic', 'idea', 'task', 'job', 'company', 'skill'}
MAX_MAPS = 100
MAX_NODES = 300
MAX_EDGES = 600
MAX_BYTES = 1_000_000


class SchemaConflict(ValueError):
    """Reload or export local edits rather than overwriting another window."""


#: Source kinds whose live data belongs to the ApplyAI module.
APPLYAI_KINDS = frozenset({'job', 'company'})
_applyai: ContextVar[bool] = ContextVar('schema_applyai', default=True)


@contextmanager
def applyai_scope(enabled: bool):
    """Request-scoped: while off, job/company sources keep their link but show no live data."""
    token = _applyai.set(enabled)
    try:
        yield
    finally:
        _applyai.reset(token)


def _text(value, name, limit=200, required=False):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError(f'{name} must be text of at most {limit} characters')
    value = value.strip()
    if required and not value:
        raise ValueError(f'{name} is required')
    return value


def _integer(value, name='ID', low=1, high=2**63 - 1):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name} must be an integer between {low} and {high}')
    return value


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f'{name} must be a finite number between {low} and {high}')
    return value


def _fields(value, allowed):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unknown schema field or invalid object')


def _url(value):
    value = _text(value, 'Link', 4000)
    if not value:
        return ''
    try:
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or any(ord(c) < 33 for c in value):
            raise ValueError()
        parts.port
    except ValueError:
        raise ValueError('Links must be absolute HTTP or HTTPS URLs without credentials') from None
    return value


def _safe_url(value):
    try:
        return _url(value or '')
    except ValueError:
        return ''


def _key(value):
    value = _text(value, 'Graph ID', 80, True)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Graph IDs may only contain letters, digits, underscores and hyphens')
    return value


def viewport(value):
    _fields(value, {'x', 'y', 'zoom'})
    return {'x': _number(value.get('x'), 'Viewport x', -1e7, 1e7),
            'y': _number(value.get('y'), 'Viewport y', -1e7, 1e7),
            'zoom': _number(value.get('zoom'), 'Zoom', 0.1, 4)}


def _source(conn, value, kind):
    if value is None:
        return None
    _fields(value, {'kind', 'id'})
    if value.get('kind') == 'calendar_task':
        source_id = _text(value.get('id'), 'Task ID', 200, True)
        if kind != 'task' or not conn.execute('SELECT id FROM calendar_tasks WHERE id=? AND deleted=0', (source_id,)).fetchone():
            raise ValueError('Calendar task source is missing or has the wrong node kind')
        return {'kind': 'calendar_task', 'id': source_id}
    if value.get('kind') == 'note_page':
        source_id = _integer(value.get('id'))
        if not conn.execute('SELECT id FROM note_page_index WHERE id=?', (source_id,)).fetchone():
            raise ValueError('Note page no longer exists')
        return {'kind': 'note_page', 'id': source_id}
    expected = {'class': ('subject', 'study_subjects'), 'job': ('job', 'jobs'), 'company': ('company', 'companies')}.get(kind)
    if not expected or value.get('kind') != expected[0]:
        raise ValueError('Source reference does not match the node kind')
    source_id = _integer(value.get('id'))
    if not conn.execute(f'SELECT id FROM {expected[1]} WHERE id=?', (source_id,)).fetchone():
        raise ValueError('The selected source no longer exists; unlink it before saving')
    return {'kind': expected[0], 'id': source_id}


def graph(conn, value, *, portable=False):
    _fields(value, {'nodes', 'edges'})
    nodes, edges = value.get('nodes'), value.get('edges')
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES or not isinstance(edges, list) or len(edges) > MAX_EDGES:
        raise ValueError(f'Maps support at most {MAX_NODES} nodes and {MAX_EDGES} connections')
    result = {'nodes': [], 'edges': []}
    ids, sources = set(), set()
    for node in nodes:
        _fields(node, {'id', 'kind', 'label', 'notes', 'x', 'y', 'url', 'source', 'collapsed', 'map_id'})
        key = _key(node.get('id'))
        kind = node.get('kind')
        if not isinstance(kind, str) or kind not in KINDS or key in ids:
            raise ValueError('Node kinds must be recognized and node IDs unique')
        ids.add(key)
        if portable and node.get('source') is not None:
            raise ValueError('Portable imports cannot contain local source IDs; export first or explicitly remove source references')
        source = _source(conn, node.get('source'), kind)
        if source:
            identity = (source['kind'], source['id'])
            if identity in sources:
                raise ValueError('A source may only appear once in a map')
            sources.add(identity)
        if not isinstance(node.get('collapsed', False), bool):
            raise ValueError('Collapsed must be true or false')
        linked_map = node.get('map_id')
        if linked_map is not None:
            if portable:
                raise ValueError('Portable imports cannot contain local map IDs')
            _row(conn, linked_map)
        result['nodes'].append({'id': key, 'kind': kind, 'label': _text(node.get('label'), 'Label', 200, True),
                                'notes': _text(node.get('notes', ''), 'Notes', 10000),
                                'x': _number(node.get('x'), 'Node x', -100000, 100000),
                                'y': _number(node.get('y'), 'Node y', -100000, 100000),
                                'url': _url(node.get('url', '')), 'source': source,
                                **({'collapsed': node['collapsed']} if 'collapsed' in node else {}),
                                **({'map_id': linked_map} if linked_map is not None else {})})
    edge_ids = set()
    for edge in edges:
        _fields(edge, {'id', 'from', 'to', 'label'})
        key = _key(edge.get('id'))
        start, end = edge.get('from'), edge.get('to')
        if not isinstance(start, str) or not isinstance(end, str) or start not in ids or end not in ids:
            raise ValueError('Every connection endpoint must be a node in this map')
        if key in edge_ids:
            raise ValueError('Connection IDs must be unique')
        edge_ids.add(key)
        result['edges'].append({'id': key, 'from': start, 'to': end, 'label': _text(edge.get('label', ''), 'Connection label', 200)})
    if len(json.dumps(result, ensure_ascii=True).encode()) > MAX_BYTES - 4096:
        raise ValueError('Graph exceeds the 1 MB size limit')
    return result


def _row(conn, item_id):
    row = conn.execute('SELECT * FROM schema_maps WHERE id=?', (_integer(item_id),)).fetchone()
    if row is None:
        raise ValueError('Map not found')
    return dict(row)


def _revision(row, value):
    if isinstance(value, bool) or not isinstance(value, int) or value != row['revision']:
        raise SchemaConflict('Map changed in another window. Export your local edits or reload before editing.')


def _lookup(conn, kind, source_id):
    """Read-only live view of one source record; deleted sources report missing."""
    if kind in APPLYAI_KINDS and not _applyai.get():
        return {'unavailable': True}
    if kind == 'calendar_task':
        row = conn.execute('SELECT data FROM calendar_tasks WHERE id=? AND deleted=0', (source_id,)).fetchone()
        return {'label': json.loads(row['data']).get('title', '')} if row else {'missing': True}
    if kind == 'note_page':
        row = conn.execute('SELECT title AS label FROM note_page_index WHERE id=?', (source_id,)).fetchone()
    elif kind == 'subject':
        row = conn.execute('SELECT name AS label FROM study_subjects WHERE id=?', (source_id,)).fetchone()
    elif kind == 'company':
        row = conn.execute('SELECT display_name AS label,website AS url FROM companies WHERE id=?', (source_id,)).fetchone()
    else:
        row = conn.execute('''SELECT j.title AS label,j.url,j.queue_state,j.closed_at,a.status,
            c.display_name AS company FROM jobs j JOIN companies c ON c.id=j.company_id
            LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=?''', (source_id,)).fetchone()
    value = dict(row) if row else {'missing': True}
    if 'url' in value:
        value['url'] = _safe_url(value['url'])
    return value


def _live(conn, document):
    return {node['id']: _lookup(conn, node['source']['kind'], node['source']['id'])
            for node in document['nodes'] if node['source'] is not None}


def load(conn, item_id):
    row = _row(conn, item_id)
    document = json.loads(row['graph_json'])
    return {'id': row['id'], 'title': row['title'], 'mode': row['mode'], 'revision': row['revision'],
            'graph': document, 'viewport': json.loads(row['viewport_json']), 'can_undo': row['previous_json'] is not None,
            'live': _live(conn, document)}


def workspace(conn):
    return {'maps': [dict(row) for row in conn.execute('SELECT id,title,mode,revision,updated_at FROM schema_maps ORDER BY updated_at DESC,id DESC')],
            'subjects': [dict(row) for row in conn.execute('SELECT id,name FROM study_subjects ORDER BY name,id')]}


def _normalized(label):
    return ' '.join(label.casefold().split())


def network(conn, mode=None, map_ids=None, merge_labels=False):
    """Merge saved maps into one read-only network.

    Nodes sharing a source record (subject/job/company ID) always unify across
    maps. Unsourced nodes stay per-map unless ``merge_labels`` is explicitly
    enabled, which unifies unsourced nodes of the same kind whose whitespace-
    and case-normalized labels match. Edges with the same merged endpoints and
    normalized label collapse into one weighted edge that records every map it
    came from. Clusters are connected components, numbered by descending size
    then smallest node ID, so the same data always yields the same clusters.
    """
    if mode is not None and (not isinstance(mode, str) or mode not in MODES):
        raise ValueError('Mode must be study, general or jobs')
    if not isinstance(merge_labels, bool):
        raise ValueError('Label merge must be true or false')
    query, params, clauses = 'SELECT id,title,mode,graph_json FROM schema_maps', [], []
    if map_ids is not None:
        if not isinstance(map_ids, list) or not map_ids or len(map_ids) > MAX_MAPS:
            raise ValueError(f'Choose between 1 and {MAX_MAPS} maps')
        map_ids = sorted({_integer(value, 'Map ID') for value in map_ids})
        found = {row[0] for row in conn.execute(f'SELECT id FROM schema_maps WHERE id IN ({",".join("?" * len(map_ids))})', map_ids)}
        if found != set(map_ids):
            raise ValueError('Map not found')
        clauses.append(f'id IN ({",".join("?" * len(map_ids))})')
        params += map_ids
    if mode is not None:
        clauses.append('mode=?')
        params.append(mode)
    if clauses:
        query += ' WHERE ' + ' AND '.join(clauses)
    rows = conn.execute(query + ' ORDER BY id LIMIT ?', (*params, MAX_MAPS)).fetchall()
    nodes, edges, lookup, sources, summaries = {}, {}, {}, {}, []
    for row in rows:
        document = json.loads(row['graph_json'])
        summaries.append({'id': row['id'], 'title': row['title'], 'mode': row['mode'],
                          'nodes': len(document['nodes']), 'edges': len(document['edges'])})
        for item in document['nodes']:
            source = item['source']
            if source is not None:
                key = f"{source['kind']}:{source['id']}"
            elif merge_labels:
                key = f"label:{item['kind']}:{_normalized(item['label'])}"
            else:
                key = f"map:{row['id']}:{item['id']}"
            lookup[(row['id'], item['id'])] = key
            entry = nodes.get(key)
            if entry is None:
                live = None
                if source is not None:
                    identity = (source['kind'], source['id'])
                    if identity not in sources:
                        sources[identity] = _lookup(conn, *identity)
                    live = sources[identity]
                entry = nodes[key] = {'id': key, 'kind': item['kind'], 'label': (live or {}).get('label') or item['label'],
                                      'source': source, 'live': live, 'url': (live or {}).get('url') or item['url'],
                                      'maps': [], 'members': [], 'degree': 0, 'cluster': 0}
            elif not entry['url'] and item['url']:
                entry['url'] = item['url']
            if row['id'] not in entry['maps']:
                entry['maps'].append(row['id'])
            entry['members'].append({'map_id': row['id'], 'node_id': item['id'], 'label': item['label'], 'notes': item['notes'][:280]})
        for item in document['edges']:
            start, end = lookup[(row['id'], item['from'])], lookup[(row['id'], item['to'])]
            key = (start, end, _normalized(item['label']))
            entry = edges.get(key)
            if entry is None:
                entry = edges[key] = {'id': f'e{len(edges)}', 'from': start, 'to': end, 'label': item['label'],
                                      'weight': 0, 'maps': [], 'members': []}
            entry['weight'] += 1
            if row['id'] not in entry['maps']:
                entry['maps'].append(row['id'])
            entry['members'].append({'map_id': row['id'], 'edge_id': item['id']})
    parent = {key: key for key in nodes}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for edge in edges.values():
        nodes[edge['from']]['degree'] += 1
        if edge['to'] != edge['from']:
            nodes[edge['to']]['degree'] += 1
        a, b = root(edge['from']), root(edge['to'])
        if a != b:
            parent[max(a, b)] = min(a, b)
    components = {}
    for key in sorted(nodes):
        components.setdefault(root(key), []).append(key)
    clusters = []
    for index, keys in enumerate(sorted(components.values(), key=lambda keys: (-len(keys), keys[0]))):
        hub = min(keys, key=lambda key: (-nodes[key]['degree'], key))
        for key in keys:
            nodes[key]['cluster'] = index
        clusters.append({'id': index, 'size': len(keys), 'hub': hub, 'label': nodes[hub]['label']})
    return {'mode': mode, 'merge_labels': merge_labels, 'maps': summaries,
            'nodes': sorted(nodes.values(), key=lambda node: node['id']),
            'edges': list(edges.values()), 'clusters': clusters,
            'unified': sum(len(node['members']) > 1 for node in nodes.values())}


def job_options(conn, search='', offset=0):
    search = _text(search, 'Search', 200)
    offset = _integer(offset, 'Offset', 0, 1000000)
    rows = conn.execute('''SELECT j.id,j.title,c.display_name AS company,j.url,j.queue_state,a.status
        FROM jobs j JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id
        WHERE instr(lower(j.title || ' ' || c.display_name),lower(?))>0 ORDER BY j.id DESC LIMIT 51 OFFSET ?''', (search, offset)).fetchall()
    return {'jobs': [{**dict(row), 'url': _safe_url(row['url'])} for row in rows[:50]], 'more': len(rows) > 50}


def _snapshot(row):
    return json.dumps({key: row[key] for key in ('title', 'mode', 'graph_json', 'viewport_json')})


def _write(conn, row, title, document, view):
    conn.execute('''UPDATE schema_maps SET title=?,graph_json=?,viewport_json=?,previous_json=?,
        revision=revision+1,updated_at=? WHERE id=?''',
        (title, json.dumps(document), json.dumps(view), _snapshot(row), datetime.now(timezone.utc).isoformat(), row['id']))
    return load(conn, row['id'])


def create(conn, body):
    _fields(body, {'title', 'mode', 'document'})
    title = _text(body.get('title'), 'Title', 200, True)
    mode = body.get('mode', 'general')
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('Choose study, general or jobs mode')
    with transaction(conn, immediate=True):
        if conn.execute('SELECT count(*) FROM schema_maps').fetchone()[0] >= MAX_MAPS:
            raise ValueError('At most 100 maps are supported; export and delete an old map first')
        document, view = {'nodes': [], 'edges': []}, {'x': 40, 'y': 40, 'zoom': 1}
        if 'document' in body:
            imported = body['document']
            _fields(imported, {'format', 'version', 'title', 'mode', 'graph', 'viewport'})
            if imported.get('format') != 'kitty-schema' or type(imported.get('version')) is not int or imported['version'] != 1:
                raise ValueError('Unsupported portable map format/version')
            _text(imported.get('title'), 'Imported title', 200, True)
            if not isinstance(imported.get('mode'), str) or imported['mode'] not in MODES:
                raise ValueError('Imported mode must be study, general or jobs')
            if len(json.dumps(imported, ensure_ascii=True).encode()) > MAX_BYTES:
                raise ValueError('Portable map exceeds the 1 MB size limit')
            document = graph(conn, imported.get('graph'), portable=True)
            view = viewport(imported.get('viewport'))
        cursor = conn.execute('INSERT INTO schema_maps(title,mode,graph_json,viewport_json,updated_at) VALUES(?,?,?,?,?)',
                              (title, mode, json.dumps(document), json.dumps(view), datetime.now(timezone.utc).isoformat()))
        return load(conn, cursor.lastrowid)


def save(conn, body):
    _fields(body, {'id', 'revision', 'title', 'graph', 'viewport'})
    with transaction(conn, immediate=True):
        row = _row(conn, body.get('id'))
        _revision(row, body.get('revision'))
        return _write(conn, row, _text(body.get('title', row['title']), 'Title', 200, True),
                      graph(conn, body.get('graph')), viewport(body.get('viewport')))


def add_node(conn, body):
    """Insert a linked or free node without overwriting another editor's work."""
    _fields(body, {'map_id', 'revision', 'label', 'kind', 'parent_id', 'source'})
    with transaction(conn, immediate=True):
        row = _row(conn, body.get('map_id'))
        _revision(row, body.get('revision'))
        document = json.loads(row['graph_json'])
        source = _source(conn, body.get('source'), body.get('kind', 'topic'))
        if source and any(node['source'] == source for node in document['nodes']):
            return load(conn, row['id'])
        parent = body.get('parent_id')
        if parent is not None and not any(node['id'] == parent for node in document['nodes']):
            raise ValueError('Parent node not found')
        node_id = uuid.uuid4().hex
        document['nodes'].append({'id': node_id, 'kind': body.get('kind', 'topic'),
            'label': body.get('label'), 'notes': '', 'url': '', 'source': source,
            'x': 280 * len(document['nodes']), 'y': 0})
        if parent is not None:
            document['edges'].append({'id': uuid.uuid4().hex, 'from': parent, 'to': node_id, 'label': ''})
        return _write(conn, row, row['title'], graph(conn, document), json.loads(row['viewport_json']))


def delete(conn, body):
    _fields(body, {'id', 'revision', 'confirm'})
    if body.get('confirm') is not True:
        raise ValueError('Confirm map deletion first')
    with transaction(conn, immediate=True):
        row = _row(conn, body.get('id'))
        _revision(row, body.get('revision'))
        conn.execute('DELETE FROM schema_maps WHERE id=?', (row['id'],))
    return {'deleted': row['id']}


def undo(conn, body):
    _fields(body, {'id', 'revision'})
    with transaction(conn, immediate=True):
        row = _row(conn, body.get('id'))
        _revision(row, body.get('revision'))
        if row['previous_json'] is None:
            raise ValueError('No saved change to undo')
        previous = json.loads(row['previous_json'])
        # Missing sources can remain visible after undo; they are never rebound.
        conn.execute('''UPDATE schema_maps SET title=?,mode=?,graph_json=?,viewport_json=?,previous_json=NULL,
            revision=revision+1,updated_at=? WHERE id=?''',
            (*[previous[key] for key in ('title', 'mode', 'graph_json', 'viewport_json')], datetime.now(timezone.utc).isoformat(), row['id']))
        return load(conn, row['id'])


def export(conn, item_id):
    item = load(conn, item_id)
    for node in item['graph']['nodes']:
        live = item['live'].get(node['id'], {})
        node['label'] = (live.get('label') or node['label'])[:200]
        node['url'] = live.get('url', node['url'])
        node['source'] = None
        node.pop('map_id', None)
    return {'format': 'kitty-schema', 'version': 1, 'title': item['title'], 'mode': item['mode'],
            'graph': item['graph'], 'viewport': item['viewport']}


def import_jobs(conn, body):
    _fields(body, {'id', 'revision', 'job_ids'})
    ids = body.get('job_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 50:
        raise ValueError('Select between 1 and 50 tracked jobs')
    ids = list(dict.fromkeys(_integer(value) for value in ids))
    with transaction(conn, immediate=True):
        row = _row(conn, body.get('id'))
        _revision(row, body.get('revision'))
        if row['mode'] != 'jobs':
            raise ValueError('Import tracked jobs into a jobs-mode map')
        document = json.loads(row['graph_json'])
        sources = {(node['source']['kind'], node['source']['id']): node['id'] for node in document['nodes'] if node['source']}
        changed = False
        for job_id in ids:
            job = conn.execute('''SELECT j.id,j.title,j.company_id,j.url,c.display_name FROM jobs j
                JOIN companies c ON c.id=j.company_id WHERE j.id=?''', (job_id,)).fetchone()
            if job is None:
                raise ValueError('A selected job no longer exists')
            for kind, source_id, label, url in (('company', job['company_id'], job['display_name'], ''), ('job', job_id, job['title'], _safe_url(job['url']))):
                identity = (kind, source_id)
                if identity not in sources:
                    key = uuid.uuid4().hex
                    sources[identity] = key
                    index = len(document['nodes'])
                    document['nodes'].append({'id': key, 'kind': kind, 'label': label[:200], 'notes': '',
                                              'x': (index % 4) * 240, 'y': (index // 4) * 130, 'url': url,
                                              'source': {'kind': kind, 'id': source_id}})
                    changed = True
            start, end = sources[('job', job_id)], sources[('company', job['company_id'])]
            if not any(edge['from'] == start and edge['to'] == end and edge['label'] == 'at' for edge in document['edges']):
                document['edges'].append({'id': uuid.uuid4().hex, 'from': start, 'to': end, 'label': 'at'})
                changed = True
        if not changed:
            return load(conn, row['id'])
        return _write(conn, row, row['title'], graph(conn, document), json.loads(row['viewport_json']))
