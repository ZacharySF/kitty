"""GET /api/bootstrap, answered by rika-server (server/src/bootstrap.rs).

The answer is compared with what the Python functions it replaces compute from the same
synthetic workspace: ``kitty.modules.modules_state``, ``tracks.weekly_targets``,
``jobs.count_jobs`` with the job views, and ``today.build_digest`` / ``today.digest_text``
(the former ``WebApplication._bootstrap``). The digest data covers every section, stored
timestamps in the forms ``kitty.dates.parse`` reads, and source errors with secrets to
redact; ``rika-server bootstrap-digest`` runs the same computation on a database directly.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from kitty.core import jobs, today, tracks
from kitty.core.followups import mark_applied
from kitty.modules import DEFAULT_MODULES, modules_state
from terminal_support import RIKA_SERVER, front_site

SECRET = "synthetic-secret-value-1234"


@pytest.fixture(autouse=True)
def _built():
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER}); run cargo build --release in server/")


def view(name: str) -> jobs.JobFilter:
    """The job views the counts use (``WebApplication.job_filter``; the order is irrelevant to a count)."""
    return jobs.JobFilter(queue_states=["queued"] if name == "queue" else None, not_applied=name == "queue",
                          applied_only=name == "applications", favorites_only=name == "favorites",
                          include_closed=name in {"applications", "favorites"})


def applyai_reference(conn, config) -> dict:
    digest = today.build_digest(conn, config)
    return {
        "tracks": [{"name": name, "label": name.replace("_", " ").title(), "weekly_target": target}
                   for name, target in tracks.weekly_targets(conn).items()],
        "counts": {key: jobs.count_jobs(conn, view(name)) for key, name in (
            ("jobs", "jobs"), ("queued", "queue"), ("applications", "applications"), ("favorites", "favorites"))},
        "weekly_total": digest.weekly_total, "streak_days": digest.streak_days, "today": today.digest_text(digest),
    }


def stamp(delta: timedelta, form: str = "z") -> str:
    moment = datetime.now(timezone.utc) + delta
    return {"z": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), "date": moment.strftime("%Y-%m-%d"),
            "offset": moment.astimezone(timezone(timedelta(hours=-5))).isoformat(),
            "space": moment.strftime("%Y-%m-%d %H:%M:%S.%f")}[form]


def populate(conn, config) -> None:
    """Synthetic rows for every digest section."""
    track = config.tracks.tracks[0].name
    made = []
    for index in range(12):
        job, _ = jobs.upsert_job(conn, company=f"Synthetic Co {index % 4}", title=f"Synthetic Intern {index}",
                                 location="Remote", url=f"https://example.invalid/{index}", source="manual",
                                 source_job_id=f"bootstrap-{index}", jd_text="Synthetic posting")
        jobs.link_track(conn, job.id, track)
        jobs.set_tier(conn, job.id, "AB"[index % 2], reason="Synthetic")
        made.append(job.id)
    forms = ["z", "date", "offset", "space", "z", "z"]
    for index, job_id in enumerate(made[:6]):
        conn.execute("UPDATE jobs SET deadline_at=? WHERE id=?", (stamp(timedelta(days=index), forms[index]), job_id))
    conn.execute("UPDATE jobs SET source_deadline_at=?, deadline_at='' WHERE id=?", (stamp(timedelta(days=2)), made[6]))
    conn.execute("UPDATE jobs SET deadline_at='not a date' WHERE id=?", (made[7],))
    conn.execute("UPDATE jobs SET queue_state='skipped' WHERE id=?", (made[5],))
    conn.execute("UPDATE jobs SET queue_state='queued' WHERE id IN (?, ?)", (made[8], made[9]))
    conn.execute("UPDATE jobs SET favorited_at=? WHERE id IN (?, ?)", (stamp(timedelta()), made[1], made[10]))
    conn.execute("UPDATE jobs SET is_active=0 WHERE id=?", (made[11],))
    for index, days_ago in enumerate((0, 1, 2, 40)):
        application = mark_applied(conn, config, made[8 + index], applied_at=stamp(-timedelta(days=days_ago)))
        conn.execute("UPDATE applications SET last_activity_at=?, status=? WHERE id=?",
                     (stamp(-timedelta(days=days_ago)), "oa" if index == 3 else "applied", application.id))
    at = stamp(timedelta())
    for kind, due, note, job_id in (("custom", stamp(-timedelta(days=1)), "Call the synthetic recruiter", None),
                                    ("custom", stamp(timedelta(hours=1), "date"), None, made[2]),
                                    ("deadline", "2026-02-30", "Stored badly", None),
                                    ("custom", stamp(timedelta(days=3)), "Later", None)):
        conn.execute("INSERT INTO reminders(kind, due_at, note, job_id, created_at) VALUES (?, ?, ?, ?, ?)",
                     (kind, due, note, job_id, at))
    for name, status, error in (
        ("synthetic-board", "error", f"GET https://user:pw@board.invalid/jobs?page=2&token=abc failed: 401 {SECRET}"),
        ("synthetic-feed", "rate_limited", "Authorization: Bearer abc.def.ghi; too many requests " + "x" * 200),
        ("synthetic-ok", "ok", None),
        ("synthetic-dns", "partial", "getaddrinfo failed; page cap reached"),
    ):
        conn.execute("INSERT INTO sources(name, kind, enabled, last_status, last_error, updated_at) VALUES (?, 'other', 1, ?, ?, ?)"
                     " ON CONFLICT(name) DO UPDATE SET last_status=excluded.last_status, last_error=excluded.last_error",
                     (name, status, error, at))
    soon = datetime.now(timezone.utc) + timedelta(days=10)
    for index, (months, first_seen) in enumerate(((json.dumps([soon.month]), None),
                                                  (None, (soon - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")),
                                                  (json.dumps([1, 7]), None))):
        conn.execute("INSERT INTO companies(norm_name, display_name, from_watchlist, expected_open_months, created_at, updated_at)"
                     " VALUES (?, ?, 1, ?, ?, ?)", (f"synthetic watch {index}", f"Synthetic Watch {index}", months, at, at))
        if first_seen:
            company = conn.execute("SELECT id FROM companies WHERE norm_name=?", (f"synthetic watch {index}",)).fetchone()[0]
            conn.execute("INSERT INTO jobs(company_id, title, url, dedupe_key, first_seen_at, last_seen_at, is_active, created_at, updated_at)"
                         " VALUES (?, 'Old posting', 'https://example.invalid/old', 'synthetic-old', ?, ?, 0, ?, ?)",
                         (company, first_seen, first_seen, at, at))
    conn.commit()


def test_bootstrap_through_the_front_matches_the_python_functions(workspace, monkeypatch):
    # The worker loads the root's .env into its environment, and its redaction reads it.
    (workspace.settings.root / ".env").write_text(f"SYNTHETIC_API_KEY={SECRET}\n", encoding="utf-8")
    monkeypatch.setenv("SYNTHETIC_API_KEY", SECRET)
    populate(workspace.conn, workspace.config)
    with front_site(workspace.settings) as running:
        status, answer = running.request("/api/bootstrap")
        assert status == 200
        expected = {"demo": False, "modules": modules_state(DEFAULT_MODULES, workspace.settings.config_dir),
                    **applyai_reference(workspace.conn, workspace.config)}
        assert answer == expected
        assert list(answer) == list(expected), "the same key order"
        text = answer["today"]
        assert "Due (" in text and "Deadlines soon (" in text and "Source problems (" in text
        assert "Quiet, consider archiving (" in text and "Opens soon (" in text and "streak 3 days" in text
        assert SECRET not in text and "[redacted]" in text
        assert running.request("/api/bootstrap?x=1") == (400, {"error": "This route does not accept query parameters"})
        status, head = running.request("/api/bootstrap", method="HEAD")
        assert status == 200 and not head


def test_without_applyai_the_answer_is_the_module_state(tmp_path):
    from test_web_modules import make_root

    settings = make_root(tmp_path, ("workspace",))
    with front_site(settings) as running:
        assert running.request("/api/bootstrap") == (200, {"demo": False, "modules": modules_state(("workspace",), settings.config_dir)})


def test_the_digest_command_matches_on_edge_data(workspace, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_API_KEY", SECRET)
    populate(workspace.conn, workspace.config)
    # Hour 24 is the next day's midnight; a weekly target of zero; an inactive track.
    workspace.conn.execute("UPDATE applications SET applied_at='2026-11-12T24:00:00' WHERE id=(SELECT MIN(id) FROM applications)")
    workspace.conn.execute("UPDATE tracks SET weekly_target=0 WHERE rowid=(SELECT MIN(rowid) FROM tracks)")
    workspace.conn.execute("UPDATE tracks SET active=0 WHERE rowid=(SELECT MAX(rowid) FROM tracks)")
    workspace.conn.commit()
    apply = workspace.config.apply
    result = subprocess.run([str(RIKA_SERVER), "bootstrap-digest", str(workspace.settings.db_path), str(workspace.settings.root),
                             str(apply.deadline_window_days), str(apply.archive_after_days)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == applyai_reference(workspace.conn, workspace.config)
