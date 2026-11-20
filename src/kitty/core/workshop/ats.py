"""The automatic ATS check's posting comparison, and the job fit checklist built on it.

The Rust engine reports what a resume parser reads from the compiled PDF (contact
details, recognised sections, layout and character problems). This module adds the
linked ApplyAI job: which vocabulary terms (config/resume_terms.yaml, the resume coach's
list) the posting asks for, and which of those the resume's extracted text contains.
A missing term is a question for the applicant, never something to add without evidence.

:func:`fit` is the same comparison for any job (Find's fit column and the job detail's
Fit tab, and Rika's ``match_job_to_resume``): the resume compared is the Workshop copy
tailored to that job, else the newest master, and a term the resume lacks still counts
as covered when a fact the user confirmed mentions it.
"""
from __future__ import annotations

from kitty.core import jobs, resume_coach
from kitty.core.workshop import ai, render, store


def job_terms(conn, vocabulary, doc, resume_text):
    """Found and missing posting terms for a document linked to a job, else None."""
    if not doc.get("job_id"):
        return None
    job = jobs.get_job(conn, doc["job_id"])
    if job is None:
        return {"job_id": doc["job_id"], "title": "", "company": "", "found": [], "missing": [],
                "error": "The linked job no longer exists"}
    return _compare(job, resume_coach.compile_vocabulary(vocabulary), resume_text)


def _compare(job, matchers, resume_text):
    """:func:`job_terms` for a job that exists, with the vocabulary compiled once."""
    report = {"job_id": job.id, "title": job.title, "company": job.company_name, "found": [], "missing": []}
    if not (job.jd_text or "").strip():
        return {**report, "error": "This job has no saved description to compare against"}
    if not matchers:
        return {**report, "error": "config/resume_terms.yaml has no terms; restore it from git"}
    asked = [matchers[index] for index in sorted(resume_coach._scan(job, matchers).hits)]
    text = resume_coach.normalize_text(resume_text)
    lowered = text.lower()
    present = {matcher.term for matcher in asked if matcher.find(text, lowered)}
    report["found"] = sorted(present, key=str.lower)
    report["missing"] = sorted({m.term for m in asked} - present, key=str.lower)
    return report


def default_document(docs, job_id):
    """The newest unarchived Workshop document tailored to `job_id`, else the newest master.

    `docs` is :func:`store.list_docs` (newest first), read once for many jobs.
    """
    current = [doc for doc in docs if not doc["archived"]]
    return next((doc for doc in current if doc["job_id"] == job_id), None) or next(
        (doc for doc in current if doc["kind"] == "master"), None)


def resume_text(settings, doc):
    """(text, what it is): the compiled PDF's extracted text when this revision was compiled, else the source."""
    try:
        extracted = render.compiled_checks(settings, doc).get("extracted_text")
    except store.WorkshopError:
        extracted = None
    return (extracted, "compiled PDF text") if extracted else (doc["source"], "document source")


def fact_backing(matchers, facts, terms):
    """{term: [fact id, ...]} for each of `terms` a confirmed fact mentions."""
    by_term = {matcher.term: matcher for matcher in matchers}
    texts = [(fact["id"], normal := resume_coach.normalize_text(fact["text"]), normal.lower())
             for fact in facts if fact["confirmed"]]
    backed = {}
    for term in terms:
        ids = [ident for ident, text, lowered in texts if by_term[term].find(text, lowered)]
        if ids:
            backed[term] = ids
    return backed


def fit(conn, settings, vocabulary, job_ids):
    """Per existing job of `job_ids`, in that order: the posting's skills and what covers each.

    Each skill is ``{"term", "covered": "resume" | "fact" | None, "fact_ids"}``, the ones on
    the resume first, then those only a confirmed fact backs, then the rest.
    """
    matchers = resume_coach.compile_vocabulary(vocabulary)
    docs = store.list_docs(conn)
    facts = ai.list_facts(conn)
    texts: dict[str, tuple[dict, str, str]] = {}
    reports = []
    for job_id in job_ids:
        job = jobs.get_job(conn, job_id)
        if job is None:
            continue
        summary = default_document(docs, job_id)
        if summary is not None and summary["id"] not in texts:
            doc = store.get_doc(conn, summary["id"])
            texts[doc["id"]] = (doc, *resume_text(settings, doc))
        doc, text, compared = texts[summary["id"]] if summary is not None else (None, "", None)
        terms = _compare(job, matchers, text)
        backed = fact_backing(matchers, facts, terms["missing"])
        skills = ([{"term": term, "covered": "resume", "fact_ids": []} for term in terms["found"]]
                  + [{"term": term, "covered": "fact", "fact_ids": backed[term]} for term in terms["missing"] if term in backed]
                  + [{"term": term, "covered": None, "fact_ids": []} for term in terms["missing"] if term not in backed])
        report = {"job_id": job_id, "skills": skills, "total": len(skills),
                  "covered": sum(1 for skill in skills if skill["covered"]),
                  "document": {"id": doc["id"], "title": doc["title"], "kind": doc["kind"]} if doc else None,
                  "compared_with": compared}
        if terms.get("error"):
            report["error"] = terms["error"]
        reports.append(report)
    return reports
