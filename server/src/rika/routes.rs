//! `kitty.core.rika.routes`' readouts and `forget`: the routes most used first with their
//! shortcuts and averages, and the summary of today's requests and the fourteen local days
//! before, in the personal calendar's zone. Recording, recall and the shortcuts' matching stay
//! in Python with Rika's loop.

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::decimal::Decimal;
use crate::ported::time::{self, Date, Dt, Zone, SECOND};
use crate::ported::{db, fail, internal, py, Done};

const SHORTCUT_ACTIVE: i64 = 2;
const TREND_DAYS: i64 = 14;

fn decimal(value: Option<&Value>) -> Done<Decimal> {
    match value {
        Some(Value::String(text)) => Decimal::parse(text).ok_or_else(internal),
        Some(Value::Number(number)) => Decimal::parse(&number.to_string()).ok_or_else(internal),
        _ => Err(internal()),
    }
}

/// `_money(total / count)`.
fn money(total: Decimal, count: i64) -> Done<f64> {
    total.divide(count).and_then(Decimal::money).ok_or_else(internal)
}

fn int(row: &Map<String, Value>, key: &str) -> Done<i64> {
    row.get(key).and_then(Value::as_i64).ok_or_else(internal)
}

/// `routes.listing`: routes, most used first.
fn listing(conn: &Connection) -> Done<Vec<Value>> {
    let mut shortcuts: Vec<(Value, Value)> = Vec::new();
    for row in db::rows(conn, "SELECT * FROM rika_shortcuts ORDER BY seen>=? DESC,uses DESC,seen DESC,updated_at DESC", [SHORTCUT_ACTIVE])? {
        let signature = row.get("signature").cloned().unwrap_or(Value::Null);
        if !shortcuts.iter().any(|(known, _)| *known == signature) {
            let active = int(&row, "seen")? >= SHORTCUT_ACTIVE;
            shortcuts.push((signature, json!({"template": row["template"], "uses": row["uses"], "active": active})));
        }
    }
    let mut found = Vec::new();
    for row in db::rows(conn, "SELECT * FROM rika_routes ORDER BY uses DESC,updated_at DESC", [])? {
        let uses = int(&row, "uses")?.max(1);
        let path: Value = serde_json::from_str(row.get("path_json").and_then(Value::as_str).ok_or_else(internal)?)?;
        let shortcut = shortcuts.iter().find(|(signature, _)| *signature == row["signature"]).map_or(Value::Null, |(_, shortcut)| shortcut.clone());
        found.push(json!({"signature": row["signature"], "label": row["label"], "tier": row["tier"], "path": path,
                          "uses": row["uses"], "escalations": row["escalations"], "avg_tokens": int(&row, "tokens")?.div_euclid(uses),
                          "avg_cost_usd": money(decimal(row.get("cost_usd"))?, uses)?, "shortcut": shortcut,
                          "example": row["example"], "updated_at": row["updated_at"]}));
    }
    Ok(found)
}

/// `datetime.fromisoformat(stamp).astimezone(zone)`: a naive stamp is the computer's local time.
fn local(stamp: &str, zone: &Zone) -> Done<Dt> {
    let parsed = time::fromisoformat(stamp).ok_or_else(internal)?;
    let aware = if parsed.utcoffset().is_some() {
        parsed
    } else {
        let date = parsed.date();
        let of_day = parsed.time_of_day();
        let civil = jiff::civil::date(date.year as i16, date.month as i8, date.day as i8)
            .at((of_day / time::HOUR) as i8, (of_day / time::MINUTE % 60) as i8, (of_day / SECOND % 60) as i8, ((of_day % SECOND) * 1000) as i32);
        let instant = jiff::tz::TimeZone::system().to_ambiguous_zoned(civil).compatible().map_err(|_| internal())?.timestamp();
        Dt { wall: instant.as_microsecond(), zone: Some(Zone::Utc), fold: false }
    };
    Ok(aware.astimezone(zone)?)
}

fn count(row: &Map<String, Value>, key: &str) -> Done<i64> {
    match row.get(key) {
        None | Some(Value::Null) => Ok(0),
        Some(value) => value.as_i64().ok_or_else(internal),
    }
}

/// `routes.summary`.
fn summary(conn: &Connection) -> Done<Value> {
    let name: String = conn.query_row("SELECT timezone FROM calendar_calendars WHERE id='personal'", [], |row| row.get(0))?;
    let zone = Zone::named(&name).ok_or_else(internal)?;
    let today = Dt::now_utc().astimezone(&zone)?.date();
    let first = today.add_days(1 - TREND_DAYS).ok_or_else(internal)?;
    let start = Dt::midnight(first, Some(zone.clone())).astimezone(&Zone::Utc)?;
    let of_day = start.time_of_day();
    let start = format!("{}T{:02}:{:02}:{:02}.{:06}+00:00", start.date().iso(), of_day / time::HOUR, of_day / time::MINUTE % 60,
                        of_day / SECOND % 60, of_day % SECOND);
    let mut days: Vec<(Date, i64, Decimal)> =
        (0..TREND_DAYS).map(|offset| Ok((first.add_days(offset).ok_or_else(internal)?, 0, Decimal::ZERO))).collect::<Done<_>>()?;
    let (mut tokens, mut prompt, mut cached) = (0_i64, 0_i64, 0_i64);
    for row in db::rows(conn, "SELECT created_at,cost_usd,tokens_in,tokens_cached,tokens_out FROM rika_messages \
                               WHERE role='rika' AND reply_to IS NOT NULL AND status<>'pending' AND created_at>=?", [&start])? {
        let day = local(row.get("created_at").and_then(Value::as_str).ok_or_else(internal)?, &zone)?.date();
        let Some(entry) = days.iter_mut().find(|(known, _, _)| *known == day) else { continue };
        entry.1 += 1;
        // `Decimal(row["cost_usd"] or 0)`.
        let cost = row.get("cost_usd").filter(|value| py::truthy(value));
        entry.2 = entry.2.add(if cost.is_some() { decimal(cost)? } else { Decimal::ZERO }).ok_or_else(internal)?;
        prompt += count(&row, "tokens_in")?;
        cached += count(&row, "tokens_cached")?;
        if day == today {
            tokens += count(&row, "tokens_in")? + count(&row, "tokens_out")?;
        }
    }
    let (_, requests, cost) = days.iter().find(|(day, _, _)| *day == today).copied().ok_or_else(internal)?;
    let share = if prompt != 0 { json!(1.0_f64.min(cached as f64 / prompt as f64)) } else { json!(0) };
    let trend = days.iter().map(|&(day, count, total)| {
        Ok(json!({"day": day.iso(), "requests": count, "avg_cost_usd": if count != 0 { json!(money(total, count)?) } else { json!(0) }}))
    }).collect::<Done<Vec<_>>>()?;
    Ok(json!({"today_requests": requests, "today_tokens": tokens, "today_cost_usd": cost.money().ok_or_else(internal)?,
              "cached_share": share, "trend": trend}))
}

/// `{routes, summary}`.
pub fn readout(conn: &Connection) -> Done<Value> {
    Ok(json!({"routes": listing(conn)?, "summary": summary(conn)?}))
}

/// `routes.forget`: the route with its shapes and shortcuts (their foreign keys cascade).
pub fn forget(conn: &Connection, signature: &str) -> Done<()> {
    db::transaction(conn, false, || {
        if conn.execute("DELETE FROM rika_routes WHERE signature=?", [signature])? == 0 {
            return Err(fail(404, "Route not found"));
        }
        Ok(())
    })
}
