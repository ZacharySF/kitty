//! Registered references (`kitty.core.notes`): the PNG files under `data/notes-attachments`,
//! served only when the row, the path and the bytes still agree; a page's portable copy with
//! its references; and the retention cleanup of unreferenced ones. Registering and importing
//! rasterize with Pillow and PyMuPDF and stay on Python.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use base64::Engine;
use hyper::body::Incoming;
use hyper::{Method, Request, Response, StatusCode};
use rusqlite::Connection;
use serde_json::{json, Value};

use super::library::{page, properties};
use super::outline::get_blocks;
use super::{bad, within};
use crate::front::{refuse, send, Body, Front, Refusal};
use crate::ported::{db, disabled, fail, internal, time, Done};
use crate::target::{parse_target, Target};

const MAX_ATTACHMENT_BYTES: u64 = 8 * 1024 * 1024;
const MAX_IMPORT_BYTES: usize = 12 * 1024 * 1024;
const DOWNLOAD: &str = "/api/notes/attachments/";

fn is_png_name(name: &str) -> bool {
    name.len() == 36 && name.ends_with(".png") && name[..32].bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// `web_notes.download`'s path: `/api/notes/attachments/<32 lowercase hex>`.
pub fn is_download(path: &str, mutation: bool) -> bool {
    !mutation && path.strip_prefix(DOWNLOAD).is_some_and(|id| id.len() == 32 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')))
}

/// `GET /api/notes/attachments/<id>`: the registered PNG (`image/png`, the front's headers).
pub async fn route(front: &Front, target: &Target, forward_target: &str, request: &Request<Incoming>, mutation: bool, upgrade: bool)
                   -> Option<Result<Response<Body>, Refusal>> {
    if upgrade || !is_download(&target.path, mutation) {
        return None;
    }
    let head = request.method() == Method::HEAD;
    let outcome = tokio::task::block_in_place(|| -> Done<Vec<u8>> {
        if !front.workspace.active.iter().any(|module| module == "workspace") {
            return Err(disabled("workspace"));
        }
        let target = parse_target(forward_target).map_err(|invalid| fail(invalid.0, invalid.1))?;
        if !target.query.is_empty() {
            return Err(bad("Unexpected or missing canvas fields"));
        }
        let id = target.path.trim_start_matches(DOWNLOAD).to_owned();
        let data_dir = front.bootstrap.root().join("data");
        db::with(front.bootstrap.db_path(), |conn| attachment_bytes(conn, &data_dir, &id))
    });
    Some(outcome.map(|data| send(StatusCode::OK, data, "image/png", head)).map_err(|fault| refuse(fault.status, fault.message)))
}

fn is_symlink(path: &Path) -> bool {
    std::fs::symlink_metadata(path).is_ok_and(|meta| meta.file_type().is_symlink())
}

/// `attachment_root`: the storage folder (made owner-only when missing), resolved.
fn attachment_root(data_dir: &Path) -> Done<PathBuf> {
    let root = data_dir.join("notes-attachments");
    if is_symlink(&root) {
        return Err(fail(409, "Attachment storage cannot be a symbolic link"));
    }
    if !root.is_dir() {
        use std::os::unix::fs::DirBuilderExt;
        std::fs::create_dir_all(data_dir).map_err(|_| internal())?;
        if let Err(error) = std::fs::DirBuilder::new().mode(0o700).create(&root) {
            if error.kind() != std::io::ErrorKind::AlreadyExists {
                return Err(internal());
            }
        }
    }
    std::fs::canonicalize(&root).map_err(|_| internal())
}

/// `path.resolve().parent == root and path.name == name` for a path that is no symbolic link.
fn stored_in(path: &Path, root: &Path, name: &str) -> bool {
    let parent = path.parent().filter(|parent| !parent.as_os_str().is_empty()).unwrap_or(Path::new("."));
    path.file_name().is_some_and(|file| file == name) && std::fs::canonicalize(parent).is_ok_and(|parent| parent == root)
}

/// `attachment_bytes`: a registered reference's PNG, refused unless row, path and bytes agree.
pub fn attachment_bytes(conn: &Connection, data_dir: &Path, id: &str) -> Done<Vec<u8>> {
    let row = db::row(conn, "SELECT * FROM note_attachments WHERE id=?", [id])?.ok_or_else(|| fail(404, "Registered reference not found"))?;
    let root = attachment_root(data_dir)?;
    let path = PathBuf::from(row.get("filepath").and_then(Value::as_str).ok_or_else(internal)?);
    if is_symlink(&path) || !stored_in(&path, &root, &format!("{id}.png")) {
        return Err(fail(404, "Registered reference path is invalid"));
    }
    let size = row.get("byte_size").and_then(Value::as_i64).ok_or_else(internal)?;
    let meta = std::fs::metadata(&path).map_err(|_| fail(404, "Registered reference unavailable"))?;
    if meta.len() as i64 != size || size as u64 > MAX_ATTACHMENT_BYTES {
        return Err(fail(404, "Registered reference changed or is unavailable"));
    }
    let data = std::fs::read(&path).map_err(|_| fail(404, "Registered reference unavailable"))?;
    if !data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err(fail(404, "Registered reference is not a normalized image"));
    }
    Ok(data)
}

/// `notes.export_page`: the portable copy (`kitty-notes` format 1) with its references.
pub fn export_page(conn: &Connection, data_dir: &Path, number: i64) -> Done<Value> {
    let result = page(conn, number)?;
    let mut refs: Vec<String> = Vec::new();
    for item in result["document"]["elements"].as_array().ok_or_else(internal)? {
        if item.get("kind").ok_or_else(internal)? == "image" {
            let attachment = item.get("attachment").and_then(Value::as_str).ok_or_else(internal)?.to_owned();
            if !refs.contains(&attachment) {
                refs.push(attachment);
            }
        }
    }
    refs.sort();
    let mut attachments = Vec::new();
    let mut size = 0;
    for id in refs {
        let data = attachment_bytes(conn, data_dir, &id)?;
        size += data.len();
        if size > MAX_IMPORT_BYTES {
            return Err(bad("Portable page exceeds 12 MiB of references; export drawings or split references across pages"));
        }
        attachments.push(json!({"id": id, "data": base64::engine::general_purpose::STANDARD.encode(&data), "name": "Reference.png"}));
    }
    let notebook = db::row(conn, "SELECT title,subject,project FROM note_notebooks WHERE id=?", [db::bind(&result["notebook_id"])?])?.ok_or_else(internal)?;
    let blocks = get_blocks(conn, number)?["blocks"].take();
    Ok(json!({"format": "kitty-notes", "version": 1, "title": result["title"], "notebook": notebook, "document": result["document"],
              "viewport": result["viewport"], "attachments": attachments, "blocks": blocks}))
}

/// The ids an image block or a legacy raw canvas names (`"attachment" in item`, the raw elements).
fn referenced(conn: &Connection) -> Done<HashSet<String>> {
    let mut found = HashSet::new();
    let mut add = |value: Option<&Value>| -> Done<()> {
        match value {
            Some(Value::String(id)) => {
                found.insert(id.clone());
            }
            // An unhashable value is Python's TypeError.
            Some(Value::Array(_) | Value::Object(_)) => return Err(internal()),
            _ => {}
        }
        Ok(())
    };
    for row in db::rows(conn, "SELECT properties FROM blocks WHERE type IN ('image','canvas')", [])? {
        let item = properties(&row)?;
        if item.contains_key("attachment") {
            add(item.get("attachment"))?;
        }
        if let Some(Value::Object(raw)) = item.get("raw") {
            if let Some(Value::Array(elements)) = raw.get("elements") {
                for element in elements {
                    if let Value::Object(element) = element {
                        if element.get("kind").and_then(Value::as_str) == Some("image") {
                            add(element.get("attachment"))?;
                        }
                    }
                }
            }
        }
    }
    Ok(found)
}

fn modified(meta: &std::fs::Metadata) -> Option<f64> {
    let stamp = meta.modified().ok()?;
    let since = stamp.duration_since(std::time::UNIX_EPOCH).ok()?;
    Some(since.as_secs() as f64 + f64::from(since.subsec_nanos()) * 1e-9)
}

/// `notes.cleanup`: remove unreferenced references older than the retention window, never pages.
pub fn cleanup(conn: &Connection, data_dir: &Path) -> Done<Value> {
    let root = attachment_root(data_dir)?;
    let removed = within(conn, false, || {
        // The writer lock first, so concurrent saves cannot race the cleanup.
        conn.execute("UPDATE note_preferences SET revision=revision WHERE id=1", [])?;
        let days: i64 = conn.query_row("SELECT retention_days FROM note_preferences WHERE id=1", [], |row| row.get(0))?;
        let cutoff = time::Dt::now_utc().add(-days * 86_400_000_000).map_err(|_| internal())?;
        let cutoff_text = cutoff.isoformat();
        let referenced = referenced(conn)?;
        let mut removed = 0;
        for row in db::rows(conn, "SELECT * FROM note_attachments WHERE created_at <= ?", [&cutoff_text])? {
            let id = row.get("id").and_then(Value::as_str).ok_or_else(internal)?;
            if referenced.contains(id) {
                continue;
            }
            let path = PathBuf::from(row.get("filepath").and_then(Value::as_str).ok_or_else(internal)?);
            if !is_symlink(&path) && stored_in(&path, &root, &format!("{id}.png")) {
                match std::fs::remove_file(&path) {
                    Ok(()) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                    Err(_) => return Err(internal()),
                }
                conn.execute("DELETE FROM note_attachments WHERE id=?", [id])?;
                removed += 1;
            }
        }
        let mut statement = conn.prepare("SELECT id FROM note_attachments")?;
        let registered: HashSet<String> = statement.query_map([], |row| row.get(0))?.collect::<rusqlite::Result<_>>()?;
        let cutoff_epoch = cutoff.utc() as f64 / 1e6;
        for entry in std::fs::read_dir(&root).map_err(|_| internal())? {
            let path = entry.map_err(|_| internal())?.path();
            let Some(name) = path.file_name().and_then(|name| name.to_str()) else { continue };
            if !is_png_name(name) || registered.contains(&name[..32]) || is_symlink(&path) {
                continue;
            }
            let Ok(meta) = std::fs::metadata(&path) else { continue };
            if meta.is_file() && modified(&meta).is_some_and(|stamp| stamp <= cutoff_epoch) {
                std::fs::remove_file(&path).map_err(|_| internal())?;
                removed += 1;
            }
        }
        Ok(removed)
    })?;
    Ok(json!({"removed": removed}))
}
