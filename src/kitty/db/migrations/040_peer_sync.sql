-- Peer sync (docs/platform-design.md, build step 3: Q5 Loro, Q16 paired devices).
--
-- Every page is one Loro document from its first transaction after this step on.
-- `page_crdt` holds its latest snapshot; the `op_log` rows after `snapshot_seq` carry
-- the Loro update bytes of their transaction in `crdt`, so a page loads as snapshot +
-- tail. Rows written before this step have no `crdt` and stay replayable from their
-- JSON operations. `vv` is the document's version vector (Loro encoding), kept current
-- so peers can be told what this device holds without loading the document.
ALTER TABLE op_log ADD COLUMN crdt BLOB;
-- The Loro tree node ("counter@peer") a projected block came from.
ALTER TABLE blocks ADD COLUMN node TEXT;

CREATE TABLE page_crdt (
    page_id TEXT PRIMARY KEY,
    snapshot BLOB NOT NULL,
    snapshot_seq INTEGER NOT NULL DEFAULT 0,
    tail INTEGER NOT NULL DEFAULT 0,
    vv BLOB NOT NULL,
    updated_at TEXT NOT NULL
);

-- This device as other kittys see it. One row.
CREATE TABLE sync_device (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL DEFAULT '',
    paused INTEGER NOT NULL DEFAULT 0
);
INSERT INTO sync_device(id) VALUES (1);

-- Paired devices: the trust list. `public_key` is the peer's long-term X25519 key,
-- exchanged at pairing; only a device holding the matching private key can sync.
-- `acked_seq` is the highest of this device's op_log sequence numbers the peer has
-- confirmed it holds: compaction never removes rows above the lowest one.
-- `their_seq` is the highest of the peer's sequence numbers this device has applied.
CREATE TABLE peers (
    device_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    public_key BLOB NOT NULL UNIQUE,
    address TEXT,
    last_address TEXT,
    paired_at TEXT NOT NULL,
    acked_seq INTEGER NOT NULL DEFAULT 0,
    their_seq INTEGER NOT NULL DEFAULT 0,
    last_sync_at TEXT,
    reached_at TEXT,
    last_error TEXT,
    sent_bytes INTEGER NOT NULL DEFAULT 0,
    received_bytes INTEGER NOT NULL DEFAULT 0
);
