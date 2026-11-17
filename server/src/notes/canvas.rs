//! A page's canvas: the drawing document the Notes canvas saves (`notes.normalize`) and its
//! mapping onto blocks (`kitty.core.blocks.convert`: the `canvas` block holds the document
//! settings, its children the elements in paint order, adjacent strokes of one layer one
//! `ink` block), with `sync_tree`, the smallest transaction that turns the page's current
//! blocks into a wanted shape.

use std::collections::{HashMap, HashSet};

use indexmap::IndexMap;
use rusqlite::Connection;
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use super::{bad, keys, text};
use crate::blocks::doc::{is_tag_key, Block, PageDoc};
use crate::blocks::json::{self, PLAIN};
use crate::blocks::order;
use crate::ported::{db, internal, py, Done};

const MAX_DOCUMENT_BYTES: usize = 2 * 1024 * 1024;
const MAX_ELEMENTS: usize = 2000;
const MAX_POINTS: usize = 100_000;
const KINDS: [&str; 10] = ["pen", "highlighter", "text", "sticky", "rect", "ellipse", "line", "arrow", "connector", "image"];
const NOTE_TAGS: [&str; 4] = ["todo", "important", "question", "definition"];
const BACKGROUNDS: [&str; 5] = ["blank", "lined", "grid", "dotted", "cornell"];
const SETTINGS: [&str; 5] = ["version", "grid", "snap", "background", "layers"];
const ELEMENT_FIELDS: [&str; 19] = ["id", "kind", "layer", "x", "y", "w", "h", "color", "fill", "width", "points", "text", "fontSize", "group",
                                    "attachment", "from", "to", "tags", "checked"];
const INK_STROKES: usize = 64;
const INK_POINTS: usize = 20_000;
/// `convert.NAMESPACE`.
const NAMESPACE: [u8; 16] = [0x5d, 0x2b, 0x9c, 0x1e, 0x4a, 0x7f, 0x4e, 0x4b, 0x9b, 0x39, 0x0c, 0x7f, 0x0c, 0x3b, 0x6a, 0x11];

/// `uuid.uuid5(NAMESPACE, name)`.
fn uuid5(name: &str) -> String {
    let mut hash = Sha1::new();
    hash.update(NAMESPACE);
    hash.update(name.as_bytes());
    let digest = hash.finalize();
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x50;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex = hex::encode(bytes);
    format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..])
}

pub fn canvas_block_id(page_id: &str) -> String {
    uuid5(&format!("{page_id}/canvas"))
}

// The document ----------------------------------------------------------------------------

/// `number`: a finite int or float within the range, as a float (`range` is Python's text of it).
fn number(value: Option<&Value>, low: f64, high: f64, range: &str) -> Done<Value> {
    let found = match value {
        Some(Value::Number(number)) => number.as_f64().filter(|value| value.is_finite() && low <= *value && *value <= high),
        _ => None,
    };
    found.map(Value::from).ok_or_else(|| bad(format!("Canvas numbers must be finite and between {range}")))
}

fn coordinate(value: Option<&Value>) -> Done<Value> {
    number(value, -1_000_000.0, 1_000_000.0, "-1000000 and 1000000")
}

fn is_identifier(text: &str) -> bool {
    (1..=80).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// `identifier`.
pub fn identifier(value: Option<&Value>) -> Done<&str> {
    match value {
        Some(Value::String(text)) if is_identifier(text) => Ok(text),
        _ => Err(bad("Invalid canvas identifier")),
    }
}

fn is_color(text: &str) -> bool {
    text.len() == 7 && text.starts_with('#') && text[1..].bytes().all(|b| b.is_ascii_hexdigit())
}

/// `viewport`.
pub fn viewport(value: &Value) -> Done<Value> {
    let map = keys(value, &["x", "y", "zoom"], &["x", "y", "zoom"])?;
    Ok(json!({"x": coordinate(map.get("x"))?, "y": coordinate(map.get("y"))?, "zoom": number(map.get("zoom"), 0.05, 20.0, "0.05 and 20")?}))
}

/// `empty_document`.
pub fn empty_document() -> Map<String, Value> {
    let Value::Object(map) = json!({"version": 1, "grid": true, "snap": false,
        "layers": [{"id": "default", "name": "Drawing", "visible": true, "locked": false}], "elements": []}) else { unreachable!() };
    map
}

fn point(value: &Value, message: &str) -> Done<Value> {
    match value {
        Value::Array(items) if items.len() == 3 => Ok(Value::Array(vec![
            coordinate(Some(&items[0]))?, coordinate(Some(&items[1]))?, number(Some(&items[2]), 0.0, 1.0, "0 and 1")?,
        ])),
        _ => Err(bad(message)),
    }
}

/// `normalize`: a canvas document as it is stored, or the first refusal; with `conn`, image
/// elements must name a registered attachment.
pub fn normalize(value: &Value, conn: Option<&Connection>) -> Done<Map<String, Value>> {
    let value = keys(value, &["version", "grid", "snap", "background", "layers", "elements"], &["version", "layers", "elements"])?;
    if py::int(&value["version"]) != Some(1) {
        return Err(bad("Unsupported notebook document version"));
    }
    let (Some(layers), Some(elements)) = (value["layers"].as_array().filter(|items| (1..=32).contains(&items.len())),
                                          value["elements"].as_array().filter(|items| items.len() <= MAX_ELEMENTS)) else {
        return Err(bad("Canvas is limited to 32 layers and 2000 elements"));
    };
    let mut clean_layers = Vec::with_capacity(layers.len());
    let mut layer_ids = HashSet::new();
    for layer in layers {
        let layer = keys(layer, &["id", "name", "visible", "locked"], &["id", "name", "visible", "locked"])?;
        if !layer["visible"].is_boolean() || !layer["locked"].is_boolean() {
            return Err(bad("Layer flags must be booleans"));
        }
        let id = identifier(layer.get("id"))?;
        let name = text(&layer["name"], 100, "Text")?;
        layer_ids.insert(id.to_owned());
        clean_layers.push(json!({"id": id, "name": name, "visible": layer["visible"], "locked": layer["locked"]}));
    }
    if layer_ids.len() != layers.len() {
        return Err(bad("Duplicate layer identifier"));
    }
    let mut output: Vec<Map<String, Value>> = Vec::with_capacity(elements.len());
    let mut ids: HashSet<&str> = HashSet::new();
    let mut points_total = 0;
    for element in elements {
        let element = keys(element, &ELEMENT_FIELDS, &["id", "kind", "layer", "x", "y", "w", "h", "color", "width"])?;
        let eid = identifier(element.get("id"))?;
        if !ids.insert(eid) {
            return Err(bad("Duplicate element identifier"));
        }
        let layer_id = identifier(element.get("layer"))?;
        let kind = match &element["kind"] {
            Value::String(kind) if KINDS.contains(&kind.as_str()) && layer_ids.contains(layer_id) => kind.as_str(),
            _ => return Err(bad("Unknown element type or layer")),
        };
        let none = Value::String("none".into());
        let (color, fill) = (&element["color"], element.get("fill").unwrap_or(&none));
        let good_fill = fill.as_str().is_some_and(|fill| fill == "none" || is_color(fill));
        if !color.as_str().is_some_and(is_color) || !good_fill {
            return Err(bad("Use six-digit hex colors or no fill"));
        }
        let mut item = Map::new();
        item.insert("id".into(), eid.into());
        item.insert("kind".into(), kind.into());
        item.insert("layer".into(), layer_id.into());
        item.insert("x".into(), coordinate(element.get("x"))?);
        item.insert("y".into(), coordinate(element.get("y"))?);
        item.insert("w".into(), number(element.get("w"), 0.0, 1_000_000.0, "0 and 1000000")?);
        item.insert("h".into(), number(element.get("h"), 0.0, 1_000_000.0, "0 and 1000000")?);
        item.insert("color".into(), color.clone());
        item.insert("fill".into(), fill.clone());
        item.insert("width".into(), number(element.get("width"), 0.1, 100.0, "0.1 and 100")?);
        if element.get("group").is_some_and(py::truthy) {
            item.insert("group".into(), identifier(element.get("group"))?.into());
        }
        if matches!(kind, "pen" | "highlighter") {
            let Some(points) = element.get("points").and_then(Value::as_array).filter(|items| (1..=20_000).contains(&items.len())) else {
                return Err(bad("A stroke needs 1\u{2013}20000 pressure points"));
            };
            points_total += points.len();
            if points_total > MAX_POINTS {
                return Err(bad("Page exceeds 100000 pressure points"));
            }
            let clean = points.iter().map(|item| point(item, "Stroke point must be x, y, pressure")).collect::<Done<Vec<_>>>()?;
            item.insert("points".into(), Value::Array(clean));
        }
        if matches!(kind, "text" | "sticky") {
            let empty = Value::String(String::new());
            item.insert("text".into(), text(element.get("text").unwrap_or(&empty), 20_000, "Text")?.into());
            let size = Value::from(20);
            item.insert("fontSize".into(), number(Some(element.get("fontSize").unwrap_or(&size)), 8.0, 120.0, "8 and 120")?);
            let no_tags = Value::Array(Vec::new());
            let tags = match element.get("tags").unwrap_or(&no_tags) {
                Value::Array(tags) if tags.len() <= 4 && tags.iter().all(|tag| tag.as_str().is_some_and(|tag| NOTE_TAGS.contains(&tag))) => tags,
                _ => return Err(bad("Unknown note tag")),
            };
            if !tags.is_empty() {
                let mut unique: Vec<Value> = Vec::new();
                for tag in tags {
                    if !unique.contains(tag) {
                        unique.push(tag.clone());
                    }
                }
                item.insert("tags".into(), Value::Array(unique));
            }
            if let Some(checked) = element.get("checked") {
                if !checked.is_boolean() {
                    return Err(bad("Checkbox state must be boolean"));
                }
                item.insert("checked".into(), checked.clone());
            }
        }
        if kind == "image" {
            let attachment = identifier(element.get("attachment"))?;
            if let Some(conn) = conn {
                if !db::exists(conn, "SELECT 1 FROM note_attachments WHERE id=?", [attachment])? {
                    return Err(bad("Image references an unregistered local attachment"));
                }
            }
            item.insert("attachment".into(), attachment.into());
        }
        if matches!(kind, "line" | "arrow" | "connector") {
            let Some(points) = element.get("points").and_then(Value::as_array).filter(|items| items.len() == 2) else {
                return Err(bad("Line needs two endpoints"));
            };
            let clean = points.iter().map(|item| point(item, "Line point must be x, y, pressure")).collect::<Done<Vec<_>>>()?;
            item.insert("points".into(), Value::Array(clean));
            if kind == "connector" {
                item.insert("from".into(), identifier(element.get("from"))?.into());
                item.insert("to".into(), identifier(element.get("to"))?.into());
            }
        }
        output.push(item);
    }
    for item in &output {
        if item["kind"] == "connector" {
            let (from, to, id) = (item["from"].as_str().unwrap_or(""), item["to"].as_str().unwrap_or(""), item["id"].as_str().unwrap_or(""));
            if !ids.contains(from) || !ids.contains(to) || from == to || id == from || id == to {
                return Err(bad("Connector endpoints must be distinct existing elements"));
            }
        }
    }
    let off = Value::Bool(false);
    for flag in ["grid", "snap"] {
        if !value.get(flag).unwrap_or(&off).is_boolean() {
            return Err(bad("Grid and snap flags must be boolean"));
        }
    }
    let mut result = Map::new();
    result.insert("version".into(), 1.into());
    result.insert("grid".into(), value.get("grid").unwrap_or(&off).clone());
    result.insert("snap".into(), value.get("snap").unwrap_or(&off).clone());
    result.insert("layers".into(), Value::Array(clean_layers));
    result.insert("elements".into(), Value::Array(output.into_iter().map(Value::Object).collect()));
    if let Some(background) = value.get("background") {
        if !background.as_str().is_some_and(|background| BACKGROUNDS.contains(&background)) {
            return Err(bad("Unknown page background"));
        }
        result.insert("background".into(), background.clone());
    }
    if json::dumps_map(&result, PLAIN).len() > MAX_DOCUMENT_BYTES {
        return Err(bad("Canvas document exceeds 2 MiB"));
    }
    Ok(result)
}

// Blocks <-> document ------------------------------------------------------------------------

/// A wanted block (`{id, parent, type, props, text}`).
pub struct Spec {
    pub id: String,
    pub parent: String,
    pub kind: String,
    pub props: Map<String, Value>,
    pub text: String,
}

/// `convert.canvas_settings`.
pub fn settings(document: &Map<String, Value>) -> Map<String, Value> {
    SETTINGS.iter().filter_map(|key| document.get(*key).map(|value| ((*key).to_owned(), value.clone()))).collect()
}

fn float(value: &Value) -> Done<f64> {
    value.as_f64().ok_or_else(internal)
}

fn ink_spec(page_id: &str, canvas_id: &str, strokes: &[&Map<String, Value>]) -> Done<Spec> {
    // `min`/`max` keep the first of equal values (0.0 and -0.0), as Python's do.
    let (mut left, mut top, mut right, mut bottom) = (None::<f64>, None::<f64>, None::<f64>, None::<f64>);
    for item in strokes {
        let (x, y) = (float(&item["x"])?, float(&item["y"])?);
        let (far_x, far_y) = (x + float(&item["w"])?, y + float(&item["h"])?);
        if left.is_none_or(|left| x < left) { left = Some(x); }
        if top.is_none_or(|top| y < top) { top = Some(y); }
        if right.is_none_or(|right| far_x > right) { right = Some(far_x); }
        if bottom.is_none_or(|bottom| far_y > bottom) { bottom = Some(far_y); }
    }
    let (left, top, right, bottom) = (left.ok_or_else(internal)?, top.ok_or_else(internal)?, right.ok_or_else(internal)?, bottom.ok_or_else(internal)?);
    let first = strokes[0];
    let props = json!({"layer": first["layer"], "x": left, "y": top, "w": right - left, "h": bottom - top,
                       "strokes": strokes.iter().map(|item| Value::Object((*item).clone())).collect::<Vec<_>>()});
    let Value::Object(props) = props else { unreachable!() };
    let first_id = first["id"].as_str().ok_or_else(internal)?;
    Ok(Spec { id: uuid5(&format!("{page_id}/ink/{first_id}")), parent: canvas_id.to_owned(), kind: "ink".into(), props, text: String::new() })
}

fn element_type(kind: &str) -> &'static str {
    match kind {
        "pen" | "highlighter" => "ink",
        "text" => "text_box",
        "sticky" => "sticky",
        "connector" => "connector",
        "image" => "image",
        _ => "shape",
    }
}

/// `convert.canvas_specs`: the wanted children of the page's canvas block, in paint order.
pub fn canvas_specs(page_id: &str, document: &Map<String, Value>) -> Done<Vec<Spec>> {
    let canvas_id = canvas_block_id(page_id);
    let (mut specs, mut run, mut points): (Vec<Spec>, Vec<&Map<String, Value>>, usize) = (Vec::new(), Vec::new(), 0);
    let elements = document.get("elements").and_then(Value::as_array).ok_or_else(internal)?;
    for element in elements {
        let element = element.as_object().ok_or_else(internal)?;
        let kind = element["kind"].as_str().ok_or_else(internal)?;
        if matches!(kind, "pen" | "highlighter") {
            let size = element.get("points").and_then(Value::as_array).map_or(0, Vec::len);
            if let Some(last) = run.last() {
                if last["layer"] != element["layer"] || run.len() >= INK_STROKES || points + size > INK_POINTS {
                    specs.push(ink_spec(page_id, &canvas_id, &run)?);
                    (run, points) = (Vec::new(), 0);
                }
            }
            run.push(element);
            points += size;
            continue;
        }
        if !run.is_empty() {
            specs.push(ink_spec(page_id, &canvas_id, &run)?);
            (run, points) = (Vec::new(), 0);
        }
        let mut props: Map<String, Value> = element.iter().filter(|(key, _)| *key != "id" && *key != "text").map(|(k, v)| (k.clone(), v.clone())).collect();
        let eid = element["id"].as_str().ok_or_else(internal)?;
        props.insert("element_id".into(), eid.into());
        let text = element.get("text").and_then(Value::as_str).unwrap_or("").to_owned();
        specs.push(Spec { id: uuid5(&format!("{page_id}/element/{eid}")), parent: canvas_id.clone(), kind: element_type(kind).into(), props, text });
    }
    if !run.is_empty() {
        specs.push(ink_spec(page_id, &canvas_id, &run)?);
    }
    Ok(specs)
}

/// `notes.canvas_of`: the page's first live `canvas` child.
pub fn canvas_of(doc: &PageDoc) -> Option<&Block> {
    doc.children(Some(&doc.page_id), false).into_iter().find(|item| item.kind == "canvas")
}

/// `convert.canvas_document` of the page's canvas (`notes.page_document`).
pub fn page_document(doc: &PageDoc) -> Done<Value> {
    let Some(canvas) = canvas_of(doc) else { return canvas_document(None, &[]) };
    canvas_document(Some(canvas), &doc.children(Some(&canvas.id), false))
}

fn canvas_document(canvas: Option<&Block>, elements: &[&Block]) -> Done<Value> {
    let empty = Map::new();
    let settings = canvas.map_or(&empty, |canvas| &canvas.properties);
    if let Some(raw) = settings.get("raw") {
        return Ok(raw.clone());
    }
    let Value::Object(mut document) = json!({"version": 1, "grid": true, "snap": false,
        "layers": [{"id": "default", "name": "Drawing", "visible": true, "locked": false}]}) else { unreachable!() };
    for key in SETTINGS {
        if let Some(value) = settings.get(key) {
            document.insert(key.into(), value.clone());
        }
    }
    let mut output = Vec::new();
    for item in elements {
        if item.kind == "ink" {
            output.extend(item.properties.get("strokes").and_then(Value::as_array).ok_or_else(internal)?.iter().cloned());
            continue;
        }
        let mut props = item.properties.clone();
        let id = props.shift_remove("element_id").ok_or_else(internal)?;
        let mut element = Map::new();
        element.insert("id".into(), id);
        element.extend(props);
        match element.get("kind") {
            Some(Value::String(kind)) if kind == "text" || kind == "sticky" => {
                element.insert("text".into(), item.text.clone().into());
            }
            Some(Value::Array(_) | Value::Object(_)) | None => return Err(internal()),
            _ => {}
        }
        output.push(Value::Object(element));
    }
    document.insert("elements".into(), Value::Array(output));
    Ok(Value::Object(document))
}

// Transactions -------------------------------------------------------------------------------

/// `order.keys_between`: `count` ascending keys between `a` and `b`.
pub fn keys_between(a: Option<&str>, b: Option<&str>, count: usize) -> Done<Vec<String>> {
    let between = |a: Option<&str>, b: Option<&str>| order::key_between(a, b).map_err(|_| internal());
    if count == 0 {
        return Ok(Vec::new());
    }
    if count == 1 {
        return Ok(vec![between(a, b)?]);
    }
    if b.is_none() {
        let mut result = vec![between(a, None)?];
        while result.len() < count {
            let next = between(result.last().map(String::as_str), None)?;
            result.push(next);
        }
        return Ok(result);
    }
    if a.is_none() {
        let mut result = vec![between(None, b)?];
        while result.len() < count {
            let next = between(None, result.last().map(String::as_str))?;
            result.push(next);
        }
        result.reverse();
        return Ok(result);
    }
    let middle = count / 2;
    let pivot = between(a, b)?;
    let mut result = keys_between(a, Some(&pivot), middle)?;
    let after = keys_between(Some(&pivot), b, count - middle - 1)?;
    result.push(pivot);
    result.extend(after);
    Ok(result)
}

/// `convert.insert_ops`: insert operations for new blocks, keyed per parent in listed order.
pub fn insert_ops(specs: Vec<Spec>) -> Done<Vec<Value>> {
    let mut counts: IndexMap<String, usize> = IndexMap::new();
    for spec in &specs {
        *counts.entry(spec.parent.clone()).or_default() += 1;
    }
    let mut keys: HashMap<String, std::vec::IntoIter<String>> = HashMap::new();
    for (parent, count) in counts {
        keys.insert(parent, keys_between(None, None, count)?.into_iter());
    }
    specs.into_iter().map(|spec| {
        let key = keys.get_mut(&spec.parent).and_then(Iterator::next).ok_or_else(internal)?;
        Ok(json!({"op": "insert", "id": spec.id, "type": spec.kind, "parent": spec.parent, "key": key, "props": spec.props, "text": spec.text}))
    }).collect()
}

/// `convert.props_diff`: a `set` operation's props turning `old` into `new` (null removes a key).
pub fn props_diff(old: &Map<String, Value>, new: &Map<String, Value>) -> Map<String, Value> {
    let mut changes: Map<String, Value> = new.iter().filter(|(key, value)| old.get(*key).is_none_or(|was| !py::eq(was, value)))
        .map(|(key, value)| (key.clone(), value.clone())).collect();
    for key in old.keys() {
        if !new.contains_key(key) {
            changes.insert(key.clone(), Value::Null);
        }
    }
    changes
}

/// Python's `==` of two dicts.
fn dict_eq(a: &Map<String, Value>, b: &Map<String, Value>) -> bool {
    a.len() == b.len() && a.iter().all(|(key, value)| b.get(key).is_some_and(|other| py::eq(value, other)))
}

/// Indexes of one longest strictly increasing subsequence of `keys` (`_longest_increasing`).
fn longest_increasing(keys: &[&str]) -> HashSet<usize> {
    let (mut tails, mut tail_index): (Vec<&str>, Vec<usize>) = (Vec::new(), Vec::new());
    let mut previous = vec![-1i64; keys.len()];
    for (index, key) in keys.iter().enumerate() {
        let position = tails.partition_point(|tail| tail < key);
        if position == tails.len() {
            tails.push(key);
            tail_index.push(index);
        } else {
            tails[position] = key;
            tail_index[position] = index;
        }
        previous[index] = if position > 0 { tail_index[position - 1] as i64 } else { -1 };
    }
    let mut result = HashSet::new();
    let mut cursor = tail_index.last().map_or(-1, |&index| index as i64);
    while cursor >= 0 {
        result.insert(cursor as usize);
        cursor = previous[cursor as usize];
    }
    result
}

/// `convert.sync_tree`: operations turning the live in-`scope` blocks under `root_id` into
/// `desired` (parents before children, siblings in order). Unchanged blocks produce nothing;
/// reordering keeps the longest run of ordered siblings; blocks no longer wanted go to Trash.
pub fn sync_tree(doc: &PageDoc, root_id: &str, desired: &[Spec], scope: &dyn Fn(&Block) -> bool) -> Done<Vec<Value>> {
    let mut existing: IndexMap<String, &Block> = IndexMap::new();
    for bid in doc.descendants(root_id) {
        let item = &doc.blocks[&bid];
        if item.live() && scope(item) {
            existing.insert(bid, item);
        }
    }
    let mut groups: IndexMap<&str, Vec<&str>> = IndexMap::new();
    for spec in desired {
        groups.entry(spec.parent.as_str()).or_default().push(spec.id.as_str());
    }
    let mut keys: HashMap<String, String> = HashMap::new();
    for (parent, ids) in &groups {
        let current: Vec<Option<&Block>> = ids.iter().map(|bid| doc.blocks.get(*bid).filter(|item| item.parent_id.as_deref() == Some(*parent))).collect();
        let stable: Vec<usize> = (0..ids.len()).filter(|&index| current[index].is_some()).collect();
        let ordered: Vec<&str> = stable.iter().map(|&index| current[index].map_or("", |item| item.order_key.as_str())).collect();
        let kept: HashSet<usize> = longest_increasing(&ordered).into_iter().map(|i| stable[i]).collect();
        let mut siblings: Vec<&str> = if doc.blocks.contains_key(*parent) {
            doc.children(Some(parent), false).into_iter().map(|item| item.order_key.as_str()).collect()
        } else {
            Vec::new()
        };
        siblings.sort();
        let mut floor: Option<String> = None;
        let mut index = 0;
        while index < ids.len() {
            if kept.contains(&index) {
                let key = current[index].map(|item| item.order_key.clone()).ok_or_else(internal)?;
                keys.insert(ids[index].to_owned(), key.clone());
                floor = Some(key);
                index += 1;
                continue;
            }
            let mut end = index;
            while end < ids.len() && !kept.contains(&end) {
                end += 1;
            }
            let ceiling = if end < ids.len() { current[end].map(|item| item.order_key.clone()) } else { None };
            if floor.is_none() && ceiling.is_none() {
                if let Some(last) = siblings.last() {
                    floor = Some((*last).to_owned());
                }
            }
            for (bid, key) in ids[index..end].iter().zip(keys_between(floor.as_deref(), ceiling.as_deref(), end - index)?) {
                keys.insert((*bid).to_owned(), key);
            }
            floor = keys.get(ids[end - 1]).cloned();
            index = end;
        }
    }
    let mut ops = Vec::new();
    let wanted: HashSet<&str> = desired.iter().map(|spec| spec.id.as_str()).collect();
    for spec in desired {
        let bid = spec.id.as_str();
        let key = keys.get(bid).ok_or_else(internal)?;
        let Some(item) = doc.blocks.get(bid) else {
            ops.push(json!({"op": "insert", "id": bid, "type": spec.kind, "parent": spec.parent, "key": key, "props": spec.props, "text": spec.text}));
            continue;
        };
        if !item.live() {
            ops.push(json!({"op": "restore", "id": bid}));
            for child in doc.descendants(bid) {
                let found = &doc.blocks[&child];
                if found.deleted_at == item.deleted_at && scope(found) {
                    existing.insert(child, found);
                }
            }
        }
        if item.parent_id.as_deref() != Some(spec.parent.as_str()) || &item.order_key != key {
            ops.push(json!({"op": "move", "id": bid, "parent": spec.parent, "key": key}));
        }
        // Properties are replaced as a whole, except tag states: they belong to tagging.
        let mut props: Map<String, Value> = item.properties.iter().filter(|(key, _)| is_tag_key(key)).map(|(k, v)| (k.clone(), v.clone())).collect();
        for (key, value) in &spec.props {
            props.insert(key.clone(), value.clone());
        }
        if item.kind != spec.kind || !dict_eq(&item.properties, &props) {
            let mut op = json!({"op": "set", "id": bid, "props": props_diff(&item.properties, &props)});
            if item.kind != spec.kind {
                op["type"] = spec.kind.clone().into();
            }
            ops.push(op);
        }
        if item.text != spec.text {
            ops.push(json!({"op": "text", "id": bid, "text": spec.text}));
        }
    }
    let doomed: std::collections::BTreeSet<&str> = existing.keys().map(String::as_str).filter(|bid| !wanted.contains(bid)).collect();
    for bid in &doomed {
        if existing[*bid].parent_id.as_deref().is_none_or(|parent| !doomed.contains(parent)) {
            ops.push(json!({"op": "delete", "id": bid}));
        }
    }
    Ok(ops)
}

/// `notes.canvas_ops`: operations making the page's canvas blocks match a normalized document.
pub fn canvas_ops(doc: &PageDoc, document: &Map<String, Value>) -> Done<Vec<Value>> {
    let wanted = settings(document);
    let (canvas_id, mut ops) = match canvas_of(doc) {
        None => {
            let canvas_id = canvas_block_id(&doc.page_id);
            let op = json!({"op": "insert", "id": canvas_id, "type": "canvas", "parent": doc.page_id, "after": null, "props": wanted});
            (canvas_id, vec![op])
        }
        Some(canvas) => {
            let changes = props_diff(&canvas.properties, &wanted);
            let ops = if changes.is_empty() { Vec::new() } else { vec![json!({"op": "set", "id": canvas.id, "props": changes})] };
            (canvas.id.clone(), ops)
        }
    };
    let specs = canvas_specs(&doc.page_id, document)?;
    let scope_id = canvas_id.clone();
    ops.extend(sync_tree(doc, &canvas_id, &specs, &move |item: &Block| item.parent_id.as_deref() == Some(scope_id.as_str()))?);
    Ok(ops)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn element_ids_are_pythons_uuid5() {
        // uuid.uuid5(convert.NAMESPACE, "p/canvas")
        assert_eq!(canvas_block_id("p").len(), 36);
        assert_eq!(&canvas_block_id("p")[14..15], "5");
    }

    #[test]
    fn keys_between_is_ascending() {
        let keys = keys_between(None, None, 5).unwrap();
        assert!(keys.windows(2).all(|pair| pair[0] < pair[1]));
        let inner = keys_between(Some(&keys[0]), Some(&keys[1]), 7).unwrap();
        assert!(inner.windows(2).all(|pair| pair[0] < pair[1]) && inner[0].as_str() > keys[0].as_str() && inner[6].as_str() < keys[1].as_str());
    }

    #[test]
    fn numbers_and_documents_refuse_as_python_does() {
        assert_eq!(number(Some(&json!(true)), 0.0, 1.0, "0 and 1").unwrap_err().message, "Canvas numbers must be finite and between 0 and 1");
        assert_eq!(number(Some(&json!(5)), 0.0, 10.0, "").unwrap(), json!(5.0));
        let document = json!({"version": 1, "layers": [{"id": "a", "name": "A", "visible": true, "locked": false}],
                              "elements": [{"id": "e", "kind": "rect", "layer": "a", "x": 0, "y": 0, "w": 1, "h": 1, "color": "#000000", "width": 1}]});
        let clean = normalize(&document, None).unwrap();
        assert_eq!(clean["elements"][0]["fill"], "none");
        assert_eq!(longest_increasing(&["b", "a", "c"]).len(), 2);
    }
}
