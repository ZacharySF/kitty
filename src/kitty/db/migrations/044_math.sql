-- rika! math. The topic graph is authored in kitty.core.math.topics and synced here;
-- the trigger keeps math_prereqs acyclic whatever writes it.
CREATE TABLE math_topics (
    id       TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 64),
    strand   TEXT NOT NULL,
    title    TEXT NOT NULL,
    position INTEGER NOT NULL
);

-- topic_id needs prereq_id mastered first; credit is the FIRe-lite weight a correct
-- answer on topic_id passes to prereq_id (0 = prerequisite only).
CREATE TABLE math_prereqs (
    topic_id  TEXT NOT NULL REFERENCES math_topics(id) ON DELETE CASCADE,
    prereq_id TEXT NOT NULL REFERENCES math_topics(id) ON DELETE CASCADE,
    credit    REAL NOT NULL DEFAULT 0 CHECK (credit BETWEEN 0 AND 1),
    PRIMARY KEY (topic_id, prereq_id),
    CHECK (topic_id <> prereq_id)
);
CREATE INDEX math_prereqs_prereq ON math_prereqs(prereq_id);

CREATE TRIGGER math_prereqs_acyclic_insert BEFORE INSERT ON math_prereqs
WHEN EXISTS (
    WITH RECURSIVE above(id) AS (
        SELECT NEW.prereq_id
        UNION SELECT p.prereq_id FROM math_prereqs p JOIN above ON p.topic_id = above.id
    ) SELECT 1 FROM above WHERE id = NEW.topic_id
)
BEGIN SELECT RAISE(ABORT, 'math prerequisite cycle'); END;

CREATE TRIGGER math_prereqs_acyclic_update BEFORE UPDATE OF topic_id, prereq_id ON math_prereqs
WHEN EXISTS (
    WITH RECURSIVE above(id) AS (
        SELECT NEW.prereq_id
        UNION SELECT p.prereq_id FROM math_prereqs p JOIN above ON p.topic_id = above.id
        WHERE NOT (p.topic_id = OLD.topic_id AND p.prereq_id = OLD.prereq_id)
    ) SELECT 1 FROM above WHERE id = NEW.topic_id
)
BEGIN SELECT RAISE(ABORT, 'math prerequisite cycle'); END;

-- Per-topic mastery. While learning, `run` counts consecutive correct first tries;
-- mastery starts the topic's FSRS card (`fsrs`, fsrs.Card JSON) and its reviews.
-- implicit_credit accumulates FIRe-lite credit until it is worth one implicit review.
CREATE TABLE math_mastery (
    topic_id        TEXT PRIMARY KEY REFERENCES math_topics(id) ON DELETE CASCADE,
    run             INTEGER NOT NULL DEFAULT 0 CHECK (run >= 0),
    mastered_at     TEXT,
    fsrs            TEXT,
    due_at          TEXT,
    implicit_credit REAL NOT NULL DEFAULT 0 CHECK (implicit_credit >= 0),
    reviews         INTEGER NOT NULL DEFAULT 0,
    implicit_reviews INTEGER NOT NULL DEFAULT 0,
    lapses          INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    CHECK ((mastered_at IS NULL) = (fsrs IS NULL))
);

-- One issued problem, reproducible from (template_id, seed[, step]). The first
-- decided answer is graded (correct, elapsed_ms, rating); `answers` keeps every try.
CREATE TABLE math_attempts (
    id          TEXT PRIMARY KEY CHECK (length(id) = 32),
    topic_id    TEXT NOT NULL REFERENCES math_topics(id) ON DELETE CASCADE,
    template_id TEXT NOT NULL,
    seed        INTEGER NOT NULL CHECK (seed >= 0),
    step        INTEGER CHECK (step >= 0),
    purpose     TEXT NOT NULL CHECK (purpose IN ('lesson', 'guided', 'review')),
    created_at  TEXT NOT NULL,
    answers     TEXT NOT NULL DEFAULT '[]',
    correct     INTEGER CHECK (correct IN (0, 1)),
    elapsed_ms  INTEGER CHECK (elapsed_ms >= 0),
    rating      INTEGER CHECK (rating BETWEEN 1 AND 4),
    graded_at   TEXT,
    solved_at   TEXT,
    CHECK ((purpose = 'guided') = (step IS NOT NULL))
);
CREATE INDEX math_attempts_topic ON math_attempts(topic_id, created_at);

-- A timed sprint; problems are regenerated from (mode, seed, ops, problem_count).
CREATE TABLE math_sprints (
    id            TEXT PRIMARY KEY CHECK (length(id) = 32),
    mode          TEXT NOT NULL CHECK (mode IN ('zetamac', 'mc')),
    duration_s    INTEGER NOT NULL CHECK (duration_s BETWEEN 10 AND 900),
    seed          INTEGER NOT NULL CHECK (seed >= 0),
    ops           TEXT NOT NULL,
    problem_count INTEGER NOT NULL CHECK (problem_count > 0),
    started_at    TEXT NOT NULL,
    score         INTEGER,
    wrong         INTEGER CHECK (wrong >= 0),
    elapsed_ms    INTEGER CHECK (elapsed_ms >= 0),
    finished_at   TEXT,
    CHECK ((finished_at IS NULL) = (score IS NULL))
);
CREATE INDEX math_sprints_finished ON math_sprints(finished_at);
