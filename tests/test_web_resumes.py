"""Resume HTTP slice contracts use isolated synthetic data and local rendering only."""
from __future__ import annotations

import base64
from contextlib import closing
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from applyai import web_resumes
from applyai.core import resume_editor, resumes, resume_versions
from applyai.db import connect
from applyai.db.connection import transaction
from applyai.web_server import APIError
from test_resume_versions import space  # noqa: F401 — isolated offline rendering fixture


@pytest.fixture
def app(workspace):
    return SimpleNamespace(settings=workspace.settings, config=workspace.config)


def test_registration_requires_confirmation_and_retains_identity(app, workspace):
    payload = {"label": "Synthetic web resume", "upload": base64.b64encode(b"%PDF-1.4\nsynthetic").decode()}
    with pytest.raises(APIError, match="confirmation"):
        web_resumes.post(app, workspace.conn, "/api/resumes/register", payload)
    result = web_resumes.post(app, workspace.conn, "/api/resumes/register", {**payload, "confirm": True})
    stored = resumes.get_resume(workspace.conn, result["resume"]["id"])
    assert Path(stored.file_path).read_bytes() == b"%PDF-1.4\nsynthetic"
    assert resumes.file_hash(Path(stored.file_path)) == stored.file_hash
    library = web_resumes.get(app, workspace.conn, "/api/resumes/workflow", {})
    assert library["resumes"][0]["label"] == "Synthetic web resume"
    assert library["resumes"][0]["default_tracks"] == []


@pytest.mark.parametrize("extra", [
    {"upload": "!!!"}, {"upload": base64.b64encode(b"not pdf").decode()},
    {"upload": 42}, {"path": "absent.pdf"}, {"upload": "", "path": "also.pdf"},
    {"upload": "", "unexpected": True},
])
def test_registration_rejects_bad_inputs(app, workspace, extra):
    with pytest.raises(APIError):
        web_resumes.post(app, workspace.conn, "/api/resumes/register",
                         {"label": "Synthetic", "confirm": True, **extra})
    assert not resumes.list_resumes(workspace.conn)


def test_registration_validates_size_before_decode(app, workspace, monkeypatch):
    monkeypatch.setattr(web_resumes, "MAX_UPLOAD", 8)
    with pytest.raises(APIError, match="exceeds"):
        web_resumes.post(app, workspace.conn, "/api/resumes/register", {
            "label": "Synthetic", "confirm": True, "upload": "A" * 20})


def test_local_registration_and_coach(app, workspace):
    path = workspace.settings.root / "synthetic.pdf"
    path.write_bytes(b"%PDF-1.4\nsynthetic")
    result = web_resumes.post(app, workspace.conn, "/api/resumes/register",
                              {"label": "Local", "path": str(path), "confirm": True})
    assert result["resume"]["label"] == "Local"
    reports = web_resumes.get(app, workspace.conn, "/api/resumes/coach", {})["reports"]
    assert [report["track"] for report in reports] == app.config.track_names
    with pytest.raises(APIError, match="Unknown"):
        web_resumes.get(app, workspace.conn, "/api/resumes/coach", {"track": "invalid"})
    with pytest.raises(APIError):
        web_resumes.get(app, workspace.conn, "/api/resumes/workflow", {"path": str(path)})


def test_generation_requires_confirmation_and_valid_track(app, workspace):
    with pytest.raises(APIError, match="confirmation"):
        web_resumes.post(app, workspace.conn, "/api/resumes/generate", {"track": app.config.track_names[0]})
    with pytest.raises(APIError, match="Unknown"):
        web_resumes.post(app, workspace.conn, "/api/resumes/generate", {"track": "invalid", "confirm": True})
    with pytest.raises(APIError, match="min_postings"):
        web_resumes.post(app, workspace.conn, "/api/resumes/generate", {
            "track": app.config.track_names[0], "confirm": True, "min_postings": True})


@pytest.mark.parametrize("change_default", [False, True])
def test_generation_persists_frozen_evidence_after_render_boundary_write(space, monkeypatch, change_default):
    app = SimpleNamespace(settings=space.settings, config=space.config,
                          tasks=SimpleNamespace(start=lambda kind, work: {"kind": kind, "result": work()}))
    replacement = (resume_versions.create_candidate(space.conn, space.config, space.settings,
                                                     space.track.name, space.source)
                   if change_default else None)
    expected = resume_editor.propose(space.conn, space.config, space.settings, space.track.name, space.source)
    posting = expected["cohort"]["jobs"][0]
    changed_description = "Completely revised synthetic posting without the original terms."
    render = resume_versions.render_pdf
    mutations = []

    def render_after_committed_write(source, pdf):
        # Commit after generation's evidence reads but before its INSERT, without
        # sleeps or threads. A transaction spanning both would fail to upgrade
        # its stale WAL snapshot even though the writer has already committed.
        with closing(connect(space.settings.db_path)) as other, transaction(other):
            other.execute("UPDATE jobs SET jd_text=? WHERE id=?", (changed_description, posting["job_id"]))
            if replacement is not None:
                resume_versions.promote(other, space.config, space.settings, replacement["id"])
        mutations.append(posting["job_id"])
        return render(source, pdf)

    monkeypatch.setattr(resume_versions, "render_pdf", render_after_committed_write)
    result = web_resumes.post(app, space.conn, "/api/resumes/generate", {
        "track": space.track.name, "source": str(space.source), "confirm": True})
    assert result["task"]["kind"] == "resume-generation"
    assert result["task"]["result"]["valid"]
    assert mutations == [posting["job_id"]]
    assert space.conn.execute("SELECT jd_text FROM jobs WHERE id=?", (posting["job_id"],)).fetchone()[0] == changed_description

    # The worker has closed its own connection; the independent reader must see
    # the committed candidate and the original, not newly recomputed, evidence.
    route = "/api/resumes/versions/" + result["task"]["result"]["version_id"]
    stored = web_resumes.get(app, space.conn, route, {})["version"]
    assert stored["status"] == "candidate" and stored["resume_id"] is None
    assert stored["validation"]["valid"] and not stored["is_default"]
    assert stored["baseline"] == {"resume_id": space.baseline.id, "file_hash": space.baseline.file_hash,
                                  "history_id": None, "decision_id": None}
    assert {key: value for key, value in stored["cohort"].items() if key != "timestamp"} == {
        key: value for key, value in expected["cohort"].items() if key != "timestamp"}
    for key in ("source_hash", "original_html", "candidate_html", "patches", "facts",
                "review_items", "metrics", "diff", "warnings"):
        assert stored[key] == expected[key]
    audit, _, _ = web_resumes.download(app, space.conn, route + "/audit", {})
    assert json.loads(audit) == stored

    if change_default:
        # A changed default still blocks promotion; successful persistence must
        # not silently rebase the candidate onto the newly selected resume.
        current = resume_versions.get_version(space.conn, space.settings, replacement["id"])
        assert resume_versions.default_resume(space.conn, space.config, space.track.name).id == current["resume_id"]
        with pytest.raises(APIError, match="default changed"):
            web_resumes.post(app, space.conn, route + "/promote", {"confirm": True})
        assert web_resumes.get(app, space.conn, route, {})["version"]["status"] == "candidate"
    else:
        assert resume_versions.default_resume(space.conn, space.config, space.track.name).id == space.baseline.id
        # Cohort changes do not invalidate an explicitly reviewed, frozen audit.
        promoted = web_resumes.post(app, space.conn, route + "/promote", {"confirm": True})["version"]
        assert promoted["is_default"] and promoted["cohort"] == stored["cohort"]
        assert promoted["patches"] == stored["patches"]


def test_exact_audit_download_decisions_and_rollback(space):
    app = SimpleNamespace(settings=space.settings, config=space.config)
    item = resume_versions.create_candidate(space.conn, space.config, space.settings,
                                            space.track.name, space.source)
    route = "/api/resumes/versions/" + item["id"]
    review = web_resumes.get(app, space.conn, route, {})["version"]
    assert review["diff"] == item["diff"] and review["facts"] == item["facts"]
    assert review["patches"] == item["patches"] and review["warnings"] == item["warnings"]
    audit, content_type, filename = web_resumes.download(app, space.conn, route + "/audit", {})
    assert content_type == "application/json" and filename.endswith("-audit.json")
    assert json.loads(audit)["cohort"] == item["cohort"]
    pdf, content_type, filename = web_resumes.download(app, space.conn, route + "/pdf", {})
    assert pdf == Path(item["pdf_path"]).read_bytes() and content_type == "application/pdf"
    with pytest.raises(APIError, match="confirmation"):
        web_resumes.post(app, space.conn, route + "/promote", {})
    promoted = web_resumes.post(app, space.conn, route + "/promote", {"confirm": True})
    assert promoted["version"]["is_default"]
    restored = web_resumes.post(app, space.conn, "/api/resumes/rollback",
                                {"track": space.track.name, "confirm": True})
    assert restored["default"]["id"] == space.baseline.id
    other = resume_versions.create_candidate(space.conn, space.config, space.settings,
                                             space.track.name, space.source)
    rejected = web_resumes.post(app, space.conn, "/api/resumes/versions/" + other["id"] + "/reject",
                                {"confirm": True})
    assert rejected["version"]["status"] == "rejected"
    assert Path(other["pdf_path"]).is_file()


def test_changed_candidate_pdf_cannot_be_previewed_or_promoted(space):
    app = SimpleNamespace(settings=space.settings, config=space.config)
    item = resume_versions.create_candidate(space.conn, space.config, space.settings,
                                            space.track.name, space.source)
    path = Path(item["pdf_path"])
    path.chmod(0o600)
    path.write_bytes(b"%PDF-1.4\nchanged synthetic artifact")
    route = "/api/resumes/versions/" + item["id"]
    assert not web_resumes.get(app, space.conn, route, {})["version"]["validation"]["valid"]
    with pytest.raises(APIError, match="identity"):
        web_resumes.download(app, space.conn, route + "/pdf", {})
    with pytest.raises(APIError, match="validation"):
        web_resumes.post(app, space.conn, route + "/promote", {"confirm": True})
