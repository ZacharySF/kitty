//! The job reads: the lists and their selection (`/api/jobs`, `/api/jobs/selection`), the
//! options, one job, its tool panel, the pipeline board and the reminders, as the deleted
//! `web_jobs.get` and `WebApplication._applyai_get` answered them.

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::config::JobConfig;
use super::dates;
use super::filter::{self, Filter};
use super::model::{self, Job, Resume};
use super::{integer, text, Env};
use crate::ported::db::{row, rows};
use crate::ported::{fail, internal, Call, Done};

const QUEUE_STATES: [&str; 5] = ["new", "queued", "later", "skipped", "not_eligible"];
const STATUSES: [&str; 7] = ["applied", "oa", "interview", "offer", "rejected", "ghosted", "withdrawn"];
const ELIGIBILITY: [&str; 5] = ["eligible", "boosted", "ambiguous", "unknown", "ineligible"];
/// `models.PIPELINE_STATUSES`.
const PIPELINE_STATUSES: [&str; 6] = ["applied", "oa", "interview", "offer", "rejected", "ghosted"];

/// `sources.list_sources`' names: enabled first, then by name.
fn source_names(conn: &Connection) -> Done<Vec<Value>> {
    Ok(rows(conn, "SELECT name FROM sources ORDER BY enabled DESC, name", [])?.into_iter()
        .map(|mut found| found.remove("name").unwrap_or(Value::Null)).collect())
}

/// `WebApplication._job`.
pub fn job(conn: &Connection, job_id: i64) -> Done<Job> {
    model::get_job(conn, job_id)?.ok_or_else(|| fail(404, "Job not found"))
}

/// `resume_versions.default_resume`: an explicit override wins over the track's YAML label.
pub fn default_resume(conn: &Connection, config: &JobConfig, track: &str) -> Done<Option<Resume>> {
    if let Some(found) = row(conn, "SELECT resume_id FROM resume_default_overrides WHERE track=?", [track])? {
        return match found.get("resume_id").ok_or_else(internal)? {
            Value::Null => Ok(None),
            id => model::get_resume(conn, id.as_i64().ok_or_else(internal)?),
        };
    }
    match config.track(track).and_then(|configured| configured.default_resume.as_deref()).filter(|label| !label.is_empty()) {
        Some(label) => model::get_resume_by_label(conn, label),
        None => Ok(None),
    }
}

/// `filler.choose_resume`: the job's chosen resume, else its first track's default.
pub fn choose_resume(conn: &Connection, config: &JobConfig, job: &Job) -> Done<Option<Resume>> {
    if let Some(resume_id) = job.resume_id()? {
        if let Some(chosen) = model::get_resume(conn, resume_id)? {
            return Ok(Some(chosen));
        }
    }
    match job.tracks.first() {
        Some(track) => default_resume(conn, config, track.as_str().ok_or_else(internal)?),
        None => Ok(None),
    }
}

/// `GET /api/jobs` and `/api/jobs/selection`.
fn listing(env: &Env, call: &Call, selection: bool) -> Done<Value> {
    let (conn, config) = (env.conn, env.config()?);
    if !call.only(&["view", "search", "track", "tier", "offset", "limit", "saved", "source", "eligibility", "status", "state",
                    "closed", "order", "company", "age", "favorites"]) {
        return Err(fail(400, "Unknown jobs query parameter"));
    }
    let view = call.get("view").unwrap_or("jobs");
    let mut filt = filter::for_view(if view == "apply" { "queue" } else { view }, config)?;
    if view == "apply" || view == "inbox" {
        filt.eligibility = Some(filter::NOT_FILTERED_OUT.iter().map(|value| (*value).to_owned()).collect());
    }
    if view == "apply" {
        filt.queue_states = Some(vec!["new".into(), "queued".into()]);
        filt.tiers = Some(vec!["A".into(), "B".into()]);
    }
    if view == "inbox" {
        filt.posted_within_days = Some(json!(14));
    }
    let saved = call.get("saved").unwrap_or("");
    if !saved.is_empty() {
        let key = integer(saved, "Saved view", 0, 9)?;
        let configured = config.views.iter().find(|view| view.key == key).ok_or_else(|| fail(400, "Saved view not found"))?;
        filt.apply_view(&configured.filters);
    }
    let search = text(&Value::String(call.get("search").unwrap_or("").to_owned()), "Search", 500)?;
    let company = call.get("company").unwrap_or("0");
    if company != "0" && company != "1" {
        return Err(fail(400, "Company filter must be 0 or 1"));
    }
    if !search.is_empty() {
        if company == "1" {
            filt.company_text = Some(search);
        } else {
            filt.text = Some(search);
        }
    }
    if let Some(age) = call.get("age") {
        filt.posted_within_days = if age.is_empty() { None } else { Some(json!(integer(age, "Age", 1, 36500)?)) };
    }
    let favorites = call.get("favorites").unwrap_or("");
    if favorites != "" && favorites != "1" {
        return Err(fail(400, "Favorites must be 1 when enabled"));
    }
    if favorites == "1" {
        filt.favorites_only = Some(true);
    }
    let sources = source_names(conn)?;
    let tracks: Vec<&str> = config.track_names().collect();
    for parameter in ["track", "tier", "status", "state", "eligibility", "source"] {
        let value = call.get(parameter).unwrap_or("");
        if value.is_empty() {
            continue;
        }
        let allowed = match parameter {
            "track" => tracks.contains(&value),
            "tier" => matches!(value, "A" | "B" | "C"),
            "status" => STATUSES.contains(&value),
            "state" => QUEUE_STATES.contains(&value) || value == "applied",
            "eligibility" => ELIGIBILITY.contains(&value),
            _ => sources.iter().any(|name| name == value),
        };
        if !allowed {
            return Err(fail(400, format!("Unknown {parameter}")));
        }
        let field = match parameter {
            "track" => &mut filt.tracks,
            "tier" => &mut filt.tiers,
            "status" => &mut filt.statuses,
            "state" => &mut filt.queue_states,
            "eligibility" => &mut filt.eligibility,
            _ => &mut filt.sources,
        };
        Filter::choose(field, value);
    }
    let closed = call.get("closed").unwrap_or("");
    if !matches!(closed, "" | "0" | "1") {
        return Err(fail(400, "Closed must be 0 or 1"));
    }
    if !closed.is_empty() {
        filt.include_closed = closed == "1";
    }
    let order = call.get("order").unwrap_or("");
    if !order.is_empty() {
        if !matches!(order, "best" | "newest" | "company" | "deadline" | "score" | "track") {
            return Err(fail(400, "Unknown ordering"));
        }
        filt.order = order.to_owned();
    }
    if view == "apply" {
        filt.include_closed = false;
    }
    let offset = integer(call.get("offset").unwrap_or("0"), "offset", 0, 100_000)?;
    let limit = integer(call.get("limit").unwrap_or("30"), "limit", 1, 100)?;
    let total = filt.count(conn)?;
    if selection {
        if total > 10_000 {
            return Err(fail(400, "Narrow the search to at most 10,000 jobs before selecting all"));
        }
        filt.limit = Some(10_000);
        let ids = filt.list(conn)?.iter().map(|job| job.get("id").cloned()).collect::<Done<Vec<Value>>>()?;
        return Ok(json!({"ids": ids, "total": total}));
    }
    let page: Vec<Job> = if view == "apply" && (order.is_empty() || order == "best") {
        filt.limit = None;
        let mut ranked = Vec::new();
        for job in filt.list(conn)? {
            let key = (job.get("fill_state")? == "ready", job.get("tier")? != "A");
            ranked.push((key, job));
        }
        // `list.sort` is stable.
        ranked.sort_by(|a, b| a.0.cmp(&b.0));
        ranked.into_iter().map(|(_, job)| job).skip(offset as usize).take(limit as usize).collect()
    } else {
        filt.limit = Some(offset + limit);
        if offset < total { filt.list(conn)?.into_iter().skip(offset as usize).collect() } else { Vec::new() }
    };
    let jobs = page.iter().map(|job| job.json().map(Value::Object)).collect::<Done<Vec<Value>>>()?;
    Ok(json!({"jobs": jobs, "total": total}))
}

/// `GET /api/job-options`.
fn options(env: &Env, call: &Call) -> Done<Value> {
    call.no_query()?;
    let config = env.config()?;
    let views: Vec<Value> = config.views.iter().map(|view| json!({"key": view.key, "title": view.title})).collect();
    Ok(json!({"views": views, "sources": source_names(env.conn)?}))
}

/// `pipeline.funnel` as answered: the counts and both rates.
fn funnel(conn: &Connection, track: Option<&str>) -> Done<Value> {
    let mut sql = "SELECT status, COUNT(*) AS n FROM applications WHERE status != 'withdrawn'".to_owned();
    let mut params: Vec<Value> = Vec::new();
    if let Some(track) = track {
        sql.push_str(" AND track = ?");
        params.push(track.into());
    }
    sql.push_str(" GROUP BY status");
    let params = params.iter().map(crate::ported::db::bind).collect::<Done<Vec<_>>>()?;
    let mut counts: Map<String, Value> = Map::new();
    for found in rows(conn, &sql, rusqlite::params_from_iter(params))? {
        if let Some(Value::String(status)) = found.get("status") {
            counts.insert(status.clone(), found.get("n").cloned().unwrap_or(Value::Null));
        }
    }
    let count = |status: &str| counts.get(status).and_then(Value::as_i64).unwrap_or(0);
    let [applied, oa, interview, offer, rejected, ghosted] = PIPELINE_STATUSES.map(count);
    let total = applied + oa + interview + offer + rejected + ghosted;
    let rate = |part: i64| if total == 0 { 0.0 } else { part as f64 / total as f64 };
    Ok(json!({
        "applied": applied, "oa": oa, "interview": interview, "offer": offer, "rejected": rejected, "ghosted": ghosted,
        "response_rate": rate(oa + interview + offer + rejected), "advance_rate": rate(oa + interview + offer),
    }))
}

/// `GET /api/pipeline`: `pipeline.board` (stalest first, withdrawn left out) and the funnel.
fn pipeline(env: &Env, call: &Call) -> Done<Value> {
    if !call.only(&["track"]) {
        return Err(fail(400, "Unknown pipeline parameter"));
    }
    let track = call.get("track").filter(|track| !track.is_empty());
    if let Some(track) = track {
        if !env.config()?.has_track(track) {
            return Err(fail(400, "Unknown track"));
        }
    }
    let mut sql = "
SELECT a.id            AS application_id,
       a.job_id        AS job_id,
       a.status        AS status,
       a.track         AS track,
       a.applied_at    AS applied_at,
       a.last_activity_at AS last_activity_at,
       a.next_action_at   AS next_action_at,
       c.display_name  AS company_name,
       j.title         AS title,
       r.label         AS resume_label,
       EXISTS (SELECT 1 FROM status_events se
                WHERE se.application_id = a.id AND se.state = 'proposed') AS has_proposals
  FROM applications a
  JOIN jobs j      ON j.id = a.job_id
  JOIN companies c ON c.id = j.company_id
  LEFT JOIN resumes r ON r.id = a.resume_id
 WHERE a.status != 'withdrawn'".to_owned();
    if track.is_some() {
        sql.push_str(" AND a.track = ?");
    }
    sql.push_str(" ORDER BY a.last_activity_at");
    let found = match track {
        Some(track) => rows(env.conn, &sql, [track])?,
        None => rows(env.conn, &sql, [])?,
    };
    let mut columns: Map<String, Value> = PIPELINE_STATUSES.iter().map(|status| ((*status).to_owned(), json!([]))).collect();
    for card in found {
        let get = |key: &str| card.get(key).cloned().ok_or_else(internal);
        let activity = get("last_activity_at")?;
        let days = dates::days_since(match &activity {
            Value::Null => None,
            Value::String(text) => Some(text.as_str()),
            _ => return Err(internal()),
        });
        let value = json!({
            "application_id": get("application_id")?, "job_id": get("job_id")?, "company_name": get("company_name")?,
            "title": get("title")?, "status": get("status")?, "track": get("track")?, "resume_label": get("resume_label")?,
            "applied_at": get("applied_at")?, "last_activity_at": activity, "days_since_change": days,
            "next_action_at": get("next_action_at")?, "has_proposals": crate::ported::py::truthy(&get("has_proposals")?),
        });
        let status = get("status")?.as_str().ok_or_else(internal)?.to_owned();
        columns.entry(status).or_insert_with(|| json!([])).as_array_mut().ok_or_else(internal)?.push(value);
    }
    Ok(json!({"columns": columns, "funnel": funnel(env.conn, track)?}))
}

/// `GET /api/reminders`: `today.due_reminders` through today's end, and the next hundred.
fn reminders(env: &Env, call: &Call) -> Done<Value> {
    call.no_query()?;
    let conn = env.conn;
    let now = dates::now();
    let through = format!("{}T23:59:59Z", now.date().iso());
    let mut due = Vec::new();
    for found in rows(conn, "
        SELECT r.id, r.kind, r.due_at, r.note,
               COALESCE(r.job_id, a.job_id) AS job_id,
               c.display_name AS company_name, j.title AS title
          FROM reminders r
          LEFT JOIN applications a ON a.id = r.application_id
          LEFT JOIN jobs j         ON j.id = COALESCE(r.job_id, a.job_id)
          LEFT JOIN companies c    ON c.id = j.company_id
         WHERE r.done_at IS NULL AND r.due_at <= ?
           AND NOT (r.kind = 'followup' AND a.status IS NOT NULL AND a.status != 'applied')
         ORDER BY r.due_at, r.id", [&through])? {
        let get = |key: &str| found.get(key).cloned().ok_or_else(internal);
        let label = if crate::ported::py::truthy(&get("title")?) {
            Value::String(format!("{} - {}", python_str(&get("company_name")?)?, python_str(&get("title")?)?))
        } else if crate::ported::py::truthy(&get("note")?) {
            get("note")?
        } else {
            get("kind")?
        };
        due.push(json!({"id": get("id")?, "kind": get("kind")?, "due_at": get("due_at")?, "note": get("note")?,
                        "job_id": get("job_id")?, "label": label}));
    }
    let found = rows(conn, "SELECT * FROM reminders WHERE done_at IS NULL AND due_at > ? ORDER BY due_at LIMIT ?",
                     rusqlite::params![dates::now_iso()?, 100])?;
    // `jobs.get_job(conn, reminder.job_id)` for each, of which the label reads the company and
    // title: the same rows (`_JOB_SELECT`'s join), read in one query.
    let mut wanted: Vec<i64> = Vec::new();
    for item in &found {
        match item.get("job_id").ok_or_else(internal)? {
            id if crate::ported::py::truthy(id) => wanted.push(id.as_i64().ok_or_else(internal)?),
            _ => {}
        }
    }
    let mut labels: std::collections::HashMap<i64, String> = std::collections::HashMap::new();
    if !wanted.is_empty() {
        let sql = format!("SELECT j.id, c.display_name, j.title FROM jobs j JOIN companies c ON c.id = j.company_id WHERE j.id IN ({})",
                          vec!["?"; wanted.len()].join(","));
        for job in rows(conn, &sql, rusqlite::params_from_iter(&wanted))? {
            let id = job.get("id").and_then(Value::as_i64).ok_or_else(internal)?;
            let get = |key: &str| job.get(key).ok_or_else(internal);
            labels.insert(id, format!("{} — {}", python_str(get("display_name")?)?, python_str(get("title")?)?));
        }
    }
    let mut upcoming = Vec::new();
    for found in &found {
        let mut item = model::reminder(found)?;
        let attached = item.get("job_id").and_then(Value::as_i64).filter(|_| crate::ported::py::truthy(&item["job_id"])).and_then(|id| labels.get(&id));
        let label = match attached {
            Some(label) => Value::String(label.clone()),
            None => {
                let note = item.get("note").ok_or_else(internal)?;
                if crate::ported::py::truthy(note) { note.clone() } else { "Reminder".into() }
            }
        };
        item.insert("label".into(), label);
        upcoming.push(Value::Object(item));
    }
    Ok(json!({"due": due, "upcoming": upcoming}))
}

/// `str(value)` inside an f-string, for the values a label is built from.
pub fn python_str(value: &Value) -> Done<String> {
    Ok(match value {
        Value::Null => "None".into(),
        Value::String(text) => text.clone(),
        Value::Bool(flag) => if *flag { "True" } else { "False" }.into(),
        Value::Number(number) if number.is_f64() => crate::ported::py::float_repr(number.as_f64().ok_or_else(internal)?),
        Value::Number(number) => number.to_string(),
        _ => return Err(internal()),
    })
}

/// `GET /api/job-tools/<id>`.
fn tools(env: &Env, call: &Call, job_id: i64) -> Done<Value> {
    call.no_query()?;
    let conn = env.conn;
    let job = job(conn, job_id)?;
    let application = model::application_for_job(conn, job.id()?)?;
    let apply_url = {
        let own = job.get("apply_url")?;
        if crate::ported::py::truthy(own) { own.clone() } else { job.get("url")?.clone() }
    };
    let (can_undo, followups) = match &application {
        Some(found) => {
            let id = found.get("id").and_then(Value::as_i64).ok_or_else(internal)?;
            let followups = model::followups_for(conn, id)?.into_iter().map(Value::Object).collect::<Vec<_>>();
            (model::last_undoable_event(conn, id)?.is_some(), followups)
        }
        None => (false, Vec::new()),
    };
    Ok(json!({
        "tier": job.get("tier")?, "queue_state": job.get("queue_state")?, "deadline_at": job.deadline_at()?,
        "tracks": job.tracks, "eligibility": job.get("eligibility")?, "apply_url": apply_url,
        "sources": job.sources, "evidence": job.evidence()?, "freshness": job.freshness()?,
        "application": application, "can_undo": can_undo, "followups": followups,
    }))
}

/// `GET /api/jobs/<id>`: the job, its description and notes, the resume it would use (or the
/// one its application recorded) and the application with its status history.
fn detail(env: &Env, call: &Call, job_id: i64) -> Done<Value> {
    call.no_query()?;
    let conn = env.conn;
    let job = job(conn, job_id)?;
    let application = model::application_for_job(conn, job.id()?)?;
    let chosen = match &application {
        Some(found) => match found.get("resume_id").ok_or_else(internal)? {
            Value::Null => None,
            id => model::get_resume(conn, id.as_i64().ok_or_else(internal)?)?,
        },
        None => choose_resume(conn, env.config()?, &job)?,
    };
    let mut result = job.json()?;
    result.insert("description".into(), job.get("jd_text")?.clone());
    result.insert("my_notes".into(), job.get("my_notes")?.clone());
    result.insert("tier_reason".into(), job.get("tier_reason")?.clone());
    result.insert("fill_blocker".into(), job.get("fill_blocker")?.clone());
    result.insert("resume".into(), match chosen {
        Some(resume) => json!({"id": resume.id, "label": resume.label}),
        None => Value::Null,
    });
    result.insert("application".into(), match application {
        Some(mut found) => {
            let id = found.get("id").and_then(Value::as_i64).ok_or_else(internal)?;
            found.insert("history".into(), Value::Array(model::history(conn, id)?));
            Value::Object(found)
        }
        None => Value::Null,
    });
    Ok(Value::Object(result))
}

/// One GET the front answers, or None for a path it does not own.
pub fn answer(env: &Env, call: &Call) -> Done<Option<Value>> {
    let path = call.path;
    Ok(Some(match path {
        "/api/jobs" => listing(env, call, false)?,
        "/api/jobs/selection" => listing(env, call, true)?,
        "/api/job-options" => options(env, call)?,
        "/api/pipeline" => pipeline(env, call)?,
        "/api/reminders" => reminders(env, call)?,
        _ => {
            if let Some(id) = super::ident(path, "/api/job-tools/", "") {
                tools(env, call, id)?
            } else if let Some(id) = super::ident(path, "/api/jobs/", "") {
                detail(env, call, id)?
            } else {
                return super::resumes::answer(env, call);
            }
        }
    }))
}
