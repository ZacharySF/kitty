-- foreign_keys: off
-- Calendar colours follow the theme. `color` now holds a theme palette slot
-- (c1..c6, drawn from --c1..--c6) or a #rrggbb kept from before or from Google;
-- `subject_id` links a calendar to a study subject, whose colour then wins.
-- calendar_calendars is rebuilt to drop the fixed '#6d5dfc' default (SQLite cannot
-- change a column default in place). Local calendars still on that old default
-- take the slot the week view already painted them: their place in name order.
-- The header line above makes the runner switch foreign keys off around this
-- script (events and Google mappings reference calendar_calendars) and run
-- PRAGMA foreign_key_check before committing.

CREATE TABLE calendar_calendars_new (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT 'c1',
 timezone TEXT NOT NULL DEFAULT 'UTC', visible INTEGER NOT NULL DEFAULT 1,
 revision INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
 google_id TEXT UNIQUE, sync_token TEXT, google_etag TEXT, updated_at TEXT NOT NULL,
 subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL
);

INSERT INTO calendar_calendars_new (id, name, color, timezone, visible, revision, deleted,
                                    google_id, sync_token, google_etag, updated_at)
SELECT id, name,
       CASE WHEN lower(color) = '#6d5dfc' AND google_id IS NULL
            THEN 'c' || ((ROW_NUMBER() OVER (PARTITION BY deleted ORDER BY name) - 1) % 6 + 1)
            ELSE color END,
       timezone, visible, revision, deleted, google_id, sync_token, google_etag, updated_at
FROM calendar_calendars;

DROP TABLE calendar_calendars;
ALTER TABLE calendar_calendars_new RENAME TO calendar_calendars;

-- Events and tasks keep their subject in their JSON payload; unlink it when the
-- subject is deleted, as the calendar column does. Local display data only, so
-- revisions (and Google sync state) are left alone.
CREATE TRIGGER calendar_unlink_deleted_subject AFTER DELETE ON study_subjects BEGIN
    UPDATE calendar_events SET data = json_set(data, '$.subject_id', NULL)
        WHERE json_extract(data, '$.subject_id') = OLD.id;
    UPDATE calendar_exceptions SET data = json_set(data, '$.subject_id', NULL)
        WHERE json_extract(data, '$.subject_id') = OLD.id;
    UPDATE calendar_tasks SET data = json_set(data, '$.subject_id', NULL)
        WHERE json_extract(data, '$.subject_id') = OLD.id;
END;
