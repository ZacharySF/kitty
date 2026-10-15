-- Local graph snapshots. A single previous snapshot enables revision-safe undo.
CREATE TABLE schema_maps (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('study','general','jobs')),
    graph_json TEXT NOT NULL,
    viewport_json TEXT NOT NULL,
    previous_json TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
