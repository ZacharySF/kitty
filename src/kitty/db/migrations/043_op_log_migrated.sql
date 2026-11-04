-- The op_log rows the block migrations wrote (033 converting every note page, document and
-- quick note; 039 making tag records of existing #tags) are conversions, not edits, so
-- /api/brain leaves them out. On every supported upgrade (a fresh database or one at main's
-- schema) 032-043 run in one pass before anything else writes, so they are exactly the rows
-- present now.
CREATE TABLE op_log_migrated (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq INTEGER NOT NULL
);
INSERT INTO op_log_migrated(id, last_seq) SELECT 1, COALESCE(MAX(seq), 0) FROM op_log;
