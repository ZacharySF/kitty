"""Incomplete discovery stays visible with safe, actionable recovery guidance."""

import pytest

from applyai.core import sources
from applyai.core.doctor import check_sources
from applyai.core.today import TEXT_LIMIT, build_digest, digest_text


def _seed(conn):
    conn.execute("UPDATE sources SET enabled = 0")
    errors = {
        "workday_fixture": "36/97 boards incomplete: intern query truncated at 500 hits",
        "indeed_fixture": "220/224 searches failed: NameResolutionError for apis.indeed.com",
        "research_fixture": "NSF: robots.txt denied access",
    }
    for name, error in errors.items():
        sources.ensure_source(conn, name)
        sources.record_run(conn, name, status="partial", count=4, error=error)
    sources.ensure_source(conn, "healthy_fixture")
    sources.record_run(conn, "healthy_fixture", status="ok", count=5)
    return errors


def test_partial_coverage_is_in_today_and_doctor_without_writes(workspace):
    conn = workspace.conn
    errors = _seed(conn)
    writes = conn.total_changes
    digest = build_digest(conn, workspace.config)
    assert {s.name for s in digest.source_problems} == set(errors)
    text = digest_text(digest)  # This same payload is delivered by today --notify.
    assert all(name in text for name in errors)
    assert "220/224" in text and "36/97" in text
    checks = {c.name.removeprefix("source/"): c for c in check_sources(conn)}
    for name in errors:
        assert checks[name].warning
        assert checks[name].fix in text
    assert not checks["healthy_fixture"].warning
    assert conn.total_changes == writes


@pytest.mark.parametrize(("status", "detail", "categories"), [
    ("partial", "robots.txt denied", {"robots"}),
    ("partial", "220/224 NameResolutionError: failed to resolve apis.indeed.com", {"dns"}),
    ("partial", "36/97 boards: query truncated at 500", {"coverage"}),
    ("partial", "page cap 6 reached", {"coverage"}),
    ("partial", "HTTP 429 Retry-After: 60", {"cooldown"}),
    ("error", "authentication required: missing API_KEY", {"setup"}),
    ("error", "HTTP 503", {"retry"}),
    ("partial", "robots denied; DNS error; query cap reached; HTTP 429", {"robots", "dns", "coverage", "cooldown"}),
])
def test_recovery_distinguishes_restrictions_from_transient_failures(status, detail, categories):
    actions = sources.recovery_actions(status, detail)
    assert set(actions) == categories
    if "robots" in categories:
        assert "retry" not in actions["robots"].lower()
    if "dns" in categories:
        assert "resolver" in actions["dns"].lower()
        assert "direct source" in actions["dns"].lower()


def test_warning_redacts_credentials_before_truncation(workspace, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "known-secret-value")
    name = "secret_fixture"
    sources.ensure_source(workspace.conn, name)
    sources.record_run(workspace.conn, name, status="partial", error=(
        "220/224 failures known-secret-value "
        "https://user:password@provider.example/jobs?key=query-secret "
        "Authorization: Bearer bearer-secret api_key=inline-secret"
    ))
    source = next(s for s in sources.list_sources(workspace.conn) if s.name == name)
    warning = sources.source_warning(source, detail_limit=160)
    for secret in ("known-secret-value", "user:password", "query-secret", "bearer-secret", "inline-secret"):
        assert secret not in warning
    assert "220/224" in warning
    assert "provider.example" in sources.safe_error(source.last_error)


def test_notification_source_section_is_bounded(workspace):
    workspace.conn.execute("UPDATE sources SET enabled = 0")
    for index in range(TEXT_LIMIT + 3):
        name = f"bounded_{index:02}"
        sources.ensure_source(workspace.conn, name)
        sources.record_run(workspace.conn, name, status="partial", error="DNS failure " + "x" * 1000)
    text = digest_text(build_digest(workspace.conn, workspace.config))
    assert text.count("Action:") == TEXT_LIMIT
    assert "and 3 more" in text
    assert "x" * 161 not in text
    assert "resolver" in text


