//! Committed `op_log` rows as the live channel pushes them (`web_live.changes` and
//! `kitty.core.blocks.actor.from_row` in Python).

use rusqlite::types::Value as Sql;
use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};

/// `web_live.BATCH`: rows read per query.
pub const BATCH: usize = 200;
/// `actor.KINDS`.
const KINDS: [&str; 5] = ["user", "agent", "client", "system", "peer"];
const MAX_NAME: usize = 100;

/// One `change` message, ready to send, and the page it belongs to (for `subscribe`).
pub struct Change {
    pub seq: i64,
    pub page_id: String,
    pub text: String,
}

/// `actor.clean_name`: printable, one line, at most 100 characters (None when empty). Every
/// name on a row was cleaned this way by the Python writer, so this keeps them as they are.
fn clean_name(value: &str) -> Option<String> {
    let kept: String = value.chars().filter(|c| !c.is_control() || c.is_whitespace()).collect();
    let text: String = kept.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(MAX_NAME).collect();
    (!text.is_empty()).then_some(text)
}

/// `actor._SESSION`: 1-100 letters, digits or `. _ : -`.
fn valid_session(session: &str) -> bool {
    (1..=100).contains(&session.len()) && session.bytes().all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
}

fn actor_json(kind: &str, name: Option<String>, session: Option<String>, via: Vec<Value>) -> Value {
    let mut out = Map::new();
    out.insert("kind".into(), json!(kind));
    out.insert("name".into(), json!(name));
    out.insert("session".into(), json!(session));
    if !via.is_empty() {
        out.insert("via".into(), Value::Array(via));
    }
    Value::Object(out)
}

/// `actor.parse`: one actor a peer merge carried (untrusted), or None when malformed.
fn parse_via(value: &Value) -> Option<Value> {
    let item = value.as_object()?;
    let kind = item.get("kind")?.as_str().filter(|kind| KINDS.contains(kind) && *kind != "peer")?;
    let session = match item.get("session") {
        None | Some(Value::Null) => None,
        Some(Value::String(session)) if valid_session(session) => Some(session.clone()),
        Some(_) => return None,
    };
    let name = item.get("name").and_then(Value::as_str).and_then(clean_name);
    Some(actor_json(kind, name, session, Vec::new()))
}

/// `actor.from_row(row).as_dict()`.
fn actor(kind: String, name: Option<String>, session: Option<String>, via: Option<String>) -> Value {
    let via = via
        .filter(|raw| !raw.is_empty())
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .and_then(|parsed| parsed.as_array().map(|items| items.iter().filter_map(parse_via).collect()))
        .unwrap_or_default();
    actor_json(&kind, name.as_deref().and_then(clean_name), session, via)
}

fn sql_json(value: Sql) -> Value {
    match value {
        Sql::Null => Value::Null,
        Sql::Integer(i) => json!(i),
        Sql::Real(f) => json!(f),
        Sql::Text(t) => json!(t),
        Sql::Blob(_) => Value::Null,
    }
}

/// `web_live.changes`: rows after `after` (up to `upto`), at most `limit`, oldest first.
pub fn read(conn: &Connection, after: i64, upto: Option<i64>, limit: usize) -> rusqlite::Result<Vec<Change>> {
    let mut statement = conn.prepare(
        "SELECT o.seq,o.txn_id,o.page_id,o.at,o.device_id,o.ops,o.actor,o.actor_name,o.actor_session,o.actor_via,\
         b.type,json_extract(b.properties,'$.number') AS number \
         FROM op_log o LEFT JOIN blocks b ON b.id=o.page_id AND b.parent_id IS NULL WHERE o.seq>? AND o.seq<=? ORDER BY o.seq LIMIT ?",
    )?;
    let rows = statement.query_map(params![after, upto.unwrap_or(i64::MAX), limit as i64], |row| {
        let seq: i64 = row.get(0)?;
        let page_id: String = row.get(2)?;
        // Python writes the operations as UTF-8 JSON bytes (a BLOB column).
        let ops: Vec<u8> = match row.get_ref(5)? {
            rusqlite::types::ValueRef::Text(bytes) | rusqlite::types::ValueRef::Blob(bytes) => bytes.to_vec(),
            _ => Vec::new(),
        };
        let kind: Option<String> = row.get(10)?;
        let root = match kind {
            Some(kind) => json!({ "type": kind, "number": sql_json(row.get(11)?) }),
            None => Value::Null,
        };
        let ops = serde_json::from_slice::<Value>(&ops).ok().and_then(|mut doc| doc.get_mut("ops").map(Value::take)).unwrap_or(Value::Null);
        let message = json!({
            "type": "change", "seq": seq, "txn_id": row.get::<_, String>(1)?, "page_id": page_id,
            "at": row.get::<_, String>(3)?, "device_id": row.get::<_, String>(4)?,
            "actor": actor(row.get(6)?, row.get(7)?, row.get(8)?, row.get(9)?),
            "root": root, "ops": ops,
        });
        Ok(Change { seq, page_id, text: message.to_string() })
    })?;
    rows.collect()
}

/// The newest committed sequence and this database's device id (`blocks.device_id`).
pub fn position(conn: &Connection) -> rusqlite::Result<(i64, String)> {
    let seq = conn.query_row("SELECT COALESCE(MAX(seq),0) FROM op_log", [], |row| row.get(0))?;
    let device = conn.query_row("SELECT device_id FROM block_history WHERE id=1", [], |row| row.get(0))?;
    Ok((seq, device))
}

/// `block_history.horizon_seq`: history before it was compacted away.
pub fn horizon(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("SELECT horizon_seq FROM block_history WHERE id=1", [], |row| row.get(0))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn actors_read_as_python_writes_them() {
        let agent = actor("agent".into(), Some("Claude  Code".into()), Some("s-1".into()), None);
        assert_eq!(agent, json!({ "kind": "agent", "name": "Claude Code", "session": "s-1" }));
        let via = r#"[{"kind":"agent","name":"A","session":"x"},{"kind":"peer"},{"kind":"user","session":"bad session"},3,{"kind":"user"}]"#;
        let peer = actor("peer".into(), None, None, Some(via.into()));
        assert_eq!(peer, json!({ "kind": "peer", "name": null, "session": null,
                                 "via": [{ "kind": "agent", "name": "A", "session": "x" }, { "kind": "user", "name": null, "session": null }] }));
        assert_eq!(actor("user".into(), Some("\u{7}\n ".into()), None, Some("not json".into())),
                   json!({ "kind": "user", "name": null, "session": null }));
    }
}
