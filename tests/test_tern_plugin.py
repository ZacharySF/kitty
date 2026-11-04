"""Installing the kitty Tern plugin (platform design Q1) against a synthetic ``tern``.

The plugin's own logic is tested in Luau (``integrations/tern/tests``); these tests cover
rika!'s side: the shipped package, the copy in the workspace, ``tern plugin link`` aimed
only at rika!'s socket, status, removal and the build switch.
"""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from terminal_support import make_root, site
from test_web_tern import tern  # noqa: F401  (fixture)

from kitty.core import tern_plugin

PLUGIN = "/api/terminal/tern/plugin"
STATUS_KEYS = {"available", "version", "dir", "installed", "installed_version", "current", "linked_elsewhere",
               "tern_status", "problems", "enabled"}
REPO = Path(__file__).resolve().parents[1]


def plugin_status(running) -> dict:
    code, body = running.request(PLUGIN)
    assert code == 200, body
    assert set(body) == STATUS_KEYS
    return body


def plugins_folder(tmp_path: Path) -> Path:
    return tmp_path / "user-tern-config" / "plugins"


def test_the_shipped_package_is_the_repository_plugin():
    shipped = tern_plugin.shipped_dir()
    assert shipped == REPO / "integrations" / "tern"
    names = {path.as_posix() for path in tern_plugin._files(shipped)}
    assert {"plugin.toml", "host.luau", "window.luau", "lib/core.luau", "lib/client.luau", "lib/inbox.luau",
            "README.md", "LICENSE"} <= names
    assert not any(name.startswith("tests/") for name in names)


@pytest.mark.skipif(shutil.which("luau") is None, reason="the Luau CLI is not installed")
def test_the_plugins_own_luau_tests_pass():
    result = subprocess.run(["luau", "tests/run.luau"], cwd=REPO / "integrations" / "tern", capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert " 0 failed" in result.stdout


def test_a_desktop_bundle_ships_the_plugin_beside_the_website(tmp_path, monkeypatch):
    bundle = tmp_path / "_MEIbundle"
    (bundle / "tern-plugin").mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert tern_plugin.shipped_dir() is None
    (bundle / "tern-plugin" / "plugin.toml").write_text('id = "kitty"\n')
    assert tern_plugin.shipped_dir() == bundle / "tern-plugin"


def test_status_before_installing_reads_tern_without_starting_it(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        body = plugin_status(running)
    assert body["available"] is True and body["enabled"] is True and body["version"] == "0.1.0"
    assert body["installed"] is False and body["installed_version"] is None and body["current"] is False
    assert body["tern_status"] is None and body["problems"] == [] and body["linked_elsewhere"] is None
    assert body["dir"] == str(settings.root / ".private" / "tern" / "plugin")
    assert [call for call in tern.calls() if call != ["--version"]] == [["plugin", "dir"], ["plugin", "list", "--json"]]


def test_install_copies_the_package_into_the_workspace_and_links_it(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request(PLUGIN + "/install", {})
        assert code == 200, body
    copy = settings.root / ".private" / "tern" / "plugin"
    assert body["installed"] is True and body["current"] is True and body["tern_status"] == "ready"
    assert body["installed_version"] == "0.1.0"
    assert (plugins_folder(tmp_path) / "kitty.path").read_text().strip() == str(copy)
    assert tern_plugin.digest(copy) == tern_plugin.digest(REPO / "integrations" / "tern")
    assert (copy / "lib" / "client.luau").is_file() and not (copy / "tests").exists()
    for folder in (copy.parent, copy, copy / "lib"):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert not [item for item in copy.parent.iterdir() if item.name.startswith(".plugin")]
    # Every `tern plugin` rika! ran was aimed at rika!'s own socket, never the user's daemon.
    links = [item for item in tern.invocations() if item["argv"][:1] == ["plugin"]]
    assert ["plugin", "link", str(copy), "--json"] in [item["argv"] for item in links]
    assert all(item["env"]["TERN_DAEMON_SOCKET"] != str(tern.owner_socket) for item in links)
    assert all("TERN_PANE" not in item["env"] for item in links)


def test_installing_again_replaces_a_stale_copy_in_place(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        assert running.request(PLUGIN + "/install", {})[0] == 200
        copy = settings.root / ".private" / "tern" / "plugin"
        (copy / "window.luau").write_text("-- edited\n")
        (copy / "stale.luau").write_text("-- left over\n")
        assert plugin_status(running)["current"] is False
        code, body = running.request(PLUGIN + "/install", {})
        assert code == 200 and body["current"] is True and body["installed"] is True
    assert not (copy / "stale.luau").exists()
    assert not [item for item in copy.parent.iterdir() if item.name.startswith(".plugin")]


def test_install_takes_over_a_link_to_another_workspace_and_says_so_before(tmp_path, tern):
    other = tmp_path / "other" / ".private" / "tern" / "plugin"
    other.mkdir(parents=True)
    (other / "plugin.toml").write_text('schema = 1\nid = "kitty"\nname = "rika!"\nversion = "0.0.9"\nwindow = "w.luau"\n')
    plugins_folder(tmp_path).mkdir(parents=True)
    (plugins_folder(tmp_path) / "kitty.path").write_text(str(other) + "\n")
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        before = plugin_status(running)
        assert before["installed"] is False and before["linked_elsewhere"] == str(other)
        code, after = running.request(PLUGIN + "/install", {})
        assert code == 200 and after["installed"] is True and after["linked_elsewhere"] is None


def test_install_refuses_a_different_plugin_holding_the_id(tmp_path, tern):
    foreign = plugins_folder(tmp_path) / "kitty"
    foreign.mkdir(parents=True)
    (foreign / "plugin.toml").write_text('schema = 1\nid = "kitty"\nname = "kitty terminal"\nversion = "3"\nwindow = "w.luau"\n')
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        code, body = running.request(PLUGIN + "/install", {})
    assert code == 409 and "tern plugin remove kitty" in body["error"]
    assert not (settings.root / ".private" / "tern" / "plugin").exists()
    assert not (plugins_folder(tmp_path) / "kitty.path").exists()


def test_a_failed_plugin_and_folder_problems_are_reported(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        assert running.request(PLUGIN + "/install", {})[0] == 200
        tern.mode("plugin-failed")
        body = plugin_status(running)
        assert body["tern_status"] == "failed: host.luau:1: boom"
        duplicate = plugins_folder(tmp_path) / "zz-kitty-copy"
        duplicate.mkdir()
        (duplicate / "plugin.toml").write_text('schema = 1\nid = "kitty"\nname = "x"\nversion = "1"\nwindow = "w.luau"\n')
        assert plugin_status(running)["problems"] == [f"{duplicate}: duplicate id kitty"]


def test_the_build_switch_refuses_installing_but_not_removing(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        assert running.request(PLUGIN + "/install", {})[0] == 200
        path = settings.root / "config" / "build.json"
        data = json.loads(path.read_text())
        data["settings"] = {"tern": {"plugin": False}}
        path.write_text(json.dumps(data))
        running.app.builds.refresh()
        assert plugin_status(running)["enabled"] is False
        code, body = running.request(PLUGIN + "/install", {})
        assert code == 409 and "turned off" in body["error"]
        code, body = running.request(PLUGIN + "/remove", {})
        assert code == 200 and body["installed"] is False
    assert not (settings.root / ".private" / "tern" / "plugin").exists()
    assert not (plugins_folder(tmp_path) / "kitty.path").exists()


def test_remove_never_unlinks_another_workspaces_plugin(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        assert running.request(PLUGIN + "/install", {})[0] == 200
        (plugins_folder(tmp_path) / "kitty.path").write_text("/elsewhere/.private/tern/plugin\n")
        code, body = running.request(PLUGIN + "/remove", {})
        assert code == 200 and body["linked_elsewhere"] == "/elsewhere/.private/tern/plugin"
    assert (plugins_folder(tmp_path) / "kitty.path").read_text().strip() == "/elsewhere/.private/tern/plugin"
    assert not (settings.root / ".private" / "tern" / "plugin").exists()
    assert ["plugin", "unlink", "kitty"] not in tern.calls()


def test_a_symlinked_copy_location_is_refused(tmp_path, tern):
    settings = make_root(tmp_path, ("terminal",))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    private = settings.root / ".private" / "tern"
    private.mkdir(parents=True, mode=0o700)
    (private / "plugin").symlink_to(elsewhere)
    with site(settings) as running:
        code, body = running.request(PLUGIN + "/install", {})
    assert code == 409 and "not rika!'s plugin folder" in body["error"]
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("path", [PLUGIN + "/install", PLUGIN + "/remove"])
def test_plugin_actions_expect_an_empty_body(tmp_path, tern, path):
    with site(make_root(tmp_path, ("terminal",))) as running:
        code, body = running.request(path, {"force": True})
    assert code == 400


def test_without_tern_the_plugin_routes_point_to_the_install_page(tmp_path, tern, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with site(make_root(tmp_path, ("terminal",))) as running:
        for path, body in ((PLUGIN, None), (PLUGIN + "/install", {}), (PLUGIN + "/remove", {})):
            code, answer = running.request(path, body)
            assert code == 409 and "https://stencil.so/tern" in answer["error"], path
    assert tern.calls() == []


def test_the_plugin_routes_belong_to_the_terminal_module(tmp_path, tern):
    with site(make_root(tmp_path, ())) as running:
        for path, body in ((PLUGIN, None), (PLUGIN + "/install", {}), (PLUGIN + "/remove", {})):
            code, answer = running.request(path, body)
            assert code == 403 and "module is disabled" in answer["error"]
    assert tern.calls() == []
