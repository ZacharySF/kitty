//! Fractional order keys (`kitty.core.blocks.order`, a port of rocicorp/fractional-indexing):
//! base-62 strings whose byte order is sibling order, so a new key always fits between two
//! neighbours. Keys are ASCII, so byte offsets are character offsets.

const DIGITS: &[u8] = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const ZERO: &str = "a0";
const SMALLEST: &str = "A00000000000000000000000000";

/// Why a key cannot be made or used (Python's `ValueError`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Invalid;

fn index(digit: u8) -> Option<usize> {
    DIGITS.iter().position(|&d| d == digit)
}

fn midpoint(a: &str, b: Option<&str>) -> Result<String, Invalid> {
    let zero = DIGITS[0];
    if let Some(b) = b {
        if a >= b {
            return Err(Invalid);
        }
    }
    if a.as_bytes().last() == Some(&zero) || b.is_some_and(|b| b.as_bytes().last() == Some(&zero)) {
        return Err(Invalid);
    }
    if let Some(b) = b.filter(|b| !b.is_empty()) {
        let (ab, bb) = (a.as_bytes(), b.as_bytes());
        let mut n = 0;
        while n < bb.len() && *ab.get(n).unwrap_or(&zero) == bb[n] {
            n += 1;
        }
        if n >= bb.len() {
            // Python indexes past the end: `b[n]` raises IndexError (a >= b, refused above).
            return Err(Invalid);
        }
        if n > 0 {
            return Ok(format!("{}{}", &b[..n], midpoint(a.get(n..).unwrap_or(""), Some(&b[n..]))?));
        }
    }
    let low = match a.as_bytes().first() {
        Some(&digit) => index(digit).ok_or(Invalid)?,
        None => 0,
    };
    let high = match b {
        Some(b) => match b.as_bytes().first() {
            Some(&digit) => index(digit).ok_or(Invalid)?,
            // `DIGITS.index(b[0])` on an empty `b` is an IndexError.
            None => return Err(Invalid),
        },
        None => DIGITS.len(),
    };
    if high - low > 1 {
        return Ok((DIGITS[(low + high + 1) / 2] as char).to_string());
    }
    if let Some(b) = b.filter(|b| b.len() > 1) {
        return Ok(b[..1].to_owned());
    }
    Ok(format!("{}{}", DIGITS[low] as char, midpoint(a.get(1..).unwrap_or(""), None)?))
}

fn integer_length(head: u8) -> Result<usize, Invalid> {
    match head {
        b'a'..=b'z' => Ok((head - b'a') as usize + 2),
        b'A'..=b'Z' => Ok((b'Z' - head) as usize + 2),
        _ => Err(Invalid),
    }
}

fn integer_part(key: &str) -> Result<&str, Invalid> {
    let length = integer_length(*key.as_bytes().first().ok_or(Invalid)?)?;
    if length > key.len() {
        return Err(Invalid);
    }
    Ok(&key[..length])
}

/// `order.validate`: the key itself when it is well formed.
pub fn validate(key: &str) -> Result<&str, Invalid> {
    if key.is_empty() || key == SMALLEST || key.bytes().any(|c| index(c).is_none()) {
        return Err(Invalid);
    }
    let fraction = &key[integer_part(key)?.len()..];
    if fraction.as_bytes().last() == Some(&DIGITS[0]) {
        return Err(Invalid);
    }
    Ok(key)
}

fn increment(value: &str) -> Option<String> {
    let head = value.as_bytes()[0];
    let mut digits: Vec<u8> = value.as_bytes()[1..].to_vec();
    for position in (0..digits.len()).rev() {
        let digit = index(digits[position]).unwrap_or(0) + 1;
        if digit == DIGITS.len() {
            digits[position] = DIGITS[0];
        } else {
            digits[position] = DIGITS[digit];
            return Some(format!("{}{}", head as char, String::from_utf8(digits).unwrap_or_default()));
        }
    }
    if head == b'Z' {
        return Some(ZERO.to_owned());
    }
    if head == b'z' {
        return None;
    }
    let head = head + 1;
    if head > b'a' {
        digits.push(DIGITS[0]);
    } else {
        digits.pop();
    }
    Some(format!("{}{}", head as char, String::from_utf8(digits).unwrap_or_default()))
}

fn decrement(value: &str) -> Option<String> {
    let head = value.as_bytes()[0];
    let mut digits: Vec<u8> = value.as_bytes()[1..].to_vec();
    for position in (0..digits.len()).rev() {
        let digit = index(digits[position]).unwrap_or(0);
        if digit == 0 {
            digits[position] = DIGITS[DIGITS.len() - 1];
        } else {
            digits[position] = DIGITS[digit - 1];
            return Some(format!("{}{}", head as char, String::from_utf8(digits).unwrap_or_default()));
        }
    }
    if head == b'a' {
        return Some(format!("Z{}", DIGITS[DIGITS.len() - 1] as char));
    }
    if head == b'A' {
        return None;
    }
    let head = head - 1;
    if head < b'Z' {
        digits.push(DIGITS[DIGITS.len() - 1]);
    } else {
        digits.pop();
    }
    Some(format!("{}{}", head as char, String::from_utf8(digits).unwrap_or_default()))
}

/// `order.key_between`: a key sorting strictly after `a` and before `b` (None is open-ended).
pub fn key_between(a: Option<&str>, b: Option<&str>) -> Result<String, Invalid> {
    if let Some(a) = a {
        validate(a)?;
    }
    if let Some(b) = b {
        validate(b)?;
    }
    if let (Some(a), Some(b)) = (a, b) {
        if a >= b {
            return Err(Invalid);
        }
    }
    let Some(a) = a else {
        let Some(b) = b else { return Ok(ZERO.to_owned()) };
        let integer = integer_part(b)?;
        let fraction = &b[integer.len()..];
        if integer == SMALLEST {
            return Ok(format!("{integer}{}", midpoint("", Some(fraction))?));
        }
        if integer < b {
            return Ok(integer.to_owned());
        }
        return decrement(integer).ok_or(Invalid);
    };
    let Some(b) = b else {
        let integer = integer_part(a)?;
        return match increment(integer) {
            None => Ok(format!("{integer}{}", midpoint(&a[integer.len()..], None)?)),
            Some(result) => Ok(result),
        };
    };
    let (integer_a, integer_b) = (integer_part(a)?, integer_part(b)?);
    if integer_a == integer_b {
        return Ok(format!("{integer_a}{}", midpoint(&a[integer_a.len()..], Some(&b[integer_b.len()..]))?));
    }
    let result = increment(integer_a).ok_or(Invalid)?;
    if result.as_str() < b {
        Ok(result)
    } else {
        Ok(format!("{integer_a}{}", midpoint(&a[integer_a.len()..], None)?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_fit_between_their_neighbours() {
        assert_eq!(key_between(None, None).unwrap(), "a0");
        assert_eq!(key_between(Some("a0"), None).unwrap(), "a1");
        assert_eq!(key_between(None, Some("a0")).unwrap(), "Zz");
        assert_eq!(key_between(Some("a0"), Some("a1")).unwrap(), "a0V");
        assert_eq!(key_between(Some("a0V"), Some("a1")).unwrap(), "a0l");
        assert_eq!(key_between(Some("az"), None).unwrap(), "b00");
        assert!(key_between(Some("a1"), Some("a0")).is_err());
        assert!(validate("a00").is_err() && validate("A00000000000000000000000000").is_err() && validate("a").is_err());
    }
}
