//! Python's `str` operations the search documents and queries use, with this interpreter's
//! Unicode facts (crate::search::tables, crate::builds::unicode): word tokens, `casefold`,
//! `strip`, `split`, slicing by code point, and `html.unescape`.

use super::tables::{ALNUM, CASEFOLD, DECIMAL, HTML5, SPACE};
use crate::builds::value::in_ranges;

pub fn is_alnum(c: char) -> bool {
    in_ranges(ALNUM, c as u32)
}

pub fn is_space(c: char) -> bool {
    in_ranges(SPACE, c as u32)
}

/// `str.isdecimal()` for one character (`re`'s `\d`).
pub fn is_decimal(c: char) -> bool {
    in_ranges(DECIMAL, c as u32)
}

/// `int(text)` for text of decimal digits only (any script, as `str.isdecimal` takes them), or
/// None when one character is not such a digit. Each range of the table starts at a zero.
pub fn decimal_value(text: &str) -> Option<u128> {
    let mut value: u128 = 0;
    for c in text.chars() {
        let code = c as u32;
        let index = DECIMAL.partition_point(|&(_, end)| end < code);
        let &(start, end) = DECIMAL.get(index)?;
        if code < start || code > end {
            return None;
        }
        value = value.saturating_mul(10).saturating_add(u128::from((code - start) % 10));
    }
    Some(value)
}

/// `str.casefold()`.
pub fn casefold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match CASEFOLD.binary_search_by_key(&(c as u32), |&(code, _)| code) {
            Ok(index) => out.push_str(CASEFOLD[index].1),
            Err(_) => out.push(c),
        }
    }
    out
}

/// `[token.casefold() for token in re.findall(r"[^\W_]+", text)]`: runs of `str.isalnum()` characters.
pub fn words(text: &str) -> Vec<String> {
    text.split(|c: char| !is_alnum(c)).filter(|run| !run.is_empty()).map(casefold).collect()
}

/// `str.strip()`.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `str.split()`: runs of non-whitespace.
pub fn split(text: &str) -> impl Iterator<Item = &str> {
    text.split(is_space).filter(|part| !part.is_empty())
}

/// `text[:count]`, counting code points.
pub fn head(text: &str, count: usize) -> &str {
    match text.char_indices().nth(count) {
        Some((at, _)) => &text[..at],
        None => text,
    }
}

/// `len(text)`.
pub fn length(text: &str) -> usize {
    text.chars().count()
}

/// `text.split("\n", 1)[0]`.
pub fn first_line(text: &str) -> &str {
    text.split('\n').next().unwrap_or("")
}

/// `text.strip().split("\n", 1)[0][:200]`: how a quick note is titled.
pub fn quick_title(text: &str) -> &str {
    head(first_line(strip(text)), 200)
}

/// `kitty.core.search.clean`: text without HTML tags (`<[^>]{0,2000}>` as a space) or entities.
pub fn clean(value: &str) -> String {
    if !value.contains('<') && !value.contains('&') {
        return value.to_owned();
    }
    unescape(&strip_tags(value))
}

fn strip_tags(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut rest = value;
    while let Some(open) = rest.find('<') {
        out.push_str(&rest[..open]);
        let after = &rest[open + 1..];
        // The first `>` closes the tag when at most 2000 characters lie between.
        let mut close = None;
        for (count, (at, c)) in after.char_indices().enumerate() {
            if c == '>' {
                close = Some(at);
                break;
            }
            if count == 2000 {
                break;
            }
        }
        match close {
            Some(at) => {
                out.push(' ');
                rest = &after[at + 1..];
            }
            None => {
                out.push('<');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// `html.unescape`.
pub fn unescape(text: &str) -> String {
    if !text.contains('&') {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(at) = rest.find('&') {
        out.push_str(&rest[..at]);
        let after = &rest[at + 1..];
        match reference(after) {
            Some((consumed, replacement)) => {
                out.push_str(&replacement);
                rest = &after[consumed..];
            }
            None => {
                out.push('&');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// One `_charref` match after an `&`: the bytes it spans and what replaces it.
fn reference(after: &str) -> Option<(usize, String)> {
    let bytes = after.as_bytes();
    if bytes.first() == Some(&b'#') {
        let hex = matches!(bytes.get(1), Some(b'x' | b'X'));
        let start = if hex { 2 } else { 1 };
        let digits = bytes[start..].iter().take_while(|b| if hex { b.is_ascii_hexdigit() } else { b.is_ascii_digit() }).count();
        if digits == 0 {
            // `&#` without digits is no numeric reference; `#` cannot start a name either.
            return None;
        }
        let mut end = start + digits;
        let text = &after[start..end];
        if bytes.get(end) == Some(&b';') {
            end += 1;
        }
        let number = u64::from_str_radix(text.trim_start_matches('0'), if hex { 16 } else { 10 })
            .unwrap_or(if text.trim_start_matches('0').is_empty() { 0 } else { u64::MAX });
        return Some((end, numeric(number)));
    }
    // `[^\t\n\f <&#;]{1,32};?`
    let mut end = 0;
    for (count, (at, c)) in after.char_indices().enumerate() {
        if count == 32 || matches!(c, '\t' | '\n' | '\x0c' | ' ' | '<' | '&' | '#' | ';') {
            break;
        }
        end = at + c.len_utf8();
    }
    if end == 0 {
        return None;
    }
    if after.as_bytes().get(end) == Some(&b';') {
        end += 1;
    }
    let name = &after[..end];
    if let Some(value) = entity(name) {
        return Some((end, value.to_owned()));
    }
    // The longest prefix of two or more characters that is a name.
    let bounds: Vec<usize> = name.char_indices().map(|(at, _)| at).skip(2).collect();
    for &cut in bounds.iter().rev() {
        if let Some(value) = entity(&name[..cut]) {
            return Some((end, format!("{value}{}", &name[cut..])));
        }
    }
    Some((end, format!("&{name}")))
}

fn entity(name: &str) -> Option<&'static str> {
    HTML5.binary_search_by_key(&name, |&(key, _)| key).ok().map(|index| HTML5[index].1)
}

fn numeric(number: u64) -> String {
    const WINDOWS_1252: [u32; 32] = [
        0x20ac, 0x81, 0x201a, 0x192, 0x201e, 0x2026, 0x2020, 0x2021, 0x2c6, 0x2030, 0x160, 0x2039, 0x152, 0x8d, 0x17d, 0x8f,
        0x90, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x2dc, 0x2122, 0x161, 0x203a, 0x153, 0x9d, 0x17e, 0x178,
    ];
    match number {
        0x00 => "\u{fffd}".into(),
        0x0d => "\r".into(),
        0x80..=0x9f => char::from_u32(WINDOWS_1252[(number - 0x80) as usize]).expect("valid").to_string(),
        0xd800..=0xdfff => "\u{fffd}".into(),
        n if n > 0x10ffff => "\u{fffd}".into(),
        0x1..=0x8 | 0xb | 0xe..=0x1f | 0x7f | 0xfdd0..=0xfdef => String::new(),
        n if n & 0xfffe == 0xfffe => String::new(),
        n => char::from_u32(n as u32).expect("a scalar value").to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_string_semantics() {
        assert_eq!(words("Café, STRASSE_straße x²"), ["café", "strasse", "strasse", "x²"]);
        assert_eq!(strip("\u{1c} a \u{3000}"), "a");
        assert_eq!(split(" a\u{85}b  c ").collect::<Vec<_>>(), ["a", "b", "c"]);
        assert_eq!(head("añb", 2), "añ");
        assert_eq!(clean("<p>a &amp; b</p>&notit; &#x41;&#0;&#128;&#xd800;&#65"), " a & b ¬it; A\u{fffd}€\u{fffd}A");
        assert_eq!(clean("a < b"), "a < b");
        assert_eq!(unescape("&amp &ampx &zz; &#; &#x;"), "& &x &zz; &#; &#x;");
    }
}
