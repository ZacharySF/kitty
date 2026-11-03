"""Counting and combinatorics."""
from __future__ import annotations

from collections import Counter
from fractions import Fraction as F
from math import comb, factorial, lcm, perm

from kitty.core.math.problems import Spec, Step, template

WORDS = ("BANANA", "LETTER", "PEPPER", "COFFEE", "TATTOO", "ASSESS", "BALLOON", "COOKBOOK", "SETTEE", "PAPAYA")


@template("count.product-rule/outfits", target_s=10)
def product_rule(r):
    s, p, h = r.randint(3, 9), r.randint(2, 6), r.randint(2, 5)
    return Spec({"s": s, "p": p, "h": h}, f"You own {s} shirts, {p} pairs of trousers and {h} hats. How many different outfits of one of each are there?", s * p * h, [
        Step("Each choice multiplies the options", f"{s} \\times {p} \\times {h}", F(s * p * h)),
    ], mistakes=[(s + p + h, "Independent choices multiply; adding counts single items, not outfits.")])


@template("count.permutations/podium", target_s=15)
def permutations(r):
    n, k = r.randint(5, 12), r.randint(2, 4)
    return Spec({"n": n, "k": k}, f"{n} runners race. In how many ways can the first {k} places be filled (no ties)?", perm(n, k), [
        Step("Order matters and nobody repeats", f"{' \\times '.join(str(n - i) for i in range(k))}", F(perm(n, k))),
    ], mistakes=[(comb(n, k), "That counts groups; here the order of the places matters."),
                 (n ** k, "That allows one runner to take several places.")])


@template("count.multisets/word", target_s=25)
def multisets(r):
    word = r.choice(WORDS)
    counts = Counter(word)
    repeats = [c for c in counts.values() if c > 1]
    denominator = 1
    for c in counts.values():
        denominator *= factorial(c)
    answer = factorial(len(word)) // denominator
    return Spec({"word": word}, f"How many distinct arrangements of the letters of **{word}** are there?", answer, [
        Step("All orders of the letters", f"{len(word)}!", F(factorial(len(word)))),
        Step("Divide out swaps of identical letters", " \\times ".join(f"{c}!" for c in repeats), F(denominator)),
        Step("Arrangements", f"\\frac{{{factorial(len(word))}}}{{{denominator}}}", F(answer)),
    ], mistakes=[(factorial(len(word)), "Identical letters swapped give the same word; divide by their factorials.")])


@template("count.combinations/committee", target_s=15)
def combinations(r):
    n, k = r.randint(6, 15), r.randint(2, 5)
    return Spec({"n": n, "k": k}, f"How many ways are there to choose a committee of {k} from {n} people?", comb(n, k), [
        Step("Ordered choices", f"{n}!/{n - k}!", F(perm(n, k))),
        Step("Divide by orderings of the committee", f"\\frac{{{perm(n, k)}}}{{{k}!}}", F(comb(n, k))),
    ], mistakes=[(perm(n, k), f"A committee has no order: divide by ${k}!$.")])


@template("count.combinations/polynomial", target_s=30)
def combinations_polynomial(r):
    k = r.choice([2, 3])
    ordered = r.random() < 0.5
    if ordered:
        answer = "n*(n-1)" if k == 2 else "n*(n-1)*(n-2)"
        prompt = f"In terms of $n$: how many ways can {k} distinct prizes go to {k} different people out of $n$?"
        steps = [Step("Choices for each prize in turn", " \\cdot ".join(["n", "(n-1)", "(n-2)"][:k]))]
        mistakes = [("n**%d" % k, "That lets one person win several prizes."),
                    ("n*(n-1)/2" if k == 2 else "n*(n-1)*(n-2)/6", "The prizes are distinct, so order matters.")]
    else:
        answer = "n*(n-1)/2" if k == 2 else "n*(n-1)*(n-2)/6"
        prompt = f"In terms of $n$: how many ways are there to choose {k} people out of $n$, i.e. $\\binom{{n}}{{{k}}}$ as a polynomial?"
        steps = [Step("Ordered choices", " \\cdot ".join(["n", "(n-1)", "(n-2)"][:k])),
                 Step("Divide by the orderings", f"{k}! = {factorial(k)}")]
        mistakes = [("n*(n-1)" if k == 2 else "n*(n-1)*(n-2)", f"Choosing ignores order: divide by ${k}!$."),
                    ("n**2/2" if k == 2 else "n**3/6", "Each later choice has one person fewer.")]
    return Spec({"k": k, "ordered": ordered}, prompt, answer, steps, kind="expression", variables=("n",), mistakes=mistakes)


@template("count.complement/at-least-one", target_s=25)
def complement(r):
    m, k = r.randint(3, 6), r.randint(2, 5)
    answer = m ** k - (m - 1) ** k
    return Spec({"m": m, "k": k}, f"Strings of length {k} use an alphabet of {m} letters, one of which is **A**. How many strings contain at least one A?", answer, [
        Step("All strings", f"{m}^{{{k}}}", F(m ** k)),
        Step("Strings with no A", f"{m - 1}^{{{k}}}", F((m - 1) ** k)),
        Step("Complement", f"{m ** k} - {(m - 1) ** k}", F(answer)),
    ], mistakes=[(k * m ** (k - 1), "Fixing an A in each position counts strings with several A's more than once."),
                 ((m - 1) ** k, "That's the strings with no A at all.")])


@template("count.inclusion-exclusion/divisible", target_s=40)
def inclusion_exclusion(r):
    n = r.randint(50, 500)
    a = r.randint(2, 9)
    b = r.choice([x for x in range(2, 10) if x != a and x % a and a % x])
    both = n // lcm(a, b)
    answer = n // a + n // b - both
    return Spec({"n": n, "a": a, "b": b}, f"How many integers from 1 to {n} are divisible by {a} or by {b}?", answer, [
        Step(f"Divisible by {a}", f"\\lfloor {n}/{a} \\rfloor", F(n // a)),
        Step(f"Divisible by {b}", f"\\lfloor {n}/{b} \\rfloor", F(n // b)),
        Step("Divisible by both (the lcm)", f"\\lfloor {n}/{lcm(a, b)} \\rfloor", F(both)),
        Step("Inclusion–exclusion", f"{n // a} + {n // b} - {both}", F(answer)),
    ], mistakes=[(n // a + n // b, "Numbers divisible by both were counted twice; subtract them once."),
                 (n // a + n // b - n // (a * b), f"Both means divisible by $\\operatorname{{lcm}}({a}, {b}) = {lcm(a, b)}$, not ${a * b}$.")])


@template("count.stars-bars/solutions", target_s=30)
def stars_bars(r):
    n, k = r.randint(4, 12), r.randint(2, 4)
    positive = r.random() < 0.5
    answer = comb(n - 1, k - 1) if positive else comb(n + k - 1, k - 1)
    variables = " + ".join(f"x_{i + 1}" for i in range(k))
    kind = "positive integers" if positive else "non-negative integers"
    steps = [Step("Give each variable 1 first, leaving", f"{n} - {k}", F(n - k))] if positive else []
    stars = n - k if positive else n
    steps.append(Step(f"Arrange {stars} stars and {k - 1} bars", f"\\binom{{{stars + k - 1}}}{{{k - 1}}}", F(answer)))
    return Spec({"n": n, "k": k, "positive": positive}, f"How many solutions in {kind} does ${variables} = {n}$ have?", answer, steps,
                mistakes=[(comb(n + k - 1, k - 1) if positive else comb(n - 1, k - 1),
                           "Check the range: zero is " + ("not allowed" if positive else "allowed") + " here."),
                          (comb(n + k - 1, k), f"Choose the positions of the ${k - 1}$ bars, not ${k}$.")])


@template("count.binomial-theorem/coefficient", target_s=40)
def binomial_theorem(r):
    n = r.randint(3, 7)
    a = r.randint(1, 3)
    b = r.choice([-3, -2, -1, 1, 2, 3])
    k = r.randint(1, n - 1)
    answer = comb(n, k) * a ** k * b ** (n - k)
    inner = f"{a if a != 1 else ''}x {'+' if b > 0 else '-'} {abs(b)}"
    return Spec({"n": n, "a": a, "b": b, "k": k}, f"What is the coefficient of $x^{{{k}}}$ in $({inner})^{{{n}}}$?", answer, [
        Step("The term with $x^k$", f"\\binom{{{n}}}{{{k}}} ({a}x)^{{{k}}} ({b})^{{{n - k}}}"),
        Step("Binomial coefficient", f"\\binom{{{n}}}{{{k}}}", F(comb(n, k))),
        Step("Multiply", f"{comb(n, k)} \\times {a ** k} \\times {b ** (n - k)}", F(answer)),
    ], mistakes=[(comb(n, k), f"Also multiply by ${a}^{{{k}}}$ and $({b})^{{{n - k}}}$."),
                 (comb(n, k) * a ** k * abs(b) ** (n - k), "Watch the sign of the constant term.")])
