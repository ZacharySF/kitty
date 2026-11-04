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
        # v0.3.30's schema before math: main's migrations only (the block backend's 032/033
        # and the stack's 038-043 freeze quick_notes, so they come after, with 044).
        if migration.version < 44 and migration.version not in {32, 33, 38, 39, 40, 41, 42, 43}:
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


def test_math_books_migration_lands_after_math_and_guards_its_rows(tmp_path):
    early = tmp_path / "before-books"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 50:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "books.sqlite")
    try:
        migrate(conn, early)
        graph.sync(conn)
        conn.execute("INSERT INTO math_mastery(topic_id, updated_at) VALUES ('prob.bayes', 't')")
        assert 50 in [m.version for m in migrate(conn)]
        assert conn.execute("SELECT count(*) FROM math_mastery").fetchone()[0] == 1
        conn.execute("INSERT INTO math_textbook_cards(exercise_id, fsrs, due_at, last_rating, created_at, updated_at)"
                     " VALUES ('gs.4.1.1', '{}', 't', 3, 't', 't')")
        conn.execute("INSERT INTO math_textbook_attempts(id, exercise_id, created_at) VALUES (?, 'gs.4.1.1', 't')", ("a" * 32,))
        row = ("b" * 32, "Zhou", "2.3", "14", "1/3", 3, "prob.bayes", "{}", "t", 3, "t", "t")
        insert = ("INSERT INTO math_book_problems(id, book, section, problem, answer, confidence, topic_id, fsrs, due_at,"
                  " last_rating, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
        conn.execute(insert, row)
        for statement, params in (
            (insert, ("c" * 32, *row[1:])),  # the same book, section and problem twice
            (insert, ("d" * 32, "", *row[2:])),  # a book needs a name
            (insert, ("e" * 32, "Zhou", "2.3", "15", "x" * 201, *row[5:])),  # answers are short references
            (insert, ("f" * 32, "Zhou", "2.3", "16", "", 5, *row[6:])),  # confidence is 1–4
            (insert, ("g" * 32, "Zhou", "2.3", "17", "", 3, "missing", *row[7:])),  # topics must exist
            ("INSERT INTO math_textbook_cards(exercise_id, fsrs, due_at, last_rating, created_at, updated_at)"
             " VALUES ('gs.4.1.2', '{}', 't', 0, 't', 't')", ()),
            ("INSERT INTO math_textbook_attempts(id, exercise_id, created_at) VALUES ('short', 'gs.4.1.1', 't')", ()),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement, params)
        conn.execute("DELETE FROM math_topics WHERE id='prob.bayes'")
        assert conn.execute("SELECT topic_id FROM math_book_problems").fetchone()[0] is None
    finally:
        conn.close()
