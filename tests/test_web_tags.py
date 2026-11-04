"""Auto-tags over HTTP and the blocks WebSocket: acks carry suggestions and always-attach
operations, every tag write is pushed live, and /api/tags belongs to the workspace module."""
from __future__ import annotations

import uuid

import pytest
from terminal_support import make_root, site
from test_web_blocks import Client, page_ops


@pytest.fixture
def running(tmp_path):
    with site(make_root(tmp_path, ("workspace",))) as server:
        yield server


def key(tag_id: str) -> str:
    return "tag_" + tag_id.replace("-", "")


def test_the_editor_gets_suggestions_in_its_ack_and_attachments_with_its_edit(running):
    status, python = running.request("/api/tags/create", {"name": "Python", "keywords": ["pandas"]})
    assert status == 200 and python["effective_mode"] == "suggest"
    status, rust = running.request("/api/tags/create", {"name": "Rust", "mode": "always"})
    assert status == 200
    window, other = Client(running), Client(running)
    try:
        page, block = str(uuid.uuid4()), str(uuid.uuid4())
        window.send({"type": "txn", "ops": page_ops(page, block, "")})
        window.expect("ack")
        other.expect("change")
        window.send({"type": "txn", "ops": [{"op": "text", "id": block, "text": "pandas in rust"}], "base": {page: 1}})
        ack = window.expect("ack")
        assert ack["suggestions"] == {block: {"text": "pandas in rust", "items": [{"tag": python["id"], "name": "Python", "start": 0, "end": 6}]}}
        assert ack["tagged"] == {page: [{"op": "set", "id": block, "props": {key(rust["id"]): "auto"}}]}
        assert ack["tags"] == {python["id"]: "Python", rust["id"]: "Rust"} and ack["revisions"] == {page: 2}
        pushed = other.expect("change")
        assert {"op": "set", "id": block, "props": {key(rust["id"]): "auto"}} in pushed["ops"]
        # Tab in the editor: a tag state is not an edit, so the page revision stays put.
        window.send({"type": "txn", "ops": [{"op": "set", "id": block, "props": {key(python["id"]): "accepted"}}], "base": {page: 2}})
        ack = window.expect("ack")
        assert ack["revisions"] == {page: 2} and ack["suggestions"][block]["items"] == []
        assert other.expect("change")["ops"] == [{"op": "set", "id": block, "props": {key(python["id"]): "accepted"}}]
        status, loaded = running.request(f"/api/blocks/page?page={page}")
        assert status == 200 and loaded["suggestions"] == {} and loaded["tags"] == {python["id"]: "Python", rust["id"]: "Rust"}
        # Removing by hand (the tag page's ×) is remembered and pushed.
        status, removed = running.request("/api/tags/remove", {"block": block, "tag": rust["id"]})
        assert status == 200 and removed["state"] == "rejected"
        assert other.expect("change")["ops"] == [{"op": "set", "id": block, "props": {key(rust["id"]): "rejected"}}]
        window.send({"type": "txn", "ops": [{"op": "text", "id": block, "text": "pandas in rust, more rust"}], "base": {page: 2}})
        assert "tagged" not in window.expect("ack")
    finally:
        window.close()
        other.close()


def test_tag_routes_settings_pages_and_link_all(running):
    status, listing = running.request("/api/tags")
    assert status == 200 and listing == {"mode": "suggest", "revision": 1, "tags": []}
    assert running.request("/api/tags/settings", {"mode": "always", "revision": 1}) == (200, {"mode": "always", "revision": 2})
    assert running.request("/api/tags/settings", {"mode": "suggest", "revision": 1})[0] == 409
    assert running.request("/api/tags/settings", {"mode": "never", "revision": 2})[0] == 400
    assert running.request("/api/tags/settings", {"mode": "suggest", "revision": 2})[0] == 200
    status, tag = running.request("/api/tags/create", {"name": "Graphs", "aliases": ["graph"]})
    assert status == 200
    assert running.request("/api/tags/create", {"name": "graph"})[0] == 409
    assert running.request("/api/tags/create", {"name": "x", "colour": "red"})[0] == 400
    status, notebook = running.request("/api/notes/notebooks", {"title": "Tags"})
    status, note = running.request("/api/notes/pages", {"notebook_id": notebook["id"], "title": "Algorithms"})
    status, loaded = running.request(f"/api/blocks/page?type=page&number={note['id']}")
    window = Client(running)
    try:
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        window.send({"type": "txn", "ops": [{"op": "insert", "id": first, "type": "text", "parent": loaded["page_id"], "text": "graphs everywhere"},
                                            {"op": "insert", "id": second, "type": "text", "parent": loaded["page_id"], "text": "see #graph"}]})
        window.expect("ack")
    finally:
        window.close()
    assert running.request("/api/tags/find?name=GRAPH") == (200, {"id": tag["id"]})
    assert running.request("/api/tags/find?name=nothing") == (200, {"id": None})
    status, view = running.request(f"/api/tags/page?id={tag['id']}")
    assert status == 200 and [item["id"] for item in view["blocks"]] == [second] and view["blocks"][0]["number"] == note["id"]
    assert [item["id"] for item in view["unlinked"]] == [first] and view["unlinked_count"] == 1
    assert running.request("/api/tags/link-all", {"id": tag["id"]}) == (200, {"linked": 1, "tag": tag["id"]})
    status, view = running.request(f"/api/tags/page?id={tag['id']}")
    assert {item["id"]: item["source"] for item in view["blocks"]} == {first: "accepted", second: "manual"} and view["unlinked"] == []
    status, updated = running.request("/api/tags/update", {"id": tag["id"], "mode": "always", "keywords": ["DAG"], "short": True})
    assert status == 200 and updated["mode"] == "always" and updated["keywords"] == ["DAG"] and updated["short"] is True
    assert running.request("/api/tags/update", {"id": tag["id"], "mode": None})[1]["mode"] is None
    assert running.request("/api/tags/page?id=bad")[0] == 400
    assert running.request(f"/api/tags/page?id={uuid.uuid4()}")[0] == 404
    assert running.request("/api/tags/delete", {"id": tag["id"]}) == (200, {"deleted": tag["id"]})
    assert running.request("/api/tags")[1]["tags"] == []


def test_tags_belong_to_the_workspace_module(tmp_path):
    with site(make_root(tmp_path, ("terminal",))) as server:
        assert server.request("/api/tags")[0] == 403
        assert server.request("/api/tags/create", {"name": "x"})[0] == 403
