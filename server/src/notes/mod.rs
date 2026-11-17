//! The Notes routes (`kitty.web_notes` in Python) over the block engine: the library
//! (folders, notebooks, the page list and its search), note pages (load, canvas save,
//! viewport, Trash, daily and resolve, favourites and visits, library order), the outline
//! readers (a page's blocks with live references, citations of a block, topic and tag pages,
//! unlinked title mentions), a page's mind map, privacy preferences, the portable export,
//! the unused-reference cleanup and the download of a registered PNG.
//!
//! ```text
//! GET  /api/notes?search=                       folders, notebooks, up to 500 pages, preferences
//! GET  /api/notes/tags                          canvas cards and outline blocks with tags
//! GET  /api/notes/pages/<n>[/export]            a page (its canvas document), or its portable copy
//! GET  /api/notes/pages/<n>/blocks              the outline with revision, references, citation counts
//! GET  /api/notes/pages/<n>/mentions            other pages' titles named but not linked
//! GET  /api/notes/pages/<n>/map                 {map_id}; POST {} creates it
//! GET  /api/notes/blocks/<uuid>/contexts?offset= the blocks citing one block
//! GET  /api/notes/topics?kind=page|tag&topic=&offset=
//! GET  /api/notes/attachments/<32 hex>          the registered PNG (crate::notes::route)
//! POST /api/notes/{daily, resolve, reorder, folders, notebooks, pages, preferences, cleanup}
//! POST /api/notes/folders/<n>[/delete], /api/notes/notebooks/<n>
//! POST /api/notes/pages/<n>[/viewport|/delete|/organize]
//! ```
//!
//! Every page write is one block transaction as the person at this device. Stay on Python,
//! forwarded: `POST /api/notes/attachments` and `POST /api/notes/import`, which decode
//! images and PDFs with Pillow and PyMuPDF and run as the worker's background tasks.

mod canvas;
mod files;
mod library;
mod outline;

use std::sync::LazyLock;

use regex::Regex;
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::ported::{fail, Call, Ctx, Done, Fault, Group};

pub use files::route;

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

const PREFIX: &str = "/api/notes";
/// The writes that stay on the worker (Pillow, PyMuPDF, its task runner).
const FORWARDED: [&str; 2] = ["/api/notes/attachments", "/api/notes/import"];
const MAX_PAGES: i64 = 500;
const MAX_BLOCKS: i64 = 2000;

static PAGE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/notes/pages/([1-9][0-9]{0,15})(/blocks|/mentions|/map|/export|/viewport|/delete|/organize)?$").expect("valid"));
static FOLDER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/notes/folders/([1-9][0-9]{0,15})(/delete)?$").expect("valid"));
static NOTEBOOK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/notes/notebooks/([1-9][0-9]{0,15})$").expect("valid"));
static CONTEXTS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/notes/blocks/([0-9a-f-]{36})/contexts$").expect("valid"));

fn owns(path: &str, mutation: bool) -> bool {
    (path == PREFIX || path.starts_with("/api/notes/")) && !(mutation && FORWARDED.contains(&path)) && !files::is_download(path, mutation)
}

/// `NotesError(message)`: a 400.
pub(crate) fn bad(message: impl Into<String>) -> Fault {
    fail(400, message)
}

fn unexpected() -> Fault {
    bad("Unexpected or missing canvas fields")
}

/// `notes.keys` of a JSON value: an object naming only `allowed`, every `required` among them.
pub(crate) fn keys<'a>(value: &'a Value, allowed: &[&str], required: &[&str]) -> Done<&'a Map<String, Value>> {
    let map = value.as_object().ok_or_else(unexpected)?;
    body_keys(map, allowed, required)?;
    Ok(map)
}

pub(crate) fn body_keys(map: &Map<String, Value>, allowed: &[&str], required: &[&str]) -> Done<()> {
    if map.keys().any(|key| !allowed.contains(&key.as_str())) || required.iter().any(|key| !map.contains_key(*key)) {
        return Err(unexpected());
    }
    Ok(())
}

/// `notes.keys` of a query.
fn query_keys(call: &Call, allowed: &[&str], required: &[&str]) -> Done<()> {
    if !call.only(allowed) || required.iter().any(|key| call.get(key).is_none()) {
        return Err(unexpected());
    }
    Ok(())
}

/// `notes.text`: a string without NUL of at most `maximum` UTF-8 bytes.
pub(crate) fn text<'a>(value: &'a Value, maximum: usize, label: &str) -> Done<&'a str> {
    match value {
        Value::String(text) if !text.contains('\0') => {
            if text.len() > maximum {
                return Err(bad(format!("{label} exceeds {maximum} bytes")));
            }
            Ok(text)
        }
        _ => Err(bad(format!("{label} must be text"))),
    }
}

/// `notes.integer`: a JSON integer (never a bool) in `low..=high`.
pub(crate) fn integer(value: &Value, low: i64, high: i64) -> Done<i64> {
    match value {
        Value::Number(number) => number.as_i64().filter(|value| (low..=high).contains(value)).ok_or_else(|| bad("Expected a valid integer")),
        _ => Err(bad("Expected a valid integer")),
    }
}

/// `2**53 - 1`, `notes.integer`'s default bound.
pub(crate) const MAX_INTEGER: i64 = (1 << 53) - 1;

/// `notes.integer` of a path number (at most 16 digits), where the core checks it.
pub(crate) fn checked(number: i64) -> Done<i64> {
    if (1..=MAX_INTEGER).contains(&number) { Ok(number) } else { Err(bad("Expected a valid integer")) }
}

/// A path number (`[1-9][0-9]{0,15}`, so it fits); the core checks its range where Python does.
fn path_number(text: &str) -> i64 {
    text.parse().unwrap_or(i64::MAX)
}

/// `_offset`: up to eight ASCII digits (the core then checks `notes.integer(offset, 0, ...)`).
fn offset(call: &Call) -> Done<i64> {
    let value = call.get("offset").unwrap_or("0");
    if value.is_empty() || value.len() > 8 || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(bad("Offset must be a nonnegative integer"));
    }
    value.parse().map_err(|_| bad("Offset must be a nonnegative integer"))
}

/// `notes.integer(offset, 0, MAX_PAGES * MAX_BLOCKS)`.
pub(crate) fn offset_checked(offset: i64) -> Done<i64> {
    if offset > MAX_PAGES * MAX_BLOCKS { Err(bad("Expected a valid integer")) } else { Ok(offset) }
}

/// `with transaction(conn)` unless one is already open (`nullcontext() if conn.in_transaction`).
pub(crate) fn within<T>(conn: &Connection, immediate: bool, body: impl FnOnce() -> Done<T>) -> Done<T> {
    if conn.is_autocommit() { crate::ported::db::transaction(conn, immediate, body) } else { body() }
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    match call.body {
        None => get(ctx, call),
        Some(body) => post(ctx, call.path, body),
    }
}

fn get(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    let path = call.path;
    if let Some(found) = PAGE.captures(path) {
        let suffix = found.get(2).map_or("", |m| m.as_str());
        if !matches!(suffix, "" | "/blocks" | "/mentions" | "/map" | "/export") {
            return Ok(None);
        }
        query_keys(call, &[], &[])?;
        let number = path_number(&found[1]);
        return Ok(Some(match suffix {
            "/blocks" => outline::get_blocks(conn, number)?,
            "/mentions" => outline::mentions(conn, number)?,
            "/map" => library::page_map(conn, number)?,
            "/export" => files::export_page(conn, &ctx.root.join("data"), number)?,
            _ => library::page(conn, number)?,
        }));
    }
    if let Some(found) = CONTEXTS.captures(path) {
        query_keys(call, &["offset"], &[])?;
        let offset = offset(call)?;
        return Ok(Some(outline::contexts(conn, &found[1], offset)?));
    }
    match path {
        "/api/notes/topics" => {
            query_keys(call, &["kind", "topic", "offset"], &["kind", "topic"])?;
            let offset = offset(call)?;
            Ok(Some(outline::topics(conn, call.get("kind").unwrap_or(""), call.get("topic").unwrap_or(""), offset)?))
        }
        "/api/notes/tags" => {
            query_keys(call, &[], &[])?;
            Ok(Some(library::tags_summary(conn)?))
        }
        PREFIX => {
            query_keys(call, &["search"], &[])?;
            Ok(Some(library::listing(conn, call.get("search").unwrap_or(""))?))
        }
        _ => Ok(None),
    }
}

fn post(ctx: &Ctx, path: &str, body: &Map<String, Value>) -> Done<Option<Value>> {
    let conn = ctx.conn;
    let null = Value::Null;
    let field = |key: &str| body.get(key).unwrap_or(&null);
    if let Some(found) = PAGE.captures(path) {
        let suffix = found.get(2).map_or("", |m| m.as_str());
        let number = path_number(&found[1]);
        return Ok(Some(match suffix {
            "/map" => {
                body_keys(body, &[], &[])?;
                library::create_page_map(conn, number)?
            }
            "/organize" => library::organize_page(conn, number, body)?,
            "/viewport" => {
                body_keys(body, &["revision", "viewport"], &["revision", "viewport"])?;
                library::save_viewport(conn, number, field("revision"), field("viewport"))?
            }
            "/delete" => {
                body_keys(body, &["revision", "confirm"], &["revision", "confirm"])?;
                if body.get("confirm") != Some(&Value::Bool(true)) {
                    return Err(bad("Confirm page deletion"));
                }
                library::delete_page(conn, number, field("revision"))?
            }
            "" => {
                body_keys(body, &["revision", "title", "document"], &["revision", "title", "document"])?;
                library::save_page(conn, number, field("revision"), field("document"), field("title"))?
            }
            _ => return Ok(None),
        }));
    }
    if let Some(found) = FOLDER.captures(path) {
        let number = path_number(&found[1]);
        if found.get(2).is_some() {
            body_keys(body, &["revision", "confirm"], &["revision", "confirm"])?;
            if body.get("confirm") != Some(&Value::Bool(true)) {
                return Err(bad("Confirm folder deletion"));
            }
            return Ok(Some(library::delete_folder(conn, number, field("revision"))?));
        }
        body_keys(body, &["revision", "title", "parent_id"], &["revision", "title", "parent_id"])?;
        return Ok(Some(library::save_folder(conn, number, field("revision"), field("title"), field("parent_id"))?));
    }
    if let Some(found) = NOTEBOOK.captures(path) {
        body_keys(body, &["revision", "title", "subject", "project", "folder_id"], &["revision", "title", "subject", "project"])?;
        return Ok(Some(library::save_notebook(conn, path_number(&found[1]), field("revision"), field("title"), field("subject"),
                                              field("project"), body.get("folder_id"))?));
    }
    let empty = Value::String(String::new());
    Ok(Some(match path {
        "/api/notes/daily" => {
            body_keys(body, &["date"], &[])?;
            library::daily_page(conn, field("date"))?
        }
        "/api/notes/resolve" => {
            body_keys(body, &["title", "notebook_id"], &["title"])?;
            library::resolve_page(conn, field("title"), field("notebook_id"))?
        }
        "/api/notes/reorder" => {
            body_keys(body, &["kind", "ids"], &["kind", "ids"])?;
            library::reorder(conn, field("kind"), field("ids"))?
        }
        "/api/notes/folders" => {
            body_keys(body, &["title", "parent_id"], &["title"])?;
            library::create_folder(conn, field("title"), field("parent_id"))?
        }
        "/api/notes/notebooks" => {
            body_keys(body, &["title", "subject", "project", "folder_id"], &["title"])?;
            library::create_notebook(conn, field("title"), body.get("subject").unwrap_or(&empty), body.get("project").unwrap_or(&empty), field("folder_id"))?
        }
        "/api/notes/pages" => {
            body_keys(body, &["notebook_id", "title"], &["notebook_id"])?;
            let untitled = Value::String("Untitled page".into());
            library::create_page(conn, field("notebook_id"), body.get("title").unwrap_or(&untitled), None)?
        }
        "/api/notes/preferences" => {
            body_keys(body, &["revision", "allow_ai", "retention_days"], &["revision", "allow_ai", "retention_days"])?;
            library::preferences(conn, field("revision"), field("allow_ai"), field("retention_days"))?
        }
        "/api/notes/cleanup" => {
            body_keys(body, &["confirm"], &["confirm"])?;
            if body.get("confirm") != Some(&Value::Bool(true)) {
                return Err(bad("Confirm unused reference cleanup"));
            }
            files::cleanup(conn, &ctx.root.join("data"))?
        }
        _ => return Ok(None),
    }))
}
