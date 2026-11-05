"""The submit-proof action layer's rules (research note N3): nothing kitty does
and nothing the page does while kitty fills a tab may submit an application.

Everything here is pure (no I/O, no clock, no state); kitty.core.apply.bidi
applies it. Three layers share one set of action intents:

- L0, action admission (N3 §4.2). Every pointer click, typed text and key press
  declares an intent. The click inspection script (`INSPECT_CLICK_JS`) computes
  the control a click would activate (a label resolves to its control, shadow
  roots included) and `RULES_JS.refusal` refuses it for that intent: a `FIELD`
  click never activates a submit control, a link or a final-labelled action.
  `untypeable()` refuses Enter, Tab, every control character and every
  WebDriver special key before anything is typed; `KEYS` is the only set of
  keys `press()` sends, and Enter only under `LIST_COMMIT` (react-select's own
  condition: an open list with a focused option). In-page activations go
  through `ACTIVATE_JS` (`kittyActivate`), which applies the same refusal and
  sets the one-shot token first.
- L1, the in-page veto (N3 §4.3): `GUARD_JS` installs window-capture
  listeners for click, keydown/keypress/keyup and submit in kitty's private
  sandbox realm (a preload script for new documents, and every current frame),
  never page-realm prototype patches. While its lease is live it cancels any
  click that would activate a submit-capable or final-labelled control without
  a matching one-shot token, any click that misses the token's element, every
  Enter except a `LIST_COMMIT` one, Space on button-like controls, keys that
  reach anything but the field kitty is typing into, and form submissions no
  `CREDENTIAL`/`PROFILE_GATE` token covers. Each veto is logged without labels
  or values. `veto_cause()` reads the log: a trusted click or key with no kitty
  token live means the owner acted (`owner_took_over`); any other veto is a
  defect or a page that tried to submit (`submit_vetoed`).
- L2, the network tripwire's request policy (N3 §4.4-4.5): while kitty fills a
  tab, Firefox holds every request of that tab at `network.beforeRequestSent`
  and kitty answers each one at once with `continueRequest` or
  `failRequest`. `policy()` is that answer; kitty.core.apply.bidi arms it per
  tab for the whole fill (`Tab.arm`) and releases it before the tab is handed
  over. A failed request stops the fill (`SubmitBlockedNetwork`, cause
  `submit_blocked_network`) and the tab stays open for the owner.

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

#: Intents a pointer click or an in-page activation may declare (N3 §4.2).
CLICK_INTENTS = frozenset({FIELD, ENTRY, NEXT, CREDENTIAL, PROFILE_GATE, RECORD_SAVE, MAILBOX})
#: Intents a key press may declare: list navigation, or committing an open list's focused option.
KEY_INTENTS = frozenset({FIELD, LIST_COMMIT})

#: How a profile's allowlist is applied to unlisted non-GET requests.
#: Deny rules are enforced in both modes.
AllowlistMode = Literal["enforce", "observe"]

#: The survey stage (N3 §4.5 staging): these families' allowlists are inferred,
#: not observed, so an unlisted non-GET continues (recorded as observed) until
#: the dry-run survey has seen their upload paths. Every deny rule, the generic
#: submit pattern and the navigation-POST rule stay enforced for them.
OBSERVED_ALLOWLISTS = frozenset({"greenhouse", "lever", "smartrecruiters"})

#: Intents whose action may need a network allowance (policy rules 3 and 4):
#: while kitty performs one, its tab holds a same-origin token for it.
NETWORK_INTENTS = frozenset({CREDENTIAL, PROFILE_GATE, NEXT})
#: How long such a token outlives the action that caused its requests.
NETWORK_TOKEN_SECONDS = 10.0

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Firefox 155 gives top-level and iframe form posts a navigation id
# (tests/test_bidi_events.py); a document destination still counts as a
# navigation should one ever arrive without an id (the safe direction).
_DOCUMENT_DESTINATIONS = frozenset({"document", "iframe", "frame", "embed", "object"})
_NAVIGATION_TOKENS = frozenset({CREDENTIAL, PROFILE_GATE})
_REQUEST_TOKENS = frozenset({CREDENTIAL, PROFILE_GATE})

#: N3 §4.5 custom row: a path or query parameter name that looks like a submit.
GENERIC_SUBMIT = re.compile(r"submit|submission|finali[sz]e|sendapplication|applications?/?$", re.IGNORECASE)
#: The same for a GET navigation (N3 §8 R11, a legacy site that submits by GET): the
#: submit verbs only. A page whose path ends in "application(s)" is where a form
#: is read and filled (seen in the account fixtures' post-login redirect), not a send.
GENERIC_SUBMIT_GET = re.compile(r"submit|submission|finali[sz]e|sendapplication", re.IGNORECASE)


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
    "custom": Profile("custom", deny=(
        _generic(), Rule("generic.submit", path=GENERIC_SUBMIT_GET, query=GENERIC_SUBMIT_GET, navigations=True),
    ), unlisted=CONTINUE),
}

_GENERIC = _generic()


def profile_for(family: str) -> Profile:
    """The profile for kitty's ATS classification (`urls.detect_ats`); unknown → custom."""
    return PROFILES.get(family, PROFILES["custom"])


def allowlist_mode(family: str) -> AllowlistMode:
    """How strictly a family's allowlist applies during fills (`OBSERVED_ALLOWLISTS`)."""
    return "observe" if family in OBSERVED_ALLOWLISTS else "enforce"


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


_UUID_SEGMENT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_HEX_SEGMENT = re.compile(r"[0-9a-f]{16,}", re.IGNORECASE)


def path_template(path: str) -> str:
    """A decoded URL path with its identifier-like segments replaced: the endpoint's shape.

    Numbers become {n}, UUIDs {uuid}, long hex strings {hex}, and any other
    segment holding an @, more than 40 characters, or a digit (beyond a short tag
    such as v2) becomes {id}. Evidence records (survey observations, passive
    learning) keep this template, never the candidate or record ids a path names.
    """
    def template(segment: str) -> str:
        if not segment:
            return segment
        if segment.isdigit():
            return "{n}"
        if _UUID_SEGMENT.fullmatch(segment):
            return "{uuid}"
        if _HEX_SEGMENT.fullmatch(segment):
            return "{hex}"
        if "@" in segment or len(segment) > 40 or (len(segment) > 4 and any(c.isdigit() for c in segment)):
            return "{id}"
        return segment

    return "/".join(template(segment) for segment in path.split("/"))


# ------------------------------------------------------------ L0 and L1


class UnsafeAction(RuntimeError):
    """An action was refused: its target is not a form field, or it could submit."""


class SubmitGuardStop(Exception):
    """The submit guard says the fill must stop. Not an UnsafeAction: helpers that turn a
    refused interaction into a blocker must not swallow it."""


class SubmitVetoed(SubmitGuardStop):
    """The in-page veto blocked an activation kitty or the page caused during a fill."""


class OwnerTookOver(SubmitGuardStop):
    """The owner clicked or pressed a key in the tab while kitty was filling it."""


class SubmitBlockedNetwork(SubmitGuardStop):
    """L2 failed a request this tab sent during a fill (a possible submit): the fill stops."""


#: Fill-report notes (and their attempt causes, kitty.core.apply.attempts).
OWNER_TOOK_OVER = "owner_took_over"
SUBMIT_VETOED = "submit_vetoed"
OWNER_NOTE = ("owner: you clicked or pressed a key in this tab while kitty was filling it, "
              "so kitty stopped filling this tab; if you pressed Submit, press Submit again.")
#: Added to an earlier stop's note when the owner also acted (that stop keeps its cause).
OWNER_HINT = "You also clicked or pressed a key in this tab during the fill; if you pressed Submit, press Submit again."
VETO_NOTE = ("vetoed: the submit guard blocked an action in this tab while kitty was filling it; "
             "kitty stopped. Review the tab before you submit.")
SUBMIT_BLOCKED_NETWORK = "submit_blocked_network"


def network_note(decision: Decision) -> str:
    """The fill-report note for a request L2 failed: method, path and rule only."""
    return (f"network: the submit guard blocked a request this tab sent while kitty was filling it "
            f"({decision.method} {decision.path}, rule {decision.rule}); kitty stopped and kept the tab "
            "for you. Review it before you submit.")


#: How long the in-page veto stays armed after kitty last renewed it (N3 §4.3):
#: every sandbox script kitty runs in the tab renews it, and so does a heartbeat,
#: so a fill keeps it armed; if kitty dies, it lapses by itself.
LEASE_MS = 15000
HEARTBEAT_SECONDS = 5.0

#: The only keys `press()` sends (WebDriver key codes). Never Tab or Space.
KEYS = {"ArrowDown": "\ue015", "ArrowUp": "\ue013", "Escape": "\ue00c", "Enter": "\ue007"}

_OWNER_KINDS = frozenset({"click", "keydown", "keypress", "keyup"})


def untypeable(text: str) -> str | None:
    """Why `text` must not be typed key by key (None: it may be).

    Refuses newlines and tabs, every C0/C1 control and the private-use range
    U+E000-U+F8FF, where WebDriver puts its special keys (U+E006/U+E007 Enter,
    U+E004 Tab, U+E00D Space). Multi-line answers are written without key events.
    """
    for char in text:
        code = ord(char)
        if code < 0x20 or 0x7F <= code <= 0x9F:
            return f"control character U+{code:04X}"
        if 0xE000 <= code <= 0xF8FF:
            return f"WebDriver key U+{code:04X}"
    return None


def veto_cause(entries: Iterable[Mapping]) -> str | None:
    """What a veto log says happened: `owner_took_over`, `submit_vetoed` or nothing.

    `isTrusted` cannot tell kitty's pointer from the owner's; a live kitty token
    can. A trusted click or key vetoed while no kitty token was live is the
    owner's own action. Every other veto (kitty's diverted click, a page script
    clicking or submitting) is a defect or a page that tried to submit.
    """
    cause = None
    for entry in entries:
        if entry.get("trusted") and not entry.get("token") and entry.get("kind") in _OWNER_KINDS:
            return OWNER_TOOK_OVER
        cause = SUBMIT_VETOED
    return cause


def stop_for(entries: Iterable[Mapping]) -> None:
    """Raise OwnerTookOver or SubmitVetoed when the veto log says the fill must stop."""
    cause = veto_cause(entries)
    if cause == OWNER_TOOK_OVER:
        raise OwnerTookOver(OWNER_NOTE)
    if cause is not None:
        raise SubmitVetoed(VETO_NOTE)


#: The activation rules shared by L0 (click inspection, kittyActivate) and L1
#: (the in-page listeners): one JS expression evaluating to an object.
#: WHATWG: a <button> without a valid type is a submit button; activating one
#: with a form owner submits that form; a label activates its control; a
#: cancelled click runs no activation behaviour. Labels and values are read only
#: to classify; `describe()` (what the veto log keeps) never includes them.
RULES_JS = r"""(() => {
  const parentOf = n => n.assignedSlot || n.parentElement || n.getRootNode?.()?.host || null;
  const text = node => {
    let out = '';
    const walk = n => {
      if (out.length > 200) return;
      if (n.nodeType === 3) { out += n.nodeValue + ' '; return; }
      if (n.nodeType === 1 && n.matches('script, style, template')) return;
      if (n.nodeType === 1 && n.matches('slot')) { for (const a of n.assignedNodes({flatten: true})) walk(a); return; }
      if (n.nodeType !== 1 && n.nodeType !== 11) return;
      for (const child of (n.shadowRoot || n).childNodes) walk(child);
    };
    walk(node);
    return out.replace(/\s+/g, ' ').trim();
  };
  const ACTIVATES = 'button, input, a[href], area[href], summary, [role=button], [role=link]';
  const BUTTONISH = 'button, input[type=submit], input[type=button], input[type=image], input[type=reset], ' +
    'a[href], area[href], summary, [role=button], [role=link]';
  // A label that starts with a submit verb (several locales), names the
  // application submit anywhere, or carries a CJK submit word. Letter
  // boundaries are Unicode-aware (`lähetä`, `prześlij`).
  const FINAL = /^(?:submit|send|finish|complete|apply|absenden|abschicken|senden|einreichen|bewerben|envoyer|soumettre|postuler|enviar|solicitar|candidatar|invia|inviare|verzenden|solliciteren|prześlij|indsend|skicka|lähetä|odeslat)(?![\p{L}\p{N}])|(?<![\p{L}\p{N}])(?:submit|absenden|abschicken|einreichen|soumettre|prześlij)(?![\p{L}\p{N}])|\b(?:send|finish|complete) (?:my |your |the )?application\b|\bbewerbung (?:absenden|abschicken|senden|einreichen)\b|\bjetzt bewerben\b|送信|提交|제출/iu;
  const element = n => !!n && n.nodeType === 1;
  const activating = n => element(n) && (n.matches(ACTIVATES) || (n.matches('label') && !!n.control));
  const resolve = n => n.matches('label') ? n.control : n;
  const targetFrom = node => {
    for (let n = node; n; n = parentOf(n)) if (activating(n)) return resolve(n);
    return null;
  };
  const targetOf = path => {
    for (const n of path) if (activating(n)) return resolve(n);
    return null;
  };
  const submitCapable = t => element(t) && !!t.form &&
    ((t.matches('button') && t.type === 'submit') || (t.matches('input') && (t.type === 'submit' || t.type === 'image')));
  const names = t => [text(t), t.value, t.getAttribute('aria-label'), t.getAttribute('title'), t.getAttribute('alt')];
  const final = t => element(t) && t.matches(BUTTONISH) &&
    names(t).some(s => typeof s === 'string' && FINAL.test(s.replace(/\s+/g, ' ').trim()));
  const formOf = e => {
    if (!element(e)) return null;
    if (e.form) return e.form;
    for (let n = e; n; n = parentOf(n)) if (element(n) && n.matches('form')) return n;
    return null;
  };
  const refusal = (t, intent) => {
    if (intent === 'field') {
      if (!t) return null;
      if (submitCapable(t)) return 'a submit control';
      if (t.matches('a[href], area[href], input[type=submit], input[type=image], input[type=reset]')) return 'a link or form action';
      return final(t) ? 'a final action' : null;
    }
    if (intent === 'entry') return submitCapable(t) ? 'a submit control' : null;
    if (intent === 'next') return submitCapable(t) ? 'a submit control' : final(t) ? 'a final action' : null;
    return ['credential', 'profile_gate', 'record_save', 'mailbox'].includes(intent) ? null : 'an undeclared intent';
  };
  const buttonLike = t => element(t) && t.matches(BUTTONISH);
  const textEntry = t => element(t) && (t.isContentEditable || t.matches('textarea') || (t.matches('input') &&
    !['button', 'submit', 'image', 'reset', 'checkbox', 'radio', 'file', 'color', 'range', 'hidden'].includes(t.type)));
  const listCommitReady = e => {
    if (!element(e) || e.getAttribute('role') !== 'combobox' || e.getAttribute('aria-expanded') !== 'true') return false;
    const root = e.getRootNode(), id = e.getAttribute('aria-activedescendant');
    const listId = e.getAttribute('aria-controls') || e.getAttribute('aria-owns');
    const option = id ? root.getElementById?.(id) : null;
    const list = listId ? root.getElementById?.(listId) : null;
    return element(option) && option.isConnected && option.getAttribute('role') === 'option' &&
      option.getClientRects().length > 0 && getComputedStyle(option).visibility !== 'hidden' &&
      element(list) && list !== option && list.contains(option);
  };
  const describe = t => element(t) ? {
    tag: t.tagName.toLowerCase(), type: (t.getAttribute('type') || '').toLowerCase().slice(0, 20),
    role: (t.getAttribute('role') || '').slice(0, 30), automation: (t.getAttribute('data-automation-id') || '').slice(0, 60),
  } : null;
  return {parentOf, targetFrom, targetOf, submitCapable, final, formOf, refusal, buttonLike, textEntry,
    listCommitReady, describe};
})()"""

#: L1: installs the in-page veto in this document's sandbox realm, or re-arms
#: an installed one. Argument: the lease in milliseconds. Page scripts can
#: neither see nor change `globalThis.kittyGuard` (it lives in kitty's sandbox).
#: The listeners stay registered after release but do nothing once the lease
#: has passed. Tokens: `grant` names the one element a click may activate (for
#: one intent, 5 s, consumed by the first matching click); `keys` names the one
#: element key events may reach; `settle` clears both and returns the log.
#: `ownerAt` is the wall time of the last trusted key press while no kitty keys
#: were bound (the owner typing in the tab; never which key): a lost connection
#: must not restart Firefox under the owner's hands.
GUARD_JS = r"""(lease) => {
  if (globalThis.kittyGuard) return globalThis.kittyGuard.arm(lease);
  const rules = """ + RULES_JS + r""";
  const now = () => performance.now();
  const g = {leaseUntil: 0, token: null, keys: null, chain: null, log: [], ownerAt: null};
  const armed = () => now() < g.leaseUntil;
  const liveToken = () => g.token && !g.token.used && now() < g.token.until ? g.token : null;
  // `kitty` says whether kitty caused the vetoed event; each listener decides it
  // for its own event before any token is consumed: the token's own click and a
  // click it caused are kitty's; an untokened click is not (even while kitty
  // types); a key is kitty's only while kitty's keys are bound; a submit only
  // inside the chain of kitty's click.
  const veto = (event, reason, target, kitty) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    g.log.push({t: Math.round(now()), kind: event.type, trusted: event.isTrusted,
      token: !!kitty, reason, target: rules.describe(target)});
    if (g.log.length > 100) g.log.splice(0, g.log.length - 100);
  };
  const exceptional = ['credential', 'profile_gate', 'record_save', 'mailbox'];
  // A click the token's own click caused in the same task (a label's control,
  // a proxy's hidden native button): it must pass the token's intent, and an
  // exception intent reaches a submit control only inside its proven form.
  const nestedRefusal = (target, chain) => {
    const refused = rules.refusal(target, chain.intent);
    if (refused) return refused;
    if (exceptional.includes(chain.intent) && target && (rules.submitCapable(target) || rules.final(target)) &&
        target !== rules.targetFrom(chain.el) &&
        (!rules.formOf(target) || rules.formOf(target) !== rules.formOf(chain.el))) return 'outside the proven form';
    return null;
  };
  window.addEventListener('click', event => {
    if (!armed()) return;
    const path = event.composedPath();
    const target = rules.targetOf(path);
    const token = liveToken();
    if (token && (event.isTrusted || path.includes(token.el))) {
      token.used = true;
      const proven = rules.targetFrom(token.el);
      const wrong = !path.includes(token.el) ? 'missed_target' : rules.refusal(target, token.intent) ? 'intent' :
        token.intent !== 'field' && target && target !== proven && rules.refusal(target, 'field') ? 'intent' : null;
      if (wrong) { token.vetoed = true; return veto(event, wrong, target, true); }
      g.chain = token;
      setTimeout(() => { if (g.chain === token) g.chain = null; }, 0);
      return;
    }
    if (g.chain) {
      if (nestedRefusal(target, g.chain)) { g.chain.vetoed = true; veto(event, 'nested', target, true); }
      return;
    }
    if (target && (rules.submitCapable(target) || rules.final(target))) veto(event, 'unguarded', target, false);
  }, {capture: true});
  const onKey = event => {
    if (!armed()) return;
    const target = event.composedPath()[0];
    const keys = g.keys;
    if (event.isTrusted && event.type === 'keydown' && !keys) g.ownerAt = Date.now();
    // A keyup that follows focus to the next text box (segmented code fields)
    // cannot activate anything; every other stray key is cancelled.
    if (keys && target !== keys.el && !(event.type === 'keyup' && rules.textEntry(target)))
      return veto(event, 'focus_moved', target, !!keys);
    if (event.key === 'Enter') {
      if (!(keys && keys.intent === 'list_commit' && target === keys.el &&
            (event.type !== 'keydown' || rules.listCommitReady(target)))) veto(event, 'enter', target, !!keys);
      return;
    }
    if ((event.key === ' ' || event.key === 'Spacebar') && rules.buttonLike(target)) veto(event, 'space', target, !!keys);
  };
  for (const type of ['keydown', 'keypress', 'keyup']) window.addEventListener(type, onKey, {capture: true});
  window.addEventListener('submit', event => {
    if (!armed()) return;
    const chain = g.chain, submitter = event.submitter || null;
    if (chain && (chain.intent === 'credential' || chain.intent === 'profile_gate') &&
        ((submitter && (submitter === chain.el || submitter === rules.targetFrom(chain.el))) ||
         event.target === rules.formOf(chain.el))) return;
    if (chain) chain.vetoed = true;
    veto(event, 'submit', submitter || event.target, !!chain);
  }, {capture: true});
  globalThis.kittyGuard = {
    armed,
    arm(ms) { g.token = null; g.keys = null; g.chain = null; g.leaseUntil = now() + ms; return true; },
    renew(ms) { if (g.leaseUntil) g.leaseUntil = Math.max(g.leaseUntil, now() + ms); return true; },
    grant(el, intent) { g.keys = null; g.token = {el, intent, until: now() + 5000, used: false, vetoed: false}; return true; },
    keys(el, intent) { g.token = null; g.keys = {el, intent}; return true; },
    mark() { return g.log.length; },
    settle(drain = true) {
      const token = g.token, log = g.log;
      g.token = null; g.keys = null;
      if (drain) g.log = [];
      return {used: !!token?.used, vetoed: !!token?.vetoed, log, ownerAt: g.ownerAt};
    },
    drain() { const log = g.log; g.log = []; return {log, ownerAt: g.ownerAt}; },
    release() {
      g.leaseUntil = 0; g.token = null; g.keys = null; g.chain = null;
      const log = g.log; g.log = [];
      return {armed: armed(), token: g.token !== null || g.keys !== null, log, ownerAt: g.ownerAt};
    },
  };
  return globalThis.kittyGuard.arm(lease);
}"""

#: The preload script (script.addPreloadScript, sandbox "kitty"): every new
#: document and iframe of an armed tab starts armed, before any page script runs.
PRELOAD_JS = "() => { (" + GUARD_JS + ")(" + str(LEASE_MS) + "); }"

#: L0 for in-page activations: every `.click()` or synthetic mouse event a kitty
#: script fires goes through `kittyActivate(element, intent, fire)`. It refuses
#: the activation for that intent (the same rule as a pointer click), names the
#: element in a one-shot token so the armed veto lets exactly it through, fires
#: synchronously, and throws if the veto blocked anything it caused.
ACTIVATE_JS = r"""(element, intent, fire) => {
  const rules = """ + RULES_JS + r""";
  const refused = rules.refusal(rules.targetFrom(element), intent);
  if (refused) throw new Error('kitty refused to activate ' + refused);
  const guard = globalThis.kittyGuard;
  if (intent === 'mailbox' && guard?.armed()) throw new Error('kitty refused a mailbox action in an application tab');
  const armed = !!guard?.armed();
  const mark = armed ? guard.mark() : 0;
  if (armed) guard.grant(element, intent);
  let settled = null, result;
  try { result = fire(); } finally { if (armed) settled = guard.settle(false); }
  if (settled && (settled.vetoed || settled.log.slice(mark).some(entry => entry.token)))
    throw new Error('the submit guard blocked an activation kitty caused');
  return result;
}"""

#: L0 for a pointer click (bidi.Frame.click), in one round trip before the
#: pointer moves: the element must be connected, visible and the topmost thing
#: at its centre; the control that point would activate must pass the intent;
#: for exception intents it must be the proven element's own control. Then the
#: one-shot token is set (armed tabs only). Returns JSON.
INSPECT_CLICK_JS = r"""(element, intent, armed) => {
  if (!element.isConnected) throw new Error('The requested control is detached');
  element.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
  const r = element.getClientRects()[0];
  if (!r?.width || !r.height) throw new Error('The requested control has no visible bounds');
  const x = Math.floor((Math.max(0, r.left) + Math.min(innerWidth, r.right)) / 2);
  const y = Math.floor((Math.max(0, r.top) + Math.min(innerHeight, r.bottom)) / 2);
  let hit = document.elementFromPoint(x, y);
  while (hit?.shadowRoot) {
    const inner = hit.shadowRoot.elementFromPoint(x, y);
    if (!inner || inner === hit) break;
    hit = inner;
  }
  const rules = """ + RULES_JS + r""";
  let inside = false;
  for (let n = hit; n; n = rules.parentOf(n)) if (n === element) { inside = true; break; }
  if (!inside) throw new Error('Another element covers the requested control');
  const target = rules.targetFrom(hit);
  const refused = rules.refusal(target, intent) || (intent !== 'field' && target &&
    target !== rules.targetFrom(element) && rules.refusal(target, 'field') ? 'another control inside it' : null);
  if (refused) return JSON.stringify({refused: 'a ' + intent + ' click would activate ' + refused});
  const guard = globalThis.kittyGuard;
  if (intent === 'mailbox' && guard?.armed()) return JSON.stringify({refused: 'a mailbox click in an application tab'});
  if (armed && !guard) return JSON.stringify({guard: false});
  if (armed) guard.grant(element, intent);
  return JSON.stringify({guard: !!guard});
}"""

#: L0 for typed text and key presses: typed text needs the remembered text box
#: itself to have the (deep) focus; a key press may reach a focused control
#: inside the remembered one (a combobox wrapper's input); Enter needs
#: react-select's condition. Key events are then bound to that focused
#: element (armed tabs only).
INSPECT_KEYS_JS = r"""(element, intent, armed, typing, commit) => {
  const rules = """ + RULES_JS + r""";
  let active = document.activeElement;
  while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
  let inside = !!active && active === element;
  if (!typing) for (let n = active; n && !inside; n = rules.parentOf(n)) inside = n === element;
  if (!element.isConnected || !inside) return JSON.stringify({refused: 'the focus is not on the field'});
  if (typing && !rules.textEntry(active)) return JSON.stringify({refused: 'the focused control is not a text box'});
  if (commit && !rules.listCommitReady(active))
    return JSON.stringify({refused: 'Enter outside an open list with a focused option'});
  const guard = globalThis.kittyGuard;
  if (armed && !guard) return JSON.stringify({guard: false});
  if (armed) guard.keys(active, intent);
  return JSON.stringify({guard: !!guard});
}"""
