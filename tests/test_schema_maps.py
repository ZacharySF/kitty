"""Consumer-visible graph persistence, source identity and conflict boundaries."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kitty import web_schema
from kitty.core import jobs, notes, schema_maps as schema
from kitty.web_server import APIError


def node(key, kind='topic', source=None, **extra):
    return {'id': key, 'kind': kind, 'label': key, 'notes': '', 'x': 0, 'y': 0,
            'url': '', 'source': source, **extra}


def create(conn, mode='study'):
    return schema.create(conn, {'title': 'Connections', 'mode': mode})


def save(conn, item, document=None, **extra):
    return schema.save(conn, {'id': item['id'], 'revision': item['revision'],
                              'graph': document if document is not None else item['graph'],
                              'viewport': item['viewport'], **extra})


def connected(conn):
    item = create(conn)
    return save(conn, item, {'nodes': [node('calculus'), node('physics')],
                             'edges': [{'id': 'uses', 'from': 'physics', 'to': 'calculus', 'label': 'uses'}]})


def test_positions_viewport_rename_and_direction_survive_reload(workspace):
    conn = workspace.conn
    item = connected(conn)
    item['graph']['nodes'][0].update(x=-450.5, y=972)
    changed = save(conn, item, title='Topics across classes', viewport={'x': -30, 'y': 65, 'zoom': 0.7})
    loaded = schema.load(conn, item['id'])
    assert loaded['title'] == 'Topics across classes'
    assert loaded['viewport'] == {'x': -30, 'y': 65, 'zoom': 0.7}
    assert loaded['graph']['nodes'][0]['x'] == -450.5
    assert loaded['graph']['edges'][0] == {'id': 'uses', 'from': 'physics', 'to': 'calculus', 'label': 'uses'}
    assert changed['revision'] == item['revision'] + 1


def test_dangling_and_crossmap_endpoints_rejected_atomically(workspace):
    conn = workspace.conn
    first, second = connected(conn), create(conn)
    for target in ('missing', 'physics'):
        with pytest.raises(ValueError, match='endpoint'):
            save(conn, second, {'nodes': [node('local')], 'edges': [{'id': 'bad', 'from': 'local', 'to': target, 'label': ''}]})
    broken = deepcopy(first['graph'])
    broken['nodes'] = broken['nodes'][:1]
    with pytest.raises(ValueError, match='endpoint'):
        save(conn, first, broken)
    assert schema.load(conn, first['id'])['revision'] == first['revision']
    assert schema.load(conn, second['id'])['graph'] == {'nodes': [], 'edges': []}


def test_node_and_incident_edge_deletion_undo_survives_reload(workspace):
    conn = workspace.conn
    before = connected(conn)
    deleted = save(conn, before, {'nodes': [before['graph']['nodes'][0]], 'edges': []})
    reloaded = schema.load(conn, before['id'])
    assert len(reloaded['graph']['nodes']) == 1
    assert reloaded['can_undo']
    restored = schema.undo(conn, {'id': reloaded['id'], 'revision': reloaded['revision']})
    assert restored['graph'] == before['graph']
    assert restored['revision'] == deleted['revision'] + 1
    assert not restored['can_undo']
    with pytest.raises(ValueError, match='No saved change'):
        schema.undo(conn, {'id': restored['id'], 'revision': restored['revision']})


def test_stale_save_delete_undo_and_import_do_not_overwrite(workspace):
    conn = workspace.conn
    old = create(conn, 'jobs')
    latest = save(conn, old, title='Other window')
    for operation, body in ((schema.save, {'graph': old['graph'], 'viewport': old['viewport']}),
                            (schema.delete, {'confirm': True}), (schema.undo, {}),
                            (schema.import_jobs, {'job_ids': [1]})):
        with pytest.raises(schema.SchemaConflict):
            operation(conn, {'id': old['id'], 'revision': old['revision'], **body})
    assert schema.load(conn, old['id'])['title'] == latest['title']
    with pytest.raises(ValueError, match='Confirm'):
        schema.delete(conn, {'id': latest['id'], 'revision': latest['revision']})
    assert schema.delete(conn, {'id': latest['id'], 'revision': latest['revision'], 'confirm': True}) == {'deleted': latest['id']}


def test_class_source_is_live_shared_and_never_recreated(workspace):
    conn = workspace.conn
    subject_id = conn.execute("INSERT INTO study_subjects(name) VALUES('Calculus')").lastrowid
    subject = {'kind': 'subject', 'id': subject_id}
    first, second = create(conn), create(conn, 'jobs')
    first = save(conn, first, {'nodes': [node('class', 'class', subject), node('topic')],
                                'edges': [{'id': 'part', 'from': 'topic', 'to': 'class', 'label': 'part of'}]})
    second = save(conn, second, {'nodes': [node('same-class', 'class', subject)], 'edges': []})
    conn.execute("UPDATE study_subjects SET name='Calculus II' WHERE id=?", (subject_id,))
    assert schema.load(conn, first['id'])['live']['class']['label'] == 'Calculus II'
    assert schema.load(conn, second['id'])['live']['same-class']['label'] == 'Calculus II'
    assert conn.execute('SELECT count(*) FROM study_subjects').fetchone()[0] == 1
    with pytest.raises(ValueError, match='once'):
        save(conn, first, {'nodes': [node('a', 'class', subject), node('b', 'class', subject)], 'edges': []})
    for invalid in ({'kind': 'job', 'id': subject_id}, {'kind': 'subject', 'id': 999999}):
        with pytest.raises(ValueError, match='Source reference|source no longer'):
            save(conn, first, {'nodes': [node('bad', 'class', invalid)], 'edges': []})
    conn.execute('DELETE FROM study_subjects WHERE id=?', (subject_id,))
    assert schema.load(conn, first['id'])['live']['class']['missing'] is True
    with pytest.raises(ValueError, match='source no longer'):
        save(conn, first)
    first['graph']['nodes'][0]['source'] = None
    assert save(conn, first)['graph']['nodes'][0]['source'] is None


def test_selected_jobs_import_idempotently_with_live_status_and_safe_source(workspace):
    conn = workspace.conn
    first, _ = jobs.upsert_job(conn, company='Example Company', title='Developer', url='https://example.invalid/a')
    second, _ = jobs.upsert_job(conn, company='Example Company', title='Analyst', url='https://example.invalid/b')
    item = create(conn, 'jobs')
    body = {'id': item['id'], 'revision': item['revision'], 'job_ids': [first.id, second.id, first.id]}
    imported = schema.import_jobs(conn, body)
    assert sorted(n['kind'] for n in imported['graph']['nodes']) == ['company', 'job', 'job']
    assert len(imported['graph']['edges']) == 2
    assert {e['label'] for e in imported['graph']['edges']} == {'at'}
    again = schema.import_jobs(conn, {**body, 'revision': imported['revision']})
    assert again['revision'] == imported['revision']
    assert again['graph'] == imported['graph']
    job_node = next(n for n in again['graph']['nodes'] if n['source'] == {'kind': 'job', 'id': first.id})
    conn.execute("INSERT INTO applications(job_id,status,applied_at,last_activity_at,created_at,updated_at) VALUES(?,'interview','now','now','now','now')", (first.id,))
    conn.execute("UPDATE jobs SET queue_state='applied',url='javascript:alert(1)' WHERE id=?", (first.id,))
    loaded = schema.load(conn, item['id'])
    assert loaded['live'][job_node['id']]['status'] == 'interview'
    assert loaded['live'][job_node['id']]['queue_state'] == 'applied'
    assert loaded['live'][job_node['id']]['url'] == ''
    assert schema.job_options(conn, 'Developer')['jobs'][0]['status'] == 'interview'
    assert schema.job_options(conn, 'Developer')['jobs'][0]['url'] == ''
    assert not any(n['kind'] == 'skill' for n in loaded['graph']['nodes'])
    with pytest.raises(ValueError, match='jobs-mode'):
        other = create(conn)
        schema.import_jobs(conn, {'id': other['id'], 'revision': other['revision'], 'job_ids': [first.id]})


def test_invalid_job_selection_rolls_back_entire_import(workspace):
    conn = workspace.conn
    job, _ = jobs.upsert_job(conn, company='Example', title='Developer', url='https://example.invalid/job')
    item = create(conn, 'jobs')
    with pytest.raises(ValueError, match='no longer exists'):
        schema.import_jobs(conn, {'id': item['id'], 'revision': item['revision'], 'job_ids': [job.id, 999999]})
    assert schema.load(conn, item['id'])['graph'] == item['graph']
    assert schema.load(conn, item['id'])['revision'] == item['revision']


def test_portable_export_detaches_source_ids_and_import_is_independent(workspace):
    conn = workspace.conn
    source_id = conn.execute("INSERT INTO study_subjects(name) VALUES('Algorithms')").lastrowid
    item = save(conn, create(conn), {'nodes': [node('a', 'class', {'kind': 'subject', 'id': source_id})], 'edges': []})
    document = schema.export(conn, item['id'])
    assert document['graph']['nodes'][0]['source'] is None
    assert document['graph']['nodes'][0]['label'] == 'Algorithms'
    imported = schema.create(conn, {'title': document['title'], 'mode': document['mode'], 'document': document})
    assert imported['id'] != item['id']
    assert imported['live'] == {}
    assert schema.load(conn, item['id'])['graph']['nodes'][0]['source']['id'] == source_id
    document['graph']['nodes'][0]['source'] = {'kind': 'subject', 'id': source_id}
    with pytest.raises(ValueError, match='local source IDs'):
        schema.create(conn, {'title': 'Unsafe rebinding', 'document': document})


@pytest.mark.parametrize('change', [
    {'kind': []}, {'kind': 'unknown'}, {'x': True}, {'x': float('nan')}, {'y': float('inf')},
    {'x': 10**1000}, {'url': 'javascript:alert(1)'}, {'url': 'https://user:pass@example.invalid/'},
    {'url': '//example.invalid/'}, {'label': 'a' * 201}, {'source': {'kind': 'subject', 'id': True}},
])
def test_invalid_node_boundaries_are_clear_validation_errors(workspace, change):
    conn = workspace.conn
    item = create(conn)
    with pytest.raises(ValueError):
        save(conn, item, {'nodes': [node('n', **change)], 'edges': []})
    assert schema.load(conn, item['id'])['revision'] == 1


def test_graph_size_unknown_fields_and_invalid_viewport_are_bounded(workspace):
    conn = workspace.conn
    item = create(conn)
    for document in ({'nodes': [node(str(i)) for i in range(301)], 'edges': []},
                     {'nodes': [node('same'), node('same')], 'edges': []},
                     {'nodes': [node('a')], 'edges': [], 'remote_image': 'https://example.invalid/'},
                     {'nodes': [node(str(i), notes='x' * 10000) for i in range(110)], 'edges': []}):
        with pytest.raises(ValueError):
            save(conn, item, document)
    for view in ({'x': 0, 'y': 0, 'zoom': 0}, {'x': 0, 'y': 0, 'zoom': True}):
        with pytest.raises(ValueError):
            save(conn, item, viewport=view)


def test_web_routes_return_conflicts_and_ignore_other_modules(workspace):
    conn = workspace.conn
    app = SimpleNamespace(_no_query=lambda query: None)
    created = web_schema.post(app, conn, '/api/schema/create', {'title': 'Web graph', 'mode': 'general'})
    assert web_schema.get(app, conn, '/api/schema/map', {'id': str(created['id'])})['title'] == 'Web graph'
    assert web_schema.get(app, conn, '/api/schema', {})['maps'][0]['id'] == created['id']
    save(conn, created)
    with pytest.raises(APIError) as caught:
        web_schema.post(app, conn, '/api/schema/undo', {'id': created['id'], 'revision': created['revision']})
    assert caught.value.status == 409
    with pytest.raises(APIError) as caught:
        web_schema.get(app, conn, '/api/schema/map', {'id': 'bad'})
    assert caught.value.status == 400
    assert web_schema.get(app, conn, '/api/other', {}) is None
    assert web_schema.post(app, conn, '/api/other', {}) is None


def test_add_node_task_identity_revision_parent_and_live_title(workspace):
    conn = workspace.conn
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES(?,?,?)",
                 ('synthetic-task', '{"title":"Review synthetic chapter"}', '2026-01-01'))
    item = connected(conn)
    body = {'map_id': item['id'], 'revision': item['revision'],
            'label': 'Review chapter', 'kind': 'task', 'parent_id': 'physics',
            'source': {'kind': 'calendar_task', 'id': 'synthetic-task'}}
    result = web_schema.post(None, conn, '/api/schema/add-node', body)
    added = result['graph']['nodes'][-1]
    assert added['kind'] == 'task'
    assert result['live'][added['id']]['label'] == 'Review synthetic chapter'
    assert result['graph']['edges'][-1]['to'] == added['id']
    assert result['graph']['edges'][-1]['from'] == 'physics'
    with pytest.raises(APIError) as caught:
        web_schema.post(None, conn, '/api/schema/add-node', body)
    assert caught.value.status == 409
    body['revision'] = result['revision']
    repeated = schema.add_node(conn, body)
    assert repeated['revision'] == result['revision']
    assert len(repeated['graph']['nodes']) == 3
    assert schema.network(conn)['nodes']


def test_add_node_rejects_missing_parent_atomically(workspace):
    item = create(workspace.conn)
    with pytest.raises(ValueError, match='Parent'):
        schema.add_node(workspace.conn, {'map_id': item['id'], 'revision': item['revision'],
                                        'label': 'Synthetic topic', 'parent_id': 'missing'})
    assert schema.load(workspace.conn, item['id'])['revision'] == item['revision']


def test_branch_collapse_reparent_and_map_link_persist(workspace):
    conn = workspace.conn
    item, linked = connected(conn), create(conn)
    item['graph']['nodes'][0].update(collapsed=True, map_id=linked['id'])
    item['graph']['edges'][0].update(**{'from': 'calculus', 'to': 'physics'})
    saved = save(conn, item)
    loaded = schema.load(conn, item['id'])
    assert loaded['graph']['nodes'][0]['collapsed'] is True
    assert loaded['graph']['nodes'][0]['map_id'] == linked['id']
    assert loaded['graph']['edges'][0]['from'] == 'calculus'
    assert 'map_id' not in schema.export(conn, item['id'])['graph']['nodes'][0]
    restored = schema.undo(conn, {'id': item['id'], 'revision': saved['revision']})
    assert not restored['graph']['nodes'][0].get('collapsed', False)


def test_detached_free_branch_position_persists_and_round_trips_portably(workspace):
    conn = workspace.conn
    item = connected(conn)
    item['graph']['nodes'][1].update(free=True, x=640, y=-120)
    item['graph']['edges'] = []
    save(conn, item)
    loaded = schema.load(conn, item['id'])
    assert loaded['graph']['nodes'][1]['free'] is True
    assert (loaded['graph']['nodes'][1]['x'], loaded['graph']['nodes'][1]['y']) == (640, -120)
    assert 'free' not in loaded['graph']['nodes'][0]
    copy = schema.create(conn, {'title': 'Copy', 'mode': 'study', 'document': schema.export(conn, item['id'])})
    assert copy['graph']['nodes'][1]['free'] is True
    with pytest.raises(ValueError, match='Free'):
        save(conn, schema.load(conn, item['id']), {'nodes': [node('n', free='yes')], 'edges': []})


def test_node_palette_colour_persists_and_round_trips_portably(workspace):
    conn = workspace.conn
    item = connected(conn)
    item['graph']['nodes'][0]['color'] = 'c4'
    save(conn, item)
    loaded = schema.load(conn, item['id'])
    assert loaded['graph']['nodes'][0]['color'] == 'c4'
    assert 'color' not in loaded['graph']['nodes'][1]
    copy = schema.create(conn, {'title': 'Copy', 'mode': 'study', 'document': schema.export(conn, item['id'])})
    assert copy['graph']['nodes'][0]['color'] == 'c4'
    for colour in ('red', '#ff0000', 'c7', 4, ''):
        with pytest.raises(ValueError, match='Colour'):
            save(conn, schema.load(conn, item['id']), {'nodes': [node('n', color=colour)], 'edges': []})


def test_cross_connection_kind_persists_and_round_trips_portably(workspace):
    conn = workspace.conn
    item = connected(conn)
    item['graph']['nodes'].append(node('optics'))
    item['graph']['edges'] += [{'id': 'link', 'from': 'optics', 'to': 'calculus', 'label': 'needs', 'kind': 'cross'},
                               {'id': 'branch', 'from': 'physics', 'to': 'optics', 'label': '', 'kind': 'tree'}]
    save(conn, item)
    edges = {edge['id']: edge for edge in schema.load(conn, item['id'])['graph']['edges']}
    assert edges['link'] == {'id': 'link', 'from': 'optics', 'to': 'calculus', 'label': 'needs', 'kind': 'cross'}
    assert 'kind' not in edges['branch'] and 'kind' not in edges['uses']
    copy = schema.create(conn, {'title': 'Copy', 'mode': 'study', 'document': schema.export(conn, item['id'])})
    assert {edge['id']: edge.get('kind') for edge in copy['graph']['edges']}['link'] == 'cross'
    for kind in ('bogus', 1, None):
        with pytest.raises(ValueError, match='Connection kind'):
            save(conn, schema.load(conn, item['id']), {'nodes': [node('a'), node('b')],
                                                       'edges': [{'id': 'x', 'from': 'a', 'to': 'b', 'label': '', 'kind': kind}]})


def test_note_card_source_is_unique_and_live(workspace):
    conn = workspace.conn
    notebook = notes.create_notebook(conn, 'Synthetic notebook')['id']
    page = notes.create_page(conn, notebook, 'Synthetic note card')['id']
    item = create(conn)
    body = {'map_id': item['id'], 'revision': item['revision'], 'label': 'Note card',
            'source': {'kind': 'note_page', 'id': page}}
    saved = schema.add_node(conn, body)
    assert saved['live'][saved['graph']['nodes'][0]['id']]['label'] == 'Synthetic note card'
    body['revision'] = saved['revision']
    assert len(schema.add_node(conn, body)['graph']['nodes']) == 1


def network_map(conn, mode, document, title='Connections'):
    item = schema.create(conn, {'title': title, 'mode': mode})
    return save(conn, item, document)


def test_network_unifies_sources_across_maps_with_live_metadata_and_provenance(workspace):
    conn = workspace.conn
    subject_id = conn.execute("INSERT INTO study_subjects(name) VALUES('Linear Algebra')").lastrowid
    job, _ = jobs.upsert_job(conn, company='Example Company', title='ML Engineer', url='https://example.invalid/ml')
    subject = {'kind': 'subject', 'id': subject_id}
    study = network_map(conn, 'study', {'nodes': [node('la', 'class', subject), node('vectors')],
                                         'edges': [{'id': 'part', 'from': 'vectors', 'to': 'la', 'label': 'part of'}]})
    career = network_map(conn, 'jobs', {'nodes': [node('class', 'class', subject), node('role', 'job', {'kind': 'job', 'id': job.id}),
                                                  node('vectors')],
                                        'edges': [{'id': 'needs', 'from': 'role', 'to': 'class', 'label': 'needs'},
                                                  {'id': 'part', 'from': 'vectors', 'to': 'class', 'label': 'Part  Of'}]})
    conn.execute("UPDATE study_subjects SET name='Linear Algebra II' WHERE id=?", (subject_id,))
    result = web_schema.get(SimpleNamespace(), conn, '/api/schema/network', {})
    by_id = {n['id']: n for n in result['nodes']}
    shared = by_id[f'subject:{subject_id}']
    assert shared['label'] == 'Linear Algebra II'
    assert shared['maps'] == [study['id'], career['id']]
    assert {(m['map_id'], m['node_id']) for m in shared['members']} == {(study['id'], 'la'), (career['id'], 'class')}
    assert shared['degree'] == 3
    assert by_id[f'job:{job.id}']['live']['company'] == 'Example Company'
    assert f"map:{study['id']}:vectors" in by_id and f"map:{career['id']}:vectors" in by_id
    assert len(result['nodes']) == 4
    assert result['unified'] == 1
    assert [s['id'] for s in result['maps']] == [study['id'], career['id']]
    edges = {(e['from'], e['to'], e['label']): e for e in result['edges']}
    assert edges[(f'job:{job.id}', f'subject:{subject_id}', 'needs')]['maps'] == [career['id']]
    assert len(result['edges']) == 3
    only_jobs = schema.network(conn, mode='jobs')
    assert [s['id'] for s in only_jobs['maps']] == [career['id']]
    picked = web_schema.get(SimpleNamespace(), conn, '/api/schema/network', {'map_ids': str(study['id'])})
    assert {n['id'] for n in picked['nodes']} == {f'subject:{subject_id}', f"map:{study['id']}:vectors"}


def test_network_label_merge_is_opt_in_kind_aware_and_weights_edges(workspace):
    conn = workspace.conn
    first = network_map(conn, 'general', {'nodes': [node('a', label='Graph  Theory'), node('b', 'skill', label='Proofs'),
                                                    node('c', 'idea', label='graph theory')],
                                          'edges': [{'id': 'x', 'from': 'b', 'to': 'a', 'label': 'uses'}]})
    second = network_map(conn, 'study', {'nodes': [node('d', label='graph theory'), node('e', 'skill', label='proofs ')],
                                         'edges': [{'id': 'y', 'from': 'e', 'to': 'd', 'label': 'USES'}]})
    separate = schema.network(conn)
    assert len(separate['nodes']) == 5 and separate['unified'] == 0
    assert all(e['weight'] == 1 for e in separate['edges'])
    merged = web_schema.get(SimpleNamespace(), conn, '/api/schema/network', {'merge_labels': 'true'})
    assert merged['merge_labels'] is True
    by_id = {n['id']: n for n in merged['nodes']}
    assert set(by_id) == {'label:topic:graph theory', 'label:skill:proofs', 'label:idea:graph theory'}
    assert by_id['label:topic:graph theory']['maps'] == [first['id'], second['id']]
    assert by_id['label:idea:graph theory']['maps'] == [first['id']]
    [edge] = merged['edges']
    assert edge['weight'] == 2 and edge['maps'] == [first['id'], second['id']]
    assert {m['edge_id'] for m in edge['members']} == {'x', 'y'}


def test_network_keeps_deleted_sources_visible_and_marked_missing(workspace):
    conn = workspace.conn
    subject_id = conn.execute("INSERT INTO study_subjects(name) VALUES('Topology')").lastrowid
    subject = {'kind': 'subject', 'id': subject_id}
    network_map(conn, 'study', {'nodes': [node('t', 'class', subject, label='Topology (saved)')], 'edges': []})
    network_map(conn, 'general', {'nodes': [node('u', 'class', subject, label='Topology')], 'edges': []})
    conn.execute('DELETE FROM study_subjects WHERE id=?', (subject_id,))
    [only] = schema.network(conn, merge_labels=True)['nodes']
    assert only['id'] == f'subject:{subject_id}'
    assert only['live'] == {'missing': True}
    assert only['label'] == 'Topology (saved)'
    assert len(only['members']) == 2
    assert conn.execute('SELECT count(*) FROM study_subjects').fetchone()[0] == 0


def test_network_clusters_are_connected_components_and_deterministic(workspace):
    conn = workspace.conn
    network_map(conn, 'general', {'nodes': [node(k) for k in 'abcdef'],
                                  'edges': [{'id': 'ab', 'from': 'a', 'to': 'b', 'label': ''},
                                            {'id': 'bc', 'from': 'c', 'to': 'b', 'label': ''},
                                            {'id': 'de', 'from': 'd', 'to': 'e', 'label': ''},
                                            {'id': 'loop', 'from': 'f', 'to': 'f', 'label': 'self'}]})
    first, second = schema.network(conn), schema.network(conn)
    assert first == second
    cluster = {n['id'].rsplit(':', 1)[1]: n['cluster'] for n in first['nodes']}
    assert cluster['a'] == cluster['b'] == cluster['c'] == 0
    assert cluster['d'] == cluster['e'] == 1
    assert cluster['f'] == 2
    assert [(c['id'], c['size']) for c in first['clusters']] == [(0, 3), (1, 2), (2, 1)]
    assert first['clusters'][0]['label'] == 'b'
    assert {n['id'].rsplit(':', 1)[1]: n['degree'] for n in first['nodes']}['f'] == 1


def test_network_query_boundaries(workspace):
    conn = workspace.conn
    app = SimpleNamespace()
    item = network_map(conn, 'study', {'nodes': [node('a')], 'edges': []})
    assert schema.network(conn, mode='jobs')['nodes'] == []
    for query in ({'map_ids': 'x'}, {'merge_labels': 'maybe'}, {'mode': 'other'}, {'map_ids': '999999'},
                  {'map_ids': ','.join(str(i) for i in range(1, 102))}, {'map_ids': '0'}, {'unknown': '1'}):
        with pytest.raises(APIError) as caught:
            web_schema.get(app, conn, '/api/schema/network', query)
        assert caught.value.status == 400
    assert web_schema.get(app, conn, '/api/schema/network', {'map_ids': str(item['id']), 'mode': 'jobs'})['nodes'] == []
    for _ in range(schema.MAX_MAPS - 1):
        schema.create(conn, {'title': 'Filler', 'mode': 'general'})
    assert len(schema.network(conn)['maps']) == schema.MAX_MAPS
    with pytest.raises(ValueError, match='At most'):
        schema.create(conn, {'title': 'Too many', 'mode': 'general'})


def test_split_merge_and_paired_undo_redo_are_atomic(workspace):
    conn = workspace.conn
    original = save(conn, create(conn), {'nodes': [node('root'), node('branch'), node('leaf')],
        'edges': [{'id': 'a', 'from': 'root', 'to': 'branch', 'label': ''},
                  {'id': 'b', 'from': 'branch', 'to': 'leaf', 'label': ''}]})
    branch = schema.transfer(conn, {'id': original['id'], 'revision': original['revision'], 'node_id': 'branch'})
    left = schema.load(conn, original['id'])
    assert [n['label'] for n in branch['graph']['nodes']] == ['branch', 'leaf']
    assert [n['label'] for n in left['graph']['nodes']] == ['root', 'branch']
    assert left['graph']['nodes'][1]['map_id'] == branch['id']
    assert branch['graph']['nodes'][0]['map_id'] == original['id']
    undone = schema.undo(conn, {'id': branch['id'], 'revision': branch['revision']})
    assert schema.load(conn, original['id'])['graph'] == original['graph']
    assert undone['graph']['nodes'] == []
    assert undone['can_redo'] and not undone['can_undo']
    redone = schema.undo(conn, {'id': undone['id'], 'revision': undone['revision'], 'redo': True})
    assert redone['graph'] == branch['graph']
    left = schema.load(conn, original['id'])
    merged = schema.transfer(conn, {'id': redone['id'], 'revision': redone['revision'],
        'node_id': redone['graph']['nodes'][0]['id'], 'target_id': left['id'], 'target_revision': left['revision']})
    assert sorted(n['label'] for n in merged['graph']['nodes']) == ['branch', 'leaf', 'root']
    assert len(merged['graph']['edges']) == 2
    assert all(n.get('map_id') != merged['id'] for n in merged['graph']['nodes'])
    assert schema.load(conn, branch['id'])['graph']['nodes'] == []
    restored = schema.undo(conn, {'id': merged['id'], 'revision': merged['revision']})
    assert restored['graph'] == left['graph']
    assert schema.load(conn, branch['id'])['graph'] == branch['graph']


def test_transfer_conflicts_preserve_both_maps(workspace):
    conn = workspace.conn
    source, target = connected(conn), create(conn)
    with pytest.raises(schema.SchemaConflict):
        schema.transfer(conn, {'id': source['id'], 'revision': source['revision'], 'node_id': 'physics',
            'target_id': target['id'], 'target_revision': target['revision'] - 1})
    assert schema.load(conn, source['id'])['graph'] == source['graph']
    assert schema.load(conn, target['id'])['graph'] == target['graph']
    moved = schema.transfer(conn, {'id': source['id'], 'revision': source['revision'], 'node_id': 'physics'})
    changed = save(conn, schema.load(conn, source['id']), title='Another window')
    with pytest.raises(schema.SchemaConflict):
        schema.undo(conn, {'id': moved['id'], 'revision': moved['revision']})
    assert schema.load(conn, source['id'])['title'] == changed['title']
    assert schema.load(conn, moved['id'])['graph'] == moved['graph']


def ink(key, node_id, points=((0, 0, 0.4), (12.5, -3, 0.9))):
    return {'id': key, 'node': node_id, 'points': [list(point) for point in points]}


def test_ink_strokes_attach_to_nodes_and_survive_reload_undo_and_export(workspace):
    conn = workspace.conn
    item = connected(conn)
    document = deepcopy(item['graph'])
    document['strokes'] = [ink('tick', 'calculus'), ink('note', 'physics', ((-4, 50.25, 0), (3, 2, 1), (9, 9, 0.5)))]
    saved = save(conn, item, document)
    loaded = schema.load(conn, item['id'])
    assert loaded['graph']['strokes'] == document['strokes'] == saved['graph']['strokes']
    assert schema.export(conn, item['id'])['graph']['strokes'] == document['strokes']
    # Erasing every stroke stores no ink at all, and undo brings the strokes back.
    erased = save(conn, saved, {**saved['graph'], 'strokes': []})
    assert 'strokes' not in erased['graph']
    restored = schema.undo(conn, {'id': erased['id'], 'revision': erased['revision']})
    assert restored['graph']['strokes'] == document['strokes']


@pytest.mark.parametrize('stroke, message', [
    (ink('lost', 'missing'), 'Ink'),
    ({'id': 'bad', 'node': 'calculus', 'points': []}, 'Ink'),
    ({'id': 'bad', 'node': 'calculus', 'points': [[0, 0]]}, 'Ink'),
    ({'id': 'bad', 'node': 'calculus', 'points': [[0, 0, 1.5]]}, 'Pressure'),
    ({'id': 'bad', 'node': 'calculus', 'points': [[0, 'x', 0.5]]}, 'Ink'),
    ({'id': 'bad', 'node': 'calculus', 'points': [[0, 0, 0.5]] * (schema.MAX_STROKE_POINTS + 1)}, 'Ink'),
    ({**ink('bad', 'calculus'), 'colour': 'red'}, 'Unknown'),
])
def test_invalid_ink_is_rejected_atomically(workspace, stroke, message):
    conn = workspace.conn
    item = connected(conn)
    with pytest.raises(ValueError, match=message):
        save(conn, item, {**item['graph'], 'strokes': [stroke]})
    with pytest.raises(ValueError, match='unique'):
        save(conn, item, {**item['graph'], 'strokes': [ink('twice', 'calculus'), ink('twice', 'physics')]})
    with pytest.raises(ValueError, match='at most'):
        save(conn, item, {**item['graph'], 'strokes': [ink(f's{i}', 'calculus') for i in range(schema.MAX_STROKES + 1)]})
    assert schema.load(conn, item['id'])['revision'] == item['revision']


def test_ink_travels_with_a_split_branch_and_back_on_merge(workspace):
    conn = workspace.conn
    original = save(conn, create(conn), {'nodes': [node('root'), node('branch'), node('leaf')],
        'edges': [{'id': 'a', 'from': 'root', 'to': 'branch', 'label': ''},
                  {'id': 'b', 'from': 'branch', 'to': 'leaf', 'label': ''}],
        'strokes': [ink('on-root', 'root'), ink('on-leaf', 'leaf'), ink('on-branch', 'branch')]})
    split = schema.transfer(conn, {'id': original['id'], 'revision': original['revision'], 'node_id': 'branch'})
    left = schema.load(conn, original['id'])
    assert [s['id'] for s in left['graph']['strokes']] == ['on-root']
    labels = {n['id']: n['label'] for n in split['graph']['nodes']}
    assert sorted(labels[s['node']] for s in split['graph']['strokes']) == ['branch', 'leaf']
    merged = schema.transfer(conn, {'id': split['id'], 'revision': split['revision'],
        'node_id': split['graph']['nodes'][0]['id'], 'target_id': left['id'], 'target_revision': left['revision']})
    labels = {n['id']: n['label'] for n in merged['graph']['nodes']}
    assert sorted(labels[s['node']] for s in merged['graph']['strokes']) == ['branch', 'leaf', 'root']
    assert 'strokes' not in schema.load(conn, split['id'])['graph']
