-- Stage 1 schema. Everything later stages need exists now, mostly unfilled,
-- so Stages 2-6 add rows and not columns.
-- All timestamps are ISO-8601 UTC strings ('2026-09-27T14:03:11Z').

-- ---------------------------------------------------------------- companies
CREATE TABLE companies (
    id                   INTEGER PRIMARY KEY,
    norm_name            TEXT NOT NULL UNIQUE,   -- lowercased, legal suffixes stripped
    display_name         TEXT NOT NULL,
    website              TEXT,
    ats_kind             TEXT CHECK (ats_kind IN
                             ('greenhouse','lever','ashby','smartrecruiters','workday','other')),
    ats_slug             TEXT,                   -- workday: "tenant/wdN/site"
    notes                TEXT,
    school_selective     INTEGER NOT NULL DEFAULT 0,
    from_watchlist       INTEGER NOT NULL DEFAULT 0,
    priority             INTEGER NOT NULL DEFAULT 0,  -- >0 nudges triage toward tier A
    expected_open_months TEXT,                   -- json array of month numbers, or NULL
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- ------------------------------------------------------------------- tracks
-- Mirror of tracks.yaml, re-synced on every run. The yaml stays the source of
-- truth for rules; this table exists so other rows can reference a track by name.
CREATE TABLE tracks (
    name              TEXT PRIMARY KEY,
    weekly_target     INTEGER NOT NULL DEFAULT 0,
    triage_depth      TEXT NOT NULL CHECK (triage_depth IN ('none','rules','llm')),
    default_resume    TEXT,                      -- resumes.label, not enforced: yaml may lead
    active            INTEGER NOT NULL DEFAULT 1,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    config_json       TEXT NOT NULL,             -- the yaml block verbatim, for provenance
    updated_at        TEXT NOT NULL
);

-- ------------------------------------------------------------------ resumes
CREATE TABLE resumes (
    id             INTEGER PRIMARY KEY,
    label          TEXT NOT NULL UNIQUE,
    file_path      TEXT NOT NULL,
    file_hash      TEXT NOT NULL,                -- sha256; doctor warns when it drifts
    description    TEXT,                         -- I write this; Stage 3 shows it to the model
    default_tracks TEXT,                         -- json array of track names
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

-- --------------------------------------------------------------------- jobs
CREATE TABLE jobs (
    id                    INTEGER PRIMARY KEY,
    company_id            INTEGER NOT NULL REFERENCES companies(id),
    title                 TEXT NOT NULL,
    location              TEXT,
    is_remote             INTEGER,
    url                   TEXT NOT NULL,
    apply_url             TEXT,
    season                TEXT,                  -- 'Summer 2027', 'part-time', NULL
    jd_text               TEXT,
    jd_hash               TEXT,                  -- sha256 of jd_text; re-triage when it changes
    dedupe_key            TEXT NOT NULL UNIQUE,  -- norm_company|norm_title|norm_location
    posted_at             TEXT,
    deadline_at           TEXT,
    opens_at              TEXT,                  -- estimated opening date (Stage 2)
    first_seen_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    closed_at             TEXT,
    is_active             INTEGER NOT NULL DEFAULT 1,
    -- rules-only eligibility (Stage 2). 'unknown' = not evaluated yet.
    eligibility           TEXT NOT NULL DEFAULT 'unknown'
                          CHECK (eligibility IN ('eligible','ineligible','ambiguous','boosted','unknown')),
    eligibility_reason    TEXT,                  -- includes the phrase that matched
    -- triage (Stage 3)
    tier                  TEXT CHECK (tier IN ('A','B','C')),
    tier_source           TEXT CHECK (tier_source IN ('rules','llm','manual')),
    tier_reason           TEXT,
    resume_id             INTEGER REFERENCES resumes(id),
    edit_notes            TEXT,                  -- json array of short notes, never resume text
    triaged_at            TEXT,
    school_selective      INTEGER NOT NULL DEFAULT 0,
    school_selective_note TEXT,
    -- my workflow state, independent of applications.status
    queue_state           TEXT NOT NULL DEFAULT 'new'
                          CHECK (queue_state IN ('new','queued','later','skipped','not_eligible','applied')),
    my_notes              TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX jobs_company_idx   ON jobs(company_id);
CREATE INDEX jobs_queue_idx     ON jobs(queue_state, tier);
CREATE INDEX jobs_last_seen_idx ON jobs(last_seen_at);
CREATE INDEX jobs_active_idx    ON jobs(is_active, posted_at);

-- Every source a job was ever found in is kept.
CREATE TABLE job_sources (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source        TEXT NOT NULL REFERENCES sources(name),
    source_job_id TEXT,                          -- adapter's own id (Simplify UUID, etc.)
    source_url    TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    raw_json      TEXT,
    UNIQUE (source, source_job_id)
);
CREATE INDEX job_sources_job_idx ON job_sources(job_id);

-- A job can belong to several tracks.
CREATE TABLE job_tracks (
    job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    track        TEXT NOT NULL REFERENCES tracks(name),
    match_reason TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (job_id, track)
);

-- ------------------------------------------------------------- applications
CREATE TABLE applications (
    id               INTEGER PRIMARY KEY,
    job_id           INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    track            TEXT REFERENCES tracks(name),
    resume_id        INTEGER REFERENCES resumes(id),
    status           TEXT NOT NULL DEFAULT 'applied'
                     CHECK (status IN ('applied','oa','interview','offer','rejected','ghosted','withdrawn')),
    applied_at       TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,              -- drives "days since" and ghost detection
    next_action_at   TEXT,
    next_action_note TEXT,
    notes            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX applications_status_idx ON applications(status, applied_at);
CREATE INDEX applications_next_idx   ON applications(next_action_at);

-- Append-only history. Nothing here is ever updated or deleted:
--   state='confirmed'     it happened (manual entry, import, or a proposal I accepted)
--   state='proposed'      Gmail (Stage 6) thinks it happened; waiting on my keypress
--   state='rejected_by_me' I looked at a proposal and said no
-- Undo appends a reverting row pointing at what it undoes.
CREATE TABLE status_events (
    id               INTEGER PRIMARY KEY,
    application_id   INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    from_status      TEXT,
    to_status        TEXT NOT NULL,
    occurred_at      TEXT NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('manual','gmail','rule','import')),
    evidence         TEXT,                       -- gmail id + subject, rule name, csv row
    state            TEXT NOT NULL DEFAULT 'confirmed'
                     CHECK (state IN ('confirmed','proposed','rejected_by_me')),
    undoes_event_id  INTEGER REFERENCES status_events(id),
    created_at       TEXT NOT NULL
);
CREATE INDEX status_events_app_idx   ON status_events(application_id, id);
CREATE INDEX status_events_state_idx ON status_events(state);

-- -------------------------------------------------------------- screening Q
-- The unanswered-questions queue. Answers themselves live in config/answers.yaml,
-- which I write by hand; nothing in this table is ever an invented answer.
CREATE TABLE screening_questions (
    id                 INTEGER PRIMARY KEY,
    text               TEXT NOT NULL,
    norm_hash          TEXT NOT NULL UNIQUE,     -- sha256 of the normalized question
    first_seen_job_id  INTEGER REFERENCES jobs(id),
    times_seen         INTEGER NOT NULL DEFAULT 1,
    answered           INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

-- ---------------------------------------------------------------- reminders
CREATE TABLE reminders (
    id             INTEGER PRIMARY KEY,
    job_id         INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('followup','deadline','opens','custom')),
    due_at         TEXT NOT NULL,
    note           TEXT,
    done_at        TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX reminders_due_idx ON reminders(done_at, due_at);

-- ------------------------------------------------------------------ sources
-- Adapter health. A rate-limited or failing source must never look like "0 jobs".
CREATE TABLE sources (
    name            TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    fragile         INTEGER NOT NULL DEFAULT 0,
    last_run_at     TEXT,
    last_success_at TEXT,
    last_status     TEXT CHECK (last_status IN ('ok','partial','error','rate_limited')),
    last_error      TEXT,
    last_count      INTEGER,
    config_json     TEXT,
    updated_at      TEXT NOT NULL
);

-- ---------------------------------------------------------------- llm cache
CREATE TABLE llm_cache (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,                 -- triage | resume_pick | edit_notes
    model         TEXT NOT NULL,
    input_hash    TEXT NOT NULL,                 -- sha256 of the exact prompt payload
    response_json TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    created_at    TEXT NOT NULL,
    UNIQUE (job_id, kind, model, input_hash)
);

-- --------------------------------------------------------------------- runs
CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN
                    ('discover','triage','finder','mail','import','digest','demo')),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats_json  TEXT,                            -- counts, cost, cap hits, per-source timings
    error       TEXT
);

-- --------------------------------------------------------- firm candidates
-- Stage 5. A rejected firm is never proposed again (matched on norm_name).
CREATE TABLE firm_candidates (
    id              INTEGER PRIMARY KEY,
    norm_name       TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    area            TEXT,                        -- skills.yaml area that proposed it
    score           REAL,
    reason          TEXT,                        -- one line, why it matched me
    ats_kind        TEXT,
    ats_slug        TEXT,
    board_url       TEXT,
    evidence        TEXT,
    proposed_run_id INTEGER REFERENCES runs(id),
    decision        TEXT NOT NULL DEFAULT 'pending'
                    CHECK (decision IN ('pending','approved','rejected')),
    decided_at      TEXT,
    created_at      TEXT NOT NULL
);
