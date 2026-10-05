"""Bounded, robots-aware public fetching and employer JobPosting extraction.

Untrusted URLs never reach the normal redirect-following HTTP client. DNS answers
are checked and the connection uses the validated IP, with the original TLS SNI
and Host. The source's existing PoliteHttp supplies pacing and disk-cache policy.
Employer authority is a caller decision, not something JSON-LD can prove.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
from collections import Counter
from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

from applyai.core.discovery.base import NormalizedJob
from applyai.core.discovery.http import HttpError, PoliteHttp, RateLimited, TIMEOUT, USER_AGENT
from applyai.core.discovery.text import html_to_text, iso_to_utc, season_from
from applyai.normalize import canonical_url


@dataclass(frozen=True)
class PublicPage:
    url: str
    text: str
    retrieved_at: str
    status_code: int = 200


class PageHTTPError(HttpError):
    def __init__(self, status_code: int, url: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code} from {url}")


def public_addresses(url: str) -> tuple[str, list[str]]:
    """Reject credentials, local names, and any DNS answer that is not global."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
        if (parts.scheme not in {"http", "https"} or not host
                or parts.username is not None or parts.password is not None
                or port not in {None, 80, 443}):
            raise ValueError("only public HTTP(S) URLs without credentials are allowed")
        host = host.rstrip(".").encode("idna").decode("ascii")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "%" in host:
            raise ValueError("local hostname")
        answers = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80),
                                     type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
        if not addresses or any(not address.is_global or address.is_multicast
                                for address in parsed_addresses):
            raise ValueError("DNS includes a non-public address")
        return host, addresses
    except (ValueError, OSError, UnicodeError) as exc:
        raise HttpError(f"Unsafe or unresolved public URL {url}: {exc}") from exc


def _robot_path(value: str) -> str:
    """Normalize escaped unreserved octets without decoding reserved delimiters."""
    value = quote(value, safe="/?=&%:*+$,;@!~'()-._")
    def octet(match: re.Match) -> str:
        code = int(match.group(1), 16)
        char = chr(code)
        return char if char.isascii() and (char.isalnum() or char in "-._~") else f"%{code:02X}"
    return re.sub(r"%([0-9a-fA-F]{2})", octet, value)


def _robot_matches(parts: tuple[str, ...], anchored: bool, path: str) -> bool:
    """Match robots' '*' without regex backtracking on untrusted rule text."""
    if not path.startswith(parts[0]):
        return False
    if len(parts) == 1:
        return not anchored or path == parts[0]
    offset = len(parts[0])
    for literal in parts[1:-1]:
        found = path.find(literal, offset)
        if found < 0:
            return False
        offset = found + len(literal)
    suffix = parts[-1]
    if anchored:
        return path.endswith(suffix) and len(path) - len(suffix) >= offset
    return path.find(suffix, offset) >= 0


class _Robots:
    """Longest matching rule wins; specific user-agent groups replace wildcard groups."""

    def __init__(self, text: str):
        groups: list[tuple[list[str], list[tuple[str, str]]]] = []
        agents: list[str] = []
        directives: list[tuple[str, str]] = []
        for line in text.lstrip("\ufeff").splitlines():
            name, separator, value = line.split("#", 1)[0].partition(":")
            if not separator:
                continue
            name, value = name.strip().lower(), value.strip()
            if name == "user-agent":
                if directives:
                    groups.append((agents, directives))
                    agents, directives = [], []
                agents.append(value.lower())
            elif agents:
                directives.append((name, value))
        if agents:
            groups.append((agents, directives))
        product = USER_AGENT.split("/", 1)[0].lower()
        ranked = [
            (max((len(agent) for agent in names if agent != "*" and agent in product), default=0),
             "*" in names, rules)
            for names, rules in groups
        ]
        specificity = max((rank for rank, _, _ in ranked), default=0)
        selected = [rules for rank, wildcard, rules in ranked
                    if (rank == specificity if specificity else wildcard)]
        self.rules: list[tuple[int, bool, tuple[str, ...], bool]] = []
        self.delay = 0.0
        for rules in selected:
            for name, value in rules:
                if name in {"allow", "disallow"} and value:
                    pattern = _robot_path(value)
                    anchored = pattern.endswith("$")
                    if anchored:
                        pattern = pattern[:-1]
                    self.rules.append((len(pattern.replace("*", "").encode()), name == "allow",
                                       tuple(pattern.split("*")), anchored))
                elif name in {"crawl-delay", "request-rate"}:
                    try:
                        if name == "request-rate":
                            count, seconds = value.split("/", 1)
                            delay = float(seconds) / float(count)
                        else:
                            delay = float(value)
                        if 0 <= delay < float("inf"):
                            self.delay = max(self.delay, delay)
                    except (ValueError, ZeroDivisionError):
                        pass

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        path = _robot_path((parts.path or "/") + ("?" + parts.query if parts.query else ""))
        matches = [(length, allowed) for length, allowed, rule, anchored in self.rules
                   if _robot_matches(rule, anchored, path)]
        return max(matches)[1] if matches else True


class PublicPages:
    """GET-only fetcher; never inherits auth headers, cookies, or environment proxies."""

    def __init__(self, http: PoliteHttp, *, max_bytes: int = 2_000_000,
                 max_redirects: int = 3, transport: httpx.BaseTransport | None = None):
        if not 1 <= max_bytes <= 10_000_000 or not 0 <= max_redirects <= 10:
            raise ValueError("public page limits out of range")
        self.http = http
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.client = httpx.Client(timeout=TIMEOUT, trust_env=False,
                                   follow_redirects=False, transport=transport)
        self._robots: dict[str, _Robots] = {}
        self._limited: set[str] = set()
        self._host_delays: dict[str, float] = {}

    def __enter__(self) -> PublicPages:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def _once(self, url: str, *, token: str | None = None) -> tuple[PublicPage, str | None]:
        host, addresses = public_addresses(url)
        if host in self._limited:
            raise RateLimited(f"Already rate limited by {host}")
        parts = urlsplit(url)
        if token is not None and (
            parts.scheme != "https" or host != "api.search.brave.com"
            or parts.port not in {None, 443}
        ):
            raise HttpError("Search credentials may only be sent to Brave HTTPS")
        # This namespace cannot collide with the unbounded legacy cache.
        cache = self.http._cache_path("PUBLIC_GET_V2_AUTH" if token is not None else "PUBLIC_GET_V2", url, None)
        if self.http.ttl_seconds and cache.exists() and time.time() - cache.stat().st_mtime < self.http.ttl_seconds:
            if cache.stat().st_size <= self.max_bytes * 6 + 4096:
                try:
                    record = json.loads(cache.read_text())
                    if len(record["text"].encode("utf-8")) <= self.max_bytes:
                        self.http.cache_hits += 1
                        return PublicPage(url, record["text"], record["retrieved_at"], record["status"]), record["location"]
                except (OSError, ValueError, KeyError, TypeError):
                    pass
        address = addresses[0]
        netloc = f"[{address}]" if ":" in address else address
        if parts.port:
            netloc += f":{parts.port}"
        pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
        authority = f"[{host}]" if ":" in host else host
        if parts.port:
            authority += f":{parts.port}"
        headers = {"Host": authority, "User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if token is not None:
            # The endpoint and origin were checked before any cache access.
            headers["X-Subscription-Token"] = token
        self.http._wait_for_host(host, minimum_interval=self._host_delays.get(host, 0))
        self.http.requests_made += 1
        request = httpx.Request("GET", pinned, headers=headers,
                                extensions={"sni_hostname": host, "timeout": TIMEOUT.as_dict()})
        try:
            with closing(self.client.send(request, stream=True)) as response:
                if response.status_code == 429:
                    self._limited.add(host)
                    raise RateLimited(f"429 from {host}")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > self.max_bytes:
                    raise HttpError(f"Public response exceeds {self.max_bytes} bytes: {url}")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise HttpError(f"Server ignored identity encoding: {url}")
                body = bytearray()
                for chunk in response.iter_raw(chunk_size=16384):
                    if len(body) + len(chunk) > self.max_bytes:
                        raise HttpError(f"Public response exceeds {self.max_bytes} bytes: {url}")
                    body.extend(chunk)
                text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
                status = response.status_code
                location = response.headers.get("location")
        except httpx.HTTPError as exc:
            raise HttpError(f"{type(exc).__name__} fetching {url}") from exc
        retrieved = datetime.now(timezone.utc).isoformat()
        if self.http.ttl_seconds and status < 400:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"text": text, "retrieved_at": retrieved,
                                         "status": status, "location": location}))
        return PublicPage(url, text, retrieved, status), location

    def _allowed(self, url: str) -> None:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if origin not in self._robots:
            robots_url = origin + "/robots.txt"
            current = robots_url
            for hop in range(self.max_redirects + 1):
                page, location = self._once(current)
                if page.status_code in {301, 302, 303, 307, 308}:
                    if not location or hop == self.max_redirects:
                        raise HttpError(f"Invalid/excessive robots redirect: {robots_url}")
                    current = urljoin(current, location)
                    continue
                if page.status_code in {404, 410}:
                    parser = _Robots("")
                elif page.status_code in {401, 403}:
                    parser = _Robots("User-agent: *\nDisallow: /")
                elif not 200 <= page.status_code < 300:
                    raise PageHTTPError(page.status_code, current)
                else:
                    if re.search(r"<(?:!doctype\s+html|html|head|body)\b", page.text, re.I):
                        raise HttpError(f"robots.txt returned HTML, not rules: {current}")
                    parser = _Robots(page.text)
                self._robots[origin] = parser
                break
        rules = self._robots[origin]
        if not rules.allows(url):
            raise HttpError(f"robots.txt disallows {url}")
        if rules.delay > 60:
            raise HttpError(f"robots.txt requests a delay over 60 seconds; deferring {origin}")
        host = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii")
        self._host_delays[host] = max(self._host_delays.get(host, 0), rules.delay)

    def fetch(self, url: str) -> PublicPage:
        """Fetch HTML or JSON, checking robots at every resource redirect target."""
        current = url
        for hop in range(self.max_redirects + 1):
            public_addresses(current)
            self._allowed(current)
            page, location = self._once(current)
            if page.status_code in {301, 302, 303, 307, 308}:
                if not location or hop == self.max_redirects:
                    raise HttpError(f"Invalid/excessive public redirect: {url}")
                current = urljoin(current, location)
                continue
            if not 200 <= page.status_code < 300:
                raise PageHTTPError(page.status_code, current)
            return page
        raise HttpError(f"Too many redirects: {url}")

    def brave_json(self, url: str, token: str) -> dict:
        """Authenticated documented API only; never redirect a credentialed request."""
        if not url.startswith("https://api.search.brave.com/res/v1/web/search?"):
            raise HttpError("Not a Brave web search endpoint")
        page, _ = self._once(url, token=token)
        if page.status_code != 200:
            raise PageHTTPError(page.status_code, url)
        try:
            result = json.loads(page.text)
        except ValueError as exc:
            raise HttpError("Brave returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise HttpError("Brave returned a non-object response")
        return result


class _Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.documents: list[str] = []
        self.links: list[str] = []
        self._script: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and (values.get("type") or "").lower().split(";")[0] == "application/ld+json":
            self._script = []
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.documents.append("".join(self._script))
            self._script = None


def page_links(page: PublicPage) -> list[str]:
    markup = _Markup()
    markup.feed(page.text)
    return list(dict.fromkeys(urljoin(page.url, link) for link in markup.links))


def _objects(value):
    """Walk JSON-LD lists, @graph, and nested ItemList/mainEntity structures."""
    pending = [value]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(reversed(node))
        elif isinstance(node, dict):
            yield node
            pending.extend(reversed(list(node.values())))


def _string(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _locations(record: dict) -> tuple[str | None, str | None]:
    values = record.get("jobLocation") or []
    values = values if isinstance(values, list) else [values]
    locations, countries = [], []
    for value in values:
        if isinstance(value, str):
            locations.append(value)
        elif isinstance(value, dict):
            address = value.get("address") or {}
            if isinstance(address, str):
                locations.append(address)
            elif isinstance(address, dict):
                country = address.get("addressCountry")
                if isinstance(country, dict):
                    country = country.get("name")
                parts = [_string(address.get(key)) for key in ("addressLocality", "addressRegion")]
                parts.append(_string(country))
                if any(parts):
                    locations.append(", ".join(part for part in parts if part))
                elif _string(value.get("name")):
                    locations.append(value["name"])
                if _string(country):
                    countries.append(country)
    # Remote restrictions are evidence of country, not an inferred office location.
    if record.get("jobLocationType") == "TELECOMMUTE":
        requirements = record.get("applicantLocationRequirements") or []
        requirements = requirements if isinstance(requirements, list) else [requirements]
        for requirement in requirements:
            if isinstance(requirement, dict) and requirement.get("@type") == "Country":
                country = _string(requirement.get("name"))
                if country:
                    countries.append(country)
    return "; ".join(dict.fromkeys(locations)) or None, "; ".join(dict.fromkeys(countries)) or None


def _description(record: dict) -> str | None:
    """Keep separate qualification fields visible to routing and eligibility."""
    parts = []
    for key in ("description", "qualifications", "educationRequirements",
                "experienceRequirements", "skills", "responsibilities", "eligibilityToWorkRequirement"):
        values = record.get(key) or []
        values = values if isinstance(values, list) else [values]
        for value in values:
            if isinstance(value, dict):
                value = value.get("description") or value.get("credentialCategory") or value.get("name")
            text = html_to_text(_string(value))
            if text and text not in parts:
                parts.append(text)
    return "\n\n".join(parts) or None


def parse_job_postings(page: PublicPage, *, source: str, query: str | None = None) -> list[NormalizedJob]:
    """Extract exact employer records, retaining expiry separately from explicit closure.

    No snippets, title rewriting, eligibility inference, or title filtering here.
    Callers may select student titles; routing remains the pipeline's decision.
    """
    markup = _Markup()
    markup.feed(page.text)
    result: dict[str, NormalizedJob] = {}
    for document in markup.documents:
        try:
            payload = json.loads(document)
        except (ValueError, RecursionError):
            continue
        for record in _objects(payload):
            types = record.get("@type", [])
            types = types if isinstance(types, list) else [types]
            if not any(isinstance(kind, str) and kind.rsplit("/", 1)[-1] == "JobPosting" for kind in types):
                continue
            title = _string(record.get("title"))
            organization = record.get("hiringOrganization")
            company = _string(organization.get("name")) if isinstance(organization, dict) else _string(organization)
            if not title or not company:
                continue
            try:
                url = urljoin(page.url, _string(record.get("url")) or page.url)
                parts = urlsplit(url)
                if (parts.scheme not in {"http", "https"}
                        or parts.username is not None or parts.password is not None):
                    continue
                # A site's markup cannot redirect job identity to an unrelated host.
                listing_url = parts.netloc.lower() != urlsplit(page.url).netloc.lower()
                if listing_url:
                    url = page.url
                job_url = canonical_url(url)
            except ValueError:
                continue
            identifier = record.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value")
            # IDs are employer-scoped; repeated URLs on a listing page are not.
            if isinstance(identifier, (str, int)) and not isinstance(identifier, bool):
                identity = f"{urlsplit(job_url).netloc}|{company}|{identifier}"
            else:
                identity = f"{job_url}|{company}|{title}"
            valid = iso_to_utc(_string(record.get("validThrough")))
            expired = bool(valid and datetime.fromisoformat(valid.replace("Z", "+00:00")) < datetime.now(timezone.utc))
            closed = str(record.get("status", record.get("jobStatus", ""))).lower() in {"closed", "filled", "expired"}
            location, country = _locations(record)
            remote = record.get("jobLocationType") == "TELECOMMUTE"
            raw = dict(record)
            raw["provenance"] = {"query": query, "retrieved_at": page.retrieved_at, "url": page.url,
                                 "expired": expired, "explicitly_closed": closed,
                                 "url_is_listing": listing_url,
                                 "employmentType": record.get("employmentType"),
                                 "validThrough": record.get("validThrough")}
            result[identity] = NormalizedJob(
                source=source, source_job_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                company=company, title=title, url=job_url, location=location,
                country=country, is_remote=True if remote else None,
                description=_description(record),
                posted_at=iso_to_utc(_string(record.get("datePosted"))),
                deadline_at=valid,
                opens_at=iso_to_utc(_string(record.get("applicationStartDate"))),
                season=season_from(title), closed=closed or expired, raw=raw,
            )
    jobs = list(result.values())
    url_counts = Counter(job.url for job in jobs)
    for job in jobs:
        if url_counts[job.url] > 1:
            job.raw["provenance"]["url_is_listing"] = True
    return jobs
