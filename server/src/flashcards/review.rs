//! The review queue (daily limits per deck and its parents, learning steps first), review
//! sessions (front, reveal, grade, undo), the overview, statistics and the browser.

use std::collections::{BTreeMap, BTreeSet, HashMap};

use serde_json::{json, Map, Value};

use super::scheduler::{self, Card};
use super::store::{self, now_iso, Config};
use super::{first, loads, py_str, render, require, rows, scalar, transaction, value_error, Error, R};
use super::kit::{clock, token_hex};
use super::{pyjson, pytime};
use rusqlite::types::Value as Sql;
use rusqlite::{params, Connection};

pub const GONE_NOTE: &str = "That note no longer exists";
pub const GONE_CARD: &str = "That card no longer exists";

type Row = Map<String, Value>;

fn text<'a>(row: &'a Row, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

fn int(row: &Row, key: &str) -> i64 {
    row.get(key).and_then(Value::as_i64).unwrap_or(0)
}

fn flag(row: &Row, key: &str) -> bool {
    super::truthy(row.get(key))
}

fn days(moment_days: i64) -> String {
    pytime::date_iso(moment_days)
}

fn utc_of(text: &str) -> R<i64> {
    pytime::parse(text).and_then(|parsed| parsed.utc())
        .ok_or_else(|| value_error(format!("Invalid isoformat string: '{text}'")))
}

/// `_rows`: every card with its note and deck, filtered by deck (with subdecks) and search.
pub fn card_rows(conn: &Connection, deck: Option<i64>, search: &str) -> R<Vec<Row>> {
    let mut found = rows(conn, "SELECT c.*,n.deck_id,n.type_id,n.fields,n.tags,n.archived AS note_archived,d.name AS deck_name,d.settings,d.archived AS deck_archived,n.revision AS note_revision FROM flash_cards c JOIN flash_notes n ON n.id=c.note_id JOIN flash_decks d ON d.id=n.deck_id ORDER BY c.due,c.id", &[])?;
    if let Some(deck) = deck {
        let chosen = require(conn, "flash_decks", &Value::from(deck))?;
        let prefix = format!("{}::", text(&chosen, "name"));
        found.retain(|row| int(row, "deck_id") == deck || text(row, "deck_name").starts_with(&prefix));
    }
    let lowered = search.to_lowercase();
    for term in lowered.split_whitespace() {
        if let Some(tag) = term.strip_prefix("tag:") {
            let mut kept = Vec::new();
            for row in found {
                let tags = loads(&row["tags"])?;
                if tags.as_array().is_some_and(|tags| tags.iter().any(|t| t.as_str().is_some_and(|t| t.to_lowercase() == tag))) {
                    kept.push(row);
                }
            }
            found = kept;
        } else if let Some(note) = term.strip_prefix("nid:") {
            found.retain(|row| int(row, "note_id").to_string() == note);
        } else if let Some(value) = term.strip_prefix("is:") {
            let now = now_iso();
            found.retain(|row| text(row, "state") == value
                || (value == "suspended" && flag(row, "suspended"))
                || (value == "leech" && flag(row, "leech"))
                || (value == "archived" && (flag(row, "note_archived") || flag(row, "deck_archived")))
                || (value == "due" && text(row, "state") != "new" && text(row, "due") <= now.as_str()));
        } else if let Some(name) = term.strip_prefix("deck:") {
            found.retain(|row| text(row, "deck_name").to_lowercase().contains(name));
        } else {
            found.retain(|row| format!("{}{}", text(row, "fields"), text(row, "tags")).to_lowercase().contains(term));
        }
    }
    Ok(found)
}

/// The decks by name (`{r["name"]: dict(r)}`).
fn decks_by_name(conn: &Connection) -> R<HashMap<String, Row>> {
    Ok(rows(conn, "SELECT * FROM flash_decks", &[])?.into_iter().map(|row| (text(&row, "name").to_owned(), row)).collect())
}

fn ancestors<'a>(decks: &'a HashMap<String, Row>, name: &str) -> R<Vec<&'a Row>> {
    let parts: Vec<&str> = name.split("::").collect();
    (1..=parts.len()).map(|index| {
        let key = parts[..index].join("::");
        decks.get(&key).ok_or_else(|| value_error(format!("'{key}'")))
    }).collect()
}

fn rank(state: &str) -> u8 {
    match state {
        "learning" | "relearning" => 0,
        "review" => 1,
        _ => 2,
    }
}

/// `_eligible`: the cards a regular review may show now, in queue order.
pub fn eligible(conn: &Connection, candidates: &[Row], moment: i64) -> R<Vec<Row>> {
    let now = pytime::iso(moment);
    let decks = decks_by_name(conn)?;
    let mut usage: HashMap<i64, [i64; 2]> = HashMap::new();
    let mut result = Vec::new();
    for row in candidates {
        let buried = row.get("buried_until").and_then(Value::as_str).filter(|until| !until.is_empty());
        if flag(row, "suspended") || flag(row, "note_archived") || buried.is_some_and(|until| until > now.as_str()) {
            continue;
        }
        let chain = ancestors(&decks, text(row, "deck_name"))?;
        if chain.iter().any(|deck| flag(deck, "archived")) {
            continue;
        }
        let state = text(row, "state");
        if state != "new" && text(row, "due") > now.as_str() {
            continue;
        }
        let mut blocked = false;
        for deck in &chain {
            let config = Config::of(deck)?;
            let id = int(deck, "id");
            if let std::collections::hash_map::Entry::Vacant(entry) = usage.entry(id) {
                let zone = config.zone()?;
                let today = pytime::local_day(moment, &zone);
                let midnight = pytime::iso(pytime::local_midnight(today, &zone));
                let tomorrow = pytime::iso(pytime::local_midnight(today + 1, &zone));
                let name = text(deck, "name");
                let counts = conn.query_row(
                    "SELECT COALESCE(SUM(r.was_new),0),COALESCE(SUM(r.was_review),0) FROM flash_reviews r JOIN flash_decks d ON d.id=r.deck_id WHERE (d.id=? OR substr(d.name,1,?)=?) AND r.reviewed_at>=? AND r.reviewed_at<? AND r.undone=0 AND r.mode='regular'",
                    params![id, name.chars().count() as i64 + 2, format!("{name}::"), midnight, tomorrow],
                    |found| Ok([found.get::<_, i64>(0)?, found.get::<_, i64>(1)?]))?;
                entry.insert(counts);
            }
            let used = usage[&id];
            if (state == "new" && used[0] >= config.int("new_limit")?) || (state == "review" && used[1] >= config.int("review_limit")?) {
                blocked = true;
                break;
            }
        }
        if blocked {
            continue;
        }
        result.push(row.clone());
        for deck in &chain {
            let counts = usage.get_mut(&int(deck, "id")).expect("counted above");
            match state {
                "new" => counts[0] += 1,
                "review" => counts[1] += 1,
                _ => {}
            }
        }
    }
    result.sort_by(|a, b| (rank(text(a, "state")), text(a, "due"), int(a, "id")).cmp(&(rank(text(b, "state")), text(b, "due"), int(b, "id"))));
    Ok(result)
}

/// `_streak`: consecutive reviewed days ending today, or yesterday while today is open.
fn streak(reviewed: &BTreeSet<String>, today: i64) -> i64 {
    let mut current = today;
    if !reviewed.contains(&days(current)) {
        current -= 1;
    }
    let mut count = 0;
    while reviewed.contains(&days(current)) {
        count += 1;
        current -= 1;
    }
    count
}

fn count(rows: &[Row], test: impl Fn(&Row) -> bool) -> i64 {
    rows.iter().filter(|row| test(row)).count() as i64
}

fn is_learning(row: &Row) -> bool {
    matches!(text(row, "state"), "learning" | "relearning")
}

/// `overview`.
pub fn overview(conn: &Connection) -> R<Value> {
    store::bootstrap(conn)?;
    let moment = clock::now();
    let mut decks = rows(conn, "SELECT * FROM flash_decks ORDER BY name", &[])?;
    let mut kinds = rows(conn, "SELECT * FROM flash_types ORDER BY id", &[])?;
    for deck in &mut decks {
        let settings = loads(&deck["settings"])?;
        deck.insert("settings".into(), settings);
    }
    for kind in &mut kinds {
        let (fields, templates) = (loads(&kind["fields"])?, loads(&kind["templates"])?);
        kind.insert("fields".into(), fields);
        kind.insert("templates".into(), templates);
    }
    let all = card_rows(conn, None, "")?;
    let queue = eligible(conn, &all, moment)?;
    let reviewed: BTreeSet<String> = conn.prepare("SELECT DISTINCT local_day FROM flash_reviews WHERE undone=0 AND mode='regular'")?
        .query_map([], |row| row.get(0))?.collect::<Result<_, _>>()?;
    let stats = json!({
        "total": all.len(),
        "new": count(&queue, |r| text(r, "state") == "new"),
        "learning": count(&queue, is_learning),
        "review": count(&queue, |r| text(r, "state") == "review"),
        "due": count(&queue, |r| text(r, "state") != "new"),
        "streak": streak(&reviewed, pytime::whole_days(moment)),
        "suspended": count(&all, |r| flag(r, "suspended")),
        "leeches": count(&all, |r| flag(r, "leech")),
    });
    let since = pytime::iso(moment - 90 * pytime::MICROS_PER_DAY);
    let names: HashMap<i64, String> = decks.iter().map(|deck| (int(deck, "id"), text(deck, "name").to_owned())).collect();
    let recalled: Vec<(String, i64, i64)> = conn.prepare(
        "SELECT deck_id,SUM(rating>1),COUNT(*) FROM flash_reviews WHERE undone=0 AND mode='regular' AND was_review=1 AND reviewed_at>=? GROUP BY deck_id")?
        .query_map(params![since], |row| Ok((row.get::<_, Option<i64>>(0)?, row.get::<_, i64>(1)?, row.get::<_, i64>(2)?)))?
        .map(|found| found.map(|(deck, passed, total)| (deck.and_then(|d| names.get(&d).cloned()).unwrap_or_default(), passed, total)))
        .collect::<Result<_, _>>()?;
    for deck in &mut decks {
        let name = text(deck, "name").to_owned();
        let prefix = format!("{name}::");
        let inside = |deck_name: &str| deck_name == name || deck_name.starts_with(&prefix);
        let members: Vec<Row> = queue.iter().filter(|row| inside(text(row, "deck_name"))).cloned().collect();
        let inner: Vec<(i64, i64)> = recalled.iter().filter(|(deck_name, _, _)| inside(deck_name)).map(|(_, passed, total)| (*passed, *total)).collect();
        let total: i64 = inner.iter().map(|(_, total)| total).sum();
        let passed: i64 = inner.iter().map(|(passed, _)| passed).sum();
        deck.insert("due".into(), json!(count(&members, |r| text(r, "state") != "new")));
        deck.insert("new".into(), json!(count(&members, |r| text(r, "state") == "new")));
        deck.insert("learning".into(), json!(count(&members, is_learning)));
        deck.insert("review".into(), json!(count(&members, |r| text(r, "state") == "review")));
        deck.insert("recall".into(), if total > 0 { pyjson::number(pyjson::round(passed as f64 / total as f64, 4)) } else { Value::Null });
    }
    let media = rows(conn, "SELECT name,mime,length(content) AS size FROM flash_media ORDER BY name", &[])?;
    Ok(json!({ "decks": decks, "types": kinds, "stats": stats, "media": media }))
}

fn quantile(values: &[f64], q: f64) -> f64 {
    let mut ordered = values.to_vec();
    ordered.sort_by(|a, b| a.total_cmp(b));
    let position = (ordered.len() - 1) as f64 * q;
    let low = position as usize;
    let high = (low + 1).min(ordered.len() - 1);
    ordered[low] + (ordered[high] - ordered[low]) * (position - low as f64)
}

const MEASURED_BUCKETS: [(f64, i64); 7] = [(1.0, 1), (3.0, 3), (7.0, 7), (14.0, 14), (30.0, 30), (60.0, 60), (36500.0, 120)];

/// `stats`: read-only memory statistics for a deck (with subdecks) or the collection.
pub fn stats(conn: &Connection, deck: Option<i64>, zone_name: &str) -> R<Value> {
    let zone = pytime::zone(zone_name).ok_or_else(|| value_error("Unknown IANA timezone"))?;
    let moment = clock::now();
    let today = pytime::local_day(moment, &zone);
    let scoped = card_rows(conn, deck, "")?;
    let decks: BTreeMap<i64, Row> = rows(conn, "SELECT id,name,archived,settings FROM flash_decks", &[])?
        .into_iter().map(|row| (int(&row, "id"), row)).collect();
    let scope: Option<Vec<i64>> = match deck {
        Some(id) => {
            let name = text(decks.get(&id).ok_or_else(|| value_error(format!("{id}")))?, "name").to_owned();
            Some(decks.iter().filter(|(_, d)| text(d, "name") == name || text(d, "name").starts_with(&format!("{name}::"))).map(|(id, _)| *id).collect())
        }
        None => None,
    };
    let archived: Vec<String> = decks.values().filter(|d| flag(d, "archived")).map(|d| text(d, "name").to_owned()).collect();
    let mut forecast: Vec<(String, i64, i64)> = (0..30).map(|offset| (days(today + offset), 0, 0)).collect();
    for row in &scoped {
        let deck_name = text(row, "deck_name");
        let active = !flag(row, "suspended") && !flag(row, "note_archived")
            && !archived.iter().any(|a| deck_name == a || deck_name.starts_with(&format!("{a}::")));
        if !active || text(row, "state") == "new" {
            continue;
        }
        let offset = (pytime::local_day(utc_of(text(row, "due"))?, &zone) - today).max(0);
        if offset < 30 {
            let slot = &mut forecast[offset as usize];
            if text(row, "state") == "review" { slot.1 += 1 } else { slot.2 += 1 }
        }
    }
    let mut maturity = [0i64; 4];
    let mut stabilities = Vec::new();
    for row in &scoped {
        match text(row, "state") {
            "new" => maturity[0] += 1,
            "review" => {
                let memory = match row.get("fsrs").and_then(Value::as_str).filter(|s| !s.is_empty()) {
                    Some(raw) => serde_json::from_str::<Value>(raw).map_err(|_| Error::Internal)?,
                    None => json!({}),
                };
                let interval = match memory.get("last_review").and_then(Value::as_str).filter(|s| !s.is_empty()) {
                    Some(last) => pytime::whole_days(utc_of(text(row, "due"))? - utc_of(last)?),
                    None => 0,
                };
                maturity[if interval >= 21 { 3 } else { 2 }] += 1;
                if let Some(stability) = memory.get("stability").and_then(Value::as_f64).filter(|s| *s != 0.0) {
                    stabilities.push(stability);
                }
            }
            _ => maturity[1] += 1,
        }
    }
    let (filter, mut scope_args) = match &scope {
        Some(ids) => {
            let mut sorted = ids.clone();
            sorted.sort();
            (format!(" AND deck_id IN ({})", vec!["?"; sorted.len()].join(",")), sorted.into_iter().map(Sql::Integer).collect::<Vec<_>>())
        }
        None => (String::new(), Vec::new()),
    };
    let mut counts: HashMap<String, i64> = HashMap::new();
    for row in rows(conn, &format!("SELECT reviewed_at FROM flash_reviews WHERE undone=0 AND mode='regular'{filter}"), &scope_args)? {
        *counts.entry(days(pytime::local_day(utc_of(text(&row, "reviewed_at"))?, &zone))).or_default() += 1;
    }
    let heatmap: Vec<Value> = (0..182).rev().map(|back| {
        let day = days(today - back);
        json!({ "day": day, "reviews": counts.get(&day).copied().unwrap_or(0) })
    }).collect();
    let reviewed: BTreeSet<String> = counts.keys().cloned().collect();
    let mut buckets = [[0i64; 2]; 7];
    let mut args = vec![Sql::Text(pytime::iso(moment - 365 * pytime::MICROS_PER_DAY))];
    args.append(&mut scope_args.clone());
    for row in rows(conn, &format!("SELECT reviewed_at,rating,before_state FROM flash_reviews WHERE undone=0 AND mode='regular' AND was_review=1 AND reviewed_at>=?{filter}"), &args)? {
        let before = loads(&row["before_state"])?;
        let memory_text = before.get("fsrs").filter(|v| super::truthy(Some(v))).and_then(Value::as_str).unwrap_or("{}");
        let memory: Value = serde_json::from_str(memory_text).map_err(|_| Error::Internal)?;
        let Some(last) = memory.get("last_review").and_then(Value::as_str).filter(|s| !s.is_empty()) else { continue };
        let elapsed = (utc_of(text(&row, "reviewed_at"))? - utc_of(last)?) as f64 / pytime::MICROS_PER_DAY as f64;
        let index = MEASURED_BUCKETS.iter().position(|(limit, _)| elapsed <= *limit).ok_or(Error::Internal)?;
        buckets[index][0] += i64::from(int(&row, "rating") > 1);
        buckets[index][1] += 1;
    }
    let measured: Vec<Value> = MEASURED_BUCKETS.iter().zip(buckets).filter(|(_, [_, total])| *total > 0)
        .map(|((_, shown), [passed, total])| json!({ "t": shown, "r": pyjson::number(pyjson::round(passed as f64 / total as f64, 4)), "n": total })).collect();
    let queue = eligible(conn, &scoped, moment)?;
    let mut args = vec![Sql::Text(pytime::iso(moment - 30 * pytime::MICROS_PER_DAY))];
    args.append(&mut scope_args);
    let durations: Option<f64> = conn.query_row(&format!("SELECT AVG(duration_ms) FROM flash_reviews WHERE undone=0 AND mode='regular' AND reviewed_at>=?{filter}"),
                                                rusqlite::params_from_iter(args.iter()), |row| row.get(0))?;
    let target = match deck {
        Some(id) => decks.get(&id).cloned(),
        None => decks.values().find(|d| text(d, "name") == "Default").cloned(),
    };
    let target = match target {
        Some(deck) => loads(&deck["settings"])?.get("retention").cloned().ok_or_else(|| value_error("'retention'"))?,
        None => json!(0.9),
    };
    let now = pytime::iso(moment);
    Ok(json!({
        "forecast": forecast.into_iter().map(|(day, review, learning)| json!({"day": day, "review": review, "learning": learning})).collect::<Vec<_>>(),
        "retention": {
            "decay": scheduler::FSRS_DEFAULT_DECAY,
            "target": target,
            "stability": if stabilities.is_empty() { Value::Null } else { json!({
                "weak": quantile(&stabilities, 0.25), "medium": quantile(&stabilities, 0.5), "strong": quantile(&stabilities, 0.75),
            }) },
            "measured": measured,
        },
        "maturity": {"new": maturity[0], "learning": maturity[1], "young": maturity[2], "mature": maturity[3]},
        "heatmap": heatmap,
        "streak": streak(&reviewed, today),
        "queue": {
            "learning": count(&queue, is_learning),
            "review": count(&queue, |r| text(r, "state") == "review"),
            "new": count(&queue, |r| text(r, "state") == "new"),
            "suspended": count(&scoped, |r| flag(r, "suspended")),
            "leeches": count(&scoped, |r| flag(r, "leech")),
            "buried": count(&scoped, |r| r.get("buried_until").and_then(Value::as_str).is_some_and(|u| !u.is_empty() && u > now.as_str())),
            "seconds_per_card": durations.map(|ms| pyjson::number(pyjson::round(ms / 1000.0, 3))),
        },
    }))
}

const CARD_KEYS: [&str; 10] = ["id", "ordinal", "state", "due", "suspended", "buried_until", "leech", "lapses", "reviews", "revision"];

fn parsed_note(conn: &Connection, id: i64) -> R<Row> {
    let mut note = require(conn, "flash_notes", &Value::from(id))?;
    let (fields, tags) = (loads(&note["fields"])?, loads(&note["tags"])?);
    note.insert("fields".into(), fields);
    note.insert("tags".into(), tags);
    Ok(note)
}

/// `browser`: notes with their cards, in due order.
pub fn browser(conn: &Connection, deck: Option<i64>, search: &str, limit: usize, offset: usize) -> R<Value> {
    let mut notes: Vec<Row> = Vec::new();
    let mut index: HashMap<i64, usize> = HashMap::new();
    for row in card_rows(conn, deck, search)? {
        let id = int(&row, "note_id");
        let at = match index.get(&id) {
            Some(at) => *at,
            None => {
                let mut note = parsed_note(conn, id)?;
                note.insert("cards".into(), json!([]));
                notes.push(note);
                index.insert(id, notes.len() - 1);
                notes.len() - 1
            }
        };
        let card: Map<String, Value> = CARD_KEYS.iter().map(|key| (key.to_string(), row[*key].clone())).collect();
        notes[at]["cards"].as_array_mut().expect("a list").push(Value::Object(card));
    }
    let total = notes.len();
    let page: Vec<Row> = notes.into_iter().skip(offset).take(limit).collect();
    Ok(json!({ "notes": page, "total": total }))
}

/// `GET /api/flashcards/note`: one note for the exact editor.
pub fn exact_note(conn: &Connection, id: i64) -> R<Value> {
    let Some(_) = first(conn, "SELECT id FROM flash_notes WHERE id=?", &[id.into()])? else { return Err(value_error(GONE_NOTE)) };
    let mut note = parsed_note(conn, id)?;
    let cards = rows(conn, "SELECT id,ordinal,state,due,suspended,buried_until,leech,lapses,reviews,revision FROM flash_cards WHERE note_id=? ORDER BY ordinal,id", &[id.into()])?;
    note.insert("cards".into(), json!(cards));
    Ok(json!({ "note": note }))
}

fn fsrs_card(card: &Row) -> R<Card> {
    match card.get("fsrs").and_then(Value::as_str).filter(|raw| !raw.is_empty()) {
        Some(raw) => Card::from_json(raw).map_err(Error::Value),
        None => Ok(Card::new(int(card, "id"), pytime::parse(text(card, "due")).ok_or_else(|| value_error("Invalid isoformat string"))?)),
    }
}

fn template_of(conn: &Connection, kind_id: &Value, ordinal: i64) -> R<Value> {
    let kind = require(conn, "flash_types", kind_id)?;
    let templates = loads(&kind["templates"])?;
    let index = if kind["kind"] == "cloze" { 0 } else { ordinal };
    usize::try_from(index).ok().and_then(|at| templates.get(at)).cloned().ok_or_else(|| value_error("list index out of range"))
}

/// `start_review`: the next card's front and a session token for it.
pub fn start_review(conn: &Connection, deck: Option<i64>, search: &str, mode: &Value, study: Option<String>) -> R<Value> {
    let mode = match mode.as_str() {
        Some(mode @ ("regular" | "cram" | "filtered")) => mode.to_owned(),
        _ => return Err(value_error("Unknown study mode")),
    };
    let mut candidates = card_rows(conn, deck, search)?;
    let moment = clock::now();
    conn.execute("DELETE FROM flash_studies WHERE created_at<?", params![pytime::iso(moment - 7 * pytime::MICROS_PER_DAY)])?;
    let mut study_id = None;
    if mode == "regular" {
        candidates = eligible(conn, &candidates, moment)?;
    } else {
        let archived: Vec<String> = conn.prepare("SELECT name FROM flash_decks WHERE archived=1")?
            .query_map([], |row| row.get(0))?.collect::<Result<_, _>>()?;
        candidates.retain(|r| !flag(r, "suspended") && !flag(r, "note_archived")
            && !archived.iter().any(|name| text(r, "deck_name") == name || text(r, "deck_name").starts_with(&format!("{name}::"))));
        let order: Vec<i64> = match &study {
            Some(id) => {
                let found = first(conn, "SELECT * FROM flash_studies WHERE id=?", &[id.clone().into()])?;
                let matches = found.as_ref().is_some_and(|s| text(s, "mode") == mode && s["deck_id"].as_i64() == deck && text(s, "search") == search);
                if !matches {
                    return Err(value_error("Practice session expired or filter changed; start a new session"));
                }
                study_id = Some(id.clone());
                loads(&found.expect("matched")["card_ids"])?.as_array().ok_or(Error::Internal)?.iter().filter_map(Value::as_i64).collect()
            }
            None => {
                let id = token_hex(24);
                let mut order: Vec<i64> = candidates.iter().map(|r| int(r, "id")).collect();
                shuffle(&mut order);
                conn.execute("INSERT INTO flash_studies(id,mode,deck_id,search,card_ids,created_at) VALUES(?,?,?,?,?,?)",
                             params![id, mode, deck, search, pyjson::compact(&json!(order)), pytime::iso(moment)])?;
                study_id = Some(id);
                order
            }
        };
        let completed: BTreeSet<i64> = conn.prepare("SELECT card_id FROM flash_sessions WHERE study_id=? AND consumed=1")?
            .query_map(params![study_id], |row| row.get(0))?.collect::<Result<_, _>>()?;
        let lookup: HashMap<i64, Row> = candidates.into_iter().map(|r| (int(&r, "id"), r)).collect();
        candidates = order.iter().filter(|id| !completed.contains(id)).filter_map(|id| lookup.get(id).cloned()).collect();
    }
    let Some(card) = candidates.first() else { return Ok(json!({ "card": null, "remaining": 0, "study_id": study_id })) };
    let token = token_hex(24);
    conn.execute("DELETE FROM flash_sessions WHERE study_id IS NULL AND created_at<?", params![pytime::iso(moment - pytime::MICROS_PER_DAY)])?;
    conn.execute("INSERT INTO flash_sessions(token,card_id,revision,mode,created_at,study_id) VALUES(?,?,?,?,?,?)",
                 params![token, int(card, "id"), int(card, "revision"), mode, pytime::iso(moment), study_id])?;
    let template = template_of(conn, &card["type_id"], int(card, "ordinal"))?;
    let typed = template.get("front").and_then(Value::as_str).unwrap_or("").contains("{{type:");
    Ok(json!({
        "card": {"id": int(card, "id"), "note_id": int(card, "note_id"), "token": token,
                 "front": render::render(conn, int(card, "note_id"), int(card, "ordinal"), false)?,
                 "state": text(card, "state"), "mode": mode, "typed": typed},
        "remaining": candidates.len(),
        "study_id": study_id,
    }))
}

/// `secrets.SystemRandom().shuffle`.
fn shuffle(items: &mut [i64]) {
    for index in (1..items.len()).rev() {
        let mut bytes = [0u8; 8];
        getrandom::fill(&mut bytes).expect("the system's random source");
        let pick = (u64::from_le_bytes(bytes) % (index as u64 + 1)) as usize;
        items.swap(index, pick);
    }
}

/// `_session`: an open session and its unchanged card.
fn session(conn: &Connection, token: &str) -> R<(Row, Row)> {
    let found = first(conn, "SELECT * FROM flash_sessions WHERE token=? AND consumed=0", &[token.to_owned().into()])?;
    let expiry = pytime::iso(clock::now() - pytime::MICROS_PER_DAY);
    let Some(session) = found.filter(|s| text(s, "created_at") >= expiry.as_str()) else { return Err(value_error("Review session expired")) };
    if !session["study_id"].is_null() && scalar(conn, "SELECT 1 FROM flash_sessions WHERE study_id=? AND card_id=? AND consumed=1",
                                                 &[super::sql(&session["study_id"])?, int(&session, "card_id").into()])?.is_some() {
        return Err(value_error("This card has already been graded in the practice session"));
    }
    let card = require(conn, "flash_cards", &session["card_id"])?;
    if card["revision"] != session["revision"] {
        return Err(value_error("Card changed; begin a new review"));
    }
    Ok((session, card))
}

fn deck_config(conn: &Connection, card: &Row) -> R<(Row, Config)> {
    let note = require(conn, "flash_notes", &card["note_id"])?;
    let config = Config::of(&require(conn, "flash_decks", &note["deck_id"])?)?;
    Ok((note, config))
}

/// `reveal`: the answer, and for a regular review each rating's next due time.
pub fn reveal(conn: &Connection, token: &str) -> R<Value> {
    let (session, card) = session(conn, token)?;
    let (_, config) = deck_config(conn, &card)?;
    let mut previews = Vec::new();
    if text(&session, "mode") == "regular" {
        let settings = config.scheduler()?;
        let current = fsrs_card(&card)?;
        let moment = clock::now();
        for rating in 1..=4u8 {
            let updated = scheduler::review(&settings, &current, rating, moment).map_err(Error::Value)?;
            let due = updated.due.utc().ok_or(Error::Internal)?;
            previews.push(json!({ "rating": rating, "due": pytime::iso_parsed(updated.due), "seconds": (due - moment) / pytime::MICROS_PER_SECOND }));
        }
    }
    conn.execute("UPDATE flash_sessions SET revealed=1 WHERE token=?", params![token])?;
    Ok(json!({ "back": render::render(conn, int(&card, "note_id"), int(&card, "ordinal"), true)?, "intervals": previews }))
}

/// `grade`: schedule the card (regular reviews only) and log the review.
pub fn grade(conn: &Connection, token: &str, rating: Option<&Value>, duration: &Value) -> R<Value> {
    let rating = rating.filter(|r| r.is_i64()).and_then(Value::as_i64).filter(|r| (1..=4).contains(r))
        .ok_or_else(|| value_error("Rating must be 1–4"))? as u8;
    let duration = Some(duration).filter(|d| d.is_i64()).and_then(Value::as_i64).filter(|d| (0..=3_600_000).contains(d))
        .ok_or_else(|| value_error("Invalid review duration"))?;
    let (review_id, card_id) = transaction(conn, || {
        let (session, card) = session(conn, token)?;
        if !flag(&session, "revealed") {
            return Err(value_error("Reveal the answer before grading"));
        }
        let (note, config) = deck_config(conn, &card)?;
        let moment = clock::now();
        let mut siblings = Vec::new();
        let mut after_revision = int(&card, "revision");
        let id = int(&card, "id");
        let mode = text(&session, "mode").to_owned();
        if mode == "regular" {
            let joined: Vec<Row> = card_rows(conn, None, "")?.into_iter().filter(|row| int(row, "id") == id).take(1).collect();
            if eligible(conn, &joined, moment)?.is_empty() {
                return Err(value_error("Card is no longer eligible (archived, buried, suspended, not due, or daily limit reached)"));
            }
            let updated = scheduler::review(&config.scheduler()?, &fsrs_card(&card)?, rating, moment).map_err(Error::Value)?;
            let threshold = config.int("leech_threshold")?;
            let lapsed = text(&card, "state") == "review" && rating == 1;
            let lapses = int(&card, "lapses") + i64::from(lapsed);
            let leech = i64::from(flag(&card, "leech") || lapses >= threshold);
            let leech_event = lapsed && lapses >= threshold && (lapses - threshold).rem_euclid((threshold / 2).max(1)) == 0;
            let suspended = if flag(&card, "suspended") { card["suspended"].clone() } else { json!(i64::from(leech_event)) };
            conn.execute("UPDATE flash_cards SET state=?,due=?,fsrs=?,lapses=?,leech=?,suspended=?,reviews=reviews+1,revision=revision+1 WHERE id=?",
                         rusqlite::params_from_iter([Sql::Text(updated.state.name().into()), Sql::Text(pytime::iso_parsed(updated.due)),
                                                     Sql::Text(updated.to_json()), lapses.into(), leech.into(), super::sql(&suspended)?, id.into()]))?;
            after_revision += 1;
            if config.flag("bury_siblings")? {
                let midnight = store::next_midnight(moment, &config.zone()?);
                for sibling in rows(conn, "SELECT * FROM flash_cards WHERE note_id=? AND id<>?", &[card["note_id"].as_i64().into(), id.into()])? {
                    let revision = int(&sibling, "revision");
                    siblings.push(json!({ "id": int(&sibling, "id"), "buried_until": sibling["buried_until"], "before_revision": revision, "revision": revision + 1 }));
                    conn.execute("UPDATE flash_cards SET buried_until=?,revision=revision+1 WHERE id=?", params![midnight, int(&sibling, "id")])?;
                }
            }
        }
        let local_day = days(pytime::local_day(moment, &config.zone()?));
        conn.execute("INSERT INTO flash_reviews(card_id,deck_id,rating,reviewed_at,local_day,duration_ms,mode,before_state,after_revision,sibling_state,was_new,was_review,session_token) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     rusqlite::params_from_iter([Sql::Integer(id), super::sql(&note["deck_id"])?, Sql::Integer(rating.into()), Sql::Text(pytime::iso(moment)),
                                                 Sql::Text(local_day), Sql::Integer(duration), Sql::Text(mode), Sql::Text(pyjson::compact(&Value::Object(card.clone()))),
                                                 Sql::Integer(after_revision), Sql::Text(pyjson::compact(&json!(siblings))),
                                                 Sql::Integer(i64::from(text(&card, "state") == "new")), Sql::Integer(i64::from(text(&card, "state") == "review")),
                                                 Sql::Text(token.to_owned())]))?;
        let review_id = conn.last_insert_rowid();
        conn.execute("UPDATE flash_sessions SET consumed=1 WHERE token=?", params![token])?;
        Ok((review_id, id))
    })?;
    let due = require(conn, "flash_cards", &Value::from(card_id))?["due"].clone();
    Ok(json!({ "review_id": review_id, "due": due }))
}

/// `undo`: restore the card (and its buried siblings) as the last review found them.
pub fn undo(conn: &Connection) -> R<Value> {
    transaction(conn, || {
        let Some(row) = first(conn, "SELECT * FROM flash_reviews WHERE undone=0 ORDER BY id DESC LIMIT 1", &[])? else {
            return Err(value_error("No review to undo"));
        };
        let review_id = int(&row, "id");
        if text(&row, "mode") == "regular" {
            let old = loads(&row["before_state"])?;
            let current = require(conn, "flash_cards", &row["card_id"])?;
            if !super::same_int(&current["revision"], row.get("after_revision")) {
                return Err(value_error("Card edited since review; cannot undo"));
            }
            let siblings = loads(&row["sibling_state"])?.as_array().cloned().unwrap_or_default();
            for sibling in &siblings {
                if require(conn, "flash_cards", &sibling["id"])?["revision"] != sibling["revision"] {
                    return Err(value_error("Sibling changed since review; cannot undo"));
                }
            }
            let columns = ["state", "due", "fsrs", "suspended", "buried_until", "lapses", "reviews", "leech"];
            let mut values = Vec::new();
            for column in columns {
                values.push(super::sql(old.get(column).ok_or_else(|| value_error(format!("'{column}'")))?)?);
            }
            values.push(super::sql(&old["id"])?);
            conn.execute(&format!("UPDATE flash_cards SET {},revision=revision+1 WHERE id=?", columns.map(|c| format!("{c}=?")).join(",")),
                         rusqlite::params_from_iter(values))?;
            let mut restored: HashMap<i64, (i64, i64)> = HashMap::new();
            restored.insert(old["id"].as_i64().unwrap_or(0), (old["revision"].as_i64().unwrap_or(0), int(&current, "revision") + 1));
            for sibling in &siblings {
                conn.execute("UPDATE flash_cards SET buried_until=?,revision=revision+1 WHERE id=?",
                             rusqlite::params_from_iter([super::sql(&sibling["buried_until"])?, super::sql(&sibling["id"])?]))?;
                let revision = sibling["revision"].as_i64().unwrap_or(0);
                restored.insert(sibling["id"].as_i64().unwrap_or(0), (sibling.get("before_revision").and_then(Value::as_i64).unwrap_or(revision - 1), revision + 1));
            }
            // Restoring an exact prior state must not make the preceding undo look like an
            // intervening edit: revision counters stay monotonic.
            for earlier in rows(conn, "SELECT id,card_id,after_revision,sibling_state FROM flash_reviews WHERE id<? AND undone=0 AND mode='regular'", &[review_id.into()])? {
                let mut expected = earlier["after_revision"].clone();
                if let Some(change) = earlier["card_id"].as_i64().and_then(|card| restored.get(&card)) {
                    if expected.as_i64() == Some(change.0) {
                        expected = json!(change.1);
                    }
                }
                let mut previous = loads(&earlier["sibling_state"])?.as_array().cloned().unwrap_or_default();
                for sibling in previous.iter_mut() {
                    let Some(object) = sibling.as_object_mut() else { continue };
                    let revision = object.get("revision").and_then(Value::as_i64).unwrap_or(0);
                    object.entry("before_revision").or_insert(json!(revision - 1));
                    if let Some(change) = object.get("id").and_then(Value::as_i64).and_then(|id| restored.get(&id)) {
                        if revision == change.0 {
                            object.insert("revision".into(), json!(change.1));
                        }
                    }
                }
                conn.execute("UPDATE flash_reviews SET after_revision=?,sibling_state=? WHERE id=?",
                             rusqlite::params_from_iter([super::sql(&expected)?, Sql::Text(pyjson::compact(&json!(previous))), earlier["id"].as_i64().into()]))?;
            }
        }
        conn.execute("UPDATE flash_reviews SET undone=1 WHERE id=?", params![review_id])?;
        conn.execute("UPDATE flash_sessions SET consumed=0,revealed=0,revision=(SELECT revision FROM flash_cards WHERE id=flash_sessions.card_id) WHERE token=?",
                     rusqlite::params_from_iter([super::sql(&row["session_token"])?]))?;
        Ok(json!({ "undone": review_id }))
    })
}

/// `history`: the latest 200 reviews, of one card or all.
pub fn history(conn: &Connection, card: Option<i64>) -> R<Value> {
    let reviews = match card {
        Some(card) => rows(conn, "SELECT id,card_id,rating,reviewed_at,local_day,duration_ms,mode,undone FROM flash_reviews WHERE card_id=? ORDER BY id DESC LIMIT 200", &[card.into()])?,
        None => rows(conn, "SELECT id,card_id,rating,reviewed_at,local_day,duration_ms,mode,undone FROM flash_reviews  ORDER BY id DESC LIMIT 200", &[])?,
    };
    Ok(json!({ "reviews": reviews }))
}

/// `card_detail`: both faces and the FSRS memory state of one card.
pub fn card_detail(conn: &Connection, id: i64) -> R<Value> {
    let Some(card) = first(conn, "SELECT c.*,d.name AS deck FROM flash_cards c JOIN flash_notes n ON n.id=c.note_id JOIN flash_decks d ON d.id=n.deck_id WHERE c.id=?", &[id.into()])? else {
        return Err(value_error(GONE_CARD));
    };
    let mut memory = Value::Null;
    if text(&card, "state") != "new" && card.get("fsrs").and_then(Value::as_str).is_some_and(|raw| !raw.is_empty()) {
        let current = fsrs_card(&card)?;
        if let Some(stability) = current.stability {
            memory = json!({ "stability": stability, "difficulty": current.difficulty,
                             "retrievability": scheduler::retrievability(&current, clock::now()) });
        }
    }
    let mut result: Map<String, Value> = ["id", "note_id", "ordinal", "deck", "state", "due", "suspended", "buried_until", "leech", "lapses", "reviews", "revision"]
        .iter().map(|key| (key.to_string(), card[*key].clone())).collect();
    result.insert("front".into(), json!(render::render(conn, int(&card, "note_id"), int(&card, "ordinal"), false)?));
    result.insert("back".into(), json!(render::render(conn, int(&card, "note_id"), int(&card, "ordinal"), true)?));
    result.insert("memory".into(), memory);
    Ok(json!({ "card": result }))
}

#[allow(dead_code)]
fn unused(_: &str) -> String {
    py_str(&Value::Null)
}
