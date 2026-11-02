"""The demo database: fake data so I can learn the TUI safely.

Everything here is invented and labelled "(demo)", so demo rows can never be
mistaken for real companies or real applications. Only ever writes to a file
named demo.db.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from kitty.config import Config
from kitty.core import applications as applications_core
from kitty.core import concept_maps
from kitty.core import jobs as jobs_core
from kitty.core import questions as questions_core
from kitty.core import reminders as reminders_core
from kitty.core import resumes as resumes_core
from kitty.core.workspace import sync_config_to_db
from kitty.dates import now, to_iso
from kitty.db import connect, migrate
from kitty.db.connection import transaction
from kitty.settings import Settings

DEMO_DB_NAME = "demo.db"

#: (label, description) for the fake resumes the demo registers.
_DEMO_RESUMES = (
    ("quant", "demo resume emphasising probability, C++ and a trading bot project"),
    ("systems", "demo resume emphasising CUDA, RTL coursework and a cache simulator"),
    ("embedded", "demo resume emphasising STM32 firmware and a robotics club project"),
    ("general", "demo general-purpose resume for part-time work"),
)

# (company, title, location, track, tier, eligibility, days_old, deadline_in_days)
_DEMO_JOBS: tuple[tuple[str, str, str, str, str | None, str, int, int | None], ...] = (
    ("Ridgeline Capital (demo)", "Quantitative Trading Intern", "New York, NY", "quant", "A", "boosted", 2, 21),
    ("Blue Harbor Trading (demo)", "Quant Research Intern", "Chicago, IL", "quant", "A", "eligible", 4, 14),
    ("Tidewater Markets (demo)", "Quant Developer Intern", "Austin, TX", "quant", "B", "eligible", 6, None),
    ("Copper Fox Capital (demo)", "Trading Intern - Summer 2027", "New York, NY", "quant", "B", "ambiguous", 9, 30),
    ("Sixth Street Signals (demo)", "Quantitative Analyst Intern", "Remote", "quant", "C", "ineligible", 12, None),
    ("Halcyon Prop (demo)", "Junior Trader Intern", "Houston, TX", "quant", "B", "eligible", 3, None),
    ("Lumen Silicon (demo)", "GPU Software Intern", "Santa Clara, CA", "gpu", "A", "eligible", 1, 28),
    ("Vector Forge (demo)", "CUDA Performance Intern", "Austin, TX", "gpu", "B", "eligible", 5, None),
    ("Prism Graphics (demo)", "Graphics Engineering Intern", "Seattle, WA", "gpu", "B", "ambiguous", 7, None),
    ("Nimbus ML Systems (demo)", "ML Systems Intern", "Remote", "gpu", "C", "ambiguous", 15, None),
    ("Quartz Microsystems (demo)", "RTL Design Intern", "Austin, TX", "cpu", "A", "boosted", 2, 20),
    ("Anvil Semiconductor (demo)", "Silicon Validation Intern", "Fort Collins, CO", "cpu", "B", "eligible", 8, None),
    ("Cascade Logic (demo)", "FPGA Engineering Intern", "Dallas, TX", "cpu", "B", "eligible", 10, None),
    ("Meridian Arch (demo)", "Performance Modeling Intern", "Remote", "cpu", "C", "ineligible", 18, None),
    ("Saltgrass Robotics (demo)", "Embedded Software Intern", "Houston, TX", "embedded", "A", "eligible", 3, 12),
    ("Beacon Avionics (demo)", "Firmware Intern", "Hewitt, TX", "embedded", "B", "eligible", 6, None),
    ("Tern Devices (demo)", "Edge ML Intern", "Remote", "embedded", "B", "ambiguous", 11, None),
    ("Lakeside Sensors (demo)", "Embedded Systems Co-op", "Woodway, TX", "embedded", "C", "ambiguous", 20, None),
    ("Main Street Coffee (demo)", "Barista - Part Time", "Waco, TX", "parttime", "B", "eligible", 1, None),
    ("Town Library (demo)", "Student Library Assistant", "Waco, TX", "parttime", "B", "eligible", 2, None),
    ("Bench Electronics (demo)", "Electronics Bench Tech (Part Time)", "Waco, TX", "parttime", "A", "boosted", 4, None),
    ("Corner Tutors (demo)", "Physics Tutor", "Waco, TX", "parttime", "C", "eligible", 9, None),
    ("Campus IT (demo)", "IT Help Desk Student Worker", "Waco, TX", "parttime", "B", "eligible", 5, None),
    ("Orion Quant Labs (demo)", "Quant Intern (First-Year Program)", "New York, NY", "quant", "A", "boosted", 1, 10),
    ("Basalt Compute (demo)", "Compiler Intern", "Remote", "gpu", "B", "ambiguous", 13, None),
    ("Ironwood Chips (demo)", "Verification Intern", "Phoenix, AZ", "cpu", "B", "eligible", 14, None),
    ("Marsh Hawk Systems (demo)", "Bare-Metal Firmware Intern", "Remote", "embedded", "B", "eligible", 16, None),
    ("Delta Bay Trading (demo)", "Quant Ops Intern", "Chicago, IL", "quant", "C", "ambiguous", 22, None),
    ("Seagrass Analytics (demo)", "Data Engineering Intern", "Houston, TX", "gpu", "C", "ambiguous", 25, None),
    ("Hilltop Hardware (demo)", "Sales Associate (Part Time)", "Waco, TX", "parttime", "C", "eligible", 8, None),
    ("Longleaf Systems (demo)", "Backend Engineer Intern", "Austin, TX", "software", "A", "boosted", 2, 18),
    ("Packet Ridge (demo)", "Infrastructure Intern", "Dallas, TX", "software", "B", "eligible", 6, None),
    ("Kestrel Latency (demo)", "Low-Latency Systems Software Intern", "Remote", "software", "C", "ambiguous", 17, None),
    ("Bayou Grid Services (demo)", "Power Systems Engineering Intern", "Houston, TX", "power", "A", "eligible", 3, 25),
    ("Caprock Controls (demo)", "Controls Engineering Intern", "Dallas, TX", "power", "B", "eligible", 7, None),
    ("Prairie Energy Co-op (demo)", "Field Test Engineering Intern", "Woodway, TX", "power", "C", "ambiguous", 19, None),
    ("Prairie University (demo)", "Research program - Undergraduate Summer Research", "Arlington, TX", "research", "B", "ambiguous", 2, 30),
)

# (job index, status, resume label, applied days ago, extra status days ago)
_DEMO_APPLICATIONS: tuple[tuple[int, str, str, int, int | None], ...] = (
    (0, "interview", "quant", 18, 4),
    (1, "oa", "quant", 12, 5),
    (2, "applied", "quant", 6, None),
    (3, "rejected", "quant", 24, 9),
    (6, "oa", "systems", 14, 6),
    (7, "applied", "systems", 5, None),
    (10, "interview", "systems", 21, 3),
    (11, "applied", "systems", 9, None),
    (14, "offer", "embedded", 30, 2),
    (15, "applied", "embedded", 7, None),
    (18, "applied", "general", 4, None),
    (19, "ghosted", "general", 40, 26),
    (22, "oa", "general", 11, 7),
    (24, "applied", "systems", 2, None),
)

_DEMO_QUESTIONS = (
    "Describe a project where you optimized code for performance.",
    "What is your expected hourly rate?",
    "Are you available to work 20 hours per week during the semester?",
)


def _write_demo_resumes(settings: Settings) -> Path:
    """Create placeholder resume files so hashes and doctor checks line up."""
    folder = settings.data_dir / "demo_resumes"
    folder.mkdir(parents=True, exist_ok=True)
    for label, description in _DEMO_RESUMES:
        (folder / f"{label}.txt").write_text(
            f"DEMO RESUME - {label}\n{description}\n"
            "This is placeholder text in the demo database. Not a real resume.\n",
            encoding="utf-8",
        )
    return folder


def seed(conn: sqlite3.Connection, settings: Settings, config: Config) -> dict[str, int]:
    """Fill an empty demo database with fake jobs, applications and history."""
    folder = _write_demo_resumes(settings)
    reference = now()
    job_ids: list[int] = []

    with transaction(conn):
        for label, description in _DEMO_RESUMES:
            resumes_core.add_resume(
                conn, label, folder / f"{label}.txt",
                description=description, default_tracks=[],
            )

        for company, title, location, track, tier, eligibility, days_old, deadline in _DEMO_JOBS:
            posted = to_iso(reference - timedelta(days=days_old))
            job, _ = jobs_core.upsert_job(
                conn,
                company=company,
                title=title,
                location=location,
                url=f"https://example.invalid/demo/{len(job_ids)}",
                source="manual",
                source_job_id=f"demo:{len(job_ids)}",
                season="Summer 2027" if track != "parttime" else "part-time",
                posted_at=posted,
                deadline_at=to_iso(reference + timedelta(days=deadline)) if deadline else None,
                jd_text=(
                    f"[demo job description] {title} at {company}. "
                    "Mentions C++, Python, data structures and a fast-paced team."
                ),
                tracks=[track],
                match_reason="demo seed",
                eligibility=eligibility,
                eligibility_reason={
                    "boosted": "matched boost phrase 'first-year'",
                    "ineligible": "matched 'must be a rising senior'",
                    "ambiguous": "no standing or graduation requirement found",
                    "eligible": "no disqualifying requirement found",
                }[eligibility],
            )
            job_ids.append(job.id)
            if tier:
                jobs_core.set_tier(
                    conn, job.id, tier,
                    reason={
                        "A": "demo: strong match with your coursework and a watchlist-style firm",
                        "B": "demo: good match, worth a fast application",
                        "C": "demo: weak match on requirements",
                    }[tier],
                    tier_source="llm" if track != "parttime" else "rules",
                )
                jobs_core.set_edit_notes(conn, job.id, [
                    "the JD asks for CUDA; your resume mentions it only under Coursework",
                    "the JD asks for Python testing; your projects section has no tests listed",
                ] if tier in {"A", "B"} else [])
            if tier in {"A", "B"}:
                jobs_core.set_queue_state(conn, job.id, "queued")

        for index, status, resume_label, applied_days, event_days in _DEMO_APPLICATIONS:
            resume = resumes_core.get_resume_by_label(conn, resume_label)
            track_row = conn.execute(
                "SELECT track FROM job_tracks WHERE job_id = ? LIMIT 1", (job_ids[index],)
            ).fetchone()
            application = applications_core.record_application(
                conn,
                job_ids[index],
                resume_id=resume.id if resume else None,
                track=track_row["track"] if track_row else None,
                applied_at=to_iso(reference - timedelta(days=applied_days)),
                notes=None,
            )
            if status != "applied" and event_days is not None:
                if status in {"interview", "offer"}:
                    applications_core.set_status(
                        conn, application.id, "oa",
                        occurred_at=to_iso(reference - timedelta(days=event_days + 5)),
                    )
                applications_core.set_status(
                    conn, application.id, status,
                    occurred_at=to_iso(reference - timedelta(days=event_days)),
                )
            if status in {"applied", "oa"}:
                applications_core.set_next_action(
                    conn, application.id,
                    due_at=to_iso(reference + timedelta(days=3)),
                    note="demo: follow up with the recruiter",
                )
                reminders_core.create_reminder(
                    conn, kind="followup",
                    due_at=to_iso(reference + timedelta(days=3)),
                    note="demo follow-up", job_id=job_ids[index],
                    application_id=application.id,
                )

        # One pending Gmail-style proposal, so the confirm flow is visible.
        first = applications_core.get_application_for_job(conn, job_ids[2])
        if first:
            applications_core.set_status(
                conn, first.id, "oa", source="gmail", state="proposed",
                evidence="demo email: 'Your online assessment is ready'",
            )

        for question in _DEMO_QUESTIONS:
            questions_core.log_question(conn, question, job_id=job_ids[0])

    return {
        "jobs": len(job_ids),
        "applications": len(_DEMO_APPLICATIONS),
        "resumes": len(_DEMO_RESUMES),
        "questions": len(_DEMO_QUESTIONS),
    }


def reset_demo(settings: Settings, config: Config) -> dict[str, int]:
    """Delete and rebuild the demo database.

    Refuses to touch anything that is not named demo.db, so a stray --demo flag
    can never wipe real applications.
    """
    if settings.db_path.name != DEMO_DB_NAME:
        raise ValueError(
            f"refusing to reset {settings.db_path}: the demo database must be named {DEMO_DB_NAME}"
        )

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(settings.db_path) + suffix)
        if candidate.exists():
            candidate.unlink()

    conn = connect(settings.db_path)
    try:
        migrate(conn)
        concept_maps.migrate(conn, settings.data_dir / "exports")
        sync_config_to_db(conn, config)
        return seed(conn, settings, config)
    finally:
        conn.close()
