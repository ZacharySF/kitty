CREATE TABLE application_recovery_events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('inspect','wait','retry','reload','handoff','outcome')),
    result TEXT NOT NULL CHECK(result IN ('planned','observed','completed','failed','denied','cancelled','handed_off')),
    reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 64),
    step INTEGER NOT NULL DEFAULT 0 CHECK(step >= 0)
);
CREATE INDEX application_recovery_history ON application_recovery_events(job_id, id);
