-- Study timers stop at phase boundaries; only completed focus and manual logs count.
CREATE TABLE study_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    timezone TEXT NOT NULL DEFAULT 'UTC',
    focus_seconds INTEGER NOT NULL DEFAULT 1500 CHECK (focus_seconds BETWEEN 60 AND 14400),
    short_break_seconds INTEGER NOT NULL DEFAULT 300 CHECK (short_break_seconds BETWEEN 60 AND 7200),
    long_break_seconds INTEGER NOT NULL DEFAULT 900 CHECK (long_break_seconds BETWEEN 60 AND 7200),
    cycle_count INTEGER NOT NULL DEFAULT 4 CHECK (cycle_count BETWEEN 1 AND 12),
    sound INTEGER NOT NULL DEFAULT 0 CHECK (sound IN (0,1)),
    notifications INTEGER NOT NULL DEFAULT 0 CHECK (notifications IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 1
);
INSERT INTO study_settings(id) VALUES (1);
CREATE TABLE study_subjects (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#7c6fdb', revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE study_projects (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL,
    revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE study_targets (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES study_projects(id) ON DELETE SET NULL,
    period TEXT NOT NULL CHECK (period IN ('daily','weekly')), minutes INTEGER NOT NULL CHECK(minutes BETWEEN 1 AND 10080),
    revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE study_timer (
    id INTEGER PRIMARY KEY CHECK (id=1), revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'idle' CHECK(status IN ('idle','running','paused','awaiting_next')),
    phase TEXT NOT NULL DEFAULT 'focus' CHECK(phase IN ('focus','short_break','long_break')),
    phase_id TEXT, cycle INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 1500, elapsed_seconds REAL NOT NULL DEFAULT 0,
    started_at TEXT, running_since TEXT, deadline_at TEXT, completed_at TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES study_projects(id) ON DELETE SET NULL,
    target_id INTEGER REFERENCES study_targets(id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT '', distractions TEXT NOT NULL DEFAULT '',
    segments_json TEXT NOT NULL DEFAULT '[]'
);
INSERT INTO study_timer(id) VALUES(1);
CREATE TABLE study_sessions (
    id INTEGER PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL CHECK(source IN ('focus','manual')), source_key TEXT NOT NULL UNIQUE,
    subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES study_projects(id) ON DELETE SET NULL,
    target_id INTEGER REFERENCES study_targets(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL, ended_at TEXT NOT NULL, timezone TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL CHECK(duration_seconds > 0), segments_json TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '', distractions TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX study_sessions_date ON study_sessions(ended_at);
CREATE TABLE study_tasks (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL,
    subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES study_projects(id) ON DELETE SET NULL,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
    due_date TEXT, done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)),
    checklist_json TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE study_exams (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, exam_date TEXT NOT NULL, timezone TEXT NOT NULL,
    subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE study_plan_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER REFERENCES study_exams(id) ON DELETE CASCADE,
    subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL,
    title TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, timezone TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)), revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE study_requests (
    request_id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, fingerprint TEXT NOT NULL, response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
