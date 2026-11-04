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
    "linalg": "Linear algebra",
    "calculus": "Calculus",
    "discrete": "Discrete math",
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
    #: The solution method; mixed review never puts two problems of one strategy in a row.
    strategy: str = ""

    def weight(self, prereq: str) -> float:
        return self.credit.get(prereq, CREDIT)


def _t(id, strand, title, *prereqs, strategy, credit=None):
    return Topic(id, strand, title, tuple(prereqs), dict(credit or {}), strategy)


TOPICS: tuple[Topic, ...] = (
    _t("arith.add-sub", "arithmetic", "Addition and subtraction", strategy="mental-add"),
    _t("arith.times-tables", "arithmetic", "Times tables to 20 × 20", strategy="times-table"),
    _t("arith.mul-2x1", "arithmetic", "2-digit × 1-digit", "arith.times-tables", "arith.add-sub", strategy="split-multiply"),
    _t("arith.mul-2x2", "arithmetic", "2-digit × 2-digit", "arith.mul-2x1", credit={"arith.mul-2x1": 0.5}, strategy="split-multiply"),
    _t("arith.squares", "arithmetic", "Squares", "arith.mul-2x2", strategy="near-base-square"),
    _t("arith.division", "arithmetic", "Exact division", "arith.times-tables", strategy="divide"),
    _t("arith.powers-of-two", "arithmetic", "Powers of 2", "arith.times-tables", strategy="powers"),
    _t("arith.fractions", "arithmetic", "Fraction arithmetic", "arith.division", strategy="common-denominator"),
    _t("arith.frac-decimal", "arithmetic", "Fractions to decimals", "arith.fractions", strategy="long-division"),
    _t("arith.percentages", "arithmetic", "Percentages", "arith.frac-decimal", strategy="percent"),
    _t("arith.estimation", "arithmetic", "Division to 2 significant figures", "arith.division", "arith.frac-decimal", strategy="estimate"),
    _t("arith.odds", "arithmetic", "Odds and probability", "arith.fractions", strategy="odds"),

    _t("count.product-rule", "counting", "Product rule", "arith.times-tables", strategy="product-rule"),
    _t("count.permutations", "counting", "Permutations", "count.product-rule", strategy="permutation"),
    _t("count.multisets", "counting", "Arrangements with repeats", "count.permutations", "arith.division", strategy="divide-out-repeats"),
    _t("count.combinations", "counting", "Combinations", "count.permutations", "arith.division", strategy="combination"),
    _t("count.complement", "counting", "Complementary counting", "count.product-rule", strategy="complement"),
    _t("count.inclusion-exclusion", "counting", "Inclusion–exclusion", "count.complement", "arith.division", strategy="inclusion-exclusion"),
    _t("count.stars-bars", "counting", "Stars and bars", "count.combinations", strategy="stars-and-bars"),
    _t("count.binomial-theorem", "counting", "Binomial coefficients", "count.combinations", strategy="binomial-theorem"),

    _t("prob.basic", "probability", "Equally likely outcomes", "count.product-rule", "arith.fractions", strategy="count-outcomes"),
    _t("prob.complement", "probability", "At least one", "prob.basic", "count.complement", strategy="complement"),
    _t("prob.independence", "probability", "Independent events", "prob.basic", strategy="multiply-independent"),
    _t("prob.hypergeometric", "probability", "Drawing without replacement", "prob.basic", "count.combinations", strategy="count-hands"),
    _t("prob.conditional", "probability", "Conditional probability", "prob.basic", strategy="restrict-sample-space"),
    _t("prob.total-probability", "probability", "Law of total probability", "prob.conditional", strategy="condition-on-cases"),
    _t("prob.bayes", "probability", "Bayes' rule", "prob.total-probability", "arith.percentages",
       credit={"prob.total-probability": 0.5}, strategy="bayes"),
    _t("prob.binomial", "probability", "Binomial probabilities", "prob.independence", "count.combinations", strategy="binomial"),
    _t("prob.geometric", "probability", "First success", "prob.independence", strategy="geometric"),
    _t("prob.symmetry", "probability", "Symmetry", "prob.basic", strategy="symmetry"),
    _t("prob.order-stats", "probability", "Max and min of dice", "prob.independence", "count.complement", strategy="cdf-power"),
    _t("prob.gamblers-ruin", "probability", "First-step analysis", "prob.total-probability", strategy="first-step"),

    _t("ev.expected-value", "ev", "Expected value", "prob.basic", strategy="weighted-average"),
    _t("ev.linearity", "ev", "Linearity and indicators", "ev.expected-value", "prob.independence", strategy="indicators"),
    _t("ev.max-dice", "ev", "Expected maximum", "ev.expected-value", "prob.order-stats", strategy="cdf-power"),
    _t("ev.optimal-stopping", "ev", "Rerolls and stopping", "ev.expected-value", strategy="backward-induction"),
    _t("ev.variance", "ev", "Variance", "ev.expected-value", strategy="second-moment"),
    _t("ev.variance-sum", "ev", "Variance of sums", "ev.variance", "ev.linearity", strategy="variance-rules"),
    _t("ev.waiting-time", "ev", "Waiting times", "ev.expected-value", "prob.geometric", strategy="first-step"),
    _t("ev.fair-price", "ev", "Fair prices", "ev.expected-value", "arith.odds", strategy="break-even"),

    _t("stats.mean-median", "statistics", "Mean and median", "arith.division", strategy="summary-statistic"),
    _t("stats.spread", "statistics", "Variance and standard deviation of data", "stats.mean-median", strategy="second-moment"),
    _t("stats.z-score", "statistics", "z-scores", "stats.spread", strategy="standardise"),
    _t("stats.normal-rule", "statistics", "The 68–95–99.7 rule", "stats.z-score", strategy="normal-rule"),
    _t("stats.standard-error", "statistics", "Standard error of the mean", "stats.normal-rule", "ev.variance-sum", strategy="sqrt-n"),
    _t("stats.confidence", "statistics", "95% confidence intervals", "stats.standard-error", strategy="interval"),

    _t("linalg.vectors", "linalg", "Vector arithmetic", "arith.add-sub", "arith.times-tables", strategy="componentwise"),
    _t("linalg.norm", "linalg", "Vector length", "linalg.vectors", "arith.squares", strategy="pythagoras"),
    _t("linalg.dot", "linalg", "Dot product", "linalg.vectors", strategy="dot-product"),
    _t("linalg.orthogonal", "linalg", "Orthogonal vectors", "linalg.dot", "arith.division", strategy="solve-dot-zero"),
    _t("linalg.matvec", "linalg", "Matrix times vector", "linalg.dot", strategy="row-times-column"),
    _t("linalg.matmul", "linalg", "Matrix multiplication", "linalg.matvec", credit={"linalg.matvec": 0.5}, strategy="row-times-column"),
    _t("linalg.det2", "linalg", "2 × 2 determinants", "arith.times-tables", "arith.add-sub", strategy="ad-minus-bc"),
    _t("linalg.det3", "linalg", "3 × 3 determinants", "linalg.det2", credit={"linalg.det2": 0.5}, strategy="cofactor-expansion"),
    _t("linalg.inverse2", "linalg", "2 × 2 inverses", "linalg.det2", "linalg.matmul", "arith.fractions", strategy="adjugate"),
    _t("linalg.inverse3", "linalg", "3 × 3 inverses", "linalg.inverse2", "linalg.det3", strategy="adjugate"),
    _t("linalg.eigen2", "linalg", "2 × 2 eigenvalues", "linalg.det2", "linalg.matvec", strategy="characteristic-polynomial"),

    _t("calc.limits", "calculus", "Limits by factoring", "arith.fractions", strategy="factor-cancel"),
    _t("calc.limits-infinity", "calculus", "Limits at infinity", "calc.limits", strategy="leading-terms"),
    _t("calc.power-rule", "calculus", "Power rule", "arith.times-tables", strategy="power-rule"),
    _t("calc.slopes", "calculus", "Slope at a point", "calc.power-rule", strategy="evaluate-derivative"),
    _t("calc.product-rule", "calculus", "Product rule", "calc.power-rule", strategy="product-rule-derivative"),
    _t("calc.quotient-rule", "calculus", "Quotient rule", "calc.product-rule", strategy="quotient-rule"),
    _t("calc.chain-rule", "calculus", "Chain rule", "calc.power-rule", strategy="chain-rule"),
    _t("calc.standard-derivatives", "calculus", "Derivatives of exp, log and trig", "calc.power-rule", strategy="standard-derivatives"),
    _t("calc.antiderivatives", "calculus", "Integrals of polynomials", "calc.power-rule", "arith.fractions", strategy="reverse-power-rule"),
    _t("calc.definite", "calculus", "Definite integrals", "calc.antiderivatives", strategy="fundamental-theorem"),
    _t("calc.integrals-exp-trig", "calculus", "Integrals of exp and trig", "calc.antiderivatives", "calc.standard-derivatives",
       strategy="reverse-chain-rule"),
    _t("calc.taylor", "calculus", "Taylor approximations", "calc.standard-derivatives", "calc.chain-rule", strategy="taylor-series"),

    _t("disc.mod-arith", "discrete", "Modular arithmetic", "arith.division", strategy="reduce-mod"),
    _t("disc.mod-power", "discrete", "Modular powers", "disc.mod-arith", "arith.powers-of-two", strategy="repeated-squaring"),
    _t("disc.gcd", "discrete", "Greatest common divisors", "arith.division", strategy="euclid"),
    _t("disc.mod-inverse", "discrete", "Modular inverses", "disc.gcd", "disc.mod-arith", strategy="extended-euclid"),
    _t("disc.recurrence", "discrete", "Linear recurrences", "arith.times-tables", "arith.add-sub", strategy="iterate-recurrence"),
    _t("disc.recurrence-closed", "discrete", "Closed forms of recurrences", "disc.recurrence", "arith.powers-of-two",
       strategy="characteristic-roots"),
    _t("disc.loop-count", "discrete", "Counting loop iterations", "count.combinations", strategy="sum-formula"),
    _t("disc.big-o", "discrete", "Big-O growth", "disc.loop-count", strategy="dominant-term"),
    _t("disc.graph-edges", "discrete", "Handshake lemma", "count.combinations", strategy="degree-sum"),
    _t("disc.graph-counting", "discrete", "Counting graphs and trees", "disc.graph-edges", "arith.powers-of-two", strategy="count-graphs"),
    _t("disc.lattice-paths", "discrete", "Lattice paths", "count.combinations", strategy="binomial-path"),
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
