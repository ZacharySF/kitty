"""Bounded local text documents, private originals and deterministic PDF import/export."""
from __future__ import annotations

import base64
import binascii
from collections import Counter
import html
import io
import os
from pathlib import Path, PurePosixPath
import re
import uuid

from kitty.core import backlinks, blocks, notes
from kitty.core.blocks import convert
from kitty.db.connection import transaction

MAX_TEXT = 512 * 1024
MAX_UPLOAD = 12 * 1024 * 1024
MAX_FILES = 100
MAX_STORAGE = 128 * 1024 * 1024
EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}


def filename(value):
    value = notes.text(value, 200, 'Filename').strip()
    if not value or value in {'.', '..'} or any(c in value for c in '/\\') or any(ord(c) < 32 for c in value):
        raise notes.NotesError('Choose a filename without paths or control characters')
    return value


def relative_path(value):
    value = notes.text(value, 1000, 'Relative path')
    parts = value.split('/')
    if len(parts) > 12 or any(part in {'', '.', '..'} for part in parts):
        raise notes.NotesError('Import paths need 1–12 safe relative components')
    for part in parts:
        filename(part)
    return parts


def notebook(conn, value):
    if value is None:
        return None
    value = notes.integer(value)
    if not conn.execute('SELECT 1 FROM note_notebooks WHERE id=?', (value,)).fetchone():
        raise notes.NotesError('Destination notebook not found', 404)
    return value


def listing(conn):
    rows = conn.execute("SELECT json_extract(properties,'$.number') AS id,text AS title,json_extract(properties,'$.format') AS format,"
                        "json_extract(properties,'$.notebook_id') AS notebook_id,json_extract(properties,'$.revision') AS revision,updated_at "
                        "FROM blocks WHERE type='document' AND parent_id IS NULL AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 500")
    return {'documents': [dict(row) for row in rows]}


def _doc(conn, document_id):
    return blocks.load_page(conn, notes.root_block(conn, 'document', document_id, 'Document not found')['id'])


def _shape(doc):
    root = doc.root
    props = root.properties
    return {'id': props['number'], 'title': root.text, 'content': convert.join_document(doc.children(root.id)),
            'format': props['format'], 'notebook_id': props.get('notebook_id'), 'revision': props['revision'],
            'created_at': root.created_at, 'updated_at': root.updated_at}


def document(conn, document_id):
    return _shape(_doc(conn, document_id))


def _write_ops(doc, title, content, fmt, notebook_id, revision):
    """Operations saving a whole document: changed chunks only, plus the next revision."""
    root = doc.root
    ops = convert.sync_tree(doc, root.id, convert.document_specs(doc, content, fmt == 'markdown'), lambda item: item.parent_id == root.id)
    if root.text != title:
        ops.append({'op': 'text', 'id': root.id, 'text': title})
    return [*ops, {'op': 'set', 'id': root.id, 'props': {'format': fmt, 'notebook_id': notebook_id, 'revision': revision + 1}}]


def save(conn, body, document_id=None):
    notes.keys(body, {'title', 'content', 'format', 'notebook_id', 'revision'}, {'title', 'content', 'format', 'notebook_id'} | ({'revision'} if document_id else set()))
    title = notes.text(body['title'], 200, 'Title').strip()
    content = notes.text(body['content'], MAX_TEXT, 'Document')
    if not title or body['format'] not in ('markdown', 'text'):
        raise notes.NotesError('A title and Markdown or plain text format are required')
    with transaction(conn, immediate=True):
        nid = notebook(conn, body['notebook_id'])
        if document_id is None:
            if conn.execute("SELECT COUNT(*) FROM blocks WHERE type='document' AND parent_id IS NULL AND deleted_at IS NULL").fetchone()[0] >= 500:
                raise notes.NotesError('Document limit reached (500)')
            root_id, document_id = str(uuid.uuid4()), notes.next_number(conn, 'document')
            chunks = [{'id': str(uuid.uuid4()), 'parent': root_id, 'type': chunk['type'], 'props': {'sep': chunk['sep']}, 'text': chunk['text']}
                      for chunk in convert.split_document(content, body['format'] == 'markdown')]
            notes.apply(conn, [{'op': 'insert', 'id': root_id, 'type': 'document', 'parent': None, 'text': title,
                                'props': {'number': document_id, 'format': body['format'], 'notebook_id': nid, 'revision': 1}},
                               *convert.insert_ops(chunks)])
        else:
            doc = _doc(conn, document_id)
            revision = notes.integer(body['revision'])
            if doc.root.properties['revision'] != revision:
                raise notes.NotesError('Document changed in another tab; your local edits were not overwritten. Reload before saving.', 409)
            notes.apply(conn, _write_ops(doc, title, content, body['format'], nid, revision), base={doc.page_id: revision})
    return document(conn, document_id)


def decode_file(item):
    notes.keys(item, {'name', 'data', 'path'}, {'name', 'data'})
    name = filename(item['name'])
    extension = PurePosixPath(name).suffix.lower().lstrip('.')
    if extension not in EXTENSIONS:
        raise notes.NotesError('Supported imports: .md, .markdown, .txt and text-bearing .pdf only')
    encoded = notes.text(item['data'], MAX_UPLOAD * 4 // 3 + 4, 'Upload')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise notes.NotesError('Upload must be base64') from None
    if not raw or len(raw) > MAX_UPLOAD:
        raise notes.NotesError('Each file must contain 1 byte–12 MiB')
    if extension == 'pdf':
        if not raw.startswith(b'%PDF-'):
            raise notes.NotesError('The selected file is not a PDF')
        try:
            import pymupdf
            with pymupdf.open(stream=raw, filetype='pdf') as pdf:
                if pdf.is_encrypted or not 1 <= len(pdf) <= 100:
                    raise notes.NotesError('PDF must be unencrypted and contain 1–100 pages')
                parts, size = [], 0
                for page in pdf:
                    part = page.get_text(sort=True)
                    size += len(part.encode('utf-8'))
                    if size > MAX_TEXT:
                        raise notes.NotesError('Extracted PDF text exceeds 512 KiB; split the PDF first')
                    parts.append(part)
                content = '\n\n'.join(parts)
        except notes.NotesError:
            raise
        except (ValueError, RuntimeError, OSError):
            raise notes.NotesError('PDF is corrupt or unsupported') from None
        if not content.strip():
            raise notes.NotesError('This PDF has no extractable text. OCR is not supported; paste a text transcription instead.')
    else:
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeError:
            raise notes.NotesError('Text and Markdown imports must use UTF-8 encoding') from None
    content = notes.text(content, MAX_TEXT, 'Imported text')
    parts = relative_path(item.get('path', name))
    if parts[-1] != name:
        raise notes.NotesError('Import path must end with the selected filename')
    return {'name': name, 'extension': extension, 'content': content, 'path': parts, 'raw': raw}


def prepare(conn, body):
    notes.keys(body, {'file', 'folder_id', 'notebook_id'}, {'file'})
    item = decode_file(body['file'])
    return {'name': item['name'], 'content': item['content'], 'format': 'markdown' if item['extension'] in {'md', 'markdown'} else 'text', **topics(conn, {'text': item['content'], 'folder_id': body.get('folder_id'), 'notebook_id': body.get('notebook_id')})}


def original_root(data_dir):
    root = Path(data_dir) / 'document-originals'
    if root.is_symlink():
        raise notes.NotesError('Original storage cannot be a symbolic link', 409)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root.resolve()


def _store_original(conn, root, created, item, *, page_id=None, document_id=None):
    """Retain source bytes privately inside the caller's rollback/cleanup boundary."""
    if not item['raw']:
        return None
    original_id = uuid.uuid4().hex
    target = root / original_id
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    created.append(target)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(item['raw'])
    conn.execute(
        'INSERT INTO document_originals(id,name,extension,byte_size,page_id,document_id,created_at) VALUES (?,?,?,?,?,?,?)',
        (original_id, item['name'], item['extension'], len(item['raw']), page_id, document_id, notes.now()),
    )
    return original_id


def originals(conn, *, page_id=None, document_id=None):
    """List retained source metadata for exactly one saved page or document."""
    if (page_id is None) == (document_id is None):
        raise notes.NotesError('Choose one page_id or document_id')
    column, value = ('page_id', page_id) if page_id is not None else ('document_id', document_id)
    return [dict(row) for row in conn.execute(
        f'SELECT id,name,extension,byte_size,page_id,document_id FROM document_originals WHERE {column}=? ORDER BY created_at,id',
        (notes.integer(value),),
    )]


def import_notes(conn, data_dir, body):
    """Import files/pasted text into new notes, or append to a revision-checked document.

    ``document_id`` requires ``revision`` and returns ``{document, originals}``.
    The saved document keeps its title, format and notebook even if note destination
    fields are supplied. Without it, the existing ``{pages}`` folder import is used.
    Source bytes and database changes roll back together on any batch failure.
    """
    notes.keys(body, {'files', 'text', 'title', 'notebook_id', 'folder_id', 'document_id', 'revision'}, set())
    selected = body.get('document_id') is not None
    if selected != ('revision' in body):
        raise notes.NotesError('Selected document import requires document_id and revision together')
    document_id = notes.integer(body['document_id']) if selected else None
    revision = notes.integer(body['revision']) if selected else None
    files = body.get('files', [])
    if not isinstance(files, list) or len(files) > MAX_FILES or bool(files) == ('text' in body):
        raise notes.NotesError('Choose 1–100 supported files or paste text')
    prepared = [decode_file(item) for item in files]
    if not files:
        title = notes.text(body.get('title', 'Pasted text'), 200).strip()
        content = notes.text(body['text'], MAX_TEXT, 'Pasted text')
        if not title or not content.strip():
            raise notes.NotesError('Pasted text and title are required')
        prepared = [{'name': title, 'content': content, 'path': [title], 'raw': b'', 'extension': 'txt'}]
    if sum(len(item['raw']) for item in prepared) > MAX_UPLOAD:
        raise notes.NotesError('Combined imports must not exceed 12 MiB')
    created = []
    try:
        with transaction(conn, immediate=True):
            if selected:
                saved = document(conn, document_id)
                if saved['revision'] != revision:
                    raise notes.NotesError('Document changed in another tab; reload before importing.', 409)
                appended = '\n\n'.join(item['content'] for item in prepared)
                content = saved['content'] + ('\n\n' if saved['content'] and appended else '') + appended
                content = notes.text(content, MAX_TEXT, 'Document')
            else:
                nid = notebook(conn, body.get('notebook_id'))
                folder = notes._folder_id(conn, body.get('folder_id'))
                if nid is not None:
                    folder = conn.execute('SELECT folder_id FROM note_notebooks WHERE id=?', (nid,)).fetchone()[0]
            root = original_root(data_dir)
            disk_usage = sum(p.stat().st_size for p in root.iterdir() if not p.is_symlink() and p.is_file())
            if disk_usage + sum(len(item['raw']) for item in prepared) > MAX_STORAGE:
                raise notes.NotesError('Private original storage exceeds 128 MiB')
            if selected:
                doc = _doc(conn, document_id)
                notes.apply(conn, _write_ops(doc, saved['title'], content, saved['format'], saved['notebook_id'], revision),
                            base={doc.page_id: revision})
                for item in prepared:
                    _store_original(conn, root, created, item, document_id=document_id)
                result = {'document': document(conn, document_id), 'originals': originals(conn, document_id=document_id)}
            else:
                result = _import_pages(conn, root, created, prepared, nid, folder)
        return result
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def _import_pages(conn, root, created, prepared, nid, folder):
    """Create note destinations under the import transaction, preserving path hierarchy."""
    if notes.page_count(conn) + len(prepared) > notes.MAX_PAGES:
        raise notes.NotesError('Note page limit reached')
    folders, books, result = {}, {}, []
    from kitty.core import note_blocks
    for item in prepared:
        parent = folder
        for part in item['path'][:-1]:
            key = (parent, part)
            if key not in folders:
                row = conn.execute('SELECT id FROM note_folders WHERE parent_id IS ? AND title=? ORDER BY id LIMIT 1', key).fetchone()
                if row:
                    folders[key] = row['id']
                else:
                    stamp = notes.now()
                    folders[key] = conn.execute('INSERT INTO note_folders(title,parent_id,created_at,updated_at) VALUES (?,?,?,?)', (part, parent, stamp, stamp)).lastrowid
            parent = folders[key]
        destination = nid if not item['path'][:-1] else None
        if destination is None:
            if parent not in books:
                row = conn.execute("SELECT id FROM note_notebooks WHERE folder_id IS ? AND title='Imported documents' ORDER BY id LIMIT 1", (parent,)).fetchone()
                books[parent] = row['id'] if row else notes.create_notebook(conn, 'Imported documents', folder_id=parent)['id']
            destination = books[parent]
        title = item['name'] if not item['raw'] else PurePosixPath(item['name']).stem
        page = notes.create_page(conn, destination, title)
        # Prefer paragraph boundaries, but keep the outline bounded even
        # for inputs containing hundreds of thousands of tiny paragraphs.
        chunks, remaining = [], item['content']
        while remaining:
            end = min(2000, len(remaining))
            boundary = remaining.rfind('\n\n', 1000, end)
            if boundary >= 0:
                end = boundary + 2
            chunks.append(remaining[:end])
            remaining = remaining[end:]
        blocks = [{'id': str(uuid.uuid4()), 'parent_id': None, 'text': chunk, 'ordering': index, 'collapsed': False} for index, chunk in enumerate(chunks)]
        note_blocks.save_blocks(conn, page['id'], page['revision'], blocks)
        original_id = _store_original(conn, root, created, item, page_id=page['id'])
        matches = topics(conn, {'text': item['content'], 'folder_id': folder, 'exclude_page_id': page['id']})
        result.append({'page_id': page['id'], 'title': title, 'notebook_id': destination, 'original_id': original_id, **matches})
    return {'pages': result}


def topics(conn, body):
    notes.keys(body, {'text', 'folder_id', 'notebook_id', 'exclude_page_id', 'scope'}, {'text'})
    text = notes.text(body['text'], MAX_TEXT, 'Topic text')
    # Share token normalization, stop words and privacy-safe snippets with backlinks.
    def terms(value):
        return Counter(token for token in (backlinks.phrase(match.group()) for match in backlinks.TOKEN.finditer(value)) if token and token[0] not in backlinks.STOPWORDS)
    query = terms(text)
    ranked = query.most_common(24)
    local = []
    for term, count in ranked[:12]:
        hit = next((m for m in backlinks.TOKEN.finditer(text) if backlinks.phrase(m.group()) == term), None)
        if hit:
            local.append({'term': term[0], 'count': count, 'snippet': backlinks.snippet(text, hit.start(), hit.end())})
    if body.get('scope') == 'document':
        return {'topics': local, 'matches': [], 'scanned': 0}
    nid = notebook(conn, body.get('notebook_id'))
    folder = notes._folder_id(conn, body.get('folder_id'))
    if nid is not None:
        rows = conn.execute('SELECT id,block_id,title FROM note_page_index WHERE notebook_id=? ORDER BY updated_at DESC LIMIT 1000', (nid,)).fetchall()
    elif folder is not None:
        rows = conn.execute('WITH RECURSIVE descendants(id) AS (SELECT ? UNION SELECT f.id FROM note_folders f JOIN descendants d ON f.parent_id=d.id) SELECT p.id,p.block_id,p.title FROM note_page_index p JOIN note_notebooks n ON n.id=p.notebook_id WHERE n.folder_id IN (SELECT id FROM descendants) ORDER BY p.updated_at DESC LIMIT 1000', (folder,)).fetchall()
    else:
        rows = conn.execute('SELECT id,block_id,title FROM note_page_index ORDER BY updated_at DESC LIMIT 1000').fetchall()
    texts = notes.page_texts(conn, [row['block_id'] for row in rows])
    matches, scanned, remaining = [], 0, 4_000_000
    for row in rows:
        if row['id'] == body.get('exclude_page_id'):
            continue
        elements, outline = texts[row['block_id']]
        value = (row['title'] + '\n' + '\n'.join([*(text for _, text in elements), *outline]))[:16000]
        remaining -= len(value)
        if remaining < 0:
            break
        scanned += 1
        shared = query.keys() & terms(value).keys()
        if not shared:
            continue
        shared = sorted(shared, key=lambda term: (-query[term], term))[:8]
        hit = next((m for m in backlinks.TOKEN.finditer(value) if backlinks.phrase(m.group()) in shared), None)
        matches.append({'page_id': row['id'], 'title': row['title'], 'terms': [term[0] for term in shared], 'score': len(shared), 'snippet': backlinks.snippet(value, hit.start(), hit.end()) if hit else ''})
    matches.sort(key=lambda item: (-item['score'], item['page_id']))
    return {'topics': local, 'matches': matches[:24], 'scanned': scanned}


def original(conn, data_dir, original_id):
    if not re.fullmatch(r'[a-f0-9]{32}', original_id):
        raise notes.NotesError('Original not found', 404)
    row = conn.execute('SELECT * FROM document_originals WHERE id=?', (original_id,)).fetchone()
    if row is None:
        raise notes.NotesError('Original not found', 404)
    root = original_root(data_dir)
    path = root / original_id
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root or path.stat().st_size != row['byte_size'] or row['byte_size'] > MAX_UPLOAD:
        raise notes.NotesError('Registered original is unavailable', 404)
    return path.read_bytes(), 'application/octet-stream', download_name(row['name'], row['extension'])


def download_name(title, extension):
    base = re.sub(r'[^A-Za-z0-9._-]+', '-', title).strip('.-')[:100] or 'document'
    if base.lower().endswith('.' + extension):
        base = base[:-(len(extension) + 1)] or 'document'
    return f'{base}.{extension}'


_INLINE = re.compile(
    r'\\(?P<escaped>[\\`*_{}\[\]()#+.!>~-])'
    r'|(?P<ticks>`+)(?P<code>.+?)(?P=ticks)(?!`)'
    r'|(?P<both>\*\*\*|___)(?=\S)(?P<both_text>.+?)(?<=\S)(?P=both)'
    r'|(?P<strong>\*\*|__)(?=\S)(?P<strong_text>.+?)(?<=\S)(?P=strong)'
    r'|(?<!\w)(?P<em>\*|_)(?=\S)(?P<em_text>.+?)(?<=\S)(?P=em)(?!\w)',
    re.DOTALL,
)
_HEADING = re.compile(r'^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$')
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_QUOTE = re.compile(r'^ {0,3}> ?(.*)$')
_LIST = re.compile(r'^( *)([-+*]|\d{1,9}[.)]) +(.+|$)')


def _markdown_inline(value, depth=0):
    """Emit only generated emphasis/code tags; never pass user HTML or asset URLs."""
    if depth >= 24:
        return html.escape(value)
    parts, end = [], 0
    for match in _INLINE.finditer(value):
        parts.append(html.escape(value[end:match.start()]))
        if match['escaped'] is not None:
            parts.append(html.escape(match['escaped']))
        elif match['code'] is not None:
            parts.append('<code>' + html.escape(match['code'].replace('\n', ' ')) + '</code>')
        else:
            kind = 'both' if match['both'] else 'strong' if match['strong'] else 'em'
            opening, closing = {
                'both': ('<strong><em>', '</em></strong>'),
                'strong': ('<strong>', '</strong>'),
                'em': ('<em>', '</em>'),
            }[kind]
            parts.append(opening + _markdown_inline(match[kind + '_text'], depth + 1) + closing)
        end = match.end()
    parts.append(html.escape(value[end:]))
    return ''.join(parts)


def _markdown_blocks(lines, depth=0):
    """Render bounded Markdown blocks, including indented mixed list outlines.

    Only locally generated tags reach Story. HTML, images and links remain
    literal selectable text, so imported markup cannot run or fetch resources.
    """
    if depth >= 24:
        return '<p>' + html.escape('\n'.join(lines)) + '</p>'
    parts, index = [], 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = _FENCE.match(line)
        heading = _HEADING.match(line)
        quote = _QUOTE.match(line)
        item = _LIST.match(line)
        if fence:
            marker, code = fence[1], []
            index += 1
            closing = re.compile(r'^ {0,3}' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}\s*$')
            while index < len(lines) and not closing.match(lines[index]):
                code.append(lines[index])
                index += 1
            index += index < len(lines)
            parts.append('<pre><code>' + html.escape('\n'.join(code)) + '</code></pre>')
        elif heading:
            level = min(len(heading[1]) + 1, 6)
            parts.append(f'<h{level}>' + _markdown_inline(heading[2]) + f'</h{level}>')
            index += 1
        elif quote:
            quoted = []
            while index < len(lines) and (match := _QUOTE.match(lines[index])):
                quoted.append(match[1])
                index += 1
            parts.append('<blockquote>' + _markdown_blocks(quoted, depth + 1) + '</blockquote>')
        elif item:
            indent = len(item[1])
            ordered = item[2][0].isdigit()
            tag = 'ol' if ordered else 'ul'
            start = f' start="{int(item[2][:-1])}"' if ordered else ''
            parts.append(f'<{tag}{start}>')
            while index < len(lines):
                item = _LIST.match(lines[index])
                if not item or len(item[1]) != indent or item[2][0].isdigit() != ordered:
                    break
                content_indent = item.start(3)
                children = [item[3]]
                index += 1
                while index < len(lines):
                    child = lines[index]
                    if not child.strip():
                        children.append('')
                        index += 1
                        continue
                    leading = len(child) - len(child.lstrip(' '))
                    if leading <= indent:
                        break
                    children.append(child[min(leading, content_indent):])
                    index += 1
                parts.append('<li>' + _markdown_blocks(children, depth + 1) + '</li>')
            parts.append(f'</{tag}>')
        else:
            paragraph = [line]
            index += 1
            while index < len(lines) and lines[index].strip() and not any(
                pattern.match(lines[index]) for pattern in (_FENCE, _HEADING, _QUOTE, _LIST)
            ):
                paragraph.append(lines[index])
                index += 1
            parts.append('<p>' + _markdown_inline('\n'.join(paragraph)) + '</p>')
    return ''.join(parts)


def pdf(conn, document_id):
    """Export selectable, styled Markdown with escaped local-only Story HTML."""
    import pymupdf
    doc = document(conn, document_id)
    # Story lays out generated local-only HTML as real selectable Unicode text.
    parts = [f'<h1>{html.escape(doc["title"])}</h1>']
    if doc['format'] == 'text':
        parts.extend(f'<p>{html.escape(line) or "&#160;"}</p>' for line in doc['content'].splitlines())
    else:
        parts.append(_markdown_blocks(doc['content'].expandtabs(4).splitlines()))
    story = pymupdf.Story(html=''.join(parts), user_css='''
        body { font-family: sans-serif; font-size: 11pt; }
        h1 { font-size: 24pt; } h2 { font-size: 20pt; }
        h3 { font-size: 17pt; } h4 { font-size: 14pt; }
        h5 { font-size: 12pt; } h6 { font-size: 11pt; }
        p { margin: 0 0 5pt; overflow-wrap: anywhere; }
        ul, ol { margin: 5pt 0; padding-left: 22pt; }
        ul { list-style-type: disc; } ul ul { list-style-type: circle; }
        li { margin-bottom: 3pt; }
        strong { font-weight: bold; } em { font-style: italic; }
        code { font-family: monospace; font-size: 9pt; }
        pre { white-space: pre-wrap; margin: 5pt 0; }
        blockquote { margin: 5pt 0 5pt 16pt; padding-left: 8pt;
                     border-left: 2pt solid #888; color: #444; }
    ''')
    stream = io.BytesIO()
    writer = pymupdf.DocumentWriter(stream)
    def page_rect(index, filled):
        if index >= 300:
            raise notes.NotesError('PDF exceeds 300 pages; split the document')
        return pymupdf.Rect(0, 0, 595, 842), pymupdf.Rect(48, 48, 547, 794), None
    try:
        story.write(writer, page_rect)
    finally:
        writer.close()
    return stream.getvalue(), 'application/pdf', download_name(doc['title'], 'pdf')
