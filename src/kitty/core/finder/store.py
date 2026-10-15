"""firm_candidates rows: storing proposals, listing them, approving and rejecting.

Approving a firm appends it to config/watchlist.yaml (as text, so my comments
and layout survive), checks the whole config still loads, and adds it to the
companies table as a watchlist company. Rejecting only records the decision;
the row stays, so the firm is never proposed again.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from kitty.config import Config, ConfigError, load_config
from kitty.core import companies as companies_core
from kitty.db.connection import transaction
from kitty.dates import now_iso
from kitty.normalize import norm_company
from kitty.settings import Settings

DECISIONS = ("pending", "approved", "rejected")

#: Which tracks a skills.yaml area feeds, by words in the area's name. Only names
#: that exist in config/tracks.yaml are used; an area matching none gets no tracks
#: (watchlist tracks are hints, jobs are routed by their own text).
AREA_TRACK_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("quant", "trading"), "quant"),
    (("gpu", "cuda"), "gpu"),
    (("arch", "rtl", "fpga", "asic", "silicon", "cpu", "chip"), "cpu"),
    (("embedded", "firmware", "edge", "hardware", "avionics", "audio", "dsp", "pcb", "robot"), "embedded"),
    (("linux", "systems", "software", "backend", "infra", "kernel"), "software"),
    (("power", "energy", "electrical", "grid"), "power"),
)


@dataclass(frozen=True)
class Candidate:
    """One firm_candidates row, with its evidence lines unpacked."""

    id: int
    norm_name: str
    display_name: str
    area: str | None
    score: float | None
    reason: str | None
    ats_kind: str | None
    ats_slug: str | None
    board_url: str | None
    website: str | None
    evidence: list[str]
    proposed_run_id: int | None
    decision: str
    decided_at: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Candidate":
        try:
            evidence = json.loads(row["evidence"] or "{}")
        except ValueError:                        # hand-edited row: keep the text
            evidence = {"lines": [row["evidence"]]}
        return cls(
            id=row["id"], norm_name=row["norm_name"], display_name=row["display_name"],
            area=row["area"], score=row["score"], reason=row["reason"],
            ats_kind=row["ats_kind"], ats_slug=row["ats_slug"], board_url=row["board_url"],
            website=evidence.get("website"), evidence=list(evidence.get("lines") or []),
            proposed_run_id=row["proposed_run_id"], decision=row["decision"],
            decided_at=row["decided_at"], created_at=row["created_at"],
        )


# ------------------------------------------------------------------ reading


def known_names(conn: sqlite3.Connection, config: Config) -> set[str]:
    """Normalized names never to propose: the watchlist file, watchlist companies, every past proposal."""
    names = {norm_company(c.name) for c in config.watchlist.companies}
    names.update(row[0] for row in conn.execute("SELECT norm_name FROM companies WHERE from_watchlist = 1"))
    names.update(row[0] for row in conn.execute("SELECT norm_name FROM firm_candidates"))
    return names


def list_candidates(conn: sqlite3.Connection, decision: str = "pending") -> list[Candidate]:
    """Candidates with this decision, best score first."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    rows = conn.execute(
        "SELECT * FROM firm_candidates WHERE decision = ? "
        "ORDER BY score DESC, created_at DESC, display_name COLLATE NOCASE",
        (decision,),
    )
    return [Candidate.from_row(row) for row in rows]


def get_candidate(conn: sqlite3.Connection, candidate_id: int) -> Candidate | None:
    row = conn.execute("SELECT * FROM firm_candidates WHERE id = ?", (candidate_id,)).fetchone()
    return Candidate.from_row(row) if row else None


def decision_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """How many candidates are pending, approved and rejected (zeros included)."""
    counts = dict.fromkeys(DECISIONS, 0)
    for row in conn.execute("SELECT decision, COUNT(*) FROM firm_candidates GROUP BY decision"):
        counts[row[0]] = row[1]
    return counts


def last_run(conn: sqlite3.Connection) -> tuple[str, dict] | None:
    """(started_at, stats) of the newest finished finder run, or None if it never ran."""
    row = conn.execute(
        "SELECT started_at, stats_json FROM runs WHERE kind = 'finder' AND finished_at IS NOT NULL "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return row["started_at"], json.loads(row["stats_json"] or "{}")


# ------------------------------------------------------------------ writing


def insert_candidate(
    conn: sqlite3.Connection,
    *,
    name: str,
    area: str,
    score: float,
    reason: str,
    ats_kind: str | None,
    ats_slug: str | None,
    board_url: str | None,
    website: str | None,
    evidence: list[str],
    run_id: int,
) -> int:
    """Store one proposal as pending; return its id."""
    cursor = conn.execute(
        """
        INSERT INTO firm_candidates (norm_name, display_name, area, score, reason, ats_kind,
                                     ats_slug, board_url, evidence, proposed_run_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (norm_company(name), name, area, score, reason, ats_kind, ats_slug, board_url,
         json.dumps({"website": website, "lines": evidence}), run_id, now_iso()),
    )
    return int(cursor.lastrowid)


def _pending(conn: sqlite3.Connection, candidate_id: int) -> Candidate:
    candidate = get_candidate(conn, candidate_id)
    if candidate is None:
        raise ValueError(f"no firm candidate with id {candidate_id}")
    if candidate.decision != "pending":
        raise ValueError(f"{candidate.display_name} was already {candidate.decision}")
    return candidate


def _decide(conn: sqlite3.Connection, candidate_id: int, decision: str) -> None:
    conn.execute("UPDATE firm_candidates SET decision = ?, decided_at = ? WHERE id = ?",
                 (decision, now_iso(), candidate_id))


def reject(conn: sqlite3.Connection, candidate_id: int) -> None:
    """Mark a pending candidate rejected. Its row stays, so it is never proposed again."""
    _pending(conn, candidate_id)
    _decide(conn, candidate_id, "rejected")


def tracks_for_area(area: str | None, track_names: list[str]) -> list[str]:
    """Tracks a firm from `area` most likely feeds, in tracks.yaml order (may be empty)."""
    name = (area or "").lower()
    wanted = {track for words, track in AREA_TRACK_WORDS if any(word in name for word in words)}
    if name in track_names:
        wanted.add(name)
    return [track for track in track_names if track in wanted]


def _yaml_str(value: str) -> str:
    """A YAML double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(value, ensure_ascii=False)


def watchlist_entry(candidate: Candidate, tracks: list[str], day: str) -> str:
    """The watchlist.yaml block for an approved candidate, in the file's own layout."""
    notes = f"{candidate.reason or 'proposed by finder'} (added by finder {day})"
    lines = [f"  - name: {_yaml_str(candidate.display_name)}"]
    if candidate.ats_kind and candidate.ats_slug:
        lines += [f"    ats: {candidate.ats_kind}", f"    slug: {_yaml_str(candidate.ats_slug)}"]
    else:
        lines.append("    ats: none")
        notes += "; no supported ATS: no public board found on Greenhouse, Lever, Ashby or SmartRecruiters"
    lines += [
        f"    tracks: [{', '.join(tracks)}]",
        "    school_selective: false",
        f"    notes: {_yaml_str(notes)}",
    ]
    return "\n".join(lines) + "\n"


def append_to_watchlist(config_dir: Path, entry: str) -> None:
    """Append `entry` under `companies:` in watchlist.yaml, then reload the config to prove it is valid.

    The file is edited as text so comments survive. If the result does not
    load, the file is put back exactly as it was and ConfigError is raised.
    """
    path = config_dir / "watchlist.yaml"
    before = path.read_text(encoding="utf-8") if path.exists() else None
    text = before if before is not None else "companies:\n"
    # Convert only the empty flow sequence; keep surrounding comments intact.
    node = yaml.compose(text)
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if (key.value == "companies" and isinstance(value, yaml.SequenceNode)
                    and value.flow_style and not value.value):
                text = text[:value.start_mark.index] + text[value.end_mark.index:]
                break
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(f"{text}\n{entry}", encoding="utf-8")
    try:
        load_config(config_dir)
    except ConfigError:
        if before is None:
            path.unlink()
        else:
            path.write_text(before, encoding="utf-8")
        raise


def approve(conn: sqlite3.Connection, settings: Settings, config: Config, candidate_id: int) -> None:
    """Add a pending candidate to config/watchlist.yaml and the companies table; mark it approved.

    A firm already in the watchlist file is not appended twice. Raises
    ValueError for an unknown or already-decided candidate, ConfigError when the
    appended watchlist would not load (the file is left unchanged).
    """
    candidate = _pending(conn, candidate_id)
    in_file = {norm_company(c.name) for c in load_config(settings.config_dir).watchlist.companies}
    if candidate.norm_name not in in_file:
        tracks = tracks_for_area(candidate.area, config.track_names)
        append_to_watchlist(settings.config_dir, watchlist_entry(candidate, tracks, now_iso()[:10]))
    entry = next(c for c in load_config(settings.config_dir).watchlist.companies
                 if norm_company(c.name) == candidate.norm_name)
    with transaction(conn):
        companies_core.upsert_company(
            conn, entry.name,
            ats_kind="other" if entry.ats == "none" else entry.ats,
            ats_slug=entry.slug or None,
            website=candidate.website,
            notes=entry.notes or None,
            school_selective=entry.school_selective,
            from_watchlist=True,
            priority=entry.priority,
        )
        _decide(conn, candidate_id, "approved")
