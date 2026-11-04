-- Agent mediation (paper §13, docs/platform-design.md "Agent mediation"): agent tabs that
-- confined themselves get a scoped bearer token of the new client kind `agent-session`
-- (hashed like a paired client's), every read and write such a token makes is journaled
-- in `agent_access` in the same transaction, policies are versioned, and retention
-- reasons keep journal rows and the op_log rows they cite past the History window.

-- clients.kind gains 'agent-session' (SQLite cannot widen a CHECK in place). Nothing
-- references clients yet, so the rebuild needs no foreign-key pause.
CREATE TABLE clients_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('tern-plugin', 'browser-extension', 'agent-session')),
    scopes TEXT NOT NULL,
    origin TEXT,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT,
    CHECK ((kind = 'browser-extension') = (origin IS NOT NULL))
);
INSERT INTO clients_new SELECT id, name, kind, scopes, origin, token_hash, created_at, last_used_at, revoked_at FROM clients;
DROP TABLE clients;
ALTER TABLE clients_new RENAME TO clients;
CREATE INDEX clients_origin ON clients(origin) WHERE revoked_at IS NULL;

-- One launch of an agent through kitty-agent-launch. `id` is the actor session its block
-- writes carry (op_log.actor_session). `state`: pending (nonce issued), confined (token
-- issued), unconfined (the launcher ran without confinement: no token), refused (the
-- launcher stopped the agent), expired (the nonce was never exchanged), ended (the agent
-- exited or the token was revoked). `guarantee` is what the UI shows (paper §13's kernel
-- matrix): full, attribution (writes carry the session; completeness is not claimed) or
-- none (no exchange happened). `report` is the launcher's JSON report plus what rika!
-- checked; `baseline` the planted-file baseline taken at launch (.envrc, git hooks).
CREATE TABLE agent_sessions (
    id TEXT PRIMARY KEY,
    client_id TEXT REFERENCES clients(id),
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    host TEXT NOT NULL CHECK (host IN ('herdr', 'tern', 'direct')),
    pane TEXT,
    cwd TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'confined', 'unconfined', 'refused', 'expired', 'ended')),
    guarantee TEXT NOT NULL CHECK (guarantee IN ('full', 'attribution', 'none')),
    abi INTEGER,
    pid INTEGER,
    pid_start INTEGER,
    report TEXT,
    baseline TEXT,
    message TEXT,
    policy_version INTEGER,
    created_at TEXT NOT NULL,
    confined_at TEXT,
    ended_at TEXT
);
CREATE INDEX agent_sessions_created ON agent_sessions(created_at);

-- The journal: one row per record an agent token read or wrote (block_id NULL for a
-- call that returns no record, e.g. the tag list), written in the transaction of the
-- access itself. `purpose` is the record's label (kitty.core.agent_policy.PURPOSES);
-- `seq` the op_log sequence the read reflects or the write made; `decision` what the
-- policy in force (`policy_version`, `mode`) decided. Rows are never updated; they are
-- deleted only by compaction once no retention reason covers them.
CREATE TABLE agent_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL REFERENCES agent_sessions(id),
    tool TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('read', 'write')),
    purpose TEXT NOT NULL,
    block_id TEXT,
    seq INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('allow', 'deny')),
    mode TEXT NOT NULL CHECK (mode IN ('enforce', 'observe')),
    policy_version INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX agent_access_session ON agent_access(session, id);
CREATE INDEX agent_access_block ON agent_access(block_id, seq) WHERE block_id IS NOT NULL;
CREATE INDEX agent_access_at ON agent_access(at);
CREATE TRIGGER agent_access_append_only BEFORE UPDATE ON agent_access
BEGIN SELECT RAISE(ABORT, 'agent_access is append-only'); END;

-- Applied agent policies; the highest version is in force (none: the built-in version 0).
-- `body` is the validated policy as JSON, `source` the YAML as the user wrote it.
CREATE TABLE agent_policies (
    version INTEGER PRIMARY KEY CHECK (version >= 1),
    body TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- User holds (kitty.core.blocks.retention): keep one agent session's journal rows past the
-- History window, with the op_log rows from `seq` (the session's oldest journaled seq) on.
-- The other retention reasons are derived, not stored: the replay window (journal rows
-- inside the History window) and pinned ((id@seq)) citations in live block text. A hold the
-- size cap had to drop is kept, released.
CREATE TABLE retention_holds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL REFERENCES agent_sessions(id),
    seq INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    released_at TEXT,
    released_by TEXT CHECK (released_by IN ('user', 'size_cap'))
);
CREATE UNIQUE INDEX retention_holds_live ON retention_holds(session) WHERE released_at IS NULL;
