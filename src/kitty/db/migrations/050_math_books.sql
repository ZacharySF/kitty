-- rika! math phase 2: Grinstead & Snell exercises and problems from the learner's own
-- books, each on its own FSRS schedule (fsrs.Card JSON, like math_mastery). The book's
-- text ships as data in third_party/grinstead-snell (GNU FDL); these rows reference an
-- exercise by id and never store its text. Own-book rows are references only.

-- One FSRS card per textbook exercise the learner has worked; created on the first rating.
CREATE TABLE math_textbook_cards (
    exercise_id TEXT PRIMARY KEY CHECK (length(exercise_id) BETWEEN 1 AND 64),
    fsrs        TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    reviews     INTEGER NOT NULL DEFAULT 0 CHECK (reviews >= 0),
    lapses      INTEGER NOT NULL DEFAULT 0 CHECK (lapses >= 0),
    last_rating INTEGER NOT NULL CHECK (last_rating BETWEEN 1 AND 4),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX math_textbook_cards_due ON math_textbook_cards(due_at);

-- One issued textbook exercise with a numeric answer, graded like math_attempts: the
-- first decided answer rates the exercise's card; `answers` keeps every try.
CREATE TABLE math_textbook_attempts (
    id          TEXT PRIMARY KEY CHECK (length(id) = 32),
    exercise_id TEXT NOT NULL CHECK (length(exercise_id) BETWEEN 1 AND 64),
    created_at  TEXT NOT NULL,
    answers     TEXT NOT NULL DEFAULT '[]',
    correct     INTEGER CHECK (correct IN (0, 1)),
    elapsed_ms  INTEGER CHECK (elapsed_ms >= 0),
    rating      INTEGER CHECK (rating BETWEEN 1 AND 4),
    graded_at   TEXT,
    solved_at   TEXT
);
CREATE INDEX math_textbook_attempts_exercise ON math_textbook_attempts(exercise_id, created_at);

-- A problem from one of the learner's own books, logged by reference (book, section,
-- problem number) with the learner's answer and confidence; never the book's text.
-- confidence is the first FSRS rating (1 couldn't do it … 4 certain).
CREATE TABLE math_book_problems (
    id          TEXT PRIMARY KEY CHECK (length(id) = 32),
    book        TEXT NOT NULL CHECK (length(book) BETWEEN 1 AND 120),
    section     TEXT NOT NULL DEFAULT '' CHECK (length(section) <= 60),
    problem     TEXT NOT NULL CHECK (length(problem) BETWEEN 1 AND 40),
    answer      TEXT NOT NULL DEFAULT '' CHECK (length(answer) <= 200),
    confidence  INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 4),
    topic_id    TEXT REFERENCES math_topics(id) ON DELETE SET NULL,
    fsrs        TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    reviews     INTEGER NOT NULL DEFAULT 0 CHECK (reviews >= 0),
    lapses      INTEGER NOT NULL DEFAULT 0 CHECK (lapses >= 0),
    last_rating INTEGER NOT NULL CHECK (last_rating BETWEEN 1 AND 4),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (book, section, problem)
);
CREATE INDEX math_book_problems_due ON math_book_problems(due_at);
