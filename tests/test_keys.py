"""kitty Keys (Q10): resolution order, owner-only files, the Secret Service adapter, the
`kitty keys` CLI, the Settings → Keys routes and every consumer reading through it.

Every key value here is synthetic. No test reaches the real keyring: conftest turns it
off, and keyring tests inject FakeKeyring or a fake Secret Service backend.
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


@pytest.fixture
def running(tmp_path, clean_env):
    from terminal_support import make_root, site

    with site(make_root(tmp_path, ())) as server:
        yield server


def test_keys_routes_set_replace_remove_without_echo(running):
    status, listed = running.request("/api/keys")
    assert status == 200 and listed["keyring"] is False and listed["store"] == "file"
    status, after = running.request("/api/keys/set", {"name": "DUMMY", "value": VALUE})
    assert status == 200 and VALUE not in json.dumps(after)
    row = next(row for row in after["keys"] if row["name"] == "DUMMY")
    assert row["set"] and row["backend"] == "file"
    status, after = running.request("/api/keys/set", {"name": "DUMMY", "value": OTHER})
    assert status == 200 and OTHER not in json.dumps(after)
    assert keys.for_settings(running.app.settings).read("DUMMY") == OTHER
    status, listed = running.request("/api/keys")
    assert VALUE not in json.dumps(listed) and OTHER not in json.dumps(listed)
    status, after = running.request("/api/keys/remove", {"name": "DUMMY"})
    assert status == 200 and all(row["name"] != "DUMMY" for row in after["keys"])


def test_keys_routes_use_the_mutation_guard_and_bounded_bodies(running):
    root = running.app.settings.root
    status, _ = running.request("/api/keys/set", {"name": "DUMMY", "value": VALUE}, headers={"X-Kitty-Request": None})
    assert status == 403
    status, _ = running.request("/api/keys/set", {"name": "DUMMY", "value": VALUE},
                                headers={"Origin": "http://evil.example"})
    assert status == 403
    assert not (root / ".private" / "keys" / "DUMMY").exists()
    status, body = running.request("/api/keys/set", {"name": "DUMMY", "value": "has space " + VALUE})
    assert status == 400 and VALUE not in json.dumps(body)
    status, body = running.request("/api/keys/set", {"name": "DUMMY", "value": VALUE, "extra": 1})
    assert status == 400 and VALUE not in json.dumps(body)
    status, body = running.request("/api/keys/move", {"name": "DUMMY"})
    assert status == 409 and "No OS keyring" in body["error"]
    status, _ = running.request("/api/keys?x=1")
    assert status == 400


def test_keys_move_route_moves_the_file_copy_into_an_available_keyring(running, monkeypatch):
    fake = FakeKeyring()
    private_file(running.app.settings.root, "OPENROUTER")
    monkeypatch.setattr(keys, "keyring", lambda: fake)
    status, after = running.request("/api/keys/move", {"name": "OPENROUTER"})
    assert status == 200 and VALUE not in json.dumps(after)
    row = next(row for row in after["keys"] if row["name"] == "OPENROUTER")
    assert row["backend"] == "keyring" and not row["in_file"]
    assert fake.items == {"OPENROUTER": VALUE}


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
