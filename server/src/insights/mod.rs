//! ApplyAI's local insight routes and Settings → AI's local half, as the deleted handlers of
//! `kitty.web_insights`, `web_automation` and `web_ai` answered them over the retained core:
//!
//! ```text
//! GET  /api/reports                             response rates and the narrative summary (stats)
//! GET  /api/reports/export?format=&dataset=     a JSON document, a CSV file or a zip of CSVs (export)
//! GET  /api/mail                                Gmail setup files, the last check, pending proposals
//! POST /api/mail/proposals/<id>/{confirm,reject}
//! POST /api/finder/<id>/reject
//! POST /api/questions                           log a screening question (questions.log_question)
//! GET  /api/apply/health                        apply attempt health (apply.health.report)
//! GET  /api/ai/status, POST /api/ai/settings    (crate::insights::ai)
//! ```
//!
//! The same answers, refusals, order of checks and rows. Discovery, the finder's run and
//! approval (it writes the watchlist and reloads the worker's config), Gmail's consent and
//! check, triage, posting refreshes, the browser controller, preparation plans, screening
//! question summaries and AI previews and runs stay on the worker.

mod ai;
mod decimal;
mod health;
mod reports;

use std::io::ErrorKind;
use std::path::Path;

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::bootstrap::Bootstrap;
use crate::modules::{Failure, Workspace};
use crate::ported::{db, fail, internal, py, Call, Ctx, Done, Group};
use crate::target::Target;

/// The routes under ApplyAI's prefixes.
pub static GROUP: Group = Group { owns, module: Some("applyai"), answer };
/// Core routes: `/api/apply/health` (no ApplyAI prefix; only ApplyAI's extension answered it)
/// and the AI settings.
pub static CORE: Group = Group { owns: owns_core, module: None, answer: answer_core };

pub const EXPORT: &str = "/api/reports/export";
const QUESTION_MAX: usize = 4000;
const JOB_ID_MAX: i64 = (1 << 53) - 1;

enum Route {
    Reports,
    Mail,
    Proposal(Option<i64>, bool),
    Reject(Option<i64>),
    Question,
}

/// `[1-9][0-9]*` and `int()` of it; None past 64 bits, where binding it raised (a 500).
fn ident(text: &str) -> Option<Option<i64>> {
    let bytes = text.as_bytes();
    if bytes.is_empty() || bytes[0] == b'0' || !bytes.iter().all(u8::is_ascii_digit) {
        return None;
    }
    Some(text.parse().ok())
}

fn route(path: &str, mutation: bool) -> Option<Route> {
    if !mutation {
        return match path {
            "/api/reports" => Some(Route::Reports),
            "/api/mail" => Some(Route::Mail),
            _ => None,
        };
    }
    if path == "/api/questions" {
        return Some(Route::Question);
    }
    if let Some(rest) = path.strip_prefix("/api/mail/proposals/") {
        let (id, action) = rest.split_once('/')?;
        let confirm = match action {
            "confirm" => true,
            "reject" => false,
            _ => return None,
        };
        return Some(Route::Proposal(ident(id)?, confirm));
    }
    let id = path.strip_prefix("/api/finder/")?.strip_suffix("/reject")?;
    Some(Route::Reject(ident(id)?))
}

fn owns(path: &str, mutation: bool) -> bool {
    route(path, mutation).is_some()
}

fn owns_core(path: &str, mutation: bool) -> bool {
    path == if mutation { "/api/ai/settings" } else { "/api/ai/status" } || (!mutation && path == "/api/apply/health")
}

/// `_keys(values, allowed)` of web_insights and web_automation.
fn only(names: impl IntoIterator<Item = impl AsRef<str>>, allowed: &[&str]) -> Done<()> {
    if names.into_iter().all(|name| allowed.contains(&name.as_ref())) { Ok(()) } else { Err(fail(400, "Unknown parameter")) }
}

/// `_confirm(body)`.
fn confirmed(body: &Map<String, Value>) -> Done<()> {
    if body.get("confirm") == Some(&Value::Bool(true)) { Ok(()) } else { Err(fail(400, "Explicit confirmation is required")) }
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let Some(route) = route(call.path, call.mutation()) else { return Ok(None) };
    let conn = ctx.conn;
    let body = call.body();
    match route {
        Route::Reports => {
            only(call.names(), &[])?;
            reports::report(conn).map(Some)
        }
        Route::Mail => {
            only(call.names(), &[])?;
            mail(conn, ctx.root).map(Some)
        }
        Route::Proposal(id, confirm) => {
            only(body.keys(), &["confirm"])?;
            confirmed(body)?;
            let id = id.ok_or_else(internal)?;
            if pending(conn, Some(id))?.is_empty() {
                return Err(fail(409, "Proposal is missing or already decided"));
            }
            if confirm {
                confirm_proposal(conn, id)?;
            } else {
                conn.execute("UPDATE status_events SET state = 'rejected_by_me' WHERE id = ? AND state = 'proposed'", [id])?;
            }
            Ok(Some(json!({"ok": true})))
        }
        Route::Reject(id) => {
            only(body.keys(), &["confirm"])?;
            confirmed(body)?;
            let id = id.ok_or_else(internal)?;
            let decision = db::row(conn, "SELECT decision FROM firm_candidates WHERE id = ?", [id])?;
            if decision.and_then(|row| row.get("decision").cloned()) != Some(json!("pending")) {
                return Err(fail(409, "Candidate is missing or already decided"));
            }
            conn.execute("UPDATE firm_candidates SET decision = ?, decided_at = ? WHERE id = ?",
                          rusqlite::params!["rejected", reports::now_z(), id])?;
            Ok(Some(json!({"ok": true, "decision": "rejected"})))
        }
        Route::Question => question(conn, body).map(Some),
    }
}

fn answer_core(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    match (call.path, call.mutation()) {
        ("/api/apply/health", false) => {
            if !ctx.has("applyai") {
                return Ok(None);
            }
            only(call.names(), &[])?;
            health::report(ctx.conn).map(Some)
        }
        ("/api/ai/status", false) => {
            if call.names().next().is_some() {
                return Err(fail(400, "Unknown AI request field"));
            }
            ai::status(ctx).map(Some)
        }
        ("/api/ai/settings", true) => ai::save(ctx, call.body()).map(Some),
        _ => Ok(None),
    }
}

/// `pending_proposals` (newest first), or `get_pending` for one event: `asdict` of each.
fn pending(conn: &Connection, id: Option<i64>) -> Done<Vec<Value>> {
    const SELECT: &str = "
SELECT se.id, se.application_id, a.job_id, c.display_name, j.title, a.status,
       se.from_status, se.to_status, se.occurred_at, se.source, se.evidence
  FROM status_events se
  JOIN applications a ON a.id = se.application_id
  JOIN jobs j         ON j.id = a.job_id
  JOIN companies c    ON c.id = j.company_id
 WHERE se.state = 'proposed'
";
    const FIELDS: [&str; 11] = ["event_id", "application_id", "job_id", "company", "title", "current_status",
                                "from_status", "to_status", "occurred_at", "source", "evidence"];
    let mut statement;
    let mut rows = match id {
        Some(id) => {
            statement = conn.prepare(&format!("{SELECT} AND se.id = ?"))?;
            statement.query([id])?
        }
        None => {
            statement = conn.prepare(&format!("{SELECT} ORDER BY se.occurred_at DESC, se.id DESC"))?;
            statement.query([])?
        }
    };
    let mut out = Vec::new();
    while let Some(row) = rows.next()? {
        let mut item = Map::new();
        for (index, field) in FIELDS.iter().enumerate() {
            item.insert((*field).to_owned(), db::column(row.get_ref(index)?)?);
        }
        out.push(Value::Object(item));
        if id.is_some() {
            break;
        }
    }
    Ok(out)
}

/// `applications.confirm_proposal`: the event confirmed, then the application's status moved
/// (each statement its own autocommit, as Python's connection ran them).
fn confirm_proposal(conn: &Connection, id: i64) -> Done<()> {
    let Some(event) = db::row(conn, "SELECT * FROM status_events WHERE id = ? AND state = 'proposed'", [id])? else { return Ok(()) };
    conn.execute("UPDATE status_events SET state = 'confirmed' WHERE id = ?", [id])?;
    let field = |key: &str| event.get(key).map(db::bind).ok_or_else(internal)?;
    conn.execute("UPDATE applications SET status = ?, last_activity_at = ?, updated_at = ? WHERE id = ?",
                 rusqlite::params![field("to_status")?, field("occurred_at")?, reports::now_z(), field("application_id")?])?;
    Ok(())
}

/// `Path.exists()`: false for a missing path (or one through a non-folder or a loop); any other
/// failure raised.
fn exists(path: &Path) -> Done<bool> {
    match std::fs::metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if matches!(error.kind(), ErrorKind::NotFound | ErrorKind::NotADirectory)
            || error.raw_os_error() == Some(libc::ELOOP) => Ok(false),
        Err(_) => Err(internal()),
    }
}

/// `GET /api/mail`: never reads the client secret or the token, only whether they exist.
fn mail(conn: &Connection, root: &Path) -> Done<Value> {
    let client = exists(&root.join("config").join("gmail_client.json"))?;
    let token = exists(&root.join("data").join("gmail_token.json"))?;
    let last = db::row(conn, "SELECT started_at, stats_json, error FROM runs WHERE kind='mail' ORDER BY id DESC LIMIT 1", [])?;
    let last_check = match last {
        None => Value::Null,
        Some(row) => {
            let raw = match row.get("stats_json") {
                Some(Value::String(text)) if !text.is_empty() => text.clone(),
                Some(Value::String(_) | Value::Null) | None => "{}".to_owned(),
                Some(_) => return Err(internal()),
            };
            let stored: Value = serde_json::from_str(&raw)?;
            let stored = stored.as_object().ok_or_else(internal)?;
            let get = |key: &str, default: Value| stored.get(key).cloned().unwrap_or(default);
            let error = row.get("error").is_some_and(py::truthy);
            json!({"started_at": row.get("started_at").cloned().unwrap_or(Value::Null), "status": get("status", json!("unknown")),
                   "fetched": get("fetched", json!(0)), "proposed": get("proposed", json!(0)),
                   "error": if error { json!("Last check needs attention; review authorization or connection.") } else { Value::Null }})
        }
    };
    Ok(json!({"client_present": client, "token_present": token, "ready": client && token, "scope": "gmail.readonly",
              "last_check": last_check, "proposals": pending(conn, None)?}))
}

/// `normalize.question_hash`: SHA-256 of the lower-cased text's ASCII letter and digit runs,
/// joined by single spaces.
fn question_hash(text: &str) -> String {
    use sha2::{Digest, Sha256};
    let lower = crate::builds::value::lossy(&crate::builds::value::lower(&crate::builds::value::text(text)));
    let words: Vec<&str> = lower.split(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit())).filter(|word| !word.is_empty()).collect();
    hex::encode(Sha256::digest(words.join(" ").as_bytes()))
}

/// `POST /api/questions`: `questions.log_question` in one transaction.
fn question(conn: &Connection, body: &Map<String, Value>) -> Done<Value> {
    only(body.keys(), &["text", "job_id"])?;
    let text = match body.get("text") {
        Some(Value::String(text)) if !py::strip(text).is_empty() && py::len(text) <= QUESTION_MAX && !text.contains('\0') => text,
        _ => return Err(fail(400, "Question must contain 1–4000 characters")),
    };
    let job_id = match body.get("job_id") {
        None | Some(Value::Null) => None,
        Some(value) => {
            let id = value.as_i64().filter(|id| (1..=JOB_ID_MAX).contains(id))
                .ok_or_else(|| fail(400, format!("job_id must be an integer from 1 to {JOB_ID_MAX}")))?;
            if !db::exists(conn, "SELECT 1 FROM jobs j JOIN companies c ON c.id = j.company_id WHERE j.id = ?", [id])? {
                return Err(fail(404, "Job not found"));
            }
            Some(id)
        }
    };
    db::transaction(conn, false, || {
        let digest = question_hash(text);
        let stamp = reports::now_z();
        let inserted = conn.execute(
            "
        INSERT INTO screening_questions (text, norm_hash, first_seen_job_id, times_seen,
                                        answered, created_at, updated_at)
        VALUES (?, ?, ?, 1, 0, ?, ?)
        ON CONFLICT(norm_hash) DO NOTHING
        ",
            rusqlite::params![py::strip(text), digest, job_id, stamp, stamp],
        )? == 1;
        let id: i64 = conn.query_row("SELECT id FROM screening_questions WHERE norm_hash = ?", [&digest], |row| row.get(0))?;
        if !inserted {
            conn.execute("UPDATE screening_questions SET times_seen = times_seen + 1, updated_at = ? WHERE id = ?",
                         rusqlite::params![reports::now_z(), id])?;
        }
        if let Some(job_id) = job_id {
            conn.execute("INSERT OR IGNORE INTO question_jobs (question_id, job_id, seen_at) VALUES (?, ?, ?)",
                         rusqlite::params![id, job_id, reports::now_z()])?;
        }
        Ok(json!({"id": id}))
    })
}

/// `GET /api/reports/export`: the module guard, then `web_insights.download`. Returns the bytes,
/// the content type and the attachment's file name.
pub fn download(workspace: &Workspace, bootstrap: &Bootstrap, target: &Target) -> Result<(Vec<u8>, &'static str, String), Failure> {
    let run = || {
        if !workspace.active.iter().any(|module| module == "applyai") {
            return Err(crate::ported::disabled("applyai"));
        }
        db::with(bootstrap.db_path(), |conn| reports::export(conn, &target.query))
    };
    run().map_err(|fault| Failure { status: fault.status, message: fault.message })
}
