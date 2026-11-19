//! `kitty.core.math.problems`: the template registry and the problem model (prompt, exact
//! answer, worked steps, common-mistake detectors) and its TeX and decimal formatting.
//!
//! A template is a function of a seeded `random.Random` (crate::math::pyrandom) returning a
//! [`Spec`]; `generate(template_id, seed)` seeds it with `"<template_id>:<seed>"`, so the
//! stored `(template_id, seed)` gives Python's problem. Answers are exact: a [`F`] for numbers
//! and fractions, a SymPy expression string for algebra, the text of a choice.

use super::frac::F;
use super::pyrandom::Random;
use super::templates;

/// `Value`: an exact number, or text (a SymPy expression or a choice).
#[derive(Clone, Debug, PartialEq)]
pub enum Answer {
    Num(F),
    Text(String),
}

impl Answer {
    pub fn num(&self) -> Option<F> {
        match self {
            Answer::Num(value) => Some(*value),
            Answer::Text(_) => None,
        }
    }

    pub fn text(&self) -> Option<&str> {
        match self {
            Answer::Text(text) => Some(text),
            Answer::Num(_) => None,
        }
    }
}

impl From<F> for Answer {
    fn from(value: F) -> Answer {
        Answer::Num(value)
    }
}

impl From<i64> for Answer {
    fn from(value: i64) -> Answer {
        Answer::Num(F::from(value))
    }
}

impl From<i32> for Answer {
    fn from(value: i32) -> Answer {
        Answer::Num(F::from(value))
    }
}

impl From<String> for Answer {
    fn from(text: String) -> Answer {
        Answer::Text(text)
    }
}

impl From<&str> for Answer {
    fn from(text: &str) -> Answer {
        Answer::Text(text.to_owned())
    }
}

/// One worked-solution line, `**why**: $tex = value$`. A step with a value is fadeable: a
/// guided lesson blanks it and checks the learner's value.
#[derive(Clone, Debug, PartialEq)]
pub struct Step {
    pub why: String,
    pub tex: String,
    pub value: Option<F>,
}

impl Step {
    /// `Step.md(blank)`.
    pub fn md(&self, blank: bool) -> String {
        let Some(value) = self.value else {
            return if self.tex.is_empty() { format!("**{}**", self.why) } else { format!("**{}:** ${}$", self.why, self.tex) };
        };
        let result = if blank { "[[blank]]".to_owned() } else { format!("${}$", num(value)) };
        if self.tex.is_empty() {
            return format!("**{}:** {result}", self.why);
        }
        if blank {
            return format!("**{}:** ${} =$ {result}", self.why, self.tex);
        }
        format!("**{}:** ${} = {}$", self.why, self.tex, num(value))
    }
}

/// `Step(why, tex, value)`.
pub fn step(why: impl Into<String>, tex: impl Into<String>, value: impl Into<F>) -> Step {
    Step { why: why.into(), tex: tex.into(), value: Some(value.into()) }
}

/// `Step(why, tex)`: a line without a value.
pub fn line(why: impl Into<String>, tex: impl Into<String>) -> Step {
    Step { why: why.into(), tex: tex.into(), value: None }
}

/// `Step(why)`.
pub fn note(why: impl Into<String>) -> Step {
    line(why, "")
}

/// A `(wrong answer, feedback)` pair.
pub fn wrong(value: impl Into<Answer>, message: impl Into<String>) -> (Answer, String) {
    (value.into(), message.into())
}

/// What a template returns for one seed (Python's `Spec`; `params` are never shown, so they
/// are not kept).
#[derive(Clone, Debug)]
pub struct Spec {
    pub prompt: String,
    pub answer: Answer,
    pub steps: Vec<Step>,
    pub kind: Option<&'static str>,
    /// (wrong answer, feedback) pairs; one equal to the right answer (or an earlier one) is dropped.
    pub mistakes: Vec<(Answer, String)>,
    pub choices: Option<Vec<String>>,
    pub tolerance: Option<F>,
    pub sigfigs: Option<u32>,
    pub variables: Vec<&'static str>,
    /// An antiderivative: any added constant (`+ C` or a number) is accepted.
    pub up_to_constant: bool,
}

impl Spec {
    pub fn new(prompt: impl Into<String>, answer: impl Into<Answer>, steps: Vec<Step>) -> Spec {
        Spec { prompt: prompt.into(), answer: answer.into(), steps, kind: None, mistakes: Vec::new(), choices: None, tolerance: None,
               sigfigs: None, variables: Vec::new(), up_to_constant: false }
    }

    pub fn kind(mut self, kind: &'static str) -> Spec {
        self.kind = Some(kind);
        self
    }

    pub fn mistakes(mut self, mistakes: Vec<(Answer, String)>) -> Spec {
        self.mistakes = mistakes;
        self
    }

    pub fn choices(mut self, choices: Vec<String>) -> Spec {
        self.choices = Some(choices);
        self
    }

    pub fn tolerance(mut self, tolerance: F) -> Spec {
        self.tolerance = Some(tolerance);
        self
    }

    pub fn sigfigs(mut self, sigfigs: u32) -> Spec {
        self.sigfigs = Some(sigfigs);
        self
    }

    pub fn variables(mut self, variables: &[&'static str]) -> Spec {
        self.variables = variables.to_vec();
        self
    }

    pub fn up_to_constant(mut self) -> Spec {
        self.up_to_constant = true;
        self
    }
}

/// A registered generator; its topic is the id before `/`.
pub struct Template {
    pub id: &'static str,
    pub make: fn(&mut Random) -> Spec,
    /// Seconds a fluent learner needs (Python's default is 30).
    pub target_s: u32,
}

impl Template {
    pub fn topic_id(&self) -> &'static str {
        self.id.split_once('/').map_or(self.id, |(topic, _)| topic)
    }
}

/// `@template(id, target_s=...)`.
pub const fn template(id: &'static str, target_s: u32, make: fn(&mut Random) -> Spec) -> Template {
    Template { id, make, target_s }
}

#[derive(Clone, Debug)]
pub struct Problem {
    pub template_id: String,
    pub topic_id: String,
    pub seed: i64,
    pub prompt_md: String,
    pub answer: Answer,
    pub answer_kind: &'static str,
    pub steps: Vec<Step>,
    pub mistakes: Vec<(Answer, String)>,
    pub choices: Option<Vec<String>>,
    pub tolerance: Option<F>,
    pub sigfigs: Option<u32>,
    pub variables: Vec<String>,
    /// Seconds a fluent learner needs; reviews grade time against it.
    pub target_s: u32,
    pub up_to_constant: bool,
}

/// A template id with no generator (an attempt stored by a later version): Python's KeyError.
#[derive(Debug)]
pub struct UnknownTemplate;

/// Every template in Python's registration order (the strands in import order).
pub fn all() -> impl Iterator<Item = &'static Template> {
    templates::STRANDS.iter().flat_map(|strand| strand.iter())
}

/// `for_topic`.
pub fn for_topic(topic_id: &str) -> Vec<&'static Template> {
    all().filter(|item| item.topic_id() == topic_id).collect()
}

/// `generate(template_id, seed)`.
pub fn generate(template_id: &str, seed: i64) -> Result<Problem, UnknownTemplate> {
    let item = all().find(|item| item.id == template_id).ok_or(UnknownTemplate)?;
    let spec = (item.make)(&mut Random::from_text(&format!("{template_id}:{seed}")));
    // Templates whose answers are sometimes non-integers declare their kind, so the kind never
    // hints at the answer; the checker accepts every exact form anyway.
    let kind = spec.kind.unwrap_or(match &spec.answer {
        Answer::Text(_) => "expression",
        Answer::Num(value) if spec.sigfigs.is_some_and(|figures| figures != 0) || spec.tolerance.is_some_and(|tolerance| !tolerance.is_zero()) || !value.is_int() => "fraction",
        Answer::Num(_) => "number",
    });
    let mut seen = vec![spec.answer.clone()];
    let mut mistakes = Vec::new();
    for (value, message) in spec.mistakes {
        if !seen.contains(&value) {
            seen.push(value.clone());
            mistakes.push((value, message));
        }
    }
    Ok(Problem {
        template_id: template_id.to_owned(),
        topic_id: item.topic_id().to_owned(),
        seed,
        prompt_md: spec.prompt,
        answer: spec.answer,
        answer_kind: kind,
        steps: spec.steps,
        mistakes,
        choices: spec.choices.filter(|choices| !choices.is_empty()),
        tolerance: spec.tolerance,
        sigfigs: spec.sigfigs,
        variables: spec.variables.iter().map(|name| (*name).to_owned()).collect(),
        target_s: item.target_s,
        up_to_constant: spec.up_to_constant,
    })
}

/// `pick(topic_id, seed)`: the template a topic uses for `seed` (several rotate); None for a
/// topic without templates (Python's KeyError).
pub fn pick(topic_id: &str, seed: i64) -> Option<&'static Template> {
    let mut options = for_topic(topic_id);
    if options.is_empty() {
        return None;
    }
    options.sort_by_key(|item| item.id);
    let index = Random::from_text(&format!("{topic_id}:{seed}")).randbelow(options.len() as u64) as usize;
    Some(options[index])
}

impl Problem {
    /// `Problem.canonical`: the right answer as TeX (numbers also get a decimal echo). An
    /// expression's is SymPy's `latex`, which only the worker has: None here.
    pub fn canonical(&self) -> Option<String> {
        let value = match &self.answer {
            Answer::Text(text) if self.answer_kind == "choice" => return Some(text.clone()),
            Answer::Text(_) => return None,
            Answer::Num(_) if self.answer_kind == "expression" => return None,
            Answer::Num(value) if self.answer_kind == "choice" => return Some(value.to_string()),
            Answer::Num(value) => *value,
        };
        let exact = decimal_text(value);
        if value.is_int() || (exact.is_some() && self.answer_kind == "number") {
            return Some(exact.unwrap_or_else(|| value.numer().to_string()));
        }
        Some(match exact {
            Some(exact) => format!("{} = {exact}", tex(value)),
            None => format!("{} \\approx {}", tex(value), approx(value, 4)),
        })
    }

    /// `step_problem(index)`: the guided-lesson problem for faded step `index`; None for a step
    /// without a value (Python's "That step can't be faded").
    pub fn step_problem(&self, index: usize) -> Option<Problem> {
        let value = self.steps.get(index)?.value?;
        let whole = value.is_int();
        Some(Problem {
            template_id: self.template_id.clone(),
            topic_id: self.topic_id.clone(),
            seed: self.seed,
            prompt_md: self.prompt_md.clone(),
            answer: Answer::Num(value),
            answer_kind: if whole { "number" } else { "fraction" },
            steps: self.steps[..=index].to_vec(),
            mistakes: Vec::new(),
            choices: None,
            tolerance: None,
            sigfigs: if whole { None } else { Some(self.sigfigs.filter(|figures| *figures != 0).unwrap_or(3)) },
            variables: Vec::new(),
            target_s: self.target_s,
            up_to_constant: false,
        })
    }
}

/// `tex(value)`.
pub fn tex(value: F) -> String {
    if value.is_int() {
        return value.numer().to_string();
    }
    let sign = if value < 0 { "-" } else { "" };
    format!("{sign}\\frac{{{}}}{{{}}}", value.numer().abs(), value.denom())
}

/// `decimal_text(value)`: exact decimal text for a terminating fraction with at most 6
/// places, else None.
pub fn decimal_text(value: F) -> Option<String> {
    let mut scaled = value;
    for places in 0..7usize {
        if places > 0 {
            scaled = value * F::int(10_i128.pow(places as u32));
        }
        if scaled.is_int() {
            if places == 0 {
                return Some(scaled.numer().to_string());
            }
            let sign = if scaled < 0 { "-" } else { "" };
            let digits = format!("{:0>width$}", scaled.numer().abs(), width = places + 1);
            let (whole, fraction) = digits.split_at(digits.len() - places);
            return Some(format!("{sign}{whole}.{fraction}"));
        }
    }
    None
}

/// `approx(value, places)`: `f"{float(value):.{places}g}"` below 1, two more figures above.
pub fn approx(value: F, places: usize) -> String {
    format_g(value.to_f64(), if value.abs() < 1 { places } else { places + 2 })
}

/// `num(value)`: a parameter or intermediate value as compact TeX (decimals stay decimal).
pub fn num(value: impl Into<F>) -> String {
    let value = value.into();
    decimal_text(value).unwrap_or_else(|| tex(value))
}

/// Python's `format(x, f".{precision}g")` for a finite double.
pub fn format_g(x: f64, precision: usize) -> String {
    let precision = precision.max(1);
    if x == 0.0 {
        return if x.is_sign_negative() { "-0".into() } else { "0".into() };
    }
    // The digits rounded to `precision` significant figures, and the exponent after rounding.
    let scientific = format!("{:.*e}", precision - 1, x);
    let (mantissa, exponent) = scientific.split_once('e').expect("exponent notation");
    let exponent: i32 = exponent.parse().expect("an exponent");
    let (sign, mantissa) = mantissa.strip_prefix('-').map_or(("", mantissa), |rest| ("-", rest));
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    if -4 <= exponent && exponent < precision as i32 {
        let (whole, fraction) = if exponent >= 0 {
            let split = exponent as usize + 1;
            (digits[..split].to_owned(), digits[split..].to_owned())
        } else {
            ("0".to_owned(), "0".repeat((-exponent - 1) as usize) + &digits)
        };
        let fraction = fraction.trim_end_matches('0');
        return if fraction.is_empty() { format!("{sign}{whole}") } else { format!("{sign}{whole}.{fraction}") };
    }
    let fraction = digits[1..].trim_end_matches('0');
    let point = if fraction.is_empty() { String::new() } else { format!(".{fraction}") };
    format!("{sign}{}{point}e{}{:02}", &digits[..1], if exponent < 0 { '-' } else { '+' }, exponent.abs())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_as_python() {
        for (x, p, want) in [(0.0078125, 4, "0.007812"), (12345.5, 4, "1.235e+04"), (0.000123455, 4, "0.0001235"), (9.9996, 4, "10"),
                             (1.0 / 3.0, 4, "0.3333"), (2.0 / 3.0 * 100.0, 6, "66.6667"), (1e-5, 4, "1e-05"), (-0.5, 4, "-0.5"), (123456.0, 6, "123456")] {
            assert_eq!(format_g(x, p), want, "{x}");
        }
        assert_eq!(decimal_text(F::new(-1, 8)).as_deref(), Some("-0.125"));
        assert_eq!(decimal_text(F::new(1, 3)), None);
        assert_eq!(num(F::new(1, 3)), "\\frac{1}{3}");
        assert_eq!(tex(F::new(-7, 2)), "-\\frac{7}{2}");
    }
}
