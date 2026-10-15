CREATE TABLE opportunities (
 id INTEGER PRIMARY KEY, identity TEXT NOT NULL UNIQUE, title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'event', description TEXT NOT NULL DEFAULT '',
 location TEXT NOT NULL DEFAULT '', remote INTEGER, start TEXT, end TEXT, deadline TEXT,
 timezone TEXT NOT NULL DEFAULT 'America/Chicago', availability TEXT NOT NULL DEFAULT 'unknown',
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('discovered','new','saved','queued','applied','participating','completed','skipped')),
 favorite INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
 overrides TEXT NOT NULL DEFAULT '[]', deadline_override INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX opportunities_review ON opportunities(status,favorite);
CREATE TABLE opportunity_evidence (
 id INTEGER PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
 source TEXT NOT NULL, url TEXT NOT NULL, observed_at TEXT NOT NULL, facts TEXT NOT NULL,
 UNIQUE(opportunity_id,source,url)
);
CREATE TABLE opportunity_projects (
 id INTEGER PRIMARY KEY, opportunity_id INTEGER NOT NULL UNIQUE REFERENCES opportunities(id) ON DELETE CASCADE,
 title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE opportunity_project_items (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES opportunity_projects(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('task','milestone','resource')), title TEXT NOT NULL,
 due TEXT, url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE opportunity_sources (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL, category TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, timezone TEXT NOT NULL DEFAULT 'America/Chicago',
 last_run TEXT, last_success TEXT, last_status TEXT NOT NULL DEFAULT 'never', last_error TEXT,
 last_count INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE opportunity_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), profile TEXT NOT NULL DEFAULT '{"major":"","track":"","interests":[],"locations":[],"prefer_remote":false}',
 enabled INTEGER NOT NULL DEFAULT 0, interval_hours INTEGER NOT NULL DEFAULT 24,
 last_run TEXT, next_run TEXT, last_error TEXT, revision INTEGER NOT NULL DEFAULT 1
);
INSERT INTO opportunity_settings(id) VALUES(1);
CREATE TABLE opportunity_identities (
 identity TEXT PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE
);
