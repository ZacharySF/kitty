//! `templates/expectation.py`: expected value, variance, stopping problems and fair prices.

use crate::math::frac::{ipow, F};
use crate::math::problems::{line, step, template, tex, wrong, Spec, Template};
use crate::math::pyrandom::Random;

pub static TEMPLATES: &[Template] = &[
    template("ev.expected-value/threshold-game", 40, threshold_game),
    template("ev.expected-value/die-n", 30, die_n),
    template("ev.linearity/hh-pairs", 40, hh_pairs),
    template("ev.linearity/distinct-faces", 45, distinct_faces),
    template("ev.max-dice/two", 45, max_two),
    template("ev.optimal-stopping/rerolls", 60, rerolls),
    template("ev.variance/distribution", 60, variance),
    template("ev.variance-sum/linear-combination", 45, variance_sum),
    template("ev.variance-sum/symbolic", 45, variance_symbolic),
    template("ev.waiting-time/until-face", 20, until_face),
    template("ev.waiting-time/coin-pattern", 60, coin_pattern),
    template("ev.fair-price/break-even", 40, fair_price),
];

const PATTERNS: &[&str] = &["HH", "HT", "HHH", "HTH", "HHT", "HTT", "THH"];

/// `_solve`: exact Gaussian elimination.
fn solve(matrix: Vec<Vec<F>>, rhs: Vec<F>) -> Vec<F> {
    let size = rhs.len();
    let mut rows: Vec<Vec<F>> = matrix.into_iter().zip(rhs).map(|(mut row, value)| { row.push(value); row }).collect();
    for col in 0..size {
        let pivot = (col..size).find(|&i| !rows[i][col].is_zero()).expect("singular system");
        rows.swap(col, pivot);
        let lead = rows[col][col];
        rows[col] = rows[col].iter().map(|&value| value / lead).collect();
        for i in 0..size {
            if i != col && !rows[i][col].is_zero() {
                let factor = rows[i][col];
                rows[i] = rows[i].iter().zip(&rows[col]).map(|(&a, &b)| a - factor * b).collect();
            }
        }
    }
    rows.iter().map(|row| row[size]).collect()
}

/// `pattern_wait`: expected fair-coin flips until `pattern` first appears (prefix-automaton states).
pub fn pattern_wait(pattern: &str) -> F {
    let states = pattern.len();
    let advance = |matched: usize, flip: char| -> usize {
        let text = format!("{}{flip}", &pattern[..matched]);
        (0..=text.len().min(states)).rev().find(|&length| text.ends_with(&pattern[..length])).unwrap_or(0)
    };
    let mut matrix = vec![vec![F::ZERO; states]; states];
    let rhs = vec![F::ONE; states];
    for i in 0..states {
        matrix[i][i] = matrix[i][i] + 1;
        for flip in ['H', 'T'] {
            let nxt = advance(i, flip);
            if nxt < states {
                matrix[i][nxt] = matrix[i][nxt] - F::new(1, 2);
            }
        }
    }
    solve(matrix, rhs)[0]
}

fn threshold_game(r: &mut Random) -> Spec {
    let m = r.choice(&[6, 8, 10, 12]);
    let t = r.randint(2, m - 1);
    let cost = r.randint(1, 5);
    let wins: i64 = (t..=m).sum();
    let answer = F::new(wins - cost * (t - 1), m);
    Spec::new(format!("Roll a fair {m}-sided die. If it shows {t} or more you win that many points; otherwise you lose {cost}. What is the expected score?"), answer, vec![
        step("Winning faces", format!("{t} + \\dots + {m}"), wins),
        step("Losing faces", format!("{} \\times (-{cost})", t - 1), -cost * (t - 1)),
        step("Average over the faces", format!("\\frac{{{wins} - {}}}{{{m}}}", cost * (t - 1)), answer),
    ]).kind("fraction").mistakes(vec![
        wrong(F::new(wins, m), "Include the losses on the low faces."),
        wrong(F::new(wins, m - t + 1) - cost, format!("Average over every face, each with probability $1/{m}$.")),
    ])
}

fn die_n(r: &mut Random) -> Spec {
    let (a, b) = (r.randint(1, 4), r.randint(0, 6));
    let expression = format!("{a}*(n + 1)/2 + {b}");
    let shown = format!("{}X{}", if a == 1 { String::new() } else { a.to_string() }, if b != 0 { format!(" + {b}") } else { String::new() });
    Spec::new(format!("$X$ is the roll of a fair die with faces $1, \\dots, n$. In terms of $n$, what is $\\mathbb{{E}}[{shown}]$?"), expression, vec![
        line("Mean of a fair die", "\\mathbb{E}[X] = \\frac{1 + 2 + \\dots + n}{n} = \\frac{n+1}{2}"),
        line("Linearity", format!("\\mathbb{{E}}[{shown}] = {}\\,\\mathbb{{E}}[X]{}", if a != 1 { a.to_string() } else { String::new() },
                                  if b != 0 { format!(" + {b}") } else { String::new() })),
    ]).kind("expression").variables(&["n"]).mistakes(vec![
        wrong(format!("{a}*n/2 + {b}"), "The faces start at 1: the mean is $(n+1)/2$."),
        wrong(format!("{a}*(n + 1)/2"), "Constants add straight through an expectation."),
    ])
}

fn hh_pairs(r: &mut Random) -> Spec {
    let n = r.randint(4, 14);
    let answer = F::new(n - 1, 4);
    Spec::new(format!("Flip a fair coin {n} times. What is the expected number of places where a head is immediately followed by another head?"), answer, vec![
        step("Adjacent pairs", format!("{n} - 1"), n - 1),
        step("Each pair is HH with probability", "\\tfrac12 \\cdot \\tfrac12", F::new(1, 4)),
        step("Linearity: sum the indicators", format!("{} \\cdot \\tfrac14", n - 1), answer),
    ]).kind("fraction").mistakes(vec![
        wrong(F::new(n, 4), format!("There are {} adjacent pairs, not {n}.", n - 1)),
        wrong(F::new(n - 1, 2), "Each pair needs two heads: probability $\\frac14$."),
    ])
}

fn distinct_faces(r: &mut Random) -> Spec {
    let k = r.randint(2, 5);
    let miss = F::new(5, 6).pow(k as i32);
    let answer = 6 * (1 - miss);
    Spec::new(format!("Roll a fair die {k} times. What is the expected number of distinct faces that appear?"), answer, vec![
        step("A given face never appears", format!("\\left(\\tfrac56\\right)^{{{k}}}"), miss),
        step("Six indicators, linearity", format!("6\\left(1 - {}\\right)", tex(miss)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(F::from(k), "Repeats are possible; count each face once with an indicator."),
        wrong(6 * miss, "That counts the faces that never appear."),
    ])
}

fn max_two(r: &mut Random) -> Spec {
    let m = r.choice(&[4, 6, 8, 10, 12, 20]);
    let answer = F::new((1..=m).map(|k| k * (2 * k - 1)).sum::<i64>(), m * m);
    Spec::new(format!("Roll two fair {m}-sided dice. What is $\\mathbb{{E}}[\\max]$?"), answer, vec![
        step("$P(\\max = k) = \\frac{k^2 - (k-1)^2}{m^2} = \\frac{2k - 1}{m^2}$, so",
             format!("\\mathbb{{E}}[\\max] = \\sum_{{k=1}}^{{{m}}} \\frac{{k(2k-1)}}{{{}}}", m * m), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(F::new(m + 1, 2), "That's the mean of one die; the maximum is larger."),
        wrong(F::from(m), "The maximum is not always the top face."),
    ])
}

fn rerolls(r: &mut Random) -> Spec {
    let m = r.choice(&[4, 6, 8, 10, 12, 20]);
    let rerolls = r.choice(&[1, 2]);
    let mut value = F::new(m + 1, 2);
    let mut steps = vec![step("No reroll left: the mean", format!("\\frac{{{m} + 1}}{{2}}"), value)];
    for left in 1..=rerolls {
        let keep = (1..=m).find(|&x| value < F::from(x)).expect("a face beats the mean");
        let current = value;
        value = (1..=m).map(|x| if F::from(x) > current { F::from(x) } else { current }).sum::<F>() / m;
        steps.push(step(format!("With {left} reroll{}: keep {keep} or more, else reroll", if left > 1 { "s" } else { "" }),
                        format!("\\frac{{1}}{{{m}}}\\sum_x \\max(x, \\text{{continuation}})"), value));
    }
    let plural = if rerolls == 1 { "reroll once" } else { "reroll up to twice" };
    Spec::new(format!("Roll a fair {m}-sided die and score its value. You may {plural}, but you must keep the last roll. What is the game worth when played optimally?"), value, steps)
        .sigfigs(3).mistakes(vec![
            wrong(F::new(m + 1, 2), "That ignores the option to reroll a low roll."),
            wrong(F::from(m), "You can't always end on the top face."),
        ])
}

fn variance(r: &mut Random) -> Spec {
    let mut values = r.sample(&(-3..10).collect::<Vec<i64>>(), 3);
    values.sort();
    let counts: Vec<i64> = values.iter().map(|_| r.randint(1, 4)).collect();
    let total: i64 = counts.iter().sum();
    let probs: Vec<F> = counts.iter().map(|&c| F::new(c, total)).collect();
    let mean: F = probs.iter().zip(&values).map(|(&p, &v)| p * v).sum();
    let square: F = probs.iter().zip(&values).map(|(&p, &v)| p * v * v).sum();
    let answer = square - mean * mean;
    let table = values.iter().zip(&probs).map(|(v, &p)| format!("${v}$ with probability ${}$", tex(p))).collect::<Vec<_>>().join(", ");
    Spec::new(format!("$X$ takes {table}. What is $\\operatorname{{Var}}(X)$?"), answer, vec![
        step("Mean", "\\mathbb{E}[X]", mean),
        step("Second moment", "\\mathbb{E}[X^2]", square),
        step("Variance", format!("{} - \\left({}\\right)^2", tex(square), tex(mean)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(square, "Subtract $\\mathbb{E}[X]^2$ from $\\mathbb{E}[X^2]$."),
        wrong(square - mean, "Square the mean before subtracting it."),
    ])
}

fn variance_sum(r: &mut Random) -> Spec {
    let (m1, m2) = (r.randint(1, 5), r.randint(1, 5));
    let (var_x, var_y) = (F::new(m1 * (m1 + 2), 12), F::new(m2 * (m2 + 2), 12));
    let (a, b, c) = (r.choice(&[-3, -2, -1, 2, 3]), r.choice(&[-3i64, -2, -1, 1, 2, 3]), r.randint(-5, 5));
    let (tx, ty) = (a * a * var_x, b * b * var_y);
    let answer = tx + ty;
    let expr = format!("{a}X {} {}Y {} {}", if b > 0 { '+' } else { '-' }, if b.abs() != 1 { b.abs().to_string() } else { String::new() },
                       if c >= 0 { '+' } else { '-' }, c.abs());
    Spec::new(format!("$X$ and $Y$ are independent with $\\operatorname{{Var}}(X) = {}$ and $\\operatorname{{Var}}(Y) = {}$. What is $\\operatorname{{Var}}({expr})$?",
                      tex(var_x), tex(var_y)), answer, vec![
        step("Scaling squares the factor", format!("{a}^2 \\cdot {}", tex(var_x)), tx),
        step("The other term", format!("({b})^2 \\cdot {}", tex(var_y)), ty),
        step("Independent variances add; constants vanish", format!("{} + {}", tex(tx), tex(ty)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(a * var_x + b * var_y, "Variance scales by the square of the factor."),
        wrong(if b < 0 { tx - ty } else { answer }, "Variances add even when the variables are subtracted."),
        wrong(answer + c, "Adding a constant doesn't change variance."),
    ])
}

fn variance_symbolic(r: &mut Random) -> Spec {
    let (a, b) = (r.choice(&[2, 3, 4, 5]), r.choice(&[1, 2, 3]));
    let bs = if b != 1 { b.to_string() } else { String::new() };
    Spec::new(format!("$X$ and $Y$ are independent with $\\operatorname{{Var}}(X) = u$ and $\\operatorname{{Var}}(Y) = v$. In terms of $u$ and $v$, what is $\\operatorname{{Var}}({a}X - {bs}Y)$?"),
              format!("{}*u + {}*v", a * a, b * b), vec![
        line("Scaling squares the factor", format!("\\operatorname{{Var}}({a}X) = {}u", a * a)),
        line("Independent variances add, even for a difference", format!("{}u + {}v", a * a, b * b)),
    ]).kind("expression").variables(&["u", "v"]).mistakes(vec![
        wrong(format!("{}*u - {}*v", a * a, b * b), "Variances add even when the variables are subtracted."),
        wrong(format!("{a}*u + {b}*v"), "Variance scales by the square of the factor."),
        wrong(format!("{a}*u - {b}*v"), "Square the factors and add the variances."),
    ])
}

fn until_face(r: &mut Random) -> Spec {
    let m = r.choice(&[4, 6, 8, 10, 12, 20]);
    let k = r.randint(1, 3);
    let p = F::new(k, m);
    let answer = 1 / p;
    let faces = if k == 1 { "a 1".to_string() } else { format!("one of {} or {k}", (1..k).map(|i| i.to_string()).collect::<Vec<_>>().join(", ")) };
    Spec::new(format!("Roll a fair {m}-sided die until it shows {faces}. What is the expected number of rolls, including the last?"), answer, vec![
        step("Success per roll", format!("\\frac{{{k}}}{{{m}}}"), p),
        step("Geometric mean $1/p$", format!("\\frac{{1}}{{{}}}", tex(p)), answer),
    ]).kind("fraction").mistakes(vec![
        wrong(answer - 1, "Count the successful roll too."),
        wrong(p, "That's the success probability; the wait is $1/p$."),
    ])
}

fn coin_pattern(r: &mut Random) -> Spec {
    let pattern = r.choice(PATTERNS);
    let answer = pattern_wait(pattern);
    let len = pattern.len() as i64;
    Spec::new(format!("Flip a fair coin until the pattern **{pattern}** first appears. What is the expected number of flips?"), answer, vec![
        step("States: how much of the pattern you've matched; first-step equations $E_i = 1 + \\tfrac12 E_{\\text{after }H} + \\tfrac12 E_{\\text{after }T}$, solved from the start", "E_0", answer),
    ]).mistakes(vec![
        wrong(ipow(2, len as u32), "Overlaps matter: a failed match can keep a useful prefix (compare HH with HT)."),
        wrong(2 * len, "Each extra symbol doubles the wait, not adds two."),
    ])
}

fn fair_price(r: &mut Random) -> Spec {
    let p = r.choice(&[F::new(1, 2), F::new(1, 3), F::new(1, 4), F::new(1, 5), F::new(2, 5), F::new(1, 6), F::new(3, 10)]);
    let loss = r.randint(1, 12);
    let answer = loss * (1 - p) / p;
    Spec::new(format!("A bet loses {loss} points with probability ${}$ and otherwise wins $W$ points. What value of $W$ makes the bet fair (expected value zero)?", tex(1 - p)), answer, vec![
        line("Expected value", format!("{}\\,W - {} \\cdot {loss} = 0", tex(p), tex(1 - p))),
        step("Solve", format!("W = \\frac{{{} \\cdot {loss}}}{{{}}}", tex(1 - p), tex(p)), answer),
    ]).kind("fraction").mistakes(vec![
        wrong(F::from(loss), "Equal stakes are only fair at even odds."),
        wrong(loss * p / (1 - p), "The win has to make up for the more likely loss: $W = L(1-p)/p$."),
    ])
}
