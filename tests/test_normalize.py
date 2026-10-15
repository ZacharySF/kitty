"""Dedupe and normalization: the rules that decide what counts as one job."""

from kitty.normalize import (
    canonical_url,
    company_from_url,
    dedupe_key,
    norm_company,
    norm_location,
    norm_title,
    question_hash,
)


def test_company_suffixes_are_dropped():
    assert norm_company("Jane Street, Inc.") == norm_company("Jane Street")
    assert norm_company("Acme Technologies LLC") == "acme"


def test_titles_ignore_season_and_year_noise():
    assert norm_title("Quantitative Trader Intern (Summer 2027)") == norm_title(
        "Quantitative Trader Internship"
    )


def test_locations_collapse_state_names_and_remote():
    assert norm_location("Waco, Texas") == norm_location("Waco, TX")
    assert norm_location("Remote - US") == "remote"
    assert norm_location(None) == ""


def test_same_posting_from_two_boards_shares_a_dedupe_key():
    first = dedupe_key("NVIDIA Corporation", "GPU Software Intern", "Santa Clara, California")
    second = dedupe_key("NVIDIA", "GPU Software Internship (Summer 2027)", "Santa Clara, CA")
    assert first == second


def test_tracking_parameters_are_stripped_from_urls():
    assert canonical_url("https://Boards.Greenhouse.io/x/jobs/1?utm_source=a&gh_src=b") == (
        "https://boards.greenhouse.io/x/jobs/1"
    )
    # gh_jid is the job id on a company-hosted Greenhouse page: two postings, two URLs.
    assert canonical_url("https://acme.com/careers/?gh_jid=77&gh_src=x") == (
        "https://acme.com/careers?gh_jid=77"
    )


def test_company_guess_uses_path_for_ats_hosts_and_nothing_for_aggregators():
    assert company_from_url("https://boards.greenhouse.io/hudson-river-trading/jobs/1") == (
        "Hudson River Trading"
    )
    assert company_from_url("https://www.indeed.com/viewjob?jk=1") is None


def test_question_hash_ignores_case_and_punctuation():
    assert question_hash("Why this role?") == question_hash("why this role")
