//! Tags and auto-tagging (`kitty.web_tags` over `kitty.core.blocks.tags` in Python): the
//! records, the global mode, tag pages with their unlinked mentions, and attaching by hand.
//!
//! ```text
//! GET  /api/tags                          {mode, revision, tags: [{id, name, keywords, aliases, mode,
//!                                          short, effective_mode, count, revision}]}
//! GET  /api/tags/page?id=<tag>[&offset=n]  {tag, blocks, count, next_offset, unlinked, unlinked_count}
//! GET  /api/tags/find?name=<name>          {id}: the tag a #name or alias names (null when none)
//! POST /api/tags/settings  {mode, revision}           the global mode (the build file's settings.tags)
//! POST /api/tags/create    {name, keywords?, aliases?, mode?, short?}
//! POST /api/tags/update    {id, name?, keywords?, aliases?, mode?, short?}   (mode null: follow the setting)
//! POST /api/tags/delete    {id}                       to Trash
//! POST /api/tags/link-all  {id}                       attach every unlinked mention
//! POST /api/tags/attach    {block, tag}               by hand
//! POST /api/tags/remove    {block, tag}               detach and remember
//! ```
//!
//! A tag is a `tag` root block whose text is its name; every write is one block transaction
//! as the person at this device (crate::blocks::store), so it is logged, undoable from
//! History and pushed live.

use std::collections::HashSet;

use indexmap::IndexMap;
use rusqlite::{params_from_iter, Connection};
use serde_json::{json, Map, Value};

use crate::blocks::actor::Actor;
use crate::blocks::doc::{block_id, refuse, OUTLINE_TYPES};
use crate::blocks::store;
use crate::blocks::tagging::{is_taggable_type, key_of, load_vocabulary, tag_record, Tag, Vocabulary, MODES};
use crate::blocks::text::normalize_term;
use crate::ported::{db, fail, internal, py, Call, Ctx, Done, Group};
use crate::search::text::{casefold, split};

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

const PREFIX: &str = "/api/tags";
const MAX_NAME: usize = 100;
const MAX_TERMS: usize = 200;
const MAX_TERM: usize = 100;
const PAGE_SIZE: usize = 100;
const MAX_LINK: usize = 5000;

fn owns(path: &str, mutation: bool) -> bool {
    path.starts_with("/api/tags/") || (!mutation && path == PREFIX)
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    let Some(body) = call.body else { return get(conn, call) };
    let null = Value::Null;
    let field = |key: &str| body.get(key).unwrap_or(&null);
    let expect = |keys: &[&str]| -> Done<()> {
        let mut names: Vec<&str> = body.keys().map(String::as_str).collect();
        names.sort_unstable();
        if names != keys {
            return Err(refuse(format!("Expected {{{}}}", keys.iter().map(|key| format!("\"{key}\"")).collect::<Vec<_>>().join(", "))));
        }
        Ok(())
    };
    Ok(Some(match call.path.trim_start_matches("/api/tags/") {
        "settings" => {
            expect(&["mode", "revision"])?;
            set_mode(conn, field("mode"), field("revision"))?
        }
        "create" => create(conn, body)?,
        "update" => {
            let Some(id) = body.get("id") else { return Err(refuse("Name the tag with id")) };
            let mut fields = body.clone();
            fields.shift_remove("id");
            update(conn, id, &fields)?
        }
        "delete" => {
            expect(&["id"])?;
            delete(conn, field("id"))?
        }
        "link-all" => {
            expect(&["id"])?;
            link_all(conn, field("id"))?
        }
        "attach" | "remove" => {
            expect(&["block", "tag"])?;
            let state = if call.path.ends_with("attach") { "manual" } else { "rejected" };
            set_state(conn, field("block"), field("tag"), state)?
        }
        _ => return Ok(None),
    }))
}

/// `_offset`.
fn offset(value: Option<&str>) -> Done<usize> {
    let Some(value) = value else { return Ok(0) };
    if value.is_empty() || value.len() > 8 || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(refuse("Offset must be a whole number"));
    }
    value.parse().map_err(|_| internal())
}

fn get(conn: &Connection, call: &Call) -> Done<Option<Value>> {
    match call.path {
        PREFIX => {
            call.no_query()?;
            Ok(Some(list_tags(conn)?))
        }
        "/api/tags/page" => {
            if !call.only(&["id", "offset"]) || call.get("id").is_none() {
                return Err(refuse("Name the tag page with id"));
            }
            let offset = offset(call.get("offset"))?;
            Ok(Some(page(conn, &Value::String(call.get("id").unwrap_or("").to_owned()), offset)?))
        }
        "/api/tags/find" => {
            if call.query.len() != 1 || call.get("name").is_none() {
                return Err(refuse("Name the tag with name"));
            }
            Ok(Some(json!({"id": find(conn, call.get("name").unwrap_or(""))?})))
        }
        _ => Ok(None),
    }
}

/// `_read`: a transaction unless one is open.
fn within<T>(conn: &Connection, immediate: bool, body: impl FnOnce() -> Done<T>) -> Done<T> {
    if conn.is_autocommit() { db::transaction(conn, immediate, body) } else { body() }
}

fn transact(conn: &Connection, ops: Vec<Value>) -> Done<Map<String, Value>> {
    store::transact(conn, &Value::Array(ops), None, None, false, &Actor::user())
}

// Settings ----------------------------------------------------------------------------------

/// `tags.settings`: the global mode, projected from the build file into tag_settings.
fn settings(conn: &Connection) -> Done<(Value, Value)> {
    let row = db::row(conn, "SELECT mode, revision FROM tag_settings WHERE id=1", [])?.ok_or_else(internal)?;
    Ok((row["mode"].clone(), row["revision"].clone()))
}

fn settings_json(conn: &Connection) -> Done<Value> {
    let (mode, revision) = settings(conn)?;
    Ok(json!({"mode": mode, "revision": revision}))
}

/// `tags.set_mode`: the build file's `settings.tags.mode`.
fn set_mode(conn: &Connection, mode: &Value, revision: &Value) -> Done<Value> {
    let Some(mode) = mode.as_str().filter(|mode| MODES.contains(mode)) else { return Err(refuse("The tag mode is suggest or always")) };
    let Some(revision) = py::int(revision) else { return Err(refuse("Send the settings revision you last saw")) };
    use crate::builds::value::{text, Value as Build};
    crate::builds::edit_settings(&["settings", "tags"], vec![("mode", Build::Str(text(mode)))], |tx| {
        let current: i64 = tx.query_row("SELECT revision FROM tag_settings WHERE id=1", [], |row| row.get(0)).map_err(|_| (500, internal().message))?;
        if current != revision { Err((409, "Tag settings changed in another window; reload them".into())) } else { Ok(()) }
    }).map_err(|(status, message)| fail(status, message))?;
    settings_json(conn)
}

// Records -----------------------------------------------------------------------------------

/// `" ".join(value.split())`.
fn spaced(value: &str) -> String {
    split(value).collect::<Vec<_>>().join(" ")
}

/// `_name`.
fn name(value: &Value) -> Done<String> {
    match value {
        Value::String(text) if !normalize_term(text).is_empty() && py::len(py::strip(text)) <= MAX_NAME && !text.contains('\n') => Ok(spaced(text)),
        _ => Err(refuse(format!("A tag name is 1\u{2013}{MAX_NAME} characters on one line"))),
    }
}

/// `_terms`.
fn terms(value: &Value, what: &str) -> Done<Value> {
    let items = match value {
        Value::Array(items) if items.len() <= MAX_TERMS && items.iter().all(Value::is_string) => items,
        _ => return Err(refuse(format!("Tag {what} are a list of at most {MAX_TERMS} strings"))),
    };
    let mut kept: IndexMap<String, String> = IndexMap::new();
    for item in items {
        let item = item.as_str().unwrap_or("");
        let term = spaced(item);
        if py::len(&term) > MAX_TERM || item.contains('\n') {
            return Err(refuse(format!("Each tag {} is at most {MAX_TERM} characters on one line", &what[..what.len() - 1])));
        }
        if !term.is_empty() {
            kept.entry(normalize_term(&term)).or_insert(term);
        }
    }
    Ok(Value::Array(kept.into_values().map(Value::String).collect()))
}

/// `_fields`: the validated name (when given or creating) and property changes (null removes).
fn fields(body: &Map<String, Value>, creating: bool) -> Done<(Option<String>, Map<String, Value>)> {
    if body.keys().any(|key| !["name", "keywords", "aliases", "mode", "short"].contains(&key.as_str())) {
        return Err(refuse("Unexpected tag fields"));
    }
    let name = if body.contains_key("name") || creating {
        // `body["name"]` of a create without one is Python's KeyError.
        Some(name(body.get("name").ok_or_else(internal)?)?)
    } else {
        None
    };
    let mut props = Map::new();
    if let Some(value) = body.get("keywords") {
        props.insert("keywords".into(), terms(value, "keywords")?);
    }
    if let Some(value) = body.get("aliases") {
        props.insert("aliases".into(), terms(value, "aliases")?);
    }
    if let Some(mode) = body.get("mode") {
        if !mode.is_null() && !mode.as_str().is_some_and(|mode| MODES.contains(&mode)) {
            return Err(refuse("A tag's mode is suggest, always, or null to follow the setting"));
        }
        props.insert("mode".into(), mode.clone());
    }
    if let Some(short) = body.get("short") {
        let Value::Bool(short) = short else { return Err(refuse("short is true or false")) };
        props.insert("short".into(), if *short { Value::Bool(true) } else { Value::Null });
    }
    Ok((name, props))
}

/// `repr(name)`.
fn repr(text: &str) -> String {
    let mut out = Vec::new();
    crate::builds::value::repr_str(&crate::builds::value::text(text), &mut out);
    out.into_iter().filter_map(char::from_u32).collect()
}

/// `_taken`: a name or alias another live tag already uses.
fn taken(conn: &Connection, names: &[String], other_than: Option<&str>) -> Done<Option<String>> {
    let vocab = load_vocabulary(conn)?;
    Ok(names.iter().find(|name| vocab.names.get(&normalize_term(name)).is_some_and(|owners| owners.iter().any(|tag| Some(tag.as_str()) != other_than))).cloned())
}

/// `_row`: a live tag record.
fn row(conn: &Connection, tag_id: &Value) -> Done<Map<String, Value>> {
    let bid = block_id(Some(tag_id))?;
    let found = db::row(conn, "SELECT id, text, properties, deleted_at FROM blocks WHERE id=? AND type='tag' AND parent_id IS NULL", [&bid])?;
    found.filter(|row| row["deleted_at"].is_null()).ok_or_else(|| fail(404, "Tag not found"))
}

fn string<'a>(row: &'a Map<String, Value>, key: &str) -> Done<&'a str> {
    row.get(key).and_then(Value::as_str).ok_or_else(internal)
}

fn record(row: &Map<String, Value>) -> Done<Tag> {
    tag_record(string(row, "id")?.to_owned(), string(row, "text")?.to_owned(), string(row, "properties")?)
}

fn revision_of(row: &Map<String, Value>) -> Done<Value> {
    let props: Map<String, Value> = serde_json::from_str(string(row, "properties")?).map_err(|_| internal())?;
    Ok(props.get("revision").cloned().unwrap_or(Value::from(1)))
}

/// `_json`.
fn tag_json(tag: &Tag, default: &Value, count: i64, revision: Value) -> Value {
    json!({"id": tag.id, "name": tag.name, "keywords": tag.terms, "aliases": tag.aliases, "mode": tag.mode, "short": tag.short,
           "effective_mode": tag.mode.clone().map_or_else(|| default.clone(), Value::String), "count": count, "revision": revision})
}

/// `tags.get`.
fn get_tag(conn: &Connection, tag_id: &Value) -> Done<Value> {
    within(conn, false, || {
        let row = row(conn, tag_id)?;
        let count: i64 = conn.query_row("SELECT COUNT(*) FROM block_tags t JOIN blocks b ON b.id=t.block_id AND b.deleted_at IS NULL WHERE t.tag_id=?",
                                        [string(&row, "id")?], |found| found.get(0))?;
        Ok(tag_json(&record(&row)?, &settings(conn)?.0, count, revision_of(&row)?))
    })
}

/// `tags.list_tags`: the setting and every live tag with its number of tagged blocks, by name.
fn list_tags(conn: &Connection) -> Done<Value> {
    let (mode, revision, mut tags) = within(conn, false, || {
        let (mode, revision) = settings(conn)?;
        let mut counts = std::collections::HashMap::new();
        for row in db::rows(conn, "SELECT t.tag_id, COUNT(*) FROM block_tags t JOIN blocks b ON b.id=t.block_id AND b.deleted_at IS NULL GROUP BY t.tag_id", [])? {
            counts.insert(string(&row, "tag_id")?.to_owned(), row["COUNT(*)"].as_i64().ok_or_else(internal)?);
        }
        let mut tags = Vec::new();
        for row in db::rows(conn, "SELECT id, text, properties FROM blocks WHERE type='tag' AND parent_id IS NULL AND deleted_at IS NULL", [])? {
            let tag = record(&row)?;
            let count = counts.get(&tag.id).copied().unwrap_or(0);
            tags.push((casefold(&tag.name), tag.id.clone(), tag_json(&tag, &mode, count, revision_of(&row)?)));
        }
        Ok((mode, revision, tags))
    })?;
    tags.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
    Ok(json!({"mode": mode, "revision": revision, "tags": tags.into_iter().map(|(_, _, tag)| tag).collect::<Vec<_>>()}))
}

/// `tags.find`: the live tag a name or alias (as written after `#`) names.
fn find(conn: &Connection, name: &str) -> Done<Option<String>> {
    if py::len(name) > MAX_NAME {
        return Err(refuse("Name a tag"));
    }
    Ok(load_vocabulary(conn)?.names.get(&normalize_term(name)).and_then(|owners| owners.first().cloned()))
}

fn strings(value: Option<&Value>) -> Vec<String> {
    value.and_then(Value::as_array).map(|items| items.iter().filter_map(|item| item.as_str().map(str::to_owned)).collect()).unwrap_or_default()
}

fn create(conn: &Connection, body: &Map<String, Value>) -> Done<Value> {
    let (name, props) = fields(body, true)?;
    let name = name.ok_or_else(internal)?;
    within(conn, true, || {
        let mut names = vec![name.clone()];
        names.extend(strings(props.get("aliases")));
        if let Some(clash) = taken(conn, &names, None)? {
            return Err(fail(409, format!("A tag already uses the name {}", repr(&clash))));
        }
        let tag_id = store::uuid4()?;
        let Value::Object(mut stored) = json!({"revision": 1, "keywords": [], "aliases": []}) else { unreachable!() };
        for (key, value) in props {
            if !value.is_null() {
                stored.insert(key, value);
            }
        }
        transact(conn, vec![json!({"op": "insert", "id": tag_id, "type": "tag", "parent": null, "text": name, "props": stored})])?;
        get_tag(conn, &Value::String(tag_id))
    })
}

fn update(conn: &Connection, tag_id: &Value, body: &Map<String, Value>) -> Done<Value> {
    let (name, props) = fields(body, false)?;
    within(conn, true, || {
        let row = row(conn, tag_id)?;
        let current = record(&row)?;
        let id = string(&row, "id")?.to_owned();
        let mut names = vec![name.clone().unwrap_or_else(|| current.name.clone())];
        match props.get("aliases") {
            Some(aliases) => names.extend(strings(Some(aliases))),
            None => names.extend(current.aliases.iter().cloned()),
        }
        if let Some(clash) = taken(conn, &names, Some(&id))? {
            return Err(fail(409, format!("A tag already uses the name {}", repr(&clash))));
        }
        let mut ops = Vec::new();
        if !props.is_empty() {
            ops.push(json!({"op": "set", "id": id, "props": props}));
        }
        if let Some(name) = name.filter(|name| name != string(&row, "text").unwrap_or("")) {
            ops.push(json!({"op": "text", "id": id, "text": name}));
        }
        if !ops.is_empty() {
            transact(conn, ops)?;
        }
        get_tag(conn, &Value::String(id))
    })
}

fn delete(conn: &Connection, tag_id: &Value) -> Done<Value> {
    let id = within(conn, true, || {
        let row = row(conn, tag_id)?;
        let id = string(&row, "id")?.to_owned();
        transact(conn, vec![json!({"op": "delete", "id": id})])?;
        Ok(id)
    })?;
    Ok(json!({"deleted": id}))
}

/// `_set_state`: attach by hand (`manual`, lifting a rejection) or detach and remember (`rejected`).
fn set_state(conn: &Connection, block: &Value, tag: &Value, state: &str) -> Done<Value> {
    within(conn, true, || {
        let row = row(conn, tag)?;
        let tag_id = string(&row, "id")?.to_owned();
        let bid = block_id(Some(block))?;
        let target = db::row(conn, "SELECT id, type, parent_id, deleted_at FROM blocks WHERE id=?", [&bid])?
            .filter(|target| target["deleted_at"].is_null()).ok_or_else(|| fail(404, "Block not found"))?;
        if target["parent_id"].is_null() {
            return Err(refuse("Tag blocks within a page, not the page itself"));
        }
        let mut props = Map::new();
        props.insert(key_of(&tag_id), state.into());
        let result = transact(conn, vec![json!({"op": "set", "id": target["id"], "props": props})])?;
        Ok(json!({"block": target["id"], "tag": tag_id, "state": state, "seq": result.get("seq").cloned().unwrap_or(Value::Null)}))
    })
}

// Tag pages ------------------------------------------------------------------------------------

fn taggable_sql() -> String {
    let mut kinds: Vec<&str> = OUTLINE_TYPES.iter().copied().filter(|kind| is_taggable_type(kind)).collect();
    kinds.sort_unstable();
    kinds.iter().map(|kind| format!("'{kind}'")).collect::<Vec<_>>().join(",")
}

/// `_mentions`: live text blocks of note pages whose words match `tag` but that neither carry nor
/// rejected it, newest first; up to `limit` of them and how many there are.
fn mentions(conn: &Connection, tag: Tag, limit: usize) -> Done<(Vec<Value>, usize)> {
    let mut terms: Vec<String> = std::iter::once(&tag.name).chain(&tag.aliases).chain(&tag.terms).map(|value| normalize_term(value))
        .filter(|term| !term.is_empty()).collect::<HashSet<_>>().into_iter().collect();
    terms.sort();
    let tag_id = tag.id.clone();
    let vocab = Vocabulary::of(vec![tag]);
    if vocab.empty() {
        return Ok((Vec::new(), 0));
    }
    let (ascii, other): (Vec<&String>, Vec<&String>) = terms.iter().partition(|term| term.is_ascii());
    // Python's `b.text LIKE ?` (ASCII case-insensitive) and `instr(casefold(b.text), ?)` clauses:
    // the LIKEs stay in SQL when every term is ASCII; otherwise both are checked here.
    let mut params: Vec<String> = Vec::new();
    let filter = if other.is_empty() {
        params.extend(ascii.iter().map(|term| format!("%{}%", term.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_"))));
        format!("AND ({})", vec!["b.text LIKE ? ESCAPE '\\'"; ascii.len()].join(" OR "))
    } else {
        String::new()
    };
    params.push(tag_id.clone());
    params.push(tag_id);
    let sql = format!("SELECT b.id, b.text, b.properties, b.page_id, p.text AS title, CAST(json_extract(p.properties,'$.number') AS INTEGER) AS number \
        FROM blocks b JOIN blocks p ON p.id=b.page_id AND p.type='page' AND p.deleted_at IS NULL \
        WHERE b.deleted_at IS NULL AND b.parent_id IS NOT NULL AND b.type IN ({}) {filter} \
        AND NOT EXISTS (SELECT 1 FROM block_tags t WHERE t.block_id=b.id AND t.tag_id=?) \
        AND NOT EXISTS (SELECT 1 FROM tag_rejections r WHERE r.block_id=b.id AND r.tag_id=?) \
        ORDER BY b.updated_at DESC, b.id", taggable_sql());
    let (mut found, mut total) = (Vec::new(), 0);
    for row in db::rows(conn, &sql, params_from_iter(params))? {
        let text = string(&row, "text")?;
        if !other.is_empty() {
            let lowered = text.to_ascii_lowercase();
            let folded = casefold(text);
            if !ascii.iter().any(|term| lowered.contains(term.as_str())) && !other.iter().any(|term| folded.contains(term.as_str())) {
                continue;
            }
        }
        let props: Map<String, Value> = serde_json::from_str(string(&row, "properties")?).map_err(|_| internal())?;
        if vocab.scan(text, props.get("marks")).is_empty() {
            continue;
        }
        total += 1;
        if found.len() < limit {
            found.push(json!({"id": row["id"], "page": row["page_id"], "number": row["number"], "title": row["title"],
                              "text": crate::blocks::doc::slice(text, 0, 2000)}));
        }
    }
    Ok((found, total))
}

/// `tags.page`: the tag, the blocks it is attached to (newest first) and its unlinked mentions.
fn page(conn: &Connection, tag_id: &Value, offset: usize) -> Done<Value> {
    let (tag, rows, unlinked, unlinked_count) = within(conn, false, || {
        let tag = get_tag(conn, tag_id)?;
        let id = tag["id"].as_str().ok_or_else(internal)?.to_owned();
        let sql = format!("SELECT b.id, substr(b.text,1,2000) AS text, t.source, t.created_at, b.page_id, p.type AS page_type, p.text AS title, \
            CAST(json_extract(p.properties,'$.number') AS INTEGER) AS number, json_extract(b.properties, '$.{}') IS NOT t.source AS inline \
            FROM block_tags t JOIN blocks b ON b.id=t.block_id AND b.deleted_at IS NULL JOIN blocks p ON p.id=b.page_id AND p.deleted_at IS NULL \
            WHERE t.tag_id=? ORDER BY b.updated_at DESC, b.id LIMIT ? OFFSET ?", key_of(&id));
        let rows = db::rows(conn, &sql, rusqlite::params![id, PAGE_SIZE as i64, offset as i64])?;
        let found = record(&row(conn, &Value::String(id))?)?;
        let (unlinked, count) = mentions(conn, found, PAGE_SIZE)?;
        Ok((tag, rows, unlinked, count))
    })?;
    let count = tag["count"].as_i64().ok_or_else(internal)?;
    let next = offset as i64 + rows.len() as i64;
    let blocks: Vec<Value> = rows.iter().map(|row| json!({"id": row["id"], "text": row["text"], "source": row["source"], "page": row["page_id"],
        "page_type": row["page_type"], "number": row["number"], "title": row["title"], "inline": row.get("inline").is_some_and(py::truthy)})).collect();
    Ok(json!({"tag": tag, "blocks": blocks, "count": count, "next_offset": if next < count { Value::from(next) } else { Value::Null },
              "unlinked": unlinked, "unlinked_count": unlinked_count}))
}

/// `tags.link_all`: attach the tag (`accepted`) to every unlinked mention.
fn link_all(conn: &Connection, tag_id: &Value) -> Done<Value> {
    within(conn, true, || {
        let row = row(conn, tag_id)?;
        let id = string(&row, "id")?.to_owned();
        let (found, _) = mentions(conn, record(&row)?, MAX_LINK)?;
        let key = key_of(&id);
        let ops: Vec<Value> = found.iter().map(|item| json!({"op": "set", "id": item["id"], "props": {key.clone(): "accepted"}})).collect();
        for chunk in ops.chunks(2000) {
            transact(conn, chunk.to_vec())?;
        }
        Ok(json!({"linked": ops.len(), "tag": id}))
    })
}
