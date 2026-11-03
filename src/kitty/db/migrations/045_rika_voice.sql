-- Rika phase 2a: voice and brain dumps (docs/rika-design.md Q3–Q8, kitty.core.rika).

-- How a request reached Rika: typed in the bar, spoken (Rika then speaks a short
-- summary of its reply, Q6 C) or the transcript of a brain dump (Q7 A).
ALTER TABLE rika_messages ADD COLUMN source TEXT NOT NULL DEFAULT 'typed'
    CHECK (source IN ('typed', 'voice', 'dump'));

-- One plan per brain dump: its items grouped by area, what the user ticked and what
-- each item made. message_id is Rika's reply holding the plan, request_id the dump.
-- The dump note (Q8 A) keeps the transcript; note_block_id is its "Created from this
-- dump" block, which lists links to everything the plan made once it ran.
CREATE TABLE rika_plans (
    id            TEXT PRIMARY KEY,
    message_id    TEXT NOT NULL REFERENCES rika_messages(id) ON DELETE CASCADE,
    request_id    TEXT NOT NULL REFERENCES rika_messages(id) ON DELETE CASCADE,
    items_json    TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    duration_s    REAL NOT NULL CHECK (duration_s >= 0),
    note_page_id  INTEGER REFERENCES note_pages(id) ON DELETE SET NULL,
    note_block_id TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'applied', 'dismissed', 'failed')),
    created_at    TEXT NOT NULL,
    decided_at    TEXT
);
CREATE INDEX rika_plans_created ON rika_plans(created_at);

-- Settings → AI: the on-device Whisper model voice input uses (kitty.core.rika.voice.MODELS).
ALTER TABLE ai_settings ADD COLUMN rika_voice_model TEXT NOT NULL DEFAULT 'base';
