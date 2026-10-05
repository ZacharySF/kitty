-- Candidate artifacts and decisions are independent of the YAML track mirror.
CREATE TABLE resume_versions (
    id TEXT PRIMARY KEY,
    track TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate','promoted','rejected')),
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    resume_id INTEGER UNIQUE REFERENCES resumes(id),
    created_at TEXT NOT NULL
);
CREATE INDEX resume_versions_track_idx ON resume_versions(track, created_at);
CREATE TABLE resume_version_history (
    id INTEGER PRIMARY KEY,
    track TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('promote','reject','rollback')),
    version_id TEXT REFERENCES resume_versions(id),
    from_resume_id INTEGER REFERENCES resumes(id),
    to_resume_id INTEGER REFERENCES resumes(id),
    previous_history_id INTEGER REFERENCES resume_version_history(id),
    created_at TEXT NOT NULL
);
CREATE TABLE resume_default_overrides (
    track TEXT PRIMARY KEY,
    resume_id INTEGER REFERENCES resumes(id),
    history_id INTEGER REFERENCES resume_version_history(id)
);
CREATE TABLE resume_artifacts (
    resume_id INTEGER PRIMARY KEY REFERENCES resumes(id),
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    original_path TEXT NOT NULL
);
