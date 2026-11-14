//! Queries over the index, as `kitty.core.search` answers them: `search` (one hit per record),
//! `block_hits` (one hit per block), and the candidate lookups Notes uses.

use std::collections::HashSet;

use rusqlite::{params_from_iter, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use super::docs::{self, Result};
use super::text::{head, length, quick_title, split, words};

pub const MAX_QUERY: usize = 300;
pub const MAX_TOKENS: usize = 16;
pub const MAX_LIMIT: i64 = 200;
const HIT: char = '\u{2}';
const END: char = '\u{3}';

/// A query `search` refuses (Python's `ValueError`, a 400).
pub struct Refused(pub String);

pub enum Error {
    Refused(Refused),
    Failed(docs::Failure),
}

impl From<docs::Failure> for Error {
    fn from(failure: docs::Failure) -> Self {
        Error::Failed(failure)
    }
}

impl From<rusqlite::Error> for Error {
    fn from(error: rusqlite::Error) -> Self {
        Error::Failed(docs::Failure::Database(error))
    }
}

/// `KINDS`: result kind, its SQL over `d`/`r`, the view that opens it and the module it needs.
const KINDS: &[(&str, &str, &str, &str)] = &[
    ("note_page", "d.kind='block' AND r.type='page'", "notes", "workspace"),
    ("document", "d.kind='block' AND r.type='document'", "documents", "workspace"),
    ("quick_note", "d.kind='block' AND r.type='quick_note'", "quick-notes", "workspace"),
    ("tag", "d.kind='block' AND r.type='tag'", "settings", "workspace"),
    ("study_subject", "d.kind='study_subject'", "study", "workspace"),
    ("study_project", "d.kind='study_project'", "study", "workspace"),
    ("study_task", "d.kind='study_task'", "study", "workspace"),
    ("calendar_event", "d.kind='calendar_event'", "calendar", "workspace"),
    ("calendar_task", "d.kind='calendar_task'", "calendar", "workspace"),
    ("flashcard_deck", "d.kind='flashcard_deck'", "flashcards", "workspace"),
    ("flashcard_note", "d.kind='flashcard_note'", "flashcards", "workspace"),
    ("job", "d.kind='job'", "jobs", "applyai"),
];

fn view(kind: &str) -> &'static str {
    KINDS.iter().find(|entry| entry.0 == kind).map(|entry| entry.2).expect("a known kind")
}

/// `search.prefix_query`: every word must start an indexed word.
pub fn prefix_query(text: &str) -> Option<String> {
    let tokens = words(text);
    let parts: Vec<String> = tokens.iter().take(MAX_TOKENS).map(|token| format!("\"{token}\"*")).collect();
    (!parts.is_empty()).then(|| parts.join(" AND "))
}

/// `search.substring_query`: the whole query as a substring (three characters or more).
pub fn substring_query(text: &str) -> Option<String> {
    let text = split(text).collect::<Vec<_>>().join(" ");
    (length(&text) >= 3).then(|| format!("\"{}\"", text.replace('"', "\"\"")))
}

/// `search.segments`: a marked snippet as `[[text, is_hit], ...]`.
fn segments(marked: &str) -> Vec<Value> {
    let mut result = Vec::new();
    let mut hit = false;
    for part in marked.split_inclusive([HIT, END]) {
        let (text, marker) = match part.chars().last() {
            Some(c @ (HIT | END)) => (&part[..part.len() - c.len_utf8()], Some(c)),
            _ => (part, None),
        };
        if !text.is_empty() {
            result.push(json!([text, hit]));
        }
        match marker {
            Some(HIT) => hit = true,
            Some(_) => hit = false,
            None => {}
        }
    }
    result
}

fn marks(conn: &Connection, table: &str, query: &str, rowid: i64, tokens: i64) -> Result<(Vec<Value>, Vec<Value>)> {
    let sql = format!("SELECT highlight({table},0,?,?),snippet({table},1,?,?,'…',?) FROM {table} WHERE {table} MATCH ? AND rowid=?");
    let row: Option<(Option<String>, Option<String>)> = conn.prepare(&sql)?
        .query_row(rusqlite::params![HIT.to_string(), END.to_string(), HIT.to_string(), END.to_string(), tokens, query, rowid],
                   |row| Ok((row.get(0)?, row.get(1)?)))
        .optional()?;
    Ok(match row {
        Some((title, snippet)) => (segments(title.as_deref().unwrap_or("")), segments(snippet.as_deref().unwrap_or(""))),
        None => (Vec::new(), Vec::new()),
    })
}

fn check_text(text: &str) -> std::result::Result<(), Error> {
    if length(text) > MAX_QUERY || text.contains('\0') {
        return Err(Error::Refused(Refused(format!("Search text must be a string of at most {MAX_QUERY} characters"))));
    }
    Ok(())
}

fn check_limit(limit: i64) -> std::result::Result<(), Error> {
    if !(1..=MAX_LIMIT).contains(&limit) {
        return Err(Error::Refused(Refused(format!("Limit must be 1-{MAX_LIMIT}"))));
    }
    Ok(())
}

/// `search.kind_filter`: SQL admitting the kinds the active modules show (and `kinds`, when given).
fn kind_filter(kinds: Option<&HashSet<String>>, modules: &[String]) -> String {
    let active = |module: &str| modules.iter().any(|m| m == module);
    let clauses: Vec<String> = KINDS.iter()
        .filter(|(kind, _, _, module)| active(module) && kinds.is_none_or(|kinds| kinds.contains(*kind)))
        .map(|(kind, clause, _, _)| {
            let extra = if *kind == "calendar_event" && !active("applyai") { " AND d.flags & 1 = 0" } else { "" };
            format!("({clause}{extra})")
        })
        .collect();
    if clauses.is_empty() { "0".into() } else { clauses.join(" OR ") }
}

struct Plan {
    table: &'static str,
    query: String,
    matched: &'static str,
    tokens: i64,
}

fn plans(text: &str, word_tokens: i64, gram_tokens: i64) -> Vec<Plan> {
    [("search_fts", prefix_query(text), "word", word_tokens), ("search_grams", substring_query(text), "substring", gram_tokens)]
        .into_iter()
        .filter_map(|(table, query, matched, tokens)| query.map(|query| Plan { table, query, matched, tokens }))
        .collect()
}

/// Runs `body` in one read transaction (`transaction(conn)`), unless one is open already.
fn reading<T>(conn: &Connection, body: impl FnOnce() -> std::result::Result<T, Error>) -> std::result::Result<T, Error> {
    let own = conn.is_autocommit();
    if own {
        conn.execute_batch("BEGIN")?;
    }
    let outcome = body();
    if own {
        match &outcome {
            Ok(_) => conn.execute_batch("COMMIT")?,
            Err(_) => {
                let _ = conn.execute_batch("ROLLBACK");
            }
        }
    }
    outcome
}

/// `search.require` then `search.sync`.
pub fn synced(conn: &Connection) -> Result<()> {
    if !docs::has_index(conn)? {
        return Err(docs::Failure::Data("This database has no search index; open it with rika! to apply migration 047"));
    }
    docs::sync(conn)?;
    Ok(())
}

/// `search.search`: ranked records for a typed query, one hit per record, with highlighted snippets.
pub fn search(conn: &Connection, text: &str, limit: i64, kinds: Option<&[String]>, modules: &[String]) -> std::result::Result<Value, Error> {
    check_text(text)?;
    check_limit(limit)?;
    let kinds: Option<HashSet<String>> = kinds.map(|kinds| kinds.iter().cloned().collect());
    if let Some(kinds) = &kinds {
        if kinds.iter().any(|kind| !KINDS.iter().any(|entry| entry.0 == kind)) {
            let names: Vec<&str> = KINDS.iter().map(|entry| entry.0).collect();
            return Err(Error::Refused(Refused(format!("Kinds are {}", names.join(", ")))));
        }
    }
    synced(conn)?;
    let allowed = kind_filter(kinds.as_ref(), modules);
    let plans = plans(text, 12, 48);
    reading(conn, || {
        struct Found {
            rowid: i64,
            plan: usize,
            doc_title: String,
            hit: Map<String, Value>,
        }
        let mut hits: Vec<Found> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        let mut more = false;
        for (index, plan) in plans.iter().enumerate() {
            if more {
                break;
            }
            let sql = format!(
                "SELECT d.id,d.kind,d.ref,d.page_id,d.title AS doc_title,d.label,r.type AS root_type,r.text AS root_title,
    CAST(json_extract(r.properties,'$.number') AS INTEGER) AS number,bm25({table},8.0,1.0) AS score
    FROM {table} JOIN search_docs d ON d.id={table}.rowid LEFT JOIN blocks r ON d.kind='block' AND r.id=d.page_id
    WHERE {table} MATCH ? AND ({allowed}) ORDER BY score, d.id", table = plan.table);
            let mut statement = conn.prepare(&sql)?;
            let mut rows = statement.query([&plan.query])?;
            while let Some(row) = rows.next()? {
                let doc_kind: String = row.get("kind")?;
                let reference: String = row.get("ref")?;
                let (kind, key, ident, title): (String, String, Value, String) = if doc_kind == "block" {
                    let root_type: Option<String> = row.get("root_type")?;
                    let page_id: Option<String> = row.get("page_id")?;
                    let page_id = page_id.unwrap_or_default();
                    let root_title: String = row.get::<_, Option<String>>("root_title")?.unwrap_or_default();
                    let kind = match root_type.as_deref() {
                        Some("page") => "note_page",
                        Some("document") => "document",
                        Some("quick_note") => "quick_note",
                        Some("tag") => "tag",
                        _ => return Err(Error::Failed(docs::Failure::Data("a block document outside a known root"))),
                    };
                    let ident = if kind == "tag" { Value::String(page_id.clone()) } else { json!(row.get::<_, Option<i64>>("number")?) };
                    let title = if kind == "quick_note" { quick_title(&root_title).to_owned() } else { head(&root_title, 200).to_owned() };
                    (kind.into(), format!("block:{page_id}"), ident, title)
                } else {
                    let numeric = !reference.is_empty() && reference.bytes().all(|b| b.is_ascii_digit())
                        && doc_kind != "calendar_event" && doc_kind != "calendar_task";
                    let ident = match reference.parse::<i64>() {
                        Ok(number) if numeric => json!(number),
                        _ => Value::String(reference.clone()),
                    };
                    let label: String = row.get("label")?;
                    (doc_kind.clone(), format!("{doc_kind}:{reference}"), ident, label)
                };
                if seen.contains(&key) {
                    continue;
                }
                if hits.len() as i64 >= limit {
                    more = true;
                    break;
                }
                seen.insert(key);
                let mut hit = Map::new();
                hit.insert("kind".into(), Value::String(kind.clone()));
                hit.insert("id".into(), ident);
                hit.insert("title".into(), Value::String(title));
                hit.insert("view".into(), Value::String(view(&kind).into()));
                hit.insert("match".into(), Value::String(plan.matched.into()));
                hits.push(Found { rowid: row.get("id")?, plan: index, doc_title: row.get("doc_title")?, hit });
            }
        }
        let mut total = hits.len() as i64;
        if more {
            let union: Vec<String> = plans.iter().map(|plan| format!(
                "SELECT CASE WHEN d.kind='block' THEN 'block:'||d.page_id ELSE d.kind||':'||d.ref END
    FROM {table} JOIN search_docs d ON d.id={table}.rowid LEFT JOIN blocks r ON d.kind='block' AND r.id=d.page_id
    WHERE {table} MATCH ? AND ({allowed})", table = plan.table)).collect();
            total = conn.query_row(&format!("SELECT COUNT(*) FROM ({})", union.join(" UNION ")),
                                   params_from_iter(plans.iter().map(|plan| plan.query.as_str())), |row| row.get(0))?;
        }
        let mut result = Vec::new();
        for Found { rowid, plan, doc_title, mut hit } in hits {
            let plan = &plans[plan];
            let (title, snippet) = marks(conn, plan.table, &plan.query, rowid, plan.tokens)?;
            let whole = hit["title"].as_str().unwrap_or("").to_owned();
            // The record's title is marked where the hit document's own title is it (a page root,
            // a job's title before " · company"); a block inside a page shows its page title plainly.
            let rest = (!doc_title.is_empty() && whole.starts_with(&doc_title)).then(|| &whole[doc_title.len()..]);
            let marked = rest.is_some() && title.iter().any(|part| part[1] == Value::Bool(true));
            let title_marks = if marked {
                let mut parts = title;
                if let Some(rest) = rest.filter(|rest| !rest.is_empty()) {
                    parts.push(json!([rest, false]));
                }
                parts
            } else {
                vec![json!([whole, false])]
            };
            hit.insert("title_marks".into(), Value::Array(title_marks));
            hit.insert("snippet".into(), Value::Array(snippet));
            result.push(Value::Object(hit));
        }
        Ok(json!({ "query": text, "hits": result, "total": total, "truncated": more }))
    })
}

const BLOCK_WHERE: &str = "d.kind='block' AND r.deleted_at IS NULL AND r.type IN ('page','document','quick_note')";

/// `search.block_hits`: ranked blocks of note pages, documents and quick notes, one hit per block.
pub fn block_hits(conn: &Connection, text: &str, limit: i64) -> std::result::Result<Value, Error> {
    check_text(text)?;
    check_limit(limit)?;
    if !docs::has_index(conn)? {
        return Err(Error::Failed(docs::Failure::Data("This database has no search index; open it with rika! to apply migration 047")));
    }
    let plans = plans(text, 16, 64);
    reading(conn, || {
        let mut hits: Vec<(i64, usize, Map<String, Value>)> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        let mut more = false;
        for (index, plan) in plans.iter().enumerate() {
            if more {
                break;
            }
            let sql = format!(
                "SELECT d.id,d.ref,d.page_id,d.type,r.type AS root_type,r.text AS root_title,
    CAST(json_extract(r.properties,'$.number') AS INTEGER) AS number,bm25({table},8.0,1.0) AS score
    FROM {table} JOIN search_docs d ON d.id={table}.rowid JOIN blocks r ON r.id=d.page_id
    WHERE {table} MATCH ? AND {BLOCK_WHERE} ORDER BY score, d.id", table = plan.table);
            let mut statement = conn.prepare(&sql)?;
            let mut rows = statement.query([&plan.query])?;
            while let Some(row) = rows.next()? {
                let reference: String = row.get("ref")?;
                if seen.contains(&reference) {
                    continue;
                }
                if hits.len() as i64 >= limit {
                    more = true;
                    break;
                }
                seen.insert(reference.clone());
                let root_type: String = row.get("root_type")?;
                let root_title: String = row.get("root_title")?;
                let page_title = if root_type == "quick_note" { quick_title(&root_title) } else { head(&root_title, 200) }.to_owned();
                let mut hit = Map::new();
                hit.insert("block_id".into(), Value::String(reference));
                hit.insert("page_id".into(), json!(row.get::<_, Option<String>>("page_id")?));
                hit.insert("root_type".into(), Value::String(root_type));
                hit.insert("number".into(), json!(row.get::<_, Option<i64>>("number")?));
                hit.insert("page_title".into(), Value::String(page_title));
                hit.insert("type".into(), Value::String(row.get("type")?));
                hit.insert("match".into(), Value::String(plan.matched.into()));
                hits.push((row.get("id")?, index, hit));
            }
        }
        let mut total = hits.len() as i64;
        if more {
            let union: Vec<String> = plans.iter().map(|plan| format!(
                "SELECT d.ref FROM {table} JOIN search_docs d ON d.id={table}.rowid JOIN blocks r ON r.id=d.page_id WHERE {table} MATCH ? AND {BLOCK_WHERE}",
                table = plan.table)).collect();
            total = conn.query_row(&format!("SELECT COUNT(*) FROM ({})", union.join(" UNION ")),
                                   params_from_iter(plans.iter().map(|plan| plan.query.as_str())), |row| row.get(0))?;
        }
        let mut result = Vec::new();
        for (rowid, plan, mut hit) in hits {
            let plan = &plans[plan];
            let (title, snippet) = marks(conn, plan.table, &plan.query, rowid, plan.tokens)?;
            let has_text = snippet.iter().any(|part| part[0].as_str().is_some_and(|text| !text.is_empty()));
            hit.insert("snippet".into(), Value::Array(if has_text { snippet } else { title }));
            result.push(Value::Object(hit));
        }
        Ok(json!({ "query": text, "hits": result, "total": total, "truncated": more }))
    })
}

/// `search.page_matches`: root block ids of note pages whose title or text matches.
pub fn page_matches(conn: &Connection, text: &str) -> Result<HashSet<String>> {
    synced(conn)?;
    let mut found = HashSet::new();
    for (table, query) in [("search_fts", prefix_query(text)), ("search_grams", substring_query(text))] {
        let Some(query) = query else { continue };
        let sql = format!("SELECT DISTINCT d.page_id FROM {table} JOIN search_docs d ON d.id={table}.rowid
                JOIN blocks r ON r.id=d.page_id WHERE {table} MATCH ? AND d.kind='block' AND r.type='page'");
        let mut statement = conn.prepare(&sql)?;
        let rows = statement.query_map([&query], |row| row.get::<_, String>(0))?;
        for row in rows {
            found.insert(row?);
        }
    }
    Ok(found)
}

/// `search.phrase_blocks`: `(block id, page id)` of searchable non-root blocks holding `text`'s words as one phrase.
pub fn phrase_blocks(conn: &Connection, text: &str) -> Result<Vec<(String, String)>> {
    let tokens: Vec<String> = words(text).into_iter().take(MAX_TOKENS).collect();
    if tokens.is_empty() {
        return Ok(Vec::new());
    }
    if !docs::has_index(conn)? {
        return Err(docs::Failure::Data("This database has no search index; open it with rika! to apply migration 047"));
    }
    let expression = format!("{{body}} : \"{}\"", tokens.join(" "));
    let sql = format!("SELECT d.ref,d.page_id FROM search_fts JOIN search_docs d ON d.id=search_fts.rowid JOIN blocks r ON r.id=d.page_id \
        WHERE search_fts MATCH ? AND d.ref<>d.page_id AND {BLOCK_WHERE} ORDER BY d.id");
    let mut statement = conn.prepare(&sql)?;
    let rows = statement.query_map([&expression], |row| Ok((row.get(0)?, row.get(1)?)))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// `search.page_titles_within`: `{id, block_id, title}` of live note pages whose title's words all occur in `tokens`.
pub fn page_titles_within(conn: &Connection, tokens: &HashSet<String>, exclude: Option<&str>) -> Result<Vec<Value>> {
    synced(conn)?;
    let mut ordered: Vec<&String> = tokens.iter().collect();
    // Python sorts `str` by code point, as Rust orders UTF-8.
    ordered.sort();
    let mut found: Vec<Value> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for chunk in ordered.chunks(200) {
        let expression = format!("{{title}} : ({})", chunk.iter().map(|token| format!("\"{token}\"")).collect::<Vec<_>>().join(" OR "));
        let mut statement = conn.prepare("SELECT d.ref,d.title,CAST(json_extract(r.properties,'$.number') AS INTEGER) AS number
                FROM search_fts JOIN search_docs d ON d.id=search_fts.rowid JOIN blocks r ON r.id=d.ref
                WHERE search_fts MATCH ? AND d.kind='block' AND d.type='page' AND d.ref=d.page_id")?;
        let mut rows = statement.query([&expression])?;
        while let Some(row) = rows.next()? {
            let reference: String = row.get(0)?;
            let title: String = row.get(1)?;
            if Some(reference.as_str()) != exclude && !seen.contains(&reference) && words(&title).iter().all(|word| tokens.contains(word)) {
                seen.insert(reference.clone());
                found.push(json!({ "id": row.get::<_, Option<i64>>(2)?, "block_id": reference, "title": title }));
            }
        }
    }
    Ok(found)
}

