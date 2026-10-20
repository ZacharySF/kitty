"""Reading form questions without a browser: parsing, targets, classification, merging."""

from dataclasses import replace

import pytest

from kitty.config import AnswerEntry, AnswersFile
from kitty.core import form_questions as fq
from kitty.core import jobs as jobs_core
from kitty.core import questions as questions_core
from kitty.core.discovery.http import HttpError, RateLimited
from fakes import FIXTURES, FakeHttp, fixture

GH_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{posting}?questions=true"


def _lever_page() -> str:
    return (FIXTURES / "form_questions" / "lever_apply.html").read_text(encoding="utf-8")


def _by_label(found):
    return {q.label: q for q in found}


# ----------------------------------------------------------------- parsing


def test_greenhouse_reads_questions_choices_and_marks_eeo_demographic():
    found = _by_label(fq.parse_greenhouse(fixture("form_questions/greenhouse_job.json")))

    assert found["Resume/CV"].kind == "file"                 # a file plus a paste-in textarea
    assert found["Latitude"].kind == "hidden"
    status = found["Please select your enrollment status:"]
    assert status.kind == "select" and status.required
    assert status.choices[-1] == "None of the above"
    assert found["Why do you want to work at Example Space?"].kind == "textarea"
    assert found["Gender"].demographic and found["Gender"].choices == ("Decline To Self Identify", "Female", "Male")
    assert found["What is your sexual orientation?"].demographic          # new-style demographic_questions
    assert not found["How did you hear about this job?"].demographic


def test_lever_groups_radios_and_checkboxes_and_ignores_other_forms():
    found = _by_label(fq.parse_lever(_lever_page()))

    clearance = found["Do you currently hold an active US security clearance?"]
    assert (clearance.kind, clearance.choices, clearance.required) == ("radio", ("Yes", "No"), True)
    start = found["If selected, when would you be able to start?"]
    assert start.kind == "select" and start.choices == ("May 2027", "June 2027")   # no "Select..." placeholder
    languages = found["Language Skill(s) (Check all that apply)"]
    assert languages.choices == ("English (ENG)", "Spanish (SPA)") and not languages.required
    assert found["Current company"].kind == "text" and not found["Current company"].required
    assert "Add a cover letter or anything else you want to share." in found     # no label: placeholder
    assert found["Veteran status"].demographic and found["Date"].demographic
    assert "Newsletter" not in found and "selectedLocation" not in {q.name for q in found.values()}


# ----------------------------------------------------------------- targets


def test_form_target_prefers_the_discovery_id_over_a_company_page():
    target = fq.form_target("https://trading.example.invalid/listings/fpga-intern-900031", None,
                            {"greenhouse": "drweng:900031"})
    assert target == fq.FormTarget("greenhouse", "drweng", "900031")


def test_form_target_parses_ats_urls_and_skips_the_rest_with_a_reason():
    lever = fq.form_target("https://jobs.lever.co/palantir/655f/apply", None, {})
    assert lever.url == "https://jobs.lever.co/palantir/655f/apply"
    assert fq.form_target("https://job-boards.greenhouse.io/hpr/jobs/900032", None, {}).posting == "900032"
    assert fq.form_target("https://boards.greenhouse.io/embed/job_app?for=imc&token=42", None, {}).board == "imc"
    assert fq.form_target("https://boards.greenhouse.io/embed/job_app?token=42", None, {}, "coinbase").board == "coinbase"
    assert fq.form_target("https://acme.com/careers?gh_jid=77", None, {}, "acme").board == "acme"

    assert fq.form_target("https://jobs.ashbyhq.com/etched/699f", None, {}) == fq.SKIP_REASONS["ashby"]
    assert fq.form_target("https://x.wd1.myworkdayjobs.com/Site/job/1", None, {}) == fq.SKIP_REASONS["workday"]
    assert fq.form_target("https://boards.greenhouse.io/acme", None, {}) == "greenhouse: unrecognised URL"


# ----------------------------------------------------------- classification


def test_classify_boundaries(workspace):
    unconfigured = replace(
        workspace.config,
        profile=workspace.config.profile.model_copy(update={
            "name": "", "first_name": "", "email": "",
        }),
    )
    for label in ("First Name", "Email"):
        assert fq.classify(label, "text", (), False, unconfigured) == "factual"
    config = replace(
        workspace.config,
        profile=workspace.config.profile.model_copy(update={
            "name": "Example Applicant", "email": "applicant@example.invalid",
        }),
        answers=AnswersFile(answers=[
            AnswerEntry(question="Where did you find us?", answer="Careers site", match=["hear about this job"]),
        ]),
    )
    kind = lambda label, field="text", choices=(), demographic=False: fq.classify(   # noqa: E731
        label, field, choices, demographic, config)

    assert kind("How did you hear about this job?", "select", ("Careers site",)) == "answered"   # a match phrase
    assert kind("First Name") == "profile"
    assert kind("Email") == "profile"
    assert kind("Gender", "select", ("Female", "Male")) == "demographic"
    assert kind("Date", "text", demographic=True) == "demographic"         # asked inside an EEO section
    assert kind("Why do you want to join {company}?", "textarea") == "essay"
    assert kind("Why are you interested?", "select", ("Pay", "Mission")) == "choice"   # choices are never essays
    assert kind("If (f) Other, please specify:") == "factual"
    assert kind("High School Name", "textarea") == "factual"               # a textarea, but a fact
    assert kind("Citizenship Status", "select", ("U.S. citizen", "Other")) == "choice"


def test_generic_label_replaces_only_the_company_name_as_written():
    assert fq.generic_label("Why do you want to join Etched?", ("Etched",)) == "Why do you want to join {company}?"
    assert fq.generic_label("Have you worked at SpaceX-adjacent firms?", ("SpaceX",)) == \
        "Have you worked at SpaceX-adjacent firms?"
    assert fq.generic_label("Your current employer", ("Current",)) == "Your current employer"


# ----------------------------------------------------------------- collect


def _queued_job(conn, company, title, url, **source):
    job, _ = jobs_core.upsert_job(conn, company=company, title=title, url=url, **source)
    conn.execute("UPDATE jobs SET tier = 'A', queue_state = 'new' WHERE id = ?", (job.id,))
    return job


def _collect(workspace, http):
    return fq.collect(workspace.conn, workspace.settings, workspace.config,
                      states=["new", "queued"], tiers=["A", "B"], http=http)


def test_collect_merges_repeats_and_a_rerun_changes_no_counts(workspace):
    workspace.config = replace(
        workspace.config,
        profile=workspace.config.profile.model_copy(update={
            "name": "Example Applicant", "email": "applicant@example.invalid",
        }),
    )
    conn = workspace.conn
    payload = fixture("form_questions/greenhouse_job.json")
    acme = _queued_job(conn, "Example Space", "Intern A", "https://job-boards.greenhouse.io/example/jobs/1")
    other = _queued_job(conn, "Beta", "Intern B", "https://www.beta.com/jobs/2",
                        source="greenhouse", source_job_id="beta:2")
    _queued_job(conn, "Lev", "Intern C", "https://jobs.lever.co/lev/abc")
    _queued_job(conn, "Ash", "Intern D", "https://jobs.ashbyhq.com/ash/def")
    _queued_job(conn, "Gone", "Intern E", "https://boards.greenhouse.io/gone/jobs/9")
    http = FakeHttp({
        GH_URL.format(board="example", posting="1"): payload,
        GH_URL.format(board="beta", posting="2"): payload,
        "https://jobs.lever.co/lev/abc/apply": _lever_page().encode(),
    })

    first = _collect(workspace, http)
    assert (first.considered, first.fetched) == (5, 3)
    assert first.skipped == {fq.SKIP_REASONS["ashby"]: 1}
    assert first.failed == {"HTTP 404": 1}

    summaries = {s.text: s for s in fq.summarize(conn, workspace.config)}
    status = summaries["Please select your enrollment status:"]
    assert status.job_ids == (acme.id, other.id) and status.required_jobs == 2
    assert set(status.companies) == {"Example Space", "Beta"}
    assert "First Name" not in summaries and "Resume/CV" not in summaries      # profile and uploads left out
    assert "Email" not in summaries
    assert summaries["Why do you want to work at {company}?"].kind == "essay"
    assert summaries["Why do you want to work at Example Space?"].job_ids == (other.id,)

    times_seen = {q.text: q.times_seen for q in questions_core.list_questions(conn, unanswered_only=False)}
    again = _collect(workspace, http)
    assert (again.new_links, again.new_questions) == (0, 0)
    assert again.distinct_questions == first.distinct_questions
    assert {q.text: q.times_seen for q in questions_core.list_questions(conn, unanswered_only=False)} == times_seen


def test_a_rate_limit_stops_that_ats_for_the_run(workspace):
    conn = workspace.conn
    for n in (1, 2):
        _queued_job(conn, f"Co{n}", f"Intern {n}", f"https://boards.greenhouse.io/co{n}/jobs/{n}")
    http = FakeHttp({GH_URL.format(board="co1", posting="1"): RateLimited("429 from boards-api")})

    result = _collect(workspace, http)

    assert len(http.calls) == 1
    assert result.failed == {"rate limited (429)": 1, "rate limited (429), greenhouse skipped": 1}


def test_logged_questions_are_listed_with_their_job(workspace):
    conn = workspace.conn
    job = _queued_job(conn, "Ash", "Intern", "https://jobs.ashbyhq.com/ash/def")
    questions_core.log_question(conn, "What is your expected graduation term?", job_id=job.id)
    again = questions_core.log_question(conn, "What is your expected graduation term?", job_id=job.id)

    assert again.times_seen == 2                                  # the filler's own count is unchanged
    logged = next(s for s in fq.summarize(conn, workspace.config)
                  if s.text == "What is your expected graduation term?")
    assert (logged.kind, logged.job_ids, logged.field_kind) == ("factual", (job.id,), None)


def test_a_failed_fetch_stores_nothing(workspace):
    _queued_job(workspace.conn, "Co", "Intern", "https://boards.greenhouse.io/co/jobs/5")
    http = FakeHttp({GH_URL.format(board="co", posting="5"): HttpError("ReadTimeout after 3 tries: x")})

    result = _collect(workspace, http)

    assert result.failed == {"ReadTimeout": 1}
    assert fq.summarize(workspace.conn, workspace.config) == []


def _permission_answers(workspace):
    return replace(workspace.config, answers=AnswersFile(answers=[
        AnswerEntry(
            question="Are you authorized to work in the United States?", answer="Yes",
            match=["authorized to work"],
        ),
        AnswerEntry(
            question="Will you require sponsorship?", answer="No", match=["require sponsorship"],
            choices=["No sponsorship required in the United States"],
        ),
    ]))


@pytest.mark.parametrize(("location", "country"), [
    ("Singapore", "Singapore"),
    ("London", "United Kingdom"),
    ("Chicago;London", None),
    ("Chicago", "Singapore"),
    ("Remote", None),
    (None, None),
])
def test_merged_relative_permission_is_not_globally_answered_for_mixed_jobs(workspace, location, country):
    conn = workspace.conn
    config = _permission_answers(workspace)
    us = _queued_job(conn, "US Co", "Intern US", "https://example.invalid/us", location="Chicago", country="US")
    other = _queued_job(conn, "Other Co", "Intern Other", "https://example.invalid/other",
                        location=location, country=country)
    relative = "Are you authorized to work in this country?"
    questions_core.log_question(conn, relative, job_id=us.id)
    assert next(s for s in fq.summarize(conn, config) if s.text == relative).kind == "answered"
    assert questions_core.refresh_answered_flags(conn, config) == 1

    questions_core.log_question(conn, relative, job_id=other.id)
    summary = next(s for s in fq.summarize(conn, config) if s.text == relative)
    assert summary.kind == "factual" and set(summary.job_ids) == {us.id, other.id}
    assert questions_core.refresh_answered_flags(conn, config) == 0
    assert next(q for q in questions_core.list_questions(conn) if q.text == relative).answered is False


def test_explicit_us_permission_remains_answered_without_job_country(workspace):
    config = _permission_answers(workspace)
    question = "Are you authorized to work in the United States?"
    questions_core.log_question(workspace.conn, question)
    assert next(s for s in fq.summarize(workspace.conn, config) if s.text == question).kind == "answered"
    assert questions_core.refresh_answered_flags(workspace.conn, config) == 1


def test_stored_choices_scope_sponsorship_per_form_not_most_common_choices(workspace):
    conn = workspace.conn
    config = _permission_answers(workspace)
    question = "Will you require sponsorship?"
    for number, choices in enumerate([
        ("No sponsorship required in the United States", "Yes"),
        ("No sponsorship required in the United States", "Yes"),
        ("No sponsorship required in Singapore", "Yes"),
    ]):
        job = _queued_job(conn, f"Co{number}", "Intern", f"https://example.invalid/{number}",
                          location="Chicago", country="US")
        questions_core.record_form_question(
            conn, question, job.id, field_kind="radio", required=True,
            choices=choices, demographic=False,
        )
    summary = next(s for s in fq.summarize(conn, config) if s.text == question)
    assert summary.choices[0] == "No sponsorship required in the United States"
    assert summary.kind == "choice"
    assert questions_core.refresh_answered_flags(conn, config) == 0


def test_country_specific_generic_question_with_no_job_is_unanswered(workspace):
    config = _permission_answers(workspace)
    question = "Will you require sponsorship?"
    questions_core.log_question(workspace.conn, question)
    assert next(s for s in fq.summarize(workspace.conn, config) if s.text == question).kind == "factual"
    assert questions_core.refresh_answered_flags(workspace.conn, config) == 0


def test_classification_uses_stored_company_and_track_scope(workspace):
    conn = workspace.conn
    track = workspace.config.track_names[0]
    question = "What device do you use?"
    config = replace(workspace.config, answers=AnswersFile(answers=[
        AnswerEntry(question=question, answer="Laptop", companies=["Acme"], tracks=[track]),
    ]))
    acme = _queued_job(conn, "Acme", "Intern", "https://example.invalid/acme", tracks=[track])
    questions_core.log_question(conn, question, job_id=acme.id)
    assert next(s for s in fq.summarize(conn, config) if s.text == question).kind == "answered"
    assert questions_core.refresh_answered_flags(conn, config) == 1
    other = _queued_job(conn, "Other", "Intern", "https://example.invalid/other", tracks=[track])
    questions_core.log_question(conn, question, job_id=other.id)
    assert next(s for s in fq.summarize(conn, config) if s.text == question).kind == "factual"
    assert questions_core.refresh_answered_flags(conn, config) == 0
