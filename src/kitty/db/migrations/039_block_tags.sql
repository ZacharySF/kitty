-- python after: kitty.core.blocks.tags:backfill
-- Auto-tags (docs/platform-design.md, Q13). A tag is a root block of type `tag`: its text
-- is the name; `keywords`, `aliases`, `mode` and `short` are properties. A block's state
-- for a tag is its `tag_<tag id without dashes>` property (manual, auto, accepted or
-- rejected), written through block transactions like any other property, so it is in the
-- op log and pushed live. `block_tags` and `tag_rejections` are its projection, written
-- only by kitty.core.blocks.store (with #name tokens counted as manual attachments).
-- The backfill makes every existing #tag name a tag record, so nothing is lost.

-- The global Tags setting (moves to the build file with Q8). `vocabulary` counts changes
-- to tag records, so a server can cache the keyword automaton until it moves.
CREATE TABLE tag_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL DEFAULT 'suggest' CHECK (mode IN ('suggest', 'always')),
    revision INTEGER NOT NULL DEFAULT 1,
    vocabulary INTEGER NOT NULL DEFAULT 0
);
INSERT INTO tag_settings(id) VALUES (1);

CREATE TABLE block_tags (
    block_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK (source IN ('manual', 'auto', 'accepted')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (block_id, tag_id)
);
CREATE INDEX block_tags_tag ON block_tags(tag_id);

CREATE TABLE tag_rejections (
    block_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (block_id, tag_id)
);
CREATE INDEX tag_rejections_tag ON tag_rejections(tag_id);
