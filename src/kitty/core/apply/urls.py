"""Which applicant tracking system a posting uses, and where its form lives.

Kept apart from filler.py so screens can ask "is this Workday?" without
importing the browser code. Also the URL half of the entry router
(proposal-05 Step 3): which opaque redirect a stored URL is, which flows are
explicit handoffs, and the tracking-free form of a resolved employer URL.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from kitty.models import Job
from kitty.normalize import TRACKING_PARAMS

# Platform signatures observed on employer domains (N2 entry probe, 2026-11-04).
# Eightfold PCS: /careers/job/<pid> postings and /careers/apply?pid=<pid> forms.
_EIGHTFOLD_POSTING = re.compile(r"/careers/job/\d+/?")
# Oracle Recruiting Cloud served from a company domain: /en/sites/CX_2001/job/<id>.
_ORACLE_VANITY = re.compile(r"/[a-z]{2}(?:-[A-Z]{2})?/sites/CX(?:_\d+)?/job/\d+(?:/.*)?")
# Phenom fronts: /us/en/job/<id>/<slug>.
_PHENOM_POSTING = re.compile(r"/[a-z]{2}/[a-z]{2}/job/[A-Za-z]*\d+[A-Za-z0-9_-]*(?:/.*)?")
# Avature portals: /en_US/<portal>/JobDetail/<slug>/<id>.
_AVATURE_POSTING = re.compile(r"/[a-z]{2}_[A-Z]{2}/[^/]+/JobDetail/.+")


def _owned(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def detect_ats(url: str) -> str:
    """Recognize provider-owned hosts, explicit posting IDs and observed platform paths.

    Custom employer hosts remain ``other`` unless they carry ``gh_jid`` or the
    exact path signature of a platform N2 confirmed behind employer domains
    (Eightfold PCS, Oracle Recruiting Cloud, Phenom, Avature); preflight still
    verifies their actual application entry controls.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    owned = lambda domain: _owned(host, domain)
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
    if owned("icims.com"):
        return "icims"
    if owned("eightfold.ai"):
        return "eightfold"
    if owned("phenompeople.com"):
        return "phenom"
    if owned("avature.net"):
        return "avature"
    if parts.scheme != "https" or not host or owned("oraclecloud.com"):
        return "other"
    path = parts.path
    if _EIGHTFOLD_POSTING.fullmatch(path) or (
            path.rstrip("/") == "/careers/apply" and
            any(name == "pid" and value.isdigit() for name, value in parse_qsl(parts.query))):
        return "eightfold"
    if _ORACLE_VANITY.fullmatch(path):
        return "oracle"
    if _PHENOM_POSTING.fullmatch(path):
        return "phenom"
    if _AVATURE_POSTING.fullmatch(path):
        return "avature"
    return "other"


#: Opaque job-board redirects whose employer URL kitty resolves by loading them.
REDIRECT_SOURCES = {
    "zapply.jobs": "zapply", "recruitics.com": "recruitics", "appcast.io": "appcast",
    "trylitos.com": "trylitos", "indeed.com": "indeed",
}


def redirect_source(url: str) -> str | None:
    """The opaque redirect/aggregator a stored URL points into, or None."""
    host = (urlsplit(url).hostname or "").lower()
    return next((name for domain, name in REDIRECT_SOURCES.items() if _owned(host, domain)), None)


#: Prefix of every explicit-exclusion handoff note ("unsupported_flow: <kind>: <why>").
UNSUPPORTED_FLOW = "unsupported_flow:"

# Excluded flows (proposal-05 Step 3): typed handoffs, never attempted.
_EXCLUDED: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("indeed.com",), "indeed_apply",
     "Indeed's own apply form (Indeed Apply) is not automated; apply on Indeed yourself "
     "or give the job the employer's posting URL."),
    (("tesla.com",), "bot_wall",
     "Tesla's careers site walls automated browsers; apply there yourself."),
    (("lifeattiktok.com", "tiktok.com", "bytedance.com", "amazon.jobs", "metacareers.com"), "employer_portal",
     "this is the employer's own login portal, which has no kitty adapter; apply there yourself."),
    (("paradox.ai",), "conversational",
     "Paradox applications are a chat, not a form; complete it yourself."),
    # Resolution replaces a Litos URL with the employer's posting when it finds one.
    (("trylitos.com",), "third_party_account",
     "Litos's Apply button is Litos's own account funnel, and this job has no resolved unique "
     "posting on the employer's board; find it there and set the job's apply URL."),
)


def unsupported_flow(url: str) -> str | None:
    """The typed handoff note for an explicitly excluded flow, or None."""
    host = (urlsplit(url).hostname or "").lower()
    for domains, kind, why in _EXCLUDED:
        if any(_owned(host, domain) for domain in domains):
            return f"{UNSUPPORTED_FLOW} {kind}: {host}: {why}"
    return None


def clean_url(url: str) -> str:
    """The URL without tracking parameters (utm_*, _ccid, ...) or fragment; path untouched."""
    parts = urlsplit(url.strip())
    kept = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if not TRACKING_PARAMS.match(name)]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/",
                       urlencode(kept), ""))


_POSTING_ID = re.compile(r"\d{4,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def names_posting(url: str) -> bool:
    """True when the URL identifies one posting (an id in its path or query, or a
    Workday /job/ path), not a careers home page, board index or search."""
    parts = urlsplit(url)
    return bool(_POSTING_ID.search(parts.path + "?" + parts.query)) or (
        detect_ats(url) == "workday" and "job" in parts.path.split("/"))


def route_apply_link(page_url: str, destination: str) -> tuple[str | None, str | None]:
    """Route a custom site's visible Apply link by its destination.

    Returns ``(url to open, None)`` for a posting/application on a recognized
    family, ``(None, handoff note)`` for an excluded flow, else ``(None, None)``.
    Only pages kitty does not already recognize are routed, and only to a URL
    that names one posting, so a header "Apply" link to a board's home page is
    never followed.
    """
    if flow := unsupported_flow(destination):
        return None, flow
    parts = urlsplit(destination)
    if (detect_ats(page_url) != "other" or detect_ats(destination) == "other" or parts.scheme != "https"
            or parts.username or parts.password or not names_posting(destination)):
        return None, None
    return clean_url(destination), None


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
