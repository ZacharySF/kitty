"""Module packs (server/src/packs.rs, scripts/build-packs.py): the signed index, download,
verification, atomic install and rollback, uninstall, the module gate and the front's rules.

Every pack is synthetic, signed with a throwaway key and served from loopback (tests/pack_support);
the packs folder is the test's own XDG data directory.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import time

import pytest

from kitty import web_auth
from pack_support import VERSION, Key, Release, build_packs, make_pack
from terminal_support import front_site, make_root

ASSET = "assets/BrainStage-synthetic.js"
CONTENT = b"export const brain = 'synthetic';\n"
BAD_SIGNATURE = "The download list is not signed by rika!'s release key"


@pytest.fixture
def release():
    serving = Release()
    yield serving
    serving.close()


@pytest.fixture
def key():
    return Key()


def start(tmp_path, release, key, modules=("workspace",), files=None):
    """A front over a workspace whose website lacks the brain pack's files, and its packs folder."""
    settings = make_root(tmp_path, modules)
    index, archives, _ = make_pack(settings.root, files or {ASSET: CONTENT})
    env = {"KITTY_PACKS_URL": release.url, "KITTY_PACKS_KEY": key.public}
    return settings, index, archives, env, settings.root / "xdg-data" / "kitty" / "packs"


def brain(site) -> dict:
    status, body = site.request("/api/packs")
    assert status == 200, body
    return next(pack for pack in body["packs"] if pack["id"] == "brain")


def settle(site, timeout=20) -> dict:
    deadline = time.monotonic() + timeout
    while (pack := brain(site))["state"] == "downloading":
        assert time.monotonic() < deadline, pack
        time.sleep(0.05)
    return pack


def leftovers(packs) -> list[str]:
    return sorted(name for name in os.listdir(packs) if name.startswith(".tmp-")) if packs.is_dir() else []


# ------------------------------------------------------------------ build

def test_a_pack_build_is_reproducible_strips_the_website_and_lists_its_archive(tmp_path):
    files = {"assets/a.js": b"a" * 100, "assets/b.css": b"b" * 10}
    first, archives, dist = make_pack(tmp_path / "one", files)
    again, archives_again, _ = make_pack(tmp_path / "two", files)
    assert first == again and archives == archives_again
    [entry] = first["packs"]
    assert first == {"format": 1, "version": VERSION, "packs": [entry]}
    data = archives[entry["file"]]
    assert entry == {"id": "brain", "version": VERSION, "file": f"rika-pack-brain-{VERSION}.tar.gz",
                     "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "installed_size": entry["installed_size"]}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile("pack.json").read())
        assert all(member.isfile() and member.mtime == 0 and member.uid == 0 for member in tar.getmembers())
        assert entry["installed_size"] == sum(member.size for member in tar.getmembers())
    assert names == ["pack.json", "web/assets/a.js", "web/assets/b.css"]
    assert manifest == {"id": "brain", "version": VERSION}
    # The base install no longer carries the pack's files; the list of them stays.
    assert not (dist / "assets/a.js").exists() and (dist / "pack-files.json").is_file()
    with pytest.raises(ValueError, match="rebuild the website"):
        build_packs.build(dist, tmp_path / "again", VERSION)


def test_the_web_build_lists_the_brain_pack_and_the_base_never_imports_it_statically():
    """web/vite.config.ts: the Brain and the 3D engine are the pack; index.html loads neither."""
    dist = build_packs.ROOT / "web/dist"
    if not (dist / "pack-files.json").is_file():
        pytest.skip("the website is not built; run npm run build in web/")
    listed = json.loads((dist / "pack-files.json").read_text())
    assert set(listed) == {"brain"}
    names = [name.rsplit("/", 1)[-1].split("-")[0] for name in listed["brain"]]
    assert {"BrainStage", "brainScene", "three.module", "3d"} <= set(names)
    page = (dist / "index.html").read_text()
    assert not any(name in page for name in listed["brain"])


# ------------------------------------------------------------------ verify and install

def test_a_signed_pack_installs_gates_the_module_and_is_served(tmp_path, release, key):
    settings, index, archives, env, packs = start(tmp_path, release, key)
    release.publish(key, index, archives)
    with front_site(settings, env) as site:
        pack = brain(site)
        assert pack["state"] == "available" and pack["size"] == index["packs"][0]["size"]
        assert pack["modules"] == ["brain"] and pack["installed_version"] is None
        assert site.request("/" + ASSET)[0] == 404
        # The module needs its pack first; nothing is written.
        build = (settings.config_dir / "build.json").read_text()
        status, body = site.request("/api/modules", {"enabled": ["workspace", "brain"]})
        assert status == 409 and body["error"].startswith("Brain needs its download first")
        assert (settings.config_dir / "build.json").read_text() == build

        status, body = site.request("/api/packs/install", {"id": "brain"})
        assert status == 200, body
        pack = settle(site)
        assert pack["state"] == "installed" and pack["error"] is None and pack["installed_version"] == VERSION
        status, served = site.request("/" + ASSET)
        assert status == 200 and served == CONTENT
        installed = json.loads((packs / "installed.json").read_text())
        assert installed["brain"]["version"] == VERSION and installed["brain"]["sha256"] == index["packs"][0]["sha256"]
        assert (packs / installed["brain"]["dir"] / "web" / ASSET).read_bytes() == CONTENT
        assert leftovers(packs) == []
        # Installing again is a no-op; the module can now be turned on.
        release.requests.clear()
        assert site.request("/api/packs/install", {"id": "brain"})[0] == 200
        assert archives.keys().isdisjoint(release.requests)
        status, body = site.request("/api/modules", {"enabled": ["workspace", "brain"]})
        assert status == 200 and body["configured"] == ["workspace", "brain"]
        # A pack never serves a path outside its folder.
        assert site.request("/assets/../installed.json")[0] in (400, 404)


def test_progress_is_reported_while_the_download_runs(tmp_path, release, key):
    settings, index, archives, env, _ = start(tmp_path, release, key, files={ASSET: os.urandom(400_000)})
    release.publish(key, index, archives)
    release.held = index["packs"][0]["file"]
    with front_site(settings, env) as site:
        assert site.request("/api/packs/install", {"id": "brain"})[0] == 200
        deadline = time.monotonic() + 10
        while not ((pack := brain(site))["progress"] or {}).get("received"):
            assert time.monotonic() < deadline, pack
            time.sleep(0.02)
        assert pack["state"] == "downloading"
        assert 0 < pack["progress"]["received"] < pack["progress"]["total"] == index["packs"][0]["size"]
        # While it runs, neither a second download nor an uninstall starts.
        assert site.request("/api/packs/install", {"id": "brain"})[0] == 200
        assert site.request("/api/packs/uninstall", {"id": "brain"})[0] == 409
        release.hold.set()
        assert settle(site)["state"] == "installed"
    assert release.requests.count(index["packs"][0]["file"]) == 1


@pytest.mark.parametrize("fault", ["other key", "other file", "tampered index", "other version", "no signature"])
def test_an_index_without_a_valid_signature_offers_nothing(tmp_path, release, key, fault):
    settings, index, archives, env, packs = start(tmp_path, release, key)
    if fault == "other key":
        release.publish(key, index, archives, signer=Key())
    elif fault == "other file":
        release.publish(key, index, archives)
        release.files["packs.json.sig"] = key.sign(release.files["packs.json"], file="kitty_0.3.36_amd64.deb")
    elif fault == "tampered index":
        release.publish(key, index, archives)
        release.files["packs.json"] = release.files["packs.json"].replace(b'"format": 1', b'"format":  1')
    elif fault == "other version":
        index = {**index, "version": "0.0.1", "packs": [{**entry, "version": "0.0.1"} for entry in index["packs"]]}
        release.publish(key, index, archives)
    else:
        release.publish(key, index, archives)
        del release.files["packs.json.sig"]
    with front_site(settings, env) as site:
        status, listing = site.request("/api/packs")
        assert status == 200
        pack = next(pack for pack in listing["packs"] if pack["id"] == "brain")
        assert pack["state"] == "unavailable" and pack["size"] is None
        expected = {"other version": "The download list is for rika! 0.0.1", "no signature": "No downloads are published"}
        assert listing["index"]["error"].startswith(expected.get(fault, BAD_SIGNATURE))
        status, body = site.request("/api/packs/install", {"id": "brain"})
        assert status == 503 and body["error"] == listing["index"]["error"]
        assert archives.keys().isdisjoint(release.requests)
    assert not (packs / "installed.json").exists()


@pytest.mark.parametrize("fault, message", [
    ("bad hash", "does not match its signed checksum"),
    ("truncated", "nothing was installed"),
    ("larger", "larger than its signed size"),
    ("escaping path", "not a valid pack"),
    ("wrong manifest", "not a valid pack"),
])
def test_a_download_that_is_not_the_signed_pack_installs_nothing(tmp_path, release, key, fault, message):
    settings, index, archives, env, packs = start(tmp_path, release, key)
    [entry] = index["packs"]
    name = entry["file"]
    if fault == "bad hash":
        archives[name] = archives[name][:-1] + bytes([archives[name][-1] ^ 1])
    elif fault == "truncated":
        release.cut = name
    elif fault == "larger":
        archives[name] += b"\0"
    else:
        # A correctly signed archive whose content breaks the pack rules.
        members = [("pack.json", json.dumps({"id": "brain", "version": VERSION if fault == "escaping path" else "0.0.1"}).encode()),
                   ("../escaped.js" if fault == "escaping path" else "web/" + ASSET, CONTENT)]
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for member, data in members:
                info = tarfile.TarInfo(member)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        archives[name] = buffer.getvalue()
        entry = {**entry, "size": len(archives[name]), "sha256": hashlib.sha256(archives[name]).hexdigest(),
                 "installed_size": sum(len(data) for _, data in members)}
        index = {**index, "packs": [entry]}
    release.publish(key, index, archives)
    with front_site(settings, env) as site:
        assert site.request("/api/packs/install", {"id": "brain"})[0] == 200
        pack = settle(site)
        assert pack["state"] == "available" and message in pack["error"], pack
        assert site.request("/" + ASSET)[0] == 404
        status, body = site.request("/api/modules", {"enabled": ["brain"]})
        assert status == 409
    assert not (packs / "installed.json").exists()
    assert leftovers(packs) == [] and not (tmp_path / "root" / "xdg-data" / "kitty" / "escaped.js").exists()
    assert {name for name in os.listdir(packs) if not name.startswith(".")} <= {"index.json", "index.json.sig"}


def test_a_failed_update_keeps_the_installed_pack_and_a_good_one_replaces_it(tmp_path, release, key):
    settings, index, archives, env, packs = start(tmp_path, release, key)
    release.publish(key, index, archives)
    with front_site(settings, env) as site:
        site.request("/api/packs/install", {"id": "brain"})
        assert settle(site)["state"] == "installed"
        before = (packs / "installed.json").read_bytes()
        first_dir = json.loads(before)["brain"]["dir"]

        # A newer build of the same version whose download is cut short: nothing changes.
        newer, newer_archives, _ = make_pack(tmp_path / "newer", {ASSET: b"export const brain = 'newer';\n"})
        release.publish(key, newer, newer_archives)
        release.cut = newer["packs"][0]["file"]
        site.request("/api/packs/install", {"id": "brain"})
        pack = settle(site)
        assert pack["state"] == "installed" and "nothing was installed" in pack["error"]
        assert (packs / "installed.json").read_bytes() == before
        assert site.request("/" + ASSET)[1] == CONTENT
        assert leftovers(packs) == []

        # Delivered whole, it replaces the old files in one switch; the old folder goes.
        release.cut = None
        site.request("/api/packs/install", {"id": "brain"})
        pack = settle(site)
        assert pack["state"] == "installed" and pack["error"] is None
        assert site.request("/" + ASSET)[1] == b"export const brain = 'newer';\n"
        assert not (packs / first_dir).exists()
        assert sorted(name for name in os.listdir(packs) if not name.startswith(".")) == sorted(
            ["installed.json", "index.json", "index.json.sig", json.loads((packs / "installed.json").read_text())["brain"]["dir"]])


def test_crash_leftovers_are_swept_and_an_older_versions_pack_is_not_used(tmp_path, release, key):
    settings, index, archives, env, packs = start(tmp_path, release, key)
    (packs / "brain-0.0.1-abcd" / "web" / "assets").mkdir(parents=True)
    (packs / "brain-0.0.1-abcd" / "web" / ASSET).write_bytes(b"old")
    (packs / "brain-orphan").mkdir()
    (packs / ".tmp-brain-stale").mkdir()
    (packs / ".tmp-brain-live").mkdir()
    stale = time.time() - 3 * 24 * 3600
    os.utime(packs / ".tmp-brain-stale", (stale, stale))
    (packs / "installed.json").write_text(json.dumps({"brain": {
        "version": "0.0.1", "dir": "brain-0.0.1-abcd", "size": 1, "installed_size": 3, "sha256": "0" * 64}}))
    release.publish(key, index, archives)
    with front_site(settings, env) as site:
        assert not (packs / "brain-orphan").exists() and not (packs / ".tmp-brain-stale").exists()
        assert (packs / ".tmp-brain-live").exists(), "a download another front may still be writing stays"
        pack = brain(site)
        assert pack["state"] == "outdated" and pack["installed_version"] == "0.0.1"
        # Another version's chunks would import files this build does not have.
        assert site.request("/" + ASSET)[0] == 404
        assert site.request("/api/modules", {"enabled": ["brain"]})[0] == 409
        site.request("/api/packs/install", {"id": "brain"})
        assert settle(site)["state"] == "installed"
        assert not (packs / "brain-0.0.1-abcd").exists()


def test_uninstall_waits_until_the_module_is_off_and_removes_every_file(tmp_path, release, key):
    settings, index, archives, env, packs = start(tmp_path, release, key)
    release.publish(key, index, archives)
    with front_site(settings, env) as site:
        site.request("/api/packs/install", {"id": "brain"})
        settle(site)
        assert site.request("/api/modules", {"enabled": ["workspace", "brain"]})[0] == 200
        status, body = site.request("/api/packs/uninstall", {"id": "brain"})
        assert status == 409 and body["error"] == "Brain is turned on: turn it off and save before removing its download"
        assert site.request("/" + ASSET)[0] == 200
        assert site.request("/api/modules", {"enabled": ["workspace"]})[0] == 200
        status, body = site.request("/api/packs/uninstall", {"id": "brain"})
        assert status == 200
        assert next(pack for pack in body["packs"] if pack["id"] == "brain")["state"] == "available"
        assert site.request("/" + ASSET)[0] == 404
        assert json.loads((packs / "installed.json").read_text()) == {}
        assert sorted(name for name in os.listdir(packs) if not name.startswith(".")) == ["index.json", "index.json.sig", "installed.json"]
        # Removing what is not installed is a no-op; unknown packs and bodies are refused.
        assert site.request("/api/packs/uninstall", {"id": "brain"})[0] == 200
        assert site.request("/api/packs/install", {"id": "nope"})[0] == 404
        assert site.request("/api/packs/install", {"id": "brain", "more": 1})[0] == 400
        assert site.request("/api/packs/install", {})[0] == 400


def test_a_build_that_carries_the_pack_has_it_built_in(tmp_path, release, key):
    settings, index, archives, env, _ = start(tmp_path, release, key)
    (settings.root / "dist" / "assets").mkdir(exist_ok=True)
    (settings.root / "dist" / ASSET).write_bytes(CONTENT)
    with front_site(settings, env) as site:
        assert brain(site)["state"] == "built-in"
        assert site.request("/api/packs/install", {"id": "brain"})[0] == 409
        assert site.request("/api/packs/uninstall", {"id": "brain"})[0] == 409
        assert site.request("/api/modules", {"enabled": ["workspace", "brain"]})[0] == 200


def test_the_cached_index_lists_packs_offline(tmp_path, release, key):
    settings, index, archives, env, packs = start(tmp_path, release, key)
    release.publish(key, index, archives)
    with front_site(settings, env) as site:
        assert brain(site)["state"] == "available"
    release.files.clear()
    with front_site(settings, env) as site:
        status, listing = site.request("/api/packs")
        pack = next(pack for pack in listing["packs"] if pack["id"] == "brain")
        assert pack["state"] == "available" and listing["index"]["error"].startswith("No downloads are published")
    # A cache that no longer verifies is not used.
    (packs / "index.json").write_text((packs / "index.json").read_text().replace("brain", "brian"))
    with front_site(settings, env) as site:
        assert brain(site)["state"] == "unavailable"


# ------------------------------------------------------------------ the front's rules

@pytest.mark.parametrize("path, body", [("/api/packs", None), ("/api/packs/install", {"id": "brain"}),
                                        ("/api/packs/uninstall", {"id": "brain"})])
def test_pack_routes_follow_the_front_rules(tmp_path, release, key, path, body):
    settings, index, archives, env, packs = start(tmp_path, release, key)
    release.publish(key, index, archives)
    with front_site(settings, env) as site:
        origin = site.server.origin
        refused = {"error": web_auth.REFUSAL}
        assert site.request(path, body, headers={web_auth.HEADER: None}) == (401, refused)
        assert site.request(path, body, headers={web_auth.HEADER: "0" * 64}) == (401, refused)
        assert site.request(path, body, headers={"Origin": "http://127.0.0.1:1"})[0] == 403
        assert site.request(path, body, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert site.request(path, body, headers={"Host": "localhost"})[0] == 403
        assert site.request(path + "?x=1", body)[0] == 400
        if body is not None:
            assert site.request(path, body, headers={"X-Kitty-Request": None})[0] == 403
            assert site.request(path, body, headers={"Content-Type": "text/plain"})[0] == 415
            assert site.request(path, method="GET")[0] == 404
        else:
            assert site.request(path, {}, headers={"Origin": origin})[0] == 404
    assert not (packs / "installed.json").exists()
