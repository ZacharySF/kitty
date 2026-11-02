-- A note page's mind map lives in schema_maps; this links the two, one map per page and
-- one page per map. Deleting either side removes the link only. `migrated` marks maps
-- built from a page's old concept map (stickies/text joined by connectors on its canvas
-- document) by kitty.core.concept_maps, so a revert removes exactly those.
CREATE TABLE note_page_maps (
    page_id  INTEGER PRIMARY KEY REFERENCES note_pages(id) ON DELETE CASCADE,
    map_id   INTEGER NOT NULL UNIQUE REFERENCES schema_maps(id) ON DELETE CASCADE,
    migrated INTEGER NOT NULL DEFAULT 0 CHECK (migrated IN (0, 1))
);

-- One row once the one-time concept-map migration has run; export_path is the owner-only
-- JSON snapshot written before any map was created (NULL when there was nothing to move).
CREATE TABLE concept_map_migration (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    done_at     TEXT NOT NULL,
    export_path TEXT
);
