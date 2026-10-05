"""First-run setup: copy the .example config files and write profile.yaml.

`applyai init` in the CLI asks the questions; this module does the file work so
nothing interactive lives in the core library.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from applyai.config import CONFIG_FILES
from applyai.settings import Settings

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


def copy_examples(settings: Settings) -> list[CopyResult]:
    """Create any missing config file from its .example. Never overwrites.

    All runtime YAML files are local-only; only their generic .example templates
    are committed. An existing local file is always reported as 'kept'.
    """
    results: list[CopyResult] = []
    for filename, _model, _required, has_example in CONFIG_FILES:
        target = settings.config_path(filename)
        example = settings.config_path(f"{filename}.example")

        if target.exists():
            results.append(CopyResult(filename, "kept", target))
        elif has_example and example.exists():
            shutil.copy(example, target)
            results.append(CopyResult(filename, "created", target))
        else:
            results.append(CopyResult(filename, "no_example", target))
    return results


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
