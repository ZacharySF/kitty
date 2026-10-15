"""Stage 3 triage with a fake Anthropic client: what is sent, what is stored, what it costs."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kitty.core import jobs as jobs_core
from kitty.core import resumes as resumes_core
from kitty.core.triage.run import collect_batches, plan_triage, run_now, submit_batch
from kitty.core.triage.prompt import AnswerError, parse_answer

JD = "You will write firmware. Experience with SPI and I2C buses is a plus. Python required."
RESUME = "Pat Example | you@example.invalid | (555) 010-0000\nProgramming: C++, Python, CUDA\nBuilt a rover"


class FakeClient:
    """Answers every request with `answer`; records what was sent."""

    def __init__(self, answer: dict, *, status: str = "ended"):
        self.answer, self.sent, self.batches_sent = answer, [], []
        self.status = status
        self.messages = SimpleNamespace(create=self._create, batches=SimpleNamespace(
            create=self._batch_create, retrieve=self._batch_retrieve, results=self._batch_results))

    def _message(self):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200,
                                cache_creation_input_tokens=0, cache_read_input_tokens=3000)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(self.answer))],
                               usage=usage, stop_reason="end_turn")

    def _create(self, **params):
        self.sent.append(params)
        return self._message()

    def _batch_create(self, requests):
        self.batches_sent.append(requests)
        return SimpleNamespace(id="batch_1")

    def _batch_retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self.status)

    def _batch_results(self, batch_id):
        for request in self.batches_sent[-1]:
            yield SimpleNamespace(custom_id=request["custom_id"],
                                  result=SimpleNamespace(type="succeeded", message=self._message()))


@pytest.fixture
def space(workspace):
    """A model track with a registered text resume, and one job per triage path."""
    tracks = workspace.config.tracks.tracks
    llm = next(t for t in tracks if t.triage_depth == "llm" and t.default_resume)
    rules = next(t for t in tracks if t.triage_depth == "rules")
    path = workspace.settings.root / "resumes" / "resume.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text(RESUME)
    resumes_core.add_resume(workspace.conn, llm.default_resume, path)

    def job(title, track, **extra):
        stored, _ = jobs_core.upsert_job(workspace.conn, company=f"{title} Co", title=title,
                                         url=f"https://example.com/{title}", jd_text=JD,
                                         tracks=[track.name], **extra)
        return stored

    workspace.ids = SimpleNamespace(
        model=job("Firmware Intern", llm, eligibility="eligible").id,
        filtered=job("PhD Intern", llm, eligibility="ineligible", eligibility_reason="requires phd").id,
        parttime=job("Tutor", rules, eligibility="eligible").id,
        mine=job("Hand Tiered Intern", llm, eligibility="eligible").id,
    )
    jobs_core.set_tier(workspace.conn, workspace.ids.mine, "A", reason="mine", tier_source="manual")
    workspace.label = llm.default_resume
    workspace.conn.commit()
    return workspace


def _answer(label: str) -> dict:
    return {"tier": "A", "tier_reason": "Firmware work open to freshmen.", "resume": label,
            "resume_reason": "It lists Python and C++.",
            "edit_notes": [
                {"jd_phrase": "Experience with SPI and I2C buses", "resume_line": "",
                 "note": "Postings ask for SPI/I2C; add only if true."},
                {"jd_phrase": "Rust experience required", "resume_line": "",           # not in the JD
                 "note": "invented quote"},
                {"jd_phrase": "Python required", "resume_line": "Programming: C++, Python, CUDA",
                 "note": "Python is already there; keep it near the top."},
            ]}


def _get(space, job_id):
    return jobs_core.get_job(space.conn, job_id)


def test_now_run_tiers_picks_a_resume_and_keeps_only_real_quotes(space):
    client = FakeClient(_answer(space.label))
    plan = plan_triage(space.conn, space.config, space.settings)
    result = run_now(space.conn, space.config, plan, client)

    assert [p["messages"][0]["content"].split("\n")[1] for p in client.sent] == ["Title: Firmware Intern"]
    job = _get(space, space.ids.model)
    assert job.tier == "A" and job.tier_source == "llm" and job.resume_label == space.label
    assert len(job.edit_notes) == 2 and not any("Rust" in note for note in job.edit_notes)
    assert result.notes_dropped == 1 and result.cost_usd > 0

    assert _get(space, space.ids.filtered).tier == "C"                   # rules, no request
    assert _get(space, space.ids.parttime).tier_source == "rules"        # rules-only track
    mine = _get(space, space.ids.mine)
    assert (mine.tier, mine.tier_source) == ("A", "manual")              # never overwritten

    system = client.sent[0]["system"][0]
    assert system["cache_control"]["ttl"] == "1h"
    assert "you@example.invalid" not in system["text"] and "555" not in system["text"]


def test_a_second_run_reuses_the_saved_answer_for_free(space):
    run_now(space.conn, space.config, plan_triage(space.conn, space.config, space.settings),
            FakeClient(_answer(space.label)))
    plan = plan_triage(space.conn, space.config, space.settings)
    assert plan.to_send == [] and plan.cached == [] and plan.rules == []


def test_the_cost_cap_stops_a_run_before_it_spends_more(space):
    capped = replace(space.config, triage=space.config.triage.model_copy(update={"max_cost_usd_per_run": 0.0}))
    client = FakeClient(_answer(space.label))
    result = run_now(space.conn, capped, plan_triage(space.conn, capped, space.settings), client)

    assert client.sent == [] and result.cap_hit and result.skipped_for_cap == 1
    stats = json.loads(space.conn.execute("SELECT stats_json FROM runs WHERE id = ?",
                                          (result.run_id,)).fetchone()[0])
    assert stats["cap_hit"] is True


def test_a_batch_is_applied_only_once_it_has_ended(space):
    client = FakeClient(_answer(space.label), status="in_progress")
    submitted = submit_batch(space.conn, space.config,
                             plan_triage(space.conn, space.config, space.settings), client)
    assert submitted.pending and submitted.sent == 1
    assert _get(space, space.ids.model).tier_source == "rules"          # provisional until the batch lands

    [waiting] = collect_batches(space.conn, space.config, space.settings, client)
    assert waiting.pending and _get(space, space.ids.model).tier_source == "rules"

    client.status = "ended"
    [done] = collect_batches(space.conn, space.config, space.settings, client)
    job = _get(space, space.ids.model)
    assert done.applied == 1 and (job.tier, job.tier_source) == ("A", "llm")
    assert collect_batches(space.conn, space.config, space.settings, client) == []   # collected once


def test_late_batch_cannot_restore_a_verdict_from_an_old_description(space):
    client = FakeClient(_answer(space.label))
    submit_batch(space.conn, space.config,
                 plan_triage(space.conn, space.config, space.settings), client)
    space.conn.execute(
        "UPDATE jobs SET jd_text = ? WHERE id = ?",
        ("Updated qualification: a master's degree is required.", space.ids.model),
    )
    space.conn.commit()
    before = _get(space, space.ids.model)
    [done] = collect_batches(space.conn, space.config, space.settings, client)
    after = _get(space, space.ids.model)
    assert done.applied == 0 and done.cost_usd > 0
    assert (after.tier, after.tier_source, after.resume_id, after.edit_notes) == (
        before.tier, before.tier_source, before.resume_id, before.edit_notes
    )
    replanned = plan_triage(space.conn, space.config, space.settings)
    assert [job.id for job in replanned.to_send] == [space.ids.model]


def test_an_answer_naming_an_unknown_resume_is_rejected(space):
    bad = json.dumps(_answer("not-a-resume"))
    with pytest.raises(AnswerError):
        parse_answer(bad, JD, {space.label: RESUME.splitlines()}, space.config.profile)
