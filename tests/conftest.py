"""Shared fixtures: a temporary workspace with a real schema and real config."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from kitty.config import load_config
from kitty.core import sources as sources_core
from kitty.core.workspace import Workspace, sync_config_to_db
from kitty.db import connect, migrate
from kitty.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    """A throwaway project root with the committed config and a fresh database."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in ("tracks.yaml", "sources.yaml", "profile.yaml", "answers.yaml", "watchlist.yaml", "views.yaml"):
        shutil.copy(REPO_ROOT / "config" / f"{name}.example", config_dir / name)

    # Synthetic policy for rule/LLM boundary tests, never the user's runtime config.
    tracks_path = config_dir / "tracks.yaml"
    tracks = yaml.safe_load(tracks_path.read_text(encoding="utf-8"))
    for track in tracks["tracks"]:
        track["weekly_target"] = 2
        if track["name"] == "parttime":
            track["location_rules"] = {
                "allow_remote": False, "countries": ["US"],
                "allow_cities": ["Hewitt", "Woodway", "Lancaster"],
                "near": {"city": "Hewitt", "state": "TX", "radius_miles": 10},
            }
            track["eligibility_rules"] = {"min_hourly_pay": 12}
        else:
            track["triage_depth"] = "llm"
            track["eligibility_rules"] = {
                "drop_if_requires": ["PhD", "master's degree", "junior standing", "senior standing"],
                "drop_if_grad_year_required": [2027, 2028],
                "boost_phrases": ["first-year", "freshman", "sophomore", "underclassmen", "explore"],
            }
    tracks_path.write_text(yaml.safe_dump(tracks, sort_keys=False), encoding="utf-8")

    settings = Settings.load(root=tmp_path, db_path=tmp_path / "data" / "test.db")
    config = load_config(config_dir)
    conn = connect(settings.db_path)
    migrate(conn)
    sync_config_to_db(conn, config)
    sources_core.ensure_source(conn, "manual", "manual")

    space = Workspace(settings=settings, config=config, conn=conn)
    yield space
    space.close()
