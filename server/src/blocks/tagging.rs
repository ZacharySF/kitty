//! Auto-tags on the transaction path (`kitty.core.blocks.tagging`) and the keyword matcher
//! they use (`kitty.core.blocks.autotag`).
//!
//! `annotate` runs after a transaction's operations: it scans each changed text block of a
//! note page against the tag vocabulary (one Aho-Corasick pass per block), attaches *always*
//! tags (`tag_<id>: "auto"`), drops automatic attachments whose keyword is gone, and returns
//! *suggest* matches for the editor. `project` runs after the projection rows are written:
//! it rewrites `block_tags` and `tag_rejections` for the changed blocks. The vocabulary is
//! cached per database until `tag_settings.vocabulary` moves. Offsets are code points of the
//! block text, as Python's; `utf16` converts them for the editor.

use std::collections::HashSet;
use std::sync::{Arc, LazyLock, Mutex};

use indexmap::{IndexMap, IndexSet};
use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use super::doc::{is_outline, Block, PageDoc, OUTLINE_TYPES};
use super::store::Transaction;
use super::text::{code_marks, normalize_term, skip_spans, tags as tag_tokens, utf16};
use crate::ported::{internal, Done};

pub const MODES: [&str; 2] = ["suggest", "always"];
const ATTACHED: [&str; 3] = ["manual", "auto", "accepted"];
const MIN_TERM: usize = 3;
const MAX_TERM: usize = 100;
const CACHE_SIZE: usize = 4;

/// `TAGGABLE`: text-flow blocks whose words are matched (code, dividers and page links are not prose).
pub fn is_taggable_type(kind: &str) -> bool {
    is_outline(kind) && !matches!(kind, "code" | "divider" | "page_link")
}

/// The block property holding a block's state for one tag.
pub fn key_of(tag_id: &str) -> String {
    format!("tag_{}", tag_id.replace('-', ""))
}

/// The tag ID a `tag_*` property names.
pub fn tag_of(key: &str) -> Option<String> {
    if !super::doc::is_tag_key(key) {
        return None;
    }
    let raw = &key[4..];
    Some(format!("{}-{}-{}-{}-{}", &raw[..8], &raw[8..12], &raw[12..16], &raw[16..20], &raw[20..]))
}

/// A block's tag states, by tag ID.
pub fn states(properties: &Map<String, Value>) -> IndexMap<String, String> {
    properties.iter().filter_map(|(key, value)| Some((tag_of(key)?, value.as_str()?.to_owned()))).collect()
}

/// One tag as the matcher sees it (`autotag.Tag`); `mode` is the tag's own override.
pub struct Tag {
    pub id: String,
    pub name: String,
    pub terms: Vec<String>,
    pub aliases: Vec<String>,
    pub mode: Option<String>,
    pub short: bool,
}

/// One whole-word occurrence of a tag's term (`autotag.Match`).
pub struct Match {
    pub tag: String,
    pub start: usize,
    pub end: usize,
}

/// A plain Aho-Corasick automaton over characters (`autotag._Automaton`).
struct Automaton {
    goto: Vec<IndexMap<char, usize>>,
    fail: Vec<usize>,
    out: Vec<Vec<usize>>,
}

impl Automaton {
    fn new(terms: &[Vec<char>]) -> Automaton {
        let mut automaton = Automaton { goto: vec![IndexMap::new()], fail: vec![0], out: vec![Vec::new()] };
        for (index, term) in terms.iter().enumerate() {
            let mut state = 0;
            for &c in term {
                state = match automaton.goto[state].get(&c) {
                    Some(&next) => next,
                    None => {
                        let next = automaton.goto.len();
                        automaton.goto[state].insert(c, next);
                        automaton.goto.push(IndexMap::new());
                        automaton.fail.push(0);
                        automaton.out.push(Vec::new());
                        next
                    }
                };
            }
            automaton.out[state].push(index);
        }
        let mut queue: Vec<usize> = automaton.goto[0].values().copied().collect();
        let mut at = 0;
        while at < queue.len() {
            let state = queue[at];
            at += 1;
            let edges: Vec<(char, usize)> = automaton.goto[state].iter().map(|(&c, &next)| (c, next)).collect();
            for (c, next) in edges {
                queue.push(next);
                let mut back = automaton.fail[state];
                while back != 0 && !automaton.goto[back].contains_key(&c) {
                    back = automaton.fail[back];
                }
                let target = automaton.goto[back].get(&c).copied().unwrap_or(0);
                automaton.fail[next] = if target != next { target } else { 0 };
                let inherited = automaton.out[automaton.fail[next]].clone();
                automaton.out[next].extend(inherited);
            }
        }
        automaton
    }

    /// (end index, term index) for every occurrence of every term in `text`.
    fn find(&self, text: &[char]) -> Vec<(usize, usize)> {
        let mut found = Vec::new();
        let mut state = 0;
        for (index, c) in text.iter().enumerate() {
            while state != 0 && !self.goto[state].contains_key(c) {
                state = self.fail[state];
            }
            state = self.goto[state].get(c).copied().unwrap_or(0);
            for &term in &self.out[state] {
                found.push((index + 1, term));
            }
        }
        found
    }
}

fn casefold_char(c: char) -> Vec<char> {
    let table = crate::search::tables::CASEFOLD;
    match table.binary_search_by_key(&(c as u32), |&(code, _)| code) {
        Ok(index) => table[index].1.chars().collect(),
        Err(_) => vec![c],
    }
}

/// Every tag's matchable terms in one automaton, plus name and alias lookup for `#tag` tokens.
pub struct Vocabulary {
    pub tags: IndexMap<String, Tag>,
    terms: Vec<(Vec<char>, Vec<String>)>,
    names: std::collections::HashMap<String, Vec<String>>,
    automaton: Automaton,
}

impl Vocabulary {
    pub fn of(tags: Vec<Tag>) -> Vocabulary {
        let tags: IndexMap<String, Tag> = tags.into_iter().map(|tag| (tag.id.clone(), tag)).collect();
        let mut owners: IndexMap<String, Vec<String>> = IndexMap::new();
        let mut names: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
        for tag in tags.values() {
            for value in std::iter::once(&tag.name).chain(&tag.aliases) {
                let name = normalize_term(value);
                if !name.is_empty() {
                    let ids = names.entry(name).or_default();
                    if !ids.contains(&tag.id) {
                        ids.push(tag.id.clone());
                    }
                }
            }
            for value in std::iter::once(&tag.name).chain(&tag.aliases).chain(&tag.terms) {
                let term = normalize_term(value);
                let length = term.chars().count();
                if term.is_empty() || length > MAX_TERM || (length < MIN_TERM && !tag.short) {
                    continue;
                }
                let ids = owners.entry(term).or_default();
                if !ids.contains(&tag.id) {
                    ids.push(tag.id.clone());
                }
            }
        }
        let terms: Vec<(Vec<char>, Vec<String>)> = owners.into_iter().map(|(term, ids)| (term.chars().collect(), ids)).collect();
        let automaton = Automaton::new(&terms.iter().map(|(term, _)| term.clone()).collect::<Vec<_>>());
        Vocabulary { tags, terms, names, automaton }
    }

    pub fn empty(&self) -> bool {
        self.terms.is_empty()
    }

    /// Tag IDs named by `#name` tokens in `text` (by tag name or alias).
    pub fn inline(&self, text: &str) -> IndexSet<String> {
        let mut found = IndexSet::new();
        for (_, _, name) in tag_tokens(text) {
            if let Some(ids) = self.names.get(&normalize_term(&name)) {
                found.extend(ids.iter().cloned());
            }
        }
        found
    }

    /// Whole-word term occurrences outside skipped spans; one per tag per place, leftmost longest.
    pub fn scan(&self, text: &str, marks: Option<&Value>) -> Vec<Match> {
        if self.terms.is_empty() || text.is_empty() {
            return Vec::new();
        }
        let original: Vec<char> = text.chars().collect();
        let mut folded: Vec<char> = Vec::with_capacity(original.len());
        let mut source: Vec<usize> = Vec::with_capacity(original.len());
        for (index, &c) in original.iter().enumerate() {
            let fold = casefold_char(c);
            source.extend(std::iter::repeat_n(index, fold.len()));
            folded.extend(fold);
        }
        let mapped = folded.len() != original.len();
        let wordy = |c: char| super::text::is_word(c);
        let mut hits: Vec<(usize, usize, usize)> = Vec::new();
        for (end, term) in self.automaton.find(&folded) {
            let chars = &self.terms[term].0;
            let (mut start, mut end) = (end - chars.len(), end);
            if mapped {
                if (start > 0 && source[start - 1] == source[start]) || (end < folded.len() && source[end] == source[end - 1]) {
                    continue;
                }
                (start, end) = (source[start], source[end - 1] + 1);
            }
            if wordy(chars[0]) && start > 0 && wordy(original[start - 1]) {
                continue;
            }
            if wordy(chars[chars.len() - 1]) && end < original.len() && wordy(original[end]) {
                continue;
            }
            hits.push((start, end, term));
        }
        if hits.is_empty() {
            return Vec::new();
        }
        let skipped = skip_spans(text, &code_marks(text, marks.unwrap_or(&Value::Null)));
        hits.sort_by_key(|&(start, end, _)| (start, start as i64 - end as i64));
        let mut taken: std::collections::HashMap<&str, usize> = std::collections::HashMap::new();
        let mut result = Vec::new();
        for (start, end, term) in hits {
            if skipped.iter().any(|&(s, e)| s < end && start < e) {
                continue;
            }
            for tag in &self.terms[term].1 {
                if taken.get(tag.as_str()).is_some_and(|&last| last > start) {
                    continue;
                }
                taken.insert(tag, end);
                result.push(Match { tag: tag.clone(), start, end });
            }
        }
        result
    }
}

/// `tag_settings` with this database's device: (mode, vocabulary revision, device), or None
/// before migration 039 (Python catches the `OperationalError`) or without the row.
fn settings(conn: &Connection) -> Done<Option<(String, i64, String)>> {
    let found = conn.query_row(
        "SELECT s.mode, s.vocabulary, h.device_id FROM tag_settings s, block_history h WHERE s.id=1 AND h.id=1", [],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    ).optional();
    match found {
        Ok(found) => Ok(found),
        Err(rusqlite::Error::SqliteFailure(_, Some(message))) if message.starts_with("no such table") => Ok(None),
        Err(error) => Err(error.into()),
    }
}

/// `tagging.tag_record`.
fn tag_record(id: String, text: String, properties: &str) -> Done<Tag> {
    let Value::Object(props) = serde_json::from_str(properties).map_err(|_| internal())? else { return Err(internal()) };
    let strings = |key: &str| -> Vec<String> {
        match props.get(key) {
            Some(Value::Array(items)) => items.iter().filter_map(|item| item.as_str().map(str::to_owned)).collect(),
            _ => Vec::new(),
        }
    };
    let mode = props.get("mode").and_then(Value::as_str).filter(|mode| MODES.contains(mode)).map(str::to_owned);
    Ok(Tag { id, name: text, terms: strings("keywords"), aliases: strings("aliases"), mode, short: props.get("short") == Some(&Value::Bool(true)) })
}

fn load_vocabulary(conn: &Connection) -> Done<Vocabulary> {
    let mut statement = conn.prepare("SELECT id, text, properties FROM blocks WHERE type='tag' AND parent_id IS NULL AND deleted_at IS NULL")?;
    let rows: Vec<(String, String, String)> = statement.query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))?.collect::<rusqlite::Result<_>>()?;
    let tags = rows.into_iter().map(|(id, name, props): (String, String, String)| tag_record(id, name, &props)).collect::<Done<Vec<Tag>>>()?;
    Ok(Vocabulary::of(tags))
}

static CACHE: LazyLock<Mutex<IndexMap<(String, i64), Arc<Vocabulary>>>> = LazyLock::new(|| Mutex::new(IndexMap::new()));

/// `tagging.vocabulary`: every live tag's terms, cached until a tag record changes.
pub fn vocabulary(conn: &Connection, found: &(String, i64, String)) -> Done<Arc<Vocabulary>> {
    let key = (found.2.clone(), found.1);
    if let Some(cached) = CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).get(&key) {
        return Ok(cached.clone());
    }
    let loaded = Arc::new(load_vocabulary(conn)?);
    let mut cache = CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    cache.insert(key, loaded.clone());
    while cache.len() > CACHE_SIZE {
        cache.shift_remove_index(0);
    }
    Ok(loaded)
}

/// `tagging.taggable`: a live text-flow block of a note page, nested only in text-flow blocks.
fn taggable(doc: &PageDoc, block: &Block) -> bool {
    let Some(root) = doc.root() else { return false };
    if !block.live() || !is_taggable_type(&block.kind) || root.kind != "page" {
        return false;
    }
    let Some(parent_id) = &block.parent_id else { return false };
    let mut parent = doc.blocks.get(parent_id);
    while let Some(item) = parent {
        let Some(above) = &item.parent_id else { break };
        if !is_outline(&item.kind) {
            return false;
        }
        parent = doc.blocks.get(above);
    }
    true
}

/// `tagging.suggestion`: matches of *suggest* tags not attached or rejected (UTF-16 offsets).
fn suggestion(vocab: &Vocabulary, text: &str, properties: &Map<String, Value>, matches: &[Match], mode: &dyn Fn(&str) -> String) -> Value {
    let known = states(properties);
    let inline = vocab.inline(text);
    let items: Vec<&Match> = matches.iter().filter(|m| mode(&m.tag) == "suggest" && !known.contains_key(&m.tag) && !inline.contains(&m.tag)).collect();
    let offsets = utf16(text, &items.iter().flat_map(|m| [m.start, m.end]).collect::<Vec<_>>());
    let listed: Vec<Value> = items.iter().enumerate().map(|(index, m)| json!({
        "tag": m.tag, "name": vocab.tags[&m.tag].name, "start": offsets[2 * index], "end": offsets[2 * index + 1],
    })).collect();
    json!({"text": text, "items": listed})
}

fn mode_of<'v>(vocab: &'v Vocabulary, default: &'v str) -> impl Fn(&str) -> String + 'v {
    move |tag: &str| vocab.tags.get(tag).and_then(|tag| tag.mode.clone()).unwrap_or_else(|| default.to_owned())
}

/// `tagging.annotate`: attach *always* tags to the changed blocks and collect *suggest* matches;
/// `{tagged, suggestions, tags}` with empty parts left out.
pub fn annotate(work: &mut Transaction, suggest: bool) -> Done<Map<String, Value>> {
    let Some(found) = settings(work.conn)? else { return Ok(Map::new()) };
    let vocab = vocabulary(work.conn, &found)?;
    let mode = mode_of(&vocab, &found.0);
    let mut tagged: Map<String, Value> = Map::new();
    let mut suggestions: Map<String, Value> = Map::new();
    let mut names: Map<String, Value> = Map::new();
    let pages: Vec<String> = work.docs.keys().cloned().collect();
    for page_id in pages {
        let mut changed: Vec<String> = work.docs[&page_id].changed.iter().cloned().collect();
        changed.sort();
        for bid in changed {
            let doc = &work.docs[&page_id];
            let Some(block) = doc.blocks.get(&bid).filter(|block| taggable(doc, block)).cloned() else { continue };
            let known = states(&block.properties);
            if vocab.empty() && !known.values().any(|value| value == "auto") {
                if suggest {
                    suggestions.insert(bid.clone(), json!({"text": block.text, "items": []}));
                }
                continue;
            }
            let matches = vocab.scan(&block.text, block.properties.get("marks"));
            let matched: IndexSet<&str> = matches.iter().map(|m| m.tag.as_str()).collect();
            let inline = vocab.inline(&block.text);
            let mut changes = Map::new();
            let mut sorted: Vec<&str> = matched.iter().copied().collect();
            sorted.sort();
            for tag in sorted {
                if !known.contains_key(tag) && !inline.contains(tag) && mode(tag) == "always" {
                    changes.insert(key_of(tag), "auto".into());
                }
            }
            for (tag, value) in &known {
                if value == "auto" && vocab.tags.contains_key(tag) && !matched.contains(tag.as_str()) {
                    changes.insert(key_of(tag), Value::Null);
                }
            }
            if !changes.is_empty() {
                for tag in &matched {
                    if changes.contains_key(&key_of(tag)) {
                        names.insert((*tag).to_owned(), vocab.tags[*tag].name.clone().into());
                    }
                }
                let op = json!({"op": "set", "id": bid, "props": changes});
                work.record(&page_id, op.as_object().cloned().expect("an object"))?;
                match tagged.entry(page_id.clone()).or_insert_with(|| Value::Array(Vec::new())) {
                    Value::Array(ops) => ops.push(op),
                    _ => unreachable!("a list of operations"),
                }
            }
            if suggest {
                let now = &work.docs[&page_id].blocks[&bid];
                let found = suggestion(&vocab, &now.text, &now.properties, &matches, &mode);
                for item in found["items"].as_array().into_iter().flatten() {
                    names.insert(item["tag"].as_str().unwrap_or_default().to_owned(), item["name"].clone());
                }
                suggestions.insert(bid.clone(), found);
            }
        }
    }
    let mut result = Map::new();
    if !tagged.is_empty() {
        result.insert("tagged".into(), Value::Object(tagged));
    }
    if suggest {
        result.insert("suggestions".into(), Value::Object(suggestions));
    }
    if !names.is_empty() {
        result.insert("tags".into(), Value::Object(names));
    }
    Ok(result)
}

/// `tagging.page_suggestions`: `suggestions` and tag `tags` (names) for a page as
/// `store.page_state` lists it.
pub fn page_suggestions(conn: &Connection, state: &Map<String, Value>) -> Done<(Value, Value)> {
    let Some(found) = settings(conn)? else { return Ok((json!({}), json!({}))) };
    let vocab = vocabulary(conn, &found)?;
    let mode = mode_of(&vocab, &found.0);
    let root = &state["root"];
    let rows: &[Value] = state["blocks"].as_array().map(Vec::as_slice).unwrap_or(&[]);
    let by_id: std::collections::HashMap<&str, &Value> = rows.iter().filter_map(|row| Some((row["id"].as_str()?, row))).collect();
    let mut suggestions = Map::new();
    let mut names = Map::new();
    let mut attached: IndexSet<String> = IndexSet::new();
    let empty = Map::new();
    for row in rows {
        let properties = row["properties"].as_object().unwrap_or(&empty);
        attached.extend(states(properties).into_keys());
        let kind = row["type"].as_str().unwrap_or_default();
        if root["type"].as_str() != Some("page") || !is_taggable_type(kind) {
            continue;
        }
        let mut parent = row["parent_id"].as_str().and_then(|id| by_id.get(id));
        while let Some(item) = parent.filter(|item| OUTLINE_TYPES.contains(&item["type"].as_str().unwrap_or_default())) {
            parent = item["parent_id"].as_str().and_then(|id| by_id.get(id));
        }
        if parent.is_some() {
            continue;
        }
        let text = row["text"].as_str().unwrap_or_default();
        let matches = vocab.scan(text, properties.get("marks"));
        let found = suggestion(&vocab, text, properties, &matches, &mode);
        if found["items"].as_array().is_some_and(|items| !items.is_empty()) {
            for item in found["items"].as_array().into_iter().flatten() {
                names.insert(item["tag"].as_str().unwrap_or_default().to_owned(), item["name"].clone());
            }
            suggestions.insert(row["id"].as_str().unwrap_or_default().to_owned(), found);
        }
    }
    for tag in attached {
        if let Some(known) = vocab.tags.get(&tag) {
            names.insert(tag, known.name.clone().into());
        }
    }
    Ok((Value::Object(suggestions), Value::Object(names)))
}

/// `tagging.project`: rewrite `block_tags` and `tag_rejections` for `changed` blocks.
pub fn project(conn: &Connection, changed: &[Block], at: &str) -> Done<()> {
    let Some(found) = settings(conn)? else { return Ok(()) };
    if changed.is_empty() {
        return Ok(());
    }
    let roots: Vec<&Block> = changed.iter().filter(|item| item.kind == "tag" && item.parent_id.is_none()).collect();
    let vocab = if roots.is_empty() {
        vocabulary(conn, &found)?
    } else {
        conn.execute("UPDATE tag_settings SET vocabulary=vocabulary+1 WHERE id=1", [])?;
        // Built from this transaction's own (uncommitted) rows, so never cached.
        Arc::new(load_vocabulary(conn)?)
    };
    let mut wanted: Vec<String> = changed.iter().flat_map(|item| states(&item.properties).into_keys()).collect::<HashSet<_>>().into_iter().collect();
    wanted.sort();
    let mut existing: HashSet<String> = HashSet::new();
    for chunk in wanted.chunks(500) {
        let marks = vec!["?"; chunk.len()].join(",");
        let mut statement = conn.prepare(&format!("SELECT id FROM blocks WHERE type='tag' AND parent_id IS NULL AND id IN ({marks})"))?;
        existing.extend(statement.query_map(rusqlite::params_from_iter(chunk), |row| row.get::<_, String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?);
    }
    let ids: Vec<&str> = changed.iter().map(|item| item.id.as_str()).collect();
    let mut current: std::collections::HashMap<String, IndexMap<String, String>> = std::collections::HashMap::new();
    let mut rejected_now: std::collections::HashMap<String, HashSet<String>> = std::collections::HashMap::new();
    for chunk in ids.chunks(500) {
        let marks = vec!["?"; chunk.len()].join(",");
        let mut statement = conn.prepare(&format!("SELECT block_id, tag_id, source FROM block_tags WHERE block_id IN ({marks})"))?;
        let mut rows = statement.query(rusqlite::params_from_iter(chunk))?;
        while let Some(row) = rows.next()? {
            current.entry(row.get(0)?).or_default().insert(row.get(1)?, row.get(2)?);
        }
        let mut statement = conn.prepare(&format!("SELECT block_id, tag_id FROM tag_rejections WHERE block_id IN ({marks})"))?;
        let mut rows = statement.query(rusqlite::params_from_iter(chunk))?;
        while let Some(row) = rows.next()? {
            rejected_now.entry(row.get(0)?).or_default().insert(row.get(1)?);
        }
    }
    let empty_tags = IndexMap::new();
    let empty_rejections = HashSet::new();
    let mut delete = conn.prepare("DELETE FROM block_tags WHERE block_id=? AND tag_id=?")?;
    let mut insert = conn.prepare("INSERT INTO block_tags(block_id,tag_id,source,created_at) VALUES (?,?,?,?) \
                                   ON CONFLICT(block_id,tag_id) DO UPDATE SET source=excluded.source")?;
    let mut forget = conn.prepare("DELETE FROM tag_rejections WHERE block_id=? AND tag_id=?")?;
    let mut reject = conn.prepare("INSERT INTO tag_rejections(block_id,tag_id,created_at) VALUES (?,?,?)")?;
    for item in changed {
        let mut attached: IndexMap<String, String> = IndexMap::new();
        let mut rejected: IndexSet<String> = IndexSet::new();
        if item.live() && !(item.kind == "tag" && item.parent_id.is_none()) {
            for (tag, value) in states(&item.properties) {
                if !existing.contains(&tag) {
                    continue;
                }
                if ATTACHED.contains(&value.as_str()) {
                    attached.insert(tag, value);
                } else if value == "rejected" {
                    rejected.insert(tag);
                }
            }
            for tag in vocab.inline(&item.text) {
                attached.entry(tag.clone()).or_insert_with(|| "manual".into());
                rejected.shift_remove(&tag);
            }
        }
        let before = current.get(&item.id).unwrap_or(&empty_tags);
        // Python's `executemany` per block; the statements are prepared once here.
        for tag in before.keys().filter(|tag| !attached.contains_key(*tag)) {
            delete.execute([&item.id, tag])?;
        }
        for (tag, source) in &attached {
            if before.get(tag) != Some(source) {
                insert.execute([item.id.as_str(), tag, source, at])?;
            }
        }
        let was = rejected_now.get(&item.id).unwrap_or(&empty_rejections);
        for tag in was.iter().filter(|tag| !rejected.contains(*tag)) {
            forget.execute([&item.id, tag])?;
        }
        for tag in rejected.iter().filter(|tag| !was.contains(*tag)) {
            reject.execute([item.id.as_str(), tag, at])?;
        }
    }
    for root in roots {
        refresh_inline(conn, root, at)?;
    }
    Ok(())
}

/// `tagging._refresh_inline`: a tag record changed; re-derive its attachments.
fn refresh_inline(conn: &Connection, tag: &Block, at: &str) -> Done<()> {
    let key = key_of(&tag.id);
    conn.execute(&format!("DELETE FROM block_tags WHERE tag_id=? AND source='manual' AND block_id NOT IN \
                           (SELECT id FROM blocks WHERE json_extract(properties, '$.{key}') = 'manual')"), [&tag.id])?;
    let stated = format!("FROM blocks WHERE properties LIKE ? AND deleted_at IS NULL AND NOT (type='tag' AND parent_id IS NULL) \
                          AND json_extract(properties, '$.{key}')");
    let like = format!("%\"{key}\"%");
    conn.execute(&format!("INSERT INTO block_tags(block_id,tag_id,source,created_at) SELECT id, ?, json_extract(properties, '$.{key}'), ? \
                           {stated} IN ('manual','auto','accepted') ON CONFLICT(block_id,tag_id) DO UPDATE SET source=excluded.source"),
                 [&tag.id, at, &like])?;
    conn.execute(&format!("INSERT OR IGNORE INTO tag_rejections(block_id,tag_id,created_at) SELECT id, ?, ? {stated} = 'rejected'"),
                 [&tag.id, at, &like])?;
    if !tag.live() {
        return Ok(());
    }
    let aliases: Vec<&str> = match tag.properties.get("aliases") {
        Some(Value::Array(items)) => items.iter().filter_map(Value::as_str).collect(),
        _ => Vec::new(),
    };
    let mut names: Vec<String> = std::iter::once(tag.text.as_str()).chain(aliases).map(normalize_term).filter(|name| !name.is_empty())
        .collect::<HashSet<_>>().into_iter().collect();
    names.sort();
    if names.is_empty() {
        return Ok(());
    }
    let marks = vec!["?"; names.len()].join(",");
    let mut values: Vec<String> = vec![tag.id.clone(), at.to_owned()];
    values.extend(names);
    conn.execute(&format!("INSERT INTO block_tags(block_id,tag_id,source,created_at) \
                           SELECT DISTINCT t.block_id, ?, 'manual', ? FROM block_topics t JOIN blocks b ON b.id=t.block_id \
                           WHERE t.kind='tag' AND t.topic IN ({marks}) AND b.deleted_at IS NULL \
                           AND NOT (b.type='tag' AND b.parent_id IS NULL) ON CONFLICT(block_id,tag_id) DO NOTHING"),
                 rusqlite::params_from_iter(values))?;
    conn.execute("DELETE FROM tag_rejections WHERE tag_id=? AND block_id IN (SELECT block_id FROM block_tags WHERE tag_id=? AND source='manual')",
                 [&tag.id, &tag.id])?;
    Ok(())
}
