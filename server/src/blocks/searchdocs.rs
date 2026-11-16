//! The block half of the search index (`kitty.core.blocks.searchdocs`): one `search_docs`
//! row per searchable block, folded inside every transaction (crate::search holds the index).

use rusqlite::Connection;
use serde_json::Value;

use super::doc::Block;
use crate::ported::{internal, Done};
use crate::search::docs::{self, Doc, Failure, TEXT_TYPES};
use crate::search::text::{head, quick_title, strip};

fn failed(failure: Failure) -> crate::ported::Fault {
    match failure {
        Failure::Database(error) => error.into(),
        Failure::Data(_) => internal(),
    }
}

/// `[term for key in ("keywords", "aliases") for term in (properties.get(key) or []) if isinstance(term, str)]`.
fn terms(block: &Block) -> Done<Vec<String>> {
    let mut out = Vec::new();
    for key in ["keywords", "aliases"] {
        match block.properties.get(key) {
            None | Some(Value::Null) | Some(Value::Bool(false)) => {}
            Some(Value::String(text)) => out.extend(text.chars().map(String::from)),
            Some(Value::Array(items)) => out.extend(items.iter().filter_map(|item| item.as_str().map(str::to_owned))),
            Some(Value::Object(map)) => out.extend(map.keys().cloned()),
            // Zero is falsy (`or []`); any other number or `True` is not iterable: a TypeError.
            Some(Value::Number(number)) if number.as_f64() == Some(0.0) => {}
            Some(_) => return Err(internal()),
        }
    }
    Ok(out)
}

/// `searchdocs.block_doc`: the document for one block, or None when it is not searchable.
pub fn block_doc(block: &Block) -> Done<Option<Doc>> {
    if !block.live() {
        return Ok(None);
    }
    let doc = |title: String, body: String, label: String| Doc {
        kind: "block", reference: block.id.clone(), page_id: Some(block.page_id.clone()), kind_of_block: block.kind.clone(),
        title, body, label, flags: 0,
    };
    if block.parent_id.is_none() {
        return Ok(match block.kind.as_str() {
            "page" | "document" => Some(doc(block.text.clone(), String::new(), head(&block.text, 200).to_owned())),
            "quick_note" => Some(doc(String::new(), block.text.clone(), quick_title(&block.text).to_owned())),
            "tag" => Some(doc(block.text.clone(), terms(block)?.join("\n"), head(&block.text, 200).to_owned())),
            _ => None,
        });
    }
    Ok((TEXT_TYPES.contains(&block.kind.as_str()) && !strip(&block.text).is_empty())
        .then(|| doc(String::new(), block.text.clone(), String::new())))
}

/// `searchdocs.project`: fold one transaction's changed and removed blocks into the index.
pub fn project(conn: &Connection, changed: &[Block], removed: &[String]) -> Done<()> {
    let present: std::collections::HashSet<&str> = changed.iter().map(|block| block.id.as_str()).collect();
    let mut gone: Vec<(String, String)> = removed.iter().filter(|bid| !present.contains(bid.as_str())).map(|bid| ("block".to_owned(), bid.clone())).collect();
    let mut made = Vec::new();
    for block in changed {
        match block_doc(block)? {
            Some(doc) => made.push(doc),
            None => gone.push(("block".to_owned(), block.id.clone())),
        }
    }
    if (!made.is_empty() || !gone.is_empty()) && docs::has_index(conn).map_err(failed)? {
        docs::write(conn, &made, &gone).map_err(failed)?;
    }
    Ok(())
}
