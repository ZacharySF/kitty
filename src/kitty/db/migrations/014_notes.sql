CREATE TABLE note_notebooks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE note_pages (
    id INTEGER PRIMARY KEY,
    notebook_id INTEGER NOT NULL REFERENCES note_notebooks(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    document TEXT NOT NULL,
    search_text TEXT NOT NULL DEFAULT '',
    viewport TEXT NOT NULL DEFAULT '{"x":0,"y":0,"zoom":1}',
    revision INTEGER NOT NULL DEFAULT 1,
    viewport_revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX note_pages_notebook ON note_pages(notebook_id);
CREATE TABLE note_attachments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    filepath TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE note_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    allow_ai INTEGER NOT NULL DEFAULT 0,
    retention_days INTEGER NOT NULL DEFAULT 30,
    revision INTEGER NOT NULL DEFAULT 1
);
INSERT INTO note_preferences(id) VALUES (1);
