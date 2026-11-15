-- GitHub backup (docs/GUIDE.md "GitHub backup"): an opt-in, push-only copy of the notes
-- in a private GitHub repository the owner picks. One config row; `owner`/`repo` are NULL
-- until the owner connects one. `problem` is set when rika! stopped by itself (the
-- repository became public, GitHub rejected the push) and cleared by the owner's next
-- Sync now or Resume. `synced_seq` is the newest op_log row the pushed copy includes.
CREATE TABLE repo_sync_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    owner TEXT,
    repo TEXT,
    paused INTEGER NOT NULL DEFAULT 0,
    problem TEXT,
    synced_seq INTEGER NOT NULL DEFAULT 0
);
INSERT INTO repo_sync_config(id) VALUES (1);

-- Every run, append-only. `pages` is how many pages the copy held after the run;
-- `result` is pushed, unchanged, offline, refused (not private), rejected or error.
CREATE TABLE repo_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    "commit" TEXT,
    pages INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL CHECK (result IN ('pushed', 'unchanged', 'offline', 'refused', 'rejected', 'error')),
    error TEXT
);
CREATE TRIGGER repo_sync_runs_append_only BEFORE UPDATE ON repo_sync_runs
BEGIN SELECT RAISE(ABORT, 'repo_sync_runs is append-only'); END;
CREATE TRIGGER repo_sync_runs_no_delete BEFORE DELETE ON repo_sync_runs
BEGIN SELECT RAISE(ABORT, 'repo_sync_runs is append-only'); END;
