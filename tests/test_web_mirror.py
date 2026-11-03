"""The live page mirror (Q11): frames from the paired browser extension, kept in memory and pushed live."""
from __future__ import annotations

import base64
import io
import json
import sqlite3

import pytest
from PIL import Image
from terminal_support import make_root, site
from test_web_blocks import Client
from test_web_clients import EXTENSION, OTHER_EXTENSION, call, pair, preflight

from kitty.core import mirror

FRAME = "/api/ingress/mirror/frame"
STOP = "/api/ingress/mirror/stop"


def picture(kind="JPEG", size=(64, 40), colour=(200, 40, 90)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, colour).save(stream, format=kind)
    return stream.getvalue()


def data_url(data: bytes, kind="image/jpeg") -> str:
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"


def frame(tab="7", **fields):
    return {"tab": tab, "url": "https://example.com/article", "title": "An article",
            "text": "First paragraph.\n\nSecond paragraph.", "selection": "", **fields}


@pytest.fixture
def running(tmp_path, monkeypatch):
    # Tests send frames back to back; the pacing rule has its own test.
    monkeypatch.setattr(mirror, "MIN_INTERVAL", 0)
    with site(make_root(tmp_path, ("workspace",))) as server:
        yield server


@pytest.fixture
def extension(running):
    client, token = pair(running, kind="browser-extension", origin=EXTENSION, scopes=("capture",), name="kitty for Chromium")
    return client, token


def send(running, body, token, origin=EXTENSION, path=FRAME):
    return call(running, "POST", path, body, token=token, origin=origin)


def page_get(running, path):
    """A GET as rika!'s own page makes it (an <img> in the Mirror pane)."""
    return call(running, "GET", path, origin=running.server.origin)


def ops_count(running) -> int:
    with sqlite3.connect(running.app.settings.db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM op_log").fetchone()[0]


def test_a_frame_is_pushed_to_every_window_and_listed_with_its_screenshot(running, extension):
    client, token = extension
    first, second = Client(running), Client(running)
    try:
        jpeg = picture()
        status, reply, headers = send(running, frame(image=data_url(jpeg), selection="Second"), token)
        assert status == 200, reply
        assert headers["access-control-allow-origin"] == EXTENSION
        assert reply["frame"] == 1 and reply["expires_in"] == mirror.EXPIRY
        for window in (first, second):
            pushed = window.expect("mirror")
            assert pushed["id"] == reply["mirror"] and pushed["client"] == {"id": client["id"], "name": "kitty for Chromium"}
            assert pushed["url"] == "https://example.com/article" and pushed["title"] == "An article"
            assert pushed["text"] == "First paragraph.\n\nSecond paragraph." and pushed["selection"] == "Second"
            assert pushed["image"] == {"frame": 1, "type": "image/jpeg", "width": 64, "height": 40, "bytes": len(jpeg)}

        status, listed = running.request("/api/mirror")
        assert status == 200 and [item["id"] for item in listed["mirrors"]] == [reply["mirror"]]
        assert listed["mirrors"][0]["text"].startswith("First paragraph.")
        assert listed["retention"]["memory_only"] is True and listed["retention"]["frames_kept"] == 1

        status, body, headers = page_get(running, f"/api/mirror/{reply['mirror']}/image?frame=1")
        assert status == 200 and body == jpeg
        assert headers["content-type"] == "image/jpeg" and headers["x-content-type-options"] == "nosniff"
        assert headers["cache-control"] == "no-store"

        # The same text again is not pushed again; a new screenshot replaces the old one.
        png = picture("PNG", colour=(10, 120, 200))
        status, again, _ = send(running, frame(image=data_url(png, "image/png")), token)
        assert status == 200 and again["mirror"] == reply["mirror"] and again["frame"] == 2
        pushed = first.expect("mirror")
        assert "text" not in pushed and pushed["text_rev"] == 1 and pushed["image"]["frame"] == 2
        assert pushed["image"]["type"] == "image/png"
        assert page_get(running, f"/api/mirror/{reply['mirror']}/image?frame=1")[0] == 404
        assert page_get(running, f"/api/mirror/{reply['mirror']}/image?frame=2")[1] == png

        # A frame without a screenshot (the tab was in the background) keeps the last one.
        status, third, _ = send(running, frame(text="Changed text."), token)
        assert status == 200
        pushed = first.expect("mirror")
        assert pushed["text"] == "Changed text." and pushed["text_rev"] == 2 and pushed["image"]["frame"] == 2
    finally:
        first.close()
        second.close()


def test_frames_never_reach_the_op_log_or_the_disk(running, extension, tmp_path):
    _, token = extension
    before = ops_count(running)
    jpeg = picture(colour=(1, 2, 3))
    assert send(running, frame(image=data_url(jpeg), selection="secret selection"), token)[0] == 200
    assert ops_count(running) == before
    marker = base64.b64encode(jpeg)[:40]
    for path in running.app.settings.root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert jpeg[:200] not in content and marker not in content and b"secret selection" not in content, path


def test_stopping_drops_the_frame_at_once_and_tells_every_window(running, extension):
    _, token = extension
    window = Client(running)
    try:
        status, reply, _ = send(running, frame(image=data_url(picture())), token)
        assert status == 200
        window.expect("mirror")
        status, stopped, headers = send(running, {"tab": "7"}, token, path=STOP)
        assert status == 200 and stopped == {"stopped": True} and headers["access-control-allow-origin"] == EXTENSION
        assert window.expect("mirror_end") == {"type": "mirror_end", "id": reply["mirror"], "reason": "stopped"}
        assert running.request("/api/mirror")[1]["mirrors"] == []
        assert page_get(running, f"/api/mirror/{reply['mirror']}/image?frame=1")[0] == 404
        assert send(running, {"tab": "7"}, token, path=STOP)[1] == {"stopped": False}
        # Mirroring the tab again starts a new mirror.
        assert send(running, frame(), token)[1]["mirror"] != reply["mirror"]
    finally:
        window.close()


def test_revoking_the_extension_drops_its_mirrors(running, extension):
    client, token = extension
    window = Client(running)
    try:
        reply = send(running, frame(), token)[1]
        window.expect("mirror")
        running.request("/api/clients/revoke", {"id": client["id"]})
        assert window.expect("mirror_end") == {"type": "mirror_end", "id": reply["mirror"], "reason": "revoked"}
        assert running.request("/api/mirror")[1]["mirrors"] == []
        assert send(running, frame(), token)[0] == 401
    finally:
        window.close()


def test_frames_need_the_capture_scope_and_the_paired_origin(running, extension):
    _, token = extension
    _, reader = pair(running, kind="browser-extension", origin=OTHER_EXTENSION, scopes=("read",), name="Reader")
    assert send(running, frame(), reader, origin=OTHER_EXTENSION)[0] == 403
    assert send(running, {"tab": "7"}, reader, origin=OTHER_EXTENSION, path=STOP)[0] == 403
    status, _, headers = call(running, "POST", FRAME, frame())
    assert status == 401 and headers["www-authenticate"].startswith("Bearer")
    # The token works only from the extension origin that paired it.
    assert send(running, frame(), token, origin=OTHER_EXTENSION)[0] == 403
    assert send(running, frame(), token, origin=None)[0] == 403
    assert send(running, frame(), token, origin="https://example.com")[0] == 403
    # Pages and other programs never see frames through the page routes with a token.
    forged = {"Origin": running.server.origin, "X-Kitty-Request": "1"}
    assert call(running, "GET", "/api/mirror", token=token, headers=forged)[0] == 403
    assert running.request("/api/mirror", headers={"Origin": "https://attacker.example"})[0] == 403


def test_cors_preflight_answers_the_frame_routes_only_for_the_paired_extension(running):
    assert preflight(running, FRAME, EXTENSION)[0] == 403
    pair(running, kind="browser-extension", origin=EXTENSION, scopes=("capture",))
    for path in (FRAME, STOP):
        status, _, headers = preflight(running, path, EXTENSION, extra={"Access-Control-Request-Private-Network": "true"})
        assert status == 204 and headers["access-control-allow-origin"] == EXTENSION
        assert headers["access-control-allow-methods"] == "POST"
        assert headers["access-control-allow-private-network"] == "true"
        assert preflight(running, path, OTHER_EXTENSION)[0] == 403
        assert preflight(running, path, "https://attacker.example")[0] == 403
        assert preflight(running, path, EXTENSION, method="GET")[0] == 403


@pytest.mark.parametrize(("image", "status"), [
    (data_url(picture("GIF"), "image/gif"), 415),
    (base64.b64encode(picture("GIF")).decode(), 415),
    (data_url(picture("PNG"), "image/jpeg"), 415),
    (data_url(picture()[:-200]), 415),
    (data_url(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/png"), 415),
    ("data:image/svg+xml;base64,PHN2Zy8+", 415),
    ("data:image/jpeg;base64,not base64!", 400),
    ("", 400),
    (data_url(picture("PNG", size=(mirror.MAX_SIDE + 1, 2)), "image/png"), 413),
    (12, 400),
])
def test_screenshots_are_decoded_and_only_jpeg_png_or_webp_pass(running, extension, image, status):
    _, token = extension
    result = send(running, frame(image=image), token)
    assert result[0] == status, result[1]
    assert running.request("/api/mirror")[1]["mirrors"] == []


def test_webp_screenshots_pass(running, extension):
    _, token = extension
    webp = picture("WEBP")
    status, reply, _ = send(running, frame(image=data_url(webp, "image/webp")), token)
    assert status == 200, reply
    assert page_get(running, f"/api/mirror/{reply['mirror']}/image?frame=1")[2]["content-type"] == "image/webp"


def test_size_caps(running, extension, monkeypatch):
    _, token = extension
    monkeypatch.setattr(mirror, "MAX_IMAGE_BYTES", 2000)
    noisy = Image.effect_noise((200, 200), 90).convert("RGB")
    stream = io.BytesIO()
    noisy.save(stream, format="PNG")
    assert len(stream.getvalue()) > 2000
    assert send(running, frame(image=data_url(stream.getvalue(), "image/png")), token)[0] == 413
    # The whole request body is capped before it is read.
    status, _, _ = call(running, "POST", FRAME, None, token=token, origin=EXTENSION,
                        headers={"Content-Type": "application/json", "Content-Length": str(mirror.MAX_FRAME_BODY + 1)})
    assert status == 413
    assert send(running, frame(text="x" * 200_001), token)[0] == 400
    assert send(running, frame(selection="x" * 20_001), token)[0] == 400


@pytest.mark.parametrize("body", [
    frame(url="javascript:alert(1)"), frame(url="file:///etc/passwd"), frame(url="about:blank"),
    frame(title=""), frame(tab="../7"), frame(tab=""), frame(tab=7), {"url": "https://example.com/", "title": "x"},
    frame(extra=1),
])
def test_frame_fields_are_validated(running, extension, body):
    _, token = extension
    assert send(running, body, token)[0] == 400


def test_frames_are_paced(running, extension, monkeypatch):
    _, token = extension
    monkeypatch.setattr(mirror, "MIN_INTERVAL", 30)
    assert send(running, frame(), token)[0] == 200
    status, _, headers = send(running, frame(), token)
    assert status == 429 and headers["retry-after"] == "1"
    # Another tab has its own pace.
    assert send(running, frame(tab="8"), token)[0] == 200


def test_mirrors_are_capped_per_client_and_the_oldest_goes(running, extension):
    _, token = extension
    window = Client(running)
    try:
        ids = []
        for tab in range(mirror.MAX_PER_CLIENT + 1):
            ids.append(send(running, frame(tab=str(tab)), token)[1]["mirror"])
        messages = [window.expect("mirror") for _ in ids]
        assert [message["id"] for message in messages][-1] == ids[-1]
        listed = [item["id"] for item in running.request("/api/mirror")[1]["mirrors"]]
        assert len(listed) == mirror.MAX_PER_CLIENT and ids[0] not in listed
    finally:
        window.close()


def test_the_frame_routes_need_the_workspace_module(tmp_path):
    with site(make_root(tmp_path, ("terminal",))) as running:
        _, token = pair(running, kind="browser-extension", origin=EXTENSION, scopes=("capture",))
        assert send(running, frame(), token)[0] == 403
        assert running.request("/api/mirror")[0] == 403


def test_connected_apps_shows_the_address_to_paste_into_the_extension(running):
    status, state = running.request("/api/clients")
    assert status == 200 and state["address"] == running.server.origin


def test_the_store_expires_silent_mirrors_and_keeps_one_frame():
    clock = [100.0]
    store = mirror.Mirrors(clock=lambda: clock[0])
    client = {"id": "a" * 32, "name": "Ext"}
    reply, pushes = store.accept(client, frame(image=data_url(picture())))
    assert [push["type"] for push in pushes] == ["mirror"]
    clock[0] += mirror.EXPIRY - 1
    assert store.expire() == [] and len(store.listing()) == 1
    clock[0] += 2
    assert store.expire() == [{"type": "mirror_end", "id": reply["mirror"], "reason": "expired"}]
    assert store.listing() == [] and store.image(reply["mirror"], 1) is None
    # The overall cap spans clients.
    for index in range(mirror.MAX_MIRRORS + 1):
        clock[0] += 1
        store.accept({"id": f"{index:032x}", "name": "Ext"}, frame())
    assert len(store.listing()) == mirror.MAX_MIRRORS
    json.dumps(store.listing())
