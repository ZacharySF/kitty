//! Saved Documents (`kitty.core.documents`, `web_documents`): the list, read and save
//! routes, source metadata and original downloads, and topic matches. The block engine
//! applies the chunk edits (including its Loro mirror, history, tags and search projection).
//! Extraction/import, document-to-note tasks and PDF export remain on Python (pymupdf).

mod convert;
mod topics;

use std::path::Path;

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::blocks::{actor::Actor, doc::PageDoc, store};
use crate::bootstrap::Bootstrap;
use crate::modules::{Failure, Workspace};
use crate::ported::{db, fail, internal, py, Call, Ctx, Done, Group};
use crate::target::Target;

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };
const ROOT: &str = "/api/documents";
const ORIGINALS: &str = "/api/documents/originals";
const TOPICS: &str = "/api/documents/topics";
const MAX_TEXT: usize = 512 * 1024;
const MAX_UPLOAD: i64 = 12 * 1024 * 1024;

/// `[1-9][0-9]{0,15}` (path/query syntax, before the integer's range check).
fn destination(value: &str) -> Option<i64> {
    let bytes = value.as_bytes();
    if !(1..=16).contains(&bytes.len()) || !(b'1'..=b'9').contains(&bytes[0]) || !bytes.iter().all(u8::is_ascii_digit) {
        return None;
    }
    value.parse().ok()
}

fn number(path: &str) -> Option<i64> {
    destination(path.strip_prefix("/api/documents/")?)
}

fn owns(path: &str, mutation: bool) -> bool {
    path == ROOT || number(path).is_some() || if mutation { path == TOPICS } else { path == ORIGINALS }
}

/// `notes.keys`.
fn keys(body: &Map<String, Value>, allowed: &[&str], required: &[&str]) -> Done<()> {
    if body.keys().any(|key| !allowed.contains(&key.as_str())) || required.iter().any(|key| !body.contains_key(*key)) {
        return Err(fail(400, "Unexpected or missing canvas fields"));
    }
    Ok(())
}

/// `notes.text` (UTF-8 byte bounds, not code points).
fn text<'a>(value: &'a Value, maximum: usize, label: &str) -> Done<&'a str> {
    let text = value.as_str().filter(|text| !text.contains('\0')).ok_or_else(|| fail(400, format!("{label} must be text")))?;
    if text.len() > maximum {
        return Err(fail(400, format!("{label} exceeds {maximum} bytes")));
    }
    Ok(text)
}

/// `notes.integer`.
fn integer(value: &Value) -> Done<i64> {
    py::int(value).filter(|number| (1..=(1_i64 << 53) - 1).contains(number))
        .ok_or_else(|| fail(400, "Expected a valid integer"))
}

fn notebook(conn: &Connection, value: Option<&Value>) -> Done<Option<i64>> {
    let Some(value) = value.filter(|value| !value.is_null()) else { return Ok(None) };
    let id = integer(value)?;
    if !db::exists(conn, "SELECT 1 FROM note_notebooks WHERE id=?", [id])? {
        return Err(fail(404, "Destination notebook not found"));
    }
    Ok(Some(id))
}

/// `notes._folder_id`.
fn folder_id(conn: &Connection, value: Option<&Value>) -> Done<Option<i64>> {
    let Some(value) = value.filter(|value| !value.is_null()) else { return Ok(None) };
    let id = integer(value)?;
    if !db::exists(conn, "SELECT 1 FROM note_folders WHERE id=?", [id])? {
        return Err(fail(404, "Folder not found"));
    }
    Ok(Some(id))
}

fn listing(conn: &Connection) -> Done<Value> {
    Ok(json!({"documents": db::rows(conn,
        "SELECT json_extract(properties,'$.number') AS id,text AS title,json_extract(properties,'$.format') AS format,\
         json_extract(properties,'$.notebook_id') AS notebook_id,json_extract(properties,'$.revision') AS revision,updated_at \
         FROM blocks WHERE type='document' AND parent_id IS NULL AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 500", [])?}))
}

/// `_doc`: a live document root addressed by its number, then its chunk blocks.
fn doc(conn: &Connection, id: i64) -> Done<PageDoc> {
    integer(&Value::from(id))?;
    let row = db::row(conn,
        "SELECT id FROM blocks WHERE type='document' AND parent_id IS NULL AND deleted_at IS NULL AND json_extract(properties,'$.number')=?", [id])?
        .ok_or_else(|| fail(404, "Document not found"))?;
    store::load_page(conn, row["id"].as_str().ok_or_else(internal)?)
}

/// `_shape`.
fn shape(doc: &PageDoc) -> Done<Value> {
    let root = doc.root().ok_or_else(internal)?;
    let props = &root.properties;
    Ok(json!({"id": props.get("number").ok_or_else(internal)?, "title": root.text,
              "content": convert::join(&doc.children(Some(&root.id), false)),
              "format": props.get("format").ok_or_else(internal)?, "notebook_id": props.get("notebook_id").unwrap_or(&Value::Null),
              "revision": props.get("revision").ok_or_else(internal)?, "created_at": root.created_at, "updated_at": root.updated_at}))
}

fn document(conn: &Connection, id: i64) -> Done<Value> {
    shape(&doc(conn, id)?)
}

/// `_write_ops`: only the changed chunks, the title when changed, and the next revision.
fn write_ops(doc: &PageDoc, title: &str, content: &str, format: &str, notebook: Option<i64>, revision: i64) -> Done<Vec<Value>> {
    let root = doc.root().ok_or_else(internal)?;
    let desired = convert::document_specs(doc, content, format == "markdown")?;
    let mut ops = convert::sync_chunks(doc, &desired)?;
    if root.text != title {
        ops.push(json!({"op": "text", "id": root.id, "text": title}));
    }
    ops.push(json!({"op": "set", "id": root.id, "props": {"format": format, "notebook_id": notebook, "revision": revision + 1}}));
    Ok(ops)
}

fn save(conn: &Connection, body: &Map<String, Value>, id: Option<i64>) -> Done<Value> {
    let required = if id.is_some() { &["title", "content", "format", "notebook_id", "revision"][..] } else { &["title", "content", "format", "notebook_id"][..] };
    keys(body, &["title", "content", "format", "notebook_id", "revision"], required)?;
    let title = py::strip(text(&body["title"], 200, "Title")?);
    let content = text(&body["content"], MAX_TEXT, "Document")?;
    let format = body["format"].as_str().unwrap_or("");
    if title.is_empty() || !["markdown", "text"].contains(&format) {
        return Err(fail(400, "A title and Markdown or plain text format are required"));
    }
    let id = db::transaction(conn, true, || {
        let nid = notebook(conn, body.get("notebook_id"))?;
        if let Some(id) = id {
            let doc = doc(conn, id)?;
            let revision = integer(&body["revision"])?;
            if !py::eq_int(doc.root().and_then(|root| root.properties.get("revision")).ok_or_else(internal)?, revision) {
                return Err(fail(409, "Document changed in another tab; your local edits were not overwritten. Reload before saving."));
            }
            let ops = write_ops(&doc, title, content, format, nid, revision)?;
            let mut base = Map::new();
            base.insert(doc.page_id.clone(), revision.into());
            store::transact(conn, &Value::Array(ops), Some(&Value::Object(base)), None, false, &Actor::user())?;
            Ok(id)
        } else {
            let count: i64 = conn.query_row("SELECT COUNT(*) FROM blocks WHERE type='document' AND parent_id IS NULL AND deleted_at IS NULL", [], |row| row.get(0))?;
            if count >= 500 {
                return Err(fail(400, "Document limit reached (500)"));
            }
            let root = store::uuid4()?;
            let id: i64 = conn.query_row("SELECT COALESCE(MAX(json_extract(properties,'$.number')),0)+1 FROM blocks \
                WHERE type='document' AND parent_id IS NULL AND json_extract(properties,'$.number') IS NOT NULL", [], |row| row.get(0))?;
            let specs = convert::new_specs(&root, content, format == "markdown")?;
            let mut ops = vec![json!({"op": "insert", "id": root, "type": "document", "parent": null, "text": title,
                "props": {"number": id, "format": format, "notebook_id": nid, "revision": 1}})];
            ops.extend(convert::insert_ops(specs)?);
            store::transact(conn, &Value::Array(ops), None, None, false, &Actor::user())?;
            Ok(id)
        }
    })?;
    document(conn, id)
}

fn originals(conn: &Connection, column: &str, id: i64) -> Done<Value> {
    integer(&Value::from(id))?;
    Ok(json!({"originals": db::rows(conn, &format!(
        "SELECT id,name,extension,byte_size,page_id,document_id FROM document_originals WHERE {column}=? ORDER BY created_at,id"), [id])?}))
}

fn query_fields(call: &Call, allowed: &[&str]) -> Done<()> {
    if !call.only(allowed) { return Err(fail(400, "Unexpected or missing canvas fields")); }
    Ok(())
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    if let Some(body) = call.body {
        return Ok(Some(if call.path == TOPICS {
            topics::topics(conn, body)?
        } else {
            save(conn, body, number(call.path))?
        }));
    }
    if call.path == ORIGINALS {
        query_fields(call, &["page_id", "document_id"])?;
        if call.query.len() != 1 {
            return Err(fail(400, "Choose one page_id or document_id"));
        }
        let (column, value) = &call.query[0];
        let id = destination(value).ok_or_else(|| fail(400, "Invalid original destination id"))?;
        return Ok(Some(originals(conn, column, id)?));
    }
    query_fields(call, &[])?;
    Ok(Some(if call.path == ROOT { listing(conn)? } else { document(conn, number(call.path).ok_or_else(internal)?)? }))
}

/// An original download route (`[a-f0-9]{32}`). PDFs still belong to the Python worker.
pub fn original_id(path: &str) -> Option<&str> {
    let id = path.strip_prefix("/api/documents/originals/")?;
    (id.len() == 32 && id.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))).then_some(id)
}

/// `download_name`: a bounded ASCII filename with no paths or control characters.
fn download_name(title: &str, extension: &str) -> String {
    let mut base = String::new();
    let mut other = false;
    for c in title.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
            base.push(c);
            other = false;
        } else if !other {
            base.push('-');
            other = true;
        }
    }
    let mut base: String = base.trim_matches(['.', '-']).chars().take(100).collect();
    if base.is_empty() { base.push_str("document"); }
    if base.to_ascii_lowercase().ends_with(&format!(".{extension}")) {
        base.truncate(base.len() - extension.len() - 1);
        if base.is_empty() { base.push_str("document"); }
    }
    format!("{base}.{extension}")
}

/// `original_root`: private storage, refusing a symlink even if it points inside the workspace.
fn original_root(data_dir: &Path) -> Done<std::path::PathBuf> {
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
    let root = data_dir.join("document-originals");
    if root.is_symlink() {
        return Err(fail(409, "Original storage cannot be a symbolic link"));
    }
    std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&root).map_err(|_| internal())?;
    std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700)).map_err(|_| internal())?;
    root.canonicalize().map_err(|_| internal())
}

/// `original`: registered bytes only, attachment-only, never rendered as active content.
fn original(conn: &Connection, data_dir: &Path, id: &str) -> Done<(Vec<u8>, String)> {
    let row = db::row(conn, "SELECT * FROM document_originals WHERE id=?", [id])?.ok_or_else(|| fail(404, "Original not found"))?;
    let root = original_root(data_dir)?;
    let path = root.join(id);
    let size = row["byte_size"].as_i64().ok_or_else(internal)?;
    if path.is_symlink() || !path.is_file() || path.canonicalize().map_err(|_| internal())?.parent() != Some(root.as_path())
        || path.metadata().map_err(|_| internal())?.len() as i64 != size || size > MAX_UPLOAD {
        return Err(fail(404, "Registered original is unavailable"));
    }
    let data = std::fs::read(path).map_err(|_| internal())?;
    let name = download_name(row["name"].as_str().ok_or_else(internal)?, row["extension"].as_str().ok_or_else(internal)?);
    Ok((data, name))
}

pub fn download(workspace: &Workspace, bootstrap: &Bootstrap, target: &Target) -> Result<(Vec<u8>, String), Failure> {
    let run = || {
        if !workspace.active.iter().any(|module| module == "workspace") {
            return Err(crate::ported::disabled("workspace"));
        }
        if !target.query.is_empty() {
            return Err(fail(400, "Unexpected or missing canvas fields"));
        }
        let id = original_id(&target.path).ok_or_else(internal)?;
        db::with(bootstrap.db_path(), |conn| original(conn, &bootstrap.root().join("data"), id))
    };
    run().map_err(|fault| Failure { status: fault.status, message: fault.message })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn route_syntax_and_download_names_are_bounded() {
        assert_eq!(number("/api/documents/9007199254740992"), Some(9007199254740992));
        assert!(number("/api/documents/01").is_none());
        assert!(number("/api/documents/1/note").is_none());
        assert_eq!(download_name("..a /😀b.MD", "md"), "a-b.md");
        assert_eq!(download_name("...", "txt"), "document.txt");
    }
}
