//! `kitty.core.calendar` over rusqlite: local calendars, RFC 5545 recurrence, revisioned
//! editing with undo, tasks that repeat, availability, reminders, activity and the reviewed
//! imports. Dates are exclusive-end RFC 5545 dates; times carry offsets and a separate IANA
//! zone. Values, messages, stored rows and their JSON text are Python's.

use std::sync::LazyLock;

use regex::Regex;
use rusqlite::types::{Value as Sql, ValueRef};
use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use super::rrule::{Invalid, Rule, DAILY};
use crate::ported::db::{bind, exists, row, rows, transaction};
use crate::ported::py;
use crate::ported::time::{now_iso, Date, Dt, Zone, DAY, MINUTE, SECOND};
use crate::ported::{fail, internal, Done};

pub type Obj = Map<String, Value>;

pub const EVENT_FIELDS: &[&str] = &["title", "start", "end", "timezone", "all_day", "description", "location", "url",
    "rrule", "exdates", "attendees", "privacy", "busy", "reminders", "calendar_id", "source_key", "source_url",
    "attachments", "event_type", "google_options", "subject_id", "color"];
pub const TASK_FIELDS: &[&str] = &["title", "description", "due", "timezone", "priority", "status", "tags", "project",
    "calendar_id", "repeat_frequency", "repeat_interval", "repeat_until", "subject_id"];
/// Source-key prefixes of copies imported from ApplyAI data: hidden without that module.
const APPLYAI_SOURCES: &[&str] = &["job", "application", "status", "reminder"];
const REFRESHED_SOURCES: &[&str] = &["job", "application", "status", "reminder", "opportunity"];

static COLOR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(?:c[1-6]|#[0-9a-fA-F]{6})$").expect("valid"));
static ALL_DAY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\d{4}-\d{2}-\d{2}$").expect("valid"));
static EMAIL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[^\s@]+@[^\s@]+\.[^\s@]+$").expect("valid"));
static RRULE_TEXT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[A-Z0-9=;,+:\-]+$").expect("valid"));
static UNTIL_DATE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?:^|;)UNTIL=(\d{8})(?:;|$)").expect("valid"));
static COUNT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?:^|;)COUNT=(\d+)").expect("valid"));
static COUNT_ANY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"COUNT=\d+").expect("valid"));

pub fn s(text: &str) -> Value {
    Value::String(text.to_owned())
}

fn bad(message: impl Into<String>) -> crate::ported::Fault {
    fail(400, message)
}

/// `uuid4().hex`.
pub fn uuid() -> String {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).expect("the system random source");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex::encode(bytes)
}

// ------------------------------------------------------------------------------ values

/// `text(value, name, limit)`: the stripped text, or the refusal.
pub fn text(value: &Value, name: &str, limit: usize) -> Done<String> {
    match value {
        Value::String(text) if py::len(text) <= limit && !text.contains('\0') => Ok(py::strip(text).to_owned()),
        _ => Err(bad(format!("{name} must be text of at most {limit} characters"))),
    }
}

fn get<'a>(map: &'a Obj, key: &str, default: &'a Value) -> &'a Value {
    map.get(key).unwrap_or(default)
}

fn str_of<'a>(map: &'a Obj, key: &str) -> &'a str {
    map.get(key).and_then(Value::as_str).unwrap_or("")
}

fn flag(map: &Obj, key: &str) -> bool {
    map.get(key).is_some_and(py::truthy)
}

/// `zone(value)`.
pub fn zone(value: &Value) -> Done<Zone> {
    let name = text(value, "Timezone", 100)?;
    Zone::named(&name).ok_or_else(|| bad("Timezone must be an IANA zone such as America/Chicago"))
}

/// `instant(value, tz, all_day)`.
pub fn instant(value: &Value, tz: &Value, all_day: bool) -> Done<Dt> {
    let value = text(value, "Date", 100)?;
    if all_day {
        if !ALL_DAY.is_match(&value) {
            return Err(bad("All-day values must be YYYY-MM-DD dates"));
        }
        let date = Date::parse_dashed(&value).ok_or_else(|| bad("Invalid ISO8601 date or timestamp"))?;
        return Ok(Dt::midnight(date, Some(zone(tz)?)));
    }
    let parsed = crate::ported::time::fromisoformat(&value.replace('Z', "+00:00")).ok_or_else(|| bad("Invalid ISO8601 date or timestamp"))?;
    if parsed.zone.is_none() {
        return Err(bad("Timed dates require an explicit UTC offset"));
    }
    Ok(parsed.astimezone(&zone(tz)?)?)
}

pub fn instant_utc(value: &Value) -> Done<Dt> {
    instant(value, &s("UTC"), false)
}

pub fn stamp(dt: &Dt, all_day: bool) -> String {
    if all_day { dt.date().iso() } else { dt.isoformat() }
}

fn utc(dt: &Dt) -> Done<Dt> {
    Ok(dt.astimezone(&Zone::Utc)?)
}

/// `wall_instant(value, tz, fold)`: direct local input; DST gaps refused, the fold chosen.
pub fn wall_instant(value: &Value, tz: &Value, fold: &Value) -> Done<String> {
    let invalid = || bad("Invalid local date/time");
    let Value::String(text) = value else { return Err(invalid()) };
    let parsed = crate::ported::time::fromisoformat(text).ok_or_else(invalid)?;
    if parsed.zone.is_some() {
        return Ok(instant(value, tz, false)?.isoformat());
    }
    let place = zone(tz)?;
    let number = match fold {
        Value::Bool(flag) => i64::from(*flag),
        Value::Number(number) => match number.as_i64() {
            Some(value) => value,
            None => {
                let value = number.as_f64().ok_or_else(invalid)?;
                if !value.is_finite() {
                    return Err(internal());
                }
                value.trunc() as i64
            }
        },
        Value::String(text) => {
            let text = py::strip(text).replace('_', "");
            text.parse::<i64>().map_err(|_| invalid())?
        }
        _ => return Err(invalid()),
    };
    if !(0..=1).contains(&number) {
        return Err(invalid());
    }
    let dt = parsed.with_zone(place, number == 1);
    if !dt.exists() {
        return Err(bad("This local time does not exist during the DST transition"));
    }
    if !(py::eq(fold, &json!(0)) || py::eq(fold, &json!(1))) {
        return Err(bad("DST fold must be 0 or 1"));
    }
    Ok(dt.isoformat())
}

/// `_url(value)`: http or https with a host, or empty.
fn url(value: &Value) -> Done<String> {
    let value = text(value, "URL", 4096)?;
    if value.is_empty() {
        return Ok(value);
    }
    // `urlsplit`: tabs and newlines removed, leading C0 controls and spaces stripped.
    let cleaned: String = value.chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
    let cleaned = cleaned.trim_start_matches(|c: char| c <= ' ');
    let (scheme, rest) = match cleaned.find(':') {
        Some(at) if at > 0 && cleaned.as_bytes()[0].is_ascii_alphabetic()
            && cleaned[..at].bytes().all(|b| b.is_ascii_alphanumeric() || b"+-.".contains(&b)) => (cleaned[..at].to_ascii_lowercase(), &cleaned[at + 1..]),
        _ => (String::new(), cleaned),
    };
    let netloc = rest.strip_prefix("//").map(|after| after.split(['/', '?', '#']).next().unwrap_or("")).unwrap_or("");
    if netloc.contains('[') != netloc.contains(']') {
        return Err(internal());
    }
    if !matches!(scheme.as_str(), "https" | "http") || netloc.is_empty() {
        return Err(bad("URLs must use http or https"));
    }
    Ok(value)
}

fn color(value: &Value) -> Done<Value> {
    match value {
        Value::String(text) if COLOR.is_match(text) => Ok(value.clone()),
        _ => Err(bad("Color must be a theme palette slot (c1–c6) or a six-digit hex color")),
    }
}

/// `subject(conn, value)`: a study subject id or None.
fn subject(conn: &Connection, value: &Value) -> Done<Value> {
    match value {
        Value::Null => Ok(Value::Null),
        Value::Number(number) if number.as_i64().is_some() => {
            if exists(conn, "SELECT 1 FROM study_subjects WHERE id=?", [number.as_i64()])? {
                Ok(value.clone())
            } else {
                Err(bad("Study subject not found"))
            }
        }
        _ => Err(bad("Study subject not found")),
    }
}

pub fn applyai_sourced(source_key: &Value) -> bool {
    let key = source_key.as_str().unwrap_or("");
    APPLYAI_SOURCES.contains(&key.split(':').next().unwrap_or(""))
}

// ------------------------------------------------------------------------------ calendars

fn calendar(conn: &Connection, ident: &Value) -> Done<Obj> {
    row(conn, "SELECT * FROM calendar_calendars WHERE id=? AND deleted=0", [bind(ident)?])?.ok_or_else(|| fail(404, "Calendar not found"))
}

pub fn calendars(conn: &Connection) -> Done<Vec<Obj>> {
    rows(conn, "SELECT * FROM calendar_calendars WHERE deleted=0 ORDER BY name", [])
}

pub fn save_calendar(conn: &Connection, body: &Obj) -> Done<Obj> {
    let given = body.get("id").filter(|id| py::truthy(id));
    let ident = given.cloned().unwrap_or_else(|| s(&uuid()));
    let old = match given { Some(id) => Some(calendar(conn, id)?), None => None };
    let from_old = |key: &str, default: Value| old.as_ref().map(|old| old[key].clone()).unwrap_or(default);
    let name = text(body.get("name").unwrap_or(&from_old("name", s(""))), "Calendar name", 200)?;
    if name.is_empty() {
        return Err(bad("Calendar name is required"));
    }
    let shade = color(&match body.get("color") {
        Some(value) => value.clone(),
        None => match &old {
            Some(old) => old["color"].clone(),
            None => {
                let count: i64 = conn.query_row("SELECT COUNT(*) FROM calendar_calendars WHERE deleted=0", [], |r| r.get(0))?;
                s(&format!("c{}", count % 6 + 1))
            }
        },
    })?;
    let linked = subject(conn, &body.get("subject_id").cloned().unwrap_or_else(|| from_old("subject_id", Value::Null)))?;
    let tz = body.get("timezone").cloned().unwrap_or_else(|| from_old("timezone", s("UTC")));
    zone(&tz)?;
    let visible = body.get("visible").cloned().unwrap_or_else(|| Value::Bool(old.as_ref().map(|old| py::truthy(&old["visible"])).unwrap_or(true)));
    let Value::Bool(visible) = visible else { return Err(bad("Visibility must be boolean")) };
    transaction(conn, false, || {
        if let Some(old) = &old {
            if !py::eq(body.get("revision").unwrap_or(&Value::Null), &old["revision"]) {
                return Err(fail(409, "Calendar changed; reload before editing"));
            }
            conn.execute("UPDATE calendar_calendars SET name=?,color=?,subject_id=?,timezone=?,visible=?,revision=revision+1,updated_at=? WHERE id=?",
                params![name, bind(&shade)?, bind(&linked)?, bind(&tz)?, i64::from(visible), now_iso(), bind(&ident)?])?;
        } else {
            conn.execute("INSERT INTO calendar_calendars(id,name,color,subject_id,timezone,visible,updated_at) VALUES(?,?,?,?,?,?,?)",
                params![bind(&ident)?, name, bind(&shade)?, bind(&linked)?, bind(&tz)?, i64::from(visible), now_iso()])?;
        }
        Ok(())
    })?;
    calendar(conn, &ident)
}

/// `require_writable`: a calendar imported from an outside account (`calendar_remote`) is
/// read-only here.
fn require_writable(conn: &Connection, calendar_id: &Value) -> Done<()> {
    if exists(conn, "SELECT 1 FROM calendar_remote WHERE calendar_id=?", [bind(calendar_id)?])? {
        return Err(fail(409, "This calendar comes from Outlook and is read-only in rika!; change it in Outlook"));
    }
    Ok(())
}

pub fn delete_calendar(conn: &Connection, body: &Obj) -> Done<Value> {
    let cal = calendar(conn, body.get("id").unwrap_or(&Value::Null))?;
    if cal["id"] == "personal" {
        return Err(bad("Personal is the default calendar and cannot be deleted"));
    }
    require_writable(conn, &cal["id"])?;
    if !py::eq(body.get("revision").unwrap_or(&Value::Null), &cal["revision"]) {
        return Err(fail(409, "Calendar changed; reload before deleting"));
    }
    let id = bind(&cal["id"])?;
    if exists(conn, "SELECT 1 FROM calendar_events WHERE calendar_id=? AND deleted=0 LIMIT 1", [&id])? {
        return Err(fail(409, "Move or delete this calendar’s events before deleting it"));
    }
    if exists(conn, "SELECT 1 FROM calendar_tasks WHERE deleted=0 AND json_extract(data, '$.calendar_id')=? LIMIT 1", [&id])? {
        return Err(fail(409, "Move or delete this calendar’s tasks before deleting it"));
    }
    conn.execute("UPDATE calendar_calendars SET deleted=1,revision=revision+1,updated_at=? WHERE id=?", params![now_iso(), id])?;
    Ok(json!({"ok": true}))
}

// ------------------------------------------------------------------------------ events

fn is_list(value: &Value, max: usize) -> Option<&Vec<Value>> {
    value.as_array().filter(|items| items.len() <= max)
}

/// `_rule(data)`: a date-only UNTIL is inclusive in the event's zone.
pub fn rule(data: &Obj) -> Done<Result<Rule, Invalid>> {
    let mut text = str_of(data, "rrule").to_owned();
    let all_day = flag(data, "all_day");
    if let Some(found) = UNTIL_DATE.captures(&text.clone()) {
        if !all_day {
            return Err(bad("Timed UNTIL requires a UTC timestamp"));
        }
        let raw = &found[1];
        let day = (|| Date::new(raw[..4].parse().ok()?, raw[4..6].parse().ok()?, raw[6..].parse().ok()?))();
        let Some(day) = day.and_then(|day| day.add_days(1)) else { return Ok(Err(Invalid)) };
        let cutoff = Dt::midnight(day, Some(zone(&data["timezone"])?)).add(-SECOND);
        let Ok(cutoff) = cutoff.and_then(|cutoff| cutoff.astimezone(&Zone::Utc)) else { return Ok(Err(Invalid)) };
        text = text.replace(&format!("UNTIL={raw}"), &format!("UNTIL={}", cutoff.compact_utc()));
    }
    let start = instant(&data["start"], &data["timezone"], all_day)?;
    Ok(Rule::parse(&text, &start))
}

/// `normalize_event(conn, body)`.
pub fn normalize_event(conn: &Connection, body: &Obj) -> Done<Obj> {
    let mut unknown: Vec<&String> = body.keys().filter(|key| !EVENT_FIELDS.contains(&key.as_str())).collect();
    if !unknown.is_empty() {
        unknown.sort();
        return Err(bad(format!("Unknown event fields: {}", unknown.iter().map(|key| key.as_str()).collect::<Vec<_>>().join(", "))));
    }
    let mut out = body.clone();
    let calendar_id = out.get("calendar_id").filter(|id| py::truthy(id)).cloned().unwrap_or_else(|| s("personal"));
    out.insert("calendar_id".into(), calendar_id.clone());
    let cal = calendar(conn, &calendar_id)?;
    let tz = out.get("timezone").filter(|tz| py::truthy(tz)).cloned().unwrap_or_else(|| cal["timezone"].clone());
    out.insert("timezone".into(), tz.clone());
    zone(&tz)?;
    let title = text(get(&out, "title", &s("")), "Title", 500)?;
    if title.is_empty() {
        return Err(bad("Title is required"));
    }
    out.insert("title".into(), s(&title));
    let all_day = get(&out, "all_day", &Value::Bool(false)).clone();
    out.insert("all_day".into(), all_day.clone());
    let Value::Bool(all_day) = all_day else { return Err(bad("All-day must be boolean")) };
    let start = instant(get(&out, "start", &s("")), &tz, all_day)?;
    let end = instant(get(&out, "end", &s("")), &tz, all_day)?;
    if utc(&end)?.le(&utc(&start)?) {
        return Err(bad("End must be after start (all-day end is exclusive)"));
    }
    if end.sub(&start) > 3660 * DAY {
        return Err(bad("An event must span less than ten years"));
    }
    out.insert("start".into(), s(&stamp(&start, all_day)));
    out.insert("end".into(), s(&stamp(&end, all_day)));
    let subject_id = subject(conn, get(&out, "subject_id", &Value::Null))?;
    out.insert("subject_id".into(), subject_id);
    let shade = match get(&out, "color", &Value::Null) {
        Value::Null => Value::Null,
        value => color(value)?,
    };
    out.insert("color".into(), shade);
    for key in ["description", "location"] {
        let value = text(get(&out, key, &s("")), key, 20000)?;
        out.insert(key.into(), s(&value));
    }
    for key in ["url", "source_url"] {
        let value = url(get(&out, key, &s("")))?;
        out.insert(key.into(), s(&value));
    }
    let source_key = match get(&out, "source_key", &Value::Null) {
        Value::Null => String::new(),
        value => text(value, "Source key", 500)?,
    };
    out.insert("source_key".into(), if source_key.is_empty() { Value::Null } else { s(&source_key) });
    let privacy = get(&out, "privacy", &s("default")).clone();
    if !matches!(privacy.as_str(), Some("default" | "public" | "private" | "confidential")) {
        return Err(bad("Invalid privacy value"));
    }
    out.insert("privacy".into(), privacy);
    let busy = get(&out, "busy", &Value::Bool(true)).clone();
    if !busy.is_boolean() {
        return Err(bad("Busy must be boolean"));
    }
    out.insert("busy".into(), busy);
    let kind = get(&out, "event_type", &s("default")).clone();
    if !matches!(kind.as_str(), Some("default" | "focusTime" | "outOfOffice" | "workingLocation")) {
        return Err(bad("Unsupported event type; appointment schedules are not exposed by Calendar API"));
    }
    out.insert("event_type".into(), kind);
    let options = get(&out, "google_options", &Value::Object(Map::new())).clone();
    let allowed = ["focusTimeProperties", "outOfOfficeProperties", "workingLocationProperties", "conferenceData"];
    if !options.as_object().is_some_and(|map| map.keys().all(|key| allowed.contains(&key.as_str()))) {
        return Err(bad("Invalid Google event properties"));
    }
    out.insert("google_options".into(), options);
    let reminders = get(&out, "reminders", &json!([10])).clone();
    if !is_list(&reminders, 5).is_some_and(|items| items.iter().all(|v| py::int(v).is_some_and(|m| (0..=40320).contains(&m)))) {
        return Err(bad("Reminders must be up to five minute offsets between 0 and 40320"));
    }
    out.insert("reminders".into(), reminders);
    let attendees = get(&out, "attendees", &json!([])).clone();
    let Some(attendees) = is_list(&attendees, 200) else { return Err(bad("Attendees must be an array of at most 200 people")) };
    let mut clean = Vec::new();
    for attendee in attendees {
        let Some(person) = attendee.as_object().filter(|map| map.keys().all(|key| ["email", "name", "response", "optional"].contains(&key.as_str()))) else {
            return Err(bad("Invalid attendee"));
        };
        let email = text(get(person, "email", &s("")), "Attendee email", 320)?;
        if !EMAIL.is_match(&email) {
            return Err(bad("Invalid attendee email"));
        }
        let response = get(person, "response", &s("needsAction")).clone();
        if !matches!(response.as_str(), Some("needsAction" | "accepted" | "declined" | "tentative")) {
            return Err(bad("Invalid RSVP"));
        }
        let name = text(get(person, "name", &s("")), "Attendee name", 200)?;
        clean.push(json!({"email": email, "name": name, "response": response, "optional": flag(person, "optional")}));
    }
    out.insert("attendees".into(), Value::Array(clean));
    let attachments = get(&out, "attachments", &json!([])).clone();
    let Some(attachments) = is_list(&attachments, 25) else { return Err(bad("Attachments must be an array of at most 25 links")) };
    if attachments.iter().any(|item| !item.as_object().is_some_and(|map| map.keys().all(|key| key == "title" || key == "url"))) {
        return Err(bad("Each attachment must contain a title and URL"));
    }
    let mut links = Vec::new();
    for item in attachments {
        let item = item.as_object().expect("checked above");
        let title = text(get(item, "title", &s("")), "Attachment title", 200)?;
        links.push(json!({"title": title, "url": url(get(item, "url", &s("")))?}));
    }
    out.insert("attachments".into(), Value::Array(links));
    let recurrence = text(get(&out, "rrule", &s("")), "Recurrence", 2000)?;
    let recurrence = recurrence.strip_prefix("RRULE:").unwrap_or(&recurrence).to_owned();
    out.insert("rrule".into(), s(&recurrence));
    if !recurrence.is_empty() {
        if recurrence.contains('\n') || !RRULE_TEXT.is_match(&recurrence) {
            return Err(bad("Use a single RFC5545 RRULE"));
        }
        if recurrence.contains("COUNT=") && recurrence.contains("UNTIL=") {
            return Err(bad("RRULE cannot combine COUNT and UNTIL"));
        }
        let invalid = || bad("Invalid RRULE; timed UNTIL must be UTC, e.g. 20271001T000000Z");
        let parsed = rule(&out)?.map_err(|_| invalid())?;
        if parsed.freq > DAILY || parsed.interval < 1 {
            return Err(bad("Supported recurrence frequencies: DAILY, WEEKLY, MONTHLY, YEARLY"));
        }
        if parsed.count.is_some_and(|count| !(1..=100_000).contains(&count)) {
            return Err(bad("Recurrence count must be between 1 and 100000"));
        }
    }
    let exdates = get(&out, "exdates", &json!([])).clone();
    let Some(exdates) = is_list(&exdates, 10000) else { return Err(bad("Exclusions must be an array of dates")) };
    let mut keys = Vec::new();
    for value in exdates {
        keys.push(stamp(&instant(value, &tz, all_day)?, all_day));
    }
    keys.sort();
    keys.dedup();
    out.insert("exdates".into(), Value::Array(keys.into_iter().map(Value::String).collect()));
    Ok(out)
}

/// `_event(conn, ident)`: the stored data with the row's bookkeeping.
fn event_row(conn: &Connection, ident: &Value, include_deleted: bool) -> Done<Obj> {
    let found = row(conn, "SELECT * FROM calendar_events WHERE id=?", [bind(ident)?])?;
    let Some(found) = found.filter(|found| include_deleted || !py::truthy(&found["deleted"])) else {
        return Err(fail(404, "Event not found"));
    };
    let mut out: Obj = serde_json::from_str(found["data"].as_str().ok_or_else(internal)?)?;
    out.insert("id".into(), found["id"].clone());
    out.insert("revision".into(), found["revision"].clone());
    out.insert("deleted".into(), Value::Bool(py::truthy(&found["deleted"])));
    out.insert("updated_at".into(), found["updated_at"].clone());
    out.insert("source_overridden".into(), Value::Bool(py::truthy(&found["source_overridden"])));
    Ok(out)
}

/// `ensure_visible`: 404 for an ApplyAI-sourced copy while that module is off.
pub fn ensure_visible(conn: &Connection, ident: &Value, applyai: bool) -> Done<()> {
    if applyai {
        return Ok(());
    }
    if let Some(found) = row(conn, "SELECT source_key FROM calendar_events WHERE id=?", [bind(ident)?])? {
        if applyai_sourced(&found["source_key"]) {
            return Err(fail(404, "Event not found"));
        }
    }
    Ok(())
}

pub fn get_event(conn: &Connection, ident: &Value, applyai: bool) -> Done<Obj> {
    ensure_visible(conn, ident, applyai)?;
    event_row(conn, ident, false)
}

fn snapshot(conn: &Connection, ident: &Value) -> Done<Value> {
    let Some(found) = row(conn, "SELECT * FROM calendar_events WHERE id=?", [bind(ident)?])? else { return Ok(Value::Null) };
    let exceptions = rows(conn, "SELECT * FROM calendar_exceptions WHERE event_id=?", [bind(ident)?])?;
    Ok(json!({"row": found, "exceptions": exceptions}))
}

fn history(conn: &Connection, entity: &str, ident: &Value, before: &Value, after: &Value) -> Done<i64> {
    conn.execute("INSERT INTO calendar_history(entity,entity_id,before_json,after_json,created_at) VALUES(?,?,?,?,?)",
                 params![entity, bind(ident)?, py::dumps(before), py::dumps(after), now_iso()])?;
    Ok(conn.last_insert_rowid())
}

fn insert(conn: &Connection, data: &Obj, ident: Option<String>) -> Done<String> {
    let ident = ident.unwrap_or_else(uuid);
    conn.execute("INSERT INTO calendar_events(id,calendar_id,title,start,end,timezone,all_day,data,source_key,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        params![ident, bind(&data["calendar_id"])?, bind(&data["title"])?, bind(&data["start"])?, bind(&data["end"])?, bind(&data["timezone"])?,
                i64::from(flag(data, "all_day")), py::dumps(&Value::Object(data.clone())), bind(&data["source_key"])?, now_iso()])?;
    Ok(ident)
}

fn update(conn: &Connection, ident: &Value, data: &Obj, deleted: bool, manual: bool) -> Done<()> {
    conn.execute("UPDATE calendar_events SET calendar_id=?,title=?,start=?,end=?,timezone=?,all_day=?,data=?,deleted=?,revision=revision+1,source_overridden=MAX(source_overridden,?),updated_at=? WHERE id=?",
        params![bind(&data["calendar_id"])?, bind(&data["title"])?, bind(&data["start"])?, bind(&data["end"])?, bind(&data["timezone"])?,
                i64::from(flag(data, "all_day")), py::dumps(&Value::Object(data.clone())), i64::from(deleted),
                i64::from(manual && flag(data, "source_key")), now_iso(), bind(ident)?])?;
    Ok(())
}

fn with(base: &Obj, changes: &[(&str, Value)]) -> Obj {
    let mut out = base.clone();
    for (key, value) in changes {
        out.insert((*key).to_owned(), value.clone());
    }
    out
}

pub fn create_event(conn: &Connection, body: &Obj) -> Done<Obj> {
    let data = normalize_event(conn, body)?;
    require_writable(conn, &data["calendar_id"])?;
    let created = transaction(conn, false, || {
        if !data["source_key"].is_null() {
            if let Some(found) = row(conn, "SELECT id FROM calendar_events WHERE source_key=?", [bind(&data["source_key"])?])? {
                return Ok(Err(event_row(conn, &found["id"], true)?));
            }
        }
        let ident = s(&insert(conn, &data, None)?);
        let undo = history(conn, "event", &ident, &Value::Null, &snapshot(conn, &ident)?)?;
        Ok(Ok((ident, undo)))
    })?;
    match created {
        Err(existing) => Ok(existing),
        Ok((ident, undo)) => {
            let mut out = event_row(conn, &ident, false)?;
            out.insert("undo_id".into(), Value::from(undo));
            Ok(out)
        }
    }
}

/// `_starts(data, lower, upper)`: occurrence starts in a range; a nonexistent local time is
/// skipped without consuming COUNT.
pub fn starts(data: &Obj, lower: &Dt, upper: &Dt) -> Done<Vec<Dt>> {
    let all_day = flag(data, "all_day");
    let base = instant(&data["start"], &data["timezone"], all_day)?;
    if str_of(data, "rrule").is_empty() {
        return Ok(if lower.le(&base) && base.le(upper) { vec![base] } else { Vec::new() });
    }
    let parsed = rule(data)?.map_err(|_| internal())?;
    let count = parsed.count;
    let mut values = Vec::new();
    let mut valid = 0;
    for mut occurrence in parsed.iter(false) {
        if count.is_none() && occurrence.lt(lower) {
            continue;
        }
        if base.fold && occurrence.wall == base.wall {
            occurrence = base.clone();
        }
        if upper.lt(&occurrence) {
            break;
        }
        if !occurrence.exists() {
            continue;
        }
        valid += 1;
        if count.is_some_and(|count| valid > count) {
            break;
        }
        if !occurrence.lt(lower) {
            values.push(occurrence);
        }
        if values.len() > 10000 {
            return Err(bad("Range contains too many occurrences; select a shorter range"));
        }
    }
    Ok(values)
}

fn duration(data: &Obj) -> Done<i64> {
    let all_day = flag(data, "all_day");
    let start = instant(&data["start"], &data["timezone"], all_day)?;
    let end = instant(&data["end"], &data["timezone"], all_day)?;
    Ok(if all_day { end.sub(&start) } else { utc(&end)?.sub(&utc(&start)?) })
}

fn occurrence_end(data: &Obj, start: &Dt, length: i64) -> Done<String> {
    let all_day = flag(data, "all_day");
    let end = if all_day { start.add(length)? } else { utc(start)?.add(length)?.astimezone(&zone(&data["timezone"])?)? };
    Ok(stamp(&end, all_day))
}

pub struct Range<'a> {
    pub search: &'a str,
    pub calendar_id: Option<&'a str>,
    pub include_hidden: bool,
    pub applyai: bool,
}

/// `occurrences(conn, start, end, ...)`.
pub fn occurrences(conn: &Connection, start: &Value, end: &Value, range: &Range) -> Done<Vec<Obj>> {
    let lower = instant_utc(start)?;
    let upper = instant_utc(end)?;
    if upper.le(&lower) || upper.sub(&lower) > 740 * DAY {
        return Err(bad("Choose a positive calendar range of at most two years"));
    }
    let mut sql = "SELECT e.* FROM calendar_events e JOIN calendar_calendars c ON c.id=e.calendar_id WHERE e.deleted=0 AND c.deleted=0".to_owned();
    if !range.include_hidden {
        sql.push_str(" AND c.visible=1");
    }
    let found = match range.calendar_id.filter(|id| !id.is_empty()) {
        Some(id) => rows(conn, &(sql + " AND e.calendar_id=?"), [id])?,
        None => rows(conn, &sql, [])?,
    };
    let needle = py::casefold(range.search);
    let mut overrides = conn.prepare("SELECT occurrence,data FROM calendar_exceptions WHERE event_id=?")?;
    let mut output: Vec<(Dt, Obj)> = Vec::new();
    for event in found {
        if !range.applyai && applyai_sourced(&event["source_key"]) {
            continue;
        }
        let data: Obj = serde_json::from_str(event["data"].as_str().ok_or_else(internal)?)?;
        if !needle.is_empty() {
            let haystack = py::casefold(&[str_of(&data, "title"), str_of(&data, "description"), str_of(&data, "location")].join(" "));
            if !haystack.contains(&needle) {
                continue;
            }
        }
        let all_day = flag(&data, "all_day");
        let length = duration(&data)?;
        let mut exceptions: Vec<(String, Option<Obj>)> = Vec::new();
        let mut listed = overrides.query([bind(&event["id"])?])?;
        while let Some(item) = listed.next()? {
            let key: String = item.get(0)?;
            let replacement = match item.get_ref(1)? {
                ValueRef::Text(text) if !text.is_empty() => Some(serde_json::from_slice(text)?),
                _ => None,
            };
            match exceptions.iter_mut().find(|(seen, _)| *seen == key) {
                Some(slot) => slot.1 = replacement,
                None => exceptions.push((key, replacement)),
            }
        }
        // Equal keys are adjacent: the starts come in order.
        let mut candidates: Vec<(String, Dt)> = Vec::new();
        for dt in starts(&data, &lower.add(-length)?.add(-2 * DAY)?, &upper)? {
            let key = stamp(&dt, all_day);
            match candidates.last_mut() {
                Some(last) if last.0 == key => last.1 = dt,
                _ => candidates.push((key, dt)),
            }
        }
        for (key, _) in &exceptions {
            if !candidates.iter().any(|(seen, _)| seen == key) {
                let dt = instant(&s(key), &data["timezone"], all_day)?;
                candidates.push((key.clone(), dt));
            }
        }
        let exdates = data.get("exdates").cloned().unwrap_or(Value::Null);
        for (key, dt) in candidates {
            if exdates.as_array().is_some_and(|items| items.iter().any(|item| item.as_str() == Some(key.as_str()))) {
                continue;
            }
            let exception = exceptions.iter().find(|(seen, _)| *seen == key);
            let item = match exception {
                Some((_, None)) => continue,
                Some((_, Some(replacement))) => replacement.clone(),
                None => with(&data, &[("start", s(&stamp(&dt, all_day))), ("end", s(&occurrence_end(&data, &dt, length)?))]),
            };
            let item_all_day = flag(&item, "all_day");
            let a = instant(&item["start"], &item["timezone"], item_all_day)?;
            let b = instant(&item["end"], &item["timezone"], item_all_day)?;
            if a.lt(&upper) && lower.lt(&b) {
                let mut out = item.clone();
                for (name, value) in [("id", event["id"].clone()), ("revision", event["revision"].clone()), ("occurrence", s(&key)),
                                      ("series_start", data["start"].clone()), ("series_end", data["end"].clone()),
                                      ("is_exception", Value::Bool(exception.is_some())), ("rrule", data["rrule"].clone()),
                                      ("exdates", exdates.clone()), ("source_overridden", Value::Bool(py::truthy(&event["source_overridden"])))] {
                    out.insert(name.into(), value);
                }
                output.push((utc(&a)?, out));
            }
            if output.len() > 20000 {
                return Err(bad("Too many events; choose a shorter range"));
            }
        }
    }
    output.sort_by(|a, b| a.0.wall.cmp(&b.0.wall));
    Ok(output.into_iter().map(|(_, item)| item).collect())
}

/// `_remap_exceptions`: keep overrides when a series' wall time shifts, DST included.
fn remap_exceptions(conn: &Connection, ident: &Value, old: &Obj, updated: &mut Obj, exceptions: &[Obj]) -> Done<()> {
    let (old_all_day, new_all_day) = (flag(old, "all_day"), flag(updated, "all_day"));
    let a = instant(&old["start"], &old["timezone"], old_all_day)?;
    let b = instant(&updated["start"], &updated["timezone"], new_all_day)?;
    let delta = b.wall - a.wall;
    let new_zone = zone(&updated["timezone"])?;
    let shifted = |value: &Value| -> Done<String> {
        let original = instant(value, &old["timezone"], old_all_day)?;
        let moved = Dt { wall: original.wall, zone: None, fold: false }.add(delta)?.with_zone(new_zone.clone(), original.fold);
        if !moved.exists() {
            return Err(fail(409, "This series shift would move an exception into a DST gap; edit that occurrence first"));
        }
        Ok(stamp(&moved, new_all_day))
    };
    if py::eq(&updated["exdates"], &old["exdates"]) {
        let mut moved = Vec::new();
        for value in old["exdates"].as_array().cloned().unwrap_or_default() {
            moved.push(s(&shifted(&value)?));
        }
        updated.insert("exdates".into(), Value::Array(moved));
    }
    let mut replacements = Vec::new();
    for ex in exceptions {
        let key = shifted(&ex["occurrence"])?;
        let dt = instant(&s(&key), &updated["timezone"], new_all_day)?;
        if starts(updated, &dt, &dt)?.is_empty() {
            return Err(fail(409, "New recurrence would invalidate an edited occurrence; keep its recurrence boundary or edit that occurrence first"));
        }
        let replacement: Option<Obj> = match ex["data"].as_str() {
            Some(text) if !text.is_empty() => Some(serde_json::from_str(text)?),
            _ => None,
        };
        let replacement = match replacement.filter(|item| !item.is_empty()) {
            Some(item) => {
                let moved = with(&item, &[("calendar_id", updated["calendar_id"].clone()), ("start", s(&shifted(&item["start"])?)),
                    ("end", s(&shifted(&item["end"])?)), ("timezone", updated["timezone"].clone()), ("all_day", updated["all_day"].clone())]);
                Some(py::dumps(&Value::Object(normalize_event(conn, &moved)?)))
            }
            None => None,
        };
        replacements.push((key, replacement));
    }
    conn.execute("DELETE FROM calendar_exceptions WHERE event_id=?", [bind(ident)?])?;
    for (key, replacement) in replacements {
        conn.execute("INSERT INTO calendar_exceptions VALUES(?,?,?)", params![bind(ident)?, key, replacement])?;
    }
    Ok(())
}

fn assert_occurrence(data: &Obj, value: &Value) -> Done<(Dt, String)> {
    let all_day = flag(data, "all_day");
    let dt = instant(value, &data["timezone"], all_day)?;
    let key = stamp(&dt, all_day);
    let excluded = data["exdates"].as_array().is_some_and(|items| items.iter().any(|item| item == &s(&key)));
    let at = utc(&dt)?.wall;
    let mut found = false;
    for start in starts(data, &dt, &dt)? {
        if utc(&start)?.wall == at {
            found = true;
        }
    }
    if excluded || !found {
        return Err(bad("Selected occurrence is not in this series"));
    }
    Ok((dt, key))
}

fn truncate(rule_text: &str, cutoff: &Dt, all_day: bool) -> Done<String> {
    let mut parts: Vec<String> = rule_text.split(';').filter(|part| !part.starts_with("COUNT=") && !part.starts_with("UNTIL=")).map(str::to_owned).collect();
    let until = if all_day {
        let day = cutoff.date().add_days(-1).ok_or_else(internal)?;
        format!("{:04}{:02}{:02}", day.year, day.month, day.day)
    } else {
        utc(cutoff)?.add(-SECOND)?.compact_utc()
    };
    parts.push(format!("UNTIL={until}"));
    Ok(parts.join(";"))
}

fn event_data(current: &Obj) -> Obj {
    current.iter().filter(|(key, _)| EVENT_FIELDS.contains(&key.as_str())).map(|(k, v)| (k.clone(), v.clone())).collect()
}

/// `edit_event(conn, ident, body)`: this occurrence, this and future, or the whole series.
pub fn edit_event(conn: &Connection, ident: &Value, body: &Obj) -> Done<Obj> {
    if body.keys().any(|key| !["revision", "scope", "occurrence", "changes", "delete"].contains(&key.as_str())) {
        return Err(bad("Unknown edit field"));
    }
    let current = event_row(conn, ident, false)?;
    let revision = body.get("revision").cloned().unwrap_or(Value::Null);
    if !py::eq(&revision, &current["revision"]) {
        return Err(fail(409, "Event changed; reload and review before saving"));
    }
    let empty = Value::Object(Map::new());
    let changes = match get(body, "changes", &empty) {
        Value::Object(map) if map.keys().all(|key| EVENT_FIELDS.contains(&key.as_str())) => map.clone(),
        _ => return Err(bad("Invalid event changes")),
    };
    require_writable(conn, &current["calendar_id"])?;
    require_writable(conn, changes.get("calendar_id").unwrap_or(&current["calendar_id"]))?;
    if let Some(key) = changes.get("source_key") {
        if !py::eq(key, current.get("source_key").unwrap_or(&Value::Null)) {
            return Err(bad("Stable source linkage cannot be edited"));
        }
    }
    let scope = get(body, "scope", &s("all")).clone();
    let scope = match scope.as_str() {
        Some(scope @ ("all" | "this" | "future")) => scope.to_owned(),
        _ => return Err(bad("Scope must be this, future, or all")),
    };
    let repeating = flag(&current, "rrule");
    if repeating && scope == "this" && ["calendar_id", "rrule", "exdates"].iter().any(|key| {
        changes.get(*key).is_some_and(|value| !py::eq(value, current.get(*key).unwrap_or(&Value::Null)))
    }) {
        return Err(bad("An individual occurrence cannot change its calendar or series recurrence; choose all/future scope"));
    }
    let target_calendar = changes.get("calendar_id").unwrap_or(&current["calendar_id"]);
    if !py::eq(target_calendar, &current["calendar_id"]) && exists(conn, "SELECT 1 FROM calendar_google_events WHERE event_id=?", [bind(ident)?])? {
        return Err(fail(409, "Moving synchronized events between calendars is not supported; unlink the Google calendar first or create a separate local copy"));
    }
    let Value::Bool(deleted) = get(body, "delete", &Value::Bool(false)).clone() else { return Err(bad("Delete must be boolean")) };
    let data = event_data(&current);
    let all_day = flag(&data, "all_day");
    let (undo, new_ident) = transaction(conn, false, || {
        let before = snapshot(conn, ident)?;
        if !py::eq(&before["row"]["revision"], &revision) {
            return Err(fail(409, "Event changed; reload before saving"));
        }
        let before_exceptions: Vec<Obj> = before["exceptions"].as_array().map(|items| items.iter().filter_map(|v| v.as_object().cloned()).collect()).unwrap_or_default();
        let mut new_ident = None;
        if scope == "all" || str_of(&data, "rrule").is_empty() {
            let mut updated = normalize_event(conn, &py::merged(&[&data, &changes]))?;
            if !deleted {
                remap_exceptions(conn, ident, &data, &mut updated, &before_exceptions)?;
            }
            update(conn, ident, &updated, deleted, true)?;
        } else {
            let (dt, key) = assert_occurrence(&data, get(body, "occurrence", &s("")))?;
            let length = duration(&data)?;
            if scope == "this" {
                let existing = row(conn, "SELECT data FROM calendar_exceptions WHERE event_id=? AND occurrence=?", params![bind(ident)?, key])?;
                let original: Obj = match existing.as_ref().and_then(|found| found["data"].as_str()).filter(|text| !text.is_empty()) {
                    Some(text) => serde_json::from_str(text)?,
                    None => with(&data, &[("start", s(&key)), ("end", s(&occurrence_end(&data, &dt, length)?))]),
                };
                let merged = with(&py::merged(&[&original, &changes]), &[("rrule", s("")), ("exdates", json!([]))]);
                let updated = normalize_event(conn, &merged)?;
                conn.execute("INSERT INTO calendar_exceptions(event_id,occurrence,data) VALUES(?,?,?) ON CONFLICT(event_id,occurrence) DO UPDATE SET data=excluded.data",
                    params![bind(ident)?, key, if deleted { None } else { Some(py::dumps(&Value::Object(updated))) }])?;
                update(conn, ident, &data, false, true)?;
            } else if dt.eq(&instant(&data["start"], &data["timezone"], all_day)?) {
                let mut updated = normalize_event(conn, &py::merged(&[&data, &changes]))?;
                if !deleted {
                    remap_exceptions(conn, ident, &data, &mut updated, &before_exceptions)?;
                }
                update(conn, ident, &updated, deleted, true)?;
            } else {
                let original_rule = str_of(&data, "rrule").to_owned();
                let mut new_rule = original_rule.clone();
                if let Some(found) = COUNT.captures(&original_rule) {
                    let first = instant(&data["start"], &data["timezone"], all_day)?;
                    let prior = starts(&data, &first, &utc(&dt)?.add(-1)?)?.len() as i64;
                    let total: i64 = found[1].parse().map_err(|_| internal())?;
                    new_rule = COUNT_ANY.replace_all(&original_rule, format!("COUNT={}", total - prior).as_str()).into_owned();
                }
                let exdates: Vec<Value> = data["exdates"].as_array().cloned().unwrap_or_default();
                let mut later = Vec::new();
                for value in &exdates {
                    if !instant(value, &data["timezone"], all_day)?.lt(&dt) {
                        later.push(value.clone());
                    }
                }
                let kept: Vec<Value> = exdates.iter().filter(|value| !later.contains(value)).cloned().collect();
                let shortened = normalize_event(conn, &with(&data, &[("rrule", s(&truncate(&original_rule, &dt, all_day)?)), ("exdates", Value::Array(kept))]))?;
                update(conn, ident, &shortened, false, true)?;
                let end = s(&occurrence_end(&data, &dt, length)?);
                let mut updated = None;
                if !deleted {
                    let base = with(&data, &[("start", s(&key)), ("end", end.clone()), ("rrule", s(&new_rule)), ("exdates", Value::Array(later.clone())), ("source_key", Value::Null)]);
                    let normalized = normalize_event(conn, &py::merged(&[&base, &changes]))?;
                    new_ident = Some(s(&insert(conn, &normalized, None)?));
                    updated = Some(normalized);
                }
                let mut later_exceptions = Vec::new();
                for ex in &before_exceptions {
                    if !instant(&ex["occurrence"], &data["timezone"], all_day)?.lt(&dt) {
                        later_exceptions.push(ex.clone());
                    }
                }
                if let (Some(new), Some(updated)) = (&new_ident, &mut updated) {
                    let old = with(&data, &[("start", s(&key)), ("end", end), ("exdates", Value::Array(later))]);
                    remap_exceptions(conn, new, &old, updated, &later_exceptions)?;
                    conn.execute("UPDATE calendar_events SET data=? WHERE id=?", params![py::dumps(&Value::Object(updated.clone())), bind(new)?])?;
                }
                for ex in &later_exceptions {
                    conn.execute("DELETE FROM calendar_exceptions WHERE event_id=? AND occurrence=?", params![bind(ident)?, bind(&ex["occurrence"])?])?;
                }
            }
        }
        let mut after = snapshot(conn, ident)?;
        if let Some(new) = &new_ident {
            after["created"] = snapshot(conn, new)?;
        }
        Ok((history(conn, "event", ident, &before, &after)?, new_ident))
    })?;
    let mut out = event_row(conn, ident, true)?;
    out.insert("undo_id".into(), Value::from(undo));
    out.insert("future_event".into(), match new_ident {
        Some(new) => Value::Object(event_row(conn, &new, false)?),
        None => Value::Null,
    });
    Ok(out)
}

/// `undo(conn, history_id)`.
pub fn undo(conn: &Connection, history_id: i64) -> Done<Value> {
    transaction(conn, true, || {
        let Some(entry) = row(conn, "SELECT * FROM calendar_history WHERE id=? AND undone=0", [history_id])? else {
            return Err(fail(404, "Undo is no longer available"));
        };
        let before: Value = serde_json::from_str(entry["before_json"].as_str().unwrap_or("null"))?;
        let after: Value = serde_json::from_str(entry["after_json"].as_str().ok_or_else(internal)?)?;
        let entity_id = &entry["entity_id"];
        if entry["entity"] == "task" {
            let current = row(conn, "SELECT * FROM calendar_tasks WHERE id=?", [bind(entity_id)?])?;
            let Some(current) = current.filter(|current| py::eq(&current["revision"], &after["revision"])) else {
                return Err(fail(409, "Task changed since that action; cannot undo safely"));
            };
            if after.get("created").is_some_and(py::truthy) {
                let created = row(conn, "SELECT * FROM calendar_tasks WHERE id=?", [bind(&after["created"]["id"])?])?;
                let Some(created) = created.filter(|created| !py::truthy(&created["deleted"]) && py::eq(&created["revision"], &after["created"]["revision"])) else {
                    return Err(fail(409, "Next task changed since completion; cannot undo safely"));
                };
                conn.execute("UPDATE calendar_tasks SET deleted=1,revision=revision+1,updated_at=? WHERE id=?", params![now_iso(), bind(&created["id"])?])?;
            }
            let restored = py::truthy(&before);
            let pick = |key: &str, default: Value| if restored { before.get(key).cloned().unwrap_or(default) } else { current[key].clone() };
            conn.execute("UPDATE calendar_tasks SET data=?,deleted=?,repeat_anchor=?,task_series_id=?,previous_task_id=?,revision=revision+1,updated_at=? WHERE id=?",
                params![bind(&pick("data", Value::Null))?, if restored { bind(&before["deleted"])? } else { Sql::Integer(1) },
                        bind(&pick("repeat_anchor", s("")))?, bind(&pick("task_series_id", Value::Null))?, bind(&pick("previous_task_id", Value::Null))?,
                        now_iso(), bind(entity_id)?])?;
        } else {
            let current = snapshot(conn, entity_id)?;
            if !py::eq(&current["row"]["revision"], &after["row"]["revision"]) {
                return Err(fail(409, "Event changed since that action; cannot undo safely"));
            }
            if after.get("created").is_some_and(py::truthy) {
                let created = snapshot(conn, &after["created"]["row"]["id"])?;
                if !py::eq(&created["row"]["revision"], &after["created"]["row"]["revision"]) {
                    return Err(fail(409, "Future series changed; cannot undo safely"));
                }
                conn.execute("UPDATE calendar_events SET deleted=1,revision=revision+1,updated_at=? WHERE id=?", params![now_iso(), bind(&created["row"]["id"])?])?;
            }
            if py::truthy(&before) {
                let old = &before["row"];
                let mut values = Vec::new();
                for key in ["calendar_id", "title", "start", "end", "timezone", "all_day", "data", "deleted", "source_overridden"] {
                    values.push(bind(&old[key])?);
                }
                values.push(Sql::Text(now_iso()));
                values.push(bind(entity_id)?);
                conn.execute("UPDATE calendar_events SET calendar_id=?,title=?,start=?,end=?,timezone=?,all_day=?,data=?,deleted=?,source_overridden=?,revision=revision+1,updated_at=? WHERE id=?",
                             rusqlite::params_from_iter(values))?;
                conn.execute("DELETE FROM calendar_exceptions WHERE event_id=?", [bind(entity_id)?])?;
                for item in before["exceptions"].as_array().cloned().unwrap_or_default() {
                    conn.execute("INSERT INTO calendar_exceptions(event_id,occurrence,data) VALUES(?,?,?)",
                                 params![bind(&item["event_id"])?, bind(&item["occurrence"])?, bind(&item["data"])?])?;
                }
            } else {
                conn.execute("UPDATE calendar_events SET deleted=1,revision=revision+1,updated_at=? WHERE id=?", params![now_iso(), bind(entity_id)?])?;
            }
        }
        conn.execute("UPDATE calendar_history SET undone=1 WHERE id=?", [history_id])?;
        Ok(json!({"ok": true}))
    })
}

// ------------------------------------------------------------------------------ tasks

fn task_output(found: &Obj) -> Done<Obj> {
    let defaults = json!({"repeat_frequency": "", "repeat_interval": 1, "repeat_until": "", "subject_id": null});
    let data: Obj = serde_json::from_str(found["data"].as_str().ok_or_else(internal)?)?;
    let mut out = py::merged(&[defaults.as_object().expect("an object"), &data]);
    for key in ["id", "revision", "updated_at"] {
        out.insert(key.into(), found[key].clone());
    }
    Ok(out)
}

pub fn tasks(conn: &Connection, search: &str) -> Done<Vec<Obj>> {
    let needle = py::casefold(search);
    let mut output = Vec::new();
    for found in rows(conn, "SELECT * FROM calendar_tasks WHERE deleted=0 ORDER BY updated_at DESC", [])? {
        let item = task_output(&found)?;
        if needle.is_empty() {
            output.push(item);
            continue;
        }
        let mut words = vec![str_of(&item, "title"), str_of(&item, "description"), str_of(&item, "project")];
        words.extend(item.get("tags").and_then(Value::as_array).into_iter().flatten().map(|tag| tag.as_str().unwrap_or("")));
        if py::casefold(&words.join(" ")).contains(&needle) {
            output.push(item);
        }
    }
    Ok(output)
}

fn normalize_task(conn: &Connection, body: &Obj, previous: &Obj, completed_at: &str) -> Done<Obj> {
    let mut data = previous.clone();
    for (key, value) in body {
        if TASK_FIELDS.contains(&key.as_str()) {
            data.insert(key.clone(), value.clone());
        }
    }
    let title = text(get(&data, "title", &s("")), "Title", 500)?;
    if title.is_empty() {
        return Err(bad("Task title is required"));
    }
    data.insert("title".into(), s(&title));
    let description = text(get(&data, "description", &s("")), "Description", 20000)?;
    data.insert("description".into(), s(&description));
    let tz = get(&data, "timezone", &s("UTC")).clone();
    data.insert("timezone".into(), tz.clone());
    zone(&tz)?;
    let calendar_id = get(&data, "calendar_id", &s("personal")).clone();
    data.insert("calendar_id".into(), calendar_id.clone());
    calendar(conn, &calendar_id)?;
    let mut due = text(get(&data, "due", &s("")), "Due", 100)?;
    if !due.is_empty() {
        let all_day = py::len(&due) == 10;
        due = stamp(&instant(&s(&due), &tz, all_day)?, all_day);
    }
    data.insert("due".into(), s(&due));
    let frequency = text(get(&data, "repeat_frequency", &s("")), "Repeat frequency", 20)?;
    if !["", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"].contains(&frequency.as_str()) {
        return Err(bad("Repeat frequency must be DAILY, WEEKLY, MONTHLY, YEARLY, or empty"));
    }
    data.insert("repeat_frequency".into(), s(&frequency));
    let interval = get(&data, "repeat_interval", &json!(1)).clone();
    data.insert("repeat_interval".into(), interval.clone());
    if !py::int(&interval).is_some_and(|value| value >= 1) {
        return Err(bad("Repeat interval must be an integer of at least 1"));
    }
    let until = text(get(&data, "repeat_until", &s("")), "Repeat stop date", 10)?;
    data.insert("repeat_until".into(), s(&until));
    if !until.is_empty() {
        instant(&s(&until), &tz, true)?;
    }
    if !frequency.is_empty() && due.is_empty() {
        return Err(bad("Repeating tasks require a due date or time"));
    }
    if !frequency.is_empty() && !until.is_empty() && until.as_str() < due.chars().take(10).collect::<String>().as_str() {
        return Err(bad("Repeat stop date must be on or after the task due date"));
    }
    let priority = get(&data, "priority", &s("normal")).clone();
    data.insert("priority".into(), priority.clone());
    let status = get(&data, "status", &s("todo")).clone();
    data.insert("status".into(), status.clone());
    if !matches!(priority.as_str(), Some("low" | "normal" | "high" | "urgent")) || !matches!(status.as_str(), Some("todo" | "doing" | "done" | "cancelled")) {
        return Err(bad("Invalid priority or status"));
    }
    let completed = if status == "done" {
        previous.get("completed_at").filter(|v| py::truthy(v)).cloned().unwrap_or_else(|| s(completed_at))
    } else {
        Value::Null
    };
    data.insert("completed_at".into(), completed);
    let project = text(get(&data, "project", &s("")), "Project", 200)?;
    data.insert("project".into(), s(&project));
    let subject_id = subject(conn, get(&data, "subject_id", &Value::Null))?;
    data.insert("subject_id".into(), subject_id);
    let tags = get(&data, "tags", &json!([])).clone();
    let Some(tags) = is_list(&tags, 50) else { return Err(bad("Tags must be an array of at most 50 values")) };
    let mut clean: Vec<Value> = Vec::new();
    for tag in tags {
        if py::truthy(tag) {
            let value = s(&text(tag, "Tag", 100)?);
            if !clean.contains(&value) {
                clean.push(value);
            }
        }
    }
    data.insert("tags".into(), Value::Array(clean));
    Ok(data)
}

/// `relativedelta(months=n)` on a date: the day clipped to the month's length.
fn add_months(date: Date, months: i64) -> Option<Date> {
    let index = date.year * 12 + i64::from(date.month) - 1 + months;
    let (year, month) = (index.div_euclid(12), (index.rem_euclid(12) + 1) as u32);
    Date::new(year, month, date.day.min(crate::ported::time::days_in_month(year, month)))
}

/// `_next_task_due`: skip missed dates; month ends clamp from the anchor, not the last one.
fn next_task_due(data: &Obj, anchor_value: &str, completed_at: &str) -> Done<Option<String>> {
    let due_text = str_of(data, "due");
    let all_day = py::len(due_text) == 10;
    let tz = &data["timezone"];
    let anchor = instant(&s(anchor_value), tz, all_day)?;
    let due = instant(&s(due_text), tz, all_day)?;
    let completed = instant(&s(completed_at), tz, false)?;
    let (due_utc, done_utc) = (utc(&due)?, utc(&completed)?);
    let latest = if done_utc.wall > due_utc.wall { done_utc } else { due_utc };
    let place = anchor.zone.clone().ok_or_else(internal)?;
    let boundary = latest.astimezone(&place)?;
    let frequency = str_of(data, "repeat_frequency");
    let interval = py::int(&data["repeat_interval"]).unwrap_or(1);
    let until = str_of(data, "repeat_until");
    let (a, b) = (anchor.date(), boundary.date());
    let (step, mut index) = match frequency {
        "DAILY" | "WEEKLY" => {
            let step = interval * if frequency == "WEEKLY" { 7 } else { 1 };
            (step, ((b.days() - a.days()).div_euclid(step)).max(1))
        }
        "MONTHLY" => (interval, (((b.year - a.year) * 12 + i64::from(b.month) - i64::from(a.month)).div_euclid(interval)).max(1)),
        _ => (interval, ((b.year - a.year).div_euclid(interval)).max(1)),
    };
    loop {
        let amount = index.checked_mul(step).ok_or_else(internal)?;
        let date = match frequency {
            "DAILY" | "WEEKLY" => a.add_days(amount),
            "MONTHLY" => add_months(a, amount),
            _ => add_months(a, amount.checked_mul(12).ok_or_else(internal)?),
        };
        let Some(date) = date else { return Ok(None) };
        let candidate = Dt { wall: date.days() * DAY + anchor.time_of_day(), zone: anchor.zone.clone(), fold: anchor.fold };
        if !until.is_empty() && candidate.date().iso().as_str() > until {
            return Ok(None);
        }
        let future = if all_day { candidate.date() > b } else { utc(&candidate)?.wall > utc(&boundary)?.wall };
        if future && candidate.exists() {
            return Ok(Some(stamp(&candidate, all_day)));
        }
        index += 1;
    }
}

pub fn save_task(conn: &Connection, body: &Obj) -> Done<Obj> {
    if body.keys().any(|key| !TASK_FIELDS.contains(&key.as_str()) && !["id", "revision", "delete"].contains(&key.as_str())) {
        return Err(bad("Unknown task field"));
    }
    if !get(body, "delete", &Value::Bool(false)).is_boolean() {
        return Err(bad("Delete must be boolean"));
    }
    let deleting = flag(body, "delete");
    let given = body.get("id").filter(|id| py::truthy(id));
    let ident = given.cloned().unwrap_or_else(|| s(&uuid()));
    let mut next_task = Value::Null;
    let (after, undo_id) = transaction(conn, true, || {
        let found = row(conn, "SELECT * FROM calendar_tasks WHERE id=?", [bind(&ident)?])?;
        if given.is_some() && found.as_ref().is_none_or(|found| py::truthy(&found["deleted"])) {
            return Err(fail(404, "Task not found"));
        }
        if let Some(found) = &found {
            if !py::eq(body.get("revision").unwrap_or(&Value::Null), &found["revision"]) {
                return Err(fail(409, "Task changed; reload before saving"));
            }
        }
        let previous: Obj = match &found {
            Some(found) => serde_json::from_str(found["data"].as_str().ok_or_else(internal)?)?,
            None => Map::new(),
        };
        let changed_at = now_iso();
        let data = normalize_task(conn, body, &previous, &changed_at)?;
        let was_done = previous.get("status").is_some_and(|status| status == "done");
        let completed = data["status"] == "done" && !was_done;
        let successor = row(conn, "SELECT id FROM calendar_tasks WHERE previous_task_id=? AND deleted=0", [bind(&ident)?])?;
        if was_done {
            for key in ["repeat_frequency", "repeat_interval", "repeat_until"] {
                let default = if key == "repeat_interval" { json!(1) } else { s("") };
                if body.contains_key(key) && !py::eq(&data[key], previous.get(key).unwrap_or(&default)) {
                    return Err(fail(409, "Edit repetition on the next pending task, not completed history"));
                }
            }
            if data["status"] != "done" && successor.is_some() {
                return Err(fail(409, "This completion has a next task; use undo to reopen it safely"));
            }
        }
        let mut anchor = found.as_ref().map(|found| found["repeat_anchor"].clone()).unwrap_or_else(|| s(""));
        let mut series_id = found.as_ref().map(|found| found["task_series_id"].clone()).unwrap_or(Value::Null);
        if flag(&data, "repeat_frequency") {
            let moved = ["due", "timezone", "repeat_frequency", "repeat_interval"].iter()
                .any(|key| !py::eq(&data[*key], previous.get(*key).unwrap_or(&Value::Null)));
            if !py::truthy(&anchor) || (!was_done && moved) {
                anchor = data["due"].clone();
            }
            if !py::truthy(&series_id) {
                series_id = ident.clone();
            }
        } else if !was_done {
            anchor = s("");
        }
        let text_data = py::dumps(&Value::Object(data.clone()));
        if let Some(found) = &found {
            conn.execute("UPDATE calendar_tasks SET data=?,repeat_anchor=?,task_series_id=?,revision=revision+1,deleted=?,updated_at=? WHERE id=? AND revision=?",
                params![text_data, bind(&anchor)?, bind(&series_id)?, i64::from(deleting), changed_at, bind(&ident)?, bind(&found["revision"])?])?;
        } else {
            conn.execute("INSERT INTO calendar_tasks(id,data,repeat_anchor,task_series_id,updated_at) VALUES(?,?,?,?,?)",
                params![bind(&ident)?, text_data, bind(&anchor)?, bind(&series_id)?, changed_at])?;
        }
        let mut after = row(conn, "SELECT * FROM calendar_tasks WHERE id=?", [bind(&ident)?])?.ok_or_else(internal)?;
        if completed && flag(&data, "repeat_frequency") && !deleting {
            let next_due = next_task_due(&data, anchor.as_str().unwrap_or(""), &changed_at)?;
            if let (Some(next_due), None) = (next_due, &successor) {
                let next_id = uuid();
                let next_data = with(&data, &[("due", s(&next_due)), ("status", s("todo")), ("completed_at", Value::Null)]);
                conn.execute("INSERT INTO calendar_tasks(id,data,repeat_anchor,task_series_id,previous_task_id,updated_at) VALUES(?,?,?,?,?,?)",
                    params![next_id, py::dumps(&Value::Object(next_data)), bind(&anchor)?, bind(&series_id)?, bind(&ident)?, changed_at])?;
                let created = row(conn, "SELECT * FROM calendar_tasks WHERE id=?", [&next_id])?.ok_or_else(internal)?;
                next_task = Value::Object(task_output(&created)?);
                after.insert("created".into(), Value::Object(created));
            }
        }
        let before = found.map(Value::Object).unwrap_or(Value::Null);
        let undo_id = history(conn, "task", &ident, &before, &Value::Object(after.clone()))?;
        Ok((after, undo_id))
    })?;
    let mut out = task_output(&after)?;
    out.insert("undo_id".into(), Value::from(undo_id));
    out.insert("next_task".into(), next_task);
    Ok(out)
}

// ------------------------------------------------------------------------------ availability, reminders, activity

/// `availability(events, start, end, exclude_id=...)`: merged busy blocks, free gaps, conflicts.
pub fn availability(events: &[Obj], start: &Value, end: &Value, exclude_id: &Value) -> Done<Value> {
    let lower = instant_utc(start)?;
    let upper = instant_utc(end)?;
    let mut busy = Vec::new();
    for event in events {
        if !flag(event, "busy") || py::eq(&event["id"], exclude_id) {
            continue;
        }
        let all_day = flag(event, "all_day");
        let a = lower.clone().max(instant(&event["start"], &event["timezone"], all_day)?);
        let b = upper.clone().min(instant(&event["end"], &event["timezone"], all_day)?);
        busy.push((utc(&a)?.wall, a, b, event["id"].clone()));
    }
    busy.sort_by_key(|item| item.0);
    let mut merged: Vec<(Dt, Dt, Vec<Value>)> = Vec::new();
    let mut conflicts = Vec::new();
    for (_, a, b, ident) in busy {
        if b.le(&a) {
            continue;
        }
        if let Some(last) = merged.last_mut() {
            if utc(&a)?.wall < utc(&last.1)?.wall {
                conflicts.push(json!({"event_id": ident, "with": last.2, "start": a.isoformat(), "end": last.1.clone().min(b.clone()).isoformat()}));
            }
            if utc(&a)?.wall <= utc(&last.1)?.wall {
                last.1 = b.max(last.1.clone());
                last.2.push(ident);
                continue;
            }
        }
        merged.push((a, b, vec![ident]));
    }
    let mut free = Vec::new();
    let mut cursor = lower;
    for (a, b, _) in &merged {
        if cursor.lt(a) {
            free.push(json!({"start": cursor.isoformat(), "end": a.isoformat()}));
        }
        cursor = cursor.max(b.clone());
    }
    if cursor.lt(&upper) {
        free.push(json!({"start": cursor.isoformat(), "end": upper.isoformat()}));
    }
    let busy: Vec<Value> = merged.into_iter().map(|(a, b, ids)| json!({"start": a.isoformat(), "end": b.isoformat(), "events": ids})).collect();
    Ok(json!({"busy": busy, "free": free, "conflicts": conflicts}))
}

pub fn due_reminders(conn: &Connection, applyai: bool) -> Done<Vec<Value>> {
    let at = Dt::now_utc();
    let range = Range { search: "", calendar_id: None, include_hidden: false, applyai };
    let events = occurrences(conn, &s(&at.add(-DAY)?.isoformat()), &s(&at.add(29 * DAY)?.isoformat()), &range)?;
    let mut dismissed = Vec::new();
    for item in rows(conn, "SELECT * FROM calendar_reminder_dismissals", [])? {
        dismissed.push((item["event_id"].clone(), item["occurrence"].clone(), item["minutes"].clone()));
    }
    let earliest = at.add(-DAY)?;
    let mut output = Vec::new();
    for event in events {
        let start = instant(&event["start"], &event["timezone"], flag(&event, "all_day"))?;
        let place = start.zone.clone().ok_or_else(internal)?;
        for minutes in event.get("reminders").and_then(Value::as_array).cloned().unwrap_or_default() {
            let amount = minutes.as_i64().ok_or_else(internal)?;
            let due = utc(&start)?.add(-amount * MINUTE)?.astimezone(&place)?;
            let gone = dismissed.iter().any(|(id, occurrence, mins)| py::eq(id, &event["id"]) && py::eq(occurrence, &event["occurrence"]) && py::eq(mins, &minutes));
            if earliest.le(&due) && due.le(&at) && !gone {
                output.push(json!({"event_id": event["id"], "occurrence": event["occurrence"], "minutes": minutes, "title": event["title"], "due": due.isoformat()}));
            }
        }
    }
    Ok(output)
}

pub fn dismiss_reminder(conn: &Connection, body: &Obj) -> Done<Value> {
    let event_id = body.get("event_id").cloned().unwrap_or(Value::Null);
    event_row(conn, &event_id, false)?;
    let occurrence = text(get(body, "occurrence", &s("")), "Occurrence", 100)?;
    let minutes = body.get("minutes").and_then(py::int).filter(|m| (0..=40320).contains(m)).ok_or_else(|| bad("Invalid reminder minutes"))?;
    conn.execute("INSERT OR IGNORE INTO calendar_reminder_dismissals VALUES(?,?,?)", params![bind(&event_id)?, occurrence, minutes])?;
    Ok(json!({"ok": true}))
}

pub fn activity(conn: &Connection, start: &Value, end: &Value, tz: &Value, applyai: bool) -> Done<Vec<Value>> {
    let lower = instant_utc(start)?;
    let upper = instant_utc(end)?;
    let place = zone(tz)?;
    let mut counts: Vec<(String, Obj)> = Vec::new();
    let mut count = |day: String, kind: &str| {
        let slot = match counts.iter().position(|(seen, _)| *seen == day) {
            Some(at) => at,
            None => {
                counts.push((day, Map::new()));
                counts.len() - 1
            }
        };
        let values = &mut counts[slot].1;
        let next = values.get(kind).and_then(Value::as_i64).unwrap_or(0) + 1;
        values.insert(kind.into(), Value::from(next));
    };
    for (sql, kind) in [("SELECT created_at AS at FROM calendar_history", "calendar_edits"),
                        ("SELECT applied_at AS at FROM applications", "applications"),
                        ("SELECT occurred_at AS at FROM status_events WHERE state='confirmed'", "status_updates")] {
        if !applyai && kind != "calendar_edits" {
            continue;
        }
        for item in rows(conn, sql, [])? {
            let Ok(dt) = instant_utc(&item["at"]) else { continue };
            if lower.le(&dt) && dt.lt(&upper) {
                count(dt.astimezone(&place)?.date().iso(), kind);
            }
        }
    }
    for item in rows(conn, "SELECT data,updated_at FROM calendar_tasks WHERE deleted=0", [])? {
        let task: Obj = serde_json::from_str(item["data"].as_str().ok_or_else(internal)?)?;
        if task.get("status").is_some_and(|status| status == "done") && flag(&task, "completed_at") {
            let dt = instant_utc(&task["completed_at"])?;
            if lower.le(&dt) && dt.lt(&upper) {
                count(dt.astimezone(&place)?.date().iso(), "tasks_completed");
            }
        }
    }
    counts.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(counts.into_iter().map(|(day, values)| {
        let mut out = Map::new();
        out.insert("date".into(), s(&day));
        out.extend(values);
        Value::Object(out)
    }).collect())
}

// ------------------------------------------------------------------------------ imports

/// `_source_event`: refresh a source-dated copy unless the person edited or deleted it.
fn source_event(conn: &Connection, body: &Obj) -> Done<bool> {
    let data = normalize_event(conn, body)?;
    let digest = hex::encode(Sha256::digest(py::dumps_sorted(&Value::Object(data.clone())).as_bytes()));
    let found = row(conn, "SELECT * FROM calendar_events WHERE source_key=?", [bind(&data["source_key"])?])?;
    transaction(conn, false, || {
        match &found {
            Some(found) => {
                if py::truthy(&found["source_overridden"]) || (!py::truthy(&found["deleted"]) && found["source_fingerprint"] == s(&digest)) {
                    return Ok(false);
                }
                update(conn, &found["id"], &data, false, false)?;
                conn.execute("UPDATE calendar_events SET source_fingerprint=? WHERE id=?", params![digest, bind(&found["id"])?])?;
            }
            None => {
                let ident = insert(conn, &data, None)?;
                conn.execute("UPDATE calendar_events SET source_fingerprint=? WHERE id=?", params![digest, ident])?;
            }
        }
        Ok(true)
    })
}

/// `str.title()`.
fn title_case(text: &str) -> String {
    let mut out = String::new();
    let mut previous_cased = false;
    for c in text.chars() {
        if previous_cased { out.extend(c.to_lowercase()) } else { out.extend(c.to_uppercase()) }
        previous_cased = c.is_alphabetic();
    }
    out
}

fn opt(value: &Value) -> String {
    value.as_str().unwrap_or("").to_owned()
}

fn or(value: &Value, default: &str) -> String {
    if py::truthy(value) { value.as_str().map(str::to_owned).unwrap_or_else(|| value.to_string()) } else { default.to_owned() }
}

/// `import_jobs`: refresh source-dated copies; job, application and reminder dates only with ApplyAI.
pub fn import_jobs(conn: &Connection, applyai: bool) -> Done<Value> {
    let mut entries: Vec<(String, String, Value, Value, String, String)> = Vec::new();
    if applyai {
        for item in rows(conn, "SELECT j.*,c.display_name AS company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.deadline_at IS NOT NULL", [])? {
            entries.push((format!("job:{}:deadline", item["id"]), format!("Deadline · {} · {}", opt(&item["company"]), opt(&item["title"])),
                          item["deadline_at"].clone(), item["url"].clone(), "Job deadline (source date)".into(), "UTC".into()));
        }
        for item in rows(conn, "SELECT a.*,j.title,j.url,c.display_name AS company FROM applications a JOIN jobs j ON j.id=a.job_id JOIN companies c ON c.id=j.company_id", [])? {
            for (field, label) in [("applied_at", "Applied"), ("next_action_at", "Next action")] {
                if py::truthy(&item[field]) {
                    let description = if field == "next_action_at" { or(&item["next_action_note"], "") } else { "Application submission".into() };
                    entries.push((format!("application:{}:{field}", item["id"]), format!("{label} · {} · {}", opt(&item["company"]), opt(&item["title"])),
                                  item[field].clone(), item["url"].clone(), description, "UTC".into()));
                }
            }
        }
        for item in rows(conn, "SELECT s.*,j.title,j.url,c.display_name AS company FROM status_events s JOIN applications a ON a.id=s.application_id JOIN jobs j ON j.id=a.job_id JOIN companies c ON c.id=j.company_id WHERE s.state='confirmed' AND s.to_status='interview' AND s.undoes_event_id IS NULL", [])? {
            entries.push((format!("status:{}:interview", item["id"]), format!("Interview status · {} · {}", opt(&item["company"]), opt(&item["title"])),
                          item["occurred_at"].clone(), item["url"].clone(), "Confirmed interview status activity; not an inferred interview appointment.".into(), "UTC".into()));
        }
        for item in rows(conn, "SELECT r.*,j.title,j.url,c.display_name AS company FROM reminders r LEFT JOIN jobs j ON j.id=r.job_id LEFT JOIN companies c ON c.id=j.company_id WHERE r.done_at IS NULL", [])? {
            entries.push((format!("reminder:{}", item["id"]), format!("{} · {} · {}", title_case(&opt(&item["kind"])), or(&item["company"], "Reminder"), or(&item["title"], "")),
                          item["due_at"].clone(), s(&or(&item["url"], "")), or(&item["note"], "Existing reminder; not a source deadline."), "UTC".into()));
        }
    }
    if exists(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunities'", [])? {
        for item in rows(conn, "SELECT id,title,url,deadline,timezone,description FROM opportunities WHERE deadline IS NOT NULL", [])? {
            entries.push((format!("opportunity:{}:deadline", item["id"]), format!("Deadline · {}", opt(&item["title"])),
                          item["deadline"].clone(), item["url"].clone(), or(&item["description"], ""), or(&item["timezone"], "UTC")));
        }
    }
    let mut changed = 0;
    let mut errors = Vec::new();
    for (key, title, value, link, description, tz) in &entries {
        let Some(text_value) = value.as_str() else { return Err(internal()) };
        let all_day = py::len(text_value) == 10;
        let attempt = (|| -> Done<bool> {
            let dt = instant(value, &s(tz), all_day)?;
            let end = dt.add(if all_day { DAY } else { 30 * MINUTE })?;
            let mut body = Map::new();
            for (name, item) in [("title", s(title)), ("start", s(&stamp(&dt, all_day))), ("end", s(&stamp(&end, all_day))), ("timezone", s(tz)),
                                 ("all_day", Value::Bool(all_day)), ("source_key", s(key)), ("source_url", link.clone()), ("description", s(description)),
                                 ("busy", Value::Bool(false)), ("reminders", json!([]))] {
                body.insert(name.into(), item);
            }
            source_event(conn, &body)
        })();
        match attempt {
            Ok(done) => changed += i64::from(done),
            Err(fault) if fault.status < 500 => errors.push(json!({"source_key": key, "error": fault.message})),
            Err(fault) => return Err(fault),
        }
    }
    let active: Vec<&String> = entries.iter().map(|entry| &entry.0).collect();
    let refreshed: &[&str] = if applyai { REFRESHED_SOURCES } else { &["opportunity"] };
    transaction(conn, false, || {
        for item in rows(conn, "SELECT * FROM calendar_events WHERE deleted=0 AND source_overridden=0 AND source_key IS NOT NULL", [])? {
            let key = opt(&item["source_key"]);
            if py::truthy(&item["source_fingerprint"]) && refreshed.contains(&key.split(':').next().unwrap_or("")) && !active.contains(&&key) {
                let data: Obj = serde_json::from_str(item["data"].as_str().ok_or_else(internal)?)?;
                update(conn, &item["id"], &data, true, false)?;
                changed += 1;
            }
        }
        Ok(())
    })?;
    Ok(json!({"changed": changed, "review_errors": errors, "source_count": entries.len()}))
}

/// `import_reviewed(conn, items)`: validate the whole reviewed import, then write it at once.
pub fn import_reviewed(conn: &Connection, items: &Value) -> Done<Value> {
    let Some(items) = is_list(items, 5000) else { return Err(bad("Import must contain at most 5000 reviewed items")) };
    let mut normalized = Vec::new();
    for item in items {
        let Some(event) = item.as_object().and_then(|item| item.get("event")).and_then(Value::as_object) else {
            return Err(bad("Each reviewed item requires an event"));
        };
        let data = normalize_event(conn, event)?;
        let mut exceptions = Vec::new();
        let listed = item.get("exceptions").cloned().unwrap_or(json!([]));
        let Value::Array(listed) = listed else { return Err(internal()) };
        for ex in &listed {
            let Some(ex) = ex.as_object() else { return Err(internal()) };
            let (_, key) = assert_occurrence(&data, get(ex, "occurrence", &s("")))?;
            let replacement = match ex.get("event").filter(|v| py::truthy(v)) {
                Some(Value::Object(event)) => Some(normalize_event(conn, event)?),
                Some(_) => return Err(internal()),
                None => None,
            };
            exceptions.push((key, replacement));
        }
        require_writable(conn, &data["calendar_id"])?;
        normalized.push((data, exceptions));
    }
    let (mut created, mut duplicates, mut undo_ids) = (Vec::new(), Vec::new(), Vec::new());
    transaction(conn, false, || {
        for (data, exceptions) in &normalized {
            if !data["source_key"].is_null() {
                if let Some(existing) = row(conn, "SELECT id FROM calendar_events WHERE source_key=?", [bind(&data["source_key"])?])? {
                    duplicates.push(existing["id"].clone());
                    continue;
                }
            }
            let ident = insert(conn, data, None)?;
            for (key, replacement) in exceptions {
                conn.execute("INSERT INTO calendar_exceptions VALUES(?,?,?)",
                             params![ident, key, replacement.as_ref().map(|item| py::dumps(&Value::Object(item.clone())))])?;
            }
            let id = s(&ident);
            undo_ids.push(history(conn, "event", &id, &Value::Null, &snapshot(conn, &id)?)?);
            created.push(ident);
        }
        Ok(())
    })?;
    Ok(json!({"created": created, "duplicates": duplicates, "undo_ids": undo_ids}))
}
