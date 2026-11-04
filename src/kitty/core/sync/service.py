"""The sync service one rika! server runs (Q16): listener, pairing, scheduler, state.

- **Listener**: a TCP port (``8770`` unless taken or set with ``KITTY_SYNC_PORT``) on
  every interface, so devices on the LAN or the tailnet can reach it. It speaks only
  the encrypted channel (kitty.core.sync.channel): a paired device's key opens a sync,
  a pairing attempt is answered only while this device shows a code, and anything
  else is closed without a byte.
- **Pairing**: this device shows a 6-character code for 5 minutes (three failed tries
  retire it); the other device types it with this device's address (picked from LAN
  discovery or typed, e.g. a Tailscale name). SPAKE2 turns the code into the key that
  authenticates the exchange of both devices' long-term keys; each then trusts the
  other (``peers``).
- **Scheduler**: syncs with a paired device when this device has changes it has not
  acknowledged (checked every couple of seconds, so edits reach a reachable device in
  seconds), every 30 seconds otherwise (to pull its changes), and at once on "Sync
  now". An unreachable device is retried with backoff up to 5 minutes. Addresses are
  tried in order: the one the person typed, the one LAN discovery sees now, the last
  one that worked.
- **Discovery**: mDNS announcement and browsing (kitty.core.sync.discovery).

Only block content syncs (pages, documents, quick notes, tags: everything in the block
backend). Jobs, applications, calendar and other tables stay on each device.
"""
from __future__ import annotations

import os
import secrets
import socket
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kitty.core import keys as keys_core
from kitty.core.blocks import history
from kitty.core.sync import channel as channel_core
from kitty.core.sync import peers, protocol
from kitty.core.sync.discovery import Discovery
from kitty.db.connection import connect, transaction

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_TTL = 300.0
CODE_TRIES = 3
TICK = 2.0
INTERVAL = 30.0
MAX_BACKOFF = 300.0
CONNECT_TIMEOUT = 5.0
IO_TIMEOUT = 30.0


@dataclass
class _Code:
    code: str
    expires: float
    tries: int = CODE_TRIES
    paired: dict | None = None


@dataclass
class _PeerState:
    status: str = "idle"
    error: str | None = None
    next_try: float = 0.0
    backoff: float = 0.0
    last_try: float = 0.0
    wanted: bool = False
    online: bool = False


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).isoformat()


def _short(error: BaseException) -> str:
    if isinstance(error, channel_core.ChannelError):
        return error.reason
    if isinstance(error, (ConnectionRefusedError, socket.timeout, TimeoutError)):
        return "not reachable"
    if isinstance(error, OSError):
        return error.strerror or "not reachable"
    return str(error)[:200] or type(error).__name__


class SyncService:
    """Everything sync does for one database; thread-safe, started and stopped by the web server."""

    def __init__(self, db_path: Path, keys: keys_core.Keys, *, port: int | None = None, host: str = "0.0.0.0",
                 discovery: bool = True, scheduler: bool = True, clock: Callable[[], float] = time.time):
        self.db_path = db_path
        self.keys = keys
        self.wanted_port = peers.DEFAULT_PORT if port is None else port
        self.host = host
        self.use_discovery = discovery
        self.use_scheduler = scheduler
        self.clock = clock
        self.port: int | None = None
        self.error: str | None = None
        self.identity: peers.Identity | None = None
        self.discovery: Discovery | None = None
        self._lock = threading.RLock()
        self._code: _Code | None = None
        self._states: dict[str, _PeerState] = {}
        self._busy: set[str] = set()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._threads: list[threading.Thread] = []

    # Lifecycle ---------------------------------------------------------------------

    def connect(self):
        return connect(self.db_path)

    def start(self) -> None:
        try:
            with closing(self.connect()) as conn:
                self.identity = peers.identity(conn, self.keys)
        except Exception as exc:  # noqa: BLE001 - a locked keyring must not stop the server
            self.error = f"No sync key: {_short(exc)}"
            return
        try:
            self._server = self._listen()
            self.port = self._server.getsockname()[1]
        except OSError as exc:
            self.error = f"Cannot listen for other devices: {_short(exc)}"
            self._server = None
        if self._server is not None:
            self._spawn(self._accept_loop, "kitty-sync-listen")
            if self.use_discovery:
                self.discovery = Discovery(device_id=self.identity.device_id, name=self.identity.name, port=self.port,
                                           fingerprint=self.identity.fingerprint)
                self.discovery.start()
        if self.use_scheduler:
            self._spawn(self._schedule_loop, "kitty-sync-schedule")

    def _listen(self) -> socket.socket:
        try:
            server = socket.create_server((self.host, self.wanted_port), backlog=16)
        except OSError:
            if self.wanted_port == 0:
                raise
            server = socket.create_server((self.host, 0), backlog=16)
        # Closing a socket does not wake accept() on Linux, so the accept loop polls the stop
        # flag. Set here, before the loop's thread exists: stop() may close the socket before
        # that thread first runs.
        server.settimeout(0.5)
        return server

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        if self.discovery is not None:
            self.discovery.close()
        for thread in self._threads:
            thread.join(timeout=5)

    # State -------------------------------------------------------------------------

    def _state(self, device_id: str) -> _PeerState:
        with self._lock:
            return self._states.setdefault(device_id, _PeerState())

    def _paused(self, conn) -> bool:
        return bool(conn.execute("SELECT paused FROM sync_device WHERE id=1").fetchone()[0])

    def addresses(self) -> list[str]:
        if self.port is None:
            return []
        found: list[str] = []
        try:
            import ifaddr

            for adapter in ifaddr.get_adapters():
                for ip in adapter.ips:
                    text = ip.ip if isinstance(ip.ip, str) else ip.ip[0]
                    if ":" in text or text.startswith("127.") or text.startswith("169.254."):
                        continue
                    found.append(f"{text}:{self.port}")
        except Exception:  # noqa: BLE001
            pass
        return found or [f"127.0.0.1:{self.port}"]

    def state(self) -> dict:
        """What Settings → Devices shows."""
        now = self.clock()
        with closing(self.connect()) as conn:
            paused = self._paused(conn)
            head = conn.execute("SELECT COALESCE(MAX(seq),0) FROM op_log").fetchone()[0]
            horizon_limit = conn.execute("SELECT MAX(seq) FROM op_log WHERE at<?",
                                         ((datetime.now(timezone.utc) - timedelta(days=history.retention_days(conn))).isoformat(),)).fetchone()[0]
            rows = peers.paired(conn)
            name = peers.device_name(conn)
        seen = {item.device_id: item for item in (self.discovery.seen() if self.discovery else [])}
        listed = []
        for row in rows:
            state = self._state(row["device_id"])
            discovered = seen.get(row["device_id"])
            status = "paused" if paused else state.status
            listed.append({
                "device_id": row["device_id"], "name": row["name"], "address": row["address"],
                "last_address": row["last_address"], "paired_at": row["paired_at"], "last_sync_at": row["last_sync_at"],
                "reached_at": row["reached_at"], "status": status, "error": state.error if status == "error" else None,
                "pending": max(0, head - row["acked_seq"]) if row["last_sync_at"] else head,
                "holds_history": horizon_limit is not None and row["acked_seq"] < horizon_limit,
                "online": discovered is not None or state.online,
            })
        with self._lock:
            code = self._code
            if code is not None and code.paired is None and (code.expires <= now or code.tries <= 0):
                self._code = code = None
        identity = self.identity
        return {
            "device": {
                "id": identity.device_id if identity else None, "name": name,
                "fingerprint": peers.spaced(identity.fingerprint) if identity else "",
                "port": self.port, "addresses": self.addresses(), "listening": self._server is not None,
                "error": self.error, "paused": paused,
                "discovery": "off" if self.discovery is None else ("error" if self.discovery.error else "on"),
                "discovery_error": self.discovery.error if self.discovery else None,
            },
            "pairing": None if code is None else {
                "code": code.code, "expires_at": _iso(code.expires), "attempts_left": code.tries, "paired": code.paired},
            "discovered": [{"device_id": item.device_id, "name": item.name, "address": f"{item.host}:{item.port}" if ":" not in item.host
                            else f"[{item.host}]:{item.port}", "pairing": item.pairing,
                            "paired": any(row["device_id"] == item.device_id for row in rows)} for item in seen.values()],
            "peers": listed,
        }

    # Settings ----------------------------------------------------------------------

    def rename(self, name: object) -> None:
        with closing(self.connect()) as conn:
            with transaction(conn, immediate=True):
                clean = peers.rename(conn, name)
        if self.identity is not None:
            self.identity = peers.Identity(self.identity.device_id, clean, self.identity.private_key, self.identity.public_key)
        if self.discovery is not None:
            self.discovery.update(name=clean)

    def set_paused(self, paused: object) -> None:
        if not isinstance(paused, bool):
            raise peers.PeersError("paused is true or false")
        with closing(self.connect()) as conn:
            conn.execute("UPDATE sync_device SET paused=? WHERE id=1", (int(paused),))
        if not paused:
            self.sync_now()

    def unpair(self, device_id: object) -> None:
        with closing(self.connect()) as conn:
            with transaction(conn, immediate=True):
                peers.remove(conn, device_id)
        with self._lock:
            self._states.pop(str(device_id), None)

    def set_address(self, device_id: object, address: object) -> None:
        with closing(self.connect()) as conn:
            with transaction(conn, immediate=True):
                peers.set_address(conn, device_id, address)
        self.sync_now(str(device_id))

    def sync_now(self, device_id: str | None = None) -> None:
        with closing(self.connect()) as conn:
            rows = [peers.get(conn, device_id)] if device_id else peers.paired(conn)
        for row in rows:
            state = self._state(row["device_id"])
            state.wanted, state.next_try, state.backoff = True, 0.0, 0.0
        self._wake.set()

    # Pairing -----------------------------------------------------------------------

    def show_code(self) -> None:
        if self._server is None:
            raise peers.PeersError(self.error or "This device is not listening for other devices", 409)
        letters = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        with self._lock:
            self._code = _Code(f"{letters[:3]}-{letters[3:]}", self.clock() + CODE_TTL)
        if self.discovery is not None:
            self.discovery.update(pairing=True)

    def cancel_code(self) -> None:
        with self._lock:
            self._code = None
        if self.discovery is not None:
            self.discovery.update(pairing=False)

    def _offered_code(self) -> str | None:
        """The code an incoming pairing attempt must prove, taking one of its tries.

        The try is spent before the handshake starts, so a wrong guess is already counted
        when the other side sees the connection close, and a third one retires the code
        at once. A successful pairing marks the code used instead.
        """
        with self._lock:
            code = self._code
            if code is None or code.paired is not None or code.expires <= self.clock() or code.tries <= 0:
                return None
            code.tries -= 1
            return code.code

    def _attempt_done(self, code: _Code) -> None:
        """A pairing attempt that took a try of `code` finished; a spent code is retired."""
        with self._lock:
            if self._code is not code or code.paired is not None or code.tries > 0:
                return
            self._code = None
        if self.discovery is not None:
            self.discovery.update(pairing=False)

    def pair(self, code: object, address: object) -> dict:
        """Pair with the device at `address` that shows `code`; returns ``{device_id, name}``."""
        if self.identity is None:
            raise peers.PeersError(self.error or "Sync is not available", 409)
        if not isinstance(code, str) or len("".join(code.split()).replace("-", "")) != 6:
            raise peers.PeersError("Enter the 6-character code the other device shows")
        host, port = peers.split_address(address if isinstance(address, str) else "")
        try:
            sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError:
            raise peers.PeersError("No rika! answered at that address", 504) from None
        sock.settimeout(IO_TIMEOUT)
        try:
            channel = channel_core.pair_connect(sock, code=code, private_key=self.identity.private_key)
        except channel_core.ChannelError as exc:
            raise peers.PeersError("Wrong code or the code expired" if exc.reason == channel_core.WRONG_CODE
                                   else f"Pairing failed: {exc.reason}", 400) from None
        try:
            return self._finish_pairing(channel, f"{host}:{port}" if ":" not in host else f"[{host}]:{port}")
        finally:
            channel.close()

    def _finish_pairing(self, channel: channel_core.Channel, address: str | None, peer_host: str | None = None,
                        *, code: _Code | None = None) -> dict:
        """Swap identities and trust the other device.

        The side that shows the code (`code` given; `peer_host` is where the other device
        connected from) trusts the other device before it confirms, so by the time the
        typing side hears "paired", both list each other; it takes the trust back if the
        other side refuses.
        """
        assert self.identity is not None
        responder = code is not None
        try:
            theirs = protocol.pair_exchange(channel, self.identity, self.port)
        except (protocol.ProtocolError, channel_core.ChannelError) as exc:
            raise peers.PeersError(f"Pairing failed: {exc}", 400) from None
        error = None
        if theirs["device"] == self.identity.device_id:
            error = "That is this device (or a copy of its database); copies cannot be paired"
        else:
            try:
                peers.clean_name(theirs["name"])
            except peers.PeersError as exc:
                error = str(exc)
        if address is None:
            address = _join(peer_host, theirs["port"])
        result = None
        if responder and error is None:
            result = self._trust(theirs, channel.remote_static, address)
            with self._lock:
                code.paired = result
                if self._code is None:  # the last try succeeded after state() had hidden the spent code
                    self._code = code
        try:
            protocol.pair_confirm(channel, error)
        except (protocol.ProtocolError, channel_core.ChannelError) as exc:
            if result is not None:
                with closing(self.connect()) as conn:
                    with transaction(conn, immediate=True):
                        conn.execute("DELETE FROM peers WHERE device_id=?", (result["device_id"],))
                with self._lock:
                    code.paired = None
            raise peers.PeersError(f"Pairing failed: {exc}", 400) from None
        if result is None:
            result = self._trust(theirs, channel.remote_static, address)
        if responder and self.discovery is not None:
            self.discovery.update(pairing=False)
        self.sync_now(result["device_id"])
        return result

    def _trust(self, theirs: dict, public_key: bytes, address: str | None) -> dict:
        with closing(self.connect()) as conn:
            with transaction(conn, immediate=True):
                row = peers.add(conn, self.identity, device_id=theirs["device"], name=theirs["name"],
                                public_key=public_key, address=address)
        return {"device_id": row["device_id"], "name": row["name"]}

    # Listener ------------------------------------------------------------------------

    def _accept_loop(self) -> None:
        server = self._server
        assert server is not None
        while not self._stop.is_set():
            try:
                sock, address = server.accept()
            except TimeoutError:
                continue
            except OSError:  # stop() closed the listening socket
                return
            try:
                sock.settimeout(None)
            except OSError:
                sock.close()
                continue
            self._spawn_connection(sock, address)

    def _spawn_connection(self, sock: socket.socket, address) -> None:
        threading.Thread(target=self._serve, args=(sock, address), name="kitty-sync-peer", daemon=True).start()

    def _known(self, public_key: bytes) -> bool:
        with closing(self.connect()) as conn:
            return peers.by_key(conn, public_key) is not None

    def _serve(self, sock: socket.socket, address) -> None:
        identity = self.identity
        if identity is None:
            sock.close()
            return
        sock.settimeout(IO_TIMEOUT)
        attempt: _Code | None = None

        def offered() -> str | None:
            nonlocal attempt
            with self._lock:
                code, value = self._code, self._offered_code()
            attempt = code if value is not None else None
            return value

        try:
            kind, channel = channel_core.serve(sock, private_key=identity.private_key, is_known=self._known,
                                               pairing_code=offered)
        except channel_core.ChannelError:
            if attempt is not None:
                self._attempt_done(attempt)
            return
        try:
            if kind == "pair":
                try:
                    self._finish_pairing(channel, None, address[0] if address else None, code=attempt)
                finally:
                    if attempt is not None:
                        self._attempt_done(attempt)
            else:
                with closing(self.connect()) as conn:
                    row = peers.by_key(conn, channel.remote_static)
                    paused = self._paused(conn)
                if row is not None and paused:
                    channel.send(protocol.encode({"type": "error", "error": f"sync is paused on {self.identity.name}"}))
                elif row is not None:
                    self._session(channel, row, initiator=False, address=None, peer_host=address[0] if address else None)
        except Exception:  # noqa: BLE001 - one bad connection must not stop the listener
            pass
        finally:
            channel.close()

    # Sessions ------------------------------------------------------------------------

    def _session(self, channel: channel_core.Channel, row, *, initiator: bool, address: str | None,
                 peer_host: str | None = None) -> protocol.Result | None:
        """Run one session. Answering is never refused for being busy: two devices that dial each
        other at the same moment both finish (merging is idempotent), instead of both failing."""
        device_id = row["device_id"]
        state = self._state(device_id)
        state.status = "syncing"
        try:
            result = protocol.Session(channel, self.connect, self.identity, row, initiator=initiator, port=self.port).run()
        except Exception as exc:  # noqa: BLE001
            state.status, state.error = "error", _short(exc)
            with closing(self.connect()) as conn:
                with transaction(conn, immediate=True):
                    peers.record_sync(conn, device_id, error=state.error)
            return None
        state.status, state.error, state.online, state.backoff = "idle", None, True, 0.0
        address = address or _join(peer_host, result.port)
        with closing(self.connect()) as conn:
            with transaction(conn, immediate=True):
                peers.record_sync(conn, device_id, acked=result.acked, theirs=result.theirs, address=address,
                                  sent=result.sent_bytes, received=result.received_bytes, synced=True)
                if result.name and result.name != row["name"]:
                    conn.execute("UPDATE peers SET name=? WHERE device_id=?", (result.name, device_id))
        return result

    def sync_with(self, row) -> protocol.Result | None:
        """Connect to a paired device (trying each address it may be at) and sync; None if that failed."""
        assert self.identity is not None
        device_id = row["device_id"]
        with self._lock:
            if device_id in self._busy:
                return None
            self._busy.add(device_id)
        try:
            return self._dial(row)
        finally:
            with self._lock:
                self._busy.discard(device_id)

    def _dial(self, row) -> protocol.Result | None:
        state = self._state(row["device_id"])
        state.last_try = self.clock()
        errors = []
        for address in self._candidates(row):
            try:
                host, port = peers.split_address(address)
                sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
            except (OSError, peers.PeersError) as exc:
                errors.append(_short(exc) if not isinstance(exc, peers.PeersError) else str(exc))
                continue
            sock.settimeout(IO_TIMEOUT)
            try:
                channel = channel_core.connect(sock, private_key=self.identity.private_key, peer_public_key=bytes(row["public_key"]))
            except channel_core.ChannelError as exc:
                errors.append("the other device does not recognise this one; pair again" if exc.reason == "connection closed"
                              else exc.reason)
                continue
            try:
                return self._session(channel, row, initiator=True, address=address)
            finally:
                channel.close()
        state.online = False
        state.status = "offline" if not errors or all(error == "not reachable" for error in errors) else "error"
        state.error = errors[-1] if errors else "no address known; enter one"
        state.backoff = min(MAX_BACKOFF, max(INTERVAL, state.backoff * 2))
        state.next_try = self.clock() + state.backoff
        return None

    def _candidates(self, row) -> list[str]:
        found: list[str] = []
        if row["address"]:
            found.append(row["address"])
        if self.discovery is not None:
            for item in self.discovery.seen():
                if item.device_id == row["device_id"]:
                    found.append(f"{item.host}:{item.port}" if ":" not in item.host else f"[{item.host}]:{item.port}")
        if row["last_address"]:
            found.append(row["last_address"])
        return list(dict.fromkeys(found))

    # Scheduler -----------------------------------------------------------------------

    def _schedule_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(TICK)
            self._wake.clear()
            if self._stop.is_set() or self.identity is None:
                continue
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the scheduler keeps running
                pass

    def tick(self) -> None:
        now = self.clock()
        with closing(self.connect()) as conn:
            if self._paused(conn):
                return
            head = conn.execute("SELECT COALESCE(MAX(seq),0) FROM op_log").fetchone()[0]
            rows = peers.paired(conn)
        for row in rows:
            state = self._state(row["device_id"])
            pending = head > row["acked_seq"]
            due = state.wanted or ((pending or now - state.last_try >= INTERVAL) and now >= state.next_try)
            if not due or row["device_id"] in self._busy:
                continue
            state.wanted = False
            self.sync_with(row)


def _join(host: str | None, port: int | None) -> str | None:
    if not host or not port:
        return None
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def from_environment(db_path: Path, keys: keys_core.Keys) -> SyncService | None:
    """The service as configured by ``KITTY_SYNC`` (``off`` disables it) and ``KITTY_SYNC_PORT``."""
    if os.environ.get("KITTY_SYNC", "").strip().lower() in {"off", "0", "false", "no"}:
        return None
    raw = os.environ.get("KITTY_SYNC_PORT", "").strip()
    port = int(raw) if raw.isdigit() and 0 <= int(raw) <= 65535 else None
    discovery = os.environ.get("KITTY_SYNC_DISCOVERY", "").strip().lower() not in {"off", "0", "false", "no"}
    return SyncService(db_path, keys, port=port, discovery=discovery)
