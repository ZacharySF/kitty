-- Durable, review-only tailoring queues. Source/posting snapshots never change.
CREATE TABLE workshop_batches (
    id TEXT PRIMARY KEY,
    master_id TEXT NOT NULL REFERENCES workshop_docs(id),
    master_revision INTEGER NOT NULL,
    master_snapshot TEXT NOT NULL,
    research INTEGER NOT NULL CHECK (research IN (0,1)),
    max_cost_usd TEXT NOT NULL,
    reserved_usd TEXT NOT NULL DEFAULT '0',
    status TEXT NOT NULL CHECK (status IN ('queued','running','done','cancelled')),
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE workshop_batch_items (
    batch_id TEXT NOT NULL REFERENCES workshop_batches(id),
    position INTEGER NOT NULL,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    job_snapshot TEXT NOT NULL,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','researching','tailoring','review','failed','cancelled')),
    doc_id TEXT REFERENCES workshop_docs(id),
    proposal_id TEXT,
    research_id TEXT,
    reserved_usd TEXT NOT NULL DEFAULT '0',
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (batch_id,position),
    UNIQUE (batch_id,job_id)
);
CREATE INDEX workshop_batches_created ON workshop_batches(created_at);
