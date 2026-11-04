"""Linear algebra: vectors, dot products, matrix products, determinants, inverses and 2 × 2 eigenvalues."""
from __future__ import annotations

from fractions import Fraction as F

from kitty.core.math.problems import Spec, Step, template

ORDINAL = ("first", "second", "third", "fourth")
#: Integer-length directions: Pythagorean triples (2-D) and quadruples (3-D), length last.
TRIPLES = ((3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25), (6, 8, 10), (9, 12, 15), (12, 16, 20))
QUADRUPLES = ((1, 2, 2, 3), (2, 3, 6, 7), (1, 4, 8, 9), (4, 4, 7, 9), (2, 6, 9, 11), (6, 6, 7, 11),
              (2, 4, 4, 6), (2, 10, 11, 15), (8, 9, 12, 17))


def _vec(v) -> str:
    return "\\begin{pmatrix} " + " \\\\ ".join("k" if x is None else str(x) for x in v) + " \\end{pmatrix}"


def _mat(rows) -> str:
    return "\\begin{pmatrix} " + " \\\\ ".join(" & ".join(str(x) for x in row) for row in rows) + " \\end{pmatrix}"


def _p(x) -> str:
    """A factor in a product: negatives get parentheses."""
    return f"({x})" if x < 0 else str(x)


def _sum(values) -> str:
    """Integers as a sum with signs folded in: ``-8 + 3 - 2``."""
    values = list(values)
    return str(values[0]) + "".join(f" - {-x}" if x < 0 else f" + {x}" for x in values[1:])


def _ints(r, n, lo, hi) -> tuple[int, ...]:
    return tuple(r.randint(lo, hi) for _ in range(n))


def _nonzero(r, lo, hi) -> int:
    return r.choice([x for x in range(lo, hi + 1) if x])


def _matrix(r, rows, cols, lo, hi) -> tuple[tuple[int, ...], ...]:
    return tuple(_ints(r, cols, lo, hi) for _ in range(rows))


def _term(coefficient: int, name: str, first: bool) -> str:
    """``coefficient·name`` as the first or a later term of a sum (1 and −1 drop the digit)."""
    magnitude = "" if abs(coefficient) == 1 else str(abs(coefficient))
    sign = ("-" if coefficient < 0 else "") if first else (" - " if coefficient < 0 else " + ")
    return f"{sign}{magnitude}{name}"


def _det2(rows) -> int:
    (a, b), (c, d) = rows
    return a * d - b * c


def _det2_tex(rows) -> str:
    (a, b), (c, d) = rows
    return f"{_p(a)} \\cdot {_p(d)} - {_p(b)} \\cdot {_p(c)}"


def _minor(rows, i, j) -> tuple[tuple[int, ...], ...]:
    """``rows`` without row ``i`` and column ``j``."""
    return tuple(tuple(x for c, x in enumerate(row) if c != j) for k, row in enumerate(rows) if k != i)


def _det3(rows) -> int:
    return sum((-1) ** j * rows[0][j] * _det2(_minor(rows, 0, j)) for j in range(3))


def _expand_tex(rows) -> str:
    """Row-1 cofactor expansion with the minors' values: a(M11) − b(M12) + c(M13)."""
    a, b, c = rows[0]
    m = [_det2(_minor(rows, 0, j)) for j in range(3)]
    return f"{_p(a)}({m[0]}) - {_p(b)}({m[1]}) + {_p(c)}({m[2]})"


def _product_steps(row, column, label: str) -> list[Step]:
    """One step per matching product, then their sum."""
    steps = [Step(f"{ORDINAL[k].capitalize()} pair", f"{_p(x)} \\cdot {_p(y)}", F(x * y)) for k, (x, y) in enumerate(zip(row, column))]
    total = sum(x * y for x, y in zip(row, column))
    return steps + [Step(label, _sum(x * y for x, y in zip(row, column)), F(total))]


def _length(r):
    """A random integer-length direction: signed, shuffled components and the length."""
    base = r.choice(TRIPLES if r.random() < 0.5 else QUADRUPLES)
    parts = [x * r.choice((1, -1)) for x in base[:-1]]
    r.shuffle(parts)
    return tuple(parts), base[-1]


@template("linalg.vectors/combination", target_s=20)
def combination(r):
    n = r.choice([2, 3])
    u, v = _ints(r, n, -6, 6), _ints(r, n, -6, 6)
    a, b = _nonzero(r, -4, 4), _nonzero(r, -4, 4)
    i = r.randrange(n)
    answer = a * u[i] + b * v[i]
    combo = _term(a, "\\mathbf u", True) + _term(b, "\\mathbf v", False)
    return Spec({"u": u, "v": v, "a": a, "b": b, "i": i},
                f"Let $\\mathbf u = {_vec(u)}$ and $\\mathbf v = {_vec(v)}$. What is the {ORDINAL[i]} component of ${combo}$?", answer, [
                    Step("Componentwise: only the matching components combine", f"({combo})_{i + 1} = {a}u_{i + 1} + {_p(b)}v_{i + 1}"),
                    Step(f"Scale $u_{i + 1}$", f"{a} \\cdot {_p(u[i])}", F(a * u[i])),
                    Step(f"Scale $v_{i + 1}$", f"{b} \\cdot {_p(v[i])}", F(b * v[i])),
                    Step("Add", _sum((a * u[i], b * v[i])), F(answer)),
                ], mistakes=[(a * u[i] + v[i], f"Scale both vectors: multiply $v_{i + 1}$ by {b} too."),
                             (u[i] + v[i], "Multiply each vector by its scalar before adding."),
                             (a * u[i] - b * v[i], f"Check the sign of the second term: ${b} \\cdot {_p(v[i])} = {b * v[i]}$.")])


@template("linalg.norm/length", target_s=30)
def length(r):
    u, answer = _length(r)
    squares = sum(x * x for x in u)
    return Spec({"u": u}, f"What is the length $\\lVert \\mathbf u \\rVert$ of $\\mathbf u = {_vec(u)}$?", answer, [
        Step("Sum of squares", " + ".join(f"{_p(x)}^2" for x in u), F(squares)),
        Step("Square root", f"\\sqrt{{{squares}}}", F(answer)),
    ], mistakes=[(squares, "That's $\\lVert \\mathbf u \\rVert^2$; take the square root."),
                 (sum(abs(x) for x in u), "Length isn't the sum of the components: square them, add, then take the square root.")])


@template("linalg.norm/distance", target_s=40)
def distance(r):
    d, answer = _length(r)
    v = _ints(r, len(d), -5, 5)
    u = tuple(x + y for x, y in zip(v, d))
    squares = sum(x * x for x in d)
    return Spec({"u": u, "v": v}, f"What is the distance $\\lVert \\mathbf u - \\mathbf v \\rVert$ between $\\mathbf u = {_vec(u)}$ and $\\mathbf v = {_vec(v)}$?", answer, [
        Step("Subtract componentwise", f"\\mathbf u - \\mathbf v = {_vec(d)}"),
        Step("Sum of squares", " + ".join(f"{_p(x)}^2" for x in d), F(squares)),
        Step("Square root", f"\\sqrt{{{squares}}}", F(answer)),
    ], mistakes=[(squares, "That's the squared distance; take the square root."),
                 (sum(abs(x) for x in d), "Distance isn't the sum of the differences: square them, add, then take the square root.")])


@template("linalg.dot/product", target_s=20)
def dot(r):
    n = r.choice([2, 3, 4])
    u, v = _ints(r, n, -6, 7), _ints(r, n, -6, 7)
    answer = sum(x * y for x, y in zip(u, v))
    return Spec({"u": u, "v": v}, f"What is $\\mathbf u \\cdot \\mathbf v$ for $\\mathbf u = {_vec(u)}$ and $\\mathbf v = {_vec(v)}$?", answer,
                _product_steps(u, v, "Add the products"),
                mistakes=[(sum(x + y for x, y in zip(u, v)), "Multiply matching components, then add the products."),
                          (sum(abs(x * y) for x, y in zip(u, v)), "Watch the signs: a negative times a positive is negative.")])


@template("linalg.orthogonal/solve-k", target_s=40)
def orthogonal(r):
    n = r.choice([2, 3])
    while True:
        u = tuple(_nonzero(r, -6, 6) for _ in range(n))
        j = r.randrange(n)
        v = tuple(None if k == j else r.randint(-6, 6) for k in range(n))
        known = sum(x * y for k, (x, y) in enumerate(zip(u, v)) if k != j)
        if known:
            break
    answer = F(-known, u[j])
    pairs = " + ".join(f"{_p(x)} \\cdot {_p(y)}" for k, (x, y) in enumerate(zip(u, v)) if k != j)
    return Spec({"u": u, "v": v, "j": j},
                f"For which $k$ are $\\mathbf u = {_vec(u)}$ and $\\mathbf v = {_vec(v)}$ orthogonal?", answer, [
                    Step("Orthogonal means", "\\mathbf u \\cdot \\mathbf v = 0"),
                    Step("The known products", pairs, F(known)),
                    Step("Solve", f"{known}{_term(u[j], 'k', False)} = 0 \\;\\Rightarrow\\; k", answer),
                ], kind="fraction",
                mistakes=[(F(known, u[j]), "Moving the known terms across the $=$ flips their sign."),
                          (F(-u[j], known), f"Divide by $k$'s coefficient ${u[j]}$, not into it."),
                          (F(-known), f"Divide by $k$'s coefficient ${u[j]}$.")])


@template("linalg.matvec/entry", target_s=30)
def matvec(r):
    m, n = r.choice([2, 3]), r.choice([2, 3])
    a, v = _matrix(r, m, n, -5, 6), _ints(r, n, -4, 5)
    i = r.randrange(m)
    answer = sum(x * y for x, y in zip(a[i], v))
    mistakes = [(sum(x + y for x, y in zip(a[i], v)), "Multiply matching entries, then add the products.")]
    if m == n:
        mistakes.insert(0, (sum(a[k][i] * v[k] for k in range(n)), f"Use row {i + 1} of $A$, not column {i + 1}."))
    return Spec({"A": a, "v": v, "i": i}, f"Let $A = {_mat(a)}$ and $\\mathbf v = {_vec(v)}$. What is entry {i + 1} of $A\\mathbf v$?", answer,
                [Step(f"Entry {i + 1} is row {i + 1} of $A$ dotted with $\\mathbf v$")] + _product_steps(a[i], v, "Add the products"),
                mistakes=mistakes)


def _matmul(r, inner: int):
    a, b = _matrix(r, 2, inner, -5, 6), _matrix(r, inner, 2, -5, 6)
    i, j = r.randrange(2), r.randrange(2)
    column = [row[j] for row in b]
    answer = sum(x * y for x, y in zip(a[i], column))
    swapped = sum(b[i][k] * a[k][j] for k in range(2))
    transposed = sum(x * row[i] for x, row in zip(a[j], b))
    mistakes = [(swapped, "Order matters: $AB$ takes rows of $A$ and columns of $B$, not the other way round."),
                (transposed, f"That's the entry in row {j + 1}, column {i + 1}: the row comes first.")]
    if inner == 2:
        mistakes.insert(0, (a[i][j] * b[i][j], f"Matrix products aren't entrywise: use row {i + 1} of $A$ and column {j + 1} of $B$."))
    return Spec({"A": a, "B": b, "i": i, "j": j},
                f"Let $A = {_mat(a)}$ and $B = {_mat(b)}$. What is the entry in row {i + 1}, column {j + 1} of $AB$?", answer,
                [Step(f"Row {i + 1} of $A$ dotted with column {j + 1} of $B$")] + _product_steps(a[i], column, "Add the products"),
                mistakes=mistakes)


@template("linalg.matmul/entry-2x2", target_s=30)
def matmul_square(r):
    return _matmul(r, 2)


@template("linalg.matmul/entry-2x3", target_s=40)
def matmul_rect(r):
    return _matmul(r, 3)


@template("linalg.det2/ad-bc", target_s=15)
def det2(r):
    a = _matrix(r, 2, 2, -6, 9)
    (p, q), (s, t) = a
    answer = _det2(a)
    return Spec({"A": a}, f"What is $\\det {_mat(a)}$?", answer, [
        Step("Main diagonal", f"{_p(p)} \\cdot {_p(t)}", F(p * t)),
        Step("Other diagonal", f"{_p(q)} \\cdot {_p(s)}", F(q * s)),
        Step("Subtract", f"{p * t} - {_p(q * s)}", F(answer)),
    ], mistakes=[(p * t + q * s, "Subtract the other diagonal: $ad - bc$."),
                 (q * s - p * t, "Main diagonal minus the other diagonal, not the reverse."),
                 (p * q - s * t, "Multiply along the diagonals, not across the rows.")])


@template("linalg.det3/cofactor", target_s=90)
def det3(r):
    a = _matrix(r, 3, 3, -4, 5)
    (p, q, s), (_, e, f), (_, h, i) = a
    minors = [_det2(_minor(a, 0, j)) for j in range(3)]
    answer = _det3(a)
    forward = p * e * i + q * f * a[2][0] + s * a[1][0] * h
    return Spec({"A": a}, f"What is $\\det {_mat(a)}$?", answer, [
        *(Step(f"Minor $M_{{1{j + 1}}}$: delete row 1 and column {j + 1}", _det2_tex(_minor(a, 0, j)), F(minors[j])) for j in range(3)),
        Step("Expand along row 1 with signs $+\\,-\\,+$", _expand_tex(a), F(answer)),
    ], mistakes=[(p * minors[0] + q * minors[1] + s * minors[2], "Cofactor signs alternate $+\\,-\\,+$: subtract the middle term."),
                 (forward, "Sarrus' rule also subtracts the three products along the anti-diagonals."),
                 (p * e * i, "Only a triangular matrix's determinant is the product of its diagonal.")])


@template("linalg.inverse2/entry", target_s=45)
def inverse2(r):
    while True:
        a = _matrix(r, 2, 2, -4, 6)
        det = _det2(a)
        if det:
            break
    (p, q), (s, t) = a
    adj = ((t, -q), (-s, p))
    i, j = r.randrange(2), r.randrange(2)
    answer = F(adj[i][j], det)
    mistakes = [(adj[i][j], "Divide by the determinant: $A^{-1} = \\frac{1}{\\det A}\\,\\operatorname{adj} A$."),
                (F(adj[j][i], det), f"That's the entry in row {j + 1}, column {i + 1}."),
                (F(a[i][j], det), "Swap the diagonal entries." if i == j else "Negate the off-diagonal entries."),
                (F(1, a[i][j]) if a[i][j] else answer, "A matrix inverse isn't the reciprocal of each entry.")]
    if p * t + q * s:
        mistakes.append((F(adj[i][j], p * t + q * s), "The determinant is $ad - bc$, not $ad + bc$."))
    return Spec({"A": a, "i": i, "j": j},
                f"Let $A = {_mat(a)}$. What is the entry in row {i + 1}, column {j + 1} of $A^{{-1}}$?", answer, [
                    Step("Determinant", _det2_tex(a), F(det)),
                    Step("Adjugate: swap the diagonal, negate the off-diagonal", f"\\operatorname{{adj}} A = {_mat(adj)}"),
                    Step(f"Adjugate entry ({i + 1}, {j + 1})", f"(\\operatorname{{adj}} A)_{{{i + 1}{j + 1}}}", F(adj[i][j])),
                    Step("Divide by the determinant", f"\\frac{{{adj[i][j]}}}{{{det}}}", answer),
                ], kind="fraction", mistakes=mistakes)


@template("linalg.inverse3/entry", target_s=150)
def inverse3(r):
    while True:
        a = _matrix(r, 3, 3, -3, 4)
        det = _det3(a)
        if det and abs(det) <= 30:
            break
    i, j = r.randrange(3), r.randrange(3)
    minor = _minor(a, j, i)
    sign = (-1) ** (i + j)
    cofactor = sign * _det2(minor)
    other = (-1) ** (i + j) * _det2(_minor(a, i, j))
    answer = F(cofactor, det)
    plain = sum(a[0][k] * _det2(_minor(a, 0, k)) for k in range(3))
    mistakes = [(F(other, det), f"The adjugate is the transposed cofactor matrix: entry ({i + 1}, {j + 1}) of $A^{{-1}}$ uses $C_{{{j + 1}{i + 1}}}$."),
                (F(_det2(minor), det), f"Apply the cofactor sign $(-1)^{{{j + 1}+{i + 1}}}$."),
                (cofactor, "Divide by the determinant: $A^{-1} = \\frac{1}{\\det A}\\,\\operatorname{adj} A$.")]
    if plain:
        mistakes.append((F(cofactor, plain), "In the determinant the cofactor signs alternate $+\\,-\\,+$."))
    return Spec({"A": a, "i": i, "j": j},
                f"Let $A = {_mat(a)}$. What is the entry in row {i + 1}, column {j + 1} of $A^{{-1}}$?", answer, [
                    Step("Determinant, expanding along row 1", _expand_tex(a), F(det)),
                    Step(f"$(A^{{-1}})_{{{i + 1}{j + 1}}} = C_{{{j + 1}{i + 1}}} / \\det A$: the adjugate is the transposed cofactor matrix"),
                    Step(f"Minor $M_{{{j + 1}{i + 1}}}$: delete row {j + 1} and column {i + 1}", _det2_tex(minor), F(_det2(minor))),
                    Step(f"Cofactor, sign $(-1)^{{{j + 1}+{i + 1}}}$", f"C_{{{j + 1}{i + 1}}} = {'+' if sign > 0 else '-'}({_det2(minor)})", F(cofactor)),
                    Step("Divide by the determinant", f"\\frac{{{cofactor}}}{{{det}}}", answer),
                ], kind="fraction", mistakes=mistakes)


@template("linalg.eigen2/char-poly", target_s=90)
def eigen2(r):
    # Pick the eigenvalues, then a matrix with that trace and determinant:
    # a + d = λ₁ + λ₂ and bc = ad − λ₁λ₂ = −(a − λ₁)(a − λ₂).
    while True:
        l1, l2 = r.sample(range(-5, 8), 2)
        p = r.choice([x for x in range(min(l1, l2) - 3, max(l1, l2) + 4) if x not in (l1, l2)])
        product = -(p - l1) * (p - l2)
        q = r.choice([x * sign for x in range(1, abs(product) + 1) if product % x == 0 for sign in (1, -1)])
        s, t = product // q, l1 + l2 - p
        if max(abs(p), abs(q), abs(s), abs(t)) <= 12:
            break
    a = ((p, q), (s, t))
    which = r.choice(["larger", "smaller"])
    trace, det = p + t, _det2(a)
    disc = trace * trace - 4 * det
    root = abs(l1 - l2)  # √disc
    answer = max(l1, l2) if which == "larger" else min(l1, l2)
    other = l1 + l2 - answer
    sign = "+" if which == "larger" else "-"
    constant = f" + {det}" if det > 0 else f" - {-det}" if det < 0 else ""
    poly = "\\lambda^2" + (_term(-trace, "\\lambda", False) if trace else "") + constant
    return Spec({"A": a, "which": which}, f"The matrix $A = {_mat(a)}$ has two integer eigenvalues. What is the {which} one?", answer, [
        Step("Trace", f"{_p(p)} + {_p(t)}", F(trace)),
        Step("Determinant", _det2_tex(a), F(det)),
        Step("Characteristic polynomial $\\det(A - \\lambda I) = \\lambda^2 - (\\operatorname{tr} A)\\lambda + \\det A$", f"{poly} = 0"),
        Step("Discriminant", f"{_p(trace)}^2 - 4 \\cdot {_p(det)}", F(disc)),
        Step(f"The {which} root", f"\\frac{{{trace} {sign} \\sqrt{{{disc}}}}}{{2}}", F(answer)),
    ], mistakes=[(other, f"That's the {'smaller' if which == 'larger' else 'larger'} eigenvalue."),
                 (p, "Eigenvalues are the diagonal entries only for a triangular matrix; solve $\\det(A - \\lambda I) = 0$."),
                 (t, "Eigenvalues are the diagonal entries only for a triangular matrix; solve $\\det(A - \\lambda I) = 0$."),
                 (-other, "The characteristic polynomial is $\\lambda^2 - (\\operatorname{tr} A)\\lambda + \\det A$: check the trace term's sign."),
                 (trace + root if which == "larger" else trace - root, "Divide by 2: $\\lambda = \\frac{\\operatorname{tr} A \\pm \\sqrt{\\Delta}}{2}$.")])
