"""Desktop initialization respects the same module selection as CLI initialization."""
from pathlib import Path
import runpy

from kitty.modules import load_modules, save_modules

REPO_ROOT = Path(__file__).resolve().parents[1]
prepare_workspace = runpy.run_path(str(REPO_ROOT / "desktop" / "backend.py"))["prepare_workspace"]


def test_new_desktop_workspace_is_core_only(tmp_path):
    root = tmp_path / "fresh"
    prepare_workspace(root, REPO_ROOT / "config", "kitty")
    assert load_modules(root / "config") == ()
    assert not (root / "config" / "profile.yaml").exists()
    assert not (root / "resumes").exists()



def test_desktop_enabling_applyai_installs_missing_examples_without_overwriting(tmp_path):
    prepare_workspace(tmp_path, REPO_ROOT / "config", "kitty")
    save_modules(tmp_path / "config", ["applyai"])
    prepare_workspace(tmp_path, REPO_ROOT / "config", "kitty")
    profile = tmp_path / "config" / "profile.yaml"
    assert profile.is_file()
    profile.write_text("name: Synthetic Preserved Applicant\n")
    save_modules(tmp_path / "config", ["workspace"])
    prepare_workspace(tmp_path, REPO_ROOT / "config", "kitty")
    assert load_modules(tmp_path / "config") == ("workspace",)
    assert profile.read_text() == "name: Synthetic Preserved Applicant\n"
