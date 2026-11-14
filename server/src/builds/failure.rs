//! An answer other than 200 from the build routes: `{"error", "problems"?}`, as
//! `RequestHandler._dispatch` sends an `APIError`, a `BuildStoreError` or its catch-alls.

use super::document::{Problem, Raised};
use super::value::lossy;

/// Debug prints the status and message; tests unwrap results that carry it.
impl std::fmt::Debug for Failure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Failure({}, {:?})", self.status, self.message)
    }
}

pub struct Failure {
    pub status: u16,
    pub message: String,
    pub problems: Vec<Problem>,
}

impl Failure {
    pub fn new(status: u16, message: impl Into<String>) -> Failure {
        Failure { status, message: message.into(), problems: Vec::new() }
    }

    pub fn with(status: u16, message: impl Into<String>, problems: Vec<Problem>) -> Failure {
        Failure { status, message: message.into(), problems }
    }

    /// Python's catch-all for an exception no handler turned into an answer.
    pub fn internal() -> Failure {
        Failure::new(500, "Local operation failed; inspect CLI diagnostics")
    }

    /// Python's answer to `sqlite3.OperationalError` and `TimeoutError`.
    pub fn busy() -> Failure {
        Failure::new(503, "Local workspace is busy or unavailable; try again")
    }
}

impl From<rusqlite::Error> for Failure {
    fn from(error: rusqlite::Error) -> Failure {
        use rusqlite::ErrorCode::*;
        match &error {
            rusqlite::Error::SqliteFailure(failure, _) if matches!(failure.code,
                Unknown | PermissionDenied | OperationAborted | DatabaseBusy | DatabaseLocked | ReadOnly
                | OperationInterrupted | SystemIoFailure | DiskFull | CannotOpen | FileLockingProtocolFailed | SchemaChanged) =>
                Failure::busy(),
            _ => Failure::internal(),
        }
    }
}

impl From<std::io::Error> for Failure {
    fn from(_: std::io::Error) -> Failure {
        Failure::internal()
    }
}

/// Python raises (RecursionError, ValueError) where the reader returns `Raised`: a 500.
impl From<Raised> for Failure {
    fn from(_: Raised) -> Failure {
        Failure::internal()
    }
}

/// `str(Problem)`.
pub fn problem_text(p: &Problem) -> String {
    let path = lossy(&p.path);
    let place = if path.is_empty() { String::new() } else { format!(" ({path})") };
    format!("line {}, column {}{place}: {}", p.line, p.column, lossy(&p.message))
}

/// `Checked.errors[0]` as text.
pub fn first_error(problems: &[Problem]) -> String {
    problems.iter().find(|p| p.severity == "error").map(problem_text).unwrap_or_default()
}
