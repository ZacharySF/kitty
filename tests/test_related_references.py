"""Synthetic local reference and notes organization regressions."""
import sqlite3

import pytest

from kitty import web_backlinks, web_notes
from kitty.core import backlinks, notes, search
from kitty.web_server import APIError


def make_page(conn, title, content):
    book = conn.execute('SELECT id FROM note_notebooks ORDER BY id LIMIT 1').fetchone()
    nid = book['id'] if book else notes.create_notebook(conn, 'Synthetic notebook')['id']
    page = notes.create_page(conn, nid, title)
    doc = notes.empty_document()
    doc['elements'] = [{'id': 'text', 'kind': 'text', 'layer': 'default', 'x': 0, 'y': 0,
                        'w': 100, 'h': 100, 'color': '#123456', 'width': 1, 'text': content}]
    notes.save_page(conn, page['id'], page['revision'], doc, title)
    return notes.page(conn, page['id'])


def test_related_links_both_directions_mentions_and_exclusion(workspace):
    conn = workspace.conn
    source = make_page(conn, 'Cell energy', 'ATP powers cells')
    forward = make_page(conn, 'Mitochondria', 'Respiration produces ATP')
    reverse = make_page(conn, 'Respiration', 'See [[Cell energy]]')
    mention = make_page(conn, 'Membrane transport', 'ATP pumps')
    result = backlinks.related(conn, '[[Mitochondria]] and membrane transport', f"note_page:{source['id']}")
    assert {ref['id'] for ref in result['linked']} == {forward['id'], reverse['id']}
    assert mention['id'] in {ref['id'] for ref in result['unlinked']}
    assert source['id'] not in {ref['id'] for group in result.values() for ref in group}


def test_related_ranking_is_deterministic_and_bounded(workspace):
    conn = workspace.conn
    best = make_page(conn, 'Exact concepts', 'mitochondrial respiration ATP')
    make_page(conn, 'Broad concepts', 'mitochondrial respiration ATP chlorophyll photosynthesis sunlight chloroplast')
    for i in range(15):
        make_page(conn, f'Example {i}', 'mitochondrial biology')
    result = backlinks.related(conn, 'mitochondrial respiration ATP')
    assert result == backlinks.related(conn, 'mitochondrial respiration ATP')
    assert result['related'][0]['id'] == best['id']
    assert len(result['related']) == 12
    assert result['related'][0]['shared_terms']


def test_related_without_the_index_and_validation():
    conn = sqlite3.connect(':memory:')
    with pytest.raises(search.SearchUnavailable):
        backlinks.related(conn, 'cell energy')
    for text in [None, 42, 'x' * 20001]:
        with pytest.raises(ValueError):
            backlinks.related(conn, text)
    with pytest.raises(ValueError):
        backlinks.related(conn, 'energy', 'bogus')
    with pytest.raises(APIError):
        web_backlinks.post(None, conn, '/api/backlinks/related', {'text': 'ok', 'unknown': True})
    conn.close()


def test_daily_resolve_templates_tags_favorites_and_order(workspace):
    conn = workspace.conn
    daily = notes.daily_page(conn, '2026-01-02')
    assert notes.daily_page(conn, '2026-01-02')['id'] == daily['id']
    assert notes.resolve_page(conn, daily['title'])['id'] == daily['id']
    other = make_page(conn, 'Synthetic tagged page', 'A definition')
    doc = other['document']
    doc['background'] = 'cornell'
    doc['elements'][0].update(tags=['todo', 'definition'], checked=True)
    notes.save_page(conn, other['id'], other['revision'], doc, other['title'])
    assert notes.page(conn, other['id'])['document']['background'] == 'cornell'
    assert notes.tags_summary(conn)['tags'][0]['checked'] is True
    organized = notes.organize_page(conn, other['id'], {'favorite': True, 'visited': True})
    assert organized['favorite'] and organized['last_opened']
    notes.reorder(conn, 'pages', [other['id'], daily['id']])
    assert notes.listing(conn)['pages'][0]['id'] == other['id']
    assert web_notes.post(None, conn, '/api/notes/daily', {'date': '2026-01-02'})['id'] == daily['id']
    with pytest.raises(notes.NotesError):
        notes.daily_page(conn, '2026-02-30')
    with pytest.raises(notes.NotesError):
        notes.reorder(conn, 'pages', [daily['id'], daily['id']])

def test_flashcard_fields_are_indexed_and_exact_navigation_works(workspace):
    from kitty.core import flashcards
    conn = workspace.conn
    flashcards.bootstrap(conn)
    deck = flashcards.save_deck(conn, {"name": "Synthetic biology"})
    typ = conn.execute("SELECT id FROM flash_types WHERE kind='basic' ORDER BY id LIMIT 1").fetchone()
    card = flashcards.save_note(conn, {"deck_id": deck["id"], "type_id": typ["id"],
                                     "fields": {"Front": "Respiration", "Back": "Mitochondrial ATP synthesis"}})
    result = backlinks.related(conn, "mitochondrial ATP synthesis")
    assert any(ref["kind"] == "flashcard_note" and ref["id"] == card["id"] for ref in result["related"])
    assert not any(ref["kind"] == "flashcard_note" and ref["id"] == card["id"]
                   for group in backlinks.related(conn, "mitochondrial ATP synthesis", f"flashcard_note:{card['id']}").values()
                   for ref in group)
    assert flashcards.browser(conn, search=f"nid:{card['id']}")["notes"][0]["id"] == card["id"]
