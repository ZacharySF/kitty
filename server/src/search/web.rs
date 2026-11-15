//! What the routes answered by the search, blocks, notes and documents modules share: the
//! workspace database, Python's error answers, and `RequestHandler._body`'s JSON rules.
//!
//! The front (crate::front) has already applied the page and secret checks; a route here reads
//! its request and answers a JSON value or a `Failure` the front sends as `{"error"}`.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use bytes::Bytes;
use http_body_util::{BodyExt, Limited};
use hyper::body::Incoming;
use hyper::header::HeaderMap;
use rusqlite::{Connection, OpenFlags};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value};

pub use crate::modules::Failure;
use crate::target::{parse_target, Target};

/// `web_server.MAX_BODY`.
const MAX_BODY: u64 = 20 * 1024 * 1024;

pub fn fail(status: u16, message: impl Into<String>) -> Failure {
    Failure { status, message: message.into() }
}

pub fn internal() -> Failure {
    fail(500, "Local operation failed; inspect CLI diagnostics")
}

/// Python's answer to a `sqlite3.OperationalError` (busy, locked, unreadable, an SQL error) and to anything else.
pub fn database(error: &rusqlite::Error) -> Failure {
    use rusqlite::ErrorCode::*;
    match error {
        rusqlite::Error::SqliteFailure(failure, _) if matches!(failure.code,
            Unknown | PermissionDenied | OperationAborted | DatabaseBusy | DatabaseLocked | ReadOnly
            | OperationInterrupted | SystemIoFailure | DiskFull | CannotOpen | FileLockingProtocolFailed | SchemaChanged) =>
            fail(503, "Local workspace is busy or unavailable; try again"),
        rusqlite::Error::SqlInputError { .. } => fail(503, "Local workspace is busy or unavailable; try again"),
        _ => internal(),
    }
}

impl From<super::docs::Failure> for Failure {
    fn from(failure: super::docs::Failure) -> Self {
        match failure {
            super::docs::Failure::Database(error) => database(&error),
            super::docs::Failure::Data(_) => internal(),
        }
    }
}

/// The workspace database, opened as `kitty.db.connect` opens it and kept open between answers
/// (opening one parses the whole schema). Writers take SQLite's lock (`BEGIN IMMEDIATE`), which
/// the worker's writers wait for with the same 5 s busy timeout.
pub struct Database {
    path: PathBuf,
    connection: Mutex<Option<Connection>>,
}

static DATABASE: OnceLock<Database> = OnceLock::new();

/// The one database of this front (the worker's, which the front migrated).
pub fn workspace(path: &Path) -> &'static Database {
    DATABASE.get_or_init(|| Database { path: path.to_owned(), connection: Mutex::new(None) })
}

impl Database {
    /// Run `body` on the connection; a connection that failed is dropped and reopened next time.
    pub fn with<T>(&self, body: impl FnOnce(&Connection) -> Result<T, Failure>) -> Result<T, Failure> {
        let mut held = self.connection.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if held.is_none() {
            *held = Some(open(&self.path).map_err(|error| database(&error))?);
        }
        let conn = held.as_ref().expect("opened above");
        let outcome = body(conn);
        if !conn.is_autocommit() {
            // A body that failed inside its own transaction never leaves it open.
            let _ = conn.execute_batch("ROLLBACK");
        }
        outcome
    }
}

pub fn open(path: &Path) -> rusqlite::Result<Connection> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    conn.busy_timeout(Duration::from_millis(5000))?;
    conn.execute_batch("PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;")?;
    Ok(conn)
}

/// The query the worker would have seen (a spent link ticket removed).
pub fn query_of(forward_target: &str) -> Result<Target, Failure> {
    parse_target(forward_target).map_err(|invalid| fail(invalid.0, invalid.1))
}

/// `WebApplication._no_query`.
pub fn no_query(target: &Target) -> Result<(), Failure> {
    if target.query.is_empty() { Ok(()) } else { Err(fail(400, "This route does not accept query parameters")) }
}

/// `WebApplication.guard`: 403 unless `module` is active (`ModuleDisabledError`).
pub fn require_module(active: &[String], module: &str) -> Result<(), Failure> {
    if active.iter().any(|m| m == module) {
        return Ok(());
    }
    Err(fail(403, format!("The '{module}' module is disabled. Enable it in Settings or list it under \"modules\" in \
        config/build.json, then restart rika! (changes apply on the next start).")))
}

/// `RequestHandler._body`: one Content-Length, the whole body, a JSON object without repeated keys.
pub async fn body(headers: &HeaderMap, body: Incoming) -> Result<Map<String, Value>, Failure> {
    let lengths: Vec<&str> = headers.get_all("content-length").iter().map(|v| v.to_str().unwrap_or("\u{fffd}")).collect();
    if headers.contains_key("transfer-encoding") || lengths.len() != 1 {
        return Err(fail(400, "A single Content-Length is required"));
    }
    let value = lengths[0];
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(fail(400, "Content-Length must be an integer"));
    }
    let length: u64 = value.parse().expect("ten digits fit");
    if !(1..=MAX_BODY).contains(&length) {
        return Err(fail(400, format!("Content-Length must be between 1 and {MAX_BODY}")));
    }
    let raw: Bytes = Limited::new(body, length as usize).collect().await.map_err(|_| fail(400, "Incomplete JSON body"))?.to_bytes();
    if raw.len() as u64 != length {
        return Err(fail(400, "Incomplete JSON body"));
    }
    match serde_json::from_slice::<Strict>(&raw) {
        Ok(Strict(Value::Object(object))) => Ok(object),
        Ok(_) => Err(fail(400, "JSON body must be an object")),
        Err(_) => Err(fail(400, "Malformed JSON body")),
    }
}

/// A JSON value that refuses repeated object keys, as `_body`'s `unique_pairs` does.
struct Strict(Value);

impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct StrictVisitor;
        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = Value;
            fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
                formatter.write_str("JSON")
            }
            fn visit_bool<E>(self, value: bool) -> Result<Value, E> { Ok(Value::Bool(value)) }
            fn visit_i64<E>(self, value: i64) -> Result<Value, E> { Ok(value.into()) }
            fn visit_u64<E>(self, value: u64) -> Result<Value, E> { Ok(value.into()) }
            fn visit_f64<E>(self, value: f64) -> Result<Value, E> { Ok(value.into()) }
            fn visit_str<E>(self, value: &str) -> Result<Value, E> { Ok(value.into()) }
            fn visit_string<E>(self, value: String) -> Result<Value, E> { Ok(value.into()) }
            fn visit_unit<E>(self) -> Result<Value, E> { Ok(Value::Null) }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
                let mut items = Vec::new();
                while let Some(Strict(item)) = seq.next_element()? {
                    items.push(item);
                }
                Ok(Value::Array(items))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
                let mut object = Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if object.contains_key(&key) {
                        return Err(de::Error::custom("Repeated JSON key"));
                    }
                    let Strict(value) = map.next_value()?;
                    object.insert(key, value);
                }
                Ok(Value::Object(object))
            }
        }
        deserializer.deserialize_any(StrictVisitor).map(Strict)
    }
}
