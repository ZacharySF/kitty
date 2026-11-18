"""Whole-workspace backup: one archive with a consistent database snapshot, the files rows
point at and the settings files, restored into a separate folder. Synthetic data only."""
from __future__ import annotations

import base64
import http.client
import io
import json
import os
import sqlite3
import threading
import uuid
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from kitty import web_auth
from kitty.core import (
    applications,
    backup,
    block_api,
    calendar,
    documents,
    flashcards,
    jobs,
    notes,
    resumes,
)
from kitty.core.blocks import store as blocks
from kitty.core.builds import store
from kitty.core.workspace import open_workspace
from kitty.db import connect, latest_version, pending_migrations
from kitty.settings import Settings
from kitty.web_server import LocalHTTPServer, WebApplication

#: Strings that stand for credentials; none may reach an archive or a restored folder.
SECRETS = ("sk-or-SYNTHETIC-file-key", "sk-env-SYNTHETIC-dotenv", "SYNTHETIC-gmail-refresh",
           "SYNTHETIC-client-secret", "SYNTHETIC-owner-token", "SYNTHETIC-tern-token-hash",
           "SYNTHETIC-phone-token-hash", "SYNTHETIC-microsoft-token")
#: Tables a backup deliberately changes in its copy (pairings, sign-ins, live handoffs).
SCRUBBED = {"peers", "clients", "phones", "application_email_verification_requests", "repo_sync_config"}


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (24, 16), "#3355aa").save(stream, format="PNG")
    return stream.getvalue()


def page(conn, title, *lines):
    page_id = str(uuid.uuid4())
    ops = [{"op": "insert", "id": page_id, "type": "page", "parent": None, "text": title, "props": {"revision": 1}}]
    ops += [{"op": "insert", "id": str(uuid.uuid4()), "type": "text", "parent": page_id, "text": line} for line in lines]
    blocks.transact(conn, ops)
    return page_id


@pytest.fixture(autouse=True)
def restore_environment():
    """Settings load the planted .env into os.environ; keep its fake key out of later tests."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def away(tmp_path_factory):
    """A folder outside the workspace (the workspace fixture's root is tmp_path itself)."""
    return tmp_path_factory.mktemp("away")


@pytest.fixture
def seeded(workspace, tmp_path_factory):
    """The workspace with one of everything a student keeps, and credentials around it."""
    conn, settings = workspace.conn, workspace.settings
    root = settings.root
    store.write_text(root, store.initial_text(["applyai", "workspace"]))
    page(conn, "Lecture 7: Krebs cycle", "Citrate forms first", "NADH carries electrons")
    attachment = notes.register(conn, settings.data_dir, base64.b64encode(png()).decode(), "Diagram.png")[0]
    documents.import_notes(conn, settings.data_dir, {"files": [{"name": "Reading.md", "data": base64.b64encode(
        b"# Glycolysis\n\nTen steps.").decode()}]})
    calendar.create_event(conn, {"title": "Biology midterm", "start": "2030-03-02T09:00:00Z", "end": "2030-03-02T11:00:00Z",
                                 "timezone": "UTC", "all_day": False})
    calendar.save_task(conn, {"title": "Revise chapter 9"})
    block_api.quick_note(conn, "Buy lab goggles")
    documents.save(conn, {"title": "Essay draft", "content": "# Thesis\n\nEnzymes lower activation energy.",
                          "format": "markdown", "notebook_id": None})
    flashcards.bootstrap(conn)
    deck = flashcards.save_deck(conn, {"name": "Synthetic biology"})
    basic = conn.execute("SELECT id FROM flash_types WHERE kind='basic' ORDER BY id LIMIT 1").fetchone()[0]
    note = flashcards.save_note(conn, {"deck_id": deck["id"], "type_id": basic, "fields": {"Front": "ATP", "Back": "Energy"}})
    card = conn.execute("SELECT id FROM flash_cards WHERE note_id=?", (note["id"],)).fetchone()[0]
    conn.execute("INSERT INTO flash_reviews(card_id,deck_id,rating,reviewed_at,local_day,duration_ms,mode,before_state) "
                 "VALUES(?,?,3,'2026-11-01T09:00:00+00:00','2026-11-01',1200,'regular','{}')", (card, deck["id"]))
    job, _ = jobs.upsert_job(conn, company="Example Labs", title="Research intern", url="https://example.invalid/intern")
    resume_source = tmp_path_factory.mktemp("outside") / "Resume.pdf"
    resume_source.write_bytes(b"%PDF-1.4 synthetic resume")
    imported = resumes.import_resume(conn, root, resume_source, "Biology")
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "Cover.pdf"
    elsewhere.write_bytes(b"%PDF-1.4 synthetic cover letter")
    resumes.add_resume(conn, "Cover", elsewhere)
    applications.record_application(conn, job.id, resume_id=imported.id, notes="Asked about lab rotation")

    # Credentials: key files, .env, OAuth tokens, pairings and a live verification handoff.
    (root / ".private" / "keys").mkdir(parents=True)
    (root / ".private" / "keys" / "ANTHROPIC").write_text(SECRETS[0])
    (root / ".private" / "microsoft_token.json").write_text(json.dumps({"refresh_token": SECRETS[7]}))
    (root / ".env").write_text(f"OPENROUTER_API_KEY={SECRETS[1]}\n")
    (root / "data" / "gmail_token.json").write_text(json.dumps({"refresh_token": SECRETS[2]}))
    (root / "config" / "gmail_client.json").write_text(json.dumps({"installed": {"client_secret": SECRETS[3]}}))
    conn.execute("INSERT INTO application_email_verification_requests(job_id,owner_token,origin,sender_email,recipient_binding,"
                 "requested_at,expires_at,updated_at) VALUES(?,?,'https://example.invalid','hr@example.invalid','me',"
                 "'2026-11-01','2030-01-01','2026-11-01')", (job.id, SECRETS[4]))
    conn.execute("INSERT INTO clients(id,name,kind,scopes,token_hash,created_at) VALUES('c1','Tern','tern-plugin','[]',?,"
                 "'2026-11-01')", (SECRETS[5],))
    conn.execute("INSERT INTO phones(id,name,scopes,token_hash,created_at) VALUES('p1','Phone','[]',?,'2026-11-01')",
                 (SECRETS[6],))
    conn.execute("INSERT INTO peers(device_id,name,public_key,paired_at) VALUES(?, 'Laptop', ?, '2026-11-01')",
                 ("0" * 32, b"\x01" * 32))
    conn.execute("UPDATE repo_sync_config SET owner='zed', repo='notes', synced_seq=3 WHERE id=1")
    return workspace, attachment


def rows(path: Path, table: str) -> list[tuple]:
    with sqlite3.connect(path) as db:
        return db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def tables(path: Path) -> list[str]:
    with sqlite3.connect(path) as db:
        return [name for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND sql NOT LIKE 'CREATE VIRTUAL%' ORDER BY name")]


def moved(row: tuple, source: Path, target: Path, relocated: dict[str, str]) -> tuple:
    """A row as it should read after restore: paths under the old root point into the new one,
    files that lived outside it into the new root's data/restored-files."""
    return tuple(relocated.get(value, value.replace(str(source), str(target))) if isinstance(value, str) else value
                 for value in row)


def files_under(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_restore_reproduces_notes_files_tasks_study_and_career_without_secrets(seeded, away):
    workspace, attachment = seeded
    settings, root = workspace.settings, workspace.settings.root
    made = backup.create(settings, away / "backups")
    archive = Path(made["path"])
    assert archive.parent == away / "backups" and archive.name.startswith("rika-backup-") and archive.suffix == ".zip"

    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read(backup.MANIFEST))
        everything = archive.read_bytes() + b"".join(bundle.read(name) for name in bundle.namelist())
    assert manifest["format"] == backup.FORMAT and manifest["schema_version"] == latest_version()
    assert manifest["app_version"] and manifest["created_at"]
    for secret in SECRETS:
        assert secret.encode() not in everything, secret
    excluded = {item["category"] for item in manifest["excluded"]}
    assert {"Keys and passwords", "Sign-ins", ".env", "config/gmail_client.json", "Paired devices",
            "Paired programs and phones", "GitHub backup connection"} <= excluded

    target = away / "restored"
    report = backup.restore(archive, target, live_root=root)
    assert report["path"] == str(target) and report["counts"] == made["counts"]
    restored_db = target / "data" / "apply.db"

    # Every table outside the scrubbed ones is identical, with paths moved to the new root.
    source_db = settings.db_path
    assert tables(restored_db) == tables(source_db)
    for table in tables(source_db):
        if table in SCRUBBED:
            continue
        assert rows(restored_db, table) == [moved(row, root, target, report["relocated"]) for row in rows(source_db, table)], table
    with sqlite3.connect(restored_db) as db:
        assert db.execute("SELECT count(*) FROM peers").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM application_email_verification_requests").fetchone()[0] == 0
        assert db.execute("SELECT owner, repo, synced_seq FROM repo_sync_config").fetchone() == (None, None, 0)
        assert all(revoked for (revoked,) in db.execute("SELECT revoked_at FROM clients UNION ALL SELECT revoked_at FROM phones"))
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    # Attachment, document original and resume bytes are equal and reachable through the rows.
    kept = files_under(root / "data")
    restored = files_under(target / "data")
    for name, data in kept.items():
        if name.startswith(("notes-attachments", "document-originals")):
            assert restored[name] == data, name
    assert (target / "resumes" / "Resume.pdf").read_bytes() == (root / "resumes" / "Resume.pdf").read_bytes()
    with connect(restored_db) as conn:
        assert notes.attachment_bytes(conn, target / "data", attachment["id"]) == notes.attachment_bytes(
            workspace.conn, settings.data_dir, attachment["id"])
        cover = Path(conn.execute("SELECT file_path FROM resumes WHERE label='Cover'").fetchone()[0])
        assert cover.is_relative_to(target) and cover.read_bytes() == b"%PDF-1.4 synthetic cover letter"
        assert all(Path(path).is_file() for (path,) in conn.execute("SELECT file_path FROM resumes"))
        assert pending_migrations(conn) == []

    # Secrets are absent; the settings files came along.
    assert not (target / ".private").exists() and not (target / ".env").exists()
    assert not (target / "data" / "gmail_token.json").exists() and not (target / "config" / "gmail_client.json").exists()
    for path in target.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert not any(secret.encode() in data for secret in SECRETS), path
    assert (target / "config" / "build.json").read_bytes() == (root / "config" / "build.json").read_bytes()
    assert (target / "config" / "tracks.yaml").read_bytes() == (root / "config" / "tracks.yaml").read_bytes()

    # The restored folder opens as a workspace; the live one is untouched.
    reopened = open_workspace(Settings.load(root=target))
    try:
        assert reopened.conn.execute("SELECT title FROM jobs").fetchone()[0] == "Research intern"
    finally:
        reopened.close()
    assert (root / ".private" / "keys" / "ANTHROPIC").read_text() == SECRETS[0]


def test_preview_counts_and_warnings(seeded, away):
    workspace, _ = seeded
    archive = Path(backup.create(workspace.settings, away / "out")["path"])
    seen = backup.preview(archive, away / "fresh", live_root=workspace.settings.root)
    counts = seen["counts"]
    assert counts["Note pages"] >= 1 and counts["Note attachments"] == 1 and counts["Document originals"] == 1
    assert counts["Quick notes"] == 1 and counts["Documents"] == 1
    assert counts["Calendar events"] == 1 and counts["Tasks"] == 1
    assert counts["Flashcards"] == 1 and counts["Flashcard reviews"] == 1
    assert counts["Jobs"] == 1 and counts["Applications"] == 1 and counts["Resumes"] == 2
    assert seen["files"]["count"] >= 4 and seen["files"]["bytes"] > 0
    assert seen["can_restore"] is True and seen["problems"] == []
    assert any(item["category"] == "Keys and passwords" for item in seen["excluded"])
    assert all("version" not in warning for warning in seen["warnings"])

    def rewritten(name, change):
        path = away / name
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(path, "w") as copy:
            for item in source.infolist():
                data = source.read(item)
                if item.filename == backup.MANIFEST:
                    manifest = json.loads(data)
                    change(manifest)
                    data = json.dumps(manifest).encode()
                if data is not None:
                    copy.writestr(item, data)
        return path

    future = rewritten("future.zip", lambda manifest: manifest.update(format=backup.FORMAT + 1))
    seen = backup.preview(future, away / "fresh", live_root=workspace.settings.root)
    assert seen["can_restore"] is False and any("newer" in problem for problem in seen["problems"])
    with pytest.raises(backup.BackupError, match="newer"):
        backup.restore(future, away / "fresh", live_root=workspace.settings.root)
    assert not (away / "fresh").exists()

    other = rewritten("other.zip", lambda manifest: manifest.update(app_version="0.0.1-synthetic"))
    seen = backup.preview(other, None, live_root=workspace.settings.root)
    assert seen["can_restore"] is True and any("0.0.1-synthetic" in warning for warning in seen["warnings"])

    def drop_attachment(manifest):
        manifest["files"].append({"path": "files/data/notes-attachments/missing.png", "size": 3, "sha256": "0" * 64})
    gone = rewritten("gone.zip", drop_attachment)
    seen = backup.preview(gone, None, live_root=workspace.settings.root)
    assert seen["can_restore"] is False and any("missing.png" in problem for problem in seen["problems"])


def test_a_file_missing_at_backup_time_is_listed(seeded, away):
    workspace, attachment = seeded
    (workspace.settings.data_dir / "notes-attachments" / f"{attachment['id']}.png").unlink()
    made = backup.create(workspace.settings, away / "out")
    assert any(attachment["id"] in item for item in made["missing"])
    seen = backup.preview(Path(made["path"]), None, live_root=workspace.settings.root)
    assert seen["can_restore"] is True and any(attachment["id"] in warning for warning in seen["warnings"])


def test_restore_refuses_the_live_workspace_and_folders_that_are_not_empty(seeded, away):
    workspace, _ = seeded
    root = workspace.settings.root
    archive = Path(backup.create(workspace.settings, away / "out")["path"])
    before = files_under(root / "config")
    with pytest.raises(backup.BackupError, match="open workspace"):
        backup.restore(archive, root, live_root=root)
    with pytest.raises(backup.BackupError, match="open workspace"):
        backup.restore(archive, root / "data" / "copy", live_root=root)
    busy = away / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("mine")
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore(archive, busy, live_root=root)
    assert files_under(busy) == {"keep.txt": b"mine"}
    assert files_under(root / "config") == before and not (root / "data" / "copy").exists()
    seen = backup.preview(archive, busy, live_root=root)
    assert seen["can_restore"] is False and any("not empty" in problem for problem in seen["problems"])
    empty = away / "empty"
    empty.mkdir()
    assert backup.restore(archive, empty, live_root=root)["path"] == str(empty)
    assert (empty / "data" / "apply.db").is_file()
    with pytest.raises(backup.BackupError, match="full path"):
        backup.restore(archive, Path("relative/place"), live_root=root)


def test_the_snapshot_is_consistent_while_another_connection_writes(seeded, away):
    """A half-finished write is not in the snapshot: the database is read through SQLite, not copied."""
    workspace, _ = seeded
    writer = sqlite3.connect(workspace.settings.db_path, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO calendar_tasks(id,data,updated_at) VALUES('uncommitted','{}','2026-11-17')")
        archive = Path(backup.create(workspace.settings, away / "out")["path"])
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    target = away / "restored"
    backup.restore(archive, target, live_root=workspace.settings.root)
    with sqlite3.connect(target / "data" / "apply.db") as db:
        assert db.execute("SELECT count(*) FROM calendar_tasks WHERE id='uncommitted'").fetchone()[0] == 0


def test_archive_members_cannot_escape_the_destination(seeded, away):
    workspace, _ = seeded
    archive = Path(backup.create(workspace.settings, away / "out")["path"])
    evil = away / "evil.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(evil, "w") as copy:
        for item in source.infolist():
            data = source.read(item)
            if item.filename == backup.MANIFEST:
                manifest = json.loads(data)
                manifest["files"].append({"path": "files/../../escaped.txt", "size": 1, "sha256": "0" * 64})
                data = json.dumps(manifest).encode()
            copy.writestr(item, data)
        copy.writestr("files/../../escaped.txt", b"x")
    with pytest.raises(backup.BackupError):
        backup.restore(evil, away / "target", live_root=workspace.settings.root)
    assert not (away / "escaped.txt").exists() and not (away / "target").exists()


# Routes ---------------------------------------------------------------------------

def serve(settings, config, away):
    static = away / "dist"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>Synthetic website</title>")
    app = WebApplication(settings, config, static, ui_session="ui-test")
    server = LocalHTTPServer(app, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None):
        headers = {"Host": server.authority, "Origin": server.origin, web_auth.HEADER: server.ui_auth.secret}
        payload = None
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-Kitty-Request": "1"})
            payload = json.dumps(body).encode()
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=60)
        try:
            client.request("GET" if body is None else "POST", path, body=payload, headers=headers)
            response = client.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            client.close()

    def close():
        server.shutdown()
        server.server_close()
        app.close()
        worker.join(timeout=5)

    return request, close


def test_routes_create_preview_and_restore(seeded, away, monkeypatch):
    workspace, _ = seeded
    monkeypatch.setenv("HOME", str(away / "home"))
    request, close = serve(workspace.settings, workspace.config, away)
    try:
        status, state = request("/api/backup")
        assert status == 200 and state["last"] is None
        assert state["folder"] == str(away / "home" / "rika-backups") and state["root"] == str(workspace.settings.root)
        assert request("/api/backup/create", {"folder": "relative"})[0] == 400
        assert request("/api/backup/create", {})[0] == 400
        status, made = request("/api/backup/create", {"folder": "~/rika-backups"})
        assert status == 200 and Path(made["path"]).parent == away / "home" / "rika-backups"
        assert request("/api/backup")[1]["last"]["path"] == made["path"]

        status, seen = request("/api/backup/preview", {"archive": made["path"], "into": str(away / "copy")})
        assert status == 200 and seen["can_restore"] and seen["counts"]["Jobs"] == 1
        status, refused = request("/api/backup/restore", {"archive": made["path"], "into": str(workspace.settings.root)})
        assert status == 409 and "open workspace" in refused["error"]
        status, missing = request("/api/backup/preview", {"archive": str(away / "nope.zip"), "into": None})
        assert status == 400 and "not found" in missing["error"].lower()
        status, done = request("/api/backup/restore", {"archive": made["path"], "into": str(away / "copy")})
        assert status == 200 and done["path"] == str(away / "copy") and (away / "copy" / "data" / "apply.db").is_file()
        assert str(away / "copy") in done["open"]
    finally:
        close()


# CLI ------------------------------------------------------------------------------

def test_cli_creates_previews_and_restores(seeded, away, monkeypatch):
    from typer.testing import CliRunner

    from kitty import cli

    workspace, _ = seeded
    monkeypatch.setenv("KITTY_HOME", str(workspace.settings.root))
    monkeypatch.setenv("KITTY_DB", str(workspace.settings.db_path))
    runner = CliRunner()
    made = runner.invoke(cli.app, ["backup", "create", "--out", str(away / "out")])
    assert made.exit_code == 0, made.output
    archive = next((away / "out").glob("rika-backup-*.zip"))
    assert "Excluded" in made.output and str(archive) in made.output.replace("\n", "")
    seen = runner.invoke(cli.app, ["backup", "preview", str(archive), "--into", str(away / "copy")])
    assert seen.exit_code == 0, seen.output
    assert "Jobs" in seen.output and "Flashcard reviews" in seen.output
    refused = runner.invoke(cli.app, ["backup", "restore", str(archive), "--into", str(workspace.settings.root)])
    assert refused.exit_code == 1 and "open workspace" in refused.output
    done = runner.invoke(cli.app, ["backup", "restore", str(archive), "--into", str(away / "copy")])
    assert done.exit_code == 0, done.output
    assert "KITTY_HOME=" in done.output and (away / "copy" / "data" / "apply.db").is_file()
    assert os.stat(away / "copy" / "data" / "apply.db").st_mode & 0o077 == 0
