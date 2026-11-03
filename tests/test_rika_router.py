"""Rika's tier router (Q12): the smallest tier that can do the request, escalating on failure."""
import pytest

from kitty.core.rika import router


@pytest.mark.parametrize("text", [
    "hi rika", "what's on today?", "add a node Photosynthesis", "rename the topic Cells to Cell biology",
    "new card mitosis / cell division", "open calendar",
])
def test_talk_and_simple_edits_go_to_fast(text):
    assert router.route(text).tier == "fast"


@pytest.mark.parametrize("text", [
    "plan my week around the physics exam",
    "help me organize my notes for chemistry",
    "make a study schedule for finals",
    "break down the essay into steps",
])
def test_planning_goes_to_standard(text):
    assert router.route(text).tier == "standard"


@pytest.mark.parametrize("text", [
    "make a page for each lecture, then add cards for every definition, and then schedule reviews on the calendar",
    "add the reading to my notes; make five cards; book a study block; start an agent on the parser",
    "that didn't work, try again",
    "x " * 800,
])
def test_multi_step_or_retried_work_goes_to_deep(text):
    assert router.route(text).tier == "deep"


def test_a_failed_previous_reply_routes_to_deep():
    assert router.route("add a node Cells", failed_before=True).tier == "deep"


def test_override_wins_and_is_validated():
    assert router.route("plan my whole semester", override="fast").tier == "fast"
    assert router.route("hi", override="deep").reason == "override"
    with pytest.raises(ValueError):
        router.route("hi", override="huge")


def test_escalation_climbs_one_tier_and_stops_at_deep():
    assert router.escalate("fast") == "standard"
    assert router.escalate("standard") == "deep"
    assert router.escalate("deep") is None


def test_tier_models_default_per_provider_family_and_follow_settings(workspace):
    conn = workspace.conn
    conn.execute("UPDATE ai_settings SET model='anthropic/claude-sonnet-4.5' WHERE id=1")
    anthropic = router.models(conn)
    assert anthropic == router.DEFAULTS["anthropic"]
    conn.execute("UPDATE ai_settings SET model='openai/gpt-5' WHERE id=1")
    assert router.models(conn) == router.DEFAULTS["openai"]
    conn.execute("UPDATE ai_settings SET model='' WHERE id=1")
    assert router.models(conn) == router.DEFAULTS[""]
    conn.execute("UPDATE ai_settings SET rika_fast_model='google/gemini-2.5-flash-lite' WHERE id=1")
    assert router.models(conn)["fast"] == "google/gemini-2.5-flash-lite"
    assert router.configured(conn) == {"fast": "google/gemini-2.5-flash-lite", "standard": "", "deep": ""}
    for tiers in router.DEFAULTS.values():
        assert set(tiers) == set(router.TIERS) and all("/" in model for model in tiers.values())
