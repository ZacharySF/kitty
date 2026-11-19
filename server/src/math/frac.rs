//! Python's `fractions.Fraction` and the integer operators the generators use, over i128:
//! every value a template makes is small (the parity test runs every template over many
//! seeds), and an overflow panics rather than giving a wrong answer.

use std::cmp::Ordering;
use std::fmt;
use std::ops::{Add, Div, Mul, Neg, Sub};

/// An exact rational in lowest terms with a positive denominator.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct F {
    n: i128,
    d: i128,
}

fn gcd(mut a: i128, mut b: i128) -> i128 {
    a = a.abs();
    b = b.abs();
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

/// Python's `a // b` (floor division).
pub fn floordiv(a: i64, b: i64) -> i64 {
    let q = a / b;
    if (a % b != 0) && ((a < 0) != (b < 0)) { q - 1 } else { q }
}

/// Python's `a % b` (the result takes the divisor's sign).
pub fn pymod(a: i64, b: i64) -> i64 {
    a - b * floordiv(a, b)
}

/// `pow(base, exponent, modulus)` for a positive modulus.
pub fn powmod(base: i64, mut exponent: u64, modulus: i64) -> i64 {
    let (mut result, mut base) = (1i128 % modulus as i128, pymod(base, modulus) as i128);
    while exponent > 0 {
        if exponent & 1 == 1 {
            result = result * base % modulus as i128;
        }
        base = base * base % modulus as i128;
        exponent >>= 1;
    }
    result as i64
}

/// `math.gcd`.
pub fn igcd(a: i64, b: i64) -> i64 {
    gcd(a as i128, b as i128) as i64
}

/// `math.lcm` of two integers.
pub fn ilcm(a: i64, b: i64) -> i64 {
    if a == 0 || b == 0 { 0 } else { (a / igcd(a, b) * b).abs() }
}

/// `math.factorial`.
pub fn factorial(n: i64) -> i64 {
    (1..=n).product()
}

/// `math.comb`.
pub fn comb(n: i64, k: i64) -> i64 {
    if k < 0 || k > n {
        return 0;
    }
    let k = k.min(n - k);
    let mut out: i128 = 1;
    for i in 0..k {
        out = out * (n - i) as i128 / (i + 1) as i128;
    }
    out as i64
}

/// `math.perm`.
pub fn perm(n: i64, k: i64) -> i64 {
    if k < 0 || k > n { 0 } else { (n - k + 1..=n).product() }
}

/// `base ** exponent` for a non-negative exponent, checked.
pub fn ipow(base: i64, exponent: u32) -> i64 {
    base.checked_pow(exponent).expect("an integer power within range")
}

impl F {
    /// `Fraction(n, d)`; a zero denominator is Python's ZeroDivisionError (a panic here: no
    /// template divides by zero for any seed).
    pub fn new(n: impl Into<i128>, d: impl Into<i128>) -> F {
        let (n, d) = (n.into(), d.into());
        assert!(d != 0, "Fraction(n, 0)");
        let g = gcd(n, d).max(1);
        let (n, d) = (n / g, d / g);
        if d < 0 { F { n: -n, d: -d } } else { F { n, d } }
    }

    pub const ZERO: F = F { n: 0, d: 1 };
    pub const ONE: F = F { n: 1, d: 1 };

    /// `Fraction(n)`.
    pub fn int(n: impl Into<i128>) -> F {
        F { n: n.into(), d: 1 }
    }

    pub fn numer(self) -> i128 {
        self.n
    }

    pub fn denom(self) -> i128 {
        self.d
    }

    pub fn is_int(self) -> bool {
        self.d == 1
    }

    pub fn is_zero(self) -> bool {
        self.n == 0
    }

    pub fn abs(self) -> F {
        F { n: self.n.abs(), d: self.d }
    }

    /// `self ** k` for any integer `k` (a negative power of zero is a ZeroDivisionError).
    pub fn pow(self, k: i32) -> F {
        let (n, d) = (self.n.checked_pow(k.unsigned_abs()).expect("a power within range"), self.d.checked_pow(k.unsigned_abs()).expect("a power within range"));
        if k >= 0 { F { n, d } } else { F::new(d, n) }
    }

    /// `math.floor(self)`.
    pub fn floor(self) -> i128 {
        self.n.div_euclid(self.d)
    }

    /// The integer value of a whole fraction, as i64 (for indices and loop bounds).
    pub fn to_i64(self) -> i64 {
        assert!(self.d == 1, "a whole fraction");
        i64::try_from(self.n).expect("an integer within range")
    }

    /// `float(self)`: `numerator / denominator` correctly rounded, as Python's int division is.
    pub fn to_f64(self) -> f64 {
        const EXACT: u128 = 1 << 53;
        let (negative, n, d) = (self.n < 0, self.n.unsigned_abs(), self.d as u128);
        let value = if n <= EXACT && d <= EXACT {
            n as f64 / d as f64
        } else {
            divide_rounded(n, d)
        };
        if negative { -value } else { value }
    }

    fn checked(n: Option<i128>, d: Option<i128>) -> F {
        F::new(n.expect("a fraction within range"), d.expect("a fraction within range"))
    }
}

/// `n / d` for positive integers below 2^126, rounded to the nearest double (ties to even), as
/// CPython's `int / int` rounds it.
fn divide_rounded(n: u128, d: u128) -> f64 {
    assert!(d < 1 << 126, "a denominator within range");
    if n == 0 {
        return 0.0;
    }
    let bits = |value: u128| 128 - value.leading_zeros() as i32;
    // q = floor(n * 2^scale / d) with 55 or 56 significant bits; `sticky`: a remainder is left.
    let scale = 55 - (bits(n) - bits(d));
    let (mut q, sticky) = if scale > 0 {
        let (mut q, mut r) = (n / d, n % d);
        for _ in 0..scale {
            r <<= 1;
            q = (q << 1) | u128::from(r >= d);
            if r >= d {
                r -= d;
            }
        }
        (q, r != 0)
    } else {
        let divisor = d << -scale;
        (n / divisor, n % divisor != 0)
    };
    let extra = bits(q) - 53;
    let low = q & ((1 << extra) - 1);
    let half = 1 << (extra - 1);
    q >>= extra;
    if low > half || (low == half && (sticky || q & 1 == 1)) {
        q += 1;
    }
    (q as f64) * 2f64.powi(extra - scale)
}

impl fmt::Display for F {
    /// `str(Fraction)`: `n` or `n/d`.
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.d == 1 { write!(formatter, "{}", self.n) } else { write!(formatter, "{}/{}", self.n, self.d) }
    }
}

impl PartialOrd for F {
    fn partial_cmp(&self, other: &F) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for F {
    fn cmp(&self, other: &F) -> Ordering {
        (self.n * other.d).cmp(&(other.n * self.d))
    }
}

impl Neg for F {
    type Output = F;
    fn neg(self) -> F {
        F { n: -self.n, d: self.d }
    }
}

impl Add for F {
    type Output = F;
    fn add(self, other: F) -> F {
        F::checked(self.n.checked_mul(other.d).and_then(|a| other.n.checked_mul(self.d).and_then(|b| a.checked_add(b))), self.d.checked_mul(other.d))
    }
}

impl Sub for F {
    type Output = F;
    fn sub(self, other: F) -> F {
        self + -other
    }
}

impl Mul for F {
    type Output = F;
    fn mul(self, other: F) -> F {
        F::checked(self.n.checked_mul(other.n), self.d.checked_mul(other.d))
    }
}

impl Div for F {
    type Output = F;
    fn div(self, other: F) -> F {
        assert!(other.n != 0, "division by zero");
        F::checked(self.n.checked_mul(other.d), self.d.checked_mul(other.n))
    }
}

macro_rules! integers {
    ($($t:ty),*) => {$(
        impl From<$t> for F {
            fn from(n: $t) -> F {
                F::int(n as i128)
            }
        }
        impl Add<$t> for F { type Output = F; fn add(self, other: $t) -> F { self + F::from(other) } }
        impl Sub<$t> for F { type Output = F; fn sub(self, other: $t) -> F { self - F::from(other) } }
        impl Mul<$t> for F { type Output = F; fn mul(self, other: $t) -> F { self * F::from(other) } }
        impl Div<$t> for F { type Output = F; fn div(self, other: $t) -> F { self / F::from(other) } }
        impl Add<F> for $t { type Output = F; fn add(self, other: F) -> F { F::from(self) + other } }
        impl Sub<F> for $t { type Output = F; fn sub(self, other: F) -> F { F::from(self) - other } }
        impl Mul<F> for $t { type Output = F; fn mul(self, other: F) -> F { F::from(self) * other } }
        impl Div<F> for $t { type Output = F; fn div(self, other: F) -> F { F::from(self) / other } }
        impl PartialEq<$t> for F { fn eq(&self, other: &$t) -> bool { *self == F::from(*other) } }
        impl PartialOrd<$t> for F { fn partial_cmp(&self, other: &$t) -> Option<Ordering> { Some(self.cmp(&F::from(*other))) } }
    )*};
}
integers!(i32, i64, i128);

impl std::iter::Sum for F {
    fn sum<I: Iterator<Item = F>>(items: I) -> F {
        items.fold(F::ZERO, |total, item| total + item)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fractions_reduce_and_print_as_python() {
        assert_eq!(F::new(6, -4).to_string(), "-3/2");
        assert_eq!(F::new(4, 2).to_string(), "2");
        assert_eq!((F::new(1, 3) + F::new(1, 6)).to_string(), "1/2");
        assert_eq!(F::new(2, 3).pow(-2).to_string(), "9/4");
        assert_eq!(F::new(-7, 2).floor(), -4);
        assert_eq!(floordiv(-7, 2), -4);
        assert_eq!(pymod(-7, 3), 2);
        assert_eq!(powmod(3, 200, 50), 1);
        assert_eq!(F::new(1, 3).to_f64(), 1.0 / 3.0);
        assert_eq!(F::new(3, 500).to_f64(), 0.006);
        let big = F::new(10_i128.pow(30) + 1, 3);
        assert_eq!(big.to_f64(), 3.333333333333333e29);
        assert_eq!(F::new(1, 10_i128.pow(25) + 7).to_f64(), 1e-25);
    }
}
