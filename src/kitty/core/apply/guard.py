"""The network tripwire's request policy: may this request leave the browser?

This is layer L2 of the submit-proof action layer (research note N3, §4.4-4.5):
while kitty fills a tab, Firefox holds every request of that tab at
`network.beforeRequestSent` and kitty answers each one at once with
`continueRequest` or `failRequest`. `policy()` is that answer. It is pure: no
I/O, no clock, no state, so every rule can be tested on its own.

It reads only the request's method, URL origin and path (plus query parameter
*names*, and the value of Ashby's GraphQL `op` parameter), whether it is a
navigation (its navigation id, or a document/iframe destination), and its
browsing context. Headers, cookies, bodies and query values never enter a
`Request`, so they cannot be stored or logged from here.

Rules (N3 §4.4), first match wins:

1. GET, HEAD and OPTIONS that are not navigations continue.
2. GET navigations continue unless the family's deny list matches (only the
   custom profile denies GET navigations: a legacy site may submit by GET).
3. A navigation with any other method (an HTML form POST, `form.submit()`)
   fails unless a live CREDENTIAL or PROFILE_GATE token, or a NEXT token on a
   profile whose Next navigates by POST, covers the same origin, and the URL is
   not on the deny list.
4. Other non-GET requests (fetch, XHR, beacons) are decided by the family
   profile (N3 §4.5): deny rules first (always enforced), then
   credential-only endpoints, the allowlist, the generic submit pattern, a
   same-origin CREDENTIAL/PROFILE_GATE token, and finally the profile's
   verdict for unlisted requests.

The generic submit pattern backs up every profile, not only the custom and
Oracle rows of the §4.5 table: an unlisted request that looks like a submit
fails even while a family's allowlist is only observed (the survey stage).
That is stricter than the table, and a false positive only stops the fill.

Submit endpoints marked "expected, not observed" in N3 (Workday `…/submit`,
Oracle) and upload paths marked [INFERENCE] (Greenhouse, Lever) are encoded as
the note states them; the dry-run survey verifies them before L2 relies on the
allowlists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

#: The two answers Firefox accepts for a blocked request.
CONTINUE = "continue"
FAIL = "fail"
Verdict = Literal["continue", "fail"]

#: Action intents (N3 §4.2) that a one-shot token can name.
FIELD = "field"
ENTRY = "entry"
NEXT = "next"
CREDENTIAL = "credential"
PROFILE_GATE = "profile_gate"
RECORD_SAVE = "record_save"
MAILBOX = "mailbox"
LIST_COMMIT = "list_commit"

#: How a profile's allowlist is applied to unlisted non-GET requests.
#: Deny rules are enforced in both modes.
AllowlistMode = Literal["enforce", "observe"]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Firefox 155 gives top-level and iframe form posts a navigation id
# (tests/test_bidi_events.py); a document destination still counts as a
# navigation should one ever arrive without an id (the safe direction).
_DOCUMENT_DESTINATIONS = frozenset({"document", "iframe", "frame", "embed", "object"})
_NAVIGATION_TOKENS = frozenset({CREDENTIAL, PROFILE_GATE})
_REQUEST_TOKENS = frozenset({CREDENTIAL, PROFILE_GATE})

#: N3 §4.5 custom row: a path or query parameter name that looks like a submit.
GENERIC_SUBMIT = re.compile(r"submit|submission|finali[sz]e|sendapplication|applications?/?$", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    """What the policy may know about one request: never headers, cookies or bodies."""

    method: str
    origin: str                   # scheme://host[:port], lower case, default port dropped
    path: str                     # percent-decoded path, no query
    query_keys: frozenset[str]    # parameter names only
    operation: str | None         # the value of an `op` parameter (Ashby GraphQL), else None
    navigation: bool
    context: str | None

    @classmethod
    def from_event(cls, params: Mapping) -> Request:
        """Read a `network.beforeRequestSent` event's parameters."""
        request = params.get("request") or {}
        parts = urlsplit(str(request.get("url") or ""))
        query = parse_qsl(parts.query, keep_blank_values=True)
        operation = next((value for name, value in query if name == "op"), None)
        return cls(
            method=str(request.get("method") or "").upper(),
            origin=origin_of(parts.geturl()),
            path=unquote(parts.path) or "/",
            query_keys=frozenset(name for name, _ in query),
            operation=operation,
            navigation=(params.get("navigation") is not None
                        or request.get("destination") in _DOCUMENT_DESTINATIONS),
            context=params.get("context"),
        )


@dataclass(frozen=True)
class Token:
    """A live one-shot exception: one intent for one origin.

    The caller passes only tokens that are live (not expired, not consumed);
    lifetime and single use belong to the in-page veto (L1), not to this policy.
    """

    intent: str
    origin: str

    def covers(self, request: Request, intents: frozenset[str]) -> bool:
        return self.intent in intents and origin_of(self.origin) == request.origin


@dataclass(frozen=True)
class Decision:
    """The answer for one request, and the only record of it kitty may keep."""

    verdict: Verdict
    rule: str                     # the endpoint or rule id that decided
    method: str
    path: str
    observed: bool = False        # an unlisted request let through in observe mode

    def log_entry(self) -> dict[str, str]:
        """Method, path (no query), endpoint id and decision: nothing else is recorded."""
        return {"method": self.method, "path": self.path, "endpoint": self.rule,
                "decision": self.verdict}


@dataclass(frozen=True)
class Rule:
    """One endpoint of a family profile."""

    id: str
    path: re.Pattern[str] | None = None        # searched in the decoded path
    methods: frozenset[str] | None = None      # None: every non-GET method
    hosts: tuple[str, ...] = ()                # registrable domains; () = any host
    operations: re.Pattern[str] | None = None  # searched in the `op` parameter
    query: re.Pattern[str] | None = None       # searched in each query parameter name
    navigations: bool = False                  # also applies to GET navigations

    def matches(self, request: Request) -> bool:
        safe = request.method in _SAFE_METHODS
        if safe and not (self.navigations and request.navigation):
            return False
        if self.methods is not None and request.method not in self.methods:
            return False
        if self.hosts and not _on_hosts(request.origin, self.hosts):
            return False
        if self.operations is not None and not (
                request.operation is not None and self.operations.search(request.operation)):
            return False
        if self.path is None and self.query is None:
            return True
        return bool(
            (self.path is not None and self.path.search(request.path))
            or (self.query is not None and any(self.query.search(k) for k in request.query_keys))
        )


@dataclass(frozen=True)
class Profile:
    """One ATS family's endpoints (N3 §4.5)."""

    family: str
    deny: tuple[Rule, ...] = ()
    credential_only: tuple[Rule, ...] = ()     # allowed only under a same-origin CREDENTIAL token
    allow: tuple[Rule, ...] = ()
    unlisted: Verdict = FAIL                   # verdict for a non-GET no rule names
    deny_non_get_on_review: bool = False
    next_navigates_by_post: bool = False


def _generic(*, navigations: bool = False) -> Rule:
    return Rule("generic.submit", path=GENERIC_SUBMIT, query=GENERIC_SUBMIT, navigations=navigations)


_WORKDAY_HOSTS = ("myworkdayjobs.com", "myworkdaysite.com", "myworkday.com")
_GREENHOUSE_HOSTS = ("greenhouse.io",)
_LEVER_HOSTS = ("lever.co",)
_SMARTRECRUITERS_HOSTS = ("smartrecruiters.com",)
_ASHBY_HOSTS = ("ashbyhq.com",)
_ORACLE_HOSTS = ("oraclecloud.com",)
_POST = frozenset({"POST"})

PROFILES: dict[str, Profile] = {
    # workday.md:16-18, 24, 56, 67. The submit path is expected, not observed.
    "workday": Profile(
        "workday",
        deny=(Rule("workday.submit", path=re.compile(r"^/wday/.*/submit/?$", re.IGNORECASE), methods=_POST),),
        credential_only=(Rule("workday.credential", hosts=_WORKDAY_HOSTS,
                              path=re.compile(r"(?:^|/)(?:login|register)(?:/|$)", re.IGNORECASE)),),
        allow=(
            Rule("workday.draft", hosts=_WORKDAY_HOSTS, methods=_POST,
                 path=re.compile(r"/jobapplications/?$", re.IGNORECASE)),
            Rule("workday.questionnaire", hosts=_WORKDAY_HOSTS,
                 path=re.compile(r"/questionnaire/[^/]+/definition/?$", re.IGNORECASE)),
            Rule("workday.step_save", hosts=_WORKDAY_HOSTS, path=re.compile(r"^/wday/", re.IGNORECASE)),
        ),
        deny_non_get_on_review=True,
    ),
    # greenhouse.md:58: "Nothing but the files reaches Greenhouse before submit".
    # [INFERENCE] upload path, to confirm in the observe-only survey.
    "greenhouse": Profile(
        "greenhouse",
        allow=(Rule("greenhouse.upload", hosts=_GREENHOUSE_HOSTS,
                    path=re.compile(r"(?:^|/)(?:uploads?|attachments?|files?)(?:/|$)", re.IGNORECASE)),),
    ),
    # lever.md:48-49: "the form then posts to …/apply". [INFERENCE] resume path.
    "lever": Profile(
        "lever",
        deny=(Rule("lever.apply", path=re.compile(r"/apply/?$", re.IGNORECASE)),),
        allow=(Rule("lever.resume", hosts=_LEVER_HOSTS,
                    path=re.compile(r"(?:parse|upload)[-_]?resume", re.IGNORECASE)),),
    ),
    # smartrecruiters.md:57-58: "Next validates the profile in the browser and sends nothing".
    "smartrecruiters": Profile(
        "smartrecruiters",
        allow=(Rule("smartrecruiters.resume", hosts=_SMARTRECRUITERS_HOSTS, methods=_POST,
                    path=re.compile(r"^/oneclick-ui/api/company/[^/]+/attachment/resume/?$", re.IGNORECASE)),),
    ),
    # ashby.md:13, 51. Every operation naming a submit is denied (both listed ones included).
    "ashby": Profile(
        "ashby",
        deny=(Rule("ashby.submit", operations=re.compile(r"submit", re.IGNORECASE)),),
        allow=(
            Rule("ashby.set_form_value", hosts=_ASHBY_HOSTS,
                 path=re.compile(r"/non-user-graphql/?$"), operations=re.compile(r"^ApiSetFormValue$")),
            Rule("ashby.job_posting", hosts=_ASHBY_HOSTS,
                 path=re.compile(r"/non-user-graphql/?$"), operations=re.compile(r"^ApiJobPosting$")),
        ),
    ),
    # oracle.md:13-14, 61 (send never observed): generic deny before the draft endpoints.
    "oracle": Profile(
        "oracle",
        deny=(_generic(),),
        allow=(
            Rule("oracle.draft", hosts=_ORACLE_HOSTS, methods=frozenset({"POST", "PATCH"}),
                 path=re.compile(r"/recruitingCEJobApplicationDrafts(?:/|$)", re.IGNORECASE)),
            Rule("oracle.attachment", hosts=_ORACLE_HOSTS, path=re.compile(r"attachments?", re.IGNORECASE)),
        ),
    ),
    # Custom employer sites, redirects and every family without a profile.
    "custom": Profile("custom", deny=(_generic(navigations=True),), unlisted=CONTINUE),
}

_GENERIC = _generic()


def profile_for(family: str) -> Profile:
    """The profile for kitty's ATS classification (`urls.detect_ats`); unknown → custom."""
    return PROFILES.get(family, PROFILES["custom"])


def policy(event: Mapping | Request, family: str | Profile, tokens: Iterable[Token], *,
           review: bool = False, allowlist: AllowlistMode = "enforce") -> Decision:
    """Decide one blocked request: continue it or fail it.

    `family` is a `detect_ats` name (or a profile). `tokens` are the tab's live
    one-shot tokens. `review` is the page snapshot's review state (Workday:
    every non-GET fails on Review). `allowlist="observe"` lets unlisted non-GET
    requests continue, marked `observed`, while deny rules stay enforced.
    """
    if allowlist not in ("enforce", "observe"):
        raise ValueError(f"unknown allowlist mode {allowlist!r}")
    request = event if isinstance(event, Request) else Request.from_event(event)
    profile = family if isinstance(family, Profile) else profile_for(family)
    tokens = tuple(tokens)

    def decided(verdict: Verdict, rule: str, *, observed: bool = False) -> Decision:
        return Decision(verdict, rule, request.method, request.path, observed)

    def first(rules: Iterable[Rule]) -> Rule | None:
        return next((rule for rule in rules if rule.matches(request)), None)

    denied = first(profile.deny)
    if request.method in _SAFE_METHODS:
        if not request.navigation:
            return decided(CONTINUE, "safe_method")
        if denied is not None:
            return decided(FAIL, denied.id)
        return decided(CONTINUE, "get_navigation")

    if denied is not None:
        return decided(FAIL, denied.id)
    if profile.deny_non_get_on_review and review:
        return decided(FAIL, f"{profile.family}.review")
    looks_like_submit = _GENERIC.matches(request)

    if request.navigation:
        if looks_like_submit:
            return decided(FAIL, _GENERIC.id)
        intents = _NAVIGATION_TOKENS | ({NEXT} if profile.next_navigates_by_post else frozenset())
        token = next((t for t in tokens if t.covers(request, intents)), None)
        if token is not None:
            return decided(CONTINUE, f"token.{token.intent}")
        return decided(FAIL, "navigation_post")

    gated = first(profile.credential_only)
    if gated is not None:
        if any(t.covers(request, frozenset({CREDENTIAL})) for t in tokens):
            return decided(CONTINUE, f"token.{CREDENTIAL}")
        return decided(FAIL, gated.id)
    allowed = first(profile.allow)
    if allowed is not None:
        return decided(CONTINUE, allowed.id)
    if looks_like_submit:
        return decided(FAIL, _GENERIC.id)
    token = next((t for t in tokens if t.covers(request, _REQUEST_TOKENS)), None)
    if token is not None:
        return decided(CONTINUE, f"token.{token.intent}")
    if profile.unlisted == CONTINUE:
        return decided(CONTINUE, f"{profile.family}.unlisted")
    if allowlist == "observe":
        return decided(CONTINUE, f"{profile.family}.unlisted", observed=True)
    return decided(FAIL, f"{profile.family}.unlisted")


def origin_of(url: str) -> str:
    """scheme://host[:port] in lower case, without user info or a default port."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None and port != {"http": 80, "https": 443}.get(scheme):
        host = f"{host}:{port}"
    return f"{scheme}://{host}" if scheme else ""


def _on_hosts(origin: str, domains: tuple[str, ...]) -> bool:
    host = urlsplit(origin).hostname or ""
    return any(host == domain or host.endswith("." + domain) for domain in domains)
