"""What two paired devices say to each other over an encrypted channel (Q16).

A message is a 4-byte big-endian header length, a JSON header and an optional binary
body (Loro bytes are never base64'd). A session runs in two halves so that neither
side ever writes while the other is writing (no buffer deadlock): the initiator
pulls first, then pushes; the responder does the opposite.

::

    both        hello {v, device, name, since}     since: the other's newest op_log
                                                   sequence this device has applied
    pusher      offer {head, full, pages}          pages changed in its log after since
    puller      want  {pages: {id: vv | null}}     its version vector per page (base64),
                                                   null when it has no copy
    pusher      pages {items: [{id, len, actors}]} Loro updates from that vector (a full
                + body ...                         document for null), in batches
    pusher      done  {head}
    puller      ack   {seq: head}                  after every batch is merged

Only what the puller lacks travels: the pusher exports each page's Loro changes
from the puller's version vector. New pages arrive whole; a page purged on one side
arrives as its document with the root gone, and is purged on the other. Each page
item lists ``actors`` ({kind, name, session}) who wrote the pusher's log rows for it
after ``since`` (all its kept rows for a full transfer; rows it merged from the puller
left out): the puller records them as the merged row's ``via`` (kitty.core.blocks.actor).
A device that sends no ``actors`` (an older build) merges as the peer alone.

``full`` is the snapshot transfer: when ``since`` is older than the pusher's
compaction horizon (or newer than its log, e.g. after a restore), the log cannot say
which pages changed, so every page is offered and the version vectors decide.

The puller merges each batch as one transaction (kitty.core.blocks.merge), so the
op_log, History, live push, tags and the projection see ordinary transactions. Its
``ack`` moves the pusher's ``acked_seq`` for this peer, which gates compaction.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from kitty.core.blocks import merge
from kitty.core.blocks.actor import MAX_VIA, Actor, from_row, merged_via, parse
from kitty.core.sync.channel import Channel
from kitty.core.sync.peers import DEVICE_ID, Identity, PeersError, clean_name

VERSION = 1
BATCH_BYTES = 8 * 1024 * 1024
MAX_HEADER = 16 * 1024 * 1024
_LENGTH = struct.Struct(">I")


class ProtocolError(Exception):
    """The other device broke the protocol (or is not the device it claims to be)."""


def encode(header: dict, body: bytes = b"") -> bytes:
    raw = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode()
    return _LENGTH.pack(len(raw)) + raw + body


def decode(data: bytes) -> tuple[dict, bytes]:
    if len(data) < _LENGTH.size:
        raise ProtocolError("message too short")
    (length,) = _LENGTH.unpack_from(data)
    if length > min(MAX_HEADER, len(data) - _LENGTH.size):
        raise ProtocolError("bad header length")
    try:
        header = json.loads(data[_LENGTH.size:_LENGTH.size + length])
    except (ValueError, UnicodeError):
        raise ProtocolError("bad header") from None
    if not isinstance(header, dict) or not isinstance(header.get("type"), str):
        raise ProtocolError("bad header")
    return header, data[_LENGTH.size + length:]


def _page_id(value: Any) -> str:
    try:
        if isinstance(value, str) and str(uuid.UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise ProtocolError("bad page id")


def _seq(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ProtocolError("bad sequence number")
    return value


@dataclass
class Result:
    """What one session moved."""

    sent_pages: int = 0
    received_pages: int = 0
    sent_bytes: int = 0
    received_bytes: int = 0
    merged: list[str] = field(default_factory=list)
    #: This device's head the peer acknowledged / the peer's head this device applied.
    acked: int | None = None
    theirs: int | None = None
    name: str | None = None
    #: The port the other device listens on (so the side it connected to can reach it later).
    port: int | None = None


class Session:
    """One sync between this device and a paired one over an established channel."""

    def __init__(self, channel: Channel, connect: Callable[[], sqlite3.Connection], own: Identity, peer: sqlite3.Row,
                 *, initiator: bool, port: int | None = None):
        self.channel = channel
        self.connect = connect
        self.own = own
        self.peer = peer
        self.initiator = initiator
        self.port = port
        self.result = Result()

    # Messages ------------------------------------------------------------------------

    def send(self, header: dict, body: bytes = b"") -> None:
        data = encode(header, body)
        self.result.sent_bytes += len(data)
        self.channel.send(data)

    def expect(self, kind: str) -> tuple[dict, bytes]:
        data = self.channel.recv()
        self.result.received_bytes += len(data)
        header, body = decode(data)
        if header["type"] == "error":
            raise ProtocolError(str(header.get("error", "the other device stopped"))[:200])
        if header["type"] != kind:
            raise ProtocolError(f"expected {kind}, got {header['type'][:20]}")
        return header, body

    # The session ---------------------------------------------------------------------

    def run(self) -> Result:
        self.send({"type": "hello", "v": VERSION, "device": self.own.device_id, "name": self.own.name,
                   "since": int(self.peer["their_seq"]), "port": self.port})
        hello, _ = self.expect("hello")
        if hello.get("v") != VERSION:
            raise ProtocolError("the other device runs an incompatible rika! sync version")
        if hello.get("device") != self.peer["device_id"]:
            raise ProtocolError("the other device is not the one paired with that key")
        try:
            self.result.name = clean_name(hello.get("name"))
        except PeersError:
            self.result.name = None
        since = _seq(hello.get("since"))
        port = hello.get("port")
        self.result.port = port if type(port) is int and 1 <= port <= 65535 else None
        conn = self.connect()
        try:
            if self.initiator:
                self.pull(conn)
                self.push(conn, since)
            else:
                self.push(conn, since)
                self.pull(conn)
        finally:
            conn.close()
        return self.result

    def push(self, conn: sqlite3.Connection, since: int) -> None:
        head = conn.execute("SELECT COALESCE(MAX(seq),0) FROM op_log").fetchone()[0]
        horizon = conn.execute("SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0]
        full = since < horizon or since > head
        if full:
            pages = [row[0] for row in conn.execute(
                "SELECT id FROM blocks WHERE parent_id IS NULL UNION SELECT page_id FROM page_crdt ORDER BY 1")]
        else:
            pages = [row[0] for row in conn.execute("SELECT DISTINCT page_id FROM op_log WHERE seq>? AND seq<=? ORDER BY 1",
                                                    (since, head))]
        merge.seed(conn, pages)
        pages = [page for page in pages if conn.execute("SELECT 1 FROM page_crdt WHERE page_id=?", (page,)).fetchone()]
        self.send({"type": "offer", "head": head, "full": full, "pages": pages})
        want, _ = self.expect("want")
        wanted = want.get("pages")
        if not isinstance(wanted, dict) or not set(wanted) <= set(pages):
            raise ProtocolError("asked for pages that were not offered")
        items: list[dict] = []
        body: list[bytes] = []
        size = 0
        for page_id in sorted(wanted):
            known = wanted[page_id]
            if known is not None and not isinstance(known, str):
                raise ProtocolError("bad version vector")
            data = merge.export(conn, page_id, base64.b64decode(known) if known else None)
            if not data:
                continue
            if items and size + len(data) > BATCH_BYTES:
                self._send_pages(items, body)
                items, body, size = [], [], 0
            items.append({"id": page_id, "len": len(data),
                          "actors": [item.as_dict() for item in self._actors(conn, page_id, 0 if full else since)]})
            body.append(data)
            size += len(data)
        if items:
            self._send_pages(items, body)
        self.send({"type": "done", "head": head})
        ack, _ = self.expect("ack")
        acked = _seq(ack.get("seq"))
        if acked > head:
            raise ProtocolError("acknowledged more than was offered")
        self.result.acked = acked

    def _actors(self, conn: sqlite3.Connection, page_id: str, since: int) -> tuple[Actor, ...]:
        """Who wrote this page's log rows after `since` (merges from the other device left out)."""
        rows = conn.execute("SELECT DISTINCT actor, actor_name, actor_session, actor_via FROM op_log "
                            "WHERE page_id=? AND seq>? AND device_id<>? ORDER BY 1, 2, 3",
                            (page_id, since, self.peer["device_id"])).fetchall()
        return merged_via(from_row(row) for row in rows)

    def _send_pages(self, items: list[dict], body: list[bytes]) -> None:
        self.send({"type": "pages", "items": items}, b"".join(body))
        self.result.sent_pages += len(items)

    def pull(self, conn: sqlite3.Connection) -> None:
        offer, _ = self.expect("offer")
        head = _seq(offer.get("head"))
        pages = offer.get("pages")
        if not isinstance(pages, list):
            raise ProtocolError("bad offer")
        pages = [_page_id(page) for page in pages]
        legacy = [page for page in pages if not conn.execute("SELECT 1 FROM page_crdt WHERE page_id=?", (page,)).fetchone()]
        if legacy:
            merge.seed(conn, legacy)
        want = {}
        for page_id in pages:
            vv = merge.vv(conn, page_id)
            want[page_id] = base64.b64encode(vv).decode() if vv is not None else None
        self.send({"type": "want", "pages": want})
        while True:
            data = self.channel.recv()
            self.result.received_bytes += len(data)
            header, body = decode(data)
            if header["type"] == "done":
                if _seq(header.get("head")) != head:
                    raise ProtocolError("done does not match the offer")
                break
            if header["type"] != "pages" or not isinstance(header.get("items"), list):
                raise ProtocolError("expected pages")
            updates: dict[str, list[bytes]] = {}
            actors: dict[str, list[Actor]] = {}
            offset = 0
            for item in header["items"]:
                if not isinstance(item, dict):
                    raise ProtocolError("bad page item")
                page_id, length = _page_id(item.get("id")), _seq(item.get("len"))
                if page_id not in want or offset + length > len(body):
                    raise ProtocolError("bad page item")
                updates.setdefault(page_id, []).append(body[offset:offset + length])
                raw = item.get("actors", [])
                if not isinstance(raw, list) or len(raw) > MAX_VIA:
                    raise ProtocolError("bad page actors")
                actors.setdefault(page_id, []).extend(found for found in map(parse, raw) if found is not None)
                offset += length
            if offset != len(body):
                raise ProtocolError("bad pages body")
            merged = merge.merge(conn, updates, origin=self.peer["device_id"], actors=actors)
            self.result.received_pages += len(updates)
            self.result.merged.extend(merged["pages"])
        self.send({"type": "ack", "seq": head})
        self.result.theirs = head


# Pairing ------------------------------------------------------------------------------

def pair_exchange(channel: Channel, own: Identity, port: int | None) -> dict:
    """Swap identities over a freshly paired channel: the other device's ``{device, name, port}``."""
    channel.send(encode({"type": "pair", "v": VERSION, "device": own.device_id, "name": own.name, "port": port}))
    header, _ = decode(channel.recv())
    if header.get("type") != "pair" or header.get("v") != VERSION:
        raise ProtocolError("the other device runs an incompatible rika! sync version")
    device = header.get("device")
    if not isinstance(device, str) or not DEVICE_ID.fullmatch(device):
        raise ProtocolError("the other device sent an invalid identity")
    port = header.get("port")
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ProtocolError("the other device sent an invalid port")
    return {"device": device, "name": header.get("name"), "port": port}


def pair_confirm(channel: Channel, error: str | None) -> None:
    """Tell the other side whether this side trusts it now; raise if the other side refused."""
    channel.send(encode({"type": "paired", "ok": error is None, "error": error}))
    header, _ = decode(channel.recv())
    if header.get("type") != "paired":
        raise ProtocolError("the other device stopped pairing")
    if header.get("ok") is not True:
        raise ProtocolError(str(header.get("error") or "the other device refused to pair")[:200])
    if error is not None:
        raise ProtocolError(error)
