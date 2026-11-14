-- Microsoft (Outlook calendar and mail) and Slack connections; read-first.
-- Tokens are never stored here: they live in owner-only files under .private/.

-- One connected account per provider.
CREATE TABLE connect_accounts (
    provider   TEXT PRIMARY KEY CHECK (provider IN ('microsoft', 'slack')),
    account    TEXT NOT NULL,
    scopes     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_sync  TEXT,
    error      TEXT
);

-- The owner's own app registrations (client IDs are not secrets) and the Slack reaction.
CREATE TABLE connect_config (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Calendars imported from a provider. A calendar listed here is read-only in rika!.
CREATE TABLE calendar_remote (
    calendar_id TEXT PRIMARY KEY REFERENCES calendar_calendars(id),
    provider    TEXT NOT NULL,
    remote_id   TEXT NOT NULL,
    UNIQUE(provider, remote_id)
);
CREATE TABLE calendar_remote_events (
    calendar_id TEXT NOT NULL REFERENCES calendar_remote(calendar_id) ON DELETE CASCADE,
    remote_id   TEXT NOT NULL,
    event_id    TEXT NOT NULL UNIQUE REFERENCES calendar_events(id),
    fingerprint TEXT NOT NULL,
    PRIMARY KEY(calendar_id, remote_id)
);

-- Rika drafts replies only to mail from the mailbox it can write to (Gmail).
ALTER TABLE rika_mail_messages ADD COLUMN provider TEXT NOT NULL DEFAULT 'gmail';

-- A Slack message the user reacted to. task_id is NULL for reactions that were
-- already there when Slack was connected (or the reaction was changed).
CREATE TABLE slack_captures (
    team       TEXT NOT NULL,
    channel    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    permalink  TEXT NOT NULL,
    excerpt    TEXT NOT NULL,
    task_id    TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(team, channel, ts)
);
