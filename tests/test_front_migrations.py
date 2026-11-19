"""rika-server migrates the workspace before it starts the worker (server/src/startup.rs).

The worker opens the database without migrating (``open_workspace(auto_migrate=False)``) and
refuses one that is behind. A workspace from rika! 0.3.33 or older whose notes still have to
become blocks is converted by the Python runner, which the front runs once through the worker
program; without a worker program the front names the release that converts it. Every
workspace here is a synthetic temporary directory.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from kitty.core.workspace import open_workspace
from kitty.db import connect, migrate
from kitty.db.migrations import available_migrations, pending_migrations
from kitty.settings import Settings
from terminal_support import RIKA_SERVER, front_site

WORKER = ["--worker-program", sys.executable, "--worker-arg", "-c", "--worker-arg", "from kitty.web_server import main; main()"]
#: Main's v0.3.33 schema: before the block backend (032/033, 038-043).
MAIN_SCHEMA = {*range(1, 21), *range(22, 32), 34, 35, 36, 37, 44, 45, 49}


@pytest.fixture(autouse=True)
def _built():
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER}); run cargo build --release in server/")


def make_root(tmp_path: Path) -> Settings:
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": ["workspace"]}), encoding="utf-8")
    return Settings.load(root=root, db_path=root / "data" / "web.db")


def pre_block_database(settings: Settings, tmp_path: Path) -> None:
    """A database at main's v0.3.33 schema holding one quick note (033 converts it)."""
    folder = tmp_path / "main-files"
    folder.mkdir()
    for migration in available_migrations():
        if migration.version in MAIN_SCHEMA:
            shutil.copy(migration.path, folder / migration.path.name)
    with closing(connect(settings.db_path)) as conn:
        migrate(conn, folder)
        conn.execute("INSERT INTO quick_notes(body, created_at, updated_at) VALUES ('Synthetic note', 't', 't')")


def environment(settings: Settings) -> dict[str, str]:
    return {**os.environ, "KITTY_DB": str(settings.db_path)}


def test_the_front_migrates_a_fresh_workspace_before_the_worker_opens_it(tmp_path):
    settings = make_root(tmp_path)
    assert not settings.db_path.exists()
    with front_site(settings) as running:
        assert running.request("/api/bootstrap")[0] == 200
        with closing(connect(settings.db_path)) as conn:
            assert pending_migrations(conn) == []


def test_the_worker_refuses_a_database_the_front_did_not_migrate(tmp_path):
    settings = make_root(tmp_path)
    with pytest.raises(ValueError, match="rika-server has not applied"):
        open_workspace(settings, auto_migrate=False)


def test_a_pre_block_workspace_is_converted_by_the_python_runner(tmp_path):
    settings = make_root(tmp_path)
    pre_block_database(settings, tmp_path)
    with front_site(settings) as running:
        assert running.request("/api/bootstrap")[0] == 200
        with closing(connect(settings.db_path)) as conn:
            assert pending_migrations(conn) == []
            assert conn.execute("SELECT text FROM blocks WHERE type='quick_note'").fetchone()[0] == "Synthetic note"
    assert list((settings.data_dir / "exports").glob("notes-before-blocks-*.json")), "033 exported the notes first"


def test_without_the_python_backend_a_pre_block_workspace_names_the_release_that_converts_it(tmp_path):
    settings = make_root(tmp_path)
    pre_block_database(settings, tmp_path)
    result = subprocess.run(
        [str(RIKA_SERVER), "--port", "0", "--root", str(settings.root), "--static-dir", str(tmp_path),
         "--worker-program", str(tmp_path / "no-python-backend")],
        capture_output=True, text=True, timeout=60, env=environment(settings))
    assert result.returncode == 1 and result.stdout == ""
    assert "open the workspace once with rika! 0.3.36" in result.stderr
    with closing(connect(settings.db_path)) as conn:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        assert 32 in applied and 33 not in applied, "Rust applied what precedes the conversion and stopped"
        assert conn.execute("SELECT body FROM quick_notes").fetchone()[0] == "Synthetic note"


def test_the_demo_workspace_is_made_and_removed_by_the_front(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    process = subprocess.Popen(
        [str(RIKA_SERVER), "--port", "0", "--demo", "--static-dir", str(tmp_path), *WORKER],
        stdout=subprocess.PIPE, text=True, env={**os.environ, "TMPDIR": str(folder)})
    try:
        line = process.stdout.readline()
        assert "synthetic demo website" in line, line
        (demo,) = folder.glob("kitty-web-demo-*")
        assert (demo / "data" / "demo.db").is_file() and (demo / "config" / "tracks.yaml").is_file()
        assert demo.stat().st_mode & 0o777 == 0o700
    finally:
        process.terminate()
        process.wait(timeout=40)
    assert not list(folder.glob("kitty-web-demo-*"))
