//! `templates/discrete.py`: discrete math for tech: modular arithmetic, recurrences, big-O, loop counts and graph counting.

use super::max_by;
use crate::math::frac::{comb, floordiv, igcd, ilcm, ipow, perm, powmod, pymod, F};
use crate::math::problems::{line, note, step, template, wrong, Answer, Spec, Template};
use crate::math::pyrandom::Random;

pub static TEMPLATES: &[Template] = &[
    template("disc.mod-arith/expression", 30, mod_arith),
    template("disc.mod-power/repeated-squaring", 90, mod_power),
    template("disc.gcd/euclid", 60, gcd_euclid),
    template("disc.mod-inverse/extended-euclid", 120, mod_inverse),
    template("disc.recurrence/iterate", 60, recurrence),
    template("disc.recurrence-closed/characteristic", 180, recurrence_closed),
    template("disc.loop-count/nested", 60, loop_count),
    template("disc.big-o/cost", 30, big_o_cost),
    template("disc.big-o/loops", 40, big_o_loops),
    template("disc.graph-edges/handshake", 30, graph_edges),
    template("disc.graph-counting/labelled", 45, graph_counting),
    template("disc.lattice-paths/grid", 45, lattice_paths),
];

/// `_paren`.
fn paren(value: i64) -> String {
    if value < 0 { format!("({value})") } else { value.to_string() }
}

/// `_lin`: TeX for a sum of `(coefficient, symbol)` terms, e.g. `2a_{n-1} - a_{n-2}`.
fn lin(terms: &[(i64, &str)]) -> String {
    let mut out = String::new();
    for &(c, symbol) in terms {
        if c == 0 {
            continue;
        }
        let piece = format!("{}{symbol}", if c.abs() == 1 && !symbol.is_empty() { String::new() } else { c.abs().to_string() });
        if out.is_empty() {
            out = format!("{}{piece}", if c < 0 { "-" } else { "" });
        } else {
            out += &format!(" {} {piece}", if c < 0 { '-' } else { '+' });
        }
    }
    if out.is_empty() { "0".into() } else { out }
}

fn mod_arith(r: &mut Random) -> Spec {
    let n = r.randint(5, 30);
    let (a, b, c) = (r.randint(10, 99), r.randint(10, 99), r.randint(10, 999));
    let minus = r.random() < 0.4;
    let sign = if minus { -1 } else { 1 };
    let value = a * b + sign * c;
    let answer = pymod(value, n);
    let small = a % n * (b % n);
    let partial = small % n + sign * (c % n);
    let op = if minus { "-" } else { "+" };
    let truncated = value % n; // C's % keeps the sign of a negative value
    Spec::new(format!("Compute $({a} \\cdot {b} {op} {c}) \\bmod {n}$, as a value from $0$ to ${}$.", n - 1), answer, vec![
        step("Reduce the factors first", format!("{a} \\cdot {b} \\equiv {} \\cdot {}", a % n, b % n), small),
        step("Reduce the product", format!("{small} \\bmod {n}"), small % n),
        step(format!("{} ${c} \\equiv {}$", if minus { "Subtract" } else { "Add" }, c % n), format!("{} {op} {}", small % n, c % n), partial),
        step(format!("Bring it into $0, \\dots, {}$", n - 1), format!("{partial} \\bmod {n}"), answer),
    ]).mistakes(vec![
        wrong(value, format!("Reduce mod ${n}$: the answer must lie between $0$ and ${}$.", n - 1)),
        wrong(truncated, format!("A negative remainder isn't reduced yet: add ${n}$.")),
        wrong(small % n + sign * c, format!("Reduce ${c}$ mod ${n}$ as well.")),
    ])
}

fn mod_power(r: &mut Random) -> Spec {
    let n = r.randint(7, 60);
    let a = r.choice(&(2..41).filter(|x| x % n > 1).collect::<Vec<i64>>());
    let k = r.randint(13, 100);
    let bit_length = 64 - k.leading_zeros() as i64;
    let bits: Vec<i64> = (0..bit_length).filter(|i| k >> i & 1 == 1).collect();
    let mut squares = vec![a % n];
    for _ in 0..bit_length - 1 {
        let last = squares[squares.len() - 1];
        squares.push(last * last % n);
    }
    let used: Vec<i64> = bits.iter().map(|&i| squares[i as usize]).collect();
    let product: i64 = used.iter().product();
    let answer = product % n;
    let mut steps = vec![
        line("Write the exponent in binary", format!("{k} = {}", bits.iter().rev().map(|i| (1i64 << i).to_string()).collect::<Vec<_>>().join(" + "))),
        step("Reduce the base", format!("{a} \\bmod {n}"), squares[0]),
    ];
    for i in 1..squares.len() {
        steps.push(step(format!("Square for ${a}^{{{}}}$", 1i64 << i), format!("{}^2 \\bmod {n}", squares[i - 1]), squares[i]));
    }
    steps.push(step("Multiply the squares the binary digits pick",
                    format!("{} \\bmod {n}", used.iter().rev().map(|v| v.to_string()).collect::<Vec<_>>().join(" \\cdot ")), answer));
    Spec::new(format!("Compute ${a}^{{{k}}} \\bmod {n}$ by repeated squaring."), answer, steps).mistakes(vec![
        wrong(product, format!("Reduce the final product mod ${n}$ too.")),
        wrong(a * k % n, format!("A power multiplies ${a}$ by itself ${k}$ times; ${a} \\cdot {k}$ is a product.")),
        wrong(powmod(a, (k % n) as u64, n), format!("Exponents don't reduce mod ${n}$ (Fermat and Euler reduce them mod $\\varphi(n)$).")),
    ])
}

fn gcd_euclid(r: &mut Random) -> Spec {
    let (a, b, remainders) = loop {
        let g = r.choice(&[1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 15, 17, 18, 21, 24]);
        let (x, y) = (r.randint(8, 120), r.randint(5, 100));
        let (a, b) = ((g * x).max(g * y), (g * x).min(g * y));
        let (mut remainders, mut p, mut q) = (Vec::new(), a, b);
        while q != 0 {
            (p, q) = (q, p % q);
            remainders.push(q);
        }
        if remainders.len() >= 3 && a <= 2000 {
            break (a, b, remainders);
        }
    };
    let answer = igcd(a, b);
    let (mut steps, mut p, mut q) = (Vec::new(), a, b);
    while q != 0 {
        steps.push(step(format!("${p} = {} \\cdot {q} + r$, so", p / q), format!("r = {p} \\bmod {q}"), p % q));
        (p, q) = (q, p % q);
    }
    steps.push(step("The last non-zero remainder", format!("\\gcd({a}, {b})"), answer));
    Spec::new(format!("Use Euclid's algorithm to find $\\gcd({a}, {b})$."), answer, steps).mistakes(vec![
        wrong(0, "The final remainder is $0$; the gcd is the last non-zero remainder before it."),
        wrong(remainders[remainders.len() - 3], "You stopped one step early: keep dividing until the remainder is $0$."),
        wrong(ilcm(a, b), "That's the least common multiple; the gcd divides both numbers."),
    ])
}

fn mod_inverse(r: &mut Random) -> Spec {
    let n = r.randint(11, 97);
    let a = r.choice(&(2..n - 1).filter(|&x| igcd(x, n) == 1).collect::<Vec<i64>>());
    // Invariant: each remainder r_i ≡ t_i · a (mod n); the row with remainder 1 gives the inverse.
    let mut steps = vec![step(format!("$\\gcd({a}, {n}) = 1$, so the inverse exists; track $t$ with remainder $\\equiv t \\cdot {a}$, starting from"),
                              "t_0 = 0,\\ t_1", 1)];
    let (mut r0, mut r1, mut t0, mut t1) = (n, a, 0i64, 1i64);
    while r1 != 1 {
        let q = floordiv(r0, r1);
        steps.push(step(format!("${r0} = {q} \\cdot {r1} + {}$, so ${} \\equiv t \\cdot {a}$ with", r0 - q * r1, r0 - q * r1),
                        format!("t = {t0} - {q} \\cdot {}", paren(t1)), t0 - q * t1));
        (r0, r1, t0, t1) = (r1, r0 - q * r1, t1, t0 - q * t1);
    }
    let answer = pymod(t1, n);
    steps.push(step(format!("Reduce $t$ into $0, \\dots, {}$", n - 1), format!("{t1} \\bmod {n}"), answer));
    Spec::new(format!("Find $x$ with $0 \\le x < {n}$ and ${a}x \\equiv 1 \\pmod{{{n}}}$, i.e. ${a}^{{-1}} \\bmod {n}$."), answer, steps)
        .mistakes(vec![
            wrong(t1, format!("Reduce $t$ mod ${n}$: add ${n}$ to make it non-negative.")),
            wrong(pymod(-t1, n), format!("Check the sign of $t$: that's the inverse of $-{a}$.")),
            wrong(F::new(1, a), format!("A modular inverse is a whole number $x$ with ${a}x \\equiv 1$, not a fraction.")),
        ])
}

fn recurrence(r: &mut Random) -> Spec {
    let (rc, sc) = (r.choice(&[-2, -1, 1, 2, 3]), r.choice(&[-2, -1, 1, 2, 3]));
    let (a0, a1, n) = (r.randint(-3, 5), r.randint(-3, 5), r.randint(5, 8) as usize);
    let (mut seq, mut swapped) = (vec![a0, a1], vec![a0, a1]);
    for _ in 2..n + 2 {
        seq.push(rc * seq[seq.len() - 1] + sc * seq[seq.len() - 2]);
        swapped.push(sc * swapped[swapped.len() - 1] + rc * swapped[swapped.len() - 2]);
    }
    let steps = (2..=n)
        .map(|i| step(format!("$a_{{{i}}}$"), format!("{rc} \\cdot {} + {sc} \\cdot {}", paren(seq[i - 1]), paren(seq[i - 2])), seq[i]))
        .collect();
    let rec = lin(&[(rc, "a_{n-1}"), (sc, "a_{n-2}")]);
    Spec::new(format!("A sequence has $a_0 = {a0}$, $a_1 = {a1}$ and $a_n = {rec}$ for $n \\ge 2$. What is $a_{{{n}}}$?"), seq[n], steps)
        .mistakes(vec![
            wrong(seq[n - 1], format!("That's $a_{{{}}}$: the sequence starts at $a_0$, so count one more term.", n - 1)),
            wrong(seq[n + 1], format!("That's $a_{{{}}}$, one term too far.", n + 1)),
            wrong(swapped[n], format!("The coefficient ${rc}$ goes with $a_{{n-1}}$ and ${sc}$ with $a_{{n-2}}$.")),
        ])
}

/// `_closed`: SymPy text for Σ c·base**n, e.g. `3*2**n - 3**n`.
fn closed(terms: &[(i64, i64)]) -> String {
    let pieces: Vec<String> = terms
        .iter()
        .map(|&(c, base)| {
            let power = if base == 1 { String::new() } else { format!("{}**n", paren(base)) };
            let coefficient = if power.is_empty() {
                c.to_string()
            } else if c == 1 {
                String::new()
            } else if c == -1 {
                "-".into()
            } else {
                format!("{c}*")
            };
            coefficient + &power
        })
        .collect();
    pieces.join(" + ").replace("+ -", "- ")
}

fn recurrence_closed(r: &mut Random) -> Spec {
    let roots = [-3, -2, -1, 1, 2, 3, 4];
    let mut pq = r.sample(&roots, 2);
    pq.sort_by(|x, y| y.cmp(x));
    let (p, q) = (pq[0], pq[1]);
    let (a, b) = (r.choice(&roots), r.choice(&roots));
    let (rc, sc) = (p + q, -p * q);
    let (a0, a1) = (a + b, a * p + b * q);
    let answer = [(a, p), (b, q)];
    let values = |terms: &[(i64, i64)]| -> Vec<i64> { (0..4).map(|k| terms.iter().map(|&(c, base)| c * ipow(base, k)).sum()).collect() };
    let mut mistakes = Vec::new();
    for (terms, message) in [
        ([(b, p), (a, q)], "The coefficients are swapped: fit $\\alpha$ to the root $x_1$ from $a_0$ and $a_1$."),
        ([(a, -p), (b, -q)], "Check the signs: the roots solve $x^2 = r x + s$ for $a_n = r a_{n-1} + s a_{n-2}$."),
        ([(1, p), (1, q)], "Fit the coefficients to $a_0$ and $a_1$; $x_1^n + x_2^n$ is only the general shape."),
    ] {
        if values(&terms) != values(&answer) {
            mistakes.push(wrong(closed(&terms), message));
        }
    }
    let rec = lin(&[(rc, "a_{n-1}"), (sc, "a_{n-2}")]);
    Spec::new(format!("The recurrence $a_n = {rec}$ for $n \\ge 2$, with $a_0 = {a0}$ and $a_1 = {a1}$, has distinct integer characteristic roots. \
                       Give a closed form for $a_n$ in terms of $n$."), closed(&answer), vec![
        line("Try $a_n = x^n$: the characteristic equation", format!("{} = 0", lin(&[(1, "x^2"), (-rc, "x"), (-sc, "")]))),
        step("Its larger root", "x_1", p),
        step("Its smaller root", "x_2", q),
        step(format!("Fit $a_n = \\alpha x_1^n + \\beta x_2^n$ to $\\alpha + \\beta = {a0}$ and ${} = {a1}$", lin(&[(p, "\\alpha"), (q, "\\beta")])), "\\alpha", a),
        step("and", "\\beta", b),
    ]).kind("expression").variables(&["n"]).mistakes(mistakes)
}

/// `LOOPS[shape]`: (Python loop nest, inner count for a given i, the sum as TeX, its closed form, first pass).
fn loops(shape: &str, n: i64) -> (&'static str, &'static str, String, i64, i64) {
    match shape {
        "square" => ("for i in range(n):\n    for j in range(n):\n        count += 1", "n", format!("{n} \\cdot {n}"), n * n, n),
        "below" => ("for i in range(n):\n    for j in range(i):\n        count += 1", "i",
                    format!("0 + 1 + \\dots + {} = \\binom{{{n}}}{{2}}", n - 1), comb(n, 2), 0),
        "after" => ("for i in range(n):\n    for j in range(i + 1, n):\n        count += 1", "n - i - 1",
                    format!("{} + \\dots + 1 + 0 = \\binom{{{n}}}{{2}}", n - 1), comb(n, 2), n - 1),
        "from" => ("for i in range(n):\n    for j in range(i, n):\n        count += 1", "n - i",
                   format!("{n} + {} + \\dots + 1 = \\binom{{{}}}{{2}}", n - 1, n + 1), comb(n + 1, 2), n),
        "inclusive" => ("for i in range(1, n + 1):\n    for j in range(1, i + 1):\n        count += 1", "i",
                        format!("1 + 2 + \\dots + {n} = \\binom{{{}}}{{2}}", n + 1), comb(n + 1, 2), 1),
        "triple" => ("for i in range(n):\n    for j in range(i + 1, n):\n        for k in range(j + 1, n):\n            count += 1",
                     "\\binom{n - i - 1}{2}", format!("\\#\\{{i < j < k\\}} = \\binom{{{n}}}{{3}}"), comb(n, 3), comb(n - 1, 2)),
        "triple-upto" => ("for i in range(n):\n    for j in range(i + 1):\n        for k in range(j + 1):\n            count += 1",
                          "\\binom{i + 2}{2}", format!("\\sum_{{i=0}}^{{{}}} \\binom{{i + 2}}{{2}} = \\binom{{{}}}{{3}}", n - 1, n + 2),
                          comb(n + 2, 3), 1),
        _ => unreachable!("unknown loop shape"),
    }
}

/// `_loop_mistakes`.
fn loop_mistakes(shape: &str, n: i64) -> Vec<(Answer, String)> {
    let (half, half_up) = (comb(n, 2), comb(n + 1, 2));
    let square = "The inner loop's range depends on $i$, so it doesn't run $n$ times on every pass.";
    match shape {
        "square" => vec![wrong(half, "Both loops run over all of `range(n)`: there's no triangle here."),
                         wrong(2 * n, "Nested loops multiply their counts; they don't add.")],
        "below" => vec![wrong(half_up, "Off by one: `range(i)` stops before $i$, so $j$ takes $i$ values."), wrong(n * n, square),
                        wrong(n * (n - 1), "Halve it: the inner counts are $0, 1, \\dots, n-1$.")],
        "after" => vec![wrong(half_up, "Off by one: `range(i + 1, n)` leaves out $j = i$."), wrong(n * n, square),
                        wrong(n * (n - 1), "Halve it: the inner counts are $n-1, \\dots, 1, 0$.")],
        "from" => vec![wrong(half, "Off by one: `range(i, n)` includes $j = i$."), wrong(n * n, square)],
        "inclusive" => vec![wrong(half, "Off by one: `range(1, i + 1)` includes $j = i$."), wrong(n * n, square)],
        "triple" => vec![wrong(n * n * n, "Each loop starts after the previous index, so the counts shrink."),
                         wrong(perm(n, 3), "The indices come out in increasing order: each set of three is counted once, not $3!$ times.")],
        "triple-upto" => vec![wrong(comb(n, 3), "`range(j + 1)` includes $k = j$: indices may repeat, so it's $\\binom{n+2}{3}$."),
                              wrong(n * n * n, "Each inner range stops at the outer index, so the counts shrink.")],
        _ => unreachable!("unknown loop shape"),
    }
}

fn loop_count(r: &mut Random) -> Spec {
    let shape = r.choice(&["after", "below", "from", "inclusive", "square", "triple", "triple-upto"]);
    let n = r.randint(5, 30);
    let (code, inner, total, count, first) = loops(shape, n);
    let start = if shape == "inclusive" { 1 } else { 0 };
    Spec::new(format!("With $n = {n}$ and `count = 0` beforehand, what is `count` after this runs?\n\n```python\n{code}\n```"), count, vec![
        line(format!("For a given $i$ the inner loop{}", if shape.starts_with("triple") { "s run" } else { " runs" }), format!("{inner} \\text{{ times}}")),
        step(format!("On the first pass ($i = {start}$)"), "", first),
        step("Add them up", total, count),
    ]).mistakes(loop_mistakes(shape, n))
}

/// `ORDER`: growth classes n^k (log n)^l, slowest first.
const ORDER: [(i64, i64); 7] = [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0)];

fn order_index(cls: &(i64, i64)) -> usize {
    ORDER.iter().position(|item| item == cls).expect("growth class")
}

/// `_term`.
fn term(k: i64, l: i64) -> String {
    let n = match k {
        0 => String::new(),
        1 => "n".into(),
        _ => format!("n^{k}"),
    };
    let out = n + if l != 0 { "\\log n" } else { "" };
    if out.is_empty() { "1".into() } else { out }
}

/// `_theta`.
fn theta(cls: (i64, i64)) -> String {
    format!("$\\Theta({})$", term(cls.0, cls.1))
}

/// `_growth_choices`: four classes in growth order: the answer, the mistake and the nearest neighbours.
fn growth_choices(answer: (i64, i64), wrong: (i64, i64)) -> Vec<String> {
    let mut pool = vec![answer];
    if wrong != answer {
        pool.push(wrong);
    }
    let (at, mut step) = (order_index(&answer) as i64, 1);
    while pool.len() < 4 {
        for index in [at + step, at - step] {
            if (0..ORDER.len() as i64).contains(&index) && pool.len() < 4 && !pool.contains(&ORDER[index as usize]) {
                pool.push(ORDER[index as usize]);
            }
        }
        step += 1;
    }
    ORDER.iter().filter(|cls| pool.contains(cls)).map(|&cls| theta(cls)).collect()
}

fn big_o_cost(r: &mut Random) -> Spec {
    let classes = r.sample(&ORDER, 3);
    let top = max_by(&classes, order_index);
    let terms: Vec<(i64, i64, i64)> = classes.iter().map(|&cls| (if cls == top { r.randint(1, 9) } else { r.randint(2, 500) }, cls.0, cls.1)).collect();
    let biggest = max_by(&terms, |item| item.0);
    let others: Vec<(i64, i64)> = classes.iter().copied().filter(|&cls| cls != top).collect();
    let second = max_by(&others, order_index);
    let wrong_cls = if (biggest.1, biggest.2) != top { (biggest.1, biggest.2) } else { second };
    let c = terms.iter().find(|item| (item.1, item.2) == top).expect("top term").0;
    let cost = terms
        .iter()
        .map(|&(coefficient, k, l)| {
            let shown = k != 0 || l != 0;
            format!("{}{}", if coefficient == 1 && shown { String::new() } else { coefficient.to_string() }, if shown { term(k, l) } else { String::new() })
        })
        .collect::<Vec<_>>()
        .join(" + ");
    Spec::new(format!("An algorithm performs $T(n) = {cost}$ basic steps on an input of size $n$. Which tight bound describes $T(n)$?"), theta(top), vec![
        note("Each extra factor of $n$ beats any power of $\\log n$, and $\\log n$ beats a constant; constant factors don't matter"),
        step("Power of $n$ in the fastest-growing term", "", top.0),
        step("Factors of $\\log n$ in that term", "", top.1),
        line(format!("Drop the constant factor ${c}$"), format!("T(n) = \\Theta({})", term(top.0, top.1))),
    ]).kind("choice").choices(growth_choices(top, wrong_cls))
        .mistakes(vec![wrong(theta(wrong_cls), "For large $n$ the fastest-growing term dominates, whatever the coefficients.")])
}

/// `BIG_O_LOOPS[shape]`: (Python loop nest, (k, l), a tempting wrong class, why it's wrong, how each loop runs).
fn big_o_loop(shape: &str) -> (&'static str, (i64, i64), (i64, i64), &'static str, &'static str) {
    match shape {
        "single" => ("for i in range(n):\n    count += 1", (1, 0), (2, 0), "One loop over $n$ items is linear.", "One loop of $n$ passes"),
        "sequential" => ("for i in range(n):\n    count += 1\nfor j in range(n):\n    count += 1", (1, 0), (2, 0),
                         "Loops one after the other add: $n + n = 2n$.", "Two loops in sequence: $n + n$ passes"),
        "nested" => ("for i in range(n):\n    for j in range(n):\n        count += 1", (2, 0), (1, 0),
                     "Nested loops multiply: $n$ passes of $n$.", "$n$ outer passes of $n$ inner ones"),
        "triangular" => ("for i in range(n):\n    for j in range(i):\n        count += 1", (2, 0), (1, 1),
                         "The inner loop averages $n/2$ passes: $\\binom{n}{2} \\approx n^2/2$ is still quadratic.",
                         "$0 + 1 + \\dots + (n-1) = \\binom{n}{2}$ passes"),
        "halving" => ("i = n\nwhile i > 1:\n    i //= 2\n    count += 1", (0, 1), (1, 0),
                      "$i$ halves each pass, so it reaches $1$ after about $\\log_2 n$ passes.", "$i$ halves each pass: $\\log_2 n$ passes"),
        "doubling-inner" => ("for i in range(n):\n    j = 1\n    while j < n:\n        j *= 2\n        count += 1", (1, 1), (2, 0),
                             "The inner loop doubles $j$: $\\log_2 n$ passes, not $n$.", "$n$ outer passes of $\\log_2 n$ doublings"),
        "doubling-outer" => ("i = 1\nwhile i < n:\n    for j in range(n):\n        count += 1\n    i *= 2", (1, 1), (2, 0),
                             "The outer loop doubles $i$: $\\log_2 n$ passes, not $n$.", "$\\log_2 n$ doublings of $n$ inner passes"),
        "geometric" => ("i = 1\nwhile i < n:\n    for j in range(i):\n        count += 1\n    i *= 2", (1, 0), (1, 1),
                        "The inner counts $1 + 2 + 4 + \\dots$ form a geometric series below $2n$.", "$1 + 2 + 4 + \\dots + n/2 < n$ passes"),
        "triple" => ("for i in range(n):\n    for j in range(i + 1, n):\n        for k in range(j + 1, n):\n            count += 1", (3, 0), (2, 0),
                     "Three nested loops make $\\binom{n}{3} \\approx n^3/6$ passes: still cubic.", "$\\binom{n}{3}$ passes, one per $i < j < k$"),
        "nested-doubling" => ("for i in range(n):\n    for j in range(n):\n        k = 1\n        while k < n:\n            k *= 2\n            count += 1",
                              (2, 1), (3, 0), "The innermost loop doubles $k$: $\\log_2 n$ passes, not $n$.", "$n^2$ pairs of $\\log_2 n$ doublings"),
        _ => unreachable!("unknown big-O shape"),
    }
}

fn big_o_loops(r: &mut Random) -> Spec {
    let shape = r.choice(&["doubling-inner", "doubling-outer", "geometric", "halving", "nested", "nested-doubling", "sequential", "single",
                           "triangular", "triple"]);
    let (code, cls, wrong_cls, message, passes) = big_o_loop(shape);
    Spec::new(format!("Each `count += 1` costs $O(1)$. Which tight bound describes the running time of this code as a function of $n$?\n\n```python\n{code}\n```"),
              theta(cls), vec![
        note(passes),
        step("Power of $n$ in the pass count", "", cls.0),
        step("Factors of $\\log n$", "", cls.1),
    ]).kind("choice").choices(growth_choices(cls, wrong_cls)).mistakes(vec![wrong(theta(wrong_cls), message)])
}

/// `_random_graph`.
fn random_graph(r: &mut Random, n: i64) -> (i64, Vec<i64>) {
    loop {
        let mut edges = Vec::new();
        for i in 0..n {
            for j in i + 1..n {
                if r.random() < 0.45 {
                    edges.push((i, j));
                }
            }
        }
        let mut degrees: Vec<i64> = (0..n).map(|v| edges.iter().filter(|&&(i, j)| i == v || j == v).count() as i64).collect();
        if edges.len() as i64 >= n - 1 && degrees.iter().max().is_some_and(|&d| d > 0) {
            degrees.sort_by(|x, y| y.cmp(x));
            return (edges.len() as i64, degrees);
        }
    }
}

fn joined(values: &[i64], sep: &str) -> String {
    values.iter().map(|v| v.to_string()).collect::<Vec<_>>().join(sep)
}

fn graph_edges(r: &mut Random) -> Spec {
    let variant = r.choice(&["complete", "degrees", "missing"]);
    if variant == "complete" {
        let n = r.randint(5, 40);
        let answer = comb(n, 2);
        return Spec::new(format!("How many edges does the complete graph $K_{{{n}}}$ have?"), answer, vec![
            step("Each vertex is adjacent to every other", format!("{n} - 1"), n - 1),
            step("Degree sum", format!("{n} \\cdot {}", n - 1), n * (n - 1)),
            step("Each edge has two ends: halve", format!("\\frac{{{}}}{{2}}", n * (n - 1)), answer),
        ]).mistakes(vec![
            wrong(n * (n - 1), "Each edge was counted from both ends: halve the degree sum."),
            wrong(n * n, "A simple graph has no loops: a vertex isn't adjacent to itself."),
        ]);
    }
    let n = r.randint(5, 8);
    let (edges, degrees) = random_graph(r, n);
    if variant == "degrees" {
        let total: i64 = degrees.iter().sum();
        return Spec::new(format!("A simple graph has vertex degrees ${}$. How many edges does it have?", joined(&degrees, ", ")), edges, vec![
            step("Degree sum", joined(&degrees, " + "), total),
            step("Each edge adds $2$ to the degree sum: halve", format!("\\frac{{{total}}}{{2}}"), edges),
        ]).mistakes(vec![
            wrong(total, "Each edge has two ends, so it's counted twice in the degree sum: halve it."),
            wrong(n, "That's the number of vertices."),
        ]);
    }
    let hidden = r.randrange(0, n) as usize;
    let known: Vec<i64> = degrees[..hidden].iter().chain(&degrees[hidden + 1..]).copied().collect();
    let known_sum: i64 = known.iter().sum();
    let answer = 2 * edges - known_sum;
    Spec::new(format!("A simple graph has {n} vertices and {edges} edges. {} of its vertices have degrees ${}$. \
                       What is the degree of the remaining vertex?", n - 1, joined(&known, ", ")), answer, vec![
        step("Handshake lemma: the degree sum", format!("2 \\cdot {edges}"), 2 * edges),
        step("The known degrees add to", joined(&known, " + "), known_sum),
        step("Missing degree", format!("{} - {known_sum}", 2 * edges), answer),
    ]).mistakes(vec![
        wrong(edges - known_sum, format!("The degree sum is twice the edge count: $2 \\cdot {edges}$.")),
        wrong(edges, "That's the number of edges; each edge adds $2$ to the degree sum."),
    ])
}

fn graph_counting(r: &mut Random) -> Spec {
    let variant = r.choice(&["graphs", "edges", "trees", "spanning"]);
    let n = if variant != "edges" { r.randint(3, 6) } else { r.randint(4, 6) };
    let pairs = comb(n, 2);
    let pow2 = |e: i64| ipow(2, e as u32);
    if variant == "graphs" || variant == "edges" {
        let mut steps = vec![step("Possible edges: unordered pairs of vertices", format!("\\binom{{{n}}}{{2}}"), pairs)];
        if variant == "graphs" {
            steps.push(step("Each possible edge is in or out", format!("2^{{{pairs}}}"), pow2(pairs)));
            return Spec::new(format!("How many simple graphs are there on {n} labelled vertices (any number of edges)?"), pow2(pairs), steps)
                .mistakes(vec![
                    wrong(pow2(n * (n - 1)), "Edges are unordered pairs: there are $\\binom{n}{2}$, not $n(n-1)$."),
                    wrong(pow2(n * n), "A simple graph has no loops and no ordered pairs: use $\\binom{n}{2}$ edges."),
                    wrong(pairs, "That's the number of possible edges; each one is in or out."),
                ]);
        }
        let m = r.randint(2, pairs - 2);
        let answer = comb(pairs, m);
        steps.push(step(format!("Choose which {m} are present"), format!("\\binom{{{pairs}}}{{{m}}}"), answer));
        return Spec::new(format!("How many simple graphs on {n} labelled vertices have exactly {m} edges?"), answer, steps).mistakes(vec![
            wrong(pow2(pairs), format!("That counts graphs with any number of edges; choose exactly ${m}$.")),
            wrong(ipow(pairs, m as u32), "The edges form a set: no order and no repeats."),
            wrong(comb(n * n, m), "Edges are unordered pairs of distinct vertices: $\\binom{n}{2}$ of them."),
        ]);
    }
    let answer = ipow(n, (n - 2) as u32);
    let prompt = if variant == "trees" {
        format!("How many different trees are there on {n} labelled vertices?")
    } else {
        format!("How many spanning trees does the complete graph $K_{{{n}}}$ have?")
    };
    Spec::new(prompt, answer, vec![
        step("Trees match Prüfer sequences; their length is", format!("{n} - 2"), n - 2),
        step(format!("Cayley's formula: each entry is one of {n} labels"), format!("{n}^{{{}}}", n - 2), answer),
    ]).mistakes(vec![
        wrong(ipow(n, n as u32), "Cayley's formula is $n^{n-2}$, not $n^n$."),
        wrong(ipow(n, (n - 1) as u32), "Cayley's formula is $n^{n-2}$: a Prüfer sequence has $n - 2$ entries."),
        wrong(comb(pairs, n - 1), format!("Not every set of ${}$ edges is a tree: some contain a cycle.", n - 1)),
    ])
}

fn lattice_paths(r: &mut Random) -> Spec {
    let (m, n) = (r.randint(2, 8), r.randint(2, 8));
    let through = r.random() < 0.5;
    let start = format!("Walk from $(0, 0)$ to $({m}, {n})$ with unit steps right $(+1, 0)$ or up $(0, +1)$.");
    let total = comb(m + n, m);
    let wrong_power = wrong(ipow(2, (m + n) as u32), format!("Not every step is free: exactly ${m}$ of them go right."));
    if !through {
        return Spec::new(format!("{start} How many paths are there?"), total, vec![
            step("Total steps", format!("{m} + {n}"), m + n),
            step("Choose which are right steps", format!("\\binom{{{}}}{{{m}}}", m + n), total),
        ]).mistakes(vec![wrong_power, wrong(m * n, "Count orders of the steps: choose which of them go right.")]);
    }
    let (a, b) = (r.randint(1, m - 1), r.randint(1, n - 1));
    let (first, second) = (comb(a + b, a), comb(m - a + n - b, m - a));
    let answer = first * second;
    Spec::new(format!("{start} How many paths pass through $({a}, {b})$?"), answer, vec![
        step(format!("Paths to $({a}, {b})$"), format!("\\binom{{{}}}{{{a}}}", a + b), first),
        step(format!("Paths from $({a}, {b})$ on"), format!("\\binom{{{}}}{{{}}}", m - a + n - b, m - a), second),
        step("Any first half joins any second half", format!("{first} \\times {second}"), answer),
    ]).mistakes(vec![
        wrong(total, format!("That counts every path; only those through $({a}, {b})$ count.")),
        wrong(first + second, "Each first half pairs with each second half: multiply."),
        wrong_power,
    ])
}
