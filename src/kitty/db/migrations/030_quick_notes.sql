-- Small local scratch notes; revision guards concurrent windows.
CREATE TABLE quick_notes (
    id INTEGER PRIMARY KEY,
    body TEXT NOT NULL DEFAULT '',
    tint TEXT NOT NULL DEFAULT 'rose' CHECK (tint IN ('rose','lilac','sky','mint','honey','frost')),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX quick_notes_order ON quick_notes(pinned DESC, updated_at DESC);
