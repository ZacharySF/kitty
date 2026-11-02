-- Append-only history of browser mutations (kitty.core.record_events). A row holds only
-- route-derived identifiers, never titles or bodies: /api/brain resolves labels from the
-- live rows when it reads them, so a deleted entity reads as e.g. "Deleted page".
CREATE TABLE record_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    section TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    action TEXT NOT NULL,
    route TEXT NOT NULL
);
CREATE INDEX record_events_section ON record_events(section, seq);
CREATE TRIGGER record_events_no_update BEFORE UPDATE ON record_events
BEGIN SELECT RAISE(ABORT, 'record_events is append-only'); END;
CREATE TRIGGER record_events_no_delete BEFORE DELETE ON record_events
BEGIN SELECT RAISE(ABORT, 'record_events is append-only'); END;
