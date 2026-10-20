"""Behavior invariants for portable local documents and imports."""
from __future__ import annotations

import base64
import io
import sqlite3
import threading
from types import SimpleNamespace

import pymupdf
import pytest
from pypdf import PdfReader

from kitty.core import documents, note_blocks, notes
from kitty.db import connect
from kitty.db.migrations import available_migrations
from kitty import web_documents
from kitty.web_tasks import TaskRunner


def upload(name, data, path=None):
    return {'name': name, 'data': base64.b64encode(data).decode(), **({'path': path} if path else {})}


def test_document_revision_and_readable_pdf(workspace):
    conn = workspace.conn
    body = {'title': 'Energy overview', 'content': '# ATP synthesis\n\nMitochondria produce energy.\n<script>alert(1)</script>', 'format': 'markdown', 'notebook_id': None}
    saved = documents.save(conn, body)
    data, kind, name = documents.pdf(conn, saved['id'])
    assert kind == 'application/pdf'
    assert name == 'Energy-overview.pdf'
    reader = PdfReader(io.BytesIO(data))
    extracted = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'ATP synthesis' in extracted and 'Mitochondria produce energy.' in extracted
    assert not any(page.get('/AA') for page in reader.pages)
    updated = documents.save(conn, {**body, 'content': 'Updated source', 'revision': saved['revision']}, saved['id'])
    assert updated['revision'] == 2
    with pytest.raises(notes.NotesError) as stale:
        documents.save(conn, {**body, 'revision': 1}, saved['id'])
    assert stale.value.status == 409
    with connect(workspace.settings.db_path) as reloaded:
        assert documents.document(reloaded, saved['id'])['content'] == 'Updated source'


def test_pdf_import_extracts_real_blocks_matches_folder_and_retains_original(workspace, tmp_path):
    conn = workspace.conn
    target = notes.create_folder(conn, 'Biology')
    other = notes.create_folder(conn, 'Other')
    book = notes.create_notebook(conn, 'Cells', folder_id=target['id'])
    outside = notes.create_notebook(conn, 'Unrelated folder', folder_id=other['id'])
    wanted = notes.create_page(conn, book['id'], 'Mitochondria energy synthesis')
    excluded = notes.create_page(conn, outside['id'], 'Mitochondria energy synthesis outside')
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), 'Mitochondria energy synthesis produces ATP.')
    raw = pdf.tobytes()
    pdf.close()
    imported = documents.import_notes(conn, tmp_path, {'files': [upload('Lecture.pdf', raw)], 'notebook_id': book['id']})['pages'][0]
    blocks = note_blocks.get_blocks(conn, imported['page_id'])['blocks']
    assert 'Mitochondria energy synthesis' in '\n'.join(block['text'] for block in blocks)
    assert notes.page(conn, imported['page_id'])['notebook_id'] == book['id']
    matched = {item['page_id'] for item in imported['matches']}
    assert wanted['id'] in matched and excluded['id'] not in matched
    data, kind, name = documents.original(conn, tmp_path, imported['original_id'])
    assert data == raw and kind == 'application/octet-stream' and name == 'Lecture.pdf'
    path = documents.original_root(tmp_path) / imported['original_id']
    assert path.stat().st_mode & 0o077 == 0
    assert notes.page(conn, wanted['id'])['revision'] == 1
    original = conn.execute('SELECT page_id,document_id FROM document_originals WHERE id=?', (imported['original_id'],)).fetchone()
    assert original['page_id'] == imported['page_id'] and original['document_id'] is None


def test_folder_import_preserves_relative_hierarchy_without_overwriting(workspace, tmp_path):
    conn = workspace.conn
    target = notes.create_folder(conn, 'Destination')
    files = [upload('lecture.md', b'# Biology\n\nCell energy', 'Export/Biology/lecture.md')]
    first = documents.import_notes(conn, tmp_path, {'files': files, 'folder_id': target['id']})['pages'][0]
    second = documents.import_notes(conn, tmp_path, {'files': files, 'folder_id': target['id']})['pages'][0]
    assert first['page_id'] != second['page_id']
    book = conn.execute('SELECT folder_id FROM note_notebooks WHERE id=?', (first['notebook_id'],)).fetchone()
    child = conn.execute('SELECT * FROM note_folders WHERE id=?', (book['folder_id'],)).fetchone()
    parent = conn.execute('SELECT * FROM note_folders WHERE id=?', (child['parent_id'],)).fetchone()
    assert child['title'] == 'Biology' and parent['title'] == 'Export'
    assert parent['parent_id'] == target['id']
    assert notes.page(conn, first['page_id'])['search_text'].endswith('Cell energy')


@pytest.mark.parametrize('file', [upload('a.exe', b'data'), upload('a.md', b'text', '../a.md'), upload('a.md', b'text', '/a.md'), upload('a.pdf', b'not a pdf')])
def test_invalid_batch_never_creates_partial_notes(workspace, tmp_path, file):
    conn = workspace.conn
    before = conn.execute('SELECT COUNT(*) FROM note_pages').fetchone()[0]
    with pytest.raises(notes.NotesError):
        documents.import_notes(conn, tmp_path, {'files': [upload('valid.md', b'Valid text'), file], 'folder_id': None})
    assert conn.execute('SELECT COUNT(*) FROM note_pages').fetchone()[0] == before


def test_encrypted_and_textless_pdfs_rejected(workspace, tmp_path):
    for encrypted in (False, True):
        pdf = pymupdf.open()
        pdf.new_page()
        raw = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader') if encrypted else pdf.tobytes()
        pdf.close()
        with pytest.raises(notes.NotesError):
            documents.import_notes(workspace.conn, tmp_path, {'files': [upload('scan.pdf', raw)]})


def test_selected_document_import_appends_extracted_sources_and_preserves_destination(workspace, tmp_path):
    conn = workspace.conn
    book = notes.create_notebook(conn, 'Authoring')
    other = notes.create_notebook(conn, 'Not the document destination')
    saved = documents.save(conn, {'title': 'Existing authoring', 'content': '# Original\n\nKeep this.', 'format': 'markdown', 'notebook_id': book['id']})
    with pymupdf.open() as source:
        source.new_page().insert_text((72, 72), 'Actual extracted PDF text.')
        raw = source.tobytes()
    sources = [upload('lecture.pdf', raw), upload('outline.md', b'## Imported heading\n\n- **Important**'), upload('facts.txt', b'Plain imported facts.')]
    before_pages = conn.execute('SELECT COUNT(*) FROM note_pages').fetchone()[0]
    result = documents.import_notes(conn, tmp_path, {
        'files': sources, 'document_id': saved['id'], 'revision': saved['revision'],
        'notebook_id': other['id'], 'folder_id': None, 'title': 'Must not rename',
    })
    assert set(result) == {'document', 'originals'}
    updated = result['document']
    assert updated['content'].startswith(saved['content'] + '\n\nActual extracted PDF text.')
    assert updated['content'].endswith('## Imported heading\n\n- **Important**\n\nPlain imported facts.')
    assert (updated['id'], updated['title'], updated['format'], updated['notebook_id']) == (saved['id'], saved['title'], saved['format'], book['id'])
    assert updated['revision'] == saved['revision'] + 1
    assert conn.execute('SELECT COUNT(*) FROM note_pages').fetchone()[0] == before_pages
    assert len(result['originals']) == 3
    by_name = {item['name']: item for item in result['originals']}
    for name, expected in [('lecture.pdf', raw), ('outline.md', b'## Imported heading\n\n- **Important**'), ('facts.txt', b'Plain imported facts.')]:
        retained = by_name[name]
        assert retained['page_id'] is None and retained['document_id'] == saved['id']
        assert documents.original(conn, tmp_path, retained['id'])[0] == expected
        assert (documents.original_root(tmp_path) / retained['id']).stat().st_mode & 0o077 == 0
    pasted = documents.import_notes(conn, tmp_path, {'text': 'Pasted ending.', 'document_id': saved['id'], 'revision': updated['revision']})
    assert pasted['document']['content'] == updated['content'] + '\n\nPasted ending.'
    assert pasted['originals'] == result['originals']
    with connect(workspace.settings.db_path) as reloaded:
        assert documents.document(reloaded, saved['id']) == pasted['document']
        assert documents.originals(reloaded, document_id=saved['id']) == result['originals']


def test_selected_document_conflicts_and_invalid_batches_never_mutate(workspace, tmp_path):
    conn = workspace.conn
    saved = documents.save(conn, {'title': 'Keep', 'content': 'Existing', 'format': 'text', 'notebook_id': None})
    imported = documents.import_notes(conn, tmp_path, {'files': [upload('first.txt', b'First')], 'document_id': saved['id'], 'revision': saved['revision']})
    before = imported['document']
    paths = set(documents.original_root(tmp_path).iterdir())
    with pytest.raises(notes.NotesError) as stale:
        documents.import_notes(conn, tmp_path, {'files': [upload('second.txt', b'Second')], 'document_id': saved['id'], 'revision': saved['revision']})
    assert stale.value.status == 409
    with pytest.raises(notes.NotesError):
        documents.import_notes(conn, tmp_path, {'files': [upload('valid.txt', b'Valid'), upload('invalid.pdf', b'not PDF')], 'document_id': saved['id'], 'revision': before['revision']})
    with pytest.raises(notes.NotesError, match='revision'):
        documents.import_notes(conn, tmp_path, {'text': 'Missing revision', 'document_id': saved['id']})
    with pytest.raises(notes.NotesError, match='revision'):
        documents.import_notes(conn, tmp_path, {'text': 'Missing destination', 'revision': before['revision']})
    assert documents.document(conn, saved['id']) == before
    assert documents.originals(conn, document_id=saved['id']) == imported['originals']
    assert set(documents.original_root(tmp_path).iterdir()) == paths


def test_selected_document_rolls_back_sources_and_content_on_storage_failure(workspace, tmp_path, monkeypatch):
    conn = workspace.conn
    saved = documents.save(conn, {'title': 'Atomic', 'content': 'Unchanged', 'format': 'text', 'notebook_id': None})
    store = documents._store_original
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('Storage unavailable')
        return store(*args, **kwargs)

    monkeypatch.setattr(documents, '_store_original', fail_second)
    with pytest.raises(OSError, match='Storage unavailable'):
        documents.import_notes(conn, tmp_path, {'files': [upload('a.txt', b'First'), upload('b.md', b'Second')], 'document_id': saved['id'], 'revision': saved['revision']})
    assert documents.document(conn, saved['id']) == saved
    assert documents.originals(conn, document_id=saved['id']) == []
    assert list(documents.original_root(tmp_path).iterdir()) == []


def test_selected_document_enforces_combined_text_limit(workspace, tmp_path, monkeypatch):
    conn = workspace.conn
    saved = documents.save(conn, {'title': 'Bounded', 'content': 'Existing text', 'format': 'text', 'notebook_id': None})
    monkeypatch.setattr(documents, 'MAX_TEXT', len(saved['content']) + 2)
    with pytest.raises(notes.NotesError):
        documents.import_notes(conn, tmp_path, {'files': [upload('more.txt', b'More')], 'document_id': saved['id'], 'revision': saved['revision']})
    assert documents.document(conn, saved['id']) == saved
    assert documents.originals(conn, document_id=saved['id']) == []
    assert list(documents.original_root(tmp_path).iterdir()) == []


def test_selected_document_import_web_task_and_original_metadata(workspace):
    runner = TaskRunner()
    app = SimpleNamespace(settings=workspace.settings, tasks=runner, mutation_lock=threading.Lock())
    saved = documents.save(workspace.conn, {'title': 'API authoring', 'content': '', 'format': 'markdown', 'notebook_id': None})
    try:
        started = web_documents.post(app, workspace.conn, '/api/documents/import', {
            'files': [upload('local.md', b'# Local imported text')], 'document_id': saved['id'], 'revision': saved['revision'],
        })
        runner.close()
        task = runner.snapshot(started['task']['id'])
        assert task['status'] == 'completed'
        assert task['result']['document']['content'] == '# Local imported text'
        retained = web_documents.get(app, workspace.conn, '/api/documents/originals', {'document_id': str(saved['id'])})
        assert retained['originals'] == task['result']['originals']
        assert web_documents.download(app, workspace.conn, '/api/documents/originals/' + retained['originals'][0]['id'], {})[0] == b'# Local imported text'
    finally:
        runner.close()


def test_pdf_renders_rich_markdown_and_nested_outlines_without_active_markup(workspace):
    content = '''# Large section

## Smaller section

Ordinary text with **bold words**, *italic words* and `inline code`.

- Parent bullet
  - Nested bullet
    1. Nested numbered
    2. Second numbered
- Another bullet

1. First ordered
   - Mixed nested bullet
2. Second ordered

> Quoted **emphasis**
> > Nested quotation

```html
<b>literal fenced markup</b>
```

<script>alert(1)</script>
<img src="https://invalid.example/remote.png" onerror="alert(1)">
![remote](https://invalid.example/picture.png)
'''
    rendered = documents._markdown_blocks(content.splitlines())
    assert '<strong>bold words</strong>' in rendered
    assert '<em>italic words</em>' in rendered
    assert '<code>inline code</code>' in rendered
    assert '<ul><li><p>Nested bullet</p><ol' in rendered
    assert '<blockquote><p>Quoted <strong>emphasis</strong></p><blockquote>' in rendered
    assert '&lt;script&gt;' in rendered and '&lt;img src=' in rendered
    assert '<script' not in rendered and '<img' not in rendered
    saved = documents.save(workspace.conn, {'title': 'Rich export', 'content': content, 'format': 'markdown', 'notebook_id': None})
    data, _, _ = documents.pdf(workspace.conn, saved['id'])
    with pymupdf.open(stream=data, filetype='pdf') as exported:
        text = '\n'.join(page.get_text() for page in exported)
        for phrase in ('Large section', 'Nested bullet', 'Nested numbered', 'Mixed nested bullet', 'Quoted emphasis', 'Nested quotation', '<script>alert(1)</script>', '<b>literal fenced markup</b>'):
            assert phrase in text
        assert '**bold words**' not in text and '`inline code`' not in text
        assert any(bullet in text for bullet in ('•', '●', '○'))
        spans = [span for page in exported for block in page.get_text('dict')['blocks'] if 'lines' in block for line in block['lines'] for span in line['spans']]
        assert any('bold words' in span['text'] and span['flags'] & 16 for span in spans)
        assert any('italic words' in span['text'] and span['flags'] & 2 for span in spans)
        assert any('inline code' in span['text'] and span['flags'] & 8 for span in spans)
        sizes = {phrase: next(span['size'] for span in spans if phrase in span['text']) for phrase in ('Large section', 'Smaller section', 'Ordinary text')}
        assert sizes['Large section'] > sizes['Smaller section'] > sizes['Ordinary text']
        assert all(page.get_links() == [] for page in exported)
    reader = PdfReader(io.BytesIO(data))
    assert not reader.trailer['/Root'].get('/OpenAction')
    assert not any(page.get('/AA') for page in reader.pages)


def test_original_document_migration_preserves_existing_page_relations():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    migrations = {migration.version: migration for migration in available_migrations()}
    try:
        conn.executescript(migrations[14].sql)
        conn.executescript(migrations[24].sql)
        conn.execute("INSERT INTO note_notebooks(id,title,created_at,updated_at) VALUES (1,'Existing','before','before')")
        conn.execute("INSERT INTO note_pages(id,notebook_id,title,document,created_at,updated_at) VALUES (1,1,'Existing','{}','before','before')")
        conn.execute("INSERT INTO document_originals(id,name,extension,byte_size,page_id,created_at) VALUES ('source','lecture.pdf','pdf',7,1,'before')")
        conn.commit()
        conn.executescript(migrations[26].sql)
        old = dict(conn.execute("SELECT * FROM document_originals WHERE id='source'").fetchone())
        assert old['page_id'] == 1 and old['document_id'] is None
        assert old['name'] == 'lecture.pdf' and old['byte_size'] == 7
        conn.execute("INSERT INTO documents(id,title,content,format,notebook_id,created_at,updated_at) VALUES (1,'Saved','Text','text',1,'now','now')")
        conn.execute("UPDATE document_originals SET document_id=1 WHERE id='source'")
        conn.execute('DELETE FROM documents WHERE id=1')
        retained = conn.execute("SELECT page_id,document_id FROM document_originals WHERE id='source'").fetchone()
        assert retained['page_id'] == 1 and retained['document_id'] is None
    finally:
        conn.close()
