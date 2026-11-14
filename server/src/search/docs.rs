//! The documents of the index (`kitty.core.search` sources, `blocks.searchdocs.block_doc`) and
//! `search.sync`, which folds the `search_pending` queue into `search_docs`.
//!
//! Every document is made from its source row exactly as Python makes it, in the same order and
//! with the same SQL, so both writers give the same rows (and the same rowids, which break
//! ranking ties).

use std::collections::{HashMap, HashSet};

use rusqlite::types::Value as Sql;
use rusqlite::{params_from_iter, Connection, OptionalExtension, Row};

use super::text::{clean, head, quick_title, strip};
use crate::builds::json;
use crate::builds::value::{lossy, text as code_points, Value as Py};

/// One indexed record: `(kind, ref)` is its identity; `title` and `body` are indexed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Doc {
    pub kind: &'static str,
    pub reference: String,
    pub page_id: Option<String>,
    pub kind_of_block: String,
    pub title: String,
    pub body: String,
    pub label: String,
    pub flags: i64,
}

/// `TEXT_TYPES`: the outline/editor types, canvas text boxes and stickies, and document chunks.
pub const TEXT_TYPES: &[&str] = &[
    "bulleted_list", "callout", "code", "divider", "heading", "heading_1", "heading_2", "heading_3", "list",
    "numbered_list", "page_link", "quote", "sticky", "text", "text_box", "to_do", "toggle",
];
/// `calendar.APPLYAI_SOURCES`, and the flag bit a calendar copy of them carries.
const APPLYAI_SOURCES: &[&str] = &["job", "application", "status", "reminder"];
pub const APPLYAI: i64 = 1;

/// Anything that stops a document from being made: a database error, or a row Python could
/// not have read either (its `TypeError`/`JSONDecodeError`, a 500 there too).
#[derive(Debug)]
pub enum Failure {
    Database(rusqlite::Error),
    Data(&'static str),
}

impl From<rusqlite::Error> for Failure {
    fn from(error: rusqlite::Error) -> Self {
        Failure::Database(error)
    }
}

pub type Result<T, E = Failure> = std::result::Result<T, E>;

/// `json.loads(value) if value else default`, failing quietly as `search._json` does.
fn loads(value: Option<&str>) -> Option<Py> {
    let value = value.filter(|value| !value.is_empty())?;
    json::parse(&code_points(value)).ok().map(|parsed| parsed.value)
}

fn string(value: Option<&Py>) -> Option<String> {
    value.and_then(Py::as_str).map(|text| lossy(text))
}

fn required(row: &Row, column: &str) -> Result<String> {
    row.get::<_, Option<String>>(column)?.ok_or(Failure::Data("a required column is NULL"))
}

/// A block as `store._block` reads its projection row: the parts a document needs.
pub struct BlockRow {
    pub id: String,
    pub kind: String,
    pub parent_id: Option<String>,
    pub page_id: String,
    pub properties: Py,
    pub text: String,
    pub live: bool,
}

impl BlockRow {
    pub fn read(row: &Row) -> Result<BlockRow> {
        let properties: String = row.get("properties")?;
        let properties = json::parse(&code_points(&properties)).map_err(|_| Failure::Data("block properties are not JSON"))?.value;
        Ok(BlockRow {
            id: row.get("id")?,
            kind: row.get("type")?,
            parent_id: row.get("parent_id")?,
            page_id: row.get("page_id")?,
            properties,
            text: row.get("text")?,
            live: row.get::<_, Option<String>>("deleted_at")?.is_none(),
        })
    }
}

/// Python truthiness of a property value.
fn truthy(value: &Py) -> bool {
    match value {
        Py::Null => false,
        Py::Bool(flag) => *flag,
        Py::Int(crate::builds::value::Int::Small(number)) => *number != 0,
        Py::Int(_) => true,
        Py::Float(number) => *number != 0.0,
        Py::Str(text) => !text.is_empty(),
        Py::List(items) => !items.is_empty(),
        Py::Dict(dict) => dict.len() > 0,
    }
}

/// `[term for term in (properties.get(key) or []) if isinstance(term, str)]`.
fn terms(properties: &Py, key: &str, out: &mut Vec<String>) -> Result<()> {
    let Some(value) = properties.as_dict().and_then(|dict| dict.get_str(key)) else { return Ok(()) };
    if !truthy(value) {
        return Ok(());
    }
    match value {
        // Iterating a string gives its characters, a dict its keys: all strings.
        Py::Str(text) => out.extend(text.iter().map(|&c| lossy(&[c]))),
        Py::Dict(dict) => out.extend(dict.keys().map(|key| lossy(key))),
        Py::List(items) => out.extend(items.iter().filter_map(|item| item.as_str().map(|text| lossy(text)))),
        _ => return Err(Failure::Data("tag terms are not iterable")),
    }
    Ok(())
}

/// `searchdocs.block_doc`: the document for one block, or None when it is not searchable.
pub fn block_doc(block: &BlockRow) -> Result<Option<Doc>> {
    if !block.live {
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
            "tag" => {
                let mut found = Vec::new();
                terms(&block.properties, "keywords", &mut found)?;
                terms(&block.properties, "aliases", &mut found)?;
                Some(doc(block.text.clone(), found.join("\n"), head(&block.text, 200).to_owned()))
            }
            _ => None,
        });
    }
    Ok((TEXT_TYPES.contains(&block.kind.as_str()) && !strip(&block.text).is_empty())
        .then(|| doc(String::new(), block.text.clone(), String::new())))
}

/// `search._chunks` plus `_select`: every row (`refs` None) or the named ones, 500 per query.
fn select(conn: &Connection, sql: &str, column: &str, refs: Option<&[String]>, numeric: bool,
          mut each: impl FnMut(&Row) -> Result<()>) -> Result<()> {
    let run = |sql: String, values: Vec<Sql>, each: &mut dyn FnMut(&Row) -> Result<()>| -> Result<()> {
        let mut statement = conn.prepare(&sql)?;
        let mut rows = statement.query(params_from_iter(values))?;
        while let Some(row) = rows.next()? {
            each(row)?;
        }
        Ok(())
    };
    let Some(refs) = refs else {
        return run(sql.replace("{where}", "1"), Vec::new(), &mut each);
    };
    // `[int(ref) for ref in refs if ref.isdigit()]`: the queue holds ids as their decimal text.
    let values: Vec<Sql> = if numeric {
        refs.iter().filter(|r| !r.is_empty() && r.bytes().all(|b| b.is_ascii_digit())).filter_map(|r| r.parse::<i64>().ok()).map(Sql::Integer).collect()
    } else {
        refs.iter().map(|r| Sql::Text(r.clone())).collect()
    };
    for chunk in values.chunks(500) {
        let marks = vec!["?"; chunk.len()].join(",");
        run(sql.replace("{where}", &format!("{column} IN ({marks})")), chunk.to_vec(), &mut each)?;
    }
    Ok(())
}

fn plain(kind: &'static str, reference: String, title: String, body: String, label: String, flags: i64) -> Doc {
    Doc { kind, reference, page_id: None, kind_of_block: String::new(), title, body, label, flags }
}

fn id_text(row: &Row) -> Result<String> {
    Ok(match row.get::<_, Sql>("id")? {
        Sql::Integer(number) => number.to_string(),
        Sql::Text(text) => text,
        _ => return Err(Failure::Data("an id that is neither text nor integer")),
    })
}

/// The source kinds besides blocks, in `search.SOURCES` order.
pub const SOURCES: &[&str] = &[
    "study_subject", "study_project", "study_task", "calendar_event", "calendar_task", "flashcard_deck", "flashcard_note", "job",
];

/// The documents of one source kind: every record, or the named ones.
pub fn source(conn: &Connection, kind: &str, refs: Option<&[String]>, out: &mut Vec<Doc>) -> Result<()> {
    match kind {
        "study_subject" | "study_project" | "flashcard_deck" => {
            let (kind, sql): (&'static str, _) = match kind {
                "study_subject" => ("study_subject", "SELECT id,name FROM study_subjects WHERE {where}"),
                "study_project" => ("study_project", "SELECT id,name FROM study_projects WHERE {where}"),
                _ => ("flashcard_deck", "SELECT id,name FROM flash_decks WHERE archived=0 AND {where}"),
            };
            select(conn, sql, "id", refs, true, |row| {
                let name = required(row, "name")?;
                out.push(plain(kind, id_text(row)?, name.clone(), String::new(), head(&name, 200).to_owned(), 0));
                Ok(())
            })
        }
        "study_task" => select(conn, "SELECT id,title,checklist_json FROM study_tasks WHERE {where}", "id", refs, true, |row| {
            let title = required(row, "title")?;
            let items = loads(row.get::<_, Option<String>>("checklist_json")?.as_deref());
            let parts: Vec<String> = match &items {
                Some(Py::List(items)) => items.iter().filter_map(|item| string(item.as_dict().and_then(|d| d.get_str("text")))).collect(),
                _ => Vec::new(),
            };
            out.push(plain("study_task", id_text(row)?, title.clone(), parts.join("\n"), head(&title, 200).to_owned(), 0));
            Ok(())
        }),
        "calendar_event" => select(conn, "SELECT id,title,data,source_key FROM calendar_events WHERE deleted=0 AND {where}", "id", refs, false, |row| {
            let title = required(row, "title")?;
            let data = loads(row.get::<_, Option<String>>("data")?.as_deref());
            let description = string(data.as_ref().and_then(Py::as_dict).and_then(|d| d.get_str("description"))).unwrap_or_default();
            let source_key = row.get::<_, Option<String>>("source_key")?.unwrap_or_default();
            let flags = if APPLYAI_SOURCES.contains(&source_key.split(':').next().unwrap_or("")) { APPLYAI } else { 0 };
            out.push(plain("calendar_event", id_text(row)?, title.clone(), description, head(&title, 200).to_owned(), flags));
            Ok(())
        }),
        "calendar_task" => select(conn, "SELECT id,data FROM calendar_tasks WHERE deleted=0 AND {where}", "id", refs, false, |row| {
            let data = loads(row.get::<_, Option<String>>("data")?.as_deref());
            if let Some(dict) = data.as_ref().and_then(Py::as_dict) {
                if let Some(title) = string(dict.get_str("title")) {
                    let description = string(dict.get_str("description")).unwrap_or_default();
                    out.push(plain("calendar_task", id_text(row)?, title.clone(), description, head(&title, 200).to_owned(), 0));
                }
            }
            Ok(())
        }),
        "flashcard_note" => select(conn, "SELECT n.id,n.deck_id,n.fields,t.fields AS names,d.name AS deck FROM flash_notes n
            JOIN flash_types t ON t.id=n.type_id JOIN flash_decks d ON d.id=n.deck_id
            WHERE n.archived=0 AND d.archived=0 AND {where}", "n.id", refs, true, |row| {
            let fields = loads(row.get::<_, Option<String>>("fields")?.as_deref());
            let Some(fields) = fields.as_ref().and_then(Py::as_dict) else { return Ok(()) };
            let names = loads(row.get::<_, Option<String>>("names")?.as_deref());
            let front = match &names {
                Some(Py::List(names)) => names.first().and_then(Py::as_str).cloned(),
                _ => None,
            };
            let title = match front.as_ref().and_then(|front| fields.get(front)) {
                Some(Py::Str(value)) => clean(&lossy(value)),
                _ => String::new(),
            };
            let rest: Vec<String> = fields.iter()
                .filter(|(key, _)| front.as_ref() != Some(*key))
                .filter_map(|(_, value)| value.as_str().map(|value| clean(&lossy(value))))
                .collect();
            let deck = required(row, "deck")?;
            let label = std::iter::once(&title).chain(rest.iter()).find(|part| !strip(part).is_empty())
                .map(|part| super::text::split(part).collect::<Vec<_>>().join(" "))
                .unwrap_or(deck);
            let deck_id: i64 = row.get("deck_id")?;
            out.push(Doc { page_id: Some(deck_id.to_string()), ..plain("flashcard_note", id_text(row)?, title, rest.join("\n"), head(&label, 200).to_owned(), 0) });
            Ok(())
        }),
        "job" => select(conn, "SELECT j.id,j.title,j.location,j.jd_text,c.display_name AS company FROM jobs j
            LEFT JOIN companies c ON c.id=j.company_id WHERE {where}", "j.id", refs, true, |row| {
            let title = required(row, "title")?;
            let company = row.get::<_, Option<String>>("company")?.unwrap_or_default();
            let location = row.get::<_, Option<String>>("location")?.unwrap_or_default();
            let description = clean(&row.get::<_, Option<String>>("jd_text")?.unwrap_or_default());
            let body = [company.as_str(), location.as_str(), description.as_str()].iter().filter(|part| !part.is_empty()).copied().collect::<Vec<_>>().join("\n");
            let label = if company.is_empty() { title.clone() } else { format!("{title} · {company}") };
            out.push(plain("job", id_text(row)?, title, body, head(&label, 200).to_owned(), 0));
            Ok(())
        }),
        _ => Ok(()),
    }
}

/// `search._projection_blocks` for the named blocks.
pub fn projection_blocks(conn: &Connection, ids: &[String], out: &mut Vec<Doc>) -> Result<()> {
    for chunk in ids.chunks(500) {
        let marks = vec!["?"; chunk.len()].join(",");
        let mut statement = conn.prepare(&format!("SELECT * FROM blocks WHERE id IN ({marks})"))?;
        let mut rows = statement.query(params_from_iter(chunk.iter()))?;
        while let Some(row) = rows.next()? {
            if let Some(doc) = block_doc(&BlockRow::read(row)?)? {
                out.push(doc);
            }
        }
    }
    Ok(())
}

const UPSERT: &str = "INSERT INTO search_docs(kind,ref,page_id,type,title,body,label,flags) VALUES (?,?,?,?,?,?,?,?) \
    ON CONFLICT(kind,ref) DO UPDATE SET page_id=excluded.page_id,type=excluded.type,title=excluded.title,\
    body=excluded.body,label=excluded.label,flags=excluded.flags WHERE page_id IS NOT excluded.page_id \
    OR type IS NOT excluded.type OR title IS NOT excluded.title OR body IS NOT excluded.body \
    OR label IS NOT excluded.label OR flags IS NOT excluded.flags";

/// `searchdocs.write`: delete `gone`, then upsert `docs`; unchanged rows are not touched.
pub fn write(conn: &Connection, docs: &[Doc], gone: &[(String, String)]) -> Result<()> {
    let mut delete = conn.prepare("DELETE FROM search_docs WHERE kind=? AND ref=?")?;
    for (kind, reference) in gone {
        delete.execute([kind, reference])?;
    }
    let mut upsert = conn.prepare(UPSERT)?;
    for doc in docs {
        upsert.execute(rusqlite::params![doc.kind, doc.reference, doc.page_id, doc.kind_of_block, doc.title, doc.body, doc.label, doc.flags])?;
    }
    Ok(())
}

/// `searchdocs.has_index`.
pub fn has_index(conn: &Connection) -> Result<bool> {
    Ok(conn.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_docs'", [], |_| Ok(())).optional()?.is_some())
}

/// `search._current`: the documents the named records should have now, in Python's order.
fn current(conn: &Connection, keys: &[(String, String)]) -> Result<Vec<Doc>> {
    let mut wanted: Vec<(String, Vec<String>)> = Vec::new();
    for (kind, reference) in keys {
        match wanted.iter_mut().find(|(seen, _)| seen == kind) {
            Some((_, refs)) => refs.push(reference.clone()),
            None => wanted.push((kind.clone(), vec![reference.clone()])),
        }
    }
    let mut found: Vec<Doc> = Vec::new();
    let mut at: HashMap<(&'static str, String), usize> = HashMap::new();
    for (kind, refs) in &wanted {
        let mut made = Vec::new();
        if kind == "block" {
            projection_blocks(conn, refs, &mut made)?;
        } else {
            source(conn, kind, Some(refs), &mut made)?;
        }
        // `found.update(...)`: a repeated identity keeps its first place and takes the last document.
        for doc in made {
            let key = (doc.kind, doc.reference.clone());
            match at.get(&key) {
                Some(&index) => found[index] = doc,
                None => {
                    at.insert(key, found.len());
                    found.push(doc);
                }
            }
        }
    }
    Ok(found)
}

/// `search.sync`: fold the queued source-table changes into the index; how many records were refreshed.
/// The caller has checked `has_index`; outside a transaction this takes the writer itself.
pub fn sync(conn: &Connection) -> Result<usize> {
    if conn.query_row("SELECT 1 FROM search_pending LIMIT 1", [], |_| Ok(())).optional()?.is_none() {
        return Ok(0);
    }
    let own = conn.is_autocommit();
    if own {
        conn.execute_batch("BEGIN IMMEDIATE")?;
    }
    let outcome = (|| {
        let mut statement = conn.prepare("SELECT kind,ref FROM search_pending")?;
        let pending: Vec<(String, String)> = statement.query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
        let docs = current(conn, &pending)?;
        let made: HashSet<(&str, &str)> = docs.iter().map(|doc| (doc.kind, doc.reference.as_str())).collect();
        let gone: Vec<(String, String)> = pending.iter()
            .filter(|(kind, reference)| !made.contains(&(kind.as_str(), reference.as_str())))
            .cloned().collect();
        write(conn, &docs, &gone)?;
        let mut delete = conn.prepare("DELETE FROM search_pending WHERE kind=? AND ref=?")?;
        for (kind, reference) in &pending {
            delete.execute([kind, reference])?;
        }
        Ok(pending.len())
    })();
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
