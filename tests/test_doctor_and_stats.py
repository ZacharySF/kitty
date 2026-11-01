"""doctor checks, resume registration and the weekly/report numbers."""

from pathlib import Path

from kitty.core import applications as applications_core
from kitty.core import doctor as doctor_core
from kitty.core import jobs as jobs_core
from kitty.core import resumes as resumes_core
from kitty.core import stats as stats_core
from kitty.settings import Settings


def test_doctor_passes_on_a_fresh_workspace(workspace):
    checks = doctor_core.run_all(workspace.settings, workspace.conn)
    assert doctor_core.failures(checks) == []
    names = [check.name for check in checks]
    assert "database" in names and "config/tracks.yaml" in names


def test_doctor_fails_when_the_database_is_missing(tmp_path, workspace):
    settings = Settings.load(root=workspace.settings.root, db_path=tmp_path / "nope.db")
    failures = doctor_core.failures(doctor_core.check_database(settings))
    assert len(failures) == 1
    assert "kitty init" in failures[0].fix


def test_doctor_never_prints_a_key_value(workspace, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    checks = doctor_core.check_keys(Settings.load(root=workspace.settings.root))
    line = next(check for check in checks if check.name == "key/ANTHROPIC")
    assert line.detail == "set (env)"
    assert "secret" not in str(checks)


def test_resume_registration_hashes_the_file_and_notices_edits(workspace, tmp_path):
    path = tmp_path / "quant.txt"
    path.write_text("resume v1", encoding="utf-8")
    resume = resumes_core.add_resume(
        workspace.conn, "quant", path, description="probability and C++",
        default_tracks=["quant"],
    )
    assert resume.file_hash == resumes_core.file_hash(path)
    assert resumes_core.check_files(workspace.conn) == []

    path.write_text("resume v2", encoding="utf-8")
    problems = resumes_core.check_files(workspace.conn)
    assert problems and "changed" in problems[0][1]

    Path(path).unlink()
    assert "missing" in resumes_core.check_files(workspace.conn)[0][1]


def test_doctor_warns_about_a_track_whose_resume_is_not_registered(workspace):
    warnings = doctor_core.check_tracks(workspace.conn)
    assert {check.name for check in warnings} >= {"track/quant", "track/gpu"}
    assert all(check.ok and check.warning for check in warnings)


def _applied(workspace, company, track, resume_id=None, status="applied"):
    job, _ = jobs_core.upsert_job(
        workspace.conn, company=company, title="Intern",
        url=f"https://example.invalid/{company}", location="Remote", tracks=[track],
    )
    application = applications_core.record_application(
        workspace.conn, job.id, track=track, resume_id=resume_id
    )
    if status != "applied":
        applications_core.set_status(workspace.conn, application.id, status)
    return application


def test_weekly_progress_counts_this_week_against_the_target(workspace):
    workspace.conn.execute("UPDATE tracks SET weekly_target = 5 WHERE name = 'quant'")
    _applied(workspace, "A Co", "quant")
    _applied(workspace, "B Co", "quant")
    _applied(workspace, "C Co", "gpu")

    progress = {item.track: item for item in stats_core.weekly_progress(workspace.conn)}
    assert progress["quant"].applied_this_week == 2
    assert progress["quant"].weekly_target == 5
    assert progress["quant"].remaining == 3
    assert progress["cpu"].applied_this_week == 0
    assert stats_core.weekly_total(workspace.conn) == 3


def test_response_rate_by_resume_and_track(workspace, tmp_path):
    path = tmp_path / "quant.txt"
    path.write_text("x", encoding="utf-8")
    resume = resumes_core.add_resume(workspace.conn, "quant", path)

    _applied(workspace, "A Co", "quant", resume.id, status="oa")
    _applied(workspace, "B Co", "quant", resume.id, status="rejected")
    _applied(workspace, "C Co", "quant", resume.id)

    by_resume = {row.key: row for row in stats_core.response_rate_by_resume(workspace.conn)}
    assert by_resume["quant"].applications == 3
    assert by_resume["quant"].responses == 2
    assert by_resume["quant"].advanced == 1
    assert by_resume["quant"].open_count == 2

    by_track = {row.key: row for row in stats_core.response_rate_by_track(workspace.conn)}
    assert by_track["quant"].applications == 3
    by_source = {row.key: row for row in stats_core.response_rate_by_source(workspace.conn)}
    assert by_source["manual"].applications == 3
