"""This device's sync identity and the trust list of paired devices (Q16).

A device is its database's ``device_id`` (``block_history``), a name people
recognise (``sync_device.name``, the host name until changed) and a long-term
X25519 key pair. The private key is a secret, so it lives in the Keys service
(Q10: OS keyring, else the owner-only ``.private/keys`` file) under
``SYNC_KEY_<first 12 hex of the device ID>``: one key per workspace, never in
SQLite, never shown. Removing it there makes this device a new identity that paired
devices no longer accept until it pairs again.

``peers`` is the trust list: a paired device's ID, name, public key and where it was
last reached, plus the two sync positions. ``acked_seq`` (how much of this device's
op_log the peer has confirmed) gates history compaction: rows a paired device has not
acknowledged are never compacted away.
"""
from __future__ import annotations

import base64
import hashlib
import re
import socket
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from kitty.core import keys as keys_core
from kitty.core.sync import noise

DEVICE_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_NAME = 64
DEFAULT_PORT = 8770


class PeersError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Identity:
    device_id: str
    name: str
    private_key: bytes
    public_key: bytes

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.public_key)


def fingerprint(public_key: bytes) -> str:
    """16 hex characters people can compare (and mDNS announces)."""
    return hashlib.sha256(public_key).hexdigest()[:16]


def spaced(value: str) -> str:
    return " ".join(value[index:index + 4] for index in range(0, len(value), 4))


def clean_name(value: object) -> str:
    if not isinstance(value, str):
        raise PeersError("A device name is text")
    name = " ".join(value.split())
    if not 1 <= len(name) <= MAX_NAME or any(ord(char) < 32 for char in name):
        raise PeersError(f"A device name has 1–{MAX_NAME} characters")
    return name


def device_name(conn: sqlite3.Connection) -> str:
    name = conn.execute("SELECT name FROM sync_device WHERE id=1").fetchone()[0]
    return name or (socket.gethostname()[:MAX_NAME] or "kitty")


def rename(conn: sqlite3.Connection, value: object) -> str:
    name = clean_name(value)
    conn.execute("UPDATE sync_device SET name=? WHERE id=1", (name,))
    return name


def key_name(device_id: str) -> str:
    return f"SYNC_KEY_{device_id[:12].upper()}"


def identity(conn: sqlite3.Connection, store: keys_core.Keys) -> Identity:
    """This device's identity, making its key pair on first use."""
    device_id = conn.execute("SELECT device_id FROM block_history WHERE id=1").fetchone()[0]
    name = key_name(device_id)
    found = store.lookup(name)
    private = None
    if found:
        try:
            private = base64.b64decode(found[0], validate=True)
        except ValueError:
            private = None
        if private is not None and len(private) != 32:
            private = None
    if private is None:
        private, _public = noise.generate_keypair()
        store.set(name, base64.b64encode(private).decode())
    return Identity(device_id, device_name(conn), private, noise.public_key(private))


# The trust list --------------------------------------------------------------------

def paired(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM peers ORDER BY name COLLATE NOCASE, device_id").fetchall()


def by_key(conn: sqlite3.Connection, public_key: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM peers WHERE public_key=?", (public_key,)).fetchone()


def get(conn: sqlite3.Connection, device_id: object) -> sqlite3.Row:
    if not isinstance(device_id, str) or not DEVICE_ID.fullmatch(device_id):
        raise PeersError("Choose a paired device")
    row = conn.execute("SELECT * FROM peers WHERE device_id=?", (device_id,)).fetchone()
    if row is None:
        raise PeersError("That device is not paired", 404)
    return row


def add(conn: sqlite3.Connection, own: Identity, *, device_id: object, name: object, public_key: bytes,
        address: str | None) -> sqlite3.Row:
    """Trust a device that just proved the pairing code; pairing again replaces its key and restarts its sync."""
    if not isinstance(device_id, str) or not DEVICE_ID.fullmatch(device_id):
        raise PeersError("The other device sent an invalid identity")
    if device_id == own.device_id:
        raise PeersError("That is this device (or a copy of its database); copies cannot be paired", 409)
    if len(public_key) != 32 or public_key == own.public_key:
        raise PeersError("The other device sent an invalid key")
    name = clean_name(name)
    conn.execute("DELETE FROM peers WHERE public_key=? AND device_id<>?", (public_key, device_id))
    conn.execute("INSERT INTO peers(device_id,name,public_key,address,last_address,paired_at) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(device_id) DO UPDATE SET name=excluded.name,public_key=excluded.public_key,"
                 "last_address=excluded.last_address,paired_at=excluded.paired_at,last_error=NULL,acked_seq=0,their_seq=0",
                 (device_id, name, public_key, None, address, now()))
    return get(conn, device_id)


def remove(conn: sqlite3.Connection, device_id: object) -> None:
    get(conn, device_id)
    conn.execute("DELETE FROM peers WHERE device_id=?", (device_id,))


def set_address(conn: sqlite3.Connection, device_id: object, address: object) -> None:
    get(conn, device_id)
    conn.execute("UPDATE peers SET address=? WHERE device_id=?", (normalize_address(address) if address else None, device_id))


def normalize_address(value: object, default_port: int = DEFAULT_PORT) -> str:
    """``host:port`` from what someone typed: a LAN IP, a Tailscale name or IP, optionally with a port."""
    if not isinstance(value, str):
        raise PeersError("Enter an address such as 192.168.1.20 or laptop.tailnet.ts.net")
    text = value.strip()
    host, port = split_address(text, default_port)
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def split_address(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    text = text.strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port_text = rest[1:] if rest.startswith(":") else ""
    elif text.count(":") == 1:
        host, port_text = text.split(":")
    else:
        host, port_text = text, ""
    if not host or len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.\-:%_]+", host):
        raise PeersError("Enter an address such as 192.168.1.20 or laptop.tailnet.ts.net")
    if port_text:
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise PeersError("The port is a number from 1 to 65535")
        return host, int(port_text)
    return host, default_port


def record_sync(conn: sqlite3.Connection, device_id: str, *, acked: int | None = None, theirs: int | None = None,
                address: str | None = None, sent: int = 0, received: int = 0, error: str | None = None, synced: bool = False) -> None:
    stamp = now()
    conn.execute(
        "UPDATE peers SET acked_seq=MAX(acked_seq, COALESCE(?, acked_seq)), their_seq=COALESCE(?, their_seq), "
        "last_address=COALESCE(?, last_address), sent_bytes=sent_bytes+?, received_bytes=received_bytes+?, "
        "last_error=?, reached_at=CASE WHEN ? IS NULL THEN reached_at ELSE ? END, "
        "last_sync_at=CASE WHEN ? THEN ? ELSE last_sync_at END WHERE device_id=?",
        (acked, theirs, address, sent, received, error, address, stamp, int(synced), stamp, device_id))


def compaction_limit(conn: sqlite3.Connection) -> int | None:
    """The highest op_log sequence every paired device has acknowledged (None: no paired devices)."""
    try:
        row = conn.execute("SELECT COUNT(*), MIN(acked_seq) FROM peers").fetchone()
    except sqlite3.OperationalError:  # before migration 040
        return None
    return row[1] if row[0] else None
