-- Editable source documents and owner-only retained import originals.
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT 'markdown' CHECK(format IN ('markdown','text')),
    notebook_id INTEGER REFERENCES note_notebooks(id) ON DELETE SET NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE document_originals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    extension TEXT NOT NULL CHECK(extension IN ('pdf','md','txt','markdown')),
    byte_size INTEGER NOT NULL,
    page_id INTEGER REFERENCES note_pages(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX document_originals_page ON document_originals(page_id);
