-- Employer account registry (kitty.core.apply.accounts, proposal-07 Step 4).
--
-- One row per ATS tenant origin kitty signs in to. No secret is stored here: the
-- tenant's generated password lives in kitty Keys as APPLY_PW_<sha1-16 of origin>
-- (OS keyring, or the owner-only file when no keyring exists), and a tenant in
-- authentication.existing_account_origins uses the shared credential file instead.

CREATE TABLE apply_accounts (
    id                INTEGER PRIMARY KEY,
    family            TEXT NOT NULL,                 -- kitty.core.apply.grants.FAMILIES
    origin            TEXT NOT NULL UNIQUE,          -- https://host[:port] of the tenant
    login_email       TEXT NOT NULL,                 -- base email, or its per-tenant +alias
    state             TEXT NOT NULL DEFAULT 'unknown' CHECK (state IN
                          ('unknown','created','verified','active','rejected','locked','manual')),
    password_profile  TEXT NOT NULL CHECK (password_profile IN ('standard','strict','shared')),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    account_created_at TEXT,
    verified_at       TEXT,
    last_sign_in_at   TEXT
);

-- Append-only account actions, used for the daily caps (3 new tenant accounts a
-- day, 3 sign-in tries per tenant a day) and shown as the row's history.
CREATE TABLE apply_account_events (
    id      INTEGER PRIMARY KEY,
    origin  TEXT NOT NULL,
    kind    TEXT NOT NULL CHECK (kind IN
                ('create','sign_in','password_refused','sign_in_rejected','locked',
                 'reset_requested','reset','verified')),
    at      TEXT NOT NULL
);
CREATE INDEX apply_account_events_kind_idx ON apply_account_events(kind, at);
CREATE INDEX apply_account_events_origin_idx ON apply_account_events(origin, kind, at);
CREATE TRIGGER apply_account_events_append_only BEFORE UPDATE ON apply_account_events
BEGIN
    SELECT RAISE(ABORT, 'apply_account_events is append-only');
END;

-- The owner's one-time ATS-family grants (Settings → Apply → Accounts).
CREATE TABLE apply_family_grants (
    family      TEXT PRIMARY KEY,
    granted_at  TEXT NOT NULL
);
