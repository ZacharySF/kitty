//! Rika's local state over HTTP: the conversation history (`GET /api/rika/history`), the
//! routing memory (`/api/rika/routes`, `/routes/forget`), the memory index and preferences
//! (`/api/rika/memory`, `/memory/rebuild`, `/preferences[/<id>[/delete]]`), as the deleted
//! `kitty.web_rika_memory` and `web_rika`'s history branch answered them over
//! `kitty.core.rika.store`, `routes` and `memory`: the same answers, order, refusals and rows.
//! Chat, plans, approvals, undo, voice, speech and the agent roster stay on the worker (the AI
//! client, the tools over every module's Python core, the speech engine, the roster's memory);
//! `kitty.core.rika` stays whole for them.

pub(crate) mod decimal;
mod history;
mod memory;
mod routes;

use serde_json::{json, Map, Value};

use crate::ported::time::{self, Dt, SECOND};
use crate::ported::{fail, Call, Ctx, Done, Group};
use crate::search::text::{decimal_value, is_decimal};

pub static GROUP: Group = Group { owns, module: None, answer };

const PREFIX: &str = "/api/rika";
const QUERY_MAX: usize = 300;

enum Route {
    History,
    Routes,
    Memory,
    Forget,
    Add,
    Update(i64),
    Delete(i64),
    Rebuild,
}

/// `(\d{1,18})` and `int()` of it: decimal digits of any script.
fn ident(text: &str) -> Option<i64> {
    let count = text.chars().count();
    if !(1..=18).contains(&count) || !text.chars().all(is_decimal) {
        return None;
    }
    i64::try_from(decimal_value(text)?).ok()
}

fn route(path: &str, mutation: bool) -> Option<Route> {
    let rest = path.strip_prefix(PREFIX)?;
    if !mutation {
        return match rest {
            "/history" => Some(Route::History),
            "/routes" => Some(Route::Routes),
            "/memory" => Some(Route::Memory),
            _ => None,
        };
    }
    match rest {
        "/routes/forget" => return Some(Route::Forget),
        "/preferences" => return Some(Route::Add),
        "/memory/rebuild" => return Some(Route::Rebuild),
        _ => {}
    }
    let tail = rest.strip_prefix("/preferences/")?;
    match tail.strip_suffix("/delete") {
        Some(number) => ident(number).map(Route::Delete),
        None => ident(tail).map(Route::Update),
    }
}

fn owns(path: &str, mutation: bool) -> bool {
    route(path, mutation).is_some()
}

/// `_keys`.
fn keys(body: &Map<String, Value>, allowed: &[&str], required: &[&str]) -> Done<()> {
    if body.keys().any(|key| !allowed.contains(&key.as_str())) || required.iter().any(|key| !body.contains_key(*key)) {
        return Err(fail(400, "Unexpected or missing Rika request fields"));
    }
    Ok(())
}

/// `kitty.core.rika.store.now`: `datetime.now(timezone.utc).isoformat(timespec="microseconds")`.
fn now() -> String {
    let at = Dt::now_utc();
    let of_day = at.time_of_day();
    format!("{}T{:02}:{:02}:{:02}.{:06}+00:00", at.date().iso(), of_day / time::HOUR, of_day / time::MINUTE % 60,
            of_day / SECOND % 60, of_day % SECOND)
}

/// `_preference`'s `LookupError` (404) and `ValueError` (400) are the core's own faults here.
fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let Some(route) = route(call.path, call.mutation()) else { return Ok(None) };
    let conn = ctx.conn;
    let body = call.body();
    Ok(Some(match route {
        Route::History => {
            if !call.only(&["before"]) {
                return Err(fail(400, "History accepts only before"));
            }
            let before = call.get("before");
            if before.is_some_and(|text| {
                !(10..=40).contains(&text.len()) || !text.bytes().all(|b| b.is_ascii_digit() || b"T:.+-".contains(&b))
            }) {
                return Err(fail(400, "before must be an item's created_at"));
            }
            history::history(conn, before)?
        }
        Route::Routes => {
            call.no_query()?;
            routes::readout(conn)?
        }
        Route::Memory => {
            if !call.only(&["q"]) {
                return Err(fail(400, "Memory accepts only q"));
            }
            let text = call.get("q");
            if text.is_some_and(|text| text.chars().count() > QUERY_MAX || text.contains('\0')) {
                return Err(fail(400, format!("Search for at most {QUERY_MAX} characters")));
            }
            memory::overview(conn, ctx.modules, text)?
        }
        Route::Forget => {
            keys(body, &["signature"], &["signature"])?;
            let signature = body["signature"].as_str().filter(|text| text.chars().count() <= 2000)
                .ok_or_else(|| fail(400, "signature must be a route's signature"))?;
            routes::forget(conn, signature)?;
            routes::readout(conn)?
        }
        Route::Add => {
            keys(body, &["text"], &["text"])?;
            json!({"preference": memory::add_preference(conn, &body["text"])?})
        }
        Route::Update(id) => {
            keys(body, &["text"], &["text"])?;
            json!({"preference": memory::update_preference(conn, id, &body["text"])?})
        }
        Route::Delete(id) => {
            keys(body, &[], &[])?;
            json!({"deleted": memory::delete_preference(conn, id)?})
        }
        Route::Rebuild => {
            keys(body, &[], &[])?;
            json!({"index": memory::rebuild(conn, ctx.modules)?})
        }
    }))
}
