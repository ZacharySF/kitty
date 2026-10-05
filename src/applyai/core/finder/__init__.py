"""Stage 5: the niche company finder.

Proposes firms worth adding to the watchlist, scored against config/skills.yaml.
I approve (the firm joins config/watchlist.yaml and the companies table) or
reject (it is never proposed again). Nothing is applied to, ever.

Public API (used by the CLI, the TUI and later the MCP server):
    run_finder, FinderResult, FinderError, DEFAULT_CAP
    list_candidates, decision_counts, last_run, approve, reject, Candidate
"""

from applyai.core.finder.run import DEFAULT_CAP, FinderError, FinderResult, run_finder
from applyai.core.finder.store import Candidate, approve, decision_counts, last_run, list_candidates, reject

__all__ = [
    "DEFAULT_CAP", "Candidate", "FinderError", "FinderResult", "approve", "decision_counts",
    "last_run", "list_candidates", "reject", "run_finder",
]
