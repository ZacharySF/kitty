"""Favorites and the "more like these" score in best-first order.

The score may reorder jobs only after eligibility, tier and watchlist priority,
and before place and age; with no favorites it must change nothing.
"""

from __future__ import annotations

from applyai.core import favorites, likeness
from applyai.core import jobs as jobs_core


def _job(workspace, company, title, *, jd="", posted="2026-09-01T00:00:00Z", location="Nowhere, ZZ",
         tier="B", eligibility="eligible"):
    """One job on the first track of config/tracks.yaml, with a tier and verdict."""
    track = workspace.config.track_names[0]
    job, _ = jobs_core.upsert_job(
        workspace.conn, company=company, title=title, url=f"https://example.invalid/{company}/{title}",
        location=location, jd_text=jd, posted_at=posted, tracks=[track],
    )
    jobs_core.set_tier(workspace.conn, job.id, tier, reason="test", tier_source="manual")
    jobs_core.set_eligibility(workspace.conn, job.id, eligibility)
    return job.id


def _best(workspace, **extra) -> list[int]:
    config = workspace.config
    return [job.id for job in jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(
        order="best", prefer_cities=config.prefer_cities, prefer_remote=config.profile.open_to_remote,
        **extra,
    ))]


FPGA_JD = "Design FPGA logic in Verilog, close timing, low latency market data pipelines."
SALES_JD = "Support account executives, update the CRM, prepare customer slide decks."


def _fpga_and_sales(workspace):
    """A favorite FPGA job, an older FPGA look-alike, and a newer, nearer sales job."""
    city = (workspace.config.prefer_cities or ["Austin"])[0]
    liked = _job(workspace, "Liked Co", "FPGA Engineer Intern", jd=FPGA_JD, posted="2026-08-01T00:00:00Z")
    similar = _job(workspace, "Similar Co", "FPGA Design Intern", jd=FPGA_JD, posted="2026-08-02T00:00:00Z")
    other = _job(workspace, "Other Co", "Sales Intern", jd=SALES_JD, posted="2026-09-20T00:00:00Z",
                 location=f"{city}, TX")
    return liked, similar, other


def test_no_favorites_leaves_best_order_and_scores_untouched(workspace):
    _fpga_and_sales(workspace)
    before = _best(workspace)
    assert likeness.rescore(workspace.conn) == 0
    assert _best(workspace) == before
    scores = workspace.conn.execute("SELECT like_score FROM jobs").fetchall()
    assert all(row["like_score"] is None for row in scores)


def test_look_alikes_of_a_favorite_rank_above_newer_nearer_jobs_in_the_same_tier(workspace):
    liked, similar, other = _fpga_and_sales(workspace)
    assert _best(workspace).index(other) < _best(workspace).index(similar)   # newer and nearer wins

    favorites.favorite(workspace.conn, [liked])
    order = _best(workspace)
    assert order.index(similar) < order.index(other)
    detail = jobs_core.get_job(workspace.conn, similar)
    assert detail.like_score > 0 and "fpga" in detail.like_terms


def test_like_score_never_beats_tier_or_eligibility(workspace):
    liked, similar, _ = _fpga_and_sales(workspace)
    tier_a_stranger = _job(workspace, "Tier A Co", "Sales Intern", jd=SALES_JD, tier="A")
    boosted_stranger = _job(workspace, "Boosted Co", "Sales Intern", jd=SALES_JD, eligibility="boosted")
    jobs_core.set_eligibility(workspace.conn, similar, "ambiguous")
    plain_stranger = _job(workspace, "Plain Co", "Sales Intern", jd=SALES_JD)

    favorites.favorite(workspace.conn, [liked])
    order = _best(workspace)
    assert order.index(boosted_stranger) < order.index(similar)      # eligibility first
    assert order.index(plain_stranger) < order.index(similar)        # eligible beats ambiguous
    assert order.index(tier_a_stranger) < order.index(liked)         # then tier: A beats a B favorite


def test_skipping_a_look_alike_lowers_the_score_of_jobs_like_it(workspace):
    liked, similar, _ = _fpga_and_sales(workspace)
    favorites.favorite(workspace.conn, [liked])
    before = jobs_core.get_job(workspace.conn, similar).like_score
    disliked = _job(workspace, "Disliked Co", "FPGA Verification Intern", jd=FPGA_JD)
    favorites.skip(workspace.conn, [disliked])
    assert jobs_core.get_job(workspace.conn, similar).like_score < before
    assert jobs_core.get_job(workspace.conn, disliked).queue_state == "skipped"


def test_favorites_filter_and_unfavorite(workspace):
    liked, similar, other = _fpga_and_sales(workspace)
    assert favorites.favorite(workspace.conn, [liked, other, liked, 999_999]) == [liked, other]
    assert favorites.favorite(workspace.conn, [liked]) == []                  # already a favorite
    assert set(_best(workspace, favorites_only=True)) == {liked, other}
    assert jobs_core.count_jobs(workspace.conn, jobs_core.JobFilter(favorites_only=True)) == 2

    assert favorites.unfavorite(workspace.conn, [other, similar]) == [other]
    assert [job.id for job in favorites.list_favorites(workspace.conn, workspace.config)] == [liked]
    assert jobs_core.get_job(workspace.conn, other).queue_state == "new"     # starring never moved it

    favorites.unfavorite(workspace.conn, [liked])
    assert jobs_core.get_job(workspace.conn, similar).like_score is None     # back to no effect
