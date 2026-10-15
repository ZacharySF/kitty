-- Stage 6: Gmail messages already looked at, so a message is never fetched or
-- proposed twice. One row per Gmail message id, whatever the outcome
-- ('proposed', 'not_status_mail', 'no_company', 'ambiguous', 'same_status', ...).
CREATE TABLE gmail_messages (
    message_id  TEXT PRIMARY KEY,                -- Gmail's own message id
    received_at TEXT,                            -- the message's date (UTC)
    subject     TEXT,
    outcome     TEXT NOT NULL,
    event_id    INTEGER REFERENCES status_events(id),   -- the proposal, when one was made
    checked_at  TEXT NOT NULL
);
