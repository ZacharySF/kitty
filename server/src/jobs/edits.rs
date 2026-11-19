//! The job edits: one job's actions (`POST /api/jobs/<id>`, the deleted
//! `WebApplication._mutate_job`), the bulk actions, the tool panel's edits and the reminders
//! (the deleted `web_jobs.post` branches), over `kitty.core.jobs`, `favorites`,
//! `applications`, `followups` and `reminders` as they write: the same rows, stamps and order.
//! Recording a submission is the owner's own report (`applied` needs `value: true`); nothing
//! here submits anything.

use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};

use super::config::JobConfig;
use super::dates::{self, now_iso};
use super::likeness::{self, HAND_NOT_ELIGIBLE};
use super::model::{self, Job};
use super::reads::{choose_resume, job};
use super::resumes::pdf_bytes;
use super::{date, text, Env};
use crate::ported::db::{exists, row, transaction};
use crate::ported::time::DAY;
use crate::ported::{fail, internal, py, Call, Done};

const QUEUE_STATES: [&str; 5] = ["new", "queued", "later", "skipped", "not_eligible"];
/// `web_server.LATER_STATUSES`.
const LATER_STATUSES: [&str; 6] = ["oa", "interview", "offer", "rejected", "ghosted", "withdrawn"];
const MAX_ID: u64 = i64::MAX as u64;

fn ok() -> Value {
    json!({"ok": true})
}

/// `type(value) is int and 1 <= value <= 2**63 - 1`.
fn positive_id(value: &Value) -> Option<i64> {
    py::int(value).filter(|id| *id >= 1)
}

// ------------------------------------------------------------------------------ jobs core

/// `favorites._ids_where`: the given ids that are jobs matching `condition`, in order, once each.
fn ids_where(conn: &Connection, wanted: &[i64], condition: &str) -> Done<Vec<i64>> {
    let mut unique: Vec<i64> = Vec::new();
    for id in wanted {
        if !unique.contains(id) {
            unique.push(*id);
        }
    }
    if unique.is_empty() {
        return Ok(Vec::new());
    }
    let sql = format!("SELECT id FROM jobs WHERE id IN ({}) AND {condition}", vec!["?"; unique.len()].join(","));
    let mut statement = conn.prepare(&sql)?;
    let found: std::collections::HashSet<i64> = statement.query_map(rusqlite::params_from_iter(&unique), |row| row.get(0))?
        .collect::<Result<_, _>>()?;
    Ok(unique.into_iter().filter(|id| found.contains(id)).collect())
}

fn set_queue_state(conn: &Connection, job_id: i64, state: &str) -> Done<()> {
    conn.execute("UPDATE jobs SET queue_state = ?, updated_at = ? WHERE id = ?", params![state, now_iso()?, job_id])?;
    Ok(())
}

/// `jobs.set_tier`: a cleared tier has no source.
fn set_tier(conn: &Connection, job_id: i64, tier: Option<&str>, reason: &str) -> Done<()> {
    conn.execute("UPDATE jobs SET tier = ?, tier_reason = ?, tier_source = ?, updated_at = ? WHERE id = ?",
                 params![tier, reason, tier.map(|_| "manual"), now_iso()?, job_id])?;
    Ok(())
}

fn set_resume(conn: &Connection, job_id: i64, resume_id: Option<i64>) -> Done<()> {
    conn.execute("UPDATE jobs SET resume_id = ?, updated_at = ? WHERE id = ?", params![resume_id, now_iso()?, job_id])?;
    Ok(())
}

/// `jobs.link_track`.
fn link_track(conn: &Connection, job_id: i64, track: &str, reason: &str) -> Done<()> {
    conn.execute("
        INSERT INTO job_tracks (job_id, track, match_reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id, track) DO UPDATE SET
            match_reason = COALESCE(excluded.match_reason, job_tracks.match_reason)
        ", params![job_id, track, reason, now_iso()?])?;
    Ok(())
}

/// `favorites.favorite` / `unfavorite`: star or unstar, then rescore, when anything changed.
fn favorite(conn: &Connection, ids: &[i64], on: bool) -> Done<()> {
    let changed = ids_where(conn, ids, if on { "favorited_at IS NULL" } else { "favorited_at IS NOT NULL" })?;
    if changed.is_empty() {
        return Ok(());
    }
    transaction(conn, false, || {
        for job_id in &changed {
            if on {
                conn.execute("UPDATE jobs SET favorited_at = ? WHERE id = ?", params![now_iso()?, job_id])?;
            } else {
                conn.execute("UPDATE jobs SET favorited_at = NULL WHERE id = ?", [job_id])?;
            }
        }
        likeness::rescore(conn)
    })
}

/// `favorites.set_state`: rescored only when a job enters or leaves `skipped`.
fn set_state(conn: &Connection, ids: &[i64], state: &str) -> Done<()> {
    let ids = ids_where(conn, ids, "1")?;
    if ids.is_empty() {
        return Ok(());
    }
    let was_skipped = !ids_where(conn, &ids, "queue_state = 'skipped'")?.is_empty();
    transaction(conn, false, || {
        for job_id in &ids {
            set_queue_state(conn, *job_id, state)?;
        }
        if state == "skipped" || was_skipped {
            likeness::rescore(conn)?;
        }
        Ok(())
    })
}

/// `favorites.mark_not_eligible`: my call, which discovery keeps, then rescore.
fn mark_not_eligible(conn: &Connection, ids: &[i64]) -> Done<()> {
    let ids = ids_where(conn, ids, "1")?;
    if ids.is_empty() {
        return Ok(());
    }
    transaction(conn, false, || {
        for job_id in &ids {
            conn.execute("UPDATE jobs SET eligibility = ?, eligibility_reason = ?, updated_at = ? WHERE id = ?",
                         params!["ineligible", HAND_NOT_ELIGIBLE, now_iso()?, job_id])?;
            set_queue_state(conn, *job_id, "not_eligible")?;
            set_tier(conn, *job_id, Some("C"), HAND_NOT_ELIGIBLE)?;
        }
        likeness::rescore(conn)
    })
}

// ------------------------------------------------------------------------------ applications

fn application_id(found: &Map<String, Value>) -> Done<i64> {
    found.get("id").and_then(Value::as_i64).ok_or_else(internal)
}

/// `applications._append_event`.
#[allow(clippy::too_many_arguments)]
fn append_event(conn: &Connection, application_id: i64, from_status: &Value, to_status: &Value, occurred_at: &str,
                evidence: Option<&str>, undoes: Option<i64>) -> Done<()> {
    conn.execute("
        INSERT INTO status_events (application_id, from_status, to_status, occurred_at,
                                  source, evidence, state, undoes_event_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ", params![application_id, crate::ported::db::bind(from_status)?, crate::ported::db::bind(to_status)?, occurred_at,
                   "manual", evidence, "confirmed", undoes, now_iso()?])?;
    Ok(())
}

/// `applications._apply_status`.
fn apply_status(conn: &Connection, application_id: i64, status: &Value, occurred_at: &str) -> Done<()> {
    conn.execute("UPDATE applications SET status = ?, last_activity_at = ?, updated_at = ? WHERE id = ?",
                 params![crate::ported::db::bind(status)?, occurred_at, now_iso()?, application_id])?;
    Ok(())
}

/// `applications.set_status(..., source="manual")`, confirmed.
fn set_status(conn: &Connection, application_id: i64, status: &str, occurred_at: Option<&str>, evidence: Option<&str>) -> Done<()> {
    let application = model::get_application(conn, application_id)?.ok_or_else(internal)?;
    let timestamp = match occurred_at {
        Some(when) if !when.is_empty() => when.to_owned(),
        _ => now_iso()?,
    };
    append_event(conn, application_id, application.get("status").ok_or_else(internal)?, &status.into(), &timestamp, evidence, None)?;
    apply_status(conn, application_id, &status.into(), &timestamp)
}

/// `applications.undo_last_status`: a reverting event; false when nothing can be undone.
fn undo_last_status(conn: &Connection, application_id: i64) -> Done<bool> {
    let Some(target) = model::last_undoable_event(conn, application_id)? else { return Ok(false) };
    let from = target.get("from_status").ok_or_else(internal)?;
    if from.is_null() {
        return Ok(false);
    }
    let timestamp = now_iso()?;
    let event_id = application_id_of(&target)?;
    let evidence = format!("undo of event {}", target.get("id").map(super::reads::python_str).transpose()?.unwrap_or_default());
    append_event(conn, application_id, target.get("to_status").ok_or_else(internal)?, from, &timestamp, Some(&evidence), Some(event_id))?;
    apply_status(conn, application_id, from, &timestamp)?;
    Ok(true)
}

fn application_id_of(found: &Map<String, Value>) -> Done<i64> {
    found.get("id").and_then(Value::as_i64).ok_or_else(internal)
}

/// `reminders.create_reminder`: its id.
fn create_reminder(conn: &Connection, kind: &str, due_at: &str, note: &str, job_id: Option<i64>, application_id: Option<i64>) -> Done<i64> {
    conn.execute("
        INSERT INTO reminders (job_id, application_id, kind, due_at, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ", params![job_id, application_id, kind, due_at, note, now_iso()?])?;
    Ok(conn.last_insert_rowid())
}

/// `followups.mark_applied`: the application recorded once (`record_application`) and its
/// follow-ups scheduled while it is still `applied`.
fn mark_applied(conn: &Connection, config: &JobConfig, job_id: i64, resume_id: Option<i64>, track: Option<&Value>,
                applied_at: Option<&str>, evidence: Option<&str>) -> Done<()> {
    let application = match model::application_for_job(conn, job_id)? {
        Some(existing) => existing,
        None => {
            let timestamp = match applied_at {
                Some(when) if !when.is_empty() => when.to_owned(),
                _ => now_iso()?,
            };
            let track = track.map(crate::ported::db::bind).transpose()?;
            conn.execute("
                INSERT INTO applications (job_id, track, resume_id, status, applied_at,
                                          last_activity_at, notes, created_at, updated_at)
                VALUES (?, ?, ?, 'applied', ?, ?, ?, ?, ?)
                ", params![job_id, track, resume_id, timestamp, timestamp, None::<String>, now_iso()?, now_iso()?])?;
            let id = conn.last_insert_rowid();
            conn.execute("
                INSERT INTO status_events (application_id, from_status, to_status, occurred_at,
                                          source, evidence, state, created_at)
                VALUES (?, NULL, 'applied', ?, ?, ?, 'confirmed', ?)
                ", params![id, timestamp, "manual", evidence, now_iso()?])?;
            set_queue_state(conn, job_id, "applied")?;
            if resume_id.is_some() {
                set_resume(conn, job_id, resume_id)?;
            }
            model::get_application(conn, id)?.ok_or_else(internal)?
        }
    };
    if application.get("status").ok_or_else(internal)? != "applied" {
        return Ok(());
    }
    // `add_followups`: one per offset after the application date, unless that date has one.
    let id = application_id(&application)?;
    let applied = match application.get("applied_at").ok_or_else(internal)? {
        Value::String(text) => dates::parse(Some(text)),
        value if !py::truthy(value) => None,
        _ => return Err(internal()),
    }.unwrap_or_else(dates::now);
    let existing: Vec<Value> = model::followups_for(conn, id)?.into_iter().map(|found| found.get("due_at").cloned().unwrap_or(Value::Null)).collect();
    let job = application.get("job_id").and_then(Value::as_i64).ok_or_else(internal)?;
    for offset in &config.followup_days {
        let days = offset.as_i64().ok_or_else(internal)?;
        let due_at = dates::to_iso(&applied.add(days.checked_mul(DAY).ok_or_else(internal)?)?)?;
        if existing.iter().any(|known| *known == due_at.as_str()) {
            continue;
        }
        create_reminder(conn, "followup", &due_at, &format!("{days} days after applying"), Some(job), Some(id))?;
    }
    Ok(())
}

// ------------------------------------------------------------------------------ routes

/// `POST /api/jobs/<id>`.
fn job_action(env: &Env, body: &Map<String, Value>, job_id: i64) -> Done<Value> {
    let conn = env.conn;
    let action = match body.get("action") {
        Some(Value::String(action)) if body.keys().all(|key| ["action", "value", "when", "note"].contains(&key.as_str())) => action.as_str(),
        _ => return Err(fail(400, "Expected an action and optional value")),
    };
    let value = body.get("value").unwrap_or(&Value::Null);
    let mut occurred_at: Option<String> = None;
    let mut note: Option<String> = None;
    if body.contains_key("when") || body.contains_key("note") {
        if action != "status" && action != "applied" {
            return Err(fail(400, "Date and evidence are only valid for application records"));
        }
        let when = body.get("when").cloned().unwrap_or_else(|| "".into());
        let evidence = body.get("note").cloned().unwrap_or_else(|| "".into());
        let (Value::String(when), Value::String(evidence)) = (when, evidence) else {
            return Err(fail(400, "Invalid application date or evidence"));
        };
        if py::len(&when) > 100 || py::len(&evidence) > 20_000 {
            return Err(fail(400, "Invalid application date or evidence"));
        }
        if !py::strip(&when).is_empty() {
            occurred_at = Some(dates::parse_user_date(&when)?.ok_or_else(|| fail(400, "Use a valid application date"))?);
        }
        note = Some(evidence);
    }
    let config = env.config()?;
    match action {
        "queue" | "skip" | "restore" => {
            if body.contains_key("value") {
                return Err(fail(400, "Queue actions do not accept a value"));
            }
        }
        "favorite" => {
            if !value.is_boolean() {
                return Err(fail(400, "Favorite value must be a boolean"));
            }
        }
        "notes" => {
            if !value.as_str().is_some_and(|notes| py::len(notes) <= 20_000 && !notes.contains('\0')) {
                return Err(fail(400, "Notes must be text of at most 20000 characters without NUL"));
            }
        }
        "applied" => {
            if value != &Value::Bool(true) {
                return Err(fail(400, "Explicit confirmation that you submitted is required"));
            }
        }
        "status" => {
            if !value.as_str().is_some_and(|status| LATER_STATUSES.contains(&status)) {
                return Err(fail(400, "Choose a later application status"));
            }
        }
        "track" => {
            if !value.is_null() && !value.as_str().is_some_and(|track| config.has_track(track)) {
                return Err(fail(400, "Choose a configured track or null for none"));
            }
        }
        "resume" => {
            if !value.is_null() && positive_id(value).is_none() {
                return Err(fail(400, "Resume value must be a registered resume ID or null for the track default"));
            }
        }
        _ => return Err(fail(400, "Unknown action")),
    }
    let job = job(conn, job_id)?;
    let id = job.id()?;
    let applied = job.application_id()?;
    match action {
        "favorite" => favorite(conn, &[id], value == &Value::Bool(true))?,
        "queue" | "skip" | "restore" => {
            if applied.is_some() {
                return Err(fail(409, "An applied job cannot change queue state"));
            }
            set_state(conn, &[id], match action { "queue" => "queued", "skip" => "skipped", _ => "new" })?;
        }
        _ => transaction(conn, false, || {
            match (action, applied) {
                ("notes", _) => {
                    conn.execute("UPDATE jobs SET my_notes = ?, updated_at = ? WHERE id = ?", params![value.as_str(), now_iso()?, id])?;
                }
                ("status" | "track", None) => return Err(fail(409, "Record your submission before changing application status")),
                ("status", Some(application)) => {
                    set_status(conn, application, value.as_str().ok_or_else(internal)?, occurred_at.as_deref(), note.as_deref())?;
                }
                ("track", Some(application)) => {
                    conn.execute("UPDATE applications SET track = ?, updated_at = ? WHERE id = ?", params![value.as_str(), now_iso()?, application])?;
                }
                ("resume", Some(_)) => return Err(fail(409, "Historical application resume is pinned")),
                ("resume", None) => {
                    let chosen = positive_id(value);
                    if let Some(resume_id) = chosen {
                        let resume = model::get_resume(conn, resume_id)?.filter(|resume| resume.active)
                            .ok_or_else(|| fail(400, "Choose an active registered resume"))?;
                        pdf_bytes(env.root, &resume)?;
                    }
                    set_resume(conn, id, chosen)?;
                }
                _ => {
                    // "applied": the owner's own report of a submission they made.
                    let chosen = choose_resume(conn, config, &job)?;
                    mark_applied(conn, config, id, chosen.map(|resume| resume.id), job.tracks.first(), occurred_at.as_deref(), note.as_deref())?;
                }
            }
            Ok(())
        })?,
    }
    Ok(ok())
}

/// `POST /api/jobs/bulk`.
fn bulk(env: &Env, body: &Map<String, Value>) -> Done<Value> {
    let conn = env.conn;
    if body.keys().any(|key| !["ids", "action", "value"].contains(&key.as_str())) {
        return Err(fail(400, "Unknown bulk action field"));
    }
    let ids = match body.get("ids") {
        Some(Value::Array(items)) if (1..=10_000).contains(&items.len()) => {
            items.iter().map(|item| positive_id(item).filter(|id| *id as u64 <= MAX_ID)).collect::<Option<Vec<i64>>>()
        }
        _ => None,
    }.ok_or_else(|| fail(400, "Select between 1 and 10,000 job IDs"))?;
    let mut unique: Vec<i64> = Vec::new();
    for id in ids {
        if !unique.contains(&id) {
            unique.push(id);
        }
    }
    let selected = unique.iter().map(|id| job(conn, *id)).collect::<Done<Vec<Job>>>()?;
    let action = body.get("action").unwrap_or(&Value::Null);
    let mut value = body.get("value").cloned().unwrap_or(Value::Null);
    let any_applied = || -> Done<bool> {
        for job in &selected {
            if job.application_id()?.is_some() {
                return Ok(true);
            }
        }
        Ok(false)
    };
    let action = action.as_str().unwrap_or("");
    match action {
        "tier" => {
            if !matches!(&value, Value::Null) && !matches!(value.as_str(), Some("A" | "B" | "C")) {
                return Err(fail(400, "Choose tier A, B, C or unranked"));
            }
        }
        "state" => {
            if !value.as_str().is_some_and(|state| QUEUE_STATES.contains(&state)) {
                return Err(fail(400, "Choose a valid queue state"));
            }
            if any_applied()? {
                return Err(fail(409, "Applied jobs retain their application state"));
            }
        }
        "not_eligible" => {
            if any_applied()? {
                return Err(fail(409, "Applied jobs retain their application state"));
            }
        }
        "add_track" => {
            let config = env.config()?;
            if !value.as_str().is_some_and(|track| config.has_track(track)) {
                return Err(fail(400, "Choose a configured track"));
            }
        }
        "favorite" => {
            if !value.is_boolean() && value != "toggle" {
                return Err(fail(400, "Favorite must be a boolean or toggle"));
            }
            if value == "toggle" {
                let mut all = true;
                for job in &selected {
                    all &= py::truthy(job.get("favorited_at")?);
                }
                value = Value::Bool(!all);
            }
        }
        "delete" => {
            if value != Value::Bool(true) {
                return Err(fail(400, "Confirm job deletion first"));
            }
            if any_applied()? {
                return Err(fail(409, "Submitted applications and their history are protected from job deletion"));
            }
            let browser = env.automation.as_ref().ok_or_else(internal)?;
            if browser.running && selected.iter().any(|job| browser.job_ids.iter().any(|id| job.get("id").is_ok_and(|own| py::eq(own, id)))) {
                return Err(fail(409, "Stop preparation for these jobs before deleting them; retained Firefox tabs stay open"));
            }
        }
        _ => return Err(fail(400, "Unknown bulk action")),
    }
    let targets = selected.iter().map(Job::id).collect::<Done<Vec<i64>>>()?;
    match action {
        "state" => set_state(conn, &targets, value.as_str().ok_or_else(internal)?)?,
        "not_eligible" => mark_not_eligible(conn, &targets)?,
        "favorite" => favorite(conn, &targets, value == Value::Bool(true))?,
        "delete" => transaction(conn, false, || {
            for job_id in &targets {
                if model::application_for_job(conn, *job_id)?.is_some() {
                    return Err(fail(409, "Submitted applications and their history are protected from job deletion"));
                }
            }
            // Reusable answers survive deletion of the posting that first prompted them.
            for job_id in &targets {
                conn.execute("UPDATE screening_questions SET first_seen_job_id=NULL WHERE first_seen_job_id=?", [job_id])?;
            }
            for job_id in &targets {
                conn.execute("DELETE FROM jobs WHERE id=?", [job_id])?;
            }
            Ok(())
        })?,
        _ => transaction(conn, false, || {
            for job_id in &targets {
                if action == "add_track" {
                    link_track(conn, *job_id, value.as_str().ok_or_else(internal)?, "Manually added in the website")?;
                } else {
                    set_tier(conn, *job_id, value.as_str(), "Manually chosen in the website")?;
                }
            }
            Ok(())
        })?,
    }
    let mut answer = json!({"ok": true, "changed": selected.len()});
    if action == "favorite" {
        answer["favorite"] = value;
    }
    Ok(answer)
}

/// `POST /api/reminders`: a custom reminder, optionally about a job.
fn new_reminder(env: &Env, body: &Map<String, Value>) -> Done<Value> {
    let conn = env.conn;
    if body.keys().any(|key| !["date", "note", "job_id"].contains(&key.as_str())) {
        return Err(fail(400, "Unknown reminder field"));
    }
    let job_id = match body.get("job_id").unwrap_or(&Value::Null) {
        Value::Null => None,
        value => {
            let id = positive_id(value).ok_or_else(|| fail(400, "Invalid job ID"))?;
            job(conn, id)?;
            Some(id)
        }
    };
    let due_at = date(body.get("date").unwrap_or(&Value::Null), false)?.ok_or_else(internal)?;
    let note = text(body.get("note").unwrap_or(&Value::String(String::new())), "Note", 20_000)?;
    let id = create_reminder(conn, "custom", &due_at, &note, job_id, None)?;
    Ok(json!({"ok": true, "id": id}))
}

/// `POST /api/reminders/restore`: a deleted reminder put back exactly as it was.
fn restore(env: &Env, body: &Map<String, Value>) -> Done<Value> {
    let conn = env.conn;
    let expected = || fail(400, "Expected the deleted reminder to restore");
    const FIELDS: [&str; 8] = ["id", "job_id", "application_id", "kind", "due_at", "note", "done_at", "created_at"];
    let Some(Value::Object(row)) = body.get("reminder").filter(|_| body.len() == 1) else { return Err(expected()) };
    if row.len() != FIELDS.len() || !FIELDS.iter().all(|field| row.contains_key(*field)) {
        return Err(expected());
    }
    let field = |key: &str| &row[key];
    let ids_ok = ["job_id", "application_id"].iter().all(|key| field(key).is_null() || positive_id(field(key)).is_some())
        && positive_id(field("id")).is_some();
    let texts_ok = ["due_at", "created_at"].iter().all(|key| field(key).as_str().is_some_and(|text| py::len(text) <= 100))
        && ["note", "done_at"].iter().all(|key| field(key).is_null() || field(key).is_string());
    if !ids_ok || !texts_ok {
        return Err(expected());
    }
    // `row['kind'] not in {...}`: a list or mapping is unhashable there.
    if field("kind").is_array() || field("kind").is_object() {
        return Err(internal());
    }
    if !matches!(field("kind").as_str(), Some("followup" | "deadline" | "opens" | "custom")) {
        return Err(expected());
    }
    if !field("note").is_null() {
        text(field("note"), "Note", 20_000)?;
    }
    if exists(conn, "SELECT id FROM reminders WHERE id=?", [field("id").as_i64()])? {
        return Err(fail(409, "That reminder already exists"));
    }
    let bound = FIELDS.iter().map(|key| crate::ported::db::bind(field(key))).collect::<Done<Vec<_>>>()?;
    match conn.execute("INSERT INTO reminders (id, job_id, application_id, kind, due_at, note, done_at, created_at) \
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rusqlite::params_from_iter(bound)) {
        Ok(_) => Ok(ok()),
        Err(rusqlite::Error::SqliteFailure(failure, _)) if failure.code == rusqlite::ErrorCode::ConstraintViolation => {
            Err(fail(409, "Its job or application no longer exists"))
        }
        Err(error) => Err(error.into()),
    }
}

/// `POST /api/reminders/<id>[/complete|snooze|delete]`.
fn reminder(env: &Env, body: &Map<String, Value>, id: i64, action: &str) -> Done<Value> {
    let conn = env.conn;
    if !action.is_empty() && !body.is_empty() {
        return Err(fail(400, "Expected empty object"));
    }
    if action.is_empty() && (body.is_empty() || body.keys().any(|key| key != "date" && key != "note")) {
        return Err(fail(400, "Change the reminder date or note"));
    }
    if !exists(conn, "SELECT id FROM reminders WHERE id=?", [id])? {
        return Err(fail(404, "Reminder not found"));
    }
    match action {
        "complete" => {
            conn.execute("UPDATE reminders SET done_at = ? WHERE id = ? AND done_at IS NULL", params![now_iso()?, id])?;
            Ok(ok())
        }
        "snooze" => {
            // One day past the later of its due date and now.
            let previous = row(conn, "SELECT due_at FROM reminders WHERE id = ?", [id])?.and_then(|mut found| found.remove("due_at")).ok_or_else(internal)?;
            let current = dates::now();
            let stored = match &previous {
                Value::String(text) => dates::parse(Some(text)),
                value if !py::truthy(value) => None,
                _ => return Err(internal()),
            };
            let base = stored.unwrap_or_else(|| current.clone()).max(current);
            let due_at = dates::to_iso(&base.add(DAY)?)?;
            conn.execute("UPDATE reminders SET due_at = ? WHERE id = ?", params![due_at, id])?;
            Ok(json!({"ok": true, "previous": previous, "due_at": due_at}))
        }
        "delete" => {
            let found = row(conn, "SELECT * FROM reminders WHERE id = ?", [id])?.ok_or_else(internal)?;
            conn.execute("DELETE FROM reminders WHERE id = ?", [id])?;
            Ok(json!({"ok": true, "reminder": found}))
        }
        _ => {
            let due_at = match body.get("date") {
                Some(value) => date(value, false)?,
                None => None,
            };
            let note = body.get("note").map(|value| text(value, "Note", 20_000)).transpose()?;
            if let Some(due_at) = due_at {
                conn.execute("UPDATE reminders SET due_at = ? WHERE id = ?", params![due_at, id])?;
            }
            if let Some(note) = note {
                conn.execute("UPDATE reminders SET note = ? WHERE id = ?", params![(!note.is_empty()).then_some(note), id])?;
            }
            Ok(ok())
        }
    }
}

/// `POST /api/job-tools/<id>`: deadline, next action, undo of the last status change, tracks.
fn tools(env: &Env, body: &Map<String, Value>, job_id: i64) -> Done<Value> {
    let conn = env.conn;
    if body.keys().any(|key| !["action", "value", "note"].contains(&key.as_str())) {
        return Err(fail(400, "Unknown job action field"));
    }
    let job = job(conn, job_id)?;
    let id = job.id()?;
    let application = model::application_for_job(conn, id)?.map(|found| application_id(&found)).transpose()?;
    let action = body.get("action").unwrap_or(&Value::Null);
    let value = body.get("value").unwrap_or(&Value::Null);
    transaction(conn, false, || {
        match action.as_str() {
            Some("deadline") => {
                let deadline = date(value, true)?;
                conn.execute("UPDATE jobs SET deadline_at = ?, updated_at = ? WHERE id = ?", params![deadline, now_iso()?, id])?;
            }
            Some("next_action") => {
                let application = application.ok_or_else(|| fail(409, "Record your submission first"))?;
                let due_at = date(value, true)?;
                let note = text(body.get("note").unwrap_or(&Value::String(String::new())), "Note", 20_000)?;
                conn.execute("UPDATE applications SET next_action_at = ?, next_action_note = ?, updated_at = ? WHERE id = ?",
                             params![due_at, (!note.is_empty()).then_some(note), now_iso()?, application])?;
            }
            Some("undo") => {
                if !application.map(|application| undo_last_status(conn, application)).transpose()?.unwrap_or(false) {
                    return Err(fail(409, "No status change can be undone; the original submission stays"));
                }
            }
            Some("tracks") => {
                let config = env.config()?;
                let chosen: Vec<&str> = match value {
                    Value::Array(items) => items.iter().map(|item| item.as_str().filter(|track| config.has_track(track))).collect::<Option<Vec<_>>>(),
                    _ => None,
                }.ok_or_else(|| fail(400, "Choose configured tracks"))?;
                let current: Vec<&str> = job.tracks.iter().map(|track| track.as_str().ok_or_else(internal)).collect::<Done<_>>()?;
                for track in current.iter().filter(|track| !chosen.contains(track)) {
                    conn.execute("DELETE FROM job_tracks WHERE job_id = ? AND track = ?", params![id, track])?;
                }
                let mut added: Vec<&str> = Vec::new();
                for track in chosen.iter().filter(|track| !current.contains(track)) {
                    if !added.contains(track) {
                        added.push(track);
                        link_track(conn, id, track, "Manually assigned in the website")?;
                    }
                }
            }
            _ => return Err(fail(400, "Unknown job action")),
        }
        Ok(())
    })?;
    Ok(ok())
}

/// One POST the front answers, or None for a path it does not own.
pub fn answer(env: &Env, call: &Call) -> Done<Option<Value>> {
    let body = call.body();
    let path = call.path;
    Ok(Some(match path {
        "/api/jobs/bulk" => bulk(env, body)?,
        "/api/reminders" => new_reminder(env, body)?,
        "/api/reminders/restore" => restore(env, body)?,
        _ => {
            if let Some(id) = super::ident(path, "/api/job-tools/", "") {
                tools(env, body, id)?
            } else if let Some(id) = super::ident(path, "/api/jobs/", "") {
                job_action(env, body, id)?
            } else if let Some((id, action)) = super::reminder_route(path) {
                reminder(env, body, id, action)?
            } else {
                return Ok(None);
            }
        }
    }))
}
