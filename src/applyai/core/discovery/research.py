"""Public paid research programs, not invented lab vacancies or eligibility claims.

Application windows are parsed each run. Undated directories, login-only projects,
closed cycles and future openings remain source evidence, never active jobs.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from applyai.core.discovery.base import AdapterResult, NormalizedJob
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, RateLimited
from applyai.core.discovery.public_pages import PublicPage, PublicPages, page_links
from applyai.core.discovery.text import html_to_text

TAMU = "https://engineering.tamu.edu/admissions-and-aid/undergraduate-summer-research-grants/"
DOE = "https://science.osti.gov/wdts/suli"
NASA = "https://www.nasa.gov/learning-resources/internship-programs/"
NSF = "https://www.nsf.gov/funding/initiatives/reu/search/csvexport?page&_format=csv"
DATE = r"([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})"
ELIGIBILITY = "Research program: GPA, completed credits/semesters, citizenship, enrollment and program-specific qualifications require verification; acceptance is not assured."


def _text(page: PublicPage) -> str:
    # Keep site navigation out of the record when the publisher marks its main area.
    match = re.search(r"<main\b[^>]*>(.*?)</main>", page.text, re.I | re.S)
    return html_to_text(match[1] if match else page.text) or ""


def _date(value: str, zone: str, hour: int = 0, minute: int = 0) -> datetime:
    value = " ".join(value.split())
    return datetime.strptime(value, "%B %d, %Y").replace(
        hour=hour, minute=minute, tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)


def window_state(opens: datetime | None, deadline: datetime | None, now: datetime) -> str:
    if deadline is not None and now >= deadline:
        return "closed"
    if opens is not None and now < opens:
        return "not_open"
    if opens is not None and deadline is not None and opens <= now < deadline:
        return "open"
    return "unknown"


def _record(key: str, company: str, title: str, page: PublicPage, description: str,
            opens: datetime, deadline: datetime, evidence_urls: list[str],
            location: str, apply_url: str | None = None) -> NormalizedJob:
    return NormalizedJob(
        source="research", source_job_id=key, company=company,
        title="Research program — " + title, url=page.url, apply_url=apply_url or page.url,
        description=description, country="US", location=location,
        deadline_at=deadline.isoformat(), opens_at=opens.isoformat(),
        raw={"record_kind": "research_program", "eligibility_note": ELIGIBILITY,
             "evidence_urls": evidence_urls, "retrieved_at": page.retrieved_at,
             "application_state": "open", "paid_evidence": True},
    )


def parse_tamu(page: PublicPage, about: PublicPage, now: datetime) -> tuple[list[NormalizedJob], list[dict]]:
    text, qualifications = _text(page), _text(about)
    opening = re.search(r"Application Opens\s*:\s*" + DATE, text, re.I)
    closing = re.search(r"Application Deadline\s*:\s*" + DATE, text, re.I)
    opens = _date(opening[1], "America/Chicago") if opening else None
    # TAMU explicitly specifies 5pm CST. Do not invent a deadline time if it changes.
    exact_time = bool(closing and re.search(r"5\s*:\s*00\s*p\.?m\.?\s*CST", text[closing.end():closing.end()+60], re.I))
    deadline = _date(closing[1], "America/Chicago", 17) if exact_time else None
    state = window_state(opens, deadline, now)
    paid = bool(re.search(r"\$[\d,]+\s+stipend.*?scholarship", qualifications, re.I | re.S))
    evidence = {"channel": "tamu_sreu", "url": page.url, "state": state,
                "opens_at": opens.isoformat() if opens else None,
                "deadline_at": deadline.isoformat() if deadline else None,
                "paid_confirmed": paid, "eligibility_url": about.url}
    if state != "open" or not paid:
        return [], [evidence]
    assert opens is not None and deadline is not None
    applications = [url for url in page_links(page) if "docs.google.com/forms/" in url]
    job = _record(f"tamu-sreu-{deadline.year}", "Texas A&M University",
                  f"Undergraduate Summer Research S-REU {deadline.year} (College of Engineering)",
                  page, text + "\n\n" + qualifications, opens, deadline,
                  [page.url, about.url], "College Station, TX, US",
                  applications[0] if applications else None)
    job.observed_at = min(page.retrieved_at, about.retrieved_at)
    return [job], [evidence]


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)


def parse_doe(page: PublicPage, eligibility: PublicPage, benefits: PublicPage,
              now: datetime) -> tuple[list[NormalizedJob], list[dict]]:
    parser = _Tables()
    parser.feed(page.text)
    terms = next((r for r in parser.rows if r and "Internship Term" in r[0]), [])
    opening = next((r for r in parser.rows if r and "Application Opens" in r[0]), [])
    closing = next((r for r in parser.rows if r and "Applications" in r[0] and "Due" in r[0]), [])
    if not terms or len(terms) != len(opening) or len(terms) != len(closing):
        raise ValueError("DOE SULI current application table missing or changed")
    jobs, evidence = [], []
    paid = bool(re.search(r"stipend|paid internship", _text(benefits), re.I))
    for term, start, end in zip(terms[1:], opening[1:], closing[1:]):
        om, dm = re.search(DATE, start), re.search(DATE, end)
        opens = _date(om[1], "America/New_York") if om else None
        timed = re.search(r"5\s*:\s*00\s*PM\s*ET", end, re.I)
        deadline = _date(dm[1], "America/New_York", 17) if dm and timed else None
        state = window_state(opens, deadline, now)
        evidence.append({"channel": "doe_suli", "cycle": term, "url": page.url, "state": state,
                         "opens_at": opens.isoformat() if opens else None,
                         "deadline_at": deadline.isoformat() if deadline else None,
                         "paid_confirmed": paid})
        if state == "open" and paid:
            assert opens is not None and deadline is not None
            jobs.append(_record("doe-suli-" + term.lower().replace(" ", "-"),
                                "U.S. Department of Energy", "Science Undergraduate Laboratory Internships (SULI) " + term,
                                page, _text(page) + "\n\n" + _text(eligibility) + "\n\n" + _text(benefits),
                                opens, deadline, [page.url, eligibility.url, benefits.url], "United States"))
            jobs[-1].observed_at = min(page.retrieved_at, eligibility.retrieved_at, benefits.retrieved_at)
    return jobs, evidence


class ResearchAdapter:
    name = "research"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        channels = options.get("channels", ["tamu_sreu", "doe_suli", "nasa_ostem", "nsf_reu"])
        jobs: list[NormalizedJob] = []
        evidence: list[dict] = []
        errors: list[dict] = []
        now = datetime.now(timezone.utc)
        limited = False
        with PublicPages(self.ctx.http_for(self.name)) as pages:
            for channel in dict.fromkeys(channels):
                url = ""
                try:
                    if channel == "tamu_sreu":
                        url = options.get("tamu_url", TAMU + "index.html")
                        page = pages.fetch(url)
                        about = pages.fetch(options.get("tamu_about_url", TAMU + "about.html"))
                        found, states = parse_tamu(page, about, now)
                        jobs.extend(found)
                        evidence.extend(states)
                    elif channel == "doe_suli":
                        url = options.get("doe_dates_url", DOE + "/Key-Dates")
                        page = pages.fetch(url)
                        eligibility = pages.fetch(options.get("doe_eligibility_url", DOE + "/Eligibility"))
                        main = pages.fetch(options.get("doe_url", DOE))
                        benefits_links = [link for link in page_links(main) if link.startswith(DOE + "/") and "Benefits" in link]
                        benefits = pages.fetch(benefits_links[0]) if benefits_links else main
                        found, states = parse_doe(page, eligibility, benefits, now)
                        jobs.extend(found)
                        evidence.extend(states)
                    elif channel == "nasa_ostem":
                        url = options.get("nasa_url", NASA)
                        page = pages.fetch(url)
                        text = _text(page)
                        cycles = re.findall(r"((?:Summer|Fall|Spring)\s+20\d{2})\s+Application Deadline\s*:\s*" + DATE, text, re.I)
                        if not cycles:
                            raise ValueError("NASA OSTEM dated program cycles missing")
                        for term, date in cycles:
                            deadline = _date(date, "America/New_York", 23, 59)
                            evidence.append({"channel": channel, "cycle": term, "url": page.url,
                                             "state": window_state(None, deadline, now),
                                             "deadline_at": deadline.isoformat(),
                                             "reason": "Deadline published, but opening and project-level paid/current availability are not verified. STEM Gateway requires JavaScript/account interaction; no public project API or login bypass used.",
                                             "blocked_url": "https://stemgateway.nasa.gov/public/s/explore-opportunities"})
                    elif channel == "nsf_reu":
                        url = options.get("nsf_csv_url", NSF)
                        page = pages.fetch(url)
                        if not page.text.strip() or page.text.lstrip().startswith("<"):
                            raise ValueError("NSF CSV unavailable (empty response or HTML/WAF challenge); not bypassed")
                        reader = csv.DictReader(io.StringIO(page.text.lstrip("\ufeff")))
                        if not reader.fieldnames or len(reader.fieldnames) < 3:
                            raise ValueError("NSF REU CSV schema missing")
                        cap = max(1, min(2000, int(options.get("max_directory_rows", 500))))
                        count = 0
                        for _ in reader:
                            count += 1
                            if count > cap:
                                break
                        evidence.append({"channel": channel, "url": page.url, "state": "unknown",
                                         "directory_rows": min(count, cap), "truncated": count > cap,
                                         "reason": "NSF directory grants are not open student vacancies. Site-specific current paid application cycles must be verified; ETAP sign-in is not bypassed.",
                                         "blocked_url": "https://etap.nsf.gov/"})
                    else:
                        raise ValueError(f"Unknown research channel {channel}")
                except (HttpError, ValueError) as exc:
                    limited |= isinstance(exc, RateLimited)
                    errors.append({"channel": channel, "url": url, "error": str(exc)})
        status = ("partial" if evidence else "rate_limited" if limited else "error") if errors else "ok"
        return AdapterResult(jobs, status, "; ".join(f"{e['channel']} ({e['url']}): {e['error']}" for e in errors) or None,
                             stats={"complete_snapshot": False, "evidence": evidence, "errors": errors,
                                    "blocked_channels": [{"channel": "HireAggies / Aggie Collaborate", "reason": "Authenticated TAMU access required; no supplied session and no login bypass."}]})
