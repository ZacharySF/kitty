//! `decimal.Decimal(text)` as CPython's `_decimal` reads a string: underscores dropped
//! anywhere, Unicode white space and decimal digits of any script mapped to ASCII, ASCII white
//! space stripped at both ends, then mpdecimal's grammar (`[sign] digits [. digits]
//! [e [sign] digits]`, `Infinity`, `NaN` and `sNaN` with a payload, any case).

use crate::search::text::{decimal_value, is_space};

/// A parsed value: a finite `(-1)^negative × digits × 10^exponent`, or a special one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Decimal {
    Finite { negative: bool, digits: String, exponent: i64 },
    Special,
}

/// `Decimal(text)`, or None where Python raises `InvalidOperation`.
pub fn parse(text: &str) -> Option<Decimal> {
    let mut ascii = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '_' => {}
            '\u{1}'..='\u{7f}' => ascii.push(c),
            c if is_space(c) => ascii.push(' '),
            c => ascii.push(char::from(b'0' + u8::try_from(decimal_value(c.encode_utf8(&mut [0; 4]))?).ok()?)),
        }
    }
    let body = ascii.trim_matches(|c: char| matches!(c, ' ' | '\t' | '\n' | '\r' | '\u{b}' | '\u{c}'));
    let (negative, body) = match body.as_bytes().first()? {
        b'-' => (true, &body[1..]),
        b'+' => (false, &body[1..]),
        _ => (false, body),
    };
    let lower = body.to_ascii_lowercase();
    if lower == "inf" || lower == "infinity" {
        return Some(Decimal::Special);
    }
    for prefix in ["snan", "nan"] {
        if let Some(payload) = lower.strip_prefix(prefix) {
            return payload.bytes().all(|b| b.is_ascii_digit()).then_some(Decimal::Special);
        }
    }
    let (mantissa, power) = match body.find(['e', 'E']) {
        Some(at) => (&body[..at], Some(&body[at + 1..])),
        None => (body, None),
    };
    let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    if whole.is_empty() && fraction.is_empty() || !whole.bytes().chain(fraction.bytes()).all(|b| b.is_ascii_digit()) {
        return None;
    }
    let mut exponent: i64 = match power {
        None => 0,
        Some(power) => {
            let unsigned = power.strip_prefix(['+', '-']).unwrap_or(power);
            if unsigned.is_empty() || !unsigned.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            let magnitude = unsigned.bytes().try_fold(0_i64, |acc, b| acc.checked_mul(10)?.checked_add(i64::from(b - b'0')))
                .unwrap_or(i64::MAX / 4);
            if power.starts_with('-') { -magnitude } else { magnitude }
        }
    };
    exponent -= fraction.len() as i64;
    let digits: String = format!("{whole}{fraction}").trim_start_matches('0').to_owned();
    Some(Decimal::Finite { negative, digits: if digits.is_empty() { "0".into() } else { digits }, exponent })
}

impl Decimal {
    pub fn is_zero(&self) -> bool {
        matches!(self, Decimal::Finite { digits, .. } if digits == "0")
    }

    /// `minimum <= self <= maximum` for small non-negative integer bounds (finite values).
    pub fn within(&self, maximum: u32) -> bool {
        let Decimal::Finite { negative, digits, exponent } = self else { return false };
        if self.is_zero() {
            return true;
        }
        if *negative {
            return false;
        }
        let bound = maximum.to_string();
        // Compare digits × 10^exponent with `bound` by their adjusted exponents, then digits.
        let adjusted = digits.len() as i64 - 1 + exponent;
        let bound_adjusted = bound.len() as i64 - 1;
        if adjusted != bound_adjusted {
            return adjusted < bound_adjusted;
        }
        let width = digits.len().max(bound.len());
        format!("{digits:0<width$}") <= format!("{bound:0<width$}")
    }

    /// `float(self)` (correctly rounded); None for a special value.
    pub fn to_f64(&self) -> Option<f64> {
        let Decimal::Finite { negative, digits, exponent } = self else { return None };
        let value: f64 = format!("{digits}e{exponent}").parse().ok()?;
        Some(if *negative { -value } else { value })
    }

    /// The plain ASCII spelling `[-]digitsE<exponent>`, for the context arithmetic.
    pub fn ascii(&self) -> Option<String> {
        let Decimal::Finite { negative, digits, exponent } = self else { return None };
        Some(format!("{}{digits}E{exponent}", if *negative { "-" } else { "" }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn finite(negative: bool, digits: &str, exponent: i64) -> Option<Decimal> {
        Some(Decimal::Finite { negative, digits: digits.into(), exponent })
    }

    #[test]
    fn reads_text_as_the_c_decimal_does() {
        assert_eq!(parse("1_000"), finite(false, "1000", 0));
        assert_eq!(parse("_1"), finite(false, "1", 0));
        assert_eq!(parse("1_0.5_0"), finite(false, "1050", -2));
        assert_eq!(parse("١٢"), finite(false, "12", 0));
        assert_eq!(parse("\u{3000}5 "), finite(false, "5", 0));
        assert_eq!(parse(".5"), finite(false, "5", -1));
        assert_eq!(parse("5."), finite(false, "5", 0));
        assert_eq!(parse("-0"), finite(true, "0", 0));
        assert_eq!(parse("1E+2"), finite(false, "1", 2));
        assert_eq!(parse("1e-07"), finite(false, "1", -7));
        for special in ["inf", "Infinity", "-INF", "nan", "NaN12", "sNaN"] {
            assert_eq!(parse(special), Some(Decimal::Special), "{special}");
        }
        for invalid in ["", " ", "0x10", "1 0", "5\u{0}", "None", "[1]", "e5", ".", "1e", "\u{1c}5", "nan x"] {
            assert_eq!(parse(invalid), None, "{invalid:?}");
        }
    }

    #[test]
    fn bounds_and_floats() {
        let within = |text: &str| parse(text).unwrap().within(100);
        assert!(within("100") && within("100.000") && within("1E+2") && within("0") && within("-0") && within("99.999"));
        assert!(!within("100.0000001") && !within("101") && !within("-0.001") && !within("1E+3"));
        assert_eq!(parse("1e-07").unwrap().to_f64(), Some(1e-07));
        assert_eq!(parse("12.50").unwrap().to_f64(), Some(12.5));
    }
}
