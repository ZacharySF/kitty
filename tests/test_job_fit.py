"""The Find table's fit cell and the job detail's Fit tab: posting skills against the resume.

A posting's skills are the config/resume_terms.yaml terms it asks for (the ATS check's
list). Each is covered by the resume Rika would compare (the job's tailored Workshop copy,
else the newest master) or by a fact the user confirmed, or by neither. The local page
shows it without the hosted-editing switch: nothing leaves the machine.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kitty import web_jobs
from kitty.config import ResumeTermsFile
from kitty.core import jobs, pay
from kitty.core.workshop import ai, ats, store

ROOT = Path(__file__).resolve().parents[1]
VOCABULARY = ResumeTermsFile.model_validate(
    yaml.safe_load((ROOT / "config" / "resume_terms.yaml.example").read_text(encoding="utf-8")))


def job(conn, title, company="Example Robotics", **extra):
    made, _ = jobs.upsert_job(conn, company=company, title=title, url=f"https://example.invalid/{title.replace(' ', '-')}",
                              source="manual", source_job_id=title, **extra)
    return made


def app_for(workspace):
    return SimpleNamespace(settings=workspace.settings,
                           config=dataclasses.replace(workspace.config, resume_terms=VOCABULARY),
                           _no_query=lambda query: None)


def test_fit_ticks_posting_skills_covered_by_the_resume_or_a_confirmed_fact(workspace):
    conn = workspace.conn
    made = job(conn, "Firmware Intern", company="Python Robotics",
               jd_text="Python Robotics wants firmware experience, C++, Linux and an RTOS.")
    store.create_doc(conn, title="Master", format="html", source="<p>Wrote firmware in C++ for robots.</p>")
    ai.save_fact(conn, text="Built a small RTOS scheduler for a class", source="class project", confirmed=True)
    ai.save_fact(conn, text="I use Linux daily", source="me", confirmed=False)
    # The hosted-editing switch stays off: the local page needs no consent to show your own data.
    assert ai.get_settings(conn)["enabled"] is False

    [report] = ats.fit(conn, workspace.settings, VOCABULARY, [made.id])
    assert report["job_id"] == made.id
    assert report["document"]["title"] == "Master" and report["compared_with"] == "document source"
    assert [(skill["term"], skill["covered"]) for skill in report["skills"]] == [
        ("C++", "resume"), ("Firmware", "resume"), ("RTOS", "fact"), ("Linux", None)]
    assert report["skills"][2]["fact_ids"] and report["covered"] == 3 and report["total"] == 4


def test_fit_prefers_the_copy_tailored_to_the_job_and_reports_postings_without_text(workspace):
    conn = workspace.conn
    first = job(conn, "Embedded Intern", jd_text="Embedded C and Linux experience.")
    second = job(conn, "Data Intern", jd_text="SQL and Python for dashboards.")
    bare = job(conn, "Mystery Intern", jd_text="")
    store.create_doc(conn, title="Master", format="html", source="<p>Python and SQL.</p>")
    store.create_doc(conn, title="For embedded", format="html", source="<p>Linux drivers.</p>", kind="tailored",
                     job_id=first.id)

    reports = {item["job_id"]: item for item in ats.fit(conn, workspace.settings, VOCABULARY, [first.id, second.id, bare.id, 99999])}
    assert set(reports) == {first.id, second.id, bare.id}, "a missing job is left out"
    assert reports[first.id]["document"]["title"] == "For embedded"
    assert {skill["term"]: skill["covered"] for skill in reports[first.id]["skills"]}["Linux"] == "resume"
    assert reports[second.id]["document"]["title"] == "Master"
    assert reports[second.id]["covered"] == reports[second.id]["total"] == 2
    assert reports[bare.id]["total"] == 0 and "description" in reports[bare.id]["error"].lower()


def test_fit_without_any_workshop_resume_counts_only_confirmed_facts(workspace):
    conn = workspace.conn
    made = job(conn, "Data Intern", jd_text="SQL and Python for dashboards.")
    ai.save_fact(conn, text="Wrote SQL reports for a club", source="me", confirmed=True)
    [report] = ats.fit(conn, workspace.settings, VOCABULARY, [made.id])
    assert report["document"] is None and report["compared_with"] is None
    assert {skill["term"]: skill["covered"] for skill in report["skills"]} == {"Python": None, "SQL": "fact"}


def test_listed_pay_reads_the_source_payload_then_the_posting_text(workspace):
    conn = workspace.conn
    listed = job(conn, "Repo Intern", raw_json=json.dumps({"salary": "$45/hr"}))
    cafe = job(conn, "Cafe Intern", raw_json=json.dumps({"comp": {"hourly_min": 20, "hourly_max": 24.5}}))
    hourly = job(conn, "Store Associate", jd_text="Pay: $15.25 - $17.31 per hour, weekends.")
    yearly = job(conn, "Analyst", jd_text="The salary range is $70,000 - $85,000 per year plus benefits.")
    unknown = job(conn, "Quiet Intern", jd_text="Great team.")
    found = pay.listed(conn, [listed.id, cafe.id, hourly.id, yearly.id, unknown.id])
    assert found[listed.id] == {"text": "$45/hr", "hourly": 45.0}
    assert found[cafe.id] == {"text": "$20–$24.50 / hr", "hourly": 24.5}
    assert found[hourly.id] == {"text": "$15.25 - $17.31 per hour", "hourly": 17.31}
    assert found[yearly.id]["text"] == "$70,000 - $85,000 per year"
    assert found[yearly.id]["hourly"] == pytest.approx(85000 / 2080)
    assert found[unknown.id] is None


def test_fit_route_answers_a_page_of_ids_in_one_request(workspace):
    from kitty.web_server import APIError
    conn = workspace.conn
    made = job(conn, "Data Intern", jd_text="SQL and Python. $22/hr.")
    other = job(conn, "Other Intern", jd_text="Python.")
    store.create_doc(conn, title="Master", format="html", source="<p>Python</p>")
    app = app_for(workspace)
    result = web_jobs.get(app, conn, "/api/jobs/fit", {"ids": f"{made.id},{other.id}"})
    assert [item["job_id"] for item in result["jobs"]] == [made.id, other.id]
    assert result["jobs"][0]["covered"] == 1 and result["jobs"][0]["total"] == 2
    assert result["jobs"][0]["pay"] == {"text": "$22/hr", "hourly": 22.0} and result["jobs"][1]["pay"] is None
    for bad in ({}, {"ids": ""}, {"ids": "1,x"}, {"ids": "0"}, {"ids": ",".join(str(n) for n in range(1, 102))},
                {"ids": "1", "extra": "1"}):
        with pytest.raises(APIError) as caught:
            web_jobs.get(app, conn, "/api/jobs/fit", bad)
        assert caught.value.status == 400, bad
