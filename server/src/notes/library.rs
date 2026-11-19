//! The library and note pages (`kitty.core.notes`): folders and notebooks are rows, a page is
//! a `page` root block with its integer `number`, a `canvas` child holding the drawing and
//! text-flow outline blocks; a page's mind map is a `schema_maps` row linked in
//! `note_page_maps` (`kitty.core.concept_maps`).

use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};

use super::canvas::{self, canvas_block_id, canvas_ops, canvas_specs, empty_document, insert_ops, normalize, page_document, viewport};
use super::{bad, body_keys, checked, integer, text, within, MAX_INTEGER, MAX_PAGES};
use crate::blocks::actor::Actor;
use crate::blocks::doc::{is_outline, Block, PageDoc};
use crate::blocks::store;
use crate::ported::{db, fail, internal, py, time, Done};
use crate::search::{docs, query};

type Row = Map<String, Value>;

/// `kitty.core.notes.now()` (the test clock in tests).
pub fn now() -> String {
    time::now_iso()
}

/// Search index failures as Python's: SQLite's own, anything else a 500.
pub(crate) fn searched<T>(result: docs::Result<T>) -> Done<T> {
    result.map_err(|error| match error {
        docs::Failure::Database(error) => error.into(),
        docs::Failure::Data(_) => internal(),
    })
}

/// `notes.apply`: one block transaction as the person at this device.
pub(crate) fn apply(conn: &Connection, ops: Vec<Value>, base: Option<Value>) -> Done<Map<String, Value>> {
    store::transact(conn, &Value::Array(ops), base.as_ref(), None, false, &Actor::user())
}

/// `notes.root_block`: the live root block of `kind` with this number, or a 404.
pub(crate) fn root_block(conn: &Connection, kind: &str, number: i64, missing: &str) -> Done<Row> {
    let number = checked(number)?;
    db::row(conn, "SELECT * FROM blocks WHERE type=? AND parent_id IS NULL AND deleted_at IS NULL AND json_extract(properties,'$.number')=?",
            params![kind, number])?.ok_or_else(|| fail(404, missing))
}

/// `notes.next_number`.
pub(crate) fn next_number(conn: &Connection, kind: &str) -> Done<Value> {
    let mut statement = conn.prepare("SELECT COALESCE(MAX(json_extract(properties,'$.number')),0)+1 FROM blocks \
                                      WHERE type=? AND parent_id IS NULL AND json_extract(properties,'$.number') IS NOT NULL")?;
    let mut rows = statement.query([kind])?;
    db::column(rows.next()?.ok_or_else(internal)?.get_ref(0)?)
}

fn string<'a>(row: &'a Row, key: &str) -> Done<&'a str> {
    row.get(key).and_then(Value::as_str).ok_or_else(internal)
}

pub(crate) fn properties(row: &Row) -> Done<Row> {
    serde_json::from_str(string(row, "properties")?).map_err(|_| internal())
}

/// `notes.page_doc`.
pub(crate) fn page_doc(conn: &Connection, number: i64) -> Done<PageDoc> {
    let root = root_block(conn, "page", number, "Page not found")?;
    store::load_page(conn, string(&root, "id")?)
}

pub(crate) fn root(doc: &PageDoc) -> Done<&Block> {
    doc.root().ok_or_else(internal)
}

/// `doc.preorder(page, types=OUTLINE_TYPES)`: live outline blocks in reading order.
pub(crate) fn outline_blocks(doc: &PageDoc) -> Vec<&Block> {
    fn walk<'a>(doc: &'a PageDoc, parent: &str, out: &mut Vec<&'a Block>) {
        for child in doc.children(Some(parent), false) {
            if is_outline(&child.kind) {
                out.push(child);
                walk(doc, &child.id, out);
            }
        }
    }
    let mut out = Vec::new();
    walk(doc, &doc.page_id, &mut out);
    out
}

/// `notes.page_from_doc`.
pub(crate) fn page_from_doc(doc: &PageDoc) -> Done<Value> {
    let root = root(doc)?;
    let document = page_document(doc)?;
    let props = &root.properties;
    let mut search: Vec<String> = Vec::new();
    let elements = match document.as_object().ok_or_else(internal)?.get("elements") {
        None | Some(Value::String(_) | Value::Object(_)) => &[][..],
        Some(Value::Array(items)) => items.as_slice(),
        Some(_) => return Err(internal()),
    };
    for item in elements {
        if let Value::Object(item) = item {
            match item.get("text") {
                None => search.push(String::new()),
                Some(Value::String(text)) => search.push(text.clone()),
                Some(_) => return Err(internal()),
            }
        }
    }
    search.extend(outline_blocks(doc).into_iter().map(|item| item.text.clone()));
    let or = |key: &str, default: Value| props.get(key).cloned().unwrap_or(default);
    Ok(json!({
        "id": props.get("number").cloned().ok_or_else(internal)?, "notebook_id": or("notebook_id", Value::Null), "title": root.text,
        "document": document, "search_text": search.join("\n"), "viewport": or("viewport", json!({"x": 0, "y": 0, "zoom": 1})),
        "revision": or("revision", json!(1)), "viewport_revision": or("viewport_revision", json!(1)), "created_at": root.created_at,
        "updated_at": root.updated_at, "favorite": or("favorite", json!(0)), "last_opened": or("last_opened", Value::Null),
        "position": or("position", json!(0)),
    }))
}

/// `notes.page`.
pub fn page(conn: &Connection, number: i64) -> Done<Value> {
    page_from_doc(&page_doc(conn, number)?)
}

fn page_count(conn: &Connection) -> Done<i64> {
    Ok(conn.query_row("SELECT COUNT(*) FROM note_page_index", [], |row| row.get(0))?)
}

const MAX_LISTED: usize = 500;

/// `notes.listing`: folders, notebooks and up to 500 pages (`truncated` when there are more).
pub fn listing(conn: &Connection, search: &str) -> Done<Value> {
    let search = text(&Value::String(search.to_owned()), 300, "Text")?.to_owned();
    let mut params: Vec<Value> = Vec::new();
    let mut filter = "1";
    if !py::strip(&search).is_empty() {
        let pattern = format!("%{}%", search.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_"));
        filter = "p.block_id IN (SELECT value FROM json_each(?)) OR n.title LIKE ? ESCAPE '\\' \
                  OR n.subject LIKE ? ESCAPE '\\' OR n.project LIKE ? ESCAPE '\\'";
        let mut matches: Vec<String> = searched(query::page_matches(conn, &search))?.into_iter().collect();
        matches.sort();
        params = vec![serde_json::to_string(&matches).map_err(|_| internal())?.into(), pattern.clone().into(), pattern.clone().into(), pattern.into()];
    }
    params.push(((MAX_LISTED + 1) as i64).into());
    let bound = params.iter().map(db::bind).collect::<Done<Vec<_>>>()?;
    let rows = db::rows(conn, &format!("SELECT p.id,p.notebook_id,p.title,p.revision,p.updated_at,p.favorite,p.last_opened,p.position FROM note_page_index p \
                                        JOIN note_notebooks n ON p.notebook_id=n.id WHERE {filter} \
                                        ORDER BY p.position,p.updated_at DESC,p.id LIMIT ?"), rusqlite::params_from_iter(bound))?;
    let mut prefs = db::row(conn, "SELECT allow_ai,retention_days,revision FROM note_preferences WHERE id=1", [])?.ok_or_else(internal)?;
    let allow = prefs.get("allow_ai").is_some_and(py::truthy);
    prefs.insert("allow_ai".into(), allow.into());
    let truncated = rows.len() > MAX_LISTED;
    Ok(json!({
        "folders": db::rows(conn, "SELECT * FROM note_folders ORDER BY position,title,id", [])?,
        "notebooks": db::rows(conn, "SELECT * FROM note_notebooks ORDER BY position,updated_at DESC LIMIT 200", [])?,
        "pages": rows.into_iter().take(MAX_LISTED).collect::<Vec<_>>(), "truncated": truncated, "preferences": prefs,
    }))
}

// Folders and notebooks ---------------------------------------------------------------------

/// `notes._folder_id`.
pub(crate) fn folder_id(conn: &Connection, value: &Value) -> Done<Option<i64>> {
    if value.is_null() {
        return Ok(None);
    }
    let fid = integer(value, 1, MAX_INTEGER)?;
    if !db::exists(conn, "SELECT 1 FROM note_folders WHERE id=?", [fid])? {
        return Err(fail(404, "Folder not found"));
    }
    Ok(Some(fid))
}

fn folder_row(conn: &Connection, fid: i64) -> Done<Value> {
    Ok(Value::Object(db::row(conn, "SELECT * FROM note_folders WHERE id=?", [fid])?.ok_or_else(internal)?))
}

pub fn create_folder(conn: &Connection, title: &Value, parent_id: &Value) -> Done<Value> {
    let title = text(title, 200, "Text")?;
    if py::strip(title).is_empty() {
        return Err(bad("Folder title is required"));
    }
    let fid = within(conn, false, || {
        let parent = folder_id(conn, parent_id)?;
        let stamp = now();
        conn.execute("INSERT INTO note_folders(title,parent_id,created_at,updated_at) VALUES (?,?,?,?)", params![title, parent, stamp, stamp])?;
        Ok(conn.last_insert_rowid())
    })?;
    folder_row(conn, fid)
}

pub fn save_folder(conn: &Connection, number: i64, revision: &Value, title: &Value, parent_id: &Value) -> Done<Value> {
    let fid = checked(number)?;
    let rev = integer(revision, 1, MAX_INTEGER)?;
    let title = text(title, 200, "Text")?;
    if py::strip(title).is_empty() {
        return Err(bad("Folder title is required"));
    }
    within(conn, false, || {
        let current: Option<i64> = db::row(conn, "SELECT revision FROM note_folders WHERE id=?", [fid])?.and_then(|row| row["revision"].as_i64());
        let Some(current) = current else { return Err(fail(404, "Folder not found")) };
        if current != rev {
            return Err(fail(409, "Folder changed in another tab; reload before editing"));
        }
        let parent = folder_id(conn, parent_id)?;
        let mut ancestor = parent;
        while let Some(at) = ancestor {
            if at == fid {
                return Err(bad("A folder cannot contain itself or move into its descendants"));
            }
            ancestor = db::row(conn, "SELECT parent_id FROM note_folders WHERE id=?", [at])?.ok_or_else(internal)?["parent_id"].as_i64();
        }
        conn.execute("UPDATE note_folders SET title=?,parent_id=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?",
                     params![title, parent, now(), fid, rev])?;
        Ok(())
    })?;
    folder_row(conn, fid)
}

pub fn delete_folder(conn: &Connection, number: i64, revision: &Value) -> Done<Value> {
    let fid = checked(number)?;
    let rev = integer(revision, 1, MAX_INTEGER)?;
    within(conn, false, || {
        let current: Option<i64> = db::row(conn, "SELECT revision FROM note_folders WHERE id=?", [fid])?.and_then(|row| row["revision"].as_i64());
        let Some(current) = current else { return Err(fail(404, "Folder not found")) };
        if current != rev {
            return Err(fail(409, "Folder changed; reload before deleting"));
        }
        if db::exists(conn, "SELECT 1 FROM note_folders WHERE parent_id=? UNION ALL SELECT 1 FROM note_notebooks WHERE folder_id=? LIMIT 1", [fid, fid])? {
            return Err(fail(409, "Only empty folders can be deleted"));
        }
        conn.execute("DELETE FROM note_folders WHERE id=? AND revision=?", [fid, rev])?;
        Ok(())
    })?;
    Ok(json!({"ok": true}))
}

fn notebook_row(conn: &Connection, nid: i64) -> Done<Value> {
    Ok(Value::Object(db::row(conn, "SELECT * FROM note_notebooks WHERE id=?", [nid])?.ok_or_else(internal)?))
}

/// `notes.create_notebook`.
pub fn create_notebook(conn: &Connection, title: &Value, subject: &Value, project: &Value, folder: &Value) -> Done<Value> {
    let title = text(title, 200, "Text")?;
    let subject = text(subject, 200, "Text")?;
    let project = text(project, 200, "Text")?;
    if py::strip(title).is_empty() {
        return Err(bad("Notebook title is required"));
    }
    let nid = within(conn, false, || {
        let folder = folder_id(conn, folder)?;
        if conn.query_row("SELECT COUNT(*) FROM note_notebooks", [], |row| row.get::<_, i64>(0))? >= 200 {
            return Err(bad("Notebook limit reached"));
        }
        let stamp = now();
        conn.execute("INSERT INTO note_notebooks(title,subject,project,folder_id,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     params![title, subject, project, folder, stamp, stamp])?;
        Ok(conn.last_insert_rowid())
    })?;
    notebook_row(conn, nid)
}

pub fn save_notebook(conn: &Connection, number: i64, revision: &Value, title: &Value, subject: &Value, project: &Value,
                     folder: Option<&Value>) -> Done<Value> {
    let title = text(title, 200, "Text")?;
    let subject = text(subject, 200, "Text")?;
    let project = text(project, 200, "Text")?;
    let stamp = now();
    let nid = checked(number)?;
    let rev = integer(revision, 1, MAX_INTEGER)?;
    if py::strip(title).is_empty() {
        return Err(bad("Notebook title is required"));
    }
    within(conn, false, || {
        let changed = match folder {
            None => conn.execute("UPDATE note_notebooks SET title=?,subject=?,project=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?",
                                 params![title, subject, project, stamp, nid, rev])?,
            Some(folder) => {
                let folder = folder_id(conn, folder)?;
                conn.execute("UPDATE note_notebooks SET folder_id=?,title=?,subject=?,project=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?",
                             params![folder, title, subject, project, stamp, nid, rev])?
            }
        };
        if changed != 1 {
            return Err(fail(409, "Notebook changed in another tab; reload before editing"));
        }
        Ok(())
    })?;
    notebook_row(conn, nid)
}

// Pages -------------------------------------------------------------------------------------

/// `notes.new_page_ops`: the page's number and the transaction creating it (revision 1).
pub(crate) fn new_page_ops(conn: &Connection, notebook_id: i64, title: &str, document: Option<&Map<String, Value>>, view: Option<Value>,
                           extra: Option<Map<String, Value>>) -> Done<(Value, Vec<Value>)> {
    let page_id = store::uuid4()?;
    let number = next_number(conn, "page")?;
    let empty = empty_document();
    let document = document.unwrap_or(&empty);
    let Value::Object(mut props) = json!({"number": number, "notebook_id": notebook_id, "revision": 1,
        "viewport": view.unwrap_or_else(|| json!({"x": 0, "y": 0, "zoom": 1})), "viewport_revision": 1, "favorite": 0, "position": 0}) else { unreachable!() };
    props.extend(extra.unwrap_or_default());
    let mut ops = vec![
        json!({"op": "insert", "id": page_id, "type": "page", "parent": null, "props": props, "text": title}),
        json!({"op": "insert", "id": canvas_block_id(&page_id), "type": "canvas", "parent": page_id, "props": canvas::settings(document)}),
    ];
    ops.extend(insert_ops(canvas_specs(&page_id, document)?)?);
    Ok((number, ops))
}

fn number_of(value: &Value) -> Done<i64> {
    value.as_i64().ok_or_else(internal)
}

/// `notes.create_page`.
pub fn create_page(conn: &Connection, notebook_id: &Value, title: &Value, extra: Option<Map<String, Value>>) -> Done<Value> {
    let nid = integer(notebook_id, 1, MAX_INTEGER)?;
    let title = text(title, 200, "Text")?;
    if py::strip(title).is_empty() {
        return Err(bad("Page title is required"));
    }
    let number = within(conn, true, || {
        if !db::exists(conn, "SELECT 1 FROM note_notebooks WHERE id=?", [nid])? {
            return Err(fail(404, "Notebook not found"));
        }
        if page_count(conn)? >= MAX_PAGES {
            return Err(bad("Page limit reached"));
        }
        let (number, ops) = new_page_ops(conn, nid, title, None, None, extra)?;
        apply(conn, ops, None)?;
        number_of(&number)
    })?;
    page(conn, number)
}

fn revision_base(page_id: &str, revision: i64) -> Value {
    let mut base = Map::new();
    base.insert(page_id.to_owned(), revision.into());
    Value::Object(base)
}

pub fn save_page(conn: &Connection, number: i64, revision: &Value, document: &Value, title: &Value) -> Done<Value> {
    let title = text(title, 200, "Text")?;
    let rev = integer(revision, 1, MAX_INTEGER)?;
    if py::strip(title).is_empty() {
        return Err(bad("Page title is required"));
    }
    within(conn, true, || {
        let normalized = normalize(document, Some(conn))?;
        let doc = page_doc(conn, number)?;
        let root = root(&doc)?;
        if !root.properties.get("revision").is_some_and(|value| py::eq_int(value, rev)) {
            return Err(fail(409, "Page changed in another tab. Export your local copy, then reload; stale edits were not saved."));
        }
        let mut ops = canvas_ops(&doc, &normalized)?;
        if root.text != title {
            ops.push(json!({"op": "text", "id": root.id, "text": title}));
        }
        ops.push(json!({"op": "set", "id": root.id, "props": {"revision": rev + 1}}));
        apply(conn, ops, Some(revision_base(&root.id, rev)))
    })?;
    Ok(json!({"revision": rev + 1}))
}

pub fn save_viewport(conn: &Connection, number: i64, revision: &Value, value: &Value) -> Done<Value> {
    let view = viewport(value)?;
    let rev = integer(revision, 1, MAX_INTEGER)?;
    within(conn, true, || {
        let doc = page_doc(conn, number)?;
        let root = root(&doc)?;
        let one = json!(1);
        if !py::eq_int(root.properties.get("viewport_revision").unwrap_or(&one), rev) {
            return Err(fail(409, "Viewport changed in another tab; reload the page"));
        }
        apply(conn, vec![json!({"op": "set", "id": root.id, "props": {"viewport": view, "viewport_revision": rev + 1}})], None)
    })?;
    Ok(json!({"viewport_revision": rev + 1}))
}

pub fn delete_page(conn: &Connection, number: i64, revision: &Value) -> Done<Value> {
    within(conn, true, || {
        let row = root_block(conn, "page", number, "Page changed; reload before deleting")?;
        let stored = properties(&row)?.get("revision").cloned().unwrap_or(Value::Null);
        if !py::eq_int(&stored, integer(revision, 1, MAX_INTEGER)?) {
            return Err(fail(409, "Page changed; reload before deleting"));
        }
        apply(conn, vec![json!({"op": "delete", "id": row["id"]})], None)
    })?;
    Ok(json!({"ok": true}))
}

/// `notes.preferences`: the build file's `settings.notes` (Q8), projected into note_preferences.
pub fn preferences(conn: &Connection, revision: &Value, allow_ai: &Value, retention_days: &Value) -> Done<Value> {
    let Value::Bool(allow) = allow_ai else { return Err(bad("AI sharing preference must be boolean")) };
    let rev = integer(revision, 1, MAX_INTEGER)?;
    let days = integer(retention_days, 0, 3650)?;
    use crate::builds::value::{Int, Value as Build};
    crate::builds::edit_settings(&["settings", "notes"], vec![("allow_ai", Build::Bool(*allow)), ("attachment_retention_days", Build::Int(Int::Small(days.into())))],
        |tx| {
            let current: i64 = tx.query_row("SELECT revision FROM note_preferences WHERE id=1", [], |row| row.get(0)).map_err(|_| (500, internal().message))?;
            if current != rev { Err((409, "Privacy settings changed; reload first".into())) } else { Ok(()) }
        }).map_err(|(status, message)| fail(status, message))?;
    let prefs = db::row(conn, "SELECT allow_ai, retention_days, revision FROM note_preferences WHERE id=1", [])?.ok_or_else(internal)?;
    Ok(json!({"allow_ai": prefs.get("allow_ai").is_some_and(py::truthy), "retention_days": prefs["retention_days"], "revision": prefs["revision"]}))
}

/// `notes.resolve_page`: the page with this title (any case), created when missing.
pub fn resolve_page(conn: &Connection, title: &Value, notebook_id: &Value) -> Done<Value> {
    let title = py::strip(text(title, 200, "Text")?).to_owned();
    if title.is_empty() {
        return Err(bad("Page title is required"));
    }
    within(conn, true, || {
        if let Some(row) = db::row(conn, "SELECT id FROM note_page_index WHERE title=? COLLATE NOCASE ORDER BY id LIMIT 1", [&title])? {
            return page(conn, number_of(&row["id"])?);
        }
        let notebook = if notebook_id.is_null() {
            match db::row(conn, "SELECT id FROM note_notebooks ORDER BY id LIMIT 1", [])? {
                Some(book) => book["id"].clone(),
                None => create_notebook(conn, &json!("Notes"), &json!(""), &json!(""), &Value::Null)?["id"].clone(),
            }
        } else {
            notebook_id.clone()
        };
        create_page(conn, &notebook, &Value::String(title.clone()), None)
    })
}

/// Today's date where this computer is (`datetime.now().date()`), on the test clock in tests.
fn local_today() -> Done<String> {
    let micros = time::Dt::now_utc().utc();
    let stamp = jiff::Timestamp::from_microsecond(micros).map_err(|_| internal())?;
    let date = stamp.to_zoned(jiff::tz::TimeZone::system()).date();
    Ok(format!("{:04}-{:02}-{:02}", date.year(), date.month(), date.day()))
}

/// `re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)`, then `datetime.strptime(date, "%Y-%m-%d")`:
/// None for the first refusal, Some(false) for the second.
fn daily_date(date: &str) -> Option<bool> {
    use crate::search::text::{decimal_value, is_decimal};
    let c: Vec<char> = date.chars().collect();
    let shape = c.len() == 10 && c[4] == '-' && c[7] == '-' && [0, 1, 2, 3, 5, 6, 8, 9].iter().all(|&i| is_decimal(c[i]));
    if !shape {
        return None;
    }
    let year = decimal_value(&c[..4].iter().collect::<String>())?;
    let (m, d) = ((c[5], c[6]), (c[8], c[9]));
    let month = match m {
        ('1', '0'..='2') | ('0', '1'..='9') => (m.0 as u32 - 48) * 10 + (m.1 as u32 - 48),
        _ => return Some(false),
    };
    let day = match d {
        ('3', '0' | '1') | ('0', '1'..='9') => (d.0 as u32 - 48) * 10 + (d.1 as u32 - 48),
        ('1' | '2', second) if is_decimal(second) => (d.0 as u32 - 48) * 10 + decimal_value(&second.to_string())? as u32,
        _ => return Some(false),
    };
    Some((1..=9999).contains(&year) && day <= time::days_in_month(year as i64, month))
}

/// `notes.daily_page`: the day's page in the "Daily notes" notebook, created when missing.
pub fn daily_page(conn: &Connection, date: &Value) -> Done<Value> {
    let date = if date.is_null() { Value::String(local_today()?) } else { date.clone() };
    let Value::String(day) = &date else { return Err(bad("Date must be YYYY-MM-DD")) };
    match daily_date(day) {
        None => return Err(bad("Date must be YYYY-MM-DD")),
        Some(false) => return Err(bad("Invalid daily note date")),
        Some(true) => {}
    }
    within(conn, true, || {
        let found = db::row(conn, "SELECT json_extract(properties,'$.number') AS id FROM blocks WHERE type='page' AND parent_id IS NULL \
                                   AND deleted_at IS NULL AND json_extract(properties,'$.daily')=? ORDER BY 1 LIMIT 1", [day])?;
        if let Some(row) = found {
            return page(conn, number_of(&row["id"])?);
        }
        let notebook = match db::row(conn, "SELECT id FROM note_notebooks WHERE title='Daily notes' ORDER BY id LIMIT 1", [])? {
            Some(book) => book["id"].clone(),
            None => create_notebook(conn, &json!("Daily notes"), &json!(""), &json!(""), &Value::Null)?["id"].clone(),
        };
        let mut extra = Map::new();
        extra.insert("daily".into(), date.clone());
        create_page(conn, &notebook, &date, Some(extra))
    })
}

/// `notes.organize_page`: favourite and last visit (view state, not edits).
pub fn organize_page(conn: &Connection, number: i64, body: &Map<String, Value>) -> Done<Value> {
    body_keys(body, &["favorite", "visited"], &[])?;
    if body.values().any(|value| !value.is_boolean()) {
        return Err(bad("Page flags must be boolean"));
    }
    within(conn, true, || {
        let row = root_block(conn, "page", number, "Page not found")?;
        let mut changes = Map::new();
        if let Some(Value::Bool(favorite)) = body.get("favorite") {
            changes.insert("favorite".into(), i64::from(*favorite).into());
        }
        if body.get("visited") == Some(&Value::Bool(true)) {
            changes.insert("last_opened".into(), now().into());
        }
        if !changes.is_empty() {
            apply(conn, vec![json!({"op": "set", "id": row["id"], "props": changes})], None)?;
        }
        Ok(())
    })?;
    page(conn, number)
}

/// `notes.reorder`: page positions are view state on page blocks; notebooks and folders are rows.
pub fn reorder(conn: &Connection, kind: &Value, ids: &Value) -> Done<Value> {
    let kind = kind.as_str().filter(|kind| matches!(*kind, "pages" | "notebooks" | "folders"));
    let (Some(kind), Some(ids)) = (kind, ids.as_array().filter(|ids| ids.len() <= 500)) else {
        return Err(bad("Invalid note order"));
    };
    let ids = ids.iter().map(|value| integer(value, 1, MAX_INTEGER)).collect::<Done<Vec<i64>>>()?;
    let unique: std::collections::HashSet<i64> = ids.iter().copied().collect();
    if unique.len() != ids.len() {
        return Err(bad("Duplicate ordered item"));
    }
    let missing = || fail(404, "Ordered item not found");
    within(conn, true, || {
        if kind == "pages" {
            let mut ops = Vec::new();
            for (position, item) in ids.iter().enumerate() {
                let row = db::row(conn, "SELECT block_id FROM note_page_index WHERE id=?", [item])?.ok_or_else(missing)?;
                ops.push(json!({"op": "set", "id": row["block_id"], "props": {"position": position}}));
            }
            if !ops.is_empty() {
                apply(conn, ops, None)?;
            }
            return Ok(());
        }
        let table = if kind == "notebooks" { "note_notebooks" } else { "note_folders" };
        for (position, item) in ids.iter().enumerate() {
            if conn.execute(&format!("UPDATE {table} SET position=? WHERE id=?"), params![position as i64, item])? != 1 {
                return Err(missing());
            }
        }
        Ok(())
    })?;
    Ok(json!({"ok": true}))
}

/// `notes.tags_summary`: tagged canvas cards of the 500 newest pages, then tagged outline blocks.
pub fn tags_summary(conn: &Connection) -> Done<Value> {
    let mut result = Vec::new();
    let cards = db::rows(conn, "SELECT p.id,p.title,b.text,b.properties FROM note_page_index p JOIN blocks c ON c.parent_id=p.block_id AND c.type='canvas' \
                                JOIN blocks b ON b.parent_id=c.id AND b.type IN ('text_box','sticky') AND b.deleted_at IS NULL \
                                WHERE p.block_id IN (SELECT block_id FROM note_page_index ORDER BY updated_at DESC LIMIT 500) \
                                AND json_extract(b.properties,'$.tags') IS NOT NULL ORDER BY p.updated_at DESC,p.id,b.order_key,b.id", [])?;
    for row in cards {
        let item = properties(&row)?;
        if item.get("tags").is_some_and(py::truthy) {
            result.push(json!({"page_id": row["id"], "title": row["title"], "element_id": item.get("element_id").cloned().ok_or_else(internal)?,
                               "text": row["text"], "tags": item["tags"], "checked": item.get("checked").cloned().unwrap_or(Value::Bool(false))}));
        }
    }
    let blocks = db::rows(conn, "SELECT o.id,o.page_id,o.text,o.title FROM note_outline o WHERE EXISTS(SELECT 1 FROM block_topics t WHERE t.block_id=o.id AND t.kind='tag') \
                                 ORDER BY o.updated_at DESC,o.path,o.id LIMIT 2000", [])?;
    let mut statement = conn.prepare("SELECT topic FROM block_topics WHERE block_id=? AND kind='tag' ORDER BY topic")?;
    for row in blocks {
        let tags: Vec<String> = statement.query_map([string(&row, "id")?], |found| found.get(0))?.collect::<rusqlite::Result<_>>()?;
        result.push(json!({"page_id": row["page_id"], "title": row["title"], "block_id": row["id"], "text": row["text"], "tags": tags, "checked": false}));
    }
    Ok(json!({"tags": result}))
}

// Mind maps -----------------------------------------------------------------------------------

const MAX_MAPS: i64 = 100;

/// `concept_maps._page`.
fn map_page(conn: &Connection, number: i64) -> Done<Row> {
    let number = checked(number)?;
    db::row(conn, "SELECT id,title FROM note_page_index WHERE id=?", [number])?.ok_or_else(|| fail(404, "Page not found"))
}

fn linked(conn: &Connection, page: &Row) -> Done<Option<Value>> {
    Ok(db::row(conn, "SELECT map_id FROM note_page_maps WHERE page_id=?", [db::bind(&page["id"])?])?.map(|row| row["map_id"].clone()))
}

/// `concept_maps.page_map`: `{"map_id": id | null}`.
pub fn page_map(conn: &Connection, number: i64) -> Done<Value> {
    let page = map_page(conn, number)?;
    Ok(json!({"map_id": linked(conn, &page)?}))
}

/// `concept_maps._title`.
fn map_title(value: &Value) -> String {
    let stripped = value.as_str().map_or("", py::strip);
    let cut: String = stripped.chars().take(200).collect();
    let title = py::strip(&cut);
    if title.is_empty() { "Untitled page".into() } else { title.to_owned() }
}

/// `concept_maps.create_page_map`: the page's mind map, made with the page as its root topic.
pub fn create_page_map(conn: &Connection, number: i64) -> Done<Value> {
    within(conn, true, || {
        let page = map_page(conn, number)?;
        if let Some(existing) = linked(conn, &page)? {
            return Ok(json!({"map_id": existing}));
        }
        if conn.query_row("SELECT count(*) FROM schema_maps", [], |row| row.get::<_, i64>(0))? >= MAX_MAPS {
            return Err(bad(format!("At most {MAX_MAPS} maps are supported; export and delete an old map first")));
        }
        let title = map_title(&page["title"]);
        let maps = crate::schema::Maps { conn, applyai: true };
        let document = maps.graph(&json!({"nodes": [{"id": "page", "kind": "topic", "label": title, "notes": "", "x": 40, "y": 40, "url": "",
                                                     "source": {"kind": "note_page", "id": page["id"]}}], "edges": []}), false)?;
        conn.execute("INSERT INTO schema_maps(title,mode,graph_json,viewport_json,updated_at) VALUES(?,?,?,?,?)",
                     params![title, "study", py::dumps(&document), py::dumps(&json!({"x": 40, "y": 40, "zoom": 1})), now()])?;
        let map_id = conn.last_insert_rowid();
        conn.execute("INSERT INTO note_page_maps(page_id,map_id,migrated) VALUES(?,?,0)", params![db::bind(&page["id"])?, map_id])?;
        Ok(json!({"map_id": map_id}))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn daily_dates_are_read_as_strptime_reads_them() {
        assert_eq!(daily_date("2026-01-02"), Some(true));
        assert_eq!(daily_date("2026-02-30"), Some(false));
        assert_eq!(daily_date("0000-01-01"), Some(false));
        assert_eq!(daily_date("２０２６-01-1５"), Some(true));
        assert_eq!(daily_date("2026-1５-01"), Some(false));
        assert_eq!(daily_date("2026-01-3５"), Some(false));
        assert_eq!(daily_date("2026-1-01"), None);
        assert_eq!(map_title(&json!("  x  ")), "x");
        assert_eq!(map_title(&json!(" ")), "Untitled page");
    }
}
