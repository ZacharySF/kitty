"""Config validation, including the line numbers in error messages."""

from pathlib import Path

import pytest
import yaml

from applyai.config import (
    CONFIG_FILES,
    ConfigError,
    DEFAULT_VIEWS,
    Profile,
    TracksFile,
    WatchlistFile,
    load_config,
    parse_file,
)
from applyai.core import companies as companies_core

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
    from applyai.config import AnswersFile

    answers = parse_file(path, AnswersFile)
    assert answers.answers[0].answer == "Yes"
