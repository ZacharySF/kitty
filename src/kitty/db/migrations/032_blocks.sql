-- foreign_keys: off
-- Block backend (docs/platform-design.md, build step 1). `blocks`, `block_refs` and
-- `block_topics` are a projection of the append-only `op_log`; kitty.core.blocks.store
-- is their only writer. Root blocks (pages, documents, quick notes) keep the integer
-- `number` the existing Notes API addresses them by.
CREATE TABLE blocks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    parent_id TEXT REFERENCES blocks(id) DEFERRABLE INITIALLY DEFERRED,
    page_id TEXT NOT NULL,
    order_key TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX blocks_children ON blocks(parent_id, order_key);
CREATE INDEX blocks_page ON blocks(page_id);
CREATE INDEX blocks_roots ON blocks(type, deleted_at) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX blocks_number ON blocks(type, json_extract(properties, '$.number'))
    WHERE parent_id IS NULL AND json_extract(properties, '$.number') IS NOT NULL;

CREATE TABLE op_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    txn_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    at TEXT NOT NULL,
    ops BLOB NOT NULL
);
CREATE INDEX op_log_page ON op_log(page_id, seq);
CREATE INDEX op_log_txn ON op_log(txn_id);

-- One row: this database's device identity and the compaction horizon (Q15).
CREATE TABLE block_history (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    device_id TEXT NOT NULL,
    horizon_seq INTEGER NOT NULL DEFAULT 0,
    horizon_at TEXT
);
INSERT INTO block_history(id, device_id) VALUES (1, lower(hex(randomblob(16))));
CREATE TRIGGER op_log_append_only BEFORE UPDATE ON op_log
BEGIN SELECT RAISE(ABORT, 'op_log is append-only'); END;
CREATE TRIGGER op_log_compaction_only BEFORE DELETE ON op_log
WHEN old.seq > (SELECT horizon_seq FROM block_history WHERE id = 1)
BEGIN SELECT RAISE(ABORT, 'op_log rows are removed only below the compaction horizon'); END;

CREATE TABLE page_snapshots (
    page_id TEXT NOT NULL,
    day TEXT NOT NULL,
    seq INTEGER NOT NULL,
    state BLOB NOT NULL,
    PRIMARY KEY (page_id, day)
);
CREATE INDEX page_snapshots_seq ON page_snapshots(page_id, seq);

CREATE TABLE block_refs (
    source_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, kind)
);
CREATE INDEX block_refs_target ON block_refs(target_id, kind);
CREATE TABLE block_topics (
    block_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('page', 'tag')),
    topic TEXT NOT NULL,
    PRIMARY KEY (block_id, kind, topic)
);
CREATE INDEX block_topics_topic ON block_topics(kind, topic);

-- Note pages as the existing Notes API and its readers (atlas, schema maps) see them.
CREATE VIEW note_page_index AS
SELECT CAST(json_extract(b.properties, '$.number') AS INTEGER) AS id, b.id AS block_id,
       json_extract(b.properties, '$.notebook_id') AS notebook_id, b.text AS title,
       json_extract(b.properties, '$.revision') AS revision, b.created_at, b.updated_at,
       COALESCE(json_extract(b.properties, '$.favorite'), 0) AS favorite,
       json_extract(b.properties, '$.last_opened') AS last_opened,
       COALESCE(json_extract(b.properties, '$.position'), 0) AS position
FROM blocks b WHERE b.type = 'page' AND b.parent_id IS NULL AND b.deleted_at IS NULL;

-- Live outline blocks of live note pages; `path` (order keys joined by '/') sorts in
-- reading order because '/' sorts before every order-key digit.
CREATE VIEW note_outline AS
WITH RECURSIVE outline(id, page_block_id, parent_id, text, path) AS (
    SELECT b.id, b.page_id, NULL, b.text, b.order_key
    FROM blocks b JOIN blocks r ON r.id = b.parent_id
    WHERE r.parent_id IS NULL AND r.type = 'page' AND b.type = 'text' AND b.deleted_at IS NULL
    UNION ALL
    SELECT c.id, c.page_id, c.parent_id, c.text, o.path || '/' || c.order_key
    FROM blocks c JOIN outline o ON c.parent_id = o.id
    WHERE c.type = 'text' AND c.deleted_at IS NULL
)
SELECT o.id, p.id AS page_id, o.page_block_id, o.parent_id, o.text, o.path, p.title, p.updated_at
FROM outline o JOIN note_page_index p ON p.block_id = o.page_block_id;

-- Retained originals now point at page and document numbers that live in `blocks`.
CREATE TABLE document_originals_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    extension TEXT NOT NULL CHECK(extension IN ('pdf','md','txt','markdown')),
    byte_size INTEGER NOT NULL,
    page_id INTEGER,
    created_at TEXT NOT NULL,
    document_id INTEGER
);
INSERT INTO document_originals_new(id, name, extension, byte_size, page_id, created_at, document_id)
SELECT id, name, extension, byte_size, page_id, created_at, document_id FROM document_originals ORDER BY rowid;
DROP TABLE document_originals;
ALTER TABLE document_originals_new RENAME TO document_originals;
CREATE INDEX document_originals_page ON document_originals(page_id);
CREATE INDEX document_originals_document ON document_originals(document_id);
