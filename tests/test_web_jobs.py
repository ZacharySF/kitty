"""Selection and workflow invariants against an isolated real workspace."""
from __future__ import annotations

import pytest

from kitty.config import View, ViewFilters
from kitty.core import applications, jobs
from kitty.web_server import APIError, WebApplication


@pytest.fixture
def app(workspace, tmp_path):
    instance = WebApplication(workspace.settings, workspace.config, tmp_path)
    yield instance
    instance.automation.close()


def posting(workspace, number, *, company="Synthetic 100% Labs", title="Controls Intern"):
    job, _ = jobs.upsert_job(workspace.conn, company=company, title=f"{title} {number}",
                             url=f"https://example.invalid/job/{number}", source="manual",
                             source_job_id=f"selection-{number}")
    jobs.set_tier(workspace.conn, job.id, "A", reason="Synthetic fixture")
    return job


def test_all_matching_selection_and_bulk_favorites_span_pages_without_applying(app, workspace):
    expected = {posting(workspace, i).id for i in range(43)}
    decoy = posting(workspace, 100, company="Other Company", title="Synthetic 100% Labs role")
    query = {"view": "jobs", "company": "1", "search": "100%", "limit": "20", "offset": "20"}
    page = app.get_json(workspace.conn, "/api/jobs", query)
    selection = app.get_json(workspace.conn, "/api/jobs/selection", query)
    assert page["total"] == 43 and len(page["jobs"]) == 20
    assert set(selection["ids"]) == expected
    assert decoy.id not in selection["ids"]
    result = app.mutate(workspace.conn, "/api/jobs/bulk", {"ids": selection["ids"], "action": "favorite", "value": True})
    assert result["favorite"] is True
    assert {job.id for job in jobs.list_jobs(workspace.conn, jobs.JobFilter(favorites_only=True))} == expected
    assert all(jobs.get_job(workspace.conn, job_id).queue_state == "new" for job_id in expected)
    assert workspace.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    # Repeated right-click saves are idempotent, rather than toggling saved jobs off.
    app.mutate(workspace.conn, "/api/jobs/bulk", {"ids": selection["ids"], "action": "favorite", "value": True})
    assert jobs.count_jobs(workspace.conn, jobs.JobFilter(favorites_only=True)) == 43


def test_bulk_invalid_member_is_atomic_and_star_toggles_only_all_starred(app, workspace):
    first, second = posting(workspace, 1), posting(workspace, 2)
    with pytest.raises(APIError):
        app.mutate(workspace.conn, "/api/jobs/bulk", {"ids": [first.id, 999999], "action": "favorite", "value": True})
    assert not jobs.get_job(workspace.conn, first.id).is_favorite
    app.mutate(workspace.conn, "/api/jobs/bulk", {"ids": [first.id], "action": "favorite", "value": True})
    group = {"ids": [first.id, second.id], "action": "favorite", "value": "toggle"}
    assert app.mutate(workspace.conn, "/api/jobs/bulk", group)["favorite"] is True
    assert all(jobs.get_job(workspace.conn, i).is_favorite for i in (first.id, second.id))
    assert app.mutate(workspace.conn, "/api/jobs/bulk", group)["favorite"] is False
    assert not any(jobs.get_job(workspace.conn, i).is_favorite for i in (first.id, second.id))


def test_bulk_track_is_additive_and_saved_views_preserve_scope(app, workspace):
    first, second = posting(workspace, 1), posting(workspace, 2)
    track_a, track_b = workspace.config.track_names[:2]
    jobs.link_track(workspace.conn, first.id, track_a)
    app.mutate(workspace.conn, "/api/jobs/bulk", {"ids": [first.id], "action": "add_track", "value": track_b})
    assert set(jobs.get_job(workspace.conn, first.id).tracks) == {track_a, track_b}
    app.mutate(workspace.conn, "/api/jobs/bulk", {"ids": [first.id], "action": "favorite", "value": True})
    workspace.config.views.views = [View(key=9, title="Synthetic broad view", filters=ViewFilters(not_applied=False))]
    saved = app.get_json(workspace.conn, "/api/jobs/selection", {"view": "favorites", "saved": "9"})
    assert saved["ids"] == [first.id]
    app.mutate(workspace.conn, f"/api/jobs/{second.id}", {"action": "applied", "value": True})
    result = app.get_json(workspace.conn, "/api/jobs/selection", {"view": "applications", "saved": "9"})
    assert result["ids"] == [second.id]


def test_application_history_retains_dates_evidence_and_append_only_undo(app, workspace):
    job = posting(workspace, 1)
    endpoint = f"/api/jobs/{job.id}"
    app.mutate(workspace.conn, endpoint, {"action": "applied", "value": True, "when": "2025-01-02", "note": "Synthetic confirmation"})
    app.mutate(workspace.conn, endpoint, {"action": "status", "value": "interview", "when": "2025-01-03", "note": "Synthetic recruiter reply"})
    before = app.get_json(workspace.conn, endpoint, {})["application"]
    assert before["applied_at"].startswith("2025-01-02")
    assert any(event["evidence"] == "Synthetic recruiter reply" and event["occurred_at"].startswith("2025-01-03") for event in before["history"])
    app.mutate(workspace.conn, f"/api/job-tools/{job.id}", {"action": "undo"})
    after = app.get_json(workspace.conn, endpoint, {})["application"]
    assert after["status"] == "applied"
    assert {event["id"] for event in before["history"]} < {event["id"] for event in after["history"]}
    assert any(event["undoes_event_id"] for event in after["history"])


def test_pipeline_track_moves_leave_status_history_alone(app, workspace):
    job = posting(workspace, 1)
    endpoint = f"/api/jobs/{job.id}"
    first, second = workspace.config.track_names[:2]
    with pytest.raises(APIError) as refused:
        app.mutate(workspace.conn, endpoint, {"action": "track", "value": first})
    assert refused.value.status == 409
    app.mutate(workspace.conn, endpoint, {"action": "applied", "value": True})
    app.mutate(workspace.conn, endpoint, {"action": "track", "value": first})
    history = app.get_json(workspace.conn, endpoint, {})["application"]["history"]
    app.mutate(workspace.conn, endpoint, {"action": "track", "value": second})
    lanes = {card["job_id"]: card["track"] for cards in app.get_json(workspace.conn, "/api/pipeline", {})["columns"].values() for card in cards}
    assert lanes[job.id] == second
    assert app.get_json(workspace.conn, endpoint, {})["application"]["history"] == history
    for value in ("not-a-track", 7, ["synthetic"]):
        with pytest.raises(APIError):
            app.mutate(workspace.conn, endpoint, {"action": "track", "value": value})
    app.mutate(workspace.conn, endpoint, {"action": "track", "value": None})
    assert applications.get_application_for_job(workspace.conn, job.id).track is None


def test_applied_job_bulk_state_failure_preserves_every_selected_job(app, workspace):
    first, second = posting(workspace, 1), posting(workspace, 2)
    app.mutate(workspace.conn, f"/api/jobs/{second.id}", {"action": "applied", "value": True})
    with pytest.raises(APIError):
        app.mutate(workspace.conn, "/api/jobs/bulk", {"ids": [first.id, second.id], "action": "state", "value": "skipped"})
    assert jobs.get_job(workspace.conn, first.id).queue_state == "new"
    assert applications.get_application_for_job(workspace.conn, second.id).status == "applied"

def test_job_delete_keeps_reusable_questions_and_unrelated_postings(app, workspace):
    from kitty.core import questions, reminders
    first, other = posting(workspace, 1), posting(workspace, 2)
    question = questions.log_question(workspace.conn, 'Synthetic availability question?', job_id=first.id)
    reminder = reminders.create_reminder(workspace.conn, kind='custom', due_at='2099-01-01', note='Synthetic job reminder', job_id=first.id)
    app.mutate(workspace.conn, '/api/jobs/bulk', {'ids': [first.id], 'action': 'delete', 'value': True})
    assert jobs.get_job(workspace.conn, first.id) is None
    assert jobs.get_job(workspace.conn, other.id).title == other.title
    assert workspace.conn.execute('SELECT text,first_seen_job_id FROM screening_questions WHERE id=?', (question.id,)).fetchone()[:] == (question.text, None)
    assert workspace.conn.execute('SELECT id FROM reminders WHERE id=?', (reminder.id,)).fetchone() is None


def test_job_delete_requires_confirmation_and_protects_submissions_atomically(app, workspace):
    first, submitted = posting(workspace, 1), posting(workspace, 2)
    with pytest.raises(APIError):
        app.mutate(workspace.conn, '/api/jobs/bulk', {'ids': [first.id], 'action': 'delete', 'value': False})
    assert jobs.get_job(workspace.conn, first.id).title == first.title
    app.mutate(workspace.conn, f'/api/jobs/{submitted.id}', {'action': 'applied', 'value': True})
    with pytest.raises(APIError):
        app.mutate(workspace.conn, '/api/jobs/bulk', {'ids': [first.id, submitted.id], 'action': 'delete', 'value': True})
    assert jobs.get_job(workspace.conn, first.id).title == first.title
    assert applications.get_application_for_job(workspace.conn, submitted.id).status == 'applied'



def test_apply_list_excludes_low_tier_and_closed_jobs(app, workspace):
    first, low, closed = (posting(workspace, i) for i in range(3))
    jobs.set_tier(workspace.conn, low.id, "C", reason="Synthetic low fit")
    workspace.conn.execute("UPDATE jobs SET is_active=0 WHERE id=?", (closed.id,))
    result = app.get_json(workspace.conn, "/api/jobs/selection", {"view": "apply", "closed": "1"})
    assert result["ids"] == [first.id]


def test_score_sort_is_numeric_null_last_and_paginates_without_changing_selection(app, workspace):
    high, zero, negative, unknown = [posting(workspace, i) for i in range(4)]
    for job, score in ((high, 0.8), (zero, 0), (negative, -0.4)):
        workspace.conn.execute("UPDATE jobs SET like_score=? WHERE id=?", (score, job.id))
    expected = [high.id, zero.id, negative.id, unknown.id]
    query = {"view": "jobs", "order": "score", "limit": "2"}
    first = app.get_json(workspace.conn, "/api/jobs", query)
    second = app.get_json(workspace.conn, "/api/jobs", {**query, "offset": "2"})
    assert first["total"] == second["total"] == 4
    assert [job["id"] for job in first["jobs"] + second["jobs"]] == expected
    selected = app.get_json(workspace.conn, "/api/jobs/selection", {**query, "offset": "2"})
    assert selected == {"ids": expected, "total": 4}
    assert workspace.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_track_sort_groups_multi_track_jobs_and_keeps_filter_and_count(app, workspace):
    track_a, track_b = sorted(workspace.config.track_names[:2])
    first, second, unassigned = [posting(workspace, i) for i in range(3)]
    jobs.link_track(workspace.conn, first.id, track_b)
    jobs.link_track(workspace.conn, first.id, track_a)
    jobs.link_track(workspace.conn, second.id, track_b)
    query = {"view": "jobs", "order": "track"}
    result = app.get_json(workspace.conn, "/api/jobs", query)
    assert [job["id"] for job in result["jobs"]] == [first.id, second.id, unassigned.id]
    assert result["total"] == 3
    filtered = app.get_json(workspace.conn, "/api/jobs", {**query, "track": track_a})
    assert [job["id"] for job in filtered["jobs"]] == [first.id]
    assert filtered["total"] == 1


@pytest.mark.parametrize("order", ["score", "company"])
def test_explicit_queue_sort_is_not_replaced_by_preparation_priority(app, workspace, order):
    high = posting(workspace, 1, company="Alpha Synthetic")
    low = posting(workspace, 2, company="Zulu Synthetic")
    jobs.set_tier(workspace.conn, high.id, "B", reason="Synthetic fixture")
    workspace.conn.execute("UPDATE jobs SET fill_state='ready', like_score=0.9 WHERE id=?", (high.id,))
    workspace.conn.execute("UPDATE jobs SET like_score=0.1 WHERE id=?", (low.id,))
    explicit = app.get_json(workspace.conn, "/api/jobs", {"view": "apply", "order": order})
    assert [job["id"] for job in explicit["jobs"]] == [high.id, low.id]
    best = app.get_json(workspace.conn, "/api/jobs", {"view": "apply", "order": "best"})
    assert [job["id"] for job in best["jobs"]] == [low.id, high.id]
