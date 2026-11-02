-- Paired outside programs (platform design Q7): the kitty Tern plugin and the browser
-- extension. Only a SHA-256 hash of each random 256-bit token is stored; the token
-- itself is handed to the client once. `origin` is the browser extension's own origin
-- (chrome-extension://<id> or moz-extension://<uuid>) that paired, or NULL for a
-- program outside a browser. Revoked rows stay for the record and never authenticate.
CREATE TABLE clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('tern-plugin', 'browser-extension')),
    scopes TEXT NOT NULL,
    origin TEXT,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT,
    CHECK ((kind = 'browser-extension') = (origin IS NOT NULL))
);
CREATE INDEX clients_origin ON clients(origin) WHERE revoked_at IS NULL;
