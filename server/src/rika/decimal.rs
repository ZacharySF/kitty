//! The `decimal.Decimal` arithmetic the routing readouts do (`kitty.core.rika.routes`): costs
//! are stored as `str(Decimal)`, summed, divided by a count in the default context (28
//! significant digits, half to even) and then `quantize`d to eight places, half to even, before
//! `float()`.

/// `coefficient × 10^exponent`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Decimal {
    coefficient: i128,
    exponent: i32,
}

const PRECISION: u32 = 28;
const PLACES: i32 = 8;

fn digits(value: u128) -> u32 {
    value.checked_ilog10().map_or(1, |log| log + 1)
}

/// Round `value / 10^drop` half to even.
fn round_off(value: u128, drop: u32) -> Option<u128> {
    if drop == 0 {
        return Some(value);
    }
    if drop > 38 {
        return Some(0);
    }
    let unit = 10_u128.checked_pow(drop)?;
    let (kept, rest) = (value / unit, value % unit);
    let half = unit / 2;
    Some(if rest > half || (rest == half && kept % 2 == 1) { kept + 1 } else { kept })
}

impl Decimal {
    pub const ZERO: Decimal = Decimal { coefficient: 0, exponent: 0 };

    /// `Decimal(text)` for finite numbers: `[sign] digits [. digits] [e [sign] digits]`,
    /// surrounding white space allowed. None for anything else (Python's special values and
    /// grouping underscores, which no stored cost has).
    pub fn parse(text: &str) -> Option<Decimal> {
        let text = crate::search::text::strip(text);
        let (negative, text) = match text.as_bytes().first()? {
            b'-' => (true, &text[1..]),
            b'+' => (false, &text[1..]),
            _ => (false, text),
        };
        let (mantissa, exponent) = match text.find(['e', 'E']) {
            Some(at) => (&text[..at], {
                let power = &text[at + 1..];
                let unsigned = power.strip_prefix(['+', '-']).unwrap_or(power);
                if unsigned.is_empty() || !unsigned.bytes().all(|b| b.is_ascii_digit()) {
                    return None;
                }
                power.parse::<i32>().ok()?
            }),
            None => (text, 0),
        };
        let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
        if whole.is_empty() && fraction.is_empty() || !(whole.bytes().chain(fraction.bytes()).all(|b| b.is_ascii_digit())) {
            return None;
        }
        let mut coefficient: i128 = 0;
        for b in whole.bytes().chain(fraction.bytes()) {
            coefficient = coefficient.checked_mul(10)?.checked_add(i128::from(b - b'0'))?;
        }
        let exponent = exponent.checked_sub(i32::try_from(fraction.len()).ok()?)?;
        Some(Decimal { coefficient: if negative { -coefficient } else { coefficient }, exponent })
    }

    /// `self + other`, rounded to the context's precision.
    pub fn add(self, other: Decimal) -> Option<Decimal> {
        let exponent = self.exponent.min(other.exponent);
        let scale = |value: Decimal| -> Option<i128> {
            value.coefficient.checked_mul(10_i128.checked_pow(u32::try_from(value.exponent - exponent).ok()?)?)
        };
        Decimal { coefficient: scale(self)?.checked_add(scale(other)?)?, exponent }.rounded()
    }

    fn rounded(self) -> Option<Decimal> {
        let magnitude = self.coefficient.unsigned_abs();
        let excess = digits(magnitude).saturating_sub(PRECISION);
        if excess == 0 {
            return Some(self);
        }
        let kept = i128::try_from(round_off(magnitude, excess)?).ok()?;
        Decimal { coefficient: if self.coefficient < 0 { -kept } else { kept }, exponent: self.exponent.checked_add(excess as i32)? }
            .rounded()
    }

    /// `self / count` (a positive integer) to 28 significant digits, half to even: the quotient's
    /// digits by long division, one guard digit and whether anything nonzero follows.
    pub fn divide(self, count: i64) -> Option<Decimal> {
        let divisor = u128::try_from(count).ok().filter(|&divisor| divisor > 0)?;
        let magnitude = self.coefficient.unsigned_abs();
        if magnitude == 0 {
            return Some(Decimal { coefficient: 0, exponent: self.exponent });
        }
        let numerator: Vec<u8> = magnitude.to_string().bytes().map(|b| b - b'0').collect();
        // The quotient's digits, the first standing for 10^(exponent + len - 1) of the dividend.
        let (mut quotient, mut remainder, mut index) = (Vec::<u8>::new(), 0_u128, 0_usize);
        let mut significant = 0_u32;
        loop {
            let digit = numerator.get(index).copied().unwrap_or(0);
            remainder = remainder.checked_mul(10)?.checked_add(u128::from(digit))?;
            let next = (remainder / divisor) as u8;
            remainder %= divisor;
            quotient.push(next);
            if significant > 0 || next > 0 {
                significant += 1;
            }
            index += 1;
            if significant == PRECISION + 1 || (index >= numerator.len() && remainder == 0) {
                break;
            }
        }
        // `quotient[i]` stands for 10^(self.exponent + numerator.len() - 1 - i).
        let last = i32::try_from(numerator.len()).ok()? - i32::try_from(quotient.len()).ok()? + self.exponent;
        let sticky = remainder != 0 || numerator.iter().skip(index).any(|&digit| digit != 0);
        let mut coefficient = 0_u128;
        let mut exponent = last;
        if significant == PRECISION + 1 {
            let guard = quotient.pop()?;
            exponent += 1;
            for &digit in &quotient {
                coefficient = coefficient * 10 + u128::from(digit);
            }
            if guard > 5 || (guard == 5 && (sticky || coefficient % 2 == 1)) {
                coefficient += 1;
            }
        } else {
            for &digit in &quotient {
                coefficient = coefficient * 10 + u128::from(digit);
            }
        }
        let coefficient = i128::try_from(coefficient).ok()?;
        Decimal { coefficient: if self.coefficient < 0 { -coefficient } else { coefficient }, exponent }.rounded()
    }

    /// `float(self.quantize(Decimal("0.00000001")))`.
    pub fn money(self) -> Option<f64> {
        let magnitude = self.coefficient.unsigned_abs();
        let scaled = if self.exponent >= -PLACES {
            magnitude.checked_mul(10_u128.checked_pow(u32::try_from(self.exponent + PLACES).ok()?)?)?
        } else {
            round_off(magnitude, u32::try_from(-PLACES - self.exponent).ok()?)?
        };
        // quantize raises InvalidOperation past the context's precision.
        if digits(scaled) > PRECISION {
            return None;
        }
        let text = format!("{}{}.{:08}", if self.coefficient < 0 { "-" } else { "" }, scaled / 100_000_000, scaled % 100_000_000);
        text.parse().ok()
    }

    /// `float(self)`: the value correctly rounded (Settings → AI's spend today).
    pub fn to_f64(self) -> Option<f64> {
        format!("{}e{}", self.coefficient, self.exponent).parse().ok()
    }
}

#[cfg(test)]
mod tests {
    use super::Decimal;

    fn money(text: &str, count: i64) -> f64 {
        Decimal::parse(text).unwrap().divide(count).unwrap().money().unwrap()
    }

    #[test]
    fn quotients_round_as_python_does() {
        // float((Decimal(text) / count).quantize(Decimal("0.00000001"))) in CPython.
        assert_eq!(money("0.0024", 12), 0.0002);
        assert_eq!(money("0.0028", 7), 0.0004);
        assert_eq!(money("1E-7", 3), 3e-8);
        assert_eq!(money("0.000000045", 9), 0.0);
        assert_eq!(money("0.000000025", 1), 2e-8);
        assert_eq!(money("0.000000035", 1), 4e-8);
        assert_eq!(money("123.456789012345678901234567", 1), 123.45678901);
        assert_eq!(money("0.3333333333", 1), 0.33333333);
        assert_eq!(money("2", 3), 0.66666667);
        assert_eq!(money("0", 5), 0.0);
        assert_eq!(money("-0.000000015", 1), -2e-8);
        let sum = Decimal::parse("0.1").unwrap().add(Decimal::parse("1E-7").unwrap()).unwrap();
        assert_eq!(sum.divide(1).unwrap().money(), Some(0.1000001));
        assert!(Decimal::parse("NaN").is_none() && Decimal::parse("1_0").is_none() && Decimal::parse(".").is_none());
        assert_eq!(Decimal::parse(" .5e1 "), Decimal::parse("5"));
    }
}
