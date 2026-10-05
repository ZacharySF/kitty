"""The form filler: what it fills, what it leaves, and that it never submits.

Browser tests run in headless Firefox against the fixture forms in
tests/fixtures/forms, and are skipped when Firefox is not installed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from applyai.config import AnswerEntry, AnswersFile, load_config
from applyai.core import jobs as jobs_core
from applyai.core import questions as questions_core
from applyai.core import resumes as resumes_core
from applyai.core.apply.bidi import Tab, open_tab
from applyai.core.apply.browser import headless_firefox
from applyai.core.apply.fields import FormField, graduation_answer_spellings, match_option, plan_for
from applyai.core.apply.filler import _apply_plan, _watched_tab, choose_resume, fill_job, fill_jobs
from applyai.core.apply.page import pick_from_list, read_fields
from applyai.core.apply.urls import detect_ats, form_url
from applyai.settings import Settings, find_firefox

FORMS = Path(__file__).parent / "fixtures" / "forms"

PROFILE = """\
name: Pat Quinn Tester
first_name: Pat
last_name: Tester
email: pat@example.invalid
phone: (555) 010-0000
linkedin: https://social.example.invalid/in/pat
github: https://code.example.invalid/pat
school: Example State University
degree: B.S. Engineering
major: Electrical Engineering
standing: sophomore
graduation: 2032-05
home_city: Arlington
home_state: TX
"""

ANSWERS = """\
answers:
  - question: Are you legally authorized to work in the United States?
    answer: "Yes"
  - question: Will you now or in the future require sponsorship?
    answer: "No"
  - question: How did you hear about this position?
    answer: Company careers page
  - question: Expected graduation date
    answer: Spring 2032
"""


@pytest.fixture
def space(workspace):
    """The test workspace with a known profile, answers and a registered default resume."""
    config_dir = workspace.settings.config_dir
    (config_dir / "profile.yaml").write_text(PROFILE)
    (config_dir / "answers.yaml").write_text(ANSWERS)
    workspace.config = load_config(config_dir)
    track = next(t for t in workspace.config.tracks.tracks if t.default_resume)
    pdf = workspace.settings.root / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% test resume\n")
    resumes_core.add_resume(workspace.conn, track.default_resume, pdf)
    workspace.track = track
    return workspace


def _job(space, form: str):
    """A job whose posting is one of the fixture forms."""
    job, _ = jobs_core.upsert_job(
        space.conn, company="Fixture Co", title=f"{form} intern",
        url=(FORMS / f"{form}.html").as_uri(), tracks=[space.track.name],
    )
    return job


@pytest.fixture(scope="module")
def firefox(tmp_path_factory):
    """One headless Firefox for the module's browser tests (skips when not installed)."""
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    settings = Settings.load(root=tmp_path_factory.mktemp("firefox"))
    with headless_firefox(settings) as bidi:
        yield bidi


@pytest.fixture
def page(firefox):
    """A fresh tab in the headless Firefox."""
    tab = open_tab(firefox)
    yield tab
    tab.close()


def _js(page: Tab, expression: str, *args):
    """Evaluate a function in the page's own realm (where the fixture's guard counters live)."""
    return page.main.call(expression, *args, page_realm=True)


def _value(page: Tab, selector: str) -> str:
    return _js(page, "(s) => document.querySelector(s).value", selector)


def _parent_text(page: Tab, selector: str) -> str:
    return _js(page, "(s) => document.querySelector(s).parentElement.innerText", selector)


def _files(page: Tab, selector: str) -> list[str]:
    return _js(page, "(s) => Array.from(document.querySelector(s).files).map(f => f.name)", selector)


def _assert_nothing_submitted(page: Tab) -> None:
    counters = _js(page, "() => [window.__submitted, window.__buttonClicks, window.__enterPresses]")
    assert counters == [0, 0, 0], "a submit, button click or Enter key happened"


def _logged(space) -> set[str]:
    return {q.text for q in questions_core.list_questions(space.conn)}


# ------------------------------------------------------------ browser tests


def test_greenhouse_like_form(space, page):
    profile = space.config.profile
    job = _job(space, "greenhouse")
    report = fill_job(page, space.conn, space.settings, space.config, job)

    assert _value(page, "#first_name") == profile.given_name
    assert _value(page, "#last_name") == profile.family_name
    assert _value(page, "#email") == profile.email
    assert _value(page, "#phone") == profile.phone
    assert _value(page, "#question_1") == profile.linkedin
    assert _value(page, "#preferred_name") == ""                 # not a profile fact
    # search boxes: a dropdown choice was clicked, matched as a place / an answer
    location = _parent_text(page, "#candidate-location")
    assert location.startswith(f"{profile.home_city}, Texas")
    authorized = space.config.answer_for("Are you legally authorized to work in the United States?")
    assert _parent_text(page, "#question_2") == authorized.answer
    # "May 2032" is not in the list's words: the list opened untyped, Spring term picked
    assert _parent_text(page, "#question_4") == "2032 - Spring"
    assert _parent_text(page, "#major") == "Electrical Engineering"
    assert _js(page, "() => window.__searches.major") == []        # existing choice, no typing
    assert _files(page, "#resume") == ["resume.pdf"]
    assert _files(page, "#cover_letter") == []
    assert _value(page, "#gender") == ""                          # EEO left alone
    assert _value(page, "#question_3") == ""

    question = "What is the most impressive thing you have ever accomplished?"
    assert report.unanswered == [question]
    assert question in _logged(space)
    assert report.status == "partial"
    assert report.resume_label == space.track.default_resume
    _assert_nothing_submitted(page)

    stored = jobs_core.get_job(space.conn, job.id)
    assert stored.queue_state == "queued"
    assert stored.resume_label == space.track.default_resume
    assert stored.application_id is None                          # I mark it applied myself


def test_default_change_after_upload_cannot_relabel_the_application(space, page):
    from applyai.core.apply.watch import record_submission

    job = _job(space, "greenhouse")
    uploaded = choose_resume(space.conn, space.config, job)
    report = fill_job(page, space.conn, space.settings, space.config, job)
    assert _files(page, "#resume") == ["resume.pdf"]
    assert job.resume_id is None  # the object predates the upload's persisted pin
    alternate_file = space.settings.root / "alternate.pdf"
    alternate_file.write_bytes(b"%PDF-1.4\n% alternate synthetic resume\n")
    alternate = resumes_core.add_resume(space.conn, "alternate", alternate_file)
    space.conn.execute(
        "INSERT INTO resume_default_overrides(track,resume_id) VALUES (?,?)",
        (space.track.name, alternate.id),
    )
    watched = _watched_tab(job, page, report)
    record_submission(space.conn, space.config, watched, "https://example.invalid/confirmation", lambda _: None)
    recorded = space.conn.execute(
        "SELECT resume_id FROM applications WHERE job_id=?", (job.id,),
    ).fetchone()
    assert recorded["resume_id"] == uploaded.id
    assert jobs_core.get_job(space.conn, job.id).resume_id == uploaded.id
    _assert_nothing_submitted(page)


def test_lever_like_form(space, page):
    profile = space.config.profile
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, "lever"))

    assert len(_files(page, "input[name=resume]")) == 1
    assert _value(page, "input[name=name]") == profile.name
    assert _value(page, "input[name=email]") == profile.email
    # Lever-style suggestion picked (its list answers slowly): a place matching the profile
    # city and state, in the box and in the hidden selectedLocation that Lever submits
    location = _value(page, "input[name=location]")
    assert location.startswith(f"{profile.home_city}, {profile.home_state}")
    assert json.loads(_value(page, "#selected-location"))["name"] == location
    assert _value(page, "input[name='urls[GitHub]']") == profile.github
    assert _value(page, "input[name=org]") == ""
    sponsorship = space.config.answer_for("Will you now or in the future require sponsorship?")
    checked = _js(page, "() => document.querySelector(\"input[name='cards[abc][field0]']:checked\").value")
    assert checked == sponsorship.answer
    assert _value(page, "select[name='eeo[gender]']") == ""

    assert report.unanswered == ["Describe a hardware project you are proud of."]
    _assert_nothing_submitted(page)


def test_ashby_like_form(space, page):
    profile = space.config.profile
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, "ashby"))

    assert _files(page, "#autofill-input") == []
    assert len(_files(page, "#_systemfield_resume")) == 1
    assert _value(page, "#_systemfield_name") == profile.name
    assert _value(page, "#school") == profile.school
    assert _value(page, "#major") == profile.major
    assert _value(page, "#gh") == profile.github
    assert _value(page, "#grad") == profile.graduation[:4]
    heard = space.config.answer_for("How did you hear about this position?").answer
    ticked = _js(page, "() => Array.from(document.querySelectorAll('input[type=checkbox]:checked'), e => e.name)")
    assert ticked == [heard]
    assert _js(page, "() => document.querySelectorAll('input[name=auth]:checked').length") == 0

    assert set(report.unanswered) == {
        "Preferred Name",
        "What is your work authorization status?",
        "Are you able to work in-person in San Jose this summer?",   # Yes/No are submit buttons
    }
    _assert_nothing_submitted(page)


def _major_picker(page, choices, **settings):
    """Reconfigure the real fixture widget without retaining its old event handlers."""
    page.goto((FORMS / "greenhouse.html").as_uri())
    _js(page, """(choices, settings) => {
      const old = document.getElementById('major').parentElement;
      const control = old.cloneNode(true);
      control.querySelector('[role=listbox]').remove();
      old.replaceWith(control);
      searchBox(control.querySelector('input'), choices, settings);
      window.__unrelatedClicks = 0;
      for (const hidden of [false, true]) {
        const list = document.createElement('div');
        list.setAttribute('role', 'listbox');
        list.hidden = hidden;
        list.innerHTML = '<div role="option">Electrical Engineering</div>';
        list.addEventListener('click', () => window.__unrelatedClicks++);
        document.body.appendChild(list);
      }
    }""", choices, settings)
    frame = page.main
    major = next(f for f in read_fields([frame]) if f.label == "Major")
    return frame, major


@pytest.mark.parametrize("choice", [
    "Electrical Engineering", "Electrical and Computer Engineering",
])
def test_major_selects_existing_exact_or_safe_alias_without_typing(space, page, choice):
    frame, major = _major_picker(page, ["Electrical Engineering Technology", choice])
    plan = plan_for(major, space.config)

    assert _apply_plan([frame], major, plan)
    assert _parent_text(page, "#major") == choice
    assert _js(page, "() => window.__searches.major") == []
    assert _js(page, "() => window.__unrelatedClicks") == 0
    _assert_nothing_submitted(page)


@pytest.mark.parametrize("initial_choices", [[], ["Electrical Engineering Technology"]])
def test_major_searches_when_empty_or_virtualized_list_has_no_exact_choice(
    space, page, initial_choices,
):
    frame, major = _major_picker(
        page, ["Electrical Engineering"], initialChoices=initial_choices, delay=2500,
    )
    plan = plan_for(major, space.config)

    assert _apply_plan([frame], major, plan)
    assert _parent_text(page, "#major") == "Electrical Engineering"
    queries = _js(page, "() => window.__searches.major")
    assert queries[-1] == "Electrical Engineering"
    assert _js(page, "() => window.__unrelatedClicks") == 0
    _assert_nothing_submitted(page)


def test_major_option_click_must_commit_a_selected_value(space, page):
    frame, major = _major_picker(page, ["Electrical Engineering"], commit=False)
    plan = plan_for(major, space.config)

    assert not pick_from_list(frame, major.key, plan.candidates, location=False)
    assert _value(page, "#major") == ""
    assert _js(page, "() => document.querySelector('#major').parentElement.querySelector('.select__single-value')") is None
    assert _js(page, "() => window.__searches.major") == []
    _assert_nothing_submitted(page)


def test_fill_jobs_reports_each_job_and_never_records_an_application(space):
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    job = _job(space, "lever")
    messages: list[str] = []
    reports = fill_jobs(space.settings, space.config, [job.id, 999_999],
                        headless=True, progress=messages.append)

    filled, missing = reports
    assert (filled.job_id, filled.status, filled.company) == (job.id, "partial", "Fixture Co")
    assert {"resume", "full name", "email"} <= set(filled.filled)
    assert (missing.status, missing.error) == ("error", "no job with id 999999")
    assert messages
    count = space.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    assert count == 0


# --------------------------------------------------------------- pure tests


def test_resume_choice_prefers_the_jobs_own_resume_then_its_track_default(space):
    job = _job(space, "lever")
    default = choose_resume(space.conn, space.config, job)
    assert default is not None and default.label == space.track.default_resume

    other = resumes_core.add_resume(space.conn, "picked-by-hand", space.settings.root / "resume.pdf")
    jobs_core.set_resume(space.conn, job.id, other.id)
    assert choose_resume(space.conn, space.config, jobs_core.get_job(space.conn, job.id)).label == "picked-by-hand"

    untracked, _ = jobs_core.upsert_job(space.conn, company="No Track Co", title="Intern",
                                        url="https://example.invalid/x")
    assert choose_resume(space.conn, space.config, untracked) is None


def test_option_matching_is_exact_or_a_unique_prefix():
    assert match_option(["no"], ["Yes", "No"]) == 1
    assert match_option(["Yes"], ["Yes, I am authorized", "No, I am not"]) == 0
    assert match_option(["Yes"], ["Yes, now", "Yes, later", "No"]) is None   # ambiguous
    assert match_option(["Maybe"], ["Yes", "No"]) is None
    places = ["Arlington, Virginia, United States", "Arlington, Texas, United States"]
    assert match_option(["Arlington, TX"], places, location=True) == 1


def _field(label: str, kind: str = "text", **extra) -> FormField:
    return FormField(key="k", kind=kind, label=label, **extra)


def test_plan_takes_answers_first_then_profile_and_never_guesses(space):
    config = space.config
    expected = config.answer_for("Expected graduation date").answer
    planned = plan_for(_field("Expected graduation date"), config)
    assert planned.from_answers and planned.candidates[0] == expected
    assert plan_for(_field("Graduation Year"), config).candidates == (config.profile.graduation[:4],)
    assert plan_for(_field("Email address"), config).candidates == (config.profile.email,)
    assert plan_for(_field("Name", autocomplete="name"), config).field_name == "full name"
    # questions that merely mention a profile word are not profile fields
    assert plan_for(_field("Are you currently a graduate student?"), config) is None
    assert plan_for(_field("Can we email you about other roles at our company?"), config) is None
    assert plan_for(_field("Preferred First Name"), config) is None
    # my graduation date is the college one: a high-school graduation question is not it
    assert plan_for(_field("Year of High School Graduation", kind="select",
                           options=("2024", "2032")), config) is None
    assert plan_for(_field("Gender", kind="select", options=("Male", "Female")), config) is None


def test_answer_match_phrases_cover_reworded_questions_but_ties_answer_nothing(space):
    answers = AnswersFile(answers=[
        AnswerEntry(question="Are you legally authorized to work in the US?", answer="Yes",
                    match=["authorized to work"]),
        AnswerEntry(question="Will you require sponsorship?", answer="No", match=["sponsorship"]),
    ])
    config = replace(space.config, answers=answers)
    reworded = "Are you authorized to work lawfully in the United States for Acme?"
    assert config.answer_for(reworded).answer == "Yes"
    assert config.answer_for("Do you need visa sponsorship now or later?").answer == "No"
    assert config.answer_for("Describe your sponsorship of a club you were authorized to work for") is None
    assert config.answer_for("Are you unauthorized?") is None          # whole words only


def test_company_scoped_answers_override_general_answers_and_do_not_leak(space):
    question = "Have you previously interviewed with this company?"
    answers = AnswersFile(answers=[
        AnswerEntry(
            question=question, answer="No", exclude_companies=["Northwind", "Lockheed"],
        ),
        AnswerEntry(
            question=question, answer="Yes", companies=["Northwind", "Avionics"],
        ),
    ])
    config = replace(space.config, answers=answers)

    assert config.answer_for(question, "Northwind Avionics").answer == "Yes"
    assert config.answer_for(question, "SpaceX").answer == "No"
    assert config.answer_for(question, "Lockheed Martin") is None
    assert config.answer_for(question) is None
    assert plan_for(_field(question), config, company="Northwind Avionics").candidates == ("Yes",)


def test_track_scoped_answers_override_and_excluded_tracks_get_nothing(space):
    usual, special = space.config.track_names[:2]
    answers = AnswersFile(answers=[
        AnswerEntry(question="City", answer="Arlington", exclude_tracks=[special]),
        AnswerEntry(question="City", answer="Waco", tracks=[special]),
        AnswerEntry(question="Zip code", answer="12345", exclude_tracks=[special]),
    ])
    profile = space.config.profile.model_copy(update={"city_by_track": {special: "Waco"}})
    config = replace(space.config, answers=answers, profile=profile)

    assert config.answer_for("City", tracks=[usual]).answer == "Arlington"
    assert config.answer_for("City", tracks=[special]).answer == "Waco"
    assert config.answer_for("City").answer == "Arlington"           # no job: the general one
    assert config.answer_for("Zip code", tracks=[usual]).answer == "12345"
    assert config.answer_for("Zip code", tracks=[special]) is None           # left for me to type
    # the profile's location box follows the job's track too
    box = _field("Current location")
    assert plan_for(box, config, tracks=[special]).search_text == "Waco"
    assert plan_for(box, config, tracks=[usual]).search_text == config.profile.home_city


def test_us_spellings_match_a_united_states_phrase_but_the_pronoun_does_not(space):
    answers = AnswersFile(answers=[
        AnswerEntry(question="Are you legally authorized to work in the United States?", answer="Yes",
                    match=["eligible to work in the united states"]),
        AnswerEntry(question="Anything to tell the United States?", answer="No",
                    match=["tell united states"]),
    ])
    config = replace(space.config, answers=answers)
    for question in ("Are you legally eligible to work in the U.S.?",
                     "Are you eligible to work in the USA?",
                     "Are you eligible to work in the US?",
                     "Are you eligible to work in the United States of America?"):
        assert config.answer_for(question).answer == "Yes", question
    assert config.answer_for("Anything else to tell us?") is None        # "us" the pronoun
    assert config.answer_for("Are you legally authorized to work in the U.S.").answer == "Yes"


def test_screening_answers_keep_c_cpp_and_csharp_distinct(space):
    answers = AnswersFile(answers=[
        AnswerEntry(question="Experience in C?", answer="10+"),
        AnswerEntry(question="Experience in C++?", answer="10+"),
        AnswerEntry(question="Experience in C#?", answer="<3"),
    ])
    config = replace(space.config, answers=answers)

    assert config.answer_for("Experience in C?").answer == "10+"
    assert config.answer_for("Experience in C++?").answer == "10+"
    assert config.answer_for("Experience in C sharp?").answer == "<3"


def test_answer_choices_pick_a_reworded_dropdown_choice_and_nothing_else_guesses(space):
    statements = ("I am authorized to work in the United States for any employer",
                  "I am authorized to work in the United States for my present employer only",
                  "I require sponsorship to work in the United States")
    question = _field("Are you legally authorized to work in the United States?", kind="combobox")
    plain = AnswerEntry(question=question.label, answer="Yes")
    config = replace(space.config, answers=AnswersFile(answers=[plain]))
    assert match_option(plan_for(question, config).candidates, statements) is None
    worded = plain.model_copy(update={"choices": [statements[0]]})
    config = replace(space.config, answers=AnswersFile(answers=[worded]))
    plan = plan_for(question, config)
    assert plan.search_text == "Yes"
    assert match_option(plan.candidates, statements) == 0
    assert match_option(plan.candidates, ("Yes", "No")) == 0            # the answer still comes first


def test_graduation_choices_by_term_only_for_graduation_questions(space):
    terms = ("2031 - Fall", "2032 - Spring", "2032 - Fall", "Other")
    plan = plan_for(_field("Expected graduation.", kind="combobox"), space.config)   # profile 2032-05
    assert match_option(plan.candidates, terms, prefix=False) == 1
    answered = plan_for(_field("Expected graduation date", kind="select", options=terms), space.config)
    assert answered.from_answers and match_option(answered.candidates, terms) == 1
    assert graduation_answer_spellings("January 2032") == ("Spring 2032", "2032 - Spring")
    assert graduation_answer_spellings("June 2032") == ("Summer 2032", "2032 - Summer")
    assert graduation_answer_spellings("September 2032") == ("Fall 2032", "2032 - Fall")
    assert graduation_answer_spellings("December 2032") == ("Fall 2032", "2032 - Fall")
    assert graduation_answer_spellings("Company careers page") == ()
    start = AnswerEntry(question="When can you start?", answer="May 2032")
    config = replace(space.config, answers=AnswersFile(answers=[start]))
    assert plan_for(_field("When can you start?", kind="select", options=terms), config).candidates == ("May 2032",)


def test_long_school_and_graduation_questions_fill_only_from_a_list(space):
    config = space.config
    school = ('Which university are you currently attending or did you last attend? Please select '
              '"Other (School Not Listed)" if your school is not listed.')
    schools = ("Example International University", "Example State University", "Other (School Not Listed)")
    assert match_option(plan_for(_field(school, kind="select", options=schools), config).candidates, schools) == 1
    year = ("Please include your intended graduation year for the degree or relevant learning "
            "program that you are currently pursuing or have completed.")
    years = ("2029", "2032", "Other")
    assert match_option(plan_for(_field(year, kind="select", options=years), config).candidates, years) == 1
    # the same long wording as a free-text box is not a profile field
    assert plan_for(_field(year), config) is None
    assert plan_for(_field("Current Program Type"), config) is None
    programs = ("PhD", "Master's Degree", "Bachelor's Degree", "Other")
    plan = plan_for(_field("Current Program Type", kind="radio", options=programs), config)
    assert match_option(plan.candidates, programs) == 2


def test_a_general_major_matches_its_field_general_choice(space):
    profile = space.config.profile.model_copy(
        update={"major": "General Engineering (intended EE)", "discipline": None, "discipline_by_track": {}}
    )
    config = replace(space.config, profile=profile)
    disciplines = ("Electrical Engineering", "Engineering (General)", "Mechanical Engineering")
    plan = plan_for(_field("Discipline", kind="select", options=disciplines), config)
    assert match_option(plan.candidates, disciplines) == 1


def test_discipline_follows_the_jobs_tracks_and_falls_back_to_the_default(space):
    plain, special = space.config.track_names[:2]
    profile = space.config.profile.model_copy(update={
        "discipline": "Electrical Engineering",
        "discipline_by_track": {special: "Computer Engineering"},
    })
    config = replace(space.config, profile=profile)
    choices = ("Computer Engineering", "Electrical Engineering", "Mechanical Engineering")
    field = _field("Discipline", kind="select", options=choices)
    assert match_option(plan_for(field, config).candidates, choices) == 1
    assert match_option(plan_for(field, config, tracks=[plain]).candidates, choices) == 1
    assert match_option(plan_for(field, config, tracks=[plain, special]).candidates, choices) == 0
    # a list with only the combined program still gets it, for either half
    combined = ("Electrical and Computer Engineering", "Mechanical Engineering")
    only = _field("Major", kind="select", options=combined)
    assert match_option(plan_for(only, config, tracks=[special]).candidates, combined) == 0
    assert plan_for(_field("Major"), config).candidates[0] == "Electrical Engineering"


def test_exact_only_matching_ignores_a_unique_prefix():
    assert match_option(["Example State University"], ["Example State University - Northside"]) == 0
    assert match_option(["Example State University"], ["Example State University - Northside"], prefix=False) is None


def test_school_and_degree_pick_my_campus_and_spelling(space):
    profile = space.config.profile.model_copy(update={"school_city": "Arlington"})
    config = replace(space.config, profile=profile)
    campuses = ("Example State University - Northside", "Example State University - Arlington",
                "Example State University - Eastport")
    plan = plan_for(_field("School", kind="select", options=campuses), config)
    assert match_option(plan.candidates, campuses) == 1
    levels = ("High School", "Associate's Degree", "Bachelor of Science", "Master's Degree")
    plan = plan_for(_field("Degree", kind="select", options=levels), config)
    assert match_option(plan.candidates, levels) == 2


def test_form_urls_point_at_the_application_form(space):
    lever = _job(space, "lever")
    lever_url = "https://jobs.lever.co/acme/123"
    assert form_url(_with_url(lever, lever_url), "lever") == lever_url + "/apply"
    assert form_url(_with_url(lever, lever_url + "/apply"), "lever") == lever_url + "/apply"
    ashby_url = "https://jobs.ashbyhq.com/acme/abc"
    assert form_url(_with_url(lever, ashby_url), "ashby") == ashby_url + "/application"
    assert detect_ats("https://job-boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert detect_ats("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/1") == "workday"


def _with_url(job, url: str):
    return replace(job, url=url, apply_url=None)


def test_zero_filled_missing_answer_stays_retryable(space, page):
    from applyai.core import favorites
    from applyai.core.apply.autofill import plan_run

    job = _job(space, "unanswered")
    favorites.favorite(space.conn, [job.id])
    report = fill_job(page, space.conn, space.settings, space.config, job)
    stored = jobs_core.get_job(space.conn, job.id)
    assert report.filled == []
    assert report.unanswered == ["Why this position?"]
    assert stored.fill_state == "partial"
    assert stored.fill_attempted_at is not None and stored.fill_ready_at is None
    assert job.id in plan_run(space.conn, space.config).job_ids
    answer = AnswerEntry(question="Why this position?", answer="My own saved answer.")
    config = replace(space.config, answers=AnswersFile(answers=[answer]))
    retried = fill_job(page, space.conn, space.settings, config, stored, resume_tab=True)
    assert _value(page, "[name=motivation]") == answer.answer
    assert retried.unanswered == []
    assert retried.status == "blocked"  # no verified resume, not falsely ready
    assert "Resume" in retried.note
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _assert_nothing_submitted(page)


def test_workday_steps_resume_manual_choices_and_upload_without_submitting(space, page, monkeypatch):
    from applyai.core.apply import filler
    from applyai.core.apply.autofill import plan_run
    from applyai.core import favorites

    monkeypatch.setattr(filler, "detect_ats", lambda url: "workday")
    job = _job(space, "workday_steps")
    favorites.favorite(space.conn, [job.id])
    first = fill_job(page, space.conn, space.settings, space.config, job)
    assert first.status == "partial"
    assert _js(page, "() => [window.__steps, window.__uploads, window.__submitted]") == [1, 1, 0]
    _js(page, "() => { document.querySelector('[name=shift]').value = 'Evening'; }")
    answer = AnswerEntry(question="Why this position?", answer="My verified answer.")
    config = replace(space.config, answers=AnswersFile(answers=[answer]))
    stored = jobs_core.get_job(space.conn, job.id)
    assert job.id in plan_run(space.conn, config).job_ids
    retained = filler._retained_tab(page.bidi, stored)
    assert retained.context == page.context
    second = fill_job(retained, space.conn, space.settings, config, stored, resume_tab=True)
    assert second.status == "ready"
    assert second.resumed
    assert _value(page, "[name=shift]") == "Evening"
    assert _value(page, "[name=motivation]") == answer.answer
    assert _js(page, "() => [window.__steps, window.__uploads, window.__submitted]") == [2, 1, 0]
    stored = jobs_core.get_job(space.conn, job.id)
    assert stored.fill_state == "ready" and stored.fill_ready_at
    assert stored.application_id is None
    assert job.id not in plan_run(space.conn, config).job_ids


@pytest.mark.parametrize(("markup", "blocker"), [
    ('<form><label>Password<input type="password"></label></form>', "login:"),
    ('<input autocomplete="one-time-code">', "verification:"),
    ('<iframe title="recaptcha challenge" src="about:blank"></iframe>', "captcha:"),
    ('<p>This job is no longer available.</p>', "closed:"),
])
def test_preflight_reports_actual_gate_without_filling(space, page, markup, blocker):
    job = _job(space, "unanswered")
    page.goto(job.url)
    _js(page, "(markup) => { document.body.innerHTML = markup; }", markup)
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == "blocked"
    assert result.note.startswith(blocker)
    assert result.filled == []
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None


def test_passive_captcha_is_not_a_challenge_and_unknown_next_is_not_clicked(space, page):
    from applyai.core.apply.preflight import inspect_page

    job = _job(space, "unanswered")
    page.goto(job.url)
    _js(page, """() => {
        document.body.innerHTML = '<iframe title="reCAPTCHA" src="about:blank"></iframe>' +
            '<input name="email" aria-label="Email"><button type="button">Continue</button>';
        window.clicked = 0;
        document.querySelector('button').onclick = () => window.clicked++;
    }""")
    assert inspect_page(page).blocker is None
    report = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert report.status != "ready"
    assert "Unsupported" in report.note
    assert _js(page, "() => window.clicked") == 0


@pytest.mark.parametrize("label", ["Submit", "Finish", "Complete", "I agree", "Continue and submit"])
def test_workday_never_advances_a_final_or_consent_action(space, page, label):
    from applyai.core.apply.preflight import advance, inspect_page
    from applyai.core.apply.page import UnsafeAction

    page.goto((FORMS / "workday_steps.html").as_uri())
    _js(page, "(label) => { document.querySelector('button').textContent = label; }", label)
    state = inspect_page(page)
    assert not state.safe_next
    with pytest.raises(UnsafeAction):
        advance(state, "workday")
    assert _js(page, "() => [window.__steps, window.__submitted]") == [0, 0]


def test_workday_posting_entry_waits_for_real_gate_without_entering_identity(space, page, monkeypatch):
    from applyai.core.apply import filler

    monkeypatch.setattr(filler, "detect_ats", lambda url: "workday")
    job = _job(space, "workday_entry")
    result = fill_job(page, space.conn, space.settings, space.config, job)
    assert result.status == "blocked" and result.note.startswith("login:")
    assert _value(page, "[name=email]") == ""
    assert _value(page, "[name=password]") == ""
    assert _js(page, "() => [window.__entries, window.__submitted]") == [2, 0]
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None


def test_workday_step_rechecks_final_label_and_ignores_hidden_duplicate(page):
    from applyai.core.apply.preflight import advance, inspect_page
    from applyai.core.apply.page import UnsafeAction

    page.goto((FORMS / "workday_steps.html").as_uri())
    state = inspect_page(page)
    _js(page, "() => { document.querySelector('button').textContent = 'Submit'; }")
    with pytest.raises(UnsafeAction):
        advance(state, "workday")
    assert _js(page, "() => window.__submitted") == 0
    _js(page, """() => {
        document.querySelector('button').textContent = 'Next';
        document.querySelectorAll('[required]').forEach(e => e.required = false);
        const hidden = document.createElement('button');
        hidden.hidden = true; hidden.textContent = 'Submit';
        hidden.setAttribute('data-automation-id', 'bottom-navigation-next-button');
        hidden.onclick = () => window.__submitted++;
        document.body.prepend(hidden);
    }""")
    advance(inspect_page(page), "workday")
    assert _js(page, "() => [window.__steps, window.__submitted]") == [1, 0]


def test_repeated_workday_step_stops_without_claiming_ready(space, page, monkeypatch):
    from applyai.core.apply import filler
    from applyai.core.apply.preflight import inspect_page

    monkeypatch.setattr(filler, "detect_ats", lambda url: "workday")
    monkeypatch.setattr(filler, "wait_for_page", lambda page, **kwargs: inspect_page(page))
    job = _job(space, "workday_entry")
    page.goto(job.url)
    _js(page, "() => { document.querySelector('button').onclick = () => {}; }")
    report = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert report.status == "blocked"
    assert "did not advance" in report.note
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    assert _js(page, "() => window.__submitted") == 0


def test_unavailable_tab_restart_never_claims_manual_values_were_restored(space, page):
    from applyai.core.apply.filler import _retained_tab
    from applyai.core.apply.state import record_attempt

    job = _job(space, "unanswered")
    page.goto(job.url)
    _js(page, "() => { document.querySelector('textarea').value = 'Unsaved manual answer'; }")
    record_attempt(space.conn, job.id, "partial", context_id="missing-context", url=job.url)
    stored = jobs_core.get_job(space.conn, job.id)
    assert _retained_tab(page.bidi, stored) is None
    report = fill_job(page, space.conn, space.settings, space.config, stored)
    assert report.status == "partial"
    assert _value(page, "textarea") == ""
    assert "Manual values are not restored" in report.note
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _assert_nothing_submitted(page)


def test_retained_workday_tab_requires_exact_posting_identity(space):
    from types import SimpleNamespace
    from applyai.core.apply.filler import _retained_tab

    url = "https://company.wd1.myworkdayjobs.com/External/job/City/Role_R-1"
    job = replace(_job(space, "unanswered"), url=url, apply_url=url + "/apply",
                  fill_context_id="saved", fill_url=url + "/apply")
    current = {"context": "saved", "url": url.replace("R-1", "R-10") + "/apply/applyManually"}
    bidi = SimpleNamespace(command=lambda *args: {"contexts": [current]})
    assert _retained_tab(bidi, job) is None
    current["url"] = url + "/apply/applyManually"
    assert _retained_tab(bidi, job).context == "saved"
    current["context"] = "unrelated"
    assert _retained_tab(bidi, job) is None


def _safety_form(space, page):
    from applyai.core.apply.page import settle

    job = _job(space, "application_safety")
    page.goto(job.url)
    settle(page)
    return job


def _attach_fixture_resume(space, page):
    node = page.main.node("() => document.querySelector('[name=resume]')")
    page.main.set_files(node, space.settings.root / "resume.pdf")


@pytest.mark.parametrize("control", [
    '<input type="hidden" name="answer"><button type="button">Yes</button><button type="button">No</button>',
    '<input name="answer" aria-label="Required custom answer">',
])
def test_required_wrapper_must_have_a_readable_validated_value(space, page, control):
    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    _js(page, """(control) => {
        document.querySelector('[name=first_name]').value = 'Manual name';
        const question = document.createElement('div');
        question.setAttribute('role', 'radiogroup');
        question.setAttribute('aria-required', 'true');
        question.innerHTML = control;
        document.querySelector('form').appendChild(question);
    }""", control)
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == "blocked"
    assert result.note.startswith("unsupported:")
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _assert_nothing_submitted(page)


@pytest.mark.parametrize("retained_upload", [False, True])
def test_pending_upload_never_becomes_ready_even_with_local_file_selected(
    space, page, monkeypatch, retained_upload,
):
    from applyai.core.apply import filler
    from applyai.core.apply.page import wait_for_uploads

    job = _safety_form(space, page)
    # Exercise the real pending detector and timeout path without a 30-second test delay.
    monkeypatch.setattr(filler, "wait_for_uploads", lambda tab: wait_for_uploads(tab, timeout_ms=0))
    if retained_upload:
        _attach_fixture_resume(space, page)
        _js(page, "() => { document.querySelector('[name=first_name]').value = 'Manual name'; }")
        page.frames()[1].call("() => { document.querySelector('#upload').hidden = false; }")
    else:
        _js(page, """() => {
            document.querySelector('[name=resume]').onchange = () => {
                const progress = document.createElement('div');
                progress.className = 'resume-upload-working';
                progress.textContent = 'Analyzing resume...';
                document.body.appendChild(progress);
            };
        }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert _files(page, "[name=resume]") == ["resume.pdf"]
    assert result.status == "blocked"
    assert "pending" in result.note
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _assert_nothing_submitted(page)


def test_retained_manual_values_are_protected_before_resume_parser_can_run(space, page):
    job = _safety_form(space, page)
    _js(page, """() => {
        const name = document.querySelector('[name=first_name]');
        name.value = 'My manual answer';
        window.parsed = 0;
        document.querySelector('[name=resume]').onchange = () => {
            window.parsed++;
            name.value = 'Parser replacement';
        };
    }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == "blocked"
    assert "protect retained answers" in result.note
    assert _value(page, "[name=first_name]") == "My manual answer"
    assert _files(page, "[name=resume]") == []
    assert _js(page, "() => window.parsed") == 0
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _assert_nothing_submitted(page)


def test_embedded_validation_error_blocks_main_form_review_readiness(space, page):
    from applyai.core.apply.preflight import inspect_page

    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    _js(page, "() => { document.querySelector('[name=first_name]').value = 'Manual name'; }")
    page.frames()[1].call("() => { document.querySelector('#error').hidden = false; }")
    snapshot = inspect_page(page)
    assert snapshot.frame.context == page.main.context
    assert snapshot.boundary and snapshot.errors > 0
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == "partial"
    assert "validation" in result.note
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _assert_nothing_submitted(page)


def test_workday_active_login_step_is_a_gate_even_before_inputs_render(page):
    from applyai.core.apply.preflight import wait_for_page

    page.goto((FORMS / "workday_entry.html").as_uri())
    _js(page, """() => {
        document.body.innerHTML = '<div data-automation-id="progressBarActiveStep">' +
            'current step 1 of 6 Create Account/Sign In</div><p>Loading...</p>';
    }""")
    state = wait_for_page(page)
    assert state.blocker.startswith("login:")
    assert state.step == "current step 1 of 6 Create Account/Sign In"
    assert not state.has_fields
    assert _js(page, "() => window.__submitted") == 0


def test_workday_waits_for_fields_after_non_auth_step_shell(page):
    from applyai.core.apply.preflight import wait_for_page

    page.goto((FORMS / "workday_steps.html").as_uri())
    _js(page, """() => {
        document.body.innerHTML = '<header>Sign In</header>' +
            '<div data-automation-id="progressBarActiveStep">current step 2 of 6 My Information</div>' +
            '<div id="fields"></div><button type="button" data-automation-id="bottom-navigation-next-button">Next</button>';
        setTimeout(() => {
            document.querySelector('#fields').innerHTML =
                '<label>First Name<input name="first_name" required></label>';
        }, 500);
    }""")
    state = wait_for_page(page, seconds=3)
    assert state.blocker is None  # unrelated header is not an account gate
    assert state.has_fields
    assert state.step == "current step 2 of 6 My Information"
    assert _value(page, "[name=first_name]") == ""
    assert _js(page, "() => [window.__steps, window.__submitted]") == [0, 0]


def test_workday_prefixed_review_stops_without_waiting_for_missing_fields(page):
    from applyai.core.apply.preflight import wait_for_page

    page.goto((FORMS / "workday_steps.html").as_uri())
    _js(page, """() => {
        document.body.innerHTML = '<div data-automation-id="progressBarActiveStep">' +
            'current step 6 of 6 Review</div>' +
            '<button type="button" data-automation-id="bottom-navigation-next-button">Submit</button>';
    }""")
    state = wait_for_page(page)
    assert state.review and not state.safe_next
    assert state.step == "current step 6 of 6 Review"
    assert not state.has_fields
    assert _js(page, "() => [window.__steps, window.__submitted]") == [0, 0]
