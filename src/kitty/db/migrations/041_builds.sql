-- Builds (docs/platform-design.md, Q8/Q9). The active build file, config/build.json, is
-- the source of truth for modules, settings, theme, custom CSS and interface preferences.
-- The settings rows (ai_settings, note_preferences, study_settings, opportunity_settings,
-- tag_settings) stay as its projection, written only by kitty.core.builds.project, so the
-- code that reads them inside its own transactions is unchanged. This row records which
-- build text is projected (applied_hash/applied_text: the last good build, used when the
-- file on disk has an error), the two settings with no older table, and whether this
-- workspace's browser preferences (localStorage) were already moved into the build.
CREATE TABLE build_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    applied_hash TEXT,
    applied_text TEXT,
    applied_at TEXT,
    history_retention_days INTEGER NOT NULL DEFAULT 30 CHECK (history_retention_days BETWEEN 1 AND 3650),
    tern_plugin INTEGER NOT NULL DEFAULT 1 CHECK (tern_plugin IN (0, 1)),
    local_preferences_moved INTEGER NOT NULL DEFAULT 0 CHECK (local_preferences_moved IN (0, 1))
);
INSERT INTO build_state(id) VALUES (1);
