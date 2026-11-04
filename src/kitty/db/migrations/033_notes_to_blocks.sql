-- python: kitty.core.blocks.legacy:migrate_notes
-- Converts every note page, document and quick note into blocks after writing a full
-- JSON export (kitty.core.blocks.legacy). The legacy tables stay readable but refuse
-- writes for one release, so a rollback can still read them.
CREATE TRIGGER note_pages_read_only_insert BEFORE INSERT ON note_pages
BEGIN SELECT RAISE(ABORT, 'note_pages is read-only since the block migration'); END;
CREATE TRIGGER note_pages_read_only_update BEFORE UPDATE ON note_pages
BEGIN SELECT RAISE(ABORT, 'note_pages is read-only since the block migration'); END;
CREATE TRIGGER note_pages_read_only_delete BEFORE DELETE ON note_pages
BEGIN SELECT RAISE(ABORT, 'note_pages is read-only since the block migration'); END;
CREATE TRIGGER note_blocks_read_only_insert BEFORE INSERT ON note_blocks
BEGIN SELECT RAISE(ABORT, 'note_blocks is read-only since the block migration'); END;
CREATE TRIGGER note_blocks_read_only_update BEFORE UPDATE ON note_blocks
BEGIN SELECT RAISE(ABORT, 'note_blocks is read-only since the block migration'); END;
CREATE TRIGGER note_blocks_read_only_delete BEFORE DELETE ON note_blocks
BEGIN SELECT RAISE(ABORT, 'note_blocks is read-only since the block migration'); END;
CREATE TRIGGER note_block_refs_read_only_insert BEFORE INSERT ON note_block_refs
BEGIN SELECT RAISE(ABORT, 'note_block_refs is read-only since the block migration'); END;
CREATE TRIGGER note_block_refs_read_only_update BEFORE UPDATE ON note_block_refs
BEGIN SELECT RAISE(ABORT, 'note_block_refs is read-only since the block migration'); END;
CREATE TRIGGER note_block_refs_read_only_delete BEFORE DELETE ON note_block_refs
BEGIN SELECT RAISE(ABORT, 'note_block_refs is read-only since the block migration'); END;
CREATE TRIGGER note_block_topics_read_only_insert BEFORE INSERT ON note_block_topics
BEGIN SELECT RAISE(ABORT, 'note_block_topics is read-only since the block migration'); END;
CREATE TRIGGER note_block_topics_read_only_update BEFORE UPDATE ON note_block_topics
BEGIN SELECT RAISE(ABORT, 'note_block_topics is read-only since the block migration'); END;
CREATE TRIGGER note_block_topics_read_only_delete BEFORE DELETE ON note_block_topics
BEGIN SELECT RAISE(ABORT, 'note_block_topics is read-only since the block migration'); END;
CREATE TRIGGER note_daily_read_only_insert BEFORE INSERT ON note_daily
BEGIN SELECT RAISE(ABORT, 'note_daily is read-only since the block migration'); END;
CREATE TRIGGER note_daily_read_only_update BEFORE UPDATE ON note_daily
BEGIN SELECT RAISE(ABORT, 'note_daily is read-only since the block migration'); END;
CREATE TRIGGER note_daily_read_only_delete BEFORE DELETE ON note_daily
BEGIN SELECT RAISE(ABORT, 'note_daily is read-only since the block migration'); END;
CREATE TRIGGER documents_read_only_insert BEFORE INSERT ON documents
BEGIN SELECT RAISE(ABORT, 'documents is read-only since the block migration'); END;
CREATE TRIGGER documents_read_only_update BEFORE UPDATE ON documents
BEGIN SELECT RAISE(ABORT, 'documents is read-only since the block migration'); END;
CREATE TRIGGER documents_read_only_delete BEFORE DELETE ON documents
BEGIN SELECT RAISE(ABORT, 'documents is read-only since the block migration'); END;
CREATE TRIGGER quick_notes_read_only_insert BEFORE INSERT ON quick_notes
BEGIN SELECT RAISE(ABORT, 'quick_notes is read-only since the block migration'); END;
CREATE TRIGGER quick_notes_read_only_update BEFORE UPDATE ON quick_notes
BEGIN SELECT RAISE(ABORT, 'quick_notes is read-only since the block migration'); END;
CREATE TRIGGER quick_notes_read_only_delete BEFORE DELETE ON quick_notes
BEGIN SELECT RAISE(ABORT, 'quick_notes is read-only since the block migration'); END;
