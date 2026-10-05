"""CSV import: preview first, write second, and safe to run twice."""

from applyai.core import applications as applications_core
from applyai.core import importer as importer_core
from applyai.core import jobs as jobs_core

CSV = """Company,Role,Link,Location,Status,Date Applied,Track,Notes
Ridgeline Capital,Quant Intern,https://example.invalid/a,New York NY,Interview,2026-08-14,quant,referred
Lumen Silicon,GPU Intern,https://example.invalid/b,Santa Clara CA,rejected,2026-08-20,gpu,
Main Street Coffee,Barista,https://example.invalid/c,Waco TX,applied,2026-09-01,parttime,walk-in
"""


def _write(tmp_path, text=CSV):
    path = tmp_path / "old.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_headers_are_mapped_by_alias(workspace, tmp_path):
    plan = importer_core.plan_import(
        workspace.conn, _write(tmp_path), valid_tracks=workspace.config.track_names
    )
    assert plan.mapping["title"] == "Role"
    assert plan.mapping["url"] == "Link"
    assert plan.mapping["applied_at"] == "Date Applied"
    assert len(plan.creatable) == 3
    assert plan.problems == []


def test_status_words_are_normalized_and_dates_parsed(workspace, tmp_path):
    plan = importer_core.plan_import(
        workspace.conn, _write(tmp_path), valid_tracks=workspace.config.track_names
    )
    statuses = [row.status for row in plan.rows]
    assert statuses == ["interview", "rejected", "applied"]
    assert plan.rows[0].applied_at.startswith("2026-08-14")


def test_import_creates_jobs_applications_and_history(workspace, tmp_path):
    plan = importer_core.plan_import(
        workspace.conn, _write(tmp_path), valid_tracks=workspace.config.track_names
    )
    stats = importer_core.apply_import(workspace.conn, plan)
    assert stats == {"jobs_created": 3, "applications_created": 3, "events": 5, "skipped": 0}

    interview_job = jobs_core.list_jobs(
        workspace.conn, jobs_core.JobFilter(text="Ridgeline")
    )[0]
    application = applications_core.get_application_for_job(workspace.conn, interview_job.id)
    assert application.status == "interview"
    events = applications_core.history(workspace.conn, application.id)
    assert [e.source for e in events] == ["import", "import"]
    assert all(e.state == "confirmed" for e in events)


def test_importing_the_same_file_twice_changes_nothing(workspace, tmp_path):
    path = _write(tmp_path)
    first = importer_core.plan_import(workspace.conn, path, valid_tracks=workspace.config.track_names)
    importer_core.apply_import(workspace.conn, first)

    second = importer_core.plan_import(workspace.conn, path, valid_tracks=workspace.config.track_names)
    assert len(second.creatable) == 0
    assert len(second.already_present) == 3
    stats = importer_core.apply_import(workspace.conn, second)
    assert stats["applications_created"] == 0 and stats["skipped"] == 3
    assert jobs_core.count_jobs(workspace.conn) == 3


def test_bad_rows_are_reported_not_written(workspace, tmp_path):
    path = _write(tmp_path, CSV + "No Title Co,,https://example.invalid/d,,applied,,quant,\n"
                                  "Bad Status Co,Intern,https://example.invalid/e,,elephant,,quant,\n"
                                  "Bad Track Co,Intern,https://example.invalid/f,,applied,,welding,\n")
    plan = importer_core.plan_import(workspace.conn, path, valid_tracks=workspace.config.track_names)
    problems = [row.problem for row in plan.problems]
    assert len(problems) == 3
    assert any("company and a title" in p for p in problems)
    assert any("unknown status" in p for p in problems)
    assert any("unknown track" in p for p in problems)

    importer_core.apply_import(workspace.conn, plan)
    assert jobs_core.count_jobs(workspace.conn) == 3
