"""Basic statistics: summaries, spread, z-scores, the normal rule and standard errors."""
from __future__ import annotations

from fractions import Fraction as F

from kitty.core.math.problems import Spec, Step, num, template, tex

#: (k, share within k sd, share above +k sd) as the rule states them, in percent.
NORMAL_RULE = ((1, "68", "16"), (2, "95", "2.5"), (3, "99.7", "0.15"))
Z_975 = F(196, 100)


def _data(r, size):
    return [r.randint(1, 30) for _ in range(size)]


def _list(values) -> str:
    return ", ".join(str(value) for value in values)


@template("stats.mean-median/mean", target_s=30)
def mean(r):
    data = _data(r, r.randint(5, 8))
    answer = F(sum(data), len(data))
    ordered = sorted(data)
    middle = F(ordered[len(data) // 2] + ordered[(len(data) - 1) // 2], 2)
    return Spec({"data": data}, f"What is the mean of ${_list(data)}$?", answer, [
        Step("Sum", " + ".join(map(str, data)), F(sum(data))),
        Step("Divide by the count", f"\\frac{{{sum(data)}}}{{{len(data)}}}", answer),
    ], kind="fraction", mistakes=[(middle, "That's the median; the mean averages every value."), (F(sum(data)), f"Divide the sum by the {len(data)} values.")])


@template("stats.mean-median/median", target_s=25)
def median(r):
    data = _data(r, r.randint(5, 8))
    ordered = sorted(data)
    answer = F(ordered[len(data) // 2] + ordered[(len(data) - 1) // 2], 2)
    middle = "the middle value" if len(data) % 2 else "the mean of the two middle values"
    return Spec({"data": data}, f"What is the median of ${_list(data)}$?", answer, [
        Step("Sort", _list(ordered)),
        Step(f"Take {middle}", "", answer),
    ], kind="number", mistakes=[(F(data[len(data) // 2] + data[(len(data) - 1) // 2], 2), "Sort the values before taking the middle."),
                 (F(sum(data), len(data)), "That's the mean.")])


@template("stats.spread/variance", target_s=60)
def spread(r):
    size = r.randint(4, 6)
    data = _data(r, size)
    sample = r.random() < 0.5
    centre = F(sum(data), size)
    squares = sum((x - centre) ** 2 for x in data)
    answer = squares / (size - 1 if sample else size)
    which = "sample variance (divide by $n - 1$)" if sample else "population variance (divide by $n$)"
    return Spec({"data": data, "sample": sample}, f"What is the {which} of ${_list(data)}$?", answer, [
        Step("Mean", "\\bar x", centre),
        Step("Sum of squared deviations", "\\sum (x_i - \\bar x)^2", squares),
        Step("Divide", f"\\frac{{{tex(squares)}}}{{{size - 1 if sample else size}}}", answer),
    ], sigfigs=3, mistakes=[(squares / (size if sample else size - 1), "Check the divisor: " + ("$n - 1$ for a sample." if sample else "$n$ for the population.")),
                            (squares, "Divide the squared deviations by the count.")])


@template("stats.z-score/value", target_s=20)
def z_score(r):
    mu = r.randint(10, 200)
    sigma = r.choice([2, 4, 5, 8, 10, 12, 15, 20, 25])
    z = F(r.randint(-12, 12), 4)
    x = mu + z * sigma
    return Spec({"mu": mu, "sigma": sigma, "x": x}, f"Scores have mean ${mu}$ and standard deviation ${sigma}$. What is the $z$-score of ${num(x)}$?", z, [
        Step("Distance from the mean", f"{num(x)} - {mu}", x - mu),
        Step("In standard deviations", f"\\frac{{{num(x - mu)}}}{{{sigma}}}", z),
    ], kind="number", mistakes=[(x - mu, f"Divide the distance by the standard deviation ${sigma}$."), (-z, "Check the sign: above the mean is positive.")])


@template("stats.normal-rule/share", target_s=15)
def normal_rule(r):
    k, within, above = r.choice(NORMAL_RULE)
    outside = r.random() < 0.5
    mu, sigma = r.randint(50, 150), r.choice([5, 10, 15, 20])
    shares = ["50%", "68%", "95%", "99.7%", "16%", "2.5%", "0.15%", "32%", "5%"]
    if outside:
        question = f"above ${mu + k * sigma}$"
        answer = f"{above}%"
        wrong = [f"{within}%", f"{num(100 - F(within))}%", "50%"]
    else:
        question = f"between ${mu - k * sigma}$ and ${mu + k * sigma}$"
        answer = f"{within}%"
        wrong = [f"{above}%", "50%"] + [f"{w}%" for _, w, _ in NORMAL_RULE if w != within][:1]
    pool = [answer] + [w for w in dict.fromkeys(wrong) if w != answer]
    pool += [s for s in shares if s not in pool][: 4 - len(pool)]
    choices = pool[:4]
    r.shuffle(choices)
    rule = f"About {within}% lies within {k} standard deviation{'s' if k > 1 else ''}"
    return Spec({"k": k, "outside": outside, "mu": mu, "sigma": sigma},
                f"A quantity is normally distributed with mean ${mu}$ and standard deviation ${sigma}$. About what share of values lies {question}?", answer, [
                    Step("Distance in standard deviations", f"\\frac{{{k * sigma}}}{{{sigma}}} = {k}"),
                    Step(rule + (f"; the other {num(100 - F(within))}% splits evenly between the tails" if outside else "")),
                ], kind="choice", choices=choices,
                mistakes=[(f"{within}%", "That's the share within the band; the question asks about one tail.")] if outside else
                         [(f"{above}%", "That's one tail; the band is the middle.")])


@template("stats.standard-error/mean", target_s=30)
def standard_error(r):
    sigma = r.choice([2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30])
    root = r.randint(2, 20)
    n = root * root
    answer = F(sigma, root)
    return Spec({"sigma": sigma, "n": n}, f"Each observation has standard deviation ${sigma}$. What is the standard error of the mean of ${n}$ independent observations?", answer, [
        Step("Variance of the mean", f"\\frac{{{sigma}^2}}{{{n}}}", F(sigma * sigma, n)),
        Step("Standard error", f"\\frac{{{sigma}}}{{\\sqrt{{{n}}}}}", answer),
    ], kind="fraction", mistakes=[(F(sigma, n), "Divide by $\\sqrt n$, not $n$."), (F(sigma * sigma, n), "That's the variance of the mean; take its square root.")])


@template("stats.confidence/upper-95", target_s=45)
def confidence(r):
    sigma = r.choice([4, 5, 6, 8, 10, 12, 15, 20])
    root = r.choice([4, 5, 8, 10, 12, 15, 20])
    n = root * root
    xbar = F(r.randint(200, 2000), 10)
    upper = r.random() < 0.5
    se = F(sigma, root)
    answer = xbar + Z_975 * se if upper else xbar - Z_975 * se
    end = "upper" if upper else "lower"
    return Spec({"sigma": sigma, "n": n, "xbar": xbar, "upper": upper},
                f"A sample of ${n}$ has mean ${num(xbar)}$; the population standard deviation is ${sigma}$. Using $z = 1.96$, what is the {end} end of the 95% confidence interval for the mean? (2 decimal places)",
                answer, [
                    Step("Standard error", f"\\frac{{{sigma}}}{{\\sqrt{{{n}}}}}", se),
                    Step("Margin", f"1.96 \\times {num(se)}", Z_975 * se),
                    Step(f"{end.title()} end", f"{num(xbar)} {'+' if upper else '-'} {num(Z_975 * se)}", answer),
                ], kind="number", tolerance=F(1, 100) * F(1, 2) + F(1, 1000),
                mistakes=[(xbar + (Z_975 * sigma if upper else -Z_975 * sigma), "Scale by the standard error $\\sigma/\\sqrt n$, not $\\sigma$."),
                          (xbar - Z_975 * se if upper else xbar + Z_975 * se, f"The {end} end {'adds' if upper else 'subtracts'} the margin.")])
