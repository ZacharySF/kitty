//! Quick notes (`kitty.web_quick_notes` in Python): the list, one note, and
//! `POST /api/quick-notes/{create, save, delete}`. A quick note is a `quick_note` root block
//! addressed by its integer `number`; every write is a block transaction (crate::blocks::quick).

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::ported::{db, fail, internal, Call, Ctx, Done, Group};

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

const SELECT: &str = "SELECT text,properties,created_at,updated_at FROM blocks WHERE type='quick_note' AND parent_id IS NULL AND deleted_at IS NULL";
static ONE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/quick-notes/([1-9][0-9]{0,15})$").expect("valid"));
/// `FIELDS`: each write's allowed and required body fields.
const WRITES: [(&str, &[&str], &[&str]); 3] = [
    ("create", &["body", "tint"], &[]),
    ("save", &["id", "revision", "body", "tint", "pinned"], &["id", "revision"]),
    ("delete", &["id", "revision"], &["id", "revision"]),
];

fn write_of(path: &str) -> Option<&'static (&'static str, &'static [&'static str], &'static [&'static str])> {
    let operation = path.strip_prefix("/api/quick-notes/")?;
    WRITES.iter().find(|(name, _, _)| *name == operation)
}

fn owns(path: &str, mutation: bool) -> bool {
    if mutation {
        return write_of(path).is_some();
    }
    path == "/api/quick-notes" || path.starts_with("/api/quick-notes/")
}

/// `quick_notes._row`.
pub fn note(row: Map<String, Value>) -> Done<Value> {
    let props: Value = row.get("properties").and_then(Value::as_str).and_then(|text| serde_json::from_str(text).ok()).ok_or_else(internal)?;
    let field = |name: &str| props.get(name).cloned().ok_or_else(internal);
    Ok(json!({
        "id": field("number")?,
        "body": row.get("text").cloned().unwrap_or(Value::Null),
        "tint": field("tint")?,
        "pinned": props.get("pinned").is_some_and(crate::ported::py::truthy),
        "revision": field("revision")?,
        "created_at": row.get("created_at").cloned().unwrap_or(Value::Null),
        "updated_at": row.get("updated_at").cloned().unwrap_or(Value::Null),
    }))
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    if let Some(body) = call.body {
        return write(ctx, call.path, body);
    }
    call.no_query()?;
    if call.path == "/api/quick-notes" {
        let rows = db::rows(ctx.conn, &format!("{SELECT} ORDER BY json_extract(properties,'$.pinned') DESC, updated_at DESC, json_extract(properties,'$.number') DESC"), [])?;
        let notes = rows.into_iter().map(note).collect::<Done<Vec<Value>>>()?;
        return Ok(Some(json!({"notes": notes})));
    }
    let Some(found) = ONE.captures(call.path) else { return Ok(None) };
    // Sixteen digits at most, so it fits; Python refuses a number past 2**53 - 1.
    let number: i64 = found[1].parse().map_err(|_| internal())?;
    if number > (1 << 53) - 1 {
        return Err(fail(400, "Expected a quick note ID"));
    }
    match db::row(ctx.conn, &format!("{SELECT} AND json_extract(properties,'$.number')=?"), [number])? {
        Some(row) => Ok(Some(note(row)?)),
        None => Err(fail(404, "Quick note not found")),
    }
}

/// `web_quick_notes.post`.
fn write(ctx: &Ctx, path: &str, body: &Map<String, Value>) -> Done<Option<Value>> {
    let Some((operation, allowed, required)) = write_of(path) else { return Ok(None) };
    if body.keys().any(|key| !allowed.contains(&key.as_str())) || required.iter().any(|key| !body.contains_key(*key)) {
        return Err(fail(400, "Unexpected or missing quick note fields"));
    }
    let given = |key: &str| body.get(key).filter(|value| !value.is_null());
    use crate::blocks::quick;
    Ok(Some(match *operation {
        "create" => quick::create(ctx.conn, body.get("body"), body.get("tint"))?,
        "save" => quick::save(ctx.conn, &body["id"], &body["revision"], given("body"), given("tint"), given("pinned"))?,
        _ => {
            quick::delete(ctx.conn, &body["id"], &body["revision"])?;
            json!({"ok": true})
        }
    }))
}
