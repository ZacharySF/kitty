"""GitHub backup: the export allowlist, commits, pushes to a local bare repository, the privacy
check (injected, never the network), the schedule on a fake clock, the routes and Rika's side."""
from __future__ import annotations

import dataclasses
import http.client
import json
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from loro import LoroDoc

from kitty import web_auth
from kitty.core import reposync
from kitty.core.blocks import store as blocks
from kitty.core.rika import brief, tools
from kitty.core.rika.tools import Context, ToolError
from kitty.web_server import LocalHTTPServer, WebApplication

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


def git(repo, *args):
    return subprocess.run(["git", "--git-dir", str(repo), *args], capture_output=True, text=True, check=True).stdout


def page(conn, title, *lines, at=T0):
    page_id = str(uuid.uuid4())
    ops = [{"op": "insert", "id": page_id, "type": "page", "parent": None, "text": title, "props": {"revision": 1}}]
    ops += [{"op": "insert", "id": str(uuid.uuid4()), "type": "text", "parent": page_id, "text": line} for line in lines]
    blocks.transact(conn, ops, at=at.isoformat())
    return page_id


def add_line(conn, page_id, text, at):
    blocks.transact(conn, [{"op": "insert", "id": str(uuid.uuid4()), "type": "text", "parent": page_id, "text": text}],
                    at=at.isoformat())


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    return path


@pytest.fixture
def backup(workspace, remote):
    """A backup whose GitHub is the test: `world.visibility` answers the privacy check, the remote is local."""
    world = SimpleNamespace(visibility="private", now=T0, checks=0)

    def visibility(owner, repo, token):
        world.checks += 1
        if world.visibility == "offline":
            raise reposync.Unreachable("GitHub could not be reached")
        return world.visibility

    service = reposync.RepoSync(workspace.settings, visibility=visibility, remotes=lambda owner, repo: [str(remote)],
                                token=lambda: "", clock=lambda: world.now)
    service.world = world
    service.branch = f"devices/{blocks.device_id(workspace.conn)}"
    return service


def files(remote, branch):
    return set(git(remote, "ls-tree", "-r", "--name-only", branch).split())


def test_a_run_pushes_only_what_the_allowlist_names(workspace, backup, remote):
    conn, root = workspace.conn, workspace.settings.root
    (root / ".env").write_text("SECRET=hunter2")
    (root / ".private" / "keys").mkdir(parents=True)
    (root / ".private" / "keys" / "GITHUB").write_text("ghp_synthetic")
    (root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": ["workspace"]}))
    lecture = page(conn, "Lecture 7", "Ribosomes", "Second line")
    backup.connect(conn, "zed/notes")
    # Whatever else lands in the work tree never reaches a commit.
    tree = backup.place.tree
    (tree / "pages").mkdir(parents=True)
    (tree / "secrets.env").write_text("SECRET=hunter2")
    (tree / "pages" / "apply.db").write_text("sqlite")
    (tree / "config").mkdir()
    (tree / "config" / "profile.yaml").write_text("name: x")

    run = backup.run()

    assert run["result"] == "pushed" and run["pages"] == 1 and run["error"] is None
    pushed = files(remote, backup.branch)
    assert pushed == {"rika-sync.json", "build.json", f"pages/{lecture}.md", f"pages/{lecture}.loro"}
    assert all(reposync.ALLOWED.fullmatch(name) for name in pushed)
    assert git(remote, "show", f"{backup.branch}:pages/{lecture}.md") == "# Lecture 7\n\n- Ribosomes\n- Second line\n"
    manifest = json.loads(git(remote, "show", f"{backup.branch}:rika-sync.json"))
    assert set(manifest) == {"format", "app", "version", "device", "exported_at"} and manifest["format"] == 1
    snapshot = subprocess.run(["git", "--git-dir", str(remote), "show", f"{backup.branch}:pages/{lecture}.loro"],
                              capture_output=True, check=True).stdout
    LoroDoc().import_(snapshot)  # the snapshot is a whole Loro document
    assert git(remote, "rev-parse", backup.branch).strip() == run["commit"]
    # Its own Git directory; the workspace root gets none.
    assert (root / ".private" / "reposync" / "repo.git" / "HEAD").is_file() and not (root / ".git").exists()
    with pytest.raises(reposync.RepoSyncError):
        reposync._write(tree, "config/profile.yaml", b"x")


def test_the_commit_message_is_made_from_titles_and_only_changed_pages_are_rewritten(workspace, backup, remote):
    conn = workspace.conn
    lecture = page(conn, "Lecture 7", "Ribosomes", at=T0 + 3 * MINUTE)
    groceries = page(conn, "Groceries", "Milk", at=T0 + 2 * MINUTE)
    third = page(conn, "Reading list", "Dune", at=T0 + MINUTE)
    backup.connect(conn, "zed/notes")
    assert backup.run()["result"] == "pushed"
    assert git(remote, "log", "-1", "--format=%s", backup.branch).strip() == "3 pages: Lecture 7, Groceries, +1"
    before = {path.name: path.stat().st_mtime_ns for path in (backup.place.tree / "pages").iterdir()}

    add_line(conn, groceries, "Eggs", T0 + 20 * MINUTE)
    backup.world.now = T0 + 30 * MINUTE
    assert backup.run()["result"] == "pushed"

    assert git(remote, "log", "-1", "--format=%s", backup.branch).strip() == "1 page: Groceries"
    changed = set(git(remote, "show", "--name-only", "--format=", backup.branch).split())
    assert changed == {"rika-sync.json", f"pages/{groceries}.md", f"pages/{groceries}.loro"}
    after = {path.name: path.stat().st_mtime_ns for path in (backup.place.tree / "pages").iterdir()}
    assert {name for name in after if after[name] != before[name]} == {f"{groceries}.md", f"{groceries}.loro"}
    # Nothing changed: no commit, and the run says so.
    head = git(remote, "rev-parse", backup.branch)
    assert backup.run()["result"] == "unchanged" and git(remote, "rev-parse", backup.branch) == head
    # A deleted page leaves the copy.
    blocks.transact(conn, [{"op": "delete", "id": third}], at=(T0 + 30 * MINUTE).isoformat())
    assert backup.run()["pages"] == 2
    assert files(remote, backup.branch) >= {f"pages/{lecture}.md"} and f"pages/{third}.md" not in files(remote, backup.branch)
    assert git(remote, "log", "-1", "--format=%s", backup.branch).strip() == "1 page: Reading list (removed)"


def test_commit_messages():
    message = reposync.commit_message
    assert message(reposync.Export(written=["Lecture 7", "Groceries", "Reading list", "Inbox"])) == "4 pages: Lecture 7, Groceries, +2"
    assert message(reposync.Export(written=["A"], removed=["B"])) == "2 pages: A, B (removed)"
    assert message(reposync.Export(build=True)) == "Build file"


def test_a_public_repository_is_refused_and_pauses_the_backup(workspace, backup, remote):
    conn = workspace.conn
    page(conn, "Lecture 7", "Ribosomes")
    backup.world.visibility = "public"
    with pytest.raises(reposync.RepoSyncError) as refused:
        backup.connect(conn, "zed/notes")
    assert refused.value.status == 409 and backup.state(conn)["repo"] is None

    backup.world.visibility = "missing"  # GitHub's answer for a private repository without a token
    backup.connect(conn, "https://github.com/zed/notes.git")
    assert backup.state(conn)["repo"] == "zed/notes"
    assert backup.run()["result"] == "pushed"
    head = git(remote, "rev-parse", backup.branch)

    # Made public later: checked before every push, so nothing more leaves.
    backup.world.visibility = "public"
    add_line(conn, page(conn, "Diary", "Private"), "More", T0 + MINUTE)
    checks = backup.world.checks
    run = backup.run()
    assert run["result"] == "refused" and backup.world.checks == checks + 1
    assert git(remote, "rev-parse", backup.branch) == head
    state = backup.state(conn)
    assert state["paused"] and state["private"] is False and "public" in state["problem"] and state["next_run"] is None
    assert [alert["title"] for alert in brief.alerts(conn, backup.world.now)] == ["GitHub backup"]
    with pytest.raises(reposync.RepoSyncError):
        backup.sync_now(conn)  # paused: the owner resumes first

    backup.world.visibility = "private"
    backup.pause(conn, False)
    assert backup.tick()["result"] == "pushed" and backup.state(conn)["problem"] is None
    assert brief.alerts(conn, backup.world.now) == []


def test_github_visibility_reads_the_api_answer(monkeypatch):
    import httpx

    answers = {"zed/private": (200, {"private": True}), "zed/public": (200, {"private": False}),
               "zed/odd": (200, {}), "zed/hidden": (404, {}), "zed/limited": (403, {})}
    seen = []

    def get(url, headers, **kwargs):
        seen.append(headers.get("Authorization"))
        status, body = answers[url.removeprefix("https://api.github.com/repos/")]
        return httpx.Response(status, json=body)

    monkeypatch.setattr(httpx, "get", get)
    assert reposync.github_visibility("zed", "private", "tok") == "private" and seen == ["Bearer tok"]
    assert reposync.github_visibility("zed", "public") == "public"
    assert reposync.github_visibility("zed", "odd") == "public"  # only an explicit private: true counts
    assert reposync.github_visibility("zed", "hidden") == "missing"
    with pytest.raises(reposync.Unreachable):
        reposync.github_visibility("zed", "limited")


@pytest.mark.parametrize("value", ["zed/notes", " zed/notes ", "https://github.com/zed/notes", "https://github.com/zed/notes.git/",
                                   "git@github.com:zed/notes.git", "github.com/zed/notes"])
def test_repository_addresses(value):
    assert reposync.parse_repo(value) == ("zed", "notes")


@pytest.mark.parametrize("value", ["", "notes", "zed/notes/extra", "https://gitlab.com/zed/notes", "zed/..", "-zed/notes", 7])
def test_other_addresses_are_refused(value):
    with pytest.raises(reposync.RepoSyncError):
        reposync.parse_repo(value)


def test_the_schedule_waits_for_quiet_caps_the_wait_and_backs_off(workspace, backup):
    conn, world = workspace.conn, backup.world
    lecture = page(conn, "Lecture 7", "Ribosomes", at=T0)
    backup.connect(conn, "zed/notes")
    backup._asked.clear()  # connect asked for a first run; this test is about the clock alone
    world.now = T0 + 9 * MINUTE
    assert backup.tick() is None
    assert not backup.due(conn, T0 + 9 * MINUTE) and backup.due(conn, T0 + 10 * MINUTE)
    world.now = T0 + 10 * MINUTE
    assert backup.tick()["result"] == "pushed"
    assert backup.tick() is None and reposync.next_run(conn) is None  # nothing waiting

    # Steady typing: every edit moves the run back, but never past 60 minutes after the first.
    start = T0 + 100 * MINUTE
    for minute in range(0, 60, 5):
        add_line(conn, lecture, f"line {minute}", start + minute * MINUTE)
    assert reposync.next_run(conn) == start + 60 * MINUTE
    assert not backup.due(conn, start + 59 * MINUTE) and backup.due(conn, start + 60 * MINUTE)

    # Offline: 5, 10, 20, 40 minutes, then an hour at most.
    world.visibility = "offline"
    world.now = start + 60 * MINUTE
    waits = []
    for _ in range(6):
        assert backup.tick()["result"] == "offline"
        waits.append(reposync.next_run(conn) - world.now)
        assert backup.tick() is None  # not before the wait is over
        world.now = reposync.next_run(conn)
    assert waits == [timedelta(minutes=minutes) for minutes in (5, 10, 20, 40, 60, 60)]
    world.visibility = "private"
    assert backup.tick()["result"] == "pushed" and reposync.next_run(conn) is None
    results = [row["result"] for row in conn.execute("SELECT result FROM repo_sync_runs ORDER BY id")]
    assert results == ["pushed", *["offline"] * 6, "pushed"]

    # Paused: no runs, and no next run.
    add_line(conn, lecture, "later", world.now)
    backup.pause(conn, True)
    assert not backup.due(conn, world.now + 5 * 60 * MINUTE)


def test_a_rejected_push_is_a_problem_and_a_stale_backup_reaches_rika(workspace, backup, remote, tmp_path):
    conn, world = workspace.conn, backup.world
    lecture = page(conn, "Lecture 7", "Ribosomes")
    backup.connect(conn, "zed/notes")
    assert backup.run()["result"] == "pushed"
    # Someone else moves this device's branch on the remote.
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "-b", backup.branch, str(remote), str(clone)], check=True)
    (clone / "other.txt").write_text("elsewhere")
    for command in (["add", "."], ["-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "elsewhere"], ["push", "-q"]):
        subprocess.run(["git", "-C", str(clone), *command], check=True)
    moved = git(remote, "rev-parse", backup.branch)

    add_line(conn, lecture, "More", T0 + MINUTE)
    world.now = T0 + 20 * MINUTE
    assert backup.tick()["result"] == "rejected"
    assert git(remote, "rev-parse", backup.branch) == moved  # nothing was overwritten
    assert "rejected" in backup.state(conn)["problem"] and not backup.due(conn, world.now + 600 * MINUTE)
    assert [alert["body"] for alert in brief.alerts(conn, world.now)] == [backup.state(conn)["problem"]]

    # A backup that keeps failing for three days while changes wait is a problem too.
    backup.disconnect(conn)
    assert not backup.place.base.exists() and reposync.problems(conn, world.now) == []
    world.visibility = "offline"
    with pytest.raises(reposync.RepoSyncError) as unchecked:
        backup.connect(conn, "zed/notes")
    assert unchecked.value.status == 502
    world.visibility = "private"
    backup.connect(conn, "zed/other")
    world.visibility = "offline"
    assert backup.run()["result"] == "offline"
    assert reposync.problems(conn, T0 + timedelta(days=2)) == []
    [problem] = reposync.problems(conn, T0 + timedelta(days=4))
    assert "3 days" in problem["text"] and "GitHub could not be reached" in problem["text"]
    assert conn.execute("SELECT COUNT(*) FROM repo_sync_runs").fetchone()[0] == 3  # runs outlive a disconnect


def test_rika_reads_the_status_and_starts_a_run_but_cannot_connect(workspace, backup):
    conn = workspace.conn
    registry = tools.registry()
    assert registry["sync_status"].effect == "read" and registry["sync_now"].effect == "change"
    assert not [name for name in registry if "sync" in name and name not in {"sync_status", "sync_now"}]
    ctx = Context(SimpleNamespace(settings=workspace.settings, modules=(), reposync=backup), conn)
    assert "not set up" in registry["sync_status"].run(ctx, {}).summary
    with pytest.raises(ToolError):
        registry["sync_now"].run(ctx, {})
    page(conn, "Lecture 7", "Ribosomes")
    backup.connect(conn, "zed/notes")
    backup._asked.clear()
    registry["sync_now"].run(ctx, {})
    assert backup.tick()["result"] == "pushed"
    status = registry["sync_status"].run(ctx, {})
    assert "zed/notes" in status.summary and status.data["pages"] == 1
    demo = Context(SimpleNamespace(settings=workspace.settings, modules=(), reposync=None), conn)
    with pytest.raises(ToolError):
        registry["sync_now"].run(demo, {})


# Routes ---------------------------------------------------------------------------

def serve(settings, config, tmp_path):
    static = tmp_path / "dist"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>Synthetic website</title>")
    app = WebApplication(settings, config, static)
    server = LocalHTTPServer(app, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None):
        headers = {"Host": server.authority, "Origin": server.origin, web_auth.HEADER: server.ui_auth.secret}
        payload = None
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-Kitty-Request": "1"})
            payload = json.dumps(body).encode()
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
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

    return request, app, close


def test_routes_connect_sync_pause_and_disconnect(workspace, remote, tmp_path):
    page(workspace.conn, "Lecture 7", "Ribosomes")
    request, app, close = serve(workspace.settings, workspace.config, tmp_path)
    try:
        answers = {"zed/notes": "private", "zed/site": "public"}
        app.reposync.visibility = lambda owner, repo, token: answers[f"{owner}/{repo}"]
        app.reposync.remotes = lambda owner, repo: [str(remote)]
        status, state = request("/api/reposync")
        assert status == 200 and state["enabled"] and state["repo"] is None and state["last_run"] is None
        assert request("/api/reposync/now", {})[0] == 409
        assert request("/api/reposync/repo", {"repo": "zed/site"})[0] == 409
        assert request("/api/reposync/repo", {"repo": "not a repo"})[0] == 400
        assert request("/api/reposync/repo", {})[0] == 400
        status, state = request("/api/reposync/repo", {"repo": "zed/notes"})
        assert status == 200 and state["repo"] == "zed/notes" and state["private"] is True

        def settled():
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                state = request("/api/reposync")[1]
                if not state["running"] and state["last_run"]:
                    return state
                time.sleep(0.1)
            raise AssertionError("the backup did not run")

        state = settled()  # connecting starts the first run
        assert state["last_run"]["result"] == "pushed" and state["pages"] == 1 and state["synced_at"]
        branch = f"devices/{blocks.device_id(workspace.conn)}"
        assert any(name.endswith(".md") for name in files(remote, branch))
        assert request("/api/reposync/pause", {"paused": True})[1]["paused"] is True
        assert request("/api/reposync/now", {})[0] == 409
        assert request("/api/reposync/pause", {"paused": "yes"})[0] == 400
        assert request("/api/reposync/pause", {"paused": False})[1]["paused"] is False
        settled()
        status, state = request("/api/reposync/disconnect", {})
        assert status == 200 and state["repo"] is None and state["last_run"] is None
        assert not (workspace.settings.root / ".private" / "reposync").exists()
    finally:
        close()


def test_the_demo_has_no_backup(workspace, tmp_path):
    request, app, close = serve(dataclasses.replace(workspace.settings, demo=True), workspace.config, tmp_path)
    try:
        status, state = request("/api/reposync")
        assert status == 200 and state["enabled"] is False and "demo" in state["reason"] and app.reposync is None
        assert request("/api/reposync/repo", {"repo": "zed/notes"})[0] == 409
    finally:
        close()
