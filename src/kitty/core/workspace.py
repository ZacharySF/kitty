"""One place that opens the database and the config together.

The CLI and the TUI both call `open_workspace`, so they cannot drift apart on
migrations or on syncing tracks.yaml / sources.yaml / watchlist.yaml.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kitty.config import Config, load_config
from kitty.core import companies as companies_core
from kitty.core import sources as sources_core
from kitty.core import tracks as tracks_core
from kitty.db import connect, migrate, pending_migrations
from kitty.db.connection import transaction
from kitty.settings import Settings


@dataclass
class Workspace:
    """An open database plus the parsed config for one process."""

    settings: Settings
    config: Config
    conn: sqlite3.Connection

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def sync_config_to_db(conn: sqlite3.Connection, config: Config) -> dict[str, int]:
    """Mirror the yaml config into the tables that reference it.

    Runs on every command: editing tracks.yaml and starting the TUI is enough to
    see the change.
    """
    with transaction(conn):
        counts = {
            "tracks": tracks_core.sync_tracks(conn, config.tracks),
            "sources": sources_core.sync_sources(conn, config.sources),
            "watchlist": companies_core.sync_watchlist(conn, config.watchlist),
        }
    return counts


def open_workspace(
    settings: Settings | None = None,
    *,
    demo: bool = False,
    root: Path | None = None,
    auto_migrate: bool = True,
    sync: bool = True,
) -> Workspace:
    """Open the database and config, migrating and syncing by default.

    Raises ConfigError when a config file is missing or invalid, which the CLI
    turns into line-pointing messages.
    """
    resolved = settings or Settings.load(root=root, demo=demo)
    config = load_config(resolved.config_dir)
    conn = connect(resolved.db_path)

    if auto_migrate:
        migrate(conn)
    if sync and not pending_migrations(conn):
        sync_config_to_db(conn, config)
    return Workspace(settings=resolved, config=config, conn=conn)
