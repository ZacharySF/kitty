//! `kitty.core.blocks.convert`'s document half: a document's paragraph chunks
//! (`split_document`, `join_document`), the chunk blocks a new content wants (`document_specs`)
//! and the operations turning the saved chunks into them (`sync_tree`, `insert_ops`).

use std::collections::{HashMap, HashSet};

use serde_json::{json, Map, Value};

use crate::blocks::doc::{is_tag_key, Block, PageDoc};
use crate::blocks::{difflib, order, store};
use crate::ported::{internal, py, Done};

/// `MAX_DOCUMENT_CHUNKS`.
const MAX_CHUNKS: usize = 2000;

pub struct Chunk {
    pub kind: &'static str,
    pub text: String,
    pub sep: String,
}

/// A desired block: `{id, parent, type, props, text}`.
pub struct Spec {
    pub id: String,
    pub parent: String,
    pub kind: &'static str,
    pub props: Map<String, Value>,
    pub text: String,
}

/// `str.splitlines(keepends=True)`.
fn lines(content: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = content.char_indices().peekable();
    while let Some((at, c)) = chars.next() {
        let end = match c {
            '\r' => match chars.peek() {
                Some((_, '\n')) => { chars.next(); at + 2 }
                _ => at + 1,
            },
            '\n' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}' => at + c.len_utf8(),
            _ => continue,
        };
        out.push(&content[start..end]);
        start = end;
    }
    if start < content.len() {
        out.push(&content[start..]);
    }
    out
}

/// Leading spaces when there are at most three (` {0,3}`), else None.
fn indent(line: &str) -> Option<&str> {
    let rest = line.trim_start_matches(' ');
    (line.len() - rest.len() <= 3).then_some(rest)
}

/// `_FENCE.match(line)[1]`: `^ {0,3}(`{3,}|~{3,})`.
fn fence(line: &str) -> Option<&str> {
    let rest = indent(line)?;
    let mark = rest.chars().next().filter(|c| *c == '`' || *c == '~')?;
    let run = rest.len() - rest.trim_start_matches(mark).len();
    (run >= 3).then(|| &rest[..run])
}

fn heading(first: &str) -> bool {
    let Some(rest) = indent(first) else { return false };
    let after = rest.trim_start_matches('#');
    let count = rest.len() - after.len();
    (1..=6).contains(&count) && after.chars().next().is_none_or(py::is_space)
}

fn divider(first: &str) -> bool {
    let Some(rest) = indent(first) else { return false };
    let Some(mark) = rest.chars().next().filter(|c| matches!(c, '-' | '*' | '_')) else { return false };
    rest.chars().all(|c| c == mark || c == ' ') && rest.chars().filter(|c| *c == mark).count() >= 3
}

fn list(first: &str) -> bool {
    let rest = first.trim_start_matches(' ');
    let after = match rest.chars().next() {
        Some('-' | '+' | '*') => &rest[1..],
        _ => {
            let digits: usize = rest.chars().take_while(|c| crate::search::text::is_decimal(*c)).map(char::len_utf8).sum();
            let count = rest[..digits].chars().count();
            if !(1..=9).contains(&count) || !matches!(rest[digits..].chars().next(), Some('.' | ')')) {
                return false;
            }
            &rest[digits + 1..]
        }
    };
    after.is_empty() || after.starts_with(' ')
}

/// `_chunk_type`.
fn chunk_type(text: &str, markdown: bool) -> &'static str {
    if markdown {
        let first = text.split('\n').next().unwrap_or("");
        if heading(first) {
            return "heading";
        }
        if fence(first).is_some() {
            return "code";
        }
        if indent(first).is_some_and(|rest| rest.starts_with('>')) {
            return "quote";
        }
        if divider(first) {
            return "divider";
        }
        if list(first) {
            return "list";
        }
    }
    "text"
}

/// `_terminator`.
fn terminator(text: &str) -> usize {
    if text.ends_with("\r\n") { 2 } else if text.ends_with(['\n', '\r']) { 1 } else { 0 }
}

/// `split_document`: paragraph-level chunks; `join` restores `content` exactly.
pub fn split(content: &str, markdown: bool) -> Vec<Chunk> {
    let mut chunks: Vec<(String, String)> = Vec::new();
    let (mut body, mut gap): (Vec<&str>, Vec<&str>) = (Vec::new(), Vec::new());
    let mut open: Option<String> = None;
    let flush = |chunks: &mut Vec<(String, String)>, body: &[&str], gap: &[&str]| {
        let text = body.concat();
        let cut = if body.is_empty() { 0 } else { terminator(&text) };
        let at = text.len() - cut;
        chunks.push((text[..at].to_owned(), format!("{}{}", &text[at..], gap.concat())));
    };
    for line in lines(content) {
        if let Some(mark) = &open {
            body.push(line);
            if py::strip(line).starts_with(mark.as_str()) {
                open = None;
            }
            continue;
        }
        if py::strip(line).is_empty() {
            gap.push(line);
            continue;
        }
        if !gap.is_empty() {
            flush(&mut chunks, &body, &gap);
            body.clear();
            gap.clear();
        }
        if let Some(mark) = fence(line) {
            open = Some(mark.to_owned());
        }
        body.push(line);
    }
    if !body.is_empty() || !gap.is_empty() {
        flush(&mut chunks, &body, &gap);
    }
    while chunks.len() > MAX_CHUNKS {
        let (text, sep) = chunks.pop().expect("more than the limit");
        let last = chunks.last_mut().expect("more than the limit");
        last.0 = format!("{}{}{}", last.0, last.1, text);
        last.1 = sep;
    }
    chunks.into_iter().map(|(text, sep)| Chunk { kind: chunk_type(&text, markdown), text, sep }).collect()
}

/// `join_document`.
pub fn join(chunks: &[&Block]) -> String {
    chunks.iter().map(|item| format!("{}{}", item.text, item.properties.get("sep").and_then(Value::as_str).unwrap_or(""))).collect()
}

fn sep_props(sep: String) -> Map<String, Value> {
    let mut props = Map::new();
    props.insert("sep".into(), Value::String(sep));
    props
}

/// The new chunk specs of a new document (`split_document` with fresh IDs).
pub fn new_specs(root: &str, content: &str, markdown: bool) -> Done<Vec<Spec>> {
    split(content, markdown).into_iter()
        .map(|chunk| Ok(Spec { id: store::uuid4()?, parent: root.to_owned(), kind: chunk.kind, props: sep_props(chunk.sep), text: chunk.text }))
        .collect()
}

/// `document_specs`: desired chunk blocks for `content`, reusing the IDs of unchanged chunks.
pub fn document_specs(doc: &PageDoc, content: &str, markdown: bool) -> Done<Vec<Spec>> {
    let current = doc.children(Some(&doc.page_id), false);
    let wanted = split(content, markdown);
    let mut ids: Vec<Option<String>> = vec![None; wanted.len()];
    let old: Vec<&str> = current.iter().map(|item| item.text.as_str()).collect();
    let new: Vec<&str> = wanted.iter().map(|chunk| chunk.text.as_str()).collect();
    for (tag, i1, i2, j1, j2) in difflib::opcodes(&old, &new) {
        if matches!(tag, difflib::Op::Equal | difflib::Op::Replace) {
            for offset in 0..(i2 - i1).min(j2 - j1) {
                ids[j1 + offset] = Some(current[i1 + offset].id.clone());
            }
        }
    }
    ids.into_iter().zip(wanted).map(|(id, chunk)| Ok(Spec {
        id: match id { Some(id) => id, None => store::uuid4()? },
        parent: doc.page_id.clone(), kind: chunk.kind, props: sep_props(chunk.sep), text: chunk.text,
    })).collect()
}

/// `order.keys_between`: `count` ascending keys between `a` and `b`.
fn keys_between(a: Option<&str>, b: Option<&str>, count: usize) -> Done<Vec<String>> {
    let between = |a: Option<&str>, b: Option<&str>| order::key_between(a, b).map_err(|_| internal());
    if count == 0 {
        return Ok(Vec::new());
    }
    if count == 1 {
        return Ok(vec![between(a, b)?]);
    }
    if b.is_none() {
        let mut out = vec![between(a, None)?];
        while out.len() < count {
            let next = between(out.last().map(String::as_str), None)?;
            out.push(next);
        }
        return Ok(out);
    }
    if a.is_none() {
        let mut out = vec![between(None, b)?];
        while out.len() < count {
            let next = between(None, out.last().map(String::as_str))?;
            out.push(next);
        }
        out.reverse();
        return Ok(out);
    }
    let middle = count / 2;
    let pivot = between(a, b)?;
    let mut out = keys_between(a, Some(&pivot), middle)?;
    let after = keys_between(Some(&pivot), b, count - middle - 1)?;
    out.push(pivot);
    out.extend(after);
    Ok(out)
}

/// `insert_ops`: insert operations for new blocks, keyed per parent in listed order.
pub fn insert_ops(specs: Vec<Spec>) -> Done<Vec<Value>> {
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for spec in &specs {
        *counts.entry(spec.parent.as_str()).or_default() += 1;
    }
    let mut keys: HashMap<String, std::vec::IntoIter<String>> = HashMap::new();
    for (parent, count) in counts {
        keys.insert(parent.to_owned(), keys_between(None, None, count)?.into_iter());
    }
    specs.into_iter().map(|spec| {
        let key = keys.get_mut(&spec.parent).and_then(Iterator::next).ok_or_else(internal)?;
        Ok(json!({"op": "insert", "id": spec.id, "type": spec.kind, "parent": spec.parent, "key": key, "props": spec.props, "text": spec.text}))
    }).collect()
}

/// `_longest_increasing`: indexes of one longest strictly increasing subsequence.
fn longest_increasing(keys: &[&str]) -> HashSet<usize> {
    let (mut tails, mut tail_index): (Vec<&str>, Vec<usize>) = (Vec::new(), Vec::new());
    let mut previous = vec![-1_isize; keys.len()];
    for (index, key) in keys.iter().enumerate() {
        let position = tails.partition_point(|tail| tail < key);
        if position == tails.len() {
            tails.push(key);
            tail_index.push(index);
        } else {
            tails[position] = key;
            tail_index[position] = index;
        }
        previous[index] = if position > 0 { tail_index[position - 1] as isize } else { -1 };
    }
    let mut result = HashSet::new();
    let mut cursor = tail_index.last().map_or(-1, |index| *index as isize);
    while cursor >= 0 {
        result.insert(cursor as usize);
        cursor = previous[cursor as usize];
    }
    result
}

/// `props_diff`: a `set` operation's props turning `old` into `new` (None removes a key).
fn props_diff(old: &Map<String, Value>, new: &Map<String, Value>) -> Map<String, Value> {
    let mut changes: Map<String, Value> = new.iter()
        .filter(|(key, value)| old.get(*key).is_none_or(|before| !py::eq(before, value)))
        .map(|(key, value)| (key.clone(), value.clone())).collect();
    for key in old.keys().filter(|key| !new.contains_key(*key)) {
        changes.insert(key.clone(), Value::Null);
    }
    changes
}

/// `sync_tree` scoped to the root's direct children (a document's chunks).
pub fn sync_chunks(doc: &PageDoc, desired: &[Spec]) -> Done<Vec<Value>> {
    let root = doc.page_id.as_str();
    let scope = |item: &Block| item.parent_id.as_deref() == Some(root);
    let mut existing: HashMap<String, &Block> = doc.descendants(root).into_iter()
        .filter_map(|bid| doc.blocks.get(&bid)).filter(|item| item.live() && scope(item))
        .map(|item| (item.id.clone(), item)).collect();
    let mut groups: Vec<(&str, Vec<&str>)> = Vec::new();
    for spec in desired {
        match groups.iter_mut().find(|(parent, _)| *parent == spec.parent) {
            Some((_, ids)) => ids.push(&spec.id),
            None => groups.push((&spec.parent, vec![&spec.id])),
        }
    }
    let mut keys: HashMap<&str, String> = HashMap::new();
    for (parent, ids) in &groups {
        let current: Vec<Option<&Block>> = ids.iter()
            .map(|bid| doc.blocks.get(*bid).filter(|item| item.parent_id.as_deref() == Some(parent))).collect();
        let stable: Vec<usize> = (0..ids.len()).filter(|index| current[*index].is_some()).collect();
        let ordered: Vec<&str> = stable.iter().map(|index| current[*index].expect("stable").order_key.as_str()).collect();
        let kept: HashSet<usize> = longest_increasing(&ordered).into_iter().map(|i| stable[i]).collect();
        let siblings: Vec<String> = if doc.blocks.contains_key(*parent) {
            let mut found: Vec<String> = doc.children(Some(parent), false).into_iter().map(|item| item.order_key.clone()).collect();
            found.sort();
            found
        } else {
            Vec::new()
        };
        let mut floor: Option<String> = None;
        let mut index = 0;
        while index < ids.len() {
            if kept.contains(&index) {
                let key = current[index].expect("kept").order_key.clone();
                keys.insert(ids[index], key.clone());
                floor = Some(key);
                index += 1;
                continue;
            }
            let mut end = index;
            while end < ids.len() && !kept.contains(&end) {
                end += 1;
            }
            let ceiling = if end < ids.len() { Some(current[end].expect("kept").order_key.clone()) } else { None };
            if floor.is_none() && ceiling.is_none() && !siblings.is_empty() {
                floor = siblings.last().cloned();
            }
            for (bid, key) in ids[index..end].iter().zip(keys_between(floor.as_deref(), ceiling.as_deref(), end - index)?) {
                keys.insert(bid, key);
            }
            floor = keys.get(ids[end - 1]).cloned();
            index = end;
        }
    }
    let mut ops = Vec::new();
    let wanted: HashSet<&str> = desired.iter().map(|spec| spec.id.as_str()).collect();
    for spec in desired {
        let key = keys.get(spec.id.as_str()).ok_or_else(internal)?;
        let Some(item) = doc.blocks.get(&spec.id) else {
            ops.push(json!({"op": "insert", "id": spec.id, "type": spec.kind, "parent": spec.parent, "key": key, "props": spec.props, "text": spec.text}));
            continue;
        };
        if !item.live() {
            ops.push(json!({"op": "restore", "id": spec.id}));
            for child in doc.descendants(&spec.id).into_iter().filter_map(|bid| doc.blocks.get(&bid)) {
                if child.deleted_at == item.deleted_at && scope(child) {
                    existing.insert(child.id.clone(), child);
                }
            }
        }
        if item.parent_id.as_deref() != Some(spec.parent.as_str()) || &item.order_key != key {
            ops.push(json!({"op": "move", "id": spec.id, "parent": spec.parent, "key": key}));
        }
        let mut props: Map<String, Value> = item.properties.iter().filter(|(key, _)| is_tag_key(key))
            .map(|(key, value)| (key.clone(), value.clone())).collect();
        props.extend(spec.props.clone());
        if item.kind != spec.kind || !py::eq(&Value::Object(item.properties.clone()), &Value::Object(props.clone())) {
            let mut op = json!({"op": "set", "id": spec.id, "props": props_diff(&item.properties, &props)});
            if item.kind != spec.kind {
                op["type"] = Value::from(spec.kind);
            }
            ops.push(op);
        }
        if item.text != spec.text {
            ops.push(json!({"op": "text", "id": spec.id, "text": spec.text}));
        }
    }
    let doomed: HashSet<&str> = existing.keys().map(String::as_str).filter(|bid| !wanted.contains(bid)).collect();
    let mut sorted: Vec<&str> = doomed.iter().copied().collect();
    sorted.sort_unstable();
    for bid in sorted {
        if existing[bid].parent_id.as_deref().is_none_or(|parent| !doomed.contains(parent)) {
            ops.push(json!({"op": "delete", "id": bid}));
        }
    }
    Ok(ops)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kinds(content: &str) -> Vec<(&'static str, String, String)> {
        split(content, true).into_iter().map(|chunk| (chunk.kind, chunk.text, chunk.sep)).collect()
    }

    #[test]
    fn chunks_restore_the_content_and_name_markdown_kinds() {
        let content = "# H\n\n```\na\n\nb\n```\n\n> q\n\n- x\n\n* * *\n\n12) y\n\n####### no\r\n\r\nend";
        let chunks = kinds(content);
        assert_eq!(chunks.iter().map(|c| c.0).collect::<Vec<_>>(), ["heading", "code", "quote", "list", "divider", "list", "text", "text"]);
        assert_eq!(chunks.iter().map(|c| format!("{}{}", c.1, c.2)).collect::<String>(), content);
        assert_eq!(kinds("\n\nx").first().map(|c| (c.1.as_str(), c.2.as_str())), Some(("", "\n\n")));
        assert!(kinds("").is_empty());
        assert_eq!(lines("a\u{2028}b\r\nc\rd"), ["a\u{2028}", "b\r\n", "c\r", "d"]);
    }

    #[test]
    fn keys_between_matches_python_bisection() {
        assert_eq!(keys_between(None, None, 3).unwrap(), ["a0", "a1", "a2"]);
        assert_eq!(keys_between(None, Some("a0"), 2).unwrap(), ["Zy", "Zz"]);
        assert_eq!(keys_between(Some("a0"), Some("a1"), 3).unwrap(), ["a0G", "a0V", "a0l"]);
    }
}
