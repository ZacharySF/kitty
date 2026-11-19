//! The mind-map routes (`kitty.web_schema` and `kitty.core.schema_maps` in Python): the list,
//! one map with the live view of its sources, the portable export, the merged network, the
//! tracked jobs to pick from, and create, import, save, add a node, delete, undo and redo,
//! import tracked jobs, and the transfer of a branch between maps. Same values, stored rows
//! (`json.dumps` text), order of checks and refusals as Python.
//! The backlink routes (`/api/schema/backlinks`, `/api/schema/backlinks/apply`) are
//! crate::backlinks, which reads and writes the maps through `Maps` and the validators here.
//! `kitty.core.schema_maps` stays for Rika's tools, the page maps, the atlas and the tests.

mod network;
mod url;

use std::collections::{HashMap, HashSet};

use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};

use crate::ported::{db, disabled, fail, internal, py, time, Call, Ctx, Done, Fault, Group};

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };
/// The two routes over tracked jobs belong to ApplyAI (`ROUTE_MODULES`: the longest prefix).
pub static JOBS: Group = Group { owns: owns_jobs, module: Some("applyai"), answer: answer_jobs };

pub(crate) type Obj = Map<String, Value>;

const MODES: [&str; 3] = ["study", "general", "jobs"];
const KINDS: [&str; 7] = ["class", "topic", "idea", "task", "job", "company", "skill"];
/// Theme graph palette slots a node may be painted with.
const COLORS: [&str; 6] = ["c1", "c2", "c3", "c4", "c5", "c6"];
const MAX_MAPS: usize = 100;
pub(crate) const MAX_NODES: usize = 300;
pub(crate) const MAX_EDGES: usize = 600;
const MAX_STROKES: usize = 400;
const MAX_STROKE_POINTS: usize = 1000;
const MAX_BYTES: usize = 1_000_000;
/// Source kinds whose live data belongs to the ApplyAI module.
const APPLYAI_KINDS: [&str; 2] = ["job", "company"];
const COORDINATE: (f64, f64, &str) = (-100000.0, 100000.0, "-100000 and 100000");

const JOBS_GET: &str = "/api/schema/jobs";
const JOBS_POST: &str = "/api/schema/import-jobs";

fn owns(path: &str, mutation: bool) -> bool {
    if mutation {
        matches!(path, "/api/schema/create" | "/api/schema/import" | "/api/schema/add-node" | "/api/schema/save" | "/api/schema/delete"
            | "/api/schema/undo" | "/api/schema/transfer")
    } else {
        matches!(path, "/api/schema" | "/api/schema/map" | "/api/schema/export" | "/api/schema/network")
    }
}

fn owns_jobs(path: &str, mutation: bool) -> bool {
    path == if mutation { JOBS_POST } else { JOBS_GET }
}

/// `ValueError`.
fn bad(message: impl Into<String>) -> Fault {
    fail(400, message)
}

// Values -----------------------------------------------------------------------

/// `mapping.get(key)`: None when absent.
pub(crate) fn get<'a>(map: &'a Obj, key: &str) -> &'a Value {
    map.get(key).unwrap_or(&Value::Null)
}

/// `mapping[key]` of a stored document: a missing key was Python's `KeyError`.
pub(crate) fn at<'a>(map: &'a Obj, key: &str) -> Done<&'a Value> {
    map.get(key).ok_or_else(internal)
}

pub(crate) fn string(value: &Value) -> Done<&str> {
    value.as_str().ok_or_else(internal)
}

pub(crate) fn id_of(row: &Obj) -> Done<i64> {
    at(row, "id")?.as_i64().ok_or_else(internal)
}

/// `value in {strings}`: a list or an object is unhashable, Python's `TypeError`.
fn member<'a>(value: &'a Value) -> Done<Option<&'a str>> {
    match value {
        Value::Array(_) | Value::Object(_) => Err(internal()),
        other => Ok(other.as_str()),
    }
}

/// `_text`.
pub(crate) fn text(value: &Value, name: &str, limit: usize, required: bool) -> Done<String> {
    let Some(value) = value.as_str().filter(|text| py::len(text) <= limit && !text.contains('\0')) else {
        return Err(bad(format!("{name} must be text of at most {limit} characters")));
    };
    let value = py::strip(value);
    if required && value.is_empty() {
        return Err(bad(format!("{name} is required")));
    }
    Ok(value.to_owned())
}

/// `_integer`.
fn integer(value: &Value, name: &str, low: i64) -> Done<i64> {
    py::int(value).filter(|number| *number >= low).ok_or_else(|| out_of_range(name, low))
}

fn out_of_range(name: &str, low: i64) -> Fault {
    bad(format!("{name} must be an integer between {low} and {}", i64::MAX))
}

/// `_number`: an int or a float in range, kept as it was sent.
fn number(value: &Value, name: &str, (low, high, shown): (f64, f64, &str)) -> Done<Value> {
    match value.as_f64().filter(|float| value.is_number() && low <= *float && *float <= high) {
        Some(_) => Ok(value.clone()),
        None => Err(bad(format!("{name} must be a finite number between {shown}"))),
    }
}

/// `_fields`: an object with no key outside `allowed`.
fn fields<'a>(value: &'a Value, allowed: &[&str]) -> Done<&'a Obj> {
    match value.as_object() {
        Some(map) if known(map, allowed) => Ok(map),
        _ => Err(unknown_field()),
    }
}

pub(crate) fn known(map: &Obj, allowed: &[&str]) -> bool {
    map.keys().all(|key| allowed.contains(&key.as_str()))
}

pub(crate) fn unknown_field() -> Fault {
    bad("Unknown schema field or invalid object")
}

/// `_url`.
fn link(value: &Value) -> Done<String> {
    let value = text(value, "Link", 4000, false)?;
    if value.is_empty() || url::valid(&value) {
        Ok(value)
    } else {
        Err(bad("Links must be absolute HTTP or HTTPS URLs without credentials"))
    }
}

/// `_safe_url`: a stored link, or nothing when it is not one the map may show.
fn safe_link(value: &Value) -> String {
    let empty = json!("");
    link(if py::truthy(value) { value } else { &empty }).unwrap_or_default()
}

/// `_key`.
pub(crate) fn key(value: &Value) -> Done<String> {
    let value = text(value, "Graph ID", 80, true)?;
    if value.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        Ok(value)
    } else {
        Err(bad("Graph IDs may only contain letters, digits, underscores and hyphens"))
    }
}

fn viewport(value: &Value) -> Done<Value> {
    let value = fields(value, &["x", "y", "zoom"])?;
    let side = (-1e7, 1e7, "-10000000.0 and 10000000.0");
    Ok(json!({"x": number(get(value, "x"), "Viewport x", side)?, "y": number(get(value, "y"), "Viewport y", side)?,
              "zoom": number(get(value, "zoom"), "Zoom", (0.1, 4.0, "0.1 and 4"))?}))
}

/// `uuid.uuid4().hex`.
fn uuid() -> Done<String> {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| internal())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(hex::encode(bytes))
}

/// `int(text)` for a query value: white space around, a sign, digits with single
/// underscores between them (ASCII digits only). A value past 64 bits is still an integer.
pub(crate) fn parse_int(text: &str) -> Option<i128> {
    let text = py::strip(text);
    let (negative, digits) = match text.as_bytes().first()? {
        b'-' => (true, &text[1..]),
        b'+' => (false, &text[1..]),
        _ => (false, text),
    };
    let mut value: i128 = 0;
    let mut previous = b'_';
    for byte in digits.bytes() {
        match byte {
            b'0'..=b'9' => value = value.saturating_mul(10).saturating_add(i128::from(byte - b'0')),
            b'_' if previous != b'_' => {}
            _ => return None,
        }
        previous = byte;
    }
    (previous != b'_').then_some(if negative { -value } else { value })
}

/// A parsed query integer as the JSON value the core then checks.
pub(crate) fn query_int(value: i128) -> Value {
    i64::try_from(value).map_or_else(|_| json!(value as f64), Value::from)
}

/// A number of a stored document, as Python holds it: an `int` or a `float`.
#[derive(Clone, Copy)]
enum Num {
    Int(i64),
    Float(f64),
}

impl Num {
    fn of(value: &Value) -> Done<Self> {
        match value {
            Value::Number(number) => number.as_i64().map(Num::Int).or_else(|| number.as_f64().map(Num::Float)).ok_or_else(internal),
            _ => Err(internal()),
        }
    }

    fn float(self) -> f64 {
        match self {
            Num::Int(value) => value as f64,
            Num::Float(value) => value,
        }
    }

    fn plus(self, other: Num) -> Num {
        match (self, other) {
            (Num::Int(a), Num::Int(b)) => a.checked_add(b).map_or(Num::Float(a as f64 + b as f64), Num::Int),
            _ => Num::Float(self.float() + other.float()),
        }
    }

    fn minus(self, other: Num) -> Num {
        match (self, other) {
            (Num::Int(a), Num::Int(b)) => a.checked_sub(b).map_or(Num::Float(a as f64 - b as f64), Num::Int),
            _ => Num::Float(self.float() - other.float()),
        }
    }

    fn less(self, other: Num) -> bool {
        match (self, other) {
            (Num::Int(a), Num::Int(b)) => a < b,
            _ => self.float() < other.float(),
        }
    }

    /// `max(-100000, min(100000, value))`: a bound is the integer, anything between is kept.
    fn bounded(self) -> Num {
        let top = if self.less(Num::Int(100000)) { self } else { Num::Int(100000) };
        if Num::Int(-100000).less(top) { top } else { Num::Int(-100000) }
    }

    fn value(self) -> Done<Value> {
        match self {
            Num::Int(value) => Ok(Value::from(value)),
            Num::Float(value) => serde_json::Number::from_f64(value).map(Value::Number).ok_or_else(internal),
        }
    }
}

// Stored documents ---------------------------------------------------------------

/// A stored graph taken apart to be changed: its lists, and whatever else it held.
struct Document {
    nodes: Vec<Obj>,
    edges: Vec<Obj>,
    strokes: Option<Vec<Obj>>,
    rest: Obj,
}

fn objects(value: Value) -> Done<Vec<Obj>> {
    match value {
        Value::Array(items) => items.into_iter().map(|item| match item { Value::Object(map) => Ok(map), _ => Err(internal()) }).collect(),
        _ => Err(internal()),
    }
}

impl Document {
    fn of(row: &Obj) -> Done<Self> {
        let mut rest: Obj = serde_json::from_str(string(at(row, "graph_json")?)?)?;
        let nodes = objects(rest.shift_remove("nodes").ok_or_else(internal)?)?;
        let edges = objects(rest.shift_remove("edges").ok_or_else(internal)?)?;
        let strokes = rest.shift_remove("strokes").map(objects).transpose()?;
        Ok(Self { nodes, edges, strokes, rest })
    }

    /// The document as `graph` validates it.
    fn value(self) -> Value {
        let list = |items: Vec<Obj>| Value::Array(items.into_iter().map(Value::Object).collect());
        let mut out = Obj::new();
        out.insert("nodes".into(), list(self.nodes));
        out.insert("edges".into(), list(self.edges));
        if let Some(strokes) = self.strokes {
            out.insert("strokes".into(), list(strokes));
        }
        out.extend(self.rest);
        Value::Object(out)
    }
}

/// `_snapshot` before it is dumped: what an undo restores.
fn snapshot(row: &Obj) -> Done<Obj> {
    let mut out = Obj::new();
    for name in ["title", "mode", "graph_json", "viewport_json"] {
        out.insert(name.into(), at(row, name)?.clone());
    }
    Ok(out)
}

pub(crate) fn parsed(row: &Obj, column: &str) -> Done<Value> {
    Ok(serde_json::from_str(string(at(row, column)?)?)?)
}

/// `_revision`.
pub(crate) fn revision(row: &Obj, value: &Value) -> Done<()> {
    if py::int(value).is_some_and(|given| at(row, "revision").is_ok_and(|stored| py::eq_int(stored, given))) {
        Ok(())
    } else {
        Err(fail(409, "Map changed in another window. Export your local edits or reload before editing."))
    }
}

fn count(conn: &Connection) -> Done<usize> {
    Ok(conn.query_row("SELECT count(*) FROM schema_maps", [], |row| row.get::<_, i64>(0))? as usize)
}

/// The maps as one request sees them: without ApplyAI, job and company sources keep their
/// link but show no live data (`applyai_scope`).
pub(crate) struct Maps<'a> {
    pub(crate) conn: &'a Connection,
    pub(crate) applyai: bool,
}

impl Maps<'_> {
    /// `_row`.
    pub(crate) fn row(&self, item_id: &Value) -> Done<Obj> {
        self.row_at(integer(item_id, "ID", 1)?)
    }

    fn row_at(&self, item_id: i64) -> Done<Obj> {
        db::row(self.conn, "SELECT * FROM schema_maps WHERE id=?", [item_id])?.ok_or_else(|| bad("Map not found"))
    }

    /// `_source`: a node's link to a record, checked against the node's kind.
    fn source(&self, value: &Value, kind: &Value) -> Done<Value> {
        if value.is_null() {
            return Ok(Value::Null);
        }
        let value = fields(value, &["kind", "id"])?;
        let named = get(value, "kind").as_str();
        if named == Some("calendar_task") {
            let source_id = text(get(value, "id"), "Task ID", 200, true)?;
            if kind.as_str() != Some("task") || !db::exists(self.conn, "SELECT id FROM calendar_tasks WHERE id=? AND deleted=0", [&source_id])? {
                return Err(bad("Calendar task source is missing or has the wrong node kind"));
            }
            return Ok(json!({"kind": "calendar_task", "id": source_id}));
        }
        if named == Some("note_page") {
            let source_id = integer(get(value, "id"), "ID", 1)?;
            if !db::exists(self.conn, "SELECT id FROM note_page_index WHERE id=?", [source_id])? {
                return Err(bad("Note page no longer exists"));
            }
            return Ok(json!({"kind": "note_page", "id": source_id}));
        }
        let expected = match member(kind)? {
            Some("class") => Some(("subject", "study_subjects")),
            Some("job") => Some(("job", "jobs")),
            Some("company") => Some(("company", "companies")),
            _ => None,
        };
        let Some((name, table)) = expected.filter(|(name, _)| named == Some(name)) else {
            return Err(bad("Source reference does not match the node kind"));
        };
        let source_id = integer(get(value, "id"), "ID", 1)?;
        if !db::exists(self.conn, &format!("SELECT id FROM {table} WHERE id=?"), [source_id])? {
            return Err(bad("The selected source no longer exists; unlink it before saving"));
        }
        Ok(json!({"kind": name, "id": source_id}))
    }

    /// `graph`: a document as it is stored, or the first refusal.
    pub(crate) fn graph(&self, value: &Value, portable: bool) -> Done<Value> {
        let value = fields(value, &["nodes", "edges", "strokes"])?;
        let (Some(nodes), Some(edges)) = (get(value, "nodes").as_array().filter(|items| items.len() <= MAX_NODES),
                                          get(value, "edges").as_array().filter(|items| items.len() <= MAX_EDGES)) else {
            return Err(bad(format!("Maps support at most {MAX_NODES} nodes and {MAX_EDGES} connections")));
        };
        let strokes: &[Value] = match value.get("strokes") {
            None => &[],
            Some(Value::Array(items)) if items.len() <= MAX_STROKES => items,
            Some(_) => return Err(bad(format!("Maps support at most {MAX_STROKES} ink strokes"))),
        };
        let (empty, off) = (json!(""), json!(false));
        let (mut ids, mut sources) = (HashSet::new(), HashSet::new());
        let mut clean_nodes = Vec::with_capacity(nodes.len());
        for node in nodes {
            let node = fields(node, &["id", "kind", "label", "notes", "x", "y", "url", "source", "collapsed", "map_id", "free", "color"])?;
            let ident = key(get(node, "id"))?;
            let kind = get(node, "kind");
            if !kind.as_str().is_some_and(|kind| KINDS.contains(&kind)) || ids.contains(&ident) {
                return Err(bad("Node kinds must be recognized and node IDs unique"));
            }
            ids.insert(ident.clone());
            if portable && !get(node, "source").is_null() {
                return Err(bad("Portable imports cannot contain local source IDs; export first or explicitly remove source references"));
            }
            let source = self.source(get(node, "source"), kind)?;
            if let Some(found) = source.as_object() {
                if !sources.insert(identity(found)?) {
                    return Err(bad("A source may only appear once in a map"));
                }
            }
            if !node.get("collapsed").unwrap_or(&off).is_boolean() {
                return Err(bad("Collapsed must be true or false"));
            }
            // A free node keeps its own position instead of following the automatic tree layout.
            if !node.get("free").unwrap_or(&off).is_boolean() {
                return Err(bad("Free must be true or false"));
            }
            let color = get(node, "color");
            if !color.is_null() && !color.as_str().is_some_and(|color| COLORS.contains(&color)) {
                return Err(bad("Colour must be one of the theme palette slots c1–c6"));
            }
            let linked_map = get(node, "map_id");
            if !linked_map.is_null() {
                if portable {
                    return Err(bad("Portable imports cannot contain local map IDs"));
                }
                self.row(linked_map)?;
            }
            let mut clean = Obj::new();
            clean.insert("id".into(), json!(ident));
            clean.insert("kind".into(), kind.clone());
            clean.insert("label".into(), json!(text(get(node, "label"), "Label", 200, true)?));
            clean.insert("notes".into(), json!(text(node.get("notes").unwrap_or(&empty), "Notes", 10000, false)?));
            clean.insert("x".into(), number(get(node, "x"), "Node x", COORDINATE)?);
            clean.insert("y".into(), number(get(node, "y"), "Node y", COORDINATE)?);
            clean.insert("url".into(), json!(link(node.get("url").unwrap_or(&empty))?));
            clean.insert("source".into(), source);
            if let Some(collapsed) = node.get("collapsed") {
                clean.insert("collapsed".into(), collapsed.clone());
            }
            if py::truthy(get(node, "free")) {
                clean.insert("free".into(), json!(true));
            }
            if !color.is_null() {
                clean.insert("color".into(), color.clone());
            }
            if !linked_map.is_null() {
                clean.insert("map_id".into(), linked_map.clone());
            }
            clean_nodes.push(Value::Object(clean));
        }
        let mut edge_ids = HashSet::new();
        let mut clean_edges = Vec::with_capacity(edges.len());
        for edge in edges {
            let edge = fields(edge, &["id", "from", "to", "label", "kind"])?;
            let ident = key(get(edge, "id"))?;
            let (Some(start), Some(end)) = (get(edge, "from").as_str().filter(|start| ids.contains(*start)),
                                            get(edge, "to").as_str().filter(|end| ids.contains(*end))) else {
                return Err(bad("Every connection endpoint must be a node in this map"));
            };
            if !edge_ids.insert(ident.clone()) {
                return Err(bad("Connection IDs must be unique"));
            }
            // A cross connection links any two nodes outside the tree; without a kind, an edge may shape the tree.
            let cross = match edge.get("kind").map(Value::as_str) {
                None | Some(Some("tree")) => false,
                Some(Some("cross")) => true,
                Some(_) => return Err(bad("Connection kind must be tree or cross")),
            };
            let mut clean = Obj::new();
            clean.insert("id".into(), json!(ident));
            clean.insert("from".into(), json!(start));
            clean.insert("to".into(), json!(end));
            clean.insert("label".into(), json!(text(edge.get("label").unwrap_or(&empty), "Connection label", 200, false)?));
            if cross {
                clean.insert("kind".into(), json!("cross"));
            }
            clean_edges.push(Value::Object(clean));
        }
        let mut stroke_ids = HashSet::new();
        let mut inked = Vec::with_capacity(strokes.len());
        for stroke in strokes {
            // Ink belongs to one node and is stored relative to it, so it moves and folds with that node.
            let stroke = fields(stroke, &["id", "node", "points"])?;
            let ident = key(get(stroke, "id"))?;
            if !stroke_ids.insert(ident.clone()) {
                return Err(bad("Ink stroke IDs must be unique"));
            }
            let Some(node) = member(get(stroke, "node"))?.filter(|node| ids.contains(*node)) else {
                return Err(bad("Ink must be attached to a node in this map"));
            };
            let Some(points) = get(stroke, "points").as_array().filter(|points| (1..=MAX_STROKE_POINTS).contains(&points.len())) else {
                return Err(bad(format!("Ink strokes need 1 to {MAX_STROKE_POINTS} points")));
            };
            let mut clean = Vec::with_capacity(points.len());
            for point in points {
                let Some(point) = point.as_array().filter(|point| point.len() == 3) else { return Err(bad("Ink points must be [x, y, pressure]")) };
                clean.push(json!([number(&point[0], "Ink x", COORDINATE)?, number(&point[1], "Ink y", COORDINATE)?,
                                  number(&point[2], "Pressure", (0.0, 1.0, "0 and 1"))?]));
            }
            inked.push(json!({"id": ident, "node": node, "points": clean}));
        }
        let mut result = Obj::new();
        result.insert("nodes".into(), Value::Array(clean_nodes));
        result.insert("edges".into(), Value::Array(clean_edges));
        if !inked.is_empty() {
            result.insert("strokes".into(), Value::Array(inked));
        }
        let result = Value::Object(result);
        if py::dumps(&result).len() > MAX_BYTES - 4096 {
            return Err(bad("Graph exceeds the 1 MB size limit"));
        }
        Ok(result)
    }

    /// `_lookup`: the read-only live view of one source record; a deleted one reports missing.
    fn lookup(&self, kind: &str, source_id: &Value) -> Done<Obj> {
        let flag = |name: &str| Obj::from_iter([(name.to_owned(), json!(true))]);
        if APPLYAI_KINDS.contains(&kind) && !self.applyai {
            return Ok(flag("unavailable"));
        }
        let id = [db::bind(source_id)?];
        let found = match kind {
            "calendar_task" => {
                let Some(row) = db::row(self.conn, "SELECT data FROM calendar_tasks WHERE id=? AND deleted=0", id)? else { return Ok(flag("missing")) };
                let data: Value = parsed(&row, "data")?;
                let title = data.as_object().ok_or_else(internal)?.get("title").cloned().unwrap_or_else(|| json!(""));
                return Ok(Obj::from_iter([("label".to_owned(), title)]));
            }
            "note_page" => db::row(self.conn, "SELECT title AS label FROM note_page_index WHERE id=?", id)?,
            "subject" => db::row(self.conn, "SELECT name AS label FROM study_subjects WHERE id=?", id)?,
            "company" => db::row(self.conn, "SELECT display_name AS label,website AS url FROM companies WHERE id=?", id)?,
            _ => db::row(self.conn, "SELECT j.title AS label,j.url,j.queue_state,j.closed_at,a.status,
            c.display_name AS company FROM jobs j JOIN companies c ON c.id=j.company_id
            LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=?", id)?,
        };
        let Some(mut value) = found else { return Ok(flag("missing")) };
        if let Some(link) = value.get_mut("url") {
            *link = json!(safe_link(link));
        }
        Ok(value)
    }

    /// `_live`.
    fn live(&self, document: &Value) -> Done<Obj> {
        let mut out = Obj::new();
        for node in at(document.as_object().ok_or_else(internal)?, "nodes")?.as_array().ok_or_else(internal)? {
            let node = node.as_object().ok_or_else(internal)?;
            if let Some(source) = at(node, "source")?.as_object() {
                out.insert(string(at(node, "id")?)?.to_owned(), Value::Object(self.lookup(string(at(source, "kind")?)?, at(source, "id")?)?));
            } else if !at(node, "source")?.is_null() {
                return Err(internal());
            }
        }
        Ok(out)
    }

    fn load(&self, item_id: &Value) -> Done<Value> {
        self.loaded(self.row(item_id)?)
    }

    pub(crate) fn load_at(&self, item_id: i64) -> Done<Value> {
        self.loaded(self.row_at(item_id)?)
    }

    fn loaded(&self, row: Obj) -> Done<Value> {
        let document = parsed(&row, "graph_json")?;
        let previous: Obj = match at(&row, "previous_json")?.as_str().filter(|text| !text.is_empty()) {
            Some(text) => serde_json::from_str(text)?,
            None => Obj::new(),
        };
        let redo = previous.get("redo").cloned().unwrap_or(json!(false));
        let live = self.live(&document)?;
        Ok(json!({"id": row["id"], "title": row["title"], "mode": row["mode"], "revision": row["revision"],
                  "graph": document, "viewport": parsed(&row, "viewport_json")?,
                  "can_undo": !previous.is_empty() && !py::truthy(&redo), "can_redo": redo,
                  "paired_history": previous.contains_key("partner"), "live": live}))
    }

    fn workspace(&self) -> Done<Value> {
        Ok(json!({"maps": db::rows(self.conn, "SELECT id,title,mode,revision,updated_at FROM schema_maps ORDER BY updated_at DESC,id DESC", [])?,
                  "subjects": db::rows(self.conn, "SELECT id,name FROM study_subjects ORDER BY name,id", [])?}))
    }

    fn job_options(&self, search: &str, offset: &Value) -> Done<Value> {
        let search = text(&json!(search), "Search", 200, false)?;
        let offset = py::int(offset).filter(|offset| (0..=1_000_000).contains(offset))
            .ok_or_else(|| bad("Offset must be an integer between 0 and 1000000"))?;
        let mut rows = db::rows(self.conn, "SELECT j.id,j.title,c.display_name AS company,j.url,j.queue_state,a.status
        FROM jobs j JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id
        WHERE instr(lower(j.title || ' ' || c.display_name),lower(?))>0 ORDER BY j.id DESC LIMIT 51 OFFSET ?", params![search, offset])?;
        let more = rows.len() > 50;
        rows.truncate(50);
        for row in &mut rows {
            let link = safe_link(get(row, "url"));
            row.insert("url".into(), json!(link));
        }
        Ok(json!({"jobs": rows, "more": more}))
    }

    /// `_write` without its answer: the new state, and the one before it as the undo snapshot.
    fn store(&self, row: &Obj, title: &str, document: &Value, view: &Value) -> Done<()> {
        self.conn.execute("UPDATE schema_maps SET title=?,graph_json=?,viewport_json=?,previous_json=?,
        revision=revision+1,updated_at=? WHERE id=?",
            params![title, py::dumps(document), py::dumps(view), py::dumps(&Value::Object(snapshot(row)?)), time::now_iso(), id_of(row)?])?;
        Ok(())
    }

    /// `_write`.
    pub(crate) fn write(&self, row: &Obj, title: &str, document: &Value, view: &Value) -> Done<Value> {
        self.store(row, title, document, view)?;
        self.load_at(id_of(row)?)
    }

    fn insert(&self, title: &Value, mode: &Value, graph_json: &str, viewport_json: &str) -> Done<i64> {
        self.conn.execute("INSERT INTO schema_maps(title,mode,graph_json,viewport_json,updated_at) VALUES(?,?,?,?,?)",
            params![db::bind(title)?, db::bind(mode)?, graph_json, viewport_json, time::now_iso()])?;
        Ok(self.conn.last_insert_rowid())
    }

    fn create(&self, body: &Obj) -> Done<Value> {
        if !known(body, &["title", "mode", "document"]) {
            return Err(unknown_field());
        }
        let title = text(get(body, "title"), "Title", 200, true)?;
        let general = json!("general");
        let mode = body.get("mode").unwrap_or(&general);
        if !mode.as_str().is_some_and(|mode| MODES.contains(&mode)) {
            return Err(bad("Choose study, general or jobs mode"));
        }
        db::transaction(self.conn, true, || {
            if count(self.conn)? >= MAX_MAPS {
                return Err(bad("At most 100 maps are supported; export and delete an old map first"));
            }
            let (mut document, mut view) = (json!({"nodes": [], "edges": []}), json!({"x": 40, "y": 40, "zoom": 1}));
            if let Some(imported) = body.get("document") {
                let portable = fields(imported, &["format", "version", "title", "mode", "graph", "viewport"])?;
                if get(portable, "format").as_str() != Some("kitty-schema") || py::int(get(portable, "version")) != Some(1) {
                    return Err(bad("Unsupported portable map format/version"));
                }
                text(get(portable, "title"), "Imported title", 200, true)?;
                if !get(portable, "mode").as_str().is_some_and(|mode| MODES.contains(&mode)) {
                    return Err(bad("Imported mode must be study, general or jobs"));
                }
                if py::dumps(imported).len() > MAX_BYTES {
                    return Err(bad("Portable map exceeds the 1 MB size limit"));
                }
                document = self.graph(get(portable, "graph"), true)?;
                view = viewport(get(portable, "viewport"))?;
            }
            let created = self.insert(&json!(title), mode, &py::dumps(&document), &py::dumps(&view))?;
            self.load_at(created)
        })
    }

    fn save(&self, body: &Obj) -> Done<Value> {
        if !known(body, &["id", "revision", "title", "graph", "viewport"]) {
            return Err(unknown_field());
        }
        db::transaction(self.conn, true, || {
            let row = self.row(get(body, "id"))?;
            revision(&row, get(body, "revision"))?;
            let title = text(body.get("title").unwrap_or(at(&row, "title")?), "Title", 200, true)?;
            let document = self.graph(get(body, "graph"), false)?;
            self.write(&row, &title, &document, &viewport(get(body, "viewport"))?)
        })
    }

    /// Atomically split a branch or move a root into another saved map.
    fn transfer(&self, body: &Obj) -> Done<Value> {
        if !known(body, &["id", "revision", "node_id", "target_id", "target_revision"]) {
            return Err(unknown_field());
        }
        db::transaction(self.conn, true, || {
            let row = self.row(get(body, "id"))?;
            revision(&row, get(body, "revision"))?;
            let mut source = Document::of(&row)?;
            let picked = branch(&source, get(body, "node_id"))?;
            let root = source.nodes.iter().find(|node| node.get("id") == body.get("node_id")).ok_or_else(internal)?.clone();
            let root_id = string(at(&root, "id")?)?.to_owned();
            let splitting = get(body, "target_id").is_null();
            let (row_id, cross) = (id_of(&row)?, json!("cross"));
            let other = if splitting {
                if count(self.conn)? >= MAX_MAPS {
                    return Err(bad("At most 100 maps are supported"));
                }
                let created = self.insert(at(&root, "label")?, at(&row, "mode")?, "{\"nodes\":[],\"edges\":[]}", string(at(&row, "viewport_json")?)?)?;
                self.row_at(created)?
            } else {
                let other = self.row(get(body, "target_id"))?;
                if id_of(&other)? == row_id {
                    return Err(bad("Choose another map"));
                }
                revision(&other, get(body, "target_revision"))?;
                for edge in &source.edges {
                    if string(at(edge, "to")?)? == root_id && string(at(edge, "from")?)? != root_id && get(edge, "kind") != &cross {
                        return Err(bad("Only a root can be dropped into another map"));
                    }
                }
                other
            };
            let other_id = id_of(&other)?;
            let mut destination = Document::of(&other)?;
            let mut remap = HashMap::new();
            for ident in &picked {
                remap.insert(ident.clone(), uuid()?);
            }
            let moved_to = |ident: &Value| -> Done<Value> { Ok(json!(remap.get(string(ident)?).ok_or_else(internal)?)) };
            let new_root = remap.get(&root_id).ok_or_else(internal)?.clone();
            let mut moved = Vec::new();
            for node in &source.nodes {
                if picked.contains(string(at(node, "id")?)?) {
                    let mut copy = node.clone();
                    copy.insert("id".into(), moved_to(at(node, "id")?)?);
                    if py::eq_int(get(&copy, "map_id"), other_id) {
                        copy.shift_remove("map_id");
                    }
                    if splitting && copy["id"] == new_root {
                        copy.insert("map_id".into(), json!(row_id));
                    }
                    moved.push(copy);
                }
            }
            // A node the other map kept in this branch's place is replaced by the branch.
            let mut placeholder = None;
            if !splitting {
                if let Some(index) = destination.nodes.iter().position(|node| py::eq_int(get(node, "map_id"), row_id) && node.get("label") == root.get("label")) {
                    let found = destination.nodes.remove(index);
                    let old = at(&found, "id")?.clone();
                    for edge in &mut destination.edges {
                        for endpoint in ["from", "to"] {
                            if at(edge, endpoint)? == &old {
                                edge.insert(endpoint.into(), json!(new_root));
                            }
                        }
                    }
                    for stroke in destination.strokes.iter_mut().flatten() {
                        if at(stroke, "node")? == &old {
                            stroke.insert("node".into(), json!(new_root));
                        }
                    }
                    placeholder = Some(found);
                }
            }
            let existing = destination.nodes.len();
            if let Some(anchor) = placeholder.as_ref().or(destination.nodes.first()) {
                let (mut x, mut y) = (Num::of(at(anchor, "x")?)?, Num::of(at(anchor, "y")?)?);
                if placeholder.is_none() {
                    x = x.plus(Num::Int(300));
                    for node in &destination.nodes {
                        let other_y = Num::of(at(node, "y")?)?;
                        if y.less(other_y) {
                            y = other_y;
                        }
                    }
                    y = y.plus(Num::Int(90));
                }
                let (root_x, root_y) = (Num::of(at(&root, "x")?)?, Num::of(at(&root, "y")?)?);
                for node in &mut moved {
                    let (node_x, node_y) = (Num::of(at(node, "x")?)?, Num::of(at(node, "y")?)?);
                    node.insert("x".into(), node_x.plus(x).minus(root_x).bounded().value()?);
                    node.insert("y".into(), node_y.plus(y).minus(root_y).bounded().value()?);
                }
            }
            destination.nodes.extend(moved);
            for edge in &source.edges {
                if picked.contains(string(at(edge, "from")?)?) && picked.contains(string(at(edge, "to")?)?) {
                    let mut copy = edge.clone();
                    copy.insert("id".into(), json!(uuid()?));
                    copy.insert("from".into(), moved_to(at(edge, "from")?)?);
                    copy.insert("to".into(), moved_to(at(edge, "to")?)?);
                    destination.edges.push(copy);
                }
            }
            // Ink travels with the nodes it is attached to.
            let mut strokes = destination.strokes.take().unwrap_or_default();
            let mut kept = Vec::new();
            for stroke in source.strokes.take().unwrap_or_default() {
                if picked.contains(string(at(&stroke, "node")?)?) {
                    let mut copy = stroke.clone();
                    copy.insert("id".into(), json!(uuid()?));
                    copy.insert("node".into(), moved_to(at(&stroke, "node")?)?);
                    strokes.push(copy);
                } else {
                    kept.push(stroke);
                }
            }
            (destination.strokes, source.strokes) = (Some(strokes), Some(kept));
            if !splitting && existing > 0 && placeholder.is_none() {
                let mut parent = &destination.nodes[0];
                for node in &destination.nodes[..existing] {
                    let mut entered = false;
                    for edge in &destination.edges {
                        entered = entered || (at(edge, "to")? == at(node, "id")? && get(edge, "kind") != &cross);
                    }
                    if !entered {
                        parent = node;
                        break;
                    }
                }
                let edge = json!({"id": uuid()?, "from": at(parent, "id")?, "to": new_root, "label": ""});
                destination.edges.push(edge.as_object().ok_or_else(internal)?.clone());
            }
            let mut nodes = Vec::new();
            for mut node in std::mem::take(&mut source.nodes) {
                let ident = string(at(&node, "id")?)?;
                if !picked.contains(ident) {
                    nodes.push(node);
                } else if splitting && ident == root_id {
                    node.insert("map_id".into(), json!(other_id));
                    nodes.push(node);
                }
            }
            source.nodes = nodes;
            let mut edges = Vec::new();
            for edge in std::mem::take(&mut source.edges) {
                let (from, to) = (string(at(&edge, "from")?)?, string(at(&edge, "to")?)?);
                if (!picked.contains(from) && !picked.contains(to)) || (splitting && to == root_id && !picked.contains(from)) {
                    edges.push(edge);
                }
            }
            source.edges = edges;
            self.store(&row, string(at(&row, "title")?)?, &self.graph(&source.value(), false)?, &parsed(&row, "viewport_json")?)?;
            self.store(&other, string(at(&other, "title")?)?, &self.graph(&destination.value(), false)?, &parsed(&other, "viewport_json")?)?;
            // Each map's undo snapshot names its partner, so the two are undone together.
            for (own, partner) in [(&row, &other), (&other, &row)] {
                let mut previous = snapshot(own)?;
                previous.insert("partner".into(), json!({"id": partner["id"], "revision": at(partner, "revision")?.as_i64().ok_or_else(internal)? + 1,
                                                         "snapshot": snapshot(partner)?}));
                self.conn.execute("UPDATE schema_maps SET previous_json=? WHERE id=?", params![py::dumps(&Value::Object(previous)), id_of(own)?])?;
            }
            self.load_at(other_id)
        })
    }

    /// Insert a linked or free node without overwriting another editor's work.
    fn add_node(&self, body: &Obj) -> Done<Value> {
        if !known(body, &["map_id", "revision", "label", "kind", "parent_id", "source"]) {
            return Err(unknown_field());
        }
        db::transaction(self.conn, true, || {
            let row = self.row(get(body, "map_id"))?;
            revision(&row, get(body, "revision"))?;
            let mut document = Document::of(&row)?;
            let topic = json!("topic");
            let kind = body.get("kind").unwrap_or(&topic);
            let source = self.source(get(body, "source"), kind)?;
            if !source.is_null() {
                for node in &document.nodes {
                    if py::eq(at(node, "source")?, &source) {
                        return self.load_at(id_of(&row)?);
                    }
                }
            }
            let parent = get(body, "parent_id");
            if !parent.is_null() {
                let mut found = false;
                for node in &document.nodes {
                    found = found || py::eq(at(node, "id")?, parent);
                }
                if !found {
                    return Err(bad("Parent node not found"));
                }
            }
            let node_id = uuid()?;
            let node = json!({"id": node_id, "kind": kind, "label": get(body, "label"), "notes": "", "url": "", "source": source,
                              "x": 280 * document.nodes.len(), "y": 0});
            document.nodes.push(node.as_object().ok_or_else(internal)?.clone());
            if !parent.is_null() {
                let edge = json!({"id": uuid()?, "from": parent, "to": node_id, "label": ""});
                document.edges.push(edge.as_object().ok_or_else(internal)?.clone());
            }
            self.write(&row, string(at(&row, "title")?)?, &self.graph(&document.value(), false)?, &parsed(&row, "viewport_json")?)
        })
    }

    fn delete(&self, body: &Obj) -> Done<Value> {
        if !known(body, &["id", "revision", "confirm"]) {
            return Err(unknown_field());
        }
        if get(body, "confirm") != &json!(true) {
            return Err(bad("Confirm map deletion first"));
        }
        db::transaction(self.conn, true, || {
            let row = self.row(get(body, "id"))?;
            revision(&row, get(body, "revision"))?;
            self.conn.execute("DELETE FROM schema_maps WHERE id=?", [id_of(&row)?])?;
            Ok(json!({"deleted": row["id"]}))
        })
    }

    fn undo(&self, body: &Obj) -> Done<Value> {
        if !known(body, &["id", "revision", "redo"]) {
            return Err(unknown_field());
        }
        db::transaction(self.conn, true, || {
            let row = self.row(get(body, "id"))?;
            revision(&row, get(body, "revision"))?;
            let Some(stored) = at(&row, "previous_json")?.as_str() else { return Err(bad("No saved change to undo")) };
            let previous: Obj = serde_json::from_str(stored)?;
            let redone = py::truthy(get(&previous, "redo"));
            let wanted = py::truthy(get(body, "redo"));
            if wanted != redone {
                return Err(bad(if wanted { "No saved change to redo" } else { "No saved change to undo" }));
            }
            let row_id = id_of(&row)?;
            let restore = |saved: &Obj, previous_json: Option<String>, item_id: i64| -> Done<()> {
                let mut values = Vec::new();
                for name in ["title", "mode", "graph_json", "viewport_json"] {
                    values.push(db::bind(at(saved, name)?)?);
                }
                self.conn.execute("UPDATE schema_maps SET title=?,mode=?,graph_json=?,viewport_json=?,previous_json=?,
                    revision=revision+1,updated_at=? WHERE id=?",
                    params![values[0], values[1], values[2], values[3], previous_json, time::now_iso(), item_id])?;
                Ok(())
            };
            if let Some(paired) = previous.get("partner") {
                let paired = paired.as_object().ok_or_else(internal)?;
                let partner = self.row(at(paired, "id")?)?;
                revision(&partner, at(paired, "revision")?)?;
                let (own_before, partner_before) = (snapshot(&row)?, snapshot(&partner)?);
                let partner_saved = at(paired, "snapshot")?.as_object().ok_or_else(internal)?;
                for (own, saved, other, other_before) in [(&row, &previous, &partner, &partner_before), (&partner, partner_saved, &row, &own_before)] {
                    let mut reverse = snapshot(own)?;
                    reverse.insert("redo".into(), json!(!redone));
                    reverse.insert("partner".into(), json!({"id": other["id"], "revision": at(other, "revision")?.as_i64().ok_or_else(internal)? + 1,
                                                            "snapshot": other_before}));
                    restore(saved, Some(py::dumps(&Value::Object(reverse))), id_of(own)?)?;
                }
                return self.load_at(row_id);
            }
            // Missing sources can remain visible after undo; they are never rebound.
            restore(&previous, None, row_id)?;
            self.load_at(row_id)
        })
    }

    fn export(&self, item_id: &Value) -> Done<Value> {
        let Value::Object(mut item) = self.load(item_id)? else { return Err(internal()) };
        let live = item["live"].as_object().ok_or_else(internal)?.clone();
        for node in item["graph"]["nodes"].as_array_mut().ok_or_else(internal)? {
            let node = node.as_object_mut().ok_or_else(internal)?;
            let shown = live.get(string(at(node, "id")?)?).and_then(Value::as_object);
            let label = shown.and_then(|shown| shown.get("label")).filter(|label| py::truthy(label)).unwrap_or(at(node, "label")?);
            let label: String = string(label)?.chars().take(200).collect();
            node.insert("label".into(), json!(label));
            if let Some(link) = shown.and_then(|shown| shown.get("url")) {
                node.insert("url".into(), link.clone());
            }
            node.insert("source".into(), Value::Null);
            node.shift_remove("map_id");
        }
        Ok(json!({"format": "kitty-schema", "version": 1, "title": item["title"], "mode": item["mode"],
                  "graph": item.shift_remove("graph"), "viewport": item.shift_remove("viewport")}))
    }

    fn import_jobs(&self, body: &Obj) -> Done<Value> {
        if !known(body, &["id", "revision", "job_ids"]) {
            return Err(unknown_field());
        }
        let Some(given) = get(body, "job_ids").as_array().filter(|ids| (1..=50).contains(&ids.len())) else {
            return Err(bad("Select between 1 and 50 tracked jobs"));
        };
        let mut ids: Vec<i64> = Vec::new();
        for value in given {
            let job_id = integer(value, "ID", 1)?;
            if !ids.contains(&job_id) {
                ids.push(job_id);
            }
        }
        db::transaction(self.conn, true, || {
            let row = self.row(get(body, "id"))?;
            revision(&row, get(body, "revision"))?;
            if at(&row, "mode")?.as_str() != Some("jobs") {
                return Err(bad("Import tracked jobs into a jobs-mode map"));
            }
            let mut document = Document::of(&row)?;
            let mut sources = HashMap::new();
            for node in &document.nodes {
                let source = at(node, "source")?;
                if py::truthy(source) {
                    sources.insert(identity(source.as_object().ok_or_else(internal)?)?, string(at(node, "id")?)?.to_owned());
                }
            }
            let mut changed = false;
            for job_id in ids {
                let Some(job) = db::row(self.conn, "SELECT j.id,j.title,j.company_id,j.url,c.display_name FROM jobs j
                JOIN companies c ON c.id=j.company_id WHERE j.id=?", [job_id])? else {
                    return Err(bad("A selected job no longer exists"));
                };
                let company_id = at(&job, "company_id")?.clone();
                let mut ends = Vec::new();
                for (kind, source_id, label, link) in [("company", company_id, at(&job, "display_name")?, String::new()),
                                                        ("job", json!(job_id), at(&job, "title")?, safe_link(at(&job, "url")?))] {
                    let source = json!({"kind": kind, "id": source_id});
                    let named = identity(source.as_object().ok_or_else(internal)?)?;
                    if !sources.contains_key(&named) {
                        let (ident, index) = (uuid()?, document.nodes.len());
                        let label: String = string(label)?.chars().take(200).collect();
                        let node = json!({"id": ident, "kind": kind, "label": label, "notes": "",
                                          "x": (index % 4) * 240, "y": (index / 4) * 130, "url": link, "source": source});
                        document.nodes.push(node.as_object().ok_or_else(internal)?.clone());
                        sources.insert(named.clone(), ident);
                        changed = true;
                    }
                    ends.push(sources[&named].clone());
                }
                let (end, start) = (&ends[0], &ends[1]);
                let mut joined = false;
                for edge in &document.edges {
                    joined = joined || (string(at(edge, "from")?)? == start && string(at(edge, "to")?)? == end && at(edge, "label")?.as_str() == Some("at"));
                }
                if !joined {
                    let edge = json!({"id": uuid()?, "from": start, "to": end, "label": "at"});
                    document.edges.push(edge.as_object().ok_or_else(internal)?.clone());
                    changed = true;
                }
            }
            if !changed {
                return self.load_at(id_of(&row)?);
            }
            self.write(&row, string(at(&row, "title")?)?, &self.graph(&document.value(), false)?, &parsed(&row, "viewport_json")?)
        })
    }
}

/// A source's `(kind, id)` as one comparable key.
fn identity(source: &Obj) -> Done<String> {
    Ok(format!("{}\u{0}{}", string(at(source, "kind")?)?, py::dumps(at(source, "id")?)))
}

/// `_branch`: a node and everything below it along the tree connections.
fn branch(document: &Document, root: &Value) -> Done<HashSet<String>> {
    let wanted = member(root)?;
    let mut found = false;
    for node in &document.nodes {
        found = found || (wanted.is_some() && at(node, "id")?.as_str() == wanted);
    }
    let Some(root) = wanted.filter(|_| found) else { return Err(bad("Branch root not found")) };
    let mut parents: HashMap<&str, &str> = HashMap::new();
    for edge in &document.edges {
        let (from, to) = (string(at(edge, "from")?)?, string(at(edge, "to")?)?);
        if get(edge, "kind").as_str() != Some("cross") && from != to {
            parents.entry(to).or_insert(from);
        }
    }
    let mut picked = HashSet::from([root.to_owned()]);
    loop {
        let added: Vec<String> = parents.iter().filter(|(child, parent)| picked.contains(**parent) && !picked.contains(**child)).map(|(child, _)| (*child).to_owned()).collect();
        if added.is_empty() {
            return Ok(picked);
        }
        picked.extend(added);
    }
}

// The web layer ------------------------------------------------------------------

/// `_applyai_sources`: the job and company records a list of nodes links.
fn applyai_sources(nodes: &[Value]) -> Done<HashSet<(String, String)>> {
    let mut out = HashSet::new();
    for node in nodes {
        let Some(source) = node.as_object().and_then(|node| node.get("source")).and_then(Value::as_object) else { continue };
        let Some(kind) = member(get(source, "kind"))?.filter(|kind| APPLYAI_KINDS.contains(kind)) else { continue };
        // `str(id)`: only an integer or its digits can name a stored source.
        let ident = match get(source, "id") {
            Value::String(text) => text.clone(),
            other => match py::int(other) {
                Some(number) => number.to_string(),
                None => format!("\u{0}{}", py::dumps(other)),
            },
        };
        out.insert((kind.to_owned(), ident));
    }
    Ok(out)
}

fn nodes_of(holder: Option<&Value>) -> &[Value] {
    holder.and_then(Value::as_object).and_then(|graph| graph.get("nodes")).and_then(Value::as_array).map_or(&[], Vec::as_slice)
}

/// Without ApplyAI no job or company link may be created; existing links are kept.
fn reject_new_applyai_sources(conn: &Connection, path: &str, body: &Obj) -> Done<()> {
    let (found, existing) = match path {
        "/api/schema/add-node" => (applyai_sources(&[Value::Object(body.clone())])?, HashSet::new()),
        "/api/schema/save" => {
            let found = applyai_sources(nodes_of(body.get("graph")))?;
            // `isinstance(id, int)`: a boolean is one.
            let stored: Option<String> = match get(body, "id") {
                id @ (Value::Bool(_) | Value::Number(_)) if !id.is_f64() => {
                    let id = id.as_i64().or_else(|| id.as_bool().map(i64::from)).ok_or_else(internal)?;
                    let mut statement = conn.prepare("SELECT graph_json FROM schema_maps WHERE id=?")?;
                    let mut rows = statement.query([id])?;
                    match rows.next()? {
                        Some(row) => Some(row.get(0)?),
                        None => None,
                    }
                }
                _ => None,
            };
            let existing = match stored {
                Some(text) => {
                    let document: Obj = serde_json::from_str(&text)?;
                    applyai_sources(document.get("nodes").and_then(Value::as_array).map_or(&[], Vec::as_slice))?
                }
                None => HashSet::new(),
            };
            (found, existing)
        }
        "/api/schema/create" | "/api/schema/import" => {
            (applyai_sources(nodes_of(body.get("document").and_then(Value::as_object).and_then(|document| document.get("graph"))))?, HashSet::new())
        }
        _ => return Ok(()),
    };
    if found.difference(&existing).next().is_some() {
        return Err(disabled("applyai"));
    }
    Ok(())
}

fn map_id(call: &Call) -> Done<Value> {
    if call.query.len() != 1 || call.get("id").is_none() {
        return Err(bad("Provide a map ID only"));
    }
    parse_int(call.get("id").unwrap_or("")).map(query_int).ok_or_else(|| bad("Map ID must be an integer"))
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let maps = Maps { conn: ctx.conn, applyai: ctx.has("applyai") };
    if let Some(body) = call.body {
        if !maps.applyai {
            reject_new_applyai_sources(ctx.conn, call.path, body)?;
        }
        return Ok(Some(match call.path {
            "/api/schema/create" | "/api/schema/import" => maps.create(body)?,
            "/api/schema/add-node" => maps.add_node(body)?,
            "/api/schema/save" => maps.save(body)?,
            "/api/schema/delete" => maps.delete(body)?,
            "/api/schema/undo" => maps.undo(body)?,
            "/api/schema/transfer" => maps.transfer(body)?,
            _ => return Ok(None),
        }));
    }
    Ok(Some(match call.path {
        "/api/schema" => {
            call.no_query()?;
            maps.workspace()?
        }
        "/api/schema/map" => maps.load(&map_id(call)?)?,
        "/api/schema/export" => maps.export(&map_id(call)?)?,
        "/api/schema/network" => {
            if !call.only(&["mode", "map_ids", "merge_labels"]) {
                return Err(bad("Unknown network query parameter"));
            }
            let flag = call.get("merge_labels").unwrap_or("false");
            if !matches!(flag, "true" | "false" | "1" | "0") {
                return Err(bad("merge_labels must be true or false"));
            }
            let map_ids = match call.get("map_ids").filter(|text| !text.is_empty()) {
                Some(text) => Some(text.split(',').map(parse_int).collect::<Option<Vec<i128>>>().ok_or_else(|| bad("Map IDs must be comma-separated integers"))?),
                None => None,
            };
            network::network(&maps, call.get("mode").filter(|mode| !mode.is_empty()), map_ids.as_deref(), matches!(flag, "true" | "1"))?
        }
        _ => return Ok(None),
    }))
}

/// The routes over tracked jobs. Without the workspace module Python had no handler for them.
fn answer_jobs(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    if !ctx.has("workspace") {
        return Ok(None);
    }
    let maps = Maps { conn: ctx.conn, applyai: true };
    if let Some(body) = call.body {
        return Ok(Some(maps.import_jobs(body)?));
    }
    if !call.only(&["search", "offset"]) {
        return Err(bad("Unknown jobs query parameter"));
    }
    let offset = parse_int(call.get("offset").unwrap_or("0")).map(query_int).ok_or_else(|| bad("Offset must be an integer"))?;
    Ok(Some(maps.job_options(call.get("search").unwrap_or(""), &offset)?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn query_integers_parse_as_pythons_int() {
        for (text, value) in [("7", Some(7)), (" +7 ", Some(7)), ("-0", Some(0)), ("1_000", Some(1000)), ("007", Some(7)), ("", None), ("+", None),
                              ("1__0", None), ("_1", None), ("1_", None), ("1.0", None), ("0x1", None), ("1 2", None)] {
            assert_eq!(parse_int(text), value, "{text:?}");
        }
        assert_eq!(query_int(parse_int("99999999999999999999").unwrap()), json!(1e20));
        assert!(integer(&query_int(parse_int("99999999999999999999").unwrap()), "ID", 1).is_err());
    }

    #[test]
    fn moved_positions_stop_at_the_canvas_edge_as_python_clamps_them() {
        let moved = |value: Num| py::dumps(&value.bounded().value().unwrap());
        assert_eq!(moved(Num::Float(99990.0).plus(Num::Int(300)).minus(Num::Float(-99999.5))), "100000");
        assert_eq!(moved(Num::Float(100000.0)), "100000");
        assert_eq!(moved(Num::Float(-100000.0)), "-100000");
        assert_eq!(moved(Num::Float(-99999.5)), "-99999.5");
        assert_eq!(moved(Num::Int(5).plus(Num::Int(300)).minus(Num::Int(5))), "300");
        assert_eq!(moved(Num::Int(5).plus(Num::Float(0.5)).minus(Num::Int(5))), "0.5");
    }

    /// A stored coordinate is parsed and written back on every change of its map: the text
    /// must not drift (serde_json's `float_roundtrip`, Python's `json.loads` and `repr`).
    #[test]
    fn stored_floats_survive_a_read_and_a_write() {
        let mut state = 0x9e3779b97f4a7c15u64;
        for _ in 0..20000 {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            let value = f64::from_bits(state);
            if !value.is_finite() {
                continue;
            }
            let text = py::float_repr(value);
            let parsed: Value = serde_json::from_str(&text).unwrap();
            assert_eq!(parsed.as_f64().map(f64::to_bits), Some(value.to_bits()), "{text}");
            assert_eq!(py::dumps(&parsed), text);
        }
        assert_eq!(py::dumps(&serde_json::from_str::<Value>("[0.30000000000000004, 1e-07, 123.4565, 100000.0, 5]").unwrap()), "[0.30000000000000004, 1e-07, 123.4565, 100000.0, 5]");
    }
}
