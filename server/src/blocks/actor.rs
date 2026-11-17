//! Who made a block transaction (`kitty.core.blocks.actor`): the `actor` columns of `op_log`.
//! Every route the front answers writes as the person at this device; agents (MCP, the CLI,
//! Rika), paired clients, rika! itself and merges from paired devices write through Python.

use serde_json::{Map, Value};

use super::text::clean_name;
use crate::ported::{internal, Done};

pub const KINDS: [&str; 5] = ["user", "agent", "client", "system", "peer"];
pub const MAX_SESSION: usize = 100;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Actor {
    pub kind: String,
    pub name: Option<String>,
    pub session: Option<String>,
    /// For a peer merge: the actors on the other device whose changes it carried.
    pub via: Vec<Actor>,
}

/// `actor._SESSION`: 1-100 letters, digits or `. _ : -`.
pub fn is_session(text: &str) -> bool {
    (1..=MAX_SESSION).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b':' | b'-'))
}

impl Actor {
    /// `actor.USER`: the person at this device.
    pub fn user() -> Actor {
        Actor { kind: "user".into(), name: None, session: None, via: Vec::new() }
    }

    /// `Actor(kind, name, session, via)`, whose constructor cleans the name and refuses an
    /// unknown kind or a malformed session (a `ValueError`: 500 where Python met one).
    pub fn new(kind: &str, name: Option<&Value>, session: Option<String>, via: Vec<Actor>) -> Done<Actor> {
        if !KINDS.contains(&kind) {
            return Err(internal());
        }
        if session.as_deref().is_some_and(|session| !is_session(session)) {
            return Err(internal());
        }
        let name = match name {
            None | Some(Value::Null) => None,
            Some(value) => clean_name(value),
        };
        Ok(Actor { kind: kind.into(), name, session, via })
    }

    pub fn as_dict(&self) -> Value {
        let mut out = Map::new();
        out.insert("kind".into(), self.kind.clone().into());
        out.insert("name".into(), self.name.clone().map_or(Value::Null, Value::String));
        out.insert("session".into(), self.session.clone().map_or(Value::Null, Value::String));
        if !self.via.is_empty() {
            out.insert("via".into(), Value::Array(self.via.iter().map(Actor::as_dict).collect()));
        }
        Value::Object(out)
    }

    /// `(actor, actor_name, actor_session, actor_via)` for an `op_log` row.
    pub fn columns(&self) -> (String, Option<String>, Option<String>, Option<String>) {
        let via = (!self.via.is_empty()).then(|| {
            super::json::dumps(&Value::Array(self.via.iter().map(Actor::as_dict).collect()), super::json::COMPACT)
        });
        (self.kind.clone(), self.name.clone(), self.session.clone(), via)
    }
}

/// `actor.parse`: an actor from its `as_dict` form (another device's); None when malformed.
pub fn parse(value: &Value) -> Done<Option<Actor>> {
    let Value::Object(value) = value else { return Ok(None) };
    let Some(kind) = value.get("kind").and_then(Value::as_str) else { return Ok(None) };
    if !KINDS.contains(&kind) || kind == "peer" {
        return Ok(None);
    }
    let session = match value.get("session") {
        None | Some(Value::Null) => None,
        Some(Value::String(session)) if is_session(session) => Some(session.clone()),
        Some(_) => return Ok(None),
    };
    let name = value.get("name").and_then(clean_name).map(Value::String);
    Actor::new(kind, name.as_ref(), session, Vec::new()).map(Some)
}

/// `actor.from_row` over the four `actor` columns.
pub fn from_row(kind: &str, name: Option<String>, session: Option<String>, via: Option<String>) -> Done<Actor> {
    let mut carried = Vec::new();
    if let Some(via) = via.filter(|via| !via.is_empty()) {
        // `json.loads` failing is a ValueError (no actors carried); iterating what is not a list
        // gives keys or characters, none of them an actor, or fails (a TypeError: a 500).
        match serde_json::from_str::<Value>(&via) {
            Err(_) => {}
            Ok(Value::Array(items)) => {
                for item in &items {
                    if let Some(actor) = parse(item)? {
                        carried.push(actor);
                    }
                }
            }
            Ok(Value::Object(_) | Value::String(_)) => {}
            Ok(_) => return Err(internal()),
        }
    }
    Actor::new(kind, name.map(Value::String).as_ref(), session, carried)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rows_read_back_as_python_reads_them() {
        let via = json!([{"kind": "agent", "name": " A\u{7}gent ", "session": "s-1"}, {"kind": "peer"}, {"kind": "user", "session": "bad session"}]).to_string();
        let actor = from_row("peer", Some("Laptop".into()), None, Some(via)).unwrap();
        assert_eq!(actor.via.len(), 1);
        assert_eq!(actor.via[0].name.as_deref(), Some("Agent"));
        assert_eq!(actor.columns().3.unwrap(), "[{\"kind\":\"agent\",\"name\":\"Agent\",\"session\":\"s-1\"}]");
    }
}
