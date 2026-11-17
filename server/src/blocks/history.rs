//! Version history, point-in-time restore and Trash (`kitty.core.blocks.history`; Q15, Q20).
//!
//! Every transaction stays in `op_log` for the retention window, so any moment in it can be
//! previewed and restored exactly. Restoring never rewrites the past: it appends a transaction
//! that turns the current page into the chosen version. History groups transactions into
//! sessions per device and actor, and `citations` tells whether a block pinned as
//! `((id@seq))` changed since and how. Compaction stays on Python.

use std::collections::HashMap;

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use super::actor::{from_row, Actor, KINDS};
use super::doc::{block_id, is_edit_key, refuse, slice, Block, PageDoc};
use super::store::{load_page, logged_ops, read_block, replay, transact};
use super::text::word_diff;
use crate::ported::time::{fromisoformat, Dt, MINUTE};
use crate::ported::{db, fail, internal, py, Done};

const SESSION_GAP: i64 = 10 * MINUTE;
pub const MAX_CITATIONS: usize = 50;
const MENTIONS: &str = "SELECT MAX(o.seq) FROM op_log o, json_each(CAST(o.ops AS TEXT), '$.ops') j WHERE o.page_id=? AND o.seq>? \
AND (json_extract(j.value, '$.id')=? OR (json_extract(j.value, '$.op')='adopt' \
AND EXISTS (SELECT 1 FROM json_each(j.value, '$.blocks') b WHERE json_extract(b.value, '$.id')=?)))";

/// `with (nullcontext() if conn.in_transaction else transaction(conn, immediate=...))`.
pub(crate) fn within<T>(conn: &Connection, immediate: bool, body: impl FnOnce() -> Done<T>) -> Done<T> {
    if conn.is_autocommit() { db::transaction(conn, immediate, body) } else { body() }
}

fn page_key(page_id: &str) -> Done<String> {
    block_id(Some(&Value::String(page_id.to_owned())))
}

/// `datetime.fromisoformat` (a ValueError or TypeError: a 500).
fn when(value: &Value) -> Done<Dt> {
    value.as_str().and_then(fromisoformat).ok_or_else(internal)
}

fn horizon(conn: &Connection) -> Done<Value> {
    let found = conn.query_row("SELECT horizon_seq FROM block_history WHERE id=1", [], |row| Ok(db::column(row.get_ref(0)?))).optional()?;
    found.ok_or_else(internal)?
}

fn known(conn: &Connection, page_id: &str) -> Done<()> {
    if !db::exists(conn, "SELECT 1 FROM op_log WHERE page_id=? UNION ALL SELECT 1 FROM page_snapshots WHERE page_id=? LIMIT 1",
                   params![page_id, page_id])? {
        return Err(fail(404, "Page has no history"));
    }
    Ok(())
}

fn matches(actor: &Actor, kind: Option<&str>, session: Option<&str>) -> bool {
    let leaves = || std::iter::once(actor).chain(actor.via.iter());
    kind.is_none_or(|kind| leaves().any(|item| item.kind == kind))
        && session.is_none_or(|session| leaves().any(|item| item.session.as_deref() == Some(session)))
}

/// A JSON object key as `json.dumps` writes a dict key.
fn key_text(value: &Value) -> Done<String> {
    Ok(match value {
        Value::String(text) => text.clone(),
        Value::Null => "null".into(),
        Value::Bool(flag) => flag.to_string(),
        Value::Number(number) => match number.as_i64() {
            Some(integer) => integer.to_string(),
            None => py::float_repr(number.as_f64().ok_or_else(internal)?),
        },
        _ => return Err(internal()),
    })
}

/// `history.page_history`.
pub fn page_history(conn: &Connection, page_id: &str, actor: Option<&str>, session: Option<&str>) -> Done<Value> {
    let page_id = page_key(page_id)?;
    if actor.is_some_and(|actor| !KINDS.contains(&actor)) {
        return Err(refuse(format!("Filter by one of: {}", KINDS.join(", "))));
    }
    known(conn, &page_id)?;
    let mut sessions: Vec<Map<String, Value>> = Vec::new();
    let mut statement = conn.prepare("SELECT seq,device_id,at,ops,actor,actor_name,actor_session,actor_via FROM op_log \
                                      WHERE page_id=? ORDER BY seq")?;
    let mut found = statement.query([&page_id])?;
    while let Some(row) = found.next()? {
        let who = from_row(&row.get::<_, String>(4)?, row.get(5)?, row.get(6)?, row.get(7)?)?;
        if !matches(&who, actor, session) {
            continue;
        }
        let seq = db::column(row.get_ref(0)?)?;
        let device = db::column(row.get_ref(1)?)?;
        let at = db::column(row.get_ref(2)?)?;
        let mut counts: Map<String, Value> = Map::new();
        for op in logged_ops(row.get_ref(3)?)? {
            let Value::Object(op) = op else { return Err(internal()) };
            let kind = key_text(op.get("op").ok_or_else(internal)?)?;
            let count = counts.get(&kind).and_then(Value::as_i64).unwrap_or(0);
            counts.insert(kind, (count + 1).into());
        }
        let described = who.as_dict();
        let split = match sessions.last() {
            None => true,
            Some(current) => {
                current["device_id"] != device || current["actor"] != described || {
                    let (now, end) = (when(&at)?, when(&current["end"])?);
                    if now.zone.is_some() != end.zone.is_some() {
                        return Err(internal());
                    }
                    now.sub(&end) > SESSION_GAP
                }
            }
        };
        if split {
            let mut fresh = Map::new();
            fresh.insert("device_id".into(), device.clone());
            fresh.insert("actor".into(), described);
            fresh.insert("start".into(), at.clone());
            fresh.insert("end".into(), at.clone());
            fresh.insert("first_seq".into(), seq.clone());
            fresh.insert("last_seq".into(), seq.clone());
            fresh.insert("transactions".into(), 0.into());
            fresh.insert("ops".into(), Value::Object(Map::new()));
            fresh.insert("moments".into(), Value::Array(Vec::new()));
            sessions.push(fresh);
        }
        let current = sessions.last_mut().ok_or_else(internal)?;
        current.insert("end".into(), at.clone());
        current.insert("last_seq".into(), seq.clone());
        let transactions = current["transactions"].as_i64().unwrap_or(0) + 1;
        current.insert("transactions".into(), transactions.into());
        if let Some(Value::Array(moments)) = current.get_mut("moments") {
            moments.push(json!({"seq": seq, "at": at}));
        }
        if let Some(Value::Object(ops)) = current.get_mut("ops") {
            for (kind, count) in counts {
                let total = ops.get(&kind).and_then(Value::as_i64).unwrap_or(0) + count.as_i64().unwrap_or(0);
                ops.insert(kind, total.into());
            }
        }
    }
    drop(found);
    let snapshots = db::rows(conn, "SELECT day,seq FROM page_snapshots WHERE page_id=? ORDER BY seq DESC", [&page_id])?;
    let horizon = horizon(conn)?;
    // Merged sessions carry the paired device that made them (no `peers` before migration 040).
    let mut devices = Map::new();
    if let Ok(mut statement) = conn.prepare("SELECT device_id, name FROM peers") {
        let mut found = statement.query([])?;
        while let Some(row) = found.next()? {
            devices.insert(key_text(&db::column(row.get_ref(0)?)?)?, db::column(row.get_ref(1)?)?);
        }
    }
    let mut out = Map::new();
    out.insert("page_id".into(), page_id.into());
    out.insert("horizon_seq".into(), horizon);
    out.insert("sessions".into(), Value::Array(sessions.into_iter().rev().map(Value::Object).collect()));
    out.insert("snapshots".into(), Value::Array(snapshots.into_iter().map(Value::Object).collect()));
    out.insert("devices".into(), Value::Object(devices));
    Ok(Value::Object(out))
}

/// `history._version`: the page as it was after `seq`.
fn version(conn: &Connection, page_id: &str, seq: i64) -> Done<PageDoc> {
    let page_id = page_key(page_id)?;
    if seq < 0 {
        return Err(refuse("Choose a sequence number from the page's history"));
    }
    known(conn, &page_id)?;
    let horizon = horizon(conn)?;
    let horizon = py::int(&horizon).ok_or_else(internal)?;
    if seq <= horizon && !db::exists(conn, "SELECT 1 FROM page_snapshots WHERE page_id=? AND seq<=?", params![page_id, seq])? {
        return Err(fail(410, "That moment was compacted; the oldest kept versions are daily snapshots"));
    }
    replay(conn, &page_id, Some(seq))
}

/// `history.preview`: the page after `seq` (live and trashed blocks, reading order).
pub fn preview(conn: &Connection, page_id: &str, seq: i64) -> Done<Value> {
    let doc = version(conn, page_id, seq)?;
    let mut ordered: Vec<(usize, &Block)> = doc.blocks.values().map(|item| (doc.depth(&item.id), item)).collect();
    ordered.sort_by(|(a, x), (b, y)| (a, &x.order_key, &x.id).cmp(&(b, &y.order_key, &y.id)));
    Ok(json!({
        "page_id": doc.page_id,
        "seq": seq,
        "blocks": ordered.into_iter().map(|(_, item)| Value::Object(item.as_dict())).collect::<Vec<_>>(),
    }))
}

/// `history._content`: what a citation quotes.
fn content(item: Option<&Block>) -> Value {
    let Some(item) = item else { return Value::Null };
    let mut keys: Vec<&String> = item.properties.keys().filter(|key| is_edit_key(key) && key.as_str() != "revision").collect();
    keys.sort();
    let props: Map<String, Value> = keys.into_iter().map(|key| (key.clone(), item.properties[key].clone())).collect();
    let mut out = Map::new();
    out.insert("type".into(), item.kind.clone().into());
    out.insert("text".into(), item.text.clone().into());
    out.insert("deleted".into(), (!item.live()).into());
    out.insert("props".into(), Value::Object(props));
    Value::Object(out)
}

/// `re.fullmatch(r"\(?\(?([0-9a-f-]{36})@([0-9]{1,18})\)?\)?", value.strip())`.
fn cite_parts(value: &str) -> Option<(&str, &str)> {
    let mut text = py::strip(value);
    for _ in 0..2 {
        text = text.strip_prefix('(').unwrap_or(text);
    }
    for _ in 0..2 {
        text = text.strip_suffix(')').unwrap_or(text);
    }
    let (id, seq) = text.split_once('@')?;
    let id_ok = id.len() == 36 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f' | b'-'));
    let seq_ok = (1..=18).contains(&seq.len()) && seq.bytes().all(|b| b.is_ascii_digit());
    (id_ok && seq_ok).then_some((id, seq))
}

fn parse_cite(value: &str) -> Done<(String, i64)> {
    match cite_parts(value) {
        Some((id, seq)) => Ok((block_id(Some(&Value::String(id.to_owned())))?, seq.parse().map_err(|_| internal())?)),
        None => Err(refuse("A pinned citation is id@seq, as in ((id@seq))")),
    }
}

fn block_row(conn: &Connection, bid: &str) -> Done<Option<Block>> {
    let mut statement = conn.prepare("SELECT * FROM blocks WHERE id=?")?;
    let mut found = statement.query([bid])?;
    match found.next()? {
        Some(row) => Ok(Some(read_block(row)?)),
        None => Ok(None),
    }
}

/// `history.citations`: pinned citations `((id@seq))` checked against now. The route checks
/// that `cites` is a list of strings as Python does (a non-list: the count message; a non-string
/// item: the citation message, after the count check).
pub fn citations(conn: &Connection, cites: &[String]) -> Done<Value> {
    if !(1..=MAX_CITATIONS).contains(&cites.len()) {
        return Err(refuse(format!("Check 1-{MAX_CITATIONS} citations at a time")));
    }
    let pinned = cites.iter().map(|value| parse_cite(value)).collect::<Done<Vec<_>>>()?;
    let out = within(conn, false, || {
        let mut versions: HashMap<(String, i64), Option<PageDoc>> = HashMap::new();
        let mut out = Vec::new();
        for (bid, seq) in pinned {
            let now = block_row(conn, &bid)?;
            let mut page_id = now.as_ref().map(|item| item.page_id.clone());
            if page_id.is_none() {
                // Purged: the page it was on at `seq` (the newest log row that named it).
                page_id = db::row(conn, "SELECT o.page_id FROM op_log o, json_each(CAST(o.ops AS TEXT), '$.ops') j \
                                         WHERE o.seq<=? AND json_extract(j.value, '$.id')=? ORDER BY o.seq DESC LIMIT 1",
                                  params![seq, bid])?
                    .map(|row| row.into_iter().next().map(|(_, value)| value).unwrap_or(Value::Null))
                    .map(|value| value.as_str().map(str::to_owned).ok_or_else(internal)).transpose()?;
            }
            let mut entry = Map::new();
            entry.insert("cite".into(), format!("(({bid}@{seq}))").into());
            entry.insert("id".into(), bid.clone().into());
            entry.insert("cited_seq".into(), seq.into());
            entry.insert("page_id".into(), page_id.clone().map_or(Value::Null, Value::String));
            entry.insert("seq".into(), seq.into());
            entry.insert("changed".into(), false.into());
            entry.insert("then".into(), Value::Null);
            entry.insert("now".into(), content(now.as_ref()));
            entry.insert("diff".into(), Value::Array(Vec::new()));
            let Some(page_id) = page_id else {
                entry.insert("status".into(), "unknown".into());
                out.push(Value::Object(entry));
                continue;
            };
            let cached = (page_id.clone(), seq);
            if !versions.contains_key(&cached) {
                let doc = match version(conn, &page_id, seq) {
                    Ok(doc) => Some(doc),
                    Err(fault) if fault.status == 404 || fault.status == 410 => None,
                    Err(fault) => return Err(fault),
                };
                versions.insert(cached.clone(), doc);
            }
            let doc = &versions[&cached];
            let then = doc.as_ref().and_then(|doc| doc.blocks.get(&bid));
            let Some(then) = then else {
                let compacted = doc.is_none() && 0 < seq && {
                    let horizon = py::int(&horizon(conn)?).ok_or_else(internal)?;
                    seq <= horizon
                };
                entry.insert("status".into(), if compacted { "compacted" } else { "unknown" }.into());
                out.push(Value::Object(entry));
                continue;
            };
            let quoted = content(Some(then));
            entry.insert("then".into(), quoted.clone());
            if py::eq(&quoted, &entry["now"]) {
                entry.insert("status".into(), "unchanged".into());
                out.push(Value::Object(entry));
                continue;
            }
            entry.insert("changed".into(), true.into());
            let status = match &now {
                None => "purged",
                Some(item) if !item.live() => "deleted",
                Some(_) => "changed",
            };
            entry.insert("status".into(), status.into());
            let mentioned: Option<i64> = conn.query_row(MENTIONS, params![page_id, seq, bid, bid], |row| row.get(0))?;
            let newest = match mentioned {
                Some(found) if found != 0 => found,
                _ => conn.query_row("SELECT COALESCE(MAX(seq), ?) FROM op_log WHERE page_id=? AND seq>?",
                                    params![seq, page_id, seq], |row| row.get(0))?,
            };
            entry.insert("seq".into(), newest.into());
            let after = now.as_ref().map_or("", |item| item.text.as_str());
            let diff = word_diff(&then.text, after).into_iter().map(|(op, text)| json!({"op": op, "text": text})).collect();
            entry.insert("diff".into(), Value::Array(diff));
            out.push(Value::Object(entry));
        }
        Ok(out)
    })?;
    Ok(json!({"citations": out}))
}

/// `convert.props_diff`: a `set` operation's props turning `old` into `new`.
fn props_diff(old: &Map<String, Value>, new: &Map<String, Value>) -> Map<String, Value> {
    let mut changes: Map<String, Value> = new.iter()
        .filter(|(key, value)| old.get(*key).is_none_or(|before| !py::eq(before, value)))
        .map(|(key, value)| (key.clone(), value.clone())).collect();
    for key in old.keys() {
        if !new.contains_key(key) {
            changes.insert(key.clone(), Value::Null);
        }
    }
    changes
}

fn op(pairs: Vec<(&str, Value)>) -> Map<String, Value> {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

fn optional(value: &Option<String>) -> Value {
    value.clone().map_or(Value::Null, Value::String)
}

fn emit(working: &mut PageDoc, ops: &mut Vec<Value>, op: Map<String, Value>) -> Done<()> {
    working.apply_ordered(&op, "restore")?;
    ops.push(Value::Object(op));
    Ok(())
}

fn block_in(doc: &PageDoc, bid: &str) -> Done<Block> {
    doc.blocks.get(bid).cloned().ok_or_else(internal)
}

/// `history.restore`: one transaction that makes the page look as it did after `seq`.
pub fn restore(conn: &Connection, page_id: &str, seq: i64) -> Done<Value> {
    within(conn, true, || {
        let target = version(conn, page_id, seq)?;
        let mut working = load_page(conn, &target.page_id)?;
        let mut ops: Vec<Value> = Vec::new();
        let mut items: Vec<&Block> = target.blocks.values().collect();
        items.sort_by_key(|item| target.depth(&item.id));
        for item in items {
            if !working.blocks.contains_key(&item.id) {
                if item.parent_id.as_ref().is_some_and(|parent| !working.blocks.contains_key(parent)) {
                    continue;
                }
                if db::exists(conn, "SELECT 1 FROM blocks WHERE id=? AND page_id<>?", params![item.id, target.page_id])? {
                    continue; // The block now lives on another page; leave it there.
                }
                emit(&mut working, &mut ops, op(vec![
                    ("op", "insert".into()), ("id", item.id.clone().into()), ("type", item.kind.clone().into()),
                    ("parent", optional(&item.parent_id)), ("key", item.order_key.clone().into()),
                    ("props", Value::Object(item.properties.clone())), ("text", item.text.clone().into()),
                    ("created", item.created_at.clone().into()),
                ]))?;
            }
            let current = block_in(&working, &item.id)?;
            if !current.live() && item.live() {
                emit(&mut working, &mut ops, op(vec![("op", "restore".into()), ("id", item.id.clone().into())]))?;
            }
            let current = block_in(&working, &item.id)?;
            if current.parent_id.is_some() && (current.parent_id != item.parent_id || current.order_key != item.order_key) {
                emit(&mut working, &mut ops, op(vec![
                    ("op", "move".into()), ("id", item.id.clone().into()), ("parent", optional(&item.parent_id)),
                    ("key", item.order_key.clone().into()),
                ]))?;
            }
            let current = block_in(&working, &item.id)?;
            let mut wanted: Map<String, Value> = item.properties.iter().filter(|(key, _)| key.as_str() != "revision")
                .map(|(key, value)| (key.clone(), value.clone())).collect();
            if let Some(revision) = current.properties.get("revision").filter(|_| current.parent_id.is_none()) {
                wanted.insert("revision".into(), revision.clone());
            }
            let changes = props_diff(&current.properties, &wanted);
            if !changes.is_empty() || current.kind != item.kind {
                let mut set = op(vec![("op", "set".into()), ("id", item.id.clone().into()), ("props", Value::Object(changes))]);
                if current.kind != item.kind {
                    set.insert("type".into(), item.kind.clone().into());
                }
                emit(&mut working, &mut ops, set)?;
            }
            let current = block_in(&working, &item.id)?;
            if current.text != item.text {
                emit(&mut working, &mut ops, op(vec![("op", "text".into()), ("id", item.id.clone().into()), ("text", item.text.clone().into())]))?;
            }
        }
        let mut ids: Vec<(usize, String)> = working.blocks.values().map(|item| (working.depth(&item.id), item.id.clone())).collect();
        ids.sort_by_key(|(depth, _)| *depth);
        for (_, bid) in ids {
            let current = block_in(&working, &bid)?;
            let wanted = target.blocks.get(&bid);
            let parent_live = current.parent_id.as_ref().filter(|parent| !parent.is_empty())
                .and_then(|parent| working.blocks.get(parent)).is_none_or(Block::live);
            if current.live() && wanted.is_none_or(|wanted| !wanted.live()) && parent_live {
                emit(&mut working, &mut ops, op(vec![("op", "delete".into()), ("id", bid.into())]))?;
            }
        }
        if ops.is_empty() {
            return Err(fail(409, "The page already looks like that version"));
        }
        Ok(Value::Object(transact(conn, &Value::Array(ops), None, None, false, &Actor::user())?))
    })
}

/// `history.trash`: blocks in Trash (top-most only), newest deletion first.
pub fn trash(conn: &Connection) -> Done<Value> {
    let mut statement = conn.prepare(
        "SELECT b.id,b.type,b.page_id,b.text,b.deleted_at,r.type AS root_type,r.text AS root_text,\
         json_extract(r.properties,'$.number') AS number FROM blocks b JOIN blocks r ON r.id=b.page_id \
         LEFT JOIN blocks p ON p.id=b.parent_id WHERE b.deleted_at IS NOT NULL AND (p.id IS NULL OR p.deleted_at IS NULL) \
         ORDER BY b.deleted_at DESC,b.id LIMIT ?")?;
    let mut found = statement.query([500])?;
    let mut items = Vec::new();
    while let Some(row) = found.next()? {
        let text: Option<String> = row.get("text")?;
        let root_text: Option<String> = row.get("root_text")?;
        let root_title = root_text.as_deref().ok_or_else(internal)?;
        let title = match text.as_deref() {
            Some(text) if !text.is_empty() => text,
            _ => root_title,
        };
        let mut root = Map::new();
        root.insert("type".into(), db::column(row.get_ref("root_type")?)?);
        root.insert("number".into(), db::column(row.get_ref("number")?)?);
        root.insert("title".into(), slice(root_title, 0, 200).into());
        let mut item = Map::new();
        item.insert("id".into(), db::column(row.get_ref("id")?)?);
        item.insert("type".into(), db::column(row.get_ref("type")?)?);
        item.insert("page_id".into(), db::column(row.get_ref("page_id")?)?);
        item.insert("deleted_at".into(), db::column(row.get_ref("deleted_at")?)?);
        item.insert("title".into(), slice(title, 0, 200).into());
        item.insert("root".into(), Value::Object(root));
        items.push(Value::Object(item));
    }
    Ok(json!({"items": items}))
}

/// `history.restore_from_trash`.
pub fn restore_from_trash(conn: &Connection, bid: &Value) -> Done<Value> {
    let ops = json!([{"op": "restore", "id": block_id(Some(bid))?}]);
    Ok(Value::Object(transact(conn, &ops, None, None, false, &Actor::user())?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cites_parse_like_the_regex() {
        let id = "0123abcd-0000-4000-8000-00000000abcd";
        assert_eq!(cite_parts(&format!(" (({id}@12)) ")), Some((id, "12")));
        assert_eq!(cite_parts(&format!("({id}@1")), Some((id, "1")));
        assert_eq!(cite_parts(&format!("((({id}@1)")), None);
        assert_eq!(cite_parts(&format!("{id}@")), None);
        assert_eq!(cite_parts(&format!("{id}@1234567890123456789")), None);
        assert!(parse_cite(&format!("{}@1", "-".repeat(36))).is_err());
    }

    #[test]
    fn props_diff_lists_new_then_removed() {
        let old = json!({"a": 1, "b": 2, "c": 3});
        let new = json!({"d": 4, "a": 1.0, "b": 5});
        let diff = props_diff(old.as_object().unwrap(), new.as_object().unwrap());
        assert_eq!(Value::Object(diff).to_string(), r#"{"d":4,"b":5,"c":null}"#);
    }
}
