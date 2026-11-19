//! Server-applied transactions (`kitty.core.blocks.store`): page documents -> append-only op
//! log -> projection.
//!
//! `transact` writes `blocks`, `op_log`, `block_refs`, `block_topics`, `block_tags`,
//! `tag_rejections` (crate::blocks::tagging), `page_crdt` (crate::blocks::crdt) and the block
//! documents of the search index, as Python's `transact` does: inside one SQLite transaction
//! it loads each touched page (its projection plus its Loro document), applies every
//! operation (so cycles, limits and stale revisions are refused before anything is written),
//! appends one `op_log` row per touched page with the readable operations, the Loro update
//! bytes and the actor, and rewrites only the projection rows that changed. Python's
//! `transact` stays for its own callers (Rika's tools, MCP, the CLI, imports, peer merges);
//! both write the same rows (tests/test_blocks_parity.py).

use std::collections::{BTreeSet, HashMap, HashSet};

use indexmap::IndexMap;
use rusqlite::types::ValueRef;
use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use super::actor::Actor;
use super::crdt;
use super::doc::{block_id, refuse, Block, PageDoc, Titles};
use super::json;
use super::order;
use super::{searchdocs, tagging, text};
use crate::ported::{db, fail, internal, py, Done};

pub const MAX_OPS: usize = 20_000;
const LOG_FORMAT: i64 = 1;
/// Block types whose children belong to another UI (the canvas), not to a page loader.
pub const OPAQUE_TYPES: [&str; 1] = ["canvas"];

/// `store.now()`: `datetime.now(timezone.utc).isoformat()` (the test clock in tests).
pub fn now() -> String {
    crate::ported::time::now_iso()
}

/// `str(uuid.uuid4())`. `KITTY_TEST_UUIDS` (test-only) names a file holding a counter: each
/// ID is then the counter's, as tests/test_blocks_parity.py makes them for the Python reference.
pub fn uuid4() -> Done<String> {
    let mut bytes = [0u8; 16];
    if let Some(path) = std::env::var_os("KITTY_TEST_UUIDS") {
        let counter: u64 = std::fs::read_to_string(&path).ok().and_then(|text| text.trim().parse().ok()).unwrap_or(0);
        std::fs::write(&path, (counter + 1).to_string()).map_err(|_| internal())?;
        use sha2::Digest;
        let digest = sha2::Sha256::digest(format!("kitty-test-uuid:{counter}").as_bytes());
        bytes.copy_from_slice(&digest[..16]);
    } else {
        getrandom::fill(&mut bytes).map_err(|_| internal())?;
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex = hex::encode(bytes);
    Ok(format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..]))
}

/// This database's device identity, recorded on every op_log row.
pub fn device_id(conn: &Connection) -> Done<String> {
    conn.query_row("SELECT device_id FROM block_history WHERE id=1", [], |row| row.get(0)).optional()?.ok_or_else(internal)
}

/// `store._block`: one projection row.
pub fn read_block(row: &rusqlite::Row) -> Done<Block> {
    let properties: String = row.get("properties")?;
    let Value::Object(properties) = serde_json::from_str(&properties).map_err(|_| internal())? else { return Err(internal()) };
    Ok(Block {
        id: row.get("id")?,
        kind: row.get("type")?,
        parent_id: row.get("parent_id")?,
        page_id: row.get("page_id")?,
        order_key: row.get("order_key")?,
        properties,
        text: row.get("text")?,
        created_at: row.get("created_at")?,
        updated_at: row.get("updated_at")?,
        deleted_at: row.get("deleted_at")?,
    })
}

/// `store._rows`: a page's projection rows and the tree nodes they were projected from.
fn rows(conn: &Connection, page_id: &str) -> Done<(IndexMap<String, Block>, HashMap<String, String>)> {
    let mut statement = conn.prepare("SELECT * FROM blocks WHERE page_id=?")?;
    let has_node = statement.column_names().into_iter().any(|name| name == "node");
    let mut found = statement.query([page_id])?;
    let (mut blocks, mut nodes) = (IndexMap::new(), HashMap::new());
    while let Some(row) = found.next()? {
        let item = read_block(row)?;
        if has_node {
            if let Some(node) = row.get::<_, Option<String>>("node")?.filter(|node| !node.is_empty()) {
                nodes.insert(item.id.clone(), node);
            }
        }
        blocks.insert(item.id.clone(), item);
    }
    Ok((blocks, nodes))
}

/// `store.load_page`: the current projection of one page (deleted blocks included).
pub fn load_page(conn: &Connection, page_id: &str) -> Done<PageDoc> {
    Ok(PageDoc::new(page_id, rows(conn, page_id)?.0))
}

pub fn seq(conn: &Connection) -> Done<i64> {
    Ok(conn.query_row("SELECT COALESCE(MAX(seq),0) FROM op_log", [], |row| row.get(0))?)
}

/// A block as the page loader lists it (`_STATE_FIELDS`).
pub fn state_of(item: &Block) -> Map<String, Value> {
    let mut out = Map::new();
    out.insert("id".into(), item.id.clone().into());
    out.insert("type".into(), item.kind.clone().into());
    out.insert("parent_id".into(), item.parent_id.clone().map_or(Value::Null, Value::String));
    out.insert("order_key".into(), item.order_key.clone().into());
    out.insert("properties".into(), Value::Object(item.properties.clone()));
    out.insert("text".into(), item.text.clone().into());
    out.insert("created_at".into(), item.created_at.clone().into());
    out.insert("updated_at".into(), item.updated_at.clone().into());
    out
}

/// `store.page_state`: one live page for an editor, read in one snapshot (the caller's
/// transaction, else its own): root, live blocks in reading order, revision and the log sequence.
pub fn page_state(conn: &Connection, page_id: &Value) -> Done<Map<String, Value>> {
    let read = || -> Done<(i64, PageDoc)> { Ok((seq(conn)?, load_page(conn, &block_id(Some(page_id))?)?)) };
    let (seq, doc) = if conn.is_autocommit() { db::transaction(conn, false, read)? } else { read()? };
    let root = doc.root().filter(|root| root.live()).ok_or_else(|| fail(404, "Page not found"))?;
    let mut listed = Vec::new();
    fn walk(doc: &PageDoc, parent: &str, out: &mut Vec<Value>) {
        for child in doc.children(Some(parent), false) {
            out.push(Value::Object(state_of(child)));
            if !OPAQUE_TYPES.contains(&child.kind.as_str()) {
                walk(doc, &child.id, out);
            }
        }
    }
    walk(&doc, &root.id, &mut listed);
    let mut out = Map::new();
    out.insert("page_id".into(), root.id.clone().into());
    out.insert("seq".into(), seq.into());
    out.insert("revision".into(), root.properties.get("revision").cloned().unwrap_or(json!(1)));
    out.insert("root".into(), Value::Object(state_of(root)));
    out.insert("blocks".into(), Value::Array(listed));
    Ok(out)
}

/// The operations of one `op_log` row (`json.loads(row["ops"])["ops"]`).
pub fn logged_ops(raw: ValueRef) -> Done<Vec<Value>> {
    let bytes = match raw {
        ValueRef::Blob(bytes) | ValueRef::Text(bytes) => bytes,
        _ => return Err(internal()),
    };
    let parsed: Value = serde_json::from_slice(bytes).map_err(|_| internal())?;
    match parsed.get("ops") {
        Some(Value::Array(ops)) => Ok(ops.clone()),
        _ => Err(internal()),
    }
}

fn ignore_titles() -> impl FnMut(&Value) -> Done<Option<String>> {
    |_| Ok(None)
}

/// `store.replay`: one page from its newest snapshot at or before `upto` plus the log after it.
pub fn replay(conn: &Connection, page_id: &str, upto: Option<i64>) -> Done<PageDoc> {
    let limit = upto.unwrap_or(i64::MAX);
    let snapshot: Option<(i64, Vec<u8>)> = conn.query_row(
        "SELECT seq,state FROM page_snapshots WHERE page_id=? AND seq<=? ORDER BY seq DESC LIMIT 1",
        rusqlite::params![page_id, limit], |row| Ok((row.get(0)?, row.get::<_, Vec<u8>>(1)?)),
    ).optional()?;
    let (mut doc, after) = match snapshot {
        Some((seq, state)) => (PageDoc::decode(page_id, &state)?, seq),
        None => (PageDoc::new(page_id, IndexMap::new()), 0),
    };
    let mut statement = conn.prepare("SELECT at,ops FROM op_log WHERE page_id=? AND seq>? AND seq<=? ORDER BY seq")?;
    let mut found = statement.query(rusqlite::params![page_id, after, limit])?;
    let mut titles = ignore_titles();
    while let Some(row) = found.next()? {
        let at: String = row.get(0)?;
        for op in logged_ops(row.get_ref(1)?)? {
            let Value::Object(op) = op else { return Err(refuse("Each operation must be an object")) };
            doc.apply(&op, &at, &mut titles)?;
        }
    }
    doc.changed.clear();
    doc.removed.clear();
    Ok(doc)
}

/// `store.references`: block references and (kind, topic) mentions written in one block's text.
pub fn references(value: &str) -> (BTreeSet<String>, BTreeSet<(&'static str, String)>) {
    let refs = text::refs(value).into_iter().map(|(_, _, id)| id.to_lowercase()).collect();
    let mut topics: BTreeSet<(&'static str, String)> = text::wikis(value).into_iter()
        .map(|(_, _, title)| ("page", crate::search::text::casefold(crate::search::text::strip(&title)))).collect();
    topics.extend(text::tags(value).into_iter().map(|(_, _, name)| ("tag", crate::search::text::casefold(&name))));
    (refs, topics)
}

fn props_of(op: &Map<String, Value>) -> Option<&Map<String, Value>> {
    op.get("props").and_then(Value::as_object)
}

/// `store._is_edit`.
fn is_edit(op: &Map<String, Value>) -> bool {
    let only_view = op.get("op").and_then(Value::as_str) == Some("set") && !op.contains_key("type")
        && !props_of(op).is_some_and(|props| props.keys().any(|key| super::doc::is_edit_key(key)));
    !only_view
}

fn has_crdt(conn: &Connection) -> Done<bool> {
    Ok(db::exists(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='page_crdt'", [])?)
}

fn has_actor(conn: &Connection) -> Done<bool> {
    Ok(db::exists(conn, "SELECT 1 FROM pragma_table_info('op_log') WHERE name='actor'", [])?)
}

/// `_Transaction.notebook_title`, cached per transaction.
fn notebook_title(conn: &Connection, cache: &mut HashMap<i64, Option<String>>, notebook_id: &Value) -> Done<Option<String>> {
    // `isinstance(notebook_id, int)`: an int or a bool; an int past SQLite's integers fails to bind.
    let id = match notebook_id {
        Value::Bool(flag) => i64::from(*flag),
        Value::Number(number) if !number.is_f64() => number.as_i64().ok_or_else(internal)?,
        _ => return Ok(None),
    };
    if let Some(found) = cache.get(&id) {
        return Ok(found.clone());
    }
    let title: Option<String> = conn.query_row("SELECT title FROM note_notebooks WHERE id=?", [id], |row| row.get(0)).optional()?;
    cache.insert(id, title.clone());
    Ok(title)
}

/// One transaction in the making (`store._Transaction`).
pub struct Transaction<'c> {
    pub conn: &'c Connection,
    pub at: String,
    pub docs: IndexMap<String, PageDoc>,
    pub logged: IndexMap<String, Vec<Map<String, Value>>>,
    created: HashSet<String>,
    crdt: bool,
    actors: bool,
    peer: u64,
    titles: HashMap<i64, Option<String>>,
}

impl<'c> Transaction<'c> {
    pub fn new(conn: &'c Connection, at: String) -> Done<Transaction<'c>> {
        let crdt = has_crdt(conn)?;
        let actors = has_actor(conn)?;
        let peer = crdt::peer_of(&device_id(conn)?)?;
        Ok(Transaction { conn, at, docs: IndexMap::new(), logged: IndexMap::new(), created: HashSet::new(), crdt, actors, peer, titles: HashMap::new() })
    }

    /// Load a page's document into the transaction (once).
    pub fn doc(&mut self, page_id: &str) -> Done<&mut PageDoc> {
        if !self.docs.contains_key(page_id) {
            let (blocks, nodes) = rows(self.conn, page_id)?;
            let doc = if self.crdt { crdt::open_page(self.conn, page_id, self.peer, blocks, nodes)? } else { PageDoc::new(page_id, blocks) };
            self.docs.insert(page_id.to_owned(), doc);
        }
        Ok(self.docs.get_mut(page_id).expect("loaded above"))
    }

    /// A page created in this transaction (continuing its Loro document if one was ever made).
    fn new_page(&mut self, page_id: &str) -> Done<()> {
        if !self.crdt {
            self.docs.insert(page_id.to_owned(), PageDoc::new(page_id, IndexMap::new()));
            return Ok(());
        }
        let mut doc = crdt::open_page(self.conn, page_id, self.peer, IndexMap::new(), HashMap::new())?;
        let (conn, cache) = (self.conn, &mut self.titles);
        doc.ensure(&mut |id: &Value| notebook_title(conn, cache, id))?;
        self.docs.insert(page_id.to_owned(), doc);
        Ok(())
    }

    fn page_of(&mut self, bid: &str) -> Done<String> {
        if let Some((page_id, _)) = self.docs.iter().find(|(_, doc)| doc.blocks.contains_key(bid)) {
            return Ok(page_id.clone());
        }
        let page_id: String = self.conn.query_row("SELECT page_id FROM blocks WHERE id=?", [bid], |row| row.get(0)).optional()?
            .ok_or_else(|| fail(404, "Block not found"))?;
        self.doc(&page_id)?;
        Ok(page_id)
    }

    /// Apply one operation to its page and log it.
    pub fn record(&mut self, page_id: &str, op: Map<String, Value>) -> Done<()> {
        let Transaction { docs, conn, titles, at, logged, .. } = self;
        let conn: &Connection = conn;
        let doc = docs.get_mut(page_id).ok_or_else(internal)?;
        let mut lookup = |id: &Value| notebook_title(conn, titles, id);
        let lookup: &mut Titles = &mut lookup;
        doc.apply(&op, at, lookup)?;
        logged.entry(page_id.to_owned()).or_default().push(op);
        Ok(())
    }

    /// `_Transaction.place`: the order key for a block placed under `parent_id` as `op` says.
    fn place(&mut self, page_id: &str, parent_id: &str, op: &Map<String, Value>, moving: Option<&str>) -> Done<String> {
        let doc = self.doc(page_id)?;
        let siblings: Vec<&Block> = doc.children(Some(parent_id), false).into_iter().filter(|item| Some(item.id.as_str()) != moving).collect();
        if op.contains_key("after") && op.contains_key("before") {
            return Err(refuse("Use either after or before"));
        }
        // A key Python cannot make is its ValueError: a 500.
        let between = |a: Option<&str>, b: Option<&str>| order::key_between(a, b).map_err(|_| internal());
        let position = |wanted: &Value| siblings.iter().position(|item| wanted.as_str() == Some(item.id.as_str()));
        if let Some(after) = op.get("after") {
            if after.is_null() {
                return between(None, siblings.first().map(|item| item.order_key.as_str()));
            }
            let index = position(after).ok_or_else(|| refuse("The sibling to place after is not under that parent"))?;
            let following = siblings.get(index + 1).map(|item| item.order_key.as_str());
            return between(Some(&siblings[index].order_key), following);
        }
        if let Some(before) = op.get("before") {
            let index = position(before).ok_or_else(|| refuse("The sibling to place before is not under that parent"))?;
            let previous = if index > 0 { Some(siblings[index - 1].order_key.as_str()) } else { None };
            return between(previous, Some(&siblings[index].order_key));
        }
        between(siblings.last().map(|item| item.order_key.as_str()), None)
    }

    /// `_Transaction.apply`: one operation as a window or the API sends it.
    pub fn apply(&mut self, raw: &Value) -> Done<()> {
        let Value::Object(raw) = raw else { return Err(refuse("Each operation must be an object")) };
        let mut op = raw.clone();
        let kind = op.get("op").cloned().unwrap_or(Value::Null);
        match &kind {
            // `kind in {...}` on a list or dict is a TypeError.
            Value::Array(_) | Value::Object(_) => return Err(internal()),
            Value::String(name) if matches!(name.as_str(), "release" | "adopt") => {
                return Err(refuse("Move blocks between pages with a move operation"));
            }
            Value::String(name) if name == "put" => return Err(refuse("Unknown block operation")),
            _ => {}
        }
        if kind.as_str() == Some("insert") {
            let parent = op.get("parent").filter(|parent| !parent.is_null()).cloned();
            let page_id = match &parent {
                None => {
                    let page_id = block_id(op.get("id"))?;
                    if self.docs.contains_key(&page_id) || db::exists(self.conn, "SELECT 1 FROM blocks WHERE id=?", [&page_id])? {
                        return Err(fail(409, "Block already exists"));
                    }
                    self.new_page(&page_id)?;
                    self.created.insert(page_id.clone());
                    page_id
                }
                Some(parent) => {
                    let parent = block_id(Some(parent))?;
                    self.page_of(&parent)?
                }
            };
            if !op.contains_key("key") {
                let key = match parent.as_ref().and_then(Value::as_str) {
                    Some(parent) => self.place(&page_id, parent, &op, None)?,
                    None => order::key_between(None, None).map_err(|_| internal())?,
                };
                op.insert("key".into(), Value::String(key));
            }
            op.shift_remove("after");
            op.shift_remove("before");
            return self.record(&page_id, op);
        }
        let target = block_id(op.get("id"))?;
        let source = self.page_of(&target)?;
        if kind.as_str() != Some("move") {
            return self.record(&source, op);
        }
        let parent = block_id(op.get("parent"))?;
        let destination = self.page_of(&parent)?;
        if !op.contains_key("key") {
            let key = self.place(&destination, &parent, &op, Some(&target))?;
            op.insert("key".into(), Value::String(key));
        }
        op.shift_remove("after");
        op.shift_remove("before");
        if source == destination {
            return self.record(&source, op);
        }
        let doc = self.doc(&source)?;
        let moving = doc.get(&target)?;
        let mut rows = vec![moving.as_dict()];
        for bid in doc.descendants(&target) {
            rows.push(doc.blocks[&bid].as_dict());
        }
        let rows: Vec<Value> = rows.into_iter().map(|mut row| {
            row.shift_remove("page_id");
            Value::Object(row)
        }).collect();
        let key = op.get("key").cloned().unwrap_or(Value::Null);
        self.record(&source, json!({"op": "release", "id": target}).as_object().cloned().expect("an object"))?;
        let mut adopt = Map::new();
        adopt.insert("op".into(), "adopt".into());
        adopt.insert("parent".into(), Value::String(parent));
        adopt.insert("key".into(), key);
        adopt.insert("blocks".into(), Value::Array(rows));
        self.record(&destination, adopt)
    }

    /// `_Transaction.check_base`: the revisions the caller last saw, or a 409.
    fn check_base(&mut self, base: Option<&Value>) -> Done<()> {
        let Some(base) = base.filter(|base| !base.is_null()) else { return Ok(()) };
        let Value::Object(base) = base else { return Err(refuse("Base revisions must map page IDs to revisions")) };
        for (page_id, revision) in base {
            let doc = self.doc(&block_id(Some(&Value::String(page_id.clone())))?)?;
            let root = doc.root().ok_or_else(|| fail(404, "Page not found"))?;
            let stored = root.properties.get("revision");
            let same = py::int(revision).is_some() && stored.is_some_and(|stored| py::eq(stored, revision));
            if !same {
                return Err(fail(409, "Page changed in another window; reload before saving"));
            }
        }
        Ok(())
    }

    /// `_Transaction.separate_keys`: a block placed with a sibling's key gets one of its own.
    fn separate_keys(&mut self) -> Done<()> {
        let pages: Vec<String> = self.logged.keys().cloned().collect();
        for page_id in pages {
            let doc = &self.docs[&page_id];
            let parents: BTreeSet<String> = self.logged[&page_id].iter()
                .filter(|op| matches!(op.get("op").and_then(Value::as_str), Some("insert" | "move" | "adopt")))
                .filter_map(|op| op.get("parent").and_then(Value::as_str).filter(|parent| !parent.is_empty()).map(str::to_owned))
                .filter(|parent| doc.blocks.get(parent).is_some_and(Block::live))
                .collect();
            for parent in parents {
                let kids: Vec<(String, String)> = self.docs[&page_id].children(Some(&parent), true).into_iter()
                    .map(|kid| (kid.id.clone(), kid.order_key.clone())).collect();
                let mut previous: Option<String> = None;
                for (index, (kid, key)) in kids.iter().enumerate() {
                    let mut key = key.clone();
                    if let Some(before) = previous.as_deref().filter(|before| key.as_str() <= *before) {
                        let following = kids[index + 1..].iter().map(|(_, other)| other.as_str()).find(|other| *other > before);
                        key = order::key_between(Some(before), following).map_err(|_| internal())?;
                        let op = json!({"op": "move", "id": kid, "parent": parent, "key": key});
                        self.record(&page_id, op.as_object().cloned().expect("an object"))?;
                    }
                    previous = Some(key);
                }
            }
        }
        Ok(())
    }

    /// `_Transaction.bump`: bump the revision of every page this transaction edited.
    fn bump(&mut self) -> Done<Map<String, Value>> {
        let mut revisions = Map::new();
        let pages: Vec<String> = self.logged.keys().cloned().collect();
        for page_id in pages {
            let Some(root) = self.docs[&page_id].root() else {
                revisions.insert(page_id, Value::Null);
                continue;
            };
            let mut current = root.properties.get("revision").cloned().unwrap_or(json!(1));
            let ops = &self.logged[&page_id];
            let explicit = ops.iter().any(|op| {
                op.get("op").and_then(Value::as_str) == Some("set") && op.get("id").and_then(Value::as_str) == Some(page_id.as_str())
                    && props_of(op).is_some_and(|props| props.contains_key("revision"))
            });
            let changed = ops.iter().any(is_edit);
            if !self.created.contains(&page_id) && !explicit && changed {
                current = match &current {
                    Value::Number(number) if !number.is_f64() => json!(number.as_i64().and_then(|n| n.checked_add(1)).ok_or_else(internal)?),
                    Value::Number(number) => json!(number.as_f64().unwrap_or(f64::NAN) + 1.0),
                    Value::Bool(flag) => json!(i64::from(*flag) + 1),
                    _ => return Err(internal()),
                };
                self.record(&page_id, json!({"op": "set", "id": page_id, "props": {"revision": current}}).as_object().cloned().expect("an object"))?;
            }
            revisions.insert(page_id, current);
        }
        Ok(revisions)
    }

    /// `_Transaction.write`: append the log rows and rewrite the projection.
    fn write(&mut self, device: &str, txn_id: &str, actor: &Actor) -> Done<Map<String, Value>> {
        let conn = self.conn;
        for page_id in self.logged.keys() {
            self.docs[page_id].check()?;
        }
        let mut columns = String::from("device_id,txn_id,page_id,at,ops");
        if self.crdt {
            columns.push_str(",crdt");
        }
        if self.actors {
            columns.push_str(",actor,actor_name,actor_session,actor_via");
        }
        let marks = vec!["?"; columns.split(',').count()].join(",");
        let insert = format!("INSERT INTO op_log({columns}) VALUES ({marks})");
        let (kind, name, session, via) = actor.columns();
        let mut seqs = Map::new();
        let mut projected: Vec<Block> = Vec::new();
        let mut removed: Vec<String> = Vec::new();
        for (page_id, ops) in &self.logged {
            let doc = &self.docs[page_id];
            let payload = json::dumps(&json!({"v": LOG_FORMAT, "ops": ops}), json::COMPACT).into_bytes();
            let mut values: Vec<rusqlite::types::Value> = vec![
                device.to_owned().into(), txn_id.to_owned().into(), page_id.clone().into(), self.at.clone().into(), payload.into(),
            ];
            let update = if self.crdt { doc.finish()? } else { None };
            if self.crdt {
                values.push(update.clone().map_or(rusqlite::types::Value::Null, rusqlite::types::Value::Blob));
            }
            if self.actors {
                values.push(kind.clone().into());
                values.push(name.clone().map_or(rusqlite::types::Value::Null, Into::into));
                values.push(session.clone().map_or(rusqlite::types::Value::Null, Into::into));
                values.push(via.clone().map_or(rusqlite::types::Value::Null, Into::into));
            }
            conn.prepare(&insert)?.execute(rusqlite::params_from_iter(values))?;
            let seq = conn.last_insert_rowid();
            seqs.insert(page_id.clone(), seq.into());
            if self.crdt {
                crdt::store(conn, doc, seq, update.as_deref(), &self.at)?;
                if let Some(loro) = doc.mirror.as_ref().and_then(|mirror| mirror.loro.clone()) {
                    crdt::keep(self.peer, page_id, loro);
                }
            }
            if !doc.removed.is_empty() {
                // Scoped to this page: the same ID may be landing on another page in this transaction.
                let mut delete = conn.prepare("DELETE FROM blocks WHERE id=? AND page_id=?")?;
                for bid in &doc.removed {
                    delete.execute([bid, page_id])?;
                }
                removed.extend(doc.removed.iter().cloned());
            }
            let changed: Vec<&Block> = doc.changed.iter().map(|bid| &doc.blocks[bid]).collect();
            let nodes = doc.mirror.as_ref().map(|mirror| &mirror.nodes);
            let mut upsert = conn.prepare(if self.crdt {
                "INSERT INTO blocks(id,type,parent_id,page_id,order_key,properties,text,created_at,updated_at,deleted_at,node) \
                 VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET type=excluded.type,parent_id=excluded.parent_id,\
                 page_id=excluded.page_id,order_key=excluded.order_key,properties=excluded.properties,text=excluded.text,\
                 created_at=excluded.created_at,updated_at=excluded.updated_at,deleted_at=excluded.deleted_at,node=excluded.node"
            } else {
                "INSERT INTO blocks(id,type,parent_id,page_id,order_key,properties,text,created_at,updated_at,deleted_at) \
                 VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET type=excluded.type,parent_id=excluded.parent_id,\
                 page_id=excluded.page_id,order_key=excluded.order_key,properties=excluded.properties,text=excluded.text,\
                 created_at=excluded.created_at,updated_at=excluded.updated_at,deleted_at=excluded.deleted_at"
            })?;
            for item in &changed {
                let properties = json::dumps_map(&item.properties, json::COMPACT);
                if self.crdt {
                    let node = nodes.and_then(|nodes| nodes.get(&item.id));
                    upsert.execute(rusqlite::params![item.id, item.kind, item.parent_id, item.page_id, item.order_key, properties, item.text,
                                                     item.created_at, item.updated_at, item.deleted_at, node])?;
                } else {
                    upsert.execute(rusqlite::params![item.id, item.kind, item.parent_id, item.page_id, item.order_key, properties, item.text,
                                                     item.created_at, item.updated_at, item.deleted_at])?;
                }
            }
            if let Some(mirror) = &doc.mirror {
                let mut renode = conn.prepare("UPDATE blocks SET node=? WHERE id=?")?;
                for bid in &mirror.renodes {
                    if !doc.changed.contains(bid) && doc.blocks.contains_key(bid) {
                        renode.execute(rusqlite::params![mirror.nodes.get(bid), bid])?;
                    }
                }
            }
            let mut forget_refs = conn.prepare("DELETE FROM block_refs WHERE source_id=?")?;
            let mut forget_topics = conn.prepare("DELETE FROM block_topics WHERE block_id=?")?;
            for item in &changed {
                forget_refs.execute([&item.id])?;
            }
            for item in &changed {
                forget_topics.execute([&item.id])?;
            }
            let mut add_ref = conn.prepare("INSERT INTO block_refs(source_id,target_id,kind) VALUES (?,?,'block')")?;
            let mut add_topic = conn.prepare("INSERT INTO block_topics(block_id,kind,topic) VALUES (?,?,?)")?;
            for item in &changed {
                if !item.live() {
                    continue;
                }
                let (refs, topics) = references(&item.text);
                for target in refs {
                    add_ref.execute([&item.id, &target])?;
                }
                for (kind, topic) in topics {
                    add_topic.execute([item.id.as_str(), kind, topic.as_str()])?;
                }
            }
            projected.extend(changed.into_iter().cloned());
        }
        tagging::project(conn, &projected, &self.at)?;
        searchdocs::project(conn, &projected, &removed)?;
        Ok(seqs)
    }
}

/// `store.transact`: apply one transaction atomically; `{txn_id, seq, revisions}` keyed by page,
/// plus the auto-tags (`tagged`, `tags`) and, with `suggest`, the editor's `suggestions`.
pub fn transact(conn: &Connection, ops: &Value, base: Option<&Value>, txn_id: Option<&Value>, suggest: bool, actor: &Actor) -> Done<Map<String, Value>> {
    let ops: &Vec<Value> = match ops {
        Value::Array(ops) => ops,
        // `list(ops)` of None, a number or a bool is a TypeError.
        Value::Null | Value::Number(_) | Value::Bool(_) => return Err(internal()),
        _ => return Err(refuse(format!("A transaction carries 1\u{2013}{MAX_OPS} operations"))),
    };
    if ops.is_empty() || ops.len() > MAX_OPS {
        return Err(refuse(format!("A transaction carries 1\u{2013}{MAX_OPS} operations")));
    }
    let txn_id = match txn_id.filter(|value| !value.is_null()) {
        Some(value) => block_id(Some(value))?,
        None => uuid4()?,
    };
    let body = || -> Done<Map<String, Value>> {
        if db::exists(conn, "SELECT 1 FROM op_log WHERE txn_id=? LIMIT 1", [&txn_id])? {
            return Err(fail(409, "Transaction already applied"));
        }
        let mut work = Transaction::new(conn, now())?;
        work.check_base(base)?;
        for op in ops {
            work.apply(op)?;
        }
        if work.crdt {
            work.separate_keys()?;
        }
        let tags = tagging::annotate(&mut work, suggest)?;
        let revisions = work.bump()?;
        let seqs = work.write(&device_id(conn)?, &txn_id, actor)?;
        let mut result = Map::new();
        result.insert("txn_id".into(), Value::String(txn_id.clone()));
        result.insert("seq".into(), Value::Object(seqs));
        result.insert("revisions".into(), Value::Object(revisions));
        result.extend(tags);
        Ok(result)
    };
    if conn.is_autocommit() { db::transaction(conn, true, body) } else { body() }
}
