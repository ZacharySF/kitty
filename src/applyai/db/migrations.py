"""Numbered .sql migrations, applied in order and recorded in schema_version.

Adding a schema change means dropping `002_something.sql` next to `001_initial.sql`.
Files are never edited once shipped.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from applyai.dates import now_iso

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def available_migrations(directory: Path | None = None) -> list[Migration]:
    """All migration files on disk, ordered by version."""
    folder = directory or MIGRATIONS_DIR
    found: list[Migration] = []
    for path in sorted(folder.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if not match:
            raise ValueError(
                f"migration {path.name} must be named NNN_lower_snake.sql"
            )
        found.append(Migration(int(match.group(1)), match.group(2), path))
    return found


def latest_version(directory: Path | None = None) -> int:
    """Highest version available on disk (0 when there are none)."""
    migrations = available_migrations(directory)
    return migrations[-1].version if migrations else 0


def current_version(conn: sqlite3.Connection) -> int:
    """Highest version applied to this database (0 for a fresh file)."""
    conn.execute(_SCHEMA_VERSION_DDL)
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] or 0


def pending_migrations(
    conn: sqlite3.Connection, directory: Path | None = None
) -> list[Migration]:
    """Migrations on disk that this database has not applied yet.

    Compared by version number rather than "above the highest applied", so a
    lower-numbered file that lands after a higher one (two features built in
    parallel) still gets applied.
    """
    conn.execute(_SCHEMA_VERSION_DDL)
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    return [m for m in available_migrations(directory) if m.version not in applied]


def _wants_foreign_keys_off(migration: Migration) -> bool:
    """True when the script's first line is `-- foreign_keys: off` (a table rebuild)."""
    first = migration.sql.lstrip().split("\n", 1)[0]
    return first.strip().lower() == "-- foreign_keys: off"


def migrate(conn: sqlite3.Connection, directory: Path | None = None) -> list[Migration]:
    """Apply every pending migration; return the ones applied.

    Each migration runs as one script inside BEGIN ... COMMIT. BEGIN is written
    into the script rather than opened around it because `executescript`
    commits any transaction that is already open, which would leave a failing
    migration half-applied. Before COMMIT, `PRAGMA foreign_key_check` must come
    back empty.

    A script starting with `-- foreign_keys: off` runs with foreign keys off,
    which SQLite requires for rebuilding a table other tables point at (the
    pragma cannot change inside a transaction, so it is set around it).
    """
    applied: list[Migration] = []
    for migration in pending_migrations(conn, directory):
        # Interpolated rather than bound because executescript takes no parameters.
        # All three values are machine-generated: an int, a [a-z0-9_] name, a timestamp.
        record = (
            "INSERT INTO schema_version (version, name, applied_at) VALUES "
            f"({migration.version}, '{migration.name}', '{now_iso()}');"
        )
        keys_were_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        keys_off = _wants_foreign_keys_off(migration)
        if keys_off:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript(f"BEGIN;\n{migration.sql}\n{record}")
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise sqlite3.IntegrityError(
                    f"migration {migration.version} leaves {len(broken)} broken foreign key(s), "
                    f"first in table {broken[0][0]}"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.rollback()
            raise
        finally:
            if keys_off and keys_were_on:
                conn.execute("PRAGMA foreign_keys = ON")
        applied.append(migration)
    return applied
