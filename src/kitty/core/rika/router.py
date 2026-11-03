"""The tier router (design Q12): each request goes to the smallest tier that can do it.

* **fast**: talk, simple edits and classification (and, in phase 2, every voice reply);
* **standard**: planning;
* **deep**: multi-step work across many tools, or a retry of work that failed.

Routing is a local heuristic, so the router itself costs no tokens. The loop then
escalates one tier when a tool call fails validation or a reply is incomplete
(:func:`escalate`). A per-request override always wins.

Tiers map to OpenRouter models chosen in Settings → AI (``ai_settings.rika_*_model``);
an empty setting uses the default for the provider family of the main model.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

TIERS = ("fast", "standard", "deep")
#: Default models per provider family (the prefix of the main Settings → AI model);
#: "" is the family-less default used before a main model is chosen.
DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {"fast": "anthropic/claude-haiku-4.5", "standard": "anthropic/claude-sonnet-4.5",
                  "deep": "anthropic/claude-opus-4.5"},
    "openai": {"fast": "openai/gpt-5-mini", "standard": "openai/gpt-5", "deep": "openai/gpt-5-pro"},
    "google": {"fast": "google/gemini-2.5-flash", "standard": "google/gemini-2.5-pro",
               "deep": "google/gemini-2.5-pro"},
    "": {"fast": "google/gemini-2.5-flash", "standard": "anthropic/claude-sonnet-4.5",
         "deep": "anthropic/claude-opus-4.5"},
}
#: Longest request still treated as a short one; longer ones are long tasks.
LONG_REQUEST = 1200

_PLANNING = re.compile(
    r"\b(plan|planning|schedule|organi[sz]e|prioriti[sz]e|break (?:it |this |that )?down|outline|"
    r"strategy|roadmap|prepare for|agenda|timetable)\b", re.I)
_RETRY = re.compile(r"\b(try again|retry|didn'?t work|did not work|that failed|still broken|redo)\b", re.I)
_CLAUSES = re.compile(r"\s*(?:;|\n+|,?\s+and then\s+|,\s*then\s+|\.\s+(?=[A-Za-z]))\s*", re.I)


@dataclass(frozen=True)
class Decision:
    tier: str
    reason: str


def route(text: str, *, override: str | None = None, failed_before: bool = False) -> Decision:
    """The tier for one request."""
    if override is not None:
        if override not in TIERS:
            raise ValueError("Choose the fast, standard or deep tier")
        return Decision(override, "override")
    if failed_before or _RETRY.search(text):
        return Decision("deep", "retry")
    clauses = [part for part in _CLAUSES.split(text.strip()) if part.strip()]
    if len(clauses) >= 3 or len(text) > LONG_REQUEST:
        return Decision("deep", "multi-step")
    if _PLANNING.search(text) or len(clauses) == 2:
        return Decision("standard", "planning")
    return Decision("fast", "simple")


def escalate(tier: str) -> str | None:
    """The next tier up, or None at deep."""
    index = TIERS.index(tier)
    return TIERS[index + 1] if index + 1 < len(TIERS) else None


def configured(conn: sqlite3.Connection) -> dict[str, str]:
    """The tier models saved in Settings → AI; '' means the family default."""
    row = conn.execute("SELECT rika_fast_model,rika_standard_model,rika_deep_model FROM ai_settings WHERE id=1").fetchone()
    return dict(zip(TIERS, tuple(row)))


def defaults(conn: sqlite3.Connection) -> dict[str, str]:
    """The default tier models for the main model's provider family."""
    main = conn.execute("SELECT model FROM ai_settings WHERE id=1").fetchone()[0]
    family = main.split("/", 1)[0] if "/" in main else ""
    return dict(DEFAULTS.get(family, DEFAULTS[""]))


def models(conn: sqlite3.Connection) -> dict[str, str]:
    """The model each tier runs on: the saved one, else the family default."""
    chosen, fallback = configured(conn), defaults(conn)
    return {tier: chosen[tier] or fallback[tier] for tier in TIERS}
