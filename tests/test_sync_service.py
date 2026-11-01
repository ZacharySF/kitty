"""Q16: pairing two kittys, syncing them over the encrypted channel, and compaction.

Each device is a real SyncService on its own database, listening on 127.0.0.1 with an
ephemeral port and no LAN discovery; the tests drive pairing, sync sessions and the
scheduler directly instead of waiting for timers.
"""
from __future__ import annotations

import secrets
import socket
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kitty.core import keys as keys_core
from kitty.core.blocks import history
from kitty.core.sync import channel, peers, protocol, service

from sync_support import Device, assert_converged, make_template, view


@pytest.fixture(scope="session")
def template(tmp_path_factory) -> Path:
    return make_template(tmp_path_factory.mktemp("template") / "template.db")


class Kitty:
    """A device plus the sync service a kitty! server would run for it."""

    def __init__(self, folder: Path, name: str, template: Path):
        self.device = Device(folder / name, name, template)
        self.device.conn.execute("UPDATE sync_device SET name=?", (f"Kitty {name.upper()}",))
        (folder / name / "root").mkdir()
        self.keys = keys_core.Keys(folder / name / "root", store=None, use_env=False)
        self.sync = service.SyncService(self.device.path, self.keys, port=0, host="127.0.0.1", discovery=False, scheduler=False)
        self.sync.start()

    @property
    def conn(self):
        return self.device.conn

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.sync.port}"

    def peer(self, other: "Kitty"):
        return peers.get(self.conn, other.sync.identity.device_id)

    def close(self) -> None:
        self.sync.stop()
        self.device.close()


@pytest.fixture
def kittys(tmp_path, template):
    made: list[Kitty] = []

    def make(*names: str) -> list[Kitty]:
        for name in names:
            made.append(Kitty(tmp_path, name, template))
        return made[-len(names):]

    yield make
    for kitty in made:
        kitty.close()


def pair(a: Kitty, b: Kitty) -> None:
    b.sync.show_code()
    code = b.sync.state()["pairing"]["code"]
    a.sync.pair(code.lower().replace("-", " "), b.address)
    # The showing side trusts the typing side before confirming, so this holds at once.
    assert b.peer(a)["public_key"]


def new_page(kitty: Kitty, *texts: str) -> tuple[str, list[str]]:
    page, ids = str(uuid.uuid4()), [str(uuid.uuid4()) for _ in texts]
    kitty.device.transact([{"op": "insert", "id": page, "type": "page", "parent": None, "text": "Synced", "props": {"revision": 1}},
                           *({"op": "insert", "id": bid, "type": "text", "parent": page, "text": text} for bid, text in zip(ids, texts))])
    return page, ids


def test_pairing_with_the_shown_code_trusts_both_ways(kittys):
    a, b = kittys("a", "b")
    pair(a, b)
    assert bytes(a.peer(b)["public_key"]) == b.sync.identity.public_key
    assert bytes(b.peer(a)["public_key"]) == a.sync.identity.public_key
    assert a.peer(b)["name"] == "Kitty B" and b.peer(a)["name"] == "Kitty A"
    assert b.sync.state()["pairing"]["paired"] == {"device_id": a.sync.identity.device_id, "name": "Kitty A"}
    assert b.peer(a)["last_address"] == a.address, "the listener learns where the other device listens"
    state = a.sync.state()
    assert [item["name"] for item in state["peers"]] == ["Kitty B"]
    assert len(state["device"]["fingerprint"].replace(" ", "")) == 16


def test_wrong_codes_are_refused_and_retire_the_code(kittys):
    a, b = kittys("a", "b")
    b.sync.show_code()
    real = b.sync.state()["pairing"]["code"]
    wrong = "ZZZ-ZZZ" if real != "ZZZ-ZZZ" else "YYY-YYY"
    for tries_left in (2, 1):
        with pytest.raises(peers.PeersError, match="Wrong code"):
            a.sync.pair(wrong, b.address)
        assert b.sync.state()["pairing"]["attempts_left"] == tries_left
    with pytest.raises(peers.PeersError):
        a.sync.pair(wrong, b.address)
    assert b.sync.state()["pairing"] is None
    with pytest.raises(peers.PeersError):
        a.sync.pair(real, b.address)
    assert peers.paired(a.conn) == [] and peers.paired(b.conn) == []


def test_pairing_needs_a_shown_code_and_a_reachable_address(kittys):
    a, b = kittys("a", "b")
    with pytest.raises(peers.PeersError, match="Wrong code|Pairing failed"):
        a.sync.pair("ABC-DEF", b.address)
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    with pytest.raises(peers.PeersError, match="No kitty"):
        a.sync.pair("ABC-DEF", f"127.0.0.1:{closed}")


def test_a_copy_of_this_device_cannot_pair(kittys):
    a, b = kittys("a", "b")
    b.conn.execute("UPDATE block_history SET device_id=?", (a.sync.identity.device_id,))
    b.sync.identity = peers.Identity(a.sync.identity.device_id, "copy", b.sync.identity.private_key, b.sync.identity.public_key)
    b.sync.show_code()
    with pytest.raises(peers.PeersError, match="copy"):
        a.sync.pair(b.sync.state()["pairing"]["code"], b.address)
    assert peers.paired(a.conn) == [] and peers.paired(b.conn) == []


def test_paired_devices_sync_pages_both_ways_and_converge(kittys):
    a, b = kittys("a", "b")
    pair(a, b)
    page, (first, second) = new_page(a, "from A", "shared")
    result = a.sync.sync_with(a.peer(b))
    assert result is not None and result.sent_pages == 1
    assert view(b.conn)[first]["text"] == "from A"
    a.device.transact([{"op": "text", "id": second, "at": 0, "del": 0, "ins": "A: "}])
    b.device.transact([{"op": "text", "id": second, "at": 6, "del": 0, "ins": " :B"}])
    b_page, _ = new_page(b, "from B")
    b.sync.sync_with(b.peer(a))
    assert_converged([a.device, b.device])
    assert view(a.conn)[second]["text"] == "A: shared :B"
    assert b_page in view(a.conn)
    a.sync.sync_with(a.peer(b))
    assert a.peer(b)["acked_seq"] == a.conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0]
    assert a.peer(b)["last_sync_at"] and b.peer(a)["last_sync_at"]


def test_only_what_the_other_side_lacks_travels(kittys):
    a, b = kittys("a", "b")
    pair(a, b)
    page, (block,) = new_page(a, secrets.token_hex(10_000))
    first = a.sync.sync_with(a.peer(b))
    a.device.transact([{"op": "text", "id": block, "at": 0, "del": 0, "ins": "y"}])
    second = a.sync.sync_with(a.peer(b))
    assert second.sent_bytes < first.sent_bytes / 10
    idle = a.sync.sync_with(a.peer(b))
    assert idle.sent_pages == 0 and idle.received_pages == 0


def test_the_scheduler_pushes_local_changes(kittys):
    a, b = kittys("a", "b")
    pair(a, b)
    a.sync.tick()
    page, _ = new_page(a, "scheduled")
    assert a.sync.state()["peers"][0]["pending"] > 0
    a.sync.tick()
    assert page in view(b.conn)
    assert a.sync.state()["peers"][0]["pending"] == 0


def test_pause_refuses_and_skips_sync(kittys):
    a, b = kittys("a", "b")
    pair(a, b)
    a.sync.sync_with(a.peer(b))
    b.sync.set_paused(True)
    page, _ = new_page(a, "while paused")
    assert a.sync.sync_with(a.peer(b)) is None
    assert a.sync.state()["peers"][0]["status"] in {"error", "offline"}
    assert page not in view(b.conn)
    b.sync.set_paused(False)
    assert a.sync.sync_with(a.peer(b)) is not None and page in view(b.conn)


def test_an_unpaired_device_is_refused(kittys):
    a, b = kittys("a", "b")
    pair(a, b)
    b.sync.unpair(a.sync.identity.device_id)
    assert a.sync.sync_with(a.peer(b)) is None
    assert "pair again" in a.sync.state()["peers"][0]["error"]


def test_unknown_keys_get_nothing(kittys):
    (b,) = kittys("b")
    with closing(socket.create_connection(("127.0.0.1", b.sync.port), timeout=5)) as sock:
        sock.settimeout(5)
        from kitty.core.sync import noise

        stranger, _ = noise.generate_keypair()
        with pytest.raises(channel.ChannelError):
            channel.connect(sock, private_key=stranger, peer_public_key=b.sync.identity.public_key)


def test_compaction_waits_for_every_paired_device_to_acknowledge(kittys):
    a, b = kittys("a", "b")
    page, (block,) = new_page(a, "old")
    pair(a, b)
    later = datetime.now(timezone.utc) + timedelta(days=history.RETENTION_DAYS + 1)
    assert history.compact(a.conn, now=later)["folded"] == 0, "b has acknowledged nothing yet"
    a.sync.sync_with(a.peer(b))
    acked = a.peer(b)["acked_seq"]
    a.device.transact([{"op": "text", "id": block, "at": 3, "del": 0, "ins": " and new"}])
    result = history.compact(a.conn, now=later)
    assert result["horizon_seq"] == acked and result["folded"] > 0
    assert a.conn.execute("SELECT COUNT(*) FROM op_log WHERE seq>?", (acked,)).fetchone()[0] > 0
    a.sync.sync_with(a.peer(b))
    assert view(b.conn)[block]["text"] == "old and new"


def test_a_device_paired_after_compaction_gets_a_full_snapshot_transfer(kittys):
    a, b = kittys("a", "b")
    page, (block,) = new_page(a, "kept in snapshots only")
    later = datetime.now(timezone.utc) + timedelta(days=history.RETENTION_DAYS + 1)
    assert history.compact(a.conn, now=later)["folded"] > 0
    assert a.conn.execute("SELECT COUNT(*) FROM op_log").fetchone()[0] == 0
    pair(a, b)
    result = b.sync.sync_with(b.peer(a))
    assert result.received_pages == 1
    assert view(b.conn)[block]["text"] == "kept in snapshots only"
    assert_converged([a.device, b.device])


def test_three_devices_relay_through_each_other(kittys):
    a, b, c = kittys("a", "b", "c")
    pair(a, b)
    pair(b, c)
    page, (block,) = new_page(a, "from A")
    a.sync.sync_with(a.peer(b))
    b.sync.sync_with(b.peer(c))
    assert view(c.conn)[block]["text"] == "from A"
    c.device.transact([{"op": "text", "id": block, "at": 6, "del": 0, "ins": " via C"}])
    c.sync.sync_with(c.peer(b))
    b.sync.sync_with(b.peer(a))
    assert_converged([a.device, b.device, c.device])
    assert view(a.conn)[block]["text"] == "from A via C"


def test_messages_are_framed_and_validated():
    header, body = protocol.decode(protocol.encode({"type": "pages", "items": []}, b"\x00\x01"))
    assert header == {"type": "pages", "items": []} and body == b"\x00\x01"
    for bad in (b"", b"\x00\x00\x00\x09{}", b"\x00\x00\x00\x02[]", b"\x00\x00\x00\x02{}"):
        with pytest.raises(protocol.ProtocolError):
            protocol.decode(bad)


def test_addresses_are_normalized():
    assert peers.normalize_address("192.168.1.20") == f"192.168.1.20:{peers.DEFAULT_PORT}"
    assert peers.normalize_address(" laptop.tailnet.ts.net:9000 ") == "laptop.tailnet.ts.net:9000"
    assert peers.normalize_address("[fd7a:115c::1]:8770") == "[fd7a:115c::1]:8770"
    assert peers.normalize_address("fd7a:115c::1") == f"[fd7a:115c::1]:{peers.DEFAULT_PORT}"
    for bad in ("", "a b", "host:0", "host:70000", "http://x"):
        with pytest.raises(peers.PeersError):
            peers.normalize_address(bad)


def test_the_device_key_lives_in_the_keys_service(kittys):
    (a,) = kittys("a")
    stored = a.keys.lookup(peers.key_name(a.sync.identity.device_id))
    assert stored is not None and stored[1] == "file"
    assert a.conn.execute("SELECT COUNT(*) FROM sync_device").fetchone()[0] == 1
    again = peers.identity(a.conn, a.keys)
    assert again.public_key == a.sync.identity.public_key


def test_stopping_before_the_listener_thread_runs_is_clean(tmp_path, template, monkeypatch):
    """stop() can close the listening socket before the accept thread first runs (seen under load)."""
    spawned = []
    monkeypatch.setattr(service.SyncService, "_spawn", lambda self, target, name: spawned.append(target))
    kitty = Kitty(tmp_path, "a", template)
    try:
        kitty.sync.stop()
        accept = next(target for target in spawned if target.__name__ == "_accept_loop")
        accept()  # returns quietly: no OSError (EBADF) from a closed socket
    finally:
        kitty.device.close()
