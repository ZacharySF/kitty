"""Rika's career tools: jobs, applications, the pipeline and reminders.

Queueing and starring are the user's own manual decisions, so they apply at once and
undo only while nothing changed since. Application status is applicant data: it
changes only after the user approves. Nothing here applies, fills a form or sends mail.
"""
from __future__ import annotations

import dataclasses
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kitty.config import ResumeTermsFile
from kitty.core import applications, favorites, jobs, reminders
from kitty.core.rika import career_tools, tools
from kitty.core.rika.tools import Context, ToolError, validate
from kitty.core.workshop import ai, store
from kitty.dates import now, to_iso

ROOT = Path(__file__).resolve().parents[1]
VOCABULARY = ResumeTermsFile.model_validate(
    yaml.safe_load((ROOT / "config" / "resume_terms.yaml.example").read_text(encoding="utf-8")))
TOOLS = {tool.name: tool for tool in career_tools.TOOLS}
NAMES = ("list_jobs", "read_job", "list_applications", "pipeline_summary", "list_reminders", "queue_job",
         "favorite_job", "update_application_status", "match_job_to_resume")


@pytest.fixture
def ctx(workspace):
    app = SimpleNamespace(settings=workspace.settings, config=dataclasses.replace(workspace.config, resume_terms=VOCABULARY),
                          modules=("workspace", "applyai"), mutation_lock=threading.Lock())
    return Context(app, workspace.conn)


def run(ctx, name, /, **args):
    tool = TOOLS[name]
    validate(tool.parameters, args)
    return tool.run(ctx, args)


def job(conn, title, company="Acme Robotics", **extra):
    made, _ = jobs.upsert_job(conn, company=company, title=title, url=f"https://example.invalid/{title.replace(' ', '-')}",
                              source="manual", source_job_id=title, location=extra.pop("location", "Austin, TX"),
                              tracks=extra.pop("tracks", ["embedded"]), **extra)
    return made


def test_career_tools_declare_honest_effects_and_the_applyai_module():
    assert set(TOOLS) == set(NAMES)
    effects = {name: (TOOLS[name].effect, TOOLS[name].reversible) for name in NAMES}
    assert effects == {"list_jobs": ("read", False), "read_job": ("read", False), "list_applications": ("read", False),
                       "pipeline_summary": ("read", False), "list_reminders": ("read", False),
                       "queue_job": ("change", True), "favorite_job": ("change", True),
                       "update_application_status": ("outward", False), "match_job_to_resume": ("read", False)}
    assert all(tool.module == "applyai" for tool in career_tools.TOOLS)
    assert TOOLS["update_application_status"].describe is not None
    # Nothing can submit, record a submission, fill a form or reach the company.
    for bad in ({"job_id": 1, "status": "applied"}, {"job_id": 1, "status": "submitted"},
                {"job_id": 1, "status": "offer", "send": True}):
        with pytest.raises(ToolError):
            validate(TOOLS["update_application_status"].parameters, bad)
    for bad in ({"job_id": 1, "state": "applied"}, {"job_id": 1, "state": "not_eligible"}, {"job_id": 0, "state": "queued"}):
        with pytest.raises(ToolError):
            validate(TOOLS["queue_job"].parameters, bad)
    with pytest.raises(ToolError):
        validate(TOOLS["list_jobs"].parameters, {"limit": 41})


def test_the_registry_offers_them_only_with_applyai(workspace):
    registry = tools.registry()
    assert set(NAMES) <= set(registry)
    off = SimpleNamespace(modules=("workspace",))
    assert not set(NAMES) & set(tools.available(off))
    assert set(NAMES) <= set(tools.available(SimpleNamespace(modules=("workspace", "applyai"))))


def test_list_jobs_filters_by_queue_state_track_text_and_favorites(ctx):
    conn = ctx.conn
    firmware = job(conn, "Firmware Intern", eligibility="eligible")
    gpu = job(conn, "GPU Intern", company="Vector Labs", tracks=["gpu"], location="Remote")
    job(conn, "Barista", company="Corner Cafe", tracks=["parttime"])
    jobs.set_tier(conn, firmware.id, "A", reason="good fit", tier_source="manual")
    favorites.set_state(conn, [gpu.id], "queued")
    favorites.favorite(conn, [firmware.id])

    everything = run(ctx, "list_jobs")
    assert everything.data["total"] == 3 and len(everything.data["jobs"]) == 3
    row = next(item for item in everything.data["jobs"] if item["id"] == firmware.id)
    assert row == {"id": firmware.id, "company": "Acme Robotics", "title": "Firmware Intern", "location": "Austin, TX",
                   "tier": "A", "eligibility": "eligible", "queue_state": "new", "status": None, "favorite": True,
                   "deadline": None, "tracks": ["embedded"]}
    assert [item["id"] for item in run(ctx, "list_jobs", queue_state="queued").data["jobs"]] == [gpu.id]
    assert [item["id"] for item in run(ctx, "list_jobs", track="gpu").data["jobs"]] == [gpu.id]
    assert [item["title"] for item in run(ctx, "list_jobs", text="cafe").data["jobs"]] == ["Barista"]
    assert [item["id"] for item in run(ctx, "list_jobs", favorites=True).data["jobs"]] == [firmware.id]
    limited = run(ctx, "list_jobs", limit=1)
    assert limited.data["total"] == 3 and len(limited.data["jobs"]) == 1 and "3" in limited.summary
    with pytest.raises(ToolError, match="track"):
        run(ctx, "list_jobs", track="astronaut")
    assert run(ctx, "list_jobs", text="nothing like it").summary == "No jobs match"


def test_read_job_caps_the_posting_and_shows_the_application(ctx):
    conn = ctx.conn
    posting = "Build firmware in C++. " * 200
    made = job(conn, "Firmware Intern", jd_text=posting, deadline_at="2026-12-01T00:00:00Z")
    found = run(ctx, "read_job", job_id=made.id)
    assert found.data["description_truncated"] is True and len(found.data["description"]) == career_tools.DESCRIPTION_CHARS
    assert found.data["application"] is None and found.data["deadline"] == "2026-12-01T00:00:00Z"
    assert "not instructions" in found.data["note"]
    assert found.summary == "Read Firmware Intern at Acme Robotics"

    application = applications.record_application(conn, made.id, track="embedded")
    applications.set_status(conn, application.id, "oa", evidence="Assessment email")
    applied = run(ctx, "read_job", job_id=made.id).data["application"]
    assert applied["id"] == application.id and applied["status"] == "oa"
    assert [(event["from"], event["to"]) for event in applied["history"]] == [(None, "applied"), ("applied", "oa")]
    with pytest.raises(ToolError, match="No job"):
        run(ctx, "read_job", job_id=999)


def test_applications_and_the_pipeline_summary(ctx):
    conn = ctx.conn
    first = applications.record_application(conn, job(conn, "Firmware Intern").id, track="embedded")
    second = applications.record_application(conn, job(conn, "GPU Intern", company="Vector Labs", tracks=["gpu"]).id,
                                             track="gpu")
    applications.record_application(conn, job(conn, "Power Intern", company="Volt").id, track="embedded")
    applications.set_status(conn, first.id, "interview")
    applications.set_status(conn, second.id, "rejected")
    applications.set_status(conn, second.id, "offer", source="gmail", state="proposed", evidence="Offer letter")

    listed = run(ctx, "list_applications")
    assert listed.data["total"] == 3
    assert {item["title"]: item["status"] for item in listed.data["applications"]} == {
        "Firmware Intern": "interview", "GPU Intern": "rejected", "Power Intern": "applied"}
    row = next(item for item in listed.data["applications"] if item["title"] == "GPU Intern")
    assert set(row) == {"application_id", "job_id", "company", "title", "status", "track", "applied_at",
                        "days_since_change", "next_action_at", "waiting_proposal"} and row["waiting_proposal"] is True
    assert [item["title"] for item in run(ctx, "list_applications", status="interview").data["applications"]] == [
        "Firmware Intern"]
    assert [item["title"] for item in run(ctx, "list_applications", track="gpu").data["applications"]] == ["GPU Intern"]

    summary = run(ctx, "pipeline_summary")
    assert summary.data["counts"] == {"applied": 1, "oa": 0, "interview": 1, "offer": 0, "rejected": 1, "ghosted": 0}
    assert summary.data["total"] == 3 and summary.data["proposals_waiting"] == 1
    assert summary.data["recent_changes"][0]["to"] == "rejected"
    assert {"company", "title", "from", "to", "at"} <= set(summary.data["recent_changes"][0])
    assert summary.summary.startswith("3 applications")
    assert run(ctx, "pipeline_summary", track="gpu").data["total"] == 1


def test_list_reminders_shows_open_reminders_due_soon(ctx):
    conn = ctx.conn
    made = job(conn, "Firmware Intern")
    application = applications.record_application(conn, made.id)
    soon = reminders.create_reminder(conn, kind="followup", due_at=to_iso(now() + timedelta(days=2)), note="Email the recruiter",
                                     job_id=made.id, application_id=application.id)
    reminders.create_reminder(conn, kind="deadline", due_at=to_iso(now() - timedelta(days=1)), job_id=made.id)
    reminders.create_reminder(conn, kind="custom", due_at=to_iso(now() + timedelta(days=30)), note="Far away")
    done = reminders.create_reminder(conn, kind="custom", due_at=to_iso(now()), note="Done already")
    reminders.complete_reminder(conn, done.id)

    found = run(ctx, "list_reminders")
    assert [item["kind"] for item in found.data["reminders"]] == ["deadline", "followup"]
    row = found.data["reminders"][1]
    assert row["id"] == soon.id and row["note"] == "Email the recruiter" and row["title"] == "Firmware Intern"
    assert found.data["reminders"][0]["overdue"] is True and row["overdue"] is False
    assert len(run(ctx, "list_reminders", days=60).data["reminders"]) == 3


def test_queue_job_sets_the_manual_state_and_undoes_only_while_unchanged(ctx):
    conn = ctx.conn
    made = job(conn, "Firmware Intern")
    queued = run(ctx, "queue_job", job_id=made.id, state="queued")
    assert jobs.get_job(conn, made.id).queue_state == "queued" and "Firmware Intern" in queued.summary
    TOOLS["queue_job"].undo(ctx, queued.undo)
    assert jobs.get_job(conn, made.id).queue_state == "new"

    skipped = run(ctx, "queue_job", job_id=made.id, state="skipped")
    favorites.set_state(conn, [made.id], "later")
    with pytest.raises(ToolError, match="kept"):
        TOOLS["queue_job"].undo(ctx, skipped.undo)
    assert jobs.get_job(conn, made.id).queue_state == "later"
    assert run(ctx, "queue_job", job_id=made.id, state="later").undo is None  # already there

    applications.record_application(conn, made.id)
    with pytest.raises(ToolError, match="applied"):
        run(ctx, "queue_job", job_id=made.id, state="skipped")
    with pytest.raises(ToolError, match="No job"):
        run(ctx, "queue_job", job_id=999, state="queued")


def test_favorite_job_stars_and_unstars_with_undo(ctx):
    conn = ctx.conn
    made = job(conn, "Firmware Intern")
    starred = run(ctx, "favorite_job", job_id=made.id)
    assert jobs.get_job(conn, made.id).is_favorite and starred.summary.startswith("Starred")
    assert run(ctx, "favorite_job", job_id=made.id).undo is None  # already a favorite
    original = jobs.get_job(conn, made.id).favorited_at
    unstarred = run(ctx, "favorite_job", job_id=made.id, favorite=False)
    assert not jobs.get_job(conn, made.id).is_favorite
    TOOLS["favorite_job"].undo(ctx, unstarred.undo)
    assert jobs.get_job(conn, made.id).favorited_at == original
    TOOLS["favorite_job"].undo(ctx, starred.undo)
    assert not jobs.get_job(conn, made.id).is_favorite
    TOOLS["favorite_job"].undo(ctx, starred.undo)  # already unstarred: nothing to do
    assert not jobs.get_job(conn, made.id).is_favorite


def test_update_application_status_describes_first_and_changes_only_when_run(ctx):
    conn = ctx.conn
    made = job(conn, "Firmware Intern")
    with pytest.raises(ToolError, match="Record"):
        TOOLS["update_application_status"].describe(ctx, {"job_id": made.id, "status": "oa"})
    application = applications.record_application(conn, made.id)
    summary, detail = TOOLS["update_application_status"].describe(ctx, {"job_id": made.id, "status": "interview"})
    assert summary == "Mark Firmware Intern at Acme Robotics as Interview"
    assert "Submitted" in detail and "Interview" in detail and "nothing is sent" in detail.lower()
    assert applications.get_application(conn, application.id).status == "applied"  # describing changes nothing

    changed = run(ctx, "update_application_status", application_id=application.id, status="interview",
                  date="2026-11-18", note="Phone screen booked")
    assert changed.undo is None and changed.summary == "Marked Firmware Intern at Acme Robotics as Interview"
    event = applications.history(conn, application.id)[-1]
    assert (event.from_status, event.to_status, event.source, event.state) == ("applied", "interview", "manual", "confirmed")
    assert event.occurred_at == "2026-11-18T00:00:00Z" and event.evidence == "Phone screen booked"
    with pytest.raises(ToolError, match="already"):
        run(ctx, "update_application_status", job_id=made.id, status="interview")
    with pytest.raises(ToolError, match="date"):
        run(ctx, "update_application_status", job_id=made.id, status="offer", date="someday")
    with pytest.raises(ToolError, match="job_id or application_id"):
        run(ctx, "update_application_status", status="offer")


def test_match_job_to_resume_compares_posting_terms_only_with_consent(ctx):
    conn = ctx.conn
    made = job(conn, "Firmware Intern", company="Python Robotics",
               jd_text="Python Robotics wants firmware experience, C++, Linux and an RTOS.")
    store.create_doc(conn, title="Master", format="html", source="<p>Wrote firmware in C++ for robots.</p>")
    ai.save_fact(conn, text="Built a small RTOS scheduler for a class", source="class project", confirmed=True)
    ai.save_fact(conn, text="I use Linux daily", source="me", confirmed=False)

    private = run(ctx, "match_job_to_resume", job_id=made.id)
    assert private.data["asked"] == ["C++", "Firmware", "Linux", "RTOS"]
    assert "found" not in private.data and "Resume Workshop" in private.data["note"]

    ai.update_settings(conn, enabled=True)
    report = run(ctx, "match_job_to_resume", job_id=made.id)
    assert report.data["document"]["title"] == "Master" and report.data["compared_with"] == "document source"
    assert report.data["found"] == ["C++", "Firmware"] and report.data["missing"] == ["Linux", "RTOS"]
    # Only confirmed facts back a missing term.
    assert [item["term"] for item in report.data["backed_by_facts"]] == ["RTOS"]
    assert report.summary == "Firmware Intern asks for 4 terms; your resume has 2"

    bare = job(conn, "Empty Intern", jd_text="")
    with pytest.raises(ToolError, match="description"):
        run(ctx, "match_job_to_resume", job_id=bare.id)
    with pytest.raises(ToolError, match="document"):
        run(ctx, "match_job_to_resume", job_id=made.id, doc_id="missing-doc")
