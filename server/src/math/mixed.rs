//! `kitty.core.math.mixed`: one session of due work across the whole graph. Due topic
//! reviews, textbook cards and own-book problems, most overdue first (at most [`SESSION`]),
//! topped up with the soonest early topic reviews when fewer than [`TOP_UP`] are due, then
//! ordered so no two neighbours share a strategy.

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::library;
use super::mastery;
use super::topics;
use crate::ported::time::{fromisoformat, Dt};
use crate::ported::{internal, Done};

const MIN_MASTERED: usize = 5;
const SESSION: usize = 10;
const TOP_UP: usize = 6;

fn strategy(item: &Map<String, Value>) -> &str {
    item.get("strategy").and_then(Value::as_str).unwrap_or("")
}

fn due_at(item: &Map<String, Value>) -> &str {
    item.get("due_at").and_then(Value::as_str).unwrap_or("")
}

fn early(item: &Map<String, Value>) -> bool {
    item.get("early").and_then(Value::as_bool).unwrap_or(false)
}

/// `interleave(items)`: neighbours never share a strategy, and the items left out because no
/// such order fits them all. Each step takes the first item whose strategy differs from the
/// last one placed, unless a strategy holds more than half of what remains: then it goes now.
fn interleave(items: Vec<Map<String, Value>>) -> (Vec<Map<String, Value>>, Vec<Map<String, Value>>) {
    let (mut remaining, mut placed, mut previous) = (items, Vec::new(), None::<String>);
    while !remaining.is_empty() {
        // `Counter` keeps first-occurrence order.
        let mut counts: Vec<(String, usize)> = Vec::new();
        for item in &remaining {
            match counts.iter_mut().find(|(name, _)| name == strategy(item)) {
                Some((_, count)) => *count += 1,
                None => counts.push((strategy(item).to_owned(), 1)),
            }
        }
        let forced = counts.iter().find(|(name, count)| previous.as_deref() != Some(name.as_str()) && 2 * count > remaining.len()).map(|(name, _)| name.clone());
        let Some(index) = remaining.iter().position(|item| {
            previous.as_deref() != Some(strategy(item)) && forced.as_deref().is_none_or(|forced| forced == strategy(item))
        }) else { break };
        let choice = remaining.remove(index);
        previous = Some(strategy(&choice).to_owned());
        placed.push(choice);
    }
    (placed, remaining)
}

fn count(number: usize, noun: &str) -> String {
    format!("{number} {noun}{}", if number == 1 { "" } else { "s" })
}

/// `queue(conn)`.
pub fn queue(conn: &Connection, moment: &Dt) -> Done<Value> {
    let mastered: Vec<Map<String, Value>> = mastery::states(conn, moment)?.into_iter().filter(|state| state["state"] == "mastered").collect();
    let mut topics_due = Vec::new();
    for state in &mastered {
        let id = state["id"].as_str().unwrap_or("");
        let mut item = Map::new();
        item.insert("kind".into(), json!("topic"));
        item.insert("topic_id".into(), json!(id));
        item.insert("title".into(), state["title"].clone());
        item.insert("strategy".into(), json!(topics::by_id(id).map_or("", |topic| topic.strategy.as_str())));
        item.insert("due_at".into(), state["due_at"].clone());
        item.insert("early".into(), json!(fromisoformat(due_at(state)).ok_or_else(internal)?.compare(moment).is_gt()));
        item.insert("why".into(), state["why"].clone());
        topics_due.push(item);
    }
    let (early_items, mut due): (Vec<_>, Vec<_>) = topics_due.into_iter().partition(early);
    due.extend(library::due_textbook(conn, moment)?.into_iter().map(|(item, _)| item));
    due.extend(library::due_books(conn, moment)?);
    due.sort_by(|a, b| due_at(a).cmp(due_at(b)));
    let mut pool: Vec<Map<String, Value>> = due.into_iter().take(SESSION).collect();
    if pool.len() < TOP_UP {
        let mut soonest = early_items;
        soonest.sort_by(|a, b| due_at(a).cmp(due_at(b)));
        let room = TOP_UP - pool.len();
        pool.extend(soonest.into_iter().take(room));
    }
    let (items, left) = interleave(pool);
    let held_back = left.iter().filter(|item| !early(item)).count();
    let why = if mastered.len() < MIN_MASTERED {
        format!("Mixed review opens once {MIN_MASTERED} topics are mastered ({} so far)", mastered.len())
    } else {
        let kind_count = |kind: &str| items.iter().filter(|item| !early(item) && item["kind"] == kind).count();
        let mut parts: Vec<String> = [("topic", "due topic"), ("textbook", "textbook exercise"), ("book", "book problem")].iter()
            .filter(|(kind, _)| kind_count(kind) > 0).map(|(kind, noun)| count(kind_count(kind), noun)).collect();
        let early_count = items.iter().filter(|item| early(item)).count();
        if early_count > 0 {
            parts.push(count(early_count, "early review"));
        }
        if parts.is_empty() { "Nothing to review yet".to_owned() } else { format!("Mixed review: {}, no two in a row with the same strategy", parts.join(", ")) }
    };
    Ok(json!({"default": mastered.len() >= MIN_MASTERED, "mastered": mastered.len(), "needed": MIN_MASTERED, "why": why,
              "held_back": held_back, "items": items}))
}
