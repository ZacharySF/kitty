"""Apply health: unattended ready rate per ATS family, top causes, per-field correctness.

Read-only over the attempt records (kitty.core.apply.attempts). The unit is the job:
a job is ready when any of its attempts reached ready (the owner can press Submit),
and it belongs to the ATS family of its latest attempt. A job with a closed attempt
and no ready one is a closed posting and leaves both numerator and denominator.
URR(a), "no owner input", counts a job only when no attempt up to its first ready
one relied on an owner input.

Rates carry Wilson 95% score intervals; below MIN_N jobs (or compared fields) the
rate is reported as insufficient data instead of a number.
"""
from __future__ import annotations

import math
import sqlite3
from collections import Counter

from kitty.core.apply.attempts import CAUSES, PROVENANCE

#: Smaller samples are shown as "insufficient data".
MIN_N = 20
#: The two-sided 95% normal quantile.
Z = 1.959963984540054
#: How many causes the report lists.
TOP_CAUSES = 8


def wilson(successes: int, n: int, z: float = Z) -> tuple[float, float]:
    """The Wilson score interval for successes out of n (n > 0)."""
    if n <= 0:
        raise ValueError("n must be positive")
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def _rate(successes: int, n: int) -> tuple[float | None, float | None, float | None]:
    if n < MIN_N:
        return None, None, None
    low, high = wilson(successes, n)
    return successes / n, low, high


def _family(name: str, jobs: list[dict]) -> dict:
    live = [job for job in jobs if not job["closed"]]
    ready = sum(job["ready"] for job in live)
    unattended = sum(job["ready_no_input"] for job in live)
    urr, low, high = _rate(ready, len(live))
    urr_a, low_a, high_a = _rate(unattended, len(live))
    return {
        "family": name, "jobs": len(live), "ready": ready, "closed": len(jobs) - len(live),
        "urr": urr, "low": low, "high": high, "insufficient": len(live) < MIN_N,
        "ready_no_input": unattended, "urr_no_input": urr_a, "no_input_low": low_a, "no_input_high": high_a,
    }


def report(conn: sqlite3.Connection) -> dict:
    """The health report (also GET /api/apply/health and `kitty apply report --json`)."""
    jobs: dict[int, dict] = {}
    attempt_causes: Counter[str] = Counter()
    last_attempt_at = None
    total = 0
    for row in conn.execute(
        "SELECT job_id, ats_family, outcome, cause, owner_inputs, ended_at FROM apply_attempts ORDER BY id"
    ):
        total += 1
        last_attempt_at = row["ended_at"]
        job = jobs.setdefault(row["job_id"], {"ready": False, "ready_no_input": False, "closed_seen": False,
                                              "inputs": 0, "family": "other", "cause": None})
        job["family"] = row["ats_family"]
        if not job["ready"]:
            job["inputs"] += row["owner_inputs"]
        if row["outcome"] == "ready":
            if not job["ready"]:
                job["ready_no_input"] = job["inputs"] == 0
            job["ready"] = True
        elif row["outcome"] == "closed":
            job["closed_seen"] = True
        else:
            attempt_causes[row["cause"]] += 1
        job["cause"] = row["cause"]
    for job in jobs.values():
        job["closed"] = job["closed_seen"] and not job["ready"]

    by_family: dict[str, list[dict]] = {}
    for job in jobs.values():
        by_family.setdefault(job["family"], []).append(job)
    families = sorted((_family(name, members) for name, members in by_family.items()),
                      key=lambda item: (-item["jobs"], item["family"]))

    job_causes = Counter(job["cause"] for job in jobs.values()
                         if not job["ready"] and not job["closed"] and job["cause"] != "ready")
    causes = [
        {"cause": cause, "label": CAUSES.get(cause, ("failed", cause))[1],
         "outcome": CAUSES.get(cause, ("failed", cause))[0], "jobs": count, "attempts": attempt_causes[cause]}
        for cause, count in sorted(job_causes.items(), key=lambda item: (-item[1], -attempt_causes[item[0]], item[0]))
    ][:TOP_CAUSES]

    filled: Counter[str] = Counter()
    for row in conn.execute("SELECT provenance FROM apply_attempt_fields"):
        filled[row[0].split(":", 1)[0]] += 1
    checked: dict[str, list[int]] = {}
    for row in conn.execute(
        """SELECT f.provenance, c.edited FROM apply_field_checks c
             JOIN apply_attempt_fields f ON f.attempt_id = c.attempt_id AND f.position = c.position"""
    ):
        counts = checked.setdefault(row[0].split(":", 1)[0], [0, 0])
        counts[0] += 1
        counts[1] += int(row[1])
    correctness = []
    for kind in sorted(set(filled) | set(checked), key=lambda k: PROVENANCE.index(k) if k in PROVENANCE else 99):
        compared, edited = checked.get(kind, [0, 0])
        rate, low, high = _rate(compared - edited, compared)
        correctness.append({"class": kind, "filled": filled[kind], "compared": compared, "edited": edited,
                            "rate": rate, "low": low, "high": high, "insufficient": compared < MIN_N})

    return {
        "min_n": MIN_N, "confidence": 0.95, "attempts": total, "last_attempt_at": last_attempt_at,
        "overall": _family("all", list(jobs.values())), "families": families,
        "causes": causes, "correctness": correctness,
    }
