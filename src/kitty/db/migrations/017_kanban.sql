-- Independent local boards; moving cards never changes a linked source's status.
CREATE TABLE kanban_boards (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE kanban_columns (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 board_id INTEGER NOT NULL REFERENCES kanban_boards(id) ON DELETE CASCADE,
 title TEXT NOT NULL, position INTEGER NOT NULL CHECK(position >= 0),
 UNIQUE(board_id,position), UNIQUE(board_id,id)
);
CREATE TABLE kanban_cards (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 board_id INTEGER NOT NULL REFERENCES kanban_boards(id) ON DELETE CASCADE,
 column_id INTEGER NOT NULL, position INTEGER NOT NULL CHECK(position >= 0),
 title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
 priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high','urgent')),
 labels_json TEXT NOT NULL DEFAULT '[]', checklist_json TEXT NOT NULL DEFAULT '[]',
 due_date TEXT, archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
 source_url TEXT NOT NULL DEFAULT '',
 job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
 opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE SET NULL,
 study_project_id INTEGER REFERENCES study_projects(id) ON DELETE SET NULL,
 opportunity_project_id INTEGER REFERENCES opportunity_projects(id) ON DELETE SET NULL,
 study_task_id INTEGER REFERENCES study_tasks(id) ON DELETE SET NULL,
 FOREIGN KEY(board_id,column_id) REFERENCES kanban_columns(board_id,id) ON DELETE CASCADE,
 UNIQUE(column_id,position)
);
CREATE INDEX kanban_cards_board ON kanban_cards(board_id);
