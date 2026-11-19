//! `kitty.core.effects.canonical` and `digest`: the one JSON text of a value (sorted keys, no
//! whitespace, UTF-8 as is) and the SHA-256 that binds an approval to an exact effect.

use std::collections::BTreeSet;

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

/// `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
pub fn canonical(value: &Value) -> String {
    let mut out = String::new();
    write(value, &mut out);
    out
}

fn write(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => match (number.as_i64(), number.as_u64(), number.as_f64()) {
            (Some(integer), _, _) => out.push_str(&integer.to_string()),
            (None, Some(integer), _) => out.push_str(&integer.to_string()),
            (None, None, Some(float)) => out.push_str(&py_float(float)),
            (None, None, None) => out.push_str(&number.to_string()),
        },
        // serde_json escapes exactly what `ensure_ascii=False` escapes: the quote, the
        // backslash, \b \f \n \r \t and the other control characters as lowercase \u00XX.
        Value::String(text) => out.push_str(&serde_json::to_string(text).expect("strings serialize")),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write(item, out);
            }
            out.push(']');
        }
        Value::Object(object) => {
            // Rust's string order is UTF-8 byte order, which is code point order, as Python sorts.
            let mut keys: Vec<&String> = object.keys().collect();
            keys.sort();
            out.push('{');
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                out.push_str(&serde_json::to_string(key).expect("strings serialize"));
                out.push(':');
                write(&object[key], out);
            }
            out.push('}');
        }
    }
}

/// Python's `repr(float)`, which `json.dumps` writes (JSON has no NaN or infinity to read).
fn py_float(number: f64) -> String {
    let scientific = format!("{number:e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("{:e} has an exponent");
    let exponent: i32 = exponent.parse().expect("an integer exponent");
    if (-4..16).contains(&exponent) {
        let plain = format!("{number}");
        if plain.contains('.') { plain } else { format!("{plain}.0") }
    } else {
        format!("{mantissa}e{}{:02}", if exponent < 0 { '-' } else { '+' }, exponent.abs())
    }
}

pub fn sha256_hex(text: &str) -> String {
    hex::encode(Sha256::digest(text.as_bytes()))
}

/// `effects.digest`: SHA-256 of the canonical (kind, payload, expected precondition values).
pub fn digest(kind: &str, payload: &Value, preconditions: &Map<String, Value>) -> String {
    let mut triple = Map::new();
    triple.insert("kind".into(), Value::String(kind.into()));
    triple.insert("payload".into(), payload.clone());
    triple.insert("preconditions".into(), Value::Object(preconditions.clone()));
    sha256_hex(&canonical(&Value::Object(triple)))
}

/// `effects.changed_names`: names whose canonical value differs, including ones only one side has.
pub fn changed_names(expected: &Map<String, Value>, current: &Map<String, Value>) -> Vec<String> {
    let names: BTreeSet<&String> = expected.keys().chain(current.keys()).collect();
    names
        .into_iter()
        .filter(|name| match (expected.get(*name), current.get(*name)) {
            (Some(before), Some(now)) => canonical(before) != canonical(now),
            _ => true,
        })
        .cloned()
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn canonical_matches_python_json_dumps() {
        // Expected texts printed by kitty.core.effects.canonical.
        let value = json!({"b": 1, "a": [1, "é", null, true, false, 1.5, 1e16, 1e-5, 0.1, -0.0, 9007199254740992u64],
                           "z": {"y": "q\"\\\n\t\u{1}\u{7f}\u{2028}😀"}, "É": {}, "B": []});
        let expected = [r#"{"B":[],"a":[1,"é",null,true,false,1.5,1e+16,1e-05,0.1,-0.0,9007199254740992],"b":1,"z":{"y":"q\"\\\n\t\u0001"#,
                        "\u{7f}\u{2028}😀", r#""},"É":{}}"#].concat();
        assert_eq!(canonical(&value), expected);
    }

    #[test]
    fn digest_matches_python() {
        let preconditions = |value: Value| value.as_object().unwrap().clone();
        assert_eq!(digest("k", &json!({"b": 1, "a": [1, "é"]}), &preconditions(json!({"rev": 3}))),
                   "8a3dd6da6ba94ca31045e3c55e90632fc8721ebe970c055911acce2a3786d9e5");
        assert_eq!(digest("delete_block", &json!({"block": "x", "reason": ""}),
                          &preconditions(json!({"target": null, "subtree": null}))),
                   "7975c1a63d229626e4b261a5ef7555a9721c790a772641a48d91197879693422");
    }

    #[test]
    fn changed_names_are_sorted_and_include_one_sided_names() {
        let before = json!({"target": {"a": 1, "b": 2}, "subtree": 1, "gone": 0});
        let after = json!({"target": {"b": 2, "a": 1}, "subtree": 2, "new": 0});
        assert_eq!(changed_names(before.as_object().unwrap(), after.as_object().unwrap()), ["gone", "new", "subtree"]);
    }
}
