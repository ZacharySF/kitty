//! `templates/counting.py`: counting and combinatorics.

use crate::math::frac::{comb, factorial, ilcm, ipow, perm};
use crate::math::problems::{line, step, template, wrong, Spec, Template};
use crate::math::pyrandom::Random;

pub static TEMPLATES: &[Template] = &[
    template("count.product-rule/outfits", 10, product_rule),
    template("count.permutations/podium", 15, permutations),
    template("count.multisets/word", 25, multisets),
    template("count.combinations/committee", 15, combinations),
    template("count.combinations/polynomial", 30, combinations_polynomial),
    template("count.complement/at-least-one", 25, complement),
    template("count.inclusion-exclusion/divisible", 40, inclusion_exclusion),
    template("count.stars-bars/solutions", 30, stars_bars),
    template("count.binomial-theorem/coefficient", 40, binomial_theorem),
];

const WORDS: &[&str] = &["BANANA", "LETTER", "PEPPER", "COFFEE", "TATTOO", "ASSESS", "BALLOON", "COOKBOOK", "SETTEE", "PAPAYA"];

fn product_rule(r: &mut Random) -> Spec {
    let (s, p, h) = (r.randint(3, 9), r.randint(2, 6), r.randint(2, 5));
    Spec::new(format!("You own {s} shirts, {p} pairs of trousers and {h} hats. How many different outfits of one of each are there?"), s * p * h, vec![
        step("Each choice multiplies the options", format!("{s} \\times {p} \\times {h}"), s * p * h),
    ]).mistakes(vec![wrong(s + p + h, "Independent choices multiply; adding counts single items, not outfits.")])
}

fn permutations(r: &mut Random) -> Spec {
    let (n, k) = (r.randint(5, 12), r.randint(2, 4));
    let factors: Vec<String> = (0..k).map(|i| (n - i).to_string()).collect();
    Spec::new(format!("{n} runners race. In how many ways can the first {k} places be filled (no ties)?"), perm(n, k), vec![
        step("Order matters and nobody repeats", factors.join(" \\times "), perm(n, k)),
    ]).mistakes(vec![
        wrong(comb(n, k), "That counts groups; here the order of the places matters."),
        wrong(ipow(n, k as u32), "That allows one runner to take several places."),
    ])
}

fn multisets(r: &mut Random) -> Spec {
    let word = r.choice(WORDS);
    let mut counts: Vec<(char, i64)> = Vec::new();
    for ch in word.chars() {
        match counts.iter_mut().find(|(c, _)| *c == ch) {
            Some(entry) => entry.1 += 1,
            None => counts.push((ch, 1)),
        }
    }
    let repeats: Vec<i64> = counts.iter().map(|&(_, c)| c).filter(|&c| c > 1).collect();
    let denominator: i64 = counts.iter().map(|&(_, c)| factorial(c)).product();
    let len = word.len() as i64;
    let answer = factorial(len) / denominator;
    Spec::new(format!("How many distinct arrangements of the letters of **{word}** are there?"), answer, vec![
        step("All orders of the letters", format!("{len}!"), factorial(len)),
        step("Divide out swaps of identical letters", repeats.iter().map(|c| format!("{c}!")).collect::<Vec<_>>().join(" \\times "), denominator),
        step("Arrangements", format!("\\frac{{{}}}{{{denominator}}}", factorial(len)), answer),
    ]).mistakes(vec![wrong(factorial(len), "Identical letters swapped give the same word; divide by their factorials.")])
}

fn combinations(r: &mut Random) -> Spec {
    let (n, k) = (r.randint(6, 15), r.randint(2, 5));
    Spec::new(format!("How many ways are there to choose a committee of {k} from {n} people?"), comb(n, k), vec![
        step("Ordered choices", format!("{n}!/{}!", n - k), perm(n, k)),
        step("Divide by orderings of the committee", format!("\\frac{{{}}}{{{k}!}}", perm(n, k)), comb(n, k)),
    ]).mistakes(vec![wrong(perm(n, k), format!("A committee has no order: divide by ${k}!$."))])
}

fn combinations_polynomial(r: &mut Random) -> Spec {
    let k = r.choice(&[2, 3]);
    let ordered = r.random() < 0.5;
    let factors = ["n", "(n-1)", "(n-2)"][..k as usize].join(" \\cdot ");
    let (answer, prompt, steps, mistakes);
    if ordered {
        answer = if k == 2 { "n*(n-1)" } else { "n*(n-1)*(n-2)" };
        prompt = format!("In terms of $n$: how many ways can {k} distinct prizes go to {k} different people out of $n$?");
        steps = vec![line("Choices for each prize in turn", factors)];
        mistakes = vec![wrong(format!("n**{k}"), "That lets one person win several prizes."),
                        wrong(if k == 2 { "n*(n-1)/2" } else { "n*(n-1)*(n-2)/6" }, "The prizes are distinct, so order matters.")];
    } else {
        answer = if k == 2 { "n*(n-1)/2" } else { "n*(n-1)*(n-2)/6" };
        prompt = format!("In terms of $n$: how many ways are there to choose {k} people out of $n$, i.e. $\\binom{{n}}{{{k}}}$ as a polynomial?");
        steps = vec![line("Ordered choices", factors), line("Divide by the orderings", format!("{k}! = {}", factorial(k)))];
        mistakes = vec![wrong(if k == 2 { "n*(n-1)" } else { "n*(n-1)*(n-2)" }, format!("Choosing ignores order: divide by ${k}!$.")),
                        wrong(if k == 2 { "n**2/2" } else { "n**3/6" }, "Each later choice has one person fewer.")];
    }
    Spec::new(prompt, answer, steps).kind("expression").variables(&["n"]).mistakes(mistakes)
}

fn complement(r: &mut Random) -> Spec {
    let (m, k) = (r.randint(3, 6), r.randint(2, 5));
    let (all, none) = (ipow(m, k as u32), ipow(m - 1, k as u32));
    let answer = all - none;
    Spec::new(format!("Strings of length {k} use an alphabet of {m} letters, one of which is **A**. How many strings contain at least one A?"), answer, vec![
        step("All strings", format!("{m}^{{{k}}}"), all),
        step("Strings with no A", format!("{}^{{{k}}}", m - 1), none),
        step("Complement", format!("{all} - {none}"), answer),
    ]).mistakes(vec![
        wrong(k * ipow(m, (k - 1) as u32), "Fixing an A in each position counts strings with several A's more than once."),
        wrong(none, "That's the strings with no A at all."),
    ])
}

fn inclusion_exclusion(r: &mut Random) -> Spec {
    let n = r.randint(50, 500);
    let a = r.randint(2, 9);
    let b = r.choice(&(2..10).filter(|&x| x != a && x % a != 0 && a % x != 0).collect::<Vec<i64>>());
    let l = ilcm(a, b);
    let both = n / l;
    let answer = n / a + n / b - both;
    Spec::new(format!("How many integers from 1 to {n} are divisible by {a} or by {b}?"), answer, vec![
        step(format!("Divisible by {a}"), format!("\\lfloor {n}/{a} \\rfloor"), n / a),
        step(format!("Divisible by {b}"), format!("\\lfloor {n}/{b} \\rfloor"), n / b),
        step("Divisible by both (the lcm)", format!("\\lfloor {n}/{l} \\rfloor"), both),
        step("Inclusion–exclusion", format!("{} + {} - {both}", n / a, n / b), answer),
    ]).mistakes(vec![
        wrong(n / a + n / b, "Numbers divisible by both were counted twice; subtract them once."),
        wrong(n / a + n / b - n / (a * b), format!("Both means divisible by $\\operatorname{{lcm}}({a}, {b}) = {l}$, not ${}$.", a * b)),
    ])
}

fn stars_bars(r: &mut Random) -> Spec {
    let (n, k) = (r.randint(4, 12), r.randint(2, 4));
    let positive = r.random() < 0.5;
    let answer = if positive { comb(n - 1, k - 1) } else { comb(n + k - 1, k - 1) };
    let variables = (0..k).map(|i| format!("x_{}", i + 1)).collect::<Vec<_>>().join(" + ");
    let kind = if positive { "positive integers" } else { "non-negative integers" };
    let mut steps = if positive { vec![step("Give each variable 1 first, leaving", format!("{n} - {k}"), n - k)] } else { vec![] };
    let stars = if positive { n - k } else { n };
    steps.push(step(format!("Arrange {stars} stars and {} bars", k - 1), format!("\\binom{{{}}}{{{}}}", stars + k - 1, k - 1), answer));
    Spec::new(format!("How many solutions in {kind} does ${variables} = {n}$ have?"), answer, steps).mistakes(vec![
        wrong(if positive { comb(n + k - 1, k - 1) } else { comb(n - 1, k - 1) },
              format!("Check the range: zero is {} here.", if positive { "not allowed" } else { "allowed" })),
        wrong(comb(n + k - 1, k), format!("Choose the positions of the ${}$ bars, not ${k}$.", k - 1)),
    ])
}

fn binomial_theorem(r: &mut Random) -> Spec {
    let n = r.randint(3, 7);
    let a = r.randint(1, 3);
    let b = r.choice(&[-3, -2, -1, 1, 2, 3]);
    let k = r.randint(1, n - 1);
    let (ak, bk) = (ipow(a, k as u32), ipow(b, (n - k) as u32));
    let answer = comb(n, k) * ak * bk;
    let inner = format!("{}x {} {}", if a != 1 { a.to_string() } else { String::new() }, if b > 0 { '+' } else { '-' }, b.abs());
    Spec::new(format!("What is the coefficient of $x^{{{k}}}$ in $({inner})^{{{n}}}$?"), answer, vec![
        line("The term with $x^k$", format!("\\binom{{{n}}}{{{k}}} ({a}x)^{{{k}}} ({b})^{{{}}}", n - k)),
        step("Binomial coefficient", format!("\\binom{{{n}}}{{{k}}}"), comb(n, k)),
        step("Multiply", format!("{} \\times {ak} \\times {bk}", comb(n, k)), answer),
    ]).mistakes(vec![
        wrong(comb(n, k), format!("Also multiply by ${a}^{{{k}}}$ and $({b})^{{{}}}$.", n - k)),
        wrong(comb(n, k) * ak * ipow(b.abs(), (n - k) as u32), "Watch the sign of the constant term."),
    ])
}
