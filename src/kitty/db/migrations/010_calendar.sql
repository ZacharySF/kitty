-- Local calendar state and independent Google synchronization; no Gmail tokens.
CREATE TABLE calendar_calendars (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#6d5dfc',
 timezone TEXT NOT NULL DEFAULT 'UTC', visible INTEGER NOT NULL DEFAULT 1,
 revision INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
 google_id TEXT UNIQUE, sync_token TEXT, google_etag TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE calendar_events (
 id TEXT PRIMARY KEY, calendar_id TEXT NOT NULL REFERENCES calendar_calendars(id),
 title TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, timezone TEXT NOT NULL,
 all_day INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 deleted INTEGER NOT NULL DEFAULT 0, source_key TEXT UNIQUE, source_fingerprint TEXT,
 source_overridden INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE INDEX calendar_events_calendar ON calendar_events(calendar_id, deleted);
CREATE TABLE calendar_exceptions (
 event_id TEXT NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
 occurrence TEXT NOT NULL, data TEXT, PRIMARY KEY(event_id, occurrence)
);
CREATE TABLE calendar_tasks (
 id TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 deleted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE calendar_history (
 id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
 before_json TEXT, after_json TEXT NOT NULL, created_at TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE calendar_reminder_dismissals (
 event_id TEXT NOT NULL, occurrence TEXT NOT NULL, minutes INTEGER NOT NULL,
 PRIMARY KEY(event_id, occurrence, minutes)
);
CREATE TABLE calendar_google_events (
 calendar_id TEXT NOT NULL REFERENCES calendar_calendars(id), google_id TEXT NOT NULL,
 event_id TEXT NOT NULL REFERENCES calendar_events(id), etag TEXT,
 synced_revision INTEGER NOT NULL DEFAULT 0, base_json TEXT, remote_json TEXT,
 PRIMARY KEY(calendar_id,google_id), UNIQUE(event_id)
);
CREATE TABLE calendar_google_conflicts (
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, google_id TEXT NOT NULL,
 calendar_id TEXT NOT NULL, local_revision INTEGER NOT NULL, remote_json TEXT NOT NULL,
 created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE calendar_google_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO calendar_calendars(id,name,color,timezone,updated_at)
VALUES ('personal','Personal','#6d5dfc','UTC',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
