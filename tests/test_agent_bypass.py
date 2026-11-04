"""The bypass battery (paper §13 "Falsifiable tests"): inside a launched, confined agent
every route around rika!'s API fails with EACCES, ENOENT or EPERM, and the mediated route
works and is journaled.

A real kitty-web (synthetic workspace) registers a launch; the real launcher
(``python -I -m kitty.agent_launch``) trades the nonce, confines itself with Landlock
and seccomp and execs tests/agent_battery.py, which tries each route. Each check needs
some Landlock ABI; on a kernel below it the check is skipped with that reason (and the
whole battery when Landlock is missing).
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path

import pytest
from terminal_support import make_root, site

from kitty.core import block_api, confine
from kitty.core.blocks.actor import Actor, acting
from kitty.db import connect

ABI = confine.abi()
BATTERY = Path(__file__).with_name("agent_battery.py")
#: check -> the Landlock ABI it needs (0: seccomp or rika!'s own HTTP checks only).
CHECKS = {
    "db_open": 1, "db_sqlite3": 1, "db_wal": 1, "db_shm": 1, "private_read": 1, "answers_read": 1, "profile_read": 1,
    "resume_read": 1, "env_write": 1, "env_read": 1, "export_read": 1, "git_hook_write": 1, "envrc_plant": 1,
    "server_memory": 1, "tcp_other_port": 4, "abstract_socket": 6, "signal_server": 6, "herdr_socket": 9,
    "tern_socket": 9, "dbus_socket": 9, "tiocsti": 1, "tiocsti_reopened": 1, "forged_request": 1,
    "cli_without_token": 1, "mcp_without_token": 1, "notes_read_with_token": 1, "notes_write_with_token": 1,
    "environment": 1,
}
pytestmark = pytest.mark.skipif(ABI < 1, reason="Landlock is not available on this kernel (or is disabled): "
                                                "the battery needs a confined launch")


def _listen_unix(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen(4)
    return server


def _accepting(server: socket.socket) -> None:
    def loop():
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            connection.close()
    threading.Thread(target=loop, daemon=True).start()


@pytest.fixture(scope="module")
def battery(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bypass")
    settings = make_root(tmp, ("workspace", "terminal"))
    root = settings.root
    (root / ".private").mkdir()
    (root / ".private" / "openrouter_key").write_text("sk-synthetic\n")
    (root / "config" / "answers.yaml").write_text("synthetic: answer\n")
    (root / "config" / "profile.yaml").write_text("name: Synthetic Applicant\n")
    (root / "resumes").mkdir()
    (root / "resumes" / "synthetic.pdf").write_bytes(b"%PDF-1.4 synthetic")
    (root / ".env").write_text("KITTY_DB=data/web.db\n")
    (root / "data" / "exports").mkdir(parents=True)
    (root / "data" / "exports" / "notes-before-blocks.json").write_text("[]")
    (root / ".git" / "hooks").mkdir(parents=True)
    (root / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n")
    (root / "project").mkdir()
    # The agent runs from the tab's project folder (the tests folder is not granted).
    (root / "project" / "battery.py").write_text(BATTERY.read_text())
    sockets = tmp / "sockets"
    runtime = tmp / "runtime"
    herdr = _listen_unix(sockets / "herdr" / "sessions" / "kitty" / "herdr.sock")
    tern = _listen_unix(runtime / "kitty" / "tern-test" / "daemon.sock")
    dbus = _listen_unix(runtime / "bus")
    abstract = socket.socket(socket.AF_UNIX)
    abstract.bind(f"\0kitty-battery-{os.getpid()}")
    abstract.listen(4)
    other = socket.socket()
    other.bind(("127.0.0.1", 0))
    other.listen(4)
    for server in (herdr, tern, dbus, abstract, other):
        _accepting(server)
    with site(settings) as running:
        app = running.app
        app.agent_launches.runtime_dir = runtime
        with closing(connect(settings.db_path)) as conn, acting(Actor("user")):
            page = block_api.create_page(conn, "Synthetic page", blocks=["The synthetic source block"])
        # Keep the WAL and shared-memory files on disk while the agent runs.
        holder = connect(settings.db_path)
        holder.execute("SELECT 1 FROM op_log LIMIT 1").fetchall()
        with closing(connect(settings.db_path)) as conn:
            launch = app.agent_launches.prepare(conn, host="direct", kind="script", cwd=root / "project",
                                                ancestor=lambda pane: os.getpid(), origin=app.origin)
        spec = {"db": str(settings.db_path), "private": str(root / ".private" / "openrouter_key"),
                "answers": str(root / "config" / "answers.yaml"), "profile": str(root / "config" / "profile.yaml"),
                "resume": str(root / "resumes" / "synthetic.pdf"), "env": str(root / ".env"),
                "export": str(root / "data" / "exports" / "notes-before-blocks.json"),
                "hook": str(root / ".git" / "hooks" / "pre-commit"), "envrc": str(root / ".envrc"),
                "herdr_socket": str(sockets / "herdr" / "sessions" / "kitty" / "herdr.sock"),
                "tern_socket": str(runtime / "kitty" / "tern-test" / "daemon.sock"), "dbus_socket": str(runtime / "bus"),
                "abstract_socket": f"\0kitty-battery-{os.getpid()}", "other_port": other.getsockname()[1],
                "server_pid": os.getpid(), "port": running.port, "root": str(root), "page": page["page_id"],
                "page_text": "The synthetic source block", "append_text": "Written by the confined agent"}
        master, slave = os.openpty()
        spec["pty"] = os.ttyname(slave)
        env = {key: value for key, value in os.environ.items() if not key.startswith(("KITTY_", "PYTHON"))}
        env.update(launch.env, BATTERY=json.dumps(spec), HOME=str(tmp / "home"))
        (tmp / "home").mkdir()
        try:
            process = subprocess.run(
                [*launch.command, "--kind", "script", "--program", sys.executable, "--", str(root / "project" / "battery.py")],
                env=env, stdin=slave, capture_output=True, text=True, timeout=180, cwd=root / "project",
                start_new_session=True)
        finally:
            os.close(slave)
            os.close(master)
            holder.close()
        with closing(connect(settings.db_path)) as conn:
            session = dict(conn.execute("SELECT * FROM agent_sessions WHERE id=?", (launch.session,)).fetchone())
            journal = [dict(row) for row in conn.execute("SELECT * FROM agent_access WHERE session=? ORDER BY id",
                                                         (launch.session,))]
            actors = [dict(row) for row in conn.execute("SELECT actor, actor_name, actor_session FROM op_log "
                                                        "WHERE actor_session=?", (launch.session,))]
    for server in (herdr, tern, dbus, abstract, other):
        server.close()
    assert process.returncode == 0, (process.returncode, process.stdout[-2000:], process.stderr[-2000:])
    return {"results": json.loads(process.stdout.strip().splitlines()[-1]), "stderr": process.stderr,
            "session": session, "journal": journal, "actors": actors, "page": page}


@pytest.mark.parametrize("check", sorted(CHECKS))
def test_every_route_around_the_api_is_closed_and_the_api_works(battery, check):
    if ABI < CHECKS[check]:
        pytest.skip(f"{check} needs Landlock ABI {CHECKS[check]}; this kernel offers ABI {ABI}")
    result = battery["results"][check]
    assert result["ok"], f"{check}: {result['detail']}"


def test_the_launch_was_verified_and_reports_its_guarantee(battery):
    session = battery["session"]
    assert session["state"] == "confined" and session["client_id"]
    report = json.loads(session["report"])
    assert report["landlock"] and report["seccomp"] and report["abi"] == ABI
    assert report["checks"] == {"connection": True, "pane": True, "launcher": True, "no_new_privs": True, "seccomp": True}
    expected = "full" if ABI >= confine.FULL_ABI else "attribution"
    assert session["guarantee"] == expected
    assert ("Full" if expected == "full" else "Attribution only") in battery["stderr"]


def test_the_agents_reads_and_writes_are_journaled_and_its_writes_carry_the_session(battery):
    journal, page = battery["journal"], battery["page"]
    tools = {(row["tool"], row["op"]) for row in journal}
    assert {("get_page", "read"), ("append_blocks", "write")} <= tools
    reads = [row for row in journal if row["tool"] == "get_page"]
    assert {row["block_id"] for row in reads} >= {page["page_id"], page["blocks"][0]["id"]}
    assert all(row["purpose"] == "notes" and row["decision"] == "allow" and row["mode"] == "enforce"
               and row["policy_version"] == 0 for row in journal)
    written = [row for row in journal if row["tool"] == "append_blocks"]
    assert written and all(row["seq"] > page["seq"] for row in written)
    assert battery["actors"] and all(row["actor"] == "agent" and row["actor_name"] == "script" for row in battery["actors"])


def test_a_launch_without_its_nonce_or_with_a_used_one_does_not_start(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        app = running.app
        with closing(connect(settings.db_path)) as conn:
            launch = app.agent_launches.prepare(conn, host="direct", kind="script", cwd=tmp_path,
                                                ancestor=lambda pane: os.getpid(), origin=app.origin)
        env = {key: value for key, value in os.environ.items() if not key.startswith(("KITTY_", "PYTHON"))}
        command = [*launch.command, "--kind", "script", "--program", "/bin/sh", "--", "-c", "echo started"]
        bare = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
        assert bare.returncode == 126 and "no launch nonce" in bare.stderr and "started" not in bare.stdout
        first = subprocess.run(command, env={**env, **launch.env}, capture_output=True, text=True, timeout=60)
        assert first.returncode == 0 and first.stdout.strip() == "started"
        again = subprocess.run(command, env={**env, **launch.env}, capture_output=True, text=True, timeout=60)
        assert again.returncode == 126 and "Unknown or used launch" in again.stderr


def test_an_exchange_from_outside_the_tab_gets_no_token(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        app = running.app
        with closing(connect(settings.db_path)) as conn:
            # The pane's shell is some other process: the launcher does not descend from it.
            launch = app.agent_launches.prepare(conn, host="direct", kind="script", cwd=tmp_path,
                                                ancestor=lambda pane: 1 << 22, origin=app.origin)
        env = {key: value for key, value in os.environ.items() if not key.startswith(("KITTY_", "PYTHON"))}
        result = subprocess.run([*launch.command, "--kind", "script", "--program", "/bin/sh", "--", "-c", "echo started"],
                                env={**env, **launch.env}, capture_output=True, text=True, timeout=60)
        assert result.returncode == 126 and "started" not in result.stdout
        assert "could not verify" in result.stderr and "not a process of the tab's pane" in result.stderr
        with closing(connect(settings.db_path)) as conn:
            row = conn.execute("SELECT state, guarantee, client_id FROM agent_sessions WHERE id=?", (launch.session,)).fetchone()
        assert tuple(row) == ("refused", "none", None)


def test_a_forged_exchange_from_a_program_that_is_not_the_launcher_gets_no_token(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        app = running.app
        with closing(connect(settings.db_path)) as conn:
            launch = app.agent_launches.prepare(conn, host="direct", kind="script", cwd=tmp_path,
                                                ancestor=lambda pane: os.getpid(), origin=app.origin)
        forger = (
            "import json, os, sys, http.client\n"
            "port = int(os.environ['KITTY_LAUNCH_ORIGIN'].rsplit(':', 1)[1])\n"
            "c = http.client.HTTPConnection('127.0.0.1', port)\n"
            "nonce = os.environ['KITTY_LAUNCH_NONCE']\n"
            "for path, body in (('/api/ingress/agent/launch', {'nonce': nonce}),\n"
            "                   ('/api/ingress/agent/exchange', {'nonce': nonce, 'pid': os.getpid(), 'report':\n"
            "                    {'abi': 10, 'landlock': True, 'seccomp': True, 'closed': []}})):\n"
            "    c.request('POST', path, body=json.dumps(body), headers={'Content-Type': 'application/json'})\n"
            "    r = c.getresponse(); data = json.loads(r.read())\n"
            "print(json.dumps(data))\n")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("KITTY_", "PYTHON"))}
        result = subprocess.run([sys.executable, "-c", forger], env={**env, **launch.env}, capture_output=True, text=True,
                                timeout=60)
        answer = json.loads(result.stdout)
        assert answer["run"] is False and "KITTY_AGENT_TOKEN" not in answer["env"]
        assert "not rika!'s agent launcher" in answer["message"] and "no seccomp filter" in answer["message"]
        with closing(connect(settings.db_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0
