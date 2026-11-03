-- python after: kitty.core.search:build
-- The full-text search index (kitty.core.search; paper §16: checked derived views).
--
-- `search_docs` holds one row per searchable record: blocks (kind 'block', written by
-- kitty.core.blocks.store in the same transaction as every block transaction) and the
-- other records search covers (study subjects, projects and tasks, calendar events and
-- tasks, flashcard decks and notes, jobs). Two external-content FTS5 indexes read it:
-- `search_fts` (words: unicode61 folding case and diacritics, prefix indexes for
-- type-ahead, bm25 ranking) and `search_grams` (trigrams: substring and CJK matches).
-- Triggers keep both indexes equal to `search_docs`.
--
-- Triggers on the other source tables queue the changed record in `search_pending` in the
-- writer's own transaction (pure SQL: no application function has to be registered for a
-- write to succeed); kitty.core.search.sync folds the queue before every query and check,
-- so every answer covers every committed write. The python step builds the index for the
-- existing data.
CREATE TABLE search_docs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    page_id TEXT,
    type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    flags INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, ref)
);
CREATE INDEX search_docs_page ON search_docs(page_id) WHERE page_id IS NOT NULL;

CREATE VIRTUAL TABLE search_fts USING fts5(
    title, body, content='search_docs', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2', prefix='2 3'
);
CREATE VIRTUAL TABLE search_grams USING fts5(
    title, body, content='search_docs', content_rowid='id',
    tokenize='trigram remove_diacritics 1'
);
-- Document frequency per indexed word (related records weigh a text's terms with it).
CREATE VIRTUAL TABLE search_vocab USING fts5vocab(search_fts, row);
CREATE TRIGGER search_docs_insert AFTER INSERT ON search_docs BEGIN
    INSERT INTO search_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
    INSERT INTO search_grams(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER search_docs_delete AFTER DELETE ON search_docs BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO search_grams(search_grams, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER search_docs_update AFTER UPDATE OF title, body ON search_docs BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO search_grams(search_grams, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO search_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
    INSERT INTO search_grams(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TABLE search_pending (
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    PRIMARY KEY (kind, ref)
) WITHOUT ROWID;

-- The oracle's last result (kitty index check and the scheduled check).
CREATE TABLE search_checks (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    docs INTEGER NOT NULL,
    mismatches INTEGER NOT NULL,
    repaired INTEGER NOT NULL,
    drift INTEGER NOT NULL,
    fts_ok INTEGER NOT NULL,
    seconds REAL NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);

CREATE TRIGGER search_study_subjects_insert AFTER INSERT ON study_subjects BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_subject', new.id);
END;
CREATE TRIGGER search_study_subjects_update AFTER UPDATE OF name ON study_subjects BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_subject', new.id);
END;
CREATE TRIGGER search_study_subjects_delete AFTER DELETE ON study_subjects BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_subject', old.id);
END;

CREATE TRIGGER search_study_projects_insert AFTER INSERT ON study_projects BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_project', new.id);
END;
CREATE TRIGGER search_study_projects_update AFTER UPDATE OF name ON study_projects BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_project', new.id);
END;
CREATE TRIGGER search_study_projects_delete AFTER DELETE ON study_projects BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_project', old.id);
END;

CREATE TRIGGER search_study_tasks_insert AFTER INSERT ON study_tasks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_task', new.id);
END;
CREATE TRIGGER search_study_tasks_update AFTER UPDATE OF title, checklist_json ON study_tasks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_task', new.id);
END;
CREATE TRIGGER search_study_tasks_delete AFTER DELETE ON study_tasks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('study_task', old.id);
END;

CREATE TRIGGER search_calendar_events_insert AFTER INSERT ON calendar_events BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('calendar_event', new.id);
END;
CREATE TRIGGER search_calendar_events_update AFTER UPDATE OF title, data, deleted, source_key ON calendar_events BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('calendar_event', new.id);
END;
CREATE TRIGGER search_calendar_events_delete AFTER DELETE ON calendar_events BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('calendar_event', old.id);
END;

CREATE TRIGGER search_calendar_tasks_insert AFTER INSERT ON calendar_tasks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('calendar_task', new.id);
END;
CREATE TRIGGER search_calendar_tasks_update AFTER UPDATE OF data, deleted ON calendar_tasks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('calendar_task', new.id);
END;
CREATE TRIGGER search_calendar_tasks_delete AFTER DELETE ON calendar_tasks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('calendar_task', old.id);
END;

CREATE TRIGGER search_flash_decks_insert AFTER INSERT ON flash_decks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('flashcard_deck', new.id);
END;
CREATE TRIGGER search_flash_decks_update AFTER UPDATE OF name, archived ON flash_decks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('flashcard_deck', new.id);
    INSERT OR IGNORE INTO search_pending(kind, ref)
        SELECT 'flashcard_note', id FROM flash_notes WHERE deck_id = new.id AND old.archived IS NOT new.archived;
END;
CREATE TRIGGER search_flash_decks_delete AFTER DELETE ON flash_decks BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('flashcard_deck', old.id);
END;

CREATE TRIGGER search_flash_notes_insert AFTER INSERT ON flash_notes BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('flashcard_note', new.id);
END;
CREATE TRIGGER search_flash_notes_update AFTER UPDATE OF fields, archived, deck_id, type_id ON flash_notes BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('flashcard_note', new.id);
END;
CREATE TRIGGER search_flash_notes_delete AFTER DELETE ON flash_notes BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('flashcard_note', old.id);
END;
CREATE TRIGGER search_flash_types_update AFTER UPDATE OF fields ON flash_types BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) SELECT 'flashcard_note', id FROM flash_notes WHERE type_id = new.id;
END;

CREATE TRIGGER search_jobs_insert AFTER INSERT ON jobs BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('job', new.id);
END;
-- Discovery rewrites these columns on every run; only a real change is queued.
CREATE TRIGGER search_jobs_update AFTER UPDATE OF title, location, jd_text, company_id ON jobs
WHEN old.title IS NOT new.title OR old.location IS NOT new.location OR old.jd_text IS NOT new.jd_text
  OR old.company_id IS NOT new.company_id BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('job', new.id);
END;
CREATE TRIGGER search_jobs_delete AFTER DELETE ON jobs BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) VALUES ('job', old.id);
END;
CREATE TRIGGER search_companies_update AFTER UPDATE OF display_name ON companies
WHEN old.display_name IS NOT new.display_name BEGIN
    INSERT OR IGNORE INTO search_pending(kind, ref) SELECT 'job', id FROM jobs WHERE company_id = new.id;
END;
