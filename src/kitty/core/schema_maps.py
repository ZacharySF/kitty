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
#: Theme graph palette slots (--c1..--c6) a node may be painted with; absent means its automatic branch colour.
COLORS = {'c1', 'c2', 'c3', 'c4', 'c5', 'c6'}
MAX_MAPS = 100
MAX_NODES = 300
MAX_EDGES = 600
#: Pen ink on a map: each stroke is a list of [x, y, pressure] points relative to the node it is attached to.
MAX_STROKES = 400
MAX_STROKE_POINTS = 1000
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
    _fields(value, {'nodes', 'edges', 'strokes'})
    nodes, edges, strokes = value.get('nodes'), value.get('edges'), value.get('strokes', [])
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES or not isinstance(edges, list) or len(edges) > MAX_EDGES:
        raise ValueError(f'Maps support at most {MAX_NODES} nodes and {MAX_EDGES} connections')
    if not isinstance(strokes, list) or len(strokes) > MAX_STROKES:
        raise ValueError(f'Maps support at most {MAX_STROKES} ink strokes')
    result = {'nodes': [], 'edges': []}
    ids, sources = set(), set()
    for node in nodes:
        _fields(node, {'id', 'kind', 'label', 'notes', 'x', 'y', 'url', 'source', 'collapsed', 'map_id', 'free', 'color'})
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
        # A free node keeps its own position instead of following the automatic tree layout.
        if not isinstance(node.get('free', False), bool):
            raise ValueError('Free must be true or false')
        color = node.get('color')
        if color is not None and (not isinstance(color, str) or color not in COLORS):
            raise ValueError('Colour must be one of the theme palette slots c1–c6')
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
                                **({'free': True} if node.get('free') else {}),
                                **({'color': color} if color is not None else {}),
                                **({'map_id': linked_map} if linked_map is not None else {})})
    edge_ids = set()
    for edge in edges:
        _fields(edge, {'id', 'from', 'to', 'label', 'kind'})
        key = _key(edge.get('id'))
        start, end = edge.get('from'), edge.get('to')
        if not isinstance(start, str) or not isinstance(end, str) or start not in ids or end not in ids:
            raise ValueError('Every connection endpoint must be a node in this map')
        if key in edge_ids:
            raise ValueError('Connection IDs must be unique')
        edge_ids.add(key)
        # A cross connection links any two nodes outside the tree; without a kind, an edge may shape the tree.
        edge_kind = edge.get('kind', 'tree')
        if not isinstance(edge_kind, str) or edge_kind not in {'tree', 'cross'}:
            raise ValueError('Connection kind must be tree or cross')
        result['edges'].append({'id': key, 'from': start, 'to': end, 'label': _text(edge.get('label', ''), 'Connection label', 200),
                                **({'kind': 'cross'} if edge_kind == 'cross' else {})})
    stroke_ids, inked = set(), []
    for stroke in strokes:
        # Ink belongs to one node and is stored relative to it, so it moves and folds with that node.
        _fields(stroke, {'id', 'node', 'points'})
        key = _key(stroke.get('id'))
        if key in stroke_ids:
            raise ValueError('Ink stroke IDs must be unique')
        stroke_ids.add(key)
        if stroke.get('node') not in ids:
            raise ValueError('Ink must be attached to a node in this map')
        points = stroke.get('points')
        if not isinstance(points, list) or not 1 <= len(points) <= MAX_STROKE_POINTS:
            raise ValueError(f'Ink strokes need 1 to {MAX_STROKE_POINTS} points')
        clean = []
        for point in points:
            if not isinstance(point, list) or len(point) != 3:
                raise ValueError('Ink points must be [x, y, pressure]')
            clean.append([_number(point[0], 'Ink x', -100000, 100000), _number(point[1], 'Ink y', -100000, 100000),
                          _number(point[2], 'Pressure', 0, 1)])
        inked.append({'id': key, 'node': stroke['node'], 'points': clean})
    if inked:
        result['strokes'] = inked
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
    previous = json.loads(row['previous_json']) if row['previous_json'] else {}
    return {'id': row['id'], 'title': row['title'], 'mode': row['mode'], 'revision': row['revision'],
            'graph': document, 'viewport': json.loads(row['viewport_json']),
            'can_undo': bool(previous) and not previous.get('redo', False), 'can_redo': previous.get('redo', False),
            'paired_history': 'partner' in previous,
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


def _branch(document, root):
    if root not in {node['id'] for node in document['nodes']}:
        raise ValueError('Branch root not found')
    parents = {}
    for edge in document['edges']:
        if edge.get('kind') != 'cross' and edge['from'] != edge['to']:
            parents.setdefault(edge['to'], edge['from'])
    picked = {root}
    while True:
        added = {key for key, parent in parents.items() if parent in picked} - picked
        if not added:
            return picked
        picked |= added


def transfer(conn, body):
    """Atomically split a branch or move a root into another saved map."""
    _fields(body, {'id', 'revision', 'node_id', 'target_id', 'target_revision'})
    with transaction(conn, immediate=True):
        row = _row(conn, body.get('id'))
        _revision(row, body.get('revision'))
        source = json.loads(row['graph_json'])
        picked = _branch(source, body.get('node_id'))
        root = next(node for node in source['nodes'] if node['id'] == body['node_id'])
        splitting = body.get('target_id') is None
        if splitting:
            if conn.execute('SELECT count(*) FROM schema_maps').fetchone()[0] >= MAX_MAPS:
                raise ValueError('At most 100 maps are supported')
            cursor = conn.execute('INSERT INTO schema_maps(title,mode,graph_json,viewport_json,updated_at) VALUES(?,?,?,?,?)',
                (root['label'], row['mode'], '{"nodes":[],"edges":[]}', row['viewport_json'], datetime.now(timezone.utc).isoformat()))
            other = _row(conn, cursor.lastrowid)
        else:
            other = _row(conn, body['target_id'])
            if other['id'] == row['id']:
                raise ValueError('Choose another map')
            _revision(other, body.get('target_revision'))
            if any(edge['to'] == root['id'] and edge['from'] != root['id'] and edge.get('kind') != 'cross' for edge in source['edges']):
                raise ValueError('Only a root can be dropped into another map')
        destination = json.loads(other['graph_json'])
        remap = {key: uuid.uuid4().hex for key in picked}
        moved = [{**node, 'id': remap[node['id']]} for node in source['nodes'] if node['id'] in picked]
        for node in moved:
            if node.get('map_id') == other['id']:
                node.pop('map_id')
        if splitting:
            next(node for node in moved if node['id'] == remap[root['id']])['map_id'] = row['id']
        existing = list(destination['nodes'])
        placeholder = next((node for node in existing if node.get('map_id') == row['id']
                            and node['label'] == root['label']), None) if not splitting else None
        if placeholder:
            destination['nodes'].remove(placeholder)
            existing.remove(placeholder)
            for edge in destination['edges']:
                for endpoint in ('from', 'to'):
                    if edge[endpoint] == placeholder['id']:
                        edge[endpoint] = remap[root['id']]
            for stroke in destination.get('strokes', []):
                if stroke['node'] == placeholder['id']:
                    stroke['node'] = remap[root['id']]
        anchor = placeholder or (existing[0] if existing else None)
        if anchor:
            x = anchor['x'] if placeholder else anchor['x'] + 300
            y = anchor['y'] if placeholder else max(node['y'] for node in existing) + 90
            for node in moved:
                node['x'] = max(-100000, min(100000, node['x'] + x - root['x']))
                node['y'] = max(-100000, min(100000, node['y'] + y - root['y']))
        destination['nodes'] += moved
        destination['edges'] += [{**edge, 'id': uuid.uuid4().hex, 'from': remap[edge['from']], 'to': remap[edge['to']]}
                                 for edge in source['edges'] if edge['from'] in picked and edge['to'] in picked]
        # Ink travels with the nodes it is attached to.
        destination['strokes'] = destination.get('strokes', []) + [
            {**stroke, 'id': uuid.uuid4().hex, 'node': remap[stroke['node']]}
            for stroke in source.get('strokes', []) if stroke['node'] in picked]
        source['strokes'] = [stroke for stroke in source.get('strokes', []) if stroke['node'] not in picked]
        if not splitting and existing and not placeholder:
            parent = next((node for node in existing if not any(
                e['to'] == node['id'] and e.get('kind') != 'cross' for e in destination['edges'])), existing[0])
            destination['edges'].append({'id': uuid.uuid4().hex, 'from': parent['id'], 'to': remap[root['id']], 'label': ''})
        source['nodes'] = [node for node in source['nodes'] if node['id'] not in picked or (splitting and node['id'] == root['id'])]
        if splitting:
            root['map_id'] = other['id']
        source['edges'] = [edge for edge in source['edges'] if
                           (edge['from'] not in picked and edge['to'] not in picked) or
                           (splitting and edge['to'] == root['id'] and edge['from'] not in picked)]
        _write(conn, row, row['title'], graph(conn, source), json.loads(row['viewport_json']))
        _write(conn, other, other['title'], graph(conn, destination), json.loads(other['viewport_json']))
        for own, partner in ((row, other), (other, row)):
            previous = json.loads(_snapshot(own))
            previous['partner'] = {'id': partner['id'], 'revision': partner['revision'] + 1,
                                   'snapshot': json.loads(_snapshot(partner))}
            conn.execute('UPDATE schema_maps SET previous_json=? WHERE id=?', (json.dumps(previous), own['id']))
        return load(conn, other['id'])


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
    _fields(body, {'id', 'revision', 'redo'})
    with transaction(conn, immediate=True):
        row = _row(conn, body.get('id'))
        _revision(row, body.get('revision'))
        if row['previous_json'] is None:
            raise ValueError('No saved change to undo')
        previous = json.loads(row['previous_json'])
        if bool(body.get('redo', False)) != previous.get('redo', False):
            raise ValueError('No saved change to redo' if body.get('redo') else 'No saved change to undo')
        if 'partner' in previous:
            partner = _row(conn, previous['partner']['id'])
            _revision(partner, previous['partner']['revision'])
            own_before, partner_before = json.loads(_snapshot(row)), json.loads(_snapshot(partner))
            for own, saved, other, other_before in (
                (row, previous, partner, partner_before),
                (partner, previous['partner']['snapshot'], row, own_before),
            ):
                reverse = json.loads(_snapshot(own))
                reverse.update(redo=not previous.get('redo', False), partner={
                    'id': other['id'], 'revision': other['revision'] + 1, 'snapshot': other_before})
                conn.execute('''UPDATE schema_maps SET title=?,mode=?,graph_json=?,viewport_json=?,previous_json=?,
                    revision=revision+1,updated_at=? WHERE id=?''',
                    (*[saved[key] for key in ('title', 'mode', 'graph_json', 'viewport_json')],
                     json.dumps(reverse), datetime.now(timezone.utc).isoformat(), own['id']))
            return load(conn, row['id'])
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
