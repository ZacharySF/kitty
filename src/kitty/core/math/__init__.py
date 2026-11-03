"""rika! math: a prerequisite topic graph, seeded problem generators, exact and SymPy
answer checking, graph mastery on FSRS, and Zetamac-style sprints.

- :mod:`.problems`: the template registry and the problem model (prompt, exact answer,
  worked steps, common-mistake detectors).
- :mod:`.templates`: the generators, one module per strand.
- :mod:`.topics`: the authored topic graph and its sync into ``math_topics``.
- :mod:`.checking` / :mod:`.cas`: answer checking; SymPy runs in a killable worker.
- :mod:`.mastery`: locked → ready → learning → mastered, FSRS reviews, FIRe-lite credit.
- :mod:`.practice`: issued problems, graded answers, lessons and the AI explain request.
- :mod:`.sprints`: timed mental-arithmetic runs.

Topics and generators are authored for rika! and released under the repository's MIT
licence. Book content (Grinstead & Snell, own-book references) is a separate, later
source of problems: it adds its own tables and references topics by id.
"""
