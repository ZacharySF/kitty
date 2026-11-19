//! `json.dumps` as the Python cores wrote stored JSON: the same separators, escaping and
//! float spelling (`repr`), so a value written here reads back byte-identical in Python and
//! the other way round.

use serde_json::Value;

/// `repr(float)`: the shortest round-trip digits, fixed notation for exponents -4..16.
pub fn float(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity".into() } else { "-Infinity".into() };
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    let scientific = format!("{value:e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("LowerExp has an exponent");
    let exponent: i32 = exponent.parse().expect("an integer exponent");
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    let point = exponent + 1;
    let mut text = String::new();
    if negative {
        text.push('-');
    }
    if -4 < point && point <= 16 {
        if point <= 0 {
            text.push_str("0.");
            text.push_str(&"0".repeat((-point) as usize));
            text.push_str(&digits);
        } else if point as usize >= digits.len() {
            text.push_str(&digits);
            text.push_str(&"0".repeat(point as usize - digits.len()));
            text.push_str(".0");
        } else {
            text.push_str(&digits[..point as usize]);
            text.push('.');
            text.push_str(&digits[point as usize..]);
        }
    } else {
        text.push_str(&digits[..1]);
        if digits.len() > 1 {
            text.push('.');
            text.push_str(&digits[1..]);
        }
        text.push_str(&format!("e{}{:02}", if exponent < 0 { '-' } else { '+' }, exponent.abs()));
    }
    text
}

/// `round(value, places)`: the correctly rounded decimal, as a float.
pub fn round(value: f64, places: usize) -> f64 {
    format!("{value:.places$}").parse().unwrap_or(value)
}

fn string(out: &mut String, text: &str, ascii: bool) {
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
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if ascii && !c.is_ascii() => {
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

fn write(out: &mut String, value: &Value, compact: bool, ascii: bool) {
    let (item, key) = if compact { (",", ":") } else { (", ", ": ") };
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => match (number.as_i64(), number.as_u64()) {
            (Some(integer), _) => out.push_str(&integer.to_string()),
            (_, Some(integer)) => out.push_str(&integer.to_string()),
            _ => out.push_str(&float(number.as_f64().unwrap_or(f64::NAN))),
        },
        Value::String(text) => string(out, text, ascii),
        Value::Array(items) => {
            out.push('[');
            for (index, element) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(item);
                }
                write(out, element, compact, ascii);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (index, (name, element)) in map.iter().enumerate() {
                if index > 0 {
                    out.push_str(item);
                }
                string(out, name, ascii);
                out.push_str(key);
                write(out, element, compact, ascii);
            }
            out.push('}');
        }
    }
}

/// `json.dumps(value, ensure_ascii=False, separators=(",", ":"))`.
pub fn compact(value: &Value) -> String {
    let mut out = String::new();
    write(&mut out, value, true, false);
    out
}

/// `json.dumps(value)`: default separators, ASCII only.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write(&mut out, value, false, true);
    out
}

/// A float as a JSON value (`NaN` and infinities have none; Python would write them anyway).
pub fn number(value: f64) -> Value {
    serde_json::Number::from_f64(value).map(Value::Number).unwrap_or(Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_are_spelled_like_repr() {
        for (value, text) in [(2.3065, "2.3065"), (5.0, "5.0"), (1e-5, "1e-05"), (0.0001, "0.0001"), (1e16, "1e+16"),
                              (1e15, "1000000000000000.0"), (-0.5, "-0.5"), (123456.789, "123456.789"), (0.1 + 0.2, "0.30000000000000004")] {
            assert_eq!(float(value), text);
        }
        assert_eq!(round(0.66666, 4), 0.6667);
        assert_eq!(compact(&serde_json::json!({"a": [1, 0.5, "é\n"]})), "{\"a\":[1,0.5,\"é\\n\"]}");
        assert_eq!(dumps(&serde_json::json!({"a": [1, "é"]})), "{\"a\": [1, \"\\u00e9\"]}");
    }
}
