CREATE TABLE application_recovery_events_next (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('inspect','wait','focus','recommit','retry','reload','handoff','outcome')),
    result TEXT NOT NULL CHECK(result IN ('planned','observed','completed','no_effect','failed','denied','cancelled','handed_off')),
    reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 64),
    step INTEGER NOT NULL DEFAULT 0 CHECK(step >= 0)
);
INSERT INTO application_recovery_events_next (id,job_id,created_at,action,result,reason,step)
SELECT id,job_id,created_at,action,result,reason,step FROM application_recovery_events;
DROP TABLE application_recovery_events;
ALTER TABLE application_recovery_events_next RENAME TO application_recovery_events;
CREATE INDEX application_recovery_history ON application_recovery_events(job_id, id);
