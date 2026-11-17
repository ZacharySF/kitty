//! Quick-note mutations (`kitty.core.quick_notes`), through the shared block engine.

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::ported::{db, fail, internal, py, Done};
use super::{actor::Actor, api::{non_null, transaction}, doc::refuse, store};

const SELECT: &str = "SELECT * FROM blocks WHERE type='quick_note' AND parent_id IS NULL AND deleted_at IS NULL";
const TINTS: [&str; 6] = ["rose", "lilac", "sky", "mint", "honey", "frost"];
type Row = Map<String, Value>;

fn id(value: &Value) -> Done<i64> {
    py::int(value).filter(|n| (1..=(1_i64 << 53) - 1).contains(n)).ok_or_else(|| refuse("Expected a quick note ID"))
}
fn body(value: &Value) -> Done<&str> {
    let text = value.as_str().filter(|s| !s.contains('\0')).ok_or_else(|| refuse("Quick note text must be text"))?;
    if text.len() > 64 * 1024 { return Err(refuse("Quick notes are limited to 64 KiB")); }
    Ok(text)
}
fn tint(value: &Value) -> Done<&str> {
    value.as_str().filter(|s| TINTS.contains(s)).ok_or_else(|| refuse(format!("Tint must be one of {}", TINTS.join(", "))))
}
fn props(row: &Row) -> Done<Row> {
    serde_json::from_str(row.get("properties").and_then(Value::as_str).ok_or_else(internal)?).map_err(|_| internal())
}
fn block(conn: &Connection, note: &Value) -> Done<Row> {
    db::row(conn, &format!("{SELECT} AND json_extract(properties,'$.number')=?"), [id(note)?])?
        .ok_or_else(|| fail(404, "Quick note not found"))
}
fn get(conn: &Connection, note: &Value) -> Done<Value> { crate::quick_notes::note(block(conn, note)?) }
fn apply(conn: &Connection, ops: Vec<Value>) -> Done<()> {
    store::transact(conn, &Value::Array(ops), None, None, false, &Actor::user())?;
    Ok(())
}
fn current(conn: &Connection, note: &Value, revision: &Value) -> Done<Row> {
    let row = block(conn, note)?;
    if !(revision.is_i64() || revision.is_u64()) || !py::eq(props(&row)?.get("revision").ok_or_else(internal)?, revision) {
        return Err(fail(409, "This quick note changed in another window; reload it before saving."));
    }
    Ok(row)
}

pub fn create(conn: &Connection, body_value: Option<&Value>, tint_value: Option<&Value>) -> Done<Value> {
    let empty = json!(""); let rose = json!("rose");
    let text = body(body_value.unwrap_or(&empty))?;
    let color = tint(tint_value.unwrap_or(&rose))?;
    let number = transaction(conn, true, || {
        let mut stmt = conn.prepare("SELECT COALESCE(MAX(json_extract(properties,'$.number')),0)+1 FROM blocks WHERE type='quick_note' AND parent_id IS NULL")?;
        let mut rows = stmt.query([])?;
        let number = db::column(rows.next()?.ok_or_else(internal)?.get_ref(0)?)?;
        let bid = store::uuid4()?;
        apply(conn, vec![json!({"op": "insert", "id": bid, "type": "quick_note", "parent": null, "text": text,
            "props": {"number": number, "tint": color, "pinned": false, "revision": 1}})])?;
        Ok(number)
    })?;
    get(conn, &number)
}

pub fn save(conn: &Connection, id: &Value, revision: &Value, body_value: Option<&Value>, tint_value: Option<&Value>, pinned: Option<&Value>) -> Done<Value> {
    let text = non_null(body_value).map(body).transpose()?;
    let color = non_null(tint_value).map(tint).transpose()?;
    let pinned = non_null(pinned);
    if pinned.is_some_and(|v| !v.is_boolean()) { return Err(refuse("Pinned must be true or false")); }
    db::transaction(conn, true, || {
        let row = current(conn, id, revision)?;
        // Python integers can grow beyond i64 while still fitting a JSON u64.
        let next = if let Some(n) = revision.as_i64() {
            if n == i64::MAX { Value::from(n as u64 + 1) } else { Value::from(n + 1) }
        } else { Value::from(revision.as_u64().ok_or_else(internal)?.checked_add(1).ok_or_else(internal)?) };
        let mut props = Map::new(); props.insert("revision".into(), next);
        if let Some(color) = color { props.insert("tint".into(), color.into()); }
        if let Some(pinned) = pinned { props.insert("pinned".into(), pinned.clone()); }
        let mut ops = Vec::new();
        if let Some(text) = text { if row["text"] != text { ops.push(json!({"op": "text", "id": row["id"], "text": text})); } }
        ops.push(json!({"op": "set", "id": row["id"], "props": props}));
        apply(conn, ops)
    })?;
    get(conn, id)
}

pub fn delete(conn: &Connection, id: &Value, revision: &Value) -> Done<()> {
    db::transaction(conn, true, || {
        let row = current(conn, id, revision)?;
        apply(conn, vec![json!({"op": "delete", "id": row["id"]})])
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn quick_validation_distinguishes_types_and_utf8_bytes() {
        assert_eq!(id(&json!(true)).unwrap_err().message, "Expected a quick note ID");
        assert_eq!(body(&Value::Null).unwrap_err().message, "Quick note text must be text");
        assert!(body(&json!("é".repeat(32768))).is_ok());
        assert_eq!(body(&json!("é".repeat(32769))).unwrap_err().message, "Quick notes are limited to 64 KiB");
        assert_eq!(tint(&json!([])).unwrap_err().status, 400);
        assert!(non_null(Some(&Value::Null)).is_none());
    }
}
