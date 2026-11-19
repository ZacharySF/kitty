-- Workshop drafts never overwrite registered resumes or application selections.
CREATE TABLE workshop_docs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    format TEXT NOT NULL CHECK(format IN ('html','typst','latex')),
    source TEXT NOT NULL,
    assets TEXT NOT NULL DEFAULT '{}',
    entrypoint TEXT NOT NULL,
    engine TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    kind TEXT NOT NULL CHECK(kind IN ('master','tailored')),
    base_doc_id TEXT REFERENCES workshop_docs(id),
    base_revision INTEGER,
    job_id INTEGER REFERENCES jobs(id),
    company TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE workshop_revisions (
    doc_id TEXT NOT NULL REFERENCES workshop_docs(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    snapshot TEXT NOT NULL,
    author TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY(doc_id, revision)
);
CREATE TRIGGER workshop_revision_no_update BEFORE UPDATE ON workshop_revisions
BEGIN SELECT RAISE(ABORT, 'Workshop revisions are immutable'); END;
CREATE TRIGGER workshop_revision_no_delete BEFORE DELETE ON workshop_revisions
BEGIN SELECT RAISE(ABORT, 'Workshop revisions are immutable'); END;
CREATE TABLE workshop_exports (
    doc_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    resume_id INTEGER NOT NULL REFERENCES resumes(id),
    pdf_hash TEXT NOT NULL,
    job_id INTEGER REFERENCES jobs(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY(doc_id, revision),
    FOREIGN KEY(doc_id, revision) REFERENCES workshop_revisions(doc_id, revision)
);
CREATE TRIGGER workshop_export_no_update BEFORE UPDATE ON workshop_exports
BEGIN SELECT RAISE(ABORT, 'Workshop exports are immutable'); END;
CREATE TRIGGER workshop_export_no_delete BEFORE DELETE ON workshop_exports
BEGIN SELECT RAISE(ABORT, 'Workshop exports are immutable'); END;
CREATE INDEX workshop_docs_updated ON workshop_docs(archived, updated_at);
CREATE INDEX workshop_exports_resume ON workshop_exports(resume_id);
