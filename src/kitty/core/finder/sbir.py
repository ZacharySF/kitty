"""Candidate firms from SBIR/STTR awards: small US companies doing funded R&D.

The awards API (api.www.sbir.gov) has been under maintenance and answered 403
on 2026-10-01, so this adapter uses the public bulk CSV without abstracts
(about 85 MB). It is downloaded once and cached for 30 days; the file itself
is refreshed by SBIR.gov a few times a year. Only award titles are in that
file, so scoring sees the titles of each firm's recent awards.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date

from kitty.core.discovery.http import HttpError, RateLimited
from kitty.core.finder.base import CandidateBatch, FinderContext, RawCandidate, SourceText
from kitty.dates import now
from kitty.normalize import norm_company

CSV_URL = "https://data.www.sbir.gov/awarddatapublic_no_abstract/award_data_no_abstract.csv"
NAME = "sbir"

#: Awards older than this many years (before the current one) are ignored: the firm
#: may have moved on, and a recent award is the signal that it is building things now.
RECENT_YEARS = 3
#: Award titles kept per firm, newest first, so one prolific firm does not dominate the text.
MAX_TITLES = 25
_COLUMNS = {"Company", "Award Title", "Award Year", "Agency"}


@dataclass
class _Firm:
    """Everything the CSV says about one firm's recent awards."""

    name: str
    website: str | None = None
    place: str = ""
    employees: str = ""
    titles: list[tuple[int, str]] = field(default_factory=list)
    agencies: list[str] = field(default_factory=list)


def _display_name(name: str) -> str:
    """'ACME PHOTONICS, INC.' -> 'Acme Photonics, Inc.'; mixed-case names are kept."""
    return name.title() if name.isupper() else name


def _year(value: str) -> int | None:
    try:
        return int(value.strip())
    except ValueError:
        return None


def _add_row(firms: dict[str, _Firm], row: dict, min_year: int) -> bool:
    """Fold one award row into `firms`; False when the row is too old or unusable."""
    name = (row.get("Company") or "").strip()
    year = _year(row.get("Award Year") or "")
    title = (row.get("Award Title") or "").strip()
    if not name or not title or year is None or year < min_year:
        return False
    firm = firms.setdefault(norm_company(name), _Firm(name=_display_name(name)))
    firm.titles.append((year, title))
    firm.website = firm.website or (row.get("Company Url") or "").strip() or None
    firm.place = firm.place or ", ".join(p for p in ((row.get("City") or "").strip(),
                                                     (row.get("State") or "").strip()) if p)
    firm.employees = firm.employees or (row.get("Number Employees") or "").strip()
    agency = " ".join(p for p in ((row.get("Agency") or "").strip(), (row.get("Branch") or "").strip()) if p)
    if agency and agency not in firm.agencies:
        firm.agencies.append(agency)
    return True


def _candidate(firm: _Firm) -> RawCandidate:
    """One firm's awards to a RawCandidate: titles are the text the scorer reads."""
    titles = [title for _, title in sorted(firm.titles, key=lambda t: -t[0])]
    unique = list(dict.fromkeys(titles))[:MAX_TITLES]
    years = sorted({year for year, _ in firm.titles})
    span = f"{years[0]}" if len(years) == 1 else f"{years[0]}-{years[-1]}"
    evidence = (f"{NAME}: {len(firm.titles)} award(s) {span} from {', '.join(firm.agencies[:3])}; "
                f"{firm.place or 'location unknown'}; {firm.employees or '?'} employees")
    return RawCandidate(
        name=firm.name,
        texts=[SourceText(NAME, "award titles", "; ".join(unique))],
        website=firm.website,
        evidence=[evidence],
    )


def parse_awards(text: io.TextIOBase, *, today: date | None = None) -> tuple[list[RawCandidate], dict]:
    """The awards CSV to one candidate per firm with an award in the last RECENT_YEARS years.

    Raises ValueError when the header no longer has the columns this parser reads.
    """
    min_year = (today or now().date()).year - RECENT_YEARS
    reader = csv.DictReader(text)
    missing = _COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"SBIR CSV is missing columns {sorted(missing)}: the format changed")
    firms: dict[str, _Firm] = {}
    stats = {"rows": 0, "recent_rows": 0, "min_year": min_year}
    for row in reader:
        stats["rows"] += 1
        stats["recent_rows"] += _add_row(firms, row, min_year)
    stats["firms"] = len(firms)
    return [_candidate(firm) for firm in firms.values()], stats


class SbirAdapter:
    """Small firms with recent SBIR/STTR awards, scored on their award titles."""

    name = NAME

    def __init__(self, ctx: FinderContext) -> None:
        self.ctx = ctx

    def fetch(self) -> CandidateBatch:
        http = self.ctx.http_for(self.name)
        try:
            data = http.get_bytes(CSV_URL)
        except RateLimited as exc:
            return CandidateBatch([], "rate_limited", str(exc))
        except HttpError as exc:
            return CandidateBatch([], "error", f"{type(exc).__name__}: {exc}")
        try:
            text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace", newline="")
            candidates, stats = parse_awards(text)
        except (ValueError, csv.Error) as exc:
            return CandidateBatch([], "error", str(exc))
        candidates = [c for c in candidates if c.norm_name not in self.ctx.known]
        stats.update(requests=http.requests_made, cache_hits=http.cache_hits)
        return CandidateBatch(candidates, "ok", stats=stats)
