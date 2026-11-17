//! `kitty.core.rika.memory`'s index and preferences as the Memory page reads and edits them:
//! the FTS5 table `rika_index` rebuilt per source when its signature changed (the same SQL,
//! so the same rows in the same order), the ranked search with its snippets, and the editable
//! preferences with their limits. `memory.refresh` stays in Python too, for Rika's model
//! requests and its `search_memory` tool; both write the same rows.

use rusqlite::types::Value as Sql;
use rusqlite::{params, Connection};
use serde_json::{json, Value};

use super::now;
use crate::builds::value::{lossy, lower, text as code_points};
use crate::ported::{db, fail, internal, py, Done};
use crate::search::text::{is_alnum, split, strip};

const PREFERENCES_MAX: i64 = 40;
const PREFERENCES_CHARS: i64 = 4000;
const PREFERENCE_MAX: usize = 500;
const SEARCH_LIMIT: i64 = 20;
const QUERY_TERMS: usize = 12;

struct Source {
    name: &'static str,
    label: &'static str,
    /// The feature module the source belongs to, or None for the core.
    module: Option<&'static str>,
    /// The view a result opens, or None.
    view: Option<&'static str>,
    signature: &'static str,
    /// SELECT title, body, ref, at.
    rows: &'static str,
}

/// `memory.SOURCES`, in its order (`SOURCE_ROWS` is 5000).
static SOURCES: [Source; 9] = [
    Source { name: "page", label: "Pages", module: Some("workspace"), view: Some("notes"),
        signature: "SELECT count(*)||':'||ifnull(max(updated_at),'')||':'||ifnull(max(id),'') FROM note_page_index",
        rows: "SELECT p.title, ifnull((SELECT group_concat(o.text, ' ') FROM note_outline o WHERE o.page_id=p.id),''), p.id, p.updated_at FROM note_page_index p" },
    Source { name: "block", label: "Blocks", module: Some("workspace"), view: Some("notes"),
        signature: "SELECT count(*)||':'||total(length(text))||':'||ifnull(max(updated_at),'') FROM note_outline",
        rows: "SELECT title, text, id, updated_at FROM note_outline WHERE trim(text)<>''" },
    Source { name: "oplog", label: "Edit history", module: None, view: None,
        signature: "SELECT (SELECT count(*)||':'||ifnull(max(seq),'') FROM record_events)||'|'||(SELECT count(*)||':'||\
                    ifnull(max(created_at),'')||':'||ifnull(max(undone_at),'') FROM rika_actions)",
        rows: "SELECT * FROM (SELECT section||' '||entity||' '||action, ifnull(entity_id,'')||' '||route, 'record:'||seq, at FROM \
               record_events ORDER BY seq DESC LIMIT 5000) UNION ALL SELECT * FROM (SELECT summary, tool||' '||status, 'action:'||id, \
               created_at FROM rika_actions ORDER BY created_at DESC LIMIT 5000)" },
    Source { name: "job", label: "Jobs", module: Some("applyai"), view: Some("jobs"),
        signature: "SELECT count(*)||':'||ifnull(max(j.updated_at),'')||':'||ifnull(max(j.id),'')||':'||ifnull(max(c.updated_at),'') \
                    FROM jobs j JOIN companies c ON c.id=j.company_id",
        rows: "SELECT j.title, c.display_name||' '||ifnull(j.location,''), j.id, j.updated_at FROM jobs j JOIN companies c ON \
               c.id=j.company_id ORDER BY j.last_seen_at DESC LIMIT 5000" },
    Source { name: "event", label: "Calendar", module: Some("workspace"), view: Some("calendar"),
        signature: "SELECT count(*)||':'||ifnull(max(updated_at),'')||':'||ifnull(max(rowid),'') FROM calendar_events WHERE deleted=0",
        rows: "SELECT title, trim(ifnull(json_extract(data,'$.description'),'')||' '||ifnull(json_extract(data,'$.location'),'')), id, \
               start FROM calendar_events WHERE deleted=0" },
    Source { name: "task", label: "Tasks", module: Some("workspace"), view: Some("calendar"),
        signature: "SELECT count(*)||':'||ifnull(max(updated_at),'')||':'||ifnull(max(rowid),'') FROM calendar_tasks WHERE deleted=0",
        rows: "SELECT ifnull(json_extract(data,'$.title'),''), ifnull(json_extract(data,'$.description'),''), id, updated_at FROM \
               calendar_tasks WHERE deleted=0" },
    Source { name: "mail", label: "Email", module: None, view: None,
        signature: "SELECT count(*)||':'||ifnull(max(fetched_at),'')||':'||ifnull(max(rowid),'') FROM rika_mail_messages",
        rows: "SELECT subject, sender||' <'||sender_email||'> '||body, id, received_at FROM rika_mail_messages ORDER BY received_at \
               DESC LIMIT 5000" },
    Source { name: "goal", label: "Goals", module: None, view: Some("goals"),
        signature: "SELECT count(*)||':'||ifnull(max(updated_at),'')||':'||ifnull(max(id),'') FROM goals",
        rows: "SELECT title, why, id, updated_at FROM goals" },
    Source { name: "preference", label: "Preferences", module: None, view: Some("settings"),
        signature: "SELECT count(*)||':'||ifnull(max(updated_at),'')||':'||ifnull(max(id),'') FROM rika_preferences",
        rows: "SELECT text, '', id, updated_at FROM rika_preferences" },
];

/// `value or ""`.
fn or_blank(value: Sql) -> Sql {
    let blank = match &value {
        Sql::Null => true,
        Sql::Integer(number) => *number == 0,
        Sql::Real(number) => *number == 0.0,
        Sql::Text(text) => text.is_empty(),
        Sql::Blob(bytes) => bytes.is_empty(),
    };
    if blank { Sql::Text(String::new()) } else { value }
}

/// `str(ref)`.
fn as_str(value: Sql) -> Done<String> {
    Ok(match value {
        Sql::Null => "None".into(),
        Sql::Integer(number) => number.to_string(),
        Sql::Real(number) => py::float_repr(number),
        Sql::Text(text) => text,
        Sql::Blob(_) => return Err(internal()),
    })
}

/// `memory.refresh`: rebuild each source whose signature changed; sources of inactive modules empty.
fn refresh(conn: &Connection, modules: &[String]) -> Done<()> {
    let stored: Vec<(String, Option<String>)> = {
        let mut statement = conn.prepare("SELECT source,signature FROM rika_index_sources")?;
        let found = statement.query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?.collect::<Result<_, _>>()?;
        found
    };
    for source in &SOURCES {
        let active = source.module.is_none_or(|module| modules.iter().any(|on| on == module));
        let signature: Option<String> = if active { conn.query_row(source.signature, [], |row| row.get(0))? } else { Some("off".into()) };
        // `stored.get(name) == signature`: the last row of a source wins, as in the dict.
        let known = stored.iter().rev().find(|(name, _)| name == source.name).map(|(_, signature)| signature.clone());
        if known.as_ref() == Some(&signature) || (known.is_none() && signature.is_none()) {
            continue;
        }
        db::transaction(conn, true, || {
            conn.execute("DELETE FROM rika_index WHERE source=?", [source.name])?;
            let mut items: i64 = 0;
            if active {
                // `fetchall()` first, then the inserts.
                let found: Vec<(Sql, Sql, Sql, Sql)> = conn.prepare(source.rows)?
                    .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)))?.collect::<Result<_, _>>()?;
                let mut insert = conn.prepare("INSERT INTO rika_index(title,body,source,ref,at) VALUES(?,?,?,?,?)")?;
                for (title, body, reference, at) in found {
                    insert.execute(params![or_blank(title), or_blank(body), source.name, as_str(reference)?, or_blank(at)])?;
                    items += 1;
                }
            }
            conn.execute("INSERT INTO rika_index_sources(source,signature,items,refreshed_at) VALUES(?,?,?,?) ON CONFLICT(source) \
                          DO UPDATE SET signature=excluded.signature,items=excluded.items,refreshed_at=excluded.refreshed_at",
                         params![source.name, signature, items, now()])?;
            Ok(())
        })?;
    }
    Ok(())
}

/// `memory.rebuild`: drop and rebuild the whole index; answers `index`.
pub fn rebuild(conn: &Connection, modules: &[String]) -> Done<Value> {
    db::transaction(conn, true, || {
        conn.execute_batch("DELETE FROM rika_index; DELETE FROM rika_index_sources;")?;
        Ok(())
    })?;
    refresh(conn, modules)?;
    index(conn)
}

/// `memory.index`.
fn index(conn: &Connection) -> Done<Value> {
    let rows = db::rows(conn, "SELECT * FROM rika_index_sources", [])?;
    let mut total: i64 = 0;
    let mut sources = Vec::with_capacity(SOURCES.len());
    for source in &SOURCES {
        // `{row["source"]: row}`: the last row of a source wins.
        let row = rows.iter().rev().find(|row| row.get("source").and_then(Value::as_str) == Some(source.name));
        let items = row.map_or(Value::from(0), |row| row.get("items").cloned().unwrap_or(Value::Null));
        total = total.checked_add(items.as_i64().ok_or_else(internal)?).ok_or_else(internal)?;
        sources.push(json!({"source": source.name, "label": source.label, "items": items,
                            "refreshed_at": row.map_or(Value::Null, |row| row.get("refreshed_at").cloned().unwrap_or(Value::Null))}));
    }
    Ok(json!({"sources": sources, "total": total}))
}

/// `_terms`: the distinct `\w+` runs of `text.lower()` (`str.isalnum()` or `_`), the first twelve.
fn terms(text: &str) -> Vec<String> {
    let lowered = lossy(&lower(&code_points(text)));
    let mut seen: Vec<String> = Vec::new();
    for term in lowered.split(|c: char| c != '_' && !is_alnum(c)).filter(|term| !term.is_empty()) {
        if !seen.iter().any(|known| known == term) {
            seen.push(term.to_owned());
        }
    }
    seen.truncate(QUERY_TERMS);
    seen
}

/// `_flat`.
fn flat(text: &str) -> String {
    split(text).collect::<Vec<_>>().join(" ")
}

/// `memory.search(conn, query)`: every word as a prefix, best first.
fn search(conn: &Connection, query: &str) -> Done<Vec<Value>> {
    let terms = terms(query);
    if terms.is_empty() {
        return Ok(Vec::new());
    }
    let expression = terms.iter().map(|term| format!("\"{term}\"*")).collect::<Vec<_>>().join(" ");
    let rows = db::rows(conn, "SELECT source,ref,title,snippet(rika_index,-1,'','','…',16) AS snippet FROM rika_index \
                               WHERE rika_index MATCH ? ORDER BY rank LIMIT ?", params![expression, SEARCH_LIMIT])?;
    rows.into_iter().map(|row| {
        let source = row.get("source").cloned().unwrap_or(Value::Null);
        let view = source.as_str().and_then(|name| SOURCES.iter().find(|known| known.name == name)).and_then(|known| known.view);
        let snippet = row.get("snippet").and_then(Value::as_str).ok_or_else(internal)?;
        Ok(json!({"source": source, "ref": row.get("ref").cloned().unwrap_or(Value::Null),
                  "title": row.get("title").cloned().unwrap_or(Value::Null), "snippet": flat(snippet), "view": view}))
    }).collect()
}

/// `workspace_tools._allow_ai`.
fn allow_ai(conn: &Connection) -> Done<bool> {
    let value: Sql = conn.query_row("SELECT allow_ai FROM note_preferences WHERE id=1", [], |row| row.get(0))?;
    Ok(match value {
        Sql::Null => false,
        Sql::Integer(number) => number != 0,
        Sql::Real(number) => number != 0.0,
        Sql::Text(text) => !text.is_empty(),
        Sql::Blob(bytes) => !bytes.is_empty(),
    })
}

/// `GET /api/rika/memory[?q=]`: with a query the index is refreshed first.
pub fn overview(conn: &Connection, modules: &[String], query: Option<&str>) -> Done<Value> {
    let mut results = Vec::new();
    if let Some(query) = query.filter(|query| !strip(query).is_empty()) {
        refresh(conn, modules)?;
        results = search(conn, query)?;
    }
    Ok(json!({"preferences": preferences(conn)?, "index": index(conn)?, "results": results, "notes_shared": allow_ai(conn)?}))
}

// Preferences ------------------------------------------------------------------

fn shape(row: &serde_json::Map<String, Value>) -> Value {
    let field = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
    json!({"id": field("id"), "text": field("text"), "source": field("source"), "created_at": field("created_at"),
           "updated_at": field("updated_at")})
}

fn preferences(conn: &Connection) -> Done<Vec<Value>> {
    Ok(db::rows(conn, "SELECT * FROM rika_preferences ORDER BY id", [])?.iter().map(shape).collect())
}

fn preference(conn: &Connection, id: i64) -> Done<Value> {
    db::row(conn, "SELECT * FROM rika_preferences WHERE id=?", [id])?.as_ref().map(shape).ok_or_else(|| fail(404, "Preference not found"))
}

/// `_text`: 1–500 characters once stripped, kept with its white space collapsed.
fn text(value: &Value) -> Done<String> {
    match value.as_str() {
        Some(text) if !text.contains('\0') && (1..=PREFERENCE_MAX).contains(&strip(text).chars().count()) => Ok(flat(text)),
        _ => Err(fail(400, format!("A preference is 1–{PREFERENCE_MAX} characters"))),
    }
}

/// `_room`.
fn room(conn: &Connection, text: &str, replacing: Option<i64>) -> Done<()> {
    let (count, chars): (i64, f64) = conn.query_row(
        "SELECT count(*), total(length(text)) FROM rika_preferences WHERE id IS NOT ?", [replacing], |row| Ok((row.get(0)?, row.get(1)?)))?;
    if replacing.is_none() && count >= PREFERENCES_MAX {
        return Err(fail(400, format!("Rika keeps at most {PREFERENCES_MAX} preferences; delete one first")));
    }
    if chars + text.chars().count() as f64 > PREFERENCES_CHARS as f64 {
        return Err(fail(400, format!("Preferences hold at most {PREFERENCES_CHARS} characters in all; shorten or delete one first")));
    }
    Ok(())
}

/// `memory.add_preference(conn, text, source="added")`.
pub fn add_preference(conn: &Connection, value: &Value) -> Done<Value> {
    let (text, stamp) = (text(value)?, now());
    let id = db::transaction(conn, true, || {
        room(conn, &text, None)?;
        conn.execute("INSERT INTO rika_preferences(text,source,message_id,created_at,updated_at) VALUES(?,'added',NULL,?,?)",
                     params![text, stamp, stamp])?;
        Ok(conn.last_insert_rowid())
    })?;
    preference(conn, id)
}

pub fn update_preference(conn: &Connection, id: i64, value: &Value) -> Done<Value> {
    let text = text(value)?;
    db::transaction(conn, true, || {
        preference(conn, id)?;
        room(conn, &text, Some(id))?;
        conn.execute("UPDATE rika_preferences SET text=?,updated_at=? WHERE id=?", params![text, now(), id])?;
        Ok(())
    })?;
    preference(conn, id)
}

pub fn delete_preference(conn: &Connection, id: i64) -> Done<i64> {
    db::transaction(conn, false, || {
        if conn.execute("DELETE FROM rika_preferences WHERE id=?", [id])? == 0 {
            return Err(fail(404, "Preference not found"));
        }
        Ok(id)
    })
}

#[cfg(test)]
mod tests {
    use super::terms;

    #[test]
    fn terms_are_python_word_runs_of_the_lowered_text() {
        assert_eq!(terms("Enzymes lower ENZYMES"), ["enzymes", "lower"]);
        // U+0130 lowers to i + a combining dot, which is no word character; `_` and digits are.
        assert_eq!(terms("İstanbul snake_case x²"), ["i", "stanbul", "snake_case", "x²"]);
        assert_eq!(terms("ΣΑΣ"), ["σας"]);
        assert_eq!(terms(&(0..20).map(|n| format!("w{n}")).collect::<Vec<_>>().join(" ")).len(), 12);
    }
}
