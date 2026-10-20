-- Text outlines are authoritative rows, independent of existing canvas documents.
CREATE TABLE note_blocks (
    id TEXT PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES note_pages(id) ON DELETE CASCADE,
    parent_id TEXT,
    text TEXT NOT NULL DEFAULT '',
    ordering INTEGER NOT NULL DEFAULT 0,
    collapsed INTEGER NOT NULL DEFAULT 0 CHECK(collapsed IN (0,1)),
    UNIQUE(page_id,id),
    FOREIGN KEY(page_id,parent_id) REFERENCES note_blocks(page_id,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX note_blocks_page ON note_blocks(page_id,ordering,id);
CREATE TABLE note_block_refs (
    source_id TEXT NOT NULL REFERENCES note_blocks(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL,
    PRIMARY KEY(source_id,target_id)
);
CREATE INDEX note_block_refs_target ON note_block_refs(target_id);
CREATE TABLE note_block_topics (
    block_id TEXT NOT NULL REFERENCES note_blocks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('page','tag')),
    topic TEXT NOT NULL,
    PRIMARY KEY(block_id,kind,topic)
);
CREATE INDEX note_block_topics_topic ON note_block_topics(kind,topic);
