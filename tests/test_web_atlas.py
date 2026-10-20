"""Section-only home and private per-section entity graphs."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from kitty import web_atlas
from kitty.core import calendar, favorites, jobs
from kitty.db import connect
from kitty.web_server import APIError, WebApplication

NOW = datetime(2030, 1, 1, 12, tzinfo=UTC)
STAMP = "2029-12-31T00:00:00+00:00"



def fill(conn, count: int) -> None:
    for index in range(count):
        conn.execute("INSERT INTO study_subjects(name) VALUES(?)", (f"Subject {index:02d}",))
        conn.execute("INSERT INTO opportunities(identity,title,source,created_at,updated_at)"
                     " VALUES(?,?,?,?,?)", (f"synthetic-{index}", f"Opportunity {index}", "manual", STAMP, STAMP))
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
        assert {section["kind"] for section in result["sections"]} == set(web_atlas.KINDS)
        with pytest.raises(APIError) as error:
            app.get_json(workspace.conn, "/api/atlas", {"limit": "5"})
        assert error.value.status == 400
    finally:
        app.close()


def test_home_keeps_records_inside_sections_and_reports_full_counts(workspace):
    fill(workspace.conn, 45)
    result = web_atlas.atlas(workspace.conn, NOW)
    assert set(result) == {"sections"}
    sections = {section["kind"]: section for section in result["sections"]}
    assert sections["job"]["count"] == 45
    assert sections["opportunity"]["count"] == 45
    assert sections["subject"]["count"] == 45
    assert "Synthetic Co" not in json.dumps(result)
    assert "Role 0" not in json.dumps(result)
    assert "Opportunity 0" not in json.dumps(result)


def test_events_are_upcoming_and_deduplicated(workspace):
    calendar.create_event(workspace.conn, {"title": "Past", "start": "2029-06-01T09:00:00Z",
                                           "end": "2029-06-01T10:00:00Z", "timezone": "UTC", "all_day": False})
    calendar.create_event(workspace.conn, {"title": "Weekly", "start": "2030-01-02T09:00:00Z",
                                           "end": "2030-01-02T10:00:00Z", "timezone": "UTC", "all_day": False,
                                           "rrule": "FREQ=WEEKLY"})
    events = next(section for section in web_atlas.atlas(workspace.conn, NOW)["sections"] if section["kind"] == "event")
    assert events["count"] == 1


def test_missing_tables_are_tolerated(tmp_path):
    conn = connect(tmp_path / "old.db")
    try:
        conn.execute("CREATE TABLE study_subjects (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO study_subjects(name) VALUES ('Linear algebra')")
        result = web_atlas.atlas(conn, NOW)
    finally:
        conn.close()
    sections = {section["kind"]: section for section in result["sections"]}
    assert sections["subject"]["count"] == 1
    assert sections["job"]["count"] is None
    assert "Linear algebra" not in json.dumps(result)


def assert_section_graph(result, *, bounded=True):
    assert set(result) == {"nodes", "links", "total", "shown"}
    assert result["shown"] == len(result["nodes"])
    if bounded:
        assert result["shown"] <= web_atlas.SECTION_LIMIT
    assert result["shown"] <= result["total"]
    ids = {node["id"] for node in result["nodes"]}
    assert len(ids) == result["shown"]
    for node in result["nodes"]:
        assert set(node) == {"id", "entity", "ref", "label", "group", "kind", "size", "detail"}
        assert node["id"] == f"{node['entity']}:{node['ref']}"
        assert len(node["label"]) <= web_atlas.LABEL_LIMIT
        assert len(node["detail"]) <= web_atlas.LABEL_LIMIT
    edges = {(link["source"], link["target"]) for link in result["links"]}
    assert len(edges) == len(result["links"])
    assert all(source in ids and target in ids for source, target in edges)
    assert all(link["directed"] is True for link in result["links"])
    return edges


@pytest.mark.parametrize("section", ["jobs", "flashcards", "notes", "study", "opportunities"])
def test_section_routes_are_read_only_and_reject_unrecognized_query_fields(workspace, tmp_path, section):
    app = WebApplication(workspace.settings, workspace.config, tmp_path)
    try:
        path = f"/api/atlas/sections/{section}"
        result = app.get_json(workspace.conn, path, {})
        assert_section_graph(result)
        for query in ({"limit": "5"}, {"search": "synthetic"}, {"section": section}, {"offset": "120"}):
            with pytest.raises(APIError) as error:
                app.get_json(workspace.conn, path, query)
            assert error.value.status == 400
        assert web_atlas.post(app, workspace.conn, path, {}) is None
        assert web_atlas.get(app, workspace.conn, path + "/extra", {}) is None
        assert web_atlas.get(app, workspace.conn, path + "x", {}) is None
        assert web_atlas.get(app, workspace.conn, "/api/atlas/sections", {}) is None
        assert web_atlas.get(app, workspace.conn, "/api/atlas/sections/other", {}) is None
    finally:
        app.close()


def test_jobs_section_contains_unsaved_jobs_and_actual_multitrack_edges(workspace):
    conn = workspace.conn
    tracks = [row[0] for row in conn.execute("SELECT name FROM tracks WHERE active = 1 ORDER BY sort_order, name")]
    assert len(tracks) >= 2
    first, _ = jobs.upsert_job(conn, company="Synthetic Graph Co", title="New role",
                              url="https://example.invalid/graph/1", tracks=tracks[:2],
                              jd_text="PRIVATE_DESCRIPTION_DO_NOT_EXPOSE")
    second, _ = jobs.upsert_job(conn, company="Synthetic Graph Co", title="Unassigned role",
                               url="https://example.invalid/graph/2")
    conn.execute("UPDATE jobs SET my_notes = 'PRIVATE_JOB_NOTES', queue_state = 'new'")
    main_before = web_atlas.atlas(conn, NOW)
    assert "New role" not in json.dumps(main_before)
    result = web_atlas.SECTIONS["jobs"](conn)
    assert not assert_section_graph(result)
    assert result["total"] == len(tracks)
    assert {node["ref"] for node in result["nodes"]} == set(tracks)
    assert all(node["entity"] == "track" and node["kind"] == "hub" for node in result["nodes"])
    for track in tracks[:2]:
        scoped = web_atlas._job_track(conn, track)
        assert {node["ref"] for node in scoped["nodes"] if node["entity"] == "job"} == {first.id}
        assert assert_section_graph(scoped) == {(f"track:{track}", f"job:{first.id}")}
        assert "PRIVATE_" not in json.dumps(scoped)
    assert "PRIVATE_" not in json.dumps(result)
    assert "https://" not in json.dumps(result)
    assert web_atlas.atlas(conn, NOW) == main_before


def test_jobs_root_has_only_categories_and_track_contains_every_job_without_paging(workspace):
    conn = workspace.conn
    track = conn.execute("SELECT name FROM tracks WHERE active = 1 ORDER BY sort_order LIMIT 1").fetchone()[0]
    count = web_atlas.SECTION_LIMIT + 7
    expected = set()
    for index in range(count):
        job, _ = jobs.upsert_job(conn, company="Synthetic Graph Co", title=f"Role {index:04d}",
                                url=f"https://example.invalid/graph/{index}", tracks=[track])
        expected.add(job.id)
    # Group hubs are not sampled either, including tracks beyond the former cap.
    for index in range(web_atlas.SECTION_GROUP_LIMIT + 3):
        conn.execute("INSERT INTO tracks(name,active,sort_order,triage_depth,config_json,updated_at)"
                     " VALUES(?,1,?,'none','{}',?)",
                     (f"Extra graph type {index}", index + 100, STAMP))
    track_count = conn.execute("SELECT COUNT(*) FROM tracks WHERE active = 1").fetchone()[0]
    result = web_atlas.get(None, conn, "/api/atlas/sections/jobs", {})
    assert_section_graph(result, bounded=False)
    assert result["shown"] == result["total"] == track_count
    assert all(node["entity"] == "track" for node in result["nodes"])
    assert not result["links"]
    focused = web_atlas.get(None, conn, "/api/atlas/sections/jobs", {"focus": f"track:{track}"})
    assert_section_graph(focused, bounded=False)
    assert focused["shown"] == focused["total"] == count + 1
    assert {node["ref"] for node in focused["nodes"] if node["entity"] == "job"} == expected


def add_folder(conn, title, parent=None):
    return conn.execute("INSERT INTO note_folders(title,parent_id,created_at,updated_at) VALUES(?,?,?,?)",
                        (title, parent, STAMP, STAMP)).lastrowid


def add_notebook(conn, title, folder=None):
    return conn.execute("INSERT INTO note_notebooks(title,folder_id,created_at,updated_at) VALUES(?,?,?,?)",
                        (title, folder, STAMP, STAMP)).lastrowid


def add_page(conn, title, notebook):
    return conn.execute("INSERT INTO note_pages(title,notebook_id,document,search_text,created_at,updated_at)"
                        " VALUES(?,?,?,?,?,?)",
                        (title, notebook, '{"private":"PRIVATE_NOTE_BODY"}', "PRIVATE_SEARCH_TEXT", STAMP, STAMP)).lastrowid


def test_notes_section_retains_nested_folder_notebook_page_relations(workspace):
    conn = workspace.conn
    root = add_folder(conn, "Synthetic root")
    child = add_folder(conn, "Nested", root)
    notebook = add_notebook(conn, "Notebook", child)
    loose = add_notebook(conn, "Root notebook")
    page = add_page(conn, "Page", notebook)
    loose_page = add_page(conn, "Root page", loose)
    result = web_atlas.SECTIONS["notes"](conn)
    edges = assert_section_graph(result)
    assert result["total"] == result["shown"] == 6
    assert edges == {(f"folder:{root}", f"folder:{child}"),
                     (f"folder:{child}", f"notebook:{notebook}"),
                     (f"notebook:{notebook}", f"page:{page}"),
                     (f"notebook:{loose}", f"page:{loose_page}")}
    groups = {node["id"]: node["group"] for node in result["nodes"]}
    assert groups[f"folder:{root}"] == groups[f"folder:{child}"] == groups[f"page:{page}"]
    assert groups[f"notebook:{loose}"] == groups[f"page:{loose_page}"]
    assert "PRIVATE_" not in json.dumps(result)


def test_notes_section_bounds_retain_parents_and_report_unshown_pages(workspace):
    conn = workspace.conn
    root = add_folder(conn, "Root")
    current = root
    for index in range(web_atlas.SECTION_GROUP_LIMIT + 3):
        current = add_folder(conn, f"Deep folder {index}", current)
    book = add_notebook(conn, "Visible notebook", root)
    count = web_atlas.SECTION_LIMIT + 5
    for index in range(count):
        add_page(conn, f"Page {index}", book)
    result = web_atlas.SECTIONS["notes"](conn)
    edges = assert_section_graph(result)
    assert result["total"] == web_atlas.SECTION_GROUP_LIMIT + 5 + count
    assert result["shown"] == web_atlas.SECTION_LIMIT
    nodes = {node["id"]: node for node in result["nodes"]}
    for node in result["nodes"]:
        if node["entity"] == "folder" and node["ref"] != root:
            assert any(target == node["id"] for _, target in edges)
        if node["entity"] == "page":
            assert (f"notebook:{book}", node["id"]) in edges
    shown_pages = sum(node["entity"] == "page" for node in result["nodes"])
    assert nodes[f"notebook:{book}"]["detail"] == f"{count} pages · {shown_pages} shown"


def test_study_section_uses_only_real_project_and_explicit_concept_links(workspace):
    conn = workspace.conn
    subject = conn.execute("INSERT INTO study_subjects(name) VALUES('Synthetic algebra')").lastrowid
    other = conn.execute("INSERT INTO study_subjects(name) VALUES('Unrelated subject')").lastrowid
    project = conn.execute("INSERT INTO study_projects(name,subject_id) VALUES('Problem sets',?)", (subject,)).lastrowid
    independent = conn.execute("INSERT INTO study_projects(name) VALUES('Independent reading')").lastrowid
    document = {
        "nodes": [
            {"id": "class", "kind": "class", "label": "Stale subject label", "source": {"kind": "subject", "id": subject}},
            {"id": "topic", "kind": "topic", "label": "Vector spaces", "notes": "PRIVATE_SCHEMA_NOTES"},
            {"id": "idea", "kind": "idea", "label": "Basis"},
            {"id": "unrelated", "kind": "topic", "label": "Not connected"},
            {"id": "deleted", "kind": "class", "label": "Deleted subject", "source": {"kind": "subject", "id": 999999}},
            {"id": "dangling", "kind": "topic", "label": "Deleted subject concept"},
        ],
        "edges": [
            {"from": "class", "to": "topic"},
            {"from": "idea", "to": "class"},
            {"from": "deleted", "to": "dangling"},
        ],
    }
    map_id = conn.execute("INSERT INTO schema_maps(title,mode,graph_json,viewport_json,updated_at) VALUES(?,?,?,?,?)",
                          ("Synthetic study map", "study", json.dumps(document), "{}", STAMP)).lastrowid
    # A map in a different mode cannot establish a study-specific relation.
    conn.execute("INSERT INTO schema_maps(title,mode,graph_json,viewport_json,updated_at) VALUES(?,?,?,?,?)",
                 ("General map", "general", json.dumps(document), "{}", STAMP))
    result = web_atlas.SECTIONS["study"](conn)
    edges = assert_section_graph(result)
    assert result["shown"] == result["total"] == 6
    assert edges == {(f"subject:{subject}", f"project:{project}"),
                     (f"subject:{subject}", f"concept:{map_id}:topic"),
                     (f"subject:{subject}", f"concept:{map_id}:idea")}
    assert not any(source == f"subject:{other}" or target == f"project:{independent}" for source, target in edges)
    labels = {node["label"] for node in result["nodes"]}
    assert {"Synthetic algebra", "Vector spaces", "Basis"} <= labels
    assert {"Stale subject label", "Not connected", "Deleted subject concept"}.isdisjoint(labels)
    assert "PRIVATE_" not in json.dumps(result)


def test_study_section_bounds_use_actual_counts_and_never_dangling_parents(workspace):
    conn = workspace.conn
    count = web_atlas.SECTION_GROUP_LIMIT + 9
    for index in range(count):
        ident = conn.execute("INSERT INTO study_subjects(name) VALUES(?)", (f"Subject {index:03d}",)).lastrowid
        conn.execute("INSERT INTO study_projects(name,subject_id) VALUES(?,?)", (f"Project {index:03d}", ident))
    result = web_atlas.SECTIONS["study"](conn)
    edges = assert_section_graph(result)
    assert result["total"] == count * 2
    assert result["shown"] == web_atlas.SECTION_GROUP_LIMIT * 2
    assert len(edges) == web_atlas.SECTION_GROUP_LIMIT


@pytest.mark.parametrize("section", ["jobs", "notes", "study"])
def test_section_missing_tables_and_empty_database_are_honest(tmp_path, section):
    conn = connect(tmp_path / f"empty-{section}.db")
    try:
        assert web_atlas.SECTIONS[section](conn) == {"nodes": [], "links": [], "total": 0, "shown": 0}
        if section == "study":
            conn.execute("CREATE TABLE study_subjects(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.execute("INSERT INTO study_subjects(name) VALUES('Existing subject')")
            result = web_atlas.SECTIONS[section](conn)
            assert_section_graph(result)
            assert result["shown"] == result["total"] == 1
            assert result["nodes"][0]["entity"] == "subject"
    finally:
        conn.close()


def test_notes_section_before_folder_migration_keeps_root_notebooks(tmp_path):
    conn = connect(tmp_path / "pre-folders.db")
    try:
        conn.execute("CREATE TABLE note_notebooks(id INTEGER PRIMARY KEY, title TEXT, updated_at TEXT)")
        conn.execute("CREATE TABLE note_pages(id INTEGER PRIMARY KEY, notebook_id INTEGER, title TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO note_notebooks VALUES(1,'Existing notebook',?)", (STAMP,))
        conn.execute("INSERT INTO note_pages VALUES(2,1,'Existing page',?)", (STAMP,))
        result = web_atlas.SECTIONS["notes"](conn)
        assert assert_section_graph(result) == {("notebook:1", "page:2")}
        assert result["shown"] == result["total"] == 2
    finally:
        conn.close()
