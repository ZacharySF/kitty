"""Live page mirrors (platform design Q11): the browser tab a paired extension is mirroring.

The kitty browser extension sends a *frame* of the tab it mirrors every few seconds while
the page changes or scrolls: URL, title, readable text, the user's selection and a
screenshot. kitty! shows the latest frame in the Mirror pane beside a note; quoting a
passage from it writes a block through the editor's own transaction.

Retention (stated to the user in the pane, the extension and its README):

- Frames live only in this server process's memory. Nothing here touches SQLite or the
  disk; only what the user quotes into a note becomes a block (and so enters the op log).
- Each mirror keeps only its latest frame: one screenshot of at most `MAX_IMAGE_BYTES`
  (JPEG, PNG or WebP, decoded to validate it, at most `MAX_SIDE` pixels per side and
  `MAX_PIXELS` in all) and the readable text. A new frame replaces the old one.
- At most `MAX_PER_CLIENT` mirrors per paired client and `MAX_MIRRORS` in all; the
  least recently updated one is dropped to make room.
- A mirror is dropped when the extension stops it (mirroring switched off, the tab
  closed or navigated to a denied page), after `EXPIRY` seconds without a frame, when
  its client is revoked, and when kitty! stops.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import threading
import time
import uuid
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kitty.core.clients import ClientError, now
from kitty.core.ingress import MAX_PAGE_TEXT, MAX_TEXT, MAX_TITLE, _keys, _text, _url

MAX_IMAGE_BYTES = 2 * 1024 * 1024
#: A frame request body: the base64 screenshot (4/3 of the image) plus text and fields.
MAX_FRAME_BODY = 4 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 40_000_000
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_PER_CLIENT = 4
MAX_MIRRORS = 8
#: Seconds without a frame after which a mirror is dropped (the extension sends one at
#: least every minute while mirroring, even when the page is still).
EXPIRY = 150
#: Frames closer together than this are refused (429); Chrome itself allows two
#: screenshots a second, the extension sends one every few seconds.
MIN_INTERVAL = 0.4
_TAB = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DATA_URL = re.compile(r"data:(image/(?:jpeg|png|webp));base64,", re.IGNORECASE)


@dataclass
class Image:
    data: bytes
    type: str
    width: int
    height: int
    digest: str

    def summary(self, frame: int) -> dict:
        return {"frame": frame, "type": self.type, "width": self.width, "height": self.height, "bytes": len(self.data)}


@dataclass
class Mirror:
    id: str
    client_id: str
    client_name: str
    tab: str
    started_at: str
    url: str = ""
    title: str = ""
    text: str = ""
    selection: str = ""
    frame: int = 0
    text_rev: int = 0
    image: Image | None = None
    image_frame: int = 0
    at: str = ""
    touched: float = 0.0

    def public(self, *, text: bool) -> dict:
        result = {"type": "mirror", "id": self.id, "client": {"id": self.client_id, "name": self.client_name},
                  "tab": self.tab, "url": self.url, "title": self.title, "selection": self.selection,
                  "frame": self.frame, "text_rev": self.text_rev, "at": self.at, "started_at": self.started_at,
                  "image": self.image.summary(self.image_frame) if self.image else None, "expires_in": EXPIRY}
        if text:
            result["text"] = self.text
        return result


def _end(mirror: Mirror, reason: str) -> dict:
    return {"type": "mirror_end", "id": mirror.id, "reason": reason}


def decode_image(value: Any) -> Image:
    """A screenshot sent as a data URL or plain base64, decoded completely to validate it."""
    from PIL import Image as Pillow, UnidentifiedImageError

    if not isinstance(value, str):
        raise ClientError("The screenshot must be a data URL or base64 text")
    if len(value) > MAX_IMAGE_BYTES * 4 // 3 + 64:
        raise ClientError(f"The screenshot is limited to {MAX_IMAGE_BYTES // 1024 // 1024} MiB", 413)
    declared = None
    match = _DATA_URL.match(value)
    if match:
        declared, value = match[1].lower(), value[match.end():]
    elif value.startswith("data:"):
        raise ClientError("Screenshots must be JPEG, PNG or WebP", 415)
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ClientError("The screenshot must be base64") from None
    if not data:
        raise ClientError("The screenshot is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ClientError(f"The screenshot is limited to {MAX_IMAGE_BYTES // 1024 // 1024} MiB", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Pillow.DecompressionBombWarning)
            with Pillow.open(io.BytesIO(data), formats=tuple(IMAGE_TYPES)) as image:
                kind = IMAGE_TYPES.get(image.format or "")
                width, height = image.size
                if kind is None:
                    raise ClientError("Screenshots must be JPEG, PNG or WebP", 415)
                if declared is not None and declared != kind:
                    raise ClientError("The screenshot's type does not match its data", 415)
                if not 1 <= width <= MAX_SIDE or not 1 <= height <= MAX_SIDE or width * height > MAX_PIXELS:
                    raise ClientError(f"Screenshots are limited to {MAX_SIDE} pixels per side", 413)
                image.load()
    except ClientError:
        raise
    except (UnidentifiedImageError, Pillow.DecompressionBombError, Pillow.DecompressionBombWarning, OSError,
            ValueError, SyntaxError, EOFError):
        raise ClientError("The screenshot is not a valid JPEG, PNG or WebP image", 415) from None
    return Image(data, kind, width, height, hashlib.sha256(data).hexdigest())


class Mirrors:
    """The live mirrors of this server process, keyed by (client, tab). Thread-safe."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._mirrors: dict[tuple[str, str], Mirror] = {}

    def accept(self, client: dict, body: Any) -> tuple[dict, list[dict]]:
        """Take one frame from `client`; returns the reply and the live messages to push."""
        body = _keys(body, {"tab", "url", "title", "text", "selection", "image"}, {"tab", "url", "title"})
        tab = body["tab"]
        if not isinstance(tab, str) or not _TAB.fullmatch(tab):
            raise ClientError("tab must be 1–64 letters, digits, - or _")
        url = _url(body["url"])
        title = " ".join(_text(body["title"], "Title", MAX_TITLE, required=True).split())
        text = _text(body.get("text", ""), "Page text", MAX_PAGE_TEXT).strip()
        selection = _text(body.get("selection", ""), "Selection", MAX_TEXT).strip()
        image = decode_image(body["image"]) if body.get("image") is not None else None
        moment = self._clock()
        with self._lock:
            pushes = self._expire(moment)
            key = (client["id"], tab)
            mirror = self._mirrors.get(key)
            if mirror is not None and moment - mirror.touched < MIN_INTERVAL:
                raise ClientError("Frames are coming too fast; send one every few seconds", 429, retry_after=1)
            if mirror is None:
                pushes += self._make_room(client["id"])
                mirror = Mirror(uuid.uuid4().hex, client["id"], client["name"], tab, now())
                self._mirrors[key] = mirror
            changed = mirror.text != text or mirror.frame == 0
            mirror.url, mirror.title, mirror.selection = url, title, selection
            mirror.client_name = client["name"]
            mirror.frame += 1
            if changed:
                mirror.text = text
                mirror.text_rev += 1
            if image is not None and (mirror.image is None or mirror.image.digest != image.digest):
                mirror.image, mirror.image_frame = image, mirror.frame
            mirror.at, mirror.touched = now(), moment
            pushes.append(mirror.public(text=changed))
            reply = {"mirror": mirror.id, "frame": mirror.frame, "expires_in": EXPIRY}
        return reply, pushes

    def _make_room(self, client_id: str) -> list[dict]:
        pushes = []
        own = [item for item in self._mirrors.values() if item.client_id == client_id]
        for pool, limit in ((own, MAX_PER_CLIENT), (list(self._mirrors.values()), MAX_MIRRORS)):
            while len(pool) >= limit:
                oldest = min(pool, key=lambda item: item.touched)
                pool.remove(oldest)
                if self._mirrors.pop((oldest.client_id, oldest.tab), None) is not None:
                    pushes.append(_end(oldest, "limit"))
        return pushes

    def _expire(self, moment: float) -> list[dict]:
        stale = [key for key, item in self._mirrors.items() if moment - item.touched > EXPIRY]
        return [_end(self._mirrors.pop(key), "expired") for key in stale]

    def expire(self) -> list[dict]:
        with self._lock:
            return self._expire(self._clock())

    def stop(self, client: dict, body: Any) -> list[dict]:
        """The extension stopped mirroring a tab: drop its frame at once."""
        tab = _keys(body, {"tab"}, {"tab"})["tab"]
        if not isinstance(tab, str) or not _TAB.fullmatch(tab):
            raise ClientError("tab must be 1–64 letters, digits, - or _")
        with self._lock:
            mirror = self._mirrors.pop((client["id"], tab), None)
        return [_end(mirror, "stopped")] if mirror else []

    def drop_client(self, client_id: str) -> list[dict]:
        with self._lock:
            keys = [key for key in self._mirrors if key[0] == client_id]
            return [_end(self._mirrors.pop(key), "revoked") for key in keys]

    def clear(self) -> None:
        with self._lock:
            self._mirrors.clear()

    def listing(self) -> list[dict]:
        """Every live mirror with its text, most recently updated first (a window that just opened)."""
        with self._lock:
            self._expire(self._clock())
            mirrors = sorted(self._mirrors.values(), key=lambda item: -item.touched)
            return [item.public(text=True) for item in mirrors]

    def image(self, mirror_id: str, frame: int) -> Image | None:
        """The screenshot of `frame`, while it is still the mirror's latest one."""
        with self._lock:
            for mirror in self._mirrors.values():
                if mirror.id == mirror_id and mirror.image is not None and mirror.image_frame == frame:
                    return mirror.image
        return None


#: What the Mirror pane and the extension tell the user about retention.
RETENTION = {
    "memory_only": True,
    "frames_kept": 1,
    "expiry_seconds": EXPIRY,
    "max_image_bytes": MAX_IMAGE_BYTES,
    "max_mirrors": MAX_MIRRORS,
    "max_per_client": MAX_PER_CLIENT,
}
