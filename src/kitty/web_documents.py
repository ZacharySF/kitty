"""Python document extraction, import, note conversion and PDF export endpoints."""
from __future__ import annotations

from contextlib import closing
import re

from kitty.core import documents, notes
from kitty.db import connect


def _error(exc):
    from kitty.web_server import APIError
    return APIError(exc.status, str(exc))


def _task(app, kind, operation):
    def work():
        try:
            with app.mutation_lock, closing(connect(app.settings.db_path)) as conn:
                return operation(conn)
        except notes.NotesError as exc:
            raise _error(exc) from None
    return {'task': app.tasks.start(kind, work)}


def get(app, conn, path, query):
    """Document reads are served by rika-server; retain the extension interface."""
    return None


def post(app, conn, path, body):
    """Start imports returning task results of {pages} or {document, originals}.

    /import accepts files or text; document_id plus revision atomically appends
    to the saved document instead of creating note pages. Note destinations are
    ignored in that mode, preserving the saved document's notebook association.
    """
    if not path.startswith('/api/documents'):
        return None
    try:
        if path == '/api/documents/prepare':
            return _task(app, 'document-extract', lambda local: documents.prepare(local, body))
        if path == '/api/documents/import':
            kind = 'document-import' if isinstance(body, dict) and body.get('document_id') is not None else 'document-note-import'
            return _task(app, kind, lambda local: documents.import_notes(local, app.settings.data_dir, body))
        match = re.fullmatch(r'/api/documents/([1-9][0-9]{0,15})/note', path)
        if match:
            notes.keys(body, {'revision'}, {'revision'})
            doc = documents.document(conn, int(match[1]))
            if doc['revision'] != notes.integer(body['revision']):
                raise notes.NotesError('Document changed; reload before making a note', 409)
            return _task(app, 'document-to-note', lambda local: documents.import_notes(local, app.settings.data_dir, {'text': doc['content'], 'title': doc['title'], 'notebook_id': doc['notebook_id']}))
        return None
    except notes.NotesError as exc:
        raise _error(exc) from None


def download(app, conn, path, query):
    try:
        match = re.fullmatch(r'/api/documents/([1-9][0-9]{0,15})/pdf', path)
        if match:
            notes.keys(query, set())
            return documents.pdf(conn, int(match[1]))
        return None
    except notes.NotesError as exc:
        raise _error(exc) from None
