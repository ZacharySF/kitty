//! Linked mentions of map nodes and of free terms (`scan`, `suggestions`, `mentions`) and
//! linking them into a map (`apply`), as `kitty.core.backlinks` finds and ranks them:
//! candidates from the full-text index (a phrase query over every record of each kind, newest
//! first), confirmed by the token matcher, at most `per_term` shown per kind and term.

use std::collections::{HashMap, HashSet};
use std::f64::consts::PI;

use rusqlite::{Connection, Row, Statement};
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use super::text::{phrase, phrase_query, redact, snippet, tokens, wikilinks, Phrase};
use super::{searched, str_of};
use crate::ported::{db, fail, internal, py, Done};
use crate::schema::{self, Maps, Obj};

pub const PER_NODE: usize = 12;
pub const PER_TERM: usize = 24;
const EDGE_LABEL: &str = "mentioned in";

/// `VIEWS` in its order (`ORDER`): each kind, the view that opens it and its `NAMES` entry.
pub const KINDS: [(&str, &str, &str); 8] = [
    ("note_page", "notes", "note page"),
    ("study_subject", "study", "study subject"),
    ("study_project", "study", "study project"),
    ("study_task", "study", "study task"),
    ("calendar_event", "calendar", "calendar event"),
    ("calendar_task", "calendar", "calendar task"),
    ("job", "jobs", "job"),
    ("flashcard_deck", "flashcards", "flashcard deck"),
];

fn view(kind: &str) -> &'static str {
    KINDS.iter().find(|entry| entry.0 == kind).map_or("", |entry| entry.1)
}

fn name(kind: &str) -> &'static str {
    KINDS.iter().find(|entry| entry.0 == kind).map_or("", |entry| entry.2)
}

/// `_kinds`.
fn kinds(applyai: bool) -> impl Iterator<Item = &'static str> {
    KINDS.iter().map(|entry| entry.0).filter(move |kind| applyai || *kind != "job")
}

/// One candidate query of `_KIND_SQL`: (FTS column, extra columns, join, where, recency order).
struct Spec {
    column: Option<&'static str>,
    extra: &'static str,
    join: &'static str,
    filter: &'static str,
    order: &'static str,
}

const fn plain(filter: &'static str, join: &'static str, order: &'static str) -> Spec {
    Spec { column: None, extra: "", join, filter, order }
}

fn specs(kind: &str) -> &'static [Spec] {
    const NOTE_PAGE: &[Spec] = &[Spec { column: None, extra: ",CAST(json_extract(r.properties,'$.number') AS INTEGER) AS number,r.text AS root_title",
        join: "JOIN blocks r ON r.id=d.page_id", filter: "d.kind='block' AND r.type='page' AND r.parent_id IS NULL",
        order: "r.updated_at DESC,number DESC,d.id" }];
    const SUBJECT: &[Spec] = &[plain("d.kind='study_subject'", "", "CAST(d.ref AS INTEGER) DESC")];
    const PROJECT: &[Spec] = &[plain("d.kind='study_project'", "", "CAST(d.ref AS INTEGER) DESC")];
    const TASK: &[Spec] = &[plain("d.kind='study_task'", "", "CAST(d.ref AS INTEGER) DESC")];
    const EVENT: &[Spec] = &[plain("d.kind='calendar_event'", "JOIN calendar_events e ON e.id=d.ref", "e.updated_at DESC,e.id")];
    const CALENDAR_TASK: &[Spec] = &[plain("d.kind='calendar_task'", "JOIN calendar_tasks t ON t.id=d.ref", "t.updated_at DESC,t.id")];
    // Card fronts link back to their deck; deck review stays in Flashcards.
    const DECK: &[Spec] = &[plain("d.kind='flashcard_deck'", "", "CAST(d.ref AS INTEGER) DESC"),
        Spec { column: Some("title"), extra: ",fd.name AS deck", join: "JOIN flash_notes n ON n.id=d.ref JOIN flash_decks fd ON fd.id=n.deck_id",
               filter: "d.kind='flashcard_note'", order: "n.updated_at DESC,n.id DESC" }];
    const JOB: &[Spec] = &[plain("d.kind='job'", "", "CAST(d.ref AS INTEGER) DESC")];
    match kind {
        "note_page" => NOTE_PAGE,
        "study_subject" => SUBJECT,
        "study_project" => PROJECT,
        "study_task" => TASK,
        "calendar_event" => EVENT,
        "calendar_task" => CALENDAR_TASK,
        "flashcard_deck" => DECK,
        _ => JOB,
    }
}

/// `_COUNT_SQL`: the documents a kind counts (a note page once, by its title document).
fn count_sql(kind: &str) -> (&'static str, &'static str) {
    match kind {
        "note_page" => ("JOIN blocks r ON r.id=d.page_id", "d.kind='block' AND r.type='page' AND d.ref=d.page_id"),
        "flashcard_deck" => ("", "d.kind IN ('flashcard_deck','flashcard_note')"),
        other => {
            let spec = &specs(other)[0];
            (spec.join, spec.filter)
        }
    }
}

/// `d.flags & APPLYAI = 0`: without ApplyAI its calendar copies are left out.
fn filter(kind: &str, base: &str, applyai: bool) -> String {
    if kind == "calendar_event" && !applyai { format!("{base} AND d.flags & 1 = 0") } else { base.to_owned() }
}

/// `int(text)` of a stored id.
fn int(text: &str) -> Done<Value> {
    py::strip(text).parse::<i64>().map(Value::from).map_err(|_| internal())
}

/// `_record`: the id, title and text of the record a candidate document belongs to.
fn record(kind: &str, row: &Row) -> Done<(Value, Option<String>, String)> {
    let title: String = row.get("title")?;
    let body: String = row.get("body")?;
    if kind == "note_page" {
        let number: Option<i64> = row.get("number")?;
        return Ok((json!(number), row.get("root_title")?, if title.is_empty() { body } else { title }));
    }
    let doc_kind: String = row.get("kind")?;
    if doc_kind == "flashcard_note" {
        let page_id: Option<String> = row.get("page_id")?;
        return Ok((int(&page_id.ok_or_else(internal)?)?, row.get("deck")?, title));
    }
    let reference: String = row.get("ref")?;
    let ident = if kind.starts_with("calendar_") { Value::String(reference) } else { int(&reference)? };
    let shown = if kind == "job" { row.get("label")? } else { title.clone() };
    let text = if body.is_empty() { title } else { format!("{title}\n{body}") };
    Ok((ident, Some(shown), text))
}

/// The candidate statements of every searched kind, compiled once for all the terms of one
/// request (Python's sqlite3 reused them from its statement cache).
struct Statements<'c> {
    kinds: Vec<(&'static str, Vec<(Statement<'c>, Option<&'static str>)>)>,
}

impl<'c> Statements<'c> {
    fn prepare(conn: &'c Connection, applyai: bool) -> Done<Self> {
        let mut out = Vec::new();
        for kind in kinds(applyai) {
            let mut prepared = Vec::new();
            for spec in specs(kind) {
                let sql = format!("SELECT d.kind,d.ref,d.page_id,d.title,d.body,d.label{extra} FROM search_fts
    JOIN search_docs d ON d.id=search_fts.rowid {join} WHERE search_fts MATCH ? AND {filter} ORDER BY {order}",
                    extra = spec.extra, join = spec.join, filter = filter(kind, spec.filter, applyai), order = spec.order);
                prepared.push((conn.prepare(&sql)?, spec.column));
            }
            out.push((kind, prepared));
        }
        Ok(Self { kinds: out })
    }
}

/// `_candidates`: the records of one kind that may contain `wanted`, newest first, handed to
/// `visit` as `(id, title, texts)` until it answers true.
fn candidates(kind: &str, statements: &mut [(Statement, Option<&'static str>)], wanted: &Phrase,
              mut visit: impl FnMut(Value, Option<String>, Vec<String>) -> bool) -> Done<()> {
    let mut current: Option<(Value, Option<String>)> = None;
    let mut texts = Vec::new();
    for (statement, column) in statements.iter_mut() {
        let mut rows = statement.query([phrase_query(wanted, *column)])?;
        while let Some(row) = rows.next()? {
            let (ident, title, text) = record(kind, row)?;
            if current.as_ref().is_some_and(|(seen, _)| *seen != ident) {
                let (seen, shown) = current.take().expect("checked above");
                if visit(seen, shown, std::mem::take(&mut texts)) {
                    return Ok(());
                }
            }
            current = Some((ident, title));
            texts.push(text);
        }
    }
    if let Some((seen, shown)) = current {
        visit(seen, shown, texts);
    }
    Ok(())
}

/// `indexed`: how many records of each kind the index holds (every one is searched).
pub fn indexed(conn: &Connection, applyai: bool) -> Done<Map<String, Value>> {
    let mut out = Map::new();
    for kind in kinds(applyai) {
        let (join, base) = count_sql(kind);
        let count: i64 = conn.query_row(&format!("SELECT COUNT(*) FROM search_docs d {join} WHERE {}", filter(kind, base, applyai)), [], |row| row.get(0))?;
        if count != 0 {
            out.insert(kind.into(), json!(count));
        }
    }
    Ok(out)
}

/// `_match`: the span of the first token-phrase match of `wanted` in `text`.
fn matched(text: &str, wanted: &Phrase) -> Option<(usize, usize)> {
    let found = tokens(text);
    for position in 0..found.len() {
        if found[position].0 == wanted[0] && found.len() - position >= wanted.len()
            && found[position..position + wanted.len()].iter().zip(wanted).all(|(token, want)| token.0 == *want) {
            return Some((found[position].1, found[position + wanted.len() - 1].2));
        }
    }
    None
}

/// One confirmed reference.
#[derive(Clone)]
pub struct Reference {
    pub kind: &'static str,
    pub id: Value,
    pub title: String,
    snippet: String,
    explicit: bool,
}

impl Reference {
    pub fn value(&self) -> Map<String, Value> {
        let mut out = Map::new();
        out.insert("kind".into(), json!(self.kind));
        out.insert("id".into(), self.id.clone());
        out.insert("title".into(), json!(self.title));
        out.insert("snippet".into(), json!(self.snippet));
        out.insert("view".into(), json!(view(self.kind)));
        out.insert("match".into(), json!(if self.explicit { "wikilink" } else { "mention" }));
        out
    }
}

/// A record id as a set member (`record_id in seen`).
fn member(value: &Value) -> String {
    py::dumps(value)
}

/// `_find`: at most `per_term` confirmed references per kind, newest first, and whether more exist.
fn find(statements: &mut Statements, wanted: &Phrase, per_term: usize, exclude: Option<(&str, &str)>) -> Done<(Vec<Reference>, bool)> {
    let (mut references, mut more) = (Vec::new(), false);
    for (kind, prepared) in statements.kinds.iter_mut() {
        let kind: &'static str = *kind;
        let (mut count, mut seen) = (0, HashSet::new());
        candidates(kind, prepared, wanted, |record_id, title, texts| {
            if exclude == Some((kind, str_of(&record_id).as_str())) || seen.contains(&member(&record_id)) {
                return false;
            }
            let Some((text, (start, end))) = texts.iter().find_map(|text| matched(text, wanted).map(|span| (text, span))) else {
                return false;
            };
            if count == per_term {
                more = true;
                return true;
            }
            count += 1;
            seen.insert(member(&record_id));
            let explicit = texts.iter().any(|text| wikilinks(text).iter().any(|link| phrase(link).as_ref() == Some(wanted)));
            let title: String = title.unwrap_or_default().chars().take(200).collect();
            references.push(Reference { kind, id: record_id, title, snippet: snippet(text, start, end), explicit });
            false
        })?;
    }
    Ok((references, more))
}

/// `_rank`: explicit wikilinks first, then the kinds' mentions in turn, newest first in each.
fn rank(references: &[Reference], limit: usize) -> Vec<Reference> {
    let mut ordered: Vec<Reference> = references.iter().filter(|item| item.explicit).take(limit).cloned().collect();
    let mut queues: Vec<std::collections::VecDeque<&Reference>> = KINDS.iter()
        .map(|(kind, _, _)| references.iter().filter(|item| !item.explicit && item.kind == *kind).collect())
        .collect();
    while ordered.len() < limit && queues.iter().any(|queue| !queue.is_empty()) {
        for queue in &mut queues {
            if ordered.len() < limit {
                if let Some(item) = queue.pop_front() {
                    ordered.push(item.clone());
                }
            }
        }
    }
    ordered
}

pub struct Scanned {
    /// Per term key, in the order asked: its references and whether more exist.
    pub found: Vec<(String, Vec<Reference>, bool)>,
    pub indexed: Map<String, Value>,
}

/// `_scan`: each `key -> phrase` against every indexed local record.
fn scan(conn: &Connection, terms: &[(String, Phrase)], per_term: usize, applyai: bool, exclude: Option<(&str, &str)>) -> Done<Scanned> {
    if terms.is_empty() {
        return Ok(Scanned { found: Vec::new(), indexed: Map::new() });
    }
    searched(crate::search::query::synced(conn))?;
    let mut statements = Statements::prepare(conn, applyai)?;
    let mut found = Vec::new();
    for (key, wanted) in terms {
        let (references, more) = find(&mut statements, wanted, per_term, exclude)?;
        let ranked = rank(&references, per_term);
        let truncated = more || ranked.len() < references.len();
        found.push((key.clone(), ranked, truncated));
    }
    Ok(Scanned { found, indexed: indexed(conn, applyai)? })
}

/// `target_id`: the stable graph id of a linked reference node.
pub fn target_id(kind: &str, record_id: &Value) -> String {
    let ident = str_of(record_id);
    let raw = format!("{kind}-{ident}");
    if (1..=70).contains(&raw.len()) && raw.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') {
        return format!("ref-{raw}");
    }
    format!("ref-{kind}-{}", &hex::encode(Sha1::digest(ident.as_bytes()))[..20])
}

/// A reference's identity as `_targets` keys it: the kind and the id as Python compares it.
fn identity(kind: &str, ident: &Value) -> String {
    match ident.as_i64() {
        Some(number) => format!("{kind}\u{0}{number}"),
        None => format!("{kind}\u{0}{}", py::dumps(ident)),
    }
}

fn nodes(document: &Obj) -> Done<&Vec<Value>> {
    schema::at(document, "nodes")?.as_array().ok_or_else(internal)
}

fn edges(document: &Obj) -> Done<&Vec<Value>> {
    schema::at(document, "edges")?.as_array().ok_or_else(internal)
}

fn node_id(node: &Value) -> Done<&str> {
    schema::string(schema::at(node.as_object().ok_or_else(internal)?, "id")?)
}

/// `_targets`: sourced job and subject nodes by the record they link.
fn targets(document: &Obj) -> Done<HashMap<String, String>> {
    let mut sourced = HashMap::new();
    for node in nodes(document)? {
        let node = node.as_object().ok_or_else(internal)?;
        let Some(source) = node.get("source").and_then(Value::as_object).filter(|source| !source.is_empty()) else { continue };
        let kind = match schema::at(source, "kind")?.as_str() {
            Some("job") => "job",
            Some("subject") => "study_subject",
            _ => continue,
        };
        sourced.insert(identity(kind, schema::at(source, "id")?), schema::string(schema::at(node, "id")?)?.to_owned());
    }
    Ok(sourced)
}

/// `_terms`: each node (but reference nodes) whose label is a phrase.
fn terms(document: &Obj) -> Done<Vec<(String, Phrase)>> {
    let mut out = Vec::new();
    for node in nodes(document)? {
        let ident = node_id(node)?;
        if ident.starts_with("ref-") {
            continue;
        }
        if let Some(tokens) = schema::at(node.as_object().ok_or_else(internal)?, "label")?.as_str().and_then(phrase) {
            out.push((ident.to_owned(), tokens));
        }
    }
    Ok(out)
}

/// `_connected`: every edge, both ways.
fn connected(document: &Obj) -> Done<HashSet<(String, String)>> {
    let mut out = HashSet::new();
    for edge in edges(document)? {
        let edge = edge.as_object().ok_or_else(internal)?;
        let (from, to) = (schema::string(schema::at(edge, "from")?)?, schema::string(schema::at(edge, "to")?)?);
        out.insert((from.to_owned(), to.to_owned()));
        out.insert((to.to_owned(), from.to_owned()));
    }
    Ok(out)
}

fn target_of(sourced: &HashMap<String, String>, item: &Reference) -> String {
    sourced.get(&identity(item.kind, &item.id)).filter(|node| !node.is_empty()).cloned().unwrap_or_else(|| target_id(item.kind, &item.id))
}

struct Suggested {
    /// Per node with a phrase: its references with whether each is linked already, and truncated.
    found: HashMap<String, (Vec<(Reference, bool)>, bool)>,
    indexed: Map<String, Value>,
}

/// `_suggest`.
fn suggest(conn: &Connection, document: &Obj, applyai: bool) -> Done<Suggested> {
    let scanned = scan(conn, &terms(document)?, PER_NODE, applyai, None)?;
    let sourced = targets(document)?;
    let ids: HashSet<&str> = nodes(document)?.iter().map(node_id).collect::<Done<_>>()?;
    let connected = connected(document)?;
    let mut found = HashMap::new();
    for (node, references, truncated) in scanned.found {
        let mut kept = Vec::new();
        for item in references {
            let target = target_of(&sourced, &item);
            if target == node {
                continue;
            }
            let linked = ids.contains(target.as_str()) && connected.contains(&(node.clone(), target));
            kept.push((item, linked));
        }
        found.insert(node, (kept, truncated));
    }
    Ok(Suggested { found, indexed: scanned.indexed })
}

fn document_of(row: &Obj) -> Done<Obj> {
    match schema::parsed(row, "graph_json")? {
        Value::Object(document) => Ok(document),
        _ => Err(internal()),
    }
}

/// `suggestions`: every node's suggested references.
pub fn suggestions(maps: &Maps, map_id: &Value) -> Done<Value> {
    let row = maps.row(map_id)?;
    let document = document_of(&row)?;
    let suggested = suggest(maps.conn, &document, maps.applyai)?;
    let mut listed = Vec::new();
    for node in nodes(&document)? {
        let ident = node_id(node)?;
        let Some((references, truncated)) = suggested.found.get(ident).filter(|(references, _)| !references.is_empty()) else { continue };
        let references: Vec<Value> = references.iter().map(|(item, linked)| {
            let mut value = item.value();
            value.insert("linked".into(), json!(linked));
            Value::Object(value)
        }).collect();
        listed.push(json!({"node_id": ident, "label": schema::at(node.as_object().ok_or_else(internal)?, "label")?,
                           "references": references, "truncated": truncated}));
    }
    Ok(json!({"map_id": schema::at(&row, "id")?, "revision": schema::at(&row, "revision")?, "suggestions": listed,
              "scanned": suggested.indexed}))
}

/// `mentions`: linked mentions of a free term, e.g. a note page title or a study subject.
pub fn mentions(conn: &Connection, term: &str, exclude: Option<&str>, applyai: bool) -> Done<Value> {
    let term = schema::text(&json!(term), "Term", 200, false)?;
    let wanted = phrase(&term);
    let exclude = match exclude {
        Some(text) => {
            let (kind, record_id) = text.split_once(':').unwrap_or((text, ""));
            if !KINDS.iter().any(|entry| entry.0 == kind) || record_id.is_empty() {
                return Err(fail(400, "Exclude must look like <kind>:<id>"));
            }
            Some((kind, record_id))
        }
        None => None,
    };
    let terms: Vec<(String, Phrase)> = wanted.map(|tokens| vec![("term".to_owned(), tokens)]).unwrap_or_default();
    let scanned = scan(conn, &terms, PER_TERM, applyai, exclude)?;
    let (references, truncated) = match scanned.found.into_iter().next() {
        Some((_, references, truncated)) => (references, truncated),
        None => (Vec::new(), false),
    };
    Ok(json!({"term": term, "references": references.iter().map(|item| Value::Object(item.value())).collect::<Vec<_>>(),
              "truncated": truncated, "scanned": scanned.indexed}))
}

/// `uuid.uuid5(uuid.NAMESPACE_URL, name).hex`.
fn uuid5(name: &str) -> String {
    const NAMESPACE_URL: [u8; 16] = [0x6b, 0xa7, 0xb8, 0x11, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8];
    let mut hasher = Sha1::new();
    hasher.update(NAMESPACE_URL);
    hasher.update(name.as_bytes());
    let digest = hasher.finalize();
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x50;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex::encode(bytes)
}

/// `_clamp`.
fn clamp(value: f64) -> f64 {
    value.clamp(-100000.0, 100000.0)
}

fn float(value: f64) -> Done<Value> {
    serde_json::Number::from_f64(value).map(Value::Number).ok_or_else(internal)
}

/// `apply`: link every reference currently suggested for the accepted nodes; revision-checked
/// and idempotent.
pub fn apply(maps: &Maps, body: &Obj) -> Done<Value> {
    if !schema::known(body, &["map_id", "revision", "node_ids"]) {
        return Err(schema::unknown_field());
    }
    let Some(given) = body.get("node_ids").and_then(Value::as_array).filter(|ids| (1..=schema::MAX_NODES).contains(&ids.len())) else {
        return Err(fail(400, format!("Select between 1 and {} nodes to link", schema::MAX_NODES)));
    };
    let mut node_ids: Vec<String> = Vec::new();
    for value in given {
        let key = schema::key(value)?;
        if !node_ids.contains(&key) {
            node_ids.push(key);
        }
    }
    let conn = maps.conn;
    db::transaction(conn, true, || {
        let row = maps.row(schema::get(body, "map_id"))?;
        schema::revision(&row, schema::get(body, "revision"))?;
        let mut document = document_of(&row)?;
        let mut index: HashMap<String, usize> = HashMap::new();
        for (at, node) in nodes(&document)?.iter().enumerate() {
            index.insert(node_id(node)?.to_owned(), at);
        }
        if node_ids.iter().any(|key| !index.contains_key(key)) {
            return Err(fail(400, "A selected node is no longer in this map"));
        }
        let suggested = suggest(conn, &document, maps.applyai)?;
        let sourced = targets(&document)?;
        let mut connected = connected(&document)?;
        let mut edge_ids: HashSet<String> = HashSet::new();
        for edge in edges(&document)? {
            edge_ids.insert(schema::string(schema::at(edge.as_object().ok_or_else(internal)?, "id")?)?.to_owned());
        }
        let mut changed = false;
        let (mut node_list, mut edge_list) = match (document.shift_remove("nodes"), document.shift_remove("edges")) {
            (Some(Value::Array(nodes)), Some(Value::Array(edges))) => (nodes, edges),
            _ => return Err(internal()),
        };
        for node in &node_ids {
            let origin = node_list[index[node]].as_object().ok_or_else(internal)?;
            let (x, y) = (schema::at(origin, "x")?.as_f64().ok_or_else(internal)?, schema::at(origin, "y")?.as_f64().ok_or_else(internal)?);
            let pending: Vec<&Reference> = suggested.found.get(node).map(|(references, _)| references.iter().filter(|(_, linked)| !linked).map(|(item, _)| item).collect()).unwrap_or_default();
            for (at, item) in pending.iter().enumerate() {
                let target = target_of(&sourced, item);
                if !index.contains_key(&target) {
                    if node_list.len() >= schema::MAX_NODES {
                        return Err(fail(400, format!("Linking would exceed {} nodes; link fewer nodes or split the map", schema::MAX_NODES)));
                    }
                    let angle = 2.0 * PI * at as f64 / pending.len().max(1) as f64 - PI / 2.0;
                    let stripped = py::strip(&item.title);
                    let title = if stripped.is_empty() { name(item.kind) } else { stripped };
                    let kind = if matches!(item.kind, "note_page" | "study_subject" | "study_project" | "flashcard_deck") { "topic" } else { "idea" };
                    let mut added = Map::new();
                    added.insert("id".into(), json!(target));
                    added.insert("kind".into(), json!(kind));
                    added.insert("label".into(), json!(title));
                    added.insert("notes".into(), json!(redact(&format!("Linked from {}: {title}", name(item.kind)))));
                    added.insert("x".into(), float(clamp(x + 300.0 * angle.cos()))?);
                    added.insert("y".into(), float(clamp(y + 190.0 * angle.sin()))?);
                    added.insert("url".into(), json!(""));
                    added.insert("source".into(), Value::Null);
                    index.insert(target.clone(), node_list.len());
                    node_list.push(Value::Object(added));
                    changed = true;
                }
                if !connected.contains(&(node.clone(), target.clone())) {
                    if edge_list.len() >= schema::MAX_EDGES {
                        return Err(fail(400, format!("Linking would exceed {} connections", schema::MAX_EDGES)));
                    }
                    let mut attempt = 0;
                    let mut edge_id = String::new();
                    while edge_id.is_empty() || edge_ids.contains(&edge_id) {
                        edge_id = format!("bl-{}", &uuid5(&format!("{node}>{target}#{attempt}"))[..24]);
                        attempt += 1;
                    }
                    edge_ids.insert(edge_id.clone());
                    edge_list.push(json!({"id": edge_id, "from": node, "to": target, "label": EDGE_LABEL}));
                    connected.insert((node.clone(), target.clone()));
                    connected.insert((target.clone(), node.clone()));
                    changed = true;
                }
            }
        }
        let row_id = schema::id_of(&row)?;
        if !changed {
            return maps.load_at(row_id);
        }
        let mut whole = Obj::new();
        whole.insert("nodes".into(), Value::Array(node_list));
        whole.insert("edges".into(), Value::Array(edge_list));
        whole.extend(document);
        let graph = maps.graph(&Value::Object(whole), false)?;
        maps.write(&row, schema::string(schema::at(&row, "title")?)?, &graph, &schema::parsed(&row, "viewport_json")?)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_pythons() {
        // `uuid.uuid5(uuid.NAMESPACE_URL, name).hex` as CPython gives them.
        assert_eq!(uuid5("a>b#0"), "c08b13b40d2959e0a57d66ea93665fd7");
        assert_eq!(uuid5("nœud>ref-x#1"), "e620d821f561582aab2838e1274324ce");
        assert_eq!(target_id("note_page", &json!(7)), "ref-note_page-7");
        assert_eq!(target_id("calendar_event", &json!("a b")), format!("ref-calendar_event-{}", &hex::encode(Sha1::digest(b"a b"))[..20]));
        assert_eq!(target_id("note_page", &Value::Null), "ref-note_page-None");
    }
}
