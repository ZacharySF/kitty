//! `kitty.core.math.mastery`: locked → ready → learning → mastered, an FSRS card per mastered
//! topic (py-fsrs 6 with no learning steps, crate::flashcards::scheduler) and FIRe-lite
//! implicit credit to encompassed prerequisites; every state carries its `why`.

use std::collections::HashMap;

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::topics::{self, Topic};
use crate::flashcards::pyjson;
use crate::flashcards::pytime::{self, Parsed};
use crate::flashcards::scheduler::{self, Card, Settings};
use crate::ported::db;
use crate::ported::time::{fromisoformat, Dt};
use crate::ported::{internal, Done};

pub const MASTERY_RUN: i64 = 3;
const EARLY_DISCOUNT: f64 = 0.5;
pub const AGAIN: u8 = 1;
pub const HARD: u8 = 2;
pub const GOOD: u8 = 3;
pub const EASY: u8 = 4;

/// `_scheduler()`: a topic review is a fresh problem, not a memorised card: no same-day steps.
fn settings() -> Settings {
    Settings { retention: 0.9, learning_steps: Vec::new(), relearning_steps: Vec::new(), maximum_interval: 36500 }
}

/// `rating(correct, elapsed_ms, target_s)`: Again when wrong; Hard when slower than 2× target;
/// Easy under ½ target; else Good.
pub fn rating(correct: bool, elapsed_ms: Option<i64>, target_s: u32) -> u8 {
    if !correct {
        return AGAIN;
    }
    let Some(elapsed) = elapsed_ms else { return GOOD };
    let seconds = elapsed as f64 / 1000.0;
    if seconds > 2.0 * f64::from(target_s) {
        HARD
    } else if seconds < f64::from(target_s) / 2.0 {
        EASY
    } else {
        GOOD
    }
}

/// A UTC moment as py-fsrs takes it.
pub fn instant(moment: &Dt) -> i64 {
    moment.utc()
}

/// `Card(due=moment)`'s id: the epoch milliseconds of the real clock when it is made.
fn card_id() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |elapsed| elapsed.as_millis() as i64)
}

/// `_review(card_json, grade, moment)`: the reviewed card (a new one without `card_json`).
pub fn review(card_json: Option<&str>, grade: u8, moment: &Dt) -> Done<Card> {
    let now = instant(moment);
    let card = match card_json.filter(|text| !text.is_empty()) {
        Some(text) => Card::from_json(text).map_err(|_| internal())?,
        None => Card::new(card_id(), Parsed { wall: now, offset: Some(0) }),
    };
    scheduler::review(&settings(), &card, grade, now).map_err(|_| internal())
}

/// The card's `to_json()` and `due.isoformat()`.
pub fn stored(card: &Card) -> (String, String) {
    (card.to_json(), pytime::iso_parsed(card.due))
}

/// `_days(delta_seconds)`.
fn days(delta_seconds: f64) -> String {
    let days = (delta_seconds / 86400.0).round_ties_even();
    if days <= 0.0 {
        "today".into()
    } else if days == 1.0 {
        "1 day".into()
    } else {
        format!("{} days", days as i64)
    }
}

/// `timedelta.total_seconds()` of a difference in microseconds.
fn seconds(micros: i64) -> f64 {
    micros as f64 / 1_000_000.0
}

fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str).filter(|value| !value.is_empty())
}

fn join(names: &[&str]) -> String {
    match names {
        [one] => (*one).to_owned(),
        _ => format!("{} and {}", names[..names.len() - 1].join(", "), names[names.len() - 1]),
    }
}

/// `_rows`: every `math_mastery` row by topic.
fn rows(conn: &Connection) -> Done<HashMap<String, Map<String, Value>>> {
    Ok(db::rows(conn, "SELECT * FROM math_mastery", [])?.into_iter()
        .filter_map(|row| Some((row.get("topic_id")?.as_str()?.to_owned(), row))).collect())
}

fn unlock_reason(topic: &Topic, rows: &HashMap<String, Map<String, Value>>) -> String {
    if topic.prereqs.is_empty() {
        return "A starting point: no prerequisites".into();
    }
    let stamp = |prereq: &String| rows.get(prereq).and_then(|row| row.get("mastered_at")).and_then(Value::as_str).unwrap_or("").to_owned();
    let latest = crate::math::templates::max_by(&topic.prereqs, stamp);
    format!("Unlocked because you mastered {}", topics::by_id(&latest).map_or("", |topic| topic.title.as_str()))
}

/// `states(conn, moment)`: every topic in curriculum order with its state, schedule and why.
pub fn states(conn: &Connection, moment: &Dt) -> Done<Vec<Map<String, Value>>> {
    let rows = rows(conn)?;
    let now = instant(moment);
    let mut out = Vec::new();
    for topic in topics::topics() {
        let row = rows.get(&topic.id);
        let missing: Vec<&str> = topic.prereqs.iter()
            .filter(|prereq| rows.get(*prereq).and_then(|row| text(row, "mastered_at")).is_none())
            .map(|prereq| topics::by_id(prereq).map_or("", |topic| topic.title.as_str())).collect();
        let mut item = Map::new();
        item.insert("id".into(), json!(topic.id));
        item.insert("strand".into(), json!(topic.strand));
        item.insert("title".into(), json!(topic.title));
        item.insert("prereqs".into(), json!(topic.prereqs));
        if let Some(row) = row.filter(|row| text(row, "mastered_at").is_some()) {
            let card = Card::from_json(row.get("fsrs").and_then(Value::as_str).unwrap_or("")).map_err(|_| internal())?;
            let due = card.due.utc().ok_or_else(internal)?;
            item.insert("state".into(), json!("mastered"));
            item.insert("due_at".into(), json!(pytime::iso_parsed(card.due)));
            item.insert("stability_days".into(), card.stability.map_or(json!(0), |stability| json!(pyjson::round(stability, 2))));
            item.insert("progress".into(), json!(1.0));
            let gap = seconds(due - now);
            let why = if gap <= 0.0 {
                let last = card.last_review.and_then(Parsed::utc).ok_or_else(internal)?;
                format!("Review due: practised {} ago", days(seconds(now - last)))
            } else {
                format!("Mastered; next review in {}", days(gap))
            };
            item.insert("why".into(), json!(why));
        } else if !missing.is_empty() {
            item.insert("state".into(), json!("locked"));
            item.insert("progress".into(), json!(0.0));
            item.insert("why".into(), json!(format!("Needs {}", join(&missing))));
        } else if let Some(row) = row.filter(|row| text(row, "updated_at").is_some()) {
            let run = row.get("run").and_then(Value::as_i64).unwrap_or(0);
            item.insert("state".into(), json!("learning"));
            item.insert("progress".into(), json!(pyjson::round(run as f64 / MASTERY_RUN as f64, 3)));
            item.insert("why".into(), json!(format!("{run} of {MASTERY_RUN} correct in a row toward mastery")));
        } else {
            item.insert("state".into(), json!("ready"));
            item.insert("progress".into(), json!(0.0));
            item.insert("why".into(), json!(unlock_reason(topic, &rows)));
        }
        out.push(item);
    }
    Ok(out)
}

/// `state_of(conn, topic_id, moment)`.
pub fn state_of(conn: &Connection, topic_id: &str, moment: &Dt) -> Done<Map<String, Value>> {
    states(conn, moment)?.into_iter().find(|item| item["id"] == topic_id).ok_or_else(internal)
}

/// `record(...)`: apply one graded answer inside the caller's transaction; the FSRS rating when
/// it was a review of a mastered topic.
pub fn record(conn: &Connection, topic_id: &str, correct: bool, elapsed_ms: Option<i64>, target_s: u32, moment: &Dt) -> Done<Option<u8>> {
    let stamp = moment.isoformat();
    conn.execute("INSERT OR IGNORE INTO math_mastery(topic_id, updated_at) VALUES (?, ?)", rusqlite::params![topic_id, stamp])?;
    let row = db::row(conn, "SELECT * FROM math_mastery WHERE topic_id=?", [topic_id])?.ok_or_else(internal)?;
    let mut applied = None;
    if text(&row, "mastered_at").is_some() {
        let grade = rating(correct, elapsed_ms, target_s);
        applied = Some(grade);
        let (card, due) = stored(&review(row.get("fsrs").and_then(Value::as_str), grade, moment)?);
        conn.execute("UPDATE math_mastery SET fsrs=?, due_at=?, reviews=reviews+1, lapses=lapses+?, updated_at=? WHERE topic_id=?",
                     rusqlite::params![card, due, i64::from(grade == AGAIN), stamp, topic_id])?;
    } else {
        let run = if correct { row.get("run").and_then(Value::as_i64).unwrap_or(0) + 1 } else { 0 };
        if run >= MASTERY_RUN {
            let (card, due) = stored(&review(None, GOOD, moment)?);
            conn.execute("UPDATE math_mastery SET run=?, mastered_at=?, fsrs=?, due_at=?, updated_at=? WHERE topic_id=?",
                         rusqlite::params![run, stamp, card, due, stamp, topic_id])?;
        } else {
            conn.execute("UPDATE math_mastery SET run=?, updated_at=? WHERE topic_id=?", rusqlite::params![run, stamp, topic_id])?;
        }
    }
    if correct {
        implicit_credit(conn, topic_id, moment)?;
    }
    Ok(applied)
}

/// `_implicit_credit`: a correct answer's credit to each mastered prerequisite, whole units of
/// it becoming implicit Good reviews.
fn implicit_credit(conn: &Connection, topic_id: &str, moment: &Dt) -> Done<()> {
    let stamp = moment.isoformat();
    let edges = db::rows(conn, "SELECT p.prereq_id, p.credit, m.due_at, m.fsrs, m.implicit_credit FROM math_prereqs p \
        JOIN math_mastery m ON m.topic_id = p.prereq_id WHERE p.topic_id=? AND p.credit > 0 AND m.mastered_at IS NOT NULL", [topic_id])?;
    for edge in edges {
        let float = |key: &str| edge.get(key).and_then(Value::as_f64).unwrap_or(0.0);
        let due_text = edge.get("due_at").and_then(Value::as_str).unwrap_or("");
        let due = fromisoformat(due_text).ok_or_else(internal)?.le(moment);
        let mut credit = float("implicit_credit") + float("credit") * if due { 1.0 } else { EARLY_DISCOUNT };
        let (mut card_json, mut due_at, mut reviews) = (edge.get("fsrs").and_then(Value::as_str).unwrap_or("").to_owned(), due_text.to_owned(), 0i64);
        while credit >= 1.0 - 1e-9 {
            (card_json, due_at) = stored(&review(Some(&card_json), GOOD, moment)?);
            credit = (credit - 1.0).max(0.0);
            reviews += 1;
        }
        conn.execute("UPDATE math_mastery SET implicit_credit=?, fsrs=?, due_at=?, implicit_reviews=implicit_reviews+?, updated_at=? WHERE topic_id=?",
                     rusqlite::params![pyjson::round(credit, 6), card_json, due_at, reviews, stamp, edge.get("prereq_id").and_then(Value::as_str)])?;
    }
    Ok(())
}

fn task(kind: &str, item: &Map<String, Value>, why: Option<String>) -> Value {
    json!({"kind": kind, "topic_id": item["id"], "title": item["title"], "why": why.map_or_else(|| item["why"].clone(), Value::String)})
}

fn due_at(item: &Map<String, Value>) -> &str {
    item.get("due_at").and_then(Value::as_str).unwrap_or("")
}

/// `next_task`: a due review, the topic being learned, or a new topic.
pub fn next_task(conn: &Connection, moment: &Dt) -> Done<Value> {
    let items = states(conn, moment)?;
    let mastered: Vec<&Map<String, Value>> = items.iter().filter(|item| item["state"] == "mastered").collect();
    let mut due = Vec::new();
    for item in &mastered {
        if fromisoformat(due_at(item)).ok_or_else(internal)?.le(moment) {
            due.push(*item);
        }
    }
    due.sort_by(|a, b| due_at(a).cmp(due_at(b)));
    if let Some(first) = due.first() {
        return Ok(task("review", first, None));
    }
    let last: Option<String> = conn.query_row("SELECT topic_id FROM math_attempts WHERE graded_at IS NOT NULL AND purpose != 'guided' \
        ORDER BY graded_at DESC LIMIT 1", [], |row| row.get(0)).map(Some).or_else(|error| match error {
        rusqlite::Error::QueryReturnedNoRows => Ok(None),
        error => Err(error),
    })?;
    let learning: Vec<&Map<String, Value>> = items.iter().filter(|item| item["state"] == "learning").collect();
    if let Some(first) = learning.first() {
        let recent = learning.iter().find(|item| last.as_deref().is_some_and(|last| item["id"] == last)).unwrap_or(first);
        let why = recent["why"].as_str().unwrap_or("");
        let mut chars = why.chars();
        let lowered = chars.next().map(|c| c.to_lowercase().collect::<String>() + chars.as_str()).unwrap_or_default();
        return Ok(task("lesson", recent, Some(format!("Continue: {lowered}"))));
    }
    let ready: Vec<&Map<String, Value>> = items.iter().filter(|item| item["state"] == "ready").collect();
    if let Some(first) = ready.first() {
        let last_strand = last.as_deref().and_then(topics::by_id).map(|topic| topic.strand.as_str());
        let choice = ready.iter().find(|item| Some(item["strand"].as_str().unwrap_or("")) != last_strand).unwrap_or(first);
        return Ok(task("lesson", choice, None));
    }
    // `min(...)` of nothing is Python's ValueError (400); every graph has a ready root.
    let Some(upcoming) = mastered.iter().copied().reduce(|best, item| if due_at(item) < due_at(best) { item } else { best }) else {
        return Err(crate::ported::fail(400, "min() iterable argument is empty"));
    };
    let title = upcoming["title"].as_str().unwrap_or("");
    Ok(task("review", upcoming, Some(format!("Nothing due; the next review is {title}, an early review"))))
}
