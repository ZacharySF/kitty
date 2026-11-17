//! One page's blocks in memory and the operations that change them
//! (`kitty.core.blocks.pagedoc`: `OrderedPageDoc` and the operation vocabulary).
//!
//! A document with a [`Mirror`](super::crdt::Mirror) is a `LoroPageDoc`: every operation it
//! applies is mirrored into the page's Loro document (crate::blocks::crdt). Python's
//! dict-ordered blocks are an `IndexMap`; the sets Python iterates in hash order (`changed`,
//! `removed`, each block's children) keep their insertion or sorted order here, which is one
//! of the orders Python can produce.

use std::collections::{BTreeSet, HashMap};

use indexmap::{IndexMap, IndexSet};
use serde_json::{Map, Value};

use super::crdt::Mirror;
use super::json;
use super::order;
use crate::ported::{fail, internal, Done};

/// Properties that record how a page is viewed, not what it contains.
pub const VIEW_KEYS: [&str; 5] = ["viewport", "viewport_revision", "favorite", "last_opened", "position"];
pub const TAG_STATES: [&str; 4] = ["manual", "auto", "accepted", "rejected"];
pub const MAX_PAGE_BLOCKS: usize = 10_000;
pub const MAX_DEPTH: usize = 64;
pub const MAX_TEXT_BYTES: usize = 1024 * 1024;
pub const MAX_PROPERTY_BYTES: usize = 2 * 1024 * 1024;
/// `convert.OUTLINE_TYPES`: the block editor's text-flow types.
pub const OUTLINE_TYPES: [&str; 13] = [
    "text", "heading_1", "heading_2", "heading_3", "to_do", "bulleted_list", "numbered_list", "quote", "callout", "code",
    "divider", "toggle", "page_link",
];

pub fn is_outline(kind: &str) -> bool {
    OUTLINE_TYPES.contains(&kind)
}

/// `TAG_KEY.match(key)`: `tag_` and the tag ID's 32 lowercase hex digits.
pub fn is_tag_key(key: &str) -> bool {
    key.len() == 36 && key.starts_with("tag_") && key[4..].bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// `pagedoc.is_edit_key`.
pub fn is_edit_key(key: &str) -> bool {
    !VIEW_KEYS.contains(&key) && !is_tag_key(key)
}

/// `BlockError(message, status)`.
pub fn refuse(message: impl Into<String>) -> crate::ported::Fault {
    fail(400, message)
}

#[derive(Debug, Clone, PartialEq)]
pub struct Block {
    pub id: String,
    pub kind: String,
    pub parent_id: Option<String>,
    pub page_id: String,
    pub order_key: String,
    pub properties: Map<String, Value>,
    pub text: String,
    pub created_at: String,
    pub updated_at: String,
    pub deleted_at: Option<String>,
}

impl Block {
    pub fn live(&self) -> bool {
        self.deleted_at.is_none()
    }

    /// `dataclasses.asdict(block)`.
    pub fn as_dict(&self) -> Map<String, Value> {
        let mut out = Map::new();
        out.insert("id".into(), self.id.clone().into());
        out.insert("type".into(), self.kind.clone().into());
        out.insert("parent_id".into(), self.parent_id.clone().map_or(Value::Null, Value::String));
        out.insert("page_id".into(), self.page_id.clone().into());
        out.insert("order_key".into(), self.order_key.clone().into());
        out.insert("properties".into(), Value::Object(self.properties.clone()));
        out.insert("text".into(), self.text.clone().into());
        out.insert("created_at".into(), self.created_at.clone().into());
        out.insert("updated_at".into(), self.updated_at.clone().into());
        out.insert("deleted_at".into(), self.deleted_at.clone().map_or(Value::Null, Value::String));
        out
    }

    /// `Block(**row)` for a row as `asdict` wrote it (a snapshot's or an adopt's), or None when
    /// a field is missing, unexpected or of a type Python's dataclass could not hold here.
    pub fn from_dict(row: &Map<String, Value>, page_id: Option<&str>) -> Option<Block> {
        const FIELDS: [&str; 10] = ["id", "type", "parent_id", "page_id", "order_key", "properties", "text", "created_at", "updated_at", "deleted_at"];
        if row.keys().any(|key| !FIELDS.contains(&key.as_str())) {
            return None;
        }
        let text = |key: &str, default: &str| -> Option<String> {
            match row.get(key) {
                None => Some(default.to_owned()),
                Some(Value::String(value)) => Some(value.clone()),
                Some(_) => None,
            }
        };
        let optional = |key: &str| -> Option<Option<String>> {
            match row.get(key) {
                None | Some(Value::Null) => Some(None),
                Some(Value::String(value)) => Some(Some(value.clone())),
                Some(_) => None,
            }
        };
        let page = match page_id {
            Some(page) => page.to_owned(),
            None => row.get("page_id")?.as_str()?.to_owned(),
        };
        Some(Block {
            id: row.get("id")?.as_str()?.to_owned(),
            kind: row.get("type")?.as_str()?.to_owned(),
            parent_id: match row.get("parent_id")? {
                Value::Null => None,
                Value::String(value) => Some(value.clone()),
                _ => return None,
            },
            page_id: page,
            order_key: row.get("order_key")?.as_str()?.to_owned(),
            properties: match row.get("properties") {
                None => Map::new(),
                Some(Value::Object(map)) => map.clone(),
                Some(_) => return None,
            },
            text: text("text", "")?,
            created_at: text("created_at", "")?,
            updated_at: text("updated_at", "")?,
            deleted_at: optional("deleted_at")?,
        })
    }
}

/// `pagedoc.block_id`: a canonical lowercase UUID.
pub fn block_id(value: Option<&Value>) -> Done<String> {
    match value {
        Some(Value::String(text)) if is_canonical_uuid(text) => Ok(text.clone()),
        _ => Err(refuse("Block IDs are canonical lowercase UUIDs")),
    }
}

/// Whether `str(uuid.UUID(text)) == text`.
pub fn is_canonical_uuid(text: &str) -> bool {
    let bytes = text.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(index, &b)| match index {
            8 | 13 | 18 | 23 => b == b'-',
            _ => matches!(b, b'0'..=b'9' | b'a'..=b'f'),
        })
}

fn key(value: Option<&Value>) -> Done<String> {
    match value {
        Some(Value::String(text)) if order::validate(text).is_ok() => Ok(text.clone()),
        _ => Err(refuse("Invalid order key")),
    }
}

fn is_type(text: &str) -> bool {
    let bytes = text.as_bytes();
    !bytes.is_empty() && bytes.len() <= 32 && bytes[0].is_ascii_lowercase() && bytes[1..].iter().all(|&b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

fn kind(value: Option<&Value>) -> Done<String> {
    match value {
        Some(Value::String(text)) if is_type(text) => Ok(text.clone()),
        _ => Err(refuse("Block type must be a short lowercase name")),
    }
}

fn is_property_key(text: &str) -> bool {
    let bytes = text.as_bytes();
    !bytes.is_empty() && bytes.len() <= 64 && (bytes[0].is_ascii_alphabetic() || bytes[0] == b'_')
        && bytes[1..].iter().all(|&b| b.is_ascii_alphanumeric() || b == b'_')
}

/// `pagedoc._props` (a missing value is `{}`).
pub fn props(value: Option<&Value>) -> Done<Map<String, Value>> {
    let map = match value {
        None => return Ok(Map::new()),
        Some(Value::Object(map)) if map.keys().all(|key| is_property_key(key)) => map,
        Some(_) => return Err(refuse("Block properties must be an object with simple keys")),
    };
    let bad_state = |item: &Value| match item {
        Value::Null => false,
        Value::String(state) => !TAG_STATES.contains(&state.as_str()),
        _ => true,
    };
    if map.iter().any(|(key, item)| is_tag_key(key) && bad_state(item)) {
        return Err(refuse("A tag state is manual, auto, accepted or rejected"));
    }
    Ok(map.clone())
}

/// `pagedoc._text` (a missing value is `""`).
pub fn text(value: Option<&Value>) -> Done<String> {
    let text = match value {
        None => return Ok(String::new()),
        Some(Value::String(text)) if !text.contains('\0') => text,
        Some(_) => return Err(refuse("Block text must be a string without NUL")),
    };
    if text.len() > MAX_TEXT_BYTES {
        return Err(refuse("Block text is limited to 1 MiB"));
    }
    Ok(text.clone())
}

fn stamp(value: Option<&Value>, default: &str) -> Done<String> {
    match value {
        None | Some(Value::Null) => Ok(default.to_owned()),
        Some(Value::String(text)) if (1..=64).contains(&text.chars().count()) => Ok(text.clone()),
        Some(_) => Err(refuse("Timestamps must be ISO strings")),
    }
}

/// `type(value) is int` and its value (an integer past i64 is out of any range here).
fn integer(value: Option<&Value>) -> Option<Option<i64>> {
    match value {
        Some(Value::Number(number)) if !number.is_f64() => Some(number.as_i64()),
        _ => None,
    }
}

/// `text[start:end]` by code points, clamped as Python slices are.
pub fn slice(text: &str, start: usize, end: usize) -> &str {
    let mut indices = text.char_indices().map(|(at, _)| at).chain(std::iter::once(text.len()));
    let from = indices.by_ref().nth(start).unwrap_or(text.len());
    if end <= start {
        return "";
    }
    let to = indices.nth(end - start - 1).unwrap_or(text.len());
    &text[from..to]
}

/// Something that names a notebook's title for a page's synced properties
/// (`_Transaction.notebook_title`).
pub type Titles<'a> = dyn FnMut(&Value) -> Done<Option<String>> + 'a;

/// One page's blocks (`OrderedPageDoc`, or a `LoroPageDoc` when it has a mirror).
pub struct PageDoc {
    pub page_id: String,
    pub blocks: IndexMap<String, Block>,
    kids: HashMap<Option<String>, BTreeSet<String>>,
    /// Block IDs inserted or updated, and removed, since the doc was loaded.
    pub changed: IndexSet<String>,
    pub removed: IndexSet<String>,
    touched: Option<IndexSet<String>>,
    gone: Vec<Vec<String>>,
    pub mirror: Option<Mirror>,
}

impl PageDoc {
    pub fn new(page_id: &str, blocks: IndexMap<String, Block>) -> PageDoc {
        let mut kids: HashMap<Option<String>, BTreeSet<String>> = HashMap::new();
        for item in blocks.values() {
            kids.entry(item.parent_id.clone()).or_default().insert(item.id.clone());
        }
        PageDoc {
            page_id: page_id.to_owned(), blocks, kids, changed: IndexSet::new(), removed: IndexSet::new(), touched: None,
            gone: Vec::new(), mirror: None,
        }
    }

    pub fn root(&self) -> Option<&Block> {
        self.blocks.get(&self.page_id)
    }

    pub fn get(&self, bid: &str) -> Done<&Block> {
        self.blocks.get(bid).ok_or_else(|| fail(404, "Block not found on this page"))
    }

    /// Live (or all) children of `parent_id`, sorted by (order key, ID).
    pub fn children(&self, parent_id: Option<&str>, deleted: bool) -> Vec<&Block> {
        let mut found: Vec<&Block> = match self.kids.get(&parent_id.map(str::to_owned)) {
            Some(ids) => ids.iter().filter_map(|bid| self.blocks.get(bid)).filter(|item| deleted || item.live()).collect(),
            None => Vec::new(),
        };
        found.sort_by(|a, b| (&a.order_key, &a.id).cmp(&(&b.order_key, &b.id)));
        found
    }

    /// Every block below `bid` (any order Python's stack walk can give).
    pub fn descendants(&self, bid: &str) -> Vec<String> {
        let mut out = Vec::new();
        let mut stack: Vec<String> = self.kids.get(&Some(bid.to_owned())).map(|ids| ids.iter().cloned().collect()).unwrap_or_default();
        while let Some(current) = stack.pop() {
            if let Some(ids) = self.kids.get(&Some(current.clone())) {
                stack.extend(ids.iter().cloned());
            }
            out.push(current);
        }
        out
    }

    pub fn depth(&self, bid: &str) -> usize {
        let mut level = 0;
        let mut current = self.blocks.get(bid);
        while let Some(item) = current {
            let Some(parent) = &item.parent_id else { break };
            level += 1;
            current = self.blocks.get(parent);
        }
        level
    }

    // Writing ---------------------------------------------------------------------------

    fn touch(&mut self, bid: &str) {
        self.changed.insert(bid.to_owned());
        self.removed.shift_remove(bid);
        if let Some(touched) = &mut self.touched {
            touched.insert(bid.to_owned());
        }
    }

    fn forget(&mut self, subtree: &[String]) {
        for bid in subtree {
            self.changed.shift_remove(bid);
            self.removed.insert(bid.clone());
        }
        if let Some(touched) = &mut self.touched {
            self.gone.push(subtree.to_vec());
            for bid in subtree {
                touched.shift_remove(bid);
            }
        }
    }

    fn attach(&mut self, item: Block) {
        let bid = item.id.clone();
        self.kids.entry(item.parent_id.clone()).or_default().insert(bid.clone());
        self.blocks.insert(bid.clone(), item);
        self.touch(&bid);
    }

    /// Remove `bid` and its subtree; returns their IDs, parents first.
    fn detach(&mut self, bid: &str) -> Vec<String> {
        let parent = self.blocks[bid].parent_id.clone();
        let mut subtree = vec![bid.to_owned()];
        subtree.extend(self.descendants(bid));
        if let Some(ids) = self.kids.get_mut(&parent) {
            ids.remove(bid);
        }
        for item in &subtree {
            self.blocks.shift_remove(item);
            self.kids.remove(&Some(item.clone()));
        }
        self.forget(&subtree);
        subtree
    }

    fn live_parent(&self, value: Option<&Value>) -> Done<String> {
        let bid = block_id(value)?;
        let parent = self.blocks.get(&bid).ok_or_else(|| fail(404, "Parent block not found on this page"))?;
        if !parent.live() {
            return Err(fail(409, "Parent block is in Trash; restore it first"));
        }
        Ok(bid)
    }

    /// Apply one operation; mirrored into the Loro document when the doc has one.
    pub fn apply(&mut self, op: &Map<String, Value>, at: &str, titles: &mut Titles) -> Done<()> {
        if self.mirror.as_ref().is_none_or(|mirror| !mirror.enabled) {
            return self.apply_ordered(op, at);
        }
        self.ensure(titles)?;
        self.touched = Some(IndexSet::new());
        self.gone.clear();
        let applied = self.apply_ordered(op, at);
        let touched = self.touched.take().unwrap_or_default();
        let gone = std::mem::take(&mut self.gone);
        applied?;
        self.mirror_change(op, touched, gone, titles)
    }

    /// `OrderedPageDoc.apply`: the operation on the projection only.
    pub fn apply_ordered(&mut self, op: &Map<String, Value>, at: &str) -> Done<()> {
        match op.get("op").and_then(Value::as_str) {
            Some("insert") => self.insert(op, at),
            Some("set") => self.set(op, at),
            Some("text") => self.text(op, at),
            Some("move") => self.mv(op, at),
            Some("delete") => self.delete(op, at),
            Some("restore") => self.restore(op, at),
            Some("purge") => self.purge(op),
            Some("release") => self.release(op),
            Some("adopt") => self.adopt(op, at),
            Some("put") => self.put(op, at),
            _ => Err(refuse("Unknown block operation")),
        }
    }

    fn insert(&mut self, op: &Map<String, Value>, at: &str) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        if self.blocks.contains_key(&bid) {
            return Err(fail(409, "Block already exists"));
        }
        let (kind, key) = (kind(op.get("type"))?, key(op.get("key"))?);
        let (props, text) = (props(op.get("props"))?, text(op.get("text"))?);
        let parent_id = match op.get("parent") {
            None | Some(Value::Null) => {
                if bid != self.page_id || !self.blocks.is_empty() {
                    return Err(refuse("Only a page's own root block has no parent"));
                }
                None
            }
            parent => {
                let parent = self.live_parent(parent)?;
                if self.depth(&parent) + 1 > MAX_DEPTH {
                    return Err(refuse(format!("Blocks nest at most {MAX_DEPTH} levels")));
                }
                Some(parent)
            }
        };
        if self.blocks.len() >= MAX_PAGE_BLOCKS {
            return Err(refuse(format!("A page holds at most {MAX_PAGE_BLOCKS} blocks")));
        }
        let created = stamp(op.get("created"), at)?;
        let updated = stamp(op.get("updated"), &created)?;
        let page_id = self.page_id.clone();
        self.attach(Block {
            id: bid, kind, parent_id, page_id, order_key: key, properties: props, text, created_at: created, updated_at: updated,
            deleted_at: None,
        });
        Ok(())
    }

    fn set(&mut self, op: &Map<String, Value>, at: &str) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        self.get(&bid)?;
        let props = props(op.get("props"))?;
        let kind = if op.contains_key("type") { Some(kind(op.get("type"))?) } else { None };
        let item = self.blocks.get_mut(&bid).expect("checked above");
        let edited = kind.is_some() || props.keys().any(|key| is_edit_key(key));
        if let Some(kind) = kind {
            item.kind = kind;
        }
        for (key, value) in props {
            if value.is_null() {
                item.properties.shift_remove(&key);
            } else {
                item.properties.insert(key, value);
            }
        }
        if edited {
            item.updated_at = at.to_owned();
        }
        self.touch(&bid);
        Ok(())
    }

    fn text(&mut self, op: &Map<String, Value>, at: &str) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        let current = &self.get(&bid)?.text;
        let replaced = if op.contains_key("text") {
            text(op.get("text"))?
        } else {
            let insert = text(op.get("ins"))?;
            let start = integer(op.get("at"));
            let length = match op.get("del") {
                None => Some(Some(0)),
                value => integer(value),
            };
            let size = current.chars().count() as i64;
            let (Some(Some(start)), Some(Some(length))) = (start, length) else {
                return Err(refuse("Text splice is outside the block's text"));
            };
            if !(0..=size).contains(&start) || !(0..=size - start).contains(&length) {
                return Err(refuse("Text splice is outside the block's text"));
            }
            let (start, length) = (start as usize, length as usize);
            let joined = format!("{}{}{}", slice(current, 0, start), insert, slice(current, start + length, usize::MAX));
            text(Some(&Value::String(joined)))?
        };
        let item = self.blocks.get_mut(&bid).expect("checked above");
        item.text = replaced;
        item.updated_at = at.to_owned();
        self.touch(&bid);
        Ok(())
    }

    fn mv(&mut self, op: &Map<String, Value>, at: &str) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        let item = self.get(&bid)?;
        let Some(old_parent) = item.parent_id.clone() else {
            return Err(refuse("A page's root block cannot be moved"));
        };
        let parent = self.live_parent(op.get("parent"))?;
        let mut cursor = Some(parent.clone());
        while let Some(current) = cursor {
            if current == bid {
                return Err(refuse("Moving a block inside itself would create a cycle"));
            }
            cursor = self.blocks.get(&current).and_then(|above| above.parent_id.clone());
        }
        let own = self.depth(&bid);
        let height = self.descendants(&bid).iter().map(|child| self.depth(child)).max().unwrap_or(own) - own;
        if self.depth(&parent) + 1 + height > MAX_DEPTH {
            return Err(refuse(format!("Blocks nest at most {MAX_DEPTH} levels")));
        }
        if let Some(ids) = self.kids.get_mut(&Some(old_parent)) {
            ids.remove(&bid);
        }
        let key = key(op.get("key"))?;
        let item = self.blocks.get_mut(&bid).expect("checked above");
        item.parent_id = Some(parent.clone());
        item.order_key = key;
        item.updated_at = at.to_owned();
        self.kids.entry(Some(parent)).or_default().insert(bid.clone());
        self.touch(&bid);
        Ok(())
    }

    fn delete(&mut self, op: &Map<String, Value>, at: &str) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        if !self.get(&bid)?.live() {
            return Err(fail(409, "Block is already in Trash"));
        }
        let mut targets = vec![bid.clone()];
        targets.extend(self.descendants(&bid));
        for target in targets {
            let item = self.blocks.get_mut(&target).expect("a block of this page");
            if item.live() {
                item.deleted_at = Some(at.to_owned());
                self.touch(&target);
            }
        }
        Ok(())
    }

    fn restore(&mut self, op: &Map<String, Value>, _at: &str) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        let item = self.get(&bid)?;
        if item.live() {
            return Err(fail(409, "Block is not in Trash"));
        }
        let stamp = item.deleted_at.clone();
        let mut targets = vec![bid.clone()];
        targets.extend(self.descendants(&bid));
        for target in targets {
            let item = self.blocks.get_mut(&target).expect("a block of this page");
            if item.deleted_at == stamp {
                item.deleted_at = None;
                self.touch(&target);
            }
        }
        Ok(())
    }

    fn purge(&mut self, op: &Map<String, Value>) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        if self.blocks.contains_key(&bid) {
            self.detach(&bid);
        }
        Ok(())
    }

    fn release(&mut self, op: &Map<String, Value>) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        if self.get(&bid)?.parent_id.is_none() {
            return Err(refuse("A page's root block cannot be moved"));
        }
        self.detach(&bid);
        Ok(())
    }

    fn adopt(&mut self, op: &Map<String, Value>, at: &str) -> Done<()> {
        let rows = match op.get("blocks") {
            Some(Value::Array(rows)) if !rows.is_empty() => rows,
            _ => return Err(refuse("Adopt needs the moved blocks")),
        };
        let parent = self.live_parent(op.get("parent"))?;
        let mut incoming: Vec<Block> = Vec::new();
        for (index, row) in rows.iter().enumerate() {
            let Value::Object(row) = row else { return Err(refuse("Adopted blocks must be objects")) };
            let mut item = Block::from_dict(row, Some(&self.page_id)).ok_or_else(|| refuse("Adopted blocks have unexpected fields"))?;
            block_id(Some(&Value::String(item.id.clone())))?;
            if self.blocks.contains_key(&item.id) {
                return Err(fail(409, "Block already exists"));
            }
            if index == 0 {
                item.parent_id = Some(parent.clone());
                item.order_key = key(op.get("key"))?;
                item.updated_at = at.to_owned();
            }
            incoming.push(item);
        }
        if self.blocks.len() + incoming.len() > MAX_PAGE_BLOCKS {
            return Err(refuse(format!("A page holds at most {MAX_PAGE_BLOCKS} blocks")));
        }
        let ids: Vec<String> = incoming.iter().map(|item| item.id.clone()).collect();
        for item in incoming {
            self.attach(item);
        }
        if ids.iter().map(|bid| self.depth(bid)).max().unwrap_or(0) > MAX_DEPTH {
            return Err(refuse(format!("Blocks nest at most {MAX_DEPTH} levels")));
        }
        Ok(())
    }

    fn put(&mut self, op: &Map<String, Value>, at: &str) -> Done<()> {
        let bid = block_id(op.get("id"))?;
        let (kind, key, props) = (kind(op.get("type"))?, key(op.get("key"))?, props(op.get("props"))?);
        let parent_id = match op.get("parent") {
            None | Some(Value::Null) => {
                if bid != self.page_id {
                    return Err(refuse("Only a page's own root block has no parent"));
                }
                None
            }
            parent => {
                let parent = block_id(parent)?;
                let mut cursor = Some(parent.clone());
                while let Some(current) = cursor {
                    if current == bid {
                        return Err(refuse("Moving a block inside itself would create a cycle"));
                    }
                    let above = self.blocks.get(&current).ok_or_else(|| fail(404, "Parent block not found on this page"))?;
                    cursor = above.parent_id.clone();
                }
                Some(parent)
            }
        };
        let created = stamp(op.get("created"), at)?;
        let updated = stamp(op.get("updated"), &created)?;
        let deleted = match op.get("deleted") {
            None | Some(Value::Null) => None,
            value => Some(stamp(value, at)?),
        };
        if !self.blocks.contains_key(&bid) {
            let text = text(op.get("text"))?;
            let page_id = self.page_id.clone();
            self.attach(Block {
                id: bid, kind, parent_id, page_id, order_key: key, properties: props, text, created_at: created, updated_at: updated,
                deleted_at: deleted,
            });
            return Ok(());
        }
        let old_parent = self.blocks[&bid].parent_id.clone();
        if old_parent != parent_id {
            if let Some(ids) = self.kids.get_mut(&old_parent) {
                ids.remove(&bid);
            }
            self.kids.entry(parent_id.clone()).or_default().insert(bid.clone());
        }
        let replaced = if op.contains_key("text") { Some(text(op.get("text"))?) } else { None };
        let item = self.blocks.get_mut(&bid).expect("checked above");
        item.parent_id = parent_id;
        if let Some(replaced) = replaced {
            item.text = replaced;
        }
        item.kind = kind;
        item.order_key = key;
        item.properties = props;
        item.created_at = created;
        item.updated_at = updated;
        item.deleted_at = deleted;
        self.touch(&bid);
        Ok(())
    }

    /// Invariants after a whole transaction: live blocks hang under live parents.
    pub fn check(&self) -> Done<()> {
        for bid in &self.changed {
            let item = &self.blocks[bid];
            if item.live() {
                if let Some(parent) = &item.parent_id {
                    // Python's `self.blocks[item.parent_id]` fails (a 500) on a missing parent.
                    if !self.blocks.get(parent).ok_or_else(internal)?.live() {
                        return Err(fail(409, "A block cannot be restored inside a page or block that is in Trash"));
                    }
                }
            }
            if json::dumps_map(&item.properties, json::PLAIN).len() > MAX_PROPERTY_BYTES {
                return Err(refuse("Block properties are limited to 2 MiB"));
            }
        }
        Ok(())
    }

    /// `OrderedPageDoc.decode`: a page snapshot (`page_snapshots.state`).
    pub fn decode(page_id: &str, data: &[u8]) -> Done<PageDoc> {
        let rows: Vec<Value> = serde_json::from_slice(data).map_err(|_| internal())?;
        let mut blocks = IndexMap::new();
        for row in rows {
            let Value::Object(row) = row else { return Err(internal()) };
            let item = Block::from_dict(&row, None).ok_or_else(internal)?;
            blocks.insert(item.id.clone(), item);
        }
        Ok(PageDoc::new(page_id, blocks))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn op(value: Value) -> Map<String, Value> {
        value.as_object().cloned().unwrap()
    }

    #[test]
    fn operations_change_the_projection_as_python_does() {
        let page = "00000000-0000-4000-8000-000000000001";
        let child = "00000000-0000-4000-8000-000000000002";
        let mut doc = PageDoc::new(page, IndexMap::new());
        doc.apply_ordered(&op(json!({"op": "insert", "id": page, "type": "page", "parent": null, "key": "a0", "text": "P"})), "t1").unwrap();
        doc.apply_ordered(&op(json!({"op": "insert", "id": child, "type": "text", "parent": page, "key": "a0", "text": "a😀c"})), "t1").unwrap();
        doc.apply_ordered(&op(json!({"op": "text", "id": child, "at": 1, "del": 1, "ins": "b"})), "t2").unwrap();
        assert_eq!(doc.blocks[child].text, "abc");
        let outside = doc.apply_ordered(&op(json!({"op": "text", "id": child, "at": 2, "del": 2})), "t3").unwrap_err();
        assert_eq!(outside.message, "Text splice is outside the block's text");
        let cycle = doc.apply_ordered(&op(json!({"op": "move", "id": child, "parent": child, "key": "a1"})), "t3").unwrap_err();
        assert_eq!(cycle.message, "Moving a block inside itself would create a cycle");
        doc.apply_ordered(&op(json!({"op": "delete", "id": page})), "t4").unwrap();
        assert!(doc.blocks.values().all(|item| item.deleted_at.as_deref() == Some("t4")));
        assert_eq!(slice("a😀c", 1, 2), "😀");
    }
}
