"""Jake's Resume template and the automatic ATS check's posting-term comparison."""
from pathlib import Path
from types import SimpleNamespace

import yaml

from kitty.config import ResumeTermsFile
from kitty.core import jobs
from kitty.core.workshop import ats, store, templates

ROOT = Path(__file__).resolve().parents[1]
VOCABULARY = ResumeTermsFile.model_validate(
    yaml.safe_load((ROOT / "config" / "resume_terms.yaml.example").read_text(encoding="utf-8")))


def test_jakes_template_keeps_its_layout_and_license_but_no_sample_person(workspace):
    from kitty import web_workshop
    app = SimpleNamespace(settings=workspace.settings)
    doc = web_workshop.post(app, workspace.conn, "/api/workshop",
                            {"title": "Mine", "template_id": "latex-jake"})["document"]
    assert doc["format"] == "latex" and doc["engine"] == "pdflatex"
    source = doc["source"]
    assert r"\newcommand{\resumeSubheading}" in source and r"\pdfgentounicode=1" in source
    assert "Copyright (c) 2020 Jake Gutierrez" in source and "Permission is hereby granted" in source
    for sample in ("Jake Ryan", "jake@su.edu", "Southwestern University", "Gitlytics"):
        assert sample not in source


def test_posting_terms_split_into_found_and_missing_without_the_company_name(workspace):
    c = workspace.conn
    job, _ = jobs.upsert_job(c, company="Python Robotics", title="Firmware Intern",
                             url="https://example.invalid/ats", source="manual", source_job_id="ats-one",
                             jd_text="Python Robotics wants firmware experience, C++ and Linux.")
    doc = store.create_doc(c, title="Linked", format="html", source="<p>x</p>", kind="master")
    report = ats.job_terms(c, VOCABULARY, {**doc, "job_id": job.id}, "Wrote firmware in C++ for robots.")
    assert report["job_id"] == job.id and report["company"] == "Python Robotics"
    assert report["found"] == ["C++", "Firmware"]
    # "Python" appears only in the company's own name, so the posting does not ask for it.
    assert report["missing"] == ["Linux"]


def test_unlinked_or_descriptionless_jobs_report_why(workspace):
    c = workspace.conn
    doc = store.create_doc(c, title="Plain", format="html", source="<p>x</p>")
    assert ats.job_terms(c, VOCABULARY, doc, "text") is None
    job, _ = jobs.upsert_job(c, company="Example", title="Empty", url="https://example.invalid/e",
                             source="manual", source_job_id="ats-empty", jd_text="")
    report = ats.job_terms(c, VOCABULARY, {**doc, "job_id": job.id}, "text")
    assert report["found"] == [] and "description" in report["error"].lower()


def test_template_list_offers_jakes_resume():
    assert any(item["id"] == "latex-jake" and "Jake" in item["label"] for item in templates.list_templates())


def test_ats_route_needs_a_compiled_revision(workspace):
    import pytest
    from kitty import web_workshop
    from kitty.web_server import APIError
    app = SimpleNamespace(settings=workspace.settings, config=workspace.config)
    doc = store.create_doc(workspace.conn, title="Plain", format="html", source="<p>x</p>")
    with pytest.raises(APIError) as caught:
        web_workshop.get(app, workspace.conn, f"/api/workshop/{doc['id']}/ats", {"revision": "1"})
    assert caught.value.status == 409 and "compile" in str(caught.value).lower()
