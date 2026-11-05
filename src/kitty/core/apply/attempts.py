"""Apply attempt records and per-field correctness from real use.

Every browser attempt at a job appends one `apply_attempts` row (migration 054):
the run, the ATS family and tenant origin, how far it got (stage, page N and its
name), the outcome, a typed cause from the closed vocabulary below, the labels of
the fields that stopped it, its duration, how many owner inputs it relied on and
whether automation was hidden. `jobs.fill_*` stays the latest snapshot
(kitty.core.apply.state); these rows are the history the health report reads
(kitty.core.apply.health).

Each field kitty wrote also records where its value came from (`PROVENANCE`). At
ready, kitty stores a salted hash of each such field's value, never the value.
The salt is random per attempt and lives only in memory (the watched tab), so
the database cannot be searched for a guessed answer. After release the
read-only watcher re-reads those fields at every poll in kitty's private sandbox
and stores the latest hashes; a passive capture listener on the owner's Submit
click (installed at release, never cancelling anything) keeps one more snapshot
from the moment of the click, also in sessionStorage so a same-origin
navigation to the confirmation page keeps it. When the confirmation shows, the
last snapshot is compared with the ready hashes: a changed field is an owner
edit, i.e. a kitty error for that field's provenance class.

Known limits: only fields still present on the ready page are hashed (earlier
pages of multi-page flows are gone by then); a full navigation to another origin
between the last poll and the confirmation loses the click snapshot, and the
last poll (at most watch.POLL_SECONDS old) is used instead.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence
from urllib.parse import urlsplit

from kitty.core.apply.bidi import BidiError, ConnectionLost, Tab
from kitty.core.apply.page import _COMBO_VALUES_JS, _DOM_HELPERS_JS, form_frames
from kitty.dates import now_iso
from kitty.db.connection import transaction

#: How far an attempt got, in order. "page" carries its number and name.
STAGES = ("live-check", "entry", "account", "verify", "page", "review")

OUTCOMES = ("ready", "needs_owner", "unsupported", "failed", "closed")

#: Where a written field's value came from. `saved_answer` and `confirmed_rule`
#: carry their key or rule id after a colon.
PROVENANCE = ("profile", "saved_answer", "confirmed_rule", "resume_derived", "ai_draft",
              "consent_ruling", "account")

#: The closed cause vocabulary: cause -> (outcome, label).
CAUSES: dict[str, tuple[str, str]] = {
    "ready": ("ready", "Ready for your Submit"),
    "closed": ("closed", "Posting closed"),
    "captcha": ("needs_owner", "CAPTCHA"),
    "verification": ("needs_owner", "Email or security verification"),
    "credentials": ("needs_owner", "Saved credentials missing or invalid"),
    "login_not_authorized": ("needs_owner", "Account access not authorized for this origin"),
    "login_manual": ("needs_owner", "Account set to manual sign-in"),
    "login_rejected": ("needs_owner", "Sign-in rejected or account locked"),
    "account_exists": ("needs_owner", "Existing account needs its sign-in"),
    "login_no_advance": ("needs_owner", "Sign-in did not advance"),
    "consent_account": ("needs_owner", "Account terms or consent"),
    "consent": ("needs_owner", "Consent dialog"),
    "missing_answer": ("needs_owner", "Missing answer"),
    "upload_deferred": ("needs_owner", "Resume upload deferred on a retained tab"),
    "approval_changed": ("needs_owner", "Reviewed plan changed"),
    "handoff": ("needs_owner", "Other handoff"),
    "login_unsupported": ("unsupported", "Unsupported sign-in form"),
    "profile_records": ("unsupported", "Profile records need manual entry"),
    "custom_controls": ("unsupported", "Unsupported custom controls"),
    "choice_failed": ("unsupported", "Choice control failed"),
    "invalid_field": ("unsupported", "Saved answer rejected by the field"),
    "validation_errors": ("unsupported", "Page validation errors"),
    "eightfold_profile": ("unsupported", "Eightfold profile step"),
    "resume_unverified": ("unsupported", "Resume upload not verified"),
    "review_unverified": ("unsupported", "Review page could not be verified"),
    "step_repeated": ("unsupported", "Step did not advance"),
    "step_limit": ("unsupported", "Step limit reached"),
    "unsupported_step": ("unsupported", "Unsupported application step"),
    "unsupported_page": ("unsupported", "Unsupported application page"),
    "unsupported": ("unsupported", "Other unsupported page"),
    # Explicit exclusions (proposal-05 Step 3): Indeed Apply, Tesla, employer-own portals,
    # Paradox, unresolved Litos, broken redirects. Typed handoffs; never attempted.
    "unsupported_flow": ("unsupported", "Excluded flow (handed to you)"),
    "loading": ("failed", "Page kept loading"),
    "upload_pending": ("failed", "Upload or resume parsing still pending"),
    "stopped": ("failed", "Stopped or out of time"),
    "evidence_save": ("failed", "Recovery evidence could not be saved"),
    "tab_closed": ("failed", "Tab closed or replaced"),
    "browser_frame": ("failed", "Browser frame lost (no such frame)"),
    "browser_connection": ("failed", "Browser connection lost"),
    "browser_error": ("failed", "Browser error"),
    "unknown": ("failed", "Unclassified stop"),
}

# Ordered (cause, pattern) rules over today's blocker and error strings (casefolded).
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple((cause, re.compile(pattern)) for cause, pattern in (
    ("closed", r"^closed:"),
    ("unsupported_flow", r"^unsupported_flow:"),
    ("evidence_save", r"recovery evidence could not be saved"),
    ("approval_changed", r"preparation approval changed|review a new preparation plan|review the plan again"),
    ("stopped", r"preparation cancelled|time budget reached"),
    ("captcha", r"^captcha:"),
    ("verification", r"^verification:"),
    ("credentials", r"^login: .*(application-credentials|credentials file)"),
    ("login_not_authorized", r"^login: .*(not authorized for this run|automation is off|account creation is off"
                             r"|not an approved origin|not approved for credentials|unapproved origin"
                             r"|origin is not approved)"),
    ("login_manual", r"^login: .*(manual sign-in|manual authentication|sign in or create the required account)"),
    ("login_rejected", r"^login: .*(rejected sign-in|locked)"),
    ("account_exists", r"^login: .*(already has an account|existing account was observed|account-state evidence)"),
    ("login_no_advance", r"^login: .*(did not advance|account-stage limit|cannot repeat an account submission"
                         r"|remained on|separate sign-in step)"),
    ("login_unsupported", r"^login:"),
    ("consent_account", r"^consent: .*(account|credential|terms|agreements)"),
    ("consent", r"^consent:"),
    ("loading", r"^loading:"),
    ("profile_records", r"required profile records"),
    ("custom_controls", r"required custom controls"),
    ("choice_failed", r"^selection interaction failed"),
    ("upload_deferred", r"resume upload deferred"),
    ("upload_pending", r"upload or parsing is still pending"),
    ("resume_unverified", r"resume upload is not verified"),
    ("review_unverified", r"earlier required steps cannot be verified|required application fields still need review"),
    ("validation_errors", r"page validation errors"),
    ("step_repeated", r"did not advance or repeated"),
    ("step_limit", r"step limit reached"),
    ("unsupported_step", r"unsupported application step"),
    ("unsupported_page", r"unsupported application page"),
    ("tab_closed", r"tab closed or was replaced|no such browsing context"),
    ("browser_frame", r"no such frame"),
    ("browser_connection", r"websocket|connection (?:lost|closed)|disconnected"),
))

#: Fallbacks by the filler's OutcomeCause when no rule matched.
_BY_OUTCOME_CAUSE = {
    "ready": "ready", "unavailable": "closed", "human_boundary": "handoff",
    "missing_applicant_fact": "missing_answer", "unsupported_controls": "unsupported",
    "browser_error": "browser_error",
}

_MISSING = re.compile(r"^(required answers or uploads are missing|eightfold profile step still needs required answers)")


def classify(status: str, note: str | None, error: str | None, outcome_cause: str) -> str:
    """The typed cause of one attempt, from its status, blocker text and OutcomeCause."""
    if status == "ready":
        return "ready"
    text = "\n".join(part for part in (note, error) if part).casefold().strip()
    if _MISSING.match(text):
        if outcome_cause == "missing_applicant_fact":
            return "missing_answer"
        return "eightfold_profile" if text.startswith("eightfold") else "invalid_field"
    if text.startswith("eightfold profile step"):
        return "eightfold_profile"
    for cause, pattern in _RULES:
        if pattern.search(text):
            return cause
    if error:
        return "browser_error"
    return _BY_OUTCOME_CAUSE.get(outcome_cause, "unknown")


def outcome_of(cause: str) -> str:
    return CAUSES[cause][0]


def tenant_origin(url: str) -> str | None:
    """scheme://host[:port] of an http(s) URL, without credentials or path."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


@dataclass
class Run:
    """The `runs` row (kind 'apply') an attempt belongs to, and the browser's automation flag."""

    id: int | None = None
    hide_automation: bool = False


def start_run(conn: sqlite3.Connection) -> Run:
    from kitty.core.sources import start_run as open_run

    with transaction(conn):
        return Run(open_run(conn, "apply"))


def finish_run(conn: sqlite3.Connection, run: Run, *, error: str | None = None) -> None:
    """Close the run with its attempt counts by outcome."""
    from kitty.core.sources import finish_run as close_run

    if run.id is None:
        return
    counts = {row[0]: row[1] for row in conn.execute(
        "SELECT outcome, COUNT(*) FROM apply_attempts WHERE run_id = ? GROUP BY outcome", (run.id,))}
    with transaction(conn):
        close_run(conn, run.id, stats={"attempts": sum(counts.values()), "outcomes": counts,
                                       "hide_automation": run.hide_automation}, error=error)


@dataclass
class Progress:
    """How far one attempt got. Stages never go backwards; each filled page counts."""

    started_at: str = field(default_factory=now_iso)
    started: float = field(default_factory=time.monotonic)
    stage: str = "live-check"
    page_number: int | None = None
    page_name: str | None = None

    def reach(self, stage: str) -> None:
        if STAGES.index(stage) > STAGES.index(self.stage):
            self.stage = stage

    def page(self, name: str | None) -> None:
        self.reach("page")
        self.page_number = (self.page_number or 0) + 1
        self.page_name = (name or "").strip()[:120] or None


@dataclass(frozen=True)
class Written:
    """One field kitty wrote: where it is (frame index, sandbox keys), its label and provenance."""

    label: str
    provenance: str
    frame: int | None = None
    keys: tuple[str, ...] = ()


@dataclass
class Capture:
    """What the watcher needs to re-read a ready tab's written fields (memory only)."""

    salt: str
    #: (frame index, ((position, keys), ...)) for every hashed field.
    frames: tuple[tuple[int, tuple[tuple[int, tuple[str, ...]], ...]], ...]
    storage_key: str
    clicks_seen: dict[int, float] = field(default_factory=dict)


def _check_provenance(value: str) -> str:
    kind = value.split(":", 1)[0]
    if kind not in PROVENANCE:
        raise ValueError(f"unknown provenance class: {value}")
    return value


# ------------------------------------------------------------------ hashing in the page


def _sha256_constants() -> tuple[list[int], list[int]]:
    primes: list[int] = []
    candidate = 2
    while len(primes) < 64:
        if all(candidate % prime for prime in primes):
            primes.append(candidate)
        candidate += 1

    def cube_root(n: int) -> int:
        root = round(n ** (1 / 3))
        while root ** 3 > n:
            root -= 1
        while (root + 1) ** 3 <= n:
            root += 1
        return root

    rounds = [cube_root(prime << 96) & 0xFFFFFFFF for prime in primes]
    initial = [math.isqrt(prime << 64) & 0xFFFFFFFF for prime in primes[:8]]
    return rounds, initial


_K, _H = _sha256_constants()

#: A synchronous SHA-256 (hex) over UTF-8, so a click listener can hash in place.
_SHA256_JS = r"""
text => {
  const K = [""" + ",".join(str(k) for k in _K) + r"""];
  const H = [""" + ",".join(str(h) for h in _H) + r"""];
  const bytes = new TextEncoder().encode(text);
  const length = bytes.length, size = ((length + 9 + 63) >> 6) << 6;
  const data = new Uint8Array(size);
  data.set(bytes);
  data[length] = 0x80;
  const view = new DataView(data.buffer);
  view.setUint32(size - 8, Math.floor(length / 0x20000000));
  view.setUint32(size - 4, (length * 8) >>> 0);
  const w = new Uint32Array(64);
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  for (let offset = 0; offset < size; offset += 64) {
    for (let t = 0; t < 16; t++) w[t] = view.getUint32(offset + t * 4);
    for (let t = 16; t < 64; t++) {
      const s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3);
      const s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
      w[t] = (w[t - 16] + s0 + w[t - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let t = 0; t < 64; t++) {
      const t1 = (h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[t] + w[t]) | 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    [a, b, c, d, e, f, g, h].forEach((value, index) => { H[index] = (H[index] + value) | 0; });
  }
  return H.map(value => (value >>> 0).toString(16).padStart(8, '0')).join('');
}"""

#: (items, salt) -> {position: hash} for connected fields; passwords and files are never read.
_SNAPSHOT_JS = r"""
(items, salt) => {""" + _DOM_HELPERS_JS + r"""
  const sha256 = (""" + _SHA256_JS + r""");
  const comboValues = (""" + _COMBO_VALUES_JS + r""");
  const valueOf = el => {
    if (el.matches('input[type=password], input[type=file]')) return null;
    if (el.matches('input[type=checkbox], input[type=radio]')) return el.checked ? '1' : '0';
    if (el.matches('[role=checkbox], [role=radio], [role=switch]')) return el.getAttribute('aria-checked') === 'true' ? '1' : '0';
    if (el.matches('select')) return Array.from(el.selectedOptions).map(o => o.textContent.replace(/\s+/g, ' ').trim()).join('\u0001');
    if (el.matches('[role=combobox], input[data-uxi-widget-type=selectinput], button[aria-haspopup=listbox]'))
      return comboValues(el).join('\u0001') + '\u0002' + (el.value ?? '');
    if (el.matches('input, textarea')) return el.value;
    return el.textContent.replace(/\s+/g, ' ').trim();
  };
  const byKey = globalThis.kittyByKey;
  const hashes = {};
  for (const [position, keys] of items) {
    const elements = keys.map(key => byKey?.get(key));
    if (!elements.length || elements.some(e => !e?.isConnected)) continue;
    const values = elements.map(valueOf);
    if (values.some(value => value === null)) continue;
    hashes[position] = sha256(salt + '\n' + JSON.stringify(values));
  }
  return hashes;
}"""

#: Install the passive Submit capture: on a click on a button-like control (or a
#: form submission) keep one snapshot; never prevents, stops or changes anything.
_CAPTURE_JS = r"""
(items, salt, storageKey) => {
  const snapshot = (""" + _SNAPSHOT_JS + r""");
  globalThis.kittyCapture?.abort();
  const controller = globalThis.kittyCapture = new AbortController();
  const record = () => {
    const taken = {at: Date.now(), hashes: snapshot(items, salt)};
    globalThis.kittyClickSnapshot = taken;
    try { sessionStorage.setItem(storageKey, JSON.stringify(taken)); } catch {}
  };
  const options = {capture: true, passive: true, signal: controller.signal};
  document.addEventListener('click', event => {
    const path = event.composedPath();
    if (path.some(node => node?.matches?.('button, input[type=submit], input[type=image], [role=button]'))) record();
  }, options);
  document.addEventListener('submit', record, options);
  return true;
}"""

#: (items, salt, storageKey) -> the current hashes and the latest Submit capture, if any.
_READ_JS = r"""
(items, salt, storageKey) => {
  const snapshot = (""" + _SNAPSHOT_JS + r""");
  let click = globalThis.kittyClickSnapshot ?? null;
  if (!click) { try { click = JSON.parse(sessionStorage.getItem(storageKey) || 'null'); } catch {} }
  return {current: snapshot(items, salt), click};
}"""

_FORGET_JS = r"""
storageKey => {
  globalThis.kittyCapture?.abort();
  delete globalThis.kittyClickSnapshot;
  try { sessionStorage.removeItem(storageKey); } catch {}
  return true;
}"""


def _grouped(written: Sequence[Written]) -> tuple[tuple[int, tuple[tuple[int, tuple[str, ...]], ...]], ...]:
    frames: dict[int, list[tuple[int, tuple[str, ...]]]] = {}
    for position, item in enumerate(written):
        if item.frame is not None and item.keys and not item.provenance.startswith("account"):
            frames.setdefault(item.frame, []).append((position, item.keys))
    return tuple((frame, tuple(items)) for frame, items in sorted(frames.items()))


def release(page: Tab, written: Sequence[Written]) -> tuple[Capture | None, list[str | None]]:
    """At ready: hash every written field still on the page and install the Submit capture.

    Returns the in-memory capture (None when nothing could be hashed) and the ready
    hash per written field (None where it is not on this page). Browser failures
    leave the attempt unhashed rather than failing it.
    """
    hashes: list[str | None] = [None] * len(written)
    grouped = _grouped(written)
    if not grouped:
        return None, hashes
    capture = Capture(salt=secrets.token_hex(16), frames=grouped,
                      storage_key=f"kitty.application.snapshot.{secrets.token_hex(6)}")
    try:
        frames = form_frames(page)
        for index, items in grouped:
            if index >= len(frames):
                continue
            seen = frames[index].call(_SNAPSHOT_JS, items, capture.salt)
            for position, value in seen.items():
                hashes[int(position)] = value
            frames[index].call(_CAPTURE_JS, items, capture.salt, capture.storage_key)
    except ConnectionLost:
        raise
    except BidiError:
        return None, [None] * len(written)
    return (capture if any(hashes) else None), hashes


def record(
    conn: sqlite3.Connection, *, job_id: int, ats: str, url: str, progress: Progress, status: str,
    note: str | None, error: str | None, outcome_cause: str, run: Run | None = None,
    failing: Iterable[str] = (), owner_inputs: int = 0, written: Sequence[Written] = (),
    hashes: Sequence[str | None] = (),
) -> int:
    """Append one attempt (and its written fields) and return its id.

    Field labels are stored, never values; hashes only for a ready attempt.
    """
    cause = classify(status, note, error, outcome_cause)
    outcome = outcome_of(cause)
    if outcome == "closed":
        progress = Progress(progress.started_at, progress.started, progress.stage)  # no page facts
    labels = [] if outcome == "ready" else list(dict.fromkeys(label[:200] for label in failing if label))
    ended = now_iso()
    duration = max(0, int((time.monotonic() - progress.started) * 1000))
    for item in written:
        _check_provenance(item.provenance)
    with transaction(conn):
        attempt = conn.execute(
            """INSERT INTO apply_attempts (run_id, job_id, ats_family, tenant_origin, started_at, ended_at,
                   duration_ms, stage, page_number, page_name, outcome, cause, failing_fields, owner_inputs,
                   hide_automation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run.id if run else None, job_id, ats or "other", tenant_origin(url), progress.started_at, ended,
             duration, progress.stage, progress.page_number, progress.page_name, outcome, cause,
             json.dumps(labels), max(0, owner_inputs), int(bool(run and run.hide_automation))),
        ).lastrowid
        conn.executemany(
            "INSERT INTO apply_attempt_fields (attempt_id, position, label, provenance, ready_hash) VALUES (?, ?, ?, ?, ?)",
            [(attempt, position, item.label[:200], item.provenance[:200],
              (hashes[position] if outcome == "ready" and position < len(hashes) else None))
             for position, item in enumerate(written)],
        )
    return int(attempt)


def written_labels(conn: sqlite3.Connection, job_id: int) -> set[str]:
    """Labels kitty wrote in this job's earlier attempts."""
    return {row[0] for row in conn.execute(
        "SELECT DISTINCT f.label FROM apply_attempt_fields f JOIN apply_attempts a ON a.id = f.attempt_id "
        "WHERE a.job_id = ?", (job_id,))}


# ------------------------------------------------------------------ after release


def _store(conn: sqlite3.Connection, attempt_id: int, hashes: dict, source: str) -> None:
    if not hashes:
        return
    stamp = now_iso()
    with transaction(conn):
        conn.executemany(
            """INSERT INTO apply_field_snapshots (attempt_id, position, value_hash, source, observed_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (attempt_id, position) DO UPDATE SET
                 value_hash = excluded.value_hash, source = excluded.source, observed_at = excluded.observed_at""",
            [(attempt_id, int(position), str(value), source, stamp) for position, value in hashes.items()],
        )


def observe(conn: sqlite3.Connection, bidi, tab, *, confirmed: bool) -> None:
    """One watcher look at a released tab (read-only, kitty's sandbox).

    Before the confirmation: store the latest Submit capture, then the current
    hashes (newer). At the confirmation: store only a new Submit capture (the form
    is gone or replaced), then compare and record owner edits. A lost connection
    is raised like the watcher's own look; other browser errors skip this look.
    """
    capture: Capture | None = getattr(tab, "capture", None)
    attempt_id = getattr(tab, "attempt_id", None)
    if capture is None or attempt_id is None:
        return
    try:
        frames = form_frames(Tab(bidi, tab.context))
        for index, items in capture.frames:
            if index >= len(frames):
                continue
            seen = frames[index].call(_READ_JS, items, capture.salt, capture.storage_key, timeout=10.0)
            click = seen.get("click")
            if click and click.get("at") != capture.clicks_seen.get(index):
                capture.clicks_seen[index] = click.get("at")
                _store(conn, attempt_id, click.get("hashes") or {}, "click")
            if not confirmed:
                _store(conn, attempt_id, seen.get("current") or {}, "poll")
        if confirmed:
            for index, _ in capture.frames:
                if index < len(frames):
                    frames[index].call(_FORGET_JS, capture.storage_key, timeout=10.0)
    except ConnectionLost:
        raise
    except BidiError:
        pass
    if confirmed:
        compare(conn, attempt_id)


def compare(conn: sqlite3.Connection, attempt_id: int) -> dict[str, tuple[int, int]]:
    """Record, once, which hashed fields the owner changed; return {class: (compared, edited)}."""
    with transaction(conn):
        conn.execute(
            """INSERT OR IGNORE INTO apply_field_checks (attempt_id, position, edited, confirmed_at)
               SELECT f.attempt_id, f.position, s.value_hash != f.ready_hash, ?
                 FROM apply_attempt_fields f
                 JOIN apply_field_snapshots s ON s.attempt_id = f.attempt_id AND s.position = f.position
                WHERE f.attempt_id = ? AND f.ready_hash IS NOT NULL""",
            (now_iso(), attempt_id),
        )
    counts: dict[str, tuple[int, int]] = {}
    for row in conn.execute(
        """SELECT f.provenance, c.edited FROM apply_field_checks c
             JOIN apply_attempt_fields f ON f.attempt_id = c.attempt_id AND f.position = c.position
            WHERE c.attempt_id = ?""", (attempt_id,)):
        kind = row[0].split(":", 1)[0]
        compared, edited = counts.get(kind, (0, 0))
        counts[kind] = (compared + 1, edited + int(row[1]))
    return counts
