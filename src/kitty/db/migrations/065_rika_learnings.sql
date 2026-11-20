-- Rika's career memory (kitty.core.rika.learnings): what the job postings the user acts on
-- keep asking for, the patterns in the jobs they choose, their resume review habits (derived
-- from workshop_ai_reviews, referenced by proposal id) and notes they add. Derived rows are
-- rebuilt by a signature-gated refresh; pinned and forgotten are the user's and survive it,
-- and a forgotten row is never shown or used again.
CREATE TABLE rika_learnings (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('posting_skill', 'posting_pattern', 'resume_edit', 'note')),
    key        TEXT NOT NULL CHECK (length(key) BETWEEN 1 AND 200),
    text       TEXT NOT NULL CHECK (length(trim(text)) BETWEEN 1 AND 500),
    weight     REAL NOT NULL DEFAULT 0,
    evidence   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence) AND json_type(evidence) = 'array'),
    detail     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail) AND json_type(detail) = 'object'),
    pinned     INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    forgotten  INTEGER NOT NULL DEFAULT 0 CHECK (forgotten IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (kind, key)
);
CREATE INDEX rika_learnings_kind ON rika_learnings(kind, forgotten, weight);

-- The inputs' signature at the last refresh (one row).
CREATE TABLE rika_learning_state (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    signature    TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
);
