//! The study routes (`kitty.web_study` and `kitty.core.study` in Python): the workspace
//! with its statistics, the observed Pomodoro timer, guides and spaced returns, sessions,
//! the collections (subjects, projects, targets, tasks, exams, plan blocks) and plan
//! generation. Same values, stored rows, order of checks and refusals as Python.
//! `POST /api/study/settings` stays with the Python worker: it edits the build file.
//! `kitty.core.study` stays for Rika's tools and the build projection.

mod stats;

use std::sync::LazyLock;

use regex::Regex;
use rusqlite::{params, params_from_iter, Connection};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::ported::time::{self, Date, Dt, Zone, DAY, HOUR, MINUTE, SECOND};
use crate::ported::{db, fail, internal, py, Call, Ctx, Done, Fault, Group};

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

type Obj = Map<String, Value>;

/// The collections: the route's name, its table, and the fields a save may carry.
const TABLES: [(&str, &str, &[&str]); 6] = [
    ("subjects", "study_subjects", &["name", "color"]),
    ("projects", "study_projects", &["name", "subject_id"]),
    ("targets", "study_targets", &["title", "subject_id", "project_id", "period", "minutes"]),
    ("tasks", "study_tasks", &["title", "subject_id", "project_id", "priority", "due_date", "done", "checklist"]),
    ("exams", "study_exams", &["title", "subject_id", "exam_date", "timezone", "notes"]),
    ("plan", "study_plan_blocks", &["title", "subject_id", "exam_id", "start", "end", "timezone", "done"]),
];
const PRIMING_SECONDS: i64 = 300;
/// Days until the next spaced return; recalling a return climbs one rung, a shaky one restarts.
const RETURN_LADDER: [i64; 5] = [1, 3, 7, 16, 35];
const MAX_ID: i64 = i64::MAX;

static COLOR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^#[0-9a-fA-F]{6}$").expect("valid"));
static CLOCK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{2}:\d{2}$").expect("valid"));

fn owns(path: &str, mutation: bool) -> bool {
    if mutation { path.starts_with("/api/study/") && path != "/api/study/settings" } else { matches!(path, "/api/study" | "/api/study/timer") }
}

/// `ValueError`.
fn bad(message: impl Into<String>) -> Fault {
    fail(400, message)
}

/// `StudyConflict`: the caller must reload rather than overwrite another window's change.
fn conflict(message: &str) -> Fault {
    fail(409, message)
}

// Values -----------------------------------------------------------------------

fn absent() -> &'static Value {
    &Value::Null
}

fn field<'a>(body: &'a Obj, name: &str) -> &'a Value {
    body.get(name).unwrap_or(absent())
}

fn st<'a>(row: &'a Obj, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

fn num(row: &Obj, key: &str) -> Done<i64> {
    row.get(key).and_then(Value::as_i64).ok_or_else(internal)
}

fn text(value: &Value, name: &str, limit: usize, required: bool) -> Done<String> {
    let Some(value) = value.as_str().filter(|text| py::len(text) <= limit && !text.contains('\0')) else {
        return Err(bad(format!("{name} must be text of at most {limit} characters")));
    };
    let value = py::strip(value);
    if required && value.is_empty() {
        return Err(bad(format!("{name} is required")));
    }
    Ok(value.to_owned())
}

fn note(value: &Value, name: &str) -> Done<String> {
    text(value, name, 20000, false)
}

fn integer(value: &Value, name: &str, low: i64, high: i64) -> Done<i64> {
    py::int(value).filter(|number| (low..=high).contains(number)).ok_or_else(|| bad(format!("{name} must be an integer between {low} and {high}")))
}

fn boolean(value: &Value, name: &str) -> Done<bool> {
    value.as_bool().ok_or_else(|| bad(format!("{name} must be true or false")))
}

fn zone(value: &Value) -> Done<String> {
    let name = text(value, "Timezone", 100, true)?;
    if Zone::named(&name).is_none() {
        return Err(bad("Use an IANA timezone such as Europe/London or UTC"));
    }
    Ok(name)
}

fn tz(name: &str) -> Done<Zone> {
    Zone::named(name).ok_or_else(internal)
}

/// `day`: exactly `YYYY-MM-DD`.
fn day(value: &Value, name: &str) -> Done<Date> {
    let value = text(value, name, 10, true)?;
    Date::parse_dashed(&value).ok_or_else(|| bad(format!("{name} must use YYYY-MM-DD")))
}

/// An instant: microseconds since the epoch, in UTC.
fn at(micros: i64) -> Dt {
    Dt { wall: micros, zone: Some(Zone::Utc), fold: false }
}

fn iso(micros: i64) -> String {
    at(micros).isoformat()
}

fn local_date(micros: i64, zone: &Zone) -> Done<Date> {
    Ok(at(micros).astimezone(zone).map_err(|_| internal())?.date())
}

fn timestamp(value: &Value, name: &str) -> Done<i64> {
    let value = text(value, name, 100, true)?;
    let Some(parsed) = time::fromisoformat(&value.replace('Z', "+00:00")) else {
        return Err(bad(format!("{name} must be an ISO8601 timestamp with an explicit offset")));
    };
    if parsed.utcoffset().is_none() {
        return Err(bad(format!("{name} needs an explicit timezone offset")));
    }
    Ok(parsed.astimezone(&Zone::Utc).map_err(|_| internal())?.wall)
}

/// A stored timestamp (always written by `iso`).
fn stored(row: &Obj, key: &str) -> Done<i64> {
    timestamp(field(row, key), "Timestamp")
}

/// `timedelta.total_seconds()`.
fn seconds(micros: i64) -> f64 {
    micros as f64 / 1e6
}

/// `timedelta(seconds=value)` for a float: whole seconds plus the fraction rounded half to even.
fn micros_of(value: f64) -> i64 {
    let whole = value.trunc();
    whole as i64 * SECOND + ((value - whole) * 1e6).round_ties_even() as i64
}

fn fields(body: &Value, allowed: &[&str]) -> Done<()> {
    match body.as_object() {
        Some(body) if body.keys().all(|key| allowed.contains(&key.as_str())) => Ok(()),
        _ => Err(bad("Unknown study field")),
    }
}

fn only(body: &Obj, allowed: &[&str]) -> Done<()> {
    if body.keys().all(|key| allowed.contains(&key.as_str())) { Ok(()) } else { Err(bad("Unknown study field")) }
}

/// `value in {choices}` for a JSON value: a list or an object is unhashable in Python.
fn choice<'a>(value: &'a Value) -> Done<Option<&'a str>> {
    match value {
        Value::Array(_) | Value::Object(_) => Err(internal()),
        value => Ok(value.as_str()),
    }
}

fn reference(conn: &Connection, value: &Value, table: &str) -> Done<Value> {
    if value.is_null() || value.as_str() == Some("") {
        return Ok(Value::Null);
    }
    let ident = integer(value, "Reference", 1, MAX_ID)?;
    if !db::exists(conn, &format!("SELECT id FROM {table} WHERE id=?"), [ident])? {
        return Err(bad("The selected study item no longer exists"));
    }
    Ok(json!(ident))
}

fn revision(row: &Obj, value: &Value) -> Done<()> {
    if py::int(value).is_none() || py::int(value) != row.get("revision").and_then(Value::as_i64) {
        return Err(conflict("Study state changed in another window. Reload before editing."));
    }
    Ok(())
}

fn item(conn: &Connection, table: &str, ident: &Value) -> Done<Obj> {
    let ident = integer(ident, "ID", 1, MAX_ID)?;
    db::row(conn, &format!("SELECT * FROM {table} WHERE id=?"), [ident])?.ok_or_else(|| bad("Study item not found"))
}

fn binds(values: &[&Value]) -> Done<Vec<rusqlite::types::Value>> {
    values.iter().map(|value| db::bind(value)).collect()
}

/// `str(uuid.uuid4())`.
fn uuid() -> Done<String> {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| internal())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex = hex::encode(bytes);
    Ok(format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..]))
}

// Settings, timer and guides -----------------------------------------------------

fn settings(conn: &Connection) -> Done<Obj> {
    let mut result = db::row(conn, "SELECT * FROM study_settings WHERE id=1", [])?.ok_or_else(internal)?;
    result.remove("id");
    for key in ["sound", "notifications"] {
        let on = result.get(key).is_some_and(py::truthy);
        result.insert(key.into(), json!(on));
    }
    Ok(result)
}

fn timer_row(conn: &Connection) -> Done<Obj> {
    db::row(conn, "SELECT * FROM study_timer WHERE id=1", [])?.ok_or_else(internal)
}

fn save_timer(conn: &Connection, timer: &mut Obj) -> Done<()> {
    let next = num(timer, "revision")? + 1;
    timer.insert("revision".into(), json!(next));
    let columns: Vec<&String> = timer.keys().filter(|key| key.as_str() != "id").collect();
    let sql = format!("UPDATE study_timer SET {} WHERE id=1", columns.iter().map(|key| format!("{key}=?")).collect::<Vec<_>>().join(","));
    conn.execute(&sql, params_from_iter(binds(&columns.iter().map(|key| &timer[key.as_str()]).collect::<Vec<_>>())?))?;
    Ok(())
}

fn remaining(timer: &Obj, now: i64) -> Done<f64> {
    let mut elapsed = timer.get("elapsed_seconds").and_then(Value::as_f64).ok_or_else(internal)?;
    if st(timer, "status") == "running" {
        elapsed += seconds(now - stored(timer, "running_since")?).max(0.0);
    }
    Ok((num(timer, "duration_seconds")? as f64 - elapsed).max(0.0))
}

fn public_timer(timer: &Obj, now: i64) -> Done<Value> {
    let mut result = timer.clone();
    result.remove("segments_json");
    result.insert("remaining_seconds".into(), json!(remaining(timer, now)?));
    Ok(Value::Object(result))
}

fn guide_state(value: &Value) -> Done<Obj> {
    let mut state = Obj::new();
    if value.is_null() {
        state.insert("checked".into(), json!(vec![false; 14]));
        state.insert("responses".into(), json!(vec![""; 14]));
        state.insert("reflection".into(), json!(""));
        state.insert("next_review".into(), json!(""));
        return Ok(state);
    }
    fields(value, &["checked", "responses", "reflection", "next_review"])?;
    let Some(checked) = value.get("checked").and_then(Value::as_array).filter(|items| items.len() == 14) else {
        return Err(bad("The study guide needs all 14 checklist items"));
    };
    let Some(responses) = value.get("responses").and_then(Value::as_array).filter(|items| items.len() == 14) else {
        return Err(bad("The study guide needs all 14 responses"));
    };
    let checked = checked.iter().map(|item| boolean(item, "Checklist item")).collect::<Done<Vec<_>>>()?;
    let responses = responses.iter().map(|item| text(item, "Guide response", 4000, false)).collect::<Done<Vec<_>>>()?;
    let reflection = text(value.get("reflection").unwrap_or(&json!("")), "Reflection", 10000, false)?;
    let next_review = match value.get("next_review") {
        Some(next) if py::truthy(next) => day(next, "Next review")?.iso(),
        _ => String::new(),
    };
    state.insert("checked".into(), json!(checked));
    state.insert("responses".into(), json!(responses));
    state.insert("reflection".into(), json!(reflection));
    state.insert("next_review".into(), json!(next_review));
    Ok(state)
}

/// A guide with what the server keeps beside the user's state: the session's subject and the return's ladder rung.
fn guide(conn: &Connection, session_id: &Value) -> Done<Value> {
    let Some(session_id) = session_id.as_str() else { return Ok(Value::Null) };
    let Some(mut result) = db::row(conn, "SELECT * FROM study_guides WHERE id=?", [session_id])? else { return Ok(Value::Null) };
    let mut state: Obj = serde_json::from_str(st(&result, "state_json")).map_err(|_| internal())?;
    result.remove("state_json");
    result.insert("subject_id".into(), state.shift_remove("subject_id").unwrap_or(Value::Null));
    result.insert("return_step".into(), state.shift_remove("return_step").unwrap_or(json!(0)));
    result.insert("state".into(), Value::Object(state));
    Ok(Value::Object(result))
}

fn store_guide(conn: &Connection, session_id: &str, mut state: Obj, subject_id: &Value, return_step: &Value) -> Done<()> {
    state.insert("subject_id".into(), subject_id.clone());
    state.insert("return_step".into(), return_step.clone());
    conn.execute("UPDATE study_guides SET state_json=?,revision=revision+1 WHERE id=?", params![py::dumps(&Value::Object(state)), session_id])?;
    Ok(())
}

fn save_guide(conn: &Connection, body: &Obj) -> Done<Value> {
    only(body, &["session_id", "revision", "state"])?;
    let session_id = text(field(body, "session_id"), "Study session", 100, true)?;
    let state = guide_state(field(body, "state"))?;
    db::transaction(conn, true, || {
        let current = guide(conn, &json!(session_id))?;
        let Some(current) = current.as_object() else { return Err(bad("Study guide not found")) };
        revision(current, field(body, "revision"))?;
        store_guide(conn, &session_id, state.clone(), &current["subject_id"], &current["return_step"])?;
        Ok(json!({"guide": guide(conn, &json!(session_id))?}))
    })
}

/// Grade a spaced return: recalled climbs the ladder, shaky restarts it; both reschedule from today.
fn review_return(conn: &Connection, body: &Obj, now: i64) -> Done<Value> {
    only(body, &["session_id", "revision", "result"])?;
    let session_id = text(field(body, "session_id"), "Study session", 100, true)?;
    let result = choice(field(body, "result"))?.filter(|result| matches!(*result, "recalled" | "shaky")).ok_or_else(|| bad("A return is either recalled or shaky"))?;
    db::transaction(conn, true, || {
        let current = guide(conn, &json!(session_id))?;
        let Some(current) = current.as_object() else { return Err(bad("Study guide not found")) };
        revision(current, field(body, "revision"))?;
        let mut state = current["state"].as_object().cloned().ok_or_else(internal)?;
        if !py::truthy(state.get("next_review").ok_or_else(internal)?) {
            return Err(bad("This session has no spaced return to grade"));
        }
        let step = if result == "recalled" { (current["return_step"].as_i64().ok_or_else(internal)? + 1).min(RETURN_LADDER.len() as i64 - 1) } else { 0 };
        let today = local_date(now, &tz(st(&settings(conn)?, "timezone"))?)?;
        let next = today.add_days(RETURN_LADDER[step as usize]).ok_or_else(internal)?;
        state.insert("next_review".into(), json!(next.iso()));
        store_guide(conn, &session_id, state, &current["subject_id"], &json!(step))?;
        Ok(json!({"guide": guide(conn, &json!(session_id))?}))
    })
}

fn begin_phase(timer: &mut Obj, now: i64, running: bool) -> Done<()> {
    let duration = num(timer, "duration_seconds")?;
    timer.insert("status".into(), json!(if running { "running" } else { "paused" }));
    timer.insert("phase_id".into(), json!(uuid()?));
    timer.insert("started_at".into(), json!(iso(now)));
    timer.insert("running_since".into(), if running { json!(iso(now)) } else { Value::Null });
    timer.insert("deadline_at".into(), if running { json!(iso(now + duration * SECOND)) } else { Value::Null });
    timer.insert("elapsed_seconds".into(), json!(0));
    timer.insert("segments_json".into(), json!("[]"));
    timer.insert("notes".into(), json!(""));
    timer.insert("distractions".into(), json!(""));
    Ok(())
}

fn segments(timer: &Obj) -> Done<Vec<Value>> {
    serde_json::from_str(st(timer, "segments_json")).map_err(|_| internal())
}

/// One finished phase is logged and the next begins when it is observed: a sleeping or
/// offline window must not invent hours of study.
fn reconcile(conn: &Connection, now: i64) -> Done<Obj> {
    let mut timer = timer_row(conn)?;
    if st(&timer, "status") != "running" {
        return Ok(timer);
    }
    let ended = stored(&timer, "deadline_at")?;
    if now < ended {
        return Ok(timer);
    }
    let mut parts = segments(&timer)?;
    parts.push(json!([timer["running_since"], iso(ended)]));
    let completed_phase = timer["phase"].clone();
    let config = settings(conn)?;
    if st(&timer, "phase") == "focus" {
        conn.execute(
            "INSERT OR IGNORE INTO study_sessions (source,source_key,subject_id,project_id,target_id,started_at,ended_at,timezone, duration_seconds,segments_json,notes,distractions,created_at) VALUES('focus',?,?,?,?,?,?,?,?,?,?,?,?)",
            params_from_iter(binds(&[&timer["phase_id"], &timer["subject_id"], &timer["project_id"], &timer["target_id"], &timer["started_at"], &json!(iso(ended)),
                &timer["timezone"], &timer["duration_seconds"], &json!(py::dumps(&json!(parts))), &timer["notes"], &timer["distractions"], &json!(iso(now))])?),
        )?;
        let cycle = num(&timer, "cycle")? + 1;
        timer.insert("cycle".into(), json!(cycle));
        timer.insert("phase".into(), json!("short_break"));
        timer.insert("duration_seconds".into(), config["short_break_seconds"].clone());
    } else {
        timer.insert("phase".into(), json!("focus"));
        timer.insert("duration_seconds".into(), config["focus_seconds"].clone());
    }
    timer.insert("completed_at".into(), json!(iso(ended)));
    let phase_id = timer["phase_id"].clone();
    timer.insert("completed_phase_id".into(), phase_id);
    timer.insert("completed_phase".into(), completed_phase);
    begin_phase(&mut timer, now, true)?;
    save_timer(conn, &mut timer)?;
    Ok(timer)
}

fn timer_state(conn: &Connection, now: i64) -> Done<Value> {
    let timer = db::transaction(conn, true, || reconcile(conn, now))?;
    Ok(json!({"timer": public_timer(&timer, now)?, "server_now": iso(now), "settings": settings(conn)?, "guide": guide(conn, &timer["session_id"])?}))
}

/// A retried command answers what it answered the first time; its ID never carries another change.
fn request(conn: &Connection, body: &Obj, endpoint: &str) -> Done<(String, String, Option<Value>)> {
    let request_id = text(field(body, "request_id"), "Request ID", 100, true)?;
    let fingerprint = hex::encode(Sha256::digest(py::dumps_compact_sorted(&Value::Object(body.clone())).as_bytes()));
    if let Some(row) = db::row(conn, "SELECT * FROM study_requests WHERE request_id=?", [&request_id])? {
        if st(&row, "endpoint") != endpoint || st(&row, "fingerprint") != fingerprint {
            return Err(conflict("A request ID cannot be reused for different changes"));
        }
        let replay = serde_json::from_str(st(&row, "response_json")).map_err(|_| internal())?;
        return Ok((request_id, fingerprint, Some(replay)));
    }
    Ok((request_id, fingerprint, None))
}

fn remember(conn: &Connection, request_id: &str, fingerprint: &str, endpoint: &str, response: &Value, now: i64) -> Done<()> {
    conn.execute("INSERT INTO study_requests VALUES(?,?,?,?,?)", params![request_id, endpoint, fingerprint, py::dumps(response), iso(now)])?;
    Ok(())
}

fn timer_command(conn: &Connection, body: &Obj, now: i64) -> Done<Value> {
    only(body, &["action", "revision", "request_id", "subject_id", "project_id", "target_id", "timezone", "notes", "distractions", "guide"])?;
    db::transaction(conn, true, || {
        let (request_id, fingerprint, replay) = request(conn, body, "timer")?;
        if let Some(replay) = replay {
            return Ok(replay);
        }
        let mut timer = reconcile(conn, now)?;
        revision(&timer, field(body, "revision"))?;
        let action = choice(field(body, "action"))?.unwrap_or("");
        let annotate = |timer: &mut Obj| -> Done<()> {
            for (key, name) in [("notes", "Notes"), ("distractions", "Distractions")] {
                let value = note(body.get(key).unwrap_or(&timer[key]), name)?;
                timer.insert(key.into(), json!(value));
            }
            Ok(())
        };
        if matches!(action, "pause" | "resume") {
            annotate(&mut timer)?;
        }
        match action {
            "start" => {
                if st(&timer, "status") != "idle" {
                    return Err(conflict("Pause or stop the active timer before starting another"));
                }
                let config = settings(conn)?;
                for (key, table) in [("subject_id", "study_subjects"), ("project_id", "study_projects"), ("target_id", "study_targets")] {
                    if let Some(value) = body.get(key) {
                        timer.insert(key.into(), reference(conn, value, table)?);
                    }
                }
                let session_id = uuid()?;
                let zone_name = zone(body.get("timezone").unwrap_or(&config["timezone"]))?;
                timer.insert("phase".into(), json!("priming"));
                timer.insert("duration_seconds".into(), json!(PRIMING_SECONDS));
                timer.insert("cycle".into(), json!(0));
                timer.insert("session_id".into(), json!(session_id));
                for key in ["completed_at", "completed_phase_id", "completed_phase"] {
                    timer.insert(key.into(), Value::Null);
                }
                timer.insert("timezone".into(), json!(zone_name));
                let mut state = guide_state(field(body, "guide"))?;
                state.insert("subject_id".into(), timer["subject_id"].clone());
                state.insert("return_step".into(), json!(0));
                conn.execute("INSERT INTO study_guides(id,created_at,state_json) VALUES(?,?,?)", params![session_id, iso(now), py::dumps(&Value::Object(state))])?;
                begin_phase(&mut timer, now, true)?;
                let notes = note(body.get("notes").unwrap_or(&json!("")), "Notes")?;
                let distractions = note(body.get("distractions").unwrap_or(&json!("")), "Distractions")?;
                timer.insert("notes".into(), json!(notes));
                timer.insert("distractions".into(), json!(distractions));
            }
            "pause" => {
                if st(&timer, "status") != "running" {
                    return Err(conflict("Only a running timer can be paused"));
                }
                let mut parts = segments(&timer)?;
                parts.push(json!([timer["running_since"], iso(now)]));
                let elapsed = timer["elapsed_seconds"].as_f64().ok_or_else(internal)? + seconds(now - stored(&timer, "running_since")?).max(0.0);
                timer.insert("elapsed_seconds".into(), json!(elapsed));
                timer.insert("status".into(), json!("paused"));
                timer.insert("running_since".into(), Value::Null);
                timer.insert("deadline_at".into(), Value::Null);
                timer.insert("segments_json".into(), json!(py::dumps(&json!(parts))));
            }
            "resume" => {
                if st(&timer, "status") != "paused" {
                    return Err(conflict("Only a paused timer can be resumed"));
                }
                let deadline = now + micros_of(remaining(&timer, now)?);
                timer.insert("status".into(), json!("running"));
                timer.insert("running_since".into(), json!(iso(now)));
                timer.insert("deadline_at".into(), json!(iso(deadline)));
            }
            "stop" | "reset" => {
                conn.execute("UPDATE study_guides SET ended_at=COALESCE(ended_at,?) WHERE id=?", params_from_iter(binds(&[&json!(iso(now)), &timer["session_id"]])?))?;
                for (key, value) in [("status", json!("idle")), ("phase", json!("priming")), ("phase_id", Value::Null), ("cycle", json!(0)), ("started_at", Value::Null),
                    ("running_since", Value::Null), ("deadline_at", Value::Null), ("elapsed_seconds", json!(0)), ("segments_json", json!("[]")), ("notes", json!("")),
                    ("distractions", json!("")), ("duration_seconds", json!(PRIMING_SECONDS))] {
                    timer.insert(key.into(), value);
                }
            }
            "skip_break" => {
                if st(&timer, "phase") != "short_break" || st(&timer, "status") == "idle" {
                    return Err(conflict("There is no break to skip"));
                }
                let running = st(&timer, "status") == "running";
                timer.insert("phase".into(), json!("focus"));
                timer.insert("duration_seconds".into(), settings(conn)?["focus_seconds"].clone());
                begin_phase(&mut timer, now, running)?;
            }
            "annotate" => {
                if !matches!(st(&timer, "status"), "running" | "paused") {
                    return Err(conflict("Add notes to the completed session instead"));
                }
                annotate(&mut timer)?;
            }
            _ => return Err(bad("Unknown timer action")),
        }
        save_timer(conn, &mut timer)?;
        let response = json!({"timer": public_timer(&timer, now)?, "server_now": iso(now)});
        remember(conn, &request_id, &fingerprint, "timer", &response, now)?;
        Ok(response)
    })
}

// Collections ------------------------------------------------------------------

/// The columns a save writes, in Python's order.
fn entity_values(conn: &Connection, kind: &str, body: &Obj) -> Done<Vec<(&'static str, Value)>> {
    let mut values: Vec<(&'static str, Value)> = Vec::new();
    if matches!(kind, "subjects" | "projects") {
        values.push(("name", json!(text(field(body, "name"), "Name", 250, true)?)));
    } else {
        values.push(("title", json!(text(field(body, "title"), "Title", 500, true)?)));
    }
    if kind == "subjects" {
        let color = text(body.get("color").unwrap_or(&json!("#7c6fdb")), "Color", 7, false)?;
        if !COLOR.is_match(&color) {
            return Err(bad("Color must use #RRGGBB"));
        }
        values.push(("color", json!(color)));
    } else {
        values.push(("subject_id", reference(conn, field(body, "subject_id"), "study_subjects")?));
    }
    if matches!(kind, "targets" | "tasks") {
        values.push(("project_id", reference(conn, field(body, "project_id"), "study_projects")?));
    }
    match kind {
        "targets" => {
            let period = choice(field(body, "period"))?.filter(|period| matches!(*period, "daily" | "weekly")).ok_or_else(|| bad("Goal period must be daily or weekly"))?;
            values.push(("period", json!(period)));
            values.push(("minutes", json!(integer(field(body, "minutes"), "Goal minutes", 1, 10080)?)));
        }
        "tasks" => {
            let priority = match body.get("priority") {
                None => "normal",
                Some(priority) => choice(priority)?.filter(|priority| matches!(*priority, "low" | "normal" | "high")).ok_or_else(|| bad("Unknown task priority"))?,
            };
            let empty = json!([]);
            let Some(checklist) = body.get("checklist").unwrap_or(&empty).as_array().filter(|items| items.len() <= 100) else {
                return Err(bad("Checklist must contain at most 100 items"));
            };
            let mut cleaned = Vec::new();
            let mut ids: Vec<String> = Vec::new();
            for entry in checklist {
                fields(entry, &["id", "text", "done"])?;
                let ident = text(entry.get("id").unwrap_or(absent()), "Checklist ID", 100, true)?;
                if ids.contains(&ident) {
                    return Err(bad("Checklist IDs must be unique"));
                }
                ids.push(ident.clone());
                let label = text(entry.get("text").unwrap_or(absent()), "Checklist item", 500, true)?;
                cleaned.push(json!({"id": ident, "text": label, "done": boolean(entry.get("done").unwrap_or(&json!(false)), "Checklist done")?}));
            }
            let due = if body.get("due_date").is_some_and(py::truthy) { json!(day(&body["due_date"], "Due date")?.iso()) } else { Value::Null };
            values.push(("priority", json!(priority)));
            values.push(("due_date", due));
            values.push(("done", json!(i64::from(boolean(body.get("done").unwrap_or(&json!(false)), "Task done")?))));
            values.push(("checklist_json", json!(py::dumps(&json!(cleaned)))));
        }
        "exams" => {
            values.push(("exam_date", json!(day(field(body, "exam_date"), "Exam date")?.iso())));
            values.push(("timezone", json!(zone(field(body, "timezone"))?)));
            values.push(("notes", json!(note(body.get("notes").unwrap_or(&json!("")), "Notes")?)));
        }
        "plan" => {
            let (start, end) = (timestamp(field(body, "start"), "Start")?, timestamp(field(body, "end"), "End")?);
            if end <= start || end - start > DAY {
                return Err(bad("Study blocks need a positive duration of at most one day"));
            }
            values.push(("exam_id", reference(conn, field(body, "exam_id"), "study_exams")?));
            values.push(("start", json!(iso(start))));
            values.push(("end", json!(iso(end))));
            values.push(("timezone", json!(zone(field(body, "timezone"))?)));
            values.push(("done", json!(i64::from(boolean(body.get("done").unwrap_or(&json!(false)), "Block done")?))));
        }
        _ => {}
    }
    Ok(values)
}

fn public_entity(mut row: Obj) -> Done<Value> {
    if let Some(checklist) = row.shift_remove("checklist_json") {
        let parsed: Value = serde_json::from_str(checklist.as_str().unwrap_or("")).map_err(|_| internal())?;
        row.insert("checklist".into(), parsed);
    }
    if let Some(done) = row.get("done") {
        let done = py::truthy(done);
        row.insert("done".into(), json!(done));
    }
    Ok(Value::Object(row))
}

fn write_row(conn: &Connection, table: &str, values: &[(&str, Value)], existing: Option<i64>) -> Done<i64> {
    let bound = binds(&values.iter().map(|(_, value)| value).collect::<Vec<_>>())?;
    match existing {
        Some(ident) => {
            let sets = values.iter().map(|(key, _)| format!("{key}=?")).collect::<Vec<_>>().join(",");
            let mut bound = bound;
            bound.push(rusqlite::types::Value::Integer(ident));
            conn.execute(&format!("UPDATE {table} SET {sets},revision=revision+1 WHERE id=?"), params_from_iter(bound))?;
            Ok(ident)
        }
        None => {
            let names = values.iter().map(|(key, _)| *key).collect::<Vec<_>>().join(",");
            let marks = vec!["?"; values.len()].join(",");
            conn.execute(&format!("INSERT INTO {table} ({names}) VALUES ({marks})"), params_from_iter(bound))?;
            Ok(conn.last_insert_rowid())
        }
    }
}

/// `body.get("action", "save")`: delete, save, or the refusal.
fn action(body: &Obj, unknown: &str) -> Done<bool> {
    match body.get("action") {
        None => Ok(false),
        Some(action) => match action.as_str() {
            Some("delete") => Ok(true),
            Some("save") => Ok(false),
            _ => Err(bad(unknown)),
        },
    }
}

fn save_entity(conn: &Connection, kind: &str, table: &str, columns: &[&str], body: &Obj) -> Done<Value> {
    let allowed: Vec<&str> = columns.iter().copied().chain(["action", "id", "revision"]).collect();
    only(body, &allowed)?;
    db::transaction(conn, true, || {
        let existing = match body.get("id") {
            Some(ident) if !ident.is_null() => Some(item(conn, table, ident)?),
            _ => None,
        };
        if let Some(existing) = &existing {
            revision(existing, field(body, "revision"))?;
        }
        // Python compares `action == "delete"` before it refuses an unknown one.
        if body.get("action").and_then(Value::as_str) == Some("delete") {
            let Some(existing) = &existing else { return Err(bad("An existing item is required for deletion")) };
            conn.execute(&format!("DELETE FROM {table} WHERE id=?"), [num(existing, "id")?])?;
            return Ok(json!({"deleted": existing["id"]}));
        }
        action(body, "Unknown collection action")?;
        let mut merged = match &existing {
            Some(existing) => public_entity(existing.clone())?.as_object().cloned().ok_or_else(internal)?,
            None => Obj::new(),
        };
        for (key, value) in body {
            merged.insert(key.clone(), value.clone());
        }
        let values = entity_values(conn, kind, &merged)?;
        let ident = write_row(conn, table, &values, existing.as_ref().map(|row| num(row, "id")).transpose()?)?;
        Ok(json!({"item": public_entity(item(conn, table, &json!(ident))?)?}))
    })
}

fn public_session(row: &Obj) -> Obj {
    let mut result = row.clone();
    result.shift_remove("segments_json");
    result
}

fn save_session(conn: &Connection, body: &Obj, now: i64) -> Done<Value> {
    only(body, &["action", "id", "revision", "request_id", "subject_id", "project_id", "target_id", "started_at", "timezone", "duration_seconds", "notes", "distractions"])?;
    db::transaction(conn, true, || {
        let (request_id, fingerprint, replay) = request(conn, body, "sessions")?;
        if let Some(replay) = replay {
            return Ok(replay);
        }
        let existing = match body.get("id") {
            Some(ident) if !ident.is_null() => Some(item(conn, "study_sessions", ident)?),
            _ => None,
        };
        if let Some(existing) = &existing {
            revision(existing, field(body, "revision"))?;
        }
        let response = if body.get("action").and_then(Value::as_str) == Some("delete") {
            let Some(existing) = &existing else { return Err(bad("An existing session is required for deletion")) };
            conn.execute("DELETE FROM study_sessions WHERE id=?", [num(existing, "id")?])?;
            json!({"deleted": existing["id"]})
        } else {
            action(body, "Unknown session action")?;
            let mut merged = existing.clone().unwrap_or_default();
            for (key, value) in body {
                merged.insert(key.clone(), value.clone());
            }
            let started = timestamp(field(&merged, "started_at"), "Session start")?;
            let duration = integer(field(&merged, "duration_seconds"), "Duration seconds", 1, 604_800)?;
            let ended = started + duration * SECOND;
            if ended > now {
                return Err(bad("A completed session cannot end in the future"));
            }
            let mut values: Vec<(&str, Value)> = vec![
                ("subject_id", reference(conn, field(&merged, "subject_id"), "study_subjects")?),
                ("project_id", reference(conn, field(&merged, "project_id"), "study_projects")?),
                ("target_id", reference(conn, field(&merged, "target_id"), "study_targets")?),
                ("started_at", json!(iso(started))),
                ("ended_at", json!(iso(ended))),
                ("timezone", json!(zone(field(&merged, "timezone"))?)),
                ("duration_seconds", json!(duration)),
                ("notes", json!(note(merged.get("notes").unwrap_or(&json!("")), "Notes")?)),
                ("distractions", json!(note(merged.get("distractions").unwrap_or(&json!("")), "Distractions")?)),
            ];
            // Note-only changes retain the focus's actual paused intervals.
            let kept = match &existing {
                Some(existing) if started == stored(existing, "started_at")? && duration == num(existing, "duration_seconds")? => Some(existing),
                _ => None,
            };
            match kept {
                Some(existing) => {
                    values[4].1 = existing["ended_at"].clone();
                    values.push(("segments_json", existing["segments_json"].clone()));
                }
                None => values.push(("segments_json", json!(py::dumps(&json!([[iso(started), iso(ended)]]))))),
            }
            let ident = match &existing {
                Some(existing) => write_row(conn, "study_sessions", &values, Some(num(existing, "id")?))?,
                None => {
                    values.push(("source", json!("manual")));
                    values.push(("source_key", json!(request_id)));
                    values.push(("created_at", json!(iso(now))));
                    write_row(conn, "study_sessions", &values, None)?
                }
            };
            json!({"item": public_session(&item(conn, "study_sessions", &json!(ident))?)})
        };
        remember(conn, &request_id, &fingerprint, "sessions", &response, now)?;
        Ok(response)
    })
}

fn workspace(conn: &Connection, now: i64, timezone: Option<&str>, period: &str, date: Option<&str>) -> Done<Value> {
    db::transaction(conn, true, || {
        let timer = reconcile(conn, now)?;
        let mut result = Obj::new();
        result.insert("timer".into(), public_timer(&timer, now)?);
        result.insert("settings".into(), Value::Object(settings(conn)?));
        result.insert("server_now".into(), json!(iso(now)));
        result.insert("guide".into(), guide(conn, &timer["session_id"])?);
        let mut guides = Vec::new();
        for row in db::rows(conn, "SELECT id FROM study_guides ORDER BY created_at DESC LIMIT 50", [])? {
            guides.push(guide(conn, &row["id"])?);
        }
        result.insert("guides".into(), json!(guides));
        for (kind, table, _) in TABLES {
            let rows = db::rows(conn, &format!("SELECT * FROM {table} ORDER BY id"), [])?;
            let items = rows.into_iter().map(public_entity).collect::<Done<Vec<_>>>()?;
            result.insert(if kind == "plan" { "plans".into() } else { kind.to_owned() }, json!(items));
        }
        let (stats, sessions) = stats::statistics(conn, now, timezone, period, date)?;
        result.insert("stats".into(), stats);
        result.insert("sessions".into(), sessions);
        Ok(Value::Object(result))
    })
}

/// Distribute user-entered study time over real, pre-exam dates.
fn generate_plan(conn: &Connection, body: &Obj, now: i64) -> Done<Value> {
    only(body, &["exam_id", "exam_revision", "start_date", "minutes", "block_minutes", "local_time", "replace"])?;
    db::transaction(conn, true, || {
        let exam = item(conn, "study_exams", field(body, "exam_id"))?;
        revision(&exam, field(body, "exam_revision"))?;
        let start_date = day(field(body, "start_date"), "Plan start")?;
        let exam_date = Date::parse_dashed(st(&exam, "exam_date")).ok_or_else(internal)?;
        let zone = tz(st(&exam, "timezone"))?;
        if start_date < local_date(now, &zone)? || start_date >= exam_date {
            return Err(bad("Plan start must be today or later and before the exam date"));
        }
        let minutes = integer(field(body, "minutes"), "Total minutes", 1, 10080)?;
        let block_minutes = integer(field(body, "block_minutes"), "Block minutes", 1, 240)?;
        let local_time = text(body.get("local_time").unwrap_or(&json!("17:00")), "Local time", 5, false)?;
        if !CLOCK.is_match(&local_time) {
            return Err(bad("Local time must use HH:MM"));
        }
        let (hour, minute): (i64, i64) = match (local_time[..2].parse(), local_time[3..].parse()) {
            (Ok(hour), Ok(minute)) if hour <= 23 && minute <= 59 => (hour, minute),
            _ => return Err(bad("Invalid local time")),
        };
        let count = (minutes + block_minutes - 1) / block_minutes;
        let days_available = exam_date.days() - start_date.days();
        if count > days_available {
            return Err(bad("Not enough pre-exam days for one block per day; increase block minutes or reduce total time"));
        }
        let exam_id = num(&exam, "id")?;
        let existing: i64 = conn.query_row("SELECT COUNT(*) FROM study_plan_blocks WHERE exam_id=?", [exam_id], |row| row.get(0))?;
        let replace = boolean(body.get("replace").unwrap_or(&json!(false)), "Replace plan")?;
        if existing > 0 && !replace {
            return Err(conflict("This exam already has a plan. Explicitly choose replacement."));
        }
        if replace {
            conn.execute("DELETE FROM study_plan_blocks WHERE exam_id=?", [exam_id])?;
        }
        let mut items = Vec::new();
        let mut remaining = minutes;
        for index in 0..count {
            let chosen = start_date.add_days(index * days_available / count).ok_or_else(internal)?;
            let local = Dt { wall: chosen.days() * DAY + hour * HOUR + minute * MINUTE, zone: Some(zone.clone()), fold: false };
            let start = local.utc();
            if at(start).astimezone(&zone).map_err(|_| internal())?.wall != local.wall {
                return Err(bad("A planned time falls in a daylight-saving gap; choose another local time"));
            }
            if start < now {
                return Err(bad("A planned block starts in the past; choose a later time or start date"));
            }
            let duration = block_minutes.min(remaining);
            let end = start + duration * MINUTE;
            if local_date(end, &zone)? >= exam_date {
                return Err(bad("A block would overlap the exam day; choose an earlier time"));
            }
            conn.execute("INSERT INTO study_plan_blocks(exam_id,subject_id,title,start,end,timezone) VALUES(?,?,?,?,?,?)",
                params_from_iter(binds(&[&exam["id"], &exam["subject_id"], &json!(format!("Study: {}", st(&exam, "title"))), &json!(iso(start)), &json!(iso(end)), &exam["timezone"]])?))?;
            items.push(public_entity(item(conn, "study_plan_blocks", &json!(conn.last_insert_rowid()))?)?);
            remaining -= duration;
        }
        Ok(json!({"items": items}))
    })
}

// Routes -----------------------------------------------------------------------

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    let now = Dt::now_utc().wall;
    if !call.mutation() {
        if call.path == "/api/study" {
            if !call.only(&["timezone", "period", "date"]) {
                return Err(bad("Unknown study query parameter"));
            }
            return Ok(Some(workspace(conn, now, call.get("timezone"), call.get("period").unwrap_or("week"), call.get("date"))?));
        }
        call.no_query()?;
        return Ok(Some(timer_state(conn, now)?));
    }
    let body = call.body();
    let route = call.path.strip_prefix("/api/study/").unwrap_or("");
    Ok(Some(match route {
        "timer" => timer_command(conn, body, now)?,
        "guide" => save_guide(conn, body)?,
        "returns" => review_return(conn, body, now)?,
        "sessions" => save_session(conn, body, now)?,
        "generate-plan" => generate_plan(conn, body, now)?,
        route => match TABLES.iter().find(|table| table.0 == route) {
            Some((kind, table, columns)) => save_entity(conn, kind, table, columns, body)?,
            None => return Ok(None),
        },
    }))
}
