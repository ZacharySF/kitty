"""The tracks table: a mirror of config/tracks.yaml."""

from __future__ import annotations

import json
import sqlite3

from kitty.config import TracksFile
from kitty.dates import now_iso


def sync_tracks(conn: sqlite3.Connection, tracks: TracksFile) -> int:
    """Write every track from tracks.yaml into the tracks table; return the count.

    Tracks dropped from the yaml are marked inactive rather than deleted, because
    old jobs and applications still point at them.
    """
    timestamp = now_iso()
    names: list[str] = []
    for order, track in enumerate(tracks.tracks):
        names.append(track.name)
        conn.execute(
            """
            INSERT INTO tracks (name, weekly_target, triage_depth, default_resume,
                                active, sort_order, config_json, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                weekly_target = excluded.weekly_target,
                triage_depth  = excluded.triage_depth,
                default_resume= excluded.default_resume,
                active        = 1,
                sort_order    = excluded.sort_order,
                config_json   = excluded.config_json,
                updated_at    = excluded.updated_at
            """,
            (
                track.name, track.weekly_target, track.triage_depth, track.default_resume,
                order, track.model_dump_json(), timestamp,
            ),
        )

    if names:
        placeholders = ",".join("?" for _ in names)
        conn.execute(
            f"UPDATE tracks SET active = 0, updated_at = ? WHERE name NOT IN ({placeholders})",
            (timestamp, *names),
        )
    return len(names)


def list_track_names(conn: sqlite3.Connection, *, active_only: bool = True) -> list[str]:
    """Track names in their tracks.yaml order."""
    sql = "SELECT name FROM tracks"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY sort_order, name"
    return [row["name"] for row in conn.execute(sql)]


def weekly_targets(conn: sqlite3.Connection) -> dict[str, int]:
    """Weekly application target per active track."""
    return {
        row["name"]: row["weekly_target"]
        for row in conn.execute(
            "SELECT name, weekly_target FROM tracks WHERE active = 1 ORDER BY sort_order"
        )
    }


def track_config(conn: sqlite3.Connection, name: str) -> dict | None:
    """The stored yaml block for a track, as a dict (provenance for later stages)."""
    row = conn.execute("SELECT config_json FROM tracks WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    return json.loads(row["config_json"])
