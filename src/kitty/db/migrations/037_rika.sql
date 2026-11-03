-- Rika, rika!'s own agent (kitty.core.rika): its conversation, the actions it took and
-- the outward or irreversible actions waiting on the user's approval.

-- One row per user request and per Rika reply. A reply stays 'pending' while it
-- streams or waits on an approval; reply_to links it to the request it answers.
CREATE TABLE rika_messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'rika')),
    text            TEXT NOT NULL,
    reply_to        TEXT REFERENCES rika_messages(id) ON DELETE SET NULL,
    tier            TEXT CHECK (tier IN ('fast', 'standard', 'deep')),
    model           TEXT,
    status          TEXT NOT NULL DEFAULT 'done' CHECK (status IN ('pending', 'done', 'failed')),
    cost_usd        TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX rika_messages_created ON rika_messages(created_at);
CREATE INDEX rika_messages_conversation ON rika_messages(conversation_id, created_at);

-- One row per tool call that changed something. undo_json is the tool's own undo
-- payload (for example calendar's history id), present only for reversible actions.
CREATE TABLE rika_actions (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES rika_messages(id) ON DELETE CASCADE,
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    summary     TEXT NOT NULL,
    reversible  INTEGER NOT NULL CHECK (reversible IN (0, 1)),
    undo_json   TEXT,
    status      TEXT NOT NULL CHECK (status IN ('done', 'undone', 'failed')),
    created_at  TEXT NOT NULL,
    undone_at   TEXT
);
CREATE INDEX rika_actions_created ON rika_actions(created_at);

-- An outward or irreversible tool call (or a model run above the daily budget) that
-- waits for one tap. A restart expires whatever was still pending.
CREATE TABLE rika_approvals (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES rika_messages(id) ON DELETE CASCADE,
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    summary     TEXT NOT NULL,
    detail      TEXT NOT NULL,
    cost_usd    TEXT,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    created_at  TEXT NOT NULL,
    decided_at  TEXT
);
CREATE INDEX rika_approvals_created ON rika_approvals(created_at);

-- Settings → AI: the OpenRouter model of each Rika tier; '' uses the default for the
-- provider family of the main model (kitty.core.rika.router.DEFAULTS).
ALTER TABLE ai_settings ADD COLUMN rika_fast_model TEXT NOT NULL DEFAULT '';
ALTER TABLE ai_settings ADD COLUMN rika_standard_model TEXT NOT NULL DEFAULT '';
ALTER TABLE ai_settings ADD COLUMN rika_deep_model TEXT NOT NULL DEFAULT '';
