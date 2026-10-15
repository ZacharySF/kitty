"""Resume coach: what a track's postings ask for, checked against its resume."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from kitty.config import ResumeTermsFile, parse_file
from kitty.core import jobs as jobs_core
from kitty.core import resumes as resumes_core
from kitty.core.resume_coach import coach, compile_vocabulary

REPO_ROOT = Path(__file__).resolve().parents[1]

#: A tiny vocabulary so the counts below are easy to check by hand.
VOCABULARY = ResumeTermsFile.model_validate({"groups": [
    {"name": "languages", "terms": [
        {"term": "C", "cased": ["C"]},
        {"term": "C++", "aliases": ["C++"]},
        {"term": "Python", "aliases": ["Python"]},
        {"term": "Verilog", "aliases": ["Verilog"]},
        {"term": "Rust", "cased": ["Rust"]},
    ]},
]})

RESUME = """Jane Student
Skills
Programming: Python, MATLAB
Built a drone in my garage
"""


@pytest.fixture
def space(workspace):
    """The workspace plus a track with a registered .txt resume and the tiny vocabulary."""
    workspace.track = next(t for t in workspace.config.tracks.tracks if t.default_resume)
    path = workspace.settings.root / "resume.txt"
    path.write_text(RESUME, encoding="utf-8")
    resumes_core.add_resume(workspace.conn, workspace.track.default_resume, path)
    workspace.config = replace(workspace.config, resume_terms=VOCABULARY)
    return workspace


def _posting(space, company, jd_text, *, posted_at="2026-09-01T00:00:00Z", **kwargs):
    """A job on the test track with this description."""
    job, _ = jobs_core.upsert_job(
        space.conn, company=company, title="Engineering Intern",
        url=f"https://example.invalid/{company.replace(' ', '-')}",
        jd_text=jd_text, posted_at=posted_at, tracks=[space.track.name], **kwargs,
    )
    return job


def _report(space, **kwargs):
    (report,) = coach(space.conn, space.config, space.settings, track=space.track.name, **kwargs)
    return report


def test_shares_count_each_posting_once_and_split_missing_from_covered(space):
    _posting(space, "A Co", "Python and more Python. Python everywhere.", posted_at="2026-09-01T00:00:00Z")
    newest = _posting(space, "B Co", "We use Python for test benches.", posted_at="2026-09-05T00:00:00Z")
    _posting(space, "C Co", "Python scripting is a plus.", posted_at="2026-09-03T00:00:00Z")
    verilog = _posting(space, "D Co", "Write RTL in Verilog and simulate it.", posted_at="2026-09-02T00:00:00Z")

    report = _report(space)

    assert report.error is None
    assert report.postings == 4
    assert report.resume_label == space.track.default_resume
    (python,) = report.covered
    assert (python.term, python.postings, python.share) == ("Python", 3, 0.75)
    assert python.resume_line == "Programming: Python, MATLAB"
    assert python.example_job_id == newest.id               # newest posting that mentions it

    (missing,) = report.missing                              # Rust: in no posting, not listed
    assert (missing.term, missing.postings, missing.share, missing.on_resume) == ("Verilog", 1, 0.25, False)
    assert missing.resume_line is None
    assert (missing.example_job_id, missing.example_company) == (verilog.id, "D Co")
    assert "Verilog" in missing.example_snippet and "\n" not in missing.example_snippet


def test_min_share_and_top_trim_the_lists(space):
    for index in range(9):
        _posting(space, f"Py Co {index}", "Python and C++ required.")
    _posting(space, "Rust Co", "Rust preferred.")

    report = _report(space, min_share=0.2)
    assert [s.term for s in report.missing] == ["C++"]       # Rust is 1/10 < 0.2

    report = _report(space, min_share=0.0, top=1)
    assert [s.term for s in report.missing] == ["C++"]       # most common first, capped
    assert [s.term for s in report.covered] == ["Python"]


def test_c_matches_only_the_language_not_cpp_csharp_or_prose(space):
    _posting(space, "Cpp Co", "Strong C++ and C# skills; Level C clearance; brief the C-suite.")
    _posting(space, "List Co", "Experience with C/C++ or Rust.")
    _posting(space, "Prose Co", "Programming in Python, C, or Go.")

    stats = {s.term: s for s in _report(space).missing}

    assert stats["C"].postings == 2                           # List Co and Prose Co only
    assert stats["C"].example_company in {"List Co", "Prose Co"}
    assert stats["C++"].postings == 2


def test_pdf_letter_spaced_headings_do_not_count_as_c(space):
    path = space.settings.root / "resume.txt"
    path.write_text("E D U C A T I O N\nT E C H N I C A L  S K I L L S\nPython\n", encoding="utf-8")
    _posting(space, "List Co", "Experience with C/C++ or Python.")

    report = _report(space)

    assert "C" in {s.term for s in report.missing}
    assert {s.term for s in report.covered} == {"Python"}


def test_ineligible_closed_and_empty_postings_are_not_counted(space):
    counted = _posting(space, "Open Co", "Python required.", eligibility="eligible")
    _posting(space, "Far Co", "Verilog required.", eligibility="ineligible")
    closed = _posting(space, "Closed Co", "Verilog required.")
    jobs_core.close_job(space.conn, closed.id)
    _posting(space, "Blank Co", "   ")

    report = _report(space)

    assert report.postings == 1
    assert [s.example_job_id for s in report.covered] == [counted.id]
    assert report.missing == []


def test_a_missing_resume_file_is_reported_not_raised(space):
    _posting(space, "A Co", "Python required.")
    (space.settings.root / "resume.txt").unlink()

    report = _report(space)

    assert report.error and "not found" in report.error
    assert report.postings == 1
    assert report.missing == report.covered == []


def test_an_unregistered_label_says_how_to_register_it(space):
    _posting(space, "A Co", "Python required.")
    space.conn.execute("DELETE FROM resumes")

    report = _report(space)

    assert f"--label {space.track.default_resume}" in report.error


def test_a_track_with_no_postings_explains_itself(space):
    report = _report(space)
    assert report.postings == 0
    assert report.error and "no active postings" in report.error


def test_shipped_vocabulary_ignores_everyday_english():
    vocabulary = parse_file(REPO_ROOT / "config" / "resume_terms.yaml.example", ResumeTermsFile)
    prose = (
        "You can go see the solar system. Our leadership team meets the C-suite at "
        "Level C. Bring your arm. Complete employment verification and documentation "
        "requirements. Join our R&D group. Networking events every Friday."
    )
    found = [m.term for m in compile_vocabulary(vocabulary) if m.find(prose)]
    assert found == []
