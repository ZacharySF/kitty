-- Local notes hierarchy; existing notebooks remain at the drive root.
CREATE TABLE note_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    parent_id INTEGER REFERENCES note_folders(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX note_folders_parent ON note_folders(parent_id);
ALTER TABLE note_notebooks ADD COLUMN folder_id INTEGER REFERENCES note_folders(id) ON DELETE RESTRICT;
CREATE INDEX note_notebooks_folder ON note_notebooks(folder_id);
