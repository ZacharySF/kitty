"""Which applicant tracking system a posting uses, and where its form lives.

Kept apart from filler.py so screens can ask "is this Workday?" without
importing the browser code.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit, urlunsplit

from kitty.models import Job


def detect_ats(url: str) -> str:
    """Recognize provider-owned hosts, or an explicit Greenhouse posting ID.

    Custom employer hosts remain ``other`` unless they carry ``gh_jid``;
    preflight separately verifies their actual application entry controls.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    owned = lambda domain: host == domain or host.endswith("." + domain)
    if owned("greenhouse.io") or any(
            name == "gh_jid" and value.isdigit()
            for name, value in parse_qsl(parts.query)):
        return "greenhouse"
    if owned("lever.co"):
        return "lever"
    if owned("ashbyhq.com"):
        return "ashby"
    if owned("smartrecruiters.com"):
        return "smartrecruiters"
    if owned("workable.com"):
        return "workable"
    if owned("myworkdayjobs.com") or owned("myworkdaysite.com"):
        return "workday"
    if owned("oraclecloud.com") and parts.path.startswith("/hcmUI/CandidateExperience/"):
        return "oracle"
    if owned("dayforcehcm.com"):
        return "dayforce"
    if owned("successfactors.com") or owned("successfactors.eu"):
        return "successfactors"
    return "other"


def _with_path_suffix(url: str, suffix: str) -> str:
    """Append '/suffix' to a URL's path unless it is already there."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if not path.endswith("/" + suffix):
        path = f"{path}/{suffix}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def form_url(job: Job, ats: str) -> str:
    """The page holding the application form for this job.

    Greenhouse shows the form on the job page; Lever, Ashby and Workable put it
    on /apply, /application and /apply/. Anything else opens the stored apply
    URL or posting.
    """
    base = job.apply_url or job.url
    if ats in ("lever", "workable"):
        return _with_path_suffix(base, "apply")
    if ats == "ashby":
        return _with_path_suffix(base, "application")
    return base
