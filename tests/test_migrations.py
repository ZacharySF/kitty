"""The migration runner."""

import pytest

from kitty.db import connect, current_version, latest_version, migrate, pending_migrations
from kitty.db.migrations import available_migrations


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

    from kitty.db.migrations import MIGRATIONS_DIR

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


def test_migration_resources_survive_removal_of_import_alias(tmp_path, workspace, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    from kitty.core.workspace import sync_config_to_db
    from kitty.db import migrations

    alias = tmp_path / 'import-alias'
    alias.symlink_to(Path(migrations.__file__).resolve().parent, target_is_directory=True)
    spec = importlib.util.spec_from_file_location('migration_alias_probe', alias / 'migrations.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    alias.unlink()
    conn = connect(tmp_path / 'after-alias-removal.sqlite')
    try:
        module.migrate(conn)
        sync_config_to_db(conn, workspace.config)
        assert {row['name'] for row in conn.execute('SELECT name FROM tracks')} == set(workspace.config.track_names)
    finally:
        conn.close()


@pytest.mark.parametrize('exists', [False, True])
def test_missing_bundled_migrations_cannot_bootstrap_a_partial_workspace(tmp_path, monkeypatch, exists):
    from kitty.db import migrations
    directory = tmp_path / 'missing-or-empty'
    if exists:
        directory.mkdir()
    monkeypatch.setattr(migrations, 'MIGRATIONS_DIR', directory)
    conn = connect(tmp_path / 'uninitialized.sqlite')
    try:
        with pytest.raises(FileNotFoundError):
            migrations.migrate(conn)
        assert conn.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0] == 0
    finally:
        conn.close()

def test_recovery_strategy_migration_preserves_history_and_constraints(tmp_path):
    import shutil
    import sqlite3

    early = tmp_path / "before-strategies"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 29:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "recovery.sqlite")
    try:
        migrate(conn, early)
        conn.execute(
            "INSERT INTO application_recovery_events(id,created_at,action,result,reason,step) "
            "VALUES(41,'t','retry','failed','supported_retry',2)"
        )
        migrate(conn)
        row = conn.execute("SELECT * FROM application_recovery_events WHERE id=41").fetchone()
        assert (row["action"], row["result"], row["step"]) == ("retry", "failed", 2)
        conn.execute(
            "INSERT INTO application_recovery_events(created_at,action,result,reason) "
            "VALUES('t','recommit','no_effect','commit_saved_value')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO application_recovery_events(created_at,action,result,reason) "
                "VALUES('t','arbitrary_script','completed','untrusted')"
            )
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_calendar_palette_migration_moves_the_fixed_default_onto_theme_slots(tmp_path):
    """035 drops the #6d5dfc default: local calendars still on it take the palette slot
    the week view already painted them (name order); chosen and Google hex colours stay."""
    import shutil
    import sqlite3

    early = tmp_path / "before-palette"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 35:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "calendar.sqlite")
    try:
        migrate(conn, early)
        conn.executescript(
            "INSERT INTO calendar_calendars(id,name,color,updated_at) VALUES"
            " ('school','School','#6d5dfc','t'),('work','Work','#123abc','t'),('alpha','Alpha','#6D5DFC','t');"
            "INSERT INTO calendar_calendars(id,name,color,google_id,updated_at) VALUES ('g','Google','#6d5dfc','remote','t');"
            "INSERT INTO calendar_events(id,calendar_id,title,start,end,timezone,data,updated_at)"
            " VALUES ('e','school','Seminar','2026-10-26','2026-10-27','UTC','{}','t');"
        )
        migrate(conn)
        colors = dict(conn.execute("SELECT id,color FROM calendar_calendars").fetchall())
        assert colors == {"alpha": "c1", "g": "#6d5dfc", "personal": "c3", "school": "c4", "work": "#123abc"}
        assert conn.execute("SELECT calendar_id FROM calendar_events WHERE id='e'").fetchone()[0] == "school"
        assert "6d5dfc" not in conn.execute("SELECT sql FROM sqlite_master WHERE name='calendar_calendars'").fetchone()[0]
        subject = conn.execute("INSERT INTO study_subjects(name) VALUES('Physics')").lastrowid
        conn.execute("UPDATE calendar_calendars SET subject_id=? WHERE id='school'", (subject,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE calendar_calendars SET subject_id=999 WHERE id='work'")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
