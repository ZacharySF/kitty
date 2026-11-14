//! `kitty.core.builds.share`: export, preview the difference, and what an install makes active.
//!
//! A few settings change privacy or cost (notes to AI, the AI models, the daily budget,
//! scheduled monitoring): an install changes them only when the person installing agrees;
//! otherwise the current values are kept and listed as held back. Module changes need a restart.

use super::document::{self, Problem};
use super::failure::{first_error, Failure};
use super::value::{dump, dump_document, dump_str, py_eq, text, Dict, Text, Value};

const RESTART_PATHS: [&str; 1] = ["modules"];
const SENSITIVE_PATHS: [&str; 7] = [
    "settings.notes.allow_ai", "settings.ai.model", "settings.ai.rika_fast_model", "settings.ai.rika_standard_model",
    "settings.ai.rika_deep_model", "settings.ai.daily_budget_usd", "settings.opportunities.monitoring",
];

/// `flatten`: leaf values by dotted path (lists are leaves), in Python's dict order.
pub fn flatten(build: &Dict, prefix: &[u32], out: &mut Dict) {
    for (key, value) in build.iter() {
        let path = if prefix.is_empty() { key.clone() } else { [prefix, &[u32::from('.')], key].concat() };
        match value {
            Value::Dict(child) => flatten(child, &path, out),
            other => out.insert(path, other.clone()),
        }
    }
}

/// One `diff` item.
pub struct Change {
    pub path: Text,
    pub before: Value,
    pub after: Value,
    pub restart: bool,
    pub sensitive: bool,
}

impl Change {
    pub fn json(&self, out: &mut String) {
        out.push_str("{\"path\":");
        dump_str(&self.path, out);
        out.push_str(",\"before\":");
        dump(&self.before, out);
        out.push_str(",\"after\":");
        dump(&self.after, out);
        out.push_str(&format!(",\"restart\":{},\"sensitive\":{}}}", self.restart, self.sensitive));
    }
}

/// `float(value)` of a number.
fn float(value: &Value) -> Result<f64, Failure> {
    match value {
        Value::Bool(b) => Ok(f64::from(u8::from(*b))),
        Value::Int(i) => {
            let f: f64 = i.decimal().parse().map_err(|_| Failure::internal())?;
            if f.is_finite() { Ok(f) } else { Err(Failure::internal()) }
        }
        Value::Float(f) => Ok(*f),
        _ => Err(Failure::internal()),
    }
}

/// `_raises_cost_or_privacy`.
fn raises_cost_or_privacy(path: &str, before: &Value, after: &Value) -> Result<bool, Failure> {
    Ok(match path {
        "settings.notes.allow_ai" | "settings.opportunities.monitoring" => {
            matches!(after, Value::Bool(true)) && !matches!(before, Value::Bool(true))
        }
        "settings.ai.daily_budget_usd" => float(after)? > float(before)?,
        "settings.ai.model" | "settings.ai.rika_fast_model" | "settings.ai.rika_standard_model" | "settings.ai.rika_deep_model" => {
            !py_eq(after, before) && !py_eq(after, &Value::Str(Text::new()))
        }
        _ => false,
    })
}

/// `diff`: what changes from one complete build to another, in the builds' key order.
pub fn diff(before: &Dict, after: &Dict) -> Result<Vec<Change>, Failure> {
    let (mut old, mut new) = (Dict::new(), Dict::new());
    flatten(before, &[], &mut old);
    flatten(after, &[], &mut new);
    let paths: Vec<&Text> = old.keys().chain(new.keys().filter(|path| !old.contains(path))).collect();
    let mut changes = Vec::new();
    for path in paths {
        if *path == text("schema") {
            continue;
        }
        let was = old.get(path).cloned().unwrap_or(Value::Null);
        let now = new.get(path).cloned().unwrap_or(Value::Null);
        if py_eq(&was, &now) {
            continue;
        }
        let name = super::value::lossy(path);
        let sensitive = SENSITIVE_PATHS.contains(&name.as_str()) && raises_cost_or_privacy(&name, &was, &now)?;
        changes.push(Change { path: path.clone(), before: was, after: now, restart: RESTART_PATHS.contains(&name.as_str()), sensitive });
    }
    Ok(changes)
}

/// `_set`: put `value` at a dotted path of existing objects.
fn set(build: &mut Dict, path: &[u32], value: Value) -> Result<(), Failure> {
    let parts: Vec<&[u32]> = path.split(|&c| c == u32::from('.')).collect();
    let (last, parents) = parts.split_last().ok_or_else(Failure::internal)?;
    let mut node = build;
    for key in parents {
        node = match node.get_mut(key) {
            Some(Value::Dict(child)) => child,
            _ => return Err(Failure::internal()),
        };
    }
    node.insert(last.to_vec(), value);
    Ok(())
}

/// What `resolve` returns.
pub struct Plan {
    pub build: Value,
    pub changes: Vec<Change>,
    pub held: Vec<Change>,
    pub restart: bool,
    pub warnings: Vec<Problem>,
    /// `{name, author, description, kitty_version}` of the incoming build.
    pub about: Dict,
}

/// `resolve`: the build an install would make active and how it differs from `current`. Its
/// refusal is the `ShareError` (400 with the problems).
pub fn resolve(current: &Value, incoming: &[u32], accept_sensitive: bool) -> Result<Plan, Failure> {
    let checked = document::check(incoming)?;
    let Some(Value::Dict(incoming)) = checked.build else {
        return Err(Failure::with(400, format!("That build cannot be installed: {}", first_error(&checked.problems)), checked.problems));
    };
    let current = current.as_dict().ok_or_else(Failure::internal)?;
    let held: Vec<Change> = diff(current, &incoming)?.into_iter().filter(|change| change.sensitive && !accept_sensitive).collect();
    let mut last = incoming.clone();
    for change in &held {
        set(&mut last, &change.path, change.before.clone())?;
    }
    let changes = diff(current, &last)?;
    let mut about = Dict::new();
    for key in ["name", "author", "description", "kitty_version"] {
        about.insert(text(key), incoming.get_str(key).cloned().ok_or_else(Failure::internal)?);
    }
    Ok(Plan {
        restart: changes.iter().any(|change| change.restart),
        build: Value::Dict(last),
        changes,
        held,
        warnings: checked.problems.into_iter().filter(|p| p.severity == "warning").collect(),
        about,
    })
}

/// `export_text`: the shareable file for an active build, stamped with this rika! version.
pub fn export_text(active: &Value, version: &str) -> Result<Text, Failure> {
    let active = active.as_dict().ok_or_else(Failure::internal)?;
    let schema = active.get_str("schema").cloned().ok_or_else(Failure::internal)?;
    let mut ordered = Dict::new();
    ordered.insert(text("schema"), schema);
    ordered.insert(text("kitty_version"), Value::Str(text(version)));
    for (key, value) in active.iter() {
        if *key != text("schema") && *key != text("kitty_version") {
            ordered.insert(key.clone(), value.clone());
        }
    }
    Ok(dump_document(&Value::Dict(ordered)))
}
