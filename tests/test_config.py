"""Config validation, including the line numbers in error messages."""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from kitty.config import (
    CONFIG_FILES,
    AnswerEntry,
    AnswersFile,
    ConfigError,
    DEFAULT_VIEWS,
    Profile,
    TracksFile,
    WatchlistFile,
    load_config,
    parse_file,
)
from kitty.core import companies as companies_core

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_loads(workspace):
    config = workspace.config
    # Read the names from the file itself, so adding a track never breaks this test.
    tracks_path = workspace.settings.root / "config" / "tracks.yaml"
    shipped = yaml.safe_load(tracks_path.read_text(encoding="utf-8"))
    assert config.track_names == [track["name"] for track in shipped["tracks"]]
    assert len(set(config.track_names)) == len(config.track_names)
    assert config.track("parttime").triage_depth == "rules"     # never calls the LLM


def test_every_shipped_config_file_has_a_model():
    # A yaml file in config/ that no model reads would be silently ignored.
    names = {filename for filename, _model, _required, _example in CONFIG_FILES}
    shipped = {path.name for path in (REPO_ROOT / "config").glob("*.yaml")}
    shipped |= {path.name.removesuffix(".example") for path in (REPO_ROOT / "config").glob("*.yaml.example")}
    assert shipped <= names


def test_a_typo_reports_its_line_and_a_fix(tmp_path):
    path = tmp_path / "tracks.yaml"
    path.write_text(
        "tracks:\n"
        "  - name: quant\n"
        "    weekly_target: 3\n"
        "    triage_depth: llm\n"
        "  - name: gpu\n"
        "    weekly_targt: 4\n"          # typo on line 6
        "    triage_depth: llm\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        parse_file(path, TracksFile)

    problem = caught.value.problems[0]
    assert problem.line == 6
    assert "weekly_targt" in problem.location
    assert "unknown key" in (problem.hint or "")


def test_a_bad_enum_value_lists_the_allowed_ones(tmp_path):
    path = tmp_path / "tracks.yaml"
    path.write_text(
        "tracks:\n  - name: quant\n    triage_depth: sometimes\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as caught:
        parse_file(path, TracksFile)
    assert "use one of" in (caught.value.problems[0].hint or "")


def test_yaml_syntax_errors_point_at_the_line(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text("name: me\n  bad_indent: true\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        parse_file(path, Profile)
    assert caught.value.problems[0].line is not None
    assert "YAML syntax error" in caught.value.problems[0].message


def test_duplicate_track_names_are_rejected(tmp_path):
    path = tmp_path / "tracks.yaml"
    path.write_text("tracks:\n  - name: quant\n  - name: quant\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        parse_file(path, TracksFile)
    assert "duplicate track names" in caught.value.problems[0].message


def test_workday_slug_shape_is_checked(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        "companies:\n  - name: NVIDIA\n    ats: workday\n    slug: nvidia\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as caught:
        parse_file(path, WatchlistFile)
    assert "tenant/wdN/site" in caught.value.problems[0].message


def test_ats_none_needs_a_reason_and_no_slug(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        "companies:\n"
        "  - name: Lockheed Martin\n    ats: none\n    notes: uses BrassRing\n",
        encoding="utf-8",
    )
    company = parse_file(path, WatchlistFile).companies[0]
    assert (company.ats, company.slug) == ("none", "")

    path.write_text("companies:\n  - name: Lockheed Martin\n    ats: none\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        parse_file(path, WatchlistFile)
    assert "notes must say why" in caught.value.problems[0].message

    path.write_text("companies:\n  - name: Stripe\n    ats: greenhouse\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        parse_file(path, WatchlistFile)
    assert "slug is required" in caught.value.problems[0].message


def test_public_watchlist_loads_and_only_names_real_tracks(workspace):
    path = workspace.settings.config_dir / "watchlist.yaml"
    watchlist = parse_file(path, WatchlistFile)
    names = [company.name.lower() for company in watchlist.companies]
    assert len(set(names)) == len(names)
    known = set(workspace.config.track_names)
    for company in watchlist.companies:
        assert set(company.tracks) <= known, company.name
    # No two entries collapse to the same company row when synced.
    assert companies_core.sync_watchlist(workspace.conn, watchlist) == len(watchlist.companies)


def test_missing_optional_files_fall_back_to_defaults(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "tracks.yaml").write_text("tracks:\n  - name: quant\n", encoding="utf-8")
    (config_dir / "sources.yaml").write_text(
        "sources:\n  manual:\n    kind: manual\n", encoding="utf-8"
    )
    (config_dir / "profile.yaml").write_text(
        "name: me\nemail: me@example.invalid\nschool: Example Coastal University\nmajor: EE\n"
        "standing: sophomore\ngraduation: 2032-05\nhome_city: Waco\nhome_state: TX\n",
        encoding="utf-8",
    )
    config = load_config(config_dir)
    assert config.watchlist.companies == []
    assert config.views == DEFAULT_VIEWS


def test_unquoted_yes_is_still_a_text_answer(tmp_path):
    path = tmp_path / "answers.yaml"
    path.write_text("answers:\n  - question: Authorized to work?\n    answer: Yes\n", encoding="utf-8")
    from kitty.config import AnswersFile

    answers = parse_file(path, AnswersFile)
    assert answers.answers[0].answer == "Yes"


def _jurisdiction_config(workspace):
    return replace(workspace.config, answers=AnswersFile(answers=[
        AnswerEntry(
            question="Are you legally authorized to work in the United States?",
            answer="Yes", match=["authorized to work", "work authorization"],
            choices=["I am authorized to work in the United States for any employer"],
        ),
        AnswerEntry(
            question="Will you now or in the future require sponsorship?",
            answer="No", match=["require sponsorship", "need sponsorship"],
            choices=["I do not require sponsorship for employment in the United States"],
        ),
    ]))


@pytest.mark.parametrize(("question", "jurisdiction", "options", "expected"), [
    ("Are you authorized to work in this country?", None, (), False),
    ("Are you authorized to work in this country?", "US", (), True),
    ("Are you authorized to work in this country?", "SG", (), False),
    ("Are you authorized to work in Singapore?", "US", (), False),
    ("Are you authorized to work in Pakistan?", "US", (), False),
    ("Are you authorized to work in your country of residence?", "US", (), False),
    ("Are you authorized to work in the United States?", None, (), True),
    ("Are you authorized to work in the U.S.?", "SG", (), True),
    ("Are you authorized to work here?", None,
     ("Authorized to work in Singapore", "Not authorized"), False),
    ("Are you authorized to work here?", None,
     ("Authorized to work in the United States", "Not authorized"), True),
    ("Are you authorized to work in the United States?", "US",
     ("Authorized to work in Singapore", "Not authorized"), False),
    ("Are you authorized to work in the US or Singapore?", "US", (), False),
    ("Tell us whether you are authorized to work here", None, (), False),
    ("Will you now or in the future require sponsorship?", None, (), False),
    ("Will you now or in the future require sponsorship?", "US", (), True),
    ("Will you require sponsorship in the UK?", "US", (), False),
    ("Will you require sponsorship in the United States?", None, (), True),
    ("Will you need sponsorship here?", "US",
     ("No sponsorship required in Singapore", "Yes"), False),
])
def test_country_specific_permission_gates_exact_alias_and_choices(
    workspace, question, jurisdiction, options, expected,
):
    config = _jurisdiction_config(workspace)
    assert (config.answer_for(question, jurisdiction=jurisdiction, options=options) is not None) is expected


@pytest.mark.parametrize(("question", "jurisdiction", "options", "expected"), [
    ("Are you authorized to work here?", None, (), None),
    ("Are you authorized to work here?", "US", (), "US"),
    ("Are you authorized to work here?", "SG", (), "SG"),
    ("Are you authorized to work here?", "GB", (), "GB"),
    ("Are you authorized to work in the United States?", "SG", (), "US"),
    ("Are you legally authorized to work in Singapore?", "US", (), "SG"),
    ("Are you authorized to work in the UK?", "US", (), "GB"),
    ("Are you authorized to work here?", "US",
     ("Authorized to work in Singapore", "Not authorized"), "SG"),
    ("Are you authorized to work in the US or Singapore?", "US", (), None),
    ("Are you authorized to work in the United States?", "US",
     ("Authorized to work in Singapore", "Not authorized"), None),
    ("Are you authorized to work in Pakistan?", "US", (), None),
    ("Are you authorized to work in your country of residence?", "US", (), None),
])
def test_country_evidence_resolves_permission_alias_alternatives(
    workspace, question, jurisdiction, options, expected,
):
    entries = {
        code: AnswerEntry(
            question=f"Are you legally authorized to work in {country}?",
            answer=f"Saved {code} permission fact", match=["authorized to work"],
        )
        for code, country in (
            ("US", "the United States"), ("SG", "Singapore"), ("GB", "the United Kingdom"),
        )
    }
    config = replace(workspace.config, answers=AnswersFile(answers=list(entries.values())))
    assert config.answer_for(question, jurisdiction=jurisdiction, options=options) is (
        entries[expected] if expected is not None else None
    )


@pytest.mark.parametrize(("scope", "company", "tracks", "ambiguous"), [
    ({"companies": ["Acme"]}, "Acme", (), True),
    ({"companies": ["Acme"]}, "Other", (), False),
    ({"companies": ["Acme"]}, None, (), False),
    ({"exclude_companies": ["Acme"]}, "Acme", (), False),
    ({"tracks": ["hardware"]}, None, ("hardware",), True),
    ({"tracks": ["hardware"]}, None, ("other",), False),
    ({"tracks": ["hardware"]}, None, (), False),
    ({"exclude_tracks": ["hardware"]}, None, ("hardware",), False),
])
def test_unresolved_permission_ambiguity_respects_company_and_track_scope(
    workspace, scope, company, tracks, ambiguous,
):
    permission = AnswerEntry(
        question="Are you legally authorized to work in the US?", answer="Yes",
        match=["authorized to work"], **scope,
    )
    sponsorship = AnswerEntry(
        question="Will you require sponsorship?", answer="No", match=["sponsorship"],
    )
    config = replace(workspace.config, answers=AnswersFile(answers=[permission, sponsorship]))
    question = "Are you authorized to work and do you require sponsorship?"
    assert config.answer_for(question, company, tracks) is (
        None if ambiguous else sponsorship
    )


@pytest.mark.parametrize(("location", "country", "expected"), [
    ("Chicago, IL", None, "US"),
    ("Chicago; Austin, TX", None, "US"),
    ("Chicago; London", None, None),
    ("Chicago, London", None, None),
    ("Chicago; London", "United States", None),
    ("Chicago", "Singapore", None),
    ("Singapore", "United States", None),
    ("Remote", None, None),
    ("Remote", "US", "US"),
    (None, None, None),
    (None, "US", "US"),
    ("Chicago; Unknown office", None, None),
    ("Chicago; Remote", None, None),
    ("Singapore", "Singapore", "SG"),
    ("United Kingdom", None, "GB"),
    ("London, United Kingdom", None, None),
    ("London", None, None),
    ("London", "Singapore", None),
    ("Contact us", None, None),
    ("London, Singapore", "Singapore", None),
])
def test_authorization_jurisdiction_requires_consistent_exclusive_evidence(location, country, expected):
    from kitty.core.discovery.text import authorization_jurisdiction

    assert authorization_jurisdiction(location, country) == expected


@pytest.mark.parametrize("question", [
    "What is your citizenship?",
    "What is your nationality?",
    "Have you previously held citizenship in the United States?",
    "What is your country of birth?",
    "Are you a US person for export-control purposes?",
])
def test_identity_answers_do_not_depend_on_job_jurisdiction(workspace, question):
    entry = AnswerEntry(question=question, answer="My saved fact", choices=["United States"])
    config = replace(workspace.config, answers=AnswersFile(answers=[entry]))
    assert config.answer_for(question) is entry
    assert config.answer_for(question, jurisdiction="SG") is entry


def test_permission_scope_keeps_general_device_source_and_specificity(workspace):
    entries = [
        AnswerEntry(question="Do you have a device?", answer="Yes", match=["device"]),
        AnswerEntry(question="How did you hear about us?", answer="Website", match=["hear about"]),
        AnswerEntry(question="What device?", answer="Laptop", match=["device"], companies=["Acme"]),
        AnswerEntry(question="Device preference?", answer="Desktop", match=["device"], tracks=["hardware"]),
    ]
    config = replace(workspace.config, answers=AnswersFile(answers=entries))
    assert config.answer_for("Which device do you own?", jurisdiction="SG").answer == "Yes"
    assert config.answer_for("How did you hear about this job?", jurisdiction="SG").answer == "Website"
    assert config.answer_for("Which device do you own?", "Acme", jurisdiction="SG").answer == "Laptop"
    assert config.answer_for("Which device do you own?", tracks=["hardware"], jurisdiction="SG").answer == "Desktop"
    assert config.answer_for("Which device do you own?", "Acme", ["hardware"], jurisdiction="SG") is None


def test_field_planning_preserves_country_evidence_without_using_profile_residence(workspace):
    from kitty.core.apply.fields import FormField, lookup_answer, plan_for

    config = _jurisdiction_config(workspace)
    config = replace(config, profile=config.profile.model_copy(update={
        "country": "United States", "work_authorization": "Yes",
    }))
    generic = FormField(key="auth", kind="radio", label="Are you authorized to work here?",
                        options=("Yes", "No"))
    singapore = replace(generic, options=("Authorized to work in Singapore", "Not authorized"))
    assert plan_for(generic, config) is None
    assert plan_for(generic, config, jurisdiction="US") is not None
    assert plan_for(singapore, config, jurisdiction="US") is None
    assert lookup_answer(config, generic.label, jurisdiction="US", options=singapore.options) is None
    explicit_us = replace(generic, label="Are you authorized to work in the United States?")
    assert plan_for(explicit_us, config) is not None


@pytest.mark.parametrize(("place", "jurisdiction", "expected"), [
    ("", "US", True),
    ("", "SG", False),
    ("", None, False),
    (" in Singapore", "US", False),
    (" in Wakanda", "US", False),
])
def test_relative_application_country_clause_uses_only_proven_jurisdiction(
    workspace, place, jurisdiction, expected,
):
    from kitty.core.apply.fields import lookup_answer

    config = _jurisdiction_config(workspace)
    question = (
        "Are you legally authorized to work in the country in which you are applying"
        f"{place}?"
    )
    answer = lookup_answer(config, question, company="Vertiv", jurisdiction=jurisdiction)
    assert answer is (config.answers.answers[0] if expected else None)
