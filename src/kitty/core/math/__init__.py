"""rika! math: a prerequisite topic graph, seeded problem generators, exact and SymPy
answer checking, graph mastery on FSRS, Zetamac-style sprints, mixed review, and book
sources (Grinstead & Snell exercises, problems from your own books).

- :mod:`.problems`: the template registry and the problem model (prompt, exact answer,
  worked steps, common-mistake detectors).
- :mod:`.templates`: the generators, one module per strand.
- :mod:`.topics`: the authored topic graph (with each topic's strategy) and its sync
  into ``math_topics``.
- :mod:`.checking` / :mod:`.cas`: answer checking; SymPy runs in a killable worker.
- :mod:`.mastery`: locked → ready → learning → mastered, FSRS reviews, FIRe-lite credit.
- :mod:`.practice`: issued problems, graded answers, lessons and the AI explain request.
- :mod:`.mixed`: mixed review, interleaved so no two problems in a row share a strategy.
- :mod:`.textbook`: Grinstead & Snell's exercises, their topic links and authored answers.
- :mod:`.library`: textbook and own-book cards on their own FSRS schedules.
- :mod:`.sprints`: timed mental-arithmetic runs.

Topics, generators, topic links and authored answers are written for rika! and released
under the repository's MIT licence. Grinstead & Snell's text is GNU FDL data kept outside
the MIT tree (``third_party/grinstead-snell``); it is shown to the learner and never sent
to AI. Own-book problems are stored by reference only.
"""
