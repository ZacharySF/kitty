"""kitty sync LAN discovery (platform design Q16): find this person's other kitty servers.

Each kitty server that offers sync announces itself as a DNS-SD service of type
``_kitty-sync._tcp.local.`` over multicast DNS and browses for the same type.
Discovery is only a convenience for picking a peer: it carries no secrets and is not
trusted. Authentication happens later in the Noise channel (:mod:`.channel`), which
checks the peer's static key, so a spoofed announcement can at worst show a bogus
entry in the device list.

Why mDNS/DNS-SD rather than a custom UDP broadcast:

* it is the standard way LAN services find each other; no port of our own to open;
* python-zeroconf binds 5353 with ``SO_REUSEADDR``/``SO_REUSEPORT``, so it coexists
  with avahi (or macOS mDNSResponder) already running on the machine;
* announcements are inspectable with ordinary tools, e.g.
  ``avahi-browse -r _kitty-sync._tcp`` or ``dns-sd -B _kitty-sync._tcp``.

TXT properties: ``v=1``, ``id`` (32 lowercase hex device id), ``name`` (device name),
``pair`` (``1`` while the device shows a pairing code, else ``0``) and ``fp``
(16 lowercase hex: sha256 prefix of its sync public key, may be empty). The instance
name is a DNS-safe label from the device name plus the first 8 hex of the device id,
so two devices called "laptop" do not collide. Everything read from the network is
validated (version, lengths, hex, port range); malformed announcements are ignored.

Threading: python-zeroconf's sync API runs its own threads. Browser callbacks resolve
the service and update a dict under a lock; :meth:`Discovery.seen` only copies that
dict, so it is cheap and safe from any thread. Entries vanish when their service is
removed: a goodbye packet, or record expiry when zeroconf's cache stops being
refreshed, both reported as ``Removed``. There is deliberately no age limit of our
own: zeroconf refreshes cached records silently, so a device that stays online and
unchanged gets no new event and an age cut-off would wrongly hide it.

Failure policy: :meth:`Discovery.start` never raises for network problems (no
multicast, no interface, port busy); it records ``error`` and stays inert, so a
server without working mDNS still runs and peers can be added by address.

Importing this module has no side effects; nothing touches the network before
:meth:`Discovery.start`.
"""
from __future__ import annotations

import contextlib
import ipaddress
import re
import socket
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

SERVICE = "_kitty-sync._tcp.local."
VERSION = "1"
NAME_MAX = 64
RESOLVE_TIMEOUT_MS = 3000

_DEVICE_ID_RE = re.compile(r"[0-9a-f]{32}")
_FP_RE = re.compile(r"(?:[0-9a-f]{16})?")
_LABEL_UNSAFE_RE = re.compile(r"[^A-Za-z0-9-]+")


@dataclass(frozen=True)
class Seen:
    """One other device currently visible on the LAN."""

    device_id: str
    name: str
    host: str
    port: int
    pairing: bool
    fingerprint: str
    seen_at: float


@dataclass(frozen=True)
class Announcement:
    """Validated TXT content of an announcement."""

    device_id: str
    name: str
    pairing: bool
    fingerprint: str


def instance_name(name: str, device_id: str) -> str:
    """DNS-safe instance label: sanitized name (≤ 40 chars) + ``-`` + 8 id chars."""
    base = _LABEL_UNSAFE_RE.sub("-", name).strip("-")[:40].strip("-") or "kitty"
    return f"{base}-{device_id[:8]}"


def build_properties(device_id: str, name: str, pairing: bool, fingerprint: str) -> dict[str, str]:
    """TXT properties we announce."""
    return {
        "v": VERSION,
        "id": device_id,
        "name": name[:NAME_MAX],
        "pair": "1" if pairing else "0",
        "fp": fingerprint,
    }


def _text(raw: object) -> str | None:
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return raw if isinstance(raw, str) else None


def parse_properties(props: Mapping[Any, Any]) -> Announcement | None:
    """Validate TXT properties (bytes or str keys/values); ``None`` when malformed."""
    fields: dict[str, str] = {}
    for key, value in props.items():
        k, v = _text(key), _text(value)
        if k is None:
            return None
        fields[k] = v if v is not None else ""
    if fields.get("v") != VERSION:
        return None
    device_id = fields.get("id", "")
    fingerprint = fields.get("fp", "")
    pair = fields.get("pair", "0")
    name = fields.get("name", "").strip()
    if not _DEVICE_ID_RE.fullmatch(device_id) or not _FP_RE.fullmatch(fingerprint):
        return None
    if pair not in ("0", "1") or not name or len(name) > NAME_MAX:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return None
    return Announcement(device_id, name, pair == "1", fingerprint)


def valid_port(port: object) -> bool:
    """True for an int in 1..65535."""
    return isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536


def best_host(addresses: list[str]) -> str | None:
    """First usable IPv4 address, else first IPv6 (no brackets); ``None`` when none."""
    v4: list[str] = []
    v6: list[str] = []
    for text in addresses:
        try:
            addr = ipaddress.ip_address(text.split("%", 1)[0])
        except ValueError:
            continue
        if addr.is_unspecified or addr.is_multicast:
            continue
        (v4 if addr.version == 4 else v6).append(text)
    if v4:
        return v4[0]
    return v6[0] if v6 else None


def _info_addresses(info: Any) -> list[str]:
    parsed = getattr(info, "parsed_scoped_addresses", None)
    if callable(parsed):
        return list(parsed())
    return [socket.inet_ntoa(a) for a in getattr(info, "addresses", []) if len(a) == 4]


class Discovery:
    """Announce this device and track the others; see the module docstring."""

    def __init__(
        self,
        *,
        device_id: str,
        name: str,
        port: int,
        fingerprint: str = "",
        interfaces: list[str] | None = None,
        zeroconf_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not _DEVICE_ID_RE.fullmatch(device_id):
            raise ValueError("device_id must be 32 lowercase hex characters")
        if not _FP_RE.fullmatch(fingerprint):
            raise ValueError("fingerprint must be empty or 16 lowercase hex characters")
        if not valid_port(port):
            raise ValueError("port must be in 1..65535")
        self.error: str | None = None
        self._device_id = device_id
        self._name = name.strip()[:NAME_MAX] or "kitty"
        self._port = port
        self._fingerprint = fingerprint
        self._pairing = False
        self._interfaces = interfaces
        self._factory = zeroconf_factory
        self._zc: Any = None
        self._browser: Any = None
        self._info: Any = None
        self._lock = threading.Lock()
        self._seen: dict[str, Seen] = {}  # keyed by service name
        self._closed = False

    def start(self) -> None:
        """Register our service and browse; network failures set ``error`` instead."""
        if self._zc is not None or self._closed:
            return
        try:
            from zeroconf import ServiceBrowser, Zeroconf

            if self._factory is not None:
                zc = self._factory(interfaces=self._interfaces)
            elif self._interfaces is not None:
                zc = Zeroconf(interfaces=self._interfaces)
            else:
                zc = Zeroconf()
            self._zc = zc
            self._info = self._build_info()
            zc.register_service(self._info)
            if isinstance(zc, Zeroconf):
                self._browser = ServiceBrowser(zc, SERVICE, handlers=[self._on_change])
            else:  # test doubles deliver browse events through on_change directly
                self._browser = zc.browse(SERVICE, self._on_change)
        except Exception as exc:  # noqa: BLE001 - any network failure makes discovery inert
            self.error = f"mDNS unavailable: {exc}" if str(exc) else f"mDNS unavailable: {type(exc).__name__}"
            self._shutdown()

    def update(self, *, name: str | None = None, pairing: bool | None = None, port: int | None = None) -> None:
        """Re-announce with a changed name, pairing flag or port."""
        if name is not None:
            self._name = name.strip()[:NAME_MAX] or "kitty"
        if pairing is not None:
            self._pairing = pairing
        if port is not None:
            if not valid_port(port):
                raise ValueError("port must be in 1..65535")
            self._port = port
        if self._zc is None or self._info is None:
            return
        info = self._build_info(service_name=self._info.name)
        try:
            self._zc.update_service(info)
            self._info = info
        except Exception as exc:  # noqa: BLE001
            self.error = f"mDNS update failed: {exc}"

    def seen(self) -> list[Seen]:
        """Other devices currently visible, freshest first."""
        with self._lock:
            items = list(self._seen.values())
        best: dict[str, Seen] = {}
        for item in items:
            if item.device_id not in best or item.seen_at > best[item.device_id].seen_at:
                best[item.device_id] = item
        return sorted(best.values(), key=lambda s: s.seen_at, reverse=True)

    def close(self) -> None:
        """Unregister and stop; idempotent."""
        self._closed = True
        self._shutdown()

    def _shutdown(self) -> None:
        zc, browser, info = self._zc, self._browser, self._info
        self._zc = self._browser = self._info = None
        with self._lock:
            self._seen.clear()
        # Best-effort teardown: a dead socket must not stop the remaining steps.
        if browser is not None and hasattr(browser, "cancel"):
            with contextlib.suppress(Exception):
                browser.cancel()
        if zc is not None and info is not None:
            with contextlib.suppress(Exception):
                zc.unregister_service(info)
        if zc is not None:
            with contextlib.suppress(Exception):
                zc.close()

    def _build_info(self, service_name: str | None = None) -> Any:
        from zeroconf import ServiceInfo

        name = service_name or f"{instance_name(self._name, self._device_id)}.{SERVICE}"
        return ServiceInfo(
            SERVICE,
            name,
            port=self._port,
            properties=build_properties(self._device_id, self._name, self._pairing, self._fingerprint),
            server=f"kitty-{self._device_id[:12]}.local.",
            parsed_addresses=self._local_addresses(),
        )

    def _local_addresses(self) -> list[str]:
        if self._interfaces:
            return [a for a in self._interfaces if best_host([a]) is not None]
        import ifaddr  # zeroconf's own dependency for enumerating adapters

        found: list[str] = []
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                text = ip.ip if isinstance(ip.ip, str) else ip.ip[0]
                try:
                    addr = ipaddress.ip_address(text)
                except ValueError:
                    continue
                if not (addr.is_loopback or addr.is_link_local) and text not in found:
                    found.append(text)
        return found

    def _on_change(self, zeroconf: Any, service_type: str, name: str, state_change: Any) -> None:
        kind = getattr(state_change, "name", str(state_change))
        if kind == "Removed":
            with self._lock:
                self._seen.pop(name, None)
            return
        try:
            info = zeroconf.get_service_info(service_type, name, RESOLVE_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - unresolvable peers are simply not shown
            info = None
        entry = self._entry(info) if info is not None else None
        with self._lock:
            if entry is None:
                self._seen.pop(name, None)
            else:
                self._seen[name] = entry

    def _entry(self, info: Any) -> Seen | None:
        announcement = parse_properties(getattr(info, "properties", None) or {})
        if announcement is None or announcement.device_id == self._device_id:
            return None
        port = getattr(info, "port", None)
        host = best_host(_info_addresses(info))
        if not valid_port(port) or host is None:
            return None
        return Seen(
            device_id=announcement.device_id,
            name=announcement.name,
            host=host,
            port=port,
            pairing=announcement.pairing,
            fingerprint=announcement.fingerprint,
            seen_at=time.time(),
        )
