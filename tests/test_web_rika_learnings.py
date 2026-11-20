"""Rika's career memory over HTTP (kitty.web_rika_learnings): strict fields, synthetic rows only."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kitty import web_rika_learnings as routes
from kitty.core.rika import learnings
from kitty.web_server import EXTENSIONS, APIError
from test_rika_learnings import VOCABULARY, _job


@pytest.fixture
def app(workspace):
    return SimpleNamespace(settings=workspace.settings, config=SimpleNamespace(resume_terms=VOCABULARY),
                           modules=("applyai", "workspace"))


def _status(call) -> int:
    with pytest.raises(APIError) as caught:
        call()
    return caught.value.status


def test_the_routes_run_with_applyai():
    assert ("applyai", "web_rika_learnings") in EXTENSIONS


def test_list_refreshes_and_names_the_evidence_jobs(app, workspace):
    c = workspace.conn
    job = _job(c, "a", "Python and Kubernetes.", state="queued", title="Platform intern")
    body = routes.get(app, c, "/api/rika/learnings", {})
    assert set(body) == {"learnings", "jobs", "refreshed_at", "forgotten"}
    skills = {item["text"]: item for item in body["learnings"] if item["kind"] == "posting_skill"}
    assert skills["Python"]["evidence"] == [job]
    assert body["jobs"] == {str(job): {"title": "Platform intern", "company": "Synthetic Labs"}}
    assert body["forgotten"] == 0 and body["refreshed_at"]
    assert _status(lambda: routes.get(app, c, "/api/rika/learnings", {"q": "x"})) == 400
    assert routes.get(app, c, "/api/rika/memory", {}) is None


def test_notes_pins_and_forgets(app, workspace):
    c = workspace.conn
    _job(c, "a", "Python.", state="applied")
    note = routes.post(app, c, "/api/rika/learnings", {"text": "Prefer hardware teams"})["learning"]
    assert note["text"] == "Prefer hardware teams"
    python = next(i for i in routes.get(app, c, "/api/rika/learnings", {})["learnings"] if i["text"] == "Python")
    pinned = routes.post(app, c, f"/api/rika/learnings/{python['id']}/pin", {"pinned": True})["learning"]
    assert pinned["pinned"] is True
    assert routes.post(app, c, f"/api/rika/learnings/{python['id']}/forget", {}) == {"forgotten": python["id"]}
    body = routes.get(app, c, "/api/rika/learnings", {})
    assert [item["text"] for item in body["learnings"]] == ["Prefer hardware teams"]
    assert body["forgotten"] == 1
    assert _status(lambda: routes.post(app, c, f"/api/rika/learnings/{python['id']}/forget", {})) == 404
    assert routes.post(app, c, f"/api/rika/learnings/{note['id']}/forget", {}) == {"forgotten": note["id"]}
    assert learnings.learnings(c) == []


@pytest.mark.parametrize("path,body", [
    ("/api/rika/learnings", {}),
    ("/api/rika/learnings", {"text": ""}),
    ("/api/rika/learnings", {"text": 5}),
    ("/api/rika/learnings", {"text": "ok", "pinned": True}),
    ("/api/rika/learnings/1/pin", {}),
    ("/api/rika/learnings/1/pin", {"pinned": "true"}),
    ("/api/rika/learnings/1/pin", {"pinned": True, "extra": 1}),
    ("/api/rika/learnings/1/forget", {"confirm": True}),
])
def test_bad_bodies_are_refused(app, workspace, path, body):
    learnings.add_note(workspace.conn, "A note")
    assert _status(lambda: routes.post(app, workspace.conn, path, body)) == 400


@pytest.mark.parametrize("path", ["/api/rika/learnings/0/pin", "/api/rika/learnings/01/forget",
                                  "/api/rika/learnings/١/forget", "/api/rika/learnings/x/pin",
                                  "/api/rika/learnings/1/delete", "/api/rika/learnings/1"])
def test_unknown_paths_are_not_answered(app, workspace, path):
    assert routes.post(app, workspace.conn, path, {}) is None


def test_missing_learnings_are_404(app, workspace):
    assert _status(lambda: routes.post(app, workspace.conn, "/api/rika/learnings/99/pin", {"pinned": True})) == 404
    assert _status(lambda: routes.post(app, workspace.conn, "/api/rika/learnings/99/forget", {})) == 404


def test_a_duplicate_note_is_refused(app, workspace):
    routes.post(app, workspace.conn, "/api/rika/learnings", {"text": "Remote first"})
    assert _status(lambda: routes.post(app, workspace.conn, "/api/rika/learnings", {"text": "remote  first"})) == 409


def test_the_front_forwards_the_routes_to_the_worker(tmp_path):
    """rika-server answers /api/rika/memory itself but not these: a note round-trips through it."""
    import shutil
    from pathlib import Path

    from terminal_support import front_site, make_root

    settings = make_root(tmp_path, ("applyai",))
    examples = Path(__file__).resolve().parents[1] / "config"
    for name in ("profile", "tracks", "sources", "watchlist", "answers", "views", "resume_terms"):
        shutil.copy(examples / f"{name}.yaml.example", settings.config_dir / f"{name}.yaml")
    with front_site(settings) as running:
        status, body = running.request("/api/rika/learnings", {"text": "Prefer hardware teams"})
        assert status == 200 and body["learning"]["kind"] == "note"
        status, body = running.request("/api/rika/learnings")
        assert status == 200 and [item["text"] for item in body["learnings"]] == ["Prefer hardware teams"]
        status, _ = running.request(f"/api/rika/learnings/{body['learnings'][0]['id']}/pin", {"pinned": "yes"})
        assert status == 400
