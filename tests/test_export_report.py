"""Stage 6: the response-rate report by tier, and the formula-safe export."""

import csv
import json

from applyai.core import applications as applications_core
from applyai.core import export as export_core
from applyai.core import jobs as jobs_core
from applyai.core import stats as stats_core


def _applied(workspace, company, *, title="Intern", tier=None, status="applied"):
    track = workspace.config.track_names[0]
    job, _ = jobs_core.upsert_job(
        workspace.conn, company=company, title=title,
        url=f"https://example.invalid/{company}", location="Remote", tracks=[track],
    )
    if tier:
        jobs_core.set_tier(workspace.conn, job.id, tier)
    application = applications_core.record_application(workspace.conn, job.id, track=track)
    if status != "applied":
        applications_core.set_status(workspace.conn, application.id, status)
    return application


def test_response_rate_by_tier_separates_a_from_b_and_untriaged(workspace):
    _applied(workspace, "A1", tier="A", status="interview")
    _applied(workspace, "A2", tier="A", status="rejected")
    _applied(workspace, "B1", tier="B")
    _applied(workspace, "U1")

    by_tier = {row.key: row for row in stats_core.response_rate_by_tier(workspace.conn)}
    assert (by_tier["A"].applications, by_tier["A"].responses, by_tier["A"].advanced) == (2, 2, 1)
    assert by_tier["B"].responses == 0 and by_tier["B"].open_count == 1
    assert by_tier["untriaged"].applications == 1
    assert set(stats_core.response_report(workspace.conn)) == {"resume", "track", "source", "tier"}


def test_csv_export_neutralises_formulas_but_json_keeps_text(workspace, tmp_path):
    hostile = '=HYPERLINK("http://evil.invalid","x")'
    application = _applied(workspace, "Evil Co", title=hostile, status="oa")

    result = export_core.export_csv(workspace.conn, tmp_path / "csv")
    assert result.counts == {"jobs": 1, "applications": 1, "status_events": 2}
    with (tmp_path / "csv" / "applications.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["title"] == "'" + hostile
    assert row["status"] == "oa" and row["company"] == "Evil Co"

    export_core.export_json(workspace.conn, tmp_path / "all.json")
    document = json.loads((tmp_path / "all.json").read_text(encoding="utf-8"))
    assert document["applications"][0]["title"] == hostile
    events = [e for e in document["status_events"] if e["application_id"] == application.id]
    assert [e["to_status"] for e in events] == ["applied", "oa"]


def test_escape_cell_only_touches_formula_starts():
    assert [export_core.escape_cell(v) for v in ("+1", "-2", "@x", "ok", 5, None)] == \
        ["'+1", "'-2", "'@x", "ok", 5, None]
