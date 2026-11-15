-- Agent usage (kitty.core.agent_usage): what Claude Code, Codex and omp spent, read from
-- their own local session logs once the user turns it on per agent kind. Numbers only:
-- no prompt, reply, title or tool argument is ever stored. Rika's and the in-app AI's
-- usage is not copied here; it is read from rika_messages, ai_reviews and llm_cache.

-- One session log rika! has read, how far, and that session's running totals.
CREATE TABLE usage_files (
    key            TEXT PRIMARY KEY,           -- sha256(path)[:16]; the page only ever sees this
    path           TEXT NOT NULL UNIQUE,
    agent          TEXT NOT NULL CHECK (agent IN ('claude', 'codex', 'omp')),
    parent         TEXT,                       -- key of the session a Claude subagent log belongs to
    session        TEXT,
    cwd            TEXT,
    inode          INTEGER NOT NULL,
    size           INTEGER NOT NULL,
    mtime_ns       INTEGER NOT NULL,
    offset         INTEGER NOT NULL DEFAULT 0, -- end of the last complete line read
    first_t        INTEGER,                    -- epoch seconds of the first and last request
    last_t         INTEGER,
    model          TEXT,                       -- of the last main request
    context_tokens INTEGER,                    -- prompt size of the last main request
    context_window INTEGER,
    tool_calls     INTEGER NOT NULL DEFAULT 0,
    changed        TEXT NOT NULL DEFAULT '[]', -- hashes of the paths its edit tools named
    state          TEXT NOT NULL DEFAULT '{}', -- the parser's cursor (request ids seen, Codex's running total)
    skipped        INTEGER NOT NULL DEFAULT 0  -- lines that did not parse
);
CREATE INDEX usage_files_last ON usage_files(last_t);

-- A log's requests summed per five minutes, model and role.
CREATE TABLE agent_usage (
    file_key    TEXT NOT NULL REFERENCES usage_files(key) ON DELETE CASCADE,
    t           INTEGER NOT NULL,              -- epoch seconds, start of the five minutes
    model       TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('main', 'subagent', 'aux')),
    requests    INTEGER NOT NULL DEFAULT 0,
    priced      INTEGER NOT NULL DEFAULT 0,    -- requests that carry a cost
    input       INTEGER NOT NULL DEFAULT 0,
    cache_read  INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    output      INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (file_key, t, model, role)
) WITHOUT ROWID;
CREATE INDEX agent_usage_t ON agent_usage(t);
