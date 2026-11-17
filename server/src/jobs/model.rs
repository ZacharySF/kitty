//! `kitty.models`' job-side rows as the routes return them: `jobs._JOB_SELECT` with its tracks
//! and sources attached (`jobs._attach`), `Job.from_row`'s derived fields and `freshness`, and
//! the `dataclasses.asdict` shapes of applications, status events, reminders and resumes.

use rusqlite::{params_from_iter, Connection};
use serde_json::{json, Map, Value};

use super::dates;
use crate::ported::db::{row, rows};
use crate::ported::time::DAY;
use crate::ported::{internal, py, Done};

/// `jobs._JOB_SELECT`.
pub const JOB_SELECT: &str = "
SELECT j.*,
       c.display_name       AS company_name,
       r.label              AS resume_label,
       a.id                 AS application_id,
       a.status             AS status,
       a.next_action_at     AS next_action_at,
       a.next_action_note   AS next_action_note
  FROM jobs j
  JOIN companies c     ON c.id = j.company_id
  LEFT JOIN resumes r  ON r.id = j.resume_id
  LEFT JOIN applications a ON a.job_id = j.id
";

/// `models.VERIFICATION_MAX_AGE`.
const VERIFICATION_MAX_AGE: i64 = 7 * DAY;

/// A job row with its tracks (by name) and sources (`JobSource` as `asdict`).
pub struct Job {
    row: Map<String, Value>,
    pub tracks: Vec<Value>,
    pub sources: Vec<Value>,
}

/// `row[key]`: a column the select always has.
fn at<'a>(map: &'a Map<String, Value>, key: &str) -> Done<&'a Value> {
    map.get(key).ok_or_else(internal)
}

/// `_loads(raw)`: None for a falsy value or malformed JSON; a truthy non-text value is
/// `json.loads`' TypeError.
pub fn loads(raw: &Value) -> Done<Value> {
    if !py::truthy(raw) {
        return Ok(Value::Null);
    }
    let text = raw.as_str().ok_or_else(internal)?;
    Ok(serde_json::from_str(text).unwrap_or(Value::Null))
}

/// `_loads(raw) or []`.
fn loads_list(raw: &Value) -> Done<Value> {
    let value = loads(raw)?;
    Ok(if py::truthy(&value) { value } else { json!([]) })
}

/// `a or b` of two columns.
fn either(first: &Value, second: &Value) -> Value {
    if py::truthy(first) { first.clone() } else { second.clone() }
}

fn bool_of(value: &Value) -> Value {
    Value::Bool(py::truthy(value))
}

/// `JobSource.from_row` as `asdict`.
fn source(map: &Map<String, Value>) -> Done<Value> {
    let raw = loads(at(map, "raw_json")?)?;
    let listing = raw.get("provenance").and_then(Value::as_object).and_then(|provenance| provenance.get("url_is_listing")) == Some(&Value::Bool(true));
    Ok(json!({
        "source": at(map, "source")?, "source_job_id": at(map, "source_job_id")?, "source_url": at(map, "source_url")?,
        "first_seen_at": at(map, "first_seen_at")?, "last_seen_at": at(map, "last_seen_at")?, "url_is_listing": listing,
    }))
}

fn id_of(map: &Map<String, Value>) -> Done<i64> {
    at(map, "id")?.as_i64().ok_or_else(internal)
}

/// `jobs._attach`: the rows' tracks (by track) and sources (by source), two queries.
fn attach(conn: &Connection, found: Vec<Map<String, Value>>) -> Done<Vec<Job>> {
    if found.is_empty() {
        return Ok(Vec::new());
    }
    let ids = found.iter().map(id_of).collect::<Done<Vec<i64>>>()?;
    let placeholders = vec!["?"; ids.len()].join(",");
    let mut jobs: Vec<Job> = found.into_iter().map(|row| Job { row, tracks: Vec::new(), sources: Vec::new() }).collect();
    let mut positions = std::collections::HashMap::new();
    for (position, id) in ids.iter().enumerate() {
        positions.entry(*id).or_insert_with(Vec::new).push(position);
    }
    for track in rows(conn, &format!("SELECT job_id, track FROM job_tracks WHERE job_id IN ({placeholders}) ORDER BY track"), params_from_iter(&ids))? {
        let job_id = at(&track, "job_id")?.as_i64().ok_or_else(internal)?;
        // `tracks[row["job_id"]]`: one list per id, shared by repeated rows of one job.
        for &position in positions.get(&job_id).ok_or_else(internal)? {
            jobs[position].tracks.push(at(&track, "track")?.clone());
        }
    }
    for found in rows(conn, &format!("SELECT * FROM job_sources WHERE job_id IN ({placeholders}) ORDER BY source"), params_from_iter(&ids))? {
        let job_id = at(&found, "job_id")?.as_i64().ok_or_else(internal)?;
        let value = source(&found)?;
        for &position in positions.get(&job_id).ok_or_else(internal)? {
            jobs[position].sources.push(value.clone());
        }
    }
    Ok(jobs)
}

/// Jobs for `JOB_SELECT` + `tail`, attached.
pub fn select(conn: &Connection, tail: &str, params: &[rusqlite::types::Value]) -> Done<Vec<Job>> {
    attach(conn, rows(conn, &format!("{JOB_SELECT}{tail}"), params_from_iter(params))?)
}

/// `jobs.get_job`.
pub fn get_job(conn: &Connection, job_id: i64) -> Done<Option<Job>> {
    Ok(select(conn, " WHERE j.id = ?", &[job_id.into()])?.into_iter().next())
}

impl Job {
    pub fn get(&self, key: &str) -> Done<&Value> {
        at(&self.row, key)
    }

    pub fn id(&self) -> Done<i64> {
        id_of(&self.row)
    }

    pub fn company_name(&self) -> Done<&Value> {
        self.get("company_name")
    }

    /// `row["deadline_at"] or row["source_deadline_at"]`.
    pub fn deadline_at(&self) -> Done<Value> {
        Ok(either(self.get("deadline_at")?, self.get("source_deadline_at")?))
    }

    pub fn is_active(&self) -> Done<bool> {
        Ok(py::truthy(self.get("is_active")?))
    }

    /// `application_id`: an applied job has one.
    pub fn application_id(&self) -> Done<Option<i64>> {
        match self.get("application_id")? {
            Value::Null => Ok(None),
            value => value.as_i64().map(Some).ok_or_else(internal),
        }
    }

    pub fn resume_id(&self) -> Done<Option<i64>> {
        match self.get("resume_id")? {
            Value::Null => Ok(None),
            value => value.as_i64().map(Some).ok_or_else(internal),
        }
    }

    /// `Job.freshness`.
    pub fn freshness(&self) -> Done<&'static str> {
        if !self.is_active()? || self.get("verification_status")? == "closed" {
            return Ok("closed");
        }
        let verified = self.get("verified_at")?;
        if !py::truthy(verified) {
            return Ok("unverified");
        }
        let Some(checked) = dates::parse(Some(verified.as_str().ok_or_else(internal)?)) else {
            return Ok("unverified");
        };
        let age = dates::now().sub(&checked);
        Ok(if self.get("verification_status")? == "open" && (0..=VERIFICATION_MAX_AGE).contains(&age) { "verified" } else { "stale" })
    }

    pub fn edit_notes(&self) -> Done<Value> {
        loads_list(self.get("edit_notes")?)
    }

    pub fn like_terms(&self) -> Done<Value> {
        loads_list(self.get("like_terms")?)
    }

    /// `web_server._job_json`.
    pub fn json(&self) -> Done<Map<String, Value>> {
        let is_remote = match self.get("is_remote")? {
            Value::Null => Value::Null,
            value => bool_of(value),
        };
        let mut out = Map::new();
        out.insert("id".into(), self.get("id")?.clone());
        out.insert("company".into(), self.company_name()?.clone());
        out.insert("title".into(), self.get("title")?.clone());
        out.insert("location".into(), self.get("location")?.clone());
        // `bool(job.is_remote)`.
        out.insert("is_remote".into(), Value::Bool(py::truthy(&is_remote)));
        out.insert("tier".into(), self.get("tier")?.clone());
        out.insert("tracks".into(), Value::Array(self.tracks.clone()));
        out.insert("url".into(), self.get("url")?.clone());
        out.insert("eligibility".into(), self.get("eligibility")?.clone());
        out.insert("eligibility_reason".into(), self.get("eligibility_reason")?.clone());
        out.insert("queue_state".into(), self.get("queue_state")?.clone());
        out.insert("status".into(), self.get("status")?.clone());
        out.insert("favorite".into(), Value::Bool(!self.get("favorited_at")?.is_null()));
        out.insert("posted_at".into(), self.get("posted_at")?.clone());
        out.insert("deadline_at".into(), self.deadline_at()?);
        out.insert("fill_state".into(), self.get("fill_state")?.clone());
        out.insert("is_active".into(), Value::Bool(self.is_active()?));
        out.insert("closed_at".into(), self.get("closed_at")?.clone());
        out.insert("freshness".into(), self.freshness()?.into());
        out.insert("verification_status".into(), self.get("verification_status")?.clone());
        out.insert("verification_reason".into(), self.get("verification_reason")?.clone());
        out.insert("verification_checked_at".into(), self.get("verification_checked_at")?.clone());
        Ok(out)
    }

    /// The tool panel's `evidence` (`web_jobs.get` for `/api/job-tools/<id>`).
    pub fn evidence(&self) -> Done<Value> {
        let mut out = Map::new();
        for key in ["tier_source", "first_seen_at", "last_seen_at", "closed_at"] {
            out.insert(key.into(), self.get(key)?.clone());
        }
        out.insert("is_active".into(), Value::Bool(self.is_active()?));
        for key in ["school_selective_note", "verification_status", "verified_at", "verification_checked_at",
                    "verification_reason", "fill_attempted_at", "fill_ready_at"] {
            out.insert(key.into(), self.get(key)?.clone());
        }
        out.insert("edit_notes".into(), self.edit_notes()?);
        out.insert("like_score".into(), self.get("like_score")?.clone());
        out.insert("like_terms".into(), self.like_terms()?);
        Ok(Value::Object(out))
    }
}

/// `applications._APP_SELECT`.
const APP_SELECT: &str = "SELECT a.*, r.label AS resume_label FROM applications a LEFT JOIN resumes r ON r.id = a.resume_id";

/// `Application` as `asdict`.
fn application(map: &Map<String, Value>) -> Done<Map<String, Value>> {
    let mut out = Map::new();
    for key in ["id", "job_id", "status", "applied_at", "last_activity_at", "track", "resume_id", "resume_label",
                "next_action_at", "next_action_note", "notes"] {
        out.insert(key.into(), at(map, key)?.clone());
    }
    Ok(out)
}

/// `applications.get_application_for_job`.
pub fn application_for_job(conn: &Connection, job_id: i64) -> Done<Option<Map<String, Value>>> {
    row(conn, &format!("{APP_SELECT} WHERE a.job_id = ?"), [job_id])?.as_ref().map(application).transpose()
}

/// `applications.get_application`.
pub fn get_application(conn: &Connection, application_id: i64) -> Done<Option<Map<String, Value>>> {
    row(conn, &format!("{APP_SELECT} WHERE a.id = ?"), [application_id])?.as_ref().map(application).transpose()
}

/// `StatusEvent` as `asdict`.
pub fn event(map: &Map<String, Value>) -> Done<Value> {
    let mut out = Map::new();
    for key in ["id", "application_id", "from_status", "to_status", "occurred_at", "source", "state", "evidence", "undoes_event_id"] {
        out.insert(key.into(), at(map, key)?.clone());
    }
    Ok(Value::Object(out))
}

/// `applications.history`.
pub fn history(conn: &Connection, application_id: i64) -> Done<Vec<Value>> {
    rows(conn, "SELECT * FROM status_events WHERE application_id = ? ORDER BY id", [application_id])?.iter().map(event).collect()
}

/// `applications.last_undoable_event`.
pub fn last_undoable_event(conn: &Connection, application_id: i64) -> Done<Option<Map<String, Value>>> {
    row(conn, "
        SELECT * FROM status_events
         WHERE application_id = ?
           AND state = 'confirmed'
           AND undoes_event_id IS NULL
           AND from_status IS NOT NULL
           AND id NOT IN (SELECT undoes_event_id FROM status_events
                           WHERE undoes_event_id IS NOT NULL)
         ORDER BY id DESC
         LIMIT 1
        ", [application_id])
}

/// `Reminder` as `asdict`.
pub fn reminder(map: &Map<String, Value>) -> Done<Map<String, Value>> {
    let mut out = Map::new();
    for key in ["id", "kind", "due_at", "note", "job_id", "application_id", "done_at"] {
        out.insert(key.into(), at(map, key)?.clone());
    }
    Ok(out)
}

/// `reminders.followups_for`.
pub fn followups_for(conn: &Connection, application_id: i64) -> Done<Vec<Map<String, Value>>> {
    rows(conn, "SELECT * FROM reminders WHERE application_id = ? AND kind = 'followup' ORDER BY due_at", [application_id])?
        .iter().map(reminder).collect()
}

/// A registered resume (`Resume.from_row`).
pub struct Resume {
    pub id: i64,
    pub label: Value,
    pub file_path: Value,
    pub file_hash: Value,
    pub description: Value,
    pub default_tracks: Value,
    pub active: bool,
}

pub fn resume(map: &Map<String, Value>) -> Done<Resume> {
    Ok(Resume {
        id: id_of(map)?,
        label: at(map, "label")?.clone(),
        file_path: at(map, "file_path")?.clone(),
        file_hash: at(map, "file_hash")?.clone(),
        description: at(map, "description")?.clone(),
        default_tracks: loads_list(at(map, "default_tracks")?)?,
        active: py::truthy(at(map, "active")?),
    })
}

/// `resumes.get_resume`.
pub fn get_resume(conn: &Connection, resume_id: i64) -> Done<Option<Resume>> {
    row(conn, "SELECT * FROM resumes WHERE id = ?", [resume_id])?.as_ref().map(resume).transpose()
}

/// `resumes.get_resume_by_label`.
pub fn get_resume_by_label(conn: &Connection, label: &str) -> Done<Option<Resume>> {
    row(conn, "SELECT * FROM resumes WHERE label = ?", [label])?.as_ref().map(resume).transpose()
}

/// `resumes.list_resumes()`: active ones, by label without case.
pub fn list_resumes(conn: &Connection) -> Done<Vec<Resume>> {
    rows(conn, "SELECT * FROM resumes WHERE active = 1 ORDER BY label COLLATE NOCASE", [])?.iter().map(resume).collect()
}
