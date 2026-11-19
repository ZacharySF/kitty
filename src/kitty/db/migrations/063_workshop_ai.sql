CREATE TABLE workshop_ai_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    model_id TEXT NOT NULL DEFAULT '',
    max_cost_usd TEXT NOT NULL DEFAULT '0.10',
    revision INTEGER NOT NULL DEFAULT 1
);
INSERT INTO workshop_ai_settings(id) VALUES(1);
CREATE TABLE workshop_facts (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT NOT NULL,
    confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE workshop_ai_previews (
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES workshop_docs(id),
    base_revision INTEGER NOT NULL, digest TEXT NOT NULL,
    snapshot_json TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','completed','failed'))
);
CREATE TRIGGER workshop_preview_immutable BEFORE UPDATE OF doc_id,base_revision,digest,snapshot_json,expires_at,created_at ON workshop_ai_previews
BEGIN SELECT RAISE(ABORT, 'Workshop preview snapshots are immutable'); END;
CREATE TABLE workshop_proposals (
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES workshop_docs(id),
    preview_id TEXT NOT NULL UNIQUE REFERENCES workshop_ai_previews(id),
    base_revision INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','applied','rejected','stale')),
    proposal_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER workshop_proposal_immutable BEFORE UPDATE OF doc_id,preview_id,base_revision,proposal_json,created_at ON workshop_proposals
BEGIN SELECT RAISE(ABORT, 'Workshop proposal content is immutable'); END;
CREATE TABLE workshop_ai_reviews (
    proposal_id TEXT PRIMARY KEY REFERENCES workshop_proposals(id),
    accepted_json TEXT NOT NULL, rejected_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE workshop_research (
    id TEXT PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL,
    snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER workshop_research_immutable BEFORE UPDATE ON workshop_research
BEGIN SELECT RAISE(ABORT, 'Workshop research snapshots are immutable'); END;
