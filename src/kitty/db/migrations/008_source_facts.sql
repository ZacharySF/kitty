-- Published source dates are not personal reminders.
ALTER TABLE jobs ADD COLUMN source_deadline_at TEXT;
ALTER TABLE jobs ADD COLUMN source_opens_at TEXT;
CREATE TABLE source_facts (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    observed_at TEXT NOT NULL,
    authority INTEGER NOT NULL,
    value_json TEXT NOT NULL,
    accepted INTEGER NOT NULL CHECK (accepted IN (0, 1))
);
CREATE INDEX source_facts_choice ON source_facts(job_id, field, accepted, id DESC);
