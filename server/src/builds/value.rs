//! Parsed JSON as Python holds it, so messages and comparisons come out as `document.py` has them.
//!
//! Strings are sequences of code points ([`Text`]) rather than `String`: JSON's `\ud800`
//! escapes give Python strings with lone surrogates, and Python indexes and measures strings
//! by code point. Integers keep their full value as Python's do.

use std::cmp::Ordering;
use std::collections::HashMap;

use super::unicode;

/// A Python `str`: code points, lone surrogates allowed.
pub type Text = Vec<u32>;

pub fn text(s: &str) -> Text {
    s.chars().map(u32::from).collect()
}

/// A `String` for display; a lone surrogate becomes U+FFFD.
pub fn lossy(t: &[u32]) -> String {
    t.iter().map(|&c| char::from_u32(c).unwrap_or('\u{fffd}')).collect()
}

pub fn push(out: &mut Text, s: &str) {
    out.extend(s.chars().map(u32::from));
}

pub fn in_ranges(table: &[(u32, u32)], c: u32) -> bool {
    table
        .binary_search_by(|&(start, end)| {
            if end < c {
                Ordering::Less
            } else if start > c {
                Ordering::Greater
            } else {
                Ordering::Equal
            }
        })
        .is_ok()
}

/// `str.isspace()` of one character (and what `str.strip()` removes).
pub fn is_space(c: u32) -> bool {
    in_ranges(unicode::SPACE, c)
}

/// `str.strip()`.
pub fn strip(t: &[u32]) -> &[u32] {
    let start = t.iter().position(|&c| !is_space(c)).unwrap_or(t.len());
    let end = t.iter().rposition(|&c| !is_space(c)).map_or(start, |i| i + 1);
    &t[start..end.max(start)]
}

/// `str.lower()`, final sigma included.
pub fn lower(t: &[u32]) -> Text {
    let mut out = Text::with_capacity(t.len());
    for (i, &c) in t.iter().enumerate() {
        match c {
            0x130 => out.extend([0x69, 0x307]),
            0x3a3 => out.push(if final_sigma(t, i) { 0x3c2 } else { 0x3c3 }),
            _ => out.push(match unicode::LOWER.binary_search_by_key(&c, |&(from, _)| from) {
                Ok(index) => unicode::LOWER[index].1,
                Err(_) => c,
            }),
        }
    }
    out
}

/// CPython's `handle_capital_sigma`.
fn final_sigma(t: &[u32], i: usize) -> bool {
    let cased = |c| in_ranges(unicode::CASED, c);
    let ignorable = |c| in_ranges(unicode::CASE_IGNORABLE, c);
    let before = t[..i].iter().rev().find(|&&c| !ignorable(c));
    if !before.is_some_and(|&c| cased(c)) {
        return false;
    }
    match t[i + 1..].iter().find(|&&c| !ignorable(c)) {
        None => true,
        Some(&c) => !cased(c),
    }
}

/// A Python `int`: `Small` whenever it fits an `i128`, else its decimal digits.
#[derive(Clone, Debug)]
pub enum Int {
    Small(i128),
    Big { negative: bool, digits: Vec<u8> },
}

impl Int {
    /// From JSON's integer grammar (`-?(0|[1-9][0-9]*)`).
    pub fn parse(literal: &str) -> Int {
        if let Ok(small) = literal.parse::<i128>() {
            return Int::Small(small);
        }
        let (negative, digits) = match literal.strip_prefix('-') {
            Some(rest) => (true, rest),
            None => (false, literal),
        };
        Int::Big { negative, digits: digits.as_bytes().to_vec() }
    }

    pub fn decimal(&self) -> String {
        match self {
            Int::Small(small) => small.to_string(),
            Int::Big { negative, digits } => {
                format!("{}{}", if *negative { "-" } else { "" }, std::str::from_utf8(digits).unwrap_or(""))
            }
        }
    }

    fn cmp_int(&self, other: &Int) -> Ordering {
        match (self, other) {
            (Int::Small(a), Int::Small(b)) => a.cmp(b),
            (Int::Small(_), Int::Big { negative, .. }) => if *negative { Ordering::Greater } else { Ordering::Less },
            (Int::Big { negative, .. }, Int::Small(_)) => if *negative { Ordering::Less } else { Ordering::Greater },
            (Int::Big { negative: a_neg, digits: a }, Int::Big { negative: b_neg, digits: b }) => {
                match (a_neg, b_neg) {
                    (false, true) => Ordering::Greater,
                    (true, false) => Ordering::Less,
                    _ => {
                        let magnitude = a.len().cmp(&b.len()).then_with(|| a.cmp(b));
                        if *a_neg { magnitude.reverse() } else { magnitude }
                    }
                }
            }
        }
    }

    /// Python's exact `int` against `float` comparison (None when the float is NaN).
    fn cmp_float(&self, f: f64) -> Option<Ordering> {
        const TWO_127: f64 = 170141183460469231731687303715884105728.0;
        if f.is_nan() {
            return None;
        }
        if f == f64::INFINITY {
            return Some(Ordering::Less);
        }
        if f == f64::NEG_INFINITY {
            return Some(Ordering::Greater);
        }
        match self {
            Int::Small(i) => {
                if f.abs() < TWO_127 {
                    let whole = f.trunc();
                    Some(i.cmp(&(whole as i128)).then_with(|| {
                        let fraction = f - whole;
                        if fraction > 0.0 {
                            Ordering::Less
                        } else if fraction < 0.0 {
                            Ordering::Greater
                        } else {
                            Ordering::Equal
                        }
                    }))
                } else if f > 0.0 {
                    Some(Ordering::Less)
                } else if f == -TWO_127 && *i == i128::MIN {
                    Some(Ordering::Equal)
                } else {
                    Some(Ordering::Greater)
                }
            }
            Int::Big { negative, digits } => {
                if f.abs() < TWO_127 {
                    return Some(if *negative { Ordering::Less } else { Ordering::Greater });
                }
                // |f| >= 2**127 is a whole number; `{:.0}` prints it exactly.
                let exact = format!("{:.0}", f.abs());
                let f_negative = f < 0.0;
                if *negative != f_negative {
                    return Some(if *negative { Ordering::Less } else { Ordering::Greater });
                }
                let magnitude = digits.len().cmp(&exact.len()).then_with(|| digits.as_slice().cmp(exact.as_bytes()));
                Some(if *negative { magnitude.reverse() } else { magnitude })
            }
        }
    }
}

/// A Python `dict` from JSON: insertion order, a repeated key keeps its first place and takes the last value.
#[derive(Clone, Debug, Default)]
pub struct Dict {
    entries: Vec<(Text, Value)>,
    index: HashMap<Text, usize>,
}

impl Dict {
    pub fn new() -> Dict {
        Dict::default()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn get(&self, key: &[u32]) -> Option<&Value> {
        self.index.get(key).map(|&i| &self.entries[i].1)
    }

    pub fn get_str(&self, key: &str) -> Option<&Value> {
        self.get(&text(key))
    }

    pub fn get_str_mut(&mut self, key: &str) -> Option<&mut Value> {
        let i = *self.index.get(&text(key))?;
        Some(&mut self.entries[i].1)
    }

    pub fn contains(&self, key: &[u32]) -> bool {
        self.index.contains_key(key)
    }

    pub fn insert(&mut self, key: Text, value: Value) {
        match self.index.get(&key) {
            Some(&i) => self.entries[i].1 = value,
            None => {
                self.index.insert(key.clone(), self.entries.len());
                self.entries.push((key, value));
            }
        }
    }

    pub fn iter(&self) -> impl Iterator<Item = (&Text, &Value)> {
        self.entries.iter().map(|(k, v)| (k, v))
    }

    pub fn keys(&self) -> impl Iterator<Item = &Text> {
        self.entries.iter().map(|(k, _)| k)
    }
}

#[derive(Clone, Debug)]
pub enum Value {
    Null,
    Bool(bool),
    Int(Int),
    Float(f64),
    Str(Text),
    List(Vec<Value>),
    Dict(Dict),
}

impl Value {
    pub fn as_dict(&self) -> Option<&Dict> {
        match self {
            Value::Dict(d) => Some(d),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&Text> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }
}

// Numbers ------------------------------------------------------------------------------------

enum Num<'a> {
    I(&'a Int),
    F(f64),
}

const ZERO: Int = Int::Small(0);
const ONE: Int = Int::Small(1);

/// The value as a number, a `bool` counting as the `int` it is in Python.
fn num(v: &Value) -> Option<Num<'_>> {
    match v {
        Value::Bool(false) => Some(Num::I(&ZERO)),
        Value::Bool(true) => Some(Num::I(&ONE)),
        Value::Int(i) => Some(Num::I(i)),
        Value::Float(f) => Some(Num::F(*f)),
        _ => None,
    }
}

fn num_cmp(a: &Num, b: &Num) -> Option<Ordering> {
    match (a, b) {
        (Num::I(a), Num::I(b)) => Some(a.cmp_int(b)),
        (Num::I(a), Num::F(b)) => a.cmp_float(*b),
        (Num::F(a), Num::I(b)) => b.cmp_float(*a).map(Ordering::reverse),
        (Num::F(a), Num::F(b)) => a.partial_cmp(b),
    }
}

/// `a < b` for two JSON numbers (never `bool`s), as `minimum` and `maximum` compare.
pub fn number_lt(a: &Value, b: &Value) -> bool {
    match (num(a), num(b)) {
        (Some(a), Some(b)) => num_cmp(&a, &b) == Some(Ordering::Less),
        _ => false,
    }
}

/// `isinstance(v, int) and not isinstance(v, bool) and v > n`.
pub fn int_gt(v: &Value, n: i128) -> bool {
    matches!(v, Value::Int(i) if i.cmp_int(&Int::Small(n)) == Ordering::Greater)
}

// Python equality and order -----------------------------------------------------------------

/// `a is b` for parsed JSON: every `NaN` is the one object `json` hands out.
fn identical(a: &Value, b: &Value) -> bool {
    matches!((a, b), (Value::Float(x), Value::Float(y)) if x.is_nan() && y.is_nan())
}

/// `PyObject_RichCompareBool(a, b, Py_EQ)`: `==` with the identity shortcut.
fn py_eq(a: &Value, b: &Value) -> bool {
    if identical(a, b) {
        return true;
    }
    if let (Some(x), Some(y)) = (num(a), num(b)) {
        return num_cmp(&x, &y) == Some(Ordering::Equal);
    }
    match (a, b) {
        (Value::Null, Value::Null) => true,
        (Value::Str(x), Value::Str(y)) => x == y,
        (Value::List(x), Value::List(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| py_eq(p, q)),
        (Value::Dict(x), Value::Dict(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| py_eq(v, w)))
        }
        _ => false,
    }
}

/// `a < b`; `Err` where Python raises `TypeError`.
fn py_lt(a: &Value, b: &Value) -> Result<bool, ()> {
    if let (Some(x), Some(y)) = (num(a), num(b)) {
        return Ok(num_cmp(&x, &y) == Some(Ordering::Less));
    }
    match (a, b) {
        (Value::Str(x), Value::Str(y)) => Ok(x < y),
        (Value::List(x), Value::List(y)) => match x.iter().zip(y).position(|(p, q)| !py_eq(p, q)) {
            None => Ok(x.len() < y.len()),
            Some(i) => py_lt(&x[i], &y[i]),
        },
        _ => Err(()),
    }
}

/// Nested containers `jsonschema._utils.equal` compares before Python's recursion limit (1000
/// frames, three per level) raises `RecursionError`; measured from a shallow caller.
pub const EQUAL_DEPTH: usize = 330;

/// Python's `RecursionError`.
#[derive(Debug)]
pub struct TooDeep;

/// `jsonschema._utils.equal`: `==` that tells `True` from `1`, recursively; `level` counts the
/// nested calls (1 at the top).
pub fn js_equal(a: &Value, b: &Value, level: usize) -> Result<bool, TooDeep> {
    if identical(a, b) {
        return Ok(true);
    }
    Ok(match (a, b) {
        (Value::Str(x), Value::Str(y)) => x == y,
        (Value::Str(_), _) | (_, Value::Str(_)) => false,
        (Value::List(x), Value::List(y)) => {
            if level > EQUAL_DEPTH {
                return Err(TooDeep);
            }
            if x.len() != y.len() {
                return Ok(false);
            }
            for (p, q) in x.iter().zip(y) {
                if !js_equal(p, q, level + 1)? {
                    return Ok(false);
                }
            }
            true
        }
        (Value::Dict(x), Value::Dict(y)) => {
            if level > EQUAL_DEPTH {
                return Err(TooDeep);
            }
            if x.len() != y.len() {
                return Ok(false);
            }
            for (k, v) in x.iter() {
                match y.get(k) {
                    Some(w) if js_equal(v, w, level + 1)? => {}
                    _ => return Ok(false),
                }
            }
            true
        }
        (Value::Bool(x), Value::Bool(y)) => x == y,
        (Value::Bool(_), _) | (_, Value::Bool(_)) => false,
        (Value::Null, Value::Null) => true,
        _ => match (num(a), num(b)) {
            (Some(x), Some(y)) => num_cmp(&x, &y) == Some(Ordering::Equal),
            _ => false,
        },
    })
}

/// An item after `jsonschema._utils.unbool`: a top-level `bool` becomes an orderless sentinel.
#[derive(Clone, Copy)]
enum Item<'a> {
    Sentinel(bool),
    Value(&'a Value),
}

fn unbool(v: &Value) -> Item<'_> {
    match v {
        Value::Bool(b) => Item::Sentinel(*b),
        other => Item::Value(other),
    }
}

fn item_lt(a: Item, b: Item) -> Result<bool, ()> {
    match (a, b) {
        (Item::Value(a), Item::Value(b)) => py_lt(a, b),
        _ => Err(()),
    }
}

fn item_equal(a: Item, b: Item) -> Result<bool, TooDeep> {
    match (a, b) {
        (Item::Sentinel(x), Item::Sentinel(y)) => Ok(x == y),
        (Item::Value(a), Item::Value(b)) => js_equal(a, b, 1),
        _ => Ok(false),
    }
}

/// CPython's `list.sort` on fewer than 64 items: one counted run, then a binary insertion sort.
fn small_sort(items: &mut [Item]) -> Result<(), ()> {
    let n = items.len();
    if n < 2 {
        return Ok(());
    }
    // count_run
    let mut run = 2;
    if item_lt(items[1], items[0])? {
        while run < n && item_lt(items[run], items[run - 1])? {
            run += 1;
        }
        items[..run].reverse();
    } else {
        while run < n && !item_lt(items[run], items[run - 1])? {
            run += 1;
        }
    }
    // binarysort
    for start in run..n {
        let pivot = items[start];
        let (mut l, mut r) = (0, start);
        loop {
            let p = l + ((r - l) >> 1);
            if item_lt(pivot, items[p])? {
                r = p;
            } else {
                l = p + 1;
            }
            if l >= r {
                break;
            }
        }
        items.copy_within(l..start, l + 1);
        items[l] = pivot;
    }
    Ok(())
}

fn has_nan(v: &Value) -> bool {
    match v {
        Value::Float(f) => f.is_nan(),
        Value::List(items) => items.iter().any(has_nan),
        Value::Dict(d) => d.iter().any(|(_, v)| has_nan(v)),
        _ => false,
    }
}

/// `jsonschema._utils.uniq`: sort and compare neighbours, or compare every pair when sorting raises.
pub fn uniq(container: &[Value]) -> Result<bool, TooDeep> {
    let mut items: Vec<Item> = container.iter().map(unbool).collect();
    let sorted = if items.len() < 64 {
        small_sort(&mut items).is_ok()
    } else {
        // Timsort proper is not reproduced: past 63 items a sort either raises (an orderless or
        // mixed item is always compared) or orders a consistent set, where neighbours tell the
        // same as every pair. Only NaN or lists can order inconsistently; those take every pair.
        let numbers = container.iter().all(|v| matches!(v, Value::Int(_) | Value::Float(_)));
        let strings = container.iter().all(|v| matches!(v, Value::Str(_)));
        let consistent = (numbers && !container.iter().any(has_nan)) || strings;
        if consistent {
            items.sort_by(|&a, &b| match (item_lt(a, b), item_lt(b, a)) {
                (Ok(true), _) => Ordering::Less,
                (_, Ok(true)) => Ordering::Greater,
                _ => Ordering::Equal,
            });
        }
        consistent
    };
    if sorted {
        for pair in items.windows(2) {
            if item_equal(pair[0], pair[1])? {
                return Ok(false);
            }
        }
        return Ok(true);
    }
    let mut seen: Vec<Item> = Vec::new();
    for item in container.iter().map(unbool) {
        for &other in &seen {
            if item_equal(other, item)? {
                return Ok(false);
            }
        }
        seen.push(item);
    }
    Ok(true)
}

// repr -------------------------------------------------------------------------------------

/// `repr(float)`.
pub fn float_repr(f: f64) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return if f > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let sci = format!("{f:e}");
    let (mantissa, exponent) = sci.split_once('e').unwrap_or((&sci, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", mantissa),
    };
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if (-4..16).contains(&exponent) {
        if exponent >= 0 {
            let point = exponent as usize + 1;
            let (whole, fraction) = if digits.len() > point {
                (digits[..point].to_owned(), digits[point..].to_owned())
            } else {
                (format!("{digits}{}", "0".repeat(point - digits.len())), "0".to_owned())
            };
            format!("{sign}{whole}.{fraction}")
        } else {
            format!("{sign}0.{}{digits}", "0".repeat((-exponent - 1) as usize))
        }
    } else {
        let (first, rest) = digits.split_at(1);
        let rest = if rest.is_empty() { String::new() } else { format!(".{rest}") };
        format!("{sign}{first}{rest}e{}{:02}", if exponent < 0 { "-" } else { "+" }, exponent.abs())
    }
}

/// `repr(str)`.
pub fn repr_str(s: &[u32], out: &mut Text) {
    let single = s.contains(&u32::from('\''));
    let double = s.contains(&u32::from('"'));
    let quote = if single && !double { u32::from('"') } else { u32::from('\'') };
    out.push(quote);
    for &c in s {
        if c == quote || c == u32::from('\\') {
            out.extend([u32::from('\\'), c]);
        } else if c == 9 {
            push(out, "\\t");
        } else if c == 10 {
            push(out, "\\n");
        } else if c == 13 {
            push(out, "\\r");
        } else if c < 0x20 || c == 0x7f {
            push(out, &format!("\\x{c:02x}"));
        } else if c < 0x7f || in_ranges(unicode::PRINTABLE, c) {
            out.push(c);
        } else if c <= 0xff {
            push(out, &format!("\\x{c:02x}"));
        } else if c <= 0xffff {
            push(out, &format!("\\u{c:04x}"));
        } else {
            push(out, &format!("\\U{c:08x}"));
        }
    }
    out.push(quote);
}

/// `repr(value)`.
pub fn repr(v: &Value, out: &mut Text) {
    match v {
        Value::Null => push(out, "None"),
        Value::Bool(true) => push(out, "True"),
        Value::Bool(false) => push(out, "False"),
        Value::Int(i) => push(out, &i.decimal()),
        Value::Float(f) => push(out, &float_repr(*f)),
        Value::Str(s) => repr_str(s, out),
        Value::List(items) => {
            out.push(u32::from('['));
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    push(out, ", ");
                }
                repr(item, out);
            }
            out.push(u32::from(']'));
        }
        Value::Dict(d) => {
            out.push(u32::from('{'));
            for (i, (k, item)) in d.iter().enumerate() {
                if i > 0 {
                    push(out, ", ");
                }
                repr_str(k, out);
                push(out, ": ");
                repr(item, out);
            }
            out.push(u32::from('}'));
        }
    }
}

pub fn repr_text(v: &Value) -> Text {
    let mut out = Text::new();
    repr(v, &mut out);
    out
}

// JSON out ---------------------------------------------------------------------------------

/// A JSON string literal, ASCII only (`json.dumps` with `ensure_ascii`).
pub fn dump_str(s: &[u32], out: &mut String) {
    out.push('"');
    for &c in s {
        match c {
            0x22 => out.push_str("\\\""),
            0x5c => out.push_str("\\\\"),
            0x0a => out.push_str("\\n"),
            0x0d => out.push_str("\\r"),
            0x09 => out.push_str("\\t"),
            0x08 => out.push_str("\\b"),
            0x0c => out.push_str("\\f"),
            0x20..=0x7e => out.push(char::from(c as u8)),
            0x10000.. => {
                let v = c - 0x10000;
                out.push_str(&format!("\\u{:04x}\\u{:04x}", 0xd800 + (v >> 10), 0xdc00 + (v & 0x3ff)));
            }
            _ => out.push_str(&format!("\\u{c:04x}")),
        }
    }
    out.push('"');
}

/// `json.dumps(value)` (compact separators).
pub fn dump(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Int(i) => out.push_str(&i.decimal()),
        Value::Float(f) if f.is_nan() => out.push_str("NaN"),
        Value::Float(f) if f.is_infinite() => out.push_str(if *f > 0.0 { "Infinity" } else { "-Infinity" }),
        Value::Float(f) => out.push_str(&float_repr(*f)),
        Value::Str(s) => dump_str(s, out),
        Value::List(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                dump(item, out);
            }
            out.push(']');
        }
        Value::Dict(d) => {
            out.push('{');
            for (i, (k, item)) in d.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                dump_str(k, out);
                out.push(':');
                dump(item, out);
            }
            out.push('}');
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_print_as_python_repr() {
        for (f, expected) in [
            (1.0, "1.0"), (0.5, "0.5"), (1e16, "1e+16"), (1e15, "1000000000000000.0"), (0.0001, "0.0001"),
            (0.00001, "1e-05"), (-0.0, "-0.0"), (123.456, "123.456"), (1.5e300, "1.5e+300"), (5e-324, "5e-324"),
            (0.1 + 0.2, "0.30000000000000004"), (f64::INFINITY, "inf"), (2048.0, "2048.0"),
        ] {
            assert_eq!(float_repr(f), expected);
        }
    }

    #[test]
    fn strings_print_as_python_repr() {
        for (s, expected) in [
            ("a", "'a'"), ("it's", "\"it's\""), ("'\"", "'\\'\"'"), ("tab\there", "'tab\\there'"),
            ("\u{7f}\u{85}\u{a0}é\u{200b}", "'\\x7f\\x85\\xa0é\\u200b'"), ("\u{e0001}", "'\\U000e0001'"),
            ("back\\slash", "'back\\\\slash'"),
        ] {
            assert_eq!(lossy(&repr_text(&Value::Str(text(s)))), expected);
        }
        let mut out = Text::new();
        repr_str(&[0xd800], &mut out);
        assert_eq!(lossy(&out), "'\\ud800'");
    }

    #[test]
    fn python_comparisons_and_uniqueness() {
        let int = |n| Value::Int(Int::Small(n));
        assert!(number_lt(&int(1), &Value::Float(1.5)));
        assert!(!number_lt(&Value::Float(f64::NAN), &int(1)));
        let eq = |a: &Value, b: &Value| js_equal(a, b, 1).unwrap();
        assert!(eq(&int(1), &Value::Float(1.0)) && !eq(&int(1), &Value::Bool(true)));
        let big = Value::Int(Int::parse("1000000000000000000000000000000000000000000"));
        assert!(number_lt(&Value::Float(1e40), &big) && number_lt(&big, &Value::Float(1e43)));
        let unique = |items: &[Value]| uniq(items).unwrap();
        assert!(!unique(&[int(1), Value::Float(1.0)]));
        assert!(unique(&[int(1), Value::Bool(true)]));
        assert!(!unique(&[Value::Bool(true), Value::Bool(true)]));
        let nan = Value::Float(f64::NAN);
        assert!(!unique(&[nan.clone(), nan.clone()]));
        // Sorting with NaN leaves the two 1s apart, so Python calls these unique.
        assert!(unique(&[int(1), nan, int(1)]));
        assert_eq!(lower(&text("ΑΣ")), text("ας"));
        assert_eq!(lower(&text("Σ")), text("σ"));
        assert_eq!(lower(&text("\u{212a}")), text("k"));
    }
}
