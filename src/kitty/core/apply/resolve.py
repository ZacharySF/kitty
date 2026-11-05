"""Reaching the form before any browser: liveness, redirect resolution, entry routing.

Proposal-05 Step 3 (N2 entry probe). For one stored job this module answers:
is the posting still open, which employer URL does an opaque job-board redirect
lead to, and is the flow an explicit handoff kitty never automates?

* **Liveness** markers, per family: HTTP 404/410 on the posting; a primary
  page notice such as "no longer accepting applications"; Greenhouse's
  ``?questions=true`` detail answering 404; Workday's public cxs detail
  answering 403 with error ``S22`` *and* the posting page serving an empty
  ``og:title`` shell (N4 §8: all 62 such postings were closed). Anything else
  (robots, 403/429 walls, network errors) is ``unknown`` and changes nothing.
* **Resolve-by-load**: zapply and recruitics by following HTTP redirects
  (zapply landing on its /jobs/ listing means the posting expired); appcast by
  its ``navigateTo(window.parent, window, "<url>")`` JavaScript literal;
  trylitos by the employer board in its ``company-logo?board=`` parameter plus
  an exact-title search of that board. The clean employer URL (no tracking
  parameters) becomes the job's apply URL.
* **Routing**: a custom page's static Apply link to a recognized family is
  routed by its destination (`urls.route_apply_link`); excluded flows (Indeed
  Apply, Tesla, employer-own portals, Paradox, unresolved Litos) are typed
  ``unsupported_flow:`` handoffs.

Every request is a public GET through `PublicPages` (public addresses only,
robots.txt checked at every redirect hop) paced by one `PoliteHttp` at one
request per second per host. Workday's board search is POST-only; robots.txt is
checked for its URL first and only exact ``*.myworkdayjobs.com`` hosts get it.
Closed jobs are closed with the existing posting-verification fields, so they
leave the queues; nothing here ever submits, clicks or signs in.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Callable, Literal
from urllib.parse import quote, unquote, urljoin, urlsplit

from kitty.core import jobs as jobs_core
from kitty.core.apply.state import record_attempt
from kitty.core.apply.urls import (
    UNSUPPORTED_FLOW, clean_url, detect_ats, names_posting, redirect_source, route_apply_link, unsupported_flow,
)
from kitty.core.discovery.http import HttpError, PoliteHttp
from kitty.core.discovery.public_pages import PageHTTPError, PublicPage, PublicPages
from kitty.core.evidence import merge_facts
from kitty.core.refresh import PostingCheck, persist_check
from kitty.db.connection import transaction
from kitty.models import Job
from kitty.settings import Settings

#: At most one request per second to any one host.
REQUEST_INTERVAL = 1.0
#: A resolve dry run followed by the real run (or a fill) reuses these answers.
CACHE_MINUTES = 60
#: The opaque redirects a job board lists can chain several hops.
MAX_REDIRECTS = 10
#: A resolved employer URL is direct evidence of where this posting's form lives;
#: it outranks the aggregator's redirect it was loaded from.
RESOLVED_AUTHORITY = 30

Status = Literal["live", "closed", "handoff", "unknown"]


@dataclass(frozen=True)
class EntryCheck:
    """What loading one stored job showed. Only ``closed`` and ``handoff`` act."""

    status: Status
    reason: str
    url: str                          # the stored URL that was checked
    resolved_url: str | None = None   # the clean employer URL, when it differs
    family: str = "other"             # detect_ats of the URL kitty will open
    source: str | None = None         # the opaque redirect the stored URL is, if any
    observed_at: str | None = None


@dataclass(frozen=True)
class EntryResult:
    job_id: int
    company: str
    title: str
    check: EntryCheck
    changes: tuple[str, ...]          # "close", "apply_url", "handoff": what was (or would be) written


class Net:
    """One run's fetchers: one pacing/cache state shared by GETs and the Workday search."""

    def __init__(self, http: PoliteHttp, pages: PublicPages):
        self.http = http
        self.pages = pages

    def get(self, url: str) -> PublicPage:
        return self.pages.fetch(url)

    def post_json(self, url: str, body: dict):
        self.pages.allowed(url)
        return self.http.post_json(url, body)

    def close(self) -> None:
        self.pages.close()
        self.http.close()


def open_net(settings: Settings) -> Net:
    http = PoliteHttp(settings.cache_dir / "apply-resolve", min_interval=REQUEST_INTERVAL,
                      cache_ttl_minutes=CACHE_MINUTES)
    return Net(http, PublicPages(http, max_redirects=MAX_REDIRECTS))


# ---------------------------------------------------------------- page reading

_CLOSED_TEXT = re.compile(
    r"\b(?:this (?:job|position|posting|requisition|role) (?:is (?:no longer (?:available|posted|open|active)|closed)"
    r"|has (?:closed|expired|been (?:closed|filled|removed)))"
    r"|(?:is )?no longer accepting (?:applications|applicants|candidates)"
    r"|(?:job|job posting|posting|position|requisition) (?:has expired|not found|no longer exists"
    r"|is no longer available|has been removed|has been filled)"
    r"|page (?:not found|(?:cannot|could not) be found)"
    r"|page you are looking for (?:doesn[’']t|does not) exist)\b",
    re.I,
)
_APPLY_LABEL = re.compile(
    r"^(?:apply|apply now|apply online|apply here|apply for (?:this )?(?:job|position|role)"
    r"|apply to (?:this )?job|apply on (?:the )?(?:company|employer) (?:site|website))$", re.I,
)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
         "source", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "template"}
#: Below this much text, a page's whole body is its notice (bare error pages).
_SHORT_PAGE = 1500


class _Scan(HTMLParser):
    """Headings, alerts, og:title and links of a static page (no scripts run)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.notices: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.og_title: str | None = None
        self.text: list[str] = []
        self._stack: list[list] = []      # [tag, kind, href, parts]
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "meta" and (values.get("property") or values.get("name") or "").lower() == "og:title":
            self.og_title = values.get("content", "").strip()
        if tag in _VOID:
            return
        kind = ("link" if tag == "a" and values.get("href") else
                "notice" if tag in {"title", "h1", "h2", "h3"} or values.get("role", "").lower() == "alert"
                else None)
        self._skipping += tag in _SKIP
        self._stack.append([tag, kind, values.get("href"), []])

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                while len(self._stack) > index:
                    self._finish(self._stack.pop())
                return

    def close(self) -> None:
        super().close()
        while self._stack:
            self._finish(self._stack.pop())

    def _finish(self, item: list) -> None:
        tag, kind, href, parts = item
        self._skipping -= tag in _SKIP
        text = " ".join("".join(parts).split())
        if kind == "link":
            self.links.append((href, text))
        elif kind == "notice" and text:
            self.notices.append(text)

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return
        for item in self._stack:
            if item[1]:
                item[3].append(data)
        self.text.append(data)


def _scan(page: PublicPage) -> _Scan:
    scan = _Scan()
    scan.feed(page.text)
    scan.close()
    return scan


def _closed_notice(scan: _Scan) -> str | None:
    """The page's primary notice saying the posting is gone, if any."""
    for notice in scan.notices:
        if _CLOSED_TEXT.search(notice):
            return notice[:160]
    body = " ".join(" ".join(scan.text).split())
    if len(body) <= _SHORT_PAGE and (match := _CLOSED_TEXT.search(body)):
        return match.group(0)
    return None


def _static_apply_link(scan: _Scan, base: str) -> str | None:
    """The single destination of the page's exact-label Apply links, if they agree."""
    targets = {urljoin(base, href).split("#", 1)[0] for href, text in scan.links
               if href and _APPLY_LABEL.match(text)}
    return targets.pop() if len(targets) == 1 else None


# ------------------------------------------------------------------- families

_GREENHOUSE_BOARDS = {"boards.greenhouse.io", "job-boards.greenhouse.io",
                      "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"}
_SLUG = re.compile(r"[A-Za-z0-9_-]+")
_WORKDAY_HOST = re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com")
_LOCALE = re.compile(r"[a-z]{2}-[A-Z]{2}")


def _greenhouse_posting(url: str, job: Job | None) -> tuple[str, str] | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = [part for part in parts.path.split("/") if part]
    query = dict(re.findall(r"([^&=]+)=([^&]*)", parts.query))
    if host in _GREENHOUSE_BOARDS:
        if len(path) >= 3 and path[1] == "jobs" and path[2].isdigit() and _SLUG.fullmatch(path[0]):
            return path[0], path[2]
        if query.get("for") and query.get("token", "").isdigit() and _SLUG.fullmatch(query["for"]):
            return query["for"], query["token"]
    identity = query.get("gh_jid", "")
    if identity.isdigit() and job is not None:
        for source in job.sources:
            slug, _, posting = (source.source_job_id or "").partition(":")
            if source.source == "greenhouse" and posting == identity and _SLUG.fullmatch(slug):
                return slug, identity
    return None


def _workday_posting(url: str) -> tuple[str, str] | None:
    """(cxs detail URL, public posting page URL) of a Workday posting URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    match = _WORKDAY_HOST.fullmatch(host)
    path = [part for part in parts.path.split("/") if part]
    if not match or parts.scheme != "https":
        return None
    page = list(path)
    while page and page[-1] in {"apply", "applyManually"}:
        page.pop()
    posting = page[1:] if page and _LOCALE.fullmatch(page[0]) else page
    if len(posting) < 3 or posting[1] != "job":
        return None
    tenant = match.group(1)
    cxs = f"https://{host}/wday/cxs/{tenant}/{posting[0]}/" + "/".join(posting[1:])
    return cxs, f"https://{host}/" + "/".join(page)


def _zapply_listing(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "").lower() == "zapply.jobs" and parts.path.rstrip("/") == "/jobs"


def _unknown(url: str, exc: Exception, family: str = "other") -> EntryCheck:
    observed = getattr(exc, "observed_at", None)
    return EntryCheck("unknown", f"{exc}; not closure evidence", url, family=family, observed_at=observed)


def _gone(url: str, exc: PageHTTPError, what: str, family: str) -> EntryCheck | None:
    if exc.status_code in {404, 410}:
        return EntryCheck("closed", f"{what} returned HTTP {exc.status_code}", url, family=family,
                          observed_at=exc.observed_at)
    return None


def _json(page: PublicPage):
    try:
        return json.loads(page.text)
    except ValueError:
        return None


def _greenhouse_live(url: str, slug: str, identity: str, net: Net) -> EntryCheck:
    api = f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs/{quote(identity)}?questions=true"
    try:
        answer = net.get(api)
    except PageHTTPError as exc:
        return _gone(url, exc, "Greenhouse ?questions=true", "greenhouse") or _unknown(url, exc, "greenhouse")
    except HttpError as exc:
        return _unknown(url, exc, "greenhouse")
    data = _json(answer)
    if isinstance(data, dict) and str(data.get("id", "")) == identity:
        return EntryCheck("live", "Greenhouse ?questions=true answers for this posting", url,
                          family="greenhouse", observed_at=answer.retrieved_at)
    return EntryCheck("unknown", "Greenhouse answered without this posting; not closure evidence", url,
                      family="greenhouse", observed_at=answer.retrieved_at)


def _workday_live(url: str, cxs: str, posting: str, net: Net) -> EntryCheck:
    try:
        answer = net.get(cxs)
    except PageHTTPError as exc:
        if gone := _gone(url, exc, "Workday cxs posting detail", "workday"):
            return gone
        if exc.status_code != 403 or not re.search(r"\bS22\b", exc.text):
            return _unknown(url, exc, "workday")
        # N4 §8: cxs 403 S22 plus an empty og:title shell was a closed posting 62 of 62 times.
        try:
            shell = net.get(posting)
        except HttpError as page_exc:
            return EntryCheck("unknown", f"Workday cxs 403 S22, but the posting page is unreadable ({page_exc})",
                              url, family="workday", observed_at=exc.observed_at)
        if not (_scan(shell).og_title or ""):
            return EntryCheck("closed", "Workday cxs answered 403 S22 and the posting page is an empty "
                              "og:title shell", url, family="workday", observed_at=shell.retrieved_at)
        return EntryCheck("unknown", "Workday cxs 403 S22, but the posting page has a title; not closure evidence",
                          url, family="workday", observed_at=shell.retrieved_at)
    except HttpError as exc:
        return _unknown(url, exc, "workday")
    data = _json(answer)
    if isinstance(data, dict) and isinstance(data.get("jobPostingInfo"), dict):
        return EntryCheck("live", "Workday cxs posting detail answers", url, family="workday",
                          observed_at=answer.retrieved_at)
    return EntryCheck("unknown", "Workday cxs answered without jobPostingInfo; not closure evidence", url,
                      family="workday", observed_at=answer.retrieved_at)


def liveness(url: str, net: Net, job: Job | None = None, *, page: PublicPage | None = None) -> EntryCheck:
    """Check one posting URL with its family's closed markers; route a custom page's Apply link."""
    family = detect_ats(url)
    if family == "greenhouse" and (posting := _greenhouse_posting(url, job)):
        return _greenhouse_live(url, *posting, net)
    if family == "workday" and (posting := _workday_posting(url)):
        return _workday_live(url, *posting, net)
    if page is None:
        try:
            page = net.get(url)
        except PageHTTPError as exc:
            return _gone(url, exc, "The posting page", family) or _unknown(url, exc, family)
        except HttpError as exc:
            return _unknown(url, exc, family)
    final = page.url
    if _zapply_listing(final):
        return EntryCheck("closed", "The link now lands on zapply's generic /jobs/ listing: the posting "
                          "expired upstream", url, family=family, observed_at=page.retrieved_at)
    scan = _scan(page)
    if notice := _closed_notice(scan):
        return EntryCheck("closed", f"The posting page says: {notice}", url, family=family,
                          observed_at=page.retrieved_at)
    resolved = None
    if clean_url(final) != clean_url(url) and (
            urlsplit(final).hostname != urlsplit(url).hostname or detect_ats(final) != family):
        if not names_posting(final):
            return EntryCheck("unknown", f"The posting redirects to {clean_url(final)}, which names no posting; "
                              "not closure evidence", url, family=family, observed_at=page.retrieved_at)
        resolved = clean_url(final)
        if detect_ats(resolved) in {"greenhouse", "workday"}:
            return replace(liveness(resolved, net, job), url=url, resolved_url=resolved)
    reason = "The posting page loads without a closed notice"
    if detect_ats(final) == "other" and (link := _static_apply_link(scan, final)):
        destination, flow = route_apply_link(final, link)
        if flow:
            return EntryCheck("handoff", flow, url, resolved_url=clean_url(link), family=detect_ats(link),
                              observed_at=page.retrieved_at)
        if destination:
            resolved = destination
            reason += f"; its Apply link leads to {detect_ats(destination)}"
    return EntryCheck("live", reason, url, resolved_url=resolved,
                      family=detect_ats(resolved or url), observed_at=page.retrieved_at)


# ------------------------------------------------------------------ resolvers

def _landed(url: str, destination: str, net: Net, job: Job, *, page: PublicPage | None = None) -> EntryCheck:
    """The redirect's employer destination: its liveness, or an excluded flow's handoff.

    An excluded destination is never requested just to check it, but a page the
    redirect already loaded can still show the posting closed.
    """
    clean = clean_url(destination)
    if flow := unsupported_flow(clean):
        if page is not None and (notice := _closed_notice(_scan(page))):
            return EntryCheck("closed", f"The posting page says: {notice}", url, resolved_url=clean,
                              family=detect_ats(clean), observed_at=page.retrieved_at)
        return EntryCheck("handoff", flow, url, resolved_url=clean, family=detect_ats(clean))
    check = liveness(clean, net, job, page=page)
    if check.resolved_url is None and not names_posting(clean):
        if check.status != "live":
            return replace(check, url=url)
        return EntryCheck("unknown", f"The redirect lands on {clean}, which names no posting", url,
                          family=check.family, observed_at=check.observed_at)
    return replace(check, url=url, resolved_url=check.resolved_url or clean)


def _follow(url: str, source: str, net: Net, job: Job) -> EntryCheck:
    """zapply and recruitics: their HTTP redirects lead to the employer posting."""
    try:
        page = net.get(url)
    except PageHTTPError as exc:
        return _gone(url, exc, f"The {source} link", "other") or _unknown(url, exc)
    except HttpError as exc:
        return _unknown(url, exc)
    if _zapply_listing(page.url):
        return EntryCheck("closed", "The zapply link now redirects to its generic /jobs/ listing: the posting "
                          "expired upstream", url, observed_at=page.retrieved_at)
    if redirect_source(page.url) == source:
        return EntryCheck("unknown", f"The {source} link did not redirect to an employer page", url,
                          observed_at=page.retrieved_at)
    return _landed(url, page.url, net, job, page=page)


_NAVIGATE = re.compile(r"navigateTo\(\s*window\.parent\s*,\s*window\s*,\s*([\"'])((?:\\.|(?!\1).)*)\1\s*\)", re.S)


def _js_string(value: str) -> str:
    def escape(match: re.Match) -> str:
        code = match.group(1)
        if code[0] in "ux":
            return chr(int(code[1:], 16))
        return {"n": "\n", "t": "\t", "r": "\r"}.get(code, code)
    return re.sub(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", escape, value)


def _wellformed(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    labels = host.split(".")
    return (parts.scheme in {"http", "https"} and len(labels) >= 2 and re.fullmatch(r"[a-z]{2,}", labels[-1])
            is not None and all(labels) and parts.username is None and parts.password is None)


def _appcast(url: str, net: Net, job: Job) -> EntryCheck:
    """appcast answers 200 with a JavaScript redirect; read its target literally."""
    try:
        page = net.get(url)
    except PageHTTPError as exc:
        return _gone(url, exc, "The appcast link", "other") or _unknown(url, exc)
    except HttpError as exc:
        return _unknown(url, exc)
    if redirect_source(page.url) != "appcast":
        return _landed(url, page.url, net, job, page=page)
    match = _NAVIGATE.search(page.text)
    if not match:
        return EntryCheck("unknown", "The appcast page names no redirect target", url, observed_at=page.retrieved_at)
    target = html.unescape(_js_string(match.group(2))).strip()
    if not _wellformed(target):
        host = urlsplit(target).hostname or target[:80]
        return EntryCheck("handoff", f"{UNSUPPORTED_FLOW} broken_redirect: appcast's redirect target is malformed "
                          f"({host}); open this posting from the employer's careers site.", url,
                          observed_at=page.retrieved_at)
    return _landed(url, target, net, job)


_BOARD = re.compile(r"company-logo\?board=([^\"'&\s<>]+)")


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _board_candidates(board: str, title: str, net: Net) -> list[tuple[str, str, str]]:
    """(title, location, posting URL) on the employer board, from its public search."""
    parts = urlsplit(board)
    host = (parts.hostname or "").lower()
    path = [part for part in parts.path.split("/") if part]
    family = detect_ats(board)
    if family == "greenhouse" and host.endswith("greenhouse.io"):
        slug = path[2] if path[:2] == ["v1", "boards"] and len(path) > 2 else (path[0] if path else "")
        if not _SLUG.fullmatch(slug):
            raise ValueError(f"no Greenhouse board name in {board}")
        data = _json(net.get(f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs"))
        return [(str(item.get("title") or ""), str((item.get("location") or {}).get("name") or ""),
                 f"https://job-boards.greenhouse.io/{slug}/jobs/{item['id']}")
                for item in (data or {}).get("jobs", []) if isinstance(item, dict) and str(item.get("id", "")).isdigit()]
    if family == "workday" and (match := _WORKDAY_HOST.fullmatch(host)) and parts.scheme == "https":
        site = path[1] if path and _LOCALE.fullmatch(path[0]) and len(path) > 1 else (path[0] if path else "")
        if not _SLUG.fullmatch(site):
            raise ValueError(f"no Workday site in {board}")
        data = net.post_json(f"https://{host}/wday/cxs/{match.group(1)}/{site}/jobs", {
            "appliedFacets": {}, "limit": 20, "offset": 0, "searchText": title,
        })
        return [(str(item.get("title") or ""), str(item.get("locationsText") or ""),
                 f"https://{host}/{site}{item['externalPath']}")
                for item in (data or {}).get("jobPostings", [])
                if isinstance(item, dict) and str(item.get("externalPath", "")).startswith("/job/")]
    if family == "lever" and host in {"jobs.lever.co", "jobs.eu.lever.co"} and path and _SLUG.fullmatch(path[0]):
        api = "api.eu.lever.co" if host == "jobs.eu.lever.co" else "api.lever.co"
        data = _json(net.get(f"https://{api}/v0/postings/{quote(path[0])}?mode=json"))
        return [(str(item.get("text") or ""), str((item.get("categories") or {}).get("location") or ""),
                 str(item["hostedUrl"]))
                for item in data or [] if isinstance(item, dict) and str(item.get("hostedUrl", "")).startswith("https://")]
    raise ValueError(f"the employer board {host or board[:80]} has no searchable public listing ({family})")


def _trylitos(url: str, net: Net, job: Job) -> EntryCheck:
    """Litos never redirects; its page names the employer board to search by title."""
    try:
        page = net.get(url)
    except PageHTTPError as exc:
        return _gone(url, exc, "The Litos page", "other") or _unknown(url, exc)
    except HttpError as exc:
        return _unknown(url, exc)
    handoff = unsupported_flow(url)
    match = _BOARD.search(page.text)
    if not match:
        return EntryCheck("handoff", f"{handoff} (The Litos page names no employer board.)", url,
                          observed_at=page.retrieved_at)
    board = unquote(html.unescape(match.group(1)))
    title = _norm_title(job.title)
    try:
        candidates = _board_candidates(board, job.title, net)
    except (HttpError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return EntryCheck("handoff", f"{handoff} (Board search failed: {exc}.)", url, observed_at=page.retrieved_at)
    exact = {link: location for name, location, link in candidates if _norm_title(name) == title}
    if len(exact) > 1 and job.location:
        city = _norm_title(job.location.split(",")[0])
        exact = {link: location for link, location in exact.items() if city and city in _norm_title(location)}
    if len(exact) != 1:
        host = urlsplit(board).hostname or board[:80]
        return EntryCheck("handoff", f"{handoff} ({len(exact)} postings with exactly this title on {host}.)",
                          url, observed_at=page.retrieved_at)
    return _landed(url, next(iter(exact)), net, job)


_RESERVED = (".invalid", ".test", ".example", ".localhost", ".local", ".internal")


def _checkable(url: str) -> bool:
    """A public http(s) URL worth a request (reserved names never resolve, so skip their DNS)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    return (parts.scheme in {"http", "https"} and bool(host) and host != "localhost"
            and not host.endswith(_RESERVED)
            and host not in {"example.com", "example.net", "example.org"}
            and not host.endswith((".example.com", ".example.net", ".example.org")))


def check_job(job: Job, net: Net) -> EntryCheck:
    """Liveness, resolution and routing for one stored job. Reads only; writes nothing."""
    url = job.apply_url or job.url
    source = redirect_source(url)
    if not _checkable(url):
        return EntryCheck("unknown", "Not a public http(s) URL; nothing to check", url, family=detect_ats(url),
                          source=source)
    try:
        if source in {"zapply", "recruitics"}:
            check = _follow(url, source, net, job)
        elif source == "appcast":
            check = _appcast(url, net, job)
        elif source == "trylitos":
            check = _trylitos(url, net, job)
        elif flow := unsupported_flow(url):
            check = EntryCheck("handoff", flow, url, family=detect_ats(url))
        else:
            check = liveness(url, net, job)
    except (HttpError, ValueError, KeyError, TypeError, AttributeError) as exc:
        check = _unknown(url, exc, detect_ats(url))
    return replace(check, url=url, source=source)


# ------------------------------------------------------------------- recording

def planned_changes(job: Job, check: EntryCheck, *, write_url: bool = True) -> tuple[str, ...]:
    """What `record` writes for this check: close, a new apply URL, a handoff."""
    if check.status == "closed":
        return ("close",) if job.is_active else ()
    changes = []
    if (write_url and check.resolved_url and
            clean_url(check.resolved_url) != clean_url(job.apply_url or job.url)):
        changes.append("apply_url")
    if (check.status == "handoff" and job.fill_blocker != check.reason and job.fill_state != "ready"
            and not job.fill_context_id):
        changes.append("handoff")
    return tuple(changes)


def store_url(conn: sqlite3.Connection, job_id: int, url: str, *, source_url: str) -> bool:
    """Store a resolved/routed employer URL as the job's apply URL (source ``resolve``)."""
    with transaction(conn):
        return "apply_url" in merge_facts(conn, job_id, source="resolve", source_url=source_url,
                                          authority=RESOLVED_AUTHORITY, apply_url=clean_url(url))


def record(conn: sqlite3.Connection, job: Job, check: EntryCheck, *, write_url: bool = True) -> tuple[str, ...]:
    """Close the job, store its clean employer URL, or file its typed handoff.

    Closed postings use the existing posting-verification fields (they leave the
    queues). A prepared or retained tab's preparation evidence is never replaced.
    """
    changes = planned_changes(job, check, write_url=write_url)
    if "close" in changes:
        with transaction(conn):
            persist_check(conn, job.id, PostingCheck(
                "closed", check.reason, check.resolved_url or check.url, check.observed_at))
    if "apply_url" in changes:
        store_url(conn, job.id, check.resolved_url, source_url=check.url)
    if "handoff" in changes:
        current = jobs_core.get_job(conn, job.id)
        record_attempt(conn, job.id, "blocked", blocker=check.reason,
                       url=(current.apply_url or current.url) if current else check.url)
    return changes


def resolve_jobs(
    conn: sqlite3.Connection, settings: Settings, jobs: list[Job], *, dry_run: bool,
    net: Net | None = None, say: Callable[[str], None] | None = None, write_url: bool = True,
) -> list[EntryResult]:
    """Check each job in order; with ``dry_run`` report the changes without writing any."""
    own = net is None
    net = net or open_net(settings)
    results = []
    try:
        for job in jobs:
            if say:
                say(f"checking {job.id} {job.company_name} - {job.title}")
            check = check_job(job, net)
            changes = (planned_changes(job, check, write_url=write_url) if dry_run
                       else record(conn, job, check, write_url=write_url))
            results.append(EntryResult(job.id, job.company_name, job.title, check, changes))
    finally:
        if own:
            net.close()
    return results


def before_attempt(
    conn: sqlite3.Connection, settings: Settings, job_ids: list[int], *, write_url: bool = True,
    say: Callable[[str], None] | None = None, net: Net | None = None,
) -> list[EntryResult]:
    """The live check right before preparing: close, resolve or hand off each active job.

    Jobs without a public http(s) URL (local fixtures, reserved names) cost no request.
    """
    jobs = [job for job in (jobs_core.get_job(conn, job_id) for job_id in dict.fromkeys(job_ids))
            if job is not None and job.is_active and _checkable(job.apply_url or job.url)]
    if not jobs:
        return []
    if say:
        say(f"checking {len(jobs)} posting{'s' if len(jobs) != 1 else ''} are still open")
    return resolve_jobs(conn, settings, jobs, dry_run=False, net=net, write_url=write_url)
