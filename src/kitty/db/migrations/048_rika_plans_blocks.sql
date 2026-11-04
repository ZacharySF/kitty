-- rika_plans (045, main) pointed note_page_id at note_pages, which the block backend
-- (032/033) froze: a dump note made since lives only in `blocks`, addressed by its Notes
-- number (what note_pages.id was, kitty.core.blocks.legacy). Rebuilt like 042 with that
-- number and no foreign key to the frozen table; Trash keeps the link (a restored note
-- keeps its plan's link) and the trigger clears it when the note's root block is purged.
CREATE TABLE rika_plans_new (
    id            TEXT PRIMARY KEY,
    message_id    TEXT NOT NULL REFERENCES rika_messages(id) ON DELETE CASCADE,
    request_id    TEXT NOT NULL REFERENCES rika_messages(id) ON DELETE CASCADE,
    items_json    TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    duration_s    REAL NOT NULL CHECK (duration_s >= 0),
    note_page_id  INTEGER,
    note_block_id TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'applied', 'dismissed', 'failed')),
    created_at    TEXT NOT NULL,
    decided_at    TEXT
);
INSERT INTO rika_plans_new SELECT id, message_id, request_id, items_json, segments_json, duration_s, note_page_id,
    note_block_id, status, created_at, decided_at FROM rika_plans;
DROP TABLE rika_plans;
ALTER TABLE rika_plans_new RENAME TO rika_plans;
CREATE INDEX rika_plans_created ON rika_plans(created_at);

CREATE TRIGGER rika_plans_note_purged AFTER DELETE ON blocks
WHEN OLD.parent_id IS NULL AND OLD.type = 'page'
BEGIN
    UPDATE rika_plans SET note_page_id = NULL, note_block_id = NULL
    WHERE note_page_id = json_extract(OLD.properties, '$.number');
END;
