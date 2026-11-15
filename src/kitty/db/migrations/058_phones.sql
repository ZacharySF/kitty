-- Phones (docs/GUIDE.md "Phone"): an iPhone reaches rika! through a second, off-by-default
-- listener that serves only /phone/v1/*. Each phone holds one scoped, revocable token;
-- only its SHA-256 is stored. Phones are their own table, not `clients`: a phone token
-- must never work on the loopback ingress, and a client token never on the phone surface.
CREATE TABLE phones (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scopes TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);

-- Where the phone listener listens: off (the default), this Wi-Fi's address, or the
-- Tailscale address. One row.
CREATE TABLE phone_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    network TEXT NOT NULL CHECK (network IN ('off', 'wifi', 'tailscale'))
);
