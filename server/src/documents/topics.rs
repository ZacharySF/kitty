//! `documents.topics`: a text's most frequent phrases and the note pages sharing them, with
//! the backlinks' tokens, stop words and privacy-safe snippets (crate::backlinks::text).

use indexmap::IndexMap;
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::{folder_id, keys, notebook, text, MAX_TEXT};
use crate::backlinks::text::{phrase, snippet, stopword, Phrase};
use crate::ported::{db, py, Done};
use crate::search::text::is_alnum;

/// Outline (text-flow) and canvas element block types (`OUTLINE_TYPES | CANVAS_TYPES`).
const PAGE_TYPES: &str = "'callout','code','connector','divider','heading_1','heading_2','heading_3','image','ink','numbered_list',\
    'page_link','quote','shape','sticky','text','text_box','to_do','toggle','bulleted_list'";

/// `TOKEN.finditer(value)` as (phrase, code-point start, end) of every run with a phrase.
fn runs(value: &str) -> Vec<(Option<Phrase>, usize, usize)> {
    let mut out = Vec::new();
    let (mut current, mut start) = (String::new(), 0);
    let mut index = 0;
    for c in value.chars() {
        if is_alnum(c) {
            if current.is_empty() {
                start = index;
            }
            current.push(c);
        } else if !current.is_empty() {
            out.push((phrase(&current), start, index));
            current.clear();
        }
        index += 1;
    }
    if !current.is_empty() {
        out.push((phrase(&current), start, index));
    }
    out
}

/// `terms`: a Counter of phrases whose first token is not a stop word, in first-seen order.
fn terms(found: &[(Option<Phrase>, usize, usize)]) -> IndexMap<&Phrase, usize> {
    let mut counts = IndexMap::new();
    for (term, _, _) in found {
        if let Some(term) = term.as_ref().filter(|term| term.first().is_some_and(|first| !stopword(first))) {
            *counts.entry(term).or_default() += 1;
        }
    }
    counts
}

/// `notes.page_texts`: per page root, its canvas elements' texts (an ink stroke is an empty
/// one) in paint order, then its outline texts in reading order.
fn page_texts(conn: &Connection, block_ids: &[String]) -> Done<IndexMap<String, (Vec<String>, Vec<String>)>> {
    let mut result: IndexMap<String, (Vec<String>, Vec<String>)> = block_ids.iter().map(|bid| (bid.clone(), Default::default())).collect();
    for chunk in block_ids.chunks(500) {
        let marks = vec!["?"; chunk.len()].join(",");
        let mut statement = conn.prepare(&format!(
            "SELECT id,page_id,parent_id,type,text,CASE WHEN type='ink' THEN json_array_length(properties,'$.strokes') \
             END AS strokes FROM blocks WHERE deleted_at IS NULL AND type IN ({PAGE_TYPES}) \
             AND page_id IN ({marks}) ORDER BY order_key,id"))?;
        let mut rows = statement.query(rusqlite::params_from_iter(chunk))?;
        let mut children: IndexMap<Option<String>, Vec<(String, String)>> = IndexMap::new();
        while let Some(row) = rows.next()? {
            let (id, page, parent, kind, value): (String, String, Option<String>, String, String) =
                (row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?);
            let entry = result.get_mut(&page);
            if crate::blocks::doc::is_outline(&kind) {
                children.entry(parent).or_default().push((id, value));
            } else if let Some((elements, _)) = entry {
                if kind == "ink" {
                    let strokes: i64 = row.get(5)?;
                    elements.extend(std::iter::repeat_n(String::new(), strokes.max(0) as usize));
                } else {
                    elements.push(value);
                }
            }
        }
        for bid in chunk {
            let mut stack: Vec<&(String, String)> = children.get(&Some(bid.clone())).map(|kids| kids.iter().rev().collect()).unwrap_or_default();
            let mut outline = Vec::new();
            while let Some((id, value)) = stack.pop() {
                outline.push(value.clone());
                if let Some(kids) = children.get(&Some(id.clone())) {
                    stack.extend(kids.iter().rev());
                }
            }
            if let Some((_, texts)) = result.get_mut(bid) {
                texts.extend(outline);
            }
        }
    }
    Ok(result)
}

pub fn topics(conn: &Connection, body: &Map<String, Value>) -> Done<Value> {
    keys(body, &["text", "folder_id", "notebook_id", "exclude_page_id", "scope"], &["text"])?;
    let source = text(&body["text"], MAX_TEXT, "Topic text")?;
    let found = runs(source);
    let query = terms(&found);
    let mut ranked: Vec<(&Phrase, usize)> = query.iter().map(|(term, count)| (*term, *count)).collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1));
    let local: Vec<Value> = ranked.iter().take(12).filter_map(|(term, count)| {
        found.iter().find(|(phrase, _, _)| phrase.as_ref() == Some(*term))
            .map(|(_, start, end)| json!({"term": term[0], "count": count, "snippet": snippet(source, *start, *end)}))
    }).collect();
    if body.get("scope").is_some_and(|scope| scope == "document") {
        return Ok(json!({"topics": local, "matches": [], "scanned": 0}));
    }
    let nid = notebook(conn, body.get("notebook_id"))?;
    let folder = folder_id(conn, body.get("folder_id"))?;
    let rows = if let Some(nid) = nid {
        db::rows(conn, "SELECT id,block_id,title FROM note_page_index WHERE notebook_id=? ORDER BY updated_at DESC LIMIT 1000", [nid])?
    } else if let Some(folder) = folder {
        db::rows(conn, "WITH RECURSIVE descendants(id) AS (SELECT ? UNION SELECT f.id FROM note_folders f JOIN descendants d ON f.parent_id=d.id) \
                        SELECT p.id,p.block_id,p.title FROM note_page_index p JOIN note_notebooks n ON n.id=p.notebook_id \
                        WHERE n.folder_id IN (SELECT id FROM descendants) ORDER BY p.updated_at DESC LIMIT 1000", [folder])?
    } else {
        db::rows(conn, "SELECT id,block_id,title FROM note_page_index ORDER BY updated_at DESC LIMIT 1000", [])?
    };
    let block_ids: Vec<String> = rows.iter().map(|row| row["block_id"].as_str().unwrap_or_default().to_owned()).collect();
    let texts = page_texts(conn, &block_ids)?;
    let exclude = body.get("exclude_page_id").unwrap_or(&Value::Null);
    let (mut matches, mut scanned, mut remaining) = (Vec::new(), 0, 4_000_000_i64);
    for (row, bid) in rows.iter().zip(&block_ids) {
        if py::eq(&row["id"], exclude) {
            continue;
        }
        let title = row["title"].as_str().unwrap_or_default();
        let (elements, outline) = &texts[bid];
        let joined = elements.iter().chain(outline).map(String::as_str).collect::<Vec<_>>().join("\n");
        let value: String = format!("{title}\n{joined}").chars().take(16000).collect();
        remaining -= py::len(&value) as i64;
        if remaining < 0 {
            break;
        }
        scanned += 1;
        let page_runs = runs(&value);
        let page_terms = terms(&page_runs);
        let mut shared: Vec<&Phrase> = page_terms.keys().copied().filter(|term| query.contains_key(*term)).collect();
        if shared.is_empty() {
            continue;
        }
        shared.sort_by(|a, b| query[*b].cmp(&query[*a]).then_with(|| a.cmp(b)));
        shared.truncate(8);
        let hit = page_runs.iter().find(|(phrase, _, _)| phrase.as_ref().is_some_and(|phrase| shared.contains(&phrase)));
        matches.push(json!({"page_id": row["id"], "title": title, "terms": shared.iter().map(|term| &term[0]).collect::<Vec<_>>(),
                            "score": shared.len(), "snippet": hit.map(|(_, start, end)| snippet(&value, *start, *end)).unwrap_or_default()}));
    }
    matches.sort_by(|a, b| b["score"].as_u64().cmp(&a["score"].as_u64()).then_with(|| a["page_id"].as_i64().cmp(&b["page_id"].as_i64())));
    matches.truncate(24);
    Ok(json!({"topics": local, "matches": matches, "scanned": scanned}))
}
