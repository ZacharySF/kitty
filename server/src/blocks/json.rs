//! `json.dumps(value, ensure_ascii=False, ...)` as the block engine writes its rows: the op log's
//! operations, block properties, actors and the canonical form properties are compared in.

use serde_json::{Map, Value};

use crate::ported::py::float_repr;

/// How one `json.dumps` call is made.
#[derive(Clone, Copy)]
pub struct Style {
    /// `separators=(",", ":")`, else Python's default `(", ", ": ")`.
    pub compact: bool,
    pub sort_keys: bool,
}

/// `separators=(",", ":")`: the op log, the properties column, actors.
pub const COMPACT: Style = Style { compact: true, sort_keys: false };
/// `crdt.canonical`: sorted keys, compact.
pub const CANONICAL: Style = Style { compact: true, sort_keys: true };
/// The default separators (`PageDoc.check`'s size of the properties).
pub const PLAIN: Style = Style { compact: false, sort_keys: false };

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
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write(out: &mut String, value: &Value, style: Style) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => {
            if number.is_f64() {
                out.push_str(&float_repr(number.as_f64().unwrap_or(f64::NAN)));
            } else {
                out.push_str(&number.to_string());
            }
        }
        Value::String(text) => string(out, text),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(if style.compact { "," } else { ", " });
                }
                write(out, item, style);
            }
            out.push(']');
        }
        Value::Object(map) => object(out, map, style),
    }
}

fn object(out: &mut String, map: &Map<String, Value>, style: Style) {
    out.push('{');
    let mut entries: Vec<(&String, &Value)> = map.iter().collect();
    if style.sort_keys {
        // Code point order, which is UTF-8 byte order.
        entries.sort_by(|a, b| a.0.cmp(b.0));
    }
    for (index, (key, item)) in entries.into_iter().enumerate() {
        if index > 0 {
            out.push_str(if style.compact { "," } else { ", " });
        }
        string(out, key);
        out.push_str(if style.compact { ":" } else { ": " });
        write(out, item, style);
    }
    out.push('}');
}

pub fn dumps(value: &Value, style: Style) -> String {
    let mut out = String::new();
    write(&mut out, value, style);
    out
}

pub fn dumps_map(map: &Map<String, Value>, style: Style) -> String {
    let mut out = String::new();
    object(&mut out, map, style);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn text_is_written_as_python_writes_it() {
        let value = json!({"b": [1, 2.5, 1e16, true, null], "a": "é\u{1}\"\n\u{7f}😀"});
        assert_eq!(dumps(&value, COMPACT), "{\"b\":[1,2.5,1e+16,true,null],\"a\":\"é\\u0001\\\"\\n\u{7f}😀\"}");
        assert_eq!(dumps(&value, CANONICAL), "{\"a\":\"é\\u0001\\\"\\n\u{7f}😀\",\"b\":[1,2.5,1e+16,true,null]}");
        assert_eq!(dumps(&value, PLAIN), "{\"b\": [1, 2.5, 1e+16, true, null], \"a\": \"é\\u0001\\\"\\n\u{7f}😀\"}");
    }
}
