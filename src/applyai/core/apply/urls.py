"""Which applicant tracking system a posting uses, and where its form lives.

Kept apart from filler.py so screens can ask "is this Workday?" without
importing the browser code.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from applyai.models import Job


def detect_ats(url: str) -> str:
    """Which applicant tracking system a URL belongs to, from its host (port ignored)."""
    host = urlsplit(url).hostname or ""
    if "greenhouse.io" in host or "gh_jid=" in url:
        return "greenhouse"
    if host.endswith("lever.co"):
        return "lever"
    if host.endswith("ashbyhq.com"):
        return "ashby"
    if host.endswith("smartrecruiters.com"):
        return "smartrecruiters"
    if host.endswith("workable.com"):
        return "workable"
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        return "workday"
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
