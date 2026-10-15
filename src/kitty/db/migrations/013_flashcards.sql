-- Local flashcards, FSRS state, review undo, safe media and persisted review sessions.
CREATE TABLE flash_decks (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, parent_id INTEGER REFERENCES flash_decks(id),
 settings TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL
);
CREATE TABLE flash_types (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 fields TEXT NOT NULL, templates TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE flash_notes (
 id INTEGER PRIMARY KEY, guid TEXT NOT NULL UNIQUE, deck_id INTEGER NOT NULL REFERENCES flash_decks(id),
 type_id INTEGER NOT NULL REFERENCES flash_types(id), fields TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
 archived INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE flash_cards (
 id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES flash_notes(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'new', due TEXT NOT NULL,
 fsrs TEXT, suspended INTEGER NOT NULL DEFAULT 0, buried_until TEXT,
 lapses INTEGER NOT NULL DEFAULT 0, reviews INTEGER NOT NULL DEFAULT 0, leech INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1, UNIQUE(note_id,ordinal)
);
CREATE INDEX flash_due ON flash_cards(state,due);
CREATE TABLE flash_media (
 name TEXT PRIMARY KEY, mime TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL
);
CREATE TABLE flash_reviews (
 id INTEGER PRIMARY KEY, card_id INTEGER REFERENCES flash_cards(id) ON DELETE SET NULL,
 deck_id INTEGER REFERENCES flash_decks(id), rating INTEGER NOT NULL, reviewed_at TEXT NOT NULL,
 local_day TEXT NOT NULL, duration_ms INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL,
 before_state TEXT NOT NULL, after_revision INTEGER, sibling_state TEXT NOT NULL DEFAULT '[]',
 undone INTEGER NOT NULL DEFAULT 0, was_new INTEGER NOT NULL DEFAULT 0, was_review INTEGER NOT NULL DEFAULT 0,
 session_token TEXT
);
CREATE INDEX flash_review_day ON flash_reviews(deck_id,local_day,undone);
CREATE TABLE flash_studies (
 id TEXT PRIMARY KEY, mode TEXT NOT NULL, deck_id INTEGER REFERENCES flash_decks(id),
 search TEXT NOT NULL, card_ids TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE flash_sessions (
 token TEXT PRIMARY KEY, card_id INTEGER NOT NULL REFERENCES flash_cards(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, mode TEXT NOT NULL, created_at TEXT NOT NULL,
 revealed INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0,
 study_id TEXT REFERENCES flash_studies(id) ON DELETE CASCADE
);
