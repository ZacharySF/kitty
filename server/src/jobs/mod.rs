//! ApplyAI's local job routes, answered by the front over the workspace database: the job
//! lists and their selection, one job and its actions, the bulk actions, the tool panel, the
//! options, the pipeline, the reminders and the resume library's reads (the deleted handlers
//! of `kitty.web_jobs`, `web_resumes` and `WebApplication._applyai_get`/`_mutate_job`), with
//! the same answers, refusals, order of checks and rows.
//!
//! The YAML config stays the worker's (pydantic validates it and only it reloads it): the
//! routes read its projection through crate::jobs::config, as does bulk delete the browser
//! controller's state. Public metadata lookups, adding a posting (the shared dedupe and
//! evidence write path discovery uses), the dashboard (screening-question refresh against the
//! answers file), the profile editors (PyYAML, pydantic), resume registration, generation,
//! the coach (pypdf), decisions and downloads stay on the worker.

pub mod config;
mod dates;
mod edits;
mod filter;
mod likeness;
mod model;
mod reads;
mod resumes;

use std::path::Path;
use std::sync::Arc;

use http_body_util::{BodyExt, Limited};
use hyper::body::Incoming;
use hyper::{Method, Request, Response, StatusCode};
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::front::{content_length, json_object, refuse, send_json, Body, Front, Refusal};
use crate::ported::{db, disabled, events, fail, internal, py, Call, Done, Fault};
use crate::target::{parse_target, Target};
use config::{Automation, JobConfig};

/// What a handler reads: the database, the worker's config (when the route needs it), the
/// workspace root (resume files) and, for bulk delete, the browser controller's state.
pub struct Env<'a> {
    pub conn: &'a Connection,
    config: Option<&'a JobConfig>,
    pub root: &'a Path,
    pub automation: Option<Automation>,
}

impl Env<'_> {
    pub fn config(&self) -> Done<&JobConfig> {
        self.config.ok_or_else(internal)
    }
}

/// `[1-9][0-9]{0,17}` between `prefix` and `suffix`.
pub fn ident(path: &str, prefix: &str, suffix: &str) -> Option<i64> {
    let digits = path.strip_prefix(prefix)?.strip_suffix(suffix)?;
    let bytes = digits.as_bytes();
    (!bytes.is_empty() && bytes.len() <= 18 && bytes[0] != b'0' && bytes.iter().all(u8::is_ascii_digit)).then(|| digits.parse().ok()).flatten()
}

/// `/api/reminders/<id>[/complete|snooze|delete]`: the id and the action ("" for an edit).
pub fn reminder_route(path: &str) -> Option<(i64, &str)> {
    let rest = path.strip_prefix("/api/reminders/")?;
    let (id, action) = match rest.split_once('/') {
        Some((id, action)) if matches!(action, "complete" | "snooze" | "delete") => (id, action),
        Some(_) => return None,
        None => (rest, ""),
    };
    Some((ident(id, "", "")?, action))
}

/// Whether the front answers this request itself.
fn owns(path: &str, mutation: bool) -> bool {
    if mutation {
        matches!(path, "/api/jobs/bulk" | "/api/reminders" | "/api/reminders/restore")
            || ident(path, "/api/job-tools/", "").is_some()
            || ident(path, "/api/jobs/", "").is_some()
            || reminder_route(path).is_some()
    } else {
        matches!(path, "/api/jobs" | "/api/jobs/selection" | "/api/job-options" | "/api/pipeline" | "/api/reminders"
                       | "/api/resumes" | "/api/resumes/workflow")
            || ident(path, "/api/job-tools/", "").is_some()
            || ident(path, "/api/jobs/", "").is_some()
            || path.strip_prefix("/api/resumes/versions/").is_some_and(resumes::is_version)
    }
}

/// Whether the route reads the worker's config.
fn needs_config(path: &str) -> bool {
    !(path == "/api/reminders" || path.starts_with("/api/reminders/"))
}

/// `web_jobs._text`: text of at most `limit` code points without NUL, stripped.
pub fn text(value: &Value, name: &str, limit: usize) -> Done<String> {
    match value {
        Value::String(text) if py::len(text) <= limit && !text.contains('\0') => Ok(py::strip(text).to_owned()),
        _ => Err(fail(400, format!("{name} must be text of at most {limit} characters"))),
    }
}

/// `web_jobs._date`: a typed date as a stored stamp; None for an empty optional one.
pub fn date(value: &Value, optional: bool) -> Done<Option<String>> {
    let typed = text(value, "Date", 100)?;
    if typed.is_empty() && optional {
        return Ok(None);
    }
    match dates::parse_user_date(&typed)? {
        Some(stamp) => Ok(Some(stamp)),
        None => Err(fail(400, "Use a date such as YYYY-MM-DD, today, or +7")),
    }
}

/// `web_server._integer`: 1-10 ASCII digits within a range.
pub fn integer(value: &str, name: &str, minimum: i64, maximum: i64) -> Done<i64> {
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(fail(400, format!("{name} must be an integer")));
    }
    let number: i64 = value.parse().map_err(|_| internal())?;
    if !(minimum..=maximum).contains(&number) {
        return Err(fail(400, format!("{name} must be between {minimum} and {maximum}")));
    }
    Ok(number)
}

fn refused(fault: Fault) -> Refusal {
    refuse(fault.status, fault.message)
}

/// A mutation's JSON object body (`RequestHandler._body`).
async fn body(request: &mut Request<Incoming>) -> Result<Map<String, Value>, Refusal> {
    let length = content_length(request.headers())?;
    let collected = Limited::new(request.body_mut(), length as usize).collect().await.map_err(|_| refuse(400, "Incomplete JSON body"))?.to_bytes();
    if collected.len() as u64 != length {
        return Err(refuse(400, "Incomplete JSON body"));
    }
    json_object(&collected)
}

/// The job routes; None for any other request.
pub async fn route(front: &Front, target: &Target, forward_target: &str, request: &mut Request<Incoming>, mutation: bool,
                   upgrade: bool) -> Option<Result<Response<Body>, Refusal>> {
    if upgrade || !owns(&target.path, mutation) {
        return None;
    }
    let head = request.method() == Method::HEAD;
    Some(answer(front, forward_target, request, mutation).await.map(|value| send_json(StatusCode::OK, &value, head)))
}

async fn answer(front: &Front, forward_target: &str, request: &mut Request<Incoming>, mutation: bool) -> Result<Value, Refusal> {
    // The query as the worker would have seen it: a spent link ticket removed.
    let target = parse_target(forward_target)?;
    let body = if mutation {
        crate::ported::no_query(&target)?;
        Some(body(request).await?)
    } else {
        None
    };
    if !front.workspace.active.iter().any(|module| module == "applyai") {
        return Err(refused(disabled("applyai")));
    }
    let config: Option<Arc<JobConfig>> = if needs_config(&target.path) { Some(config::current(&front.backend).await.map_err(refused)?) } else { None };
    let deleting = target.path == "/api/jobs/bulk"
        && body.as_ref().is_some_and(|body| body.get("action").is_some_and(|action| action == "delete") && body.get("value") == Some(&Value::Bool(true)));
    let automation = if deleting { Some(config::automation(&front.backend).await.map_err(refused)?) } else { None };
    let value = tokio::task::block_in_place(|| db::with(front.bootstrap.db_path(), |conn| {
        let env = Env { conn, config: config.as_deref(), root: front.bootstrap.root(), automation };
        let call = Call { path: &target.path, query: &target.query, body: body.as_ref() };
        let before = call.mutation().then(|| events::op_log_mark(conn)).flatten();
        let handler = if mutation { edits::answer } else { reads::answer };
        let result = handler(&env, &call)?.ok_or_else(|| fail(404, "API route not found"))?;
        if let Some(body) = &body {
            // `WebApplication.mutate`'s `record_events.capture`.
            events::capture(conn, &target.path, body, &result, before);
        }
        Ok(result)
    })).map_err(refused)?;
    if mutation {
        // `WebApplication.mutated`: windows hear of the change at once.
        crate::live::nudge();
    }
    Ok(value)
}
