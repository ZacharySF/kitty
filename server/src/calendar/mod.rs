//! The calendar module's local routes (`kitty.web_calendar` in Python): calendars, events
//! with recurrence, tasks, undo, availability, reminders, wall-time resolution and the
//! source-date and reviewed imports. Google Calendar sync (`/api/calendar/google/*`) stays
//! with the Python worker: its previews propose effects approved in the worker's window
//! session, and its OAuth and network calls are Python's.

pub mod core;
pub mod rrule;

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{json, Value};

use self::core::{s, Obj, Range};
use crate::ported::{fail, Call, Ctx, Done, Group};

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

static EVENT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/calendar/events/([a-zA-Z0-9_-]{1,100})$").expect("valid"));

fn owns(path: &str, _mutation: bool) -> bool {
    (path == "/api/calendar" || path.starts_with("/api/calendar/"))
        && !(path == "/api/calendar/google" || path.starts_with("/api/calendar/google/"))
        && !matches!(path, "/api/calendar/export" | "/api/calendar/import/preview")
}

fn object(value: Obj) -> Value {
    Value::Object(value)
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    if call.mutation() { post(ctx, call) } else { get(ctx, call) }
}

fn get(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    let applyai = ctx.has("applyai");
    if call.path == "/api/calendar" {
        if !call.only(&["start", "end", "timezone", "search", "calendar_id", "hidden"]) {
            return Err(fail(400, "Unknown calendar query"));
        }
        let at = crate::ported::time::Dt::now_utc();
        let start = call.get("start").map(s).unwrap_or_else(|| s(&at.add(-7 * crate::ported::time::DAY).expect("in range").isoformat()));
        let end = call.get("end").map(s).unwrap_or_else(|| s(&at.add(35 * crate::ported::time::DAY).expect("in range").isoformat()));
        let tz = s(call.get("timezone").unwrap_or("UTC"));
        core::zone(&tz)?;
        let search = core::text(&s(call.get("search").unwrap_or("")), "Search", 500)?;
        let range = Range { search: &search, calendar_id: call.get("calendar_id"), include_hidden: call.get("hidden") == Some("1"), applyai };
        let events = core::occurrences(conn, &start, &end, &range)?;
        let available = core::availability(&events, &start, &end, &Value::Null)?;
        let calendars: Vec<Value> = core::calendars(conn)?.into_iter().map(|mut cal| {
            cal.remove("sync_token");
            cal.remove("google_etag");
            Value::Object(cal)
        }).collect();
        let subjects = crate::ported::db::rows(conn, "SELECT id,name,color FROM study_subjects ORDER BY name,id", [])?;
        let mut out = json!({
            "calendars": calendars,
            "events": events,
            "tasks": core::tasks(conn, &search)?,
            "subjects": subjects,
            "activity": core::activity(conn, &start, &end, &tz, applyai)?,
        });
        if let (Value::Object(out), Value::Object(available)) = (&mut out, available) {
            out.extend(available);
        }
        return Ok(Some(out));
    }
    if let Some(found) = EVENT.captures(call.path) {
        call.no_query()?;
        return Ok(Some(object(core::get_event(conn, &s(&found[1]), applyai)?)));
    }
    if call.path == "/api/calendar/reminders" {
        call.no_query()?;
        return Ok(Some(json!({"items": core::due_reminders(conn, applyai)?})));
    }
    Ok(None)
}

fn post(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    let applyai = ctx.has("applyai");
    let body = call.body();
    let field = |name: &str, default: Value| body.get(name).cloned().unwrap_or(default);
    Ok(Some(match call.path {
        "/api/calendar/calendars" => object(core::save_calendar(conn, body)?),
        "/api/calendar/calendars/delete" => core::delete_calendar(conn, body)?,
        "/api/calendar/events" => object(core::create_event(conn, body)?),
        "/api/calendar/tasks" => object(core::save_task(conn, body)?),
        "/api/calendar/undo" => {
            let Some(id) = body.get("id").and_then(crate::ported::py::int) else { return Err(fail(400, "Undo ID must be an integer")) };
            core::undo(conn, id)?
        }
        "/api/calendar/reminders/dismiss" => {
            core::ensure_visible(conn, &field("event_id", Value::Null), applyai)?;
            core::dismiss_reminder(conn, body)?
        }
        "/api/calendar/time" => json!({"value": core::wall_instant(&field("value", s("")), &field("timezone", s("UTC")), &field("fold", json!(0)))?}),
        "/api/calendar/import-jobs" => {
            if !body.is_empty() {
                return Err(fail(400, "Job-date refresh expects an empty object"));
            }
            core::import_jobs(conn, applyai)?
        }
        "/api/calendar/import" => {
            if body.get("confirmed") != Some(&Value::Bool(true)) {
                return Err(fail(400, "Review and confirm selected imported items"));
            }
            core::import_reviewed(conn, &field("items", Value::Null))?
        }
        "/api/calendar/availability" => {
            let (start, end) = (field("start", s("")), field("end", s("")));
            let range = Range { search: "", calendar_id: None, include_hidden: true, applyai };
            let events = core::occurrences(conn, &start, &end, &range)?;
            core::availability(&events, &start, &end, &field("exclude_id", Value::Null))?
        }
        path => match EVENT.captures(path) {
            Some(found) => {
                let ident = s(&found[1]);
                core::ensure_visible(conn, &ident, applyai)?;
                object(core::edit_event(conn, &ident, body)?)
            }
            None => return Ok(None),
        },
    }))
}
