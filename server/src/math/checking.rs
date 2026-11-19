//! `kitty.core.math.checking` for numbers, fractions and choices: exact values, a stated
//! tolerance or significant figures for decimals. Expressions are SymPy's (the worker's
//! `kitty.core.math.cas`, crate::math `equivalent`); their pre-checks are here.
//!
//! A verdict is `equal`, `mistake` (a template's known wrong answer, with its feedback),
//! `different` or `undecided`; undecided never marks an answer wrong.
//!
//! A typed number is kept exactly ([`Big`] numerator and denominator, as `Fraction` keeps
//! it), with `re`'s `\d` as CPython's `str.isdecimal` and `Decimal`'s exponent limits.
//! An exponent so large or small that Python would expand a power of ten of millions of
//! digits is judged with the exponent bounded at [`EXPONENT_BOUND`], which changes no
//! comparison against the problem's small numbers.

use serde_json::Value;

use super::big::Big;
use super::frac::F;
use super::problems::{Answer, Problem};
use crate::ported::py;
use crate::search::text::{decimal_value, is_decimal};

pub const MAX_ANSWER: usize = 500;
/// Beyond 10^±4000 every comparison with a problem's numbers (at most ~10^600 in size, the
/// significant figures included) comes out as it would for the exact value.
const EXPONENT_BOUND: i128 = 4000;
/// libmpdec's `MAX_EMAX` and `MIN_ETINY` (the exact context `Decimal(text)` uses).
const EMAX: i128 = 999_999_999_999_999_999;
const ETINY: i128 = -1_999_999_999_999_999_997;

#[derive(Clone, Debug, PartialEq)]
pub struct Verdict {
    pub correct: Option<bool>,
    pub kind: String,
    pub feedback: Option<String>,
}

fn verdict(correct: Option<bool>, kind: &str, feedback: Option<String>) -> Verdict {
    Verdict { correct, kind: kind.to_owned(), feedback }
}

fn equal() -> Verdict {
    verdict(Some(true), "equal", None)
}

fn different() -> Verdict {
    verdict(Some(false), "different", None)
}

fn undecided(feedback: impl Into<String>) -> Verdict {
    verdict(None, "undecided", Some(feedback.into()))
}

/// A worker's `(kind, feedback)` for an expression: `correct` as Python maps it.
pub fn from_kind(kind: &str, feedback: Option<String>) -> Verdict {
    let correct = match kind {
        "equal" => Some(true),
        "mistake" | "different" => Some(false),
        _ => None,
    };
    verdict(correct, kind, feedback)
}

/// `Number`: `p / q` with `q > 0`, and the significant figures typed (None for a fraction).
struct Number {
    p: Big,
    q: Big,
    sigfigs: Option<usize>,
}

fn big(value: i128) -> Big {
    Big::from_i128(value)
}

/// The digits of a run of decimal characters, most significant first.
fn digit_values(text: &str) -> Vec<u32> {
    text.chars().map(|c| decimal_value(c.encode_utf8(&mut [0; 4])).expect("a decimal digit") as u32).collect()
}

/// `_digits`: significant digits typed; leading (ASCII) zeros don't count.
fn significant(mantissa: &str) -> usize {
    py::len(mantissa.replace('.', "").trim_start_matches('0')).max(1)
}

/// The end of the run of decimal characters starting at byte `at`.
fn digits_end(text: &str, at: usize) -> usize {
    text[at..].char_indices().find(|(_, c)| !is_decimal(*c)).map_or(text.len(), |(index, _)| at + index)
}

fn sign_len(text: &str, at: usize) -> usize {
    usize::from(matches!(text.as_bytes().get(at), Some(b'+' | b'-')))
}

/// `_THOUSANDS.fullmatch`: `[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?`.
fn thousands(text: &str) -> bool {
    let mut at = sign_len(text, 0);
    let end = digits_end(text, at);
    if !(1..=3).contains(&text[at..end].chars().count()) {
        return false;
    }
    at = end;
    let mut groups = 0;
    while text[at..].starts_with(',') {
        let end = digits_end(text, at + 1);
        if text[at + 1..end].chars().count() != 3 {
            return false;
        }
        at = end;
        groups += 1;
    }
    if groups == 0 {
        return false;
    }
    if text[at..].starts_with('.') {
        let end = digits_end(text, at + 1);
        return end > at + 1 && end == text.len();
    }
    at == text.len()
}

/// `_FRACTION.fullmatch`: `([+-]?)(\d+)/(\d+)`, as (negative, top, bottom).
fn fraction(text: &str) -> Option<(bool, &str, &str)> {
    let start = sign_len(text, 0);
    let end = digits_end(text, start);
    if end == start || !text[end..].starts_with('/') {
        return None;
    }
    let bottom_end = digits_end(text, end + 1);
    (bottom_end > end + 1 && bottom_end == text.len()).then(|| (text.starts_with('-'), &text[start..end], &text[end + 1..]))
}

/// `_TEX_FRACTION.fullmatch`: `([+-]?)\\[dt]?frac\{?([+-]?\d+)\}?\{?(\d+)\}?`, with the groups
/// a backtracking match gives (the top takes as many digits as it can).
fn tex_fraction(text: &str) -> Option<(bool, String, &str)> {
    let negative = text.starts_with('-');
    let mut rest = &text[sign_len(text, 0)..];
    rest = rest.strip_prefix('\\')?;
    rest = rest.strip_prefix('d').or_else(|| rest.strip_prefix('t')).filter(|after| after.starts_with("frac")).unwrap_or(rest);
    rest = rest.strip_prefix("frac")?;
    rest = rest.strip_prefix('{').unwrap_or(rest);
    let sign = &rest[..sign_len(rest, 0)];
    let run_end = digits_end(rest, sign.len());
    let run = &rest[sign.len()..run_end];
    if run.is_empty() {
        return None;
    }
    let after = &rest[run_end..];
    // The whole run as the top: `}`? `{`? then the bottom's digits, `}`?, the end.
    let tail = after.strip_prefix('}').unwrap_or(after);
    let tail = tail.strip_prefix('{').unwrap_or(tail);
    let bottom_end = digits_end(tail, 0);
    if bottom_end > 0 && matches!(&tail[bottom_end..], "" | "}") {
        return Some((negative, format!("{sign}{run}"), &tail[..bottom_end]));
    }
    // Otherwise the run's last digit is the bottom (its longest top that still leaves one).
    if matches!(after, "" | "}") && run.chars().count() > 1 {
        let split = run.char_indices().last().map(|(index, _)| index).expect("two digits");
        return Some((negative, format!("{sign}{}", &run[..split]), &run[split..]));
    }
    None
}

/// `_DECIMAL.fullmatch`: `[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?`, as (negative,
/// integer digits, fraction digits, exponent text with its sign).
fn decimal(text: &str) -> Option<(bool, &str, &str, &str)> {
    let start = sign_len(text, 0);
    let int_end = digits_end(text, start);
    let (int_part, mut at) = (&text[start..int_end], int_end);
    let mut frac = "";
    if text[at..].starts_with('.') {
        let end = digits_end(text, at + 1);
        frac = &text[at + 1..end];
        at = end;
        if int_part.is_empty() && frac.is_empty() {
            return None;
        }
    } else if int_part.is_empty() {
        return None;
    }
    let mut exponent = "";
    if text[at..].starts_with(['e', 'E']) {
        let sign = sign_len(text, at + 1);
        let end = digits_end(text, at + 1 + sign);
        if end == at + 1 + sign {
            return None;
        }
        exponent = &text[at + 1..end];
        at = end;
    }
    (at == text.len()).then_some((text.starts_with('-'), int_part, frac, exponent))
}

/// `Fraction(Decimal(text))` for text `_DECIMAL` matched; None where `Decimal` raises
/// InvalidOperation (an exponent beyond libmpdec's limits).
fn decimal_number(negative: bool, int_part: &str, frac: &str, exponent: &str) -> Option<(Big, Big)> {
    let mut given: i128 = 0;
    let (exponent_negative, exponent_digits) = match exponent.as_bytes().first() {
        Some(b'-') => (true, &exponent[1..]),
        Some(b'+') => (false, &exponent[1..]),
        _ => (false, exponent),
    };
    for digit in digit_values(exponent_digits) {
        given = given * 10 + i128::from(digit);
        if given > i128::from(i64::MAX) {
            return None;
        }
    }
    if exponent_negative {
        given = -given;
    }
    let digits: Vec<u32> = digit_values(int_part).into_iter().chain(digit_values(frac)).collect();
    let exp = given - frac.chars().count() as i128;
    let significant = digits.iter().skip_while(|digit| **digit == 0).count().max(1) as i128;
    let zero = digits.iter().all(|digit| *digit == 0);
    let adjusted = if zero { exp } else { exp + significant - 1 };
    if adjusted > EMAX || exp < ETINY {
        return None;
    }
    let mut coefficient = Big::from_digits(digits);
    if negative {
        coefficient = coefficient.neg();
    }
    let bounded = exp.clamp(-EXPONENT_BOUND, EXPONENT_BOUND);
    Some(if bounded >= 0 {
        (coefficient.mul(&Big::pow10(bounded as u32)), big(1))
    } else {
        (coefficient, Big::pow10((-bounded) as u32))
    })
}

/// `parse_number`: integers, decimals, a/b, TeX `\frac`, percentages and JSON numbers; None
/// if unreadable.
fn parse_number(answer: &Value) -> Option<Number> {
    let owned;
    let answer = match answer {
        Value::Bool(_) => return None,
        Value::Number(number) if !number.is_f64() => {
            let value = number.as_i64().map(i128::from).or_else(|| number.as_u64().map(i128::from))?;
            return Some(Number { p: big(value), q: big(1), sigfigs: Some(significant(&value.unsigned_abs().to_string())) });
        }
        Value::Number(number) => {
            owned = py::float_repr(number.as_f64()?);
            owned.as_str()
        }
        Value::String(text) => text.as_str(),
        _ => return None,
    };
    if py::len(answer) > MAX_ANSWER {
        return None;
    }
    let mut text = py::strip(py::strip(answer).trim_matches('$')).to_owned();
    for (old, new) in [("\\left", ""), ("\\right", ""), ("−", "-"), ("–", "-"), ("{,}", "."), ("\\%", "%"), ("\\,", ""), ("\\!", ""),
                       ("\\ ", ""), ("\u{2009}", ""), ("\u{202f}", ""), (" ", ""), ("_", "")] {
        text = text.replace(old, new);
    }
    let percent = text.ends_with('%');
    if percent {
        text.pop();
    }
    if text.starts_with('(') && text.ends_with(')') && !text.is_empty() {
        // `text[1:-1]`: a lone "(" is both, and slicing leaves it empty.
        text = if text.len() >= 2 { text[1..text.len() - 1].to_owned() } else { String::new() };
    }
    if thousands(&text) {
        text = text.replace(',', "");
    } else if text.matches(',').count() == 1 && !text.contains('.') {
        text = text.replace(',', ".");
    }
    let number = if let Some((negative, top, bottom)) = fraction(&text).map(|(n, t, b)| (n, t.to_owned(), b)).or_else(|| tex_fraction(&text)) {
        let (top_negative, top_digits) = match top.as_bytes().first() {
            Some(b'-') => (true, &top[1..]),
            Some(b'+') => (false, &top[1..]),
            _ => (false, top.as_str()),
        };
        let bottom = Big::from_digits(digit_values(bottom));
        if bottom.is_zero() {
            return None;
        }
        let mut p = Big::from_digits(digit_values(top_digits));
        if top_negative != negative {
            p = p.neg();
        }
        Number { p, q: bottom, sigfigs: None }
    } else if let Some((negative, int_part, frac, exponent)) = decimal(&text) {
        let (p, q) = decimal_number(negative, int_part, frac, exponent)?;
        let unsigned = text.trim_start_matches(['+', '-']);
        let mantissa = unsigned.split('e').next().unwrap_or("").split('E').next().unwrap_or("");
        Number { p, q, sigfigs: Some(significant(mantissa)) }
    } else {
        return None;
    };
    Some(if percent { Number { q: number.q.mul(&big(100)), ..number } } else { number })
}

/// `|value - target| <= num / den` (den > 0).
fn within(number: &Number, target: F, (num, den): &(Big, Big)) -> bool {
    let difference = number.p.mul(&big(target.denom())).sub(&big(target.numer()).mul(&number.q)).abs();
    difference.mul(den) <= num.mul(&number.q).mul(&big(target.denom()))
}

fn exact(number: &Number, target: F) -> bool {
    number.p.mul(&big(target.denom())) == big(target.numer()).mul(&number.q)
}

/// `_unit(target, sigfigs)`: half a unit in the last place of `target` at `sigfigs` figures.
fn unit(target: F, sigfigs: usize) -> (Big, Big) {
    if target.is_zero() {
        return (big(0), big(1));
    }
    let magnitude = target.abs();
    let exponent: i128 = if magnitude >= 1 {
        (magnitude.numer() / magnitude.denom()).to_string().len() as i128 - 1
    } else {
        // The first k with |target| * 10^k >= 1 (the targets here are far above 10^-38).
        -(1..39).find(|k| magnitude.numer() * 10_i128.pow(*k) >= magnitude.denom()).map_or(39, i128::from)
    };
    let power = exponent - sigfigs as i128 + 1;
    if power >= 0 { (Big::pow10(power as u32), big(2)) } else { (big(1), Big::pow10((-power) as u32).mul(&big(2))) }
}

#[derive(PartialEq)]
enum Closeness {
    Exact,
    Close,
    Rough,
    Far,
}

/// `_close`.
fn close(number: &Number, target: F, problem: &Problem) -> Closeness {
    if exact(number, target) {
        return Closeness::Exact;
    }
    if let Some(tolerance) = problem.tolerance {
        if within(number, target, &(big(tolerance.numer()), big(tolerance.denom()))) {
            return Closeness::Close;
        }
    }
    let (Some(typed), Some(wanted)) = (number.sigfigs, problem.sigfigs) else { return Closeness::Far };
    if typed >= wanted as usize {
        return if within(number, target, &unit(target, wanted as usize)) { Closeness::Close } else { Closeness::Far };
    }
    if within(number, target, &unit(target, typed)) { Closeness::Rough } else { Closeness::Far }
}

fn check_number(problem: &Problem, answer: &Value) -> Verdict {
    let Some(number) = parse_number(answer) else {
        return undecided("Couldn't read that as a number; try 42, 0.25, 3/7 or 25%.");
    };
    let target = problem.answer.num().expect("a numeric problem has a number answer");
    let closeness = close(&number, target, problem);
    if matches!(closeness, Closeness::Exact | Closeness::Close) {
        return equal();
    }
    for (value, message) in &problem.mistakes {
        if let Answer::Num(value) = value {
            if matches!(close(&number, *value, problem), Closeness::Exact | Closeness::Close) {
                return verdict(Some(false), "mistake", Some(message.clone()));
            }
        }
    }
    if closeness == Closeness::Rough {
        return undecided(format!("Give at least {} significant figures.", problem.sigfigs.map_or("None".to_owned(), |figures| figures.to_string())));
    }
    if let Some(typed) = number.sigfigs {
        if problem.tolerance.is_none() && problem.sigfigs.is_none() && !target.is_int() && within(&number, target, &unit(target, typed.max(1))) {
            return undecided("Give the exact value, for example as a fraction.");
        }
    }
    different()
}

fn check_choice(problem: &Problem, answer: &Value) -> Verdict {
    let choices = problem.choices.as_deref().unwrap_or(&[]);
    let mut picked = answer;
    let by_index;
    if let Some(index) = py::int(answer) {
        if (0..choices.len() as i64).contains(&index) {
            by_index = Value::String(choices[index as usize].clone());
            picked = &by_index;
        }
    }
    let Some(text) = picked.as_str().map(py::strip).filter(|text| choices.iter().any(|choice| choice == text)) else {
        return undecided("Pick one of the choices.");
    };
    if problem.answer.text() == Some(text) {
        return equal();
    }
    for (value, message) in &problem.mistakes {
        if value.text() == Some(text) {
            return verdict(Some(false), "mistake", Some(message.clone()));
        }
    }
    different()
}

/// `_check_expression`'s checks before SymPy: None when the answer goes to the worker.
pub fn expression_precheck(answer: &Value) -> Option<Verdict> {
    match answer.as_str() {
        Some(text) if !py::strip(text).is_empty() => {
            (py::len(text) > MAX_ANSWER).then(|| undecided(format!("Keep the expression under {MAX_ANSWER} characters.")))
        }
        _ => Some(undecided("Enter an expression.")),
    }
}

/// `check` for every kind but an expression (which needs the worker).
pub fn check(problem: &Problem, answer: &Value) -> Verdict {
    match problem.answer_kind {
        "choice" => check_choice(problem, answer),
        _ => check_number(problem, answer),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn read(text: &str) -> Option<String> {
        parse_number(&Value::String(text.into())).map(|number| format!("{:?}/{:?} {:?}", number.p, number.q, number.sigfigs))
    }

    #[test]
    fn tex_fractions_split_as_a_backtracking_match_does() {
        assert_eq!(tex_fraction("\\frac12").map(|(n, t, b)| (n, t, b.to_owned())), Some((false, "1".into(), "2".into())));
        assert_eq!(tex_fraction("-\\dfrac{12}").map(|(n, t, b)| (n, t, b.to_owned())), Some((true, "1".into(), "2".into())));
        assert_eq!(tex_fraction("\\frac{-3}{4}").map(|(n, t, b)| (n, t, b.to_owned())), Some((false, "-3".into(), "4".into())));
        assert!(tex_fraction("\\frac1").is_none() && tex_fraction("\\frac{1}{2}x").is_none());
        assert!(thousands("1,234,567.5") && !thousands("1234,567") && !thousands("12,34"));
        assert!(read("1e9999999999999999999").is_none() && read("1e5").is_some() && read("0x1").is_none());
    }
}
