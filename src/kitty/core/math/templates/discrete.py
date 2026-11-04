"""Discrete math for tech: modular arithmetic, recurrences, big-O, loop counts and graph counting."""
from __future__ import annotations

from fractions import Fraction as F
from math import comb, gcd, lcm, perm

from kitty.core.math.problems import Spec, Step, template


def _paren(value) -> str:
    return f"({value})" if value < 0 else str(value)


def _lin(terms) -> str:
    """TeX for a sum of ``(coefficient, symbol)`` terms, e.g. ``2a_{n-1} - a_{n-2}``."""
    out = ""
    for c, symbol in terms:
        if c == 0:
            continue
        piece = f"{'' if abs(c) == 1 and symbol else abs(c)}{symbol}"
        out += ("-" if c < 0 else "") + piece if not out else f" {'-' if c < 0 else '+'} {piece}"
    return out or "0"


@template("disc.mod-arith/expression", target_s=30)
def mod_arith(r):
    n = r.randint(5, 30)
    a, b, c = r.randint(10, 99), r.randint(10, 99), r.randint(10, 999)
    minus = r.random() < 0.4
    sign = -1 if minus else 1
    value = a * b + sign * c
    answer = value % n
    small = a % n * (b % n)
    partial = small % n + sign * (c % n)
    op = "-" if minus else "+"
    truncated = value - n * int(value / n)  # C's % keeps the sign of a negative value
    return Spec({"a": a, "b": b, "c": c, "n": n, "minus": minus},
                f"Compute $({a} \\cdot {b} {op} {c}) \\bmod {n}$, as a value from $0$ to ${n - 1}$.", answer, [
                    Step("Reduce the factors first", f"{a} \\cdot {b} \\equiv {a % n} \\cdot {b % n}", F(small)),
                    Step("Reduce the product", f"{small} \\bmod {n}", F(small % n)),
                    Step(f"{'Subtract' if minus else 'Add'} ${c} \\equiv {c % n}$", f"{small % n} {op} {c % n}", F(partial)),
                    Step(f"Bring it into $0, \\dots, {n - 1}$", f"{partial} \\bmod {n}", F(answer)),
                ], mistakes=[(value, f"Reduce mod ${n}$: the answer must lie between $0$ and ${n - 1}$."),
                             (truncated, f"A negative remainder isn't reduced yet: add ${n}$."),
                             (small % n + sign * c, f"Reduce ${c}$ mod ${n}$ as well.")])


@template("disc.mod-power/repeated-squaring", target_s=90)
def mod_power(r):
    n = r.randint(7, 60)
    a = r.choice([x for x in range(2, 41) if x % n > 1])
    k = r.randint(13, 100)
    bits = [i for i in range(k.bit_length()) if k >> i & 1]
    squares = [a % n]
    for _ in range(k.bit_length() - 1):
        squares.append(squares[-1] ** 2 % n)
    used = [squares[i] for i in bits]
    product = 1
    for value in used:
        product *= value
    answer = product % n
    steps = [Step("Write the exponent in binary", f"{k} = {' + '.join(str(1 << i) for i in reversed(bits))}"),
             Step("Reduce the base", f"{a} \\bmod {n}", F(squares[0]))]
    steps += [Step(f"Square for ${a}^{{{1 << i}}}$", f"{squares[i - 1]}^2 \\bmod {n}", F(squares[i])) for i in range(1, len(squares))]
    steps.append(Step("Multiply the squares the binary digits pick", f"{' \\cdot '.join(map(str, reversed(used)))} \\bmod {n}", F(answer)))
    return Spec({"a": a, "k": k, "n": n}, f"Compute ${a}^{{{k}}} \\bmod {n}$ by repeated squaring.", answer, steps,
                mistakes=[(product, f"Reduce the final product mod ${n}$ too."),
                          (a * k % n, f"A power multiplies ${a}$ by itself ${k}$ times; ${a} \\cdot {k}$ is a product."),
                          (pow(a, k % n, n), f"Exponents don't reduce mod ${n}$ (Fermat and Euler reduce them mod $\\varphi(n)$).")])


@template("disc.gcd/euclid", target_s=60)
def gcd_euclid(r):
    while True:
        g = r.choice([1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 15, 17, 18, 21, 24])
        x, y = r.randint(8, 120), r.randint(5, 100)
        a, b = max(g * x, g * y), min(g * x, g * y)
        remainders, p, q = [], a, b
        while q:
            p, q = q, p % q
            remainders.append(q)
        if len(remainders) >= 3 and a <= 2000:
            break
    answer = gcd(a, b)
    steps, p, q = [], a, b
    while q:
        steps.append(Step(f"${p} = {p // q} \\cdot {q} + r$, so", f"r = {p} \\bmod {q}", F(p % q)))
        p, q = q, p % q
    steps.append(Step("The last non-zero remainder", f"\\gcd({a}, {b})", F(answer)))
    return Spec({"a": a, "b": b}, f"Use Euclid's algorithm to find $\\gcd({a}, {b})$.", answer, steps,
                mistakes=[(0, "The final remainder is $0$; the gcd is the last non-zero remainder before it."),
                          (remainders[-3], "You stopped one step early: keep dividing until the remainder is $0$."),
                          (lcm(a, b), "That's the least common multiple; the gcd divides both numbers.")])


@template("disc.mod-inverse/extended-euclid", target_s=120)
def mod_inverse(r):
    n = r.randint(11, 97)
    a = r.choice([x for x in range(2, n - 1) if gcd(x, n) == 1])
    # Invariant: each remainder r_i ≡ t_i · a (mod n); the row with remainder 1 gives the inverse.
    steps = [Step(f"$\\gcd({a}, {n}) = 1$, so the inverse exists; track $t$ with remainder $\\equiv t \\cdot {a}$, starting from",
                  "t_0 = 0,\\ t_1", F(1))]
    r0, r1, t0, t1 = n, a, 0, 1
    while r1 != 1:
        q = r0 // r1
        steps.append(Step(f"${r0} = {q} \\cdot {r1} + {r0 - q * r1}$, so ${r0 - q * r1} \\equiv t \\cdot {a}$ with",
                          f"t = {t0} - {q} \\cdot {_paren(t1)}", F(t0 - q * t1)))
        r0, r1, t0, t1 = r1, r0 - q * r1, t1, t0 - q * t1
    answer = t1 % n
    steps.append(Step(f"Reduce $t$ into $0, \\dots, {n - 1}$", f"{t1} \\bmod {n}", F(answer)))
    return Spec({"a": a, "n": n}, f"Find $x$ with $0 \\le x < {n}$ and ${a}x \\equiv 1 \\pmod{{{n}}}$, i.e. ${a}^{{-1}} \\bmod {n}$.", answer, steps,
                mistakes=[(t1, f"Reduce $t$ mod ${n}$: add ${n}$ to make it non-negative."),
                          ((-t1) % n, "Check the sign of $t$: that's the inverse of $-" + str(a) + "$."),
                          (F(1, a), f"A modular inverse is a whole number $x$ with ${a}x \\equiv 1$, not a fraction.")])


@template("disc.recurrence/iterate", target_s=60)
def recurrence(r):
    rc, sc = r.choice([-2, -1, 1, 2, 3]), r.choice([-2, -1, 1, 2, 3])
    a0, a1, n = r.randint(-3, 5), r.randint(-3, 5), r.randint(5, 8)
    seq, swapped = [a0, a1], [a0, a1]
    for _ in range(2, n + 2):
        seq.append(rc * seq[-1] + sc * seq[-2])
        swapped.append(sc * swapped[-1] + rc * swapped[-2])
    steps = [Step(f"$a_{{{i}}}$", f"{rc} \\cdot {_paren(seq[i - 1])} + {sc} \\cdot {_paren(seq[i - 2])}", F(seq[i])) for i in range(2, n + 1)]
    rec = _lin([(rc, "a_{n-1}"), (sc, "a_{n-2}")])
    return Spec({"r": rc, "s": sc, "a0": a0, "a1": a1, "n": n},
                f"A sequence has $a_0 = {a0}$, $a_1 = {a1}$ and $a_n = {rec}$ for $n \\ge 2$. What is $a_{{{n}}}$?", seq[n], steps,
                mistakes=[(seq[n - 1], f"That's $a_{{{n - 1}}}$: the sequence starts at $a_0$, so count one more term."),
                          (seq[n + 1], f"That's $a_{{{n + 1}}}$, one term too far."),
                          (swapped[n], f"The coefficient ${rc}$ goes with $a_{{n-1}}$ and ${sc}$ with $a_{{n-2}}$.")])


def _closed(terms) -> str:
    """SymPy text for Σ c·base**n, e.g. ``3*2**n - 3**n``."""
    pieces = []
    for c, base in terms:
        power = "" if base == 1 else f"{_paren(base)}**n"
        coefficient = str(c) if not power else "" if c == 1 else "-" if c == -1 else f"{c}*"
        pieces.append(coefficient + power)
    return " + ".join(pieces).replace("+ -", "- ")


@template("disc.recurrence-closed/characteristic", target_s=180)
def recurrence_closed(r):
    p, q = sorted(r.sample([-3, -2, -1, 1, 2, 3, 4], 2), reverse=True)
    A, B = r.choice([-3, -2, -1, 1, 2, 3, 4]), r.choice([-3, -2, -1, 1, 2, 3, 4])
    rc, sc = p + q, -p * q
    a0, a1 = A + B, A * p + B * q
    answer = [(A, p), (B, q)]

    def values(terms):
        return [sum(c * base ** k for c, base in terms) for k in range(4)]
    mistakes = []
    for terms, message in (([(B, p), (A, q)], "The coefficients are swapped: fit $\\alpha$ to the root $x_1$ from $a_0$ and $a_1$."),
                           ([(A, -p), (B, -q)], "Check the signs: the roots solve $x^2 = r x + s$ for $a_n = r a_{n-1} + s a_{n-2}$."),
                           ([(1, p), (1, q)], "Fit the coefficients to $a_0$ and $a_1$; $x_1^n + x_2^n$ is only the general shape.")):
        if values(terms) != values(answer):
            mistakes.append((_closed(terms), message))
    rec = _lin([(rc, "a_{n-1}"), (sc, "a_{n-2}")])
    return Spec({"r": rc, "s": sc, "a0": a0, "a1": a1},
                f"The recurrence $a_n = {rec}$ for $n \\ge 2$, with $a_0 = {a0}$ and $a_1 = {a1}$, has distinct integer characteristic roots. "
                "Give a closed form for $a_n$ in terms of $n$.", _closed(answer), [
                    Step("Try $a_n = x^n$: the characteristic equation", f"{_lin([(1, 'x^2'), (-rc, 'x'), (-sc, '')])} = 0"),
                    Step("Its larger root", "x_1", F(p)),
                    Step("Its smaller root", "x_2", F(q)),
                    Step(f"Fit $a_n = \\alpha x_1^n + \\beta x_2^n$ to $\\alpha + \\beta = {a0}$ and ${_lin([(p, '\\alpha'), (q, '\\beta')])} = {a1}$", "\\alpha", F(A)),
                    Step("and", "\\beta", F(B)),
                ], kind="expression", variables=("n",), mistakes=mistakes)


#: shape → (Python loop nest, inner count for a given i, the sum as TeX, its closed form, count, first pass).
LOOPS = {
    "square": ("for i in range(n):\n    for j in range(n):\n        count += 1", "n",
               lambda n: f"{n} \\cdot {n}", lambda n: n * n, lambda n: n),
    "below": ("for i in range(n):\n    for j in range(i):\n        count += 1", "i",
              lambda n: f"0 + 1 + \\dots + {n - 1} = \\binom{{{n}}}{{2}}", lambda n: comb(n, 2), lambda n: 0),
    "after": ("for i in range(n):\n    for j in range(i + 1, n):\n        count += 1", "n - i - 1",
              lambda n: f"{n - 1} + \\dots + 1 + 0 = \\binom{{{n}}}{{2}}", lambda n: comb(n, 2), lambda n: n - 1),
    "from": ("for i in range(n):\n    for j in range(i, n):\n        count += 1", "n - i",
             lambda n: f"{n} + {n - 1} + \\dots + 1 = \\binom{{{n + 1}}}{{2}}", lambda n: comb(n + 1, 2), lambda n: n),
    "inclusive": ("for i in range(1, n + 1):\n    for j in range(1, i + 1):\n        count += 1", "i",
                  lambda n: f"1 + 2 + \\dots + {n} = \\binom{{{n + 1}}}{{2}}", lambda n: comb(n + 1, 2), lambda n: 1),
    "triple": ("for i in range(n):\n    for j in range(i + 1, n):\n        for k in range(j + 1, n):\n            count += 1",
               "\\binom{n - i - 1}{2}", lambda n: f"\\#\\{{i < j < k\\}} = \\binom{{{n}}}{{3}}", lambda n: comb(n, 3), lambda n: comb(n - 1, 2)),
    "triple-upto": ("for i in range(n):\n    for j in range(i + 1):\n        for k in range(j + 1):\n            count += 1",
                    "\\binom{i + 2}{2}", lambda n: f"\\sum_{{i=0}}^{{{n - 1}}} \\binom{{i + 2}}{{2}} = \\binom{{{n + 2}}}{{3}}",
                    lambda n: comb(n + 2, 3), lambda n: 1),
}


def _loop_mistakes(shape, n):
    half, half_up = comb(n, 2), comb(n + 1, 2)
    square = "The inner loop's range depends on $i$, so it doesn't run $n$ times on every pass."
    return {
        "square": [(half, "Both loops run over all of `range(n)`: there's no triangle here."),
                   (2 * n, "Nested loops multiply their counts; they don't add.")],
        "below": [(half_up, "Off by one: `range(i)` stops before $i$, so $j$ takes $i$ values."), (n * n, square),
                  (n * (n - 1), "Halve it: the inner counts are $0, 1, \\dots, n-1$.")],
        "after": [(half_up, "Off by one: `range(i + 1, n)` leaves out $j = i$."), (n * n, square),
                  (n * (n - 1), "Halve it: the inner counts are $n-1, \\dots, 1, 0$.")],
        "from": [(half, "Off by one: `range(i, n)` includes $j = i$."), (n * n, square)],
        "inclusive": [(half, "Off by one: `range(1, i + 1)` includes $j = i$."), (n * n, square)],
        "triple": [(n ** 3, "Each loop starts after the previous index, so the counts shrink."),
                   (perm(n, 3), "The indices come out in increasing order: each set of three is counted once, not $3!$ times.")],
        "triple-upto": [(comb(n, 3), "`range(j + 1)` includes $k = j$: indices may repeat, so it's $\\binom{n+2}{3}$."),
                        (n ** 3, "Each inner range stops at the outer index, so the counts shrink.")],
    }[shape]


@template("disc.loop-count/nested", target_s=60)
def loop_count(r):
    shape = r.choice(sorted(LOOPS))
    n = r.randint(5, 30)
    code, inner, total, count, first = LOOPS[shape]
    start = 1 if shape == "inclusive" else 0
    return Spec({"shape": shape, "n": n}, f"With $n = {n}$ and `count = 0` beforehand, what is `count` after this runs?\n\n```python\n{code}\n```",
                count(n), [
                    Step(f"For a given $i$ the inner loop{'s run' if shape.startswith('triple') else ' runs'}", f"{inner} \\text{{ times}}"),
                    Step(f"On the first pass ($i = {start}$)", "", F(first(n))),
                    Step("Add them up", total(n), F(count(n))),
                ], mistakes=_loop_mistakes(shape, n))


#: Growth classes n^k (log n)^l, slowest first.
ORDER = ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0))


def _term(k, l) -> str:
    n = "" if k == 0 else "n" if k == 1 else f"n^{k}"
    return (n + ("\\log n" if l else "")) or "1"


def _theta(cls) -> str:
    return f"$\\Theta({_term(*cls)})$"


def _growth_choices(answer, wrong):
    """Four classes in growth order: the answer, the mistake and the nearest neighbours."""
    pool, at, step = {answer, wrong}, ORDER.index(answer), 1
    while len(pool) < 4:
        for index in (at + step, at - step):
            if 0 <= index < len(ORDER) and len(pool) < 4:
                pool.add(ORDER[index])
        step += 1
    return [_theta(cls) for cls in ORDER if cls in pool]


@template("disc.big-o/cost", target_s=30)
def big_o_cost(r):
    classes = r.sample(ORDER, 3)
    top = max(classes, key=ORDER.index)
    terms = tuple((r.randint(1, 9) if cls == top else r.randint(2, 500), *cls) for cls in classes)
    biggest = max(terms, key=lambda term: term[0])
    second = max((cls for cls in classes if cls != top), key=ORDER.index)
    wrong = biggest[1:] if biggest[1:] != top else second
    c = next(term[0] for term in terms if term[1:] == top)
    cost = " + ".join(f"{'' if coefficient == 1 and (k or l) else coefficient}{_term(k, l) if k or l else ''}" for coefficient, k, l in terms)
    return Spec({"terms": terms}, f"An algorithm performs $T(n) = {cost}$ basic steps on an input of size $n$. Which tight bound describes $T(n)$?",
                _theta(top), [
                    Step("Each extra factor of $n$ beats any power of $\\log n$, and $\\log n$ beats a constant; constant factors don't matter"),
                    Step("Power of $n$ in the fastest-growing term", "", F(top[0])),
                    Step("Factors of $\\log n$ in that term", "", F(top[1])),
                    Step(f"Drop the constant factor ${c}$", f"T(n) = \\Theta({_term(*top)})"),
                ], kind="choice", choices=_growth_choices(top, wrong),
                mistakes=[(_theta(wrong), "For large $n$ the fastest-growing term dominates, whatever the coefficients.")])


#: shape → (Python loop nest, (k, l), a tempting wrong class, why it's wrong, how each loop runs).
BIG_O_LOOPS = {
    "single": ("for i in range(n):\n    count += 1", (1, 0), (2, 0), "One loop over $n$ items is linear.",
               "One loop of $n$ passes"),
    "sequential": ("for i in range(n):\n    count += 1\nfor j in range(n):\n    count += 1", (1, 0), (2, 0),
                   "Loops one after the other add: $n + n = 2n$.", "Two loops in sequence: $n + n$ passes"),
    "nested": ("for i in range(n):\n    for j in range(n):\n        count += 1", (2, 0), (1, 0),
               "Nested loops multiply: $n$ passes of $n$.", "$n$ outer passes of $n$ inner ones"),
    "triangular": ("for i in range(n):\n    for j in range(i):\n        count += 1", (2, 0), (1, 1),
                   "The inner loop averages $n/2$ passes: $\\binom{n}{2} \\approx n^2/2$ is still quadratic.", "$0 + 1 + \\dots + (n-1) = \\binom{n}{2}$ passes"),
    "halving": ("i = n\nwhile i > 1:\n    i //= 2\n    count += 1", (0, 1), (1, 0),
                "$i$ halves each pass, so it reaches $1$ after about $\\log_2 n$ passes.", "$i$ halves each pass: $\\log_2 n$ passes"),
    "doubling-inner": ("for i in range(n):\n    j = 1\n    while j < n:\n        j *= 2\n        count += 1", (1, 1), (2, 0),
                       "The inner loop doubles $j$: $\\log_2 n$ passes, not $n$.", "$n$ outer passes of $\\log_2 n$ doublings"),
    "doubling-outer": ("i = 1\nwhile i < n:\n    for j in range(n):\n        count += 1\n    i *= 2", (1, 1), (2, 0),
                       "The outer loop doubles $i$: $\\log_2 n$ passes, not $n$.", "$\\log_2 n$ doublings of $n$ inner passes"),
    "geometric": ("i = 1\nwhile i < n:\n    for j in range(i):\n        count += 1\n    i *= 2", (1, 0), (1, 1),
                  "The inner counts $1 + 2 + 4 + \\dots$ form a geometric series below $2n$.", "$1 + 2 + 4 + \\dots + n/2 < n$ passes"),
    "triple": ("for i in range(n):\n    for j in range(i + 1, n):\n        for k in range(j + 1, n):\n            count += 1", (3, 0), (2, 0),
               "Three nested loops make $\\binom{n}{3} \\approx n^3/6$ passes: still cubic.", "$\\binom{n}{3}$ passes, one per $i < j < k$"),
    "nested-doubling": ("for i in range(n):\n    for j in range(n):\n        k = 1\n        while k < n:\n            k *= 2\n            count += 1",
                        (2, 1), (3, 0), "The innermost loop doubles $k$: $\\log_2 n$ passes, not $n$.", "$n^2$ pairs of $\\log_2 n$ doublings"),
}


@template("disc.big-o/loops", target_s=40)
def big_o_loops(r):
    shape = r.choice(sorted(BIG_O_LOOPS))
    code, cls, wrong, message, passes = BIG_O_LOOPS[shape]
    return Spec({"shape": shape}, f"Each `count += 1` costs $O(1)$. Which tight bound describes the running time of this code as a function of $n$?\n\n```python\n{code}\n```",
                _theta(cls), [
                    Step(passes),
                    Step("Power of $n$ in the pass count", "", F(cls[0])),
                    Step("Factors of $\\log n$", "", F(cls[1])),
                ], kind="choice", choices=_growth_choices(cls, wrong), mistakes=[(_theta(wrong), message)])


def _random_graph(r, n):
    while True:
        edges = [(i, j) for i in range(n) for j in range(i + 1, n) if r.random() < 0.45]
        degrees = [sum(v in edge for edge in edges) for v in range(n)]
        if len(edges) >= n - 1 and max(degrees) > 0:
            return len(edges), sorted(degrees, reverse=True)


@template("disc.graph-edges/handshake", target_s=30)
def graph_edges(r):
    variant = r.choice(["complete", "degrees", "missing"])
    if variant == "complete":
        n = r.randint(5, 40)
        answer = comb(n, 2)
        return Spec({"variant": variant, "n": n}, f"How many edges does the complete graph $K_{{{n}}}$ have?", answer, [
            Step("Each vertex is adjacent to every other", f"{n} - 1", F(n - 1)),
            Step("Degree sum", f"{n} \\cdot {n - 1}", F(n * (n - 1))),
            Step("Each edge has two ends: halve", f"\\frac{{{n * (n - 1)}}}{{2}}", F(answer)),
        ], mistakes=[(n * (n - 1), "Each edge was counted from both ends: halve the degree sum."),
                     (n * n, "A simple graph has no loops: a vertex isn't adjacent to itself.")])
    n = r.randint(5, 8)
    edges, degrees = _random_graph(r, n)
    if variant == "degrees":
        total = sum(degrees)
        return Spec({"variant": variant, "degrees": tuple(degrees)},
                    f"A simple graph has vertex degrees ${', '.join(map(str, degrees))}$. How many edges does it have?", edges, [
                        Step("Degree sum", " + ".join(map(str, degrees)), F(total)),
                        Step("Each edge adds $2$ to the degree sum: halve", f"\\frac{{{total}}}{{2}}", F(edges)),
                    ], mistakes=[(total, "Each edge has two ends, so it's counted twice in the degree sum: halve it."),
                                 (n, "That's the number of vertices.")])
    hidden = r.randrange(n)
    known = degrees[:hidden] + degrees[hidden + 1:]
    answer = 2 * edges - sum(known)
    return Spec({"variant": variant, "n": n, "edges": edges, "degrees": tuple(known)},
                f"A simple graph has {n} vertices and {edges} edges. {n - 1} of its vertices have degrees ${', '.join(map(str, known))}$. "
                "What is the degree of the remaining vertex?", answer, [
                    Step("Handshake lemma: the degree sum", f"2 \\cdot {edges}", F(2 * edges)),
                    Step("The known degrees add to", " + ".join(map(str, known)), F(sum(known))),
                    Step("Missing degree", f"{2 * edges} - {sum(known)}", F(answer)),
                ], mistakes=[(edges - sum(known), f"The degree sum is twice the edge count: $2 \\cdot {edges}$."),
                             (edges, "That's the number of edges; each edge adds $2$ to the degree sum.")])


@template("disc.graph-counting/labelled", target_s=45)
def graph_counting(r):
    variant = r.choice(["graphs", "edges", "trees", "spanning"])
    n = r.randint(3, 6) if variant != "edges" else r.randint(4, 6)
    pairs = comb(n, 2)
    if variant in ("graphs", "edges"):
        base = [Step("Possible edges: unordered pairs of vertices", f"\\binom{{{n}}}{{2}}", F(pairs))]
        if variant == "graphs":
            return Spec({"variant": variant, "n": n}, f"How many simple graphs are there on {n} labelled vertices (any number of edges)?", 2 ** pairs,
                        base + [Step("Each possible edge is in or out", f"2^{{{pairs}}}", F(2 ** pairs))],
                        mistakes=[(2 ** (n * (n - 1)), "Edges are unordered pairs: there are $\\binom{n}{2}$, not $n(n-1)$."),
                                  (2 ** (n * n), "A simple graph has no loops and no ordered pairs: use $\\binom{n}{2}$ edges."),
                                  (pairs, "That's the number of possible edges; each one is in or out.")])
        m = r.randint(2, pairs - 2)
        answer = comb(pairs, m)
        return Spec({"variant": variant, "n": n, "m": m}, f"How many simple graphs on {n} labelled vertices have exactly {m} edges?", answer,
                    base + [Step(f"Choose which {m} are present", f"\\binom{{{pairs}}}{{{m}}}", F(answer))],
                    mistakes=[(2 ** pairs, f"That counts graphs with any number of edges; choose exactly ${m}$."),
                              (pairs ** m, "The edges form a set: no order and no repeats."),
                              (comb(n * n, m), "Edges are unordered pairs of distinct vertices: $\\binom{n}{2}$ of them.")])
    answer = n ** (n - 2)
    prompt = (f"How many different trees are there on {n} labelled vertices?" if variant == "trees"
              else f"How many spanning trees does the complete graph $K_{{{n}}}$ have?")
    return Spec({"variant": variant, "n": n}, prompt, answer, [
        Step("Trees match Prüfer sequences; their length is", f"{n} - 2", F(n - 2)),
        Step(f"Cayley's formula: each entry is one of {n} labels", f"{n}^{{{n - 2}}}", F(answer)),
    ], mistakes=[(n ** n, "Cayley's formula is $n^{n-2}$, not $n^n$."),
                 (n ** (n - 1), "Cayley's formula is $n^{n-2}$: a Prüfer sequence has $n - 2$ entries."),
                 (comb(pairs, n - 1), f"Not every set of ${n - 1}$ edges is a tree: some contain a cycle.")])


@template("disc.lattice-paths/grid", target_s=45)
def lattice_paths(r):
    m, n = r.randint(2, 8), r.randint(2, 8)
    through = r.random() < 0.5
    start = f"Walk from $(0, 0)$ to $({m}, {n})$ with unit steps right $(+1, 0)$ or up $(0, +1)$."
    total = comb(m + n, m)
    wrong_power = (2 ** (m + n), f"Not every step is free: exactly ${m}$ of them go right.")
    if not through:
        return Spec({"m": m, "n": n, "through": None}, f"{start} How many paths are there?", total, [
            Step("Total steps", f"{m} + {n}", F(m + n)),
            Step("Choose which are right steps", f"\\binom{{{m + n}}}{{{m}}}", F(total)),
        ], mistakes=[wrong_power, (m * n, "Count orders of the steps: choose which of them go right.")])
    a, b = r.randint(1, m - 1), r.randint(1, n - 1)
    first, second = comb(a + b, a), comb(m - a + n - b, m - a)
    answer = first * second
    return Spec({"m": m, "n": n, "through": (a, b)}, f"{start} How many paths pass through $({a}, {b})$?", answer, [
        Step(f"Paths to $({a}, {b})$", f"\\binom{{{a + b}}}{{{a}}}", F(first)),
        Step(f"Paths from $({a}, {b})$ on", f"\\binom{{{m - a + n - b}}}{{{m - a}}}", F(second)),
        Step("Any first half joins any second half", f"{first} \\times {second}", F(answer)),
    ], mistakes=[(total, f"That counts every path; only those through $({a}, {b})$ count."),
                 (first + second, "Each first half pairs with each second half: multiply."), wrong_power])
