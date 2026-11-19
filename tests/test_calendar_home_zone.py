"""The personal calendar's zone: the one Rika reads "6 pm" and "tomorrow" in.

Migration 010 seeds it as UTC, so on a computer elsewhere Rika wrote "6 pm" as 18:00 UTC and
the week grid, which draws in the computer's zone, showed it at noon. Opening the workspace
gives a still-UTC calendar the computer's zone. Every zone below is synthetic setup.
"""
from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

from kitty.core import calendar
from kitty.core.rika import tools
from kitty.core.rika.tools import Context, validate


def link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)
    return path


def personal_zone(conn):
    return conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"]


@pytest.mark.parametrize("tz", ["America/Chicago", ":America/Chicago", "/usr/share/zoneinfo/America/Chicago"])
def test_tz_names_the_zone_first(tmp_path, tz):
    localtime = link(tmp_path / "etc" / "localtime", "/usr/share/zoneinfo/Asia/Tokyo")
    assert calendar.device_zone({"TZ": tz}, localtime, tmp_path / "none") == "America/Chicago"


def test_a_sandbox_link_in_front_of_the_system_link_still_names_the_zone(tmp_path):
    """An AppImage's sandbox links /etc/localtime to the host's, which links into zoneinfo."""
    host = link(tmp_path / "host-etc" / "localtime", "/etc/zoneinfo/America/Matamoros")
    localtime = link(tmp_path / "etc" / "localtime", str(host))
    assert calendar.device_zone({}, localtime, tmp_path / "none") == "America/Matamoros"


def test_a_relative_link_and_debians_timezone_file(tmp_path):
    localtime = link(tmp_path / "etc" / "localtime", "../usr/share/zoneinfo/Europe/Berlin")
    assert calendar.device_zone({}, localtime, tmp_path / "none") == "Europe/Berlin"
    plain = tmp_path / "plain" / "localtime"
    plain.parent.mkdir()
    plain.write_bytes(b"TZif")
    (tmp_path / "timezone").write_text("Asia/Kolkata\n")
    assert calendar.device_zone({}, plain, tmp_path / "timezone") == "Asia/Kolkata"


def test_an_unknown_zone_is_not_a_reading(tmp_path):
    localtime = link(tmp_path / "etc" / "localtime", "/usr/share/zoneinfo/Not/AZone")
    assert calendar.device_zone({"TZ": "EST+5EDT"}, localtime, tmp_path / "none") is None
    assert calendar.device_zone({"TZ": "Not/AZone"}, tmp_path / "missing", tmp_path / "none") is None


def test_a_still_utc_calendar_takes_the_device_zone_and_a_chosen_one_stays(workspace):
    conn = workspace.conn
    revision = conn.execute("SELECT revision FROM calendar_calendars WHERE id='personal'").fetchone()[0]
    assert calendar.adopt_device_zone(conn, "UTC") is None
    assert personal_zone(conn) == "UTC"
    assert calendar.adopt_device_zone(conn, None) is None
    assert calendar.adopt_device_zone(conn, "America/Chicago") == "America/Chicago"
    assert personal_zone(conn) == "America/Chicago"
    # An open calendar editor holding the old revision must reload rather than write UTC back.
    assert conn.execute("SELECT revision FROM calendar_calendars WHERE id='personal'").fetchone()[0] == revision + 1
    assert calendar.adopt_device_zone(conn, "Europe/Berlin") is None
    assert personal_zone(conn) == "America/Chicago"


def test_rika_writes_six_pm_as_six_pm_on_the_device_clock(workspace):
    conn = workspace.conn
    calendar.adopt_device_zone(conn, "America/Chicago")
    app = SimpleNamespace(settings=workspace.settings, modules=("workspace",), mutation_lock=threading.Lock())
    tool = tools.registry()["create_event"]
    args = {"title": "Engineering exam", "start": "2026-11-24T18:00"}
    validate(tool.parameters, args)
    result = tool.run(Context(app, conn), args)
    assert result.data["start"] == "2026-11-24T18:00:00-06:00"
