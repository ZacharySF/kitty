"""Expected value, variance, stopping problems and fair prices."""
from __future__ import annotations

from fractions import Fraction as F

from kitty.core.math.problems import Spec, Step, template, tex

PATTERNS = ("HH", "HT", "HHH", "HTH", "HHT", "HTT", "THH")


def _solve(matrix: list[list[F]], rhs: list[F]) -> list[F]:
    """Exact Gaussian elimination."""
    size = len(rhs)
    rows = [row[:] + [value] for row, value in zip(matrix, rhs)]
    for col in range(size):
        pivot = next(i for i in range(col, size) if rows[i][col] != 0)
        rows[col], rows[pivot] = rows[pivot], rows[col]
        lead = rows[col][col]
        rows[col] = [value / lead for value in rows[col]]
        for i in range(size):
            if i != col and rows[i][col] != 0:
                factor = rows[i][col]
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[col])]
    return [row[-1] for row in rows]


def pattern_wait(pattern: str) -> F:
    """Expected fair-coin flips until ``pattern`` first appears (prefix-automaton states)."""
    states = len(pattern)

    def advance(matched: int, flip: str) -> int:
        text = pattern[:matched] + flip
        for length in range(min(len(text), states), -1, -1):
            if text.endswith(pattern[:length]):
                return length
        return 0

    # E_i = 1 + (E_next(H) + E_next(T)) / 2 for i < states, E_states = 0.
    matrix = [[F(0)] * states for _ in range(states)]
    rhs = [F(1)] * states
    for i in range(states):
        matrix[i][i] += 1
        for flip in "HT":
            nxt = advance(i, flip)
            if nxt < states:
                matrix[i][nxt] -= F(1, 2)
    return _solve(matrix, rhs)[0]


@template("ev.expected-value/threshold-game", target_s=40)
def threshold_game(r):
    m = r.choice([6, 8, 10, 12])
    t = r.randint(2, m - 1)
    cost = r.randint(1, 5)
    wins = sum(range(t, m + 1))
    answer = F(wins - cost * (t - 1), m)
    return Spec({"m": m, "t": t, "cost": cost}, f"Roll a fair {m}-sided die. If it shows {t} or more you win that many points; otherwise you lose {cost}. What is the expected score?", answer, [
        Step("Winning faces", f"{t} + \\dots + {m}", F(wins)),
        Step("Losing faces", f"{t - 1} \\times (-{cost})", F(-cost * (t - 1))),
        Step("Average over the faces", f"\\frac{{{wins} - {cost * (t - 1)}}}{{{m}}}", answer),
    ], kind="fraction", mistakes=[(F(wins, m), "Include the losses on the low faces."), (F(wins, m - t + 1) - cost, "Average over every face, each with probability $1/" + str(m) + "$.")])


@template("ev.expected-value/die-n", target_s=30)
def die_n(r):
    a, b = r.randint(1, 4), r.randint(0, 6)
    expression = f"{a}*(n + 1)/2 + {b}"
    shown = ("" if a == 1 else f"{a}") + "X" + (f" + {b}" if b else "")
    return Spec({"a": a, "b": b}, f"$X$ is the roll of a fair die with faces $1, \\dots, n$. In terms of $n$, what is $\\mathbb{{E}}[{shown}]$?", expression, [
        Step("Mean of a fair die", "\\mathbb{E}[X] = \\frac{1 + 2 + \\dots + n}{n} = \\frac{n+1}{2}"),
        Step("Linearity", f"\\mathbb{{E}}[{shown}] = {a if a != 1 else ''}\\,\\mathbb{{E}}[X]{f' + {b}' if b else ''}"),
    ], kind="expression", variables=("n",), mistakes=[(f"{a}*n/2 + {b}", "The faces start at 1: the mean is $(n+1)/2$."),
                                                       (f"{a}*(n + 1)/2", "Constants add straight through an expectation.")])


@template("ev.linearity/hh-pairs", target_s=40)
def hh_pairs(r):
    n = r.randint(4, 14)
    answer = F(n - 1, 4)
    return Spec({"n": n}, f"Flip a fair coin {n} times. What is the expected number of places where a head is immediately followed by another head?", answer, [
        Step("Adjacent pairs", f"{n} - 1", F(n - 1)),
        Step("Each pair is HH with probability", "\\tfrac12 \\cdot \\tfrac12", F(1, 4)),
        Step("Linearity: sum the indicators", f"{n - 1} \\cdot \\tfrac14", answer),
    ], kind="fraction", mistakes=[(F(n, 4), f"There are {n - 1} adjacent pairs, not {n}."), (F(n - 1, 2), "Each pair needs two heads: probability $\\frac14$.")])


@template("ev.linearity/distinct-faces", target_s=45)
def distinct_faces(r):
    k = r.randint(2, 5)
    miss = F(5, 6) ** k
    answer = 6 * (1 - miss)
    return Spec({"k": k}, f"Roll a fair die {k} times. What is the expected number of distinct faces that appear?", answer, [
        Step("A given face never appears", f"\\left(\\tfrac56\\right)^{{{k}}}", miss),
        Step("Six indicators, linearity", f"6\\left(1 - {tex(miss)}\\right)", answer),
    ], sigfigs=3, mistakes=[(F(k), "Repeats are possible; count each face once with an indicator."),
                            (6 * miss, "That counts the faces that never appear.")])


@template("ev.max-dice/two", target_s=45)
def max_two(r):
    m = r.choice([4, 6, 8, 10, 12, 20])
    answer = F(sum(k * (2 * k - 1) for k in range(1, m + 1)), m * m)
    return Spec({"m": m}, f"Roll two fair {m}-sided dice. What is $\\mathbb{{E}}[\\max]$?", answer, [
        Step("$P(\\max = k) = \\frac{k^2 - (k-1)^2}{m^2} = \\frac{2k - 1}{m^2}$, so", f"\\mathbb{{E}}[\\max] = \\sum_{{k=1}}^{{{m}}} \\frac{{k(2k-1)}}{{{m * m}}}", answer),
    ], sigfigs=3, mistakes=[(F(m + 1, 2), "That's the mean of one die; the maximum is larger."), (F(m), "The maximum is not always the top face.")])


@template("ev.optimal-stopping/rerolls", target_s=60)
def rerolls(r):
    m = r.choice([4, 6, 8, 10, 12, 20])
    rerolls = r.choice([1, 2])
    value = F(m + 1, 2)
    steps = [Step("No reroll left: the mean", f"\\frac{{{m} + 1}}{{2}}", value)]
    for left in range(1, rerolls + 1):
        keep = [x for x in range(1, m + 1) if x > value]
        value = F(sum(max(F(x), value) for x in range(1, m + 1)), m)
        steps.append(Step(f"With {left} reroll{'s' if left > 1 else ''}: keep {keep[0]} or more, else reroll", f"\\frac{{1}}{{{m}}}\\sum_x \\max(x, \\text{{continuation}})", value))
    plural = "reroll once" if rerolls == 1 else "reroll up to twice"
    return Spec({"m": m, "rerolls": rerolls}, f"Roll a fair {m}-sided die and score its value. You may {plural}, but you must keep the last roll. What is the game worth when played optimally?", value, steps,
                sigfigs=3, mistakes=[(F(m + 1, 2), "That ignores the option to reroll a low roll."),
                                     (F(m), "You can't always end on the top face.")])


@template("ev.variance/distribution", target_s=60)
def variance(r):
    values = sorted(r.sample(range(-3, 10), 3))
    counts = [r.randint(1, 4) for _ in values]
    total = sum(counts)
    probs = [F(c, total) for c in counts]
    mean = sum(p * v for p, v in zip(probs, values))
    square = sum(p * v * v for p, v in zip(probs, values))
    answer = square - mean * mean
    table = ", ".join(f"${v}$ with probability ${tex(p)}$" for v, p in zip(values, probs))
    return Spec({"values": values, "counts": counts}, f"$X$ takes {table}. What is $\\operatorname{{Var}}(X)$?", answer, [
        Step("Mean", "\\mathbb{E}[X]", mean),
        Step("Second moment", "\\mathbb{E}[X^2]", square),
        Step("Variance", f"{tex(square)} - \\left({tex(mean)}\\right)^2", answer),
    ], sigfigs=3, mistakes=[(square, "Subtract $\\mathbb{E}[X]^2$ from $\\mathbb{E}[X^2]$."),
                            (square - mean, "Square the mean before subtracting it.")])


@template("ev.variance-sum/linear-combination", target_s=45)
def variance_sum(r):
    m1, m2 = r.randint(1, 5), r.randint(1, 5)
    var_x, var_y = F(m1 * (m1 + 2), 12), F(m2 * (m2 + 2), 12)  # uniform on 0..m
    a, b, c = r.choice([-3, -2, -1, 2, 3]), r.choice([-3, -2, -1, 1, 2, 3]), r.randint(-5, 5)
    answer = a * a * var_x + b * b * var_y
    expr = f"{a}X {'+' if b > 0 else '-'} {abs(b) if abs(b) != 1 else ''}Y {'+' if c >= 0 else '-'} {abs(c)}"
    return Spec({"m1": m1, "m2": m2, "a": a, "b": b, "c": c},
                f"$X$ and $Y$ are independent with $\\operatorname{{Var}}(X) = {tex(var_x)}$ and $\\operatorname{{Var}}(Y) = {tex(var_y)}$. What is $\\operatorname{{Var}}({expr})$?", answer, [
                    Step("Scaling squares the factor", f"{a}^2 \\cdot {tex(var_x)}", a * a * var_x),
                    Step("The other term", f"({b})^2 \\cdot {tex(var_y)}", b * b * var_y),
                    Step("Independent variances add; constants vanish", f"{tex(a * a * var_x)} + {tex(b * b * var_y)}", answer),
                ], sigfigs=3, mistakes=[(a * var_x + b * var_y, "Variance scales by the square of the factor."),
                                        (a * a * var_x - b * b * var_y if b < 0 else answer, "Variances add even when the variables are subtracted."),
                                        (answer + c, "Adding a constant doesn't change variance.")])


@template("ev.variance-sum/symbolic", target_s=45)
def variance_symbolic(r):
    a, b = r.choice([2, 3, 4, 5]), r.choice([1, 2, 3])
    return Spec({"a": a, "b": b}, f"$X$ and $Y$ are independent with $\\operatorname{{Var}}(X) = u$ and $\\operatorname{{Var}}(Y) = v$. In terms of $u$ and $v$, what is $\\operatorname{{Var}}({a}X - {b if b != 1 else ''}Y)$?",
                f"{a * a}*u + {b * b}*v", [
                    Step("Scaling squares the factor", f"\\operatorname{{Var}}({a}X) = {a * a}u"),
                    Step("Independent variances add, even for a difference", f"{a * a}u + {b * b}v"),
                ], kind="expression", variables=("u", "v"),
                mistakes=[(f"{a * a}*u - {b * b}*v", "Variances add even when the variables are subtracted."),
                          (f"{a}*u + {b}*v", "Variance scales by the square of the factor."),
                          (f"{a}*u - {b}*v", "Square the factors and add the variances.")])


@template("ev.waiting-time/until-face", target_s=20)
def until_face(r):
    m = r.choice([4, 6, 8, 10, 12, 20])
    k = r.randint(1, 3)
    p = F(k, m)
    answer = 1 / p
    faces = "a 1" if k == 1 else f"one of {', '.join(str(i) for i in range(1, k))} or {k}"
    return Spec({"m": m, "k": k}, f"Roll a fair {m}-sided die until it shows {faces}. What is the expected number of rolls, including the last?", answer, [
        Step("Success per roll", f"\\frac{{{k}}}{{{m}}}", p),
        Step("Geometric mean $1/p$", f"\\frac{{1}}{{{tex(p)}}}", answer),
    ], kind="fraction", mistakes=[(answer - 1, "Count the successful roll too."), (p, "That's the success probability; the wait is $1/p$.")])


@template("ev.waiting-time/coin-pattern", target_s=60)
def coin_pattern(r):
    pattern = r.choice(PATTERNS)
    answer = pattern_wait(pattern)
    return Spec({"pattern": pattern}, f"Flip a fair coin until the pattern **{pattern}** first appears. What is the expected number of flips?", answer, [
        Step("States: how much of the pattern you've matched; first-step equations $E_i = 1 + \\tfrac12 E_{\\text{after }H} + \\tfrac12 E_{\\text{after }T}$, solved from the start", "E_0", answer),
    ], mistakes=[(F(2 ** len(pattern)), "Overlaps matter: a failed match can keep a useful prefix (compare HH with HT)."),
                 (F(2 * len(pattern)), "Each extra symbol doubles the wait, not adds two.")])


@template("ev.fair-price/break-even", target_s=40)
def fair_price(r):
    p = r.choice([F(1, 2), F(1, 3), F(1, 4), F(1, 5), F(2, 5), F(1, 6), F(3, 10)])
    loss = r.randint(1, 12)
    answer = loss * (1 - p) / p
    return Spec({"p": p, "loss": loss}, f"A bet loses {loss} points with probability ${tex(1 - p)}$ and otherwise wins $W$ points. What value of $W$ makes the bet fair (expected value zero)?", answer, [
        Step("Expected value", f"{tex(p)}\\,W - {tex(1 - p)} \\cdot {loss} = 0"),
        Step("Solve", f"W = \\frac{{{tex(1 - p)} \\cdot {loss}}}{{{tex(p)}}}", answer),
    ], kind="fraction", mistakes=[(F(loss), "Equal stakes are only fair at even odds."), (loss * p / (1 - p), "The win has to make up for the more likely loss: $W = L(1-p)/p$.")])

