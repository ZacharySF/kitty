"""Bounded searches of employers' public Eightfold, Jibe, Oracle and Siemens portals."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

from applyai.core.discovery.base import AdapterResult, BoardOutcome, NormalizedJob, summarize_boards
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, RateLimited
from applyai.core.discovery.public_pages import PublicPage, PublicPages, page_links
from applyai.core.discovery.text import html_to_text, is_internship_title, iso_to_utc, season_from, us_location


def _json(pages: PublicPages, url: str) -> tuple[dict, PublicPage]:
    page = pages.fetch(url)
    value = json.loads(page.text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value, page


def _description(*parts: str | None) -> str | None:
    return "\n\n".join(dict.fromkeys(text for part in parts if (text := html_to_text(part)))) or None


def _metadata(board: dict, page: PublicPage, query: str, record: dict) -> dict:
    return {"board": board["company"], "portal_url": board["url"],
            "fetched_url": page.url, "retrieved_at": page.retrieved_at,
            "query": query, "record": record}


def parse_jibe(board: dict, row: dict, page: PublicPage, query: str) -> NormalizedJob:
    ident = str(row["req_id"])
    url = board["url"].rstrip("/") + "/" + str(row.get("slug") or ident)
    pay = " ".join(str(value) for value in row.get("tags", []) if "$" in str(value))
    low, high = row.get("salary_min_value"), row.get("salary_max_value")
    if (str(row.get("salary_frequency", "")).upper() == "HOURLY"
            and (row.get("country_code") == "US" or row.get("country") in {"US", "United States"})
            and type(low) in (int, float) and type(high) in (int, float)
            and 0 <= low <= high):
        pay = f"Base pay: ${low:g} - ${high:g} per hour"
    schedule = (row.get("employment_type") or "").replace("_", " ")
    description = _description(pay, f"Schedule: {schedule}" if schedule else None,
                               row.get("description"), row.get("qualifications"), row.get("responsibilities"))
    return NormalizedJob(
        source="employers", source_job_id=f"{urlsplit(url).hostname}:{ident}",
        company=board["company"], title=row["title"], url=url,
        apply_url=row.get("apply_url"),
        location=", ".join(str(row[k]) for k in ("city", "state", "country") if row.get(k)) or row.get("location_name"),
        country=row.get("country_code") or row.get("country"), description=description,
        posted_at=iso_to_utc(row.get("posted_date")), season=season_from(row["title"], description),
        deadline_at=iso_to_utc(row.get("posting_expiry_date")),
        closed=row.get("applyable") is False, raw=_metadata(board, page, query, row),
    )


def parse_eightfold(board: dict, row: dict, page: PublicPage, query: str) -> NormalizedJob:
    ident = str(row["id"])
    url = row.get("publicUrl") or urljoin(board["url"], row.get("positionUrl") or f"/careers/job/{ident}")
    places = row.get("standardizedLocations") or row.get("locations") or []
    location = "; ".join(places) or row.get("location")
    description = _description(row.get("jobDescription"))
    posted = row.get("postedTs")
    return NormalizedJob(
        source="employers", source_job_id=f"{urlsplit(board['url']).hostname}:{ident}",
        company=board["company"], title=row["name"], url=url, location=location,
        is_remote=True if row.get("workLocationOption") == "remote" else None,
        country="US" if places and all(place.endswith(", US") for place in places) else None,
        description=description,
        posted_at=datetime.fromtimestamp(posted, UTC).isoformat() if posted else None,
        season=season_from(row["name"], description), raw=_metadata(board, page, query, row),
    )


def parse_oracle(board: dict, row: dict, page: PublicPage, query: str) -> NormalizedJob:
    ident = str(row["Id"])
    schedule = row.get("JobSchedule")
    description = _description(f"Schedule: {schedule}" if schedule else None,
                               row.get("ExternalDescriptionStr"), row.get("ExternalQualificationsStr"),
                               row.get("ExternalResponsibilitiesStr"))
    return NormalizedJob(
        source="employers", source_job_id=f"{urlsplit(board['url']).hostname}:{ident}",
        company=board["company"], title=row["Title"],
        url=board["url"].rstrip("/") + "/job/" + ident,
        location=row.get("PrimaryLocation"), country=row.get("PrimaryLocationCountry"),
        description=description, posted_at=iso_to_utc(row.get("ExternalPostedStartDate")),
        deadline_at=iso_to_utc(row.get("ExternalPostedEndDate")),
        season=season_from(row["Title"], description), raw=_metadata(board, page, query, row),
    )


class _SiemensDetail(HTMLParser):
    """Read semantic detail articles, excluding navigation and related vacancies."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.articles: list[str] = []
        self.fields: dict[str, str] = {}
        self._article: list[str] | None = None
        self._capture: list[str] | None = None
        self._depth = 0
        self._label = ""
        self._is_label = False
        self._location = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if tag == "meta" and values.get("property") == "og:title":
            self.title = values.get("content") or ""
        if tag == "article" and "article--details" in classes:
            self._article = []
        if self._article is not None:
            self._article.append(self.get_starttag_text())
        if tag == "div":
            if self._capture is not None:
                self._depth += 1
            elif "article__content__view__field__label" in classes or "article__content__view__field__value" in classes:
                self._capture = []
                self._depth = 1
                self._is_label = "article__content__view__field__label" in classes
            if "tf_locations" in classes:
                self._location = True

    def handle_data(self, data):
        if self._article is not None:
            self._article.append(data)
        if self._capture is not None:
            self._capture.append(data)

    def handle_endtag(self, tag):
        if self._article is not None:
            self._article.append(f"</{tag}>")
            if tag == "article":
                self.articles.append("".join(self._article))
                self._article = None
        if tag == "div" and self._capture is not None:
            self._depth -= 1
            if self._depth == 0:
                value = " ".join(" ".join(self._capture).split())
                if self._is_label:
                    self._label = value
                else:
                    self.fields["location" if self._location else self._label] = value
                    self._location = False
                    self._label = ""
                self._capture = None


def parse_siemens(board: dict, page: PublicPage, query: str) -> NormalizedJob:
    parsed = _SiemensDetail()
    parsed.feed(page.text)
    if not parsed.title or not parsed.articles or not parsed.fields.get("Job ID"):
        raise ValueError("Siemens page lacks title, Job ID or detail articles")
    description = _description(*parsed.articles)
    posted = parsed.fields.get("Posted since")
    posted_at = datetime.strptime(posted, "%d-%b-%Y").replace(tzinfo=UTC).isoformat() if posted else None
    return NormalizedJob(
        source="employers", source_job_id=f"{urlsplit(board['url']).hostname}:{parsed.fields['Job ID']}",
        company=board["company"], title=parsed.title, url=page.url,
        location=parsed.fields.get("location"), description=description, posted_at=posted_at,
        season=season_from(parsed.title, description), raw=_metadata(board, page, query, parsed.fields),
    )


def _search(board: dict, query: str, offset: int) -> str:
    base = board.get("api_url", board["url"]).rstrip("/")
    kind = board["kind"]
    if kind == "jibe":
        params = {"keywords": query, "location": board.get("location", "United States"),
                  "page": offset + 1, "limit": 20}
        if "radius" in board:
            params["radius"] = board["radius"]
        return base + "/api/jobs?" + urlencode(params)
    if kind == "eightfold":
        return base + "/api/pcsx/search?" + urlencode({"domain": board["domain"], "query": query,
                                                      "location": "United States", "start": offset})
    if kind == "oracle":
        finder = f"findReqs;siteNumber={board['site']},keyword={query},limit=25,offset={offset}"
        for key, value in board.get("search_location", {}).items():
            if key not in {"latitude", "longitude", "radius", "radiusUnit"}:
                raise ValueError(f"unsupported Oracle location parameter: {key}")
            finder += f",{key}={value}"
        return base + "/recruitingCEJobRequisitions?" + urlencode({
            "onlyData": "true", "expand": "requisitionList", "finder": finder,
        })
    if kind == "siemens":
        return base + "/SearchJobs/?" + urlencode({"search": query, "jobRecordsPerPage": 20, "jobOffset": offset})
    raise ValueError(f"unsupported employer portal kind: {kind}")


def _detail_url(board: dict, ident: str) -> str:
    base = board.get("api_url", board["url"]).rstrip("/")
    if board["kind"] == "eightfold":
        return base + "/api/pcsx/position_details?" + urlencode({"position_id": ident, "domain": board["domain"], "hl": "en"})
    return base + "/recruitingCEJobRequisitionDetails?" + urlencode({
        "onlyData": "true", "expand": "all", "finder": f"ById;Id={ident},siteNumber={board['site']}",
    })


def _local_candidate(board: dict, row: dict) -> bool:
    """Strict coastal city/state bound; reject explicitly full-time-only records."""
    location = row.get("PrimaryLocation") or ", ".join(
        str(row[key]) for key in ("city", "state", "country") if row.get(key)
    )
    if not any(re.search(rf"\b{re.escape(city)}\s*,\s*(?:TX|Texas)\b", location, re.I)
               for city in board["allow_cities"]):
        return False
    title = row.get("Title") or row.get("title") or ""
    schedule = row.get("JobSchedule") or row.get("employment_type") or ""
    hours = f"{title} {schedule}"
    full_time = re.search(r"\bfull[\s_-]*time\b|(?:^|[\s-])FT\b", hours, re.I)
    part_time = re.search(r"\bpart[\s_-]*time\b|(?:^|[\s-])PT\b", hours, re.I)
    if board.get("part_time_only") and not part_time:
        return False
    return not full_time or bool(part_time)


def _local_evidence(job: NormalizedJob, row: dict) -> None:
    """Keep store addresses distinct and hours/vacancy uncertainty visible."""
    schedule = row.get("JobSchedule") or row.get("employment_type") or ""
    notes = []
    if not re.search(r"\bpart[\s_-]*time\b|(?:^|[\s-])PT\b", f"{job.title} {schedule}", re.I):
        notes.append("Part-time availability is not stated; confirm hours with the employer.")
    if re.search(r"continuously accept applications|should a position become available",
                 job.description or "", re.I):
        notes.append("Continuous-acceptance posting; an immediate vacancy is not confirmed.")
    if notes:
        job.raw = (job.raw or {}) | {"eligibility_note": " ".join(notes)}
    # Distinct requisitions at two stores can have the same title and city.
    # Keep the published street address so normal cross-source dedupe cannot
    # collapse those openings, and the user can assess the actual commute.
    if board_locations := row.get("workLocation"):
        addresses = [
            ", ".join(str(place[key]) for key in ("AddressLine1", "TownOrCity", "Region2", "Country")
                      if place.get(key))
            for place in board_locations if place.get("AddressLine1") and place.get("TownOrCity")
        ]
        if addresses:
            job.location = "; ".join(dict.fromkeys(addresses))
    elif row.get("street_address") and job.location:
        job.location = f"{row['street_address']}, {job.location}"


def fetch_board(pages: PublicPages, board: dict, queries: list[str], max_pages: int,
                max_postings: int) -> tuple[list[NormalizedJob], list[str]]:
    """Retain successes after a failed page/detail; never infer closure from a search."""
    jobs: dict[str, NormalizedJob] = {}
    problems: list[str] = []
    seen: set[str] = set()
    kind = board["kind"]
    for query in queries:
        offset = 0
        seen_in_query: set[str] = set()
        for _ in range(max_pages):
            try:
                url = _search(board, query, offset)
                if kind == "siemens":
                    page = pages.fetch(url)
                    rows = [{"url": link} for link in page_links(page)
                            if urlsplit(link).hostname == urlsplit(board["url"]).hostname
                            and "/externaljobs/JobDetail/" in urlsplit(link).path]
                    total = None
                else:
                    data, page = _json(pages, url)
                    if kind == "jibe":
                        rows = [item["data"] for item in data["jobs"]]
                        total = int(data["totalCount"])
                    elif kind == "eightfold":
                        rows = data["data"]["positions"]
                        total = int(data["data"]["count"])
                    else:
                        group = data["items"][0]
                        rows, total = group["requisitionList"], int(group["TotalJobsCount"])
                if not rows:
                    if total is not None and offset < total:
                        problems.append(f"{query}: empty page before advertised total {total}")
                    break
                fresh = 0
                for row in rows:
                    ident = str(row.get("req_id") or row.get("id") or row.get("Id") or row.get("url"))
                    if ident in seen_in_query:
                        continue
                    seen_in_query.add(ident)
                    fresh += 1
                    if ident in seen:
                        continue
                    seen.add(ident)
                    if kind != "siemens":
                        title = row.get("title") or row.get("name") or row.get("Title") or ""
                        if board.get("allow_cities"):
                            if not _local_candidate(board, row):
                                continue
                        elif not is_internship_title(title):
                            continue
                        country = row.get("country_code") or row.get("PrimaryLocationCountry")
                        if country and us_location(None, country) is False:
                            continue
                    if len(jobs) >= max_postings:
                        problems.append(f"posting cap {max_postings} reached")
                        return list(jobs.values()), problems
                    try:
                        if kind == "siemens":
                            job = parse_siemens(board, pages.fetch(row["url"]), query)
                        elif kind == "jibe":
                            job = parse_jibe(board, row, page, query)
                        else:
                            detail_page = page
                            record = row
                            try:
                                payload, detail_page = _json(pages, _detail_url(board, ident))
                                detail = payload["data"] if kind == "eightfold" else payload["items"][0]
                                if not isinstance(detail, dict):
                                    raise ValueError("missing detail object")
                                record = row | detail
                            except RateLimited:
                                raise
                            except (HttpError, ValueError, KeyError, TypeError, IndexError) as exc:
                                problems.append(f"{ident} detail: {exc}")
                            parser = parse_eightfold if kind == "eightfold" else parse_oracle
                            job = parser(board, record, detail_page, query)
                        if board.get("allow_cities"):
                            if _local_candidate(board, record if kind == "oracle" else row):
                                _local_evidence(job, record if kind == "oracle" else row)
                                jobs[job.source_job_id] = job
                        elif is_internship_title(job.title) and us_location(job.location, job.country) is not False:
                            jobs[job.source_job_id] = job
                    except RateLimited:
                        raise
                    except (HttpError, ValueError, KeyError, TypeError, IndexError) as exc:
                        problems.append(f"{ident}: {exc}")
                consumed = offset * 20 + len(rows) if kind == "jibe" else offset + len(rows)
                if total is not None and consumed >= total:
                    break
                if not fresh:
                    if total is not None:
                        problems.append(f"{query}: repeated page before advertised total {total}")
                    break
                offset += 1 if kind == "jibe" else len(rows)
            except RateLimited as exc:
                problems.append(f"rate limited: {exc}")
                return list(jobs.values()), problems
            except (HttpError, ValueError, KeyError, TypeError, IndexError) as exc:
                problems.append(f"{query}: {exc}")
                break
        else:
            problems.append(f"{query}: page cap {max_pages} reached")
    return list(jobs.values()), problems


class EmployersAdapter:
    name = "employers"

    def __init__(self, ctx: DiscoverContext):
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        boards = options.get("boards") or []
        queries = options.get("search_queries", ["intern", "co-op", "student"])
        max_pages = int(options.get("max_pages_per_query", 20))
        max_postings = int(options.get("max_postings_per_board", 500))
        workers = int(options.get("parallel_boards", 4))
        if not boards or not queries or not 1 <= max_pages <= 100 or not 1 <= max_postings <= 2000 or not 1 <= workers <= 8:
            return AdapterResult([], "error", "employers needs boards, queries and positive bounded limits",
                                 stats={"complete_snapshot": False})
        if self.name == "local_employers":
            cities = options.get("allow_cities") or []
            if not cities or any(board.get("kind") not in {"oracle", "jibe"} for board in boards):
                return AdapterResult([], "error", "local_employers needs allow_cities and Oracle/Jibe boards",
                                     stats={"complete_snapshot": False})
            boards = [board | {"allow_cities": cities} for board in boards]
        http = self.ctx.http_for(self.name)

        def one(board):
            with PublicPages(http) as pages:
                jobs, problems = fetch_board(pages, board, queries, max_pages, max_postings)
            for job in jobs:
                job.source = self.name
            status = "partial" if jobs and problems else "error" if problems else "ok"
            return jobs, BoardOutcome(board["company"], status, len(jobs), "; ".join(problems[:8]) or None)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, boards))
        outcomes = [outcome for _, outcome in results]
        jobs = [job for found, _ in results for job in found]
        status, error = summarize_boards(outcomes)
        if jobs and status != "ok":
            status = "partial"
        return AdapterResult(jobs, status, error, boards=outcomes, stats={
            "complete_snapshot": False, "boards": len(outcomes), "requests": http.requests_made,
            "cache_hits": http.cache_hits, "board_counts": {o.board: o.count for o in outcomes},
            "board_errors": {o.board: o.error for o in outcomes if o.error},
        })


class LocalEmployersAdapter(EmployersAdapter):
    """Separately selectable local inventory, without running internship boards."""

    name = "local_employers"
