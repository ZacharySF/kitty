"""Calculus: limits, derivatives by rule, integrals of polynomials, exp and trig, and Taylor approximations.

Polynomials are coefficient tuples, lowest power first. Other functions of x are
``(name, c)`` pairs: ``("exp", c)`` is $e^{cx}$, likewise ``sin`` and ``cos``, ``("ln", 1)``
is $\\ln x$ and ``("poly", coeffs)`` a polynomial. Expression answers are SymPy strings in x.
"""
from __future__ import annotations

from fractions import Fraction as F
from math import factorial

from kitty.core.math.problems import Spec, Step, num, template, tex

X = ("x",)
UP_TO_C = "Any constant of integration is fine."


def _coef(value) -> str:
    """A coefficient written in front of a function: ``""`` for 1, ``"-"`` for -1."""
    value = F(value)
    return "" if value == 1 else "-" if value == -1 else tex(value)


def _p(value) -> str:
    """A number as TeX, bracketed when negative so it can follow an operator."""
    return f"({tex(value)})" if value < 0 else tex(value)


def _xpow(k: int) -> str:
    return "" if k == 0 else "x" if k == 1 else f"x^{{{k}}}"


def _poly_tex(coeffs, ascending: bool = False) -> str:
    out = ""
    powers = range(len(coeffs)) if ascending else range(len(coeffs) - 1, -1, -1)
    for k in powers:
        c = F(coeffs[k])
        if c == 0:
            continue
        size = tex(abs(c)) if abs(c) != 1 or k == 0 else ""
        sign = ("-" if c < 0 else "") if not out else (" - " if c < 0 else " + ")
        out += f"{sign}{size}{_xpow(k)}"
    return out or "0"


def _poly_py(coeffs) -> str:
    terms = []
    for k, c in enumerate(coeffs):
        if c:
            c = F(c)
            terms.append((f"({c})" if c < 0 else str(c)) + ("" if k == 0 else "*x" if k == 1 else f"*x**{k}"))
    return " + ".join(terms) or "0"


def _d(coeffs) -> tuple:
    return tuple(k * c for k, c in enumerate(coeffs))[1:] or (0,)


def _value(coeffs, at) -> F:
    return sum((F(c) * F(at) ** k for k, c in enumerate(coeffs)), F(0))


def _random_poly(r, degree: int, terms: int, size: int = 9) -> tuple:
    """``terms`` nonzero coefficients including the leading one."""
    coeffs = [0] * (degree + 1)
    for k in [degree] + r.sample(range(degree), terms - 1):
        coeffs[k] = r.choice([c for c in range(-size, size + 1) if c])
    return tuple(coeffs)


def _wrap(text: str) -> str:
    return f"\\left({text}\\right)" if text.startswith("-") or " + " in text or " - " in text else text


def _times(a: str, b: str) -> str:
    if a in ("1", "-1"):
        return ("-" if a == "-1" else "") + _wrap(b)
    return f"{_wrap(a)}\\,{_wrap(b)}"


def _plus(a: str, b: str, minus: bool = False) -> str:
    if b.startswith("-"):
        return f"{a} {'+' if minus else '-'} {b[1:]}"
    return f"{a} {'-' if minus else '+'} {b}"


def _arg(c) -> str:
    return _poly_tex((0, c))


def _fn(spec) -> tuple[str, str, str, str]:
    """TeX, SymPy text, derivative TeX and derivative SymPy text of a function parameter."""
    name, c = spec
    if name == "poly":
        return _poly_tex(c), _poly_py(c), _poly_tex(_d(c)), _poly_py(_d(c))
    if name == "ln":
        return "\\ln x", "log(x)", "\\frac{1}{x}", "1/x"
    if name == "exp":
        return f"e^{{{_arg(c)}}}", f"exp({c}*x)", f"{_coef(c)}e^{{{_arg(c)}}}", f"({c})*exp({c}*x)"
    if name == "sin":
        return f"\\sin({_arg(c)})", f"sin({c}*x)", f"{_coef(c)}\\cos({_arg(c)})", f"({c})*cos({c}*x)"
    return f"\\cos({_arg(c)})", f"cos({c}*x)", f"{_coef(-c)}\\sin({_arg(c)})", f"({-c})*sin({c}*x)"


def _at(spec, point) -> tuple[F, F]:
    """Value and slope of a function parameter at ``point`` (0, or 1 for ``ln``)."""
    name, c = spec
    if name == "poly":
        return _value(c, point), _value(_d(c), point)
    return {"ln": (F(0), F(1)), "exp": (F(1), F(c)), "sin": (F(0), F(c)), "cos": (F(1), F(0))}[name]


def _linear(x0) -> str:
    return _poly_tex((-x0, 1))


@template("calc.limits/factor", target_s=45)
def limit_by_factoring(r):
    a = r.choice([n for n in range(-6, 7) if n])
    form = r.choice(["quadratic", "quadratic", "cubic"])
    if form == "quadratic":
        b = r.choice([n for n in range(-7, 8) if n != -a])
        top = (-a * b, b - a, 1)
        answer = a + b
        factor = f"\\left({_linear(a)}\\right)\\left({_linear(-b)}\\right)"
        cancel = Step(f"Cancel $({_linear(a)})$ and substitute $x = {a}$", f"{a} + {_p(b)}", F(answer))
        mistakes = [(b - a, f"Substitute the point the limit approaches, $x = {a}$, into ${_linear(-b)}$.")]
    else:
        b = 0
        top = (-a ** 3, 0, 0, 1)
        answer = 3 * a * a
        factor = f"\\left({_linear(a)}\\right)\\left({_poly_tex((a * a, a, 1))}\\right)"
        cancel = Step(f"Cancel $({_linear(a)})$ and substitute $x = {a}$", f"{_p(a)}^2 + {_p(a)} \\cdot {_p(a)} + {_p(a)}^2", F(answer))
        mistakes = [(a * a, "$x^3 - a^3 = (x - a)(x^2 + ax + a^2)$: all three terms of the second factor count.")]
    mistakes.append((0, "Substituting straight away gives $\\frac{0}{0}$, which has no value: cancel the common factor first."))
    return Spec({"form": form, "a": a, "b": b},
                f"Find $\\displaystyle\\lim_{{x \\to {a}}} \\frac{{{_poly_tex(top)}}}{{{_linear(a)}}}$.", answer, [
                    Step(f"At $x = {a}$ top and bottom are both 0, so factor the top", f"{_poly_tex(top)} = {factor}"),
                    cancel,
                ], mistakes=mistakes)


@template("calc.limits-infinity/rational", target_s=40)
def limit_at_infinity(r):
    m = r.randint(1, 3)
    n = m if m == 1 or r.random() < 0.7 else m - 1
    top, bottom = _random_poly(r, n, r.randint(1, n + 1)), _random_poly(r, m, r.randint(2, m + 1))
    p, q = top[-1], bottom[-1]
    if n == m:
        answer = F(p, q)
        steps = [Step(f"Divide top and bottom by ${_xpow(m)}$; every lower power over it tends to 0, leaving the leading coefficients",
                      f"\\frac{{{p}}}{{{q}}}", answer)]
        mistakes = [(F(q, p), "Top's leading coefficient over the bottom's, not the other way up.")]
    else:
        answer = F(0)
        steps = [Step(f"Divide top and bottom by ${_xpow(m)}$: the top tends to 0 and the bottom to ${q}$",
                      f"\\frac{{0}}{{{q}}}", answer)]
        mistakes = [(F(p, q), "The bottom has the higher degree, so it grows faster and the limit is 0.")]
    if bottom[0]:
        mistakes.append((F(top[0], bottom[0]), "That's the limit as $x \\to 0$; as $x \\to \\infty$ the highest powers dominate."))
    return Spec({"top": top, "bottom": bottom},
                f"Find $\\displaystyle\\lim_{{x \\to \\infty}} \\frac{{{_poly_tex(top)}}}{{{_poly_tex(bottom)}}}$.", answer,
                [Step("Compare degrees", f"\\deg(\\text{{top}}) = {n},\\quad \\deg(\\text{{bottom}}) = {m}"), *steps],
                kind="fraction", mistakes=mistakes)


@template("calc.power-rule/polynomial", target_s=45)
def power_rule(r):
    n = r.randint(3, 5)
    f = _random_poly(r, n, r.randint(3, 4))
    df = _d(f)
    return Spec({"f": f}, f"Differentiate $f(x) = {_poly_tex(f)}$. Enter $f'(x)$.", _poly_py(df), [
        Step("Power rule, term by term", "\\frac{d}{dx}\\,ax^{n} = nax^{n-1}"),
        Step(f"Coefficient of $x^{{{n - 1}}}$", f"{n} \\cdot {_p(f[n])}", F(n * f[n])),
        Step("All terms", f"f'(x) = {_poly_tex(df)}"),
    ], kind="expression", variables=X, mistakes=[
        (_poly_py(tuple(k * c for k, c in enumerate(f))), "Lower each power by one: $\\frac{d}{dx}x^n = nx^{n-1}$."),
        (_poly_py(f[1:]), "Bring the power down as a factor: $\\frac{d}{dx}ax^n = nax^{n-1}$."),
        *([(f"{_poly_py(df)} + ({f[0]})", "A constant term differentiates to 0.")] if f[0] else []),
    ])


@template("calc.slopes/at-point", target_s=40)
def slope_at_point(r):
    f = _random_poly(r, r.randint(2, 3), r.randint(2, 3), size=6)
    a = r.randint(-3, 3)
    df = _d(f)
    answer = _value(df, a)
    return Spec({"f": f, "a": a}, f"What is the slope of $f(x) = {_poly_tex(f)}$ at $x = {a}$?", answer, [
        Step("Differentiate", f"f'(x) = {_poly_tex(df)}"),
        Step(f"Substitute $x = {a}$", f"f'({a})", answer),
    ], mistakes=[(_value(f, a), f"That's the height $f({a})$; the slope is $f'({a})$."),
                 (_value(df, -a), f"Substitute $x = {a}$ exactly, sign included.")])


@template("calc.product-rule/times-function", target_s=60)
def product_rule(r):
    n = r.randint(1, 3)
    u = ("poly", tuple([r.choice([c for c in range(-6, 7) if c])] + [0] * (n - 1) + [r.choice([c for c in range(-5, 6) if c])]))
    name = r.choice(["exp", "sin", "cos", "ln"])
    # Not e^x itself: with v' = v, forgetting uv' and multiplying the derivatives would coincide.
    v = (name, 1 if name == "ln" else r.choice([-3, -2, -1, 2, 3] if name == "exp" else [-3, -2, -1, 1, 2, 3]))
    (u_tex, u_py, du_tex, du_py), (v_tex, v_py, dv_tex, dv_py) = _fn(u), _fn(v)
    point = 1 if name == "ln" else 0
    (u0, du0), (v0, dv0) = _at(u, point), _at(v, point)
    slope = du0 * v0 + u0 * dv0
    return Spec({"u": u, "v": v}, f"Differentiate $f(x) = {_times(u_tex, v_tex)}$.", f"({du_py})*({v_py}) + ({u_py})*({dv_py})", [
        Step("The factors", f"u = {u_tex},\\quad v = {v_tex}"),
        Step("Differentiate each", f"u' = {du_tex},\\quad v' = {dv_tex}"),
        Step("Product rule $(uv)' = u'v + uv'$", f"f'(x) = {_plus(_times(du_tex, v_tex), _times(u_tex, dv_tex))}"),
        Step(f"Check: the slope at $x = {point}$", f"f'({point}) = {_p(du0)} \\cdot {_p(v0)} + {_p(u0)} \\cdot {_p(dv0)}", slope),
    ], kind="expression", variables=X, mistakes=[
        (f"({du_py})*({dv_py})", "The derivative of a product isn't the product of the derivatives: $(uv)' = u'v + uv'$."),
        (f"({du_py})*({v_py}) - ({u_py})*({dv_py})", "The product rule adds the two terms; the minus sign belongs to the quotient rule."),
        (f"({du_py})*({v_py})", "Differentiate both factors: add $uv'$ as well."),
    ])


def _proportional(a, b) -> bool:
    a, b = a + (0,) * (len(b) - len(a)), b + (0,) * (len(a) - len(b))
    return all(a[i] * b[j] == a[j] * b[i] for i in range(len(a)) for j in range(len(a)))


@template("calc.quotient-rule/over-polynomial", target_s=75)
def quotient_rule(r):
    nonzero = [c for c in range(-5, 6) if c]
    while True:
        bottom = (r.choice(nonzero), r.choice(nonzero)) if r.random() < 0.6 else (r.randint(1, 6), 0, 1)
        kind = r.choice(["linear", "quadratic", "exp", "sin"])
        if kind == "linear":
            top = ("poly", (r.randint(-5, 5), r.choice(nonzero)))
        elif kind == "quadratic":
            top = ("poly", (r.choice(nonzero), 0, r.choice(nonzero)))
        else:
            top = (kind, r.choice([-2, -1, 1, 2, 3]))
        if top[0] != "poly" or not _proportional(top[1], bottom):
            break
    (u_tex, u_py, du_tex, du_py), (v_tex, v_py, dv_tex, dv_py) = _fn(top), _fn(("poly", bottom))
    (u0, du0), (v0, dv0) = _at(top, 0), _at(("poly", bottom), 0)
    slope = (du0 * v0 - u0 * dv0) / v0 ** 2
    numerator = _plus(_times(du_tex, v_tex), _times(u_tex, dv_tex), minus=True)
    return Spec({"top": top, "bottom": bottom}, f"Differentiate $f(x) = \\dfrac{{{u_tex}}}{{{v_tex}}}$.",
                f"(({du_py})*({v_py}) - ({u_py})*({dv_py}))/({v_py})**2", [
                    Step("Top and bottom", f"u = {u_tex},\\quad v = {v_tex}"),
                    Step("Differentiate each", f"u' = {du_tex},\\quad v' = {dv_tex}"),
                    Step("Quotient rule $\\left(\\frac{u}{v}\\right)' = \\frac{u'v - uv'}{v^2}$",
                         f"f'(x) = \\frac{{{numerator}}}{{\\left({v_tex}\\right)^{{2}}}}"),
                    Step("Check: the slope at $x = 0$", f"f'(0) = \\frac{{{_p(du0)} \\cdot {_p(v0)} - {_p(u0)} \\cdot {_p(dv0)}}}{{{_p(v0)}^2}}", slope),
                ], kind="expression", variables=X, mistakes=[
                    (f"(({u_py})*({dv_py}) - ({du_py})*({v_py}))/({v_py})**2", "The numerator is $u'v - uv'$: the top's derivative comes first."),
                    (f"({du_py})/({dv_py})", "Differentiating top and bottom separately isn't the quotient rule."),
                    (f"(({du_py})*({v_py}) - ({u_py})*({dv_py}))/({v_py})", "Divide by $v^2$, the bottom squared."),
                ])


@template("calc.chain-rule/linear-inner", target_s=50)
def chain_rule(r):
    outer = r.choice(["power", "power", "exp", "sin", "cos"])
    a, k = r.choice([-5, -4, -3, -2, 2, 3, 4, 5]), r.choice([1, 1, 2, 3, -1, -2])
    b = r.choice([c for c in range(-5, 6) if c]) if outer == "power" else r.randint(-4, 4)
    n = r.randint(2, 6) if outer == "power" else 1
    inner, inner_py = _poly_tex((b, a)), f"({a}*x + ({b}))"
    if outer == "power":
        power = "" if n == 2 else f"^{{{n - 1}}}"
        f_tex, outer_tex = f"{_coef(k)}\\left({inner}\\right)^{{{n}}}", f"\\frac{{d}}{{du}}\\left({_coef(k)}u^{{{n}}}\\right) = {_coef(k * n)}u{power}"
        answer, answer_tex = f"{k * n * a}*{inner_py}**{n - 1}", f"{_coef(k * n * a)}\\left({inner}\\right){power}"
        mistakes = [(f"{k * n}*{inner_py}**{n - 1}", f"Multiply by the inner derivative ${a}$: that's the chain rule."),
                    (f"{k * n * a}*{inner_py}**{n}", "Lower the power by one: $\\frac{d}{du}u^n = nu^{n-1}$.")]
    elif outer == "exp":
        f_tex, outer_tex = f"{_coef(k)}e^{{{inner}}}", f"\\frac{{d}}{{du}}\\left({_coef(k)}e^{{u}}\\right) = {_coef(k)}e^{{u}}"
        answer, answer_tex = f"{k * a}*exp{inner_py}", f"{_coef(k * a)}e^{{{inner}}}"
        mistakes = [(f"{k}*exp{inner_py}", f"Multiply by the inner derivative ${a}$: that's the chain rule."),
                    (f"{k}*{inner_py}*exp({a}*x + ({b - 1}))", "$e^u$ isn't a power of $u$: it is its own derivative.")]
    elif outer == "sin":
        f_tex, outer_tex = f"{_coef(k)}\\sin\\left({inner}\\right)", f"\\frac{{d}}{{du}}\\left({_coef(k)}\\sin u\\right) = {_coef(k)}\\cos u"
        answer, answer_tex = f"{k * a}*cos{inner_py}", f"{_coef(k * a)}\\cos\\left({inner}\\right)"
        mistakes = [(f"{k}*cos{inner_py}", f"Multiply by the inner derivative ${a}$: that's the chain rule."),
                    (f"{-k * a}*cos{inner_py}", "$\\frac{d}{du}\\sin u = \\cos u$, with no minus sign.")]
    else:
        f_tex, outer_tex = f"{_coef(k)}\\cos\\left({inner}\\right)", f"\\frac{{d}}{{du}}\\left({_coef(k)}\\cos u\\right) = {_coef(-k)}\\sin u"
        answer, answer_tex = f"{-k * a}*sin{inner_py}", f"{_coef(-k * a)}\\sin\\left({inner}\\right)"
        mistakes = [(f"{-k}*sin{inner_py}", f"Multiply by the inner derivative ${a}$: that's the chain rule."),
                    (f"{k * a}*sin{inner_py}", "$\\frac{d}{du}\\cos u = -\\sin u$: watch the sign.")]
    return Spec({"outer": outer, "k": k, "a": a, "b": b, "n": n}, f"Differentiate $f(x) = {f_tex}$.", answer, [
        Step("Inner function", f"u = {inner}"),
        Step("Inner derivative", "u'", F(a)),
        Step("Outer derivative", outer_tex),
        Step("Chain rule: outer derivative at $u$ times $u'$", f"f'(x) = {answer_tex}"),
    ], kind="expression", variables=X, mistakes=mistakes)


_STANDARD = {"exp": ("e^{x}", "exp(x)"), "ln": ("\\ln x", "log(x)"), "sin": ("\\sin x", "sin(x)"), "cos": ("\\cos x", "cos(x)")}


@template("calc.standard-derivatives/combination", target_s=40)
def standard_derivatives(r):
    names = sorted(r.sample(["exp", "ln", "sin"], r.randint(1, 2)), key=list(_STANDARD).index) + ["cos"]
    coeffs = {name: r.choice([c for c in range(-9, 10) if c]) if name in names else 0 for name in _STANDARD}
    terms = {"exp": lambda c: (f"{_coef(c)}e^{{x}}", f"({c})*exp(x)"),
             "ln": lambda c: (f"{'-' if c < 0 else ''}\\frac{{{abs(c)}}}{{x}}", f"({c})/x"),
             "sin": lambda c: (f"{_coef(c)}\\cos x", f"({c})*cos(x)"),
             "cos": lambda c: (f"{_coef(-c)}\\sin x", f"({-c})*sin(x)")}
    f_tex = _join([f"{_coef(coeffs[name])}{_STANDARD[name][0]}" for name in names])
    derivative = [terms[name](coeffs[name]) for name in names]
    answer = " + ".join(py for _, py in derivative)
    s = coeffs["cos"]
    mistakes = [(answer.replace(f"({-s})*sin(x)", f"({s})*sin(x)"), "$\\frac{d}{dx}\\cos x = -\\sin x$: watch the sign.")]
    if coeffs["exp"]:
        mistakes.append((answer.replace(f"({coeffs['exp']})*exp(x)", f"({coeffs['exp']})*x*exp(x - 1)"), "$e^x$ isn't a power of $x$: it is its own derivative."))
    if coeffs["sin"]:
        mistakes.append((answer.replace(f"({coeffs['sin']})*cos(x)", f"({-coeffs['sin']})*cos(x)"), "$\\frac{d}{dx}\\sin x = \\cos x$, with no minus sign."))
    return Spec(coeffs, f"Differentiate $f(x) = {f_tex}$.", answer, [
        Step("Standard derivatives", "(e^x)' = e^x,\\quad (\\ln x)' = \\frac{1}{x},\\quad (\\sin x)' = \\cos x,\\quad (\\cos x)' = -\\sin x"),
        Step(f"$\\frac{{d}}{{dx}}\\left({_coef(s)}\\cos x\\right) = k\\sin x$ with", "k", F(-s)),
        Step("Term by term", f"f'(x) = {_join([text for text, _ in derivative])}"),
    ], kind="expression", variables=X, mistakes=mistakes)


def _join(terms) -> str:
    out = terms[0]
    for term in terms[1:]:
        out = _plus(out, term)
    return out


@template("calc.antiderivatives/polynomial", target_s=50)
def antiderivative(r):
    n = r.randint(2, 4)
    f = _random_poly(r, n, r.randint(2, n + 1))
    integral = (0,) + tuple(F(c, k + 1) for k, c in enumerate(f))
    return Spec({"f": f}, f"Find $\\displaystyle\\int \\left({_poly_tex(f)}\\right) dx$. {UP_TO_C}", _poly_py(integral), [
        Step("Reverse power rule, term by term", "\\int x^{n}\\,dx = \\frac{x^{n+1}}{n+1} + C"),
        Step(f"Coefficient of $x^{{{n + 1}}}$", f"\\frac{{{f[n]}}}{{{n + 1}}}", F(f[n], n + 1)),
        Step("All terms", f"{_poly_tex(integral)} + C"),
    ], kind="expression", variables=X, up_to_constant=True, mistakes=[
        (_poly_py((0,) + f), "Divide each term by its new power: $\\int x^n\\,dx = \\frac{x^{n+1}}{n+1}$."),
        (_poly_py(_d(f)), "That's the derivative; integrating raises each power by one."),
        (_poly_py((0,) + tuple(F(c, k or 1) for k, c in enumerate(f))), "Divide by the new power $n + 1$, not the old power $n$."),
    ])


@template("calc.definite/polynomial", target_s=60)
def definite_integral(r):
    degree = r.randint(1, 3)
    f = _random_poly(r, degree, r.randint(1, degree + 1), size=6)
    a, b = sorted(r.sample(range(-3, 4), 2))
    integral = (0,) + tuple(F(c, k + 1) for k, c in enumerate(f))
    upper, lower = _value(integral, b), _value(integral, a)
    answer = upper - lower
    return Spec({"f": f, "a": a, "b": b}, f"Evaluate $\\displaystyle\\int_{{{a}}}^{{{b}}} \\left({_poly_tex(f)}\\right) dx$.", answer, [
        Step("Antiderivative", f"F(x) = {_poly_tex(integral)}"),
        Step("Upper limit", f"F({b})", upper),
        Step("Lower limit", f"F({a})", lower),
        Step("Subtract", f"F({b}) - F({a}) = {tex(upper)} - {_p(lower)}", answer),
    ], kind="fraction", mistakes=[(lower - upper, "Upper limit minus lower: $F(b) - F(a)$."),
                                  (_value(f, b) - _value(f, a), "Integrate first: evaluate the antiderivative $F$, not $f$."),
                                  (upper, f"Subtract $F({a})$ too: the lower limit isn't 0.")])


@template("calc.integrals-exp-trig/linear-argument", target_s=45)
def integral_exp_trig(r):
    fn = r.choice(["exp", "sin", "cos"])
    a = r.choice([-5, -4, -3, -2, 2, 3, 4, 5, 6])
    k = r.choice([c for c in range(-9, 10) if c] + [2 * a, 3 * a])
    coef = F(k, a) * (-1 if fn == "sin" else 1)
    result = {"exp": "exp", "sin": "cos", "cos": "sin"}[fn]
    body = lambda name: f"e^{{{_arg(a)}}}" if name == "exp" else f"\\{name}({_arg(a)})"
    rule = {"exp": "\\int e^{ax}\\,dx = \\frac{1}{a}e^{ax} + C", "sin": "\\int \\sin(ax)\\,dx = -\\frac{1}{a}\\cos(ax) + C",
            "cos": "\\int \\cos(ax)\\,dx = \\frac{1}{a}\\sin(ax) + C"}[fn]
    sign = -1 if fn == "sin" else 1
    mistakes = [(f"({sign * k})*{result}({a}*x)", f"Divide by the inner coefficient ${a}$: differentiating $e^{{ax}}$, $\\sin(ax)$ or $\\cos(ax)$ brings out a factor $a$."),
                (f"({sign * k * a})*{result}({a}*x)", f"Divide by ${a}$, don't multiply: integrating undoes the chain rule's factor.")]
    if fn != "exp":
        mistakes.append((f"({-coef})*{result}({a}*x)", "$\\int \\sin = -\\cos$ and $\\int \\cos = \\sin$: watch the sign."))
    return Spec({"fn": fn, "k": k, "a": a}, f"Find $\\displaystyle\\int {_coef(k)}{body(fn)}\\,dx$. {UP_TO_C}", f"({coef})*{result}({a}*x)", [
        Step("Reverse chain rule", rule),
        Step("Coefficient", f"{'-' if fn == 'sin' else ''}\\frac{{{k}}}{{{a}}}", coef),
        Step("So", f"{_coef(coef)}{body(result)} + C"),
    ], kind="expression", variables=X, up_to_constant=True, mistakes=mistakes)


#: Maclaurin coefficient of u^k, TeX of the function of u and of its series.
_SERIES = {
    "exp": (lambda k: F(1, factorial(k)), "e^{u}", "\\sum_k \\frac{u^k}{k!}"),
    "sin": (lambda k: F((-1) ** (k // 2), factorial(k)) if k % 2 else F(0), "\\sin u", "u - \\frac{u^3}{3!} + \\frac{u^5}{5!} - \\cdots"),
    "cos": (lambda k: F(0) if k % 2 else F((-1) ** (k // 2), factorial(k)), "\\cos u", "1 - \\frac{u^2}{2!} + \\frac{u^4}{4!} - \\cdots"),
    "ln": (lambda k: F((-1) ** (k + 1), k) if k else F(0), "\\ln(1 + u)", "u - \\frac{u^2}{2} + \\frac{u^3}{3} - \\cdots"),
    "geometric": (lambda k: F(1), "\\frac{1}{1 - u}", "1 + u + u^2 + u^3 + \\cdots"),
}
_DEGREES = {"exp": (2, 3, 4), "sin": (3, 5), "cos": (2, 4), "ln": (2, 3, 4), "geometric": (2, 3, 4)}


def _function_at(fn: str, u: str, one_plus: str, one_minus: str) -> str:
    return {"exp": f"e^{{{u}}}", "sin": f"\\sin({u})", "cos": f"\\cos({u})", "ln": f"\\ln({one_plus})",
            "geometric": f"\\frac{{1}}{{{one_minus}}}"}[fn]


@template("calc.taylor/approximate", target_s=90)
def taylor_value(r):
    fn = r.choice(list(_SERIES))
    n = r.choice(_DEGREES[fn])
    h = r.choice([F(1, 10), F(1, 5), F(1, 2), F(3, 10), F(-1, 10), F(-1, 5)])
    coefficient, base, series = _SERIES[fn]
    coeffs = [coefficient(k) for k in range(n + 1)]
    terms = [(k, c * h ** k) for k, c in enumerate(coeffs) if c]
    answer = sum((value for _, value in terms), F(0))
    short = sum((value for k, value in terms if k < n), F(0))
    name = _function_at(fn, "x", "1 + x", "1 - x")
    target = _function_at(fn, num(h), num(1 + h), num(1 - h))
    mistakes = [(short, f"The degree-{n} polynomial includes the $x^{{{n}}}$ term.")]
    if fn in ("exp", "sin", "cos"):
        mistakes.append((sum((h ** k * (1 if c > 0 else -1) for k, c in enumerate(coeffs) if c), F(0)), "Divide the $x^k$ term by $k!$."))
    return Spec({"fn": fn, "n": n, "h": h},
                f"Use the degree-{n} Maclaurin polynomial of ${name}$ to approximate ${target}$. Give the polynomial's value exactly or to 6 significant figures.",
                answer, [
                    Step("Maclaurin series", f"{base} = {series}"),
                    Step(f"Degree {n}", f"T_{{{n}}}(x) = {_poly_tex(coeffs, ascending=True)}"),
                    *[Step(f"The $x^{{{k}}}$ term at $x = {num(h)}$", f"{tex(coeffs[k])} \\cdot ({num(h)})^{{{k}}}", value) for k, value in terms if k],
                    Step("Add the terms", f"T_{{{n}}}({num(h)})", answer),
                ], kind="fraction", sigfigs=6, mistakes=mistakes)


@template("calc.taylor/coefficient", target_s=60)
def taylor_coefficient(r):
    fn = r.choice(list(_SERIES))
    k = r.choice(_DEGREES[fn] + ((5,) if fn in ("exp", "geometric") else ()))
    a = r.choice([2, 3, -2, -3])
    coefficient, base, series = _SERIES[fn]
    c = coefficient(k)
    answer = c * a ** k
    name = _function_at(fn, _arg(a), _poly_tex((1, a), ascending=True), _poly_tex((1, -a), ascending=True))
    mistakes = [(c, f"Substituting $u = {_arg(a)}$ multiplies the $x^{{{k}}}$ term by $({a})^{{{k}}}$."),
                (c * a, f"Raise ${a}$ to the power {k}: $u^{{{k}}} = ({a}x)^{{{k}}}$.")]
    if fn in ("exp", "sin", "cos"):
        mistakes.append((F(a) ** k * (1 if c > 0 else -1), f"Divide by ${k}! = {factorial(k)}$."))
    return Spec({"fn": fn, "a": a, "k": k}, f"What is the coefficient of $x^{{{k}}}$ in the Maclaurin series of ${name}$?", answer, [
        Step("Maclaurin series", f"{base} = {series}"),
        Step(f"Coefficient of $u^{{{k}}}$", "", c),
        Step(f"Substitute $u = {_arg(a)}$", f"{_p(c)} \\cdot ({a})^{{{k}}}", answer),
    ], kind="fraction", mistakes=mistakes)
