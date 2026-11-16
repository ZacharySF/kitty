//! `templates/arithmetic.py`: the fast exact arithmetic quant screens test.

use crate::math::frac::{igcd, ipow, F};
use crate::math::problems::{num, step, line, template, wrong, Spec, Template};
use crate::math::pyrandom::Random;

pub static TEMPLATES: &[Template] = &[
    template("arith.add-sub/three-digit", 8, add_sub),
    template("arith.times-tables/to-20", 4, times_tables),
    template("arith.mul-2x1/split", 8, mul_2x1),
    template("arith.mul-2x2/split", 20, mul_2x2),
    template("arith.squares/near-base", 12, squares),
    template("arith.division/exact", 8, division),
    template("arith.powers-of-two/power", 5, powers_of_two),
    template("arith.fractions/add-sub", 25, fractions),
    template("arith.frac-decimal/terminating", 10, frac_decimal),
    template("arith.percentages/of", 12, percentages),
    template("arith.percentages/change", 15, percent_change),
    template("arith.estimation/divide-2sf", 20, estimation),
    template("arith.odds/to-probability", 12, odds),
];

/// `_digitwise`: combine a and b digit by digit with no carries or borrows (a classic slip).
fn digitwise(a: i64, b: i64, op: fn(i64, i64) -> i64) -> i64 {
    let (a, b) = (a.to_string(), b.to_string());
    let width = a.len().max(b.len());
    let (a, b) = (format!("{a:0>width$}"), format!("{b:0>width$}"));
    let digits: String = a.bytes().zip(b.bytes()).map(|(x, y)| op(i64::from(x - b'0'), i64::from(y - b'0')).to_string()).collect();
    digits.parse().expect("digits")
}

fn add_sub(r: &mut Random) -> Spec {
    let (mut a, mut b) = (r.randint(100, 999), r.randint(100, 999));
    while b == a {
        b = r.randint(100, 999);
    }
    if r.random() < 0.5 {
        let hundreds = (a / 100 + b / 100) * 100;
        let rest = a % 100 + b % 100;
        return Spec::new(format!("${a} + {b} = ?$"), a + b, vec![
            step("Hundreds", format!("{} + {}", a / 100 * 100, b / 100 * 100), hundreds),
            step("The rest", format!("{} + {}", a % 100, b % 100), rest),
            step("Add", format!("{hundreds} + {rest}"), a + b),
        ]).mistakes(vec![wrong(digitwise(a, b, |x, y| (x + y) % 10), "A carry was dropped: carry the 1 into the next column.")]);
    }
    (a, b) = (a.max(b), a.min(b));
    Spec::new(format!("${a} - {b} = ?$"), a - b, vec![
        step("Take the hundreds", format!("{a} - {}", b / 100 * 100), a - b / 100 * 100),
        step("Then the rest", format!("{} - {}", a - b / 100 * 100, b % 100), a - b),
    ]).mistakes(vec![wrong(digitwise(a, b, |x, y| (x - y).abs()), "Looks like a borrow was missed: subtracting the smaller digit from the larger in each column isn't subtraction.")])
}

fn times_tables(r: &mut Random) -> Spec {
    let (a, b) = (r.randint(2, 20), r.randint(2, 20));
    let (big, small) = (a.max(b), a.min(b));
    let mut steps = vec![step("Product", format!("{a} \\times {b}"), a * b)];
    if big > 10 {
        steps = vec![line("Split the larger factor", format!("10 \\times {small} + {} \\times {small}", big - 10)),
                     step("Add", format!("{} + {}", 10 * small, (big - 10) * small), a * b)];
    }
    Spec::new(format!("${a} \\times {b} = ?$"), a * b, steps).mistakes(vec![
        wrong(a * (b - 1), format!("That's ${a} \\times {}$: one row short.", b - 1)),
        wrong(a * (b + 1), format!("That's ${a} \\times {}$: one row over.", b + 1)),
        wrong(a + b, "That's the sum, not the product."),
    ])
}

fn mul_2x1(r: &mut Random) -> Spec {
    let (a, b) = (r.randint(12, 99), r.randint(3, 9));
    let (tens, ones) = (a / 10 * 10, a % 10);
    Spec::new(format!("${a} \\times {b} = ?$"), a * b, vec![
        step("Tens", format!("{tens} \\times {b}"), tens * b),
        step("Ones", format!("{ones} \\times {b}"), ones * b),
        step("Add", format!("{} + {}", tens * b, ones * b), a * b),
    ]).mistakes(vec![
        wrong(tens * b + ones, format!("Multiply the ones digit too: ${ones} \\times {b}$.")),
        wrong(digitwise(a, b, |x, y| (x * y) % 10), "Carry the tens of each partial product."),
    ])
}

fn mul_2x2(r: &mut Random) -> Spec {
    let (a, b) = (r.randint(11, 99), r.randint(11, 99));
    let (bt, bo) = (b / 10 * 10, b % 10);
    Spec::new(format!("${a} \\times {b} = ?$"), a * b, vec![
        step("Split the second factor", format!("{a} \\times {bt}"), a * bt),
        step("The ones", format!("{a} \\times {bo}"), a * bo),
        step("Add", format!("{} + {}", a * bt, a * bo), a * b),
    ]).mistakes(vec![wrong((a / 10) * (b / 10) * 100 + (a % 10) * (b % 10), "You multiplied tens by tens and ones by ones but skipped the cross terms.")])
}

fn squares(r: &mut Random) -> Spec {
    let n = r.randint(11, 99);
    let base = (n + 5) / 10 * 10;
    let d = n - base;
    let (tens, ones) = (n / 10, n % 10);
    Spec::new(format!("${n}^2 = ?$"), n * n, vec![
        line("Use $(b + d)^2 = b^2 + 2bd + d^2$ with", format!("b = {base},\\ d = {d}")),
        step("Square the base", format!("{base}^2"), base * base),
        step("Cross term", format!("2 \\times {base} \\times {}", num(d)), 2 * base * d),
        step("Add $d^2$", format!("{} + {} + {}", base * base, 2 * base * d, d * d), n * n),
    ]).mistakes(vec![
        wrong(ipow(tens * 10, 2) + ipow(ones, 2), "Missing the cross term $2ab$: $(a+b)^2 \\ne a^2 + b^2$."),
        wrong(2 * n, format!("That's $2 \\times {n}$, not ${n}^2$.")),
    ])
}

fn division(r: &mut Random) -> Spec {
    let (b, q) = (r.randint(3, 12), r.randint(12, 99));
    let a = b * q;
    Spec::new(format!("${a} \\div {b} = ?$"), q, vec![
        step("Largest easy multiple", format!("{b} \\times {}", q / 10 * 10), b * (q / 10 * 10)),
        step("What's left", format!("{a} - {}", b * (q / 10 * 10)), a - b * (q / 10 * 10)),
        step("Divide the rest", format!("{} \\div {b}", a - b * (q / 10 * 10)), q % 10),
        step("Add the parts", format!("{} + {}", q / 10 * 10, q % 10), q),
    ])
}

fn powers_of_two(r: &mut Random) -> Spec {
    let k = r.randint(3, 20);
    let power = |exponent: i64| ipow(2, exponent as u32);
    Spec::new(format!("$2^{{{k}}} = ?$"), power(k), vec![
        if k >= 10 { step("Anchor", "2^{10}", 1024) } else { step("Double up", format!("2^{{{}}}", k - 1), power(k - 1)) },
        step("Then", if k >= 10 { format!("2^{{10}} \\times 2^{{{}}}", k - 10) } else { format!("2 \\times {}", power(k - 1)) }, power(k)),
    ]).mistakes(vec![
        wrong(2 * k, format!("That's $2 \\times {k}$; $2^{{{k}}}$ doubles {k} times.")),
        wrong(power(k - 1), format!("One doubling short: that's $2^{{{}}}$.", k - 1)),
        wrong(k * k, format!("That's ${k}^2$.")),
    ])
}

fn fractions(r: &mut Random) -> Spec {
    let (b, d) = (r.randint(2, 12), r.randint(2, 12));
    let a = if b > 1 { r.randint(1, b - 1) } else { 1 };
    let c = if d > 1 { r.randint(1, d - 1) } else { 1 };
    let op = r.choice(&['+', '-']);
    let (x, y) = (F::new(a, b), F::new(c, d));
    let answer = if op == '+' { x + y } else { x - y };
    let common = b * d / igcd(b, d);
    let slip = if op == '+' { F::new(a + c, b + d) } else if b != d { F::new(a - c, b - d) } else { answer };
    Spec::new(format!("$\\frac{{{a}}}{{{b}}} {op} \\frac{{{c}}}{{{d}}} = ?$ Give an exact fraction."), answer, vec![
        step("Common denominator", format!("\\operatorname{{lcm}}({b}, {d})"), common),
        line("Rewrite", format!("\\frac{{{}}}{{{common}}} {op} \\frac{{{}}}{{{common}}}", a * common / b, c * common / d)),
        step("Combine and reduce", "", answer),
    ]).kind("fraction").mistakes(vec![wrong(slip, "Adding numerators and denominators separately doesn't combine fractions; use a common denominator.")])
}

fn frac_decimal(r: &mut Random) -> Spec {
    let d = r.choice(&[4, 8, 16, 20, 25, 40, 50, 80, 125]);
    let n = r.choice(&(1..d).filter(|k| igcd(*k, d) == 1).collect::<Vec<i64>>());
    let value = F::new(n, d);
    let scale = if [8, 125, 40].contains(&d) { 1000 } else if [16, 80].contains(&d) { 10000 } else { 100 };
    Spec::new(format!("Write $\\frac{{{n}}}{{{d}}}$ as a decimal."), value, vec![
        line("Scale to a power of ten", format!("\\frac{{{n}}}{{{d}}} = \\frac{{{}}}{{{scale}}}", n * scale / d)),
        step("Read the decimal", "", value),
    ]).kind("number").mistakes(vec![wrong(if n != 0 { F::new(d, n) } else { value }, "That's the reciprocal: divide the numerator by the denominator.")])
}

fn percentages(r: &mut Random) -> Spec {
    let p = r.choice(&[F::from(5), F::from(10), F::new(25, 2), F::from(15), F::from(20), F::from(25), F::from(30), F::from(35), F::from(40),
                       F::from(45), F::from(60), F::from(75), F::from(80), F::from(150)]);
    let x = r.choice(&[40, 60, 80, 120, 160, 240, 360, 480, 640, 800, 1200]);
    let answer = p * x / 100;
    Spec::new(format!("What is ${}\\%$ of ${x}$?", num(p)), answer, vec![
        step("One percent", format!("{x} \\div 100"), F::new(x, 100)),
        step("Scale", format!("{} \\times {}", num(F::new(x, 100)), num(p)), answer),
    ]).kind("number").mistakes(vec![wrong(p * x / 10, "Percent means per hundred: divide by 100, not 10.")])
}

fn percent_change(r: &mut Random) -> Spec {
    let old = r.choice(&[20, 25, 40, 50, 80, 120, 160, 200, 250, 400]);
    let pct = r.choice(&[-50, -40, -25, -20, -10, 10, 15, 20, 25, 30, 50, 75]);
    let new = F::new(old * (100 + pct), 100);
    let answer = (new - old) / old * 100;
    Spec::new(format!("A price moves from ${old}$ to ${}$. What is the percentage change? (Negative for a fall.)", num(new)), answer, vec![
        step("Change", format!("{} - {old}", num(new)), new - old),
        step("Relative to the start", format!("\\frac{{{}}}{{{old}}} \\times 100", num(new - old)), answer),
    ]).kind("number").mistakes(vec![
        wrong((new - old) / new * 100, "Divide by the starting value, not the new one."),
        wrong(new - old, "That's the change in price, not in percent."),
    ])
}

fn estimation(r: &mut Random) -> Spec {
    let b = r.choice(&[7, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97]);
    let mut a = r.randint(150, 9999);
    while a % b == 0 {
        a += 1;
    }
    let value = F::new(a, b);
    Spec::new(format!("Estimate ${a} \\div {b}$ to 2 significant figures."), value, vec![
        line("Bracket it", format!("{b} \\times {} = {} \\le {a} < {}", a / b, b * (a / b), b * (a / b + 1))),
        step("The quotient, then round", format!("{a} \\div {b}"), value),
    ]).kind("fraction").sigfigs(2).mistakes(vec![wrong(F::new(b, a), "That's the reciprocal.")])
}

fn odds(r: &mut Random) -> Spec {
    let (mut a, mut b) = (r.randint(1, 9), r.randint(1, 9));
    while igcd(a, b) != 1 || a == b {
        (a, b) = (r.randint(1, 9), r.randint(1, 9));
    }
    let against = r.random() < 0.5;
    let answer = if against { F::new(b, a + b) } else { F::new(a, a + b) };
    let side = if against { "against" } else { "in favour of" };
    let chances = if against { b } else { a };
    Spec::new(format!("The odds {side} an event are ${a}:{b}$. What is its probability?"), answer, vec![
        line("Odds $x:y$ in favour means $x$ chances in $x + y$", format!("{chances} \\text{{ of }} {}", a + b)),
        step("Probability", format!("\\frac{{{chances}}}{{{}}}", a + b), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(if against { F::new(b, a) } else { F::new(a, b) }, "Odds aren't a probability: $x:y$ means $x/(x+y)$."),
        wrong(if against { F::new(a, a + b) } else { F::new(b, a + b) }, format!("Check the side: these odds are {side} the event.")),
    ])
}
