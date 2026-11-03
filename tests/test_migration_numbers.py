"""Migration files keep one number each, with no unexplained gaps, and every supported
starting point upgrades to the same schema.

The runner keys migrations by number (kitty.db.migrations): two files with one number
would make one of them unreachable on any database that applied the other, as happened
when main's 035_calendar_palette_subjects and the platform stack's 035_text_flow_outline
were both written. The stack's then-unreleased 035-039 became 037-041, and its 037 and 044
became 045 and 046 when main shipped 037_rika and 044_math; a database at an old stack
numbering was never released and is not a supported starting point.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

from kitty.db import connect, migrate
from kitty.db.migrations import MIGRATIONS_DIR, available_migrations

#: Numbers deliberately left out, with the reason. Add one only with its reason.
EXPLAINED_GAPS = {
    21: "never used: no 021 file exists in any branch's history (parallel features skipped it)",
}
#: Main's own migrations after the last one the block backend builds on (released in v0.3.2x-0.3.30).
MAIN_ONLY = {"record_events", "calendar_palette_subjects", "note_page_maps", "rika", "math"}


def test_every_file_is_numbered_once_and_the_numbers_have_no_unexplained_gap():
    names = sorted(path.name for path in MIGRATIONS_DIR.iterdir() if path.suffix == ".sql")
    assert all(re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", name) for name in names), names
    numbers = [int(name[:3]) for name in names]
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    assert not duplicates, f"migration numbers used twice: {duplicates}"
    missing = set(range(1, max(numbers) + 1)) - set(numbers)
    assert missing == set(EXPLAINED_GAPS), f"unexplained gaps {sorted(missing - set(EXPLAINED_GAPS))}"
    assert not set(EXPLAINED_GAPS) & set(numbers), "an explained gap is now used; drop it from EXPLAINED_GAPS"
    assert [migration.version for migration in available_migrations()] == sorted(numbers)


def _schema(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return {(row[0], row[1], " ".join((row[2] or "").split())) for row in conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_new'")}


def _copy(tmp_path: Path, folder: str, keep) -> Path:
    target = tmp_path / folder
    target.mkdir()
    for migration in available_migrations():
        if keep(migration):
            shutil.copy(migration.path, target / migration.path.name)
    return target


def test_a_fresh_database_applies_everything(tmp_path):
    conn = connect(tmp_path / "data" / "fresh.db")
    applied = migrate(conn)
    assert [migration.version for migration in applied] == [migration.version for migration in available_migrations()]
    assert migrate(conn) == []
    conn.close()


def test_a_database_at_mains_schema_upgrades_to_the_same_schema_and_keeps_its_notes(tmp_path):
    fresh = connect(tmp_path / "data" / "fresh.db")
    migrate(fresh)
    # Main's released schema: every migration before the block backend plus main's 034-037 and 044.
    main_schema = _copy(tmp_path, "main", lambda m: m.version < 32 or m.name in MAIN_ONLY)
    conn = connect(tmp_path / "data" / "apply.db")
    migrate(conn, main_schema)
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    assert {34, 35, 36, 37, 44} <= applied and not applied & {32, 33, 38, 45}
    book = conn.execute("INSERT INTO note_notebooks(title, created_at, updated_at) VALUES ('Biology', 't', 't')").lastrowid
    page = conn.execute("INSERT INTO note_pages(notebook_id, title, document, created_at, updated_at)"
                        " VALUES (?, 'Cells', '{\"version\": 1, \"elements\": []}', 't', 't')", (book,)).lastrowid
    conn.execute("INSERT INTO note_blocks(id, page_id, parent_id, text, ordering, collapsed)"
                 " VALUES ('00000000-0000-4000-8000-000000000001', ?, NULL, 'Mitochondria', 0, 0)", (page,))
    conn.execute("INSERT INTO documents(title, content, format, created_at, updated_at) VALUES ('Paper', 'Body', 'markdown', 't', 't')")
    conn.execute("INSERT INTO quick_notes(body, created_at, updated_at) VALUES ('Buy milk', 't', 't')")
    map_id = conn.execute("INSERT INTO schema_maps(title, mode, graph_json, viewport_json, updated_at)"
                          " VALUES ('Cells', 'study', '{\"nodes\": [], \"edges\": []}', '{}', 't')").lastrowid
    conn.execute("INSERT INTO note_page_maps(page_id, map_id, migrated) VALUES (?, ?, 0)", (page, map_id))
    conn.commit()

    upgraded = [migration.version for migration in migrate(conn)]
    assert upgraded == sorted(m.version for m in available_migrations() if m.version not in applied)
    assert upgraded[:2] == [32, 33], "the block backend fills the numbers main skipped"
    assert _schema(conn) == _schema(fresh)
    assert conn.execute("SELECT title FROM note_page_index WHERE id=?", (page,)).fetchone()[0] == "Cells"
    assert [row[0] for row in conn.execute("SELECT text FROM note_outline WHERE page_id=?", (page,))] == ["Mitochondria"]
    assert {row[0] for row in conn.execute("SELECT type FROM blocks WHERE parent_id IS NULL")} >= {"page", "document", "quick_note"}
    assert [tuple(row) for row in conn.execute("SELECT page_id, map_id FROM note_page_maps")] == [(page, map_id)]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert list((tmp_path / "data" / "exports").glob("notes-before-blocks-*.json"))
    conn.close()
    fresh.close()
