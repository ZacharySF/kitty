"""Linear-algebra generators cross-checked over 240 seeds against independent oracles computed
from the problem's parameters alone: Leibniz expansion for determinants, exact Gauss–Jordan
elimination for inverses, integer scans of det(A − λI) for eigenvalues, SymPy for products."""
from __future__ import annotations

from fractions import Fraction as F
from itertools import count, permutations

import pytest
import sympy

from kitty.core.math import problems
from kitty.core.math import topics as graph

SEEDS = range(240)
problems._load()


def leibniz(rows) -> F:
    """det A = Σ over permutations σ of sign(σ) Π a[i][σ(i)]."""
    n, total = len(rows), F(0)
    for perm in permutations(range(n)):
        inversions = sum(1 for x in range(n) for y in range(x + 1, n) if perm[x] > perm[y])
        term = F(-1) ** inversions
        for i, j in enumerate(perm):
            term *= rows[i][j]
        total += term
    return total


def inverse_by_elimination(rows) -> list[list[F]]:
    """Solve A·X = I by Gauss–Jordan elimination over exact fractions."""
    n = len(rows)
    aug = [[F(x) for x in row] + [F(int(i == k)) for k in range(n)] for i, row in enumerate(rows)]
    for col in range(n):
        pivot = next(r for r in range(col, n) if aug[r][col] != 0)
        aug[col], aug[pivot] = aug[pivot], aug[col]
        lead = aug[col][col]
        aug[col] = [x / lead for x in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0:
                factor = aug[r][col]
                aug[r] = [x - factor * y for x, y in zip(aug[r], aug[col])]
    return [row[n:] for row in aug]


def integer_sqrt_by_scan(value) -> int:
    root = next(n for n in count() if n * n >= value)
    assert root * root == value, value
    return root


def eigenvalues_by_scan(rows) -> list[int]:
    """Integer roots of det(A − λI), scanning a window wider than any entry allows."""
    bound = 2 * sum(abs(x) for row in rows for x in row) + 1
    roots = [lam for lam in range(-bound, bound + 1)
             if leibniz([[x - lam * (i == j) for j, x in enumerate(row)] for i, row in enumerate(rows)]) == 0]
    assert len(roots) == 2, (rows, roots)
    return roots


def orthogonal_k(p) -> F:
    k = sympy.Symbol("k")
    v = [k if x is None else x for x in p["v"]]
    (root,) = sympy.solve(sum(a * b for a, b in zip(p["u"], v)), k)
    return F(str(root))


ORACLES = {
    "linalg.vectors/combination": lambda p: (p["a"] * sympy.Matrix(p["u"]) + p["b"] * sympy.Matrix(p["v"]))[p["i"]],
    "linalg.norm/length": lambda p: integer_sqrt_by_scan(sympy.Matrix(p["u"]).dot(sympy.Matrix(p["u"]))),
    "linalg.norm/distance": lambda p: integer_sqrt_by_scan(sum((a - b) ** 2 for a, b in zip(p["u"], p["v"]))),
    "linalg.dot/product": lambda p: sympy.Matrix(p["u"]).dot(sympy.Matrix(p["v"])),
    "linalg.orthogonal/solve-k": orthogonal_k,
    "linalg.matvec/entry": lambda p: (sympy.Matrix(p["A"]) * sympy.Matrix(p["v"]))[p["i"]],
    "linalg.matmul/entry-2x2": lambda p: (sympy.Matrix(p["A"]) * sympy.Matrix(p["B"]))[p["i"], p["j"]],
    "linalg.matmul/entry-2x3": lambda p: (sympy.Matrix(p["A"]) * sympy.Matrix(p["B"]))[p["i"], p["j"]],
    "linalg.det2/ad-bc": lambda p: leibniz(p["A"]),
    "linalg.det3/cofactor": lambda p: leibniz(p["A"]),
    "linalg.inverse2/entry": lambda p: inverse_by_elimination(p["A"])[p["i"]][p["j"]],
    "linalg.inverse3/entry": lambda p: inverse_by_elimination(p["A"])[p["i"]][p["j"]],
    "linalg.eigen2/char-poly": lambda p: (max if p["which"] == "larger" else min)(eigenvalues_by_scan(p["A"])),
}

LINALG = sorted(item.id for item in problems.TEMPLATES.values() if item.topic_id.startswith("linalg."))


def test_every_linalg_topic_has_a_template_and_every_template_an_oracle():
    topics = [topic.id for topic in graph.TOPICS if topic.strand == "linalg"]
    assert len(topics) == 11
    assert all(problems.for_topic(topic_id) for topic_id in topics)
    assert set(ORACLES) == set(LINALG)


@pytest.mark.parametrize("template_id", sorted(ORACLES))
def test_linalg_answer_matches_independent_oracle(template_id):
    oracle = ORACLES[template_id]
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        expected = F(str(oracle(problem.params)))
        assert problem.answer == expected, (seed, problem.params, problem.answer, expected)


def test_inverse_entries_come_from_an_invertible_matrix_that_checks_out():
    for template_id in ("linalg.inverse2/entry", "linalg.inverse3/entry"):
        for seed in SEEDS:
            p = problems.generate(template_id, seed).params
            a = sympy.Matrix(p["A"])
            assert leibniz(p["A"]) != 0
            assert a * sympy.Matrix(inverse_by_elimination(p["A"])) == sympy.eye(a.rows)


def test_eigenvalue_matrices_have_distinct_integer_eigenvalues():
    for seed in SEEDS:
        p = problems.generate("linalg.eigen2/char-poly", seed).params
        assert sorted(eigenvalues_by_scan(p["A"])) == sorted(int(x) for x in sympy.Matrix(p["A"]).eigenvals())
