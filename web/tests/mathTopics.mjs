// The full 80-topic math graph (ids, strands, titles and prerequisites as in
// src/kitty/core/math/topics.py) with invented progress, for tests/mathFixture.mjs's
// `full` option: the Topics drawer must stay readable at this size.

const PAST = new Date(Date.now() - 2 * 86_400_000).toISOString();
const FUTURE = new Date(Date.now() + 6 * 86_400_000).toISOString();

const GRAPH = [
  ["arith.add-sub", "arithmetic", "Addition and subtraction", ""],
  ["arith.times-tables", "arithmetic", "Times tables to 20 × 20", ""],
  ["arith.mul-2x1", "arithmetic", "2-digit × 1-digit", "arith.times-tables arith.add-sub"],
  ["arith.mul-2x2", "arithmetic", "2-digit × 2-digit", "arith.mul-2x1"],
  ["arith.squares", "arithmetic", "Squares", "arith.mul-2x2"],
  ["arith.division", "arithmetic", "Exact division", "arith.times-tables"],
  ["arith.powers-of-two", "arithmetic", "Powers of 2", "arith.times-tables"],
  ["arith.fractions", "arithmetic", "Fraction arithmetic", "arith.division"],
  ["arith.frac-decimal", "arithmetic", "Fractions to decimals", "arith.fractions"],
  ["arith.percentages", "arithmetic", "Percentages", "arith.frac-decimal"],
  ["arith.estimation", "arithmetic", "Division to 2 significant figures", "arith.division arith.frac-decimal"],
  ["arith.odds", "arithmetic", "Odds and probability", "arith.fractions"],
  ["count.product-rule", "counting", "Product rule", "arith.times-tables"],
  ["count.permutations", "counting", "Permutations", "count.product-rule"],
  ["count.multisets", "counting", "Arrangements with repeats", "count.permutations arith.division"],
  ["count.combinations", "counting", "Combinations", "count.permutations arith.division"],
  ["count.complement", "counting", "Complementary counting", "count.product-rule"],
  ["count.inclusion-exclusion", "counting", "Inclusion–exclusion", "count.complement arith.division"],
  ["count.stars-bars", "counting", "Stars and bars", "count.combinations"],
  ["count.binomial-theorem", "counting", "Binomial coefficients", "count.combinations"],
  ["prob.basic", "probability", "Equally likely outcomes", "count.product-rule arith.fractions"],
  ["prob.complement", "probability", "At least one", "prob.basic count.complement"],
  ["prob.independence", "probability", "Independent events", "prob.basic"],
  ["prob.hypergeometric", "probability", "Drawing without replacement", "prob.basic count.combinations"],
  ["prob.conditional", "probability", "Conditional probability", "prob.basic"],
  ["prob.total-probability", "probability", "Law of total probability", "prob.conditional"],
  ["prob.bayes", "probability", "Bayes' rule", "prob.total-probability arith.percentages"],
  ["prob.binomial", "probability", "Binomial probabilities", "prob.independence count.combinations"],
  ["prob.geometric", "probability", "First success", "prob.independence"],
  ["prob.symmetry", "probability", "Symmetry", "prob.basic"],
  ["prob.order-stats", "probability", "Max and min of dice", "prob.independence count.complement"],
  ["prob.gamblers-ruin", "probability", "First-step analysis", "prob.total-probability"],
  ["ev.expected-value", "ev", "Expected value", "prob.basic"],
  ["ev.linearity", "ev", "Linearity and indicators", "ev.expected-value prob.independence"],
  ["ev.max-dice", "ev", "Expected maximum", "ev.expected-value prob.order-stats"],
  ["ev.optimal-stopping", "ev", "Rerolls and stopping", "ev.expected-value"],
  ["ev.variance", "ev", "Variance", "ev.expected-value"],
  ["ev.variance-sum", "ev", "Variance of sums", "ev.variance ev.linearity"],
  ["ev.waiting-time", "ev", "Waiting times", "ev.expected-value prob.geometric"],
  ["ev.fair-price", "ev", "Fair prices", "ev.expected-value arith.odds"],
  ["stats.mean-median", "statistics", "Mean and median", "arith.division"],
  ["stats.spread", "statistics", "Variance and standard deviation of data", "stats.mean-median"],
  ["stats.z-score", "statistics", "z-scores", "stats.spread"],
  ["stats.normal-rule", "statistics", "The 68–95–99.7 rule", "stats.z-score"],
  ["stats.standard-error", "statistics", "Standard error of the mean", "stats.normal-rule ev.variance-sum"],
  ["stats.confidence", "statistics", "95% confidence intervals", "stats.standard-error"],
  ["linalg.vectors", "linalg", "Vector arithmetic", "arith.add-sub arith.times-tables"],
  ["linalg.norm", "linalg", "Vector length", "linalg.vectors arith.squares"],
  ["linalg.dot", "linalg", "Dot product", "linalg.vectors"],
  ["linalg.orthogonal", "linalg", "Orthogonal vectors", "linalg.dot arith.division"],
  ["linalg.matvec", "linalg", "Matrix times vector", "linalg.dot"],
  ["linalg.matmul", "linalg", "Matrix multiplication", "linalg.matvec"],
  ["linalg.det2", "linalg", "2 × 2 determinants", "arith.times-tables arith.add-sub"],
  ["linalg.det3", "linalg", "3 × 3 determinants", "linalg.det2"],
  ["linalg.inverse2", "linalg", "2 × 2 inverses", "linalg.det2 linalg.matmul arith.fractions"],
  ["linalg.inverse3", "linalg", "3 × 3 inverses", "linalg.inverse2 linalg.det3"],
  ["linalg.eigen2", "linalg", "2 × 2 eigenvalues", "linalg.det2 linalg.matvec"],
  ["calc.limits", "calculus", "Limits by factoring", "arith.fractions"],
  ["calc.limits-infinity", "calculus", "Limits at infinity", "calc.limits"],
  ["calc.power-rule", "calculus", "Power rule", "arith.times-tables"],
  ["calc.slopes", "calculus", "Slope at a point", "calc.power-rule"],
  ["calc.product-rule", "calculus", "Product rule", "calc.power-rule"],
  ["calc.quotient-rule", "calculus", "Quotient rule", "calc.product-rule"],
  ["calc.chain-rule", "calculus", "Chain rule", "calc.power-rule"],
  ["calc.standard-derivatives", "calculus", "Derivatives of exp, log and trig", "calc.power-rule"],
  ["calc.antiderivatives", "calculus", "Integrals of polynomials", "calc.power-rule arith.fractions"],
  ["calc.definite", "calculus", "Definite integrals", "calc.antiderivatives"],
  ["calc.integrals-exp-trig", "calculus", "Integrals of exp and trig", "calc.antiderivatives calc.standard-derivatives"],
  ["calc.taylor", "calculus", "Taylor approximations", "calc.standard-derivatives calc.chain-rule"],
  ["disc.mod-arith", "discrete", "Modular arithmetic", "arith.division"],
  ["disc.mod-power", "discrete", "Modular powers", "disc.mod-arith arith.powers-of-two"],
  ["disc.gcd", "discrete", "Greatest common divisors", "arith.division"],
  ["disc.mod-inverse", "discrete", "Modular inverses", "disc.gcd disc.mod-arith"],
  ["disc.recurrence", "discrete", "Linear recurrences", "arith.times-tables arith.add-sub"],
  ["disc.recurrence-closed", "discrete", "Closed forms of recurrences", "disc.recurrence arith.powers-of-two"],
  ["disc.loop-count", "discrete", "Counting loop iterations", "count.combinations"],
  ["disc.big-o", "discrete", "Big-O growth", "disc.loop-count"],
  ["disc.graph-edges", "discrete", "Handshake lemma", "count.combinations"],
  ["disc.graph-counting", "discrete", "Counting graphs and trees", "disc.graph-edges arith.powers-of-two"],
  ["disc.lattice-paths", "discrete", "Lattice paths", "count.combinations"],
];

const MASTERED = new Set(["prob.basic", "prob.conditional", "prob.independence", "linalg.vectors", "linalg.dot", "linalg.det2", "calc.power-rule", "disc.mod-arith", "disc.gcd"]);
const LEARNING = { "linalg.matvec": 0.5, "calc.chain-rule": 0.35, "disc.mod-inverse": 0.7 };
const DUE = new Set(["arith.fractions", "count.combinations", "linalg.dot"]);
const mastered = (id, strand) => MASTERED.has(id) || strand === "arithmetic" || strand === "counting";

export const FULL_TOPICS = GRAPH.map(([id, strand, title, needs]) => {
  const prereqs = needs ? needs.split(" ") : [];
  const strandOf = (other) => GRAPH.find((row) => row[0] === other)[1];
  if (mastered(id, strand)) return { id, strand, title, prereqs, state: "mastered", due_at: DUE.has(id) ? PAST : FUTURE, stability_days: 10 };
  if (id in LEARNING) return { id, strand, title, prereqs, state: "learning", progress: LEARNING[id] };
  return { id, strand, title, prereqs, state: prereqs.every((other) => mastered(other, strandOf(other))) ? "ready" : "locked" };
});
