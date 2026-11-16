//! The arbitrary-precision integers an answer check needs: a learner may type a long decimal
//! or fraction (up to 500 characters), which Python's `Fraction` holds exactly. Only what
//! the comparisons use: decimal parsing, powers of ten, add, subtract, multiply, compare.

use std::cmp::Ordering;

/// A signed integer: sign and base-2^32 magnitude, least significant limb first, no high zeros.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Big {
    negative: bool,
    limbs: Vec<u32>,
}

fn trim(limbs: &mut Vec<u32>) {
    while limbs.last() == Some(&0) {
        limbs.pop();
    }
}

fn compare_magnitude(a: &[u32], b: &[u32]) -> Ordering {
    a.len().cmp(&b.len()).then_with(|| a.iter().rev().cmp(b.iter().rev()))
}

fn add_magnitude(a: &[u32], b: &[u32]) -> Vec<u32> {
    let mut out = Vec::with_capacity(a.len().max(b.len()) + 1);
    let mut carry = 0u64;
    for i in 0..a.len().max(b.len()) {
        let sum = u64::from(*a.get(i).unwrap_or(&0)) + u64::from(*b.get(i).unwrap_or(&0)) + carry;
        out.push(sum as u32);
        carry = sum >> 32;
    }
    if carry > 0 {
        out.push(carry as u32);
    }
    out
}

/// `a - b` for `a >= b`.
fn sub_magnitude(a: &[u32], b: &[u32]) -> Vec<u32> {
    let mut out = Vec::with_capacity(a.len());
    let mut borrow = 0i64;
    for i in 0..a.len() {
        let mut difference = i64::from(a[i]) - i64::from(*b.get(i).unwrap_or(&0)) - borrow;
        borrow = 0;
        if difference < 0 {
            difference += 1 << 32;
            borrow = 1;
        }
        out.push(difference as u32);
    }
    trim(&mut out);
    out
}

fn mul_magnitude(a: &[u32], b: &[u32]) -> Vec<u32> {
    let mut out = vec![0u32; a.len() + b.len()];
    for (i, &x) in a.iter().enumerate() {
        let mut carry = 0u64;
        for (j, &y) in b.iter().enumerate() {
            let product = u64::from(x) * u64::from(y) + u64::from(out[i + j]) + carry;
            out[i + j] = product as u32;
            carry = product >> 32;
        }
        out[i + b.len()] = carry as u32;
    }
    trim(&mut out);
    out
}

impl Big {
    pub fn from_i128(value: i128) -> Big {
        let mut magnitude = value.unsigned_abs();
        let mut limbs = Vec::new();
        while magnitude > 0 {
            limbs.push(magnitude as u32);
            magnitude >>= 32;
        }
        Big { negative: value < 0, limbs }
    }

    /// The value of decimal digits (each 0 to 9), most significant first.
    pub fn from_digits(digits: impl IntoIterator<Item = u32>) -> Big {
        let mut limbs: Vec<u32> = Vec::new();
        for digit in digits {
            let mut carry = u64::from(digit);
            for limb in limbs.iter_mut() {
                let value = u64::from(*limb) * 10 + carry;
                *limb = value as u32;
                carry = value >> 32;
            }
            if carry > 0 {
                limbs.push(carry as u32);
            }
        }
        trim(&mut limbs);
        Big { negative: false, limbs }
    }

    /// `10 ** exponent`.
    pub fn pow10(exponent: u32) -> Big {
        let mut out = Big::from_i128(1);
        let mut base = Big::from_i128(10);
        let mut exponent = exponent;
        while exponent > 0 {
            if exponent & 1 == 1 {
                out = out.mul(&base);
            }
            base = base.mul(&base);
            exponent >>= 1;
        }
        out
    }

    pub fn is_zero(&self) -> bool {
        self.limbs.is_empty()
    }

    pub fn is_negative(&self) -> bool {
        self.negative && !self.is_zero()
    }

    pub fn neg(&self) -> Big {
        Big { negative: !self.negative && !self.is_zero(), limbs: self.limbs.clone() }
    }

    pub fn abs(&self) -> Big {
        Big { negative: false, limbs: self.limbs.clone() }
    }

    pub fn add(&self, other: &Big) -> Big {
        if self.negative == other.negative {
            return Big { negative: self.negative, limbs: add_magnitude(&self.limbs, &other.limbs) };
        }
        match compare_magnitude(&self.limbs, &other.limbs) {
            Ordering::Less => Big { negative: other.negative, limbs: sub_magnitude(&other.limbs, &self.limbs) },
            _ => {
                let limbs = sub_magnitude(&self.limbs, &other.limbs);
                Big { negative: self.negative && !limbs.is_empty(), limbs }
            }
        }
    }

    pub fn sub(&self, other: &Big) -> Big {
        self.add(&other.neg())
    }

    pub fn mul(&self, other: &Big) -> Big {
        let limbs = mul_magnitude(&self.limbs, &other.limbs);
        Big { negative: self.negative != other.negative && !limbs.is_empty(), limbs }
    }
}

impl PartialOrd for Big {
    fn partial_cmp(&self, other: &Big) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Big {
    fn cmp(&self, other: &Big) -> Ordering {
        match (self.is_negative(), other.is_negative()) {
            (false, true) => Ordering::Greater,
            (true, false) => Ordering::Less,
            (false, false) => compare_magnitude(&self.limbs, &other.limbs),
            (true, true) => compare_magnitude(&other.limbs, &self.limbs),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn arithmetic_is_exact() {
        let a = Big::from_digits("123456789012345678901234567890".bytes().map(|b| u32::from(b - b'0')));
        let b = Big::pow10(30);
        assert!(a < b && b.sub(&a) > Big::from_i128(0));
        assert_eq!(a.mul(&Big::from_i128(-3)), Big::from_digits("370370367037037036703703703670".bytes().map(|b| u32::from(b - b'0'))).neg());
        assert_eq!(Big::from_i128(5).sub(&Big::from_i128(7)), Big::from_i128(-2));
        assert_eq!(Big::pow10(40).sub(&Big::pow10(40)), Big::from_i128(0));
    }
}
