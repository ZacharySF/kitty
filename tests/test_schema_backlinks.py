"""Deterministic local backlinks: matching boundaries, sources, bounds and safe linking."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from kitty import web_schema
from kitty.core import backlinks, jobs, notes, schema_maps as schema, search
from kitty.web_server import APIError

STAMP = '2026-01-01T00:00:00+00:00'


def node(key, label, **extra):
    return {'id': key, 'kind': 'topic', 'label': label, 'notes': '', 'x': 0, 'y': 0,
            'url': '', 'source': None, **extra}


def page(conn, title, *texts, kind='text'):
    notebook = conn.execute('SELECT id FROM note_notebooks LIMIT 1').fetchone()
    notebook_id = notebook[0] if notebook else notes.create_notebook(conn, 'Synthetic')['id']
    created = notes.create_page(conn, notebook_id, title)
    document = notes.empty_document()
    document['elements'] = [{'id': f'e{index}', 'kind': kind, 'layer': 'default', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
                             'color': '#000000', 'width': 1, **({'text': text} if kind in {'text', 'sticky'} else {})}
                            for index, text in enumerate(texts)]
    notes.save_page(conn, created['id'], created['revision'], document, title)
    return created['id']


def mapped(conn, *nodes):
    item = schema.create(conn, {'title': 'Mentions', 'mode': 'study'})
    return schema.save(conn, {'id': item['id'], 'revision': item['revision'],
                              'graph': {'nodes': list(nodes), 'edges': []}, 'viewport': item['viewport']})


def refs(result, node_id):
    return next((s['references'] for s in result['suggestions'] if s['node_id'] == node_id), [])


def test_phrases_ignore_short_labels_stopwords_and_match_plurals_on_word_boundaries(workspace):
    conn = workspace.conn
    assert backlinks.phrase('AI') is None and backlinks.phrase('The') is None and backlinks.phrase('  ') is None
    assert backlinks.phrase('Neural Networks') == backlinks.phrase('neural-network')
    assert backlinks.phrase('Class') == backlinks.phrase('classes')
    assert backlinks.phrase('Study') == backlinks.phrase('studies')
    hit = page(conn, 'Week 2', 'Reviewed neural-networks and a matrix')
    page(conn, 'Week 3', 'Unneural networking matrixes')
    item = mapped(conn, node('nn', 'Neural network'), node('short', 'AI'), node('stop', 'the'))
    result = backlinks.suggestions(conn, item['id'])
    assert [ref['id'] for ref in refs(result, 'nn')] == [hit]
    assert refs(result, 'short') == [] and refs(result, 'stop') == []
    reference = refs(result, 'nn')[0]
    assert reference == {**reference, 'kind': 'note_page', 'title': 'Week 2', 'view': 'notes', 'match': 'mention', 'linked': False}
    assert 'neural-networks' in reference['snippet']


def test_sources_cover_study_calendar_jobs_and_flashcards(workspace):
    conn = workspace.conn
    subject = conn.execute("INSERT INTO study_subjects(name) VALUES('Linear Algebra')").lastrowid
    project = conn.execute("INSERT INTO study_projects(name) VALUES('Linear algebra problem set')").lastrowid
    task = conn.execute("INSERT INTO study_tasks(title,checklist_json) VALUES('Homework',?)",
                        (json.dumps([{'id': 'a', 'text': 'Eigenvalues for linear algebra', 'done': False}]),)).lastrowid
    conn.execute("""INSERT INTO calendar_events(id,calendar_id,title,start,end,timezone,data,updated_at)
        VALUES('evt1','personal','Exam','2026-02-01','2026-02-02','UTC',?,?)""", (json.dumps({'description': 'Linear algebra midterm'}), STAMP))
    conn.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES('tsk1',?,?)",
                 (json.dumps({'title': 'Read', 'description': 'linear algebras chapter'}), STAMP))
    deck = conn.execute("INSERT INTO flash_decks(name,settings,created_at) VALUES('Math','{}',?)", (STAMP,)).lastrowid
    kind = conn.execute("""INSERT INTO flash_types(name,kind,fields,templates) VALUES('Synthetic basic','basic',?,'[]')""",
                        (json.dumps(['Front', 'Back']),)).lastrowid
    conn.execute("INSERT INTO flash_notes(guid,deck_id,type_id,fields,created_at,updated_at) VALUES('g',?,?,?,?,?)",
                 (deck, kind, json.dumps({'Front': '<b>Linear</b> algebra basis', 'Back': 'span'}), STAMP, STAMP))
    job = jobs.upsert_job(conn, company='Example Company', title='Linear Algebra Tutor', url='https://example.invalid/tutor')[0].id
    conn.commit()
    result = backlinks.mentions(conn, 'linear algebra')
    found = {(ref['kind'], ref['id']): ref['view'] for ref in result['references']}
    assert found == {('study_subject', subject): 'study', ('study_project', project): 'study', ('study_task', task): 'study',
                     ('calendar_event', 'evt1'): 'calendar', ('calendar_task', 'tsk1'): 'calendar',
                     ('flashcard_deck', deck): 'flashcards', ('job', job): 'jobs'}
    assert result['scanned']['job'] == 1 and result['scanned']['flashcard_deck'] == 2
    excluded = backlinks.mentions(conn, 'linear algebra', f'study_subject:{subject}')
    assert ('study_subject', subject) not in {(ref['kind'], ref['id']) for ref in excluded['references']}
    assert backlinks.mentions(conn, 'of')['references'] == []


def test_wikilinks_rank_first_and_sticky_text_counts(workspace):
    conn = workspace.conn
    plain = page(conn, 'Plain', 'Some thermodynamics here')
    explicit = page(conn, 'Linked', 'See [[Thermodynamics|heat]] for detail', kind='sticky')
    page(conn, 'Shape', 'thermodynamics', kind='rect')
    item = mapped(conn, node('thermo', 'Thermodynamics'))
    found = refs(backlinks.suggestions(conn, item['id']), 'thermo')
    assert [(ref['id'], ref['match']) for ref in found] == [(explicit, 'wikilink'), (plain, 'mention')]


def test_a_database_without_the_search_index_says_so_instead_of_finding_nothing():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE study_subjects(id INTEGER PRIMARY KEY, name TEXT)')
    conn.execute("INSERT INTO study_subjects(name) VALUES('Organic chemistry')")
    with pytest.raises(search.SearchUnavailable):
        backlinks.mentions(conn, 'organic chemistry')


def test_every_record_is_searched_only_the_shown_references_are_bounded_and_snippets_redacted(workspace):
    conn = workspace.conn
    for index in range(30):
        page(conn, f'Lecture {index}', f'Topology notes {index}')
    secret = page(conn, 'Paths', 'Saved at /home/someone/private/file.txt and https://example.invalid/x?token=1 '
                  'mail someone@example.invalid key abcdefabcdef0123456789abcdef — topology')
    page(conn, 'Long', 'x ' * 8000 + 'topology')
    item = mapped(conn, node('topo', 'Topology'))
    result = backlinks.suggestions(conn, item['id'])
    found = refs(result, 'topo')
    assert len(found) == backlinks.PER_NODE and result['suggestions'][0]['truncated'] is True
    assert result['scanned']['note_page'] == 32
    assert all(len(ref['snippet']) <= backlinks.SNIPPET + 60 for ref in found)
    mentioned = backlinks.mentions(conn, 'topology')
    assert len(mentioned['references']) == backlinks.PER_TERM and mentioned['truncated'] is True
    text = next(ref['snippet'] for ref in mentioned['references'] if ref['id'] == secret)
    for leaked in ('/home/someone', 'example.invalid', 'someone@', 'abcdefabcdef0123'):
        assert leaked not in text
    # No per-record character cap: the match 16,000 characters into a block still counts.
    assert 'Long' in {ref['title'] for ref in mentioned['references']}
    assert backlinks.mentions(conn, 'lecture 3')['truncated'] is False


def test_apply_links_once_reuses_sourced_nodes_and_rejects_stale_revisions(workspace):
    conn = workspace.conn
    note = page(conn, 'Graph theory intro', 'Graph theory and a calculus refresher')
    subject = conn.execute("INSERT INTO study_subjects(name) VALUES('Calculus')").lastrowid
    conn.commit()
    item = mapped(conn, node('graphs', 'Graph theory'), node('calc', 'Calculus', kind='class', source={'kind': 'subject', 'id': subject}))
    suggested = backlinks.suggestions(conn, item['id'])
    assert {ref['kind'] for ref in refs(suggested, 'calc')} == {'note_page'}  # its own subject is not a backlink
    app = SimpleNamespace(_no_query=lambda query: None)
    linked = web_schema.post(app, conn, '/api/schema/backlinks/apply',
                             {'map_id': item['id'], 'revision': item['revision'], 'node_ids': ['graphs', 'calc']})
    target = backlinks.target_id('note_page', note)
    added = next(n for n in linked['graph']['nodes'] if n['id'] == target)
    assert added['kind'] == 'topic' and added['label'] == 'Graph theory intro' and added['url'] == ''
    assert added['notes'] == 'Linked from note page: Graph theory intro'
    assert sorted((e['from'], e['to'], e['label']) for e in linked['graph']['edges']) == [
        ('calc', target, 'mentioned in'), ('graphs', target, 'mentioned in')]
    assert linked['revision'] == item['revision'] + 1
    again = backlinks.apply(conn, {'map_id': item['id'], 'revision': linked['revision'], 'node_ids': ['graphs', 'calc']})
    assert again['revision'] == linked['revision'] and again['graph'] == linked['graph']
    assert all(ref['linked'] for s in backlinks.suggestions(conn, item['id'])['suggestions'] for ref in s['references'])
    with pytest.raises(APIError) as caught:
        web_schema.post(app, conn, '/api/schema/backlinks/apply',
                        {'map_id': item['id'], 'revision': item['revision'], 'node_ids': ['graphs']})
    assert caught.value.status == 409
    with pytest.raises(APIError) as caught:
        web_schema.post(app, conn, '/api/schema/backlinks/apply',
                        {'map_id': item['id'], 'revision': linked['revision'], 'node_ids': ['missing']})
    assert caught.value.status == 400


def test_apply_links_tracked_jobs_to_existing_sourced_node(workspace):
    conn = workspace.conn
    job = jobs.upsert_job(conn, company='Example Company', title='Compiler Engineer', url='https://example.invalid/c')[0].id
    conn.commit()
    item = schema.create(conn, {'title': 'Career', 'mode': 'jobs'})
    item = schema.import_jobs(conn, {'id': item['id'], 'revision': item['revision'], 'job_ids': [job]})
    item = schema.save(conn, {'id': item['id'], 'revision': item['revision'], 'viewport': item['viewport'],
                              'graph': {'nodes': [*item['graph']['nodes'], node('compilers', 'Compilers')], 'edges': item['graph']['edges']}})
    job_node = next(n['id'] for n in item['graph']['nodes'] if n['source'] == {'kind': 'job', 'id': job})
    linked = backlinks.apply(conn, {'map_id': item['id'], 'revision': item['revision'], 'node_ids': ['compilers']})
    assert len(linked['graph']['nodes']) == len(item['graph']['nodes'])
    assert ('compilers', job_node) in {(e['from'], e['to']) for e in linked['graph']['edges']}


def test_web_backlink_queries(workspace):
    conn = workspace.conn
    page(conn, 'Optics', 'Optics lab')
    item = mapped(conn, node('optics', 'Optics'))
    app = SimpleNamespace(_no_query=lambda query: None)
    by_map = web_schema.get(app, conn, '/api/schema/backlinks', {'map_id': str(item['id'])})
    assert by_map['map_id'] == item['id'] and by_map['suggestions'][0]['label'] == 'Optics'
    by_term = web_schema.get(app, conn, '/api/schema/backlinks', {'term': 'optics'})
    assert by_term['term'] == 'optics' and by_term['references'][0]['title'] == 'Optics'
    for query in ({}, {'map_id': 'x'}, {'map_id': '1', 'term': 'a'}, {'term': 'optics', 'exclude': 'bogus'}):
        with pytest.raises(APIError) as caught:
            web_schema.get(app, conn, '/api/schema/backlinks', query)
        assert caught.value.status == 400
