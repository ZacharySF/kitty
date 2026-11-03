"""Arithmetic and estimation: the fast exact arithmetic quant screens test."""
from __future__ import annotations

from fractions import Fraction as F
from math import gcd

from kitty.core.math.problems import Spec, Step, num, template


def _digitwise(a: int, b: int, op) -> int:
    """Combine a and b digit by digit with no carries or borrows (a classic slip)."""
    width = max(len(str(a)), len(str(b)))
    da, db = str(a).rjust(width, "0"), str(b).rjust(width, "0")
    return int("".join(str(op(int(x), int(y))) for x, y in zip(da, db)))


@template("arith.add-sub/three-digit", target_s=8)
def add_sub(r):
    a, b = r.randint(100, 999), r.randint(100, 999)
    while b == a:
        b = r.randint(100, 999)
    if r.random() < 0.5:
        hundreds = (a // 100 + b // 100) * 100
        rest = a % 100 + b % 100
        return Spec({"a": a, "b": b, "op": "+"}, f"${a} + {b} = ?$", a + b, [
            Step("Hundreds", f"{a // 100 * 100} + {b // 100 * 100}", F(hundreds)),
            Step("The rest", f"{a % 100} + {b % 100}", F(rest)),
            Step("Add", f"{hundreds} + {rest}", F(a + b)),
        ], mistakes=[(_digitwise(a, b, lambda x, y: (x + y) % 10), "A carry was dropped: carry the 1 into the next column.")])
    a, b = max(a, b), min(a, b)
    return Spec({"a": a, "b": b, "op": "-"}, f"${a} - {b} = ?$", a - b, [
        Step("Take the hundreds", f"{a} - {b // 100 * 100}", F(a - b // 100 * 100)),
        Step("Then the rest", f"{a - b // 100 * 100} - {b % 100}", F(a - b)),
    ], mistakes=[(_digitwise(a, b, lambda x, y: abs(x - y)), "Looks like a borrow was missed: subtracting the smaller digit from the larger in each column isn't subtraction.")])


@template("arith.times-tables/to-20", target_s=4)
def times_tables(r):
    a, b = r.randint(2, 20), r.randint(2, 20)
    big, small = max(a, b), min(a, b)
    steps = [Step("Product", f"{a} \\times {b}", F(a * b))]
    if big > 10:
        steps = [Step("Split the larger factor", f"10 \\times {small} + {big - 10} \\times {small}"),
                 Step("Add", f"{10 * small} + {(big - 10) * small}", F(a * b))]
    return Spec({"a": a, "b": b}, f"${a} \\times {b} = ?$", a * b, steps,
                mistakes=[(a * (b - 1), f"That's ${a} \\times {b - 1}$: one row short."),
                          (a * (b + 1), f"That's ${a} \\times {b + 1}$: one row over."),
                          (a + b, "That's the sum, not the product.")])


@template("arith.mul-2x1/split", target_s=8)
def mul_2x1(r):
    a, b = r.randint(12, 99), r.randint(3, 9)
    tens, ones = a // 10 * 10, a % 10
    return Spec({"a": a, "b": b}, f"${a} \\times {b} = ?$", a * b, [
        Step("Tens", f"{tens} \\times {b}", F(tens * b)),
        Step("Ones", f"{ones} \\times {b}", F(ones * b)),
        Step("Add", f"{tens * b} + {ones * b}", F(a * b)),
    ], mistakes=[(tens * b + ones, f"Multiply the ones digit too: ${ones} \\times {b}$."),
                 (_digitwise(a, b, lambda x, y: (x * y) % 10), "Carry the tens of each partial product.")])


@template("arith.mul-2x2/split", target_s=20)
def mul_2x2(r):
    a, b = r.randint(11, 99), r.randint(11, 99)
    bt, bo = b // 10 * 10, b % 10
    return Spec({"a": a, "b": b}, f"${a} \\times {b} = ?$", a * b, [
        Step("Split the second factor", f"{a} \\times {bt}", F(a * bt)),
        Step("The ones", f"{a} \\times {bo}", F(a * bo)),
        Step("Add", f"{a * bt} + {a * bo}", F(a * b)),
    ], mistakes=[((a // 10) * (b // 10) * 100 + (a % 10) * (b % 10), "You multiplied tens by tens and ones by ones but skipped the cross terms.")])


@template("arith.squares/near-base", target_s=12)
def squares(r):
    n = r.randint(11, 99)
    base = (n + 5) // 10 * 10
    d = n - base
    tens, ones = n // 10, n % 10
    return Spec({"n": n}, f"${n}^2 = ?$", n * n, [
        Step("Use $(b + d)^2 = b^2 + 2bd + d^2$ with", f"b = {base},\\ d = {d}"),
        Step("Square the base", f"{base}^2", F(base * base)),
        Step("Cross term", f"2 \\times {base} \\times {num(d)}", F(2 * base * d)),
        Step("Add $d^2$", f"{base * base} + {2 * base * d} + {d * d}", F(n * n)),
    ], mistakes=[((tens * 10) ** 2 + ones ** 2, "Missing the cross term $2ab$: $(a+b)^2 \\ne a^2 + b^2$."), (2 * n, f"That's $2 \\times {n}$, not ${n}^2$.")])


@template("arith.division/exact", target_s=8)
def division(r):
    b, q = r.randint(3, 12), r.randint(12, 99)
    a = b * q
    return Spec({"a": a, "b": b}, f"${a} \\div {b} = ?$", q, [
        Step("Largest easy multiple", f"{b} \\times {q // 10 * 10}", F(b * (q // 10 * 10))),
        Step("What's left", f"{a} - {b * (q // 10 * 10)}", F(a - b * (q // 10 * 10))),
        Step("Divide the rest", f"{a - b * (q // 10 * 10)} \\div {b}", F(q % 10)),
        Step("Add the parts", f"{q // 10 * 10} + {q % 10}", F(q)),
    ])


@template("arith.powers-of-two/power", target_s=5)
def powers_of_two(r):
    k = r.randint(3, 20)
    return Spec({"k": k}, f"$2^{{{k}}} = ?$", 2 ** k, [
        Step("Anchor", "2^{10}", F(1024)) if k >= 10 else Step("Double up", f"2^{{{k - 1}}}", F(2 ** (k - 1))),
        Step("Then", f"2^{{10}} \\times 2^{{{k - 10}}}" if k >= 10 else f"2 \\times {2 ** (k - 1)}", F(2 ** k)),
    ], mistakes=[(2 * k, f"That's $2 \\times {k}$; $2^{{{k}}}$ doubles {k} times."), (2 ** (k - 1), f"One doubling short: that's $2^{{{k - 1}}}$."),
                 (k ** 2, f"That's ${k}^2$.")])


@template("arith.fractions/add-sub", target_s=25)
def fractions(r):
    b, d = r.randint(2, 12), r.randint(2, 12)
    a, c = r.randint(1, b - 1) if b > 1 else 1, r.randint(1, d - 1) if d > 1 else 1
    op = r.choice("+-")
    x, y = F(a, b), F(c, d)
    answer = x + y if op == "+" else x - y
    common = b * d // gcd(b, d)
    return Spec({"a": a, "b": b, "c": c, "d": d, "op": op}, f"$\\frac{{{a}}}{{{b}}} {op} \\frac{{{c}}}{{{d}}} = ?$ Give an exact fraction.", answer, [
        Step("Common denominator", f"\\operatorname{{lcm}}({b}, {d})", F(common)),
        Step("Rewrite", f"\\frac{{{a * common // b}}}{{{common}}} {op} \\frac{{{c * common // d}}}{{{common}}}"),
        Step("Combine and reduce", "", answer),
    ], kind="fraction", mistakes=[(F(a + c, b + d) if op == "+" else F(a - c, b - d) if b != d else answer,
                  "Adding numerators and denominators separately doesn't combine fractions; use a common denominator.")])


@template("arith.frac-decimal/terminating", target_s=10)
def frac_decimal(r):
    d = r.choice([4, 8, 16, 20, 25, 40, 50, 80, 125])
    n = r.choice([k for k in range(1, d) if gcd(k, d) == 1])
    value = F(n, d)
    scale = 1000 if d in (8, 125, 40) else 10000 if d in (16, 80) else 100
    return Spec({"n": n, "d": d}, f"Write $\\frac{{{n}}}{{{d}}}$ as a decimal.", value, [
        Step("Scale to a power of ten", f"\\frac{{{n}}}{{{d}}} = \\frac{{{n * scale // d}}}{{{scale}}}"),
        Step("Read the decimal", "", value),
    ], kind="number", mistakes=[(F(d, n) if n else value, "That's the reciprocal: divide the numerator by the denominator.")])


@template("arith.percentages/of", target_s=12)
def percentages(r):
    p = r.choice([F(5), F(10), F(25, 2), F(15), F(20), F(25), F(30), F(35), F(40), F(45), F(60), F(75), F(80), F(150)])
    x = r.choice([40, 60, 80, 120, 160, 240, 360, 480, 640, 800, 1200])
    answer = p * x / 100
    return Spec({"p": p, "x": x}, f"What is ${num(p)}\\%$ of ${x}$?", answer, [
        Step("One percent", f"{x} \\div 100", F(x, 100)),
        Step("Scale", f"{num(F(x, 100))} \\times {num(p)}", answer),
    ], kind="number", mistakes=[(p * x / 10, "Percent means per hundred: divide by 100, not 10.")])


@template("arith.percentages/change", target_s=15)
def percent_change(r):
    old = r.choice([20, 25, 40, 50, 80, 120, 160, 200, 250, 400])
    pct = r.choice([-50, -40, -25, -20, -10, 10, 15, 20, 25, 30, 50, 75])
    new = F(old * (100 + pct), 100)
    answer = (new - old) / old * 100
    return Spec({"old": old, "new": new}, f"A price moves from ${old}$ to ${num(new)}$. What is the percentage change? (Negative for a fall.)", answer, [
        Step("Change", f"{num(new)} - {old}", new - old),
        Step("Relative to the start", f"\\frac{{{num(new - old)}}}{{{old}}} \\times 100", answer),
    ], kind="number", mistakes=[((new - old) / new * 100, "Divide by the starting value, not the new one."),
                 (new - old, "That's the change in price, not in percent.")])


@template("arith.estimation/divide-2sf", target_s=20)
def estimation(r):
    b = r.choice([7, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97])
    a = r.randint(150, 9999)
    while a % b == 0:
        a += 1
    value = F(a, b)
    return Spec({"a": a, "b": b}, f"Estimate ${a} \\div {b}$ to 2 significant figures.", value, [
        Step("Bracket it", f"{b} \\times {a // b} = {b * (a // b)} \\le {a} < {b * (a // b + 1)}"),
        Step("The quotient, then round", f"{a} \\div {b}", value),
    ], kind="fraction", sigfigs=2, mistakes=[(F(b, a), "That's the reciprocal.")])


@template("arith.odds/to-probability", target_s=12)
def odds(r):
    a, b = r.randint(1, 9), r.randint(1, 9)
    while gcd(a, b) != 1 or a == b:
        a, b = r.randint(1, 9), r.randint(1, 9)
    against = r.random() < 0.5
    answer = F(b, a + b) if against else F(a, a + b)
    side = "against" if against else "in favour of"
    return Spec({"a": a, "b": b, "against": against}, f"The odds {side} an event are ${a}:{b}$. What is its probability?", answer, [
        Step("Odds $x:y$ in favour means $x$ chances in $x + y$", f"{b if against else a} \\text{{ of }} {a + b}"),
        Step("Probability", f"\\frac{{{b if against else a}}}{{{a + b}}}", answer),
    ], sigfigs=3, mistakes=[(F(b, a) if against else F(a, b), "Odds aren't a probability: $x:y$ means $x/(x+y)$."),
                            (F(a, a + b) if against else F(b, a + b), f"Check the side: these odds are {side} the event.")])

