"""The opportunities routes answered by rika-server (server/src/opportunities) against
`kitty.core.opportunities`: one scenario over every moved route, with the refusals between,
run through the front and through the Python core on two copies of the same workspace at
the same clock readings. Every answer, the rows left behind and the content events recorded
must be the same (random ids aside). `canonical_url` and `valid_date` are compared over
tables of awkward values. Settings, discovery and the sources stay on Python, behind the front."""
from __future__ import annotations

import json
import re
import shutil
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from terminal_support import front_site

from kitty.core import calendar, record_events
from kitty.core import opportunities as core
from kitty.core import opportunity_sources as providers
from kitty.dates import ISO_FORMAT
from kitty.db import connect
from kitty.web_server import APIError, WebApplication

UTC = timezone.utc
BASE = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
ROOT = "/api/opportunities"
HEX = re.compile(r"[0-9a-f]{32}")
TABLES = ("opportunities", "opportunity_evidence", "opportunity_identities", "opportunity_projects", "opportunity_project_items",
          "opportunity_sources", "opportunity_settings", "calendar_events", "calendar_history")
FAILED = {"error": "Local operation failed; inspect CLI diagnostics"}
URLS = json.loads((Path(__file__).parent / "fixtures" / "opportunity_urls.json").read_text())
DATES = (
    "2027-01-11", "2027-01-11T12:00:00Z", "2027-01-11 12:00-06:00", "2027-01-11T12:00:00.5+05:30", "20270111T1200Z", "2027-02-30", "tomorrow",
    "2027-01-11T25:00Z", "2027-01-11T12:00:00", "20270111", "2027-W02-1", "2027-W02-1T10:00+01:00", " 2027-01-11 ", "", None, 7, True, ["2027-01-11"],
    "\uff12\uff10\uff12\uff17-01-11", "2027-01-11T12:00:00+00:00:30", "2027-01-11T12:00:00-00:00", "0001-01-01", "0000-01-01", "9999-12-31", "2027-1-1",
    "2027-01-11T12:00:00,123456789+01:00", "2027-01-11T12Z", "2027-01-11TZ", "2027-01-11T12:00:00+24:00", "2027-01-11T12:00:00+23:59:59.999999",
    "2027-01-11t12:00:00z", "x" * 101, "2027-01-11T12:00:00ZZ", "2027-01-11T12:00:00Z+00:00", "2027-01-11T23:59:60Z", "2027-01-11T12:00:00+0530",
    "2027-01-11T12:00:00+05", "2027-01-11T12:00+05:30:15.5", "2027-01-11T12:00:00.123456789Z", "2027-01-11T12:00 +01:00", "2027-01-11\x00",
    "2027-13-01", "2027-01-11T", "2027-01-11T12:00:00+", "2027-01-11T12:00:00-06", "\u00a02027-01-11T12:00:00Z\u2003", "0001-01-01T00:00:00+23:59",
)


class Masked:
    """Random ids by order of first appearance, so two runs compare."""

    def __init__(self):
        self.seen: dict[str, str] = {}

    def __call__(self, value):
        if isinstance(value, dict):
            return {key: self(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, str):
            return HEX.sub(lambda found: self.seen.setdefault(found[0], f"id-{len(self.seen)}"), value)
        return value


@contextmanager
def clock(now):
    """The Python core at one clock reading, as `KITTY_TEST_NOW` fixes the front's."""
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    with patch.object(core, "datetime", Fixed), patch.object(core, "now_iso", lambda: now.strftime(ISO_FORMAT)), \
            patch.object(calendar, "now", lambda: now.isoformat()):
        yield


def rows(db_path, mask):
    with closing(connect(db_path)) as conn:
        dump = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in TABLES}
        events = [tuple(row) for row in conn.execute("SELECT section,entity,entity_id,action,route FROM record_events ORDER BY seq")]
    return mask(dump), events


class Front:
    """The front on one workspace, restarted when the clock reading changes."""

    def __init__(self, settings):
        self.settings, self.now, self.context, self.site = settings, None, None, None

    def __call__(self, path, body, now, query):
        if now != self.now:
            self.close()
            self.context = front_site(self.settings, {"KITTY_TEST_NOW": now.isoformat()})
            self.site, self.now = self.context.__enter__(), now
        return self.site.request(path + ("?" + urlencode(query) if query else ""), body)

    def close(self):
        if self.context is not None:
            self.context.__exit__(None, None, None)
            self.context = None


class Reference:
    """The routes as `kitty.web_opportunities` answered them, and the events `WebApplication.mutate` recorded."""

    def __init__(self, db_path):
        self.conn = connect(db_path)
        self.events = []

    def get(self, path, query):
        conn = self.conn
        if path == ROOT:
            items = core.list_items(conn, query)
            config = core.settings(conn)
            return {"items": items, "total": len(items), "sources": providers.sources(conn), "profile": config["profile"],
                    "schedule": {key: value for key, value in config.items() if key != "profile"}, "statuses": list(core.STATUSES)}
        if query:
            core.fail("Detail does not accept query parameters")
        return core.detail(conn, int(path.removeprefix(ROOT + "/")))

    def post(self, path, body):
        conn = self.conn
        if path == ROOT:
            return core.create(conn, body)
        if path == ROOT + "/import":
            if set(body) != {"items"}:
                core.fail("Import requires an items array")
            return core.import_items(conn, body["items"])
        if path == ROOT + "/favorites":
            if set(body) != {"selections", "favorite"}:
                core.fail("Expected selections and favorite")
            return core.favorites(conn, body["selections"], body["favorite"])
        match = re.fullmatch(r"/api/opportunities/([1-9][0-9]*)(?:/(delete|project|items|project-details|calendar))?", path)
        ident, action = int(match[1]), match[2]
        if action == "calendar":
            if body:
                core.fail("Calendar export expects an empty object")
            return {"event": core.export_deadline(conn, ident)}
        if action == "delete":
            if set(body) != {"revision"}:
                core.fail("Deletion requires revision")
            return core.remove(conn, ident, body["revision"])
        if action == "project":
            if set(body) != {"revision"}:
                core.fail("Project creation requires opportunity revision")
            return core.project(conn, ident, body)
        if action == "items":
            return core.project_item(conn, ident, body)
        if action == "project-details":
            return core.update_project(conn, ident, body)
        return core.update(conn, ident, {key: value for key, value in body.items() if key != "revision"}, body.get("revision"))

    def __call__(self, path, body, now, query):
        with clock(now):
            try:
                if body is None:
                    answer = self.get(path, query)
                else:
                    answer = self.post(path, body)
                    event = record_events.classify(path, body, answer, self.conn)
                    if event is not None:
                        self.events.append((*event, path))
            except APIError as refused:
                return refused.status, {"error": str(refused)}
            except Exception:  # what `RequestHandler._dispatch` answers for anything else
                return 500, FAILED
        return 200, json.loads(json.dumps(answer))


def seed(conn):
    """A profile to rank against and what a monitoring run found, with its evidence."""
    conn.execute("UPDATE opportunity_settings SET profile=? WHERE id=1", (json.dumps(
        {"major": "Engineering", "track": "quant", "interests": ["hardware", "STRA\u1e9eE", ""], "locations": ["college station", "T\u014dky\u014d"],
         "prefer_remote": True}),))
    source = {"category": "hackathon", "timezone": "UTC", "description": "", "location": "", "remote": None, "start": None, "end": None,
              "deadline": None, "availability": "unknown", "evidence_url": "https://mlh.example.invalid/events"}
    found = [
        {**source, "title": "Synthetic hardware hack", "url": "https://hack.example.invalid/?utm_source=mlh", "provider_id": "p1",
         "location": "College Station, Texas", "start": "2026-11-07T15:00:00Z", "end": "2026-11-08T22:00:00Z",
         "extra": {"ratio": 0.5, "count": 3, "label": "\u00e9\u4e16"}},
        {**source, "title": "Finished cycle", "url": "https://finished.example.invalid/", "provider_id": "p2", "availability": "closed",
         "start": "2027-01-01", "description": "Engineering strasse"},
        {**source, "title": "Long past", "url": "https://past.example.invalid/", "provider_id": "p3", "remote": True,
         "start": "2001-11-07T15:00:00+00:00", "end": "2001-11-08T22:00:00+00:00"},
        {**source, "title": "Running now", "url": "https://now.example.invalid/", "provider_id": "p4", "timezone": "Asia/Tokyo",
         "start": "2026-10-13", "end": "2026-10-14", "location": "T\u014cKY\u014c", "category": "quant"},
        {**source, "title": "Deadline today in Chicago", "url": "https://today.example.invalid/", "provider_id": "p5",
         "timezone": "America/Chicago", "deadline": "2026-10-14"},
        {**source, "title": "Deadline passed at an instant", "url": "", "provider_id": "p6", "deadline": "2026-10-14T03:59:59-05:00"},
    ]
    core.upsert_discovered(conn, "mlh", found, "2026-10-14T08:00:00+00:00")
    core.upsert_discovered(conn, "devpost", [{**found[0], "url": "https://hack.example.invalid/", "evidence_url": "https://devpost.example.invalid/"}],
                           "2026-10-14T08:30:00+00:00")


def scenario(call):
    """Every moved route, with the refusals between: returns each (path, status, answer) in order."""
    answers = []

    def do(path, body=None, now=BASE, expect=200, **query):
        status, answer = call(path, body, now, query)
        assert status == expect, (path, body, answer)
        answers.append((path, status, answer))
        return answer

    listed = do(ROOT)
    hack = next(item for item in listed["items"] if item["title"] == "Synthetic hardware hack")
    past = next(item for item in listed["items"] if item["title"] == "Long past")

    # Manual entries.
    plain = do(ROOT, {"title": "  Local quantitative project  "})
    full = do(ROOT, {"title": "Engineering hardware night", "notes": " bring a laptop ", "url": "HTTP://Example.INVALID/Night/?utm_source=x&b=2&a=1#top",
                     "category": " quant ", "description": "Quant workshop \u00e9\u4e16", "location": "College Station", "remote": True,
                     "start": "2027-01-10T18:00:00-06:00", "end": "2027-01-11T02:00:00Z", "deadline": "2027-01-05", "timezone": "America/Chicago",
                     "tags": [" ieee ", "hardware", "ieee", ""], "status": "queued", "favorite": True, "availability": "future"})
    bare = do(ROOT, {"title": "No facts but a deadline", "deadline": None, "remote": None, "url": ""})
    for body, expect in (
        ({}, 400), ({"category": "x"}, 400), ({"title": ""}, 400), ({"title": "   "}, 400), ({"title": "x", "category": " "}, 400),
        ({"title": 7}, 400), ({"title": "x" * 501}, 400), ({"title": "x\x00"}, 400), ({"title": "x", "notes": "n" * 20001}, 400),
        ({"title": "x", "owner": "me"}, 400), ({"title": "x", "id": 4}, 400), ({"title": "x", "status": "job-applied"}, 400),
        ({"title": "x", "status": ["new"]}, 400), ({"title": "x", "availability": "open"}, 400), ({"title": "x", "deadline": "2027-01-01T12:00:00"}, 400),
        ({"title": "x", "deadline": "tomorrow"}, 400), ({"title": "x", "start": 20270101}, 400), ({"title": "x", "remote": "yes"}, 400),
        ({"title": "x", "favorite": 1}, 400), ({"title": "x", "favorite": None}, 400), ({"title": "x", "timezone": "not/a/timezone"}, 400),
        ({"title": "x", "timezone": "utc"}, 400), ({"title": "x", "timezone": ""}, 400), ({"title": "x", "tags": "not a list"}, 400),
        ({"title": "x", "tags": ["t"] * 101}, 400), ({"title": "x", "tags": ["ok", 5]}, 400), ({"title": "x", "tags": ["t" * 101]}, 400),
        ({"title": "x", "url": "javascript:alert(1)"}, 400), ({"title": "x", "url": "https://user:pw@example.invalid/"}, 400),
        ({"title": "x", "url": "https://example.invalid:99999/"}, 400), ({"title": "x", "url": ["https://example.invalid/"]}, 400),
        ({"title": "x", "url": "https://example.invalid/" + "a" * 4000}, 400),
        # The first field that is wrong answers, in the order the fields were sent.
        ({"status": "nope", "title": ""}, 400), ({"title": "", "status": "nope"}, 400), ({"timezone": "nope", "title": "x", "tags": 1}, 400),
        ({"title": "Same page", "url": "https://EXAMPLE.invalid/Night?a=1&utm_medium=y&b=2"}, 409),
        ({"title": "Found already", "url": "https://hack.example.invalid"}, 409),
    ):
        do(ROOT, body, expect=expect)

    # One opportunity.
    do(f"{ROOT}/{full['id']}")
    do(f"{ROOT}/{hack['id']}")
    do(f"{ROOT}/999", expect=404)
    do(f"{ROOT}/{full['id']}", expect=400, x="1")
    do(f"{ROOT}/99999999999999999999", expect=500)

    # Imports: every entry or none.
    for body in ({"items": [{"title": "a"}], "x": 1}, {}, {"items": "no"}, {"items": []}, {"items": [{"title": "a"}, 5]}, {"items": [{"title": "a"}] * 501},
                 {"items": [{"title": "First"}, {"title": "Invalid", "deadline": "tomorrow"}]}, {"items": [{"title": "First"}, {}]}):
        do(f"{ROOT}/import", body, expect=400)
    do(f"{ROOT}/import", {"items": [{"title": "First", "url": "https://twice.example.invalid/?utm_source=x"},
                                    {"title": "Duplicate", "url": "https://twice.example.invalid/"}]}, expect=409)
    imported = do(f"{ROOT}/import", {"items": [{"title": "One", "url": "https://one.example.invalid/a/"}, {"title": "Two", "tags": ["x"], "status": "new"},
                                               {"title": "\u00a0\x1f Three\u2003", "location": "\u3000T\u014cky\u014d\t", "tags": ["\x1c x \x85", "x"]}]})
    one, two, _ = imported["items"]

    # Edits: the facts named become the person's, in sorted order; the identity follows a manual entry's URL only.
    plain = do(f"{ROOT}/{plain['id']}", {"revision": plain["revision"], "notes": " Ask the advisor ", "tags": ["b", "a", "b"], "deadline": "2026-10-20T17:00:00-05:00",
                                         "timezone": "America/Chicago", "status": "applied", "url": "https://plain.example.invalid/x?b=1&a=2"})
    plain = do(f"{ROOT}/{plain['id']}", {"revision": plain["revision"]})
    plain = do(f"{ROOT}/{plain['id']}", {"url": "", "revision": plain["revision"], "remote": False, "availability": "active"})
    hack = do(f"{ROOT}/{hack['id']}", {"revision": hack["revision"], "url": "https://moved.example.invalid/", "title": "Hardware hack, renamed", "deadline": "2026-11-01"})
    for ident, body, expect in (
        (plain["id"], {"revision": plain["revision"] - 1, "notes": "stale"}, 409), (plain["id"], {"notes": "none sent"}, 409),
        (plain["id"], {"revision": str(plain["revision"]), "notes": "text"}, 409), (plain["id"], {"revision": True, "notes": "flag"}, 409),
        (plain["id"], {"revision": float(plain["revision"]), "notes": "float"}, 409), (plain["id"], {"revision": None}, 409),
        (999, {"revision": 1, "notes": "nobody"}, 404), (999, {"revision": 1, "status": "nope"}, 400), (plain["id"], {"revision": 0, "favorite": "yes"}, 400),
        (plain["id"], {"revision": plain["revision"], "title": ""}, 400), (plain["id"], {"revision": plain["revision"], "revisions": 2}, 400),
        (plain["id"], {"revision": plain["revision"], "url": "https://one.example.invalid/a"}, 409),
        (one["id"], {"revision": one["revision"], "url": "https://moved.example.invalid"}, 409),
        (99999999999999999999, {"revision": 1}, 500), (99999999999999999999, {"revision": 1, "title": ""}, 400),
    ):
        do(f"{ROOT}/{ident}", body, expect=expect)
    one = do(f"{ROOT}/{one['id']}", {"revision": one["revision"], "url": "https://one.example.invalid/a/?ref=keep", "favorite": False})

    # Favorites: all of them or none.
    for body in ({}, {"selections": [{"id": one["id"], "revision": one["revision"]}]}, {"selections": [], "favorite": True},
                 {"selections": [{"id": one["id"], "revision": one["revision"]}], "favorite": 1},
                 {"selections": {"id": one["id"], "revision": one["revision"]}, "favorite": True},
                 {"selections": [{"id": one["id"]}], "favorite": True}, {"selections": [{"id": str(one["id"]), "revision": 1}], "favorite": True},
                 {"selections": [{"id": True, "revision": 1}], "favorite": True}, {"selections": [{"id": 1.0, "revision": 1}], "favorite": True},
                 {"selections": [{"id": one["id"], "revision": one["revision"], "x": 1}], "favorite": True}, {"selections": [7], "favorite": True},
                 {"selections": [{"id": one["id"], "revision": one["revision"]}, {"id": one["id"], "revision": one["revision"]}], "favorite": True},
                 {"selections": [{"id": one["id"], "revision": one["revision"]}] * 501, "favorite": True}):
        do(f"{ROOT}/favorites", body, expect=400)
    do(f"{ROOT}/favorites", {"selections": [{"id": one["id"], "revision": one["revision"]}, {"id": two["id"], "revision": 0}], "favorite": True}, expect=409)
    do(f"{ROOT}/favorites", {"selections": [{"id": one["id"], "revision": one["revision"]}, {"id": 999, "revision": 1}], "favorite": True}, expect=404)
    do(f"{ROOT}/favorites", {"selections": [{"id": one["id"], "revision": "1"}], "favorite": True}, expect=409)
    do(f"{ROOT}/{one['id']}")  # nothing changed
    do(f"{ROOT}/favorites", {"selections": [{"id": one["id"], "revision": one["revision"]}, {"id": two["id"], "revision": two["revision"]},
                                            {"id": past["id"], "revision": past["revision"]}, {"id": plain["id"], "revision": plain["revision"]}], "favorite": True})
    two = do(f"{ROOT}/{two['id']}")
    do(f"{ROOT}/favorites", {"selections": [{"id": two["id"], "revision": two["revision"]}], "favorite": False})
    plain, one = do(f"{ROOT}/{plain['id']}"), do(f"{ROOT}/{one['id']}")

    # The project and its items.
    for body in ({}, {"revision": plain["revision"], "title": "x"}):
        do(f"{ROOT}/{plain['id']}/project", body, expect=400)
    do(f"{ROOT}/{plain['id']}/project", {"revision": plain["revision"] + 1}, expect=409)
    do(f"{ROOT}/999/project", {"revision": 1}, expect=404)
    do(f"{ROOT}/{one['id']}/items", {"revision": 1, "title": "No project yet"}, expect=409)
    do(f"{ROOT}/{one['id']}/project-details", {"revision": 1, "title": "No project yet"}, expect=404)
    plain = do(f"{ROOT}/{plain['id']}/project", {"revision": plain["revision"]})
    plain = do(f"{ROOT}/{plain['id']}/project", {"revision": plain["revision"]})  # there already: nothing changes
    items = f"{ROOT}/{plain['id']}/items"
    plain = do(items, {"revision": plain["project"]["revision"], "title": " Read the rules "})
    plain = do(items, {"revision": plain["project"]["revision"], "kind": "milestone", "title": "Prepare demo", "due": "2026-10-19", "notes": " slides ", "done": True})
    plain = do(items, {"revision": plain["project"]["revision"], "kind": "resource", "title": "Rules", "url": "http://Rules.example.invalid/Page/?utm_campaign=x",
                       "due": "2026-10-18T09:00:00+02:00", "done": 1.0})
    plain = do(items, {"revision": plain["project"]["revision"], "kind": "task", "title": "Find a team", "done": 0, "delete": False})
    first = next(item for item in plain["project"]["items"] if item["title"] == "Read the rules")
    rules = next(item for item in plain["project"]["items"] if item["kind"] == "resource")
    revision = plain["project"]["revision"]
    for body, expect in (
        ({"revision": revision, "title": "x", "owner": 1}, 400), ({"revision": revision - 1, "title": "stale"}, 409), ({"title": "no revision"}, 409),
        ({"revision": revision, "kind": "note", "title": "x"}, 400), ({"revision": revision, "kind": ["task"], "title": "x"}, 400),
        ({"revision": revision}, 400), ({"revision": revision, "title": "  "}, 400), ({"revision": revision, "title": 5}, 400),
        ({"revision": revision, "title": "x", "notes": None}, 400), ({"revision": revision, "title": "x", "url": "ftp://example.invalid/"}, 400),
        ({"revision": revision, "title": "x", "due": "soon"}, 400), ({"revision": revision, "title": "x", "due": "2026-10-19T09:00"}, 400),
        ({"revision": revision, "title": "x", "done": "yes"}, 400), ({"revision": revision, "title": "x", "done": 2}, 400),
        ({"revision": revision, "title": "x", "done": None}, 400), ({"revision": revision, "id": "1", "title": "x"}, 400),
        ({"revision": revision, "id": 999, "title": "x"}, 404), ({"revision": revision, "delete": True}, 400),
        # The kind is checked before the title, the title before the notes, then the URL, the date and the flag.
        ({"revision": revision, "kind": "note", "title": ""}, 400), ({"revision": revision, "title": "", "notes": 5}, 400),
        ({"revision": revision, "title": "x", "url": "nope", "due": "soon", "done": 9}, 400), ({"revision": revision, "title": "x", "due": "soon", "done": 9}, 400),
    ):
        do(items, body, expect=expect)
    do(f"{ROOT}/999/items", {"revision": 1, "title": "x"}, expect=404)
    plain = do(items, {"revision": revision, "id": first["id"], "done": True, "notes": "done on the train", "due": "2026-10-16"})
    plain = do(items, {"revision": plain["project"]["revision"], "id": rules["id"], "url": "", "due": None, "done": False})
    do(items, {"revision": revision, "id": first["id"], "delete": True}, expect=409)
    plain = do(items, {"revision": plain["project"]["revision"], "id": first["id"], "delete": True, "title": "ignored"})
    details = f"{ROOT}/{plain['id']}/project-details"
    revision = plain["project"]["revision"]
    for body, expect in (({"revision": revision, "color": "red"}, 400), ({"revision": revision - 1, "title": "stale"}, 409), ({"title": "x"}, 409),
                         ({"revision": revision, "title": " "}, 400), ({"revision": revision, "title": None}, 400), ({"revision": revision, "notes": 5}, 400),
                         ({"revision": revision, "title": "t" * 501, "notes": 5}, 400)):
        do(details, body, expect=expect)
    plain = do(details, {"revision": revision, "title": " Updated project ", "notes": " Milestones scoped "})
    plain = do(details, {"revision": plain["project"]["revision"], "notes": ""})
    plain = do(details, {"revision": plain["project"]["revision"]})

    # The deadline as a calendar event: a day for a date, half an hour for a time, and the same event when asked again.
    do(f"{ROOT}/{full['id']}/calendar", {"revision": full["revision"]}, expect=400)
    do(f"{ROOT}/{two['id']}/calendar", {}, expect=400)
    do(f"{ROOT}/999/calendar", {}, expect=404)
    do(f"{ROOT}/{full['id']}/calendar", {})
    do(f"{ROOT}/{full['id']}/calendar", {})
    do(f"{ROOT}/{plain['id']}/calendar", {})
    do(f"{ROOT}/{hack['id']}/calendar", {})

    # The list: filters, ranking and availability.
    for query in (
        {}, {"search": "IEEE"}, {"search": "hardware", "category": "quant", "location": "station", "remote": "true", "deadline": "2027-02-01", "availability": "future"},
        {"source": "mlh"}, {"source": "manual", "status": "saved"}, {"status": "applied"}, {"category": "hackathon"}, {"location": "TEXAS"},
        {"remote": "true"}, {"remote": "false"}, {"remote": "unknown"}, {"favorites": "true"}, {"favorites": "false"}, {"deadline": "unknown"},
        {"deadline": "2026-10-20"}, {"deadline": "2026-10-20T00:00:00+14:00"}, {"deadline": " 2026-11-01 "}, {"availability": "unknown"},
        {"availability": "future"}, {"availability": "active"}, {"availability": "closed"}, {"search": "\u00c9", "source": ""}, {"search": "%"},
        {"search": "", "remote": "", "favorites": "", "deadline": "", "availability": ""}, {"search": "ask the ADVISOR"}, {"search": "[\"b\", \"a\"]"},
    ):
        do(ROOT, **query)
    for query in ({"limit": "5"}, {"remote": "maybe"}, {"favorites": "unknown"}, {"favorites": "1"}, {"deadline": "soon"}, {"deadline": "2026-10-20T00:00:00"},
                  {"availability": "open"}, {"remote": "True"}, {"search": "x", "sort": ""}):
        do(ROOT, expect=400, **query)

    # Deletion takes the project and the evidence with it.
    for body in ({}, {"revision": plain["revision"], "force": True}):
        do(f"{ROOT}/{plain['id']}/delete", body, expect=400)
    do(f"{ROOT}/{plain['id']}/delete", {"revision": plain["revision"] + 5}, expect=409)
    do(f"{ROOT}/999/delete", {"revision": 1}, expect=404)
    do(f"{ROOT}/{plain['id']}/delete", {"revision": plain["revision"]})
    do(f"{ROOT}/{past['id']}/delete", {"revision": past["revision"] + 1})
    do(f"{ROOT}/{plain['id']}", expect=404)
    do(f"{ROOT}/{plain['id']}/delete", {"revision": plain["revision"]}, expect=404)

    # Later, the dates have moved past the clock: what was future or active is closed.
    later = BASE + timedelta(days=30, hours=7, minutes=3, seconds=5)
    do(ROOT, now=later)
    do(ROOT, now=later, availability="closed", favorites="true")
    made = do(ROOT, {"title": "Made later", "start": "2026-11-13T16:03:06+00:00", "end": "2026-12-01", "timezone": "Pacific/Kiritimati"}, now=later)
    do(f"{ROOT}/{made['id']}", {"revision": made["revision"], "end": "2026-11-13T16:03:04Z", "start": None}, now=later)
    do(ROOT, now=later)
    do(f"{ROOT}/{bare['id']}", now=later)
    return answers


def test_the_front_answers_opportunities_as_the_python_core_does(workspace, tmp_path):
    seed(workspace.conn)
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    copy = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, copy)

    reference, front = Reference(copy), Front(workspace.settings)
    try:
        expected = scenario(reference)
        answered = scenario(front)
    finally:
        front.close()
        reference.conn.close()
    left, right = Masked(), Masked()
    assert len(answered) == len(expected)
    for (path, status, answer), (_, wanted_status, wanted) in zip(answered, expected, strict=True):
        assert (status, left(answer)) == (wanted_status, right(wanted)), path
    # The run ranks, breaks ties and labels: three availabilities at the first reading, equal scores ordered by id.
    first, final = expected[0][2], expected[-2][2]
    assert {item["availability"] for item in first["items"]} == {"future", "closed", "active"}
    assert [(item["score"], item["title"]) for item in first["items"]][:4] == [
        (20, "Running now"), (20, "Finished cycle"), (20, "Synthetic hardware hack"), (10, "Long past")]
    assert first["items"][2]["reasons"] == ["Interest match: hardware", "Location match: college station"]
    assert first["items"][1]["reasons"] == ["Interest match: STRA\u1e9eE", "Major match: Engineering"]
    assert len(first["sources"]) == len(providers.DEFAULTS) and final["total"] == 11
    # A month on, what was running or due is closed.
    assert {item["title"]: item["availability"] for item in final["items"]} | {"Made later": "closed", "Running now": "closed"} == {
        item["title"]: item["availability"] for item in final["items"]}
    assert {item["availability"] for item in final["items"]} == {"closed", "future", "unknown"}
    stored, events = rows(workspace.settings.db_path, left)
    reference_rows, _ = rows(copy, right)
    assert stored == reference_rows
    assert left([list(event) for event in events]) == right([list(event) for event in reference.events])
    assert ("opportunities", "opportunity", "7", "create", ROOT) in events
    assert {event[3] for event in events} == {"create", "import", "save", "favorite", "project", "items", "project-details", "calendar", "delete"}


def refusal(check, value):
    """What a core check answers: its value, or the refusal's status and text."""
    try:
        return 200, check(value)
    except APIError as refused:
        return refused.status, str(refused)


def test_urls_and_dates_are_checked_as_python_checks_them(workspace):
    """`canonical_url` (urlsplit, parse_qsl, urlencode, credentials and ports) and `valid_date` over tables of awkward values."""
    assert [[url, *((True, answer) if status == 200 else (False, answer))] for url, _, _ in URLS
            for status, answer in [refusal(core.canonical_url, url)]] == URLS, "tests/fixtures/opportunity_urls.json is Python's answers"
    assert sum(accepted for _, accepted, _ in URLS) > 100 and sum(not accepted for _, accepted, _ in URLS) > 100
    conn = workspace.conn
    item = core.create(conn, {"title": "Carrier"})
    path = f"{ROOT}/{item['id']}"
    revision = item["revision"]
    padded = [" https://Padded.example.invalid/ ", "", "   ", "\x1fhttps://example.invalid/\x1c", "x" * 4001, " " + "https://example.invalid/" + "a" * 3976 + " ",
              None, 5, {"url": "x"}, "https://example.invalid/\x00"]
    wrong = []
    with front_site(workspace.settings) as site:
        for field, check, values in (("url", core.canonical_url, [url for url, _, _ in URLS] + padded), ("deadline", core.valid_date, DATES)):
            for value in values:
                status, wanted = refusal(check, value)
                answered, body = site.request(path, {"revision": revision, field: value})
                if (answered, body["error"] if answered != 200 else body[field]) != (status, wanted):
                    wrong.append((field, value, body, wanted))
                revision += answered == 200
        assert not wrong
        # A date at the end of the calendar is stored; the list and the export then fail in both, as an error nobody handles.
        for deadline, route in (("9999-12-31", ROOT), ("9999-12-31T23:45:00Z", f"{path}/calendar")):
            assert site.request(path, {"revision": revision, "deadline": deadline})[0] == 200
            revision += 1
            assert site.request(route, {} if route != ROOT else None) == (500, FAILED)
    with pytest.raises(OverflowError):
        core.export_deadline(conn, item["id"])
    conn.execute("UPDATE opportunities SET deadline='9999-12-31' WHERE id=?", (item["id"],))
    with pytest.raises(OverflowError):
        core.list_items(conn, {})


def test_settings_discovery_and_sources_stay_with_the_worker(workspace, tmp_path):
    conn = workspace.conn
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        # No Python handler is left for a route the front answers.
        for path in (ROOT, f"{ROOT}/1"):
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(conn, path, {})
        for path in (ROOT, f"{ROOT}/import", f"{ROOT}/favorites", f"{ROOT}/1", f"{ROOT}/1/delete", f"{ROOT}/1/project", f"{ROOT}/1/items",
                     f"{ROOT}/1/project-details", f"{ROOT}/1/calendar"):
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(conn, path, {"title": "x"})
    finally:
        app.close()
    with front_site(workspace.settings) as site:
        status, listed = site.request(ROOT)
        assert status == 200 and listed["schedule"]["enabled"] is False and listed["total"] == 0
        profile = {"major": "engineering", "track": "quant", "interests": ["hardware"], "locations": ["College Station"], "prefer_remote": True}
        status, saved = site.request(f"{ROOT}/settings", {"revision": listed["schedule"]["revision"], "profile": profile, "interval_hours": 12})
        assert status == 200 and saved["profile"] == profile and saved["interval_hours"] == 12
        status, made = site.request(ROOT, {"title": "Engineering hardware", "description": "Quant workshop", "location": "College Station", "remote": True})
        assert status == 200
        status, listed = site.request(ROOT)
        assert listed["profile"] == profile and listed["schedule"]["interval_hours"] == 12 and listed["items"][0]["score"] == 50
        source = listed["sources"][0]
        status, changed = site.request(f"{ROOT}/sources/{source['id']}", {"revision": source["revision"], "enabled": False})
        assert status == 200 and changed["enabled"] == 0
        assert site.request(f"{ROOT}/sources/{source['id']}", {"revision": changed["revision"], "url": "http://127.0.0.1/private"})[0] == 400
        assert site.request(ROOT)[1]["sources"][0] == changed
        assert site.request(f"{ROOT}/discover", {}) == (400, {"error": "Explicit confirmation is required to fetch free public sources"})
        assert site.request(f"{ROOT}/settings")[0] == 404 and site.request(f"{ROOT}/{made['id']}/nope", {})[0] == 404
        assert site.request(f"{ROOT}?x=1", {"title": "x"}) == (400, {"error": "This route does not accept query parameters"})
    # Only the opportunity was a content change.
    assert [tuple(row) for row in conn.execute("SELECT section,entity,entity_id,action,route FROM record_events ORDER BY seq")] == [
        ("opportunities", "opportunity", str(made["id"]), "create", ROOT)]
