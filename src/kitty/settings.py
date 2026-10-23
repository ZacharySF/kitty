"""Filesystem paths and environment settings.

Everything the rest of the package needs to find a file or a key goes through
`Settings.load()`, so tests can point it at a temporary directory.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

#: Markers that identify the project root when walking up from the cwd. modules.yaml
#: is the neutral one (a core-only workspace has no careers files); tracks.yaml is
#: kept so pre-module workspaces are still found.
ROOT_MARKERS = (Path("config") / "modules.yaml", Path("config") / "tracks.yaml")

#: Remote-control (WebDriver BiDi) port of the Firefox window the form filler drives.
#: Not Firefox's default 9222, so it never collides with another automated browser.
DEFAULT_BROWSER_PORT = 9333


def find_firefox() -> str | None:
    """The Firefox binary: $KITTY_FIREFOX, else firefox on PATH."""
    configured = os.environ.get("KITTY_FIREFOX", "").strip()
    if configured:
        return configured
    return shutil.which("firefox")


def find_project_root(start: Path | None = None) -> Path:
    """Return the project root: the nearest ancestor holding config/modules.yaml
    or, in a pre-module workspace, config/tracks.yaml.

    Falls back to the current directory so a fresh `kitty init` still works
    before any config exists.
    """
    env_home = os.environ.get("KITTY_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if any((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    return here


@dataclass(frozen=True)
class Settings:
    """Resolved paths and env-derived options for one process."""

    root: Path
    db_path: Path
    demo: bool
    anthropic_key_set: bool
    #: Browser the form filler starts (None when not installed).
    firefox_path: str | None = None
    #: Local remote-control port of that browser; the window stays open between runs.
    browser_port: int = DEFAULT_BROWSER_PORT

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def resumes_dir(self) -> Path:
        return self.root / "resumes"

    @property
    def cache_dir(self) -> Path:
        return self.root / ".cache"

    @property
    def browser_profile_dir(self) -> Path:
        """Firefox profile for the form filler (cookies, logins). Under data/, so gitignored.

        Separate from my everyday Firefox profile: while the filler's window runs,
        any local program could drive it, so it holds nothing but job-site logins.
        """
        return self.data_dir / "firefox"

    def config_path(self, name: str) -> Path:
        """Path to a config file by bare name, e.g. config_path('tracks.yaml')."""
        return self.config_dir / name

    @classmethod
    def load(
        cls,
        root: Path | None = None,
        *,
        demo: bool = False,
        db_path: Path | None = None,
    ) -> "Settings":
        """Build settings, reading .env from the project root if present.

        `demo=True` swaps in data/demo.db so experiments never touch real data.
        """
        project_root = (root or find_project_root()).resolve()
        load_dotenv(project_root / ".env")

        if db_path is not None:
            resolved_db = Path(db_path)
        elif demo:
            resolved_db = project_root / "data" / "demo.db"
        else:
            env_db = os.environ.get("KITTY_DB")
            resolved_db = Path(env_db) if env_db else project_root / "data" / "apply.db"
        if not resolved_db.is_absolute():
            resolved_db = project_root / resolved_db

        return cls(
            root=project_root,
            db_path=resolved_db,
            demo=demo,
            anthropic_key_set=bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            firefox_path=find_firefox(),
            browser_port=int(os.environ.get("KITTY_BROWSER_PORT") or DEFAULT_BROWSER_PORT),
        )
