//! A page's outline and the readers over outline text (`kitty.core.note_blocks`): the outline
//! with its live references and citation counts, the blocks citing one block, topic and tag
//! pages, and other pages' titles a page names without linking them.

use std::collections::{BTreeSet, HashSet};

use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};

use super::library::{outline_blocks, page_doc, root, searched};
use super::{bad, offset_checked, text};
use crate::blocks::doc::{slice, OUTLINE_TYPES};
use crate::blocks::text::{mentions as names, without_wikis, REF};
use crate::ported::{db, internal, py, Done};
use crate::search::{query, text::{casefold, words}};

const MAX_BLOCKS: usize = 2000;
const MAX_MENTIONS: usize = 200;
const MAX_DOCUMENT_BYTES: i64 = 2 * 1024 * 1024;

fn outline_sql() -> String {
    let mut kinds = OUTLINE_TYPES.to_vec();
    kinds.sort();
    kinds.iter().map(|kind| format!("'{kind}'")).collect::<Vec<_>>().join(",")
}

/// `_OUTLINE_SOURCE`: joins `s` (a citing block) to a live note page; only outline text cites.
fn outline_source() -> String {
    format!("JOIN blocks sp ON sp.id=s.page_id AND sp.type='page' AND sp.deleted_at IS NULL AND s.type IN ({}) AND s.deleted_at IS NULL", outline_sql())
}

/// `note_blocks.block_id`: a canonical lowercase UUID (the path holds only hex digits and dashes).
fn block_id(value: &str) -> Done<String> {
    let digits = value.bytes().filter(u8::is_ascii_hexdigit).count();
    if digits != 32 || value.len() != 36 {
        return Err(bad("Block ID must be a UUID"));
    }
    if !crate::blocks::doc::is_canonical_uuid(value) {
        return Err(bad("Use canonical lowercase UUID block IDs"));
    }
    Ok(value.to_owned())
}

/// `_render`: a block's text with its references expanded (cycles, a budget and Trash stop it).
fn render(conn: &Connection, bid: &str, seen: &[String], budget: &mut i64) -> Done<String> {
    if seen.iter().any(|item| item == bid) || seen.len() >= 16 {
        return Ok("[cyclic block reference]".into());
    }
    *budget -= 1;
    if *budget < 0 {
        return Ok("[reference expansion limit]".into());
    }
    let Some(row) = db::row(conn, "SELECT text FROM blocks WHERE id=? AND deleted_at IS NULL", [bid])? else {
        return Ok("[deleted or unavailable block]".into());
    };
    let source = row["text"].as_str().ok_or_else(internal)?;
    let mut inner: Vec<String> = seen.to_vec();
    inner.push(bid.to_owned());
    let mut out = String::with_capacity(source.len());
    let mut last = 0;
    for found in REF.captures_iter(source) {
        let whole = found.get(0).ok_or_else(internal)?;
        out.push_str(&source[last..whole.start()]);
        let target = found.get(1).or_else(|| found.get(2)).ok_or_else(internal)?.as_str().to_ascii_lowercase();
        out.push_str(&render(conn, &target, &inner, budget)?);
        last = whole.end();
    }
    out.push_str(&source[last..]);
    Ok(slice(&out, 0, 20_000).to_owned())
}

/// `outline_items`: the outline list (reading order, `ordering` = position) of a page.
fn outline_items(doc: &crate::blocks::doc::PageDoc, number: &Value) -> Vec<Value> {
    outline_blocks(doc).into_iter().enumerate().map(|(index, item)| {
        let props: Map<String, Value> = item.properties.iter().filter(|(key, _)| *key != "collapsed").map(|(k, v)| (k.clone(), v.clone())).collect();
        let parent = if item.parent_id.as_deref() == Some(doc.page_id.as_str()) { Value::Null } else { item.parent_id.clone().map_or(Value::Null, Value::String) };
        json!({"id": item.id, "page_id": number, "parent_id": parent, "text": item.text, "ordering": index,
               "collapsed": item.properties.get("collapsed").is_some_and(py::truthy), "type": item.kind, "props": props})
    }).collect()
}

/// UTF-8 bytes of `text` cut to `limit`, an incomplete last character dropped (`errors="ignore"`).
fn cut_bytes(text: &str, limit: usize) -> &str {
    if text.len() <= limit {
        return text;
    }
    let mut end = limit;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

/// `note_blocks.get_blocks`.
pub fn get_blocks(conn: &Connection, number: i64) -> Done<Value> {
    let doc = page_doc(conn, number)?;
    let root = root(&doc)?;
    let number = root.properties.get("number").cloned().ok_or_else(internal)?;
    let blocks = outline_items(&doc, &number);
    let mut targets: BTreeSet<String> = BTreeSet::new();
    for item in outline_blocks(&doc) {
        for found in REF.captures_iter(&item.text) {
            targets.insert(found.get(1).or_else(|| found.get(2)).ok_or_else(internal)?.as_str().to_ascii_lowercase());
        }
    }
    let mut references = Map::new();
    let mut remaining = MAX_DOCUMENT_BYTES;
    let mut budget = 4000;
    for bid in targets.iter().take(MAX_BLOCKS) {
        let rendered = if remaining > 0 { render(conn, bid, &[], &mut budget)? } else { "[page reference preview limit]".into() };
        // `len(encoded)` counts the cut bytes, a dropped partial last character included.
        let cut = rendered.len().min(remaining.max(0) as usize);
        let kept = if remaining > 0 { cut_bytes(&rendered, cut).to_owned() } else { rendered };
        references.insert(bid.clone(), kept.into());
        remaining -= cut as i64;
    }
    let counts = db::rows(conn, &format!("SELECT r.target_id,COUNT(*) AS count FROM block_refs r JOIN blocks t ON t.id=r.target_id \
        JOIN blocks s ON s.id=r.source_id {} WHERE t.page_id=? AND t.type IN ({}) AND t.deleted_at IS NULL AND r.kind='block' GROUP BY r.target_id",
        outline_source(), outline_sql()), [&doc.page_id])?;
    let counts: Map<String, Value> = counts.into_iter().map(|row| Ok((row["target_id"].as_str().ok_or_else(internal)?.to_owned(), row["count"].clone()))).collect::<Done<_>>()?;
    Ok(json!({"revision": root.properties.get("revision").cloned().ok_or_else(internal)?, "blocks": blocks, "references": references, "citation_counts": counts}))
}

fn next_offset(offset: i64, found: usize, count: i64) -> Value {
    let next = offset + found as i64;
    if next < count { next.into() } else { Value::Null }
}

/// `note_blocks.contexts`: the outline blocks citing one block, newest first.
pub fn contexts(conn: &Connection, value: &str, offset: i64) -> Done<Value> {
    let bid = block_id(value)?;
    let offset = offset_checked(offset)?;
    let rows = db::rows(conn, "SELECT b.id,b.page_id,b.parent_id,substr(b.text,1,2000) AS text,b.title,substr(parent.text,1,400) AS parent_text \
        FROM block_refs r JOIN note_outline b ON b.id=r.source_id LEFT JOIN blocks parent ON parent.id=b.parent_id \
        WHERE r.target_id=? AND r.kind='block' ORDER BY b.updated_at DESC,b.path,b.id LIMIT 500 OFFSET ?", params![bid, offset])?;
    let count: i64 = conn.query_row(&format!("SELECT COUNT(*) FROM block_refs r JOIN blocks s ON s.id=r.source_id {} WHERE r.target_id=? AND r.kind='block'",
                                             outline_source()), [&bid], |row| row.get(0))?;
    let original = db::row(conn, "SELECT page_id,title FROM note_outline WHERE id=?", [&bid])?;
    let (page, title) = match &original {
        Some(row) => (row["page_id"].clone(), row["title"].clone()),
        None => (Value::Null, Value::String(String::new())),
    };
    let rendered = render(conn, &bid, &[], &mut 200)?;
    let next = next_offset(offset, rows.len(), count);
    Ok(json!({"id": bid, "page_id": page, "title": title, "text": rendered, "count": count, "contexts": rows, "next_offset": next}))
}

/// `note_blocks.topics`: the outline blocks linking `[[topic]]` or tagged `#topic`.
pub fn topics(conn: &Connection, kind: &str, topic: &str, offset: i64) -> Done<Value> {
    if kind != "page" && kind != "tag" {
        return Err(bad("Unknown topic kind"));
    }
    let topic = casefold(py::strip(text(&Value::String(topic.to_owned()), 200, "Text")?));
    let offset = offset_checked(offset)?;
    let rows = db::rows(conn, "SELECT b.id,b.page_id,b.parent_id,substr(b.text,1,2000) AS text,b.title FROM block_topics t JOIN note_outline b ON b.id=t.block_id \
        WHERE t.kind=? AND t.topic=? ORDER BY b.updated_at DESC,b.path,b.id LIMIT 500 OFFSET ?", params![kind, topic, offset])?;
    let count: i64 = conn.query_row(&format!("SELECT COUNT(*) FROM block_topics t JOIN blocks s ON s.id=t.block_id {} WHERE t.kind=? AND t.topic=?", outline_source()),
                                    params![kind, topic], |row| row.get(0))?;
    let next = next_offset(offset, rows.len(), count);
    Ok(json!({"kind": kind, "topic": topic, "blocks": rows, "count": count, "next_offset": next}))
}

/// `note_blocks.mentions`: this page's blocks naming another page's title without linking it.
pub fn mentions(conn: &Connection, number: i64) -> Done<Value> {
    let doc = page_doc(conn, number)?;
    let flow: Vec<(&crate::blocks::doc::Block, String)> = outline_blocks(&doc).into_iter().map(|item| (item, without_wikis(&item.text))).collect();
    let tokens: HashSet<String> = flow.iter().flat_map(|(_, plain)| words(plain)).collect();
    let mut rows = searched(query::page_titles_within(conn, &tokens, Some(&doc.page_id)))?;
    let title = |row: &Value| row["title"].as_str().unwrap_or("").to_owned();
    rows.sort_by_key(|row| (std::cmp::Reverse(py::len(&title(row))), row["id"].as_i64()));
    let patterns: Vec<(&Value, String)> = rows.iter().map(|row| (row, title(row))).filter(|(_, title)| !py::strip(title).is_empty()).collect();
    let mut result = Vec::new();
    for (block, plain) in &flow {
        for (row, title) in &patterns {
            if names(title, plain) {
                if result.len() == MAX_MENTIONS {
                    return Ok(json!({"mentions": result, "truncated": true}));
                }
                result.push(json!({"block_id": block.id, "page_id": row["id"], "title": title, "text": slice(&block.text, 0, 400)}));
            }
        }
    }
    Ok(json!({"mentions": result, "truncated": false}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_and_cuts_are_pythons() {
        assert_eq!(block_id("0123456789abcdef0123456789abcdef----").unwrap_err().message, "Use canonical lowercase UUID block IDs");
        assert_eq!(block_id("01234567-89ab-cdef-0123-456789abcd--").unwrap_err().message, "Block ID must be a UUID");
        assert_eq!(cut_bytes("aé", 2), "a");
    }
}
