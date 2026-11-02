"""First-run setup: copy the .example config files and write profile.yaml.

`kitty init` in the CLI asks the questions; this module does the file work so
nothing interactive lives in the core library.
"""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from kitty.config import CONFIG_FILES
from kitty.modules import BUILD_FILE, LEGACY_MODULES_FILE, load_modules, normalize_modules, save_modules
from kitty.settings import Settings

#: profile.yaml fields `init` asks about, in the order it asks.
PROFILE_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("name", "Your full name"),
    ("email", "Your email"),
    ("graduation", "Graduation month (YYYY-MM)"),
    ("home_city", "City you live in"),
    ("home_state", "State (two letters)"),
)


@dataclass(frozen=True)
class CopyResult:
    """What `copy_examples` did to one file."""

    filename: str
    action: str          # created | kept | no_example
    path: Path


def initialize_modules(settings: Settings, enabled: Sequence[str] | None = None) -> tuple[str, ...]:
    """Settle the module selection (config/build.json) for `kitty init`; return it.

    An existing build file (or a pre-build modules.yaml) is validated and kept.
    Otherwise `enabled` is written when given, a brand-new workspace (no careers
    config yet) gets ``"modules": []`` and a pre-module workspace keeps every module.
    """
    if any(settings.config_path(name).exists() for name in (BUILD_FILE, LEGACY_MODULES_FILE)):
        return load_modules(settings.config_dir)
    if enabled is not None:
        return save_modules(settings.config_dir, normalize_modules(enabled))
    legacy = any(
        settings.config_path(name).exists() for name, _model, required, _example in CONFIG_FILES
        if required
    )
    return load_modules(settings.config_dir) if legacy else save_modules(settings.config_dir, ())


def copy_examples(settings: Settings, modules: Sequence[str] | None = None) -> list[CopyResult]:
    """Create any missing careers config file from its .example. Never overwrites.

    All runtime YAML files are local-only; only their generic .example templates
    are committed. An existing local file is always reported as 'kept'. Without
    the applyai module (`modules`, default: the build's selection) nothing is copied.
    """
    active = normalize_modules(modules) if modules is not None else load_modules(settings.config_dir)
    results: list[CopyResult] = []
    if "applyai" not in active:
        return results
    for filename, _model, _required, has_example in CONFIG_FILES:
        target = settings.config_path(filename)
        candidates = [settings.config_dir, Path(__file__).resolve().parents[3] / "config"]
        if hasattr(sys, "_MEIPASS"):
            candidates.insert(1, Path(sys._MEIPASS) / "examples")
        example = next((folder / f"{filename}.example" for folder in candidates
                        if (folder / f"{filename}.example").is_file()), None)

        if target.exists() or target.is_symlink():
            results.append(CopyResult(filename, "kept", target))
        elif has_example and example is not None:
            try:
                with target.open("x", encoding="utf-8") as output:
                    target.chmod(0o600)
                    output.write(example.read_text(encoding="utf-8"))
            except FileExistsError:
                results.append(CopyResult(filename, "kept", target))
                continue
            results.append(CopyResult(filename, "created", target))
        else:
            results.append(CopyResult(filename, "no_example", target))
    return results


def configure_modules(config_dir: Path, enabled: Sequence[str]) -> tuple[str, ...]:
    """Provision missing public defaults, then persist a restart-only selection."""
    selection = normalize_modules(enabled)
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(root=config_dir.parent, db_path=config_dir.parent / "data" / "apply.db", demo=False)
    copy_examples(settings, selection)
    return save_modules(config_dir, selection)


def _replace_scalar(text: str, key: str, value: str) -> str:
    """Replace a top-level 'key: value' line, keeping the file's comments."""
    pattern = re.compile(rf"^{re.escape(key)}:.*$", re.M)
    quoted = value if re.fullmatch(r"[\w .&'@/-]+", value) else f'"{value}"'
    replacement = f"{key}: {quoted}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.rstrip("\n") + f"\n{replacement}\n"


def write_profile(settings: Settings, answers: dict[str, str]) -> Path:
    """Write the answers from `init` into profile.yaml.

    Starts from profile.yaml (or its example) so every comment and every field I
    did not answer survives.
    """
    target = settings.config_path("profile.yaml")
    source = target if target.exists() else settings.config_path("profile.yaml.example")
    text = source.read_text(encoding="utf-8")

    for key, value in answers.items():
        if value.strip():
            text = _replace_scalar(text, key, value.strip())

    target.write_text(text, encoding="utf-8")
    return target


def ensure_env_file(settings: Settings) -> str:
    """Create .env from .env.example when it is missing.

    Returns 'created', 'kept' or 'no_example'. The file is gitignored and stays
    empty of values until I paste a key in.
    """
    target = settings.root / ".env"
    example = settings.root / ".env.example"
    if target.exists():
        return "kept"
    if example.exists():
        shutil.copy(example, target)
        return "created"
    return "no_example"
