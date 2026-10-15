"""Applications, the append-only history, undo, and proposals."""

from kitty.core import applications as applications_core
from kitty.core import jobs as jobs_core
from kitty.core import pipeline as pipeline_core


def _job(workspace, **kwargs):
    job, _ = jobs_core.upsert_job(
        workspace.conn,
        company=kwargs.pop("company", "Test Co"),
        title=kwargs.pop("title", "Embedded Intern"),
        url=kwargs.pop("url", "https://example.invalid/1"),
        location="Waco, TX",
        tracks=["embedded"],
        **kwargs,
    )
    return job


def test_recording_an_application_is_idempotent_and_marks_the_job(workspace):
    job = _job(workspace)
    first = applications_core.record_application(workspace.conn, job.id, track="embedded")
    second = applications_core.record_application(workspace.conn, job.id, track="embedded")
    assert first.id == second.id
    assert jobs_core.get_job(workspace.conn, job.id).queue_state == "applied"
    assert [e.to_status for e in applications_core.history(workspace.conn, first.id)] == ["applied"]


def test_status_changes_append_history_and_move_the_application(workspace):
    job = _job(workspace)
    application = applications_core.record_application(workspace.conn, job.id)
    applications_core.set_status(workspace.conn, application.id, "oa")
    applications_core.set_status(workspace.conn, application.id, "interview")

    events = applications_core.history(workspace.conn, application.id)
    assert [(e.from_status, e.to_status) for e in events] == [
        (None, "applied"), ("applied", "oa"), ("oa", "interview"),
    ]
    assert applications_core.get_application(workspace.conn, application.id).status == "interview"


def test_undo_appends_a_reverting_event_and_never_deletes(workspace):
    job = _job(workspace)
    application = applications_core.record_application(workspace.conn, job.id)
    applications_core.set_status(workspace.conn, application.id, "oa")

    undone = applications_core.undo_last_status(workspace.conn, application.id)
    assert undone is not None and undone.to_status == "applied"
    assert undone.undoes_event_id is not None
    assert len(applications_core.history(workspace.conn, application.id)) == 3
    assert applications_core.get_application(workspace.conn, application.id).status == "applied"


def test_undo_stops_at_the_initial_applied_event(workspace):
    job = _job(workspace)
    application = applications_core.record_application(workspace.conn, job.id)
    assert applications_core.undo_last_status(workspace.conn, application.id) is None


def test_undo_then_redo_leaves_the_status_where_i_put_it(workspace):
    job = _job(workspace)
    application = applications_core.record_application(workspace.conn, job.id)
    applications_core.set_status(workspace.conn, application.id, "oa")
    applications_core.undo_last_status(workspace.conn, application.id)
    applications_core.set_status(workspace.conn, application.id, "oa")
    assert applications_core.get_application(workspace.conn, application.id).status == "oa"
    assert len(applications_core.history(workspace.conn, application.id)) == 4


def test_a_proposed_event_changes_nothing_until_confirmed(workspace):
    job = _job(workspace)
    application = applications_core.record_application(workspace.conn, job.id)
    event = applications_core.set_status(
        workspace.conn, application.id, "oa", source="gmail", state="proposed",
        evidence="email: assessment ready",
    )
    assert applications_core.get_application(workspace.conn, application.id).status == "applied"
    assert [e.id for e in applications_core.proposed_events(workspace.conn)] == [event.id]

    applications_core.confirm_proposal(workspace.conn, event.id)
    assert applications_core.get_application(workspace.conn, application.id).status == "oa"
    assert applications_core.proposed_events(workspace.conn) == []


def test_rejecting_a_proposal_keeps_it_in_the_history(workspace):
    job = _job(workspace)
    application = applications_core.record_application(workspace.conn, job.id)
    event = applications_core.set_status(
        workspace.conn, application.id, "rejected", source="gmail", state="proposed"
    )
    applications_core.reject_proposal(workspace.conn, event.id)
    states = [e.state for e in applications_core.history(workspace.conn, application.id)]
    assert "rejected_by_me" in states
    assert applications_core.get_application(workspace.conn, application.id).status == "applied"


def test_board_and_funnel_reflect_current_status(workspace):
    for index, status in enumerate(("applied", "oa", "interview")):
        job = _job(workspace, company=f"Co {index}", url=f"https://example.invalid/{index}")
        application = applications_core.record_application(workspace.conn, job.id, track="embedded")
        if status != "applied":
            applications_core.set_status(workspace.conn, application.id, status)

    board = pipeline_core.board(workspace.conn)
    assert {status: len(cards) for status, cards in board.items()} == {
        "applied": 1, "oa": 1, "interview": 1, "offer": 0, "rejected": 0, "ghosted": 0,
    }
    funnel = pipeline_core.funnel(workspace.conn)
    assert funnel.total == 3 and funnel.advance_rate == 2 / 3
