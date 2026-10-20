"""The form filler: what it fills, what it leaves, and that it never submits.

Browser tests run in headless Firefox against the fixture forms in
tests/fixtures/forms, and are skipped when Firefox is not installed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from kitty.config import AnswerEntry, AnswersFile, load_config
from kitty.core import jobs as jobs_core
from kitty.core import questions as questions_core
from kitty.core import resumes as resumes_core
from kitty.core.apply.bidi import Tab, open_tab
from kitty.core.apply.browser import headless_firefox
from kitty.core.apply.fields import FormField, graduation_answer_spellings, match_option, plan_for
from kitty.core.apply.filler import _apply_plan, _watched_tab, choose_resume, fill_job, fill_jobs
from kitty.core.apply.page import choose_select, pick_from_list, pick_from_search, read_fields
from kitty.core.apply.urls import detect_ats, form_url
from kitty.settings import Settings, find_firefox

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
    from kitty.core.apply.watch import record_submission

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


@pytest.mark.parametrize("value_class", ["select__single-value", "css-19bb58m-singleValue"])
@pytest.mark.parametrize("search_only", [False, True])
def test_saved_combined_discipline_selects_decorated_ece_and_survives_blur(
    space, page, value_class, search_only,
):
    choice = "ECE - Electrical and Computer Engineering"
    frame, major = _major_picker(
        page, [choice, "Electrical and Computer Engineering Technology"],
        valueClass=value_class, **({"initialChoices": []} if search_only else {}),
    )
    profile = space.config.profile.model_copy(update={
        "major": "Electrical and Computer Engineering", "discipline": None, "discipline_by_track": {},
    })
    plan = plan_for(replace(major, label="Discipline"), replace(space.config, profile=profile))
    assert plan.exact_choices
    assert _apply_plan([frame], major, plan)
    assert _value(page, "#major") == ""
    assert _parent_text(page, "#major") == choice
    assert not next(f for f in read_fields([frame]) if f.key == major.key).blank
    assert _js(page, "() => document.activeElement.id") != "major"
    assert _js(page, "() => window.__unrelatedClicks") == 0
    _assert_nothing_submitted(page)


@pytest.mark.parametrize("settings", [
    {"commit": False}, {"commit": "query"}, {"rejectOnBlur": True},
])
def test_typed_discipline_is_not_selection_and_failed_click_clears_query(space, page, settings):
    choice = "ECE - Electrical and Computer Engineering"
    frame, major = _major_picker(page, [choice], **settings)
    assert not pick_from_search(
        frame, major.key, "Electrical and Computer Engineering",
        ("Electrical and Computer Engineering",), location=False, prefix=False,
    )
    assert _value(page, "#major") == ""
    assert next(f for f in read_fields([frame]) if f.key == major.key).blank
    _assert_nothing_submitted(page)


def test_read_fields_does_not_treat_closed_typed_query_as_selected(page):
    frame, major = _major_picker(page, ["ECE - Electrical and Computer Engineering"])
    _js(page, "() => { document.querySelector('#major').value = 'Electrical and Computer Engineering'; }")
    assert next(f for f in read_fields([frame]) if f.key == major.key).blank
    _assert_nothing_submitted(page)


@pytest.mark.parametrize("choices", [
    ["ECE - Electrical and Computer Engineering", "ECE — Electrical and Computer Engineering"],
    ["Electrical and Computer Engineering", "Electrical and Computer Engineering"],
    ["Electrical and Computer Engineering Technology"],
    ["XYZ - Electrical and Computer Engineering"],
])
def test_discipline_refuses_ambiguous_or_different_programs(space, page, choices):
    frame, major = _major_picker(page, choices)
    profile = space.config.profile.model_copy(update={
        "major": "Electrical and Computer Engineering", "discipline": None, "discipline_by_track": {},
    })
    plan = plan_for(major, replace(space.config, profile=profile))
    assert not _apply_plan([frame], major, plan)
    assert _value(page, "#major") == ""
    assert next(f for f in read_fields([frame]) if f.key == major.key).blank
    assert _js(page, "() => window.__unrelatedClicks") == 0
    _assert_nothing_submitted(page)


def test_disabled_custom_discipline_is_not_an_available_choice(page):
    choice = "ECE - Electrical and Computer Engineering"
    frame, major = _major_picker(page, [choice], disabledChoices=[choice])
    assert not pick_from_list(frame, major.key, ("Electrical and Computer Engineering",), location=False)
    assert _value(page, "#major") == ""
    assert next(f for f in read_fields([frame]) if f.key == major.key).blank
    _assert_nothing_submitted(page)


@pytest.mark.parametrize("replace_after_check", [False, True])
def test_stale_or_replaced_discipline_option_clears_query(page, monkeypatch, replace_after_check):
    from kitty.core.apply import page as page_core

    choice = "ECE - Electrical and Computer Engineering"
    frame, major = _major_picker(page, [choice])
    _js(page, """() => {
        const input = document.querySelector('#major');
        input.value = 'Electrical and Computer Engineering';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        window.__replacementClicks = 0;
    }""")
    original_call = type(frame).call
    if replace_after_check:
        def replace_option(self, fn, *args, **kwargs):
            result = original_call(self, fn, *args, **kwargs)
            if fn == page_core._SUGGESTIONS_JS and len(args) > 2 and args[2] == "check":
                _js(page, """() => {
                    const old = document.querySelector('#major-listbox [role=option]');
                    const replacement = old.cloneNode(true);
                    replacement.onclick = () => window.__replacementClicks++;
                    old.replaceWith(replacement);
                }""")
            return result
        monkeypatch.setattr(type(frame), "call", replace_option)
    else:
        _js(page, "() => document.querySelector('#major-listbox [role=option]').textContent = 'Mechanical Engineering'")
    assert not page_core._click_match(
        frame, major.key, [choice], ("Electrical and Computer Engineering",),
        location=False, prefix=False,
    )
    assert _value(page, "#major") == ""
    assert _js(page, "() => window.__replacementClicks") == 0
    _assert_nothing_submitted(page)


@pytest.mark.parametrize("reject_on_blur", [False, True])
def test_native_discipline_selects_unique_decorated_ece_and_checks_blur(
    space, page, reject_on_blur,
):
    page.goto((FORMS / "greenhouse.html").as_uri())
    _js(page, """(reject) => {
        const old = document.querySelector('#major').parentElement;
        old.innerHTML = `<select id="major" aria-label="Discipline">
          <option value="">Select...</option>
          <option value="wrong">Electrical and Computer Engineering Technology</option>
          <option value="ece">ECE - Electrical and Computer Engineering</option>
          <option value="disabled" disabled>Electrical and Computer Engineering</option>
        </select>`;
        if (reject) document.querySelector('#major').onblur = e => { e.target.value = ''; };
    }""", reject_on_blur)
    field = next(f for f in read_fields([page.main]) if f.element_id == "major")
    profile = space.config.profile.model_copy(update={
        "major": "Electrical and Computer Engineering", "discipline": None, "discipline_by_track": {},
    })
    assert "Electrical and Computer Engineering" not in field.options
    plan = plan_for(field, replace(space.config, profile=profile))
    assert _apply_plan([page.main], field, plan) is not reject_on_blur
    assert _value(page, "#major") == ("" if reject_on_blur else "ece")
    _assert_nothing_submitted(page)


def test_native_select_refuses_duplicate_visible_options(page):
    page.goto((FORMS / "greenhouse.html").as_uri())
    _js(page, """() => {
        document.querySelector('#gender').innerHTML =
          '<option value="">Select...</option><option value="a">Same</option><option value="b">Same</option>';
    }""")
    field = next(f for f in read_fields([page.main]) if f.element_id == "gender")
    assert not choose_select(page.main, field.key, "Same")
    assert _value(page, "#gender") == ""
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


def test_option_matching_prefers_exact_candidates_before_prefixes_and_decorations():
    assert match_option(["Yes", "No"], ["Yes, sometimes", "No"]) == 1
    options = ["ECE - Electrical and Computer Engineering", "Electrical and Computer Engineering"]
    assert match_option(["Electrical and Computer Engineering"], options, prefix=False) == 1
    assert match_option(["Electrical Engineering", "Electrical and Computer Engineering"],
                        ["Electrical and Computer Engineering", "Electrical Engineering"]) == 1
    assert match_option(["Electrical Engineering", "Electrical and Computer Engineering"],
                        ["Electrical and Computer Engineering", "EE - Electrical Engineering"],
                        prefix=False) == 1


@pytest.mark.parametrize("option", [
    "ECE - Electrical and Computer Engineering",
    "ECE — Electrical and Computer Engineering",
    "Electrical and Computer Engineering (ECE)",
])
def test_option_acronym_decoration_requires_equivalent_complete_name(option):
    major = "Electrical and Computer Engineering"
    assert match_option([major], [option], prefix=False) == 0
    assert match_option([option], [major], prefix=False) == 0
    assert match_option(["Electrical Engineering"], [option], prefix=False) is None
    assert match_option([major], ["XYZ - " + major], prefix=False) is None
    assert match_option([major], ["ECE - " + major + " Technology"], prefix=False) is None
    assert match_option([major], ["Unrelated " + major], prefix=False) is None


def test_duplicate_exact_and_decorated_options_are_ambiguous():
    major = "Electrical and Computer Engineering"
    assert match_option([major], [major, major]) is None
    assert match_option([major], ["ECE - " + major, major + " (ECE)"], prefix=False) is None
    assert match_option(["Yes", "No"], ["Yes", "YES", "No"]) is None


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
    from kitty.core import favorites
    from kitty.core.apply.autofill import plan_run

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
    from kitty.core.apply import filler
    from kitty.core.apply.autofill import plan_run
    from kitty.core import favorites

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
    ('<p>Unfortunately this position has been closed but you can search our open jobs.</p>', "closed:"),
    ('<h1>Page not found</h1>', "closed:"),
])
def test_preflight_reports_actual_gate_without_filling(space, page, markup, blocker):
    job = _job(space, "unanswered")
    page.goto(job.url)
    _js(page, "(markup) => { document.body.innerHTML = markup; }", markup)
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == "blocked"
    assert result.note.startswith(blocker)
    assert result.filled == []
    stored = jobs_core.get_job(space.conn, job.id)
    assert stored.fill_ready_at is None
    assert bool(stored.is_active) == (not blocker.startswith("closed:"))


def test_closed_posting_does_not_follow_similar_jobs_or_profile_entry(space, page):
    job = _safety_form(space, page)
    _js(page, """() => {
        const form = document.querySelector('form');
        form.insertAdjacentHTML('beforebegin', `<h1>Patient Services Specialist I</h1>
            <p>Thanks for your interest in this position.
            Unfortunately this position has been closed but you can search our open jobs
            by <a href="/jobs">clicking here</a>.</p>
            <a href="/profile">Go to Profile</a>
            <h2>Similar Listings</h2><a href="/jobs/a-different-role">Apply Now</a>`);
        window.__listingClicks = 0;
        document.querySelectorAll('a').forEach(e =>
            e.addEventListener('click', event => { event.preventDefault(); window.__listingClicks++; }));
    }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == "blocked"
    assert result.note.startswith("closed:")
    assert result.filled == []
    assert _value(page, "[name=first_name]") == ""
    assert _files(page, "[name=resume]") == []
    assert _js(page, "() => window.__listingClicks") == 0
    _assert_nothing_submitted(page)


def test_related_closed_posting_notice_does_not_close_current_job(space, page):
    job = _safety_form(space, page)
    _js(page, """() => document.body.insertAdjacentHTML('beforeend',
        '<aside><h2>This position has been closed</h2></aside>')""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert not (result.note or "").startswith("closed:")
    assert jobs_core.get_job(space.conn, job.id).is_active


def test_terminal_notice_on_another_posting_does_not_close_original_job(space, page):
    job = _job(space, "unanswered")
    page.goto(job.url)
    _js(page, """() => {
      history.replaceState(null, '', '/jobs/a-different-role');
      document.body.innerHTML = '<h1>This job is no longer available</h1>';
    }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == "blocked" and result.note.startswith("closed:")
    assert jobs_core.get_job(space.conn, job.id).is_active


@pytest.mark.parametrize("url", [
    "https://zapply.jobs/jobs/",
    "https://zapply.jobs/jobs",
    "https://zapply.jobs/jobs/?page=2",
])
def test_generic_aggregator_listing_stops_before_form_inspection(url):
    from types import SimpleNamespace
    from kitty.core.apply.preflight import advance, inspect_page
    from kitty.core.apply.page import UnsafeAction

    def unexpected_call(*args, **kwargs):
        pytest.fail("A generic listing must not be inspected as an employer application")

    frame = SimpleNamespace(url=url, call=unexpected_call)
    snapshot = inspect_page(SimpleNamespace(frames=lambda: [frame]))
    assert snapshot.blocker.startswith("listing:")
    assert "exact posting" in snapshot.blocker
    assert not snapshot.has_fields
    assert not snapshot.entry
    with pytest.raises(UnsafeAction):
        advance(snapshot, "other")


@pytest.mark.parametrize("url", [
    "https://zapply.jobs/jobs/exact-posting",
    "https://zapply.jobs/",
    "https://not-zapply.jobs/jobs/",
    "https://zapply.jobs.example.invalid/jobs/",
    "https://employer.example/jobs/",
    "http://zapply.jobs/jobs/",
])
def test_listing_diagnosis_is_not_guessed_for_other_pages(url):
    from kitty.core.apply.preflight import _listing_blocker

    assert _listing_blocker(url) is None


def test_passive_captcha_is_not_a_challenge_and_unknown_next_is_not_clicked(space, page):
    from kitty.core.apply.preflight import inspect_page

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
    from kitty.core.apply.preflight import advance, inspect_page
    from kitty.core.apply.page import UnsafeAction

    page.goto((FORMS / "workday_steps.html").as_uri())
    _js(page, "(label) => { document.querySelector('button').textContent = label; }", label)
    state = inspect_page(page)
    assert not state.safe_next
    with pytest.raises(UnsafeAction):
        advance(state, "workday")
    assert _js(page, "() => [window.__steps, window.__submitted]") == [0, 0]


def test_workday_posting_entry_waits_for_real_gate_without_entering_identity(space, page, monkeypatch):
    from kitty.core.apply import filler

    monkeypatch.setattr(filler, "detect_ats", lambda url: "workday")
    job = _job(space, "workday_entry")
    result = fill_job(page, space.conn, space.settings, space.config, job)
    assert result.status == "blocked"
    assert _value(page, "[name=email]") == ""
    assert _value(page, "[name=password]") == ""
    assert _js(page, "() => [window.__entries, window.__submitted]") == [2, 0]
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None


def test_workday_step_rechecks_final_label_and_ignores_hidden_duplicate(page):
    from kitty.core.apply.preflight import advance, inspect_page
    from kitty.core.apply.page import UnsafeAction

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
    from kitty.core.apply import filler
    from kitty.core.apply.preflight import inspect_page

    monkeypatch.setattr(filler, "detect_ats", lambda url: "workday")
    monkeypatch.setattr(filler, "wait_for_page", lambda page, **kwargs: inspect_page(page))
    job = _job(space, "workday_entry")
    page.goto(job.url)
    _js(page, "() => { window.__entryAttempts = 0; document.querySelector('button').onclick = () => window.__entryAttempts++; }")
    report = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert report.status == "blocked"
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    assert _js(page, "() => [window.__entryAttempts, window.__submitted]") == [1, 0]


def test_unavailable_tab_restart_never_claims_manual_values_were_restored(space, page):
    from kitty.core.apply.filler import _retained_tab
    from kitty.core.apply.state import record_attempt

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
    from kitty.core.apply.filler import _retained_tab

    url = "https://company.wd1.myworkdayjobs.com/External/job/City/Role_R-1"
    job = replace(_job(space, "unanswered"), url=url, apply_url=url + "/apply",
                  fill_context_id="saved", fill_url=url + "/apply")
    current = {"context": "saved", "url": url.replace("R-1", "R-10") + "/apply/applyManually"}
    bidi = SimpleNamespace(command=lambda method, *args, **kwargs:
        {"contexts": [current]} if method == "browsingContext.getTree" else
        {"result": {"type": "string", "value": "null"}})
    assert _retained_tab(bidi, job) is None
    current["url"] = url + "/apply/applyManually"
    assert _retained_tab(bidi, job).context == "saved"
    current["context"] = "unrelated"
    assert _retained_tab(bidi, job) is None

    from urllib.parse import quote
    job = replace(job, fill_blocker="verification: verify your account")
    current["context"] = "saved"
    login = "https://company.wd1.myworkdayjobs.com/External/login?redirect="
    for destination, expected in [
        ("/en-US/External/job/City/Role_R-1/apply/applyManually", True),
        ("/en-US/External/job/City/Role_R-10/apply/applyManually", False),
        ("https://other.example/job/City/Role_R-1/apply", False),
        ("//other.example/job/City/Role_R-1/apply", False),
    ]:
        current["url"] = login + quote(destination, safe="")
        assert (_retained_tab(bidi, job) is not None) is expected
    current["url"] = login + quote("/External/job/City/Role_R-1/apply", safe="") + "&redirect="
    assert _retained_tab(bidi, job) is None


def _safety_form(space, page):
    from kitty.core.apply.page import settle

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


def test_required_native_upload_inherits_group_label_and_requiredness(space, page):
    from kitty.core.apply.page import form_frames, read_fields
    from kitty.core.apply.preflight import inspect_page

    job = _safety_form(space, page)
    _js(page, """() => {
        const input = document.querySelector('[name=resume]');
        input.parentElement.outerHTML = `<div role="group" aria-labelledby="upload-label-resume" aria-required="true">
            <div id="upload-label-resume">Resume/CV<span class="required">*</span></div>
            <div class="file-upload__wrapper"><div class="button-container">
                <div class="secondary-button"><div>
                    <label for="resume">Attach</label>
                    <input id="resume" name="resume" type="file" style="position:absolute;opacity:0" accept=".pdf">
                </div></div>
            </div></div></div>`;
    }""")
    upload = next(field for field in read_fields(form_frames(page)) if field.kind == 'file')
    assert upload.required
    assert 'Resume/CV' in upload.label
    assert not inspect_page(page).unsupported_required
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert _value(page, '[name=first_name]') == 'Pat'
    assert _files(page, '[name=resume]') == ['resume.pdf']
    assert result.status == 'ready'
    _assert_nothing_submitted(page)


def test_greenhouse_aria_fields_fill_and_unknown_question_stays_manual(space, page):
    from kitty.core.apply.page import form_frames
    from kitty.core.apply.preflight import inspect_page

    job = _safety_form(space, page)
    _js(page, """() => {
        const first = document.querySelector('[name=first_name]');
        first.required = false;
        first.setAttribute('aria-required', 'true');
        const input = document.querySelector('[name=resume]');
        input.parentElement.outerHTML = `<div class="file-upload" role="group"
            aria-labelledby="upload-label-resume" aria-required="true">
            <div class="label upload-label" id="upload-label-resume">Resume/CV<span class="required">*</span></div>
            <div class="file-upload__wrapper"><div class="button-container"><div class="secondary-button">
                <button type="button">Attach</button><label for="resume" style="position:absolute;clip:rect(0,0,0,0)">Attach</label>
                <input id="resume" name="resume" type="file" style="position:absolute;width:1px;height:1px;clip:rect(0,0,0,0)">
            </div><button type="button">Enter manually</button></div></div></div>`;
        document.querySelector('form').insertAdjacentHTML('beforeend', `
            <label for="last_name">Last Name</label>
            <input id="last_name" aria-label="Last Name" aria-required="true">
            <label id="onsite-label" for="onsite">Are you comfortable being onsite five days a week?</label>
            <div class="select__control"><input id="onsite" role="combobox" aria-required="true"
                aria-expanded="false" aria-labelledby="onsite-label" autocomplete="off"></div>`);
    }""")
    fields = read_fields(form_frames(page))
    upload_field = next(field for field in fields if field.kind == "file")
    assert upload_field.required and upload_field.label == "Resume/CV"
    assert not inspect_page(page).unsupported_required
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert _value(page, "[name=first_name]") == "Pat"
    assert _value(page, "#last_name") == "Tester"
    assert _files(page, "#resume") == ["resume.pdf"]
    assert _value(page, "#onsite") == ""
    assert result.status == "partial"
    assert result.unanswered == ["Are you comfortable being onsite five days a week?"]
    assert result.unanswered[0] in _logged(space)
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _js(page, "() => { document.querySelector('#last_name').value = 'Manual surname'; }")
    retained = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert retained.status == "partial"
    assert _value(page, "#last_name") == "Manual surname"
    assert _files(page, "#resume") == ["resume.pdf"]
    assert _value(page, "#onsite") == ""
    _assert_nothing_submitted(page)


def _replacing_resume_upload(page):
    _js(page, """() => {
        const input = document.querySelector('[name=resume]');
        input.parentElement.outerHTML = `<div class="file-upload" role="group"
            aria-labelledby="upload-label-resume" aria-required="true">
            <div id="upload-label-resume">Resume/CV<span class="required">*</span></div>
            <div class="file-upload__wrapper"><label>Attach
              <input id="resume" name="resume" type="file"></label></div></div>`;
        document.querySelector('[name=resume]').addEventListener('change', event => {
            if (!event.target.files.length) return;
            const wrapper = event.target.closest('.file-upload__wrapper');
            const name = event.target.files[0].name;
            wrapper.innerHTML = `<div class="file-upload__filename">
                <p class="body body__secondary"></p>
                <button type="button" aria-label="Remove file">Remove</button></div>`;
            wrapper.querySelector('p').textContent = name;
        });
    }""")


def test_uploaded_resume_receipt_survives_removed_input_and_resume(space, page):
    job = _safety_form(space, page)
    _replacing_resume_upload(page)
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == 'ready'
    assert _js(page, "() => document.querySelector('.file-upload__filename p').textContent") == 'resume.pdf'
    assert _js(page, "() => document.querySelector('input[type=file]')") is None
    assert result.resume_id is not None
    _js(page, "() => document.querySelector('[name=first_name]').value = 'Manual retained name'")
    retained = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert retained.status == 'ready'
    assert _value(page, '[name=first_name]') == 'Manual retained name'
    _assert_nothing_submitted(page)


@pytest.mark.parametrize('state', ['pending', 'error', 'missing-filename', 'missing-remove'])
def test_incomplete_upload_receipt_cannot_prove_resume_readiness(space, page, state):
    job = _safety_form(space, page)
    _replacing_resume_upload(page)
    _attach_fixture_resume(space, page)
    _js(page, """state => {
        const group = document.querySelector('.file-upload');
        if (state === 'pending') group.insertAdjacentHTML('beforeend', '<div aria-busy="true">Uploading</div>');
        if (state === 'error') group.insertAdjacentHTML('beforeend', '<p class="helper-text--error">Upload failed</p>');
        if (state === 'missing-filename') group.querySelector('.file-upload__filename p').textContent = '';
        if (state === 'missing-remove') group.querySelector('button').remove();
    }""", state)
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == 'blocked'
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    _assert_nothing_submitted(page)


def test_unknown_required_widget_does_not_prevent_known_fields_and_resume(space, page):
    job = _safety_form(space, page)
    _js(page, """() => {
        const question = document.createElement('div');
        question.setAttribute('role', 'radiogroup');
        question.setAttribute('aria-required', 'true');
        question.innerHTML = '<p>Employer-specific required choice</p><button type="button">One</button><button type="button">Two</button>';
        window.__customChoices = 0;
        question.onclick = () => window.__customChoices++;
        document.querySelector('form').appendChild(question);
    }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert _value(page, '[name=first_name]') == 'Pat'
    assert _files(page, '[name=resume]') == ['resume.pdf']
    assert result.status == 'blocked'
    assert result.note.startswith('unsupported:')
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    assert _js(page, '() => window.__customChoices') == 0
    _assert_nothing_submitted(page)


def test_new_required_custom_widget_prevents_step_advance(page):
    from kitty.core.apply.preflight import advance, inspect_page
    from kitty.core.apply.page import UnsafeAction

    page.goto((FORMS / 'workday_steps.html').as_uri())
    _js(page, "() => document.querySelectorAll('[required]').forEach(e => e.required = false)")
    reviewed = inspect_page(page)
    _js(page, """() => {
        const control = document.createElement('div');
        control.setAttribute('role', 'radiogroup');
        control.setAttribute('aria-required', 'true');
        control.innerHTML = '<button type="button">Unrecognized choice</button>';
        document.body.appendChild(control);
    }""")
    with pytest.raises(UnsafeAction):
        advance(reviewed, 'workday')
    assert inspect_page(page).unsupported_required
    assert _js(page, '() => [window.__steps, window.__submitted]') == [0, 0]


@pytest.mark.parametrize('disconnect', [False, True])
def test_later_browser_failure_preserves_completed_job_reports(space, monkeypatch, disconnect):
    from types import SimpleNamespace
    from kitty.core.apply import filler
    from kitty.core.apply.bidi import ConnectionLost, TabClosed

    postings = [_job(space, name) for name in ('greenhouse', 'lever', 'unanswered')]
    opened = []
    prepared = []

    def open_next(*args, **kwargs):
        opened.append(len(opened))
        if len(opened) == 2:
            error = ConnectionLost if disconnect else TabClosed
            raise error('browser disconnected' if disconnect else 'no such frame: closed tab')
        return SimpleNamespace(context=str(len(opened)), activate=lambda: None)

    def prepare(tab, conn, settings, config, job, **kwargs):
        prepared.append(job.id)
        return filler.FillReport(job.id, job.company_name, job.title, job.url,
                                 'greenhouse', 'general', ['First Name', 'resume'], [], 'ready')

    monkeypatch.setattr(filler, 'open_tab', open_next)
    monkeypatch.setattr(filler, 'fill_job', prepare)
    reports = filler._fill_all(
        SimpleNamespace(redact=lambda value: value), space.conn, space.settings,
        space.config, [job.id for job in postings], lambda message: None,
        keep_open=True, pause=0,
    )
    assert [report.job_id for report in reports] == [job.id for job in postings]
    assert reports[0].status == 'ready'
    assert reports[1].status == 'error'
    if disconnect:
        assert prepared == [postings[0].id]
        assert reports[2].status == 'error'
        assert 'before this job' in reports[2].error
    else:
        assert prepared == [postings[0].id, postings[2].id]
        assert reports[2].status == 'ready'


@pytest.mark.parametrize("retained_upload", [False, True])
def test_pending_upload_never_becomes_ready_even_with_local_file_selected(
    space, page, monkeypatch, retained_upload,
):
    from kitty.core.apply import filler
    from kitty.core.apply.page import wait_for_uploads

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
    from kitty.core.apply.preflight import inspect_page

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


def test_workday_waits_for_account_fields_after_progress_shell(page):
    from kitty.core.apply.preflight import inspect_page, wait_for_page

    page.goto((FORMS / "workday_entry.html").as_uri())
    _js(page, """() => {
        document.body.innerHTML = '<div data-automation-id="progressBarActiveStep">' +
            'current step 1 of 6 Create Account/Sign In</div><div id="fields">Loading...</div>';
    }""")
    initial = inspect_page(page)
    assert initial.blocker is None
    assert not initial.has_fields
    _js(page, """() => {
        setTimeout(() => {
            document.querySelector('#fields').innerHTML =
                '<label>Email<input type="email"></label>' +
                '<label>Password<input type="password"></label><button>Sign In</button>';
        }, 300);
    }""")
    state = wait_for_page(page, seconds=3)
    assert state.blocker.startswith("login:")
    assert state.has_fields
    assert _js(page, "() => window.__submitted") == 0


def test_workday_waits_for_fields_after_non_auth_step_shell(page):
    from kitty.core.apply.preflight import wait_for_page

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
    from kitty.core.apply.preflight import wait_for_page

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


def _approval(space, job):
    from kitty.core.apply.filler import FillApproval
    resume = choose_resume(space.conn, space.config, job)
    return FillApproval(job.id, job.url, job.apply_url, job.company_name, tuple(job.tracks),
                        resume.id, resume.file_hash, resume.file_path)


@pytest.mark.parametrize("change", ["missing", "bytes", "record", "url"])
def test_approved_fill_fails_before_browser_if_inputs_changed(space, monkeypatch, change):
    from kitty.core.apply import filler
    job = _job(space, "greenhouse")
    approval = _approval(space, job)
    approvals = {job.id: approval}
    if change == "missing":
        approvals.clear()
    elif change == "bytes":
        Path(approval.file_path).write_bytes(b"%PDF-1.4\n% changed resume\n")
    elif change == "record":
        replacement = space.settings.root / "replacement.pdf"
        replacement.write_bytes(b"%PDF-1.4\n% different registered resume\n")
        resumes_core.add_resume(space.conn, space.track.default_resume, replacement)
    else:
        space.conn.execute("UPDATE jobs SET apply_url=? WHERE id=?",
                           ("https://example.invalid/changed", job.id))
    monkeypatch.setattr(filler, "ensure_firefox", lambda *a: pytest.fail("Must reject before browser"))
    with pytest.raises(filler.ApprovalError, match="review the plan again"):
        filler.fill_jobs(space.settings, space.config, [job.id], approvals=approvals)


def test_approved_fill_uploads_only_reviewed_bytes_after_default_changes(space, page):
    job = _job(space, "greenhouse")
    approval = _approval(space, job)
    expected = Path(approval.file_path).read_bytes()
    second = space.settings.root / "new-default.pdf"
    second.write_bytes(b"%PDF-1.4\n% different default resume\n")
    replacement = resumes_core.add_resume(space.conn, "New default", second)
    space.conn.execute("INSERT INTO resume_default_overrides (track, resume_id) VALUES (?, ?)",
                       (space.track.name, replacement.id))
    report = fill_job(page, space.conn, space.settings, space.config, job, approval=approval)
    assert report.resume_id == approval.resume_id
    assert jobs_core.get_job(space.conn, job.id).resume_id == approval.resume_id
    content = page.main.call("""async () => Array.from(new Uint8Array(
        await document.querySelector('input[type=file]').files[0].arrayBuffer()
    ))""", page_realm=True)
    assert bytes(content) == expected
    assert _watched_tab(job, page, report).resume_id == approval.resume_id
    _assert_nothing_submitted(page)


@pytest.mark.parametrize("change", ["changed", "missing", "symlink", "corrupt_snapshot"])
def test_approved_upload_checks_source_and_snapshot_at_upload(space, monkeypatch, change):
    from kitty.core.apply import filler
    job = _job(space, "greenhouse")
    approval = _approval(space, job)
    path = Path(approval.file_path)
    if change == "changed":
        path.write_bytes(b"%PDF-1.4\n% changed after browser began\n")
    elif change == "missing":
        path.unlink()
    else:
        folder = space.settings.data_dir / "approved_resumes"
        folder.mkdir(parents=True)
        frozen = folder / f"{approval.file_hash}.pdf"
        if change == "symlink":
            frozen.symlink_to(path)
        else:
            frozen.write_bytes(b"not the reviewed bytes")
    monkeypatch.setattr(filler, "upload", lambda *a: pytest.fail("Unapproved bytes must not upload"))
    with pytest.raises(filler.ApprovalError, match="review the plan again"):
        filler._upload_approved(None, "resume-input", path, approval, space.conn, space.settings)


def test_firefox_start_requires_desktop_but_reuses_existing_browser(space, monkeypatch):
    from kitty.core.apply import browser

    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(browser, "is_listening", lambda port: False)
    monkeypatch.setattr(browser, "_start", lambda *args: pytest.fail("No desktop: do not spawn Firefox"))
    settings = replace(space.settings, firefox_path="/system/firefox")
    with pytest.raises(browser.BrowserError, match="active desktop display"):
        browser.ensure_firefox(settings)
    monkeypatch.setattr(browser, "is_listening", lambda port: True)
    assert browser.ensure_firefox(settings) == settings.browser_port


@pytest.mark.parametrize("markup", [
    '<input id="choice" type="checkbox" style="display:none">'
    '<label for="choice">Available for relocation?</label>',
    '<button id="choice" type="button" role="checkbox" aria-checked="false" '
    'onclick="this.setAttribute(\'aria-checked\', this.getAttribute(\'aria-checked\') !== \'true\')">'
    'Available for relocation?</button>',
    '<input id="choice" type="button" role="checkbox" aria-checked="false" '
    'value="Available for relocation?" '
    'onclick="this.setAttribute(\'aria-checked\', this.getAttribute(\'aria-checked\') !== \'true\')">',
    '<div id="choice" tabindex="0" role="checkbox" aria-checked="false" '
    'onclick="this.setAttribute(\'aria-checked\', this.getAttribute(\'aria-checked\') !== \'true\')">'
    'Available for relocation?</div>',
])
def test_explicit_choices_use_real_pointer_actions_without_touching_submit(page, markup):
    from urllib.parse import quote
    from kitty.core.apply.page import tick

    page.goto("data:text/html," + quote(
        '<form onsubmit="window.submitted++;return false">' + markup +
        '<button type="submit">Submit application</button></form>'
        '<script>window.submitted=0;window.trusted=[];'
        'document.querySelector("#choice").addEventListener("click", e => {'
        'window.trusted.push(e.isTrusted);if (!e.isTrusted) e.preventDefault();'
        '});</script>'))
    choice = next(f for f in read_fields([page.main]) if f.element_id == "choice")
    assert choice.kind == "checkbox" and choice.blank
    assert tick(page.main, choice.key)
    assert not next(f for f in read_fields([page.main]) if f.element_id == "choice").blank
    assert _js(page, "() => window.trusted") == [True]
    assert _js(page, "() => window.submitted") == 0
    # Checking an already checked control must not toggle it back.
    assert tick(page.main, choice.key)
    assert _js(page, "() => window.trusted") == [True]


@pytest.mark.parametrize("role", ["checkbox", "radio"])
def test_required_aria_group_has_one_named_question_with_selectable_options(page, role):
    from urllib.parse import quote
    from kitty.core.apply.page import tick

    group_role = "radiogroup" if role == "radio" else "group"
    page.goto("data:text/html," + quote(
        f'<div role="{group_role}" aria-required="true" aria-labelledby="question">'
        '<span id="question">Available for relocation?</span>'
        f'<div id="yes" tabindex="0" role="{role}" aria-checked="false" '
        'onclick="this.setAttribute(\'aria-checked\', \'true\')">Yes</div>'
        f'<div role="{role}" tabindex="0" aria-checked="false">No</div></div>'))
    fields = read_fields([page.main])
    assert len(fields) == 1
    field = fields[0]
    assert field.kind == role and field.required and field.blank
    assert field.label == "Available for relocation?"
    assert field.options == ("Yes", "No")
    assert tick(page.main, field.option_keys[0])
    assert not read_fields([page.main])[0].blank


def test_disguised_submit_never_becomes_an_aria_choice(page):
    from urllib.parse import quote
    from kitty.core.apply.page import UnsafeAction, tick

    page.goto("data:text/html," + quote(
        '<form onsubmit="window.submitted++;return false">'
        '<button id="choice" type="button" role="checkbox" aria-checked="false">Available?</button>'
        '<button id="final" type="submit" role="checkbox" aria-checked="false">Submit application</button>'
        '</form><script>window.submitted=0;</script>'))
    fields = read_fields([page.main])
    assert not any(f.element_id == "final" for f in fields)
    choice = next(f for f in fields if f.element_id == "choice")
    _js(page, "() => { document.querySelector('#choice').type = 'submit'; }")
    with pytest.raises(UnsafeAction):
        tick(page.main, choice.key)
    assert _js(page, "() => window.submitted") == 0


def test_state_backed_aria_combobox_supports_automatic_and_manual_selection(page):
    from urllib.parse import quote

    page.goto("data:text/html," + quote("""
      <form onsubmit="window.submitted++;return false">
        <span id="question">Available for relocation?</span>
        <button type="button" id="combo" role="combobox" aria-labelledby="question"
          aria-controls="choices" aria-expanded="false">Choose...</button>
        <div id="choices" role="listbox" hidden>
          <div role="option" aria-selected="false"><span>Yes</span></div>
          <div role="option" aria-selected="false"><span>No</span></div>
        </div>
        <button type="submit">Submit application</button>
      </form>
      <script>
        window.submitted=0;window.trusted=[];
        const combo=document.querySelector('#combo'), list=document.querySelector('#choices');
        combo.onclick=() => {
          list.hidden=!list.hidden;
          combo.setAttribute('aria-expanded', String(!list.hidden));
        };
        for (const option of list.children) option.onclick=event => {
          window.trusted.push(event.isTrusted);
          for (const other of list.children) other.setAttribute('aria-selected', String(other===option));
          combo.textContent=option.textContent; list.hidden=true;
          combo.setAttribute('aria-expanded', 'false');
        };
      </script>
    """))
    field = next(f for f in read_fields([page.main]) if f.element_id == "combo")
    assert field.kind == "combobox" and field.blank
    assert pick_from_list(page.main, field.key, ("Yes",), location=False)
    assert not next(f for f in read_fields([page.main]) if f.element_id == "combo").blank
    # The guarded automation leaves the same controls usable by trusted manual input.
    for selector in ("#combo", "#choices [role=option]:last-child span"):
        target = page.main.node("(s) => document.querySelector(s)", selector)
        assert target is not None
        page.main.click(target)
    assert _js(page, "() => document.querySelector('#combo').textContent") == "No"
    assert _js(page, "() => window.trusted") == [True, True]
    assert _js(page, "() => window.submitted") == 0


@pytest.mark.parametrize(("legal_markup", "question", "answer"), [
    ('<label>I accept the privacy policy<input type="checkbox" id="legal" required></label>',
     "I accept the privacy policy", "Yes"),
    ('<fieldset><legend>Data choice</legend><label><input type="radio" name="legal" required>'
     'I accept the privacy policy</label><label><input type="radio" name="legal">No</label></fieldset>',
     "Data choice", "I accept the privacy policy"),
    ('<button id="legal" type="button" role="checkbox" aria-required="true" aria-checked="false" '
     'onclick="this.setAttribute(\'aria-checked\', \'true\')">I accept the privacy policy</button>',
     "I accept the privacy policy", "Yes"),
])
def test_saved_screening_answers_never_accept_legal_choices(space, page, legal_markup, question, answer):
    from urllib.parse import quote
    from kitty.core.apply.filler import fill_form
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question=question, answer=answer),
        AnswerEntry(question="Available for relocation?", answer="Yes"),
    ]))
    html = ('<form id="application" onsubmit="document.body.dataset.submitted=\'yes\';return false">'
            '<label>First name<input name="first_name"></label>' + legal_markup +
            '<fieldset><legend>Available for relocation?</legend>'
            '<label><input type="radio" name="relocate" value="yes">Yes</label>'
            '<label><input type="radio" name="relocate" value="no">No</label></fieldset>'
            '<button type="submit">Submit application</button></form>')
    page.goto("data:text/html," + quote(html))
    result = fill_form(page, config, None)
    assert _value(page, '[name="first_name"]') == "Pat"
    assert _js(page, "() => document.querySelector('[name=relocate][value=yes]').checked")
    assert _js(page, "() => [...document.querySelectorAll('#legal,[name=legal]')].every(e => !e.checked && e.getAttribute('aria-checked') !== 'true')")
    assert question in result.unanswered
    assert _js(page, "() => document.body.dataset.submitted") is None


def test_custom_option_cannot_hide_an_application_submit_action(page):
    from urllib.parse import quote

    page.goto("data:text/html," + quote("""
      <form onsubmit="window.submitted++;return false">
        <button type="button" id="combo" role="combobox" aria-label="Available for relocation?"
          aria-controls="choices" onclick="document.querySelector('#choices').hidden=false">Choose...</button>
        <div id="choices" role="listbox" hidden>
          <div role="option" aria-selected="false"><button type="submit">Yes</button></div>
        </div>
      </form><script>window.submitted=0;</script>
    """))
    field = next(f for f in read_fields([page.main]) if f.element_id == "combo")
    assert not pick_from_list(page.main, field.key, ("Yes",), location=False)
    assert _js(page, "() => window.submitted") == 0


def _shadow_application(page):
    """A one-click form whose native fields all live in nested open roots."""
    from urllib.parse import quote

    page.goto("data:text/html," + quote("""
      <style>application-shell {display:block}</style>
      <application-shell></application-shell>
      <script>
        window.events = {submitted: 0, final: 0, trusted: [], input: 0, change: 0};
        const shell = document.querySelector('application-shell');
        const outer = shell.attachShadow({mode:'open'});
        outer.innerHTML = '<style>:host{display:block} form{display:grid;gap:12px}</style><form></form>';
        const form = outer.querySelector('form');
        form.onsubmit = event => { event.preventDefault(); window.events.submitted++; };
        window.parts = {};
        function component(name, html, parent=form) {
          const host = document.createElement('field-component');
          host.style.display = 'block';
          parent.append(host);
          const root = host.attachShadow({mode:'open'});
          root.innerHTML = '<style>:host{display:block}label{display:block}</style>' + html;
          window.parts[name] = root;
          return root;
        }
        component('first', '<span id="caption">First name*</span><input id="field" aria-labelledby="caption" required>');
        component('last', '<label for="field">Last name*</label><input id="field" required value="Manual surname">');
        component('email', '<span id="caption">Email*</span><input id="field" type="email" aria-labelledby="caption" required>');
        component('graduation', '<label for="field">Expected graduation date</label><select id="field" required><option value="">Select</option><option value="spring">Spring 2032</option></select>');
        const resume = component('resume', '<section role="group" aria-labelledby="caption" aria-required="true"><h3 id="caption">Resume</h3></section>');
        component('file', '<input id="field" type="file" style="display:none">', resume.querySelector('section'));
        for (const name of ['relocation', 'manual-choice']) {
          const root = component(name, '<section role="radiogroup" aria-labelledby="caption" aria-required="true"><span id="caption">Available for relocation?</span></section>');
          const group = root.querySelector('section');
          for (const text of ['Yes', 'No']) {
            const option = component(name + text, '<input id="field" type="radio" name="answer" style="display:none"><label for="field">' + text + '</label>', group);
            const input = option.querySelector('input');
            input.checked = name === 'manual-choice' && text === 'No';
            input.addEventListener('click', event => window.events.trusted.push(event.isTrusted));
          }
        }
        for (const name of ['combo', 'manual-combo']) {
          const root = component(name, '<span id="caption">Available for relocation?</span><button id="field" type="button" role="combobox" aria-labelledby="caption" aria-controls="choices">Choose...</button><div id="choices" role="listbox" hidden><div role="option" aria-selected="false">Yes</div><div role="option" aria-selected="false">No</div></div>');
          const box = root.querySelector('button'), list = root.querySelector('[role=listbox]');
          box.onclick = () => { list.hidden = !list.hidden; };
          for (const option of list.children) option.onclick = event => {
            window.events.trusted.push(event.isTrusted);
            for (const other of list.children) other.setAttribute('aria-selected', String(option === other));
            box.textContent = option.textContent; list.hidden = true;
          };
          if (name === 'manual-combo') {
            list.lastElementChild.setAttribute('aria-selected', 'true');
            box.textContent = 'No';
          }
        }
        const final = document.createElement('button');
        final.type = 'submit'; final.textContent = 'Submit application';
        final.onclick = () => window.events.final++;
        form.append(final);
        shell.addEventListener('input', () => window.events.input++);
        shell.addEventListener('change', () => window.events.change++);
      </script>
    """))


def test_nested_shadow_application_uses_real_fill_paths_and_preserves_manual_values(space, page):
    from kitty.core.apply.filler import fill_form
    from kitty.core.apply.page import _QUERY_ALL_JS, tick, upload

    _shadow_application(page)
    assert _js(page, "() => document.querySelectorAll('input,select,textarea').length") == 0
    fields = read_fields([page.main])
    assert [(f.label, f.required) for f in fields[:5]] == [
        ("First name", True), ("Last name", True), ("Email", True),
        ("Expected graduation date", True), ("Resume", True),
    ]
    groups = [f for f in fields if f.kind == "radio"]
    assert len(groups) == 2
    assert all(f.label == "Available for relocation?" and f.required and f.options == ("Yes", "No") for f in groups)
    assert groups[0].blank and not groups[1].blank
    assert set(groups[0].option_keys).isdisjoint(groups[1].option_keys)
    # The same selector reaches each root once, including a matching Element root.
    assert page.main.call(
        "() => {const q=(" + _QUERY_ALL_JS + ");const all=q('input');"
        "return [all.length,new Set(all).size,q('input',all[0]).length];}"
    ) == [8, 8, 1]

    resume = space.settings.root / "resume.pdf"
    upload(page.main, next(f.key for f in fields if f.kind == "file"), resume)
    fill_form(page, space.config, None, preserve_existing=True)
    assert _js(page, "() => parts.first.querySelector('input').value") == "Pat"
    assert _js(page, "() => parts.last.querySelector('input').value") == "Manual surname"
    assert _js(page, "() => parts.email.querySelector('input').value") == space.config.profile.email
    assert _js(page, "() => parts.graduation.querySelector('select').value") == "spring"
    assert _js(page, "() => [...parts.file.querySelector('input').files].map(f=>f.name)") == ["resume.pdf"]
    assert tick(page.main, groups[0].option_keys[0])
    assert _js(page, "() => parts.relocationYes.querySelector('input').checked")
    assert _js(page, "() => parts['manual-choiceNo'].querySelector('input').checked")
    assert _js(page, "() => parts['manual-combo'].querySelector('button').textContent") == "No"
    assert _js(page, "() => events.trusted") == [True]
    assert _js(page, "() => events.input > 0 && events.change > 0")
    assert _js(page, "() => [events.submitted,events.final]") == [0, 0]


def test_shadow_combobox_owns_only_its_root_local_list(page):
    _shadow_application(page)
    fields = [f for f in read_fields([page.main]) if f.kind == "combobox"]
    assert len(fields) == 2 and fields[0].blank and not fields[1].blank
    assert pick_from_list(page.main, fields[0].key, ("Yes",), location=False)
    assert _js(page, "() => parts.combo.querySelector('button').textContent") == "Yes"
    assert _js(page, "() => parts['manual-combo'].querySelector('button').textContent") == "No"
    assert _js(page, "() => events.trusted") == [True]
    assert _js(page, "() => [events.submitted,events.final]") == [0, 0]


@pytest.mark.parametrize("mutation", [
    "host.setAttribute('aria-disabled','true')",
    "host.setAttribute('aria-readonly','true')",
    "host.setAttribute('disabled','')",
    "host.setAttribute('readonly','')",
    "host.setAttribute('inert','')",
    "const action=document.createElement('button');action.type='submit';host.replaceWith(action);action.append(host)",
    "const action=document.createElement('a');action.href='#submit';host.replaceWith(action);action.append(host)",
])
def test_shadow_host_mutations_reject_remembered_fields_before_writing(page, mutation):
    from kitty.core.apply.page import UnsafeAction, fill_text, tick, upload

    _shadow_application(page)
    fields = read_fields([page.main])
    text = next(f for f in fields if f.label == "First name")
    choice = next(f for f in fields if f.kind == "radio")
    resume = next(f for f in fields if f.kind == "file")
    _js(page, "() => {const host=document.querySelector('application-shell');" + mutation + ";}")
    assert read_fields([page.main]) == []
    with pytest.raises(UnsafeAction):
        fill_text(page.main, text.key, "Must not be written")
    with pytest.raises(UnsafeAction):
        tick(page.main, choice.option_keys[0])
    with pytest.raises(UnsafeAction):
        upload(page.main, resume.key, Path("/not-opened.pdf"))
    assert _js(page, "() => parts.first.querySelector('input').value") == ""
    assert _js(page, "() => [events.submitted,events.final]") == [0, 0]


def test_shadow_option_embedded_submit_is_rechecked_after_discovery(page):
    from kitty.core.apply.page import _SUGGESTIONS_JS

    _shadow_application(page)
    combo = next(f for f in read_fields([page.main]) if f.kind == "combobox")
    _js(page, "() => { parts.combo.querySelector('[role=listbox]').hidden=false; }")
    assert page.main.call(_SUGGESTIONS_JS, combo.key, 0, "check", "Yes") is not None
    _js(page, """() => {
      const option=parts.combo.querySelector('[role=option]');
      const host=document.createElement('action-shell');
      option.replaceChildren(host);
      host.attachShadow({mode:'open'}).innerHTML='<button type="submit">Yes</button>';
      host.shadowRoot.querySelector('button').onclick=() => window.events.final++;
    }""")
    assert page.main.node(_SUGGESTIONS_JS, combo.key, 0, "node", "Yes") is None
    assert not page.main.call(_SUGGESTIONS_JS, combo.key, 0, "click", "Yes")
    assert _js(page, "() => [events.submitted,events.final]") == [0, 0]


def test_shadow_aria_choice_cannot_acquire_an_embedded_final_action(page):
    from kitty.core.apply.page import UnsafeAction, tick

    _shadow_application(page)
    _js(page, """() => {
      const root=parts.first;
      root.innerHTML='<button type="button" role="checkbox" aria-checked="false">Available?</button>';
      root.querySelector('button').onclick=() => window.events.final++;
    }""")
    choice = next(f for f in read_fields([page.main]) if f.kind == "checkbox")
    _js(page, """() => {
      const choice=parts.first.querySelector('button');
      const host=document.createElement('embedded-action');
      choice.replaceChildren(host);
      host.attachShadow({mode:'open'}).innerHTML='<button type="submit">Submit application</button>';
    }""")
    with pytest.raises(UnsafeAction):
        tick(page.main, choice.key)
    assert not any(f.key == choice.key for f in read_fields([page.main]))
    assert _js(page, "() => [events.submitted,events.final]") == [0, 0]


def test_optional_required_group_styling_does_not_create_required_questions(page):
    page.goto("about:blank")
    _js(page, """() => { document.body.innerHTML = `
      <label for="optional"><span class="c-spl-form-field-label-required-group">Website</span></label>
      <input id="optional" name="optional" aria-required="false">
      <label for="unmarked"><span class="c-spl-form-field-label-required-group">Other link</span></label>
      <input id="unmarked" name="unmarked">
      <label for="required"><span class="c-spl-form-field-label-required-group">Email
        <span class="c-spl-form-field-required-mark">*</span></span></label>
      <input id="required" name="required" aria-required="true">
    `; }""")
    assert {field.name: field.required for field in read_fields([page.main])} == {
        "optional": False, "unmarked": False, "required": True,
    }


@pytest.mark.parametrize("covered", [False, True])
def test_real_pointer_scrolls_to_control_but_does_not_click_an_overlay(page, covered):
    from kitty.core.apply.bidi import BidiError

    page.goto("about:blank")
    _js(page, """() => {
      window.actualClicks = [];
      document.body.innerHTML = '<button id="target" type="button" style="margin-top:2200px">Continue</button>';
      document.querySelector('#target').onclick = e => window.actualClicks.push(e.isTrusted);
    }""")
    if covered:
        _js(page, """() => {
          const overlay = document.createElement('div');
          overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;background:white';
          document.body.append(overlay);
        }""")
    target = page.main.node("() => document.querySelector('#target')")
    if covered:
        with pytest.raises(BidiError, match="covers"):
            page.main.click(target)
        assert _js(page, "() => window.actualClicks") == []
    else:
        page.main.click(target)
        assert _js(page, "() => window.actualClicks") == [True]
        assert _js(page, "() => window.scrollY") > 0


def test_loaded_embedded_application_takes_precedence_over_parent_apply_button(space, page):
    job = _job(space, "unanswered")
    page.goto(job.url)
    _js(page, """async () => {
      window.entryClicks = 0;
      document.body.innerHTML =
        '<button type="button" data-automation-id="jobPostingApplyButton">Apply now</button>';
      document.querySelector('button').onclick = () => window.entryClicks++;
      const frame = document.createElement('iframe');
      const loaded = new Promise(resolve => frame.onload = resolve);
      frame.srcdoc = `<form id="application-form" onsubmit="event.preventDefault();window.submitted++">
        <label>First Name<input name="first_name" required></label>
        <label>Stadt<input name="city" required></label>
        <label>Resume/CV<input type="file" name="resume" required></label>
        <button type="submit">Submit application</button>
        </form><script>window.submitted=0</script>`;
      document.body.append(frame);
      await loaded;
    }""")
    report = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert report.status == "ready"
    frame = page.frames()[1]
    assert frame.call("() => document.querySelector('[name=first_name]').value") == "Pat"
    assert frame.call("() => document.querySelector('[name=city]').value") == "Arlington"
    assert frame.call(
        "async () => await document.querySelector('[name=resume]').files[0].text()",
        page_realm=True,
    ) == "%PDF-1.4\n% test resume\n"
    assert frame.call("() => window.submitted", page_realm=True) == 0
    assert _js(page, "() => window.entryClicks") == 0


@pytest.mark.parametrize("surface", ["complete", "missing-profile-record", "hidden-section", "ambiguous-upload"])
def test_standard_oracle_form_preserves_answers_and_verifies_separate_attachment(space, page, surface):
    job = _job(space, "unanswered")
    page.goto(job.url)
    _js(page, """surface => {
      window.parserCalls = 0; window.submitted = 0;
      document.body.innerHTML = `
        <h1>Job Application Form</h1><form class="apply-flow__content-form" method="post">
        <apply-flow-section data-scroll-spy="apply-flow-sections" aria-hidden="false">
          <label>First Name<input name="first_name" value="Previously entered name" required></label>
          ${surface === 'ambiguous-upload' ? '' : `<label>Import your profile from resume
            <input class="apply-flow-profile-import-awli__file-upload" type="file"></label>`}
          <label>Upload Resume<input class="file-form-element__input" name="attachment-upload"
            type="file" aria-required="true"></label>
        </apply-flow-section>
        <apply-flow-section data-scroll-spy="apply-flow-sections"
          ${surface === 'hidden-section' ? 'aria-hidden="true" style="display:none"' : 'aria-hidden="false"'}>
          ${surface === 'missing-profile-record' ? '<button type="button" data-profile-item-invalid="true">ADD EXPERIENCE</button>' : 'Additional information'}
        </apply-flow-section>
        <button type="button" class="apply-flow-pagination__submit-button">SUBMIT</button></form>`;
      document.querySelector('form').onsubmit = e => { e.preventDefault(); window.submitted++; };
      document.querySelector('.apply-flow-pagination__submit-button').onclick = () => window.submitted++;
      const parser = document.querySelector('.apply-flow-profile-import-awli__file-upload');
      if (parser) parser.onchange = () => {
        window.parserCalls++;
        document.querySelector('[name=first_name]').value = 'Parser overwrote retained answer';
      };
    }""", surface)
    report = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert _js(page, "() => document.querySelector('[name=first_name]').value") == "Previously entered name"
    assert _js(page, "() => [window.parserCalls, window.submitted]") == [0, 0]
    if surface == "ambiguous-upload":
        assert report.status == "blocked"
        assert _js(page, "() => document.querySelector('[name=attachment-upload]').files.length") == 0
    else:
        assert page.main.call("async () => await document.querySelector('[name=attachment-upload]').files[0].text()",
                              page_realm=True) == "%PDF-1.4\n% test resume\n"
        if surface == "complete":
            assert report.status == "ready"
        elif surface == "missing-profile-record":
            assert report.status == "blocked"
        else:
            assert report.status == "partial"
