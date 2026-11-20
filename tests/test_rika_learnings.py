"""Rika's career memory (kitty.core.rika.learnings): synthetic postings, facts and reviews only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kitty.config import ResumeTerm, ResumeTermGroup, ResumeTermsFile
from kitty.core.rika import learnings
from kitty.core.workshop import ai, store

VOCABULARY = ResumeTermsFile(groups=[ResumeTermGroup(name="software", terms=[
    ResumeTerm(term="Python", aliases=["python"]),
    ResumeTerm(term="Kubernetes", aliases=["kubernetes", "k8s"]),
    ResumeTerm(term="SQL", aliases=["sql"]),
    ResumeTerm(term="Rust", aliases=["rust"]),
])])


def _stamp(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _company(conn, name="Synthetic Labs"):
    norm = name.lower().replace(" ", "")
    conn.execute("INSERT OR IGNORE INTO companies(norm_name,display_name,created_at,updated_at) VALUES(?,?,'t','t')",
                 (norm, name))
    return conn.execute("SELECT id FROM companies WHERE norm_name=?", (norm,)).fetchone()[0]


def _job(conn, key, jd, *, state="new", favorite=False, applied=None, season=None, remote=None, title="Engineer",
         company="Synthetic Labs", days_ago=1):
    when = _stamp(days_ago)
    conn.execute(
        "INSERT INTO jobs(company_id,title,url,dedupe_key,jd_text,queue_state,favorited_at,season,is_remote,"
        "first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_company(conn, company), title, f"https://example.test/{key}", key, jd, state, when if favorite else None,
         season, remote, when, when, when, when))
    job_id = conn.execute("SELECT id FROM jobs WHERE dedupe_key=?", (key,)).fetchone()[0]
    if applied:
        conn.execute("INSERT INTO applications(job_id,status,applied_at,last_activity_at,created_at,updated_at)"
                     " VALUES(?,?,?,?,?,?)", (job_id, applied, when, when, when, when))
    return job_id


def _by_key(conn, kind):
    return {item["key"]: item for item in learnings.learnings(conn) if item["kind"] == kind}


@pytest.fixture
def jobs(workspace):
    c = workspace.conn
    return {
        "applied": _job(c, "a", "We use Python and SQL daily.", state="applied", applied="interview",
                        season="Summer 2027", remote=1),
        "queued": _job(c, "b", "Python services on Kubernetes.", state="queued", season="Summer 2027", remote=1),
        "favorite": _job(c, "c", "Python scripting.", favorite=True),
        "skipped": _job(c, "d", "Rust and SQL systems.", state="skipped"),
        "unseen": _job(c, "e", "Rust firmware.", title="Firmware intern"),
    }


def test_refresh_weights_skills_by_what_the_user_did(workspace, jobs):
    c = workspace.conn
    ai.save_fact(c, text="Built data pipelines in Python.", source="Records", confirmed=True)
    ai.save_fact(c, text="Ran SQL reports.", source="Records", confirmed=False)
    assert learnings.refresh(c, VOCABULARY) is True
    skills = _by_key(c, "posting_skill")
    assert list(skills)[0] == "Python"
    assert skills["Python"]["detail"]["postings"] == 3
    assert set(skills["Python"]["evidence"]) == {jobs["applied"], jobs["queued"], jobs["favorite"]}
    assert skills["Python"]["detail"]["fact"] is True
    assert skills["Kubernetes"]["detail"] == {**skills["Kubernetes"]["detail"], "postings": 1, "fact": False}
    # Applied (with an interview) outweighs one skipped posting; an unconfirmed fact is not a fact.
    assert skills["SQL"]["detail"]["against"] == 1 and skills["SQL"]["detail"]["fact"] is False
    assert skills["Python"]["weight"] > skills["SQL"]["weight"] > 0
    # Rust is only in a skipped posting and one never acted on.
    assert "Rust" not in skills
    patterns = _by_key(c, "posting_pattern")
    assert patterns["season:summer 2027"]["detail"]["postings"] == 2
    assert "remote" in patterns


def test_refresh_is_signature_gated_and_follows_job_state(workspace, jobs):
    c = workspace.conn
    assert learnings.refresh(c, VOCABULARY) is True
    assert learnings.refresh(c, VOCABULARY) is False
    c.execute("UPDATE jobs SET favorited_at=? WHERE id=?", (_stamp(), jobs["unseen"]))
    assert learnings.refresh(c, VOCABULARY) is True
    assert "Rust" in _by_key(c, "posting_skill")


def test_forgotten_learnings_never_come_back(workspace, jobs):
    c = workspace.conn
    learnings.refresh(c, VOCABULARY)
    python = _by_key(c, "posting_skill")["Python"]
    assert learnings.forget(c, python["id"]) == python["id"]
    assert "Python" not in _by_key(c, "posting_skill")
    _job(c, "f", "More Python.", state="applied", applied="applied")
    assert learnings.refresh(c, VOCABULARY) is True
    assert "Python" not in _by_key(c, "posting_skill")
    assert "Python" not in learnings.summary_for_prompt(c, vocabulary=VOCABULARY)
    with pytest.raises(LookupError):
        learnings.forget(c, python["id"])
    with pytest.raises(LookupError):
        learnings.pin(c, python["id"], True)


def test_pins_survive_their_evidence_and_lead_the_summary(workspace, jobs):
    c = workspace.conn
    learnings.refresh(c, VOCABULARY)
    kubernetes = _by_key(c, "posting_skill")["Kubernetes"]
    assert learnings.pin(c, kubernetes["id"], True)["pinned"] is True
    with pytest.raises(ValueError):
        learnings.pin(c, kubernetes["id"], "yes")
    assert learnings.learnings(c)[0]["key"] == "Kubernetes"
    c.execute("UPDATE jobs SET queue_state='new' WHERE id=?", (jobs["queued"],))
    learnings.refresh(c, VOCABULARY)
    assert "Kubernetes" in _by_key(c, "posting_skill")
    summary = learnings.summary_for_prompt(c, vocabulary=VOCABULARY)
    assert summary.index("Kubernetes") < summary.index("Python")
    learnings.pin(c, kubernetes["id"], False)
    learnings.refresh(c, VOCABULARY)
    assert "Kubernetes" in _by_key(c, "posting_skill")  # unchanged inputs: no refresh ran
    c.execute("UPDATE jobs SET updated_at=? WHERE id=?", (_stamp(), jobs["applied"]))
    learnings.refresh(c, VOCABULARY)
    assert "Kubernetes" not in _by_key(c, "posting_skill")


def test_notes_are_validated_capped_and_forgetting_one_deletes_it(workspace):
    c = workspace.conn
    note = learnings.add_note(c, "  Prefer   roles near Austin ")
    assert note["kind"] == "note" and note["text"] == "Prefer roles near Austin"
    for bad in ("", "   ", "x" * (learnings.NOTE_MAX + 1), "a\x00b", 7, None):
        with pytest.raises(ValueError):
            learnings.add_note(c, bad)
    with pytest.raises(ValueError):
        learnings.add_note(c, "prefer roles near austin")
    assert "Prefer roles near Austin" in learnings.summary_for_prompt(c)
    learnings.forget(c, note["id"])
    assert c.execute("SELECT count(*) FROM rika_learnings").fetchone()[0] == 0
    for index in range(learnings.NOTES_MAX):
        learnings.add_note(c, f"Note {index}")
    with pytest.raises(ValueError):
        learnings.add_note(c, "One too many")


def test_summary_for_a_job_names_what_it_asks_for(workspace, jobs):
    c = workspace.conn
    ai.save_fact(c, text="Built data pipelines in Python.", source="Records", confirmed=True)
    target = _job(c, "g", "Kubernetes and Python required.")
    learnings.refresh(c, VOCABULARY)
    summary = learnings.summary_for_prompt(c, job_id=target, vocabulary=VOCABULARY)
    assert "not applicant facts" in summary
    line = next(line for line in summary.splitlines() if line.startswith("- This posting"))
    assert "Kubernetes" in line and "Python" in line and "SQL" not in line
    assert "Python (3 postings; confirmed fact)" in summary
    assert len(summary) <= learnings.SUMMARY_CHARS
    assert learnings.summary_for_prompt(workspace.conn, job_id=10**9, vocabulary=VOCABULARY)  # unknown job: general


def test_empty_memory_summarises_to_nothing(workspace):
    learnings.refresh(workspace.conn, VOCABULARY)
    assert learnings.summary_for_prompt(workspace.conn) == ""


def test_inactive_applyai_leaves_posting_memory_alone(workspace, jobs):
    assert learnings.refresh(workspace.conn, VOCABULARY, modules=("workspace",)) is False
    assert learnings.learnings(workspace.conn) == []


# Resume edits and the Workshop preview ---------------------------------------------------

def _enabled(workspace):
    app = SimpleNamespace(settings=workspace.settings, config=SimpleNamespace(resume_terms=VOCABULARY))
    ai.update_settings(workspace.conn, enabled=True, model_id="synthetic/model", max_cost_usd="1")
    return app


def _provider(monkeypatch, text):
    from kitty.core.rika import llm
    monkeypatch.setattr(llm, "prepare", lambda app, conn, model, messages, tools:
                        llm.Call(model, {"model": model, "messages": messages}, "synthetic", Decimal("0.01"), "binding"))
    monkeypatch.setattr(llm, "reserve", lambda conn, call: "reservation")
    monkeypatch.setattr(llm, "settle", lambda *args: None)
    monkeypatch.setattr(llm, "stream", lambda call, on_token: llm.Reply(text=text, cost=Decimal("0.001")))


def _reviewed(workspace, monkeypatch, app, reason, accept):
    c = workspace.conn
    fact = ai.save_fact(c, text="Built a prototype.", source="Records", confirmed=True)
    doc = store.create_doc(c, title="Synthetic", format="html", source="Built a prototype.")
    _provider(monkeypatch, json.dumps({"summary": "Concise", "hunks": [{
        "find": "Built a prototype.", "replace": "Built the prototype.", "reason": reason, "fact_ids": [fact["id"]]}]}))
    preview = ai.preview_edit(app, c, doc_id=doc["id"], instruction="Clarify")
    proposal = ai.run_edit(app, c, preview_id=preview["id"], digest=preview["digest"])
    ai.review_proposal(c, proposal["id"], base_revision=1,
                       accept_ids=[proposal["hunks"][0]["id"]] if accept else [], confirmed=True)
    return proposal["id"]


def test_resume_reviews_become_edit_habits_referencing_proposals(workspace, monkeypatch):
    app = _enabled(workspace)
    accepted = _reviewed(workspace, monkeypatch, app, "Lead with the result", accept=True)
    rejected = _reviewed(workspace, monkeypatch, app, "Drop the article", accept=False)
    learnings.refresh(workspace.conn, VOCABULARY)
    edits = {item["text"]: item for item in learnings.learnings(workspace.conn) if item["kind"] == "resume_edit"}
    assert edits["Lead with the result"]["detail"] == {"accepted": 1, "rejected": 0}
    assert edits["Lead with the result"]["evidence"] == [accepted]
    assert edits["Drop the article"]["detail"] == {"accepted": 0, "rejected": 1}
    assert edits["Drop the article"]["evidence"] == [rejected]
    summary = learnings.summary_for_prompt(workspace.conn)
    assert "Lead with the result" in summary and "Drop the article" in summary


def test_workshop_preview_shows_the_summary_without_forgotten_learnings(workspace, monkeypatch, jobs):
    app = _enabled(workspace)
    c = workspace.conn
    _reviewed(workspace, monkeypatch, app, "Lead with the result", accept=True)
    _reviewed(workspace, monkeypatch, app, "Drop the article", accept=False)
    learnings.refresh(c, VOCABULARY)
    forgotten = next(i for i in learnings.learnings(c) if i["text"] == "Drop the article")
    learnings.forget(c, forgotten["id"])
    learnings.forget(c, _by_key(c, "posting_skill")["SQL"]["id"])
    learnings.add_note(c, "Reach me at person@example.test")
    doc = store.create_doc(c, title="Synthetic", format="html", source="Built a prototype.")
    _provider(monkeypatch, "{}")
    preview = ai.preview_edit(app, c, doc_id=doc["id"], instruction="Clarify", job_id=jobs["queued"])
    payload = json.loads(preview["redacted_payload"]["messages"][1]["content"])
    summary = payload["context"]["rika_memory_not_applicant_evidence"]
    assert "Kubernetes" in summary and "Lead with the result" in summary
    body = json.dumps(preview["redacted_payload"])
    assert "Drop the article" not in body
    assert "SQL" not in summary
    assert "person@example.test" not in body
