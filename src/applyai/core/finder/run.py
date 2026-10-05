"""`applyai finder`: gather candidate firms, score them, find their boards, store the proposals.

One run: every adapter in ADAPTERS fetches candidates (each with an honest
status); sightings of the same firm are merged; firms already on the
watchlist or ever proposed are dropped; the rest are scored against
config/skills.yaml and at most `cap` are picked within each area's quota; each
pick gets an ATS lookup; the picks are stored as pending firm_candidates and
the run is written to `runs` (kind 'finder') with per-adapter statuses.

SBIR awards come from the bulk CSV (no abstracts, ~85 MB), cached 30 days: the
awards API has been under maintenance (403 on 2026-10-01).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from applyai.config import Config
from applyai.core import sources as sources_core
from applyai.core.discovery.http import PoliteHttp
from applyai.core.finder import store
from applyai.core.finder.ats_detect import detect_ats
from applyai.core.finder.base import CandidateBatch, FinderContext, merge_by_name
from applyai.core.finder.conferences import ConferencesAdapter
from applyai.core.finder.github import API_CLIENT, SEARCH_CLIENT, GithubAdapter
from applyai.core.finder.hn import HackerNewsCompaniesAdapter
from applyai.core.finder.sbir import SbirAdapter
from applyai.core.finder.scoring import select
from applyai.core.finder.yc import YcAdapter
from applyai.db import connect
from applyai.db.connection import transaction
from applyai.settings import Settings

log = logging.getLogger(__name__)

#: Never propose more than this many firms in one run unless the caller asks for a different cap.
DEFAULT_CAP = 15

#: Every candidate source, in the order a run asks them.
ADAPTERS = {
    "yc-oss": YcAdapter,
    "hackernews": HackerNewsCompaniesAdapter,
    "conferences": ConferencesAdapter,
    "github": GithubAdapter,
    "sbir": SbirAdapter,
}

#: HTTP client name -> (seconds between requests to one host, cache minutes).
#: "hackernews" is not here: it uses the discovery source's own settings and cache.
HTTP_CLIENTS: dict[str, tuple[float, int]] = {
    "yc-oss": (2.0, 24 * 60),                 # rebuilt daily
    "conferences": (3.0, 30 * 24 * 60),       # sponsor pages change yearly
    SEARCH_CLIENT: (7.0, 7 * 24 * 60),        # 10 searches a minute, unauthenticated
    API_CLIENT: (2.0, 7 * 24 * 60),           # 60 requests an hour, unauthenticated
    "ats": (1.0, 7 * 24 * 60),
    "sbir": (2.0, 30 * 24 * 60),              # one ~85 MB CSV, refreshed monthly
}


class FinderError(Exception):
    """The finder cannot run (no skill areas to score against)."""


@dataclass
class AdapterReport:
    """How one candidate source went in this run."""

    status: str
    found: int
    error: str | None = None
    stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"status": self.status, "found": self.found, "error": self.error, "stats": self.stats}


@dataclass
class FinderResult:
    run_id: int
    cap: int
    adapters: dict[str, AdapterReport]
    proposed: list[store.Candidate]
    proposed_by_area: dict[str, int]
    skipped: dict[str, int]
    ats_found: int


def _http_factory(settings: Settings, config: Config) -> Callable[[str], PoliteHttp]:
    """Build each named client with its own pacing and cache directory."""

    def build(name: str) -> PoliteHttp:
        if name == "hackernews":
            source = config.sources.sources.get("hackernews")
            return PoliteHttp(settings.cache_dir / "http" / "hackernews",
                              min_interval=source.min_interval_seconds if source else 3.0,
                              cache_ttl_minutes=source.cache_ttl_minutes if source else 720)
        interval, ttl = HTTP_CLIENTS[name]
        return PoliteHttp(settings.cache_dir / "http" / f"finder-{name}",
                          min_interval=interval, cache_ttl_minutes=ttl)

    return build


def _fetch(name: str, adapter_class, ctx: FinderContext) -> CandidateBatch:
    """Run one adapter; a crash is reported as that adapter's error, not the run's."""
    try:
        return adapter_class(ctx).fetch()
    except Exception as exc:                  # an adapter bug must not stop the run
        log.exception("finder adapter %s crashed", name)
        return CandidateBatch([], "error", f"adapter crashed: {type(exc).__name__}: {exc}")


def run_finder(
    settings: Settings,
    config: Config,
    *,
    cap: int | None = None,
    progress: Callable[[str], None] | None = None,
    http_factory: Callable[[str], PoliteHttp] | None = None,
    adapters: dict | None = None,
) -> FinderResult:
    """Run the finder once and store its proposals. Opens its own connection (thread-safe).

    `cap` overrides DEFAULT_CAP. `http_factory` and `adapters` exist so tests can
    run without the network.
    """
    limit = DEFAULT_CAP if cap is None else cap
    if limit < 0:
        raise ValueError("cap must be 0 or more")
    areas = [a for a in config.skills.areas if a.keywords]
    if not any(a.per_run_quota > 0 for a in areas):
        raise FinderError("config/skills.yaml has no area with keywords and a per_run_quota above 0; "
                          "copy config/skills.yaml.example and edit it")
    say = progress or (lambda message: None)
    clients: dict[str, PoliteHttp] = {}
    build = http_factory or _http_factory(settings, config)

    def http_for(name: str) -> PoliteHttp:
        if name not in clients:
            clients[name] = build(name)
        return clients[name]

    conn = connect(settings.db_path)
    run_id: int | None = None
    try:
        run_id = sources_core.start_run(conn, "finder")
        known = store.known_names(conn, config)
        ctx = FinderContext(config, http_for, known)
        reports: dict[str, AdapterReport] = {}
        found = []
        for name, adapter_class in (adapters or ADAPTERS).items():
            say(f"{name}: fetching")
            batch = _fetch(name, adapter_class, ctx)
            reports[name] = AdapterReport(batch.status, len(batch.candidates), batch.error, batch.stats)
            found.extend(batch.candidates)
            say(f"{name}: {batch.status}, {len(batch.candidates)} candidate firm(s)"
                + (f" ({batch.error})" if batch.error else ""))

        merged = merge_by_name(found)
        selection = select(merged, areas, known, limit)
        say(f"{len(merged)} distinct firms; proposing {len(selection.proposed)} (cap {limit})")

        stored_ids = []
        ats_found = 0
        for pick in selection.proposed:
            say(f"looking for {pick.raw.name}'s job board")
            detection = detect_ats(http_for("ats"), pick.raw)
            hit = detection.hit
            ats_found += hit is not None
            with transaction(conn):
                stored_ids.append(store.insert_candidate(
                    conn, name=pick.raw.name, area=pick.area, score=pick.score, reason=pick.reason,
                    ats_kind=hit.kind if hit else None, ats_slug=hit.slug if hit else None,
                    board_url=hit.board_url if hit else None, website=pick.raw.website,
                    evidence=[*pick.raw.evidence, *detection.evidence], run_id=run_id,
                ))

        sources_core.finish_run(conn, run_id, stats={
            "adapters": {name: report.as_dict() for name, report in reports.items()},
            "distinct_firms": len(merged),
            "proposed": len(stored_ids),
            "proposed_by_area": selection.proposed_by_area,
            "skipped": selection.skipped,
            "cap": limit,
            "ats_found": ats_found,
        })
        proposed = [c for c in (store.get_candidate(conn, i) for i in stored_ids) if c is not None]
        return FinderResult(run_id, limit, reports, proposed, selection.proposed_by_area,
                            selection.skipped, ats_found)
    except Exception as exc:
        if run_id is not None:
            sources_core.finish_run(conn, run_id, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        for client in clients.values():
            client.close()
        conn.close()
