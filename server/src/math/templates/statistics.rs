//! `templates/statistics.py`: basic statistics: summaries, spread, z-scores, the normal rule and standard errors.

use crate::math::problems::{line, note, num, step, template, tex, wrong, Spec, Template};
use crate::math::frac::F;
use crate::math::pyrandom::Random;

pub static TEMPLATES: &[Template] = &[
    template("stats.mean-median/mean", 30, mean),
    template("stats.mean-median/median", 25, median),
    template("stats.spread/variance", 60, spread),
    template("stats.z-score/value", 20, z_score),
    template("stats.normal-rule/share", 15, normal_rule),
    template("stats.standard-error/mean", 30, standard_error),
    template("stats.confidence/upper-95", 45, confidence),
];

/// (k, share within k sd, share above +k sd) as the rule states them, in percent.
const NORMAL_RULE: [(i64, &str, &str); 3] = [(1, "68", "16"), (2, "95", "2.5"), (3, "99.7", "0.15")];

fn z_975() -> F {
    F::new(196, 100)
}

/// `_data`.
fn data(r: &mut Random, size: i64) -> Vec<i64> {
    (0..size).map(|_| r.randint(1, 30)).collect()
}

/// `_list`.
fn list(values: &[i64]) -> String {
    values.iter().map(i64::to_string).collect::<Vec<_>>().join(", ")
}

/// The middle of `values` as Python's `F(v[n // 2] + v[(n - 1) // 2], 2)`.
fn middle_of(values: &[i64]) -> F {
    let n = values.len();
    F::new(values[n / 2] + values[(n - 1) / 2], 2)
}

fn mean(r: &mut Random) -> Spec {
    let size = r.randint(5, 8);
    let data = data(r, size);
    let (total, n) = (data.iter().sum::<i64>(), data.len() as i64);
    let answer = F::new(total, n);
    let mut ordered = data.clone();
    ordered.sort();
    Spec::new(format!("What is the mean of ${}$?", list(&data)), answer, vec![
        step("Sum", data.iter().map(i64::to_string).collect::<Vec<_>>().join(" + "), total),
        step("Divide by the count", format!("\\frac{{{total}}}{{{n}}}"), answer),
    ]).kind("fraction").mistakes(vec![
        wrong(middle_of(&ordered), "That's the median; the mean averages every value."),
        wrong(F::from(total), format!("Divide the sum by the {n} values.")),
    ])
}

fn median(r: &mut Random) -> Spec {
    let size = r.randint(5, 8);
    let data = data(r, size);
    let mut ordered = data.clone();
    ordered.sort();
    let answer = middle_of(&ordered);
    let middle = if data.len() % 2 == 1 { "the middle value" } else { "the mean of the two middle values" };
    Spec::new(format!("What is the median of ${}$?", list(&data)), answer, vec![
        line("Sort", list(&ordered)),
        step(format!("Take {middle}"), "", answer),
    ]).kind("number").mistakes(vec![
        wrong(middle_of(&data), "Sort the values before taking the middle."),
        wrong(F::new(data.iter().sum::<i64>(), data.len() as i64), "That's the mean."),
    ])
}

fn spread(r: &mut Random) -> Spec {
    let size = r.randint(4, 6);
    let data = data(r, size);
    let sample = r.random() < 0.5;
    let centre = F::new(data.iter().sum::<i64>(), size);
    let squares: F = data.iter().map(|&x| (F::from(x) - centre).pow(2)).sum();
    let divisor = if sample { size - 1 } else { size };
    let answer = squares / divisor;
    let which = if sample { "sample variance (divide by $n - 1$)" } else { "population variance (divide by $n$)" };
    Spec::new(format!("What is the {which} of ${}$?", list(&data)), answer, vec![
        step("Mean", "\\bar x", centre),
        step("Sum of squared deviations", "\\sum (x_i - \\bar x)^2", squares),
        step("Divide", format!("\\frac{{{}}}{{{divisor}}}", tex(squares)), answer),
    ]).sigfigs(3).mistakes(vec![
        wrong(squares / if sample { size } else { size - 1 }, format!("Check the divisor: {}", if sample { "$n - 1$ for a sample." } else { "$n$ for the population." })),
        wrong(squares, "Divide the squared deviations by the count."),
    ])
}

fn z_score(r: &mut Random) -> Spec {
    let mu = r.randint(10, 200);
    let sigma = r.choice(&[2, 4, 5, 8, 10, 12, 15, 20, 25]);
    let z = F::new(r.randint(-12, 12), 4);
    let x = z * sigma + mu;
    Spec::new(format!("Scores have mean ${mu}$ and standard deviation ${sigma}$. What is the $z$-score of ${}$?", num(x)), z, vec![
        step("Distance from the mean", format!("{} - {mu}", num(x)), x - mu),
        step("In standard deviations", format!("\\frac{{{}}}{{{sigma}}}", num(x - mu)), z),
    ]).kind("number").mistakes(vec![
        wrong(x - mu, format!("Divide the distance by the standard deviation ${sigma}$.")),
        wrong(z * -1, "Check the sign: above the mean is positive."),
    ])
}

/// `F(within)` for the rule's within-shares.
fn share(text: &str) -> F {
    match text.split_once('.') {
        Some((whole, frac)) => F::new(format!("{whole}{frac}").parse::<i64>().expect("digits"), 10i64.pow(frac.len() as u32)),
        None => F::from(text.parse::<i64>().expect("digits")),
    }
}

fn normal_rule(r: &mut Random) -> Spec {
    let (k, within, above) = r.choice(&NORMAL_RULE);
    let outside = r.random() < 0.5;
    let (mu, sigma) = (r.randint(50, 150), r.choice(&[5, 10, 15, 20]));
    let shares = ["50%", "68%", "95%", "99.7%", "16%", "2.5%", "0.15%", "32%", "5%"];
    let rest = num(F::from(100) - share(within));
    let (question, answer, wrongs) = if outside {
        (format!("above ${}$", mu + k * sigma), format!("{above}%"), vec![format!("{within}%"), format!("{rest}%"), "50%".to_string()])
    } else {
        let mut wrongs = vec![format!("{above}%"), "50%".to_string()];
        wrongs.extend(NORMAL_RULE.iter().filter(|(_, w, _)| *w != within).take(1).map(|(_, w, _)| format!("{w}%")));
        (format!("between ${}$ and ${}$", mu - k * sigma, mu + k * sigma), format!("{within}%"), wrongs)
    };
    let mut pool = vec![answer.clone()];
    for w in wrongs {
        if w != answer && !pool.contains(&w) {
            pool.push(w);
        }
    }
    let need = 4usize.saturating_sub(pool.len());
    let extra: Vec<String> = shares.iter().filter(|s| !pool.iter().any(|p| p == *s)).take(need).map(|s| s.to_string()).collect();
    pool.extend(extra);
    let mut choices: Vec<String> = pool.into_iter().take(4).collect();
    r.shuffle(&mut choices);
    let rule = format!("About {within}% lies within {k} standard deviation{}", if k > 1 { "s" } else { "" });
    let tail = if outside { format!("; the other {rest}% splits evenly between the tails") } else { String::new() };
    Spec::new(format!("A quantity is normally distributed with mean ${mu}$ and standard deviation ${sigma}$. About what share of values lies {question}?"), answer, vec![
        line("Distance in standard deviations", format!("\\frac{{{}}}{{{sigma}}} = {k}", k * sigma)),
        note(rule + &tail),
    ]).kind("choice").choices(choices).mistakes(if outside {
        vec![wrong(format!("{within}%"), "That's the share within the band; the question asks about one tail.")]
    } else {
        vec![wrong(format!("{above}%"), "That's one tail; the band is the middle.")]
    })
}

fn standard_error(r: &mut Random) -> Spec {
    let sigma = r.choice(&[2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30]);
    let root = r.randint(2, 20);
    let n = root * root;
    let answer = F::new(sigma, root);
    Spec::new(format!("Each observation has standard deviation ${sigma}$. What is the standard error of the mean of ${n}$ independent observations?"), answer, vec![
        step("Variance of the mean", format!("\\frac{{{sigma}^2}}{{{n}}}"), F::new(sigma * sigma, n)),
        step("Standard error", format!("\\frac{{{sigma}}}{{\\sqrt{{{n}}}}}"), answer),
    ]).kind("fraction").mistakes(vec![
        wrong(F::new(sigma, n), "Divide by $\\sqrt n$, not $n$."),
        wrong(F::new(sigma * sigma, n), "That's the variance of the mean; take its square root."),
    ])
}

fn confidence(r: &mut Random) -> Spec {
    let sigma = r.choice(&[4i64, 5, 6, 8, 10, 12, 15, 20]);
    let root = r.choice(&[4i64, 5, 8, 10, 12, 15, 20]);
    let n = root * root;
    let xbar = F::new(r.randint(200, 2000), 10);
    let upper = r.random() < 0.5;
    let se = F::new(sigma, root);
    let margin = z_975() * se;
    let answer = if upper { xbar + margin } else { xbar - margin };
    let end = if upper { "upper" } else { "lower" };
    let title = if upper { "Upper" } else { "Lower" };
    Spec::new(format!("A sample of ${n}$ has mean ${}$; the population standard deviation is ${sigma}$. Using $z = 1.96$, what is the {end} end of the 95% confidence interval for the mean? (2 decimal places)", num(xbar)), answer, vec![
        step("Standard error", format!("\\frac{{{sigma}}}{{\\sqrt{{{n}}}}}"), se),
        step("Margin", format!("1.96 \\times {}", num(se)), margin),
        step(format!("{title} end"), format!("{} {} {}", num(xbar), if upper { '+' } else { '-' }, num(margin)), answer),
    ]).kind("number").tolerance(F::new(1, 100) * F::new(1, 2) + F::new(1, 1000)).mistakes(vec![
        wrong(xbar + if upper { z_975() * sigma } else { z_975() * -sigma }, "Scale by the standard error $\\sigma/\\sqrt n$, not $\\sigma$."),
        wrong(if upper { xbar - margin } else { xbar + margin }, format!("The {end} end {} the margin.", if upper { "adds" } else { "subtracts" })),
    ])
}
