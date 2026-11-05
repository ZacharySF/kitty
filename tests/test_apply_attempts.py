"""Apply attempt records, the health report and per-field correctness from real use.

All data is synthetic. Browser tests run in headless Firefox against a local HTTP
fixture and are skipped when Firefox is not installed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import yaml

from kitty.config import load_config
from kitty.core import jobs as jobs_core
from kitty.core import questions as questions_core
from kitty.core import resumes as resumes_core
from kitty.core.apply import attempts, health, watch
from kitty.core.apply.bidi import open_tab
from kitty.core.apply.browser import headless_firefox
from kitty.core.apply.filler import _watched_tab, fill_job
from kitty.settings import Settings, find_firefox

# Distinctive synthetic values, so the scanner can prove none of them is stored.
PROFILE = """\
name: Quillon Vexmoor
first_name: Quillon
last_name: Vexmoor
email: quillon.vexmoor@example.invalid
phone: (555) 010-7788
school: Example State University
degree: B.S. Engineering
major: Electrical Engineering
standing: sophomore
graduation: 2032-05
home_city: Arlington
home_state: TX
"""

ANSWERS = """\
answers:
  - question: Are you legally authorized to work in the United States?
    answer: "Yes"
  - question: How did you hear about this position?
    answer: Zephyrline careers bulletin
"""

OWNER_EDIT = "Referral from Ostrava Kettlewing"
CANARIES = ("Quillon", "Vexmoor", "quillon.vexmoor@example.invalid", "Zephyrline careers bulletin",
            "Ostrava Kettlewing", "010-7788")

FORM = """<!doctype html>
<html><head><meta charset="utf-8"><title>Synthetic Co - Job Application</title></head><body>
<div id="app">
<h1>Apply for Hardware Intern</h1>
<form id="application-form" action="/submit" method="post">
  <label for="first_name">First Name *</label><input id="first_name" name="first_name" required>
  <label for="last_name">Last Name *</label><input id="last_name" name="last_name" required>
  <label for="email">Email *</label><input id="email" name="email" type="email" required>
  <label for="authorized">Are you legally authorized to work in the United States? *</label>
  <select id="authorized" name="authorized" required><option value="">Select...</option><option>Yes</option><option>No</option></select>
  <label for="source">How did you hear about this position? *</label><input id="source" name="source" required>
  <label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" required>
  <button type="submit" id="submit">Submit application</button>
</form>
</div>
<script>
  // Greenhouse-like: the app replaces the form with a confirmation and routes to /confirmation.
  window.__submitted = 0;
  document.getElementById('application-form').addEventListener('submit', event => {
    event.preventDefault();
    window.__submitted++;
    document.getElementById('app').innerHTML = '<h1>Thank you for applying</h1>';
    history.pushState({}, '', location.pathname.replace(/\\/$/, '') + '/confirmation');
  });
</script>
</body></html>"""


@pytest.fixture
def space(workspace):
    config_dir = workspace.settings.config_dir
    (config_dir / "profile.yaml").write_text(PROFILE)
    (config_dir / "answers.yaml").write_text(ANSWERS)
    workspace.config = load_config(config_dir)
    track = next(t for t in workspace.config.tracks.tracks if t.default_resume)
    pdf = workspace.settings.root / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% synthetic resume\n")
    resumes_core.add_resume(workspace.conn, track.default_resume, pdf)
    workspace.track = track
    return workspace


def _job(space, index: int, url: str | None = None):
    job, _ = jobs_core.upsert_job(
        space.conn, company=f"Synthetic {index}", title="Hardware intern",
        url=url or f"https://boards.example.invalid/synthetic/jobs/{4000 + index}",
        tracks=[space.track.name], location="Arlington, TX, United States", country="US",
    )
    return job


# ------------------------------------------------------------------ vocabulary


@pytest.mark.parametrize(("status", "note", "error", "outcome_cause", "cause"), [
    ("ready", None, None, "ready", "ready"),
    ("partial", "Required answers or uploads are missing or invalid: How Did You Hear About Us?", None,
     "missing_applicant_fact", "missing_answer"),
    ("partial", "Required answers or uploads are missing or invalid: Phone", None, "unsupported_controls",
     "invalid_field"),
    ("blocked", "browsingContext.getTree: no such frame", "browsingContext.getTree: no such frame",
     "browser_error", "browser_frame"),
    ("blocked", "The application tab closed or was replaced. Continue filling to reopen this job; "
     "unsaved answers may need re-entry.", "same", "browser_error", "tab_closed"),
    ("blocked", "Unsupported application page: no recognized form or safe entry; continue manually.", None,
     "unsupported_controls", "unsupported_page"),
    ("blocked", "captcha: a CAPTCHA is on the sign-in page; sign in yourself.", None, "human_boundary", "captcha"),
    ("blocked", "consent: review the visible consent dialog yourself.", None, "human_boundary", "consent"),
    ("blocked", "consent: credential fields are prepared; review the account checkbox or choices.", None,
     "human_boundary", "consent_account"),
    ("blocked", "unsupported: required custom controls need manual completion. Review any fields already filled.",
     None, "unsupported_controls", "custom_controls"),
    ("blocked", "login: authentication did not advance; continue manually (no automatic retry).", None,
     "human_boundary", "login_no_advance"),
    ("blocked", "login: account access is not authorized for this run.", None, "human_boundary",
     "login_not_authorized"),
    ("blocked", "login: this origin is not approved for credentials.", None, "human_boundary",
     "login_not_authorized"),
    ("blocked", "login: unrecognized Oracle email component; continue yourself.", None, "human_boundary",
     "login_unsupported"),
    ("blocked", "Resume upload deferred to protect retained answers from resume parsing; attach it manually.",
     None, "unsupported_controls", "upload_deferred"),
    ("partial", "Selection interaction failed: How Did You Hear About Us?: source menu unavailable", None,
     "unsupported_controls", "choice_failed"),
    ("blocked", "Recovery evidence could not be saved; review local storage before resuming.", None,
     "unsupported_controls", "evidence_save"),
    ("blocked", "closed: Posting unavailable; refresh availability before preparing it.", None, "unavailable",
     "closed"),
    ("blocked", "verification: no unique fresh matching employer email arrived within the bounded wait.", None,
     "human_boundary", "verification"),
    ("blocked", "Preparation time budget reached; review the retained application tab.", None,
     "unsupported_controls", "stopped"),
    ("blocked", "Application step limit reached; continue manually.", None, "unsupported_controls", "step_limit"),
    ("error", None, "Preparation approval changed or is missing; review the plan again before filling.",
     "human_boundary", "approval_changed"),
    ("blocked", "Something new and unexpected", None, "unsupported_controls", "unsupported"),
])
def test_todays_blockers_map_to_the_closed_cause_vocabulary(status, note, error, outcome_cause, cause):
    assert attempts.classify(status, note, error, outcome_cause) == cause
    assert attempts.outcome_of(cause) in attempts.OUTCOMES


def test_every_cause_has_an_outcome_and_label():
    assert attempts.CAUSES["ready"][0] == "ready" and attempts.CAUSES["closed"][0] == "closed"
    assert {outcome for outcome, _ in attempts.CAUSES.values()} == set(attempts.OUTCOMES)
    assert all(label for _, label in attempts.CAUSES.values())


# ------------------------------------------------------------------ records and report


def _attempt(space, job, *, status="ready", note=None, error=None, outcome_cause=None, ats="greenhouse",
             owner_inputs=0, run=None):
    outcome_cause = outcome_cause or {"ready": "ready"}.get(status, "unsupported_controls")
    return attempts.record(
        space.conn, job_id=job.id, ats=ats, url=job.url, progress=attempts.Progress(), status=status,
        note=note, error=error, outcome_cause=outcome_cause, run=run, owner_inputs=owner_inputs,
    )


MISSING = dict(status="partial", note="Required answers or uploads are missing or invalid: How Did You Hear About Us?",
               outcome_cause="missing_applicant_fact")
FRAME = dict(status="blocked", note="browsingContext.getTree: no such frame",
             error="browsingContext.getTree: no such frame", outcome_cause="browser_error")
CAPTCHA = dict(status="blocked", note="captcha: a visible challenge needs your action.", outcome_cause="human_boundary")
CLOSED = dict(status="blocked", note="closed: Posting unavailable; refresh availability before preparing it.",
              outcome_cause="unavailable")


def _seed(space):
    """Greenhouse: 10 ready + 15 live not ready + 2 closed; Lever: 1 ready + 4 not ready."""
    jobs = iter(range(1, 1000))
    job = lambda: _job(space, next(jobs))
    for _ in range(6):
        _attempt(space, job())
    for _ in range(2):                                   # recovered after a browser failure
        ready_later = job()
        _attempt(space, ready_later, **FRAME)
        _attempt(space, ready_later)
    for _ in range(2):                                   # ready only after an owner input
        _attempt(space, job(), owner_inputs=1)
    ready_then_closed = job()                            # 11th ready greenhouse job...
    _attempt(space, ready_then_closed)
    _attempt(space, ready_then_closed, **CLOSED)         # ...stays counted: it was ready while live
    for index in range(9):
        missing = job()
        if index < 2:
            _attempt(space, missing, **CAPTCHA)
        _attempt(space, missing, **MISSING)
    for _ in range(4):
        _attempt(space, job(), **FRAME)
    for _ in range(2):
        _attempt(space, job(), **CAPTCHA)
    _attempt(space, job(), **CLOSED)                     # closed postings leave the denominator
    failed_then_closed = job()
    _attempt(space, failed_then_closed, **FRAME)
    _attempt(space, failed_then_closed, **CLOSED)
    _attempt(space, job(), ats="lever")
    for _ in range(4):
        _attempt(space, job(), ats="lever", **MISSING)


def test_urr_per_family_is_per_job_with_closed_removed_and_exact_wilson_intervals(space):
    _seed(space)
    found = health.report(space.conn)

    greenhouse = next(item for item in found["families"] if item["family"] == "greenhouse")
    assert (greenhouse["jobs"], greenhouse["ready"], greenhouse["closed"]) == (26, 11, 2)
    assert greenhouse["urr"] == pytest.approx(11 / 26)
    low, high = health.wilson(11, 26)
    assert (greenhouse["low"], greenhouse["high"]) == (pytest.approx(low), pytest.approx(high))
    assert greenhouse["ready_no_input"] == 9 and greenhouse["insufficient"] is False
    assert greenhouse["urr_no_input"] == pytest.approx(9 / 26)

    lever = next(item for item in found["families"] if item["family"] == "lever")
    assert (lever["jobs"], lever["ready"], lever["insufficient"]) == (5, 1, True)
    assert lever["urr"] is lever["low"] is lever["high"] is lever["urr_no_input"] is None

    overall = found["overall"]
    assert (overall["family"], overall["jobs"], overall["ready"], overall["closed"]) == ("all", 31, 12, 2)
    assert found["families"][0]["family"] == "greenhouse"
    assert found["attempts"] == 6 + 4 + 2 + 2 + 11 + 4 + 2 + 1 + 2 + 1 + 4

    causes = {item["cause"]: item for item in found["causes"]}
    assert list(causes) == ["missing_answer", "browser_frame", "captcha"]
    assert (causes["missing_answer"]["jobs"], causes["missing_answer"]["attempts"]) == (13, 13)
    assert (causes["browser_frame"]["jobs"], causes["browser_frame"]["attempts"]) == (4, 7)
    assert (causes["captcha"]["jobs"], causes["captcha"]["attempts"]) == (2, 4)
    assert causes["missing_answer"]["outcome"] == "needs_owner" and causes["missing_answer"]["label"] == "Missing answer"


def test_wilson_intervals_match_published_values():
    # Wilson score 95% intervals (z = 1.95996), e.g. 10/25 -> 0.2340-0.5926.
    assert health.wilson(10, 25) == (pytest.approx(0.23403302377747973), pytest.approx(0.5926054264103301))
    assert health.wilson(8, 25) == (pytest.approx(0.17205190306907978), pytest.approx(0.5158973072689775))
    assert health.wilson(11, 30) == (pytest.approx(0.21873920806100708), pytest.approx(0.5448643634520511))
    assert health.wilson(24, 25) == (pytest.approx(0.8045593626380625), pytest.approx(0.9929037664980127))
    assert health.wilson(0, 20)[0] == 0.0 and health.wilson(20, 20)[1] == 1.0


def test_exact_urr_numbers_for_25_live_jobs(space):
    for index in range(25):
        _attempt(space, _job(space, index), **({} if index < 10 else MISSING))
    family = health.report(space.conn)["families"][0]
    assert (family["jobs"], family["ready"], family["urr"]) == (25, 10, 0.4)
    assert family["low"] == pytest.approx(0.23403302377747973)
    assert family["high"] == pytest.approx(0.5926054264103301)


def test_correctness_per_provenance_class_from_field_checks(space):
    rows = [attempts.Written(f"Profile field {i}", "profile", 0, (f"e{i}",)) for i in range(25)]
    rows += [attempts.Written(f"Saved answer {i}", f"saved_answer:question {i}", 0, (f"s{i}",)) for i in range(3)]
    job = _job(space, 1)
    attempt = attempts.record(space.conn, job_id=job.id, ats="greenhouse", url=job.url,
                              progress=attempts.Progress(), status="ready", note=None, error=None,
                              outcome_cause="ready", written=rows, hashes=[f"h{i}" for i in range(len(rows))])
    with space.conn:
        space.conn.executemany(
            "INSERT INTO apply_field_snapshots (attempt_id, position, value_hash, source, observed_at) "
            "VALUES (?, ?, ?, 'poll', 't')",
            [(attempt, i, "changed" if i in (0, 25) else f"h{i}") for i in range(len(rows))])
    assert attempts.compare(space.conn, attempt) == {"profile": (25, 1), "saved_answer": (3, 1)}
    assert attempts.compare(space.conn, attempt) == {"profile": (25, 1), "saved_answer": (3, 1)}  # once

    correctness = {item["class"]: item for item in health.report(space.conn)["correctness"]}
    profile = correctness["profile"]
    assert (profile["filled"], profile["compared"], profile["edited"], profile["rate"]) == (25, 25, 1, 0.96)
    assert (profile["low"], profile["high"]) == (pytest.approx(0.8045593626380625), pytest.approx(0.9929037664980127))
    saved = correctness["saved_answer"]
    assert (saved["compared"], saved["edited"], saved["insufficient"], saved["rate"]) == (3, 1, True, None)


def test_attempt_rows_are_append_only_and_provenance_is_a_closed_vocabulary(space):
    job = _job(space, 1)
    attempt = _attempt(space, job, **MISSING)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        space.conn.execute("UPDATE apply_attempts SET outcome = 'ready' WHERE id = ?", (attempt,))
    with pytest.raises(ValueError, match="provenance"):
        attempts.record(space.conn, job_id=job.id, ats="greenhouse", url=job.url, progress=attempts.Progress(),
                        status="ready", note=None, error=None, outcome_cause="ready",
                        written=[attempts.Written("Name", "guess")])
    row = space.conn.execute("SELECT * FROM apply_attempts WHERE id = ?", (attempt,)).fetchone()
    assert (row["outcome"], row["cause"], row["stage"]) == ("needs_owner", "missing_answer", "live-check")
    assert json.loads(row["failing_fields"]) == []
    assert row["tenant_origin"] == "https://boards.example.invalid"


def test_closed_job_selected_for_preparation_is_a_live_check_attempt(space):
    from kitty.core.apply.filler import fill_jobs

    job = _job(space, 1)
    space.conn.execute("UPDATE jobs SET is_active = 0 WHERE id = ?", (job.id,))
    reports = fill_jobs(space.settings, space.config, [job.id])
    assert reports[0].attempt_id is not None
    row = space.conn.execute("SELECT * FROM apply_attempts").fetchone()
    assert (row["stage"], row["outcome"], row["cause"]) == ("live-check", "closed", "closed")
    run = space.conn.execute("SELECT kind, finished_at, stats_json FROM runs WHERE id = ?", (row["run_id"],)).fetchone()
    assert run["kind"] == "apply" and run["finished_at"]
    assert json.loads(run["stats_json"])["outcomes"] == {"closed": 1}


def test_apply_report_cli_and_health_api(space, monkeypatch):
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from kitty import cli
    from kitty import web_automation

    _seed(space)
    monkeypatch.setattr(cli, "_open", lambda: SimpleNamespace(
        settings=space.settings, config=space.config, conn=space.conn, close=lambda: None,
    ))
    shown = CliRunner().invoke(cli.app, ["apply", "report"])
    assert shown.exit_code == 0, shown.output
    assert "greenhouse" in shown.output and "insufficient data (n = 5)" in shown.output
    assert "Missing answer" in shown.output
    as_json = CliRunner().invoke(cli.app, ["apply", "report", "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == health.report(space.conn)
    assert CliRunner().invoke(cli.app, ["apply", "report", "5"]).exit_code == 1

    api = web_automation.get(SimpleNamespace(config=space.config), space.conn, "/api/apply/health", {})
    assert api == health.report(space.conn)


# ------------------------------------------------------------------ stale flags


def test_refresh_fixes_stale_answered_flags_on_905_rows_after_answers_change(space):
    texts = [f"Synthetic screening question number {index}?" for index in range(905)]
    for text in texts:
        questions_core.log_question(space.conn, text)
    assert len(questions_core.list_questions(space.conn)) == 905          # all stale: answered = 0

    config_dir = space.settings.config_dir
    answers = yaml.safe_load(ANSWERS)
    answers["answers"] += [{"question": text, "answer": "Synthetic answer"} for text in texts[:220]]
    (config_dir / "answers.yaml").write_text(yaml.safe_dump(answers))
    config = load_config(config_dir)
    assert questions_core.refresh_if_changed(space.conn, config) is True
    assert len(questions_core.list_questions(space.conn)) == 905 - 220
    assert questions_core.refresh_if_changed(space.conn, config) is False  # unchanged files: no work

    answers["answers"] += [{"question": text, "answer": "Synthetic answer"} for text in texts[220:264]]
    (config_dir / "answers.yaml").write_text(yaml.safe_dump(answers))
    assert questions_core.refresh_if_changed(space.conn, load_config(config_dir)) is True
    assert len(questions_core.list_questions(space.conn)) == 905 - 264

    # A profile change alone also triggers the re-check.
    (config_dir / "profile.yaml").write_text(PROFILE.replace("home_city: Arlington", "home_city: Austin"))
    assert questions_core.refresh_if_changed(space.conn, load_config(config_dir)) is True


# ------------------------------------------------------------------ the browser


@pytest.fixture(scope="module")
def firefox(tmp_path_factory):
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    settings = Settings.load(root=tmp_path_factory.mktemp("firefox"))
    with headless_firefox(settings) as bidi:
        yield bidi


@pytest.fixture
def page(firefox):
    tab = open_tab(firefox)
    yield tab
    tab.close()


@pytest.fixture
def board():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = FORM.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):                     # the fixture never submits; any POST is a failure
            self.server.posts += 1
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.posts = 0
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join()


def test_in_page_sha256_matches_hashlib(page):
    page.goto("about:blank")
    for text in ("", "abc", "Zürich – 日本", "x" * 55, "y" * 56, "z" * 64, "w" * 1000):
        got = page.main.call(f"text => ({attempts._SHA256_JS})(text)", text)
        assert got == hashlib.sha256(text.encode()).hexdigest(), text


def _stored_text(conn: sqlite3.Connection) -> str:
    """Every text value in every table, for the no-values scanner."""
    parts = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            parts.extend(str(value) for value in tuple(row) if isinstance(value, (str, bytes)))
    return "\n".join(parts)


def test_owner_edit_before_submit_on_a_replaced_form_counts_per_provenance_class(space, page, board, monkeypatch):
    base = f"http://127.0.0.1:{board.server_port}/synthetic/jobs/4242"
    job = _job(space, 1, url=base)
    stale = questions_core.log_question(space.conn, "How did you hear about this position?")
    assert stale.answered is False

    report = fill_job(page, space.conn, space.settings, space.config, job)
    assert report.status == "ready", report.note
    assert report.attempt_id is not None and report.capture is not None

    # After each attempt the queue is re-checked: the saved answer covers the stale row.
    assert not any(q.id == stale.id for q in questions_core.list_questions(space.conn))

    attempt = space.conn.execute("SELECT * FROM apply_attempts WHERE id = ?", (report.attempt_id,)).fetchone()
    assert (attempt["outcome"], attempt["cause"], attempt["stage"]) == ("ready", "ready", "review")
    assert (attempt["page_number"], attempt["ats_family"]) == (1, "other")
    assert attempt["tenant_origin"] == f"http://127.0.0.1:{board.server_port}"
    fields = {row["label"]: dict(row) for row in space.conn.execute(
        "SELECT label, provenance, ready_hash FROM apply_attempt_fields WHERE attempt_id = ?", (report.attempt_id,))}
    assert {label: row["provenance"].split(":")[0] for label, row in fields.items()} == {
        "First Name": "profile", "Last Name": "profile", "Email": "profile",
        "Are you legally authorized to work in the United States?": "saved_answer",
        "How did you hear about this position?": "saved_answer",
    }
    assert all(row["ready_hash"] for row in fields.values())
    # The page hashes salt + value with SHA-256; the salt never leaves memory.
    salt = report.capture.salt
    assert fields["First Name"]["ready_hash"] == hashlib.sha256(f'{salt}\n["Quillon"]'.encode()).hexdigest()
    assert salt not in _stored_text(space.conn)

    tab = replace(_watched_tab(job, page, report), ats="greenhouse")
    monkeypatch.setitem(watch.RULES, "greenhouse", watch.ConfirmationRule(host="127.0.0.1", path=r"/confirmation/?$"))
    attempts.observe(space.conn, page.bidi, tab, confirmed=False)          # a watcher poll, nothing changed yet
    snapshots = space.conn.execute(
        "SELECT COUNT(*) FROM apply_field_snapshots WHERE attempt_id = ? AND source = 'poll'",
        (report.attempt_id,)).fetchone()[0]
    assert snapshots == 5

    # The owner fixes one saved answer, then presses Submit; the app replaces the
    # form before the watcher's next poll, so only the Submit capture saw the edit.
    page.main.call("""value => {
      const box = document.querySelector('#source');
      box.value = value;
      box.dispatchEvent(new Event('input', {bubbles: true}));
      box.dispatchEvent(new Event('change', {bubbles: true}));
    }""", OWNER_EDIT, page_realm=True)
    page.main.click(page.main.node("() => document.querySelector('#submit')"))
    assert page.main.call("() => [window.__submitted, location.pathname]", page_realm=True) == [
        1, "/synthetic/jobs/4242/confirmation"]
    assert page.main.call("() => document.querySelector('form')", page_realm=True) is None

    recorded = []
    live = watch.Watch(hours=0.01, tabs=[tab], on_applied=recorded.append)
    watch.watch_tabs(lambda watched: watch.look(page.bidi, watched), space.conn, space.config, live,
                     lambda message: None, poll_seconds=0.1,
                     observe=lambda watched, done: attempts.observe(space.conn, page.bidi, watched, confirmed=done))
    assert recorded == [tab] and live.tabs == []

    edited = {row["label"]: row["edited"] for row in space.conn.execute(
        """SELECT f.label, c.edited FROM apply_field_checks c
             JOIN apply_attempt_fields f ON f.attempt_id = c.attempt_id AND f.position = c.position
            WHERE c.attempt_id = ?""", (report.attempt_id,))}
    assert edited == {"First Name": 0, "Last Name": 0, "Email": 0,
                      "Are you legally authorized to work in the United States?": 0,
                      "How did you hear about this position?": 1}
    correctness = {item["class"]: item for item in health.report(space.conn)["correctness"]}
    assert (correctness["profile"]["compared"], correctness["profile"]["edited"]) == (3, 0)
    assert (correctness["saved_answer"]["compared"], correctness["saved_answer"]["edited"]) == (2, 1)
    assert board.posts == 0                                                # kitty never submitted

    # Scanner: no field value (filled or owner-typed), no salt, anywhere in the database
    # or the workspace's data files.
    stored = _stored_text(space.conn)
    for canary in CANARIES:
        assert canary not in stored, canary
    for path in space.settings.data_dir.rglob("*"):
        if path.is_file() and path.suffix not in {".db", ".db-wal", ".db-shm", ".pdf"}:
            text = path.read_bytes().decode("utf-8", "replace")
            assert not any(canary in text for canary in CANARIES), path


def test_failed_attempt_records_failing_labels_only(space, page, board):
    base = f"http://127.0.0.1:{board.server_port}/synthetic/jobs/4343"
    job = _job(space, 2, url=base)
    (space.settings.config_dir / "answers.yaml").write_text(
        "answers:\n  - question: Are you legally authorized to work in the United States?\n    answer: \"Yes\"\n")
    config = load_config(space.settings.config_dir)
    report = fill_job(page, space.conn, space.settings, config, job)
    assert report.status == "partial"
    row = space.conn.execute("SELECT * FROM apply_attempts WHERE id = ?", (report.attempt_id,)).fetchone()
    assert (row["outcome"], row["cause"], row["stage"], row["page_number"]) == ("needs_owner", "missing_answer", "page", 1)
    assert json.loads(row["failing_fields"]) == ["How did you hear about this position?"]
    hashes = space.conn.execute("SELECT COUNT(*) FROM apply_attempt_fields WHERE attempt_id = ? AND ready_hash IS NOT NULL",
                                (report.attempt_id,)).fetchone()[0]
    assert hashes == 0 and report.capture is None
