-- The block editor (build step 2) writes typed text-flow blocks: headings, lists,
-- to-dos, quotes, callouts, code, dividers, toggles and page links. Outline readers
-- (citations, topics, atlas, schema maps) see all of them, not only `text` blocks.
-- Keep this list equal to kitty.core.blocks.convert.OUTLINE_TYPES.
DROP VIEW note_outline;
CREATE VIEW note_outline AS
WITH RECURSIVE outline(id, page_block_id, parent_id, text, path) AS (
    SELECT b.id, b.page_id, NULL, b.text, b.order_key
    FROM blocks b JOIN blocks r ON r.id = b.parent_id
    WHERE r.parent_id IS NULL AND r.type = 'page' AND b.deleted_at IS NULL
      AND b.type IN ('text', 'heading_1', 'heading_2', 'heading_3', 'to_do', 'bulleted_list', 'numbered_list',
                     'quote', 'callout', 'code', 'divider', 'toggle', 'page_link')
    UNION ALL
    SELECT c.id, c.page_id, c.parent_id, c.text, o.path || '/' || c.order_key
    FROM blocks c JOIN outline o ON c.parent_id = o.id
    WHERE c.deleted_at IS NULL
      AND c.type IN ('text', 'heading_1', 'heading_2', 'heading_3', 'to_do', 'bulleted_list', 'numbered_list',
                     'quote', 'callout', 'code', 'divider', 'toggle', 'page_link')
)
SELECT o.id, p.id AS page_id, o.page_block_id, o.parent_id, o.text, o.path, p.title, p.updated_at
FROM outline o JOIN note_page_index p ON p.block_id = o.page_block_id;
