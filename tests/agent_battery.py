"""The bypass battery, run as the "agent" inside a launched (confined) Agent tab.

kitty-agent-launch execs this script after confining itself; it tries every route
around rika!'s API the paper's §13 battery names and prints one JSON object:
``{check: {"ok": bool, "detail": str}}``, where ``ok`` means the route was closed (or,
for the token checks, that the mediated route worked). Paths and ports come from
``BATTERY`` (JSON in the environment), set by tests/test_agent_bypass.py.
"""
from __future__ import annotations

import errno
import fcntl
import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import termios

CLOSED = {errno.EACCES, errno.ENOENT, errno.EPERM}
spec = json.loads(os.environ["BATTERY"])
results: dict[str, dict] = {}


def closed(name: str, action) -> None:
    try:
        action()
    except OSError as exc:
        results[name] = {"ok": exc.errno in CLOSED, "detail": f"{errno.errorcode.get(exc.errno, exc.errno)}: {exc}"}
        return
    except sqlite3.Error as exc:
        results[name] = {"ok": "unable to open" in str(exc) or "authorization" in str(exc), "detail": f"sqlite3: {exc}"}
        return
    results[name] = {"ok": False, "detail": "succeeded"}


def read(path: str):
    return lambda: open(path, "rb").read()


def write(path: str):
    def action():
        with open(path, "ab") as handle:
            handle.write(b"planted\n")
    return action


def sqlite(path: str):
    def action():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
    return action


def unix(path: str):
    def action():
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.connect(path)
        finally:
            probe.close()
    return action


def tcp(port: int):
    def action():
        probe = socket.create_connection(("127.0.0.1", port), timeout=5)
        probe.close()
    return action


closed("db_open", read(spec["db"]))
closed("db_sqlite3", sqlite(spec["db"]))
closed("db_wal", read(spec["db"] + "-wal"))
closed("db_shm", read(spec["db"] + "-shm"))
closed("private_read", read(spec["private"]))
closed("answers_read", read(spec["answers"]))
closed("profile_read", read(spec["profile"]))
closed("resume_read", read(spec["resume"]))
closed("env_write", write(spec["env"]))
closed("env_read", read(spec["env"]))
closed("export_read", read(spec["export"]))
closed("git_hook_write", write(spec["hook"]))
closed("envrc_plant", write(spec["envrc"]))
closed("herdr_socket", unix(spec["herdr_socket"]))
closed("tern_socket", unix(spec["tern_socket"]))
closed("dbus_socket", unix(spec["dbus_socket"]))


def abstract():
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(spec["abstract_socket"])
    finally:
        probe.close()


closed("abstract_socket", abstract)
closed("tcp_other_port", tcp(spec["other_port"]))
closed("signal_server", lambda: os.kill(spec["server_pid"], 0))
closed("server_memory", read(f"/proc/{spec['server_pid']}/environ"))
closed("tiocsti", lambda: fcntl.ioctl(0, termios.TIOCSTI, b"x"))
closed("tiocsti_reopened", lambda: fcntl.ioctl(os.open(spec["pty"], os.O_RDWR | os.O_NOCTTY), termios.TIOCSTI, b"x"))


def forged():
    """A program's perfect page headers without the UI secret: minting a terminal token."""
    connection = http.client.HTTPConnection("127.0.0.1", spec["port"], timeout=10)
    body = b"{}"
    connection.request("POST", "/api/terminal/session", body=body, headers={
        "Host": f"127.0.0.1:{spec['port']}", "Origin": f"http://127.0.0.1:{spec['port']}", "X-Kitty-Request": "1",
        "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"})
    status = connection.getresponse().status
    connection.close()
    return status


status = forged()
results["forged_request"] = {"ok": status == 401, "detail": f"HTTP {status}"}

# kitty notes and kitty mcp without the tab's token: they would open the database directly.
bare = {key: value for key, value in os.environ.items() if not key.startswith("KITTY_AGENT_")}
bare["KITTY_HOME"] = spec["root"]
cli = subprocess.run([sys.executable, "-m", "kitty.cli", "notes", "ls"], env=bare, capture_output=True, text=True, timeout=60)
results["cli_without_token"] = {"ok": cli.returncode != 0 and ("Permission denied" in cli.stderr + cli.stdout
                                                               or "unable to open" in cli.stderr + cli.stdout),
                                "detail": f"exit {cli.returncode}: {(cli.stderr or cli.stdout).strip()[-300:]}"}
mcp = subprocess.run([sys.executable, "-m", "kitty.cli", "mcp"], env=bare, input="", capture_output=True, text=True,
                     timeout=60)
results["mcp_without_token"] = {"ok": mcp.returncode != 0, "detail": f"exit {mcp.returncode}: {mcp.stderr.strip()[-300:]}"}

# The mediated route works: kitty notes with the token, then the MCP-style call.
token_env = dict(os.environ)
shown = subprocess.run([sys.executable, "-m", "kitty.cli", "notes", "show", spec["page"], "--json"], env=token_env,
                       capture_output=True, text=True, timeout=60)
results["notes_read_with_token"] = {"ok": shown.returncode == 0 and spec["page_text"] in shown.stdout,
                                    "detail": f"exit {shown.returncode}: {(shown.stderr or shown.stdout)[-300:]}"}
appended = subprocess.run([sys.executable, "-m", "kitty.cli", "notes", "append", spec["page"], spec["append_text"], "--json"],
                          env=token_env, capture_output=True, text=True, timeout=60)
results["notes_write_with_token"] = {"ok": appended.returncode == 0, "detail": f"exit {appended.returncode}: "
                                     f"{(appended.stderr or appended.stdout)[-300:]}"}
results["environment"] = {"ok": "KITTY_AGENT_TOKEN" in os.environ and "KITTY_LAUNCH_NONCE" not in os.environ,
                          "detail": os.environ.get("KITTY_AGENT_GUARANTEE", "")}
print(json.dumps(results))
