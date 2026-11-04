-- foreign_keys: off
-- Apply attempt records (kitty.core.apply.attempts). jobs.fill_* stays the latest
-- preparation snapshot; every attempt also appends one apply_attempts row so stage,
-- outcome and cause rates can be measured per ATS family. `runs` gains kind 'apply'
-- (SQLite cannot change a CHECK in place: the documented table rebuild, ids kept;
-- proposals.proposed_run_id keeps pointing at the same ids).
--
-- Nothing here stores a field value. Labels name questions, never answers; value
-- hashes are salted SHA-256 with a per-attempt salt kept only in memory.

CREATE TABLE runs_new (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN
                    ('discover','triage','finder','mail','import','digest','demo','apply')),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats_json  TEXT,                            -- counts, cost, cap hits, per-source timings
    error       TEXT
);
INSERT INTO runs_new (id, kind, started_at, finished_at, stats_json, error)
SELECT id, kind, started_at, finished_at, stats_json, error FROM runs;
DROP TABLE runs;
ALTER TABLE runs_new RENAME TO runs;

-- One row per attempt, written once when the attempt ends; never updated.
CREATE TABLE apply_attempts (
    id               INTEGER PRIMARY KEY,
    run_id           INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    job_id           INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ats_family       TEXT NOT NULL,              -- kitty.core.apply.urls.detect_ats
    tenant_origin    TEXT,                       -- scheme://host[:port] of the form
    started_at       TEXT NOT NULL,
    ended_at         TEXT NOT NULL,
    duration_ms      INTEGER NOT NULL CHECK (duration_ms >= 0),
    stage            TEXT NOT NULL CHECK (stage IN
                         ('live-check','entry','account','verify','page','review')),
    page_number      INTEGER CHECK (page_number IS NULL OR page_number >= 1),
    page_name        TEXT,
    outcome          TEXT NOT NULL CHECK (outcome IN
                         ('ready','needs_owner','unsupported','failed','closed')),
    cause            TEXT NOT NULL,              -- closed vocabulary: attempts.CAUSES
    failing_fields   TEXT NOT NULL DEFAULT '[]', -- JSON list of field labels, never values
    owner_inputs     INTEGER NOT NULL DEFAULT 0 CHECK (owner_inputs >= 0),
    hide_automation  INTEGER NOT NULL CHECK (hide_automation IN (0, 1))
);
CREATE INDEX apply_attempts_job_idx ON apply_attempts(job_id, id);
CREATE INDEX apply_attempts_family_idx ON apply_attempts(ats_family, outcome);
CREATE TRIGGER apply_attempts_append_only BEFORE UPDATE ON apply_attempts
BEGIN
    SELECT RAISE(ABORT, 'apply_attempts is append-only');
END;

-- Each field the attempt wrote, with where its value came from; written with the attempt.
CREATE TABLE apply_attempt_fields (
    attempt_id  INTEGER NOT NULL REFERENCES apply_attempts(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL CHECK (position >= 0),
    label       TEXT NOT NULL,
    provenance  TEXT NOT NULL,                   -- class or class:<detail>: attempts.PROVENANCE
    ready_hash  TEXT,                            -- value hash at ready; NULL otherwise
    PRIMARY KEY (attempt_id, position)
);
CREATE TRIGGER apply_attempt_fields_append_only BEFORE UPDATE ON apply_attempt_fields
BEGIN
    SELECT RAISE(ABORT, 'apply_attempt_fields is append-only');
END;

-- After release: the latest value hash the read-only watcher (poll) or the Submit
-- capture listener (click) saw for each hashed field. Overwritten until confirmation.
CREATE TABLE apply_field_snapshots (
    attempt_id   INTEGER NOT NULL REFERENCES apply_attempts(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    value_hash   TEXT NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('poll', 'click')),
    observed_at  TEXT NOT NULL,
    PRIMARY KEY (attempt_id, position)
);

-- At the confirmation page: whether the owner changed each compared field (an owner
-- edit is a kitty error for that field's provenance class). Written once.
CREATE TABLE apply_field_checks (
    attempt_id    INTEGER NOT NULL REFERENCES apply_attempts(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    edited        INTEGER NOT NULL CHECK (edited IN (0, 1)),
    confirmed_at  TEXT NOT NULL,
    PRIMARY KEY (attempt_id, position)
);
CREATE TRIGGER apply_field_checks_append_only BEFORE UPDATE ON apply_field_checks
BEGIN
    SELECT RAISE(ABORT, 'apply_field_checks is append-only');
END;

-- The answers.yaml/profile.yaml digest screening_questions.answered was last refreshed for.
CREATE TABLE question_flag_refresh (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    config_digest TEXT NOT NULL,
    refreshed_at  TEXT NOT NULL
);
