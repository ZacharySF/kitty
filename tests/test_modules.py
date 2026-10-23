"""Module registry, strict modules.yaml, module-aware config loading and initialization.

All workspaces are synthetic temporary directories built from the public examples.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from kitty.config import ConfigError, load_config
from kitty.core import bootstrap, doctor as doctor_core
from kitty.core.workspace import open_workspace, sync_config_to_db
from kitty.modules import (
    MODULE_IDS, ModuleDisabledError, available_modules, load_modules, modules_state,
    normalize_modules, require_module, save_modules,
)
from kitty.settings import Settings, find_project_root

REPO_ROOT = Path(__file__).resolve().parents[1]
CAREERS = ("profile", "tracks", "sources", "watchlist", "answers", "views")


def write(config_dir: Path, text: str) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "modules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def settings_for(root: Path) -> Settings:
    return Settings.load(root=root, db_path=root / "data" / "modules.db")


def copy_careers(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in CAREERS:
        shutil.copy(REPO_ROOT / "config" / f"{name}.yaml.example", config_dir / f"{name}.yaml")


def test_registry_constants_and_metadata():
    assert MODULE_IDS == ("applyai", "workspace")
    assert [item["id"] for item in available_modules()] == list(MODULE_IDS)
    assert all(item["label"] and item["description"] for item in available_modules())


def test_absent_file_means_every_module_and_selection_is_canonical(tmp_path):
    config_dir = tmp_path / "config"
    assert load_modules(config_dir) == MODULE_IDS
    write(config_dir, "enabled: [workspace, applyai]\n")
    assert load_modules(config_dir) == ("applyai", "workspace")
    write(config_dir, "enabled: []\n")
    assert load_modules(config_dir) == ()
    write(config_dir, "enabled:\n  - workspace\n")
    assert load_modules(config_dir) == ("workspace",)


@pytest.mark.parametrize("text, fragment", [
    ("enabled: [applyai, careers]\n", "unknown module"),
    ("enabled: [applyai, applyai]\n", "duplicate module"),
    ("enabled: [applyai]\nextra: true\n", "unknown key"),
    ("enabled: [applyai]\nenabled: [workspace]\n", "duplicate key"),
    ("enabled: applyai\n", "list"),
    ("enabled: [1]\n", "string"),
    ("enabled:\n", "list"),
    ("", "required"),
    ("- applyai\n", ""),
    ("enabled: [applyai\n", "YAML syntax"),
])
def test_modules_yaml_is_strict(tmp_path, text, fragment):
    write(tmp_path / "config", text)
    with pytest.raises(ConfigError) as caught:
        load_modules(tmp_path / "config")
    assert fragment in str(caught.value)
    assert "modules.yaml" in str(caught.value)


def test_require_module_raises_a_readable_error_when_disabled(tmp_path):
    config_dir = tmp_path / "config"
    write(config_dir, "enabled: [workspace]\n")
    require_module(config_dir, "workspace")
    with pytest.raises(ModuleDisabledError) as caught:
        require_module(config_dir, "applyai")
    assert caught.value.module == "applyai"
    assert "disabled" in str(caught.value) and "restart" in str(caught.value)
    with pytest.raises(ValueError):
        require_module(config_dir, "careers")


def test_normalize_rejects_non_ids_and_duplicates():
    assert normalize_modules(["workspace", "applyai"]) == MODULE_IDS
    for bad in (["Workspace"], ["applyai", "applyai"], [None], [["applyai"]], ["applyai/../x"]):
        with pytest.raises(ValueError):
            normalize_modules(bad)


def test_save_modules_is_atomic_validated_and_never_widens_permissions(tmp_path):
    config_dir = tmp_path / "config"
    assert save_modules(config_dir, ["workspace"]) == ("workspace",)
    target = config_dir / "modules.yaml"
    assert load_modules(config_dir) == ("workspace",)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    target.chmod(0o640)
    assert save_modules(config_dir, ["workspace", "applyai"]) == MODULE_IDS
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert save_modules(config_dir, []) == ()
    assert load_modules(config_dir) == ()

    before = target.read_text(encoding="utf-8")
    for bad in (["nope"], ["applyai", "applyai"], "applyai", [1]):
        with pytest.raises(ValueError):
            save_modules(config_dir, bad)  # type: ignore[arg-type]
    assert target.read_text(encoding="utf-8") == before
    assert [p.name for p in config_dir.iterdir()] == ["modules.yaml"]


def test_save_modules_refuses_symlinks(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("keep", encoding="utf-8")
    os.symlink(outside, config_dir / "modules.yaml")
    with pytest.raises(ValueError):
        save_modules(config_dir, ["workspace"])
    assert outside.read_text(encoding="utf-8") == "keep"


def test_modules_state_separates_active_from_pending(tmp_path):
    config_dir = tmp_path / "config"
    write(config_dir, "enabled: [workspace]\n")
    state = modules_state(MODULE_IDS, config_dir)
    assert state["enabled"] == ["applyai", "workspace"]
    assert state["configured"] == ["workspace"]
    assert state["restart_required"] is True
    assert [item["id"] for item in state["available"]] == list(MODULE_IDS)
    assert modules_state(("workspace",), config_dir)["restart_required"] is False
    write(config_dir, "enabled: [bogus]\n")
    broken = modules_state(("workspace",), config_dir)
    assert broken["configured"] == ["workspace"] and broken["restart_required"] is False
    assert "bogus" in broken["error"]


def test_core_only_and_workspace_only_load_without_any_careers_yaml(tmp_path):
    config_dir = tmp_path / "config"
    for selection in ((), ("workspace",)):
        config = load_config(config_dir, modules=selection)
        assert config.modules == selection and not config.enabled("applyai")
        assert config.tracks.tracks == [] and config.track_names == []
    write(config_dir, "enabled: [workspace]\n")
    assert load_config(config_dir).modules == ("workspace",)


def test_disabled_careers_config_is_neither_read_nor_validated(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in ("profile", "tracks", "sources", "answers", "triage", "apply"):
        (config_dir / f"{name}.yaml").write_text("{{{ not yaml: [", encoding="utf-8")
    write(config_dir, "enabled: [workspace]\n")
    assert load_config(config_dir).modules == ("workspace",)
    with pytest.raises(ConfigError):
        load_config(config_dir, modules=("applyai",))


def test_applyai_still_requires_its_files_and_both_enabled_matches_legacy(tmp_path):
    config_dir = tmp_path / "config"
    with pytest.raises(ConfigError):
        load_config(config_dir)  # No modules.yaml: legacy default enables applyai.
    copy_careers(config_dir)
    legacy = load_config(config_dir)
    assert legacy.modules == MODULE_IDS and legacy.track_names
    write(config_dir, "enabled: [applyai, workspace]\n")
    assert load_config(config_dir).track_names == legacy.track_names


def test_explicit_snapshot_ignores_a_pending_file(tmp_path):
    config_dir = tmp_path / "config"
    copy_careers(config_dir)
    write(config_dir, "enabled: []\n")
    assert load_config(config_dir).modules == ()
    frozen = load_config(config_dir, modules=MODULE_IDS)
    assert frozen.modules == MODULE_IDS and frozen.track_names
    # A pending enable of applyai with its files missing must not break a frozen load.
    (config_dir / "tracks.yaml").unlink()
    write(config_dir, "enabled: [applyai]\n")
    assert load_config(config_dir, modules=()).modules == ()
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_workspace_only_open_skips_sync_and_keeps_existing_rows(workspace):
    before = workspace.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    assert before > 0
    with open_workspace(workspace.settings, modules=("workspace",)) as opened:
        assert opened.config.modules == ("workspace",)
        assert sync_config_to_db(opened.conn, opened.config) == {}
        assert opened.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == before
    with open_workspace(workspace.settings, modules=MODULE_IDS) as opened:
        assert opened.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == before


def test_core_only_workspace_opens_with_no_careers_files(tmp_path):
    root = tmp_path / "root"
    write(root / "config", "enabled: []\n")
    with open_workspace(settings_for(root)) as opened:
        assert opened.config.modules == ()
        assert opened.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0


def test_project_root_marker_is_neutral_with_legacy_support(tmp_path, monkeypatch):
    monkeypatch.delenv("KITTY_HOME", raising=False)
    neutral = tmp_path / "neutral"
    (neutral / "config").mkdir(parents=True)
    (neutral / "config" / "modules.yaml").write_text("enabled: []\n", encoding="utf-8")
    (neutral / "a" / "b").mkdir(parents=True)
    assert find_project_root(neutral / "a" / "b") == neutral.resolve()
    legacy = tmp_path / "legacy"
    (legacy / "config").mkdir(parents=True)
    (legacy / "config" / "tracks.yaml").write_text("tracks: []\n", encoding="utf-8")
    (legacy / "x").mkdir()
    assert find_project_root(legacy / "x") == legacy.resolve()


def example_workspace(root: Path) -> Settings:
    (root / "config").mkdir(parents=True)
    for example in (REPO_ROOT / "config").glob("*.yaml.example"):
        shutil.copy(example, root / "config" / example.name)
    return settings_for(root)


def test_new_init_is_core_only_and_copies_no_careers_examples(tmp_path):
    settings = example_workspace(tmp_path)
    assert bootstrap.initialize_modules(settings) == ()
    assert (settings.config_dir / "modules.yaml").read_text(encoding="utf-8").strip() == "enabled: []"
    assert bootstrap.copy_examples(settings) == []
    assert not (settings.config_dir / "tracks.yaml").exists()
    assert bootstrap.initialize_modules(settings) == ()  # An existing file is kept.


def test_init_with_applyai_copies_careers_examples(tmp_path):
    settings = example_workspace(tmp_path)
    assert bootstrap.initialize_modules(settings, ["workspace", "applyai"]) == MODULE_IDS
    created = {result.filename for result in bootstrap.copy_examples(settings) if result.action == "created"}
    assert {"profile.yaml", "tracks.yaml", "sources.yaml"} <= created
    assert bootstrap.copy_examples(settings, modules=()) == []


def test_init_in_a_legacy_workspace_leaves_the_file_absent(tmp_path):
    settings = example_workspace(tmp_path)
    shutil.copy(settings.config_path("profile.yaml.example"), settings.config_path("profile.yaml"))
    assert bootstrap.initialize_modules(settings) == MODULE_IDS
    assert not (settings.config_dir / "modules.yaml").exists()
    assert {r.action for r in bootstrap.copy_examples(settings)} == {"created", "kept"}


def test_doctor_is_module_aware(tmp_path):
    root = tmp_path / "root"
    write(root / "config", "enabled: [workspace]\n")
    settings = settings_for(root)
    open_workspace(settings).close()
    checks = doctor_core.run_all(settings)
    assert doctor_core.failures(checks) == []
    names = {check.name for check in checks}
    assert "config/modules.yaml" in names and "database" in names
    assert not any(name.startswith(("config/profile", "config/tracks", "resume", "source/", "env/")) for name in names)

    write(root / "config", "enabled: [bogus]\n")
    broken = doctor_core.failures(doctor_core.run_all(settings))
    assert [check.name for check in broken] == ["config/modules.yaml"]


def test_doctor_legacy_workspace_still_checks_careers(workspace):
    checks = doctor_core.run_all(workspace.settings, workspace.conn)
    assert doctor_core.failures(checks) == []
    names = {check.name for check in checks}
    assert "config/tracks.yaml" in names and "config/modules.yaml" in names
