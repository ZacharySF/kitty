"""Simulated rika! devices for merge and sync tests: one migrated SQLite database each.

`sync(src, dst)` sends what `dst` lacks of every page `src` holds, the way the sync
protocol does (version vector per page, Loro updates for the difference), and merges
it on `dst`. `view` is the projection a device shows, without the properties that
belong to one device's copy (page number, revision, notebook row, view state).
"""
from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from pathlib import Path

from kitty.core import blocks
from kitty.core.blocks import merge
from kitty.core.blocks.crdt import LOCAL_KEYS
from kitty.db.connection import connect
from kitty.db.migrations import migrate

def make_template(path: Path) -> Path:
    """A migrated database that devices start from (made once per test session)."""
    conn = connect(path)
    migrate(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return path


class Device:
    def __init__(self, folder: Path, name: str, template: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{name}.db"
        shutil.copyfile(template, self.path)
        self.conn = connect(self.path)
        self.id = secrets.token_hex(16)
        self.conn.execute("UPDATE block_history SET device_id=?", (self.id,))
        self.name = name

    def transact(self, ops: list[dict]) -> dict | None:
        """Apply `ops`; None when the server refuses them (as it would refuse a window)."""
        try:
            return blocks.transact(self.conn, ops)
        except blocks.BlockError:
            return None

    def pages(self) -> list[str]:
        return [row[0] for row in self.conn.execute(
            "SELECT id FROM blocks WHERE parent_id IS NULL UNION SELECT page_id FROM page_crdt ORDER BY 1")]

    def close(self) -> None:
        self.conn.close()


def sync(src: Device, dst: Device) -> dict:
    """Send `dst` every Loro change of every page `src` holds that `dst` lacks, and merge it."""
    pages = src.pages()
    merge.seed(src.conn, pages)
    updates = {}
    for page_id in pages:
        data = merge.export(src.conn, page_id, merge.vv(dst.conn, page_id))
        if data:
            updates[page_id] = [data]
    return merge.merge(dst.conn, updates, origin=src.id)


def sync_all(devices: list[Device], rounds: int = 6) -> int:
    """Sync every pair both ways until a whole round changes nothing; returns the rounds that changed something."""
    for round_number in range(rounds):
        changed = False
        for src in devices:
            for dst in devices:
                if src is not dst:
                    changed = bool(sync(src, dst)["pages"]) or changed
        if not changed:
            return round_number
    raise AssertionError(f"devices still exchanging changes after {rounds} rounds")


def view(conn: sqlite3.Connection) -> dict:
    """Every block as a device shows it, minus the properties of this device's copy."""
    result = {}
    for row in conn.execute("SELECT * FROM blocks ORDER BY id"):
        props = {key: value for key, value in json.loads(row["properties"]).items() if key not in LOCAL_KEYS}
        result[row["id"]] = {"type": row["type"], "parent": row["parent_id"], "page": row["page_id"], "key": row["order_key"],
                             "props": props, "text": row["text"], "created": row["created_at"], "updated": row["updated_at"],
                             "deleted": row["deleted_at"]}
    return result


def derived(conn: sqlite3.Connection) -> dict:
    """The tables rebuilt from blocks: refs, topics, tags and rejections."""
    return {
        "refs": sorted(map(tuple, conn.execute("SELECT source_id,target_id,kind FROM block_refs"))),
        "topics": sorted(map(tuple, conn.execute("SELECT block_id,kind,topic FROM block_topics"))),
        "tags": sorted(map(tuple, conn.execute("SELECT block_id,tag_id,source FROM block_tags"))),
        "rejections": sorted(map(tuple, conn.execute("SELECT block_id,tag_id FROM tag_rejections"))),
    }


def assert_converged(devices: list[Device]) -> None:
    first = devices[0]
    expected, expected_derived = view(first.conn), derived(first.conn)
    for other in devices[1:]:
        got = view(other.conn)
        if got != expected:
            missing = expected.keys() - got.keys()
            extra = got.keys() - expected.keys()
            differing = [bid for bid in expected.keys() & got.keys() if expected[bid] != got[bid]]
            detail = {bid: (expected[bid], got[bid]) for bid in differing[:3]}
            raise AssertionError(f"{other.name} differs from {first.name}: missing {sorted(missing)[:5]}, "
                                 f"extra {sorted(extra)[:5]}, differing {len(differing)}: {detail}")
        assert derived(other.conn) == expected_derived, f"{other.name}: refs, topics or tags differ"
