//! `templates/calculus.py`: limits, derivatives by rule, integrals of polynomials, exp and trig, and Taylor approximations.

use crate::math::frac::{factorial, F};
use crate::math::problems::{line, num, step, template, tex, wrong, Spec, Template};
use crate::math::pyrandom::Random;

pub static TEMPLATES: &[Template] = &[
    template("calc.limits/factor", 45, limit_by_factoring),
    template("calc.limits-infinity/rational", 40, limit_at_infinity),
    template("calc.power-rule/polynomial", 45, power_rule),
    template("calc.slopes/at-point", 40, slope_at_point),
    template("calc.product-rule/times-function", 60, product_rule),
    template("calc.quotient-rule/over-polynomial", 75, quotient_rule),
    template("calc.chain-rule/linear-inner", 50, chain_rule),
    template("calc.standard-derivatives/combination", 40, standard_derivatives),
    template("calc.antiderivatives/polynomial", 50, antiderivative),
    template("calc.definite/polynomial", 60, definite_integral),
    template("calc.integrals-exp-trig/linear-argument", 45, integral_exp_trig),
    template("calc.taylor/approximate", 90, taylor_value),
    template("calc.taylor/coefficient", 60, taylor_coefficient),
];

const X: &[&str] = &["x"];
const UP_TO_C: &str = "Any constant of integration is fine.";

/// A function parameter: Python's `(name, c)` pair.
#[derive(Clone)]
enum Fun {
    Poly(Vec<F>),
    Ln,
    Exp(i64),
    Sin(i64),
    Cos(i64),
}

/// Integer coefficients as a polynomial.
fn poly(coeffs: &[i64]) -> Vec<F> {
    coeffs.iter().map(|&c| F::from(c)).collect()
}

/// `_coef`: a coefficient written in front of a function: `""` for 1, `"-"` for -1.
fn coef(value: F) -> String {
    if value == 1 { String::new() } else if value == -1 { "-".into() } else { tex(value) }
}

/// `_p`: a number as TeX, bracketed when negative so it can follow an operator.
fn p(value: F) -> String {
    if value < 0 { format!("({})", tex(value)) } else { tex(value) }
}

/// `_xpow`.
fn xpow(k: usize) -> String {
    match k {
        0 => String::new(),
        1 => "x".into(),
        _ => format!("x^{{{k}}}"),
    }
}

/// `_poly_tex`.
fn poly_tex(coeffs: &[F], ascending: bool) -> String {
    let mut out = String::new();
    let powers: Vec<usize> = if ascending { (0..coeffs.len()).collect() } else { (0..coeffs.len()).rev().collect() };
    for k in powers {
        let c = coeffs[k];
        if c.is_zero() {
            continue;
        }
        let size = if c.abs() != 1 || k == 0 { tex(c.abs()) } else { String::new() };
        let sign = if out.is_empty() { if c < 0 { "-" } else { "" } } else if c < 0 { " - " } else { " + " };
        out += &format!("{sign}{size}{}", xpow(k));
    }
    if out.is_empty() { "0".into() } else { out }
}

/// `_poly_py`.
fn poly_py(coeffs: &[F]) -> String {
    let terms: Vec<String> = coeffs.iter().enumerate().filter(|(_, c)| !c.is_zero()).map(|(k, &c)| {
        let head = if c < 0 { format!("({c})") } else { c.to_string() };
        let tail = match k {
            0 => String::new(),
            1 => "*x".into(),
            _ => format!("*x**{k}"),
        };
        head + &tail
    }).collect();
    if terms.is_empty() { "0".into() } else { terms.join(" + ") }
}

/// `_d`.
fn d(coeffs: &[F]) -> Vec<F> {
    let out: Vec<F> = coeffs.iter().enumerate().skip(1).map(|(k, &c)| c * k as i64).collect();
    if out.is_empty() { vec![F::ZERO] } else { out }
}

/// `_value`.
fn value(coeffs: &[F], at: F) -> F {
    coeffs.iter().enumerate().map(|(k, &c)| c * at.pow(k as i32)).sum()
}

/// `_random_poly`: `terms` nonzero coefficients including the leading one.
fn random_poly(r: &mut Random, degree: i64, terms: i64, size: i64) -> Vec<F> {
    let mut coeffs = vec![F::ZERO; degree as usize + 1];
    let mut powers = vec![degree];
    powers.extend(r.sample(&(0..degree).collect::<Vec<i64>>(), (terms - 1) as usize));
    let options: Vec<i64> = (-size..=size).filter(|&c| c != 0).collect();
    for k in powers {
        coeffs[k as usize] = F::from(r.choice(&options));
    }
    coeffs
}

/// `_wrap`.
fn wrap(text: &str) -> String {
    if text.starts_with('-') || text.contains(" + ") || text.contains(" - ") { format!("\\left({text}\\right)") } else { text.to_owned() }
}

/// `_times`.
fn times(a: &str, b: &str) -> String {
    if a == "1" || a == "-1" {
        return format!("{}{}", if a == "-1" { "-" } else { "" }, wrap(b));
    }
    format!("{}\\,{}", wrap(a), wrap(b))
}

/// `_plus`.
fn plus(a: &str, b: &str, minus: bool) -> String {
    if let Some(rest) = b.strip_prefix('-') {
        return format!("{a} {} {rest}", if minus { '+' } else { '-' });
    }
    format!("{a} {} {b}", if minus { '-' } else { '+' })
}

/// `_arg`.
fn arg(c: i64) -> String {
    poly_tex(&poly(&[0, c]), false)
}

/// `_fn`: TeX, SymPy text, derivative TeX and derivative SymPy text of a function parameter.
fn fun(spec: &Fun) -> (String, String, String, String) {
    match spec {
        Fun::Poly(c) => (poly_tex(c, false), poly_py(c), poly_tex(&d(c), false), poly_py(&d(c))),
        Fun::Ln => ("\\ln x".into(), "log(x)".into(), "\\frac{1}{x}".into(), "1/x".into()),
        &Fun::Exp(c) => (format!("e^{{{}}}", arg(c)), format!("exp({c}*x)"), format!("{}e^{{{}}}", coef(F::from(c)), arg(c)), format!("({c})*exp({c}*x)")),
        &Fun::Sin(c) => (format!("\\sin({})", arg(c)), format!("sin({c}*x)"), format!("{}\\cos({})", coef(F::from(c)), arg(c)), format!("({c})*cos({c}*x)")),
        &Fun::Cos(c) => (format!("\\cos({})", arg(c)), format!("cos({c}*x)"), format!("{}\\sin({})", coef(F::from(-c)), arg(c)), format!("({})*sin({c}*x)", -c)),
    }
}

/// `_at`: value and slope of a function parameter at `point` (0, or 1 for `ln`).
fn at(spec: &Fun, point: i64) -> (F, F) {
    match spec {
        Fun::Poly(c) => (value(c, F::from(point)), value(&d(c), F::from(point))),
        Fun::Ln => (F::ZERO, F::ONE),
        &Fun::Exp(c) => (F::ONE, F::from(c)),
        &Fun::Sin(c) => (F::ZERO, F::from(c)),
        Fun::Cos(_) => (F::ONE, F::ZERO),
    }
}

/// `_linear`.
fn linear(x0: i64) -> String {
    poly_tex(&poly(&[-x0, 1]), false)
}

fn nonzero(a: i64, b: i64) -> Vec<i64> {
    (a..b).filter(|&c| c != 0).collect()
}

fn limit_by_factoring(r: &mut Random) -> Spec {
    let a = r.choice(&nonzero(-6, 7));
    let form = r.choice(&["quadratic", "quadratic", "cubic"]);
    let (top, answer, factor, cancel, mut mistakes);
    if form == "quadratic" {
        let b = r.choice(&(-7..8).filter(|&n| n != -a).collect::<Vec<i64>>());
        top = poly(&[-a * b, b - a, 1]);
        answer = a + b;
        factor = format!("\\left({}\\right)\\left({}\\right)", linear(a), linear(-b));
        cancel = step(format!("Cancel $({})$ and substitute $x = {a}$", linear(a)), format!("{a} + {}", p(F::from(b))), answer);
        mistakes = vec![wrong(b - a, format!("Substitute the point the limit approaches, $x = {a}$, into ${}$.", linear(-b)))];
    } else {
        top = poly(&[-a * a * a, 0, 0, 1]);
        answer = 3 * a * a;
        factor = format!("\\left({}\\right)\\left({}\\right)", linear(a), poly_tex(&poly(&[a * a, a, 1]), false));
        let pa = p(F::from(a));
        cancel = step(format!("Cancel $({})$ and substitute $x = {a}$", linear(a)), format!("{pa}^2 + {pa} \\cdot {pa} + {pa}^2"), answer);
        mistakes = vec![wrong(a * a, "$x^3 - a^3 = (x - a)(x^2 + ax + a^2)$: all three terms of the second factor count.")];
    }
    mistakes.push(wrong(0, "Substituting straight away gives $\\frac{0}{0}$, which has no value: cancel the common factor first."));
    let top_tex = poly_tex(&top, false);
    Spec::new(format!("Find $\\displaystyle\\lim_{{x \\to {a}}} \\frac{{{top_tex}}}{{{}}}$.", linear(a)), answer, vec![
        line(format!("At $x = {a}$ top and bottom are both 0, so factor the top"), format!("{top_tex} = {factor}")),
        cancel,
    ]).mistakes(mistakes)
}

fn limit_at_infinity(r: &mut Random) -> Spec {
    let m = r.randint(1, 3);
    let n = if m == 1 || r.random() < 0.7 { m } else { m - 1 };
    let terms = r.randint(1, n + 1);
    let top = random_poly(r, n, terms, 9);
    let terms = r.randint(2, m + 1);
    let bottom = random_poly(r, m, terms, 9);
    let (pp, q) = (*top.last().unwrap(), *bottom.last().unwrap());
    let (answer, steps, mut mistakes);
    if n == m {
        answer = pp / q;
        steps = vec![step(format!("Divide top and bottom by ${}$; every lower power over it tends to 0, leaving the leading coefficients", xpow(m as usize)),
                          format!("\\frac{{{pp}}}{{{q}}}"), answer)];
        mistakes = vec![wrong(q / pp, "Top's leading coefficient over the bottom's, not the other way up.")];
    } else {
        answer = F::ZERO;
        steps = vec![step(format!("Divide top and bottom by ${}$: the top tends to 0 and the bottom to ${q}$", xpow(m as usize)),
                          format!("\\frac{{0}}{{{q}}}"), answer)];
        mistakes = vec![wrong(pp / q, "The bottom has the higher degree, so it grows faster and the limit is 0.")];
    }
    if !bottom[0].is_zero() {
        mistakes.push(wrong(top[0] / bottom[0], "That's the limit as $x \\to 0$; as $x \\to \\infty$ the highest powers dominate."));
    }
    let mut all = vec![line("Compare degrees", format!("\\deg(\\text{{top}}) = {n},\\quad \\deg(\\text{{bottom}}) = {m}"))];
    all.extend(steps);
    Spec::new(format!("Find $\\displaystyle\\lim_{{x \\to \\infty}} \\frac{{{}}}{{{}}}$.", poly_tex(&top, false), poly_tex(&bottom, false)), answer, all)
        .kind("fraction").mistakes(mistakes)
}

fn power_rule(r: &mut Random) -> Spec {
    let n = r.randint(3, 5);
    let terms = r.randint(3, 4);
    let f = random_poly(r, n, terms, 9);
    let df = d(&f);
    let fnn = f[n as usize];
    let mut mistakes = vec![
        wrong(poly_py(&f.iter().enumerate().map(|(k, &c)| c * k as i64).collect::<Vec<F>>()), "Lower each power by one: $\\frac{d}{dx}x^n = nx^{n-1}$."),
        wrong(poly_py(&f[1..]), "Bring the power down as a factor: $\\frac{d}{dx}ax^n = nax^{n-1}$."),
    ];
    if !f[0].is_zero() {
        mistakes.push(wrong(format!("{} + ({})", poly_py(&df), f[0]), "A constant term differentiates to 0."));
    }
    Spec::new(format!("Differentiate $f(x) = {}$. Enter $f'(x)$.", poly_tex(&f, false)), poly_py(&df), vec![
        line("Power rule, term by term", "\\frac{d}{dx}\\,ax^{n} = nax^{n-1}"),
        step(format!("Coefficient of $x^{{{}}}$", n - 1), format!("{n} \\cdot {}", p(fnn)), fnn * n),
        line("All terms", format!("f'(x) = {}", poly_tex(&df, false))),
    ]).kind("expression").variables(X).mistakes(mistakes)
}

fn slope_at_point(r: &mut Random) -> Spec {
    let (degree, terms) = (r.randint(2, 3), r.randint(2, 3));
    let f = random_poly(r, degree, terms, 6);
    let a = r.randint(-3, 3);
    let df = d(&f);
    let answer = value(&df, F::from(a));
    Spec::new(format!("What is the slope of $f(x) = {}$ at $x = {a}$?", poly_tex(&f, false)), answer, vec![
        line("Differentiate", format!("f'(x) = {}", poly_tex(&df, false))),
        step(format!("Substitute $x = {a}$"), format!("f'({a})"), answer),
    ]).mistakes(vec![
        wrong(value(&f, F::from(a)), format!("That's the height $f({a})$; the slope is $f'({a})$.")),
        wrong(value(&df, F::from(-a)), format!("Substitute $x = {a}$ exactly, sign included.")),
    ])
}

fn product_rule(r: &mut Random) -> Spec {
    let n = r.randint(1, 3);
    let mut coeffs = vec![r.choice(&nonzero(-6, 7))];
    coeffs.extend(vec![0; (n - 1) as usize]);
    coeffs.push(r.choice(&nonzero(-5, 6)));
    let u = Fun::Poly(poly(&coeffs));
    let name = r.choice(&["exp", "sin", "cos", "ln"]);
    // Not e^x itself: with v' = v, forgetting uv' and multiplying the derivatives would coincide.
    let v = match name {
        "ln" => Fun::Ln,
        "exp" => Fun::Exp(r.choice(&[-3, -2, -1, 2, 3])),
        "sin" => Fun::Sin(r.choice(&[-3, -2, -1, 1, 2, 3])),
        _ => Fun::Cos(r.choice(&[-3, -2, -1, 1, 2, 3])),
    };
    let ((u_tex, u_py, du_tex, du_py), (v_tex, v_py, dv_tex, dv_py)) = (fun(&u), fun(&v));
    let point = if name == "ln" { 1 } else { 0 };
    let ((u0, du0), (v0, dv0)) = (at(&u, point), at(&v, point));
    let slope = du0 * v0 + u0 * dv0;
    Spec::new(format!("Differentiate $f(x) = {}$.", times(&u_tex, &v_tex)), format!("({du_py})*({v_py}) + ({u_py})*({dv_py})"), vec![
        line("The factors", format!("u = {u_tex},\\quad v = {v_tex}")),
        line("Differentiate each", format!("u' = {du_tex},\\quad v' = {dv_tex}")),
        line("Product rule $(uv)' = u'v + uv'$", format!("f'(x) = {}", plus(&times(&du_tex, &v_tex), &times(&u_tex, &dv_tex), false))),
        step(format!("Check: the slope at $x = {point}$"), format!("f'({point}) = {} \\cdot {} + {} \\cdot {}", p(du0), p(v0), p(u0), p(dv0)), slope),
    ]).kind("expression").variables(X).mistakes(vec![
        wrong(format!("({du_py})*({dv_py})"), "The derivative of a product isn't the product of the derivatives: $(uv)' = u'v + uv'$."),
        wrong(format!("({du_py})*({v_py}) - ({u_py})*({dv_py})"), "The product rule adds the two terms; the minus sign belongs to the quotient rule."),
        wrong(format!("({du_py})*({v_py})"), "Differentiate both factors: add $uv'$ as well."),
    ])
}

/// `_proportional`.
fn proportional(a: &[F], b: &[F]) -> bool {
    let len = a.len().max(b.len());
    let pad = |x: &[F]| { let mut x = x.to_vec(); x.resize(len, F::ZERO); x };
    let (a, b) = (pad(a), pad(b));
    (0..len).all(|i| (0..len).all(|j| a[i] * b[j] == a[j] * b[i]))
}

fn quotient_rule(r: &mut Random) -> Spec {
    let nz = nonzero(-5, 6);
    let (bottom, top) = loop {
        let bottom = if r.random() < 0.6 { poly(&[r.choice(&nz), r.choice(&nz)]) } else { poly(&[r.randint(1, 6), 0, 1]) };
        let kind = r.choice(&["linear", "quadratic", "exp", "sin"]);
        let top = match kind {
            "linear" => Fun::Poly(poly(&[r.randint(-5, 5), r.choice(&nz)])),
            "quadratic" => Fun::Poly(poly(&[r.choice(&nz), 0, r.choice(&nz)])),
            "exp" => Fun::Exp(r.choice(&[-2, -1, 1, 2, 3])),
            _ => Fun::Sin(r.choice(&[-2, -1, 1, 2, 3])),
        };
        match &top {
            Fun::Poly(c) if proportional(c, &bottom) => continue,
            _ => break (bottom, top),
        }
    };
    let bottom = Fun::Poly(bottom);
    let ((u_tex, u_py, du_tex, du_py), (v_tex, v_py, dv_tex, dv_py)) = (fun(&top), fun(&bottom));
    let ((u0, du0), (v0, dv0)) = (at(&top, 0), at(&bottom, 0));
    let slope = (du0 * v0 - u0 * dv0) / v0.pow(2);
    let numerator = plus(&times(&du_tex, &v_tex), &times(&u_tex, &dv_tex), true);
    Spec::new(format!("Differentiate $f(x) = \\dfrac{{{u_tex}}}{{{v_tex}}}$."), format!("(({du_py})*({v_py}) - ({u_py})*({dv_py}))/({v_py})**2"), vec![
        line("Top and bottom", format!("u = {u_tex},\\quad v = {v_tex}")),
        line("Differentiate each", format!("u' = {du_tex},\\quad v' = {dv_tex}")),
        line("Quotient rule $\\left(\\frac{u}{v}\\right)' = \\frac{u'v - uv'}{v^2}$", format!("f'(x) = \\frac{{{numerator}}}{{\\left({v_tex}\\right)^{{2}}}}")),
        step("Check: the slope at $x = 0$", format!("f'(0) = \\frac{{{} \\cdot {} - {} \\cdot {}}}{{{}^2}}", p(du0), p(v0), p(u0), p(dv0), p(v0)), slope),
    ]).kind("expression").variables(X).mistakes(vec![
        wrong(format!("(({u_py})*({dv_py}) - ({du_py})*({v_py}))/({v_py})**2"), "The numerator is $u'v - uv'$: the top's derivative comes first."),
        wrong(format!("({du_py})/({dv_py})"), "Differentiating top and bottom separately isn't the quotient rule."),
        wrong(format!("(({du_py})*({v_py}) - ({u_py})*({dv_py}))/({v_py})"), "Divide by $v^2$, the bottom squared."),
    ])
}

fn chain_rule(r: &mut Random) -> Spec {
    let outer = r.choice(&["power", "power", "exp", "sin", "cos"]);
    let (a, k) = (r.choice(&[-5, -4, -3, -2, 2, 3, 4, 5]), r.choice(&[1, 1, 2, 3, -1, -2]));
    let b = if outer == "power" { r.choice(&nonzero(-5, 6)) } else { r.randint(-4, 4) };
    let n = if outer == "power" { r.randint(2, 6) } else { 1 };
    let (inner, inner_py) = (poly_tex(&poly(&[b, a]), false), format!("({a}*x + ({b}))"));
    let c = |v: i64| coef(F::from(v));
    let (f_tex, outer_tex, answer, answer_tex, mistakes);
    match outer {
        "power" => {
            let power = if n == 2 { String::new() } else { format!("^{{{}}}", n - 1) };
            f_tex = format!("{}\\left({inner}\\right)^{{{n}}}", c(k));
            outer_tex = format!("\\frac{{d}}{{du}}\\left({}u^{{{n}}}\\right) = {}u{power}", c(k), c(k * n));
            answer = format!("{}*{inner_py}**{}", k * n * a, n - 1);
            answer_tex = format!("{}\\left({inner}\\right){power}", c(k * n * a));
            mistakes = vec![wrong(format!("{}*{inner_py}**{}", k * n, n - 1), format!("Multiply by the inner derivative ${a}$: that's the chain rule.")),
                            wrong(format!("{}*{inner_py}**{n}", k * n * a), "Lower the power by one: $\\frac{d}{du}u^n = nu^{n-1}$.")];
        }
        "exp" => {
            f_tex = format!("{}e^{{{inner}}}", c(k));
            outer_tex = format!("\\frac{{d}}{{du}}\\left({}e^{{u}}\\right) = {}e^{{u}}", c(k), c(k));
            answer = format!("{}*exp{inner_py}", k * a);
            answer_tex = format!("{}e^{{{inner}}}", c(k * a));
            mistakes = vec![wrong(format!("{k}*exp{inner_py}"), format!("Multiply by the inner derivative ${a}$: that's the chain rule.")),
                            wrong(format!("{k}*{inner_py}*exp({a}*x + ({}))", b - 1), "$e^u$ isn't a power of $u$: it is its own derivative.")];
        }
        "sin" => {
            f_tex = format!("{}\\sin\\left({inner}\\right)", c(k));
            outer_tex = format!("\\frac{{d}}{{du}}\\left({}\\sin u\\right) = {}\\cos u", c(k), c(k));
            answer = format!("{}*cos{inner_py}", k * a);
            answer_tex = format!("{}\\cos\\left({inner}\\right)", c(k * a));
            mistakes = vec![wrong(format!("{k}*cos{inner_py}"), format!("Multiply by the inner derivative ${a}$: that's the chain rule.")),
                            wrong(format!("{}*cos{inner_py}", -k * a), "$\\frac{d}{du}\\sin u = \\cos u$, with no minus sign.")];
        }
        _ => {
            f_tex = format!("{}\\cos\\left({inner}\\right)", c(k));
            outer_tex = format!("\\frac{{d}}{{du}}\\left({}\\cos u\\right) = {}\\sin u", c(k), c(-k));
            answer = format!("{}*sin{inner_py}", -k * a);
            answer_tex = format!("{}\\sin\\left({inner}\\right)", c(-k * a));
            mistakes = vec![wrong(format!("{}*sin{inner_py}", -k), format!("Multiply by the inner derivative ${a}$: that's the chain rule.")),
                            wrong(format!("{}*sin{inner_py}", k * a), "$\\frac{d}{du}\\cos u = -\\sin u$: watch the sign.")];
        }
    }
    Spec::new(format!("Differentiate $f(x) = {f_tex}$."), answer, vec![
        line("Inner function", format!("u = {inner}")),
        step("Inner derivative", "u'", a),
        line("Outer derivative", outer_tex),
        line("Chain rule: outer derivative at $u$ times $u'$", format!("f'(x) = {answer_tex}")),
    ]).kind("expression").variables(X).mistakes(mistakes)
}

/// `_STANDARD`: names in order, with TeX of the function.
const STANDARD: [(&str, &str); 4] = [("exp", "e^{x}"), ("ln", "\\ln x"), ("sin", "\\sin x"), ("cos", "\\cos x")];

fn standard_derivatives(r: &mut Random) -> Spec {
    let count = r.randint(1, 2) as usize;
    let mut names = r.sample(&["exp", "ln", "sin"], count);
    names.sort_by_key(|name| STANDARD.iter().position(|(n, _)| n == name));
    names.push("cos");
    let coeffs: Vec<i64> = STANDARD.iter().map(|(name, _)| if names.contains(name) { r.choice(&nonzero(-9, 10)) } else { 0 }).collect();
    let get = |name: &str| coeffs[STANDARD.iter().position(|(n, _)| *n == name).unwrap()];
    let term = |name: &str, c: i64| -> (String, String) {
        match name {
            "exp" => (format!("{}e^{{x}}", coef(F::from(c))), format!("({c})*exp(x)")),
            "ln" => (format!("{}\\frac{{{}}}{{x}}", if c < 0 { "-" } else { "" }, c.abs()), format!("({c})/x")),
            "sin" => (format!("{}\\cos x", coef(F::from(c))), format!("({c})*cos(x)")),
            _ => (format!("{}\\sin x", coef(F::from(-c))), format!("({})*sin(x)", -c)),
        }
    };
    let f_tex = join(&names.iter().map(|name| format!("{}{}", coef(F::from(get(name))), STANDARD.iter().find(|(n, _)| n == name).unwrap().1)).collect::<Vec<_>>());
    let derivative: Vec<(String, String)> = names.iter().map(|name| term(name, get(name))).collect();
    let answer = derivative.iter().map(|(_, py)| py.as_str()).collect::<Vec<_>>().join(" + ");
    let s = get("cos");
    let mut mistakes = vec![wrong(answer.replace(&format!("({})*sin(x)", -s), &format!("({s})*sin(x)")), "$\\frac{d}{dx}\\cos x = -\\sin x$: watch the sign.")];
    let e = get("exp");
    if e != 0 {
        mistakes.push(wrong(answer.replace(&format!("({e})*exp(x)"), &format!("({e})*x*exp(x - 1)")), "$e^x$ isn't a power of $x$: it is its own derivative."));
    }
    let si = get("sin");
    if si != 0 {
        mistakes.push(wrong(answer.replace(&format!("({si})*cos(x)"), &format!("({})*cos(x)", -si)), "$\\frac{d}{dx}\\sin x = \\cos x$, with no minus sign."));
    }
    Spec::new(format!("Differentiate $f(x) = {f_tex}$."), answer, vec![
        line("Standard derivatives", "(e^x)' = e^x,\\quad (\\ln x)' = \\frac{1}{x},\\quad (\\sin x)' = \\cos x,\\quad (\\cos x)' = -\\sin x"),
        step(format!("$\\frac{{d}}{{dx}}\\left({}\\cos x\\right) = k\\sin x$ with", coef(F::from(s))), "k", -s),
        line("Term by term", format!("f'(x) = {}", join(&derivative.iter().map(|(text, _)| text.clone()).collect::<Vec<_>>()))),
    ]).kind("expression").variables(X).mistakes(mistakes)
}

/// `_join`.
fn join(terms: &[String]) -> String {
    let mut out = terms[0].clone();
    for term in &terms[1..] {
        out = plus(&out, term, false);
    }
    out
}

/// `(0,) + tuple(F(c, k + 1) for k, c in enumerate(f))`.
fn integrate(f: &[F]) -> Vec<F> {
    let mut out = vec![F::ZERO];
    out.extend(f.iter().enumerate().map(|(k, &c)| c / (k as i64 + 1)));
    out
}

fn antiderivative(r: &mut Random) -> Spec {
    let n = r.randint(2, 4);
    let terms = r.randint(2, n + 1);
    let f = random_poly(r, n, terms, 9);
    let integral = integrate(&f);
    let fnn = f[n as usize];
    let mut shifted = vec![F::ZERO];
    shifted.extend(f.iter().copied());
    let mut old = vec![F::ZERO];
    old.extend(f.iter().enumerate().map(|(k, &c)| c / (k.max(1) as i64)));
    Spec::new(format!("Find $\\displaystyle\\int \\left({}\\right) dx$. {UP_TO_C}", poly_tex(&f, false)), poly_py(&integral), vec![
        line("Reverse power rule, term by term", "\\int x^{n}\\,dx = \\frac{x^{n+1}}{n+1} + C"),
        step(format!("Coefficient of $x^{{{}}}$", n + 1), format!("\\frac{{{fnn}}}{{{}}}", n + 1), fnn / (n + 1)),
        line("All terms", format!("{} + C", poly_tex(&integral, false))),
    ]).kind("expression").variables(X).up_to_constant().mistakes(vec![
        wrong(poly_py(&shifted), "Divide each term by its new power: $\\int x^n\\,dx = \\frac{x^{n+1}}{n+1}$."),
        wrong(poly_py(&d(&f)), "That's the derivative; integrating raises each power by one."),
        wrong(poly_py(&old), "Divide by the new power $n + 1$, not the old power $n$."),
    ])
}

fn definite_integral(r: &mut Random) -> Spec {
    let degree = r.randint(1, 3);
    let terms = r.randint(1, degree + 1);
    let f = random_poly(r, degree, terms, 6);
    let mut ends = r.sample(&(-3..4).collect::<Vec<i64>>(), 2);
    ends.sort();
    let (a, b) = (ends[0], ends[1]);
    let integral = integrate(&f);
    let (upper, lower) = (value(&integral, F::from(b)), value(&integral, F::from(a)));
    let answer = upper - lower;
    Spec::new(format!("Evaluate $\\displaystyle\\int_{{{a}}}^{{{b}}} \\left({}\\right) dx$.", poly_tex(&f, false)), answer, vec![
        line("Antiderivative", format!("F(x) = {}", poly_tex(&integral, false))),
        step("Upper limit", format!("F({b})"), upper),
        step("Lower limit", format!("F({a})"), lower),
        step("Subtract", format!("F({b}) - F({a}) = {} - {}", tex(upper), p(lower)), answer),
    ]).kind("fraction").mistakes(vec![
        wrong(lower - upper, "Upper limit minus lower: $F(b) - F(a)$."),
        wrong(value(&f, F::from(b)) - value(&f, F::from(a)), "Integrate first: evaluate the antiderivative $F$, not $f$."),
        wrong(upper, format!("Subtract $F({a})$ too: the lower limit isn't 0.")),
    ])
}

fn integral_exp_trig(r: &mut Random) -> Spec {
    let fname = r.choice(&["exp", "sin", "cos"]);
    let a = r.choice(&[-5, -4, -3, -2, 2, 3, 4, 5, 6]);
    let mut options = nonzero(-9, 10);
    options.extend([2 * a, 3 * a]);
    let k = r.choice(&options);
    let sign = if fname == "sin" { -1 } else { 1 };
    let coefficient = F::new(k, a) * sign;
    let result = match fname { "exp" => "exp", "sin" => "cos", _ => "sin" };
    let body = |name: &str| if name == "exp" { format!("e^{{{}}}", arg(a)) } else { format!("\\{name}({})", arg(a)) };
    let rule = match fname {
        "exp" => "\\int e^{ax}\\,dx = \\frac{1}{a}e^{ax} + C",
        "sin" => "\\int \\sin(ax)\\,dx = -\\frac{1}{a}\\cos(ax) + C",
        _ => "\\int \\cos(ax)\\,dx = \\frac{1}{a}\\sin(ax) + C",
    };
    let mut mistakes = vec![
        wrong(format!("({})*{result}({a}*x)", sign * k), format!("Divide by the inner coefficient ${a}$: differentiating $e^{{ax}}$, $\\sin(ax)$ or $\\cos(ax)$ brings out a factor $a$.")),
        wrong(format!("({})*{result}({a}*x)", sign * k * a), format!("Divide by ${a}$, don't multiply: integrating undoes the chain rule's factor.")),
    ];
    if fname != "exp" {
        mistakes.push(wrong(format!("({})*{result}({a}*x)", -coefficient), "$\\int \\sin = -\\cos$ and $\\int \\cos = \\sin$: watch the sign."));
    }
    Spec::new(format!("Find $\\displaystyle\\int {}{}\\,dx$. {UP_TO_C}", coef(F::from(k)), body(fname)), format!("({coefficient})*{result}({a}*x)"), vec![
        line("Reverse chain rule", rule),
        step("Coefficient", format!("{}\\frac{{{k}}}{{{a}}}", if fname == "sin" { "-" } else { "" }), coefficient),
        line("So", format!("{}{} + C", coef(coefficient), body(result))),
    ]).kind("expression").variables(X).up_to_constant().mistakes(mistakes)
}

/// `_SERIES` keys in order.
const SERIES: [&str; 5] = ["exp", "sin", "cos", "ln", "geometric"];

/// `_SERIES[fn]`: the Maclaurin coefficient of u^k.
fn series_coefficient(fname: &str, k: i64) -> F {
    let sign = |e: i64| if e % 2 == 0 { 1 } else { -1 };
    match fname {
        "exp" => F::new(1, factorial(k)),
        "sin" => if k % 2 == 1 { F::new(sign(k / 2), factorial(k)) } else { F::ZERO },
        "cos" => if k % 2 == 1 { F::ZERO } else { F::new(sign(k / 2), factorial(k)) },
        "ln" => if k != 0 { F::new(sign(k + 1), k) } else { F::ZERO },
        _ => F::ONE,
    }
}

/// `_SERIES[fn]`: TeX of the function of u and of its series.
fn series_tex(fname: &str) -> (&'static str, &'static str) {
    match fname {
        "exp" => ("e^{u}", "\\sum_k \\frac{u^k}{k!}"),
        "sin" => ("\\sin u", "u - \\frac{u^3}{3!} + \\frac{u^5}{5!} - \\cdots"),
        "cos" => ("\\cos u", "1 - \\frac{u^2}{2!} + \\frac{u^4}{4!} - \\cdots"),
        "ln" => ("\\ln(1 + u)", "u - \\frac{u^2}{2} + \\frac{u^3}{3} - \\cdots"),
        _ => ("\\frac{1}{1 - u}", "1 + u + u^2 + u^3 + \\cdots"),
    }
}

/// `_DEGREES`.
fn degrees(fname: &str) -> &'static [i64] {
    match fname {
        "sin" => &[3, 5],
        "cos" => &[2, 4],
        _ => &[2, 3, 4],
    }
}

/// `_function_at`.
fn function_at(fname: &str, u: &str, one_plus: &str, one_minus: &str) -> String {
    match fname {
        "exp" => format!("e^{{{u}}}"),
        "sin" => format!("\\sin({u})"),
        "cos" => format!("\\cos({u})"),
        "ln" => format!("\\ln({one_plus})"),
        _ => format!("\\frac{{1}}{{{one_minus}}}"),
    }
}

fn taylor_value(r: &mut Random) -> Spec {
    let fname = r.choice(&SERIES);
    let n = r.choice(degrees(fname));
    let h = r.choice(&[F::new(1, 10), F::new(1, 5), F::new(1, 2), F::new(3, 10), F::new(-1, 10), F::new(-1, 5)]);
    let (base, series) = series_tex(fname);
    let coeffs: Vec<F> = (0..=n).map(|k| series_coefficient(fname, k)).collect();
    let terms: Vec<(usize, F)> = coeffs.iter().enumerate().filter(|(_, c)| !c.is_zero()).map(|(k, &c)| (k, c * h.pow(k as i32))).collect();
    let answer: F = terms.iter().map(|&(_, v)| v).sum();
    let short: F = terms.iter().filter(|&&(k, _)| (k as i64) < n).map(|&(_, v)| v).sum();
    let name = function_at(fname, "x", "1 + x", "1 - x");
    let target = function_at(fname, &num(h), &num(h + 1), &num(F::ONE - h));
    let mut mistakes = vec![wrong(short, format!("The degree-{n} polynomial includes the $x^{{{n}}}$ term."))];
    if ["exp", "sin", "cos"].contains(&fname) {
        let signed: F = coeffs.iter().enumerate().filter(|(_, c)| !c.is_zero()).map(|(k, &c)| h.pow(k as i32) * if c > 0 { 1 } else { -1 }).sum();
        mistakes.push(wrong(signed, "Divide the $x^k$ term by $k!$."));
    }
    let mut steps = vec![
        line("Maclaurin series", format!("{base} = {series}")),
        line(format!("Degree {n}"), format!("T_{{{n}}}(x) = {}", poly_tex(&coeffs, true))),
    ];
    steps.extend(terms.iter().filter(|&&(k, _)| k != 0).map(|&(k, v)| {
        step(format!("The $x^{{{k}}}$ term at $x = {}$", num(h)), format!("{} \\cdot ({})^{{{k}}}", tex(coeffs[k]), num(h)), v)
    }));
    steps.push(step("Add the terms", format!("T_{{{n}}}({})", num(h)), answer));
    Spec::new(format!("Use the degree-{n} Maclaurin polynomial of ${name}$ to approximate ${target}$. Give the polynomial's value exactly or to 6 significant figures."),
              answer, steps).kind("fraction").sigfigs(6).mistakes(mistakes)
}

fn taylor_coefficient(r: &mut Random) -> Spec {
    let fname = r.choice(&SERIES);
    let mut options = degrees(fname).to_vec();
    if fname == "exp" || fname == "geometric" {
        options.push(5);
    }
    let k = r.choice(&options);
    let a = r.choice(&[2, 3, -2, -3]);
    let (base, series) = series_tex(fname);
    let c = series_coefficient(fname, k);
    let power = F::from(a).pow(k as i32);
    let answer = c * power;
    let name = function_at(fname, &arg(a), &poly_tex(&poly(&[1, a]), true), &poly_tex(&poly(&[1, -a]), true));
    let mut mistakes = vec![
        wrong(c, format!("Substituting $u = {}$ multiplies the $x^{{{k}}}$ term by $({a})^{{{k}}}$.", arg(a))),
        wrong(c * a, format!("Raise ${a}$ to the power {k}: $u^{{{k}}} = ({a}x)^{{{k}}}$.")),
    ];
    if ["exp", "sin", "cos"].contains(&fname) {
        mistakes.push(wrong(power * if c > 0 { 1 } else { -1 }, format!("Divide by ${k}! = {}$.", factorial(k))));
    }
    Spec::new(format!("What is the coefficient of $x^{{{k}}}$ in the Maclaurin series of ${name}$?"), answer, vec![
        line("Maclaurin series", format!("{base} = {series}")),
        step(format!("Coefficient of $u^{{{k}}}$"), "", c),
        step(format!("Substitute $u = {}$", arg(a)), format!("{} \\cdot ({a})^{{{k}}}", p(c)), answer),
    ]).kind("fraction").mistakes(mistakes)
}
