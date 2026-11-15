-- Rika follow-ups: a plan is a brain dump's plan, a question about a date before a dated
-- item is added ('confirm'), or a check-in on a task whose day has passed ('checkin').
-- A follow-up is one "did you finish it?" Rika owes for a dated task.
ALTER TABLE rika_plans ADD COLUMN kind TEXT NOT NULL DEFAULT 'dump'
    CHECK (kind IN ('dump', 'confirm', 'checkin'));

CREATE TABLE rika_followups (
    id           TEXT PRIMARY KEY,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('task', 'study_task', 'event')),
    subject_id   TEXT NOT NULL,
    ask_at       TEXT NOT NULL,                -- UTC
    origin       TEXT NOT NULL CHECK (origin IN ('rika', 'user', 'overdue')),
    status       TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'asked', 'answered', 'dismissed', 'expired')),
    plan_id      TEXT REFERENCES rika_plans(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL,
    asked_at     TEXT,
    answered_at  TEXT,
    UNIQUE (subject_kind, subject_id, ask_at)
);
CREATE INDEX rika_followups_due ON rika_followups(status, ask_at);
