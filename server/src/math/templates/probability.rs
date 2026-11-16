//! `templates/probability.py`: equally likely outcomes through conditioning, Bayes and first-step analysis.

use crate::math::frac::{comb, F};
use crate::math::problems::{line, num, step, template, tex, wrong, Spec, Template};
use crate::math::pyrandom::Random;

pub static TEMPLATES: &[Template] = &[
    template("prob.basic/dice-sum", 30, dice_sum),
    template("prob.complement/at-least-one-six", 30, at_least_one_six),
    template("prob.complement/symbolic", 40, at_least_one_symbolic),
    template("prob.independence/union", 25, union),
    template("prob.hypergeometric/urn", 45, hypergeometric),
    template("prob.conditional/dice-given", 60, conditional_dice),
    template("prob.total-probability/urns", 45, total_probability),
    template("prob.bayes/test", 60, bayes_test),
    template("prob.binomial/exactly-k", 45, binomial),
    template("prob.geometric/first-success", 30, geometric),
    template("prob.symmetry/higher-die", 30, symmetry),
    template("prob.order-stats/max-of-dice", 45, max_of_dice),
    template("prob.gamblers-ruin/reach-target", 60, gamblers_ruin),
];

/// `NICE`: simple probabilities that read well in a prompt.
fn nice() -> [F; 12] {
    [F::new(1, 2), F::new(1, 3), F::new(2, 3), F::new(1, 4), F::new(3, 4), F::new(1, 5), F::new(2, 5), F::new(3, 5), F::new(1, 6), F::new(1, 10), F::new(3, 10), F::new(7, 10)]
}

fn dice_sum(r: &mut Random) -> Spec {
    let m = r.choice(&[4, 6, 8]);
    let s = r.randint(3, 2 * m - 1);
    let ways = (1..=m).flat_map(|a| (1..=m).map(move |b| a + b)).filter(|&t| t == s).count() as i64;
    let answer = F::new(ways, m * m);
    Spec::new(format!("Roll two fair {m}-sided dice. What is the probability their sum is {s}?"), answer, vec![
        step("Equally likely ordered outcomes", format!("{m} \\times {m}"), m * m),
        step(format!("Pairs summing to {s}"), format!("(a, {s} - a) \\text{{ with both in }} 1..{m}"), ways),
        step("Probability", format!("\\frac{{{ways}}}{{{}}}", m * m), answer),
    ]).sigfigs(3).mistakes(vec![wrong(F::new(1, 2 * m - 1), "The sums aren't equally likely; count ordered pairs.")])
}

fn at_least_one_six(r: &mut Random) -> Spec {
    let n = r.randint(2, 6);
    let none = F::new(5, 6).pow(n as i32);
    let answer = 1 - none;
    Spec::new(format!("Roll a fair die {n} times. What is the probability of at least one six?"), answer, vec![
        step("No six at all", format!("\\left(\\tfrac{{5}}{{6}}\\right)^{{{n}}}"), none),
        step("Complement", format!("1 - {}", tex(none)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(F::new(n, 6), "Adding $\\frac16$ per roll double counts rolls with several sixes."),
        wrong(none, "That's the chance of no six; take the complement."),
    ])
}

fn at_least_one_symbolic(r: &mut Random) -> Spec {
    let n = r.randint(2, 6);
    Spec::new(format!("Each of {n} independent trials succeeds with probability $p$. In terms of $p$, what is the probability of at least one success?"),
              format!("1 - (1 - p)**{n}"), vec![
        line("All fail", format!("(1-p)^{{{n}}}")),
        line("Complement", format!("1 - (1-p)^{{{n}}}")),
    ]).kind("expression").variables(&["p"]).mistakes(vec![
        wrong(format!("{n}*p"), "Adding $p$ per trial double counts outcomes with several successes."),
        wrong(format!("(1 - p)**{n}"), "That's the chance every trial fails; take the complement."),
        wrong(format!("p**{n}"), "That's the chance every trial succeeds."),
    ])
}

fn union(r: &mut Random) -> Spec {
    let (a, b) = (r.choice(&nice()), r.choice(&nice()));
    let answer = a + b - a * b;
    Spec::new(format!("Independent events have $P(A) = {}$ and $P(B) = {}$. What is $P(A \\cup B)$?", tex(a), tex(b)), answer, vec![
        step("Both, by independence", format!("{} \\cdot {}", tex(a), tex(b)), a * b),
        step("Inclusion–exclusion", format!("{} + {} - {}", tex(a), tex(b), tex(a * b)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(a + b, "The overlap $A \\cap B$ is counted twice; subtract $P(A)P(B)$."),
        wrong(a * b, "That's $P(A \\cap B)$, both at once."),
    ])
}

fn hypergeometric(r: &mut Random) -> Spec {
    let (red, blue) = (r.randint(2, 6), r.randint(2, 6));
    let k = r.randint(2, 4.min(red + blue - 1));
    let j = r.randint(0.max(k - blue), k.min(red));
    let good = comb(red, j) * comb(blue, k - j);
    let total = comb(red + blue, k);
    let answer = F::new(good, total);
    let pr = F::new(red, red + blue);
    Spec::new(format!("An urn holds {red} red and {blue} blue balls. Draw {k} without replacement. What is the probability that exactly {j} are red?"), answer, vec![
        step("Equally likely hands", format!("\\binom{{{}}}{{{k}}}", red + blue), total),
        step("Hands with exactly the right reds", format!("\\binom{{{red}}}{{{j}}} \\binom{{{blue}}}{{{}}}", k - j), good),
        step("Probability", format!("\\frac{{{good}}}{{{total}}}"), answer),
    ]).sigfigs(3).mistakes(vec![wrong(comb(k, j) * pr.pow(j as i32) * (F::ONE - pr).pow((k - j) as i32),
                                      "That's sampling with replacement; the urn changes after each draw.")])
}

fn conditional_dice(r: &mut Random) -> Spec {
    let n = r.choice(&[2, 3]);
    let s = r.randint(n + 2, 6 * n - 2);
    let mut rolls: Vec<Vec<i64>> = vec![vec![]];
    for _ in 0..n {
        rolls = rolls.into_iter().flat_map(|roll| (1..=6).map(move |face| { let mut next = roll.clone(); next.push(face); next })).collect();
    }
    let given: Vec<&Vec<i64>> = rolls.iter().filter(|roll| roll.iter().sum::<i64>() >= s).collect();
    let same = given.iter().filter(|roll| roll.iter().all(|&face| face == roll[0])).count() as i64;
    let given = given.len() as i64;
    let answer = F::new(same, given);
    Spec::new(format!("Roll {n} fair dice. Given the sum is at least {s}, what is the probability all dice show the same value?"), answer, vec![
        step(format!("Outcomes with sum at least {s}"), format!("\\#\\{{\\text{{sum}} \\ge {s}\\}}"), given),
        step("Of those, all equal", format!("\\#\\{{\\text{{all equal, sum}} \\ge {s}\\}}"), same),
        step("Condition", format!("\\frac{{{same}}}{{{given}}}"), answer),
    ]).sigfigs(3).mistakes(vec![wrong(F::new(6, 1) / F::from(6).pow(n as i32), "You didn't condition on the sum: restrict to outcomes with a large enough sum.")])
}

fn total_probability(r: &mut Random) -> Spec {
    let q = r.choice(&[F::new(1, 2), F::new(1, 3), F::new(2, 3), F::new(1, 4), F::new(3, 4)]);
    let (r1, b1, r2, b2) = (r.randint(1, 6), r.randint(1, 6), r.randint(1, 6), r.randint(1, 6));
    let (pa, pb) = (F::new(r1, r1 + b1), F::new(r2, r2 + b2));
    let answer = q * pa + (1 - q) * pb;
    Spec::new(format!("Urn A holds {r1} red and {b1} blue; urn B holds {r2} red and {b2} blue. Pick urn A with probability ${}$, otherwise urn B, then draw one ball. What is $P(\\text{{red}})$?", tex(q)), answer, vec![
        step("Through A", format!("{} \\cdot {}", tex(q), tex(pa)), q * pa),
        step("Through B", format!("{} \\cdot {}", tex(1 - q), tex(pb)), (1 - q) * pb),
        step("Add the branches", format!("{} + {}", tex(q * pa), tex((1 - q) * pb)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(F::new(r1 + r2, r1 + b1 + r2 + b2), "Pooling the urns ignores how likely each urn is and their sizes."),
        wrong((pa + pb) / 2, "Weight each urn by how likely it is to be picked."),
    ])
}

fn bayes_test(r: &mut Random) -> Spec {
    let prevalence = r.choice(&[F::new(1, 100), F::new(2, 100), F::new(5, 100), F::new(1, 10)]);
    let sensitivity = r.choice(&[F::new(9, 10), F::new(95, 100), F::new(99, 100), F::new(8, 10)]);
    let false_positive = r.choice(&[F::new(5, 100), F::new(1, 10), F::new(2, 100)]);
    let positive = sensitivity * prevalence + false_positive * (1 - prevalence);
    let answer = sensitivity * prevalence / positive;
    let pct = |value: F| num(value * 100);
    Spec::new(format!("{}% of people have a condition. A test detects {}% of cases and gives a false positive for {}% of everyone else. You test positive: what is $P(\\text{{condition}} \\mid +)$?",
                      pct(prevalence), pct(sensitivity), pct(false_positive)), answer, vec![
        line("Bayes' rule", "P(C \\mid +) = \\frac{P(+ \\mid C)\\,P(C)}{P(+)}"),
        step("Total probability", format!("P(+) = {} \\cdot {} + {} \\cdot {}", num(sensitivity), num(prevalence), num(false_positive), num(1 - prevalence)), positive),
        step("Divide", format!("\\frac{{{}}}{{{}}}", num(sensitivity * prevalence), num(positive)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(sensitivity, "That's $P(+ \\mid C)$; Bayes flips it to $P(C \\mid +)$."),
        wrong(sensitivity * prevalence, "Divide by $P(+)$, the chance of any positive result."),
    ])
}

fn binomial(r: &mut Random) -> Spec {
    let n = r.randint(3, 8);
    let p = r.choice(&[F::new(1, 2), F::new(1, 3), F::new(2, 3), F::new(1, 4)]);
    let k = r.randint(0, n);
    let one = p.pow(k as i32) * (F::ONE - p).pow((n - k) as i32);
    let answer = comb(n, k) * one;
    Spec::new(format!("A coin lands heads with probability ${}$. Flip it {n} times. What is the probability of exactly {k} heads?", tex(p)), answer, vec![
        step("Positions of the heads", format!("\\binom{{{n}}}{{{k}}}"), comb(n, k)),
        step("One such sequence", format!("\\left({}\\right)^{{{k}}} \\left({}\\right)^{{{}}}", tex(p), tex(1 - p), n - k), one),
        step("Multiply", format!("{} \\cdot {}", comb(n, k), tex(one)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(one, format!("Count the $\\binom{{{n}}}{{{k}}}$ orders the heads can come in.")),
        wrong(comb(n, k) * p.pow(k as i32), format!("Include the tails: $(1-p)^{{{}}}$.", n - k)),
    ])
}

fn geometric(r: &mut Random) -> Spec {
    let m = r.choice(&[4, 6, 8, 10, 12, 20]);
    let k = r.randint(1, 6);
    let p = F::new(1, m);
    let misses = (F::ONE - p).pow((k - 1) as i32);
    let answer = misses * p;
    Spec::new(format!("Roll a fair {m}-sided die until it shows a 1. What is the probability the first 1 comes on roll {k}?"), answer, vec![
        step(format!("{} misses first", k - 1), format!("\\left({}\\right)^{{{}}}", tex(1 - p), k - 1), misses),
        step("Then a hit", format!("{} \\cdot {}", tex(misses), tex(p)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong((F::ONE - p).pow(k as i32) * p, format!("Only {} rolls miss before the hit on roll {k}.", k - 1)),
        wrong(p, "Earlier rolls have to miss first."),
    ])
}

fn symmetry(r: &mut Random) -> Spec {
    let m = r.choice(&[4, 6, 8, 10, 12, 20]);
    let answer = F::new(m - 1, 2 * m);
    Spec::new(format!("Alice and Bob each roll a fair {m}-sided die. What is the probability Alice rolls strictly higher?"), answer, vec![
        step("A tie", format!("\\frac{{{m}}}{{{}}}", m * m), F::new(1, m)),
        step("By symmetry the rest splits evenly", format!("\\frac{{1}}{{2}}\\left(1 - {}\\right)", tex(F::new(1, m))), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(F::new(1, 2), "Ties are possible; only the non-tied outcomes split evenly."),
        wrong(F::new(m + 1, 2 * m), "That's 'at least as high': it includes the ties."),
    ])
}

fn max_of_dice(r: &mut Random) -> Spec {
    let (n, m) = (r.randint(2, 4), r.choice(&[4, 6, 8]));
    let k = r.randint(1, m);
    let (upper, lower) = (F::new(k, m).pow(n as i32), F::new(k - 1, m).pow(n as i32));
    let answer = upper - lower;
    Spec::new(format!("Roll {n} fair {m}-sided dice. What is the probability the largest value shown is exactly {k}?"), answer, vec![
        step(format!("All at most {k}"), format!("\\left(\\tfrac{{{k}}}{{{m}}}\\right)^{{{n}}}"), upper),
        step(format!("All at most {}", k - 1), format!("\\left(\\tfrac{{{}}}{{{m}}}\\right)^{{{n}}}", k - 1), lower),
        step("Difference", format!("{} - {}", tex(upper), tex(lower)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(upper, format!("That's $P(\\max \\le {k})$; subtract $P(\\max \\le {})$.", k - 1)),
        wrong(F::new(1, m), "The maximum isn't uniform."),
    ])
}

fn gamblers_ruin(r: &mut Random) -> Spec {
    let target = r.randint(4, 10);
    let start = r.randint(1, target - 1);
    let p = r.choice(&[F::new(1, 2), F::new(1, 3), F::new(2, 5), F::new(3, 5), F::new(2, 3)]);
    let fair = p == F::new(1, 2);
    let (answer, steps);
    if fair {
        answer = F::new(start, target);
        steps = vec![step("First step: $h_i = \\tfrac12 h_{i-1} + \\tfrac12 h_{i+1}$, so $h$ is linear with $h_0 = 0$, $h_N = 1$",
                          format!("h_{{{start}}} = \\frac{{{start}}}{{{target}}}"), answer)];
    } else {
        let ratio = (F::ONE - p) / p;
        answer = (ratio.pow(start as i32) - 1) / (ratio.pow(target as i32) - 1);
        steps = vec![
            line("First step: $h_i = p\\,h_{i+1} + q\\,h_{i-1}$ gives $h_i = \\frac{(q/p)^i - 1}{(q/p)^N - 1}$ with", format!("q/p = {}", tex(ratio))),
            step("Plug in", format!("\\frac{{({})^{{{start}}} - 1}}{{({})^{{{target}}} - 1}}", tex(ratio), tex(ratio)), answer),
        ];
    }
    Spec::new(format!("You start with {start} chips and bet 1 chip at a time, winning each bet with probability ${}$. You stop at 0 chips or at {target}. What is the probability you reach {target}?", tex(p)),
              answer, steps).sigfigs(3).mistakes(if fair { vec![] } else {
        vec![wrong(F::new(start, target), "That's the fair-coin answer; with a biased bet use $(q/p)^i$.")]
    })
}
