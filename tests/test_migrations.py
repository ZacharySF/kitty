"""The migration runner."""

import pytest

from applyai.db import connect, current_version, latest_version, migrate, pending_migrations
from applyai.db.migrations import available_migrations


def test_migrating_twice_is_a_no_op(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    applied = migrate(conn)
    assert [m.version for m in applied] == [m.version for m in available_migrations()]
    assert current_version(conn) == latest_version()
    assert migrate(conn) == []
    assert pending_migrations(conn) == []


def test_foreign_keys_are_enforced(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO job_tracks (job_id, track, created_at) VALUES (999, 'quant', 'now')"
        )


def test_workable_migration_keeps_companies_and_their_jobs(tmp_path):
    """006 rebuilds companies (a CHECK change); ids, rows and job links must survive."""
    import shutil

    from applyai.db.migrations import MIGRATIONS_DIR

    early = tmp_path / "early"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 6:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn, early)
    conn.execute("INSERT INTO companies (id, norm_name, display_name, ats_kind, ats_slug, "
                 "created_at, updated_at) VALUES (41, 'zaber', 'Zaber', 'other', NULL, 't', 't')")
    conn.execute("INSERT INTO jobs (company_id, title, url, dedupe_key, first_seen_at, last_seen_at, "
                 "created_at, updated_at) VALUES (41, 'Intern', 'u', 'k', 't', 't', 't', 't')")

    migrate(conn, MIGRATIONS_DIR)

    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1       # switched back on
    conn.execute("UPDATE companies SET ats_kind = 'workable', ats_slug = 'zaber' WHERE id = 41")
    row = conn.execute("SELECT c.display_name FROM jobs j JOIN companies c ON c.id = j.company_id").fetchone()
    assert row[0] == "Zaber"
    with pytest.raises(Exception):                                     # still enforced
        conn.execute("INSERT INTO jobs (company_id, title, url, dedupe_key, first_seen_at, "
                     "last_seen_at, created_at, updated_at) VALUES (999, 'x', 'u', 'k2', 't', 't', 't', 't')")
    with pytest.raises(Exception):
        conn.execute("UPDATE companies SET ats_kind = 'taleo' WHERE id = 41")


def test_legacy_fill_marker_migrates_to_attempt_not_ready(tmp_path):
    import shutil

    early = tmp_path / "early"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 7:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "legacy.sqlite")
    migrate(conn, early)
    conn.execute(
        "INSERT INTO companies (id, norm_name, display_name, created_at, updated_at) "
        "VALUES (1, 'sample', 'Sample', 't', 't')"
    )
    conn.execute(
        "INSERT INTO jobs (id, company_id, title, url, dedupe_key, first_seen_at, last_seen_at, "
        "created_at, updated_at, filled_at, favorited_at, queue_state, my_notes) "
        "VALUES (1, 1, 'Intern', 'https://example.invalid/1', 'k', 't', 't', 't', 't', "
        "'2026-10-01T12:00:00Z', '2026-09-30T12:00:00Z', 'queued', 'My manual note')"
    )
    migrate(conn)
    row = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
    assert row["fill_state"] == "attempted"
    assert row["fill_attempted_at"] == "2026-10-01T12:00:00Z"
    assert row["fill_ready_at"] is None
    assert row["favorited_at"] == "2026-09-30T12:00:00Z"
    assert row["queue_state"] == "queued"
    assert row["my_notes"] == "My manual note"
    assert row["verification_status"] == "unverified" and row["verified_at"] is None
