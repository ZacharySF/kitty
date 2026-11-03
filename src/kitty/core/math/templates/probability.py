"""Probability: equally likely outcomes through conditioning, Bayes and first-step analysis."""
from __future__ import annotations

from fractions import Fraction as F
from itertools import product
from math import comb

from kitty.core.math.problems import Spec, Step, num, template, tex

#: Simple probabilities that read well in a prompt.
NICE = (F(1, 2), F(1, 3), F(2, 3), F(1, 4), F(3, 4), F(1, 5), F(2, 5), F(3, 5), F(1, 6), F(1, 10), F(3, 10), F(7, 10))


def _p(value: F) -> str:
    return tex(value)


@template("prob.basic/dice-sum", target_s=30)
def dice_sum(r):
    m = r.choice([4, 6, 8])
    s = r.randint(3, 2 * m - 1)
    ways = sum(1 for a in range(1, m + 1) for b in range(1, m + 1) if a + b == s)
    answer = F(ways, m * m)
    return Spec({"m": m, "s": s}, f"Roll two fair {m}-sided dice. What is the probability their sum is {s}?", answer, [
        Step("Equally likely ordered outcomes", f"{m} \\times {m}", F(m * m)),
        Step(f"Pairs summing to {s}", f"(a, {s} - a) \\text{{ with both in }} 1..{m}", F(ways)),
        Step("Probability", f"\\frac{{{ways}}}{{{m * m}}}", answer),
    ], sigfigs=3, mistakes=[(F(1, 2 * m - 1), "The sums aren't equally likely; count ordered pairs.")])


@template("prob.complement/at-least-one-six", target_s=30)
def at_least_one_six(r):
    n = r.randint(2, 6)
    answer = 1 - F(5, 6) ** n
    return Spec({"n": n}, f"Roll a fair die {n} times. What is the probability of at least one six?", answer, [
        Step("No six at all", f"\\left(\\tfrac{{5}}{{6}}\\right)^{{{n}}}", F(5, 6) ** n),
        Step("Complement", f"1 - {tex(F(5, 6) ** n)}", answer),
    ], sigfigs=3, mistakes=[(F(n, 6), "Adding $\\frac16$ per roll double counts rolls with several sixes."),
                            (F(5, 6) ** n, "That's the chance of no six; take the complement.")])


@template("prob.complement/symbolic", target_s=40)
def at_least_one_symbolic(r):
    n = r.randint(2, 6)
    return Spec({"n": n}, f"Each of {n} independent trials succeeds with probability $p$. In terms of $p$, what is the probability of at least one success?",
                f"1 - (1 - p)**{n}", [
                    Step("All fail", f"(1-p)^{{{n}}}"),
                    Step("Complement", f"1 - (1-p)^{{{n}}}"),
                ], kind="expression", variables=("p",),
                mistakes=[(f"{n}*p", "Adding $p$ per trial double counts outcomes with several successes."),
                          (f"(1 - p)**{n}", "That's the chance every trial fails; take the complement."),
                          (f"p**{n}", "That's the chance every trial succeeds.")])


@template("prob.independence/union", target_s=25)
def union(r):
    a, b = r.choice(NICE), r.choice(NICE)
    answer = a + b - a * b
    return Spec({"a": a, "b": b}, f"Independent events have $P(A) = {_p(a)}$ and $P(B) = {_p(b)}$. What is $P(A \\cup B)$?", answer, [
        Step("Both, by independence", f"{_p(a)} \\cdot {_p(b)}", a * b),
        Step("Inclusion–exclusion", f"{_p(a)} + {_p(b)} - {tex(a * b)}", answer),
    ], sigfigs=3, mistakes=[(a + b, "The overlap $A \\cap B$ is counted twice; subtract $P(A)P(B)$."),
                            (a * b, "That's $P(A \\cap B)$, both at once.")])


@template("prob.hypergeometric/urn", target_s=45)
def hypergeometric(r):
    red, blue = r.randint(2, 6), r.randint(2, 6)
    k = r.randint(2, min(4, red + blue - 1))
    j = r.randint(max(0, k - blue), min(k, red))
    good = comb(red, j) * comb(blue, k - j)
    total = comb(red + blue, k)
    answer = F(good, total)
    pr = F(red, red + blue)
    return Spec({"red": red, "blue": blue, "k": k, "j": j}, f"An urn holds {red} red and {blue} blue balls. Draw {k} without replacement. What is the probability that exactly {j} are red?", answer, [
        Step("Equally likely hands", f"\\binom{{{red + blue}}}{{{k}}}", F(total)),
        Step("Hands with exactly the right reds", f"\\binom{{{red}}}{{{j}}} \\binom{{{blue}}}{{{k - j}}}", F(good)),
        Step("Probability", f"\\frac{{{good}}}{{{total}}}", answer),
    ], sigfigs=3, mistakes=[(comb(k, j) * pr ** j * (1 - pr) ** (k - j), "That's sampling with replacement; the urn changes after each draw.")])


@template("prob.conditional/dice-given", target_s=60)
def conditional_dice(r):
    n = r.choice([2, 3])
    s = r.randint(n + 2, 6 * n - 2)
    rolls = list(product(range(1, 7), repeat=n))
    given = [roll for roll in rolls if sum(roll) >= s]
    same = [roll for roll in given if len(set(roll)) == 1]
    answer = F(len(same), len(given))
    return Spec({"n": n, "s": s}, f"Roll {n} fair dice. Given the sum is at least {s}, what is the probability all dice show the same value?", answer, [
        Step(f"Outcomes with sum at least {s}", f"\\#\\{{\\text{{sum}} \\ge {s}\\}}", F(len(given))),
        Step("Of those, all equal", f"\\#\\{{\\text{{all equal, sum}} \\ge {s}\\}}", F(len(same))),
        Step("Condition", f"\\frac{{{len(same)}}}{{{len(given)}}}", answer),
    ], sigfigs=3, mistakes=[(F(6, 6 ** n), "You didn't condition on the sum: restrict to outcomes with a large enough sum.")])


@template("prob.total-probability/urns", target_s=45)
def total_probability(r):
    q = r.choice([F(1, 2), F(1, 3), F(2, 3), F(1, 4), F(3, 4)])
    r1, b1, r2, b2 = r.randint(1, 6), r.randint(1, 6), r.randint(1, 6), r.randint(1, 6)
    pa, pb = F(r1, r1 + b1), F(r2, r2 + b2)
    answer = q * pa + (1 - q) * pb
    return Spec({"q": q, "r1": r1, "b1": b1, "r2": r2, "b2": b2},
                f"Urn A holds {r1} red and {b1} blue; urn B holds {r2} red and {b2} blue. Pick urn A with probability ${_p(q)}$, otherwise urn B, then draw one ball. What is $P(\\text{{red}})$?", answer, [
                    Step("Through A", f"{_p(q)} \\cdot {_p(pa)}", q * pa),
                    Step("Through B", f"{_p(1 - q)} \\cdot {_p(pb)}", (1 - q) * pb),
                    Step("Add the branches", f"{tex(q * pa)} + {tex((1 - q) * pb)}", answer),
                ], sigfigs=3, mistakes=[(F(r1 + r2, r1 + b1 + r2 + b2), "Pooling the urns ignores how likely each urn is and their sizes."),
                                        ((pa + pb) / 2, "Weight each urn by how likely it is to be picked.")])


@template("prob.bayes/test", target_s=60)
def bayes_test(r):
    prevalence = r.choice([F(1, 100), F(2, 100), F(5, 100), F(1, 10)])
    sensitivity = r.choice([F(9, 10), F(95, 100), F(99, 100), F(8, 10)])
    false_positive = r.choice([F(5, 100), F(1, 10), F(2, 100)])
    positive = sensitivity * prevalence + false_positive * (1 - prevalence)
    answer = sensitivity * prevalence / positive
    pct = lambda value: num(value * 100)  # noqa: E731
    return Spec({"prevalence": prevalence, "sensitivity": sensitivity, "false_positive": false_positive},
                f"{pct(prevalence)}% of people have a condition. A test detects {pct(sensitivity)}% of cases and gives a false positive for {pct(false_positive)}% of everyone else. You test positive: what is $P(\\text{{condition}} \\mid +)$?",
                answer, [
                    Step("Bayes' rule", "P(C \\mid +) = \\frac{P(+ \\mid C)\\,P(C)}{P(+)}"),
                    Step("Total probability", f"P(+) = {num(sensitivity)} \\cdot {num(prevalence)} + {num(false_positive)} \\cdot {num(1 - prevalence)}", positive),
                    Step("Divide", f"\\frac{{{num(sensitivity * prevalence)}}}{{{num(positive)}}}", answer),
                ], sigfigs=3, mistakes=[(sensitivity, "That's $P(+ \\mid C)$; Bayes flips it to $P(C \\mid +)$."),
                                        (sensitivity * prevalence, "Divide by $P(+)$, the chance of any positive result.")])


@template("prob.binomial/exactly-k", target_s=45)
def binomial(r):
    n = r.randint(3, 8)
    p = r.choice([F(1, 2), F(1, 3), F(2, 3), F(1, 4)])
    k = r.randint(0, n)
    answer = comb(n, k) * p ** k * (1 - p) ** (n - k)
    return Spec({"n": n, "p": p, "k": k}, f"A coin lands heads with probability ${_p(p)}$. Flip it {n} times. What is the probability of exactly {k} heads?", answer, [
        Step("Positions of the heads", f"\\binom{{{n}}}{{{k}}}", F(comb(n, k))),
        Step("One such sequence", f"\\left({_p(p)}\\right)^{{{k}}} \\left({_p(1 - p)}\\right)^{{{n - k}}}", p ** k * (1 - p) ** (n - k)),
        Step("Multiply", f"{comb(n, k)} \\cdot {tex(p ** k * (1 - p) ** (n - k))}", answer),
    ], sigfigs=3, mistakes=[(p ** k * (1 - p) ** (n - k), f"Count the $\\binom{{{n}}}{{{k}}}$ orders the heads can come in."),
                            (comb(n, k) * p ** k, f"Include the tails: $(1-p)^{{{n - k}}}$.")])


@template("prob.geometric/first-success", target_s=30)
def geometric(r):
    m = r.choice([4, 6, 8, 10, 12, 20])
    k = r.randint(1, 6)
    p = F(1, m)
    answer = (1 - p) ** (k - 1) * p
    return Spec({"m": m, "k": k}, f"Roll a fair {m}-sided die until it shows a 1. What is the probability the first 1 comes on roll {k}?", answer, [
        Step(f"{k - 1} misses first", f"\\left({_p(1 - p)}\\right)^{{{k - 1}}}", (1 - p) ** (k - 1)),
        Step("Then a hit", f"{tex((1 - p) ** (k - 1))} \\cdot {_p(p)}", answer),
    ], sigfigs=3, mistakes=[((1 - p) ** k * p, f"Only {k - 1} rolls miss before the hit on roll {k}."),
                            (p, "Earlier rolls have to miss first.")])


@template("prob.symmetry/higher-die", target_s=30)
def symmetry(r):
    m = r.choice([4, 6, 8, 10, 12, 20])
    answer = F(m - 1, 2 * m)
    return Spec({"m": m}, f"Alice and Bob each roll a fair {m}-sided die. What is the probability Alice rolls strictly higher?", answer, [
        Step("A tie", f"\\frac{{{m}}}{{{m * m}}}", F(1, m)),
        Step("By symmetry the rest splits evenly", f"\\frac{{1}}{{2}}\\left(1 - {_p(F(1, m))}\\right)", answer),
    ], sigfigs=3, mistakes=[(F(1, 2), "Ties are possible; only the non-tied outcomes split evenly."),
                            (F(m + 1, 2 * m), "That's 'at least as high': it includes the ties.")])


@template("prob.order-stats/max-of-dice", target_s=45)
def max_of_dice(r):
    n, m = r.randint(2, 4), r.choice([4, 6, 8])
    k = r.randint(1, m)
    answer = F(k ** n - (k - 1) ** n, m ** n)
    return Spec({"n": n, "m": m, "k": k}, f"Roll {n} fair {m}-sided dice. What is the probability the largest value shown is exactly {k}?", answer, [
        Step(f"All at most {k}", f"\\left(\\tfrac{{{k}}}{{{m}}}\\right)^{{{n}}}", F(k, m) ** n),
        Step(f"All at most {k - 1}", f"\\left(\\tfrac{{{k - 1}}}{{{m}}}\\right)^{{{n}}}", F(k - 1, m) ** n),
        Step("Difference", f"{tex(F(k, m) ** n)} - {tex(F(k - 1, m) ** n)}", answer),
    ], sigfigs=3, mistakes=[(F(k, m) ** n, f"That's $P(\\max \\le {k})$; subtract $P(\\max \\le {k - 1})$."),
                            (F(1, m), "The maximum isn't uniform.")])


@template("prob.gamblers-ruin/reach-target", target_s=60)
def gamblers_ruin(r):
    target = r.randint(4, 10)
    start = r.randint(1, target - 1)
    p = r.choice([F(1, 2), F(1, 3), F(2, 5), F(3, 5), F(2, 3)])
    if p == F(1, 2):
        answer = F(start, target)
        steps = [Step("First step: $h_i = \\tfrac12 h_{i-1} + \\tfrac12 h_{i+1}$, so $h$ is linear with $h_0 = 0$, $h_N = 1$", f"h_{{{start}}} = \\frac{{{start}}}{{{target}}}", answer)]
    else:
        ratio = (1 - p) / p
        answer = (ratio ** start - 1) / (ratio ** target - 1)
        steps = [
            Step("First step: $h_i = p\\,h_{i+1} + q\\,h_{i-1}$ gives $h_i = \\frac{(q/p)^i - 1}{(q/p)^N - 1}$ with", f"q/p = {tex(ratio)}"),
            Step("Plug in", f"\\frac{{({tex(ratio)})^{{{start}}} - 1}}{{({tex(ratio)})^{{{target}}} - 1}}", answer),
        ]
    return Spec({"start": start, "target": target, "p": p},
                f"You start with {start} chips and bet 1 chip at a time, winning each bet with probability ${_p(p)}$. You stop at 0 chips or at {target}. What is the probability you reach {target}?",
                answer, steps, sigfigs=3, mistakes=[(F(start, target), "That's the fair-coin answer; with a biased bet use $(q/p)^i$.")] if p != F(1, 2) else [])
