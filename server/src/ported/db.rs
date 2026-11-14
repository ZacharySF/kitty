//! The workspace database as `kitty.db.connect` opens it, kept open between answers, and
//! Python's answers to what went wrong.

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags, Params, Row};
use serde_json::{Map, Value};

/// An answer other than 200, as `APIError(status, message)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Fault {
    pub status: u16,
    pub message: String,
}

pub type Done<T> = Result<T, Fault>;

pub fn fail(status: u16, message: impl Into<String>) -> Fault {
    Fault { status, message: message.into() }
}

/// `RequestHandler._dispatch`'s catch-all for an exception no handler turned into an answer.
pub fn internal() -> Fault {
    fail(500, "Local operation failed; inspect CLI diagnostics")
}

impl From<rusqlite::Error> for Fault {
    /// `sqlite3.OperationalError` (busy, locked, unreadable) is a 503; anything else a 500.
    fn from(error: rusqlite::Error) -> Self {
        use rusqlite::ErrorCode::*;
        match &error {
            rusqlite::Error::SqliteFailure(failure, _) if matches!(failure.code,
                Unknown | PermissionDenied | OperationAborted | DatabaseBusy | DatabaseLocked | ReadOnly
                | OperationInterrupted | SystemIoFailure | DiskFull | CannotOpen | FileLockingProtocolFailed | SchemaChanged) =>
                fail(503, "Local workspace is busy or unavailable; try again"),
            _ => internal(),
        }
    }
}

impl From<serde_json::Error> for Fault {
    fn from(_: serde_json::Error) -> Self {
        internal()
    }
}

impl From<super::time::OutOfRange> for Fault {
    fn from(_: super::time::OutOfRange) -> Self {
        internal()
    }
}

static HELD: Mutex<Option<(PathBuf, Connection)>> = Mutex::new(None);

fn open(path: &Path) -> Done<Connection> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    conn.busy_timeout(Duration::from_millis(5000))?;
    conn.execute_batch("PRAGMA foreign_keys = ON; PRAGMA synchronous = NORMAL;")?;
    Ok(conn)
}

/// Run one answer on the workspace connection. Answers are serialized, as Python's
/// `mutation_lock` serialized its writes; a connection that failed, or that an answer left
/// inside a transaction, is not reused.
pub fn with<T>(path: &Path, answer: impl FnOnce(&Connection) -> Done<T>) -> Done<T> {
    let mut held = HELD.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if held.as_ref().is_none_or(|(open_path, _)| open_path != path) {
        *held = Some((path.to_owned(), open(path)?));
    }
    let conn = &held.as_ref().expect("opened above").1;
    let outcome = answer(conn);
    let broken = !conn.is_autocommit() || matches!(&outcome, Err(fault) if fault.status == 503);
    if broken {
        let _ = conn.execute_batch("ROLLBACK");
        *held = None;
    }
    outcome
}

/// `kitty.db.connection.transaction`: one `BEGIN [IMMEDIATE] ... COMMIT`, rolled back on error.
pub fn transaction<T>(conn: &Connection, immediate: bool, body: impl FnOnce() -> Done<T>) -> Done<T> {
    conn.execute_batch(if immediate { "BEGIN IMMEDIATE" } else { "BEGIN" })?;
    match body() {
        Ok(value) => {
            conn.execute_batch("COMMIT")?;
            Ok(value)
        }
        Err(fault) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(fault)
        }
    }
}

/// One column as Python's `sqlite3` returns it, in JSON.
pub fn column(value: ValueRef<'_>) -> Done<Value> {
    Ok(match value {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(number) => Value::from(number),
        ValueRef::Real(number) => serde_json::Number::from_f64(number).map(Value::Number).ok_or_else(internal)?,
        ValueRef::Text(text) => Value::String(String::from_utf8(text.to_vec()).map_err(|_| internal())?),
        // `bytes` is not JSON serializable: Python's answer would have failed.
        ValueRef::Blob(_) => return Err(internal()),
    })
}

/// `dict(row)`.
pub fn row_map(row: &Row<'_>) -> Done<Map<String, Value>> {
    let statement = row.as_ref();
    let mut out = Map::new();
    for index in 0..statement.column_count() {
        let name = statement.column_name(index).map_err(|_| internal())?.to_owned();
        out.insert(name, column(row.get_ref(index)?)?);
    }
    Ok(out)
}

/// Every row of a query as `dict(row)`.
pub fn rows(conn: &Connection, sql: &str, params: impl Params) -> Done<Vec<Map<String, Value>>> {
    let mut statement = conn.prepare(sql)?;
    let mut found = statement.query(params)?;
    let mut out = Vec::new();
    while let Some(row) = found.next()? {
        out.push(row_map(row)?);
    }
    Ok(out)
}

/// The first row of a query as `dict(row)`, or None (`fetchone()`).
pub fn row(conn: &Connection, sql: &str, params: impl Params) -> Done<Option<Map<String, Value>>> {
    let mut statement = conn.prepare(sql)?;
    let mut found = statement.query(params)?;
    match found.next()? {
        Some(row) => Ok(Some(row_map(row)?)),
        None => Ok(None),
    }
}

/// Whether a query returns any row.
pub fn exists(conn: &Connection, sql: &str, params: impl Params) -> Done<bool> {
    let mut statement = conn.prepare(sql)?;
    Ok(statement.exists(params)?)
}

/// A JSON value bound as Python's `sqlite3` binds it (None, int, float, str; a bool as int).
pub fn bind(value: &Value) -> Done<rusqlite::types::Value> {
    use rusqlite::types::Value as Sql;
    Ok(match value {
        Value::Null => Sql::Null,
        Value::Bool(flag) => Sql::Integer(i64::from(*flag)),
        Value::Number(number) => match number.as_i64() {
            Some(integer) => Sql::Integer(integer),
            None => Sql::Real(number.as_f64().ok_or_else(internal)?),
        },
        Value::String(text) => Sql::Text(text.clone()),
        // Python's sqlite3 refuses to bind a list or dict.
        _ => return Err(internal()),
    })
}
