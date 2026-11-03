"""The authored math topic graph and its sync into ``math_topics`` / ``math_prereqs``.

A topic unlocks once every prerequisite is mastered. ``credit`` is the FIRe-lite weight
a correct answer on the topic passes to each prerequisite it directly encompasses
(a heuristic, tuned after real use). Topic ids are stable: attempts and mastery
reference them. The graph must be acyclic; :func:`validate` and the database trigger
on ``math_prereqs`` both enforce it.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from kitty.db.connection import transaction

STRANDS = {
    "arithmetic": "Arithmetic and estimation",
    "counting": "Counting",
    "probability": "Probability",
    "ev": "Expected value and variance",
    "statistics": "Statistics",
}
#: Default implicit credit a topic passes to each direct prerequisite.
CREDIT = 0.3


@dataclass(frozen=True)
class Topic:
    id: str
    strand: str
    title: str
    prereqs: tuple[str, ...] = ()
    #: Per-prerequisite FIRe-lite weight overrides (0 = prerequisite, not encompassed).
    credit: dict[str, float] = field(default_factory=dict)

    def weight(self, prereq: str) -> float:
        return self.credit.get(prereq, CREDIT)


def _t(id, strand, title, *prereqs, credit=None):
    return Topic(id, strand, title, tuple(prereqs), dict(credit or {}))


TOPICS: tuple[Topic, ...] = (
    _t("arith.add-sub", "arithmetic", "Addition and subtraction"),
    _t("arith.times-tables", "arithmetic", "Times tables to 20 × 20"),
    _t("arith.mul-2x1", "arithmetic", "2-digit × 1-digit", "arith.times-tables", "arith.add-sub"),
    _t("arith.mul-2x2", "arithmetic", "2-digit × 2-digit", "arith.mul-2x1", credit={"arith.mul-2x1": 0.5}),
    _t("arith.squares", "arithmetic", "Squares", "arith.mul-2x2"),
    _t("arith.division", "arithmetic", "Exact division", "arith.times-tables"),
    _t("arith.powers-of-two", "arithmetic", "Powers of 2", "arith.times-tables"),
    _t("arith.fractions", "arithmetic", "Fraction arithmetic", "arith.division"),
    _t("arith.frac-decimal", "arithmetic", "Fractions to decimals", "arith.fractions"),
    _t("arith.percentages", "arithmetic", "Percentages", "arith.frac-decimal"),
    _t("arith.estimation", "arithmetic", "Division to 2 significant figures", "arith.division", "arith.frac-decimal"),
    _t("arith.odds", "arithmetic", "Odds and probability", "arith.fractions"),

    _t("count.product-rule", "counting", "Product rule", "arith.times-tables"),
    _t("count.permutations", "counting", "Permutations", "count.product-rule"),
    _t("count.multisets", "counting", "Arrangements with repeats", "count.permutations", "arith.division"),
    _t("count.combinations", "counting", "Combinations", "count.permutations", "arith.division"),
    _t("count.complement", "counting", "Complementary counting", "count.product-rule"),
    _t("count.inclusion-exclusion", "counting", "Inclusion–exclusion", "count.complement", "arith.division"),
    _t("count.stars-bars", "counting", "Stars and bars", "count.combinations"),
    _t("count.binomial-theorem", "counting", "Binomial coefficients", "count.combinations"),

    _t("prob.basic", "probability", "Equally likely outcomes", "count.product-rule", "arith.fractions"),
    _t("prob.complement", "probability", "At least one", "prob.basic", "count.complement"),
    _t("prob.independence", "probability", "Independent events", "prob.basic"),
    _t("prob.hypergeometric", "probability", "Drawing without replacement", "prob.basic", "count.combinations"),
    _t("prob.conditional", "probability", "Conditional probability", "prob.basic"),
    _t("prob.total-probability", "probability", "Law of total probability", "prob.conditional"),
    _t("prob.bayes", "probability", "Bayes' rule", "prob.total-probability", "arith.percentages",
       credit={"prob.total-probability": 0.5}),
    _t("prob.binomial", "probability", "Binomial probabilities", "prob.independence", "count.combinations"),
    _t("prob.geometric", "probability", "First success", "prob.independence"),
    _t("prob.symmetry", "probability", "Symmetry", "prob.basic"),
    _t("prob.order-stats", "probability", "Max and min of dice", "prob.independence", "count.complement"),
    _t("prob.gamblers-ruin", "probability", "First-step analysis", "prob.total-probability"),

    _t("ev.expected-value", "ev", "Expected value", "prob.basic"),
    _t("ev.linearity", "ev", "Linearity and indicators", "ev.expected-value", "prob.independence"),
    _t("ev.max-dice", "ev", "Expected maximum", "ev.expected-value", "prob.order-stats"),
    _t("ev.optimal-stopping", "ev", "Rerolls and stopping", "ev.expected-value"),
    _t("ev.variance", "ev", "Variance", "ev.expected-value"),
    _t("ev.variance-sum", "ev", "Variance of sums", "ev.variance", "ev.linearity"),
    _t("ev.waiting-time", "ev", "Waiting times", "ev.expected-value", "prob.geometric"),
    _t("ev.fair-price", "ev", "Fair prices", "ev.expected-value", "arith.odds"),

    _t("stats.mean-median", "statistics", "Mean and median", "arith.division"),
    _t("stats.spread", "statistics", "Variance and standard deviation of data", "stats.mean-median"),
    _t("stats.z-score", "statistics", "z-scores", "stats.spread"),
    _t("stats.normal-rule", "statistics", "The 68–95–99.7 rule", "stats.z-score"),
    _t("stats.standard-error", "statistics", "Standard error of the mean", "stats.normal-rule", "ev.variance-sum"),
    _t("stats.confidence", "statistics", "95% confidence intervals", "stats.standard-error"),
)
BY_ID = {topic.id: topic for topic in TOPICS}


class GraphError(ValueError):
    """The topic graph has a cycle, a dangling prerequisite or a bad weight."""


def validate(topics=TOPICS) -> list[str]:
    """Topic ids in prerequisite order; raises GraphError for a cycle or unknown id."""
    by_id = {}
    for topic in topics:
        if topic.id in by_id:
            raise GraphError(f"Duplicate topic {topic.id}")
        if topic.strand not in STRANDS:
            raise GraphError(f"Topic {topic.id} has unknown strand {topic.strand}")
        by_id[topic.id] = topic
    for topic in topics:
        for prereq in topic.prereqs:
            if prereq not in by_id:
                raise GraphError(f"Topic {topic.id} needs unknown topic {prereq}")
        if set(topic.credit) - set(topic.prereqs) or any(not 0 <= w <= 1 for w in topic.credit.values()):
            raise GraphError(f"Topic {topic.id} credits a non-prerequisite or a weight outside 0–1")
    order, state = [], {}

    def visit(ident, path):
        if state.get(ident) == "done":
            return
        if state.get(ident) == "open":
            cycle = path[path.index(ident):] + [ident]
            raise GraphError("Prerequisite cycle: " + " → ".join(cycle))
        state[ident] = "open"
        for prereq in by_id[ident].prereqs:
            visit(prereq, path + [ident])
        state[ident] = "done"
        order.append(ident)

    for topic in topics:
        visit(topic.id, [])
    return order


def sync(conn: sqlite3.Connection, topics=TOPICS) -> None:
    """Make the tables match the authored graph; a no-op when they already do."""
    validate(topics)
    want_topics = [(topic.id, topic.strand, topic.title, index) for index, topic in enumerate(topics)]
    want_edges = sorted((topic.id, prereq, topic.weight(prereq)) for topic in topics for prereq in topic.prereqs)
    have_topics = [tuple(row) for row in conn.execute("SELECT id, strand, title, position FROM math_topics ORDER BY position")]
    have_edges = sorted(tuple(row) for row in conn.execute("SELECT topic_id, prereq_id, credit FROM math_prereqs"))
    if have_topics == want_topics and have_edges == want_edges:
        return
    with transaction(conn, immediate=True):
        conn.execute("DELETE FROM math_prereqs")
        keep = [row[0] for row in want_topics]
        conn.execute(f"DELETE FROM math_topics WHERE id NOT IN ({','.join('?' * len(keep))})", keep)
        conn.executemany(
            "INSERT INTO math_topics(id, strand, title, position) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET strand=excluded.strand, title=excluded.title, position=excluded.position",
            want_topics)
        conn.executemany("INSERT INTO math_prereqs(topic_id, prereq_id, credit) VALUES (?,?,?)", want_edges)
