//! The goals routes (`kitty.web_goals` and `kitty.core.goals` in Python): goals with
//! measures, the links to what moves them, the brief's suggestions, and the search for
//! things to link. Same values, stored rows, order of checks and refusals as Python. The
//! morning brief (`/api/rika/brief*`, `/api/rika/alerts`) stays with the Python worker: its
//! sweep classifies with a model and schedules itself there. `kitty.core.goals` stays for
//! Rika's tools, the brief and the demo seed.

use std::sync::LazyLock;

use regex::Regex;
use rusqlite::{params, params_from_iter, Connection};
use serde_json::{json, Map, Value};

use crate::ported::time::{self, Date, Dt, Zone, SECOND};
use crate::ported::{db, fail, internal, py, Call, Ctx, Done, Group};

pub static GROUP: Group = Group { owns, module: None, answer };

type Obj = Map<String, Value>;

const GOALS: &str = "/api/goals";
const STATUSES: [&str; 4] = ["active", "paused", "done", "dropped"];
/// Sources an auto measure computes `current` from.
const AUTO: [&str; 4] = ["applications", "study_minutes", "tasks_done", "math_mastered"];
/// Link kinds, the feature module each needs (None: the core), the view that shows it and its label.
const KINDS: [(&str, Option<&str>, Option<&str>, &str); 9] = [
    ("note", Some("workspace"), Some("notes"), "note"),
    ("block", Some("workspace"), Some("notes"), "block"),
    ("task", Some("workspace"), Some("calendar"), "task"),
    ("job", Some("applyai"), Some("jobs"), "job"),
    ("study_session", Some("workspace"), Some("study"), "study session"),
    ("math_topic", Some("workspace"), Some("math"), "math topic"),
    ("opportunity", Some("workspace"), Some("opportunities"), "opportunity"),
    ("event", Some("workspace"), Some("calendar"), "event"),
    ("mail", None, None, "email"),
];
const MAX_MEASURES: usize = 10;
const MAX_LINKS: i64 = 200;
const LINKABLE_LIMIT: usize = 20;
const MEASURE_FIELDS: [&str; 7] = ["id", "label", "target", "unit", "current", "auto", "computed"];
const GOAL_FIELDS: [&str; 4] = ["title", "why", "target_date", "measures"];
const MATCH_SELECT: &str = "SELECT m.*,g.title AS goal_title FROM goal_matches m JOIN goals g ON g.id=m.goal_id";

static ONE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/goals/([1-9][0-9]{0,17})$").expect("valid"));
static MATCH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/goals/matches/([1-9][0-9]{0,17})$").expect("valid"));
static CHANGE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^/api/goals/([1-9][0-9]{0,17})(?:/(delete|links|unlink)|/measures/([A-Za-z0-9_-]{1,40}))?$").expect("valid"));
static INT_REF: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[1-9][0-9]{0,17}$").expect("valid"));
static MEASURE_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Za-z0-9_-]{1,40}$").expect("valid"));
static DASHED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{4}-\d{2}-\d{2}$").expect("valid"));

fn owns(path: &str, _mutation: bool) -> bool {
    path == GOALS || path.starts_with("/api/goals/")
}

fn kinds_list() -> String {
    KINDS.iter().map(|kind| kind.0).collect::<Vec<_>>().join(", ")
}

fn kind_of(name: &str) -> Option<&'static (&'static str, Option<&'static str>, Option<&'static str>, &'static str)> {
    KINDS.iter().find(|kind| kind.0 == name)
}

/// `datetime.now(timezone.utc).isoformat(timespec="microseconds")`.
fn now() -> String {
    let at = Dt::now_utc();
    let of_day = at.time_of_day();
    format!("{}T{:02}:{:02}:{:02}.{:06}+00:00", at.date().iso(), of_day / time::HOUR, of_day / time::MINUTE % 60, of_day / SECOND % 60, of_day % SECOND)
}

/// Today in the calendar's local zone, which decides 'today' for goals.
fn today(conn: &Connection) -> Done<Date> {
    let name: String = conn.query_row("SELECT timezone FROM calendar_calendars WHERE id='personal'", [], |row| row.get(0))?;
    let zone = Zone::named(&name).ok_or_else(internal)?;
    Ok(Dt::now_utc().astimezone(&zone).map_err(|_| internal())?.date())
}

/// `kitty.dates.parse`: a stored timestamp, naive ones read as UTC.
fn instant(value: &Value) -> Option<Dt> {
    let text = value.as_str().filter(|text| !text.is_empty())?;
    let parsed = time::fromisoformat(&py::strip(text).replace('Z', "+00:00"))?;
    Some(if parsed.utcoffset().is_none() { parsed.with_zone(Zone::Utc, false) } else { parsed })
}

fn since(value: &Value, start: &Dt) -> bool {
    instant(value).is_some_and(|parsed| start.le(&parsed))
}

fn st<'a>(row: &'a Obj, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

fn truthy(row: &Obj, key: &str) -> bool {
    row.get(key).is_some_and(py::truthy)
}

fn prefix(text: &str, count: usize) -> String {
    text.chars().take(count).collect()
}

/// `round(seconds / 60)`: Python rounds half to even.
fn minutes(seconds: f64) -> i64 {
    (seconds / 60.0).round_ties_even() as i64
}

// Validation -------------------------------------------------------------------

fn text(value: &Value, name: &str, maximum: usize, required: bool) -> Done<String> {
    let Some(value) = value.as_str().filter(|text| !text.contains('\0')) else { return Err(fail(400, format!("{name} must be text"))) };
    let value = py::strip(value);
    if py::len(value) > maximum || (required && value.is_empty()) {
        return Err(fail(400, format!("{name} must be {}–{maximum} characters", if required { "1" } else { "0" })));
    }
    Ok(value.to_owned())
}

/// A finite JSON number, never a boolean; kept as the integer or float it was sent as.
fn number(value: &Value, name: &str) -> Done<(Value, f64)> {
    match value.as_f64().filter(|_| value.is_number()) {
        Some(float) if float.is_finite() => Ok((value.clone(), float)),
        _ => Err(fail(400, format!("{name} must be a finite number"))),
    }
}

fn target_date(value: Option<&Value>) -> Done<Value> {
    let value = match value {
        None | Some(Value::Null) => return Ok(Value::Null),
        Some(Value::String(text)) if text.is_empty() => return Ok(Value::Null),
        Some(value) => value,
    };
    let Some(text) = value.as_str().filter(|text| DASHED.is_match(text)) else { return Err(fail(400, "The target date must be YYYY-MM-DD")) };
    if Date::parse_dashed(text).is_none() {
        return Err(fail(400, "The target date does not exist"));
    }
    Ok(value.clone())
}

/// Validated measures; an item with a known `id` keeps it (and its current, unless given).
fn measures(value: &Value, previous: &[Value]) -> Done<Vec<Value>> {
    let Some(items) = value.as_array().filter(|items| items.len() <= MAX_MEASURES) else {
        return Err(fail(400, format!("Measures must be a list of at most {MAX_MEASURES}")));
    };
    let mut output = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for item in items {
        let Some(item) = item.as_object().filter(|item| item.keys().all(|key| MEASURE_FIELDS.contains(&key.as_str())) && item.contains_key("label") && item.contains_key("target")) else {
            return Err(fail(400, "A measure needs a label and a target, and only id, unit, current and auto besides"));
        };
        let ident = match item.get("id") {
            None | Some(Value::Null) => {
                let mut bytes = [0u8; 4];
                getrandom::fill(&mut bytes).map_err(|_| internal())?;
                format!("m{}", bytes.iter().map(|byte| format!("{byte:02x}")).collect::<String>())
            }
            Some(Value::String(ident)) if MEASURE_ID.is_match(ident) => ident.clone(),
            Some(_) => return Err(fail(400, "A measure id is 1–40 letters, digits, - or _")),
        };
        if seen.contains(&ident) {
            return Err(fail(400, "Two measures share an id"));
        }
        seen.push(ident.clone());
        let (target, size) = number(&item["target"], "A measure's target")?;
        if size <= 0.0 {
            return Err(fail(400, "A measure's target must be above 0"));
        }
        let auto = item.get("auto").cloned().unwrap_or(Value::Null);
        if !auto.is_null() && !auto.as_str().is_some_and(|auto| AUTO.contains(&auto)) {
            return Err(fail(400, format!("A measure's auto source must be one of {}", AUTO.join(", "))));
        }
        let current = match item.get("current") {
            Some(current) => {
                let (current, size) = number(current, "A measure's current value")?;
                if size < 0.0 {
                    return Err(fail(400, "A measure's current value cannot be negative"));
                }
                current
            }
            None => previous.iter().find(|before| before.get("id").and_then(Value::as_str) == Some(&ident)).and_then(|before| before.get("current")).cloned().unwrap_or(json!(0)),
        };
        let label = text(&item["label"], "A measure's label", 120, true)?;
        let unit = text(item.get("unit").unwrap_or(&json!("")), "A measure's unit", 16, false)?;
        output.push(json!({"id": ident, "label": label, "target": target, "unit": unit, "current": if auto.is_null() { current } else { json!(0) }, "auto": auto}));
    }
    Ok(output)
}

fn revision(row: &Obj, revision: Option<&Value>) -> Done<()> {
    let Some(sent) = revision.and_then(py::int) else { return Err(fail(400, "Send the goal's revision")) };
    if row.get("revision").and_then(Value::as_i64) != Some(sent) {
        return Err(fail(409, "This goal changed since you opened it; reload it and try again"));
    }
    Ok(())
}

fn goal_row(conn: &Connection, goal_id: i64) -> Done<Obj> {
    db::row(conn, "SELECT * FROM goals WHERE id=?", [goal_id])?.ok_or_else(|| fail(404, "Goal not found"))
}

fn stored_measures(row: &Obj) -> Done<Vec<Value>> {
    serde_json::from_str(st(row, "measures_json")).map_err(|_| internal())
}

// Links ------------------------------------------------------------------------

fn int_ref(reference: &str) -> Option<i64> {
    INT_REF.is_match(reference).then(|| reference.parse().ok()).flatten()
}

/// (title, detail) of a link target, or None when it does not exist.
fn resolve(conn: &Connection, kind: &str, reference: &str) -> Done<Option<(Value, String)>> {
    let number = int_ref(reference);
    let by_number = |sql: &str| match number {
        Some(number) => db::row(conn, sql, [number]),
        None => Ok(None),
    };
    Ok(match kind {
        // Pages and outline items come from the block projection (Notes numbers); note_pages is frozen.
        "note" => by_number("SELECT p.title,n.title AS notebook FROM note_page_index p JOIN note_notebooks n ON n.id=p.notebook_id WHERE p.id=?")?
            .map(|row| (json!(if truthy(&row, "title") { st(&row, "title") } else { "Untitled" }), format!("Page in {}", st(&row, "notebook")))),
        "block" => db::row(conn, "SELECT text,title FROM note_outline WHERE id=?", [reference])?.map(|row| {
            let stripped = py::strip(st(&row, "text"));
            let end = stripped.find(['\n', '\r', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\u{85}', '\u{2028}', '\u{2029}']).unwrap_or(stripped.len());
            let line = prefix(&stripped[..end], 120);
            (json!(if line.is_empty() { "Empty block".to_owned() } else { line }), format!("Block on {}", st(&row, "title")))
        }),
        "task" => match db::row(conn, "SELECT data FROM calendar_tasks WHERE id=? AND deleted=0", [reference])? {
            None => None,
            Some(row) => {
                let data: Obj = serde_json::from_str(st(&row, "data")).map_err(|_| internal())?;
                let status = match st(&data, "status") {
                    "todo" => "To do",
                    "doing" => "Doing",
                    "done" => "Done",
                    "cancelled" => "Cancelled",
                    _ => "",
                };
                let due = if truthy(&data, "due") { format!("due {}", prefix(st(&data, "due"), 10)) } else { String::new() };
                let detail = [status, due.as_str()].into_iter().filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" · ");
                Some((data.get("title").cloned().ok_or_else(internal)?, detail))
            }
        },
        "event" => db::row(conn, "SELECT title,start,all_day FROM calendar_events WHERE id=? AND deleted=0", [reference])?.map(|row| {
            let start = st(&row, "start");
            (row["title"].clone(), if truthy(&row, "all_day") { prefix(start, 10) } else { prefix(start, 16).replace('T', " ") })
        }),
        "job" => by_number("SELECT j.title,c.display_name,a.status FROM jobs j JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=?")?
            .map(|row| (row["title"].clone(), format!("{}{}", st(&row, "display_name"), if truthy(&row, "status") { format!(" · {}", st(&row, "status")) } else { String::new() }))),
        "study_session" => by_number("SELECT s.started_at,s.duration_seconds,sub.name FROM study_sessions s LEFT JOIN study_subjects sub ON sub.id=s.subject_id WHERE s.id=?")?.map(|row| {
            let name = if truthy(&row, "name") { st(&row, "name") } else { "Study" };
            (json!(format!("{name} · {} min", minutes(row["duration_seconds"].as_f64().unwrap_or(0.0)))), prefix(st(&row, "started_at"), 10))
        }),
        "math_topic" => db::row(conn, "SELECT t.title,t.strand,m.mastered_at FROM math_topics t LEFT JOIN math_mastery m ON m.topic_id=t.id WHERE t.id=?", [reference])?
            .map(|row| (row["title"].clone(), format!("{}{}", st(&row, "strand"), if truthy(&row, "mastered_at") { " · mastered" } else { "" }))),
        "opportunity" => by_number("SELECT title,category,deadline FROM opportunities WHERE id=?")?.map(|row| {
            let deadline = if truthy(&row, "deadline") { format!(" · deadline {}", prefix(st(&row, "deadline"), 10)) } else { String::new() };
            (row["title"].clone(), format!("{}{deadline}", st(&row, "category")))
        }),
        "mail" => db::row(conn, "SELECT subject,sender,sender_email FROM rika_mail_messages WHERE id=?", [reference])?.map(|row| {
            let from = if truthy(&row, "sender") { st(&row, "sender") } else { st(&row, "sender_email") };
            (json!(if truthy(&row, "subject") { st(&row, "subject") } else { "(no subject)" }), format!("From {from}"))
        }),
        _ => return Err(fail(400, format!("Links are to one of {}", kinds_list()))),
    })
}

fn link_json(conn: &Connection, kind: &str, reference: &str) -> Done<Value> {
    let known = kind_of(kind).ok_or_else(internal)?;
    Ok(match resolve(conn, kind, reference)? {
        None => json!({"kind": kind, "ref": reference, "title": format!("Deleted {}", known.3), "detail": "", "view": null, "missing": true}),
        Some((title, detail)) => json!({"kind": kind, "ref": reference, "title": title, "detail": detail, "view": known.2, "missing": false}),
    })
}

fn link_args(kind: &Value, reference: &Value) -> Done<(&'static str, String)> {
    let known = match kind {
        Value::Array(_) | Value::Object(_) => return Err(internal()),
        kind => kind.as_str().and_then(kind_of).ok_or_else(|| fail(400, format!("Links are to one of {}", kinds_list())))?,
    };
    let reference = match reference {
        Value::Number(_) if py::int(reference).is_some() => reference.to_string(),
        Value::String(text) if (1..=200).contains(&py::len(text)) && !text.contains('\0') => text.clone(),
        _ => return Err(fail(400, "A link needs the item's id as ref")),
    };
    Ok((known.0, reference))
}

fn refs(conn: &Connection, goal_id: i64, kind: &str) -> Done<Vec<String>> {
    let mut statement = conn.prepare("SELECT ref FROM goal_links WHERE goal_id=? AND kind=?")?;
    let found = statement.query_map(params![goal_id, kind], |row| row.get::<_, String>(0))?.collect::<Result<Vec<_>, _>>()?;
    Ok(found)
}

fn marks(count: usize) -> String {
    vec!["?"; count].join(",")
}

// Measures computed from data --------------------------------------------------

fn auto_current(conn: &Connection, row: &Obj, source: &str) -> Done<i64> {
    let goal_id = row["id"].as_i64().ok_or_else(internal)?;
    // Most core timestamps have whole seconds; the goal's own has microseconds.
    let created = instant(&row["created_at"]).ok_or_else(internal)?;
    let created = created.add(-(created.time_of_day() % SECOND)).map_err(|_| internal())?;
    let column = |sql: &str| db::rows(conn, sql, []);
    Ok(match source {
        "applications" => {
            let linked: Vec<i64> = refs(conn, goal_id, "job")?.iter().filter_map(|reference| int_ref(reference)).collect();
            if linked.is_empty() {
                column("SELECT applied_at FROM applications")?.iter().filter(|item| since(&item["applied_at"], &created)).count() as i64
            } else {
                conn.query_row(&format!("SELECT count(*) FROM applications WHERE job_id IN ({})", marks(linked.len())), params_from_iter(linked), |row| row.get(0))?
            }
        }
        "study_minutes" => {
            let linked: Vec<i64> = refs(conn, goal_id, "study_session")?.iter().filter_map(|reference| int_ref(reference)).collect();
            let seconds: f64 = if linked.is_empty() {
                column("SELECT ended_at,duration_seconds FROM study_sessions")?.iter().filter(|item| since(&item["ended_at"], &created)).map(|item| item["duration_seconds"].as_f64().unwrap_or(0.0)).sum()
            } else {
                conn.query_row(&format!("SELECT coalesce(sum(duration_seconds),0) FROM study_sessions WHERE id IN ({})", marks(linked.len())), params_from_iter(linked), |row| row.get(0))?
            };
            minutes(seconds)
        }
        "tasks_done" => {
            let linked = refs(conn, goal_id, "task")?;
            let tasks = if linked.is_empty() {
                column("SELECT data FROM calendar_tasks WHERE deleted=0")?
            } else {
                db::rows(conn, &format!("SELECT data FROM calendar_tasks WHERE deleted=0 AND id IN ({})", marks(linked.len())), params_from_iter(linked.iter()))?
            };
            let mut done = 0;
            for task in tasks {
                let data: Obj = serde_json::from_str(st(&task, "data")).map_err(|_| internal())?;
                if st(&data, "status") == "done" && (!linked.is_empty() || since(data.get("completed_at").unwrap_or(&Value::Null), &created)) {
                    done += 1;
                }
            }
            done
        }
        _ => {
            let linked = refs(conn, goal_id, "math_topic")?;
            if linked.is_empty() {
                column("SELECT mastered_at FROM math_mastery WHERE mastered_at IS NOT NULL")?.iter().filter(|item| since(&item["mastered_at"], &created)).count() as i64
            } else {
                conn.query_row(&format!("SELECT count(*) FROM math_mastery WHERE mastered_at IS NOT NULL AND topic_id IN ({})", marks(linked.len())), params_from_iter(linked.iter()), |row| row.get(0))?
            }
        }
    })
}

fn computed(conn: &Connection, row: &Obj) -> Done<Vec<Value>> {
    let mut output = Vec::new();
    for item in stored_measures(row)? {
        let Value::Object(mut item) = item else { return Err(internal()) };
        let source = item.get("auto").and_then(Value::as_str).filter(|auto| AUTO.contains(auto)).map(str::to_owned);
        if let Some(source) = &source {
            item.insert("current".into(), json!(auto_current(conn, row, source)?));
        } else if !item.contains_key("current") {
            return Err(internal());
        }
        item.insert("computed".into(), json!(source.is_some()));
        output.push(Value::Object(item));
    }
    Ok(output)
}

/// `round(x, 4)`: correctly rounded on the exact binary value, as CPython does.
fn progress(status: &str, measures: &[Value]) -> f64 {
    if status == "done" {
        return 1.0;
    }
    if measures.is_empty() {
        return 0.0;
    }
    let total: f64 = measures.iter().map(|item| (item["current"].as_f64().unwrap_or(0.0) / item["target"].as_f64().unwrap_or(1.0)).clamp(0.0, 1.0)).sum();
    format!("{:.4}", total / measures.len() as f64).parse().unwrap_or(0.0)
}

// Output -----------------------------------------------------------------------

fn match_json(row: &Obj) -> Value {
    let score = format!("{:.3}", row["score"].as_f64().unwrap_or(0.0)).parse::<f64>().unwrap_or(0.0);
    json!({
        "id": row["id"], "goal_id": row["goal_id"], "goal_title": row["goal_title"], "kind": row["kind"], "ref": row["ref"],
        "title": row["title"], "detail": row["detail"], "score": score, "urgent": truthy(row, "urgent"), "due_at": row["due_at"],
        "classified_by": row["classified_by"], "status": row["status"], "view": kind_of(st(row, "kind")).and_then(|kind| kind.2),
        "created_at": row["created_at"],
    })
}

fn goal_json(conn: &Connection, row: &Obj, detail: bool) -> Done<Value> {
    let goal_id = row["id"].as_i64().ok_or_else(internal)?;
    let measures = computed(conn, row)?;
    let days_left = match row["target_date"].as_str() {
        Some(target) => json!(Date::parse_dashed(target).ok_or_else(internal)?.days() - today(conn)?.days()),
        None => Value::Null,
    };
    let links: i64 = conn.query_row("SELECT count(*) FROM goal_links WHERE goal_id=?", [goal_id], |row| row.get(0))?;
    let suggestions: i64 = conn.query_row("SELECT count(*) FROM goal_matches WHERE goal_id=? AND status='new'", [goal_id], |row| row.get(0))?;
    let mut result = json!({
        "id": goal_id, "title": row["title"], "why": row["why"], "target_date": row["target_date"], "status": row["status"],
        "progress": progress(st(row, "status"), &measures), "measures": measures, "days_left": days_left, "links_count": links,
        "suggestions_count": suggestions, "revision": row["revision"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    });
    if detail {
        let mut links = Vec::new();
        for link in db::rows(conn, "SELECT kind,ref FROM goal_links WHERE goal_id=? ORDER BY created_at,kind,ref", [goal_id])? {
            links.push(link_json(conn, st(&link, "kind"), st(&link, "ref"))?);
        }
        let found = db::rows(conn, &format!("{MATCH_SELECT} WHERE m.goal_id=? AND m.status='new' ORDER BY m.urgent DESC,m.score DESC,m.id DESC"), [goal_id])?;
        result["links"] = json!(links);
        result["suggestions"] = json!(found.iter().map(match_json).collect::<Vec<_>>());
    }
    Ok(result)
}

/// Every goal: active first, then paused, done and dropped; each by target date (undated last).
fn listing(conn: &Connection) -> Done<Vec<Value>> {
    let rows = db::rows(conn, "SELECT * FROM goals ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'done' THEN 2 ELSE 3 END, target_date IS NULL,target_date,id", [])?;
    rows.iter().map(|row| goal_json(conn, row, false)).collect()
}

/// One goal with its links and the brief's open suggestions.
fn get(conn: &Connection, goal_id: i64) -> Done<Value> {
    goal_json(conn, &goal_row(conn, goal_id)?, true)
}

// Changes ----------------------------------------------------------------------

fn create(conn: &Connection, body: &Obj) -> Done<Value> {
    if body.keys().any(|key| !GOAL_FIELDS.contains(&key.as_str())) || !body.contains_key("title") {
        return Err(fail(400, "A goal needs a title, and only why, target_date and measures besides"));
    }
    let title = text(&body["title"], "The title", 200, true)?;
    let why = text(body.get("why").unwrap_or(&json!("")), "The why", 2000, false)?;
    let target = target_date(body.get("target_date"))?;
    let measures = measures(body.get("measures").unwrap_or(&json!([])), &[])?;
    let stamp = now();
    let goal_id = db::transaction(conn, false, || {
        conn.execute("INSERT INTO goals(title,why,target_date,measures_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            params![title, why, target.as_str(), py::dumps(&json!(measures)), stamp, stamp])?;
        Ok(conn.last_insert_rowid())
    })?;
    get(conn, goal_id)
}

fn update(conn: &Connection, goal_id: i64, body: &Obj) -> Done<Value> {
    db::transaction(conn, true, || {
        let row = goal_row(conn, goal_id)?;
        revision(&row, body.get("revision"))?;
        let title = match body.get("title") {
            Some(title) => text(title, "The title", 200, true)?,
            None => st(&row, "title").to_owned(),
        };
        let why = match body.get("why") {
            Some(why) => text(why, "The why", 2000, false)?,
            None => st(&row, "why").to_owned(),
        };
        let target = if body.contains_key("target_date") { target_date(body.get("target_date"))? } else { row["target_date"].clone() };
        let measures_json = match body.get("measures") {
            Some(sent) => py::dumps(&json!(measures(sent, &stored_measures(&row)?)?)),
            None => st(&row, "measures_json").to_owned(),
        };
        let status = match body.get("status") {
            Some(Value::Array(_) | Value::Object(_)) => return Err(internal()),
            Some(status) => status.as_str().filter(|status| STATUSES.contains(status)).ok_or_else(|| fail(400, format!("The status must be one of {}", STATUSES.join(", "))))?,
            None => st(&row, "status"),
        };
        conn.execute("UPDATE goals SET title=?,why=?,target_date=?,status=?,measures_json=?,revision=revision+1,updated_at=? WHERE id=?",
            params![title, why, target.as_str(), status, measures_json, now(), goal_id])?;
        Ok(())
    })?;
    get(conn, goal_id)
}

fn delete(conn: &Connection, goal_id: i64, sent: &Value) -> Done<i64> {
    db::transaction(conn, true, || {
        revision(&goal_row(conn, goal_id)?, Some(sent))?;
        conn.execute("DELETE FROM goals WHERE id=?", [goal_id])?;
        Ok(goal_id)
    })
}

/// Link an existing item; linking it twice is a no-op. `modules`: None skips the module check (a brief's match).
fn link(conn: &Connection, goal_id: i64, kind: &Value, reference: &Value, modules: Option<&[String]>) -> Done<Value> {
    let (kind, reference) = link_args(kind, reference)?;
    let known = kind_of(kind).ok_or_else(internal)?;
    if let (Some(modules), Some(module)) = (modules, known.1) {
        if !modules.iter().any(|active| active == module) {
            return Err(fail(403, format!("Linking a {} needs the {module} module, which is off", known.3)));
        }
    }
    db::transaction(conn, true, || {
        goal_row(conn, goal_id)?;
        if resolve(conn, kind, &reference)?.is_none() {
            return Err(fail(404, format!("No such {}", known.3)));
        }
        let held: i64 = conn.query_row("SELECT count(*) FROM goal_links WHERE goal_id=?", [goal_id], |row| row.get(0))?;
        if held >= MAX_LINKS {
            return Err(fail(400, format!("A goal holds at most {MAX_LINKS} links")));
        }
        conn.execute("INSERT OR IGNORE INTO goal_links(goal_id,kind,ref,created_at) VALUES(?,?,?,?)", params![goal_id, kind, reference, now()])?;
        conn.execute("UPDATE goal_matches SET status='linked' WHERE goal_id=? AND kind=? AND ref=? AND status='new'", params![goal_id, kind, reference])?;
        conn.execute("UPDATE goals SET updated_at=? WHERE id=?", params![now(), goal_id])?;
        Ok(())
    })?;
    get(conn, goal_id)
}

fn unlink(conn: &Connection, goal_id: i64, kind: &Value, reference: &Value) -> Done<Value> {
    let (kind, reference) = link_args(kind, reference)?;
    db::transaction(conn, true, || {
        goal_row(conn, goal_id)?;
        if conn.execute("DELETE FROM goal_links WHERE goal_id=? AND kind=? AND ref=?", params![goal_id, kind, reference])? > 0 {
            conn.execute("UPDATE goals SET updated_at=? WHERE id=?", params![now(), goal_id])?;
        }
        Ok(())
    })?;
    get(conn, goal_id)
}

/// Set a manual measure's current value.
fn set_measure(conn: &Connection, goal_id: i64, measure_id: &str, current: &Value) -> Done<Value> {
    let (value, size) = number(current, "The current value")?;
    if size < 0.0 {
        return Err(fail(400, "The current value cannot be negative"));
    }
    db::transaction(conn, true, || {
        let row = goal_row(conn, goal_id)?;
        let mut measures = stored_measures(&row)?;
        let Some(item) = measures.iter_mut().find(|entry| entry.get("id").and_then(Value::as_str) == Some(measure_id)) else { return Err(fail(404, "Measure not found")) };
        if item.get("auto").is_some_and(py::truthy) {
            return Err(fail(409, "This measure is computed from linked data, so it cannot be set by hand"));
        }
        item["current"] = value;
        conn.execute("UPDATE goals SET measures_json=?,revision=revision+1,updated_at=? WHERE id=?", params![py::dumps(&json!(measures)), now(), goal_id])?;
        Ok(())
    })?;
    get(conn, goal_id)
}

/// Link a brief's match to its goal, or dismiss it for good.
fn decide_match(conn: &Connection, match_id: i64, decision: &Value) -> Done<Value> {
    let decision = match decision {
        Value::Array(_) | Value::Object(_) => return Err(internal()),
        decision => decision.as_str().filter(|decision| matches!(*decision, "link" | "dismiss")).ok_or_else(|| fail(400, "Decide link or dismiss"))?,
    };
    let row = db::row(conn, "SELECT * FROM goal_matches WHERE id=?", [match_id])?.ok_or_else(|| fail(404, "Suggestion not found"))?;
    if st(&row, "status") != "new" {
        return Err(fail(409, format!("This suggestion was already {}", st(&row, "status"))));
    }
    let goal_id = row["goal_id"].as_i64().ok_or_else(internal)?;
    if decision == "dismiss" {
        db::transaction(conn, false, || Ok(conn.execute("UPDATE goal_matches SET status='dismissed' WHERE id=?", [match_id])?))?;
        return get(conn, goal_id);
    }
    if resolve(conn, st(&row, "kind"), st(&row, "ref"))?.is_none() {
        return Err(fail(404, format!("That {} no longer exists", kind_of(st(&row, "kind")).ok_or_else(internal)?.3)));
    }
    link(conn, goal_id, &row["kind"], &row["ref"], None)
}

// Search for things to link ----------------------------------------------------

fn search(conn: &Connection, kind: &str, query: &str, limit: usize) -> Done<Vec<Value>> {
    let pattern = format!("%{}%", query.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_"));
    let limit = limit as i64;
    let (sql, twice) = match kind {
        "note" => ("SELECT id AS ref FROM note_page_index WHERE title LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT ?", false),
        "block" => ("SELECT id AS ref FROM note_outline WHERE text<>'' AND text LIKE ? ESCAPE '\\' LIMIT ?", false),
        "task" => ("SELECT id AS ref FROM calendar_tasks WHERE deleted=0 AND json_extract(data,'$.title') LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT ?", false),
        "event" => ("SELECT id AS ref FROM calendar_events WHERE deleted=0 AND title LIKE ? ESCAPE '\\' ORDER BY start DESC LIMIT ?", false),
        "job" => ("SELECT j.id AS ref FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.title LIKE ? ESCAPE '\\' OR c.display_name LIKE ? ESCAPE '\\' ORDER BY j.last_seen_at DESC LIMIT ?", true),
        "study_session" => ("SELECT s.id AS ref FROM study_sessions s LEFT JOIN study_subjects sub ON sub.id=s.subject_id WHERE coalesce(sub.name,'Study') LIKE ? ESCAPE '\\' OR s.notes LIKE ? ESCAPE '\\' ORDER BY s.started_at DESC LIMIT ?", true),
        "math_topic" => ("SELECT id AS ref FROM math_topics WHERE title LIKE ? ESCAPE '\\' OR strand LIKE ? ESCAPE '\\' ORDER BY position LIMIT ?", true),
        "opportunity" => ("SELECT id AS ref FROM opportunities WHERE title LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT ?", false),
        _ => ("SELECT id AS ref FROM rika_mail_messages WHERE subject LIKE ? ESCAPE '\\' OR sender LIKE ? ESCAPE '\\' ORDER BY received_at DESC LIMIT ?", true),
    };
    let rows = if twice { db::rows(conn, sql, params![pattern, pattern, limit])? } else { db::rows(conn, sql, params![pattern, limit])? };
    let mut items = Vec::new();
    for row in rows {
        let reference = match &row["ref"] {
            Value::String(text) => text.clone(),
            other => other.to_string(),
        };
        let (title, detail) = resolve(conn, kind, &reference)?.unwrap_or((json!(""), String::new()));
        items.push(json!({"kind": kind, "ref": reference, "title": title, "detail": detail}));
    }
    Ok(items)
}

/// Up to 20 items of the active modules' kinds whose title matches `query` (recent ones when empty).
fn linkable(conn: &Connection, query: &str, kind: Option<&str>, modules: &[String]) -> Done<Vec<Value>> {
    let query = text(&json!(query), "The search", 200, false)?;
    if kind.is_some_and(|kind| kind_of(kind).is_none()) {
        return Err(fail(400, format!("Search one of {}", kinds_list())));
    }
    let mut found = Vec::new();
    for known in KINDS.iter().filter(|known| kind.is_none_or(|kind| kind == known.0) && known.1.is_none_or(|module| modules.iter().any(|active| active == module))) {
        found.push(search(conn, known.0, &query, LINKABLE_LIMIT)?);
    }
    // Round-robin, so one busy kind does not crowd out the others.
    let mut items = Vec::new();
    for index in 0..LINKABLE_LIMIT {
        for group in &found {
            if let Some(item) = group.get(index) {
                items.push(item.clone());
            }
        }
    }
    items.truncate(LINKABLE_LIMIT);
    Ok(items)
}

// Routes -----------------------------------------------------------------------

/// `web_goals._keys`.
fn keys(body: &Obj, allowed: &[&str], required: &[&str]) -> Done<()> {
    if body.keys().any(|key| !allowed.contains(&key.as_str())) || required.iter().any(|key| !body.contains_key(*key)) {
        return Err(fail(400, "Unexpected or missing goal request fields"));
    }
    Ok(())
}

fn id(text: &str) -> Done<i64> {
    text.parse().map_err(|_| internal())
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    if !call.mutation() {
        if call.path == GOALS {
            call.no_query()?;
            return Ok(Some(json!({"goals": listing(conn)?})));
        }
        if call.path == "/api/goals/linkable" {
            if !call.only(&["q", "kind"]) {
                return Err(fail(400, "Search accepts only q and kind"));
            }
            return Ok(Some(json!({"items": linkable(conn, call.get("q").unwrap_or(""), call.get("kind"), ctx.modules)?})));
        }
        let Some(found) = ONE.captures(call.path) else { return Ok(None) };
        call.no_query()?;
        return Ok(Some(json!({"goal": get(conn, id(&found[1])?)?})));
    }
    let body = call.body();
    if call.path == GOALS {
        return Ok(Some(json!({"goal": create(conn, body)?})));
    }
    if let Some(found) = MATCH.captures(call.path) {
        keys(body, &["decision"], &["decision"])?;
        return Ok(Some(json!({"goal": decide_match(conn, id(&found[1])?, &body["decision"])?})));
    }
    let Some(found) = CHANGE.captures(call.path) else { return Ok(None) };
    let goal_id = id(&found[1])?;
    if let Some(measure) = found.get(3) {
        keys(body, &["current"], &["current"])?;
        return Ok(Some(json!({"goal": set_measure(conn, goal_id, measure.as_str(), &body["current"])?})));
    }
    Ok(Some(match found.get(2).map(|action| action.as_str()) {
        Some("delete") => {
            keys(body, &["revision"], &["revision"])?;
            json!({"deleted": delete(conn, goal_id, &body["revision"])?})
        }
        Some("links") => {
            keys(body, &["kind", "ref"], &["kind", "ref"])?;
            json!({"goal": link(conn, goal_id, &body["kind"], &body["ref"], Some(ctx.modules))?})
        }
        Some("unlink") => {
            keys(body, &["kind", "ref"], &["kind", "ref"])?;
            json!({"goal": unlink(conn, goal_id, &body["kind"], &body["ref"])?})
        }
        _ => {
            keys(body, &["revision", "title", "why", "target_date", "status", "measures"], &["revision"])?;
            json!({"goal": update(conn, goal_id, body)?})
        }
    }))
}
