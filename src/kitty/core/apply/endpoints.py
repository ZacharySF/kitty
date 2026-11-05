"""Endpoint evidence from the submit guard's network tripwire (research note N3 §4.5).

Two append-only JSON Lines files under `data/apply-endpoints/` (owner-only):

- `observed.jsonl`: what L2 decided during fills that the survey needs, one
  line per request that an observe-only allowlist let through (`observed`) or
  that a rule failed (`fail`): the family, method, path template, rule id and
  decision. It is how kitty learns Greenhouse, Lever and SmartRecruiters upload
  paths before their allowlists are enforced (guard.OBSERVED_ALLOWLISTS).
- `learned.jsonl`: passive learning after release (OWNER RULING R-PASSIVE,
  2026-11-04; kitty.core.apply.watch.SubmitLearner): the family, method and path
  template of the first non-GET request after the owner's own Submit click that
  preceded a recognised confirmation.

Never a query value, header, cookie or body: the policy never reads them and
these records keep only `guard.path_template()` of the path. A learned path
becomes a deny rule only through a reviewed change to guard.PROFILES.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from kitty.core.apply.guard import Decision, path_template
from kitty.dates import now_iso
from kitty.settings import Settings

DIRECTORY = "apply-endpoints"
OBSERVED = "observed.jsonl"
LEARNED = "learned.jsonl"


def _append(settings: Settings, name: str, rows: Iterable[dict]) -> int:
    lines = [json.dumps(row, sort_keys=True) + "\n" for row in rows]
    if not lines:
        return 0
    directory = settings.data_dir / DIRECTORY
    if directory.is_symlink():
        raise OSError("endpoint evidence directory must not be a symbolic link")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / name
    if path.is_symlink():
        raise OSError("endpoint evidence file must not be a symbolic link")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.writelines(lines)
    return len(lines)


def record_fill(settings: Settings, family: str, *, observed: Iterable[Decision],
                blocked: Iterable[Decision]) -> int:
    """Append one fill's observed and failed requests; returns the number of lines."""
    stamp = now_iso()
    rows = [{"at": stamp, "family": family, "method": decision.method,
             "path": path_template(decision.path), "endpoint": decision.rule,
             "decision": "observed" if decision.observed else decision.verdict}
            for decision in (*observed, *blocked)]
    return _append(settings, OBSERVED, rows)


def record_learned(settings: Settings, family: str, method: str, path: str) -> None:
    """Append one passively learned submit endpoint (method and path template only)."""
    _append(settings, LEARNED, [{"at": now_iso(), "family": family, "method": method.upper(),
                                 "path": path_template(path), "source": "owner_submit"}])


def read(settings: Settings, name: str) -> list[dict]:
    """Every record in one evidence file (empty when there is none yet)."""
    path: Path = settings.data_dir / DIRECTORY / name
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]
