"""The demo database is seeded, labelled, and can only be written to demo.db."""

import pytest

from kitty.core import demo as demo_core
from kitty.core import jobs as jobs_core
from kitty.core import pipeline as pipeline_core
from kitty.db import connect
from kitty.settings import Settings


def test_reset_demo_refuses_any_other_database(workspace):
    settings = Settings.load(
        root=workspace.settings.root, db_path=workspace.settings.root / "data" / "apply.db"
    )
    with pytest.raises(ValueError, match="must be named demo.db"):
        demo_core.reset_demo(settings, workspace.config)


def test_demo_seed_has_jobs_across_tracks_and_a_full_pipeline(workspace):
    settings = Settings.load(root=workspace.settings.root, demo=True)
    stats = demo_core.reset_demo(settings, workspace.config)
    assert stats["jobs"] == len(demo_core._DEMO_JOBS)

    conn = connect(settings.db_path)
    try:
        for track in workspace.config.track_names:
            assert jobs_core.count_jobs(conn, jobs_core.JobFilter(tracks=[track])) > 0
        board = pipeline_core.board(conn)
        assert all(len(cards) > 0 for cards in board.values())
        # Everything fake is labelled, so demo rows can never be mistaken for real ones.
        assert all("(demo)" in job.company_name for job in jobs_core.list_jobs(conn))
        # And there is something left to triage, plus a proposal to confirm.
        assert jobs_core.count_jobs(conn, jobs_core.JobFilter(tiers=["A"], not_applied=True)) >= 1
    finally:
        conn.close()
