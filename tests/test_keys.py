"""kitty Keys (Q10): resolution order, owner-only files, the Secret Service adapter, the
`kitty keys` CLI, the Settings → Keys routes (answered by rika-server) and every consumer
reading through it.

Every key value here is synthetic. No test reaches the real keyring: conftest turns it
off, keyring tests inject FakeKeyring or a fake Secret Service backend, and the interop test
runs its own gnome-keyring on a private session bus.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from kitty.core import keys
from kitty.core.keys import (
    SecretServiceKeyring as RealSecretService,  # before conftest swaps it
)
from kitty.settings import Settings

VALUE = "synthetic-key-value-0123456789"
OTHER = "synthetic-other-value-9876543210"


class FakeKeyring:
    label = "Fake keyring"

    def __init__(self, fail: bool = False) -> None:
        self.items: dict[str, str] = {}
        self.fail = fail

    def _check(self):
        if self.fail:
            raise RuntimeError(f"locked while holding {VALUE}")

    def get(self, name):
        self._check()
        return self.items.get(name)

    def set(self, name, value):
        self._check()
        self.items[name] = value

    def delete(self, name):
        self._check()
        return self.items.pop(name, None) is not None

    def names(self):
        self._check()
        return set(self.items)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    return tmp_path


@pytest.fixture
def clean_env(monkeypatch):
    for spec in keys.KNOWN.values():
        monkeypatch.delenv(spec.env, raising=False)
    monkeypatch.delenv("DUMMY", raising=False)
    return monkeypatch


def store(root, keyring=None, use_env=True):
    return keys.Keys(root, store=keyring, use_env=use_env)


def private_file(root, name, value=VALUE, mode=0o600):
    path = store(root).file_path(name)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(value)
    path.chmod(mode)
    return path


# ------------------------------------------------------------------ resolution


def test_resolution_order_is_keyring_then_file_then_environment(root, clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "synthetic-env-value")
    fake = FakeKeyring()
    assert store(root, fake).lookup("OPENROUTER") == ("synthetic-env-value", "env")
    private_file(root, "OPENROUTER", OTHER)
    assert store(root, fake).lookup("openrouter") == (OTHER, "file")
    fake.items["OPENROUTER"] = VALUE
    assert store(root, fake).lookup("OPENROUTER") == (VALUE, "keyring")
    assert store(root, None).lookup("OPENROUTER") == (OTHER, "file")


def test_environment_is_ignored_when_disabled_and_demo_never_uses_keyring_or_env(root, clean_env, monkeypatch):
    clean_env.setenv("ANTHROPIC_API_KEY", VALUE)
    assert store(root, use_env=False).read("ANTHROPIC") == ""
    monkeypatch.setattr(keys, "keyring", lambda: pytest.fail("demo must not reach the keyring"))
    demo = keys.for_settings(Settings(root=root, db_path=root / "demo.db", demo=True))
    assert demo.store is None and demo.read("ANTHROPIC") == ""


def test_existing_openrouter_file_keeps_working_and_custom_keys_get_their_own_files(root, clean_env):
    legacy = root / ".private" / "openrouter-key"
    legacy.parent.mkdir(mode=0o700)
    legacy.write_text(VALUE + "\n")
    legacy.chmod(0o600)
    assert store(root).lookup("OPENROUTER") == (VALUE, "file")
    assert store(root).file_path("DUMMY") == root / ".private" / "keys" / "DUMMY"


def test_keyring_off_switch_and_unreachable_keyring_fall_back_to_the_file(root, monkeypatch):
    monkeypatch.setenv("KITTY_KEYRING", "none")
    monkeypatch.setattr(keys, "SecretServiceKeyring", lambda service: pytest.fail("keyring was turned off"))
    assert keys.keyring() is None
    monkeypatch.delenv("KITTY_KEYRING")

    def unreachable(service):
        raise RuntimeError("no session bus")

    monkeypatch.setattr(keys, "SecretServiceKeyring", unreachable)
    assert keys.keyring() is None
    seen = []
    monkeypatch.setenv("KITTY_KEYRING_SERVICE", "kitty-test")
    monkeypatch.setattr(keys, "SecretServiceKeyring", lambda service: seen.append(service) or FakeKeyring())
    assert isinstance(keys.keyring(), FakeKeyring) and seen == ["kitty-test"]


# ------------------------------------------------------------------ the owner-only file


def test_file_writes_are_owner_only_and_atomic(root, clean_env):
    assert store(root).set("dummy", VALUE) == "file"
    path = root / ".private" / "keys" / "DUMMY"
    assert path.read_text() == VALUE
    assert path.stat().st_mode & 0o777 == 0o600
    assert (root / ".private").stat().st_mode & 0o777 == 0o700
    assert (root / ".private" / "keys").stat().st_mode & 0o777 == 0o700
    assert [item.name for item in path.parent.iterdir()] == ["DUMMY"]


def test_group_readable_files_are_refused_without_their_value(root, clean_env):
    private_file(root, "BRAVE", mode=0o640)
    with pytest.raises(keys.KeysError, match="owner-only") as error:
        store(root).read("BRAVE")
    assert VALUE not in str(error.value)


@pytest.mark.parametrize("link", ["file", "keys-dir", "private-dir"])
def test_symbolic_links_are_refused_for_reading_and_writing(root, tmp_path_factory, clean_env, link):
    outside = tmp_path_factory.mktemp("outside")
    target = outside / "target"
    target.write_text(VALUE)
    target.chmod(0o600)
    if link == "private-dir":
        (root / ".private").symlink_to(outside, target_is_directory=True)
    elif link == "keys-dir":
        (root / ".private").mkdir(mode=0o700)
        (root / ".private" / "keys").symlink_to(outside, target_is_directory=True)
    else:
        (root / ".private" / "keys").mkdir(mode=0o700, parents=True)
        (root / ".private" / "keys" / "DUMMY").symlink_to(target)
    with pytest.raises(keys.KeysError, match="symbolic link"):
        store(root).read("DUMMY")
    with pytest.raises(keys.KeysError, match="symbolic link"):
        store(root).set("DUMMY", OTHER)
    assert target.read_text() == VALUE


@pytest.mark.parametrize("bad", ["", "two words", "line\nbreak", "x" * 4097, 42, None])
def test_invalid_values_and_names_are_refused_without_echo(root, bad):
    with pytest.raises(keys.KeysError) as error:
        store(root).set("DUMMY", bad)
    assert str(bad) not in str(error.value) or bad == ""
    for name in ["", "1ABC", "MY-KEY", "../X", "A" * 65]:
        with pytest.raises(keys.KeysError):
            store(root).set(name, VALUE)


# ------------------------------------------------------------------ keyring, move, remove


def test_set_prefers_the_keyring_and_drops_a_stale_file_copy(root, clean_env):
    private_file(root, "OPENROUTER", OTHER)
    fake = FakeKeyring()
    assert store(root, fake).set("OPENROUTER", VALUE) == "keyring"
    assert fake.items == {"OPENROUTER": VALUE}
    assert not (root / ".private" / "openrouter-key").exists()


def test_move_to_keyring_verifies_before_removing_the_file(root, clean_env):
    private_file(root, "OPENROUTER")
    with pytest.raises(keys.KeysError, match="No OS keyring"):
        store(root).move_to_keyring("OPENROUTER")

    class Forgetful(FakeKeyring):
        def set(self, name, value):
            pass

    with pytest.raises(keys.KeysError, match="file copy stays"):
        store(root, Forgetful()).move_to_keyring("OPENROUTER")
    assert (root / ".private" / "openrouter-key").exists()
    fake = FakeKeyring()
    store(root, fake).move_to_keyring("OPENROUTER")
    assert fake.items == {"OPENROUTER": VALUE}
    assert not (root / ".private" / "openrouter-key").exists()
    assert store(root, fake).lookup("OPENROUTER") == (VALUE, "keyring")


def test_remove_clears_keyring_and_file_but_leaves_the_environment(root, clean_env):
    clean_env.setenv("BRAVE_SEARCH_API_KEY", "synthetic-env-value")
    fake = FakeKeyring()
    fake.items["BRAVE"] = VALUE
    private_file(root, "BRAVE", OTHER)
    assert store(root, fake).remove("BRAVE") is True
    assert fake.items == {} and not store(root).file_path("BRAVE").exists()
    assert store(root, fake).lookup("BRAVE") == ("synthetic-env-value", "env")
    assert store(root, fake).remove("BRAVE") is False


def test_keyring_failures_become_generic_errors(root, clean_env):
    broken = FakeKeyring(fail=True)
    for action in (lambda s: s.read("OPENROUTER"), lambda s: s.set("OPENROUTER", OTHER), lambda s: s.remove("OPENROUTER")):
        with pytest.raises(keys.KeysError) as error:
            action(store(root, broken))
        assert error.value.status == 503 and VALUE not in str(error.value) and OTHER not in str(error.value)


def test_status_lists_known_and_custom_keys_without_values(root, clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", VALUE)
    fake = FakeKeyring()
    fake.items["ZED_TOOL"] = VALUE
    private_file(root, "DUMMY", OTHER)
    private_file(root, "BRAVE", mode=0o644)
    status = store(root, fake).status()
    text = json.dumps(status)
    assert VALUE not in text and OTHER not in text
    rows = {row["name"]: row for row in status["keys"]}
    assert list(rows) == ["OPENROUTER", "ANTHROPIC", "USAJOBS", "BRAVE", "DUMMY", "ZED_TOOL"]
    assert rows["ANTHROPIC"]["backend"] == "env" and rows["DUMMY"]["backend"] == "file"
    assert rows["ZED_TOOL"]["backend"] == "keyring" and rows["OPENROUTER"]["set"] is False
    assert rows["DUMMY"]["in_file"] and rows["DUMMY"]["custom"]
    assert "owner-only" in rows["BRAVE"]["error"]
    assert status["keyring"] is True and status["store"] == "keyring"
    assert store(root).status()["keyring"] is False and store(root).status()["store"] == "file"


def test_environment_maps_known_keys_to_their_variables_and_refuses_unset(root, clean_env):
    private_file(root, "OPENROUTER")
    private_file(root, "DUMMY", OTHER)
    assert store(root).environment(["openrouter", "DUMMY"]) == {"OPENROUTER_API_KEY": VALUE, "DUMMY": OTHER}
    with pytest.raises(keys.KeysError, match="NOPE is not set"):
        store(root).environment(["NOPE"])


def test_redact_replaces_every_value():
    assert keys.redact(f"GET failed with {VALUE} twice {VALUE}", VALUE, "") == "GET failed with [redacted] twice [redacted]"


# ------------------------------------------------------------------ Secret Service adapter


class FakeItem:
    def __init__(self, attributes):
        self.attributes = attributes

    def get_attributes(self):
        return self.attributes


class FakeBackend:
    """Stands in for keyring.backends.SecretService.Keyring."""

    priority = 5
    searches: ClassVar[list] = []

    def __init__(self):
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if self.passwords.pop((service, username), None) is None:
            raise PasswordDeleteError("No such password!")

    def get_preferred_collection(self):
        backend = self

        class Collection:
            connection = SimpleNamespace(close=lambda: None)

            def search_items(self, attributes):
                FakeBackend.searches.append(attributes)
                return [FakeItem({"service": service, "username": user})
                        for service, user in backend.passwords if service == attributes.get("service")]

        return Collection()


def test_secret_service_adapter_files_entries_under_its_service_only(monkeypatch):
    import keyring.backends.SecretService as secret_service

    monkeypatch.setattr(secret_service, "Keyring", FakeBackend)
    FakeBackend.searches = []
    ring = RealSecretService("kitty-test")
    ring._backend.passwords[("someone-else", "THEIRS")] = "not-ours"
    ring.set("DUMMY", VALUE)
    assert ring.get("DUMMY") == VALUE and ring.get("THEIRS") is None
    assert ring.names() == {"DUMMY"}
    assert FakeBackend.searches == [{"service": "kitty-test"}]
    assert ring.delete("DUMMY") is True and ring.delete("DUMMY") is False


def test_secret_service_adapter_refuses_when_no_daemon_answers(monkeypatch):
    import keyring.backends.SecretService as secret_service

    class NoDaemon(type):
        @property
        def priority(cls):
            raise RuntimeError("The Secret Service daemon is neither running nor activatable")

    monkeypatch.setattr(secret_service, "Keyring", NoDaemon("Unavailable", (), {}))
    with pytest.raises(RuntimeError):
        RealSecretService("kitty-test")


# ------------------------------------------------------------------ CLI


@pytest.fixture
def cli_root(root, clean_env, monkeypatch):
    (root / "config" / "build.json").write_text('{"schema": 1, "modules": []}')
    monkeypatch.setenv("KITTY_HOME", str(root))
    return root


def test_cli_set_list_remove_never_print_the_value(cli_root):
    from kitty.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["keys", "set", "dummy"], input=VALUE + "\n")
    assert result.exit_code == 0, result.output
    assert VALUE not in result.output and "owner-only file" in result.output
    assert (cli_root / ".private" / "keys" / "DUMMY").read_text() == VALUE
    listed = runner.invoke(app, ["keys", "list"])
    assert listed.exit_code == 0 and VALUE not in listed.output
    assert "DUMMY" in listed.output and "file" in listed.output and "No OS keyring" in listed.output
    removed = runner.invoke(app, ["keys", "remove", "DUMMY"])
    assert removed.exit_code == 0 and "Removed DUMMY" in removed.output
    assert not (cli_root / ".private" / "keys" / "DUMMY").exists()
    bad = runner.invoke(app, ["keys", "set", "BAD-NAME"], input=VALUE + "\n")
    assert bad.exit_code == 1 and VALUE not in bad.output


def test_cli_run_injects_only_the_named_keys_into_the_child(cli_root, monkeypatch):
    from kitty.cli import app

    private_file(cli_root, "DUMMY")
    private_file(cli_root, "OPENROUTER", OTHER)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-unrequested")
    launched = {}

    def execvpe(file, args, env):
        launched.update(file=file, args=args, env=env)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", execvpe)
    result = CliRunner().invoke(app, ["keys", "run", "+DUMMY", "+openrouter", "--", "tool", "-c", "x"])
    assert result.exit_code == 0 and VALUE not in result.output
    assert launched["file"] == "tool" and launched["args"] == ["tool", "-c", "x"]
    assert launched["env"]["DUMMY"] == VALUE and launched["env"]["OPENROUTER_API_KEY"] == OTHER
    assert "ANTHROPIC_API_KEY" not in launched["env"]
    assert "DUMMY" not in os.environ and os.environ.get("OPENROUTER_API_KEY") is None


def test_cli_run_refuses_unset_keys_and_missing_commands(cli_root, monkeypatch):
    from kitty.cli import app

    monkeypatch.setattr(os, "execvpe", lambda *args: pytest.fail("must not start"))
    unset = CliRunner().invoke(app, ["keys", "run", "+NOPE", "--", "true"])
    assert unset.exit_code == 1 and "NOPE is not set" in unset.output
    usage = CliRunner().invoke(app, ["keys", "run", "--", "true"])
    assert usage.exit_code == 1 and "Usage" in usage.output


def test_cli_run_end_to_end_child_gets_key_and_exit_code(cli_root):
    private_file(cli_root, "DUMMY")
    env = {key: value for key, value in os.environ.items() if key not in {"DUMMY", "OPENROUTER_API_KEY"}}
    env.update(KITTY_HOME=str(cli_root), KITTY_KEYRING="none")
    script = f'test "$DUMMY" = "{VALUE}" && echo injected; exit 7'
    result = subprocess.run([sys.executable, "-m", "kitty.cli", "keys", "run", "+DUMMY", "--", "sh", "-c", script],
                            env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 7, result.stderr
    assert result.stdout == "injected\n" and VALUE not in result.stderr


# ------------------------------------------------------------------ Settings → Keys routes
# Answered by rika-server (server/src/keys/); Python's kitty.core.keys is the reference.


@pytest.fixture
def running(tmp_path, clean_env):
    from terminal_support import front_site, make_root

    settings = make_root(tmp_path, ())
    with front_site(settings) as server:
        yield server, settings


def test_keys_routes_set_replace_remove_without_echo(running):
    front, settings = running
    status, listed = front.request("/api/keys")
    assert status == 200 and listed["keyring"] is False and listed["store"] == "file"
    assert listed == keys.for_settings(settings).status()
    status, after = front.request("/api/keys/set", {"name": "dummy", "value": " " + VALUE + "\n"})
    assert status == 200 and VALUE not in json.dumps(after)
    row = next(row for row in after["keys"] if row["name"] == "DUMMY")
    assert row["set"] and row["backend"] == "file" and row["in_file"] and row["custom"]
    path = settings.root / ".private" / "keys" / "DUMMY"
    assert path.read_text() == VALUE and path.stat().st_mode & 0o777 == 0o600
    assert (settings.root / ".private").stat().st_mode & 0o777 == 0o700
    assert [item.name for item in path.parent.iterdir()] == ["DUMMY"]
    status, after = front.request("/api/keys/set", {"name": "DUMMY", "value": OTHER})
    assert status == 200 and OTHER not in json.dumps(after)
    assert keys.for_settings(settings).read("DUMMY") == OTHER
    status, listed = front.request("/api/keys")
    assert VALUE not in json.dumps(listed) and OTHER not in json.dumps(listed)
    assert listed == keys.for_settings(settings).status()
    status, after = front.request("/api/keys/set", {"name": "openrouter", "value": VALUE})
    assert (settings.root / ".private" / "openrouter-key").read_text() == VALUE
    assert keys.for_settings(settings).lookup("OPENROUTER") == (VALUE, "file")
    status, after = front.request("/api/keys/remove", {"name": "DUMMY"})
    assert status == 200 and all(row["name"] != "DUMMY" for row in after["keys"])
    assert not path.exists() and after == keys.for_settings(settings).status()
    status, again = front.request("/api/keys/remove", {"name": "DUMMY"})
    assert status == 200 and again == after


def test_keys_routes_use_the_mutation_guard_and_bounded_bodies(running):
    from kitty import web_auth

    front, settings = running
    root = settings.root
    status, _ = front.request("/api/keys/set", {"name": "DUMMY", "value": VALUE}, headers={"X-Kitty-Request": None})
    assert status == 403
    status, _ = front.request("/api/keys/set", {"name": "DUMMY", "value": VALUE},
                              headers={"Origin": "http://evil.example"})
    assert status == 403
    assert not (root / ".private" / "keys" / "DUMMY").exists()
    for body, message in (
        ({"name": "DUMMY", "value": "has space " + VALUE},
         "A key is one line of printable characters without spaces (at most 4096)"),
        ({"name": "DUMMY", "value": VALUE + "x" * 4097},
         "A key is one line of printable characters without spaces (at most 4096)"),
        ({"name": "DUMMY", "value": 42}, "Enter the key as text"),
        ({"name": "BAD-NAME", "value": VALUE}, "Key names use A-Z, 0-9 and _ and start with a letter"),
        ({"name": None, "value": VALUE}, "A key name is required"),
        ({"name": "DUMMY", "value": VALUE, "extra": 1}, 'Expected {"name", "value"}'),
        ({"value": VALUE}, 'Expected {"name", "value"}'),
    ):
        status, answer = front.request("/api/keys/set", body)
        assert (status, answer) == (400, {"error": message}), body
    status, answer = front.request("/api/keys/remove", {"name": "DUMMY", "value": VALUE})
    assert (status, answer) == (400, {"error": 'Expected {"name"}'})
    assert not (root / ".private" / "keys").exists()
    status, body = front.request("/api/keys/move", {"name": "DUMMY"})
    assert (status, body) == (409, {"error": "No OS keyring is available"})
    for path in ("/api/keys?x=1", "/api/keys?x"):
        assert front.request(path) == (400, {"error": "This route does not accept query parameters"})
    status, body = front.request("/api/keys/set?x=1", {"name": "DUMMY", "value": VALUE})
    assert (status, body) == (400, {"error": "This route does not accept query parameters"})
    for path, body in (("/api/keys/set", None), ("/api/keys/other", {"name": "DUMMY"}), ("/api/keys", {"name": "DUMMY"})):
        assert front.request(path, body) == (404, {"error": "API route not found"})
    assert front.request("/api/keys", method="HEAD") == (200, None)
    assert front.request("/api/keys", headers={web_auth.HEADER: None})[0] == 401


@pytest.mark.parametrize("link", ["file", "keys-dir", "private-dir"])
def test_keys_routes_refuse_symbolic_links_without_the_value(running, tmp_path_factory, link):
    front, settings = running
    root = settings.root
    outside = tmp_path_factory.mktemp("outside")
    target = outside / "DUMMY"
    target.write_text(VALUE)
    target.chmod(0o600)
    private = root / ".private"  # the worker already made it (endpoint.json)
    if link == "private-dir":
        if private.exists():
            private.rename(outside / "moved-private")
        private.symlink_to(outside, target_is_directory=True)
    elif link == "keys-dir":
        private.mkdir(mode=0o700, exist_ok=True)
        (private / "keys").symlink_to(outside, target_is_directory=True)
    else:
        (private / "keys").mkdir(mode=0o700, parents=True)
        (private / "keys" / "DUMMY").symlink_to(target)
    refused = (409, {"error": "Private key storage must not be a symbolic link"})
    assert front.request("/api/keys/set", {"name": "DUMMY", "value": OTHER}) == refused
    assert front.request("/api/keys/remove", {"name": "DUMMY"}) == refused
    assert target.read_text() == VALUE
    status, listed = front.request("/api/keys")
    assert status == 200 and VALUE not in json.dumps(listed)
    assert listed == keys.for_settings(settings).status()


def test_keys_routes_report_unsafe_files_and_account_passwords_like_python(running):
    front, settings = running
    private_file(settings.root, "BRAVE", mode=0o640)
    private_file(settings.root, "USAJOBS", "caf\u00e9")
    private_file(settings.root, "APPLY_PW_ACME_SYNTHETIC", OTHER)
    private_file(settings.root, "ZED_TOOL", OTHER)
    status, listed = front.request("/api/keys")
    assert status == 200 and VALUE not in json.dumps(listed) and OTHER not in json.dumps(listed)
    assert listed == keys.for_settings(settings).status()
    rows = {row["name"]: row for row in listed["keys"]}
    assert list(rows) == ["OPENROUTER", "ANTHROPIC", "USAJOBS", "BRAVE", "ZED_TOOL"]
    assert rows["BRAVE"]["error"] == "Private key files must be owner-only (chmod 600)"
    assert rows["USAJOBS"]["error"] == "A private key file is not plain text"
    assert listed["account_passwords"] == {"count": 1, "in_file": 1}
    assert listed["warnings"] == [keys.ACCOUNT_FILE_WARNING]
    assert front.request("/api/keys/move", {"name": "BRAVE"}) == (409, {"error": "No OS keyring is available"})


def test_keys_routes_fall_back_to_the_environment_and_dotenv(tmp_path, clean_env):
    from terminal_support import front_site, make_root

    settings = make_root(tmp_path, ())
    # The worker loads the root's .env into its environment at start; the front reads it too.
    (settings.root / ".env").write_text(f"USAJOBS_API_KEY={VALUE}\nAPPLY_PW_ACME={VALUE}\nZED_TOOL=\n")
    with front_site(settings, env={"ANTHROPIC_API_KEY": f" {OTHER} ", "APPLY_PW_ACME": OTHER}) as front:
        status, listed = front.request("/api/keys")
    assert status == 200 and VALUE not in json.dumps(listed) and OTHER not in json.dumps(listed)
    rows = {row["name"]: row for row in listed["keys"]}
    assert rows["ANTHROPIC"]["backend"] == "env" and rows["USAJOBS"]["backend"] == "env"
    assert rows["OPENROUTER"]["set"] is False and list(rows) == ["OPENROUTER", "ANTHROPIC", "USAJOBS", "BRAVE"]
    assert listed["account_passwords"] == {"count": 0, "in_file": 0}


# ------------------------------------------------------------------ a real Secret Service
# A private session bus with gnome-keyring unlocked by a throwaway password, in the test's
# own HOME and XDG folders: the owner's keyring is never reached.


SERVICE = "kitty-interop-test"


@pytest.fixture
def secret_service(tmp_path):
    import shutil
    import time

    daemon, keyring_daemon, send = (shutil.which(name) for name in ("dbus-daemon", "gnome-keyring-daemon", "dbus-send"))
    if not (daemon and keyring_daemon and send):
        pytest.skip("dbus-daemon and gnome-keyring-daemon are not installed")
    home = tmp_path / "ss"
    folders = {name: home / name.lower() for name in ("HOME", "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")}
    for folder in folders.values():
        folder.mkdir(mode=0o700, parents=True)
    socket = folders["XDG_RUNTIME_DIR"] / "bus"
    config = home / "bus.conf"
    config.write_text(
        '<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"'
        ' "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">'
        f"<busconfig><type>session</type><listen>unix:path={socket}</listen><auth>EXTERNAL</auth>"
        '<policy context="default"><allow send_destination="*"/><allow own="*"/></policy></busconfig>')
    address = f"unix:path={socket}"
    env = {**os.environ, **{name: str(path) for name, path in folders.items()}, "DBUS_SESSION_BUS_ADDRESS": address}
    bus = subprocess.Popen([daemon, "--config-file", str(config), "--nofork"], env=env)
    ring = None
    try:
        deadline = time.time() + 30
        while not socket.exists():
            assert time.time() < deadline and bus.poll() is None, "the private bus did not start"
            time.sleep(0.05)
        ring = subprocess.Popen([keyring_daemon, "--unlock", "--components=secrets", "--foreground"],
                                env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ring.stdin.write(b"synthetic-throwaway-password")
        ring.stdin.close()
        owner = [send, "--session", "--print-reply", "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus",
                 "org.freedesktop.DBus.NameHasOwner", "string:org.freedesktop.secrets"]
        while "boolean true" not in subprocess.run(owner, env=env, capture_output=True, text=True, check=False).stdout:
            assert time.time() < deadline and ring.poll() is None, "gnome-keyring did not start"
            time.sleep(0.1)
        yield {"DBUS_SESSION_BUS_ADDRESS": address, "KITTY_KEYRING": "", "KITTY_KEYRING_SERVICE": SERVICE,
               **{name: str(path) for name, path in folders.items() if name != "XDG_DATA_HOME"}}
    finally:
        for process in (ring, bus):
            if process is not None:
                process.terminate()
                process.wait(timeout=10)


def python_keys(settings, env, code):
    """Run `code` against `kitty.core.keys` in a process on the private bus; its JSON result."""
    script = ("import json, sys\nfrom kitty.core import keys\nfrom kitty.settings import Settings\n"
              f"store = keys.for_settings(Settings.load(root={str(settings.root)!r}, db_path={str(settings.db_path)!r}))\n"
              "assert store.store is not None, 'no keyring'\n" + code)
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ, **env}, capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout) if result.stdout.strip() else None


def test_front_and_python_share_a_real_secret_service(tmp_path, clean_env, secret_service):
    from terminal_support import front_site, make_root

    settings = make_root(tmp_path, ())
    env = secret_service
    python_keys(settings, env, f"store.set('OPENROUTER', {VALUE!r}); store.set('zed_tool', {OTHER!r})")
    with front_site(settings, env=env) as front:
        status, listed = front.request("/api/keys")
        assert status == 200 and VALUE not in json.dumps(listed) and OTHER not in json.dumps(listed)
        assert (listed["keyring"], listed["keyring_label"], listed["store"]) == (True, "OS keyring (Secret Service)", "keyring")
        rows = {row["name"]: row for row in listed["keys"]}
        assert rows["OPENROUTER"]["backend"] == "keyring" and rows["ZED_TOOL"]["backend"] == "keyring"
        assert listed == python_keys(settings, env, "print(json.dumps(store.status()))")

        status, after = front.request("/api/keys/set", {"name": "DUMMY", "value": VALUE})
        assert status == 200 and VALUE not in json.dumps(after)
        assert next(row for row in after["keys"] if row["name"] == "DUMMY")["backend"] == "keyring"
        assert not (settings.root / ".private" / "keys" / "DUMMY").exists()
        assert python_keys(settings, env, "print(json.dumps(store.lookup('DUMMY')))") == [VALUE, "keyring"]
        # The item Python's stack would have made: label, attributes, content type.
        item = python_keys(settings, env, (
            "import secretstorage\nconnection = secretstorage.dbus_init()\n"
            "collection = secretstorage.get_default_collection(connection)\n"
            f"item, = collection.search_items({{'username': 'DUMMY', 'service': {SERVICE!r}}})\n"
            "attributes = item.get_attributes(); attributes.pop('xdg:schema', None)\n"
            "print(json.dumps([item.get_label(), attributes, item.get_secret_content_type()]))"))
        assert item == [f"Password for 'DUMMY' on '{SERVICE}'",
                        {"username": "DUMMY", "service": SERVICE, "application": "Python keyring library"}, "text/plain"]
        status, _ = front.request("/api/keys/set", {"name": "DUMMY", "value": OTHER})
        assert status == 200 and python_keys(settings, env, "print(json.dumps(store.read('DUMMY')))") == OTHER
        assert python_keys(settings, env, (
            "import secretstorage\ncollection = secretstorage.get_default_collection(secretstorage.dbus_init())\n"
            f"print(len(list(collection.search_items({{'username': 'DUMMY', 'service': {SERVICE!r}}}))))")) == 1

        private_file(settings.root, "BRAVE")
        status, moved = front.request("/api/keys/move", {"name": "BRAVE"})
        assert status == 200 and VALUE not in json.dumps(moved)
        row = next(row for row in moved["keys"] if row["name"] == "BRAVE")
        assert row["backend"] == "keyring" and not row["in_file"]
        assert not (settings.root / ".private" / "keys" / "BRAVE").exists()
        assert python_keys(settings, env, "print(json.dumps(store.lookup('BRAVE')))") == [VALUE, "keyring"]
        assert front.request("/api/keys/move", {"name": "BRAVE"}) == (404, {"error": "That key has no file copy to move"})

        status, removed = front.request("/api/keys/remove", {"name": "ZED_TOOL"})
        assert status == 200 and all(row["name"] != "ZED_TOOL" for row in removed["keys"])
        assert python_keys(settings, env, "print(json.dumps(store.read('ZED_TOOL')))") == ""
        python_keys(settings, env, "store.remove('DUMMY')")
        status, listed = front.request("/api/keys")
        assert all(row["name"] != "DUMMY" for row in listed["keys"])
        assert listed == python_keys(settings, env, "print(json.dumps(store.status()))")


# ------------------------------------------------------------------ consumers


def test_openrouter_triage_and_ai_read_through_keys(workspace, clean_env, monkeypatch):
    from types import SimpleNamespace as NS

    from kitty import web_ai
    from kitty.core.triage import openrouter

    fake = FakeKeyring()
    fake.items["OPENROUTER"] = VALUE
    monkeypatch.setattr(keys, "keyring", lambda: fake)
    assert openrouter._key(workspace.settings) == VALUE
    assert web_ai._key(NS(settings=workspace.settings)) == VALUE
    web_ai._write_key(NS(settings=workspace.settings), "synthetic-replaced-key")
    assert fake.items["OPENROUTER"] == "synthetic-replaced-key"
    fake.items.clear()
    with pytest.raises(openrouter.TriageError, match="kitty keys set OPENROUTER"):
        openrouter._key(workspace.settings)


def test_anthropic_client_gets_its_key_from_keys(workspace, clean_env, monkeypatch):
    from kitty.core.triage import run

    with pytest.raises(run.TriageError, match="kitty keys set ANTHROPIC"):
        run.make_client(workspace.settings)
    private_file(workspace.settings.root, "ANTHROPIC")
    made = {}
    monkeypatch.setattr(run.anthropic, "Anthropic", lambda **kwargs: made.update(kwargs) or "client")
    assert run.make_client(workspace.settings) == "client" and made == {"api_key": VALUE}


def test_usajobs_reads_through_keys_and_redacts_errors(workspace, clean_env):
    from fakes import FakeHttp

    from kitty.core.discovery.context import DiscoverContext
    from kitty.core.discovery.http import HttpError
    from kitty.core.discovery.usajobs import UsaJobsAdapter, search_url

    private_file(workspace.settings.root, "USAJOBS")
    options = workspace.config.sources.sources["usajobs"].options
    first = search_url(options["queries"][0], 1, options["results_per_page"])
    http = FakeHttp({first: HttpError(f"HTTP 500 echoing {VALUE}")})
    http.client = SimpleNamespace(headers={})
    result = UsaJobsAdapter(DiscoverContext(workspace.config, lambda name: http, settings=workspace.settings)).fetch(None)
    assert http.client.headers["Authorization-Key"] == VALUE
    assert VALUE not in json.dumps([result.error, result.stats])


def test_brave_search_reads_through_keys(workspace, clean_env, monkeypatch):
    from kitty.core.discovery import websearch
    from kitty.core.discovery.context import DiscoverContext

    private_file(workspace.settings.root, "BRAVE")
    tracks = [track.model_copy(update={"sources": [*track.sources, "websearch"]})
              for track in workspace.config.tracks.tracks]
    config = SimpleNamespace(sources=SimpleNamespace(sources={"websearch": SimpleNamespace(options={"enable_search": True})}),
                             tracks=SimpleNamespace(tracks=tracks))
    used = []

    class Pages:
        def __init__(self, http):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def brave_json(self, url, key):
            used.append(key)
            raise websearch.HttpError(f"HTTP 401 for {key}")

    monkeypatch.setattr(websearch, "PublicPages", Pages)
    http = SimpleNamespace(requests_made=0, cache_hits=0)
    result = websearch.WebSearchAdapter(DiscoverContext(config, lambda name: http, settings=workspace.settings)).fetch(None)
    assert used and used[0] == VALUE
    assert VALUE not in json.dumps([result.error, result.stats], default=str)


def test_doctor_reports_where_keys_live_never_values(workspace, clean_env):
    from kitty.core import doctor

    private_file(workspace.settings.root, "USAJOBS")
    checks = {check.name: check for check in doctor.check_keys(workspace.settings)}
    assert checks["key/USAJOBS"].detail == "set (file)"
    assert checks["key/ANTHROPIC"].warning and "kitty keys set ANTHROPIC" in checks["key/ANTHROPIC"].fix
    assert VALUE not in str(checks)


def test_no_duplicated_key_readers_remain():
    """Consumers read through kitty.core.keys only (clean cutover)."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "kitty"
    offenders = []
    for path in source.rglob("*.py"):
        if path.name == "keys.py" or "apply" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ('"openrouter-key"', "'openrouter-key'", 'environ.get("OPENROUTER_API_KEY"', "environ.get('OPENROUTER_API_KEY'",
                       'environ.get("ANTHROPIC_API_KEY"', 'environ.get("USAJOBS_API_KEY"', 'environ.get("BRAVE_SEARCH_API_KEY"'):
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []
