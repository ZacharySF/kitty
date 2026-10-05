"""Routing, eligibility and the text helpers: the rules that decide what I see."""

import pytest

from applyai.config import EligibilityRules
from applyai.core.discovery.eligibility import evaluate, merge_rules
from applyai.core.discovery.routing import route, stem
from applyai.core.discovery.text import html_to_text, is_internship_title, season_from, us_location


@pytest.fixture
def tracks(workspace):
    return workspace.config.tracks.tracks


@pytest.fixture
def rules(tracks):
    return merge_rules([t.eligibility_rules for t in tracks if t.name != "parttime"])


def _verdict(rules, title="Software Engineer Intern", description=None,
             location="Austin, TX", country=None, degrees=None):
    return evaluate(title=title, description=description, location=location,
                    country=country, degrees=degrees, rules=rules)


# ------------------------------------------------------------------- text


@pytest.mark.parametrize("location, expected", [
    ("Austin, TX", True),
    ("NYC", True),
    ("San Mateo, California, United States", True),
    ("Boise, ID; Hsinchu", True),                 # any US part counts
    ("Bengaluru, Karnataka, IN", False),          # 'IN' is India here, not Indiana
    ("Toronto, ON, Canada", False),
    ("Remote", None),                             # unclear passes through
    ("3 Locations", None),
])
def test_us_location(location, expected):
    assert us_location(location) is expected


def test_country_field_decides_when_location_is_vague():
    assert us_location("2 Locations", "United States of America") is True
    assert us_location(None, "Japan") is False


@pytest.mark.parametrize("title, expected", [
    ("Software Engineer Intern", True),
    ("Electrical Engineering Co-Op", True),
    ("Systems Engineering Co Op", True),
    ("Engineering Cooperative Education", True),
    ("Cooperative Banking Manager", False),
    ("Co Operating Officer", False),
    ("Summer Analyst", True),
    ("Internal Tools Engineer", False),
    ("International Tax Manager", False),
    ("Senior Software Engineer", False),
])
def test_internship_titles(title, expected):
    assert is_internship_title(title) is expected


def test_season_and_html():
    assert season_from("Quant Intern - Summer 2027") == "Summer 2027"
    assert season_from(None, "Fall 26 co-op") == "Fall 2026"
    # Greenhouse double-escapes its HTML.
    assert html_to_text("&lt;p&gt;Rust &amp;amp; C++&lt;/p&gt;") == "Rust & C++"


# ---------------------------------------------------------------- routing


def test_stemming_joins_word_forms():
    assert stem("engineering") == stem("engineer")
    assert stem("trading") == stem("trader")


def test_title_routing_uses_search_terms(tracks):
    matches = route("Backend Software Engineering Intern", None, "greenhouse", tracks)
    assert [m.track for m in matches] == ["software"]
    assert "software engineer intern" in matches[0].reason


def test_hyphenated_titles_match_joined_terms(tracks):
    assert [m.track for m in route("Back-End Engineer Intern", None, "greenhouse", tracks)] == ["software"]


def test_a_track_only_takes_its_own_sources(tracks):
    # parttime lists only jobspy, so a Greenhouse tutor posting never lands there.
    assert route("Tutor Intern", None, "greenhouse", tracks) == []


def test_description_routing_ignores_degree_sentences(tracks):
    text = "Pursuing a degree in CS or Electrical Engineering. You will write firmware for embedded systems."
    matches = route("Engineering Intern", text, "greenhouse", tracks)
    assert [m.track for m in matches] == ["embedded"]
    assert matches[0].reason.startswith("description mentions")


def test_description_adds_track_without_duplicate_title_match(tracks):
    first, second = tracks[:2]
    selected = [
        first.model_copy(update={"sources": ["greenhouse"], "search_terms": ["software engineer intern"]}),
        second.model_copy(update={"sources": ["greenhouse"], "search_terms": ["embedded systems intern"]}),
    ]
    matches = route(
        "Software Engineer Intern", "Build embedded systems with our software engineers.",
        "greenhouse", selected,
    )
    assert [match.track for match in matches] == [first.name, second.name]
    assert matches[0].reason.startswith("title")
    assert matches[1].reason.startswith("description")
    assert [match.track for match in route(
        "Software Engineer Intern", "Pursuing a degree in embedded systems.",
        "greenhouse", selected,
    )] == [first.name]


@pytest.mark.parametrize("title, term", [
    ("IS Internship - Web Applications", "web applications intern"),
    ("Data Engineer Internship", "data engineer intern"),
    ("Systems Engineering Co-op", "systems engineering intern"),
    ("Electro-Optic Intern", "electro optic intern"),
])
def test_precise_configured_title_aliases(tracks, title, term):
    track = tracks[0].model_copy(update={"sources": ["greenhouse"], "search_terms": [term]})
    assert [match.track for match in route(title, None, "greenhouse", [track])] == [track.name]
    assert route("General Engineering Intern", None, "greenhouse", [track]) == []

# ------------------------------------------------------------ eligibility


def test_outside_the_us_is_filtered(rules):
    verdict = _verdict(rules, location="Toronto, ON, Canada")
    assert verdict.eligibility == "ineligible"
    assert verdict.rule == "outside the US"


def test_simplify_grad_only_degrees_are_filtered(rules):
    assert _verdict(rules, degrees=["Master's", "PhD"]).eligibility == "ineligible"
    assert _verdict(rules, degrees=["Bachelor's", "Master's"]).eligibility != "ineligible"


def test_grad_degree_in_title_is_filtered(rules):
    verdict = _verdict(rules, title="Quantitative Researcher Intern (PhD)")
    assert verdict.eligibility == "ineligible"


@pytest.mark.parametrize("title, expected", [
    ("Graduate Intern - Summer 2027", "ineligible"),
    ("Software Engineering - Intern, Graduate", "ineligible"),
    ("Undergraduate Intern - Summer 2027", "ambiguous"),
    ("Software Engineering - Intern, Bachelor's", "ambiguous"),
])
def test_graduate_student_titles_are_filtered(rules, title, expected):
    assert _verdict(rules, title=title).eligibility == expected


def test_graduation_year_i_cannot_meet(rules):
    verdict = _verdict(rules, description="Must be graduating between December 2027 and June 2028.")
    assert verdict.eligibility == "ineligible"
    assert verdict.rule == "graduation in 2027, 2028 required"


@pytest.mark.parametrize("text", [
    "Graduating in December 2026 or later.",             # a floor, not a requirement
    "Expected graduation between December 2027 and June 2030.",
    # turns away earlier graduates; not a requirement on me
    "If you are graduating prior to May 2027 please apply to Entry Level full-time roles.",
])
def test_open_graduation_windows_pass(rules, text):
    assert _verdict(rules, description=text).eligibility != "ineligible"


def test_standing_requirement_unless_underclassmen_are_named(rules):
    assert _verdict(rules, description="Must have junior standing.").rule == "requires junior standing"
    text = "Open to sophomores through rising seniors."
    assert _verdict(rules, description=text).eligibility == "boosted"


def test_masters_mentioned_alongside_bachelors_is_not_a_requirement(rules):
    text = "Pursuing a Bachelor's or Master's degree in Computer Science."
    verdict = _verdict(rules, description=text)
    assert verdict.eligibility == "eligible"
    assert "Bachelor's" in verdict.reason


def test_boost_and_ambiguous(rules):
    assert _verdict(rules, description="A program for first-year students.").eligibility == "boosted"
    # 'explore' is only trusted in a title; descriptions say it constantly.
    assert _verdict(rules, description="Explore our benefits.").eligibility == "ambiguous"
    assert _verdict(rules, title="Explore Intern").eligibility == "boosted"
    assert _verdict(rules).reason.startswith("no description to check")


def test_listed_hourly_pay_below_the_floor_drops_and_at_or_above_ranks_eligible():
    from applyai.config import EligibilityRules
    from applyai.core.discovery.jobspy_source import with_pay_line

    floor = EligibilityRules(min_hourly_pay=12)
    assert _verdict(floor, "Cashier", "Pay: $10.50 per hour. Flexible shifts.").eligibility == "ineligible"
    # a range counts by its top: $11 - $14 can reach the floor
    assert _verdict(floor, "Cashier", "Starting at $11 - $14/hr").eligibility == "eligible"
    assert _verdict(floor, "Cashier", "We pay $15.50 hourly").eligibility == "eligible"
    # unlisted pay changes nothing, and dollars that are not hourly are not pay
    assert _verdict(floor, "Cashier", "Flexible shifts, $500 sign-on bonus.").eligibility == "ambiguous"
    # no floor on the track: pay is not judged
    assert _verdict(EligibilityRules(), "Cashier", "Pay: $9 per hour.").eligibility == "ambiguous"
    # Indeed's pay shown beside the posting arrives through JobSpy's amounts
    record = {"site": "indeed", "interval": "hourly", "min_amount": 9.0, "max_amount": 10.0}
    assert _verdict(floor, "Barista", with_pay_line(record, "Make great coffee.")).eligibility == "ineligible"
    assert with_pay_line({"interval": "yearly", "min_amount": 30000}, "Text") == "Text"


def test_undergraduate_posting_year_is_not_a_graduation_requirement(rules):
    assert _verdict(rules, description=(
        "Hardware Engineering Undergraduate Intern 2027\nOpen to undergraduates."
    )).eligibility == "eligible"
    assert _verdict(rules, description=(
        "Open to undergraduates graduating in December 2027 or May 2028."
    )).eligibility == "ineligible"


@pytest.fixture
def qualification_rules():
    """Fixed applicant constraints, independent of the user's editable tracks."""
    return EligibilityRules(
        drop_if_requires=[
            "junior standing", "senior standing", "rising junior", "rising senior",
            "master's degree", "phd",
        ],
        boost_phrases=["freshman", "sophomore", "first-year", "second-year", "explore"],
    )


def test_negated_underclass_mentions_cannot_cancel_required_standing(qualification_rules):
    verdict = _verdict(
        qualification_rules,
        description="Must have junior standing. Freshmen and sophomores are not eligible.",
    )
    assert verdict.eligibility == "ineligible"
    assert "'Must have junior standing'" in verdict.reason


@pytest.mark.parametrize("description", [
    "Freshmen and sophomores are not eligible.",
    "No freshmen may apply.",
    "This program excludes first-year students.",
    "Sophomore applicants cannot apply.",
    "Not open to undergraduates.",
    "Undergraduate students are ineligible.",
    "Freshmen need not apply.",
])
def test_explicit_negative_eligibility_is_an_exclusion(qualification_rules, description):
    verdict = _verdict(qualification_rules, description=description)
    assert verdict.eligibility == "ineligible"
    assert "'" in verdict.reason


@pytest.mark.parametrize("description", [
    "This is not a freshman-friendly program.",
    "Freshmen may be eligible if they have advanced standing.",
    "Freshmen may be eligible depending on completed coursework.",
    "The brochure discusses freshmen and sophomores.",
    "We do not promise that freshmen are eligible.",
])
def test_unclear_or_negated_inclusion_does_not_invent_eligibility(qualification_rules, description):
    assert _verdict(qualification_rules, description=description).eligibility == "ambiguous"


@pytest.mark.parametrize("description", [
    "Freshmen welcome. Must have junior standing.",
    "Must have junior standing; freshmen welcome.",
    "Open to sophomores, but junior standing is required.",
    "Freshmen welcome. Freshmen are not eligible.",
    "Freshmen are not eligible. Freshmen welcome.",
    "Undergraduate coursework is useful. A master's degree is required.",
    "A master's degree is required. Open to undergraduates.",
    "Open to undergraduates, but candidates must have a master's degree.",
    "Bachelor's degree required and master's degree required.",
    "Junior standing preferred and master's degree required. Freshmen welcome.",
    "Master's required; undergraduate coursework preferred. Freshmen welcome.",
    "Freshmen welcome. Applicants must be juniors or seniors.",
    "Must have junior standing and no prior experience is required. Freshmen welcome.",
    "Must have junior standing, master's preferred. Freshmen welcome.",
    "Must have junior standing with a master's degree preferred. Freshmen welcome.",
    "A master's degree is required with undergraduate coursework preferred.",
])
def test_clear_restrictions_override_separate_positive_evidence(qualification_rules, description):
    assert _verdict(qualification_rules, description=description).eligibility == "ineligible"


@pytest.mark.parametrize("description, expected", [
    ("Pursuing a Bachelor's or Master's degree in Computer Science.", "eligible"),
    ("Pursuing a Master's degree or a Bachelor's degree.", "eligible"),
    ("Must be pursuing a bachelor or master degree.", "eligible"),
    ("Pursuing a Bachelor's, Master's, or PhD degree.", "eligible"),
    ("Bachelor's, Master's, or PhD degree required.", "eligible"),
    ("Open to sophomores through rising seniors.", "boosted"),
    ("Open to freshmen or juniors.", "boosted"),
    ("A program for first-year students.", "boosted"),
    ("Freshmen and sophomores are welcome.", "boosted"),
    ("Freshman-friendly internship.", "boosted"),
    ("Sophomores encouraged to apply.", "boosted"),
    ("Junior standing preferred. Freshmen welcome.", "boosted"),
    ("Junior standing is not required. Freshmen welcome.", "boosted"),
    ("A master's degree is preferred. Open to undergraduates.", "eligible"),
    ("A master's degree is not required. Open to undergraduates.", "eligible"),
    ("Junior standing preferred.", "ambiguous"),
    ("Junior standing is not required.", "ambiguous"),
])
def test_affirmative_alternatives_and_optional_qualifications(qualification_rules, description, expected):
    assert _verdict(qualification_rules, description=description).eligibility == expected


def test_title_boost_cannot_override_description_restriction(qualification_rules):
    assert _verdict(
        qualification_rules, title="Explore Intern",
        description="Undergraduates are not eligible.",
    ).eligibility == "ineligible"


def test_conditional_evidence_overrides_separate_boost(qualification_rules):
    assert _verdict(
        qualification_rules,
        description="Freshmen welcome. Undergraduates are eligible only if their prerequisites are approved.",
    ).eligibility == "ambiguous"


def test_junior_job_title_is_not_a_school_standing_requirement(qualification_rules):
    assert _verdict(qualification_rules, title="Junior Software Intern").eligibility == "ambiguous"
