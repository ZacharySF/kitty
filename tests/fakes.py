"""A stand-in for PoliteHttp so adapter and pipeline tests never touch the network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from applyai.core.discovery.http import HttpError

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Any:
    """A JSON fixture by file name."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeHttp:
    """Answers from a dict of url -> payload. An Exception value is raised instead.

    POSTs are looked up by (url, offset) first, so Workday pagination can be
    scripted page by page, then by url alone.
    """

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []
        self.requests_made = 0
        self.cache_hits = 0

    def _answer(self, key):
        if key not in self.routes:
            raise HttpError(f"HTTP 404 from {key}")
        value = self.routes[key]
        if isinstance(value, Exception):
            raise value
        return value

    def get_json(self, url: str) -> Any:
        self.calls.append(("GET", url, None))
        self.requests_made += 1
        return self._answer(url)

    def post_json(self, url: str, body: dict) -> Any:
        self.calls.append(("POST", url, body))
        self.requests_made += 1
        key = (url, body.get("offset"))
        return self._answer(key if key in self.routes else url)

    def get_bytes(self, url: str) -> bytes:
        self.calls.append(("GET", url, None))
        self.requests_made += 1
        return self._answer(url)

    def close(self) -> None:
        pass
