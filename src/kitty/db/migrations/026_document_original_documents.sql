-- Retained imports can belong to saved authoring documents as well as note pages.
-- ALTER preserves every existing source row and its page relationship.
ALTER TABLE document_originals ADD COLUMN document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL;
CREATE INDEX document_originals_document ON document_originals(document_id);
