//! What the flashcard routes need beside the shared kit (crate::ported): their handlers'
//! request shape, Python's error answers as `Failure`s, random ids and the clock.

use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::modules::Failure;

/// One request as a handler sees it.
pub struct Call<'a> {
    pub conn: &'a Connection,
    pub path: &'a str,
    /// The query as the worker would have seen it; empty on a POST.
    pub query: &'a [(String, String)],
    /// The JSON object of a POST; empty on a GET.
    pub body: &'a Map<String, Value>,
}

impl Call<'_> {
    pub fn param(&self, name: &str) -> Option<&str> {
        self.query.iter().find(|(key, _)| key == name).map(|(_, value)| value.as_str())
    }

    /// Whether every query parameter is one of `allowed`.
    pub fn only(&self, allowed: &[&str]) -> bool {
        self.query.iter().all(|(key, _)| allowed.contains(&key.as_str()))
    }
}

pub fn fail(status: u16, message: impl Into<String>) -> Failure {
    Failure { status, message: message.into() }
}

/// Python's 400 for a `ValueError` the handler turned into an answer.
pub fn bad(message: impl Into<String>) -> Failure {
    fail(400, message)
}

/// Python's catch-all for an exception no handler turned into an answer.
pub fn internal() -> Failure {
    fail(500, "Local operation failed; inspect CLI diagnostics")
}

/// Whether an SQLite error is one Python raised as `sqlite3.OperationalError`.
fn operational(error: &rusqlite::Error) -> bool {
    use rusqlite::ErrorCode::*;
    matches!(error, rusqlite::Error::SqliteFailure(failure, _) if matches!(failure.code,
        Unknown | PermissionDenied | OperationAborted | DatabaseBusy | DatabaseLocked | ReadOnly
        | OperationInterrupted | SystemIoFailure | DiskFull | CannotOpen | FileLockingProtocolFailed | SchemaChanged))
}

/// `RequestHandler._dispatch`'s answer to an `sqlite3.OperationalError` and to anything else.
pub fn database(error: rusqlite::Error) -> Failure {
    if operational(&error) { fail(503, "Local workspace is busy or unavailable; try again") } else { internal() }
}

/// `str(sqlite3.Error)`: SQLite's own message.
pub fn sqlite_message(error: &rusqlite::Error) -> String {
    match error {
        rusqlite::Error::SqliteFailure(_, Some(message)) => message.clone(),
        rusqlite::Error::SqliteFailure(failure, None) => failure.to_string(),
        other => other.to_string(),
    }
}

/// `secrets.token_hex(n)`.
pub fn token_hex(bytes: usize) -> String {
    let mut buffer = vec![0u8; bytes];
    getrandom::fill(&mut buffer).expect("the system's random source");
    hex::encode(buffer)
}

/// `uuid.uuid4().hex`.
pub fn uuid4_hex() -> String {
    let mut buffer = [0u8; 16];
    getrandom::fill(&mut buffer).expect("the system's random source");
    buffer[6] = (buffer[6] & 0x0f) | 0x40;
    buffer[8] = (buffer[8] & 0x3f) | 0x80;
    hex::encode(buffer)
}

pub mod clock {
    /// Now, in microseconds since the epoch (UTC): the shared clock (crate::ported::time), a test's included.
    pub fn now() -> i64 {
        crate::ported::time::Dt::now_utc().wall
    }
}
