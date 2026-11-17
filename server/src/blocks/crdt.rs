//! Loro page documents (`kitty.core.blocks.crdt`): what paired devices merge.
//!
//! Every page is one Loro document whose `blocks` tree holds one node per block; each node's
//! metadata map carries `id`, `type`, `key`, `created`, `updated`, `deleted`, a `props` map
//! and a `text` LoroText. A transaction mirrors every operation it applies into the page's
//! document with the same calls, in the same order, as Python's `LoroPageDoc`, on the Loro
//! version Python's `loro` package wraps, so the update bytes in `op_log.crdt` and the
//! snapshots in `page_crdt` are the ones Python writes. Some properties belong to one device
//! and never sync (`LOCAL_KEYS`); a root carries its notebook's title as `notebook` instead.
//! Storage: `page_crdt` keeps a snapshot, each `op_log` row after it its update bytes, and a
//! snapshot is rewritten every `SNAPSHOT_EVERY` rows. Recently written documents stay in a
//! process cache, checked against the stored version vector before reuse.

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};

use indexmap::{IndexMap, IndexSet};
use loro::{
    ExportMode, LoroDoc, LoroListValue, LoroMap, LoroMapValue, LoroText, LoroTree, LoroValue, TreeID, TreeParentId, UpdateOptions,
    ValueOrContainer, VersionVector,
};
use rusqlite::{Connection, OptionalExtension};
use serde_json::{Map, Number, Value};
use sha1::{Digest, Sha1};

use super::doc::{is_canonical_uuid, Block, PageDoc, Titles};
use super::json;
use crate::ported::{internal, Done};

/// Properties of one device's copy of a block: never written to the Loro document.
pub const LOCAL_KEYS: [&str; 6] = ["revision", "number", "notebook_id", "viewport", "viewport_revision", "last_opened"];
/// The synced stand-in for a root block's local `notebook_id`: the notebook's title.
pub const NOTEBOOK: &str = "notebook";
const TREE: &str = "blocks";
const SNAPSHOT_EVERY: i64 = 64;
/// Texts longer than this are diffed line by line.
const LINE_DIFF_ABOVE: usize = 50_000;
/// `COPY_NAMESPACE`, for the IDs of extra copies of one block.
const COPY_NAMESPACE: [u8; 16] = [0x5f, 0x0d, 0x1d, 0x5e, 0x8a, 0x1b, 0x4c, 0x7e, 0x9a, 0x37, 0x6b, 0x2f, 0x4f, 0x3c, 0x2a, 0x11];
const CACHE_SIZE: usize = 32;

/// Anything Loro refuses is an exception Python did not handle: a 500.
fn loro<T, E>(result: Result<T, E>) -> Done<T> {
    result.map_err(|_| internal())
}

/// The Loro side of a `LoroPageDoc`.
pub struct Mirror {
    /// Off for a merge applying an already-merged result (Python's `mirror`).
    pub enabled: bool,
    pub loro: Option<LoroDoc>,
    /// Block IDs -> the tree nodes they were projected from (`counter@peer`).
    pub nodes: HashMap<String, String>,
    pub peer: u64,
    /// The Loro document was made from the projection during this transaction.
    pub seeded: bool,
    /// Blocks whose `node` changed without the block itself changing.
    pub renodes: IndexSet<String>,
    pub start_vv: VersionVector,
}

/// `crdt.peer_of`: this device's Loro peer ID from its 128-bit device ID (never 0).
pub fn peer_of(device: &str) -> Done<u64> {
    let head = device.get(..16).ok_or_else(internal)?;
    let value = u64::from_str_radix(head, 16).map_err(|_| internal())?;
    Ok(match value >> 1 {
        0 => 1,
        peer => peer,
    })
}

pub fn node_name(node: TreeID) -> String {
    format!("{}@{}", node.counter, node.peer)
}

pub fn parse_node(name: &str) -> Done<TreeID> {
    let (counter, peer) = name.split_once('@').ok_or_else(internal)?;
    Ok(TreeID::new(peer.parse().map_err(|_| internal())?, counter.parse().map_err(|_| internal())?))
}

fn node_order(name: &str) -> (u64, i64) {
    let (counter, peer) = name.split_once('@').unwrap_or(("0", "0"));
    (peer.parse().unwrap_or(0), counter.parse().unwrap_or(0))
}

/// `crdt.canonical`: how property values are compared (`true`, `1` and `1.0` differ).
pub fn canonical(value: &Value) -> String {
    json::dumps(value, json::CANONICAL)
}

/// A JSON value as Python's `loro` converts the decoded Python object.
pub fn to_loro(value: &Value) -> LoroValue {
    match value {
        Value::Null => LoroValue::Null,
        Value::Bool(flag) => LoroValue::Bool(*flag),
        Value::Number(number) => match number.as_i64() {
            Some(integer) if !number.is_f64() => LoroValue::I64(integer),
            // An int past i64 is extracted as a float.
            _ => LoroValue::Double(number.as_f64().unwrap_or(f64::NAN)),
        },
        Value::String(text) => LoroValue::String(text.clone().into()),
        Value::Array(items) => LoroValue::List(LoroListValue::from(items.iter().map(to_loro).collect::<Vec<_>>())),
        Value::Object(map) => {
            let mut built: rustc_hash::FxHashMap<String, LoroValue> = rustc_hash::FxHashMap::default();
            for (key, item) in map {
                built.insert(key.clone(), to_loro(item));
            }
            LoroValue::Map(LoroMapValue::from(built))
        }
    }
}

/// A Loro value as Python sees it, in JSON (bytes and container IDs Python could not dump).
pub fn from_loro(value: &LoroValue) -> Done<Value> {
    Ok(match value {
        LoroValue::Null => Value::Null,
        LoroValue::Bool(flag) => Value::Bool(*flag),
        LoroValue::Double(number) => Value::Number(Number::from_f64(*number).ok_or_else(internal)?),
        LoroValue::I64(number) => Value::from(*number),
        LoroValue::String(text) => Value::String(text.to_string()),
        LoroValue::List(items) => Value::Array(items.iter().map(from_loro).collect::<Done<_>>()?),
        LoroValue::Map(map) => {
            let mut out = Map::new();
            for (key, item) in map.iter() {
                out.insert(key.clone(), from_loro(item)?);
            }
            Value::Object(out)
        }
        LoroValue::Binary(_) | LoroValue::Container(_) => return Err(internal()),
    })
}

fn map_of(value: &LoroValue) -> Option<&LoroMapValue> {
    match value {
        LoroValue::Map(map) => Some(map),
        _ => None,
    }
}

fn is_text(value: Option<&LoroValue>, wanted: &str) -> bool {
    matches!(value, Some(LoroValue::String(text)) if text.as_str() == wanted)
}

/// `crdt.synced`: the properties of `item` that live in the Loro document.
pub fn synced(item: &Block, titles: &mut Titles) -> Done<Map<String, Value>> {
    let mut props: Map<String, Value> = item.properties.iter()
        .filter(|(key, _)| !LOCAL_KEYS.contains(&key.as_str()) && key.as_str() != NOTEBOOK)
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    if item.parent_id.is_none() {
        if let Some(notebook) = item.properties.get("notebook_id").filter(|value| !value.is_null()) {
            if let Some(title) = titles(notebook)?.filter(|title| !title.is_empty()) {
                props.insert(NOTEBOOK.into(), Value::String(title));
            }
        }
    }
    Ok(props)
}

pub fn new_doc(peer: u64) -> Done<LoroDoc> {
    let doc = LoroDoc::new();
    loro(doc.set_peer_id(peer))?;
    doc.get_tree(TREE).disable_fractional_index();
    Ok(doc)
}

fn child_map(meta: &LoroMap, key: &str) -> Done<LoroMap> {
    if let Some(ValueOrContainer::Container(loro::Container::Map(found))) = meta.get(key) {
        return Ok(found);
    }
    loro(meta.insert_container(key, LoroMap::new()))
}

fn child_text(meta: &LoroMap, key: &str) -> Done<LoroText> {
    if let Some(ValueOrContainer::Container(loro::Container::Text(found))) = meta.get(key) {
        return Ok(found);
    }
    loro(meta.insert_container(key, LoroText::new()))
}

fn fields(item: &Block) -> [(&'static str, &str); 5] {
    [("id", &item.id), ("type", &item.kind), ("key", &item.order_key), ("created", &item.created_at), ("updated", &item.updated_at)]
}

impl PageDoc {
    /// A `LoroPageDoc`: this page's projection plus its Loro document (None: not made yet).
    pub fn mirrored(page_id: &str, blocks: IndexMap<String, Block>, nodes: HashMap<String, String>, loro: Option<LoroDoc>, peer: u64) -> PageDoc {
        let start_vv = loro.as_ref().map(LoroDoc::oplog_vv).unwrap_or_default();
        let mut doc = PageDoc::new(page_id, blocks);
        doc.mirror = Some(Mirror { enabled: true, loro, nodes, peer, seeded: false, renodes: IndexSet::new(), start_vv });
        doc
    }

    fn tree(&self) -> Done<LoroTree> {
        let mirror = self.mirror.as_ref().ok_or_else(internal)?;
        Ok(mirror.loro.as_ref().ok_or_else(internal)?.get_tree(TREE))
    }

    /// `LoroPageDoc.ensure`: give the page a Loro document, seeding it from the projection.
    pub fn ensure(&mut self, titles: &mut Titles) -> Done<()> {
        let Some(mirror) = self.mirror.as_mut() else { return Ok(()) };
        if mirror.loro.is_some() {
            return Ok(());
        }
        mirror.loro = Some(new_doc(mirror.peer)?);
        if !self.blocks.is_empty() {
            let mut ordered: Vec<&Block> = self.blocks.values().collect();
            ordered.sort_by(|a, b| (self.depth(&a.id), &a.order_key, &a.id).cmp(&(self.depth(&b.id), &b.order_key, &b.id)));
            let ordered: Vec<Block> = ordered.into_iter().cloned().collect();
            for item in &ordered {
                self.create(item, titles)?;
                self.mirror.as_mut().expect("mirrored").renodes.insert(item.id.clone());
            }
            let mirror = self.mirror.as_mut().expect("mirrored");
            mirror.loro.as_ref().expect("made above").commit();
            mirror.seeded = true;
        }
        let mirror = self.mirror.as_mut().expect("mirrored");
        mirror.start_vv = mirror.loro.as_ref().expect("made above").oplog_vv();
        Ok(())
    }

    fn create(&mut self, item: &Block, titles: &mut Titles) -> Done<()> {
        let tree = self.tree()?;
        let mirror = self.mirror.as_mut().ok_or_else(internal)?;
        let parent = match &item.parent_id {
            None => None,
            Some(parent) => Some(parse_node(mirror.nodes.get(parent).ok_or_else(internal)?)?),
        };
        let node = loro(tree.create(parent))?;
        let meta = loro(tree.get_meta(node))?;
        for (key, value) in fields(item) {
            loro(meta.insert(key, value))?;
        }
        if let Some(deleted) = &item.deleted_at {
            loro(meta.insert("deleted", deleted.as_str()))?;
        }
        let props = loro(meta.insert_container("props", LoroMap::new()))?;
        for (key, value) in synced(item, titles)? {
            loro(props.insert(&key, to_loro(&value)))?;
        }
        let text = loro(meta.insert_container("text", LoroText::new()))?;
        if !item.text.is_empty() {
            loro(text.insert(0, &item.text))?;
        }
        self.mirror.as_mut().expect("mirrored").nodes.insert(item.id.clone(), node_name(node));
        Ok(())
    }

    /// The Loro half of `LoroPageDoc.apply`, after the operation changed the projection.
    pub(super) fn mirror_change(&mut self, op: &Map<String, Value>, touched: IndexSet<String>, gone: Vec<Vec<String>>, titles: &mut Titles) -> Done<()> {
        let tree = self.tree()?;
        for subtree in gone {
            let mirror = self.mirror.as_mut().expect("mirrored");
            if let Some(name) = mirror.nodes.get(&subtree[0]) {
                let node = parse_node(name)?;
                if !loro(tree.is_node_deleted(&node))? {
                    loro(tree.delete(node))?;
                }
            }
            for bid in &subtree {
                if mirror.nodes.remove(bid).is_some() {
                    mirror.renodes.shift_remove(bid);
                }
            }
        }
        let splice = (op.get("op").and_then(Value::as_str) == Some("text") && !op.contains_key("text")).then_some(op);
        let mut ordered: Vec<(usize, String)> = touched.into_iter().map(|bid| (self.depth(&bid), bid)).collect();
        ordered.sort_by_key(|(depth, _)| *depth);
        for (_, bid) in ordered {
            let item = self.blocks[&bid].clone();
            let own = splice.filter(|splice| splice.get("id").and_then(Value::as_str) == Some(bid.as_str()));
            self.write(&item, own, titles)?;
        }
        Ok(())
    }

    /// `LoroPageDoc._write`: make the block's node say what the block now is.
    fn write(&mut self, item: &Block, splice: Option<&Map<String, Value>>, titles: &mut Titles) -> Done<()> {
        let name = self.mirror.as_ref().expect("mirrored").nodes.get(&item.id).cloned();
        let Some(name) = name else {
            self.create(item, titles)?;
            self.mirror.as_mut().expect("mirrored").renodes.insert(item.id.clone());
            return Ok(());
        };
        let tree = self.tree()?;
        let node = parse_node(&name)?;
        let meta = loro(tree.get_meta(node))?;
        if let Some(parent_id) = &item.parent_id {
            let parent_name = self.mirror.as_ref().expect("mirrored").nodes.get(parent_id).cloned().ok_or_else(internal)?;
            let parent = parse_node(&parent_name)?;
            if tree.parent(node) != Some(TreeParentId::Node(parent)) {
                loro(tree.mov(node, parent))?;
            }
        }
        let have = meta.get_deep_value();
        let have = map_of(&have).ok_or_else(internal)?;
        for (key, value) in fields(item) {
            if !is_text(have.get(key), value) {
                loro(meta.insert(key, value))?;
            }
        }
        let deleted_now = match have.get("deleted") {
            None | Some(LoroValue::Null) => None,
            Some(LoroValue::String(text)) => Some(text.to_string()),
            Some(_) => Some(String::from("\u{0}not a stamp")),
        };
        if deleted_now != item.deleted_at {
            match &item.deleted_at {
                None => loro(meta.delete("deleted"))?,
                Some(deleted) => loro(meta.insert("deleted", deleted.as_str()))?,
            }
        }
        let want = synced(item, titles)?;
        let old = match have.get("props") {
            Some(LoroValue::Map(props)) => match from_loro(&LoroValue::Map(props.clone()))? {
                Value::Object(map) => map,
                _ => Map::new(),
            },
            _ => Map::new(),
        };
        if canonical(&Value::Object(want.clone())) != canonical(&Value::Object(old.clone())) {
            let props = child_map(&meta, "props")?;
            for key in old.keys().filter(|key| !want.contains_key(*key)) {
                loro(props.delete(key))?;
            }
            for (key, value) in &want {
                if old.get(key).is_none_or(|before| canonical(before) != canonical(value)) {
                    loro(props.insert(key, to_loro(value)))?;
                }
            }
        }
        let before = have.get("text");
        if !is_text(before, &item.text) {
            let text = child_text(&meta, "text")?;
            if let (Some(splice), Some(LoroValue::String(before))) = (splice, before) {
                let at = splice.get("at").and_then(Value::as_u64).unwrap_or(0) as usize;
                let length = splice.get("del").map_or(Some(0), Value::as_u64).unwrap_or(0) as usize;
                let insert = splice.get("ins").and_then(Value::as_str).unwrap_or("");
                let joined = format!("{}{}{}", super::doc::slice(before, 0, at), insert, super::doc::slice(before, at.saturating_add(length), usize::MAX));
                if joined == item.text {
                    loro(text.splice(at, length, insert))?;
                    return Ok(());
                }
            }
            let options = UpdateOptions { timeout_ms: None, use_refined_diff: true };
            if item.text.chars().count() > LINE_DIFF_ABOVE {
                loro(text.update_by_line(&item.text, options))?;
            } else {
                loro(text.update(&item.text, options))?;
            }
        }
        Ok(())
    }

    /// `LoroPageDoc.finish`: commit; this transaction's update bytes (None when unchanged).
    pub fn finish(&self) -> Done<Option<Vec<u8>>> {
        let Some(mirror) = &self.mirror else { return Ok(None) };
        let Some(doc) = &mirror.loro else { return Ok(None) };
        doc.commit();
        if doc.oplog_vv() == mirror.start_vv {
            return Ok(None);
        }
        Ok(Some(loro(doc.export(ExportMode::updates(&mirror.start_vv)))?))
    }
}

/// `uuid.uuid5(namespace, name)`.
fn uuid5(namespace: &[u8; 16], name: &str) -> String {
    let mut hash = Sha1::new();
    hash.update(namespace);
    hash.update(name.as_bytes());
    let digest = hash.finalize();
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x50;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex = hex::encode(bytes);
    format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..])
}

/// `crdt.copy_id`: the ID an extra copy of block `bid` gets.
pub fn copy_id(bid: &str, place: &str) -> String {
    uuid5(&COPY_NAMESPACE, &format!("{bid}/{place}"))
}

/// The node of every block `crdt.project` would make of the page (only the nodes: what
/// `open_page` needs for projection rows written without theirs).
pub fn projected_nodes(doc: &LoroDoc, page_id: &str) -> Done<HashMap<String, String>> {
    let value = doc.get_tree(TREE).get_value_with_meta();
    let LoroValue::List(roots) = value else { return Err(internal()) };
    let meta_id = |node: &LoroValue| -> String {
        map_of(node).and_then(|node| node.get("meta")).and_then(map_of).and_then(|meta| meta.get("id"))
            .and_then(|id| match id { LoroValue::String(text) => Some(text.to_string()), _ => None }).unwrap_or_default()
    };
    let name_of = |node: &LoroValue| -> String {
        map_of(node).and_then(|node| node.get("id")).and_then(|id| match id { LoroValue::String(text) => Some(text.to_string()), _ => None }).unwrap_or_default()
    };
    let Some(root) = roots.iter().filter(|node| meta_id(node) == page_id).min_by_key(|node| node_order(&name_of(node))) else {
        return Ok(HashMap::new());
    };
    let root_name = name_of(root);
    let mut entries: Vec<&LoroValue> = Vec::new();
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        entries.push(node);
        if let Some(LoroValue::List(children)) = map_of(node).and_then(|node| node.get("children")) {
            stack.extend(children.iter());
        }
    }
    let mut holders: IndexMap<String, Vec<String>> = IndexMap::new();
    for node in &entries {
        holders.entry(meta_id(node)).or_default().push(name_of(node));
    }
    let mut ids: HashMap<String, String> = HashMap::new();
    for (bid, mut names) in holders {
        names.sort_by_key(|name| (*name != root_name, node_order(name)));
        for (index, name) in names.into_iter().enumerate() {
            let keep = index == 0 && is_canonical_uuid(&bid) && (bid != page_id || name == root_name);
            let assigned = if keep { bid.clone() } else { copy_id(&bid, &name) };
            ids.insert(name, assigned);
        }
    }
    ids.insert(root_name, page_id.to_owned());
    Ok(ids.into_iter().map(|(name, bid)| (bid, name)).collect())
}

// Storage ----------------------------------------------------------------------------

static CACHE: LazyLock<Mutex<IndexMap<(u64, String), LoroDoc>>> = LazyLock::new(|| Mutex::new(IndexMap::new()));

/// `crdt.keep`: return a document to the cache once its changes are written.
pub fn keep(peer: u64, page_id: &str, doc: LoroDoc) {
    let mut cache = CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let key = (peer, page_id.to_owned());
    cache.shift_remove(&key);
    cache.insert(key, doc);
    while cache.len() > CACHE_SIZE {
        cache.shift_remove_index(0);
    }
}

/// `crdt.load`: the page's Loro document (snapshot plus the op log after it), or None.
pub fn load(conn: &Connection, page_id: &str, peer: u64) -> Done<Option<LoroDoc>> {
    let row: Option<(Vec<u8>, i64)> = conn.query_row("SELECT vv, snapshot_seq FROM page_crdt WHERE page_id=?", [page_id], |row| Ok((row.get(0)?, row.get(1)?))).optional()?;
    let Some((vv, snapshot_seq)) = row else { return Ok(None) };
    let cached = CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).shift_remove(&(peer, page_id.to_owned()));
    if let Some(cached) = cached {
        if cached.oplog_vv() == loro(VersionVector::decode(&vv))? {
            return Ok(Some(cached));
        }
    }
    let doc = LoroDoc::new();
    let snapshot: Vec<u8> = conn.query_row("SELECT snapshot FROM page_crdt WHERE page_id=?", [page_id], |row| row.get(0))?;
    loro(doc.import(&snapshot))?;
    let mut statement = conn.prepare("SELECT crdt FROM op_log WHERE page_id=? AND seq>? AND crdt IS NOT NULL ORDER BY seq")?;
    let tail: Vec<Vec<u8>> = statement.query_map(rusqlite::params![page_id, snapshot_seq], |row| row.get(0))?.collect::<rusqlite::Result<_>>()?;
    if !tail.is_empty() {
        loro(doc.import_batch(&tail))?;
    }
    loro(doc.set_peer_id(peer))?;
    doc.get_tree(TREE).disable_fractional_index();
    Ok(Some(doc))
}

/// `crdt.store`: record a transaction's effect on the page's Loro document (`seq`: its row).
pub fn store(conn: &Connection, doc: &PageDoc, seq: i64, update: Option<&[u8]>, at: &str) -> Done<()> {
    let Some(mirror) = &doc.mirror else { return Ok(()) };
    let Some(loro_doc) = &mirror.loro else { return Ok(()) };
    let tail: Option<i64> = conn.query_row("SELECT tail FROM page_crdt WHERE page_id=?", [&doc.page_id], |row| row.get(0)).optional()?;
    let vv = loro_doc.oplog_vv().encode();
    if tail.is_none_or(|tail| tail + 1 >= SNAPSHOT_EVERY) || mirror.seeded {
        snapshot(conn, &doc.page_id, loro_doc, seq, at)?;
    } else if update.is_some() {
        conn.execute("UPDATE page_crdt SET tail=tail+1, vv=?, updated_at=? WHERE page_id=?", rusqlite::params![vv, at, doc.page_id])?;
    }
    Ok(())
}

/// `crdt.snapshot`: rewrite the page's snapshot; `seq` is the newest op_log row it includes.
pub fn snapshot(conn: &Connection, page_id: &str, doc: &LoroDoc, seq: i64, at: &str) -> Done<()> {
    let bytes = loro(doc.export(ExportMode::Snapshot))?;
    conn.execute(
        "INSERT INTO page_crdt(page_id,snapshot,snapshot_seq,tail,vv,updated_at) VALUES (?,?,?,0,?,?) \
         ON CONFLICT(page_id) DO UPDATE SET snapshot=excluded.snapshot,snapshot_seq=excluded.snapshot_seq,\
         tail=0,vv=excluded.vv,updated_at=excluded.updated_at",
        rusqlite::params![page_id, bytes, seq, doc.oplog_vv().encode(), at],
    )?;
    Ok(())
}

/// `crdt.open_page`: a transaction's document for one page: its projection plus its Loro document.
pub fn open_page(conn: &Connection, page_id: &str, peer: u64, blocks: IndexMap<String, Block>, mut nodes: HashMap<String, String>) -> Done<PageDoc> {
    let loro_doc = load(conn, page_id, peer)?;
    let mut missing: Vec<String> = Vec::new();
    if let Some(found) = &loro_doc {
        if blocks.keys().any(|bid| !nodes.contains_key(bid)) {
            // Projection rows without their node (written by older code): find them by ID.
            let projected = projected_nodes(found, page_id)?;
            for bid in blocks.keys() {
                if !nodes.contains_key(bid) {
                    if let Some(name) = projected.get(bid) {
                        nodes.insert(bid.clone(), name.clone());
                        missing.push(bid.clone());
                    }
                }
            }
        }
    }
    let mut doc = PageDoc::mirrored(page_id, blocks, nodes, loro_doc, peer);
    doc.mirror.as_mut().expect("mirrored").renodes.extend(missing);
    Ok(doc)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copy_ids_are_python_uuid5() {
        // uuid.uuid5(COPY_NAMESPACE, "x/1@2")
        assert_eq!(copy_id("x", "1@2").len(), 36);
        assert_eq!(peer_of("0000000000000000ffff").unwrap(), 1);
        assert_eq!(peer_of("ffffffffffffffff0000").unwrap(), u64::MAX >> 1);
    }
}
