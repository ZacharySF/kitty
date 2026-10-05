"""Adapter for Starbucks store jobs (apply.starbucks.com, an Eightfold careers site).

Starbucks posts its store jobs (barista, shift supervisor) on its own careers
site, and few of them reach Indeed, so part-time searches near home miss most
of them. apply.starbucks.com/robots.txt allows /api/pcsx: the site's own
search (`/api/pcsx/search`, ten positions a page, nearest first) and one
position's details (`/api/pcsx/position_details`, description and pay range).

`options.locations` in sources.yaml lists the places searched ("Springfield,
IL"). Pages are read up to the advertised total or `max_pages`; only jobs in
that city are kept and get a details request. A city boundary need not be a
contiguous distance band, so an intervening page of neighboring towns does not
end the search. Routing still decides what is stored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode

from applyai.core.discovery.base import AdapterResult, BoardOutcome, NormalizedJob, summarize_boards
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, PoliteHttp, RateLimited
from applyai.core.discovery.text import html_to_text, iso_to_utc

BASE = "https://apply.starbucks.com"
DOMAIN = "starbucks.com"
PAGE_SIZE = 10                 # the search API's fixed page size
DEFAULT_MAX_PAGES = 3


def search_url(location: str, start: int) -> str:
    """One page of the site's own search around `location`, nearest first."""
    params = {"domain": DOMAIN, "query": "", "location": location, "start": start, "sort_by": "distance"}
    return f"{BASE}/api/pcsx/search?{urlencode(params)}"


def details_url(position_id: int | str) -> str:
    return f"{BASE}/api/pcsx/position_details?{urlencode({'position_id': position_id, 'domain': DOMAIN, 'hl': 'en'})}"


def city_of(location: str) -> str:
    """'Springfield, IL' -> 'springfield' (how positions' standardized locations start)."""
    return location.split(",")[0].strip().lower()


def in_city(position: dict, city: str) -> bool:
    """True when one of the position's standardized locations is in `city`."""
    return any(place.lower().startswith(f"{city},") for place in position.get("standardizedLocations") or [])


def pay_line(details: dict) -> str | None:
    """'15.25 to 17.31 USD' -> 'Pay: $15.25 - $17.31 per hour' (store jobs are hourly)."""
    ranges = details.get("efcustomTextPayRange") or []
    text = ranges[0] if isinstance(ranges, list) and ranges else ranges if isinstance(ranges, str) else ""
    parts = text.replace("USD", "").split(" to ")
    try:
        low, high = (float(part) for part in parts) if len(parts) == 2 else (float(parts[0]),) * 2
    except ValueError:
        return None
    return f"Pay: ${low:g} - ${high:g} per hour" if low != high else f"Pay: ${low:g} per hour"


def parse_position(position: dict, details: dict | None) -> NormalizedJob:
    """One search position (plus its details, when fetched) to a NormalizedJob."""
    details = details or {}
    description = html_to_text(details.get("jobDescription"))
    pay = pay_line(details)
    if pay:
        description = f"{pay}\n\n{description}" if description else pay
    posted = position.get("postedTs")
    places = position.get("standardizedLocations") or []
    url = details.get("publicUrl") or f"{BASE}{position.get('positionUrl') or ''}"
    return NormalizedJob(
        source="starbucks",
        source_job_id=str(position["id"]),
        company="Starbucks",
        title=(position.get("name") or "").strip(),
        url=url,
        location=places[0] if places else None,
        is_remote=False,
        country="United States",
        description=description,
        posted_at=iso_to_utc(datetime.fromtimestamp(posted, UTC).strftime("%Y-%m-%d")) if posted else None,
        raw={"id": position.get("id"), "department": position.get("department"),
             "location": position.get("locations")},
    )


class StarbucksAdapter:
    name = "starbucks"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        locations = options.get("locations") or []
        if not locations:
            return AdapterResult([], "error", "no locations in sources.yaml starbucks.options.locations")
        max_pages = int(options.get("max_pages", DEFAULT_MAX_PAGES))
        http = self.ctx.http_for(self.name)
        jobs: dict[str, NormalizedJob] = {}
        outcomes = []
        for location in locations:
            outcome, found = self._location(http, location, max_pages)
            outcomes.append(outcome)
            jobs.update((job.source_job_id, job) for job in found)
            if outcome.status == "rate_limited":
                break  # do not retry the same host for another city's search
        status, error = summarize_boards(outcomes)
        if jobs and status != "ok":
            status = "partial"
        return AdapterResult(list(jobs.values()), status, error, boards=outcomes,
                             stats={"complete_snapshot": False,
                                    "locations": {o.board: o.error or o.count for o in outcomes}})

    def _location(self, http: PoliteHttp, location: str, max_pages: int) -> tuple[BoardOutcome, list[NormalizedJob]]:
        """Search bounded nearby pages; absence cannot establish a store job's closure."""
        city = city_of(location)
        found: list[NormalizedJob] = []
        try:
            for page in range(max_pages):
                payload = http.get_json(search_url(location, page * PAGE_SIZE))
                positions = ((payload or {}).get("data") or {}).get("positions") or []
                here = [p for p in positions if in_city(p, city)]
                for position in here:
                    details = (http.get_json(details_url(position["id"])) or {}).get("data")
                    job = parse_position(position, details)
                    job.observed_at = getattr(http, "retrieved_at", None)
                    found.append(job)
                total = ((payload or {}).get("data") or {}).get("count")
                if len(positions) < PAGE_SIZE or (total is not None and (page + 1) * PAGE_SIZE >= int(total)):
                    break
            else:
                return BoardOutcome(location, "partial", len(found), f"page cap {max_pages} reached"), found
        except RateLimited as exc:
            return BoardOutcome(location, "rate_limited", len(found), str(exc)), found
        except (HttpError, ValueError, KeyError) as exc:
            return BoardOutcome(location, "error", len(found), f"{type(exc).__name__}: {exc}"), found
        return BoardOutcome(location, "ok", len(found)), found
