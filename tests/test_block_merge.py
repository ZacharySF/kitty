"""Q5: Loro page documents and deterministic merges between devices.

Each simulated device is its own migrated database; `sync` sends the Loro changes the
other lacks (per page, by version vector) and merges them as one transaction. The
core claim is tested two ways: named scenarios of concurrent edits, and randomized
runs on two and three devices (every kind of edit, random partial syncs) that must end
with identical projections, refs, topics and tags on every device, with nothing lost
that no device deleted, and with every page's log replaying to its projection.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import uuid
from pathlib import Path

import pytest

from kitty.core import blocks
from kitty.core.blocks import crdt, history, merge, tagging
from kitty.db.connection import connect
from kitty.db.migrations import available_migrations, migrate

from sync_support import Device, assert_converged, derived, make_template, sync, sync_all, view


@pytest.fixture(scope="session")
def template(tmp_path_factory) -> Path:
    return make_template(tmp_path_factory.mktemp("template") / "template.db")


@pytest.fixture
def devices(tmp_path, template):
    made: list[Device] = []

    def make(count: int) -> list[Device]:
        for index in range(count):
            made.append(Device(tmp_path, "abcdefgh"[len(made)], template))
        return made[-count:]

    yield make
    for device in made:
        device.close()


def new_id() -> str:
    return str(uuid.uuid4())


def page_ops(page: str, *children: tuple[str, str], title: str = "Page") -> list[dict]:
    ops = [{"op": "insert", "id": page, "type": "page", "parent": None, "text": title, "props": {"revision": 1}}]
    ops.extend({"op": "insert", "id": child, "type": "text", "parent": page, "text": text} for child, text in children)
    return ops


def row(conn, bid: str) -> dict:
    return view(conn)[bid]


# The Loro mirror ---------------------------------------------------------------------

def loro_view(conn, page_id: str) -> dict:
    doc = crdt.load(conn, page_id, 1)
    projected = crdt.project(doc, page_id)
    return {bid: (item.type, item.parent_id, item.order_key, item.properties, item.text, item.created_at, item.updated_at,
                  item.deleted_at) for bid, item in projected.blocks.items()}


def sql_view(conn, page_id: str) -> dict:
    return {bid: (item["type"], item["parent"], item["key"], item["props"], item["text"], item["created"], item["updated"],
                  item["deleted"]) for bid, item in view(conn).items() if item["page"] == page_id}


def test_every_transaction_is_mirrored_into_the_page_document(devices):
    (a,) = devices(1)
    page, first, second = new_id(), new_id(), new_id()
    a.transact(page_ops(page, (first, "héllo 😀 world"), (second, "two")))
    a.transact([{"op": "text", "id": first, "at": 6, "del": 1, "ins": "🐈"}])
    a.transact([{"op": "set", "id": second, "props": {"checked": True, "n": 1.0}, "type": "to_do"}])
    a.transact([{"op": "move", "id": second, "parent": first}])
    a.transact([{"op": "delete", "id": first}])
    a.transact([{"op": "restore", "id": first}])
    third = new_id()
    a.transact([{"op": "insert", "id": third, "type": "text", "parent": page, "text": "gone soon"}])
    a.transact([{"op": "purge", "id": third}])
    assert loro_view(a.conn, page) == sql_view(a.conn, page)
    assert row(a.conn, first)["text"] == "héllo 🐈 world"
    rows = a.conn.execute("SELECT crdt FROM op_log WHERE page_id=? ORDER BY seq", (page,)).fetchall()
    assert len(rows) == 8 and all(item["crdt"] for item in rows)
    assert a.conn.execute("SELECT COUNT(*) FROM blocks WHERE page_id=? AND node IS NULL", (page,)).fetchone()[0] == 0


def test_view_state_and_page_numbers_never_reach_the_document(devices):
    (a,) = devices(1)
    page = new_id()
    a.transact(page_ops(page))
    before = a.conn.execute("SELECT vv FROM page_crdt WHERE page_id=?", (page,)).fetchone()[0]
    a.transact([{"op": "set", "id": page, "props": {"viewport": {"x": 1, "y": 2, "zoom": 1}, "last_opened": "2026-11-01"}}])
    assert a.conn.execute("SELECT vv FROM page_crdt WHERE page_id=?", (page,)).fetchone()[0] == before
    assert a.conn.execute("SELECT crdt FROM op_log ORDER BY seq DESC LIMIT 1").fetchone()[0] is None


def test_snapshot_is_rewritten_after_a_run_of_updates_and_loads_the_same(devices, monkeypatch):
    monkeypatch.setattr(crdt, "SNAPSHOT_EVERY", 4)
    (a,) = devices(1)
    page, block = new_id(), new_id()
    a.transact(page_ops(page, (block, "")))
    for index in range(10):
        a.transact([{"op": "text", "id": block, "at": index, "del": 0, "ins": str(index)}])
    state = a.conn.execute("SELECT snapshot_seq, tail FROM page_crdt WHERE page_id=?", (page,)).fetchone()
    assert state["tail"] < 4 and state["snapshot_seq"] > 1
    assert loro_view(a.conn, page)[block][4] == "0123456789"


def test_a_page_written_before_loro_is_seeded_on_first_touch_and_old_rows_still_replay(tmp_path):
    early = tmp_path / "early"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < next(m.version for m in available_migrations() if m.name == "peer_sync"):
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "kitty.db")
    migrate(conn, early)
    page, a, b = new_id(), new_id(), new_id()
    blocks.transact(conn, page_ops(page, (a, "old one"), (b, "old two")))
    blocks.transact(conn, [{"op": "delete", "id": b}])
    migrate(conn)
    assert [row[0] for row in conn.execute("SELECT crdt FROM op_log")] == [None, None]
    blocks.transact(conn, [{"op": "text", "id": a, "at": 3, "del": 0, "ins": " new"}])
    assert conn.execute("SELECT COUNT(*) FROM page_crdt").fetchone()[0] == 1
    assert loro_view(conn, page) == sql_view(conn, page)
    replayed = blocks.replay(conn, page)
    assert {bid: item.text for bid, item in replayed.blocks.items()} == {page: "Page", a: "old new one", b: "old two"}
    conn.close()


# Named concurrent scenarios -----------------------------------------------------------

def two_devices(devices, *children: tuple[str, str]):
    a, b = devices(2)
    page = new_id()
    a.transact(page_ops(page, *children))
    sync(a, b)
    return a, b, page


def test_a_new_page_arrives_with_a_local_number_and_notebook(devices):
    a, b = devices(2)
    work = a.conn.execute("INSERT INTO note_notebooks(title,subject,project,created_at,updated_at) VALUES ('Work','','','t','t')").lastrowid
    b.conn.execute("INSERT INTO note_notebooks(title,subject,project,created_at,updated_at) VALUES ('Home','','','t','t')")
    b_page = new_id()
    b.transact([{"op": "insert", "id": b_page, "type": "page", "parent": None, "text": "B's", "props": {"revision": 1, "number": 1, "notebook_id": 1}}])
    page = new_id()
    a.transact([{"op": "insert", "id": page, "type": "page", "parent": None, "text": "Plans",
                 "props": {"revision": 1, "number": 1, "notebook_id": work, "viewport": {"x": 5}}}])
    sync(a, b)
    props = json.loads(b.conn.execute("SELECT properties FROM blocks WHERE id=?", (page,)).fetchone()[0])
    notebook = b.conn.execute("SELECT title FROM note_notebooks WHERE id=?", (props["notebook_id"],)).fetchone()[0]
    assert props["number"] == 2 and props["revision"] == 1 and notebook == "Work" and "viewport" not in props
    assert b.conn.execute("SELECT title FROM note_page_index WHERE id=2").fetchone()[0] == "Plans"
    sync(b, a)
    assert_converged([a, b])
    assert json.loads(a.conn.execute("SELECT properties FROM blocks WHERE id=?", (page,)).fetchone()[0])["viewport"] == {"x": 5}


def test_concurrent_typing_in_one_block_keeps_both(devices):
    block = new_id()
    a, b, _page = two_devices(devices, (block, "The cat sat."))
    a.transact([{"op": "text", "id": block, "at": 4, "del": 0, "ins": "black "}])
    b.transact([{"op": "text", "id": block, "at": 11, "del": 0, "ins": " on the mat"}])
    b.transact([{"op": "text", "id": block, "at": 0, "del": 4, "ins": ""}])
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    assert row(a.conn, block)["text"] == "black cat sat on the mat."


def test_moves_into_each_other_never_make_a_cycle(devices):
    x, y = new_id(), new_id()
    a, b, page = two_devices(devices, (x, "x"), (y, "y"))
    assert a.transact([{"op": "move", "id": x, "parent": y}])
    assert b.transact([{"op": "move", "id": y, "parent": x}])
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    parents = {row(a.conn, x)["parent"], row(a.conn, y)["parent"]}
    assert page in parents and parents != {page}, "exactly one of the moves wins"
    for device in (a, b):
        replayed = blocks.replay(device.conn, page)
        assert {bid: item.parent_id for bid, item in replayed.blocks.items()} == {bid: item["parent"] for bid, item in view(device.conn).items()}


def test_edit_under_a_deleted_block_goes_to_trash_with_it_and_comes_back(devices):
    parent, child = new_id(), new_id()
    a, b, _page = two_devices(devices, (parent, "parent"))
    b.transact([{"op": "insert", "id": child, "type": "text", "parent": parent, "text": "written on B"}])
    a.transact([{"op": "delete", "id": parent}])
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    deleted = row(a.conn, parent)["deleted"]
    assert deleted and row(a.conn, child)["deleted"] == deleted and row(a.conn, child)["text"] == "written on B"
    assert a.transact([{"op": "restore", "id": parent}])
    sync(a, b)
    assert row(b.conn, child)["deleted"] is None and row(b.conn, parent)["deleted"] is None
    assert_converged([a, b])


def test_delete_versus_text_edit_keeps_the_edit_in_trash(devices):
    block = new_id()
    a, b, _page = two_devices(devices, (block, "draft"))
    a.transact([{"op": "delete", "id": block}])
    b.transact([{"op": "text", "id": block, "at": 5, "del": 0, "ins": " v2"}])
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    assert row(a.conn, block)["deleted"] and row(a.conn, block)["text"] == "draft v2"


def test_tag_attach_versus_rejection_settles_on_one_state_everywhere(devices):
    a, b = devices(2)
    tag = new_id()
    a.transact([{"op": "insert", "id": tag, "type": "tag", "parent": None, "text": "graphs", "props": {"revision": 1, "keywords": [], "aliases": []}}])
    block, page = new_id(), new_id()
    a.transact(page_ops(page, (block, "trees and graphs")))
    sync(a, b)
    key = tagging.key_of(tag)
    a.transact([{"op": "set", "id": block, "props": {key: "accepted"}}])
    b.transact([{"op": "set", "id": block, "props": {key: "rejected"}}])
    revision = json.loads(b.conn.execute("SELECT properties FROM blocks WHERE id=?", (page,)).fetchone()[0])["revision"]
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    state = row(a.conn, block)["props"][key]
    tables = derived(a.conn)
    assert (state == "accepted") == ((block, tag, "accepted") in tables["tags"])
    assert (state == "rejected") == ((block, tag) in tables["rejections"])
    after = json.loads(b.conn.execute("SELECT properties FROM blocks WHERE id=?", (page,)).fetchone()[0])["revision"]
    assert after == revision, "a merge that only moves tag states is not an edit"


def test_a_block_tagged_before_its_tag_arrives_is_attached_when_it_does(devices):
    a, b = devices(2)
    tag, page, block = new_id(), new_id(), new_id()
    a.transact([{"op": "insert", "id": tag, "type": "tag", "parent": None, "text": "later", "props": {"revision": 1}}])
    a.transact(page_ops(page, (block, "text")))
    a.transact([{"op": "set", "id": block, "props": {tagging.key_of(tag): "manual"}}])
    data = merge.export(a.conn, page, None)
    merge.merge(b.conn, {page: [data]}, origin=a.id)
    assert derived(b.conn)["tags"] == []
    sync(a, b)
    assert (block, tag, "manual") in derived(b.conn)["tags"]
    assert_converged([a, b])


def test_inserts_at_the_same_spot_get_distinct_keys(devices):
    first = new_id()
    a, b, page = two_devices(devices, (first, "first"))
    one, two = new_id(), new_id()
    a.transact([{"op": "insert", "id": one, "type": "text", "parent": page, "after": first, "text": "from a"}])
    b.transact([{"op": "insert", "id": two, "type": "text", "parent": page, "after": first, "text": "from b"}])
    assert row(a.conn, one)["key"] == row(b.conn, two)["key"]
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    keys = [row(a.conn, bid)["key"] for bid in (first, one, two)]
    assert len(set(keys)) == 3
    between = new_id()
    assert a.transact([{"op": "insert", "id": between, "type": "text", "parent": page, "after": min((one, two), key=lambda bid: row(a.conn, bid)["key"]), "text": "x"}])


def test_a_block_moved_to_two_pages_ends_up_on_both_with_its_own_id_on_one(devices):
    block, other_a, other_b = new_id(), new_id(), new_id()
    a, b, page = two_devices(devices, (block, "wanderer"))
    a.transact(page_ops(other_a, title="A's page"))
    b.transact(page_ops(other_b, title="B's page"))
    a.transact([{"op": "move", "id": block, "parent": other_a}])
    b.transact([{"op": "move", "id": block, "parent": other_b}])
    sync(a, b)
    sync(b, a)
    sync(a, b)
    assert_converged([a, b])
    copies = [item for item in view(a.conn).values() if item["text"] == "wanderer"]
    assert sorted(item["page"] for item in copies) == sorted((other_a, other_b))
    keeper = min(other_a, other_b)
    assert row(a.conn, block)["page"] == keeper
    assert crdt.copy_id(block, max(other_a, other_b)) in view(a.conn)


def test_a_purge_on_one_device_purges_everywhere(devices):
    block = new_id()
    a, b, page = two_devices(devices, (block, "old"))
    a.transact([{"op": "delete", "id": block}])
    sync(a, b)
    a.transact([{"op": "purge", "id": block}])
    sync(a, b)
    assert block not in view(b.conn)
    assert_converged([a, b])


def test_a_merge_is_an_ordinary_transaction_in_history(devices):
    block = new_id()
    a, b, page = two_devices(devices, (block, "one"))
    a.transact([{"op": "text", "id": block, "at": 3, "del": 0, "ins": " two"}])
    result = sync(a, b)
    seq = result["seq"][page]
    logged = b.conn.execute("SELECT device_id, ops FROM op_log WHERE seq=?", (seq,)).fetchone()
    assert logged["device_id"] == a.id
    assert [(op["op"], op["id"]) for op in json.loads(logged["ops"])["ops"]] == [("put", page), ("text", block), ("put", block)]
    preview = history.preview(b.conn, page, seq)
    assert {item["id"]: item["text"] for item in preview["blocks"]}[block] == "one two"
    sessions = history.page_history(b.conn, page)["sessions"]
    assert sessions[0]["device_id"] == a.id


# Randomized convergence -----------------------------------------------------------------

TOKEN = re.compile(r"\[[^\]]*\]")
TYPES = ("text", "to_do", "heading_2", "quote")


class Scenario:
    """Random offline edits on several devices, tracking what must survive."""

    def __init__(self, rng: random.Random, devices: list[Device], pages: int = 2, cross_page: bool = False):
        self.rng, self.devices, self.cross_page = rng, devices, cross_page
        self.counter = 0
        self.created: set[str] = set()
        self.tokens: dict[str, set[str]] = {}
        self.erased: dict[str, set[str]] = {}
        self.tags = [new_id(), new_id()]
        origin = devices[0]
        for index, tag in enumerate(self.tags):
            # The first tag attaches itself wherever "kitty" is written (auto-tags on merges too).
            extra = {"keywords": ["kitty"], "mode": "always"} if index == 0 else {"keywords": []}
            origin.transact([{"op": "insert", "id": tag, "type": "tag", "parent": None, "text": f"topic{index}",
                              "props": {"revision": 1, "aliases": [], **extra}}])
        self.pages = []
        for _ in range(pages):
            page = new_id()
            children = [(new_id(), self.token(origin)) for _ in range(3)]
            origin.transact(page_ops(page, *children))
            self.pages.append(page)
            for bid, text in children:
                self.created.add(bid)
                self.tokens[bid] = {text}
        sync_all(devices)

    def token(self, device: Device) -> str:
        self.counter += 1
        return f"[{device.name}{self.counter}{' kitty' if self.rng.random() < 0.3 else ''}]"

    def step(self, device: Device) -> None:
        rng = self.rng
        page = rng.choice(self.pages)
        state = view(device.conn)
        here = {bid: item for bid, item in state.items() if item["page"] == page and item["parent"] is not None}
        live = [bid for bid, item in here.items() if item["deleted"] is None]
        trashed = [bid for bid, item in here.items() if item["deleted"] is not None]
        kind = rng.choices(["insert", "type", "erase", "set", "move", "delete", "restore", "tag", "turn", "cross"],
                           [5, 6, 3, 2, 3, 1, 1, 2, 1, 1 if self.cross_page else 0])[0]
        target = rng.choice(live) if live else None
        if kind == "insert" or target is None:
            parent = rng.choice([page, *live])
            siblings = [bid for bid in live if here[bid]["parent"] == parent]
            bid, text = new_id(), self.token(device)
            op = {"op": "insert", "id": bid, "type": "text", "parent": parent, "text": text}
            if siblings and rng.random() < 0.7:
                op["after"] = rng.choice([None, *siblings])
            if device.transact([op]):
                self.created.add(bid)
                self.tokens[bid] = {text}
        elif kind == "type":
            text = here[target]["text"]
            bounds = [0] + [match.end() for match in TOKEN.finditer(text)]
            token = self.token(device)
            at = rng.choice(bounds)
            if device.transact([{"op": "text", "id": target, "at": at, "del": 0, "ins": token}]):
                self.tokens.setdefault(target, set()).add(token)
        elif kind == "erase":
            found = list(TOKEN.finditer(here[target]["text"]))
            if found:
                match = rng.choice(found)
                if device.transact([{"op": "text", "id": target, "at": match.start(), "del": len(match[0]), "ins": ""}]):
                    self.erased.setdefault(target, set()).add(match[0])
        elif kind == "set":
            device.transact([{"op": "set", "id": target, "props": {"color": rng.choice(["red", "blue", None]), "level": rng.randint(0, 3)}}])
        elif kind == "move":
            parent = rng.choice([page, *live])
            siblings = [bid for bid in live if here[bid]["parent"] == parent and bid != target]
            op = {"op": "move", "id": target, "parent": parent}
            if siblings:
                op["after"] = rng.choice([None, *siblings])
            device.transact([op])
        elif kind == "delete":
            device.transact([{"op": "delete", "id": target}])
        elif kind == "restore" and trashed:
            device.transact([{"op": "restore", "id": rng.choice(trashed)}])
        elif kind == "tag":
            tag = rng.choice(self.tags)
            device.transact([{"op": "set", "id": target, "props": {tagging.key_of(tag): rng.choice(["manual", "accepted", "rejected", None])}}])
        elif kind == "turn":
            device.transact([{"op": "set", "id": target, "type": rng.choice(TYPES)}])
        elif kind == "cross":
            other = rng.choice([item for item in self.pages if item != page])
            device.transact([{"op": "move", "id": target, "parent": other}])

    def run(self, steps: int) -> None:
        for _ in range(steps):
            device = self.rng.choice(self.devices)
            self.step(device)
            if self.rng.random() < 0.15:
                src, dst = self.rng.sample(self.devices, 2)
                sync(src, dst)
        sync_all(self.devices)

    def check(self) -> None:
        assert_converged(self.devices)
        final = view(self.devices[0].conn)
        for device in self.devices:
            for page in self.pages:
                replayed = blocks.replay(device.conn, page)
                shown = {bid: (item["parent"], item["key"], item["text"], item["deleted"]) for bid, item in view(device.conn).items() if item["page"] == page}
                assert {bid: (item.parent_id, item.order_key, item.text, item.deleted_at) for bid, item in replayed.blocks.items()} == shown
        siblings: dict[str, list[str]] = {}
        for item in final.values():
            if item["parent"] is not None:
                siblings.setdefault(item["parent"], []).append(item["key"])
        assert all(len(keys) == len(set(keys)) for keys in siblings.values()), "sibling keys stay distinct"
        if self.cross_page:
            return
        assert self.created <= final.keys(), "no block any device made is lost"
        for bid in self.created:
            found = TOKEN.findall(final[bid]["text"])
            assert len(found) == len(set(found)), f"{bid}: a token was duplicated"
            assert set(found) == self.tokens.get(bid, set()) - self.erased.get(bid, set()), f"{bid}: text lost or resurrected"


@pytest.mark.parametrize("seed", range(20))
def test_two_devices_converge_after_random_offline_edits(devices, seed):
    scenario = Scenario(random.Random(seed), devices(2))
    scenario.run(100)
    scenario.check()


@pytest.mark.parametrize("seed", range(12))
def test_three_devices_converge_after_random_offline_edits(devices, seed):
    scenario = Scenario(random.Random(1000 + seed), devices(3))
    scenario.run(120)
    scenario.check()


@pytest.mark.parametrize("seed", range(8))
def test_devices_converge_with_moves_between_pages(devices, seed):
    scenario = Scenario(random.Random(2000 + seed), devices(3), pages=3, cross_page=True)
    scenario.run(100)
    scenario.check()
