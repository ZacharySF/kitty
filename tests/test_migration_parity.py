"""The Rust migration runner (server/src/migrations.rs) and the Python one agree.

rika-server migrates the workspace at start-up; the CLI, TUI, MCP server, demo reset and the
tests keep using ``kitty.db.migrate`` (docs/rust-core-design.md, the migration runner). Both
read the same files, so these tests hold them to one result: a fresh database, every
historical schema (each prefix of the numbered files and every released set, gaps included)
migrated by each runner, and each of those upgraded to the latest schema by the other runner,
must give the same schema and the same rows. Timestamps the migrations take from the clock
(``applied_at``, ``CURRENT_TIMESTAMP`` defaults) read as "now"; values the schema generates
at random (the block device id) are compared by shape, since two Python runs differ there.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kitty.db import connect, migrate
from kitty.db.migrations import available_migrations
from terminal_support import RIKA_SERVER

TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)?")


def clock(value):
    """A timestamp within a day of now reads as "now" (the runs a test compares are seconds apart)."""
    if isinstance(value, str) and TIMESTAMP.fullmatch(value):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        if abs(datetime.now(timezone.utc) - stamp) < timedelta(days=1):
            return "<now>"
    return value


VERSIONS = [migration.version for migration in available_migrations()]
#: Every set of migrations a release shipped that is not a prefix of today's numbers: main's
#: releases up to v0.3.33, before the block backend filled 032/033 and 038-043.
RELEASED_GAPS = {
    "main-034": [*range(1, 21), *range(22, 32), 34],
    "main-035": [*range(1, 21), *range(22, 32), 34, 35],
    "main-036": [*range(1, 21), *range(22, 32), 34, 35, 36],
    "main-044": [*range(1, 21), *range(22, 32), 34, 35, 36, 37, 44],
    "main-045": [*range(1, 21), *range(22, 32), 34, 35, 36, 37, 44, 45],
    "main-049": [*range(1, 21), *range(22, 32), 34, 35, 36, 37, 44, 45, 49],
}
STEPS = {**{f"upto-{version:03}": [v for v in VERSIONS if v <= version] for version in VERSIONS}, **RELEASED_GAPS}


@pytest.fixture(autouse=True)
def _built():
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER}); run cargo build --release in server/")


def rust_migrate(path: Path, only: list[int] | None = None) -> subprocess.CompletedProcess:
    command = [str(RIKA_SERVER), "migrate", str(path)]
    if only is not None:
        command += ["--only", ",".join(map(str, only))]
    return subprocess.run(command, capture_output=True, text=True, timeout=60)


def python_migrate(path: Path, only: list[int] | None, tmp_path: Path) -> None:
    folder = None
    if only is not None:
        folder = tmp_path / f"files-{path.stem}"
        folder.mkdir()
        for migration in available_migrations():
            if migration.version in only:
                shutil.copy(migration.path, folder / migration.path.name)
    with closing(connect(path)) as conn:
        migrate(conn, folder)


def dump(path: Path) -> dict:
    """Schema and every row of every table (shadow tables of the FTS indexes included)."""
    with closing(sqlite3.connect(path)) as conn:
        schema = sorted(tuple(row) for row in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND sql NOT LIKE 'CREATE VIRTUAL TABLE%' ORDER BY name")]
        data = {}
        for table in tables:
            rowid = conn.execute(f"SELECT 1 FROM pragma_table_list WHERE name=? AND wr=0", (table,)).fetchone()
            order = " ORDER BY rowid" if rowid else ""
            cursor = conn.execute(f'SELECT * FROM "{table}"{order}')
            data[table] = ([column[0] for column in cursor.description], [tuple(map(clock, row)) for row in cursor])
        settings = {name: conn.execute(f"PRAGMA {name}").fetchone()[0]
                    for name in ("user_version", "application_id", "journal_mode", "page_size", "encoding", "auto_vacuum")}
    return {"schema": schema, "data": data, "pragmas": settings}


def assert_same(rust: dict, python: dict, again: dict) -> None:
    """`rust` equals `python`, except where two Python runs (`python`, `again`) differ too."""
    assert rust["schema"] == python["schema"]
    assert rust["pragmas"] == python["pragmas"]
    assert rust["data"].keys() == python["data"].keys()
    for table, (columns, rows) in python["data"].items():
        rust_columns, rust_rows = rust["data"][table]
        assert rust_columns == columns, table
        assert len(rust_rows) == len(rows), table
        for rust_row, row, other in zip(rust_rows, rows, again["data"][table][1]):
            for column, mine, theirs, second in zip(columns, rust_row, row, other):
                if theirs == second:
                    assert mine == theirs, (table, column)
                else:
                    assert type(mine) is type(theirs) and len(str(mine)) == len(str(theirs)), (table, column)


def test_a_fresh_database_matches(tmp_path):
    result = rust_migrate(tmp_path / "data" / "rust.db")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ",".join(map(str, VERSIONS))
    python_migrate(tmp_path / "data" / "python.db", None, tmp_path)
    python_migrate(tmp_path / "data" / "again.db", None, tmp_path)
    assert_same(dump(tmp_path / "data" / "rust.db"), dump(tmp_path / "data" / "python.db"), dump(tmp_path / "data" / "again.db"))
    again = rust_migrate(tmp_path / "data" / "rust.db")
    assert again.returncode == 0 and again.stdout.strip() == "", "a migrated database has nothing pending"


@pytest.mark.parametrize("step", list(STEPS))
def test_every_historical_schema_matches_and_upgrades_with_either_runner(tmp_path, step):
    only = STEPS[step]
    folder = tmp_path / "data"
    result = rust_migrate(folder / "rust.db", only)
    assert result.returncode == 0, result.stderr
    for name in ("python", "again"):
        python_migrate(folder / f"{name}.db", only, tmp_path)
    assert_same(dump(folder / "rust.db"), dump(folder / "python.db"), dump(folder / "again.db"))

    # The same historical database, upgraded by each runner (and once more by Python, for shape).
    for name in ("by-rust", "by-python", "by-python-again"):
        shutil.copy(folder / "python.db", folder / f"{name}.db")
    result = rust_migrate(folder / "by-rust.db")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ",".join(str(v) for v in VERSIONS if v not in only)
    python_migrate(folder / "by-python.db", None, tmp_path)
    shutil.copy(folder / "again.db", folder / "by-python-again.db")
    python_migrate(folder / "by-python-again.db", None, tmp_path)
    assert_same(dump(folder / "by-rust.db"), dump(folder / "by-python.db"), dump(folder / "by-python-again.db"))


def seed_main_schema(path: Path) -> None:
    """Synthetic rows at main's v0.3.33 schema in the tables later migrations rebuild or index."""
    at = "2026-01-01T00:00:00Z"
    with closing(connect(path)) as conn:
        conn.execute("INSERT INTO companies(id, norm_name, display_name, from_watchlist, created_at, updated_at)"
                     " VALUES (1, 'synthetic', 'Synthetic Co', 1, ?, ?)", (at, at))
        conn.execute("INSERT INTO jobs(id, company_id, title, location, url, dedupe_key, first_seen_at, last_seen_at,"
                     " created_at, updated_at) VALUES (1, 1, 'Synthetic Intern', 'Remote', 'https://example.invalid/1',"
                     " 'synthetic-1', ?, ?, ?, ?)", (at, at, at, at))
        conn.execute("INSERT INTO runs(id, kind, started_at) VALUES (1, 'discover', ?)", (at,))
        conn.execute("INSERT INTO study_subjects(id, name) VALUES (1, 'Synthetic subject')")
        conn.execute("INSERT INTO flash_decks(id, name, settings, created_at) VALUES (1, 'Synthetic deck', '{}', ?)", (at,))
        conn.execute("INSERT INTO rika_messages(id, conversation_id, role, text, created_at) VALUES"
                     " ('m1', 'c1', 'user', 'Plan my week', ?), ('m2', 'c1', 'rika', 'Here is a plan', ?)", (at, at))
        conn.execute("INSERT INTO rika_plans(id, message_id, request_id, items_json, segments_json, duration_s, created_at)"
                     " VALUES ('p1', 'm2', 'm1', '[]', '[]', 0, ?)", (at,))
        conn.commit()


def test_a_main_schema_database_with_rows_upgrades_the_same_with_either_runner(tmp_path):
    folder = tmp_path / "data"
    python_migrate(folder / "main.db", RELEASED_GAPS["main-049"], tmp_path)
    seed_main_schema(folder / "main.db")
    shutil.copy(folder / "main.db", folder / "main-again.db")
    for name, source in (("by-rust", "main"), ("by-python", "main"), ("by-python-again", "main-again")):
        shutil.copy(folder / f"{source}.db", folder / f"{name}.db")
    result = rust_migrate(folder / "by-rust.db")
    assert result.returncode == 0, result.stderr
    python_migrate(folder / "by-python.db", None, tmp_path)
    python_migrate(folder / "by-python-again.db", None, tmp_path)
    rust = dump(folder / "by-rust.db")
    assert_same(rust, dump(folder / "by-python.db"), dump(folder / "by-python-again.db"))
    # 047 queues the records that existed before the index; the first query builds them.
    pending = rust["data"]["search_pending"][1]
    assert ("job", "1") in pending and ("study_subject", "1") in pending and ("flashcard_deck", "1") in pending


def test_a_conversion_with_data_is_left_to_the_python_runner(tmp_path):
    """033 converts a pre-block database's notes in Python: Rust applies what comes before and stops."""
    path = tmp_path / "data" / "notes.db"
    python_migrate(path, RELEASED_GAPS["main-049"], tmp_path)
    with closing(connect(path)) as conn:
        conn.execute("INSERT INTO quick_notes(body, created_at, updated_at) VALUES ('Synthetic note', 't', 't')")
    result = rust_migrate(path)
    assert result.returncode == 3 and result.stdout.strip() == "needs-python 33", result
    with closing(connect(path)) as conn:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        assert 32 in applied and 33 not in applied
        migrate(conn)
        assert conn.execute("SELECT text FROM blocks WHERE type='quick_note'").fetchone()[0] == "Synthetic note"
    result = rust_migrate(path)
    assert result.returncode == 0 and result.stdout.strip() == ""
