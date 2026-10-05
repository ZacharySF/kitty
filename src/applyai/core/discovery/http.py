"""Polite HTTP for the adapters: pacing, a disk cache, and honest failures.

Hard rule 4 (polite over fast) lives here:
  * each host gets at least `min_interval` seconds between requests;
  * responses are cached on disk for the source's TTL, so re-running discover
    inside the TTL makes no network calls;
  * transport errors (timeouts, resets) are retried twice; HTTP errors are not;
  * a 429 is never retried. It raises RateLimited and the caller stops using
    that host for the rest of the run.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from applyai.dates import now_iso, to_iso

USER_AGENT = "applyai/0.1 (personal job-search tool; polite, cached)"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
TRANSPORT_RETRIES = 2


class HttpError(Exception):
    """A request that failed for a reason worth showing on the Sources screen."""


class RateLimited(HttpError):
    """The server answered 429 (or asked us to back off)."""


class PoliteHttp:
    """One HTTP client per source, with that source's pacing and cache TTL."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        min_interval: float,
        cache_ttl_minutes: int,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.ttl_seconds = cache_ttl_minutes * 60
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True
        )
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()
        self.requests_made = 0
        self.cache_hits = 0
        self._observation = threading.local()

    @property
    def retrieved_at(self) -> str | None:
        """Original response time in this thread, including cache hits.

        Legacy byte caches retain their original write mtime; reading a cache
        never promotes it to a new observation. Thread-local state keeps parallel
        Workday detail requests from borrowing another board's timestamp.
        """
        return getattr(self._observation, "retrieved_at", None)

    # ------------------------------------------------------------ public

    def get_json(self, url: str) -> Any:
        """GET a JSON document."""
        return json.loads(self._fetch("GET", url, None))

    def post_json(self, url: str, body: dict) -> Any:
        """POST a JSON body and parse the JSON answer (Workday's list endpoint)."""
        return json.loads(self._fetch("POST", url, body))

    def get_bytes(self, url: str) -> bytes:
        """GET raw bytes (a repo tarball)."""
        return self._fetch("GET", url, None, binary=True)

    def close(self) -> None:
        self.client.close()

    # ----------------------------------------------------------- internals

    def _cache_path(self, method: str, url: str, body: dict | None) -> Path:
        key = json.dumps([method, url, body], sort_keys=True)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / digest[:2] / digest

    def _fetch(self, method: str, url: str, body: dict | None, *, binary: bool = False):
        """Return the response body, from cache when fresh, else from the network."""
        path = self._cache_path(method, url, body)
        self._observation.retrieved_at = None
        if self.ttl_seconds and path.exists() and time.time() - path.stat().st_mtime < self.ttl_seconds:
            self.cache_hits += 1
            data = path.read_bytes()
            self._observation.retrieved_at = to_iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
            return data if binary else data.decode("utf-8")

        data = self._request(method, url, body)
        self._observation.retrieved_at = now_iso()
        if self.ttl_seconds:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self._observation.retrieved_at = to_iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
        return data if binary else data.decode("utf-8")

    def _wait_for_host(self, host: str, *, minimum_interval: float = 0) -> None:
        """Reserve a host request slot, honoring an optional robots-specific interval."""
        interval = max(self.min_interval, minimum_interval)
        with self._lock:
            last = self._last_request.get(host, 0.0)
            wait = last + interval - time.monotonic()
            self._last_request[host] = max(time.monotonic(), last + interval)
        if wait > 0:
            time.sleep(wait)

    def _request(self, method: str, url: str, body: dict | None) -> bytes:
        host = urlsplit(url).netloc
        for attempt in range(TRANSPORT_RETRIES + 1):
            self._wait_for_host(host)
            try:
                self.requests_made += 1
                response = self.client.request(method, url, json=body)
            except httpx.TransportError as exc:
                if attempt == TRANSPORT_RETRIES:
                    raise HttpError(f"{type(exc).__name__} after {attempt + 1} tries: {url}") from exc
                continue
            if response.status_code == 429:
                raise RateLimited(f"429 from {host}")
            if response.status_code >= 400:
                raise HttpError(f"HTTP {response.status_code} from {url}")
            return response.content
        raise AssertionError("unreachable")
