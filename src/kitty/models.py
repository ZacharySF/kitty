"""Typed domain objects returned by the core API.

These are plain dataclasses built from sqlite rows. The TUI, the CLI and (later)
the MCP server all consume these instead of raw rows, so the storage shape can
change without touching the UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal, Sequence

from kitty.dates import now, parse

Status = Literal["applied", "oa", "interview", "offer", "rejected", "ghosted", "withdrawn"]
QueueState = Literal["new", "queued", "later", "skipped", "not_eligible", "applied"]
Tier = Literal["A", "B", "C"]
Eligibility = Literal["eligible", "ineligible", "ambiguous", "boosted", "unknown"]
EventState = Literal["confirmed", "proposed", "rejected_by_me"]
FillState = Literal["unattempted", "attempted", "partial", "blocked", "ready"]
VerificationStatus = Literal["unverified", "open", "closed", "blocked", "error"]

#: Live availability checks expire independently of discovery sightings.
VERIFICATION_MAX_AGE = timedelta(days=7)

#: Pipeline column order, used by the kanban board and the funnel.
PIPELINE_STATUSES: tuple[Status, ...] = (
    "applied", "oa", "interview", "offer", "rejected", "ghosted",
)

#: Statuses that mean "still in play".
OPEN_STATUSES: frozenset[str] = frozenset({"applied", "oa", "interview", "offer"})


def _loads(raw: str | None) -> Any:
    """Parse a json column, tolerating NULL and malformed values."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


@dataclass(frozen=True)
class Company:
    id: int
    norm_name: str
    display_name: str
    ats_kind: str | None = None
    ats_slug: str | None = None
    website: str | None = None
    notes: str | None = None
    school_selective: bool = False
    from_watchlist: bool = False
    priority: int = 0
    expected_open_months: list[int] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: Any) -> "Company":
        return cls(
            id=row["id"],
            norm_name=row["norm_name"],
            display_name=row["display_name"],
            ats_kind=row["ats_kind"],
            ats_slug=row["ats_slug"],
            website=row["website"],
            notes=row["notes"],
            school_selective=bool(row["school_selective"]),
            from_watchlist=bool(row["from_watchlist"]),
            priority=row["priority"],
            expected_open_months=_loads(row["expected_open_months"]) or [],
        )


@dataclass(frozen=True)
class Resume:
    id: int
    label: str
    file_path: str
    file_hash: str
    description: str | None = None
    default_tracks: list[str] = field(default_factory=list)
    active: bool = True

    @classmethod
    def from_row(cls, row: Any) -> "Resume":
        return cls(
            id=row["id"],
            label=row["label"],
            file_path=row["file_path"],
            file_hash=row["file_hash"],
            description=row["description"],
            default_tracks=_loads(row["default_tracks"]) or [],
            active=bool(row["active"]),
        )


@dataclass(frozen=True)
class JobSource:
    source: str
    source_job_id: str | None
    source_url: str | None
    first_seen_at: str
    last_seen_at: str
    url_is_listing: bool = False

    @classmethod
    def from_row(cls, row: Any) -> "JobSource":
        raw = _loads(row["raw_json"])
        provenance = raw.get("provenance") if isinstance(raw, dict) else None
        return cls(
            source=row["source"],
            source_job_id=row["source_job_id"],
            source_url=row["source_url"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            url_is_listing=isinstance(provenance, dict) and provenance.get("url_is_listing") is True,
        )


@dataclass(frozen=True)
class Job:
    """A posting, joined with its company, tracks and sources."""

    id: int
    company_id: int
    company_name: str
    title: str
    url: str
    location: str | None = None
    is_remote: bool | None = None
    apply_url: str | None = None
    season: str | None = None
    jd_text: str | None = None
    dedupe_key: str = ""
    posted_at: str | None = None
    deadline_at: str | None = None
    opens_at: str | None = None
    first_seen_at: str = ""
    last_seen_at: str = ""
    closed_at: str | None = None
    is_active: bool = True
    eligibility: Eligibility = "unknown"
    eligibility_reason: str | None = None
    tier: Tier | None = None
    tier_source: str | None = None
    tier_reason: str | None = None
    resume_id: int | None = None
    resume_label: str | None = None
    edit_notes: list[str] = field(default_factory=list)
    triaged_at: str | None = None
    school_selective: bool = False
    school_selective_note: str | None = None
    queue_state: QueueState = "new"
    my_notes: str | None = None
    tracks: list[str] = field(default_factory=list)
    sources: list[JobSource] = field(default_factory=list)
    application_id: int | None = None
    status: Status | None = None
    next_action_at: str | None = None
    next_action_note: str | None = None
    #: When I starred it (None = not a favorite).
    favorited_at: str | None = None
    #: Only ready means a verified form is waiting for review, never submission.
    fill_state: FillState = "unattempted"
    fill_attempted_at: str | None = None
    fill_ready_at: str | None = None
    fill_blocker: str | None = None
    fill_context_id: str | None = None
    fill_url: str | None = None
    verification_status: VerificationStatus = "unverified"
    verified_at: str | None = None
    verification_checked_at: str | None = None
    verification_reason: str | None = None
    #: Like my favorites minus like my skips (core/likeness.py); None without favorites.
    like_score: float | None = None
    #: Terms it shares most with my favorites, for "like your favorites: ...".
    like_terms: list[str] = field(default_factory=list)

    @property
    def is_favorite(self) -> bool:
        return self.favorited_at is not None

    @property
    def freshness(self) -> str:
        """Live evidence quality; failed checks never make an old posting fresh."""
        if not self.is_active or self.verification_status == "closed":
            return "closed"
        checked = parse(self.verified_at)
        if checked is None:
            return "unverified"
        age = now() - checked
        if self.verification_status == "open" and timedelta(0) <= age <= VERIFICATION_MAX_AGE:
            return "verified"
        return "stale"

    @classmethod
    def from_row(
        cls,
        row: Any,
        *,
        tracks: Sequence[str] = (),
        sources: Sequence[JobSource] = (),
    ) -> "Job":
        keys = row.keys()
        return cls(
            id=row["id"],
            company_id=row["company_id"],
            company_name=row["company_name"] if "company_name" in keys else "",
            title=row["title"],
            url=row["url"],
            location=row["location"],
            is_remote=None if row["is_remote"] is None else bool(row["is_remote"]),
            apply_url=row["apply_url"],
            season=row["season"],
            jd_text=row["jd_text"],
            dedupe_key=row["dedupe_key"],
            posted_at=row["posted_at"],
            deadline_at=row["deadline_at"] or row["source_deadline_at"],
            opens_at=row["opens_at"] or row["source_opens_at"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            closed_at=row["closed_at"],
            is_active=bool(row["is_active"]),
            eligibility=row["eligibility"],
            eligibility_reason=row["eligibility_reason"],
            tier=row["tier"],
            tier_source=row["tier_source"],
            tier_reason=row["tier_reason"],
            resume_id=row["resume_id"],
            resume_label=row["resume_label"] if "resume_label" in keys else None,
            edit_notes=_loads(row["edit_notes"]) or [],
            triaged_at=row["triaged_at"],
            school_selective=bool(row["school_selective"]),
            school_selective_note=row["school_selective_note"],
            queue_state=row["queue_state"],
            my_notes=row["my_notes"],
            tracks=list(tracks),
            sources=list(sources),
            application_id=row["application_id"] if "application_id" in keys else None,
            status=row["status"] if "status" in keys else None,
            next_action_at=row["next_action_at"] if "next_action_at" in keys else None,
            next_action_note=row["next_action_note"] if "next_action_note" in keys else None,
            favorited_at=row["favorited_at"],
            fill_state=row["fill_state"],
            fill_attempted_at=row["fill_attempted_at"],
            fill_ready_at=row["fill_ready_at"],
            fill_blocker=row["fill_blocker"],
            fill_context_id=row["fill_context_id"],
            fill_url=row["fill_url"],
            verification_status=row["verification_status"],
            verified_at=row["verified_at"],
            verification_checked_at=row["verification_checked_at"],
            verification_reason=row["verification_reason"],
            like_score=row["like_score"],
            like_terms=_loads(row["like_terms"]) or [],
        )


@dataclass(frozen=True)
class StatusEvent:
    id: int
    application_id: int
    from_status: str | None
    to_status: str
    occurred_at: str
    source: str
    state: EventState
    evidence: str | None = None
    undoes_event_id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> "StatusEvent":
        return cls(
            id=row["id"],
            application_id=row["application_id"],
            from_status=row["from_status"],
            to_status=row["to_status"],
            occurred_at=row["occurred_at"],
            source=row["source"],
            state=row["state"],
            evidence=row["evidence"],
            undoes_event_id=row["undoes_event_id"],
        )


@dataclass(frozen=True)
class Application:
    id: int
    job_id: int
    status: Status
    applied_at: str
    last_activity_at: str
    track: str | None = None
    resume_id: int | None = None
    resume_label: str | None = None
    next_action_at: str | None = None
    next_action_note: str | None = None
    notes: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Application":
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            status=row["status"],
            applied_at=row["applied_at"],
            last_activity_at=row["last_activity_at"],
            track=row["track"],
            resume_id=row["resume_id"],
            resume_label=row["resume_label"] if "resume_label" in keys else None,
            next_action_at=row["next_action_at"],
            next_action_note=row["next_action_note"],
            notes=row["notes"],
        )


@dataclass(frozen=True)
class PipelineCard:
    """One card on the kanban board."""

    application_id: int
    job_id: int
    company_name: str
    title: str
    status: Status
    track: str | None
    resume_label: str | None
    applied_at: str
    last_activity_at: str
    days_since_change: int
    next_action_at: str | None
    has_proposals: bool = False


@dataclass(frozen=True)
class TrackProgress:
    """Weekly applications against the track's target."""

    track: str
    applied_this_week: int
    weekly_target: int

    @property
    def remaining(self) -> int:
        return max(0, self.weekly_target - self.applied_this_week)

    @property
    def fraction(self) -> float:
        if self.weekly_target <= 0:
            return 1.0 if self.applied_this_week else 0.0
        return min(1.0, self.applied_this_week / self.weekly_target)


@dataclass(frozen=True)
class Reminder:
    id: int
    kind: str
    due_at: str
    note: str | None
    job_id: int | None
    application_id: int | None
    done_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Reminder":
        return cls(
            id=row["id"],
            kind=row["kind"],
            due_at=row["due_at"],
            note=row["note"],
            job_id=row["job_id"],
            application_id=row["application_id"],
            done_at=row["done_at"],
        )


@dataclass(frozen=True)
class ScreeningQuestion:
    id: int
    text: str
    norm_hash: str
    times_seen: int
    answered: bool
    first_seen_job_id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ScreeningQuestion":
        return cls(
            id=row["id"],
            text=row["text"],
            norm_hash=row["norm_hash"],
            times_seen=row["times_seen"],
            answered=bool(row["answered"]),
            first_seen_job_id=row["first_seen_job_id"],
        )


@dataclass(frozen=True)
class SourceHealth:
    name: str
    kind: str
    enabled: bool
    fragile: bool
    last_run_at: str | None
    last_success_at: str | None
    last_status: str | None
    last_error: str | None
    last_count: int | None

    @classmethod
    def from_row(cls, row: Any) -> "SourceHealth":
        return cls(
            name=row["name"],
            kind=row["kind"],
            enabled=bool(row["enabled"]),
            fragile=bool(row["fragile"]),
            last_run_at=row["last_run_at"],
            last_success_at=row["last_success_at"],
            last_status=row["last_status"],
            last_error=row["last_error"],
            last_count=row["last_count"],
        )


@dataclass(frozen=True)
class Check:
    """One `kitty doctor` result line.

    `ok=False` fails the command; a warning (`ok=True, warning=True`) is worth
    telling me about but does not stop anything.
    """

    name: str
    ok: bool
    detail: str
    fix: str | None = None
    warning: bool = False

    @property
    def symbol(self) -> str:
        if not self.ok:
            return "FAIL"
        return "WARN" if self.warning else "OK"
