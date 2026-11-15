//! Python value semantics the ported handlers keep: `json.dumps` text (stored rows and
//! digests stay byte-identical to what the Python code wrote), `float.__repr__`, `==` across
//! JSON values, truthiness, `str.strip`, `str.casefold` and `len`.

use serde_json::{Map, Value};

/// `float.__repr__`: the shortest round-trip digits, scientific below 1e-4 and from 1e16.
pub fn float_repr(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity".into() } else { "-Infinity".into() };
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    let exp_form = format!("{value:e}");
    let (mantissa, exponent) = exp_form.split_once('e').expect("{:e} has an exponent");
    let exponent: i32 = exponent.parse().expect("an integer exponent");
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let sign = if negative { "-" } else { "" };
    if (-4..16).contains(&exponent) {
        let point = exponent + 1;
        let text = if point <= 0 {
            format!("0.{}{}", "0".repeat((-point) as usize), digits)
        } else if point as usize >= digits.len() {
            format!("{}{}.0", digits, "0".repeat(point as usize - digits.len()))
        } else {
            format!("{}.{}", &digits[..point as usize], &digits[point as usize..])
        };
        format!("{sign}{text}")
    } else {
        let rest = &digits[1..];
        let body = if rest.is_empty() { digits[..1].to_string() } else { format!("{}.{}", &digits[..1], rest) };
        format!("{sign}{body}e{}{:02}", if exponent < 0 { '-' } else { '+' }, exponent.abs())
    }
}

fn number(out: &mut String, number: &serde_json::Number) {
    if number.is_f64() {
        out.push_str(&float_repr(number.as_f64().unwrap_or(f64::NAN)));
    } else {
        out.push_str(&number.to_string());
    }
}

fn string(out: &mut String, text: &str) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write(out: &mut String, value: &Value, sort_keys: bool) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(value) => number(out, value),
        Value::String(text) => string(out, text),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write(out, item, sort_keys);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            if sort_keys {
                entries.sort_by(|a, b| a.0.cmp(b.0));
            }
            for (index, (key, item)) in entries.into_iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                string(out, key);
                out.push_str(": ");
                write(out, item, sort_keys);
            }
            out.push('}');
        }
    }
}

/// `json.dumps(value)`.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write(&mut out, value, false);
    out
}

/// `json.dumps(value, sort_keys=True)`.
pub fn dumps_sorted(value: &Value) -> String {
    let mut out = String::new();
    write(&mut out, value, true);
    out
}

/// `json.dumps(value, sort_keys=True, separators=(",", ":"))`.
pub fn dumps_compact_sorted(value: &Value) -> String {
    fn go(out: &mut String, value: &Value) {
        match value {
            Value::Array(items) => {
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    go(out, item);
                }
                out.push(']');
            }
            Value::Object(map) => {
                out.push('{');
                let mut entries: Vec<(&String, &Value)> = map.iter().collect();
                entries.sort_by(|a, b| a.0.cmp(b.0));
                for (index, (key, item)) in entries.into_iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    string(out, key);
                    out.push(':');
                    go(out, item);
                }
                out.push('}');
            }
            scalar => write(out, scalar, true),
        }
    }
    let mut out = String::new();
    go(&mut out, value);
    out
}

/// A JSON number as Python holds it: an `int` or a `float`.
fn numeric(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(flag) => Some(f64::from(u8::from(*flag))),
        Value::Number(number) => number.as_f64(),
        _ => None,
    }
}

/// Python's `==` between two decoded JSON values (`True == 1 == 1.0`).
pub fn eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Null, Value::Null) => true,
        (Value::String(x), Value::String(y)) => x == y,
        (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| eq(p, q)),
        (Value::Object(x), Value::Object(y)) => x.len() == y.len() && x.iter().all(|(key, value)| y.get(key).is_some_and(|other| eq(value, other))),
        (Value::Number(x), Value::Number(y)) => match (x.as_i64(), y.as_i64()) {
            (Some(p), Some(q)) => p == q,
            _ => match (x.as_u64(), y.as_u64()) {
                (Some(p), Some(q)) => p == q,
                _ => x.as_f64() == y.as_f64(),
            },
        },
        _ => match (numeric(a), numeric(b)) {
            (Some(x), Some(y)) => x == y,
            _ => false,
        },
    }
}

/// Python's `==` of a JSON value with an integer column value.
pub fn eq_int(a: &Value, b: i64) -> bool {
    eq(a, &Value::from(b))
}

/// `bool(value)`.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `type(value) is int`: a JSON integer, never a boolean.
pub fn int(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) => number.as_i64(),
        _ => None,
    }
}

/// `str.isspace` for one character: Unicode white space plus the four separators U+001C-U+001F.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `len(text)`: code points.
pub fn len(text: &str) -> usize {
    text.chars().count()
}

/// `str.casefold()`: lower case plus the full folds that differ from it in common text.
pub fn casefold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            'ß' | 'ẞ' => out.push_str("ss"),
            'ς' => out.push('σ'),
            'ﬀ' => out.push_str("ff"),
            'ﬁ' => out.push_str("fi"),
            'ﬂ' => out.push_str("fl"),
            'ﬃ' => out.push_str("ffi"),
            'ﬄ' => out.push_str("ffl"),
            'ﬅ' | 'ﬆ' => out.push_str("st"),
            'İ' => out.push_str("i\u{307}"),
            'ŉ' => out.push_str("\u{2bc}n"),
            'ϐ' => out.push('β'),
            'ϑ' => out.push('θ'),
            'ϕ' => out.push('φ'),
            'ϖ' => out.push('π'),
            'ϰ' => out.push('κ'),
            'ϱ' => out.push('ρ'),
            'ϵ' => out.push('ε'),
            'ſ' => out.push('s'),
            'ẛ' => out.push('ṡ'),
            'ι' => out.push('ι'),
            'µ' => out.push('μ'),
            c => out.extend(c.to_lowercase()),
        }
    }
    out
}

/// `dict(...)` merged left to right, as `{**a, **b}`.
pub fn merged(parts: &[&Map<String, Value>]) -> Map<String, Value> {
    let mut out = Map::new();
    for part in parts {
        for (key, value) in part.iter() {
            out.insert(key.clone(), value.clone());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn floats_print_as_python_repr() {
        for (value, text) in [(1.0, "1.0"), (0.1, "0.1"), (1e16, "1e+16"), (1.5e-5, "1.5e-05"), (0.0001, "0.0001"),
                              (123456789012345680.0, "1.2345678901234568e+17"), (-2.5, "-2.5"), (1e15, "1000000000000000.0")] {
            assert_eq!(float_repr(value), text);
        }
    }

    #[test]
    fn dumps_matches_python() {
        let value = json!({"b": [1, 2.0, "é\n"], "a": null, "c": {"x": true}});
        assert_eq!(dumps(&value), r#"{"b": [1, 2.0, "\u00e9\n"], "a": null, "c": {"x": true}}"#);
        assert_eq!(dumps_sorted(&value), r#"{"a": null, "b": [1, 2.0, "\u00e9\n"], "c": {"x": true}}"#);
        assert_eq!(dumps(&json!("😀")), r#""\ud83d\ude00""#);
    }

    #[test]
    fn equality_is_pythons() {
        assert!(eq(&json!(true), &json!(1)));
        assert!(eq(&json!(1.0), &json!(1)));
        assert!(!eq(&json!("1"), &json!(1)));
        assert!(eq(&json!({"a": [1]}), &json!({"a": [1.0]})));
    }
}
