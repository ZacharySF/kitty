"""The automatic ATS check's posting comparison.

The Rust engine reports what a resume parser reads from the compiled PDF (contact
details, recognised sections, layout and character problems). This module adds the
linked ApplyAI job: which vocabulary terms (config/resume_terms.yaml, the resume coach's
list) the posting asks for, and which of those the resume's extracted text contains.
A missing term is a question for the applicant, never something to add without evidence.
"""
from __future__ import annotations

from kitty.core import jobs, resume_coach


def job_terms(conn, vocabulary, doc, resume_text):
    """Found and missing posting terms for a document linked to a job, else None."""
    if not doc.get("job_id"):
        return None
    job = jobs.get_job(conn, doc["job_id"])
    if job is None:
        return {"job_id": doc["job_id"], "title": "", "company": "", "found": [], "missing": [],
                "error": "The linked job no longer exists"}
    report = {"job_id": job.id, "title": job.title, "company": job.company_name, "found": [], "missing": []}
    if not (job.jd_text or "").strip():
        return {**report, "error": "This job has no saved description to compare against"}
    matchers = resume_coach.compile_vocabulary(vocabulary)
    if not matchers:
        return {**report, "error": "config/resume_terms.yaml has no terms; restore it from git"}
    asked = [matchers[index] for index in sorted(resume_coach._scan(job, matchers).hits)]
    text = resume_coach.normalize_text(resume_text)
    lowered = text.lower()
    present = {matcher.term for matcher in asked if matcher.find(text, lowered)}
    report["found"] = sorted(present, key=str.lower)
    report["missing"] = sorted({m.term for m in asked} - present, key=str.lower)
    return report
