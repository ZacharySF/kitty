"""External modules core (docs/MODULES.md): manifest, checksums, safe extraction, registry.

Network is always faked: unit tests answer through httpx.MockTransport, and the
module tarballs are synthetic, built here.
"""
from __future__ import annotations

import tarfile

import httpx
import pytest
from external_module_kit import manifest, process_files, python_files, sha256, tarball

from kitty.core.external_modules import archive, github, installer, registry
from kitty.core.external_modules.manifest import ManifestError, parse_manifest
from kitty.db import migrate
from kitty.db.migrations import pending_migrations

# --- manifest ---------------------------------------------------------------


def test_manifest_accepts_both_kinds_with_defaults():
    python = parse_manifest(manifest())
    assert (python.id, python.version, python.kind, python.python_package) == ("rika-synth", "0.1.0", "python", "rika_synth")
    assert python.view is not None and python.view.ui == "element" and python.view.first is True
    assert python.view.script == "ui/view.js" and python.view.tag == "rika-synth-view"
    process = parse_manifest(manifest("rika-learn", kind="process"))
    assert process.process is not None and process.process.command == ("python3", "server.py")
    assert process.process.env == {"SYNTH_DATA": "{data}", "PORT": "{port}"}
    assert process.view is not None and process.view.ui == "iframe" and process.view.group == "study"
    bare = manifest("rika-bare", kind="process")
    bare["process"] = {"command": ["node", "server.js"]}
    del bare["view"]
    parsed = parse_manifest(bare)
    assert (parsed.process.cwd, parsed.process.health, parsed.process.ready_timeout_s) == (".", "/", 30)
    assert parsed.view is None
    assert parse_manifest(manifest(), expect_id="rika-synth", expect_version="0.1.0").id == "rika-synth"


def _broken(change):
    data = manifest()
    change(data)
    return data


@pytest.mark.parametrize("data,fragment", [
    (_broken(lambda d: d.update(api=2)), "api"),
    (_broken(lambda d: d.update(api=True)), "api"),
    (_broken(lambda d: d.update(id="Rika_Synth")), "id"),
    (_broken(lambda d: d.update(id="workspace")), "built-in"),
    (_broken(lambda d: d.update(version="1.0")), "version"),
    (_broken(lambda d: d.update(kind="binary")), "kind"),
    (_broken(lambda d: d.pop("python")), "python"),
    (_broken(lambda d: d.update(python={"package": "../evil"})), "package"),
    (_broken(lambda d: d.update(process={"command": ["node"]})), "process"),
    (_broken(lambda d: d.update(surprise=True)), "surprise"),
    (_broken(lambda d: d["view"].update(group="games")), "group"),
    (_broken(lambda d: d["view"].update(ui="iframe")), "element"),
    (_broken(lambda d: d["view"]["element"].update(script="../escape.js")), "script"),
    (_broken(lambda d: d["view"]["element"].update(tag="nodash")), "tag"),
    (_broken(lambda d: d["view"].update(first="yes")), "first"),
    ([], "object"),
])
def test_manifest_rejects_with_a_plain_located_reason(data, fragment):
    with pytest.raises(ManifestError) as error:
        parse_manifest(data)
    assert fragment in str(error.value)


@pytest.mark.parametrize("change,fragment", [
    (lambda d: d["process"].update(command=[]), "command"),
    (lambda d: d["process"].update(env={"lower": "x"}), "env"),
    (lambda d: d["process"].update(env={"X": "{home}"}), "{home}"),
    (lambda d: d["process"].update(cwd="../up"), "cwd"),
    (lambda d: d["process"].update(health="health"), "health"),
    (lambda d: d["process"].update(ready_timeout_s=0), "ready_timeout_s"),
    (lambda d: d["view"].update(ui="element", element={"script": "ui/x.js", "tag": "x-y"}), "iframe"),
])
def test_process_manifest_rejections(change, fragment):
    data = manifest("rika-learn", kind="process")
    change(data)
    with pytest.raises(ManifestError) as error:
        parse_manifest(data)
    assert fragment in str(error.value)


def test_manifest_must_match_the_release_it_came_from():
    with pytest.raises(ManifestError, match="rika-other"):
        parse_manifest(manifest(), expect_id="rika-other")
    with pytest.raises(ManifestError, match="0.2.0"):
        parse_manifest(manifest(), expect_version="0.2.0")


# --- safe extraction ---------------------------------------------------------


def _member(name, kind=tarfile.REGTYPE, *, link="", data=b"x"):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = link
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, data if kind == tarfile.REGTYPE else None


@pytest.mark.parametrize("member", [
    _member("../escape.txt"),
    _member("/abs/escape.txt"),
    _member("ui/../../escape.txt"),
    _member("link", tarfile.SYMTYPE, link="/etc/passwd"),
    _member("ui/link", tarfile.SYMTYPE, link="../../outside"),
    _member("hard", tarfile.LNKTYPE, link="../outside"),
    _member("device", tarfile.CHRTYPE),
    _member("fifo", tarfile.FIFOTYPE),
])
def test_extraction_refuses_anything_that_could_escape(tmp_path, member):
    path = tmp_path / "module.tar.gz"
    path.write_bytes(tarball(python_files(), extra=[member]))
    target = tmp_path / "out"
    with pytest.raises(archive.ArchiveError):
        archive.extract(path, target)
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "outside").exists()
    assert not target.exists()


def test_extraction_keeps_regular_files_and_internal_links_without_special_modes(tmp_path):
    files = python_files()
    info, _ = _member("ui/alias.js", tarfile.SYMTYPE, link="view.js")
    setuid = tarfile.TarInfo("bin/tool")
    setuid.size, setuid.mode = 2, 0o4755
    path = tmp_path / "module.tar.gz"
    path.write_bytes(tarball(files, extra=[(info, None), (setuid, b"ok")]))
    archive.extract(path, tmp_path / "out")
    assert (tmp_path / "out/rika-module.json").read_bytes() == files["rika-module.json"]
    assert (tmp_path / "out/ui/alias.js").read_bytes() == files["ui/view.js"]
    assert not (tmp_path / "out/bin/tool").stat().st_mode & 0o4000


def test_extraction_refuses_oversized_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "MAX_TOTAL_BYTES", 10)
    path = tmp_path / "module.tar.gz"
    path.write_bytes(tarball(python_files()))
    with pytest.raises(archive.ArchiveError, match="large"):
        archive.extract(path, tmp_path / "out")


def test_non_gzip_bytes_are_a_plain_archive_error(tmp_path):
    path = tmp_path / "module.tar.gz"
    path.write_bytes(b"not a tarball")
    with pytest.raises(archive.ArchiveError):
        archive.extract(path, tmp_path / "out")


# --- GitHub release fetch with checksums ------------------------------------


class Releases:
    """httpx.MockTransport handler: one fake GitHub with a redirecting asset API."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.release: dict | None = None
        self.blobs: dict[str, bytes] = {}

    def publish(self, module_id, version, data, *, digest="auto", checksum=None, extra_assets=()):
        name = f"{module_id}-{version}.tar.gz"
        asset = {"name": name, "url": f"https://api.github.com/repos/o/r/releases/assets/{len(self.blobs) + 1}",
                 "size": len(data)}
        self.blobs[asset["url"].rsplit("/", 1)[1]] = data
        if digest == "auto":
            asset["digest"] = f"sha256:{sha256(data)}"
        elif digest is not None:
            asset["digest"] = digest
        assets = [asset, *extra_assets]
        if checksum is not None:
            number = str(len(self.blobs) + 1)
            self.blobs[number] = checksum.encode()
            assets.append({"name": name + ".sha256", "size": len(checksum),
                           "url": f"https://api.github.com/repos/o/r/releases/assets/{number}"})
        self.release = {"tag_name": f"v{version}", "assets": assets}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.github.com" and request.url.path.endswith("/releases/latest"):
            if self.release is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=self.release)
        if request.url.host == "api.github.com" and "/releases/assets/" in request.url.path:
            number = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(302, headers={"Location": f"https://objects.example.invalid/blob/{number}?sig=synthetic"})
        if request.url.host == "objects.example.invalid":
            return httpx.Response(200, content=self.blobs[request.url.path.rsplit("/", 1)[1]])
        return httpx.Response(404)


@pytest.fixture
def releases(monkeypatch):
    fake = Releases()
    monkeypatch.delenv("KITTY_GITHUB_API", raising=False)
    monkeypatch.setattr(github, "client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def test_repository_input_is_owner_slash_repo_or_a_github_url():
    assert github.parse_repo("ZacharySF/rika-learn") == "ZacharySF/rika-learn"
    assert github.parse_repo(" https://github.com/ZacharySF/rika-learn.git ") == "ZacharySF/rika-learn"
    assert github.parse_repo("https://github.com/ZacharySF/rika-learn/") == "ZacharySF/rika-learn"
    for bad in ("", "rika-learn", "a/b/c", "../x", "owner/..", "https://example.invalid/o/r", "o/r?x=1", "-o/r"):
        with pytest.raises(github.GitHubError):
            github.parse_repo(bad)


def test_install_verifies_the_digest_pins_the_version_and_sends_the_token_only_to_github(workspace, releases):
    data = tarball(python_files())
    releases.publish("rika-synth", "0.1.0", data)
    record = installer.install(workspace.settings, workspace.conn, "o/r", token="synthetic-github-token")
    assert (record.id, record.repo, record.version, record.sha256) == ("rika-synth", "o/r", "0.1.0", sha256(data))
    directory = registry.install_dir(workspace.settings, "rika-synth", "0.1.0")
    assert (directory / "rika-module.json").is_file() and (directory / "rika_synth/__init__.py").is_file()
    [stored] = registry.installed(workspace.conn)
    assert stored.id == "rika-synth" and stored.sha256 == sha256(data) and stored.installed_at
    assert stored.manifest.view.label == "Synthetic"
    for request in releases.requests:
        auth = request.headers.get("Authorization")
        assert (auth == "Bearer synthetic-github-token") == (request.url.host == "api.github.com"), request.url
    assert releases.requests[-1].url.host == "objects.example.invalid"
    assert not any(path.name.startswith(".staging") for path in registry.modules_root(workspace.settings).iterdir())


@pytest.mark.parametrize("digest,checksum,fragment", [
    ("sha256:" + "0" * 64, None, "checksum"),
    (None, "0" * 64 + "  rika-synth-0.1.0.tar.gz\n", "checksum"),
    ("auto", "1" * 64, "checksum"),
    (None, None, "no SHA-256"),
    ("md5:abc", None, "no SHA-256"),
])
def test_checksum_mismatch_or_absence_refuses_and_leaves_nothing_behind(workspace, releases, digest, checksum, fragment):
    releases.publish("rika-synth", "0.1.0", tarball(python_files()), digest=digest, checksum=checksum)
    with pytest.raises(installer.InstallError, match=fragment):
        installer.install(workspace.settings, workspace.conn, "o/r", token="")
    assert registry.installed(workspace.conn) == []
    root = registry.modules_root(workspace.settings)
    assert not root.exists() or list(root.iterdir()) == []


def test_a_matching_checksum_file_alone_is_enough(workspace, releases):
    data = tarball(python_files())
    releases.publish("rika-synth", "0.1.0", data, digest=None, checksum=f"{sha256(data)}  rika-synth-0.1.0.tar.gz\n")
    assert installer.install(workspace.settings, workspace.conn, "o/r", token="").sha256 == sha256(data)


@pytest.mark.parametrize("files,fragment", [
    (python_files(version="0.2.0"), "0.2.0"),
    (python_files(module_id="rika-other"), "rika-other"),
    ({k: v for k, v in python_files().items() if k != "rika_synth/__init__.py"}, "rika_synth"),
    ({k: v for k, v in python_files().items() if k != "ui/view.js"}, "ui/view.js"),
    ({k: v for k, v in python_files().items() if k != "rika-module.json"}, "rika-module.json"),
    ({**python_files(), "rika-module.json": b"{not json"}, "rika-module.json"),
])
def test_release_contents_must_match_the_release_and_manifest(workspace, releases, files, fragment):
    releases.publish("rika-synth", "0.1.0", tarball(files))
    with pytest.raises(installer.InstallError, match=fragment):
        installer.install(workspace.settings, workspace.conn, "o/r", token="")
    assert registry.installed(workspace.conn) == []


def test_github_failures_read_as_plain_next_steps(workspace, releases):
    with pytest.raises(installer.InstallError, match="private.*Settings → Keys"):
        installer.install(workspace.settings, workspace.conn, "o/r", token="")
    releases.release = {"tag_name": "v0.1.0", "assets": []}
    with pytest.raises(installer.InstallError, match=r"rika-module\.tar\.gz|<id>-0\.1\.0\.tar\.gz"):
        installer.install(workspace.settings, workspace.conn, "o/r", token="")
    releases.release = {"tag_name": "nightly", "assets": []}
    with pytest.raises(installer.InstallError, match="version"):
        installer.install(workspace.settings, workspace.conn, "o/r", token="")


def test_reinstalling_the_same_version_keeps_the_pin_and_a_changed_release_is_refused(workspace, releases):
    data = tarball(python_files())
    releases.publish("rika-synth", "0.1.0", data)
    first = installer.install(workspace.settings, workspace.conn, "o/r", token="")
    assert installer.install(workspace.settings, workspace.conn, "o/r", token="").installed_at == first.installed_at
    releases.publish("rika-synth", "0.1.0", tarball({**python_files(), "extra.txt": b"changed"}))
    with pytest.raises(installer.InstallError, match="changed"):
        installer.install(workspace.settings, workspace.conn, "o/r", token="")
    assert registry.installed(workspace.conn)[0].sha256 == sha256(data)


def test_one_id_belongs_to_one_repository(workspace, releases, monkeypatch):
    releases.publish("rika-synth", "0.1.0", tarball(python_files()))
    installer.install(workspace.settings, workspace.conn, "o/r", token="")
    monkeypatch.setattr(github, "parse_repo", lambda value: "someone/else")
    with pytest.raises(installer.InstallError, match="already installed from o/r"):
        installer.install(workspace.settings, workspace.conn, "o/r", token="")


def test_update_installs_the_newer_release_and_checks_report_it(workspace, releases):
    releases.publish("rika-synth", "0.1.0", tarball(python_files()))
    installer.install(workspace.settings, workspace.conn, "o/r", token="")
    assert installer.check_updates(workspace.conn, token="") == ({}, {})
    releases.publish("rika-synth", "0.1.1", tarball(python_files(version="0.1.1")))
    assert installer.check_updates(workspace.conn, token="") == ({"rika-synth": "0.1.1"}, {})
    record = installer.install(workspace.settings, workspace.conn, "o/r", token="", expect_id="rika-synth")
    assert record.version == "0.1.1"
    assert [item.version for item in registry.installed(workspace.conn)] == ["0.1.1"]
    # The previous version stays on disk for the process still running it; start-up prunes it.
    assert registry.install_dir(workspace.settings, "rika-synth", "0.1.0").is_dir()
    registry.prune(workspace.settings, registry.installed(workspace.conn))
    assert not registry.install_dir(workspace.settings, "rika-synth", "0.1.0").exists()
    assert registry.install_dir(workspace.settings, "rika-synth", "0.1.1").is_dir()


def test_updates_compare_versions_not_strings():
    assert installer.newer("0.10.0", "0.9.9") and installer.newer("1.0.0", "1.0.0-rc.1")
    assert not installer.newer("0.1.0", "0.1.0") and not installer.newer("0.1.0-rc.1", "0.1.0")


def test_remove_forgets_the_pin_and_prune_keeps_module_data(workspace, releases):
    releases.publish("rika-synth", "0.1.0", tarball(python_files()))
    installer.install(workspace.settings, workspace.conn, "o/r", token="")
    data = registry.data_dir(workspace.settings, "rika-synth")
    data.mkdir(parents=True)
    (data / "progress.json").write_text("{}")
    registry.remove(workspace.conn, "rika-synth")
    assert registry.installed(workspace.conn) == []
    registry.prune(workspace.settings, [])
    assert not (registry.modules_root(workspace.settings) / "rika-synth").exists()
    assert (data / "progress.json").is_file()


def test_process_module_installs_with_its_command_checked(workspace, releases):
    releases.publish("rika-proc", "0.1.0", tarball(process_files(command=["./missing.sh"])))
    with pytest.raises(installer.InstallError, match="missing.sh"):
        installer.install(workspace.settings, workspace.conn, "o/r", token="")
    releases.publish("rika-proc", "0.1.0", tarball(process_files()))
    assert installer.install(workspace.settings, workspace.conn, "o/r", token="").manifest.kind == "process"


# --- per-module migrations ----------------------------------------------------


def test_module_migrations_use_their_own_version_namespace(workspace, tmp_path):
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "001_counter.sql").write_text("CREATE TABLE synth_counter (id INTEGER PRIMARY KEY);\n")
    applied = migrate(workspace.conn, folder, namespace="rika-synth")
    assert [m.version for m in applied] == [1]
    assert migrate(workspace.conn, folder, namespace="rika-synth") == []
    assert pending_migrations(workspace.conn, folder, namespace="rika-other")[0].version == 1
    rows = workspace.conn.execute("SELECT module, version, name FROM module_schema_version").fetchall()
    assert [tuple(row) for row in rows] == [("rika-synth", 1, "counter")]
    # The core schema's own version table is untouched by a module.
    assert workspace.conn.execute("SELECT COUNT(*) FROM schema_version WHERE version = 1 AND name = 'counter'").fetchone()[0] == 0
