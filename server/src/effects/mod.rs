//! rika!'s pending-approvals list, answered by the front:
//!
//! ```text
//! GET  /api/effects            {pending: [effect], recent: [effect]}
//! POST /api/effects/approve    {id, digest} -> {effect, notice, pending, recent}
//! POST /api/effects/execute    {id, digest} -> {effect, result, pending, recent}; 400 for a kind with no executor
//! POST /api/effects/deny       {id} -> {effect, pending, recent}
//! ```
//!
//! Agents and paired programs propose (MCP, `kitty effects propose ...`, the agent mediator);
//! only these routes, behind the UI secret, decide, as the person at this launch's window
//! session: `"ui-" + token_hex(12)`, made by the front at start and sent to the worker as its
//! second stdin line, so approvals made here and in Python's inline views (`app.ui_session`)
//! belong to the same session. The routes own only the `prompt` kinds (`delete_block`,
//! `submit_application`); any other effect is refused untouched (403).
//!
//! The kernel protocol (crate::effects::kernel) runs here over the effect tables, with
//! `kitty.core.effects` as the reference: approving re-checks every precondition inside one
//! immediate transaction and records a single-use, two-minute approval of the exact digest;
//! a changed precondition or digest refuses the effect for good (409, the changed names as
//! `problems`). `submit_application`'s preconditions need Python (`choose_resume` and the
//! config): the worker reads them on an internal route while the front holds the claim.
//! Executing runs on the worker (`POST /api/internal/effects/execute`), where the final
//! re-check, the `executed` event and the block store's delete (as the proposing actor) share
//! one transaction; the front checks the request, the kind and that it has an executor first,
//! and renders the answer itself. Pages never reach `/api/internal/effects*`.

mod canonical;
mod internal;
mod kernel;

use std::convert::Infallible;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use bytes::Bytes;
use http_body_util::{BodyExt, Empty, Full, Limited};
use hyper::body::Incoming;
use hyper::header::{HeaderMap, HeaderValue};
use hyper::{Method, Request, Response, StatusCode};
use rusqlite::{Connection, OpenFlags};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{json, Map, Value};

use crate::front::{Body, Front, MAX_BODY};
use crate::target::{parse_target, Target};
use kernel::Kind;

pub const PATH: &str = "/api/effects";
const APPROVE: &str = "/api/effects/approve";
const EXECUTE: &str = "/api/effects/execute";
const DENY: &str = "/api/effects/deny";
/// The worker's side of these routes (`kitty.web_effects_worker`); never a page's.
const INTERNAL: &str = "/api/internal/effects";
const CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src http://127.0.0.1:3210; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

/// The database the worker opened and this launch's window session.
struct Effects {
    db_path: PathBuf,
    session: String,
    /// Kept open between answers, as crate::bootstrap does.
    connection: Mutex<Option<Connection>>,
}

static STATE: OnceLock<Effects> = OnceLock::new();

/// Start-up: the workspace database and the window session the worker was given.
pub fn start(db_path: PathBuf, session: String) {
    let _ = STATE.set(Effects { db_path, session, connection: Mutex::new(None) });
}

/// An error answer as Python's `APIError`: status, message and the changed preconditions.
pub struct Failure {
    status: u16,
    message: String,
    changed: Vec<String>,
}

fn fail(status: u16, message: impl Into<String>) -> Failure {
    Failure { status, message: message.into(), changed: Vec::new() }
}

/// What Python answered for an exception it had no handler for.
fn internal_error() -> Failure {
    fail(500, "Local operation failed; inspect CLI diagnostics")
}

/// Python's answer to a `sqlite3.OperationalError` (busy, locked, unreadable) and to anything else.
fn database(error: rusqlite::Error) -> Failure {
    use rusqlite::ErrorCode::*;
    match &error {
        rusqlite::Error::SqliteFailure(failure, _) if matches!(failure.code,
            Unknown | PermissionDenied | OperationAborted | DatabaseBusy | DatabaseLocked | ReadOnly
            | OperationInterrupted | SystemIoFailure | DiskFull | CannotOpen | FileLockingProtocolFailed | SchemaChanged) =>
            fail(503, "Local workspace is busy or unavailable; try again"),
        _ => internal_error(),
    }
}

impl Effects {
    /// Run `work` on the held connection (opened as `kitty.db.connect` opens one); a
    /// connection that failed is not trusted for the next answer.
    fn with<T>(&self, work: impl FnOnce(&Connection) -> Result<T, Failure>) -> Result<T, Failure> {
        let mut held = self.connection.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if held.is_none() {
            let conn = Connection::open_with_flags(&self.db_path, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX)
                .map_err(database)?;
            conn.busy_timeout(std::time::Duration::from_millis(5000)).map_err(database)?;
            conn.execute_batch("PRAGMA foreign_keys = ON").map_err(database)?;
            *held = Some(conn);
        }
        let outcome = work(held.as_ref().expect("opened above"));
        if outcome.as_ref().is_err_and(|failure| failure.status >= 500) {
            *held = None;
        }
        outcome
    }
}

enum Action {
    List,
    Approve,
    Execute,
    Deny,
}

/// The answer for the pending-approvals routes and the refusal of the internal ones; None
/// for any other request (the front forwards it).
pub async fn route(front: &Front, target: &Target, forward_target: &str, request: &mut Request<Incoming>,
                   mutation: bool, upgrade: bool) -> Option<Response<Body>> {
    let head = request.method() == Method::HEAD;
    if target.path.starts_with(INTERNAL) {
        return Some(failure(fail(404, "API route not found"), head));
    }
    if upgrade {
        return None;
    }
    let action = match (mutation, target.path.as_str()) {
        (false, PATH) => Action::List,
        (true, APPROVE) => Action::Approve,
        (true, EXECUTE) => Action::Execute,
        (true, DENY) => Action::Deny,
        _ => return None,
    };
    let outcome = match STATE.get() {
        None => Err(internal_error()),
        Some(state) => match action {
            Action::List => list(state, forward_target),
            _ => match body(target, request).await {
                Ok(body) => decide(front, state, action, body).await,
                Err(failure) => Err(failure),
            },
        },
    };
    Some(match outcome {
        Ok(answer) => send(StatusCode::OK, serde_json::to_vec(&answer).expect("JSON values serialize"), head),
        Err(refusal) => failure(refusal, head),
    })
}

fn list(state: &Effects, forward_target: &str) -> Result<Value, Failure> {
    // The query as the worker would have seen it: a spent link ticket removed.
    let target = parse_target(forward_target).map_err(|invalid| fail(invalid.0, invalid.1))?;
    if !target.query.is_empty() {
        return Err(fail(400, "This route does not accept query parameters"));
    }
    tokio::task::block_in_place(|| state.with(|conn| Ok(Value::Object(kernel::listing(conn, kernel::now())?))))
}

/// Only the route's own fields (`Unknown effect request field`).
fn keys(body: &Map<String, Value>, allowed: &[&str]) -> Result<(), Failure> {
    if body.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err(fail(400, "Unknown effect request field"));
    }
    Ok(())
}

/// The answer to a decision: `{effect, ...extra, pending, recent}`.
fn decided(conn: &Connection, id: &str, extra: Vec<(&str, Value)>) -> Result<Value, Failure> {
    let effect = kernel::load(conn, Some(&Value::String(id.to_owned())))?;
    let mut answer = Map::new();
    answer.insert("effect".into(), kernel::view(conn, &effect)?);
    for (name, value) in extra {
        answer.insert(name.into(), value);
    }
    answer.extend(kernel::listing(conn, kernel::now())?);
    Ok(Value::Object(answer))
}

async fn decide(front: &Front, state: &Effects, action: Action, body: Map<String, Value>) -> Result<Value, Failure> {
    let session = state.session.as_str();
    match action {
        Action::Deny => {
            keys(&body, &["id"])?;
            tokio::task::block_in_place(|| state.with(|conn| {
                let effect = kernel::deny(conn, body.get("id"), session, kernel::now())?;
                decided(conn, &effect.id, Vec::new())
            }))
        }
        Action::Approve => {
            keys(&body, &["id", "digest"])?;
            tokio::task::block_in_place(|| state.with(|conn| {
                let moment = kernel::now();
                let effect = kernel::open(conn, body.get("id"))?;
                let kind = effect.owned_kind();
                kernel::approve(conn, &effect.id, body.get("digest"), session, moment, |conn, effect| match kind {
                    Kind::DeleteBlock => kernel::delete_preconditions(conn, &effect.payload),
                    // Read by the worker while this claim holds the write lock; it only reads.
                    Kind::SubmitApplication => {
                        let path = format!("{INTERNAL}/preconditions");
                        let answer = tokio::runtime::Handle::current()
                            .block_on(internal::call(&front.backend, &path, &json!({ "id": effect.id })))?;
                        answer.get("preconditions").cloned().ok_or_else(internal_error)
                    }
                })?;
                decided(conn, &effect.id, vec![("notice", json!(kind.handoff()))])
            }))
        }
        Action::Execute => {
            keys(&body, &["id", "digest"])?;
            let effect = tokio::task::block_in_place(|| state.with(|conn| kernel::open(conn, body.get("id"))))?;
            let kind = effect.owned_kind();
            if !kind.executable() {
                return Err(fail(400, format!("{} has no executor: {}", kind.name(), kind.handoff().unwrap_or("it cannot be executed"))));
            }
            let presented = body.get("digest").cloned().unwrap_or(Value::Null);
            let path = format!("{INTERNAL}/execute");
            let done = internal::call(&front.backend, &path, &json!({ "id": effect.id, "digest": presented })).await?;
            let result = done.get("result").cloned().unwrap_or(Value::Null);
            tokio::task::block_in_place(|| state.with(|conn| decided(conn, &effect.id, vec![("result", result)])))
        }
        Action::List => unreachable!("listing is not a decision"),
    }
}

// ------------------------------------------------------------------------------ HTTP
// The front's response and body rules (crate::front), for this module's own answers.

fn full(data: impl Into<Bytes>) -> Body {
    Full::new(data.into()).map_err(|never: Infallible| match never {}).boxed()
}

fn empty() -> Body {
    Empty::new().map_err(|never: Infallible| match never {}).boxed()
}

/// `RequestHandler._send`: the same security headers on everything the front answers.
fn send(status: StatusCode, data: Vec<u8>, head: bool) -> Response<Body> {
    let length = data.len();
    let mut response = Response::new(if head { empty() } else { full(data) });
    *response.status_mut() = status;
    let headers = response.headers_mut();
    for (name, value) in [
        ("content-type", "application/json; charset=utf-8"),
        ("cache-control", "no-store"),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "no-referrer"),
        ("cross-origin-resource-policy", "same-origin"),
        ("content-security-policy", CSP),
        ("server", "rika!"),
    ] {
        headers.insert(name, HeaderValue::from_static(value));
    }
    headers.insert("content-length", HeaderValue::from(length));
    response
}

/// An error answer; like Python's, it closes the connection.
fn failure(refusal: Failure, head: bool) -> Response<Body> {
    let mut answer = json!({ "error": refusal.message });
    if !refusal.changed.is_empty() {
        let problems: Vec<Value> = refusal.changed.iter().map(|name| json!({ "precondition": name })).collect();
        answer["problems"] = Value::Array(problems);
    }
    let status = StatusCode::from_u16(refusal.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let mut response = send(status, serde_json::to_vec(&answer).expect("JSON values serialize"), head);
    response.headers_mut().insert("connection", HeaderValue::from_static("close"));
    response
}

/// A mutation's JSON object body, after `_dispatch`'s query check (`RequestHandler._body`).
async fn body(target: &Target, request: &mut Request<Incoming>) -> Result<Map<String, Value>, Failure> {
    if !target.query.is_empty() {
        return Err(fail(400, "This route does not accept query parameters"));
    }
    let length = content_length(request.headers())?;
    let collected = Limited::new(request.body_mut(), length as usize)
        .collect()
        .await
        .map_err(|_| fail(400, "Incomplete JSON body"))?
        .to_bytes();
    if collected.len() as u64 != length {
        return Err(fail(400, "Incomplete JSON body"));
    }
    match serde_json::from_slice::<Strict>(&collected) {
        Ok(Strict(Value::Object(object))) => Ok(object),
        Ok(_) => Err(fail(400, "JSON body must be an object")),
        Err(_) => Err(fail(400, "Malformed JSON body")),
    }
}

/// `RequestHandler._body`'s framing rules: one Content-Length, no Transfer-Encoding.
fn content_length(headers: &HeaderMap) -> Result<u64, Failure> {
    let lengths: Vec<&str> = headers.get_all("content-length").iter().map(|value| value.to_str().unwrap_or("\u{fffd}")).collect();
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
    Ok(length)
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
