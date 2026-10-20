-- Preserve active phases while adding a non-counting preparation phase and a
-- stable identity for dismissing/restoring the whole study session.
CREATE TABLE study_timer_priming (
    id INTEGER PRIMARY KEY CHECK (id=1), revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'idle' CHECK(status IN ('idle','running','paused')),
    phase TEXT NOT NULL DEFAULT 'priming' CHECK(phase IN ('priming','focus','short_break')),
    phase_id TEXT, cycle INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 300, elapsed_seconds REAL NOT NULL DEFAULT 0,
    started_at TEXT, running_since TEXT, deadline_at TEXT, completed_at TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    subject_id INTEGER REFERENCES study_subjects(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES study_projects(id) ON DELETE SET NULL,
    target_id INTEGER REFERENCES study_targets(id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT '', distractions TEXT NOT NULL DEFAULT '',
    segments_json TEXT NOT NULL DEFAULT '[]', completed_phase_id TEXT,
    completed_phase TEXT, session_id TEXT
);
INSERT INTO study_timer_priming
    (id,revision,status,phase,phase_id,cycle,duration_seconds,elapsed_seconds,
     started_at,running_since,deadline_at,completed_at,timezone,subject_id,
     project_id,target_id,notes,distractions,segments_json,completed_phase_id,
     completed_phase,session_id)
SELECT id,revision+1,status,CASE WHEN status='idle' THEN 'priming' ELSE phase END,
    phase_id,cycle,CASE WHEN status='idle' THEN 300 ELSE duration_seconds END,
    elapsed_seconds,started_at,running_since,deadline_at,completed_at,timezone,
    subject_id,project_id,target_id,notes,distractions,segments_json,
    completed_phase_id,completed_phase,CASE WHEN status!='idle' THEN phase_id END
FROM study_timer;
DROP TABLE study_timer;
ALTER TABLE study_timer_priming RENAME TO study_timer;
CREATE TABLE study_guides (
    id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, ended_at TEXT,
    state_json TEXT NOT NULL
);
INSERT INTO study_guides(id,created_at,state_json)
SELECT session_id,COALESCE(started_at,strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    '{"checked":[false,false,false,false,false,false,false,false,false,false,false,false,false,false],"responses":["","","","","","","","","","","","","",""],"reflection":"","next_review":""}'
FROM study_timer WHERE session_id IS NOT NULL;
