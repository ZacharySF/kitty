"""`applyai export`: every job, application and status change, as CSV files or one JSON file.

The export is for my own spreadsheets and backups. CSV cells that start with
= + - or @ are prefixed with a single quote, so a spreadsheet shows them as text
instead of running them as formulas (a job title from the internet is untrusted).
JSON keeps values as they are: it is not opened by a spreadsheet.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from applyai.dates import now_iso

#: Characters a spreadsheet treats as the start of a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@")

#: Each exported table: name -> query. Joins add the names a reader needs
#: (company, title, resume label) so the files make sense on their own.
QUERIES: dict[str, str] = {
    "jobs": """
        SELECT j.*, c.display_name AS company,
               (SELECT group_concat(jt.track, ';') FROM job_tracks jt WHERE jt.job_id = j.id) AS tracks,
               (SELECT group_concat(js.source, ';') FROM job_sources js WHERE js.job_id = j.id) AS sources
          FROM jobs j
          LEFT JOIN companies c ON c.id = j.company_id
         ORDER BY j.id
    """,
    "applications": """
        SELECT a.*, c.display_name AS company, j.title AS title, j.url AS url, r.label AS resume
          FROM applications a
          JOIN jobs j ON j.id = a.job_id
          LEFT JOIN companies c ON c.id = j.company_id
          LEFT JOIN resumes r ON r.id = a.resume_id
         ORDER BY a.id
    """,
    "status_events": """
        SELECT e.*, a.job_id AS job_id, c.display_name AS company, j.title AS title
          FROM status_events e
          JOIN applications a ON a.id = e.application_id
          JOIN jobs j ON j.id = a.job_id
          LEFT JOIN companies c ON c.id = j.company_id
         ORDER BY e.id
    """,
}


@dataclass(frozen=True)
class ExportResult:
    """What was written: the files and how many rows each table had."""

    paths: list[Path]
    counts: dict[str, int]


def escape_cell(value: object) -> object:
    """Prefix text that a spreadsheet would run as a formula with a single quote."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def table_rows(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    """Column names and rows of one exported table."""
    cursor = conn.execute(QUERIES[table])
    columns = [description[0] for description in cursor.description]
    return columns, [tuple(row) for row in cursor.fetchall()]


def export_csv(conn: sqlite3.Connection, out_dir: Path) -> ExportResult:
    """Write jobs.csv, applications.csv and status_events.csv into `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths, counts = [], {}
    for table in QUERIES:
        columns, rows = table_rows(conn, table)
        path = out_dir / f"{table}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows([escape_cell(value) for value in row] for row in rows)
        paths.append(path)
        counts[table] = len(rows)
    return ExportResult(paths, counts)


def export_json(conn: sqlite3.Connection, path: Path) -> ExportResult:
    """Write one JSON file: {"exported_at": ..., "jobs": [...], "applications": [...], ...}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {"exported_at": now_iso()}
    counts = {}
    for table in QUERIES:
        columns, rows = table_rows(conn, table)
        document[table] = [dict(zip(columns, row)) for row in rows]
        counts[table] = len(rows)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    return ExportResult([path], counts)
