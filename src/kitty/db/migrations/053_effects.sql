-- The effects kernel (kitty.core.effects; kitty paper §16): every risky action is a
-- proposed effect, approved by digest, re-checked and executed through one protocol.
-- `effects` is one row per proposal: its kind, canonical JSON payload, the precondition
-- values the proposer saw, what the person reviews (`preview`), the SHA-256 `digest` of
-- (kind, payload, preconditions) and the proposing actor (as op_log's actor columns).
-- `effect_events` is every state transition: proposed, approved (with its own expiry and
-- the approving session), denied, executed, refused (a changed precondition or digest,
-- with the changed names in `detail`) or expired. Both tables are append-only. An effect
-- has at most one approval and one terminal event, so a second execution cannot commit.
-- (052 is the agent-mediation migration, PR #77.)
CREATE TABLE effects (
    id            TEXT PRIMARY KEY CHECK (length(id) = 32),
    kind          TEXT NOT NULL,
    payload       TEXT NOT NULL CHECK (json_valid(payload)),
    preconditions TEXT NOT NULL CHECK (json_valid(preconditions)),
    preview       TEXT NOT NULL CHECK (json_valid(preview)),
    digest        TEXT NOT NULL CHECK (length(digest) = 64),
    actor         TEXT NOT NULL CHECK (actor IN ('user', 'agent', 'client', 'system', 'peer')),
    actor_name    TEXT,
    actor_session TEXT,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);

CREATE TABLE effect_events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    effect_id     TEXT NOT NULL REFERENCES effects(id),
    state         TEXT NOT NULL CHECK (state IN ('proposed', 'approved', 'denied', 'executed', 'refused', 'expired')),
    actor         TEXT NOT NULL CHECK (actor IN ('user', 'agent', 'client', 'system', 'peer')),
    actor_name    TEXT,
    actor_session TEXT,
    digest        TEXT,
    detail        TEXT CHECK (detail IS NULL OR json_valid(detail)),
    at            TEXT NOT NULL,
    expires_at    TEXT
);

CREATE INDEX effect_events_effect ON effect_events(effect_id, seq);
CREATE INDEX effects_created ON effects(created_at);
CREATE UNIQUE INDEX effect_events_one_proposal ON effect_events(effect_id) WHERE state = 'proposed';
CREATE UNIQUE INDEX effect_events_one_approval ON effect_events(effect_id) WHERE state = 'approved';
CREATE UNIQUE INDEX effect_events_one_end ON effect_events(effect_id)
    WHERE state IN ('denied', 'executed', 'refused', 'expired');

CREATE TRIGGER effects_append_only_update BEFORE UPDATE ON effects
BEGIN SELECT RAISE(ABORT, 'effects is append-only'); END;
CREATE TRIGGER effects_append_only_delete BEFORE DELETE ON effects
BEGIN SELECT RAISE(ABORT, 'effects is append-only'); END;
CREATE TRIGGER effect_events_append_only_update BEFORE UPDATE ON effect_events
BEGIN SELECT RAISE(ABORT, 'effect_events is append-only'); END;
CREATE TRIGGER effect_events_append_only_delete BEFORE DELETE ON effect_events
BEGIN SELECT RAISE(ABORT, 'effect_events is append-only'); END;
-- An approval follows only the proposal, execution only the approval, and denial,
-- refusal or expiry either of them; nothing follows an end.
CREATE TRIGGER effect_events_transition BEFORE INSERT ON effect_events
WHEN NEW.state <> 'proposed' AND COALESCE(
    (SELECT state FROM effect_events WHERE effect_id = NEW.effect_id ORDER BY seq DESC LIMIT 1), '')
    NOT IN (CASE NEW.state WHEN 'approved' THEN 'proposed' WHEN 'executed' THEN 'approved' ELSE 'proposed' END,
            CASE NEW.state WHEN 'approved' THEN 'proposed' ELSE 'approved' END)
BEGIN SELECT RAISE(ABORT, 'invalid effect transition'); END;
