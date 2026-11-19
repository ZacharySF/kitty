//! `/api/flashcards*`: local Anki-compatible notes and FSRS-6 review scheduling, answered here
//! over rusqlite (`kitty.web_flashcards` and `kitty.core.flashcards` in Python).
//!
//! Only allowlisted formatting and stored media reach the review surface; fronts are served
//! separately from answers; practice reviews never change regular schedules.

mod anki;
mod entities;
mod html;
mod kit;
pub mod pyjson;
pub mod pytime;
mod render;
mod review;
pub mod scheduler;
mod store;

use std::sync::LazyLock;

use base64::Engine;
use regex::Regex;
use rusqlite::types::Value as Sql;
use rusqlite::{params_from_iter, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use self::kit::{bad, fail, sqlite_message, Call};
use crate::modules::Failure;
use crate::ported::{Done, Fault, Group};

pub const PREFIX: &str = "/api/flashcards";

const POSTS: &[&str] = &["deck", "type", "note", "action", "review", "reveal", "grade", "undo", "media", "import", "export"];

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

/// The shared kit's entry (crate::ported): the module guard, the connection and the content
/// event are its; the handlers below keep Python's own error answers.
fn answer(ctx: &crate::ported::Ctx, call: &crate::ported::Call) -> Done<Option<Value>> {
    let own = Call { conn: ctx.conn, path: call.path, query: call.query, body: call.body() };
    let outcome = if call.mutation() { post(&own) } else { get(&own) };
    if !ctx.conn.is_autocommit() {
        // A handler that failed inside its transaction leaves nothing behind.
        let _ = ctx.conn.execute_batch("ROLLBACK");
    }
    outcome.map(Some).map_err(|failure| Fault { status: failure.status, message: failure.message })
}

fn owns(path: &str, _mutation: bool) -> bool {
    path == PREFIX || path.starts_with("/api/flashcards/")
}

/// What the Python core raised.
#[derive(Debug)]
pub enum Error {
    /// A `ValueError` (or the `TypeError`/`KeyError` an invalid value caused): its message.
    Value(String),
    Sql(rusqlite::Error),
    /// Anything Python had no handler for.
    Internal,
}

impl From<rusqlite::Error> for Error {
    fn from(error: rusqlite::Error) -> Self {
        Error::Sql(error)
    }
}

pub type R<T> = Result<T, Error>;

pub fn value_error(message: impl Into<String>) -> Error {
    Error::Value(message.into())
}

/// `web_flashcards.get`'s handler: `ValueError`, `TypeError` and `IntegrityError` are 400s.
fn get_failure(error: Error) -> Failure {
    match error {
        Error::Value(message) => bad(message),
        Error::Sql(error) if matches!(&error, rusqlite::Error::SqliteFailure(failure, _) if failure.code == rusqlite::ErrorCode::ConstraintViolation) => bad(sqlite_message(&error)),
        Error::Sql(error) => kit::database(error),
        Error::Internal => kit::internal(),
    }
}

/// `web_flashcards.post`'s handler: every `sqlite3.DatabaseError` is a 400 too.
fn post_failure(error: Error) -> Failure {
    match error {
        Error::Value(message) if message.is_empty() => bad("Invalid flashcard data"),
        Error::Value(message) => bad(message),
        Error::Sql(error) => bad(sqlite_message(&error)),
        Error::Internal => kit::internal(),
    }
}

// ------------------------------------------------------------------ Python values

/// `bool(value)`.
pub fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(flag)) => *flag,
        Some(Value::Number(number)) => number.as_f64() != Some(0.0),
        Some(Value::String(text)) => !text.is_empty(),
        Some(Value::Array(items)) => !items.is_empty(),
        Some(Value::Object(map)) => !map.is_empty(),
    }
}

/// `str(value)` of a JSON value.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(flag) => if *flag { "True".into() } else { "False".into() },
        Value::Number(number) if number.is_f64() => pyjson::float(number.as_f64().unwrap_or(0.0)),
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.clone(),
        other => pyjson::dumps(other).replace("true", "True").replace("false", "False").replace("null", "None"),
    }
}

/// `stored == value` for a stored integer and a JSON value (Python's numeric equality).
pub fn same_int(stored: &Value, value: Option<&Value>) -> bool {
    let Some(stored) = stored.as_i64() else { return false };
    match value {
        Some(Value::Number(number)) => number.as_i64() == Some(stored) || number.as_f64() == Some(stored as f64),
        Some(Value::Bool(flag)) => i64::from(*flag) == stored,
        _ => false,
    }
}

/// A JSON value bound as `sqlite3` binds the Python value.
pub fn sql(value: &Value) -> R<Sql> {
    Ok(match value {
        Value::Null => Sql::Null,
        Value::Bool(flag) => Sql::Integer(i64::from(*flag)),
        Value::Number(number) => match number.as_i64() {
            Some(integer) => Sql::Integer(integer),
            None => Sql::Real(number.as_f64().unwrap_or(0.0)),
        },
        Value::String(text) => Sql::Text(text.clone()),
        Value::Array(_) => return Err(value_error("Error binding parameter 1: type 'list' is not supported")),
        Value::Object(_) => return Err(value_error("Error binding parameter 1: type 'dict' is not supported")),
    })
}

fn column(value: rusqlite::types::ValueRef) -> Value {
    use rusqlite::types::ValueRef;
    match value {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(integer) => Value::from(integer),
        ValueRef::Real(real) => pyjson::number(real),
        ValueRef::Text(text) => Value::String(String::from_utf8_lossy(text).into_owned()),
        ValueRef::Blob(_) => Value::Null,
    }
}

/// `[dict(row) for row in conn.execute(sql, args)]`.
pub fn rows(conn: &Connection, query: &str, args: &[Sql]) -> R<Vec<Map<String, Value>>> {
    let mut statement = conn.prepare(query)?;
    let names: Vec<String> = statement.column_names().into_iter().map(|name| name.to_string()).collect();
    let mut found = statement.query(params_from_iter(args.iter()))?;
    let mut result = Vec::new();
    while let Some(row) = found.next()? {
        let mut map = Map::new();
        for (index, name) in names.iter().enumerate() {
            map.insert(name.clone(), column(row.get_ref(index)?));
        }
        result.push(map);
    }
    Ok(result)
}

pub fn first(conn: &Connection, query: &str, args: &[Sql]) -> R<Option<Map<String, Value>>> {
    Ok(rows(conn, query, args)?.into_iter().next())
}

/// One integer column of the first row, if any.
pub fn scalar(conn: &Connection, query: &str, args: &[Sql]) -> R<Option<i64>> {
    Ok(conn.query_row(query, params_from_iter(args.iter()), |row| row.get::<_, Option<i64>>(0)).optional()?.flatten())
}

/// `require(conn, table, ident)`.
pub fn require(conn: &Connection, table: &str, ident: &Value) -> R<Map<String, Value>> {
    let query = match table {
        "flash_decks" => "SELECT * FROM flash_decks WHERE id=?",
        "flash_types" => "SELECT * FROM flash_types WHERE id=?",
        "flash_notes" => "SELECT * FROM flash_notes WHERE id=?",
        "flash_cards" => "SELECT * FROM flash_cards WHERE id=?",
        _ => return Err(value_error("Invalid entity")),
    };
    first(conn, query, &[sql(ident)?])?.ok_or_else(|| value_error("Flashcard item not found"))
}

/// `with transaction(conn)`: one `BEGIN ... COMMIT`, rolled back on any failure.
pub fn transaction<T>(conn: &Connection, work: impl FnOnce() -> R<T>) -> R<T> {
    conn.execute_batch("BEGIN")?;
    match work() {
        Ok(value) => {
            conn.execute_batch("COMMIT")?;
            Ok(value)
        }
        Err(error) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(error)
        }
    }
}

/// `json.loads` of a stored JSON column.
pub fn loads(value: &Value) -> R<Value> {
    serde_json::from_str(value.as_str().ok_or(Error::Internal)?).map_err(|_| Error::Internal)
}

/// `int(text)` for a query parameter.
fn py_int(text: &str) -> R<i64> {
    let trimmed = text.trim();
    let digits = trimmed.strip_prefix(['+', '-']).unwrap_or(trimmed);
    let valid = !digits.is_empty() && !digits.starts_with('_') && !digits.ends_with('_') && !digits.contains("__")
        && digits.chars().all(|c| c.is_ascii_digit() || c == '_');
    let cleaned: String = trimmed.chars().filter(|c| *c != '_').collect();
    match (valid, cleaned.parse::<i64>()) {
        (true, Ok(number)) => Ok(number),
        _ => Err(value_error(format!("invalid literal for int() with base 10: '{text}'"))),
    }
}

static ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[1-9][0-9]{0,17}$").expect("a valid pattern"));

/// `web_flashcards._id`.
fn ident(value: Option<&Value>, optional: bool) -> R<Option<i64>> {
    let value = value.unwrap_or(&Value::Null);
    let empty = match value {
        Value::Null => true,
        Value::String(text) => text.is_empty(),
        Value::Number(number) => number.as_f64() == Some(0.0),
        Value::Bool(flag) => !flag,
        _ => false,
    };
    if optional && empty {
        return Ok(None);
    }
    if value.is_boolean() || !ID.is_match(&py_str(value)) {
        return Err(value_error("Expected a positive item ID"));
    }
    py_str(value).parse().map(Some).map_err(|_| value_error("Expected a positive item ID"))
}

fn query_ident(call: &Call, name: &str, optional: bool) -> R<Option<i64>> {
    ident(call.param(name).map(|text| Value::String(text.to_owned())).as_ref(), optional)
}

/// `_decode`: strict base64.
fn decode(value: Option<&Value>) -> R<Vec<u8>> {
    let Some(Value::String(text)) = value else { return Err(value_error("Expected base64 file content")) };
    let engine = base64::engine::GeneralPurpose::new(&base64::alphabet::STANDARD,
        base64::engine::GeneralPurposeConfig::new().with_decode_allow_trailing_bits(true));
    engine.decode(text).map_err(|_| value_error("Invalid base64 file content"))
}

fn not_found() -> Failure {
    fail(404, "API route not found")
}

// ------------------------------------------------------------------ routes

fn get(call: &Call) -> Result<Value, Failure> {
    let conn = call.conn;
    let answer = (|| -> R<Option<Value>> {
        store::bootstrap(conn)?;
        match call.path {
            PREFIX => {
                if !call.query.is_empty() {
                    return Err(value_error("Overview takes no parameters"));
                }
                review::overview(conn).map(Some)
            }
            "/api/flashcards/browser" => {
                if !call.only(&["deck_id", "search", "limit", "offset"]) {
                    return Err(value_error("Unknown browser parameter"));
                }
                let limit = py_int(call.param("limit").unwrap_or("100"))?;
                let offset = py_int(call.param("offset").unwrap_or("0"))?;
                if !(1..=500).contains(&limit) || !(0..=100_000).contains(&offset) {
                    return Err(value_error("Invalid browser page"));
                }
                let deck = query_ident(call, "deck_id", true)?;
                review::browser(conn, deck, call.param("search").unwrap_or(""), limit as usize, offset as usize).map(Some)
            }
            "/api/flashcards/note" => {
                if call.query.len() != 1 || call.param("id").is_none() {
                    return Err(value_error("An exact note ID is required"));
                }
                let id = query_ident(call, "id", false)?.expect("a required ID");
                review::exact_note(conn, id).map(Some)
            }
            "/api/flashcards/history" => {
                if !call.only(&["card_id"]) {
                    return Err(value_error("Unknown history parameter"));
                }
                review::history(conn, query_ident(call, "card_id", true)?).map(Some)
            }
            "/api/flashcards/card" => {
                if call.query.len() != 1 || call.param("id").is_none() {
                    return Err(value_error("An exact card ID is required"));
                }
                let id = query_ident(call, "id", false)?.expect("a required ID");
                review::card_detail(conn, id).map(Some)
            }
            "/api/flashcards/fronts" => {
                if call.query.len() != 1 || call.param("deck_id").is_none() {
                    return Err(value_error("A deck ID is required"));
                }
                let deck = query_ident(call, "deck_id", false)?.expect("a required ID");
                Ok(Some(json!({ "fronts": store::fronts(conn, deck)? })))
            }
            "/api/flashcards/stats" => {
                if !call.only(&["deck_id", "timezone"]) {
                    return Err(value_error("Unknown stats parameter"));
                }
                let zone = call.param("timezone").unwrap_or("UTC");
                if pytime::zone(zone).is_none() {
                    return Err(value_error("Unknown IANA timezone"));
                }
                let deck = query_ident(call, "deck_id", true)?;
                if let Some(deck) = deck {
                    require(conn, "flash_decks", &Value::from(deck))?;
                }
                review::stats(conn, deck, zone).map(Some)
            }
            _ => Ok(None),
        }
    })();
    match answer {
        Ok(Some(value)) => Ok(value),
        Ok(None) => Err(not_found()),
        Err(Error::Value(message)) if message == review::GONE_NOTE || message == review::GONE_CARD => Err(fail(404, message)),
        Err(error) => Err(get_failure(error)),
    }
}

fn post(call: &Call) -> Result<Value, Failure> {
    let operation = call.path.strip_prefix("/api/flashcards/").unwrap_or("");
    if !call.path.starts_with("/api/flashcards/") || !POSTS.contains(&operation) {
        return Err(not_found());
    }
    let conn = call.conn;
    let body = call.body;
    let answer = (|| -> R<Value> {
        store::bootstrap(conn)?;
        match operation {
            "deck" => Ok(json!({ "deck": store::save_deck(conn, body)? })),
            "type" => Ok(json!({ "type": store::save_type(conn, body)? })),
            "note" => Ok(json!({ "note": store::save_note(conn, body)? })),
            "action" => store::action(conn, body),
            "review" => {
                let study = body.get("study_id");
                static STUDY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[a-f0-9]{48}$").expect("a valid pattern"));
                let study = match study {
                    None | Some(Value::Null) => None,
                    Some(Value::String(text)) if STUDY.is_match(text) => Some(text.clone()),
                    Some(_) => return Err(value_error("Invalid practice session ID")),
                };
                let deck = ident(body.get("deck_id"), true)?;
                let search = body.get("search").map(py_str).unwrap_or_default();
                let mode = body.get("mode").cloned().unwrap_or_else(|| json!("regular"));
                review::start_review(conn, deck, &search, &mode, study)
            }
            "reveal" => review::reveal(conn, &body.get("token").map(py_str).unwrap_or_default()),
            "grade" => review::grade(conn, &body.get("token").map(py_str).unwrap_or_default(), body.get("rating"),
                                     body.get("duration_ms").unwrap_or(&json!(0))),
            "undo" => {
                if !body.is_empty() {
                    return Err(value_error("Undo expects an empty object"));
                }
                review::undo(conn)
            }
            "media" => {
                let data = decode(body.get("data"))?;
                transaction(conn, || store::store_media(conn, body.get("name"), &data))
            }
            "import" => match body.get("format").and_then(Value::as_str) {
                Some("apkg") => anki::import_apkg(conn, &decode(body.get("data"))?),
                Some(kind @ ("csv" | "tsv")) => {
                    let Some(Value::String(text)) = body.get("text") else { return Err(value_error("Expected CSV/TSV text")) };
                    let deck = ident(body.get("deck_id"), false)?.expect("a required ID");
                    let kind_id = ident(body.get("type_id"), false)?.expect("a required ID");
                    anki::import_text(conn, text, deck, kind_id, if kind == "tsv" { '\t' } else { ',' })
                }
                _ => Err(value_error("Supported formats: apkg, csv, tsv")),
            },
            "export" => {
                let kind = body.get("format").and_then(Value::as_str).map(str::to_owned);
                let deck = ident(body.get("deck_id"), true)?;
                let (data, mime) = match kind.as_deref() {
                    Some("apkg") => (anki::export_apkg(conn, deck)?, "application/octet-stream"),
                    Some(kind @ ("csv" | "tsv")) => (anki::export_text(conn, deck, if kind == "tsv" { '\t' } else { ',' })?, "text/plain;charset=utf-8"),
                    _ => return Err(value_error("Supported formats: apkg, csv, tsv")),
                };
                Ok(json!({
                    "data": base64::engine::general_purpose::STANDARD.encode(data),
                    "mime": mime,
                    "filename": format!("kitty-flashcards.{}", kind.unwrap_or_default()),
                }))
            }
            _ => Err(Error::Internal),
        }
    })();
    answer.map_err(post_failure)
}
