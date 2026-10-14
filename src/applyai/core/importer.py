"""CSV import of applications I tracked before this tool existed.

The flow is preview-then-confirm: `plan_import` reads the file and says exactly
what it would create, `apply_import` writes it. Both are idempotent through the
job dedupe_key, so re-importing the same file changes nothing.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from applyai.core import applications as applications_core
from applyai.core import jobs as jobs_core
from applyai.core import sources as sources_core
from applyai.dates import now_iso, parse_user_date
from applyai.db.connection import transaction
from applyai.normalize import dedupe_key

#: Header names we recognise without a --map. Lowercased, punctuation-free.
DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "company": ("company", "company name", "employer", "organization"),
    "title": ("title", "role", "position", "job title", "job"),
    "url": ("url", "link", "job url", "posting", "job link"),
    "location": ("location", "city", "where"),
    "status": ("status", "stage", "outcome", "result"),
    "applied_at": ("applied_at", "applied", "date applied", "date", "applied on"),
    "track": ("track", "category", "type"),
    "resume": ("resume", "resume used", "cv"),
    "notes": ("notes", "note", "comment", "comments"),
}

#: Free-text status values I might have typed in a spreadsheet.
STATUS_ALIASES: dict[str, str] = {
    "applied": "applied", "submitted": "applied", "in progress": "applied",
    "oa": "oa", "online assessment": "oa", "assessment": "oa", "hackerrank": "oa",
    "codesignal": "oa", "test": "oa",
    "interview": "interview", "phone screen": "interview", "onsite": "interview",
    "superday": "interview", "final round": "interview",
    "offer": "offer", "accepted": "offer",
    "rejected": "rejected", "reject": "rejected", "denied": "rejected", "no": "rejected",
    "ghosted": "ghosted", "no response": "ghosted", "silent": "ghosted",
    "withdrawn": "withdrawn", "withdrew": "withdrawn",
}


def _normalize_header(header: str) -> str:
    """Lowercase a CSV header and strip punctuation, for alias matching."""
    return " ".join(header.strip().lower().replace("_", " ").split())


def build_mapping(
    headers: Sequence[str], overrides: dict[str, str] | None = None
) -> dict[str, str]:
    """Map our field names to the file's column names.

    `overrides` comes from --map field=column and always wins.
    """
    mapping: dict[str, str] = {}
    normalized = {_normalize_header(h): h for h in headers}
    for field_name, aliases in DEFAULT_ALIASES.items():
        for alias in aliases:
            alias = _normalize_header(alias)
            if alias in normalized:
                mapping[field_name] = normalized[alias]
                break
    for field_name, column in (overrides or {}).items():
        mapping[field_name] = column
    return mapping


@dataclass
class ImportRow:
    """One CSV row, resolved into what it would create."""

    line: int
    company: str
    title: str
    url: str
    location: str | None
    status: str
    applied_at: str | None
    track: str | None
    resume: str | None
    notes: str | None
    action: str = "create"          # create | update | skip
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None


@dataclass
class ImportPlan:
    """What an import would do, shown to me before anything is written."""

    path: Path
    mapping: dict[str, str]
    rows: list[ImportRow] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)

    @property
    def creatable(self) -> list[ImportRow]:
        return [row for row in self.rows if row.ok and row.action == "create"]

    @property
    def problems(self) -> list[ImportRow]:
        return [row for row in self.rows if not row.ok]

    @property
    def already_present(self) -> list[ImportRow]:
        return [row for row in self.rows if row.ok and row.action == "skip"]


def plan_import(
    conn: sqlite3.Connection,
    path: Path,
    *,
    overrides: dict[str, str] | None = None,
    valid_tracks: Sequence[str] = (),
) -> ImportPlan:
    """Read a CSV and work out what importing it would create. Writes nothing."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        mapping = build_mapping(headers, overrides)
        plan = ImportPlan(
            path=path,
            mapping=mapping,
            unmapped_columns=[h for h in headers if h not in mapping.values()],
        )

        for offset, raw in enumerate(reader, start=2):   # line 1 is the header
            def value(name: str) -> str | None:
                column = mapping.get(name)
                if not column:
                    return None
                text = (raw.get(column) or "").strip()
                return text or None

            company, title = value("company"), value("title")
            status_text = (value("status") or "applied").strip().lower()
            row = ImportRow(
                line=offset,
                company=company or "",
                title=title or "",
                url=value("url") or "",
                location=value("location"),
                status=STATUS_ALIASES.get(status_text, status_text),
                applied_at=parse_user_date(value("applied_at") or "") if value("applied_at") else None,
                track=value("track"),
                resume=value("resume"),
                notes=value("notes"),
            )

            if not company or not title:
                row.problem = "needs both a company and a title"
            elif row.status not in {
                "applied", "oa", "interview", "offer", "rejected", "ghosted", "withdrawn"
            }:
                row.problem = f"unknown status '{status_text}'"
            elif row.track and valid_tracks and row.track not in valid_tracks:
                row.problem = f"unknown track '{row.track}'"
            else:
                key = dedupe_key(company, title, row.location)
                existing = conn.execute(
                    "SELECT id FROM jobs WHERE dedupe_key = ?", (key,)
                ).fetchone()
                if existing:
                    has_app = conn.execute(
                        "SELECT 1 FROM applications WHERE job_id = ?", (existing["id"],)
                    ).fetchone()
                    row.action = "skip" if has_app else "update"

            plan.rows.append(row)
    return plan


def apply_import(conn: sqlite3.Connection, plan: ImportPlan) -> dict[str, int]:
    """Write an import plan: jobs, applications and confirmed status events.

    A row whose status is past 'applied' gets two events: the application itself,
    then the move to the status I recorded - so the history is honest about what
    is known and what is inferred from the spreadsheet.
    """
    from applyai.core import resumes as resumes_core

    stats = {"jobs_created": 0, "applications_created": 0, "events": 0, "skipped": 0}
    run_id = sources_core.start_run(conn, "import")

    with transaction(conn):
        for row in plan.rows:
            if not row.ok or row.action == "skip":
                stats["skipped"] += 1
                continue

            job, created = jobs_core.upsert_job(
                conn,
                company=row.company,
                title=row.title,
                url=row.url or f"imported:{row.company}/{row.title}",
                location=row.location,
                source="manual",
                source_job_id=f"import:{plan.path.name}:{row.line}",
                raw_json=json.dumps({"csv_line": row.line, "file": plan.path.name}),
                tracks=[row.track] if row.track else (),
                match_reason="csv import",
            )
            stats["jobs_created"] += int(created)

            resume = resumes_core.get_resume_by_label(conn, row.resume) if row.resume else None
            application = applications_core.record_application(
                conn,
                job.id,
                resume_id=resume.id if resume else None,
                track=row.track,
                applied_at=row.applied_at or now_iso(),
                notes=row.notes,
                source="import",
                evidence=f"{plan.path.name}:{row.line}",
            )
            stats["applications_created"] += 1
            stats["events"] += 1

            if row.status != "applied":
                applications_core.set_status(
                    conn,
                    application.id,
                    row.status,                     # validated in plan_import
                    occurred_at=row.applied_at or now_iso(),
                    source="import",
                    evidence=f"{plan.path.name}:{row.line}",
                )
                stats["events"] += 1

    sources_core.finish_run(conn, run_id, stats=stats)
    return stats
