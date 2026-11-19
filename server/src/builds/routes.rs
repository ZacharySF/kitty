//! `/api/build*`: the live build's routes (`web_build` before they moved here), with Python's
//! validation messages, statuses and `problems`.
//!
//! ```text
//! GET  /api/build                 the active build, the file's text and problems, history
//! GET  /api/build/history         {history}
//! GET  /api/build/export          the active build as a shareable kitty-build.json (download;
//!                                 also through a single-use link ticket)
//! POST /api/build/check   {text}  problems with a text and the build it describes (no write)
//! POST /api/build/save    {text, base}            replace the file (the build editor); base is
//!                                                 the file revision the editor started from
//! POST /api/build/preferences {ui?, theme?: {base}, name?, author?, description?}
//!                                                 merge interface preferences (Settings)
//! POST /api/build/local   {ui?, theme?: {base}}   once per workspace: a browser's old local
//!                                                 preferences, for keys the build does not set
//! POST /api/build/fetch   {url}                   fetch a build from an https link in the
//!                                                 background: {"task"}; its result is {text, source}
//! POST /api/build/preview {text}                  what installing a build would change
//! POST /api/build/install {text, base, accept_sensitive, source}  install it in one step
//! POST /api/build/rollback {id?}                  restore a saved build (default: the newest)
//! GET  /api/tasks/<id>                            the snapshot of a fetch the front runs
//! ```
//!
//! Answers carrying build values print them as Python's `json.dumps` would (big integers,
//! floats and lone surrogates as the reader holds them).

use std::convert::Infallible;

use bytes::Bytes;
use http_body_util::{BodyExt, Empty, Full, Limited};
use hyper::body::Incoming;
use hyper::header::HeaderValue;
use hyper::{Method, Request, Response, StatusCode};

use super::document::{self, push_problem_json, Problem};
use super::failure::{first_error, Failure};
use super::fetch;
use super::json;
use super::project;
use super::service::{self, Service};
use super::share;
use super::store::HistoryEntry;
use super::tasks;
use super::value::{dump, dump_document, dump_str, scalar, text, utf8_surrogatepass, Dict, Text, Value};
use crate::front::{Body, Front, MAX_BODY};
use crate::target::{parse_target, Target};

const PATH: &str = "/api/build";
const INTERNAL: &str = "/api/internal/build";
const TASKS: &str = "/api/tasks/";
/// `web_build.MAX_SOURCE`.
const MAX_SOURCE: usize = 500;
const CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src http://127.0.0.1:3210; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

// Answers ---------------------------------------------------------------------------------

fn full(data: impl Into<Bytes>) -> Body {
    Full::new(data.into()).map_err(|never: Infallible| match never {}).boxed()
}

fn empty() -> Body {
    Empty::new().map_err(|never: Infallible| match never {}).boxed()
}

/// `RequestHandler._send`: the front's security headers.
fn send(status: u16, data: Vec<u8>, content_type: &str, head: bool, download: Option<&str>) -> Response<Body> {
    let length = data.len();
    let mut response = Response::new(if head { empty() } else { full(data) });
    *response.status_mut() = StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let headers = response.headers_mut();
    for (name, value) in [
        ("content-type", content_type),
        ("cache-control", "no-store"),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "no-referrer"),
        ("cross-origin-resource-policy", "same-origin"),
        ("content-security-policy", CSP),
        ("server", "rika!"),
    ] {
        headers.insert(name, HeaderValue::from_str(value).expect("static header values are valid"));
    }
    if let Some(name) = download {
        headers.insert("content-disposition", HeaderValue::from_str(&format!("attachment; filename=\"{name}\"")).expect("a plain file name"));
    }
    headers.insert("content-length", HeaderValue::from(length));
    response
}

fn answer(json: String, head: bool) -> Response<Body> {
    send(200, json.into_bytes(), "application/json; charset=utf-8", head, None)
}

/// An error answer `{"error", "problems"?}`; like Python's, it closes the connection.
fn failure(failure: Failure, head: bool) -> Response<Body> {
    let mut out = String::from("{\"error\":");
    dump_str(&text(&failure.message), &mut out);
    if !failure.problems.is_empty() {
        out.push_str(",\"problems\":");
        problems(&failure.problems, &mut out);
    }
    out.push('}');
    let mut response = send(failure.status, out.into_bytes(), "application/json; charset=utf-8", head, None);
    response.headers_mut().insert("connection", HeaderValue::from_static("close"));
    response
}

fn problems(list: &[Problem], out: &mut String) {
    out.push('[');
    for (i, problem) in list.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        push_problem_json(problem, out);
    }
    out.push(']');
}

fn no_query(target: &str) -> Result<(), Failure> {
    let parsed = parse_target(target).map_err(|invalid| Failure::new(invalid.0, invalid.1))?;
    if parsed.query.is_empty() { Ok(()) } else { Err(Failure::new(400, "This route does not accept query parameters")) }
}

fn not_found() -> Failure {
    Failure::new(404, "API route not found")
}

// Bodies ----------------------------------------------------------------------------------

/// `RequestHandler._body`: one Content-Length, then JSON as Python's `json.loads` reads it
/// (lone surrogates and big integers kept), refusing repeated keys and non-finite constants.
async fn body(request: &mut Request<Incoming>) -> Result<Dict, Failure> {
    let lengths: Vec<&str> = request.headers().get_all("content-length").iter().map(|v| v.to_str().unwrap_or("\u{fffd}")).collect();
    if request.headers().contains_key("transfer-encoding") || lengths.len() != 1 {
        return Err(Failure::new(400, "A single Content-Length is required"));
    }
    let value = lengths[0];
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Failure::new(400, "Content-Length must be an integer"));
    }
    let length: u64 = value.parse().expect("ten digits fit");
    if !(1..=MAX_BODY).contains(&length) {
        return Err(Failure::new(400, format!("Content-Length must be between 1 and {MAX_BODY}")));
    }
    let raw = Limited::new(request.body_mut(), length as usize).collect().await.map_err(|_| Failure::new(400, "Incomplete JSON body"))?.to_bytes();
    if raw.len() as u64 != length {
        return Err(Failure::new(400, "Incomplete JSON body"));
    }
    let malformed = || Failure::new(400, "Malformed JSON body");
    // `json.loads(bytes)` decodes UTF-8 with `surrogatepass`, a UTF-8 BOM dropped.
    let raw = raw.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&raw);
    let source = super::decode(raw).ok_or_else(malformed)?;
    let parsed = json::parse(&source).map_err(|_| malformed())?;
    if !parsed.found.is_empty() {
        return Err(malformed());
    }
    match parsed.value {
        Value::Dict(object) => Ok(object),
        _ => Err(Failure::new(400, "JSON body must be an object")),
    }
}

fn has(body: &Dict, key: &str) -> bool {
    body.contains(&text(key))
}

/// `_fields`.
fn fields(body: &Dict, allowed: &[&str], required: &[&str]) -> Result<(), Failure> {
    let unknown = body.keys().any(|key| !allowed.iter().any(|name| text(name) == *key));
    if unknown || required.iter().any(|name| !has(body, name)) {
        let mut sorted = allowed.to_vec();
        sorted.sort_unstable();
        return Err(Failure::new(400, format!("Expected fields: {}", sorted.join(", "))));
    }
    Ok(())
}

/// `_text`.
fn build_text<'a>(body: &'a Dict, key: &str) -> Result<&'a Text, Failure> {
    let Some(Value::Str(value)) = body.get_str(key) else {
        return Err(Failure::new(400, format!("Expected {{\"{key}\": the build file's text}}")));
    };
    if utf8_surrogatepass(value).len() > document::MAX_BYTES {
        return Err(Failure::new(400, format!("A build file is limited to {} KB", document::MAX_BYTES / 1024)));
    }
    Ok(value)
}

/// `_merge`: `patch` into `target`; with `missing_only`, only keys `target` lacks.
fn merge(target: &mut Dict, patch: &Dict, missing_only: bool) {
    for (key, value) in patch.iter() {
        if let (Value::Dict(inner), Some(Value::Dict(existing))) = (value, target.get_mut(key)) {
            merge(existing, inner, missing_only);
        } else if !missing_only || !target.contains(key) {
            target.insert(key.clone(), value.clone());
        }
    }
}

/// `_preferences`: what a Settings screen may change directly.
fn preferences(body: &Dict) -> Result<Dict, Failure> {
    fields(body, &["ui", "theme", "name", "author", "description"], &[])?;
    let mut patch = Dict::new();
    for key in ["name", "author", "description"] {
        if let Some(value) = body.get_str(key) {
            patch.insert(text(key), value.clone());
        }
    }
    if let Some(ui) = body.get_str("ui") {
        if !matches!(ui, Value::Dict(_)) {
            return Err(Failure::new(400, "ui must be an object"));
        }
        patch.insert(text("ui"), ui.clone());
    }
    if let Some(theme) = body.get_str("theme") {
        let only_base = matches!(theme, Value::Dict(theme) if theme.keys().all(|key| *key == text("base")));
        if !only_base {
            return Err(Failure::new(400, "Only theme.base changes here; edit tokens and CSS in the build editor"));
        }
        patch.insert(text("theme"), theme.clone());
    }
    Ok(patch)
}

// Routes ----------------------------------------------------------------------------------

/// The build routes and the front's task snapshots; None for every other request.
pub async fn route(_front: &Front, target: &Target, forward_target: &str, request: &mut Request<Incoming>, mutation: bool, upgrade: bool) -> Option<Response<Body>> {
    let head = request.method() == Method::HEAD;
    let path = target.path.as_str();
    if path.starts_with(INTERNAL) {
        return Some(failure(not_found(), head));
    }
    if upgrade {
        return None;
    }
    if let Some(id) = path.strip_prefix(TASKS) {
        let ours = !mutation && id.len() == 32 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'));
        let snapshot = if ours { tasks::find(id)? } else { return None };
        return Some(match no_query(forward_target) {
            Ok(()) => answer(snapshot, head),
            Err(refused) => failure(refused, head),
        });
    }
    if path != PATH && !path.starts_with("/api/build/") {
        return None;
    }
    let Some(service) = service::get() else { return Some(failure(Failure::busy(), head)) };
    let outcome = if mutation { post(service, path, forward_target, request).await } else { get(service, path, forward_target, head) };
    Some(outcome.unwrap_or_else(|refused| failure(refused, head)))
}

fn get(service: &Service, path: &str, forward_target: &str, head: bool) -> Result<Response<Body>, Failure> {
    tokio::task::block_in_place(|| match path {
        PATH => {
            no_query(forward_target)?;
            service.refresh()?;
            let history = service.store.list_history()?;
            let mut inner = service.lock();
            let moved = project::local_preferences_moved(inner.conn(service)?)?;
            let synced = inner.synced.clone().ok_or_else(Failure::busy)?;
            drop(inner);
            let mut out = String::from("{");
            service.public(&synced, &mut out)?;
            out.push_str(",\"history\":");
            history_json(&history, &mut out);
            out.push_str(&format!(",\"local_preferences_moved\":{moved}}}"));
            Ok(answer(out, head))
        }
        "/api/build/history" => {
            no_query(forward_target)?;
            let mut out = String::from("{\"history\":");
            history_json(&service.store.list_history()?, &mut out);
            out.push('}');
            Ok(answer(out, head))
        }
        "/api/build/export" => {
            no_query(forward_target)?;
            let build = service.lock().synced.as_ref().map(|synced| synced.build.clone()).ok_or_else(Failure::busy)?;
            let exported = scalar(&share::export_text(&build, &service.store.version)?).ok_or_else(Failure::internal)?;
            Ok(send(200, exported.into_bytes(), "application/json; charset=utf-8", head, Some("kitty-build.json")))
        }
        _ => Err(not_found()),
    })
}

fn history_json(history: &[HistoryEntry], out: &mut String) {
    out.push('[');
    for (i, entry) in history.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        entry.json(out);
    }
    out.push(']');
}

async fn post(service: &'static Service, path: &str, forward_target: &str, request: &mut Request<Incoming>) -> Result<Response<Body>, Failure> {
    no_query(forward_target)?;
    let body = body(request).await?;
    let action = path.strip_prefix("/api/build/").ok_or_else(not_found)?;
    let result = if action == "fetch" { fetch_task(&body)? } else { tokio::task::block_in_place(|| act(service, action, &body))? };
    // `web_build.mutated`: a route may have written the file.
    tokio::task::block_in_place(|| service.refresh())?;
    Ok(answer(result, false))
}

fn act(service: &Service, action: &str, body: &Dict) -> Result<String, Failure> {
    let store = &service.store;
    match action {
        "check" => {
            fields(body, &["text"], &["text"])?;
            let checked = document::check(build_text(body, "text")?)?;
            let mut out = format!("{{\"ok\":{},\"build\":", checked.build.is_some());
            match &checked.build {
                Some(build) => dump(build, &mut out),
                None => out.push_str("null"),
            }
            out.push_str(",\"problems\":");
            problems(&checked.problems, &mut out);
            out.push('}');
            Ok(out)
        }
        "save" => {
            fields(body, &["text", "base"], &["text", "base"])?;
            let source = build_text(body, "text")?;
            service.refresh()?;
            let mut inner = service.lock();
            let revision = inner.synced.as_ref().map(|synced| text(&synced.revision));
            if !matches!((body.get_str("base"), &revision), (Some(Value::Str(base)), Some(revision)) if base == revision) {
                return Err(Failure::new(409, "The build file changed since you opened it (another window or editor saved it); \
                                              reload it, or copy your edits first"));
            }
            let checked = document::check(source)?;
            if checked.build.is_none() {
                return Err(Failure::with(400, format!("Not saved: {}", first_error(&checked.problems)), checked.problems));
            }
            project::replace(inner.conn(service)?, store, source, None)?;
            Ok("{\"ok\":true}".into())
        }
        "preferences" => {
            let patch = preferences(body)?;
            let mut inner = service.lock();
            project::editing(inner.conn(service)?, store, |build, _| {
                merge(build, &patch, false);
                Ok(())
            })?;
            Ok("{\"ok\":true}".into())
        }
        "local" => {
            let patch = preferences(body)?;
            let mut inner = service.lock();
            let conn = inner.conn(service)?;
            if project::local_preferences_moved(conn)? {
                return Ok("{\"moved\":false}".into());
            }
            project::editing(conn, store, |build, tx| {
                if project::claim_local_preferences(tx)? {
                    merge(build, &patch, true);
                }
                Ok(())
            })?;
            Ok("{\"moved\":true}".into())
        }
        "preview" => {
            fields(body, &["text"], &["text"])?;
            service.refresh()?;
            let synced = service.lock().synced.clone().ok_or_else(Failure::busy)?;
            let plan = share::resolve(&synced.build, build_text(body, "text")?, true)?;
            let mut out = String::from("{\"base\":");
            dump_str(&text(&synced.active_revision), &mut out);
            out.push_str(",\"about\":");
            dump(&Value::Dict(plan.about), &mut out);
            out.push_str(",\"changes\":");
            changes(&plan.changes, &mut out);
            out.push_str(&format!(",\"restart\":{},\"warnings\":", plan.restart));
            problems(&plan.warnings, &mut out);
            out.push('}');
            Ok(out)
        }
        "install" => {
            fields(body, &["text", "base", "accept_sensitive", "source"], &["text", "base", "accept_sensitive"])?;
            let Some(Value::Bool(accept)) = body.get_str("accept_sensitive") else {
                return Err(Failure::new(400, "accept_sensitive must be true or false"));
            };
            let source = match body.get_str("source") {
                None => Text::new(),
                Some(Value::Str(source)) => source.clone(),
                Some(_) => return Err(Failure::new(400, "source must be text")),
            };
            service.refresh()?;
            let mut inner = service.lock();
            let synced = inner.synced.clone().ok_or_else(Failure::busy)?;
            if !matches!(body.get_str("base"), Some(Value::Str(base)) if *base == text(&synced.active_revision)) {
                return Err(Failure::new(409, "Your build changed since this preview; preview the install again"));
            }
            let plan = share::resolve(&synced.build, build_text(body, "text")?, *accept)?;
            let from = &source[..source.len().min(MAX_SOURCE)];
            project::replace(inner.conn(service)?, store, &dump_document(&plan.build), Some(("install", from)))?;
            let mut out = format!("{{\"ok\":true,\"restart\":{},\"held\":", plan.restart);
            changes(&plan.held, &mut out);
            out.push_str(",\"changes\":");
            changes(&plan.changes, &mut out);
            out.push('}');
            Ok(out)
        }
        "rollback" => {
            fields(body, &["id"], &[])?;
            let entries = store.list_history()?;
            let newest = entries.first().map(|entry| Value::Str(text(&entry.id))).unwrap_or(Value::Null);
            let id = body.get_str("id").cloned().unwrap_or(newest);
            if matches!(id, Value::Null) {
                return Err(Failure::new(404, "There is no earlier build to go back to"));
            }
            let saved = store.history_text(&id)?;
            let checked = document::check(&saved)?;
            if checked.build.is_none() {
                return Err(Failure::with(400, format!("That saved build cannot be restored: {}", first_error(&checked.problems)), checked.problems));
            }
            let Value::Str(id) = id else { return Err(Failure::internal()) };
            let mut inner = service.lock();
            let reason = [text("replaced by "), id.clone()].concat();
            project::replace(inner.conn(service)?, store, &saved, Some(("rollback", &reason)))?;
            let mut out = String::from("{\"ok\":true,\"restored\":");
            dump_str(&id, &mut out);
            out.push('}');
            Ok(out)
        }
        _ => Err(not_found()),
    }
}

fn changes(list: &[share::Change], out: &mut String) {
    out.push('[');
    for (i, change) in list.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        change.json(out);
    }
    out.push(']');
}

/// `POST /api/build/fetch {url}` → `{"task"}`; the task's result is `{text, source}`.
fn fetch_task(body: &Dict) -> Result<String, Failure> {
    fields(body, &["url"], &["url"])?;
    let url = body.get_str("url").cloned().unwrap_or(Value::Null);
    let snapshot = tasks::start("build-fetch", async move {
        let link = match &url {
            Value::Str(link) => scalar(link).ok_or_else(|| "That is not a valid link".to_owned())?,
            _ => return Err("That is not a valid link".to_owned()),
        };
        let fetched = fetch::fetch(&link, &fetch::System, &fetch::Https).await?;
        let mut out = String::from("{\"text\":");
        dump_str(&text(&fetched), &mut out);
        out.push_str(",\"source\":");
        dump_str(&text(&link).into_iter().take(MAX_SOURCE).collect::<Vec<_>>(), &mut out);
        out.push('}');
        Ok(out)
    })?;
    Ok(format!("{{\"task\":{snapshot}}}"))
}
