//! `kitty.core.math.sprints`: timed mental-arithmetic runs with Zetamac's defaults. Problems
//! come from the stored seed (`random.Random("sprint:<mode>:<seed>")`), so the browser checks
//! answers with no round trip and the server can regenerate any run.

use std::collections::BTreeSet;

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::practice::{conflict, not_found};
use super::pyrandom::Random;
use crate::ported::db::{self, transaction};
use crate::ported::time::{fromisoformat, Date, Dt, Zone};
use crate::ported::{fail, internal, py, Done};

const MODES: [&str; 2] = ["zetamac", "mc"];
const OPS: [&str; 4] = ["add", "sub", "mul", "div"];
const DEFAULT_DURATION: i64 = 120;
/// Problems issued per second of sprint: well beyond the fastest Zetamac pace.
const RATE: i64 = 2;
const SLACK_MS: i128 = 5000;

/// `problems(mode, seed, ops, count)`.
pub fn problems(mode: &str, seed: i64, ops: &[&str], count: usize) -> Vec<Value> {
    let mut rng = Random::from_text(&format!("sprint:{mode}:{seed}"));
    let (mut out, mut previous) = (Vec::new(), None::<String>);
    while out.len() < count {
        let op = rng.choice(ops);
        let (q, answer, near) = if op == "add" || op == "sub" {
            let (a, b) = (rng.randint(2, 100), rng.randint(2, 100));
            let (q, answer) = if op == "add" { (format!("{a} + {b}"), a + b) } else { (format!("{} − {a}", a + b), b) };
            (q, answer, vec![answer + 10, answer - 10])
        } else {
            let (a, b) = (rng.randint(2, 12), rng.randint(2, 100));
            if op == "mul" {
                (format!("{a} × {b}"), a * b, vec![(a + 1) * b, (a - 1) * b, a * (b + 1)])
            } else {
                (format!("{} ÷ {a}", a * b), b, vec![b + 10, b - 10])
            }
        };
        if previous.as_deref() == Some(q.as_str()) {
            continue;
        }
        previous = Some(q.clone());
        let mut item = Map::new();
        item.insert("q".into(), json!(q));
        item.insert("a".into(), json!(answer));
        if mode == "mc" {
            item.insert("choices".into(), json!(choices(&mut rng, answer, &near)));
        }
        out.push(Value::Object(item));
    }
    out
}

/// `_choices`: the answer and three near misses, shuffled.
fn choices(rng: &mut Random, answer: i64, near: &[i64]) -> Vec<i64> {
    let swapped = if answer >= 10 {
        let digits = answer.to_string();
        let bytes = digits.as_bytes();
        let n = bytes.len();
        format!("{}{}{}", &digits[..n - 2], bytes[n - 1] as char, bytes[n - 2] as char).parse().expect("digits")
    } else {
        answer + 3
    };
    let mut pool: BTreeSet<i64> = [answer + 1, answer - 1, answer + 2, answer - 2, swapped].into_iter().chain(near.iter().copied()).collect();
    pool.remove(&answer);
    let pool: Vec<i64> = pool.into_iter().filter(|value| *value > 0).collect();
    let mut out = vec![answer];
    out.extend(rng.sample(&pool, 3));
    rng.shuffle(&mut out);
    out
}

/// `_ops(value)`.
fn ops(value: Option<&Value>) -> Done<Vec<&'static str>> {
    let Some(value) = value.filter(|value| !value.is_null()) else { return Ok(OPS.to_vec()) };
    let refuse = || fail(400, "ops must be a non-empty list of add, sub, mul, div");
    let items = value.as_array().filter(|items| !items.is_empty()).ok_or_else(refuse)?;
    let names: Vec<&str> = items.iter().map(|item| item.as_str().filter(|name| OPS.contains(name))).collect::<Option<_>>().ok_or_else(refuse)?;
    if names.iter().collect::<BTreeSet<_>>().len() != names.len() {
        return Err(refuse());
    }
    Ok(OPS.iter().copied().filter(|op| names.contains(op)).collect())
}

/// `start(conn, body)`.
pub fn start(conn: &Connection, body: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    if body.keys().any(|key| !["mode", "duration_s", "ops"].contains(&key.as_str())) {
        return Err(fail(400, "Unknown sprint field"));
    }
    let mode = match body.get("mode") {
        None => "zetamac",
        Some(value) => value.as_str().and_then(|mode| MODES.iter().copied().find(|known| *known == mode))
            .ok_or_else(|| fail(400, "mode must be zetamac or mc"))?,
    };
    let duration = match body.get("duration_s") {
        None => DEFAULT_DURATION,
        Some(value) => py::int(value).filter(|duration| (10..=900).contains(duration))
            .ok_or_else(|| fail(400, "duration_s must be an integer from 10 to 900"))?,
    };
    let ops = ops(body.get("ops"))?;
    let (seed, count) = (super::practice::randbelow(1 << 31) as i64, duration * RATE);
    let ident = crate::auth::token_hex(16);
    transaction(conn, false, || {
        conn.execute("INSERT INTO math_sprints(id, mode, duration_s, seed, ops, problem_count, started_at) VALUES (?,?,?,?,?,?,?)",
                     rusqlite::params![ident, mode, duration, seed, py::dumps(&json!(ops)), count, moment.isoformat()])?;
        Ok(())
    })?;
    Ok(json!({"sprint_id": ident, "mode": mode, "duration_s": duration, "ops": ops, "problems": problems(mode, seed, &ops, count as usize)}))
}

/// `finish(conn, sprint_id, body)`.
pub fn finish(conn: &Connection, sprint_id: &str, body: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    if body.keys().any(|key| !["score", "wrong", "elapsed_ms"].contains(&key.as_str())) {
        return Err(fail(400, "Unknown sprint result field"));
    }
    let wrong_value = body.get("wrong").cloned().unwrap_or(json!(0));
    let (score, wrong, elapsed) = match (body.get("score").and_then(py::int), py::int(&wrong_value), body.get("elapsed_ms").and_then(py::int)) {
        (Some(score), Some(wrong), Some(elapsed)) if wrong >= 0 && elapsed >= 0 => (i128::from(score), i128::from(wrong), i128::from(elapsed)),
        _ => return Err(fail(400, "score, wrong and elapsed_ms must be integers; wrong and elapsed_ms non-negative")),
    };
    let row = transaction(conn, true, || {
        let row = db::row(conn, "SELECT * FROM math_sprints WHERE id=?", [sprint_id])?.ok_or_else(|| not_found("Unknown sprint"))?;
        if row.get("finished_at").is_some_and(py::truthy) {
            return Err(conflict("This sprint's result is already saved"));
        }
        let int = |key: &str| row.get(key).and_then(Value::as_i64).map(i128::from).unwrap_or(0);
        let mode = row.get("mode").and_then(Value::as_str).unwrap_or("");
        let right = if mode == "mc" { score + wrong } else { score };
        if right < 0 || right + wrong > int("problem_count") || (mode == "zetamac" && score < 0) {
            return Err(fail(400, "That score isn't possible for this sprint"));
        }
        if elapsed > int("duration_s") * 1000 + SLACK_MS {
            return Err(fail(400, "elapsed_ms is longer than the sprint"));
        }
        conn.execute("UPDATE math_sprints SET score=?, wrong=?, elapsed_ms=?, finished_at=? WHERE id=?",
                     rusqlite::params![score as i64, wrong as i64, elapsed as i64, moment.isoformat(), sprint_id])?;
        Ok(row)
    })?;
    let duration = row.get("duration_s").and_then(Value::as_i64).unwrap_or(0).to_string();
    let result = history(conn, &[("duration_s".to_owned(), duration)], moment)?;
    let saved = result["sprints"].as_array().and_then(|items| items.iter().rev().find(|item| item["id"] == sprint_id)).cloned().ok_or_else(internal)?;
    Ok(json!({"sprint": saved, "best": result["best"], "streak_days": result["streak_days"]}))
}

/// `str.isdigit()` for one character: the decimal digits and the other digits (superscripts,
/// circled and parenthesized digits, ...) of CPython's Unicode 15.0 data.
fn is_digit(c: char) -> bool {
    const OTHER: &[(u32, u32)] = &[
        (0xb2, 0xb3), (0xb9, 0xb9), (0x1369, 0x1371), (0x19da, 0x19da), (0x2070, 0x2070), (0x2074, 0x2079), (0x2080, 0x2089),
        (0x2460, 0x2468), (0x2474, 0x247c), (0x2488, 0x2490), (0x24ea, 0x24ea), (0x24f5, 0x24fd), (0x24ff, 0x24ff), (0x2776, 0x277e),
        (0x2780, 0x2788), (0x278a, 0x2792), (0x10a40, 0x10a43), (0x10e60, 0x10e68), (0x11052, 0x1105a), (0x1f100, 0x1f10a),
    ];
    crate::search::text::is_decimal(c) || OTHER.iter().any(|&(start, end)| (start..=end).contains(&(c as u32)))
}

/// `history(conn, query)`: finished sprints oldest first (the last 200), best score per mode,
/// daily streak.
pub fn history(conn: &Connection, query: &[(String, String)], moment: &Dt) -> Done<Value> {
    if query.iter().any(|(key, _)| key != "duration_s" && key != "timezone") {
        return Err(fail(400, "Unknown sprint history parameter"));
    }
    let get = |name: &str| query.iter().find(|(key, _)| key == name).map(|(_, value)| value.as_str());
    let zone = match get("timezone") {
        None => Zone::named("UTC").unwrap_or(Zone::Utc),
        Some(name) => Zone::named(name).ok_or_else(|| fail(400, "Unknown timezone"))?,
    };
    let mut filter = "finished_at IS NOT NULL".to_owned();
    let mut parameters: Vec<i64> = Vec::new();
    if let Some(duration) = get("duration_s") {
        if duration.is_empty() || !duration.chars().all(is_digit) {
            return Err(fail(400, "duration_s must be an integer"));
        }
        // `int(text)`: a digit that is not decimal is its ValueError; past SQLite's range, its
        // OverflowError (500).
        let value = crate::search::text::decimal_value(duration)
            .ok_or_else(|| fail(400, format!("invalid literal for int() with base 10: {}", python_repr(duration))))?;
        parameters.push(i64::try_from(value).ok().filter(|_| duration.chars().count() < 39).ok_or_else(internal)?);
        filter.push_str(" AND duration_s=?");
    }
    let mut statement = conn.prepare(&format!("SELECT id, mode, duration_s, score, wrong, elapsed_ms, finished_at FROM math_sprints WHERE {filter} \
        ORDER BY finished_at DESC, rowid DESC LIMIT 200"))?;
    let columns: Vec<String> = statement.column_names().into_iter().map(str::to_owned).collect();
    let mut rows = Vec::new();
    let mut found = statement.query(rusqlite::params_from_iter(parameters.iter()))?;
    while let Some(row) = found.next()? {
        let mut item = Map::new();
        for (index, name) in columns.iter().enumerate() {
            item.insert(name.clone(), db::column(row.get_ref(index)?)?);
        }
        rows.push(Value::Object(item));
    }
    rows.reverse();
    let mut best = Map::new();
    for mode in MODES {
        best.insert(mode.into(), Value::Null);
    }
    let mut statement = conn.prepare(&format!("SELECT mode, MAX(score) FROM math_sprints WHERE {filter} GROUP BY mode"))?;
    let mut found = statement.query(rusqlite::params_from_iter(parameters.iter()))?;
    while let Some(row) = found.next()? {
        let mode = db::column(row.get_ref(0)?)?;
        best.insert(mode.as_str().map_or_else(|| mode.to_string(), str::to_owned), db::column(row.get_ref(1)?)?);
    }
    let mut days: BTreeSet<i64> = BTreeSet::new();
    for row in db::rows(conn, "SELECT finished_at FROM math_sprints WHERE finished_at IS NOT NULL", [])? {
        let stamp = row.get("finished_at").and_then(Value::as_str).unwrap_or("");
        days.insert(fromisoformat(stamp).ok_or_else(|| fail(400, format!("Invalid isoformat string: {}", python_repr(stamp))))?.astimezone(&zone)?.date().days());
    }
    let today = moment.astimezone(&zone)?.date();
    Ok(json!({"sprints": rows, "best": best, "streak_days": streak(&days, today)}))
}

/// `_streak`: consecutive sprint days ending today, or yesterday while today is still open.
fn streak(days: &BTreeSet<i64>, today: Date) -> i64 {
    let mut current = if days.contains(&today.days()) { today.days() } else { today.days() - 1 };
    let mut count = 0;
    while days.contains(&current) {
        count += 1;
        current -= 1;
    }
    count
}

/// `repr(text)` for the messages that quote a value (single quotes; ASCII text here).
fn python_repr(text: &str) -> String {
    if text.contains('\'') && !text.contains('"') { format!("\"{text}\"") } else { format!("'{}'", text.replace('\'', "\\'")) }
}
