//! The agent policy as Settings → Agents shows it (`kitty.core.agent_policy` `active`, `source`,
//! `versions`, `PURPOSES`, and `agent_access.TOOLS`' names, operations and scopes). Parsing a
//! policy (PyYAML), simulating and applying one stay in the worker.

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::ported::db::{internal, row, rows, Done};

/// `agent_policy.DEFAULT_SOURCE`: the built-in version 0.
pub const DEFAULT_SOURCE: &str = "\
# rika!'s built-in agent policy (version 0): agents read and write notes, nothing else.
mode: enforce
default: deny
rules:
  - decision: allow
    purposes: [notes, \"notes.*\"]
launch:
  tcp_ports: [443]
  udp_ports: [53]
  projects: []
  writable: []
  unconfined: run
";
/// `agent_policy.DEFAULT["mode"]`.
const DEFAULT_MODE: &str = "enforce";

/// `agent_policy.PURPOSES`, in its order.
const PURPOSES: [(&str, &str); 15] = [
    ("notes", "Note pages and their blocks"),
    ("notes.documents", "Imported documents (Markdown chunks)"),
    ("notes.quick", "Quick notes"),
    ("notes.tags", "Tag names, keywords and counts"),
    ("notes.history", "Agent sessions and pinned-citation checks in History"),
    ("applyai.jobs", "Saved and discovered job postings"),
    ("applyai.applications", "Applications and their status history"),
    ("applyai.profile", "The applicant profile (config/profile.yaml)"),
    ("applyai.answers", "Saved screening answers (config/answers.yaml)"),
    ("resume", "Resumes, resume versions and their PDFs (resumes/)"),
    ("calendar", "Calendar events and tasks"),
    ("calendar.attendees", "People named on calendar events"),
    ("mail", "Gmail messages and drafts"),
    ("secrets", "Keys, tokens and credentials (.private/, .env, data/*token*)"),
    ("store", "The database file and its copies (data/)"),
];

/// `agent_access.TOOLS`: each mediated tool's operation and scope, in its order.
const TOOLS: [(&str, &str, &str); 19] = [
    ("list_pages", "read", "notes.read"),
    ("get_page", "read", "notes.read"),
    ("search_blocks", "read", "search"),
    ("get_backlinks", "read", "notes.read"),
    ("list_tags", "read", "notes.read"),
    ("check_citations", "read", "notes.read"),
    ("list_agent_sessions", "read", "notes.read"),
    ("create_page", "write", "notes.write"),
    ("append_blocks", "write", "notes.write"),
    ("insert_blocks", "write", "notes.write"),
    ("update_block", "write", "notes.write"),
    ("move_block", "write", "notes.write"),
    ("delete_block", "write", "notes.write"),
    ("add_to_inbox", "write", "notes.write"),
    ("quick_note", "write", "notes.write"),
    ("tag_block", "write", "notes.write"),
    ("undo_agent_session", "write", "notes.write"),
    ("propose_delete_block", "write", "notes.write"),
    ("get_effect", "read", "notes.read"),
];

pub fn purposes() -> Value {
    Value::Object(PURPOSES.iter().map(|(name, text)| ((*name).to_owned(), json!(text))).collect())
}

pub fn tools() -> Value {
    Value::Object(TOOLS.iter().map(|(name, op, scope)| ((*name).to_owned(), json!({"op": op, "scope": scope}))).collect())
}

/// A stored policy body's `mode` (`json.loads(body)["mode"]`).
fn mode_of(body: &Value) -> Done<Value> {
    let text = body.as_str().ok_or_else(internal)?;
    let policy: Value = serde_json::from_str(text).map_err(|_| internal())?;
    policy.as_object().and_then(|policy| policy.get("mode")).cloned().ok_or_else(internal)
}

/// `{version, mode, source}` of the policy in force: the newest applied one, else version 0.
pub fn active(conn: &Connection) -> Done<Value> {
    Ok(match row(conn, "SELECT version, body, source FROM agent_policies ORDER BY version DESC LIMIT 1", [])? {
        Some(found) => json!({"version": found["version"], "mode": mode_of(&found["body"])?, "source": found["source"]}),
        None => json!({"version": 0, "mode": DEFAULT_MODE, "source": DEFAULT_SOURCE}),
    })
}

/// `agent_policy.versions`: newest first, then the built-in version 0.
pub fn versions(conn: &Connection) -> Done<Value> {
    let mut out = Vec::new();
    for found in rows(conn, "SELECT version, body, created_at FROM agent_policies ORDER BY version DESC", [])? {
        let mut item = Map::new();
        item.insert("version".into(), found["version"].clone());
        item.insert("created_at".into(), found["created_at"].clone());
        item.insert("mode".into(), mode_of(&found["body"])?);
        out.push(Value::Object(item));
    }
    out.push(json!({"version": 0, "created_at": null, "mode": DEFAULT_MODE}));
    Ok(Value::Array(out))
}
