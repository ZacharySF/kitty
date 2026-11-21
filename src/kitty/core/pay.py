"""The pay a posting lists, for Find's pay column.

A source that reports pay beside the posting keeps it in its payload (the SimplifyJobs-style
repo tables' ``salary`` cell, hiring.cafe's hourly ``comp``); otherwise the first hourly
rate or yearly salary the posting's own text names. ``hourly`` (the top of a range, a
yearly figure over 2,080 hours) only orders the column; the text is what the page shows.
Nothing is estimated: a posting that names no pay has none.
"""
from __future__ import annotations

import json
import re

from kitty.core.discovery.eligibility import _HOURLY

HOURS_PER_YEAR = 2080
_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?[kK])"
_YEARLY = re.compile(
    rf"\$\s?{_AMOUNT}(?:\s*(?:-|–|to)\s*\$?\s?{_AMOUNT})?"
    r"(\s*(?:/\s*|per\s+|an?\s+)(?:year|yr|annum)\b|\s+(?:annually|yearly)\b)?", re.I)


def _number(amount: str) -> float:
    text = amount.replace(",", "").replace(" ", "")
    return float(text[:-1]) * 1000 if text[-1] in "kK" else float(text)


def _money(value: float) -> str:
    return f"${value:g}" if value == int(value) else f"${value:.2f}"


def _in_text(text: str, *, strict: bool) -> dict | None:
    """The highest hourly rate `text` names (as eligibility reads it), else its first yearly salary.

    `strict` (a whole posting) takes a yearly figure only as a range or with "per year", so a
    "$1,000 signing bonus" is not pay; a source's own pay cell needs neither.
    """
    best = None
    for match in _HOURLY.finditer(text):
        top = float(match.group(2) or match.group(1))
        if best is None or top > best["hourly"]:
            best = {"text": re.sub(r"\s+", " ", match.group(0)), "hourly": top}
    if best:
        return best
    for match in _YEARLY.finditer(text):
        if strict and not (match.group(2) or match.group(3)):
            continue
        return {"text": re.sub(r"\s+", " ", match.group(0)).strip(),
                "hourly": _number(match.group(2) or match.group(1)) / HOURS_PER_YEAR}
    return None


def _from_payload(raw: dict) -> dict | None:
    salary = raw.get("salary")
    if isinstance(salary, str) and salary.strip():
        text = re.sub(r"\s+", " ", salary).strip()
        return {"text": text, "hourly": (_in_text(text, strict=False) or {}).get("hourly")}
    comp = raw.get("comp")
    if isinstance(comp, dict):
        rates = [value for value in (comp.get("hourly_min"), comp.get("hourly_max"))
                 if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0]
        if rates:
            low, high = min(rates), max(rates)
            text = f"{_money(low)}–{_money(high)} / hr" if high != low else f"{_money(high)} / hr"
            return {"text": text, "hourly": float(high)}
    return None


def listed(conn, job_ids) -> dict[int, dict | None]:
    """{job id: {"text", "hourly"} or None} for each existing job of `job_ids`."""
    ids = list(dict.fromkeys(job_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    found: dict[int, dict | None] = {}
    for row in conn.execute(f"SELECT job_id, raw_json FROM job_sources WHERE job_id IN ({marks}) "
                            "AND raw_json IS NOT NULL ORDER BY last_seen_at DESC, id DESC", ids):
        if found.get(row["job_id"]):
            continue
        try:
            raw = json.loads(row["raw_json"])
        except ValueError:
            continue
        if isinstance(raw, dict):
            found[row["job_id"]] = _from_payload(raw)
    for row in conn.execute(f"SELECT id, jd_text FROM jobs WHERE id IN ({marks})", ids):
        if not found.get(row["id"]):
            found[row["id"]] = _in_text(row["jd_text"] or "", strict=True)
    return {ident: found[ident] for ident in ids if ident in found}
