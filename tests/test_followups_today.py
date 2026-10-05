"""Stage 4 core: follow-ups on applied, hand 'not eligible', answers.yaml, the digest, the streak."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from applyai.config import AnswersFile, ApplyFile, ConfigError, load_config, parse_file
from applyai.core import applications as applications_core
from applyai.core import jobs as jobs_core
from applyai.core import reminders as reminders_core
from applyai.core import sources as sources_core
from applyai.core.answers_file import add_answer
from applyai.core.discovery.base import AdapterResult, NormalizedJob
from applyai.core.discovery.pipeline import run_discover
from applyai.core.followups import mark_applied
from applyai.core.stats import streak_days
from applyai.core.today import build_digest, digest_text
from applyai.dates import to_iso


def _job(conn, slug: str, **extra):
    job, _created = jobs_core.upsert_job(
        conn, company="Acme", title=f"Intern {slug}", url=f"https://acme.example/{slug}", **extra
    )
    return job


def _fixed_adapter(result: AdapterResult):
    """An adapter class that always returns `result` (the pattern from test_discovery_pipeline)."""
    class Fixed:
        def __init__(self, ctx):
            pass

        def fetch(self, since):
            return result
    return Fixed


def test_applied_creates_followups_at_the_configured_offsets_once(workspace):
    config = replace(workspace.config, apply=ApplyFile(followup_days=[10, 3]))
    job = _job(workspace.conn, "a")

    application = mark_applied(workspace.conn, config, job.id, applied_at="2026-09-01T12:00:00Z")
    due = [r.due_at for r in reminders_core.followups_for(workspace.conn, application.id)]
    assert due == ["2026-09-04T12:00:00Z", "2026-09-11T12:00:00Z"]

    # A second "applied" (stray keypress, re-run) adds nothing, even after I ticked one off.
    reminders_core.complete_reminder(workspace.conn, reminders_core.followups_for(workspace.conn, application.id)[0].id)
    again = mark_applied(workspace.conn, config, job.id)
    assert again.id == application.id
    assert len(reminders_core.followups_for(workspace.conn, application.id)) == 2


def test_a_hand_not_eligible_survives_rediscover_and_stays_tier_c(workspace):
    track = next(t for t in workspace.config.tracks.tracks
                 if t.sources and t.search_terms and not t.location_rules.allow_cities)
    source = track.sources[0]
    posting = NormalizedJob(
        source=source, source_job_id="1", company="Acme", title=track.search_terms[0].title(),
        url="https://acme.example/jobs/1", location="Austin, TX",
        description="Open to freshmen and sophomores.",
    )
    adapters = {source: _fixed_adapter(AdapterResult([posting], "ok"))}

    def discover():
        run_discover(workspace.settings, workspace.config, adapters=adapters, only=[source],
                     http_factory=lambda name: None)

    discover()
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert job.eligibility != "ineligible"            # the rules would put it back in the queue

    jobs_core.mark_not_eligible(workspace.conn, job.id)
    discover()
    job = jobs_core.get_job(workspace.conn, job.id)
    assert (job.eligibility, job.eligibility_reason) == ("ineligible", jobs_core.HAND_NOT_ELIGIBLE)
    assert job.queue_state == "not_eligible"
    assert (job.tier, job.tier_reason) == ("C", jobs_core.HAND_NOT_ELIGIBLE)


def test_add_answer_appends_below_my_comments_and_loads(workspace):
    config_dir = workspace.settings.config_dir
    path = config_dir / "answers.yaml"

    add_answer(config_dir, "Are you willing to relocate?", "Yes", category="logistics")
    add_answer(config_dir, "Preferred office?", 'Houston: "on-site" # not a comment')

    config = load_config(config_dir)
    assert config.answer_for("Are you willing to relocate?").answer == "Yes"   # text, not a bool
    assert config.answer_for("Preferred office?").answer == 'Houston: "on-site" # not a comment'

    with pytest.raises(ValueError):                                  # an exact repeat would never be used
        add_answer(config_dir, "are you willing to relocate", "No")




def test_add_answer_creates_a_missing_file(tmp_path):
    add_answer(tmp_path, "Expected graduation date", "May 2032")
    saved = parse_file(tmp_path / "answers.yaml", AnswersFile).answers
    assert [(a.question, a.answer) for a in saved] == [("Expected graduation date", "May 2032")]


@pytest.mark.parametrize("empty_list", ["[]", "[ ]"])
def test_first_saved_answer_converts_empty_flow_list_without_losing_comments(tmp_path, empty_list):
    path = tmp_path / "answers.yaml"
    path.write_text(f"# My screening answers\nanswers: {empty_list} # keep this note\n")
    add_answer(tmp_path, "Preferred office?", 'Houston: "on-site" # literal')
    saved = parse_file(path, AnswersFile).answers
    assert [(answer.question, answer.answer) for answer in saved] == [
        ("Preferred office?", 'Houston: "on-site" # literal')
    ]
    assert "# My screening answers" in path.read_text() and "# keep this note" in path.read_text()


def test_invalid_answer_file_is_preserved_when_append_is_rejected(tmp_path):
    path = tmp_path / "answers.yaml"
    original = "# My file\nanswers: not-a-list\n"
    path.write_text(original)
    with pytest.raises(ConfigError):
        add_answer(tmp_path, "Preferred office?", "Houston")
    assert path.read_text() == original


def test_digest_shows_overdue_reminders_near_deadlines_and_failing_sources(workspace):
    conn, config = workspace.conn, workspace.config
    today = date(2026, 9, 30)
    window = config.apply.deadline_window_days
    soon = _job(conn, "soon", deadline_at=to_iso(today + timedelta(days=window)))
    _job(conn, "later", deadline_at=to_iso(today + timedelta(days=window + 1)))
    _job(conn, "past", deadline_at=to_iso(today - timedelta(days=1)))
    reminders_core.create_reminder(conn, kind="custom", due_at="2026-09-25T09:00:00Z",
                                   note="email the recruiter", job_id=soon.id)
    reminders_core.create_reminder(conn, kind="custom", due_at="2026-10-02T09:00:00Z", job_id=soon.id)
    failing = next(name for name, source in config.sources.sources.items() if source.enabled)
    sources_core.record_run(conn, failing, status="error", error="HTTP 500")

    digest = build_digest(conn, config, today=today)

    assert [(r.label, r.note) for r in digest.reminders] == [("Acme - Intern soon", "email the recruiter")]
    assert [job.id for job in digest.deadlines] == [soon.id]
    assert [s.name for s in digest.source_problems] == [failing]
    text = digest_text(digest)
    assert "Acme - Intern soon" in text and failing in text


def test_a_followup_stops_showing_once_the_application_got_a_response(workspace):
    config = replace(workspace.config, apply=ApplyFile(followup_days=[7]))
    application = mark_applied(workspace.conn, config, _job(workspace.conn, "a").id,
                               applied_at="2026-09-01T12:00:00Z")
    assert len(build_digest(workspace.conn, config, today=date(2026, 9, 30)).reminders) == 1
    applications_core.set_status(workspace.conn, application.id, "rejected")
    assert build_digest(workspace.conn, config, today=date(2026, 9, 30)).reminders == []


def test_streak_counts_consecutive_days_and_resets_on_a_gap(workspace):
    for day in ("2026-09-25", "2026-09-27", "2026-09-28", "2026-09-29"):
        job = _job(workspace.conn, day)
        applications_core.record_application(workspace.conn, job.id, applied_at=f"{day}T15:00:00Z")

    assert streak_days(workspace.conn, today=date(2026, 9, 29)) == 3
    assert streak_days(workspace.conn, today=date(2026, 9, 30)) == 3     # today is not over yet
    assert streak_days(workspace.conn, today=date(2026, 10, 1)) == 0     # a whole day missed
