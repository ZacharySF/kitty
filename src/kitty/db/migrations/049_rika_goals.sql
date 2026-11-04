-- Rika phase 2b (docs/rika-design.md): routing memory and cost readouts (Q13), goals
-- (Q14), the morning brief and urgent alerts (Q15), email drafts with per-kind
-- auto-send (Q16), and the local memory index with editable preferences (Q18).

-- Q13 ---------------------------------------------------------------------------
-- Tokens of a Rika reply (summed over its model steps; NULL when no model ran) and
-- the route it took: {"path": [tool, ...], "signature": str, "remembered": bool,
-- "shortcut": bool}.
ALTER TABLE rika_messages ADD COLUMN tokens_in INTEGER;
ALTER TABLE rika_messages ADD COLUMN tokens_cached INTEGER;
ALTER TABLE rika_messages ADD COLUMN tokens_out INTEGER;
ALTER TABLE rika_messages ADD COLUMN route_json TEXT;

-- One kind of request, keyed by the tool-path signature that served it ("" is a reply
-- with no tool). `tier` is the cheapest tier that succeeded ('direct' = no model),
-- `path_json` the shortest successful tool path; tokens and cost are totals over uses.
CREATE TABLE rika_routes (
    signature   TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    tier        TEXT NOT NULL CHECK (tier IN ('direct', 'fast', 'standard', 'deep')),
    path_json   TEXT NOT NULL,
    uses        INTEGER NOT NULL DEFAULT 0,
    escalations INTEGER NOT NULL DEFAULT 0,
    tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd    TEXT NOT NULL DEFAULT '0',
    example     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- The request shapes (kitty.core.rika.routes.shape) that led to a route; the router
-- looks a request's shape up here before its heuristic.
CREATE TABLE rika_route_shapes (
    shape      TEXT PRIMARY KEY,
    signature  TEXT NOT NULL REFERENCES rika_routes(signature) ON DELETE CASCADE,
    updated_at TEXT NOT NULL
);
CREATE INDEX rika_route_shapes_signature ON rika_route_shapes(signature);

-- Learned shortcuts: a request template whose slots became one tool's arguments
-- verbatim; active once seen twice, after which it runs with no model.
CREATE TABLE rika_shortcuts (
    template       TEXT PRIMARY KEY,
    signature      TEXT NOT NULL REFERENCES rika_routes(signature) ON DELETE CASCADE,
    tool           TEXT NOT NULL,
    constants_json TEXT NOT NULL DEFAULT '{}',
    seen           INTEGER NOT NULL DEFAULT 1,
    uses           INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX rika_shortcuts_signature ON rika_shortcuts(signature);

-- Q14 ---------------------------------------------------------------------------
-- measures_json: [{id, label, target, unit, current, auto}] where auto is null or a
-- source computed from linked data (kitty.core.goals.AUTO).
CREATE TABLE goals (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
    why           TEXT NOT NULL DEFAULT '',
    target_date   TEXT,
    measures_json TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'done', 'dropped')),
    revision      INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE goal_links (
    goal_id    INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('note', 'block', 'task', 'job', 'study_session', 'math_topic',
                                             'opportunity', 'event', 'mail')),
    ref        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (goal_id, kind, ref)
);
CREATE INDEX goal_links_ref ON goal_links(kind, ref);

-- Q15 ---------------------------------------------------------------------------
-- What the daily sweep matched to a goal. Shown as "Rika found" on the goal and in the
-- brief of `brief_day`; urgent ones (strong and time-sensitive) also raise an alert.
CREATE TABLE goal_matches (
    id            INTEGER PRIMARY KEY,
    goal_id       INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('job', 'opportunity', 'mail', 'event')),
    ref           TEXT NOT NULL,
    title         TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '',
    score         REAL NOT NULL,
    urgent        INTEGER NOT NULL DEFAULT 0 CHECK (urgent IN (0, 1)),
    due_at        TEXT,
    classified_by TEXT NOT NULL DEFAULT 'keywords' CHECK (classified_by IN ('keywords', 'model')),
    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'linked', 'dismissed')),
    brief_day     TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (goal_id, kind, ref)
);
CREATE INDEX goal_matches_brief ON goal_matches(brief_day);

-- One morning brief per local day; message_id is the Rika message that announced it.
CREATE TABLE rika_briefs (
    day          TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    message_id   TEXT REFERENCES rika_messages(id) ON DELETE SET NULL,
    summary_json TEXT NOT NULL,
    seen_at      TEXT
);

-- Q16 ---------------------------------------------------------------------------
-- Mail Rika has read (Gmail, or the demo's fake mailbox). Bodies are untrusted data.
CREATE TABLE rika_mail_messages (
    id           TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    rfc_id       TEXT NOT NULL DEFAULT '',
    sender       TEXT NOT NULL DEFAULT '',
    sender_email TEXT NOT NULL,
    subject      TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    received_at  TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('scheduling', 'confirmation', 'thanks', 'question', 'personal', 'automated')),
    needs_reply  INTEGER NOT NULL CHECK (needs_reply IN (0, 1)),
    fetched_at   TEXT NOT NULL
);
CREATE INDEX rika_mail_messages_received ON rika_mail_messages(received_at);

-- Rika's replies. 'scheduled' is held on this machine until send_after (Gmail's API
-- has no undo-send), when it is sent; Undo-send moves it back to 'cancelled'.
CREATE TABLE rika_mail_drafts (
    id             TEXT PRIMARY KEY,
    mail_id        TEXT REFERENCES rika_mail_messages(id) ON DELETE SET NULL,
    message_id     TEXT REFERENCES rika_messages(id) ON DELETE SET NULL,
    thread_id      TEXT,
    to_address     TEXT NOT NULL,
    subject        TEXT NOT NULL,
    body           TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('scheduling', 'confirmation', 'thanks', 'question', 'personal', 'automated')),
    gmail_draft_id TEXT,
    status         TEXT NOT NULL CHECK (status IN ('draft', 'scheduled', 'sent', 'cancelled', 'discarded', 'failed')),
    auto           INTEGER NOT NULL DEFAULT 0 CHECK (auto IN (0, 1)),
    send_after     TEXT,
    sent_at        TEXT,
    error          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX rika_mail_drafts_created ON rika_mail_drafts(created_at);
CREATE INDEX rika_mail_drafts_status ON rika_mail_drafts(status, send_after);

-- Per-kind auto-send switches (Settings → Rika → Email), all off until confirmed on.
CREATE TABLE rika_mail_settings (
    kind         TEXT PRIMARY KEY CHECK (kind IN ('scheduling', 'confirmation', 'thanks')),
    auto_send    INTEGER NOT NULL DEFAULT 0 CHECK (auto_send IN (0, 1)),
    confirmed_at TEXT,
    updated_at   TEXT
);
INSERT INTO rika_mail_settings(kind) VALUES ('scheduling'), ('confirmation'), ('thanks');

CREATE TABLE rika_mail_config (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    hold_seconds INTEGER NOT NULL DEFAULT 30 CHECK (hold_seconds BETWEEN 10 AND 300),
    last_check   TEXT
);
INSERT INTO rika_mail_config(id) VALUES (1);

-- Q18 ---------------------------------------------------------------------------
-- Preferences Rika wrote when you stated one, or you added; editable in Settings → Rika → Memory.
CREATE TABLE rika_preferences (
    id         INTEGER PRIMARY KEY,
    text       TEXT NOT NULL CHECK (length(trim(text)) BETWEEN 1 AND 500),
    source     TEXT NOT NULL CHECK (source IN ('stated', 'added')),
    message_id TEXT REFERENCES rika_messages(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- The local search index over blocks, pages, the op log, jobs, calendar, mail, goals
-- and preferences (kitty.core.rika.memory). Rebuilt per source when its signature changes.
CREATE VIRTUAL TABLE rika_index USING fts5(
    title, body, source UNINDEXED, ref UNINDEXED, at UNINDEXED, tokenize = 'porter unicode61'
);
CREATE TABLE rika_index_sources (
    source       TEXT PRIMARY KEY,
    signature    TEXT NOT NULL,
    items        INTEGER NOT NULL,
    refreshed_at TEXT NOT NULL
);
