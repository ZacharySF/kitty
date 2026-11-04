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


def test_rika_tables_keep_their_conversation_and_tier_settings(tmp_path):
    """037: Rika's messages, actions and approvals, and the three tier models in Settings → AI."""
    import shutil
    import sqlite3

    early = tmp_path / "before-rika"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 37:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "rika.sqlite")
    try:
        migrate(conn, early)
        conn.execute("UPDATE ai_settings SET model='anthropic/claude-sonnet-4.5' WHERE id=1")
        migrate(conn)
        row = conn.execute("SELECT model,rika_fast_model,rika_standard_model,rika_deep_model FROM ai_settings").fetchone()
        assert tuple(row) == ("anthropic/claude-sonnet-4.5", "", "", "")
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,created_at) VALUES('u','c','user','hi','t1')")
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,reply_to,tier,status,created_at)"
                     " VALUES('r','c','rika','','u','fast','pending','t2')")
        conn.execute("INSERT INTO rika_actions(id,message_id,tool,args_json,summary,reversible,undo_json,status,created_at)"
                     " VALUES('a','r','add_card','{}','Added a card',1,'{\"note_id\":1}','done','t3')")
        conn.execute("INSERT INTO rika_approvals(id,message_id,tool,args_json,summary,detail,created_at)"
                     " VALUES('p','r','start_worker','{}','Start omp','Task brief','t4')")
        assert conn.execute("SELECT status FROM rika_approvals WHERE id='p'").fetchone()[0] == "pending"
        for statement in (
            "INSERT INTO rika_messages(id,conversation_id,role,text,created_at) VALUES('x','c','system','','t')",
            "INSERT INTO rika_messages(id,conversation_id,role,text,tier,created_at) VALUES('y','c','rika','','huge','t')",
            "INSERT INTO rika_actions(id,message_id,tool,args_json,summary,reversible,status,created_at)"
            " VALUES('b','r','t','{}','s',1,'maybe','t')",
            "INSERT INTO rika_approvals(id,message_id,tool,args_json,summary,detail,created_at)"
            " VALUES('q','missing','t','{}','s','d','t')",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement)
        conn.execute("DELETE FROM rika_messages WHERE id='r'")
        assert conn.execute("SELECT count(*) FROM rika_actions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM rika_approvals").fetchone()[0] == 0
    finally:
        conn.close()


def test_rika_voice_adds_message_sources_plans_and_the_voice_model(tmp_path):
    """045: how a request reached Rika, brain-dump plans, and the Whisper model setting."""
    import shutil
    import sqlite3

    early = tmp_path / "before-voice"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 45:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "voice.sqlite")
    try:
        migrate(conn, early)
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,created_at) VALUES('old','c','user','hi','t0')")
        migrate(conn)
        assert conn.execute("SELECT source FROM rika_messages WHERE id='old'").fetchone()[0] == "typed"
        assert conn.execute("SELECT rika_voice_model FROM ai_settings WHERE id=1").fetchone()[0] == "base"
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,source,created_at) VALUES('d','c','user','dump','dump','t1')")
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,reply_to,created_at) VALUES('r','c','rika','','d','t2')")
        conn.execute("INSERT INTO rika_plans(id,message_id,request_id,items_json,segments_json,duration_s,created_at)"
                     " VALUES('p','r','d','[]','[]',12.5,'t3')")
        assert conn.execute("SELECT status FROM rika_plans").fetchone()[0] == "pending"
        for statement in (
            "INSERT INTO rika_messages(id,conversation_id,role,text,source,created_at) VALUES('x','c','user','','sung','t')",
            "INSERT INTO rika_plans(id,message_id,request_id,items_json,segments_json,duration_s,created_at)"
            " VALUES('q','r','d','[]','[]',-1,'t')",
            "INSERT INTO rika_plans(id,message_id,request_id,items_json,segments_json,duration_s,status,created_at)"
            " VALUES('q','r','d','[]','[]',1,'maybe','t')",
            "INSERT INTO rika_plans(id,message_id,request_id,items_json,segments_json,duration_s,created_at)"
            " VALUES('q','missing','d','[]','[]',1,'t')",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement)
        conn.execute("DELETE FROM rika_messages WHERE id='d'")
        assert conn.execute("SELECT count(*) FROM rika_plans").fetchone()[0] == 0
    finally:
        conn.close()


def test_rika_goals_mail_and_memory_tables(tmp_path):
    """049: routing memory, goals and their matches, the brief, mail drafts, preferences and the FTS index."""
    import sqlite3

    conn = connect(tmp_path / "goals.sqlite")
    try:
        migrate(conn)
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,created_at,tokens_in,tokens_cached,tokens_out,"
                     "route_json) VALUES('r','c','rika','ok','t1',1180,960,64,'{\"path\":[\"add_card\"]}')")
        conn.execute("INSERT INTO rika_routes(signature,label,tier,path_json,created_at,updated_at)"
                     " VALUES('add_card','Add card','fast','[\"add_card\"]','t','t')")
        conn.execute("INSERT INTO rika_route_shapes(shape,signature,updated_at) VALUES('add card','add_card','t')")
        conn.execute("INSERT INTO rika_shortcuts(template,signature,tool,created_at,updated_at)"
                     " VALUES('card {front} / {back}','add_card','add_card','t','t')")
        conn.execute("DELETE FROM rika_routes WHERE signature='add_card'")
        assert conn.execute("SELECT count(*) FROM rika_route_shapes").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM rika_shortcuts").fetchone()[0] == 0

        conn.execute("INSERT INTO goals(id,title,created_at,updated_at) VALUES(1,'Internship','t','t')")
        row = conn.execute("SELECT status,measures_json,revision FROM goals").fetchone()
        assert tuple(row) == ("active", "[]", 1)
        conn.execute("INSERT INTO goal_links(goal_id,kind,ref,created_at) VALUES(1,'job','4','t')")
        conn.execute("INSERT INTO goal_matches(goal_id,kind,ref,title,score,created_at) VALUES(1,'job','5','Intern',0.8,'t')")
        conn.execute("INSERT INTO rika_briefs(day,created_at,summary_json) VALUES('2026-11-03','t','{}')")
        conn.execute("INSERT INTO rika_mail_messages(id,thread_id,sender_email,received_at,kind,needs_reply,fetched_at)"
                     " VALUES('m1','t1','maya@example.invalid','t','scheduling',1,'t')")
        conn.execute("INSERT INTO rika_mail_drafts(id,mail_id,to_address,subject,body,kind,status,created_at,updated_at)"
                     " VALUES('d1','m1','maya@example.invalid','Re: onsite','Tue works','scheduling','draft','t','t')")
        assert [tuple(r) for r in conn.execute("SELECT kind,auto_send FROM rika_mail_settings ORDER BY kind")] == [
            ("confirmation", 0), ("scheduling", 0), ("thanks", 0)]
        assert conn.execute("SELECT hold_seconds FROM rika_mail_config").fetchone()[0] == 30
        conn.execute("INSERT INTO rika_preferences(text,source,created_at,updated_at) VALUES('Evenings after 9pm are free','stated','t','t')")
        conn.execute("INSERT INTO rika_index(title,body,source,ref,at) VALUES('Portfolio plan','ship the case study','page','1','t')")
        assert conn.execute("SELECT ref FROM rika_index WHERE rika_index MATCH 'studies'").fetchone()[0] == "1"
        for statement in (
            "INSERT INTO goals(title,status,created_at,updated_at) VALUES('x','maybe','t','t')",
            "INSERT INTO goal_links(goal_id,kind,ref,created_at) VALUES(1,'planet','1','t')",
            "INSERT INTO goal_links(goal_id,kind,ref,created_at) VALUES(99,'job','1','t')",
            "INSERT INTO rika_routes(signature,label,tier,path_json,created_at,updated_at) VALUES('x','x','huge','[]','t','t')",
            "INSERT INTO rika_mail_drafts(id,to_address,subject,body,kind,status,created_at,updated_at)"
            " VALUES('d2','a','s','b','scheduling','lost','t','t')",
            "INSERT INTO rika_mail_settings(kind,auto_send) VALUES('personal',1)",
            "UPDATE rika_mail_config SET hold_seconds=1",
            "INSERT INTO rika_preferences(text,source,created_at,updated_at) VALUES('','stated','t','t')",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement)
        conn.execute("DELETE FROM goals WHERE id=1")
        assert conn.execute("SELECT count(*) FROM goal_links").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM goal_matches").fetchone()[0] == 0
    finally:
        conn.close()
