"""Opening the database.

One place sets the pragmas, so every caller gets foreign keys enforced and
dict-like rows.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def connect(db_path: Path | str, *, create_parents: bool = True) -> sqlite3.Connection:
    """Open `db_path` with the pragmas this project relies on.

    WAL keeps the TUI readable while a CLI run writes; foreign_keys is ON because
    the schema leans on cascades; busy_timeout avoids "database is locked" when
    `discover` and the TUI overlap.
    """
    path = Path(db_path)
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, isolation_level=None)   # autocommit; see transaction()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one transaction, rolling back on any exception.

    The connection is in autocommit mode, so transactions are explicit and
    multi-table writes (an application plus its status_event) cannot half-apply.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
