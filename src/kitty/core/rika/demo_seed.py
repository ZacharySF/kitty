"""Synthetic Rika data for `kitty-web --demo`: goals, a fake mailbox and routing memory.

Each feature seeds its own rows; nothing here touches the network.
"""
from __future__ import annotations

import sqlite3


def seed(conn: sqlite3.Connection) -> None:
    """Fill an empty demo database with Rika's synthetic goals, mail and routes."""
    from kitty.core import goals

    goals.demo_seed(conn)
    from kitty.core.rika import mail

    mail.seed_demo(conn)
    from kitty.core.rika import routes

    routes.seed_demo(conn)
    _seed_checkin(conn)


def _seed_checkin(conn: sqlite3.Connection) -> None:
    """A task due yesterday that Rika owes a check-in for, so the demo asks "Did you finish it?"."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from kitty.core import calendar
    from kitty.core.rika import followups

    if conn.execute("SELECT 1 FROM rika_followups LIMIT 1").fetchone():
        return
    zone = conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"]
    yesterday = datetime.now(ZoneInfo(zone)).date() - timedelta(days=1)
    task = calendar.save_task(conn, {"title": "Email the lab report to Dr. Chen", "due": yesterday.isoformat(),
                                     "timezone": zone})
    followups.schedule(conn, "task", task["id"])
