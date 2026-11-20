"""Opt-in public evidence snapshots, never applicant facts or model instructions."""
from __future__ import annotations

from contextlib import closing
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from kitty.core import keys
from kitty.core.discovery.http import HttpError, PoliteHttp
from kitty.core.discovery.public_pages import PublicPages
from kitty.core.workshop import ai, store
from kitty.db.connection import transaction

MAX_SOURCES = 8
MAX_TEXT = 16000
SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = []
        self.hidden = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False
            self.parts.append("\n")
        if tag in {"p", "div", "li", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self.in_title:
                self.title.append(data)


def _public_url(value):
    ai.text(value, "public URL", 2000)
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        if (parts.scheme not in {"http", "https"} or not host or parts.username is not None
                or parts.password is not None or parts.port not in {None, 80, 443}
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (not address.is_global or address.is_multicast):
            raise ValueError()
        if any(re.search(r"token|secret|password|auth|api.?key|email|session|signature", name, re.I)
               for name, _ in parse_qsl(parts.query)):
            raise ValueError()
        if ai.CONTACT.search(parts.query) or any(c.isspace() for c in value):
            raise ValueError()
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
    except ValueError:
        raise store.WorkshopError("Use public HTTP(S) URLs without credentials, private addresses or sensitive query parameters") from None


def _public_label(value, name, *, empty=False):
    ai.text(value, name, 200, empty=empty)
    if ai.CONTACT.search(value) or any(c in value for c in "\r\n\t"):
        raise store.WorkshopError(f"{name} must contain only a public company or role name, not contact details")
    return value.strip()


def _host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _matches_company(company, text):
    words = re.findall(r"\w+", company.casefold())
    return bool(words) and re.search(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b", text.casefold()) is not None


def list_research(conn, *, company=""):
    ai.text(company, "company", 200, empty=True)
    rows = conn.execute("SELECT snapshot_json FROM workshop_research "
                        "WHERE (?='' OR company=? COLLATE NOCASE) ORDER BY created_at DESC LIMIT 100",
                        (company, company)).fetchall()
    return [json.loads(row[0]) for row in rows]


def research_company(app, conn, *, company, role="", job_id=None, urls=None):
    ai.require_enabled(app, conn)  # Before keys, HTTP construction or personal source access.
    company = _public_label(company, "company")
    role = _public_label(role, "role", empty=True)
    if urls is not None and (not isinstance(urls, list) or len(urls) > MAX_SOURCES):
        raise store.WorkshopError(f"urls must be a list of at most {MAX_SOURCES} public URLs")
    explicit = [_public_url(url) for url in (urls or [])]
    posting = None
    website = None
    if job_id is not None:
        if type(job_id) is not int or job_id < 1:
            raise store.WorkshopError("job_id must be a positive integer")
        job = conn.execute("SELECT j.url,j.title,c.display_name,c.website FROM jobs j "
                           "JOIN companies c ON c.id=j.company_id WHERE j.id=?", (job_id,)).fetchone()
        if job is None:
            raise store.WorkshopError("Job not found", 404)
        if company.casefold() != job["display_name"].strip().casefold():
            raise store.WorkshopError("Selected job belongs to a different company; disambiguate before research", 409)
        posting, website = job["url"], job["website"]
        if not role:
            role = _public_label(job["title"], "role", empty=True)
    else:
        matches = conn.execute("SELECT website FROM companies WHERE display_name=? COLLATE NOCASE OR norm_name=? COLLATE NOCASE",
                               (company, company)).fetchall()
        if len(matches) == 1:
            website = matches[0]["website"]
    warnings, candidates = [], {}
    for url, kind in [(posting, "posting"), (website, "primary")]:
        if url:
            try:
                candidates[_public_url(url)] = kind
            except store.WorkshopError:
                warnings.append(f"Stored {kind} URL is not a safe public URL; supply a public source.")
    for url in explicit:
        candidates.setdefault(url, "supplied")
    source_config = app.config.sources.sources.get("websearch")
    search_enabled = bool(source_config and source_config.options.get("enable_search") is True)
    if not candidates and not search_enabled:
        warnings.append("No public sources available. Supply company/posting URLs or configure opt-in Brave search.")
    sources, findings = [], []
    # No network client is needed for a source-less failure.
    if candidates or search_enabled:
        with closing(PoliteHttp(app.settings.data_dir / "workshop" / "research-cache",
                                min_interval=1.0, cache_ttl_minutes=60)) as http:
            with PublicPages(http, max_bytes=1_000_000, max_redirects=3) as pages:
                if search_enabled and len(candidates) < MAX_SOURCES:
                    key = ""
                    try:
                        key = keys.read(app.settings, "BRAVE")
                        if not key:
                            raise HttpError("Brave search is enabled but its key is not configured")
                        query = f'"{company}" {role} company careers official'
                        result = pages.brave_json(SEARCH_URL + "?" + urlencode({"q": query, "count": 5}), key)
                        web = result.get("web") or {}
                        hits = web.get("results", []) if isinstance(web, dict) else None
                        if not isinstance(hits, list):
                            raise HttpError("Brave returned invalid search results")
                        for hit in hits[:5]:
                            if isinstance(hit, dict) and isinstance(hit.get("url"), str):
                                try:
                                    candidates.setdefault(_public_url(hit["url"]), "search")
                                except store.WorkshopError:
                                    warnings.append("Skipped an unsafe search-result URL.")
                    except (keys.KeysError, HttpError, ValueError, TypeError, AttributeError):
                        # Exceptions from providers can contain headers or supplied credentials.
                        warnings.append("Brave search failed or its key is missing; check search configuration. Supplied sources were still fetched.")
                if len(candidates) > MAX_SOURCES:
                    warnings.append(f"Source limit reached; only the first {MAX_SOURCES} URLs were fetched.")
                for url, kind in list(candidates.items())[:MAX_SOURCES]:
                    ai.require_enabled(app, conn)
                    record = {"url": url, "title": "", "quote": "", "text": "", "fetched_at": None,
                              "status": "failed", "kind": kind}
                    try:
                        page = pages.fetch(url)
                        final = _public_url(page.url)
                        parser = _Text()
                        parser.feed(page.text)
                        plain = "\n".join(line.strip() for line in re.sub(r"[ \t\xa0]+", " ", "".join(parser.parts)).splitlines() if line.strip())[:MAX_TEXT]
                        record.update(url=final, title=" ".join(parser.title)[:300], text=plain, fetched_at=page.retrieved_at)
                        bound = kind in {"posting", "primary"} and _host(final) == _host(url)
                        if not plain or not (bound or _matches_company(company, plain) or _matches_company(company, _host(final))):
                            record["status"] = "unverified"
                            warnings.append(f"Could not establish company identity at {_host(final)}; excluded from findings.")
                        else:
                            record["status"] = "ok"
                            quote = next((line for line in plain.splitlines() if _matches_company(company, line) and len(line) > len(company) + 5), plain.splitlines()[0])[:700]
                            record["quote"] = quote
                            findings.append({"text": quote, "url": final, "quote": quote})
                    except (HttpError, store.WorkshopError, ValueError):
                        record["error"] = "Public fetch failed, was blocked by robots, or returned an unsafe redirect."
                        warnings.append(f"Could not fetch a safe public page from {_host(url)}.")
                    sources.append(record)
    primary = any(s["status"] == "ok" and s["kind"] == "primary" for s in sources)
    posting_ok = any(s["status"] == "ok" and s["kind"] == "posting" for s in sources)
    if not primary:
        warnings.append("No verified company primary-site source; confirm the company domain before relying on this brief.")
    if not posting_ok:
        warnings.append("No selected job posting was fetched; role-specific coverage is incomplete.")
    status = "failed" if not findings else "complete" if primary and posting_ok and not warnings else "partial"
    snapshot = {"id": str(uuid4()), "company": company, "role": role, "status": status,
                "sources": sources, "findings": findings, "warnings": warnings,
                "created_at": ai.now().isoformat()}
    with transaction(conn, immediate=True):
        ai.require_enabled(app, conn)
        conn.execute("INSERT INTO workshop_research VALUES(?,?,?,?,?)",
                     (snapshot["id"], company, role, ai.dump(snapshot), snapshot["created_at"]))
    return snapshot
