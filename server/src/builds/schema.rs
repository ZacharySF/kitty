//! The published schema, checked as `jsonschema.Draft202012Validator.iter_errors` checks it:
//! the same errors in the same order, with jsonschema's `error.message` text (Python `repr`s
//! included). Only the keywords the build schema uses are implemented; a unit test holds the
//! schema to that list.

use super::json::Part;
use super::regex::compile_str;
use super::value::{js_equal, number_lt, push, repr, repr_str, repr_text, uniq, Dict, Text, TooDeep, Value};

/// Schema keywords that never yield errors.
#[cfg(test)]
pub const ANNOTATIONS: [&str; 7] = ["$schema", "$id", "title", "description", "$defs", "default", "examples"];
/// Schema keywords this validator implements.
pub const KEYWORDS: [&str; 17] = [
    "type", "required", "additionalProperties", "patternProperties", "properties", "maxProperties",
    "propertyNames", "pattern", "maxLength", "const", "enum", "uniqueItems", "items", "minimum", "maximum",
    "anyOf", "maxItems",
];

pub struct SchemaError {
    pub path: Vec<Part>,
    pub keyword: &'static str,
    pub message: Text,
    /// For `additionalProperties: false`: the instance's keys it does not allow, in order.
    pub extras: Vec<Text>,
}

pub struct Validator<'s> {
    pub root: &'s Dict,
}

fn key_is(k: &Text, s: &str) -> bool {
    k.iter().copied().eq(s.chars().map(u32::from))
}

fn is_type(instance: &Value, name: &[u32]) -> bool {
    let name: String = name.iter().filter_map(|&c| char::from_u32(c)).collect();
    match name.as_str() {
        "object" => matches!(instance, Value::Dict(_)),
        "array" => matches!(instance, Value::List(_)),
        "string" => matches!(instance, Value::Str(_)),
        "boolean" => matches!(instance, Value::Bool(_)),
        "null" => matches!(instance, Value::Null),
        "number" => matches!(instance, Value::Int(_) | Value::Float(_)),
        // Draft 2020-12: a float with no fractional part is an integer.
        "integer" => match instance {
            Value::Int(_) => true,
            Value::Float(f) => f.is_finite() && f.fract() == 0.0,
            _ => false,
        },
        _ => false,
    }
}

fn count(v: &Value) -> Option<usize> {
    match v {
        Value::Int(super::value::Int::Small(n)) => usize::try_from(*n).ok(),
        _ => None,
    }
}

/// `repr(instance) + " " + text`.
fn about(instance: &Value, text: &str) -> Text {
    let mut m = repr_text(instance);
    push(&mut m, " ");
    push(&mut m, text);
    m
}

impl<'s> Validator<'s> {
    /// `iter_errors(instance)` from `schema` at `path`.
    pub fn errors(&self, schema: &'s Dict, instance: &Value, path: &mut Vec<Part>, out: &mut Vec<SchemaError>) -> Result<(), TooDeep> {
        for (keyword, value) in schema.iter() {
            let Some(&keyword) = KEYWORDS.iter().find(|k| key_is(keyword, k)).or_else(|| {
                (key_is(keyword, "$ref")).then_some(&"$ref")
            }) else {
                continue;
            };
            macro_rules! error {
                ($message:expr) => {
                    SchemaError { path: path.clone(), keyword, message: $message, extras: Vec::new() }
                };
            }
            match keyword {
                "$ref" => {
                    let target = self.resolve(value.as_str().expect("$ref is a string"));
                    self.errors(target, instance, path, out)?;
                }
                "type" => {
                    let types: Vec<&Text> = match value {
                        Value::List(items) => items.iter().filter_map(Value::as_str).collect(),
                        Value::Str(name) => vec![name],
                        _ => Vec::new(),
                    };
                    if !types.iter().any(|name| is_type(instance, name)) {
                        let mut m = about(instance, "is not of type ");
                        for (i, name) in types.iter().enumerate() {
                            if i > 0 {
                                push(&mut m, ", ");
                            }
                            repr_str(name, &mut m);
                        }
                        out.push(error!(m));
                    }
                }
                "required" => {
                    if let (Value::Dict(d), Value::List(names)) = (instance, value) {
                        for name in names.iter().filter_map(Value::as_str) {
                            if !d.contains(name) {
                                let mut m = Text::new();
                                repr_str(name, &mut m);
                                push(&mut m, " is a required property");
                                out.push(error!(m));
                            }
                        }
                    }
                }
                "additionalProperties" => {
                    let Value::Dict(d) = instance else { continue };
                    let properties = schema.get_str("properties").and_then(Value::as_dict);
                    let patterns: Vec<_> = schema
                        .get_str("patternProperties")
                        .and_then(Value::as_dict)
                        .map(|p| p.keys().map(compile_str).collect())
                        .unwrap_or_default();
                    let extras: Vec<Text> = d
                        .keys()
                        .filter(|k| !properties.is_some_and(|p| p.contains(k)) && !patterns.iter().any(|p| p.search(k)))
                        .cloned()
                        .collect();
                    match value {
                        Value::Dict(sub) => {
                            for key in extras {
                                path.push(Part::Key(key.clone()));
                                let item = d.get(&key).expect("an extra key is in the instance");
                                self.errors(sub, item, path, out)?;
                                path.pop();
                            }
                        }
                        Value::Bool(false) if !extras.is_empty() => {
                            // The message is never shown: `_schema_problem` reports each key itself.
                            out.push(SchemaError { path: path.clone(), keyword, message: Text::new(), extras });
                        }
                        _ => {}
                    }
                }
                "patternProperties" => {
                    let (Value::Dict(d), Value::Dict(patterns)) = (instance, value) else { continue };
                    for (pattern, sub) in patterns.iter() {
                        let pattern = compile_str(pattern);
                        for (key, item) in d.iter() {
                            if pattern.search(key) {
                                path.push(Part::Key(key.clone()));
                                self.errors(sub.as_dict().expect("a subschema"), item, path, out)?;
                                path.pop();
                            }
                        }
                    }
                }
                "properties" => {
                    let (Value::Dict(d), Value::Dict(properties)) = (instance, value) else { continue };
                    for (key, sub) in properties.iter() {
                        if let Some(item) = d.get(key) {
                            path.push(Part::Key(key.clone()));
                            self.errors(sub.as_dict().expect("a subschema"), item, path, out)?;
                            path.pop();
                        }
                    }
                }
                "maxProperties" => {
                    if let (Value::Dict(d), Some(most)) = (instance, count(value)) {
                        if d.len() > most {
                            out.push(error!(about(instance, if most == 0 { "is expected to be empty" } else { "has too many properties" })));
                        }
                    }
                }
                "propertyNames" => {
                    let Value::Dict(d) = instance else { continue };
                    let sub = value.as_dict().expect("a subschema");
                    for key in d.keys() {
                        self.errors(sub, &Value::Str(key.clone()), path, out)?;
                    }
                }
                "pattern" => {
                    if let (Value::Str(s), Value::Str(pattern)) = (instance, value) {
                        if !compile_str(pattern).search(s) {
                            let mut m = about(instance, "does not match ");
                            repr_str(pattern, &mut m);
                            out.push(error!(m));
                        }
                    }
                }
                "maxLength" | "maxItems" => {
                    let length = match (keyword, instance) {
                        ("maxLength", Value::Str(s)) => s.len(),
                        ("maxItems", Value::List(items)) => items.len(),
                        _ => continue,
                    };
                    if let Some(most) = count(value) {
                        if length > most {
                            out.push(error!(about(instance, if most == 0 { "is expected to be empty" } else { "is too long" })));
                        }
                    }
                }
                "const" => {
                    if !js_equal(instance, value, 1)? {
                        let mut m = repr_text(value);
                        push(&mut m, " was expected");
                        out.push(error!(m));
                    }
                }
                "enum" => {
                    let Value::List(options) = value else { continue };
                    let mut found = false;
                    for option in options {
                        if js_equal(option, instance, 1)? {
                            found = true;
                            break;
                        }
                    }
                    if !found {
                        let mut m = about(instance, "is not one of ");
                        repr(value, &mut m);
                        out.push(error!(m));
                    }
                }
                "uniqueItems" => {
                    if let (Value::Bool(true), Value::List(items)) = (value, instance) {
                        if !uniq(items)? {
                            out.push(error!(about(instance, "has non-unique elements")));
                        }
                    }
                }
                "items" => {
                    let Value::List(items) = instance else { continue };
                    let sub = value.as_dict().expect("a subschema");
                    for (index, item) in items.iter().enumerate() {
                        path.push(Part::Index(index));
                        self.errors(sub, item, path, out)?;
                        path.pop();
                    }
                }
                "minimum" | "maximum" => {
                    if !matches!(instance, Value::Int(_) | Value::Float(_)) {
                        continue;
                    }
                    let (outside, text) = if keyword == "minimum" {
                        (number_lt(instance, value), "is less than the minimum of ")
                    } else {
                        (number_lt(value, instance), "is greater than the maximum of ")
                    };
                    if outside {
                        let mut m = about(instance, text);
                        repr(value, &mut m);
                        out.push(error!(m));
                    }
                }
                "anyOf" => {
                    let Value::List(options) = value else { continue };
                    let mut valid = false;
                    for option in options {
                        let mut errors = Vec::new();
                        self.errors(option.as_dict().expect("a subschema"), instance, path, &mut errors)?;
                        if errors.is_empty() {
                            valid = true;
                            break;
                        }
                    }
                    if !valid {
                        out.push(error!(about(instance, "is not valid under any of the given schemas")));
                    }
                }
                _ => {}
            }
        }
        Ok(())
    }

    /// A local `$ref` (`#/a/b`).
    fn resolve(&self, reference: &Text) -> &'s Dict {
        let reference: String = reference.iter().filter_map(|&c| char::from_u32(c)).collect();
        let mut node = self.root;
        for part in reference.trim_start_matches("#/").split('/') {
            node = node.get_str(part).and_then(Value::as_dict).unwrap_or_else(|| panic!("unresolvable $ref {reference}"));
        }
        node
    }
}
