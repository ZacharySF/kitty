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
