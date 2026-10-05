-- Which jobs ask which screening question, so `applyai questions collect` can
-- be re-run without inflating counts: one row per (question, job), whatever
-- the number of runs. The form's own choices are kept per job because two
-- companies can offer different options for the same question.
-- Rows come from collect (read from public ATS endpoints) and from the filler /
-- the Apply tab's `q` (those leave field_kind and choices NULL).
CREATE TABLE question_jobs (
    question_id INTEGER NOT NULL REFERENCES screening_questions(id) ON DELETE CASCADE,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    field_kind  TEXT,                      -- text | textarea | select | multiselect | radio | checkbox | boolean | date | number
    required    INTEGER NOT NULL DEFAULT 0,
    choices     TEXT,                      -- json array of the choices this job's form offers, or NULL
    demographic INTEGER NOT NULL DEFAULT 0, -- asked in an EEO / self-identification section
    seen_at     TEXT NOT NULL,
    PRIMARY KEY (question_id, job_id)
);
CREATE INDEX question_jobs_job_idx ON question_jobs(job_id);

-- Questions logged before this table existed keep the job they were first seen on.
INSERT INTO question_jobs (question_id, job_id, seen_at)
SELECT id, first_seen_job_id, updated_at FROM screening_questions
WHERE first_seen_job_id IS NOT NULL AND first_seen_job_id IN (SELECT id FROM jobs);
