//! The block API's reads and text-flow writes (`kitty.core.block_api`).

use std::collections::HashMap;

use indexmap::IndexSet;
use rusqlite::{params, params_from_iter, Connection};
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use crate::ported::{db, fail, internal, py, Done, Fault};
use crate::search::{docs, query, text::{casefold, decimal_value, is_decimal, strip}};
use super::{actor::Actor, doc::{self, refuse}, store, text};

type Row = Map<String, Value>;
const ROOT_TYPES: [&str; 3] = ["page", "document", "quick_note"];
const WRITE_TYPES: [&str; 12] = ["bulleted_list", "callout", "code", "divider", "heading_1", "heading_2", "heading_3", "numbered_list", "quote", "text", "to_do", "toggle"];
const MAX_LIST: usize = 200;

pub struct Place<'a> {
    pub page: Option<&'a Value>,
    pub parent: Option<&'a Value>,
    pub after: Option<&'a Value>,
    pub before: Option<&'a Value>,
    pub revision: Option<&'a Value>,
}

pub(super) fn non_null(value: Option<&Value>) -> Option<&Value> { value.filter(|v| !v.is_null()) }
pub(super) fn transaction<T>(conn: &Connection, immediate: bool, body: impl FnOnce() -> Done<T>) -> Done<T> {
    if conn.is_autocommit() { db::transaction(conn, immediate, body) } else { body() }
}
fn string<'a>(row: &'a Row, key: &str) -> Done<&'a str> { row.get(key).and_then(Value::as_str).ok_or_else(internal) }
fn properties(row: &Row) -> Done<Row> { serde_json::from_str(string(row, "properties")?).map_err(|_| internal()) }
fn is_int(v: &Value) -> bool { v.is_i64() || v.is_u64() }
fn searched<T>(result: docs::Result<T>) -> Done<T> {
    result.map_err(|e| match e { docs::Failure::Database(e) => e.into(), docs::Failure::Data(_) => internal() })
}

pub fn cite(bid: &str, seq: i64) -> String { format!("(({bid}@{seq}))") }
fn title<'a>(kind: &str, value: &'a str) -> &'a str {
    doc::slice(if kind == "quick_note" { strip(value).split('\n').next().unwrap_or("") } else { value }, 0, 200)
}
fn uuid(value: &Value, what: &str) -> Done<String> {
    let lower = value.as_str().map(|s| Value::String(s.to_lowercase()));
    doc::block_id(Some(lower.as_ref().unwrap_or(value))).map_err(|_| refuse(format!("{what} must be a block ID (a UUID)")))
}
// Python's isdigit includes these non-decimal digits, which int() then refuses.
fn digits(value: &str) -> bool {
    !value.is_empty() && value.chars().all(|c| is_decimal(c) || matches!(c as u32,
        0xb2..=0xb3 | 0xb9 | 0x1369..=0x1371 | 0x19da | 0x2070 | 0x2074..=0x2079 |
        0x2080..=0x2089 | 0x2460..=0x2468 | 0x2474..=0x247c | 0x2488..=0x2490 | 0x24ea |
        0x24f5..=0x24fd | 0x24ff | 0x2776..=0x277e | 0x2780..=0x2788 | 0x278a..=0x2792 |
        0x10a40..=0x10a43 | 0x10e60..=0x10e68 | 0x11052..=0x1105a | 0x1f100..=0x1f10a))
}
fn root(conn: &Connection, page: &Value) -> Done<Row> {
    const BAD: &str = "Name a page by its block ID or its note page number";
    let numeric = page.as_str().is_some_and(|s| digits(s) && s.chars().count() <= 15);
    let row = if is_int(page) || numeric {
        let number = if let Some(s) = page.as_str() {
            i64::try_from(decimal_value(s).ok_or_else(internal)?).map_err(|_| internal())?
        } else { page.as_i64().ok_or_else(internal)? };
        db::row(conn, "SELECT * FROM blocks WHERE type='page' AND parent_id IS NULL AND deleted_at IS NULL AND json_extract(properties,'$.number')=?", [number])?
    } else if let Some(s) = page.as_str() {
        let bid = doc::block_id(Some(&Value::String(s.to_lowercase()))).map_err(|_| refuse(BAD))?;
        db::row(conn, "SELECT * FROM blocks WHERE id=? AND parent_id IS NULL AND deleted_at IS NULL", [&bid])?
            .filter(|r| r.get("type").and_then(Value::as_str).is_some_and(|k| ROOT_TYPES.contains(&k)))
    } else { return Err(refuse(BAD)); };
    row.ok_or_else(|| fail(404, "Page not found"))
}
fn notebooks(conn: &Connection) -> Done<HashMap<i64, Value>> {
    db::rows(conn, "SELECT id, title FROM note_notebooks", [])?.into_iter()
        .map(|r| Ok((r["id"].as_i64().ok_or_else(internal)?, r["title"].clone()))).collect()
}
fn summary(row: &Row, notebooks: &HashMap<i64, Value>) -> Done<Value> {
    let props = properties(row)?;
    let book = props.get("notebook_id").unwrap_or(&Value::Null);
    if book.is_array() || book.is_object() { return Err(internal()); }
    let notebook = notebooks.iter().find(|(id, _)| py::eq_int(book, **id)).map(|(_, title)| (*title).clone());
    Ok(json!({"id": row["id"], "type": row["type"], "number": props.get("number"),
        "title": title(string(row, "type")?, string(row, "text")?), "notebook": notebook,
        "inbox": props.get("inbox").is_some_and(py::truthy), "revision": props.get("revision").unwrap_or(&json!(1)),
        "updated_at": row["updated_at"]}))
}

pub fn list_pages(conn: &Connection, query: &str, kind: Option<&str>, limit: i64) -> Done<Value> {
    if query.chars().count() > 200 { return Err(refuse("The title filter is at most 200 characters")); }
    if kind.is_some_and(|k| !ROOT_TYPES.contains(&k)) { return Err(refuse("Page kind is one of: page, document, quick_note")); }
    if !(1..=200).contains(&limit) { return Err(refuse("Limit must be 1-200")); }
    let wanted = casefold(strip(query));
    let (seq, books, rows) = transaction(conn, false, || {
        let seq = store::seq(conn)?;
        let books = notebooks(conn)?;
        let kinds = kind.map_or_else(|| ROOT_TYPES.to_vec(), |k| vec![k]);
        let sql = format!("SELECT * FROM blocks WHERE parent_id IS NULL AND deleted_at IS NULL AND type IN ({}) ORDER BY updated_at DESC, id", vec!["?"; kinds.len()].join(","));
        Ok((seq, books, db::rows(conn, &sql, params_from_iter(kinds))?))
    })?;
    let mut pages = Vec::new();
    let mut total = 0;
    for row in rows {
        if casefold(title(string(&row, "type")?, string(&row, "text")?)).contains(&wanted) {
            let mut page = summary(&row, &books)?;
            total += 1;
            if pages.len() < limit as usize {
                page["cite"] = cite(string(&row, "id")?, seq).into();
                pages.push(page);
            }
        }
    }
    Ok(json!({"seq": seq, "pages": pages, "total": total, "truncated": total > limit}))
}
fn excerpt(conn: &Connection, ids: &[String]) -> Done<HashMap<String, Row>> {
    let mut found = HashMap::new();
    for chunk in ids.chunks(500) {
        let sql = format!("SELECT b.id, b.type, b.page_id, b.text, r.type AS root_type, r.text AS root_text FROM blocks b JOIN blocks r ON r.id=b.page_id WHERE b.id IN ({})", vec!["?"; chunk.len()].join(","));
        for row in db::rows(conn, &sql, params_from_iter(chunk))? { found.insert(string(&row, "id")?.to_owned(), row); }
    }
    Ok(found)
}
fn source(row: &Row, seq: i64) -> Done<Value> {
    Ok(json!({"block_id": row["id"], "type": row["type"], "page_id": row["page_id"], "root_type": row["root_type"],
        "page_title": title(string(row, "root_type")?, string(row, "root_text")?), "text": doc::slice(string(row, "text")?, 0, 400),
        "cite": cite(string(row, "id")?, seq)}))
}
pub fn search_blocks(conn: &Connection, text: &str, limit: i64) -> Done<Value> {
    let (seq, found, rows) = transaction(conn, false, || {
        let seq = store::seq(conn)?;
        let found = query::block_hits(conn, text, limit).map_err(|e| match e {
            query::Error::Refused(r) => refuse(r.0), query::Error::Failed(e) => match e {
                docs::Failure::Database(e) => Fault::from(e), docs::Failure::Data(_) => internal(),
            }
        })?;
        let ids = found["hits"].as_array().ok_or_else(internal)?.iter()
            .map(|hit| hit["block_id"].as_str().map(str::to_owned).ok_or_else(internal)).collect::<Done<Vec<_>>>()?;
        let rows = excerpt(conn, &ids)?;
        Ok((seq, found, rows))
    })?;
    let mut hits = Vec::new();
    for hit in found["hits"].as_array().ok_or_else(internal)? {
        let mut snippet = String::new();
        for part in hit["snippet"].as_array().ok_or_else(internal)? {
            let text = part[0].as_str().ok_or_else(internal)?;
            if py::truthy(&part[1]) { snippet.push_str("**"); snippet.push_str(text); snippet.push_str("**"); }
            else { snippet.push_str(text); }
        }
        let mut entry = source(rows.get(hit["block_id"].as_str().ok_or_else(internal)?).ok_or_else(internal)?, seq)?;
        entry["number"] = hit["number"].clone(); entry["snippet"] = snippet.into(); entry["match"] = hit["match"].clone();
        hits.push(entry);
    }
    Ok(json!({"seq": seq, "query": text, "hits": hits, "total": found["total"], "truncated": found["truncated"]}))
}
fn listed(conn: &Connection, sql: &str, params: impl rusqlite::Params, seq: i64) -> Done<Value> {
    let mut stmt = conn.prepare(sql)?;
    let ids = stmt.query_map(params, |row| row.get::<_, String>(0))?.collect::<Result<IndexSet<_>, _>>()?;
    let first: Vec<String> = ids.iter().take(MAX_LIST).cloned().collect();
    let rows = excerpt(conn, &first)?;
    let blocks = first.iter().map(|bid| source(rows.get(bid).ok_or_else(internal)?, seq)).collect::<Done<Vec<_>>>()?;
    Ok(json!({"blocks": blocks, "count": ids.len(), "truncated": ids.len() > MAX_LIST}))
}
fn live_source(column: &str) -> String {
    format!("JOIN blocks s ON s.id={column} AND s.deleted_at IS NULL JOIN blocks sp ON sp.id=s.page_id AND sp.deleted_at IS NULL AND sp.type IN ('page','document','quick_note')")
}
pub fn backlinks(conn: &Connection, target: &str) -> Done<Value> {
    transaction(conn, false, || {
        let seq = store::seq(conn)?;
        let row = if digits(target) { root(conn, &target.into())? } else {
            db::row(conn, "SELECT * FROM blocks WHERE id=? AND deleted_at IS NULL", [uuid(&target.into(), "The target")?])?
                .ok_or_else(|| fail(404, "Block not found"))?
        };
        let is_root = row["parent_id"].is_null();
        let id = string(&row, "id")?;
        let empty = json!({"blocks": [], "count": 0, "truncated": false});
        let mut result = json!({"seq": seq, "references": empty, "links": empty, "mentions": empty, "tagged": empty});
        let refs = format!("SELECT r.source_id FROM block_refs r {} WHERE r.kind='block' AND r.target_id{} AND s.page_id<>? ORDER BY s.updated_at DESC, s.id",
            live_source("r.source_id"), if is_root { " IN (SELECT id FROM blocks WHERE page_id=? AND deleted_at IS NULL)" } else { "=?" });
        result["references"] = listed(conn, &refs, params![id, if is_root { id } else { "" }], seq)?;
        let value = string(&row, "text")?;
        if is_root && row["type"] == "page" && !strip(value).is_empty() {
            let sql = format!("SELECT t.block_id FROM block_topics t {} WHERE t.kind='page' AND t.topic=? ORDER BY s.updated_at DESC, s.id", live_source("t.block_id"));
            result["links"] = listed(conn, &sql, [casefold(strip(value))], seq)?;
            let candidates: Vec<String> = searched(query::phrase_blocks(conn, value))?.into_iter().filter(|(_, page)| page != id).map(|(bid, _)| bid).collect();
            let texts = excerpt(conn, &candidates)?;
            let mut unlinked = Vec::new();
            for bid in &candidates {
                let row = texts.get(bid).ok_or_else(internal)?;
                if text::mentions(strip(value), &text::without_wikis(string(row, "text")?)) { unlinked.push(row); }
            }
            result["mentions"] = json!({"blocks": unlinked.iter().take(MAX_LIST).map(|r| source(r, seq)).collect::<Done<Vec<_>>>()?,
                "count": unlinked.len(), "truncated": unlinked.len() > MAX_LIST});
        }
        if is_root && row["type"] == "tag" {
            let sql = format!("SELECT t.block_id FROM block_tags t {} WHERE t.tag_id=? ORDER BY s.updated_at DESC, s.id", live_source("t.block_id"));
            result["tagged"] = listed(conn, &sql, [id], seq)?;
        }
        let books = notebooks(conn)?;
        let mut head = if is_root { summary(&row, &books)? } else {
            json!({"id": row["id"], "type": row["type"], "page_id": row["page_id"], "text": doc::slice(value, 0, 400)})
        };
        head["kind"] = if is_root { row["type"].clone() } else { "block".into() };
        head["cite"] = cite(id, seq).into(); result["target"] = head;
        Ok(result)
    })
}

fn checked_text<'a>(value: &'a Value, what: &str, maximum: usize) -> Done<&'a str> {
    value.as_str().filter(|s| !s.contains('\0') && s.chars().count() <= maximum)
        .ok_or_else(|| refuse(format!("{what} must be text of at most {maximum} characters")))
}
fn kind(value: &Value) -> Done<&str> {
    if value.is_array() || value.is_object() { return Err(internal()); }
    value.as_str().filter(|s| WRITE_TYPES.contains(s)).ok_or_else(|| refuse(format!("Block type must be one of: {}", WRITE_TYPES.join(", "))))
}
fn write_props(value: Option<&Value>, removing: bool) -> Done<Row> {
    let Some(value) = non_null(value) else { return Ok(Map::new()); };
    let value = value.as_object().filter(|m| m.keys().all(|k| ["checked", "collapsed", "language"].contains(&k.as_str())))
        .ok_or_else(|| refuse("Block properties are: checked, collapsed, language"))?;
    for (key, item) in value {
        let (valid, name) = if key == "language" { (item.is_string(), "str") } else { (item.is_boolean(), "bool") };
        if !(valid || removing && item.is_null()) { return Err(refuse(format!("Property {key} must be {name}"))); }
        if key == "language" && item.as_str().is_some_and(|s| s.chars().count() > 40) { return Err(refuse("A code language name is at most 40 characters")); }
    }
    Ok(value.clone())
}
#[derive(Debug)]
struct Spec { kind: String, text: String, props: Row, children: Vec<Spec> }
fn specs(value: &Value) -> Done<Vec<Spec>> {
    fn one(item: &Value, depth: usize, count: &mut usize) -> Done<Spec> {
        let shorthand;
        let item = if item.is_string() { shorthand = json!({"text": item}); &shorthand } else { item };
        let item = item.as_object().filter(|m| m.keys().all(|k| ["type", "text", "props", "children"].contains(&k.as_str())))
            .ok_or_else(|| refuse("Each block is {type?, text?, props?, children?} or a string"))?;
        *count += 1;
        if *count > 500 { return Err(refuse("Write at most 500 blocks at once")); }
        if depth > 16 { return Err(refuse("Nested blocks go at most 16 levels deep")); }
        let children = match item.get("children") { None => &[][..], Some(v) => v.as_array().map(Vec::as_slice).ok_or_else(|| refuse("children is a list of blocks"))? };
        let kind = kind(item.get("type").unwrap_or(&json!("text")))?.to_owned();
        let text = checked_text(item.get("text").unwrap_or(&json!("")), "Block text", 100_000)?.to_owned();
        let props = write_props(item.get("props"), false)?;
        let children = children.iter().map(|child| one(child, depth + 1, count)).collect::<Done<Vec<_>>>()?;
        Ok(Spec { kind, text, props, children })
    }
    let values = value.as_array().filter(|v| !v.is_empty()).ok_or_else(|| refuse("Send a non-empty list of blocks"))?;
    let mut count = 0;
    values.iter().map(|item| one(item, 0, &mut count)).collect()
}
fn insert_ops(parent: &str, specs: &[Spec], mut position: Row) -> Done<(Vec<Value>, Vec<String>)> {
    fn emit(under: &str, spec: &Spec, where_: Row, ops: &mut Vec<Value>, written: &mut Vec<String>) -> Done<String> {
        let bid = store::uuid4()?;
        let Value::Object(mut op) = json!({"op": "insert", "id": bid, "type": spec.kind, "parent": under, "text": spec.text}) else { unreachable!() };
        op.extend(where_);
        if !spec.props.is_empty() { op.insert("props".into(), spec.props.clone().into()); }
        ops.push(op.into()); written.push(bid.clone());
        for child in &spec.children { emit(&bid, child, Map::new(), ops, written)?; }
        Ok(bid)
    }
    let mut ops = Vec::new(); let mut written = Vec::new();
    for spec in specs {
        let bid = emit(parent, spec, position, &mut ops, &mut written)?;
        position = Map::new(); position.insert("after".into(), bid.into());
    }
    Ok((ops, written))
}
fn block(conn: &Connection, bid: &Value, what: &str) -> Done<Row> {
    db::row(conn, "SELECT b.*, r.type AS root_type, r.text AS root_text, r.deleted_at AS root_deleted FROM blocks b JOIN blocks r ON r.id=b.page_id WHERE b.id=?", [uuid(bid, what)?])?
        .filter(|r| r["deleted_at"].is_null() && r["root_deleted"].is_null()).ok_or_else(|| fail(404, format!("{what} not found")))
}
fn note_page(row: &Row) -> Done<()> {
    if row["root_type"] != "page" { return Err(refuse("Only note pages are written here: documents keep their Markdown editor and quick notes their own")); }
    Ok(())
}
fn parent(conn: &Connection, bid: &Value) -> Done<Row> {
    let row = block(conn, bid, "Parent block")?; note_page(&row)?;
    if !row["parent_id"].is_null() && !doc::is_outline(string(&row, "type")?) { return Err(refuse("Blocks go under a page or a text block, not into a canvas")); }
    Ok(row)
}
fn editable(conn: &Connection, bid: &Value) -> Done<Row> {
    let row = block(conn, bid, "Block")?; note_page(&row)?;
    if !row["parent_id"].is_null() && !doc::is_outline(string(&row, "type")?) { return Err(refuse("Only text blocks are edited here; canvas elements belong to the canvas")); }
    Ok(row)
}
fn base(page: &str, revision: Option<&Value>) -> Done<Option<Value>> {
    let Some(revision) = non_null(revision) else { return Ok(None); };
    if !is_int(revision) || revision.as_u64().is_none_or(|n| n < 1) { return Err(refuse("revision is the page revision you last read (a positive integer)")); }
    let mut out = Map::new(); out.insert(page.into(), revision.clone()); Ok(Some(out.into()))
}
fn receipt(result: Row, page: &str, written: &[String]) -> Done<Value> {
    let seqs = result["seq"].as_object().ok_or_else(internal)?;
    let seq = seqs.values().try_fold(None::<i64>, |max, value| {
        let value = value.as_i64().ok_or_else(internal)?;
        Ok::<_, Fault>(Some(max.map_or(value, |max| max.max(value))))
    })?.ok_or_else(internal)?;
    let revisions = result["revisions"].as_object().ok_or_else(internal)?;
    let pages: Row = seqs.iter().map(|(pid, value)| (pid.clone(), json!({"seq": value, "revision": revisions.get(pid)}))).collect();
    Ok(json!({"txn_id": result["txn_id"], "page_id": page, "seq": seq, "revision": revisions.get(page), "pages": pages,
        "blocks": written.iter().map(|bid| json!({"id": bid, "cite": cite(bid, seq)})).collect::<Vec<_>>()}))
}
fn position(conn: &Connection, place: &Place) -> Done<(String, String, Row)> {
    let (page, under, after, before) = (non_null(place.page), non_null(place.parent), non_null(place.after), non_null(place.before));
    if after.is_some() && before.is_some() { return Err(refuse("Use either after or before")); }
    let mut where_ = Map::new();
    let anchor = if let Some(sibling) = after.or(before) {
        let row = block(conn, sibling, "Sibling block")?;
        if row["parent_id"].is_null() { return Err(refuse("after/before name a block inside a page, not the page")); }
        let anchor = parent(conn, &row["parent_id"])?;
        if let Some(under) = under { if uuid(under, "Parent")? != string(&anchor, "id")? { return Err(refuse("That sibling is not under that parent")); } }
        where_.insert(if after.is_some() { "after" } else { "before" }.into(), row["id"].clone());
        anchor
    } else if let Some(under) = under { parent(conn, under)? }
    else if let Some(page) = page { parent(conn, &root(conn, page)?["id"])? }
    else { return Err(refuse("Say where: a page, a parent block, or a sibling to go after or before")); };
    if let Some(page) = page { if anchor["page_id"] != root(conn, page)?["id"] { return Err(refuse("That block is not on that page")); } }
    Ok((string(&anchor, "page_id")?.into(), string(&anchor, "id")?.into(), where_))
}
fn apply(conn: &Connection, ops: Vec<Value>, base: Option<Value>) -> Done<Row> {
    store::transact(conn, &Value::Array(ops), base.as_ref(), None, false, &Actor::user())
}
pub fn insert_blocks(conn: &Connection, blocks: &Value, place: &Place) -> Done<Value> {
    let specs = specs(blocks)?;
    let (result, page, written) = transaction(conn, true, || {
        let (page, parent, where_) = position(conn, place)?;
        let (ops, written) = insert_ops(&parent, &specs, where_)?;
        Ok((apply(conn, ops, base(&page, place.revision)?)?, page, written))
    })?;
    receipt(result, &page, &written)
}
pub fn update_block(conn: &Connection, block: &Value, text: Option<&Value>, kind_value: Option<&Value>, props: Option<&Value>, revision: Option<&Value>) -> Done<Value> {
    let (text, kind_value, props) = (non_null(text), non_null(kind_value), non_null(props));
    if text.is_none() && kind_value.is_none() && props.is_none() { return Err(refuse("Change the text, the type or properties")); }
    let (result, row) = transaction(conn, true, || {
        let row = editable(conn, block)?;
        let mut ops = Vec::new();
        if row["parent_id"].is_null() {
            if kind_value.is_some() || props.is_some() { return Err(refuse("A page's own block only takes a new title (text)")); }
            let title = checked_text(text.unwrap_or(&Value::Null), "Page title", 200)?;
            if strip(title).is_empty() { return Err(refuse("A page title is required")); }
            ops.push(json!({"op": "text", "id": row["id"], "text": title}));
        } else {
            let changes = write_props(props, true)?;
            if kind_value.is_some() || !changes.is_empty() {
                let mut op = json!({"op": "set", "id": row["id"]});
                if let Some(value) = kind_value { op["type"] = kind(value)?.into(); }
                if !changes.is_empty() { op["props"] = changes.into(); }
                ops.push(op);
            }
            if let Some(text) = text { ops.push(json!({"op": "text", "id": row["id"], "text": checked_text(text, "Block text", 100_000)?})); }
        }
        if ops.is_empty() { return Err(refuse("Change the text, the type or properties")); }
        Ok((apply(conn, ops, base(string(&row, "page_id")?, revision)?)?, row))
    })?;
    receipt(result, string(&row, "page_id")?, &[string(&row, "id")?.into()])
}
pub fn move_block(conn: &Connection, block: &Value, place: &Place) -> Done<Value> {
    let (result, row, destination) = transaction(conn, true, || {
        let row = editable(conn, block)?;
        if row["parent_id"].is_null() { return Err(refuse("A page's own block cannot be moved")); }
        let (destination, parent, where_) = position(conn, place)?;
        if where_.get("after") == Some(&row["id"]) || where_.get("before") == Some(&row["id"]) { return Err(refuse("A block cannot be placed next to itself")); }
        let Value::Object(mut op) = json!({"op": "move", "id": row["id"], "parent": parent}) else { unreachable!() };
        op.extend(where_);
        Ok((apply(conn, vec![op.into()], base(string(&row, "page_id")?, place.revision)?)?, row, destination))
    })?;
    let mut out = receipt(result, string(&row, "page_id")?, &[string(&row, "id")?.into()])?; out["destination"] = destination.into(); Ok(out)
}
pub fn delete_block(conn: &Connection, block: &Value, revision: Option<&Value>) -> Done<Value> {
    let (result, row) = transaction(conn, true, || {
        let row = editable(conn, block)?;
        if row["parent_id"].is_null() { return Err(refuse("Delete pages in rika!, not through the block API")); }
        Ok((apply(conn, vec![json!({"op": "delete", "id": row["id"]})], base(string(&row, "page_id")?, revision)?)?, row))
    })?;
    receipt(result, string(&row, "page_id")?, &[string(&row, "id")?.into()])
}

fn create_notebook(conn: &Connection, title: &str) -> Done<Row> {
    // notes.text counts encoded bytes, unlike block_api._text's code points.
    if title.contains('\0') { return Err(refuse("Text must be text")); }
    if title.len() > 200 { return Err(refuse("Text exceeds 200 bytes")); }
    if strip(title).is_empty() { return Err(refuse("Notebook title is required")); }
    let nid = transaction(conn, false, || {
        let count: i64 = conn.query_row("SELECT COUNT(*) FROM note_notebooks", [], |r| r.get(0))?;
        if count >= 200 { return Err(refuse("Notebook limit reached")); }
        let stamp = crate::ported::time::now_iso();
        conn.execute("INSERT INTO note_notebooks(title,subject,project,folder_id,created_at,updated_at) VALUES (?,?,?,?,?,?)", params![title, "", "", Option::<i64>::None, stamp, stamp])?;
        Ok(conn.last_insert_rowid())
    })?;
    db::row(conn, "SELECT * FROM note_notebooks WHERE id=?", [nid])?.ok_or_else(internal)
}
fn notebook(conn: &Connection, value: Option<&Value>) -> Done<Value> {
    let Some(value) = non_null(value) else {
        let row = match db::row(conn, "SELECT id FROM note_notebooks ORDER BY id LIMIT 1", [])? { Some(r) => r, None => create_notebook(conn, "Notes")? };
        return Ok(row["id"].clone());
    };
    if is_int(value) {
        if !db::exists(conn, "SELECT 1 FROM note_notebooks WHERE id=?", [db::bind(value)?])? { return Err(fail(404, "Notebook not found")); }
        return Ok(value.clone());
    }
    let title = strip(checked_text(value, "Notebook title", 200)?);
    if title.is_empty() { return Err(refuse("A notebook title is required")); }
    let row = match db::row(conn, "SELECT id FROM note_notebooks WHERE title=? COLLATE NOCASE ORDER BY id LIMIT 1", [title])? { Some(r) => r, None => create_notebook(conn, title)? };
    Ok(row["id"].clone())
}
fn page_count(conn: &Connection) -> Done<i64> { Ok(conn.query_row("SELECT COUNT(*) FROM note_page_index", [], |r| r.get(0))?) }
fn canvas_id(page: &str) -> String {
    let mut hash = Sha1::new();
    hash.update([0x5d, 0x2b, 0x9c, 0x1e, 0x4a, 0x7f, 0x4e, 0x4b, 0x9b, 0x39, 0x0c, 0x7f, 0x0c, 0x3b, 0x6a, 0x11]);
    hash.update(format!("{page}/canvas").as_bytes());
    let digest = hash.finalize(); let mut bytes = [0u8; 16]; bytes.copy_from_slice(&digest[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x50; bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex = hex::encode(bytes); format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..])
}
fn new_page_ops(conn: &Connection, notebook: Value, title: &str, inbox: bool) -> Done<(Value, Vec<Value>)> {
    let page = store::uuid4()?;
    let mut stmt = conn.prepare("SELECT COALESCE(MAX(json_extract(properties,'$.number')),0)+1 FROM blocks WHERE type=? AND parent_id IS NULL AND json_extract(properties,'$.number') IS NOT NULL")?;
    let mut rows = stmt.query(["page"])?;
    let number = db::column(rows.next()?.ok_or_else(internal)?.get_ref(0)?)?;
    let mut props = json!({"number": number, "notebook_id": notebook, "revision": 1, "viewport": {"x": 0, "y": 0, "zoom": 1}, "viewport_revision": 1, "favorite": 0, "position": 0});
    if inbox { props["inbox"] = true.into(); }
    let canvas = canvas_id(&page);
    Ok((number, vec![json!({"op": "insert", "id": page, "type": "page", "parent": null, "props": props, "text": title}),
        json!({"op": "insert", "id": canvas, "type": "canvas", "parent": page, "props": {"version": 1, "grid": true, "snap": false,
            "layers": [{"id": "default", "name": "Drawing", "visible": true, "locked": false}]}})]))
}
pub fn create_page(conn: &Connection, title: &Value, book: Option<&Value>, blocks: Option<&Value>) -> Done<Value> {
    let title = checked_text(title, "Page title", 200)?;
    if strip(title).is_empty() { return Err(refuse("A page title is required")); }
    let specs = match blocks.filter(|v| py::truthy(v)) { Some(v) => specs(v)?, None => Vec::new() };
    let (result, page, written, number) = transaction(conn, true, || {
        if page_count(conn)? >= 500 { return Err(fail(409, "Page limit reached")); }
        let (number, mut ops) = new_page_ops(conn, notebook(conn, book)?, title, false)?;
        let page = ops[0]["id"].as_str().ok_or_else(internal)?.to_owned();
        let (added, written) = insert_ops(&page, &specs, Map::new())?; ops.extend(added);
        Ok((apply(conn, ops, None)?, page, written, number))
    })?;
    let mut out = receipt(result, &page, &written)?;
    out["number"] = number; out["cite"] = cite(&page, out["seq"].as_i64().ok_or_else(internal)?).into(); Ok(out)
}
fn inbox_page(conn: &Connection) -> Done<(String, Value, Vec<Value>)> {
    if let Some(row) = db::row(conn, "SELECT id, json_extract(properties,'$.number') AS number FROM blocks WHERE type='page' AND parent_id IS NULL AND deleted_at IS NULL AND json_extract(properties,'$.inbox')=1 ORDER BY number LIMIT 1", [])? {
        return Ok((string(&row, "id")?.into(), row["number"].clone(), Vec::new()));
    }
    if page_count(conn)? >= 500 { return Err(fail(409, "Page limit reached; rika! cannot create the Inbox page")); }
    let book = match db::row(conn, "SELECT id FROM note_notebooks WHERE title=? ORDER BY id LIMIT 1", ["Inbox"])? {
        Some(row) => row,
        // ingress does not catch NotesError; the HTTP block handler only catches ClientError.
        None => create_notebook(conn, "Inbox").map_err(|e| if e.status == 400 { internal() } else { e })?,
    };
    let (number, ops) = new_page_ops(conn, book["id"].clone(), "Inbox", true)?;
    Ok((ops[0]["id"].as_str().ok_or_else(internal)?.into(), number, ops))
}
pub fn add_to_inbox(conn: &Connection, blocks: &Value) -> Done<Value> {
    let specs = specs(blocks)?;
    let (result, page, written, number) = transaction(conn, true, || {
        let (page, number, mut ops) = inbox_page(conn)?;
        let (added, written) = insert_ops(&page, &specs, Map::new())?; ops.extend(added);
        Ok((apply(conn, ops, None)?, page, written, number))
    })?;
    let mut out = receipt(result, &page, &written)?; out["number"] = number; Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn spec_validation_matches_python_order() {
        assert_eq!(specs(&json!([])).unwrap_err().message, "Send a non-empty list of blocks");
        assert_eq!(specs(&json!([{"type": [], "children": null}])).unwrap_err().message, "children is a list of blocks");
        assert_eq!(specs(&json!([{"type": []}])).unwrap_err().status, 500);
        assert_eq!(specs(&json!([{"props": {"checked": 1}}])).unwrap_err().message, "Property checked must be bool");
        assert_eq!(specs(&json!([{"props": {"language": "é".repeat(40)}}])).unwrap()[0].props["language"], "é".repeat(40));
        assert_eq!(specs(&json!(vec!["x"; 501])).unwrap_err().message, "Write at most 500 blocks at once");
    }
    #[test]
    fn receipt_uses_global_sequence_and_page_revisions() {
        let result = json!({"txn_id": "txn", "seq": {"a": 3, "b": 7}, "revisions": {"a": 2, "b": null}});
        assert_eq!(receipt(result.as_object().unwrap().clone(), "a", &["block".into()]).unwrap(),
            json!({"txn_id": "txn", "page_id": "a", "seq": 7, "revision": 2,
                "pages": {"a": {"seq": 3, "revision": 2}, "b": {"seq": 7, "revision": null}}, "blocks": [{"id": "block", "cite": "((block@7))"}]}));
    }
    #[test]
    fn unicode_digits_and_text_limits() {
        assert!(digits("١²")); assert_eq!(decimal_value("١٢"), Some(12)); assert_eq!(decimal_value("²"), None);
        assert!(checked_text(&json!("😀".repeat(200)), "Page title", 200).is_ok());
        assert_eq!(title("quick_note", "\u{1c} title\nsecond"), "title");
    }
}
