//! The block routes (`kitty.web_blocks` `get`/`post` in Python): pages, history and Trash,
//! agent sessions, pinned citations and the block API's reads and writes. A page is
//! `page=<block id>` or `type=page|document|quick_note&number=<n>`.
//!
//! ```text
//! GET  /api/blocks/page?<page>                    what the block editor loads (root, live blocks,
//!                                                 revision, seq, tag suggestions and names)
//! GET  /api/blocks/history?<page>&actor=&session=  editing sessions and daily snapshots
//! GET  /api/blocks/history/preview?<page>&seq=<n>  the page as it was after transaction n
//! POST /api/blocks/history/restore  {<page>, "seq"}  appends a transaction restoring it
//! GET  /api/blocks/sessions?kind=&limit=          agent (or `kind`) sessions, most recent first
//! POST /api/blocks/sessions/undo  {session, dry_run?}
//! GET  /api/blocks/citations?cites=<id@seq,...>    pinned citations: changed since, then/now, diff
//! GET  /api/blocks/trash                           blocks in Trash, newest first
//! POST /api/blocks/trash/restore    {"id"}
//! GET  /api/blocks/pages?q=&kind=&limit=           note pages, documents and quick notes
//! GET  /api/blocks/search?q=&limit=                blocks matching in the full-text index
//! GET  /api/blocks/backlinks?id=<block|page>       references, [[links]], mentions, tagged blocks
//! POST /api/blocks/pages/create  {title, notebook?, blocks?}
//! POST /api/blocks/insert  {blocks, page? | parent? | after? | before?, revision?}
//! POST /api/blocks/update  {id, text?, type?, props?, revision?}
//! POST /api/blocks/move    {id, page? | parent? | after? | before?, revision?}
//! POST /api/blocks/delete  {id, revision?}
//! POST /api/blocks/inbox   {blocks}
//! ```
//!
//! Every write is one block transaction as the person at this device (crate::blocks::store).
//! History compaction, which runs on the worker's scheduler, stays on Python (kitty.web_blocks).

use rusqlite::Connection;
use serde_json::{Map, Value};

use super::api::{self, Place};
use super::doc::{block_id, refuse};
use super::{history, sessions, store, tagging};
use crate::ported::{db, fail, internal, Call, Ctx, Done, Group};

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

const PREFIX: &str = "/api/blocks/";
const GETS: [&str; 9] = ["page", "history", "history/preview", "sessions", "citations", "trash", "pages", "search", "backlinks"];
const POSTS: [&str; 9] = ["history/restore", "sessions/undo", "trash/restore", "pages/create", "insert", "update", "move", "delete", "inbox"];
const ROOT_TYPES: [&str; 3] = ["page", "document", "quick_note"];
const PLACE: [&str; 5] = ["page", "parent", "after", "before", "revision"];
/// `block_api.MAX_LIST`.
const MAX_LIST: i64 = 200;

fn owns(path: &str, mutation: bool) -> bool {
    path.strip_prefix(PREFIX).is_some_and(|route| if mutation { POSTS.contains(&route) } else { GETS.contains(&route) })
}

/// Values a query or a body names, as the handler's `dict` holds them.
trait Values {
    fn value(&self, name: &str) -> Option<Value>;
    fn names(&self) -> Vec<&str>;
}

impl Values for [(String, String)] {
    fn value(&self, name: &str) -> Option<Value> {
        self.iter().find(|(key, _)| key == name).map(|(_, value)| Value::String(value.clone()))
    }

    fn names(&self) -> Vec<&str> {
        self.iter().map(|(key, _)| key.as_str()).collect()
    }
}

impl Values for Map<String, Value> {
    fn value(&self, name: &str) -> Option<Value> {
        self.get(name).cloned()
    }

    fn names(&self) -> Vec<&str> {
        self.keys().map(String::as_str).collect()
    }
}

/// `set(values) - allowed` is empty.
fn only(values: &(impl Values + ?Sized), allowed: &[&str]) -> bool {
    values.names().iter().all(|name| allowed.contains(name))
}

/// `_fields`: exactly the allowed names, the required ones among them.
fn fields(values: &(impl Values + ?Sized), allowed: &[&str], required: &[&str]) -> Done<()> {
    let names = values.names();
    if !only(values, allowed) || required.iter().any(|name| !names.contains(name)) {
        let mut sorted = allowed.to_vec();
        sorted.sort();
        return Err(refuse(format!("Expected fields: {}", sorted.join(", "))));
    }
    Ok(())
}

fn is_number(text: &str) -> bool {
    let bytes = text.as_bytes();
    !bytes.is_empty() && bytes.len() <= 16 && (b'1'..=b'9').contains(&bytes[0]) && bytes[1..].iter().all(u8::is_ascii_digit)
}

/// `_page`: the page root named by `page=<block id>` or `type` plus `number` (Trash included).
fn page(conn: &Connection, values: &(impl Values + ?Sized)) -> Done<String> {
    if let Some(page) = values.value("page") {
        return block_id(Some(&page));
    }
    let kind = values.value("type");
    let number = match values.value("number") {
        Some(Value::String(text)) if is_number(&text) => Some(text.parse::<i64>().map_err(|_| internal())?),
        // `type(number) is int`; past SQLite's integers it does not bind (a 500).
        Some(Value::Number(number)) if !number.is_f64() => Some(number.as_i64().ok_or_else(internal)?),
        _ => None,
    };
    let kind = match &kind {
        // `kind not in ROOT_TYPES` on a list or dict is a TypeError.
        Some(Value::Array(_) | Value::Object(_)) => return Err(internal()),
        Some(Value::String(kind)) if ROOT_TYPES.contains(&kind.as_str()) => Some(kind.as_str()),
        _ => None,
    };
    let (Some(kind), Some(number)) = (kind, number) else {
        return Err(refuse("Name the page as page=<block id> or type=page|document|quick_note with its number"));
    };
    db::row(conn, "SELECT id FROM blocks WHERE type=? AND parent_id IS NULL AND json_extract(properties,'$.number')=?", rusqlite::params![kind, number])?
        .and_then(|row| row.get("id").and_then(Value::as_str).map(str::to_owned))
        .ok_or_else(|| fail(404, "Page not found"))
}

/// `_sequence`.
fn sequence(value: Option<Value>) -> Done<i64> {
    match value {
        Some(Value::String(text)) if (1..=18).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_digit()) => text.parse().map_err(|_| internal()),
        Some(Value::Number(number)) if !number.is_f64() => number.as_i64().ok_or_else(internal),
        _ => Err(refuse("Choose a sequence number from the page's history")),
    }
}

/// `_limit`.
fn limit(call: &Call, default: i64) -> Done<i64> {
    let value = call.get("limit").map(str::to_owned).unwrap_or_else(|| default.to_string());
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() > 3 || !(b'1'..=b'9').contains(&bytes[0]) || !bytes[1..].iter().all(u8::is_ascii_digit) {
        return Err(refuse(format!("Limit must be 1-{MAX_LIST}")));
    }
    value.parse().map_err(|_| internal())
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let Some(route) = call.path.strip_prefix(PREFIX) else { return Ok(None) };
    let conn = ctx.conn;
    match call.body {
        None => get(conn, route, call).map(Some),
        Some(body) => post(conn, route, body),
    }
}

fn get(conn: &Connection, route: &str, call: &Call) -> Done<Value> {
    let query = call.query;
    match route {
        "page" => {
            if !only(query, &["page", "type", "number"]) {
                return Err(refuse("Unexpected page parameters"));
            }
            db::transaction(conn, false, || {
                let mut state = store::page_state(conn, &Value::String(page(conn, query)?))?;
                let (suggestions, tags) = tagging::page_suggestions(conn, &state)?;
                state.insert("suggestions".into(), suggestions);
                state.insert("tags".into(), tags);
                Ok(Value::Object(state))
            })
        }
        "history" => {
            if !only(query, &["page", "type", "number", "actor", "session"]) {
                return Err(refuse("Unexpected history parameters"));
            }
            history::page_history(conn, &page(conn, query)?, call.get("actor"), call.get("session"))
        }
        "sessions" => {
            fields(query, &["kind", "limit"], &[])?;
            let kind = call.get("kind").unwrap_or("agent");
            let limit = limit(call, 50)?;
            sessions::list_sessions(conn, (kind != "all").then_some(kind), limit)
        }
        "citations" => {
            fields(query, &["cites"], &["cites"])?;
            let cites: Vec<String> = call.get("cites").unwrap_or("").split(',').filter(|item| !item.is_empty()).map(str::to_owned).collect();
            history::citations(conn, &cites)
        }
        "history/preview" => {
            if !only(query, &["page", "type", "number", "seq"]) {
                return Err(refuse("Unexpected preview parameters"));
            }
            let page_id = page(conn, query)?;
            history::preview(conn, &page_id, sequence(query.value("seq"))?)
        }
        "trash" => {
            if !query.is_empty() {
                return Err(refuse("Trash takes no parameters"));
            }
            history::trash(conn)
        }
        "pages" => {
            fields(query, &["q", "kind", "limit"], &[])?;
            let limit = limit(call, 50)?;
            api::list_pages(conn, call.get("q").unwrap_or(""), call.get("kind"), limit)
        }
        "search" => {
            fields(query, &["q", "limit"], &["q"])?;
            let limit = limit(call, 20)?;
            api::search_blocks(conn, call.get("q").unwrap_or(""), limit)
        }
        "backlinks" => {
            fields(query, &["id"], &["id"])?;
            api::backlinks(conn, call.get("id").unwrap_or(""))
        }
        _ => Err(fail(404, "API route not found")),
    }
}

/// `body.get(key)`: Python's None for a missing key and for null alike.
fn given<'a>(body: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    body.get(key).filter(|value| !value.is_null())
}

fn place(body: &Map<String, Value>) -> Place<'_> {
    Place { page: given(body, "page"), parent: given(body, "parent"), after: given(body, "after"), before: given(body, "before"), revision: given(body, "revision") }
}

fn post(conn: &Connection, route: &str, body: &Map<String, Value>) -> Done<Option<Value>> {
    let null = Value::Null;
    let field = |key: &str| body.get(key).unwrap_or(&null);
    let answer = match route {
        "history/restore" => {
            if !only(body, &["page", "type", "number", "seq"]) {
                return Err(refuse("Unexpected restore fields"));
            }
            let page_id = page(conn, body)?;
            history::restore(conn, &page_id, sequence(body.value("seq"))?)?
        }
        "sessions/undo" => {
            fields(body, &["session", "dry_run"], &["session"])?;
            sessions::undo_session(conn, field("session"), body.get("dry_run").unwrap_or(&Value::Bool(false)))?
        }
        "trash/restore" => {
            if body.len() != 1 || !body.contains_key("id") {
                return Err(refuse("Restore needs the block id from Trash"));
            }
            history::restore_from_trash(conn, field("id"))?
        }
        "pages/create" => {
            fields(body, &["title", "notebook", "blocks"], &["title"])?;
            api::create_page(conn, field("title"), given(body, "notebook"), given(body, "blocks"))?
        }
        "insert" => {
            let mut allowed = vec!["blocks"];
            allowed.extend(PLACE);
            fields(body, &allowed, &["blocks"])?;
            api::insert_blocks(conn, field("blocks"), &place(body))?
        }
        "update" => {
            fields(body, &["id", "text", "type", "props", "revision"], &["id"])?;
            api::update_block(conn, field("id"), given(body, "text"), given(body, "type"), given(body, "props"), given(body, "revision"))?
        }
        "move" => {
            let mut allowed = vec!["id"];
            allowed.extend(PLACE);
            fields(body, &allowed, &["id"])?;
            api::move_block(conn, field("id"), &place(body))?
        }
        "delete" => {
            fields(body, &["id", "revision"], &["id"])?;
            api::delete_block(conn, field("id"), given(body, "revision"))?
        }
        "inbox" => {
            fields(body, &["blocks"], &["blocks"])?;
            api::add_to_inbox(conn, field("blocks"))?
        }
        _ => return Ok(None),
    };
    Ok(Some(answer))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_numbers_are_read_as_the_handler_reads_them() {
        assert!(is_number("1") && is_number("9999999999999999") && !is_number("01") && !is_number("10000000000000000"));
        assert_eq!(sequence(Some(Value::String("007".into()))).unwrap(), 7);
        assert_eq!(sequence(Some(Value::Bool(true))).unwrap_err().message, "Choose a sequence number from the page's history");
        let body: Map<String, Value> = serde_json::from_str(r#"{"blocks": [], "x": 1}"#).unwrap();
        assert_eq!(fields(&body, &["blocks", "page"], &["blocks"]).unwrap_err().message, "Expected fields: blocks, page");
    }
}
