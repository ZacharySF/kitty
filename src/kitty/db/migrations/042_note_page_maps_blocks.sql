-- note_page_maps (036, main) pointed at note_pages, which the block backend (032/033) froze:
-- a page made since lives only in `blocks`, addressed by its Notes number (the same number
-- note_pages.id was, kitty.core.blocks.legacy). Rebuilt with page_id as that number and no
-- foreign key to the frozen table. Trash keeps the link (a restored page keeps its map); the
-- trigger removes it when the page's root block is purged, and deleting the map still does.
CREATE TABLE note_page_maps_new (
    page_id  INTEGER PRIMARY KEY,
    map_id   INTEGER NOT NULL UNIQUE REFERENCES schema_maps(id) ON DELETE CASCADE,
    migrated INTEGER NOT NULL DEFAULT 0 CHECK (migrated IN (0, 1))
);
INSERT INTO note_page_maps_new(page_id, map_id, migrated) SELECT page_id, map_id, migrated FROM note_page_maps;
DROP TABLE note_page_maps;
ALTER TABLE note_page_maps_new RENAME TO note_page_maps;

CREATE TRIGGER note_page_maps_page_purged AFTER DELETE ON blocks
WHEN OLD.parent_id IS NULL AND OLD.type = 'page'
BEGIN
    DELETE FROM note_page_maps WHERE page_id = json_extract(OLD.properties, '$.number');
END;
