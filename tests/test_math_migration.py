"""044_math: the math tables land on an existing workspace and enforce their invariants."""
from __future__ import annotations

import shutil
import sqlite3

import pytest

from kitty.core.math import topics as graph
from kitty.db import connect, migrate
from kitty.db.migrations import available_migrations


def test_math_migration_applies_to_an_existing_workspace_and_guards_its_rows(tmp_path):
    early = tmp_path / "before-math"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 44:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "math.sqlite")
    try:
        migrate(conn, early)
        conn.execute("INSERT INTO quick_notes(body, tint, created_at, updated_at) VALUES ('kept', 'mint', 't', 't')")
        applied = [m.version for m in migrate(conn)]
        assert 44 in applied
        assert conn.execute("SELECT body FROM quick_notes").fetchone()[0] == "kept"
        graph.sync(conn)
        assert conn.execute("SELECT count(*) FROM math_topics").fetchone()[0] == len(graph.TOPICS)
        conn.execute("INSERT INTO math_mastery(topic_id, updated_at) VALUES ('arith.add-sub', 't')")
        conn.execute("INSERT INTO math_attempts(id, topic_id, template_id, seed, purpose, created_at)"
                     " VALUES (?, 'arith.add-sub', 'arith.add-sub/three-digit', 1, 'lesson', 't')", ("a" * 32,))
        conn.execute("INSERT INTO math_sprints(id, mode, duration_s, seed, ops, problem_count, started_at)"
                     " VALUES (?, 'mc', 120, 5, '[\"add\"]', 240, 't')", ("b" * 32,))
        for statement in (
            "UPDATE math_mastery SET mastered_at='t' WHERE topic_id='arith.add-sub'",  # mastery needs its FSRS card
            f"INSERT INTO math_attempts(id, topic_id, template_id, seed, purpose, created_at) VALUES ('{'c' * 32}', 'arith.add-sub', 'x/y', 1, 'guided', 't')",
            f"INSERT INTO math_attempts(id, topic_id, template_id, seed, purpose, created_at) VALUES ('{'d' * 32}', 'arith.add-sub', 'x/y', 1, 'quiz', 't')",
            f"INSERT INTO math_attempts(id, topic_id, template_id, seed, purpose, created_at) VALUES ('{'e' * 32}', 'missing', 'x/y', 1, 'lesson', 't')",
            f"INSERT INTO math_sprints(id, mode, duration_s, seed, ops, problem_count, started_at) VALUES ('{'f' * 32}', 'zetamac', 5, 1, '[]', 10, 't')",
            f"UPDATE math_sprints SET finished_at='t' WHERE id='{'b' * 32}'",  # a finished sprint has a score
            "INSERT INTO math_prereqs(topic_id, prereq_id) VALUES ('arith.times-tables', 'arith.mul-2x2')",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement)
        conn.execute("DELETE FROM math_topics WHERE id='arith.add-sub'")
        assert conn.execute("SELECT count(*) FROM math_attempts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM math_mastery").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
