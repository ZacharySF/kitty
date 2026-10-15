"""Atlas API: bounded, read-only entity fan-out for the 3D home graph."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kitty import web_atlas
from kitty.core import calendar, favorites, jobs, kanban
from kitty.db import connect
from kitty.web_server import APIError, WebApplication

WORKSPACE_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "workspace.ts"
NOW = datetime(2030, 1, 1, 12, tzinfo=UTC)
STAMP = "2029-12-31T00:00:00+00:00"


def view_ids() -> set[str]:
    source = WORKSPACE_TS.read_text(encoding="utf-8")
    navigation = source.split("export const navigation", 1)[1].split("] as const", 1)[0]
    return set(re.findall(r'\bid:\s*"([a-z]+)"', navigation))


def fill(conn, count: int) -> None:
    for index in range(count):
        conn.execute("INSERT INTO study_subjects(name) VALUES(?)", (f"Subject {index:02d}",))
        conn.execute("INSERT INTO opportunities(identity,title,source,created_at,updated_at)"
                     " VALUES(?,?,?,?,?)", (f"synthetic-{index}", f"Opportunity {index}", "manual", STAMP, STAMP))
        kanban.create_board(conn, {"title": f"Board {index}"})
        calendar.create_event(conn, {"title": f"Event {index}", "start": f"2030-01-{2 + index % 20:02d}T09:00:00Z",
                                     "end": f"2030-01-{2 + index % 20:02d}T10:00:00Z", "timezone": "UTC",
                                     "all_day": False})
        job, _ = jobs.upsert_job(conn, company=f"Synthetic Co {index}", title=f"Role {index}",
                                 url=f"https://example.invalid/{index}", source="manual",
                                 source_job_id=f"atlas-{index}")
        favorites.favorite(conn, [job.id])


def test_route_scoping_and_query_rejection(workspace, tmp_path):
    assert web_atlas.get(None, workspace.conn, "/api/atlasx", {}) is None
    assert web_atlas.get(None, workspace.conn, "/api/schema", {}) is None
    assert web_atlas.post(None, workspace.conn, "/api/atlas", {}) is None
    app = WebApplication(workspace.settings, workspace.config, tmp_path)
    try:
        result = app.get_json(workspace.conn, "/api/atlas", {})
        assert {kind["kind"] for kind in result["kinds"]} == set(web_atlas.KINDS)
        with pytest.raises(APIError) as error:
            app.get_json(workspace.conn, "/api/atlas", {"limit": "5"})
        assert error.value.status == 400
    finally:
        app.close()


def test_output_is_bounded_per_kind_and_exposes_only_safe_fields(workspace):
    fill(workspace.conn, web_atlas.LIMIT + 5)
    result = web_atlas.atlas(workspace.conn, NOW)
    counts: dict[str, int] = {}
    for item in result["items"]:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        assert set(item) == {"id", "kind", "ref", "label", "detail", "view"}
        assert item["id"] == f"{item['kind']}:{item['ref']}"
    for kind in ("subject", "opportunity", "board", "event", "job"):
        assert counts[kind] == web_atlas.LIMIT
    assert all(count <= web_atlas.LIMIT for count in counts.values())
    assert len({item["id"] for item in result["items"]}) == len(result["items"])
    jobs_out = [item for item in result["items"] if item["kind"] == "job"]
    assert all(item["view"] == "favorites" and " — " in item["label"] for item in jobs_out)
    assert web_atlas.atlas(workspace.conn, NOW) == result


def test_events_are_upcoming_and_deduplicated(workspace):
    calendar.create_event(workspace.conn, {"title": "Past", "start": "2029-06-01T09:00:00Z",
                                           "end": "2029-06-01T10:00:00Z", "timezone": "UTC", "all_day": False})
    calendar.create_event(workspace.conn, {"title": "Weekly", "start": "2030-01-02T09:00:00Z",
                                           "end": "2030-01-02T10:00:00Z", "timezone": "UTC", "all_day": False,
                                           "rrule": "FREQ=WEEKLY"})
    events = [item for item in web_atlas.atlas(workspace.conn, NOW)["items"] if item["kind"] == "event"]
    assert [item["label"] for item in events] == ["Weekly"]
    assert events[0]["detail"] == "2030-01-02 09:00"


def test_missing_tables_are_tolerated(tmp_path):
    conn = connect(tmp_path / "old.db")
    try:
        conn.execute("CREATE TABLE study_subjects (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO study_subjects(name) VALUES ('Linear algebra')")
        result = web_atlas.atlas(conn, NOW)
    finally:
        conn.close()
    assert result["items"] == [{"id": "subject:1", "kind": "subject", "ref": 1, "label": "Linear algebra",
                                "detail": "", "view": "study"}]


def test_entity_kinds_map_to_real_workspace_views(workspace):
    views = view_ids()
    assert {"today", "study", "jobs", "queue", "favorites"} <= views
    assert all(view in views for _, view in web_atlas.KINDS.values())
    fill(workspace.conn, 2)
    workspace.conn.execute("UPDATE jobs SET favorited_at = NULL, queue_state = 'queued'"
                           " WHERE id = (SELECT MIN(id) FROM jobs)")
    items = web_atlas.atlas(workspace.conn, NOW)["items"]
    assert {item["view"] for item in items} <= views
    assert {"queue", "favorites"} <= {item["view"] for item in items if item["kind"] == "job"}
