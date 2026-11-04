"""LAN discovery: TXT/instance-name validation, fake-zeroconf events, one real mDNS run."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pytest

from kitty.core.sync import discovery as d

ME = "a" * 32
OTHER = "0123456789abcdef0123456789abcdef"
THIRD = "fedcba9876543210fedcba9876543210"


def props(**over: Any) -> dict[bytes, bytes | None]:
    base = {"v": "1", "id": OTHER, "name": "Desk", "pair": "0", "fp": "0011223344556677"}
    base.update(over)
    return {k.encode(): (None if v is None else str(v).encode()) for k, v in base.items()}


def test_instance_name_is_dns_safe_and_unique_per_device() -> None:
    assert d.instance_name("My Laptop!", OTHER) == "My-Laptop-01234567"
    assert d.instance_name("ÄÖ", OTHER) == "kitty-01234567"
    assert d.instance_name("x" * 100, OTHER) == "x" * 40 + "-01234567"
    assert d.instance_name("laptop", OTHER) != d.instance_name("laptop", THIRD)


def test_build_and_parse_properties_round_trip() -> None:
    built = d.build_properties(OTHER, "Desk", True, "0011223344556677")
    assert built == {"v": "1", "id": OTHER, "name": "Desk", "pair": "1", "fp": "0011223344556677"}
    parsed = d.parse_properties({k.encode(): v.encode() for k, v in built.items()})
    assert parsed == d.Announcement(OTHER, "Desk", True, "0011223344556677")
    assert d.parse_properties(props(fp="")) is not None


@pytest.mark.parametrize(
    "over",
    [
        {"v": "2"},
        {"v": None},
        {"id": "ABCDEF" * 5 + "AB"},
        {"id": "abc"},
        {"name": ""},
        {"name": "x" * 65},
        {"name": "bad\x00name"},
        {"pair": "yes"},
        {"fp": "123"},
        {"fp": "G" * 16},
    ],
)
def test_parse_properties_rejects_malformed(over: dict[str, Any]) -> None:
    assert d.parse_properties(props(**over)) is None


def test_parse_properties_rejects_invalid_utf8_key() -> None:
    assert d.parse_properties({b"\xff": b"1", **props()}) is None


def test_best_host_and_port_validation() -> None:
    assert d.best_host(["fe80::1%eth0", "192.168.1.5"]) == "192.168.1.5"
    assert d.best_host(["2001:db8::1", "junk"]) == "2001:db8::1"
    assert d.best_host(["0.0.0.0", "nope"]) is None
    assert d.valid_port(8080) and not d.valid_port(0) and not d.valid_port(70000)
    assert not d.valid_port(True) and not d.valid_port("80")


class Change(Enum):
    Added = 1
    Updated = 2
    Removed = 3


@dataclass
class FakeInfo:
    properties: dict[bytes, bytes | None]
    port: int
    addrs: list[str]
    name: str = ""

    def parsed_scoped_addresses(self) -> list[str]:
        return self.addrs


@dataclass
class FakeZeroconf:
    infos: dict[str, FakeInfo] = field(default_factory=dict)
    registered: list[Any] = field(default_factory=list)
    updated: list[Any] = field(default_factory=list)
    unregistered: list[Any] = field(default_factory=list)
    closed: int = 0
    callback: Any = None

    def register_service(self, info: Any) -> None:
        self.registered.append(info)

    def update_service(self, info: Any) -> None:
        self.updated.append(info)

    def unregister_service(self, info: Any) -> None:
        self.unregistered.append(info)

    def close(self) -> None:
        self.closed += 1

    def browse(self, service_type: str, callback: Any) -> object:
        assert service_type == d.SERVICE
        self.callback = callback
        return object()

    def get_service_info(self, service_type: str, name: str, timeout: int) -> FakeInfo | None:
        return self.infos.get(name)

    def emit(self, name: str, change: Change) -> None:
        self.callback(zeroconf=self, service_type=d.SERVICE, name=name, state_change=change)


def started(fake: FakeZeroconf) -> d.Discovery:
    disc = d.Discovery(device_id=ME, name="Mine", port=8123, interfaces=["192.168.1.2"],
                       zeroconf_factory=lambda **_: fake)
    disc.start()
    assert disc.error is None
    return disc


def test_fake_add_update_remove_and_self_exclusion() -> None:
    fake = FakeZeroconf()
    disc = started(fake)
    info = fake.registered[0]
    assert info.port == 8123 and info.name == f"Mine-{ME[:8]}.{d.SERVICE}"
    assert info.properties[b"id"] == ME.encode()

    fake.infos["self"] = FakeInfo(props(id=ME), 8123, ["192.168.1.2"])
    fake.infos["desk"] = FakeInfo(props(), 9000, ["2001:db8::2", "192.168.1.9"])
    fake.infos["bad"] = FakeInfo(props(id="nothex"), 9000, ["192.168.1.10"])
    fake.infos["badport"] = FakeInfo(props(id=THIRD), 0, ["192.168.1.11"])
    for name in ("self", "desk", "bad", "badport"):
        fake.emit(name, Change.Added)
    [entry] = disc.seen()
    assert (entry.device_id, entry.name, entry.host, entry.port, entry.pairing) == (
        OTHER, "Desk", "192.168.1.9", 9000, False)
    assert entry.fingerprint == "0011223344556677"

    time.sleep(0.01)
    fake.infos["desk"] = FakeInfo(props(pair="1", name="Desk 2"), 9001, ["2001:db8::2"])
    fake.infos["lap"] = FakeInfo(props(id=THIRD, name="Lap"), 9002, ["192.168.1.12"])
    fake.emit("lap", Change.Added)
    time.sleep(0.01)
    fake.emit("desk", Change.Updated)
    first, second = disc.seen()
    assert (first.name, first.host, first.port, first.pairing) == ("Desk 2", "2001:db8::2", 9001, True)
    assert second.device_id == THIRD

    fake.emit("desk", Change.Removed)
    assert [s.device_id for s in disc.seen()] == [THIRD]


def test_update_reannounces_and_close_is_idempotent() -> None:
    fake = FakeZeroconf()
    disc = started(fake)
    disc.update(pairing=True, name="Renamed", port=9999)
    info = fake.updated[-1]
    assert info.properties[b"pair"] == b"1" and info.properties[b"name"] == b"Renamed"
    assert info.port == 9999 and info.name == fake.registered[0].name
    disc.close()
    disc.close()
    assert fake.closed == 1 and len(fake.unregistered) == 1
    assert disc.seen() == []


def test_unchanged_device_stays_until_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeZeroconf()
    disc = started(fake)
    fake.infos["desk"] = FakeInfo(props(), 9000, ["192.168.1.9"])
    fake.emit("desk", Change.Added)
    days_later = time.time() + 7 * 24 * 3600
    monkeypatch.setattr(d.time, "time", lambda: days_later)
    assert [s.device_id for s in disc.seen()] == [OTHER]
    fake.emit("desk", Change.Removed)  # zeroconf's record expiry or goodbye
    assert disc.seen() == []


def test_start_failure_sets_error_and_stays_inert() -> None:
    def broken(**_: Any) -> Any:
        raise OSError("no multicast")

    disc = d.Discovery(device_id=ME, name="Mine", port=8123, zeroconf_factory=broken)
    disc.start()
    assert disc.error is not None and "no multicast" in disc.error
    assert disc.seen() == []
    disc.update(pairing=True)
    disc.close()


def test_constructor_validates_own_identity() -> None:
    with pytest.raises(ValueError):
        d.Discovery(device_id="xyz", name="n", port=1)
    with pytest.raises(ValueError):
        d.Discovery(device_id=ME, name="n", port=0)


def test_real_two_instances_see_each_other() -> None:
    one = d.Discovery(device_id=OTHER, name="Synthetic One", port=47001, fingerprint="0" * 16)
    two = d.Discovery(device_id=THIRD, name="Synthetic Two", port=47002)
    try:
        one.start()
        if one.error:
            pytest.skip(f"multicast unavailable: {one.error}")
        two.start()
        if two.error:
            pytest.skip(f"multicast unavailable: {two.error}")
        deadline = time.time() + 15
        while time.time() < deadline:
            if [s.device_id for s in one.seen()] == [THIRD] and [s.device_id for s in two.seen()] == [OTHER]:
                break
            time.sleep(0.2)
        else:
            pytest.skip("multicast not delivered on this host within 15 s")
        assert one.seen()[0].port == 47002 and two.seen()[0].fingerprint == "0" * 16
        two.update(pairing=True)
        deadline = time.time() + 10
        while time.time() < deadline and not one.seen()[0].pairing:
            time.sleep(0.2)
        assert one.seen()[0].pairing
        two.close()
        deadline = time.time() + 10
        while time.time() < deadline and one.seen():
            time.sleep(0.2)
        assert one.seen() == []
    finally:
        one.close()
        two.close()
