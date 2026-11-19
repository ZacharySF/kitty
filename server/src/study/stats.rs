//! `kitty.core.study.statistics`: study time by local day, subject and goal, the streaks and
//! one week or month of history. Sums are taken in Python's order (first appearance, as a
//! `dict` keeps it), so the floating-point totals are the same to the last bit; a sum with
//! no terms is the integer 0, as `sum()` gives.

use std::collections::{HashMap, HashSet};
use std::hash::Hash;

use rusqlite::Connection;
use serde_json::{json, Value};

use super::{bad, day, internal, local_date, public_session, seconds, settings, st, stored, timestamp, tz, zone};
use crate::ported::time::{Date, Dt, Zone};
use crate::ported::{db, Done};

/// A `dict`: values by key, in insertion order.
struct Ordered<K, V> {
    index: HashMap<K, usize>,
    items: Vec<(K, V)>,
}

impl<K: Hash + Eq + Clone, V> Ordered<K, V> {
    fn new() -> Self {
        Self { index: HashMap::new(), items: Vec::new() }
    }

    fn get(&self, key: &K) -> Option<&V> {
        self.index.get(key).map(|at| &self.items[*at].1)
    }

    fn entry(&mut self, key: K, default: impl FnOnce() -> V) -> &mut V {
        let at = match self.index.get(&key) {
            Some(at) => *at,
            None => {
                self.index.insert(key.clone(), self.items.len());
                self.items.push((key, default()));
                self.items.len() - 1
            }
        };
        &mut self.items[at].1
    }
}

/// `sum()` over floats: the integer 0 until a term arrives.
#[derive(Default)]
struct Sum(Option<f64>);

impl Sum {
    fn add(&mut self, term: f64) {
        self.0 = Some(self.0.unwrap_or(0.0) + term);
    }

    fn value(&self) -> Value {
        self.0.map_or(json!(0), |total| json!(total))
    }
}

fn ident(value: &Value) -> Option<i64> {
    value.as_i64()
}

/// One stretch of study cut at local midnights: (local day, seconds).
fn split_segment(mut start: i64, end: i64, zone: &Zone) -> Done<Vec<(String, f64)>> {
    let mut parts = Vec::new();
    while start < end {
        let today = local_date(start, zone)?;
        let tomorrow = Dt::midnight(today.add_days(1).ok_or_else(internal)?, Some(zone.clone())).utc();
        let boundary = end.min(tomorrow);
        if boundary <= start {
            return Err(internal());
        }
        parts.push((today.iso(), seconds(boundary - start)));
        start = boundary;
    }
    Ok(parts)
}

fn streak(values: &HashSet<Date>, current: Date, step: i64) -> i64 {
    let mut cursor = if values.contains(&current) { Some(current) } else { current.add_days(-step) };
    let mut count = 0;
    while let Some(at) = cursor.filter(|at| values.contains(at)) {
        count += 1;
        cursor = at.add_days(-step);
    }
    count
}

fn first_of_month(date: Date) -> Date {
    Date { day: 1, ..date }
}

/// (start, end, previous, next) of the week or month holding `anchor`.
fn history_range(today: Date, period: &str, anchor: Option<&str>) -> Done<(Date, Date, Date, Date)> {
    if !matches!(period, "week" | "month") {
        return Err(bad("History period must be week or month"));
    }
    let anchor = match anchor.filter(|anchor| !anchor.is_empty()) {
        Some(anchor) => day(&json!(anchor), "History date")?,
        None => today,
    };
    if !(2..=9998).contains(&anchor.year) {
        return Err(bad("History date must allow previous and next periods"));
    }
    let step = |date: Date, days: i64| date.add_days(days).ok_or_else(internal);
    if period == "week" {
        let start = step(anchor, -i64::from(anchor.weekday()))?;
        let end = step(start, 7)?;
        return Ok((start, end, step(start, -7)?, end));
    }
    let start = first_of_month(anchor);
    let end = first_of_month(step(Date { day: 28, ..start }, 4)?);
    Ok((start, end, first_of_month(step(start, -1)?), end))
}

/// The statistics and, apart, the history period's sessions (newest first).
pub fn statistics(conn: &Connection, now: i64, timezone: Option<&str>, period: &str, anchor: Option<&str>) -> Done<(Value, Value)> {
    let timezone = match timezone.filter(|name| !name.is_empty()) {
        Some(name) => zone(&json!(name))?,
        None => zone(&settings(conn)?["timezone"])?,
    };
    let zone = tz(&timezone)?;
    let today = local_date(now, &zone)?;
    let monday = today.add_days(-i64::from(today.weekday())).ok_or_else(internal)?;
    let (start, end, previous, following) = history_range(today, period, anchor)?;
    let (start_key, end_key) = (start.iso(), end.iso());

    let mut history_subjects: Ordered<Option<i64>, f64> = Ordered::new();
    let mut history_sessions: Vec<Value> = Vec::new();
    let mut daily: Ordered<String, f64> = Ordered::new();
    let mut subjects: Ordered<Option<i64>, i64> = Ordered::new();
    let mut scopes: Ordered<(Option<i64>, Option<i64>, String), f64> = Ordered::new();
    let mut daily_subjects: Ordered<String, Ordered<Option<i64>, f64>> = Ordered::new();
    let (mut completed_days, mut completed_weeks) = (HashSet::new(), HashSet::new());
    let mut total = 0i64;
    let sessions = db::rows(conn, "SELECT * FROM study_sessions ORDER BY ended_at,id", [])?;
    for row in &sessions {
        let mut period_seconds = Sum::default();
        let completed = local_date(stored(row, "ended_at")?, &zone)?;
        completed_days.insert(completed);
        completed_weeks.insert(completed.add_days(-i64::from(completed.weekday())).ok_or_else(internal)?);
        let duration = row["duration_seconds"].as_i64().ok_or_else(internal)?;
        let (subject, project) = (ident(&row["subject_id"]), ident(&row["project_id"]));
        total += duration;
        *subjects.entry(subject, || 0) += duration;
        let segments: Vec<[Value; 2]> = serde_json::from_str(st(row, "segments_json")).map_err(|_| internal())?;
        for [raw_start, raw_end] in &segments {
            for (local_day, length) in split_segment(timestamp(raw_start, "Timestamp")?, timestamp(raw_end, "Timestamp")?, &zone)? {
                *daily.entry(local_day.clone(), || 0.0) += length;
                *daily_subjects.entry(local_day.clone(), Ordered::new).entry(subject, || 0.0) += length;
                *scopes.entry((subject, project, local_day.clone()), || 0.0) += length;
                if start_key <= local_day && local_day < end_key {
                    period_seconds.add(length);
                }
            }
        }
        if let Some(length) = period_seconds.0.filter(|length| *length != 0.0) {
            let mut session = public_session(row);
            session.insert("period_seconds".into(), json!(length));
            history_sessions.push(Value::Object(session));
            *history_subjects.entry(subject, || 0.0) += length;
        }
    }

    let today_key = today.iso();
    let span = |from: Date| {
        let from = from.iso();
        let mut sum = Sum::default();
        for (key, length) in &daily.items {
            if from <= *key && *key <= today_key {
                sum.add(*length);
            }
        }
        sum.value()
    };
    let mut goals = Vec::new();
    for target in db::rows(conn, "SELECT * FROM study_targets ORDER BY id", [])? {
        let from = if st(&target, "period") == "daily" { today } else { monday }.iso();
        let (subject, project) = (ident(&target["subject_id"]), ident(&target["project_id"]));
        let mut sum = Sum::default();
        for ((scope_subject, scope_project, local_day), length) in &scopes.items {
            if from <= *local_day && *local_day <= today_key && (subject.is_none() || *scope_subject == subject) && (project.is_none() || *scope_project == project) {
                sum.add(*length);
            }
        }
        goals.push(json!({"id": target["id"], "seconds": sum.value(), "goal_seconds": target["minutes"].as_i64().ok_or_else(internal)? * 60}));
    }
    let mut names: HashMap<i64, (Value, Value)> = HashMap::new();
    for subject in db::rows(conn, "SELECT id,name,color FROM study_subjects ORDER BY name,id", [])? {
        names.insert(subject["id"].as_i64().ok_or_else(internal)?, (subject["name"].clone(), subject["color"].clone()));
    }
    let named = |key: &Option<i64>, length: Value| {
        let known = key.and_then(|key| names.get(&key));
        json!({"id": key, "name": known.map_or(json!("Unassigned"), |known| known.0.clone()), "color": known.map_or(json!("#7c6fdb"), |known| known.1.clone()), "seconds": length})
    };
    // Each day's study split by subject, largest first, so views can tint a day by its
    // main subject or stack it in subject colours.
    let day_entry = |key: &str| {
        let mut split: Vec<(Option<i64>, f64)> = daily_subjects.get(&key.to_owned()).map(|by_subject| by_subject.items.clone()).unwrap_or_default();
        split.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.is_none().cmp(&b.0.is_none())).then(a.0.unwrap_or(0).cmp(&b.0.unwrap_or(0))));
        let length = daily.get(&key.to_owned()).copied();
        (json!({"date": key, "seconds": length.map_or(json!(0), |length| json!(length)),
                "subjects": split.iter().map(|(subject, length)| json!({"id": subject, "seconds": length})).collect::<Vec<_>>()}), length)
    };

    let mut history_days = Vec::new();
    let mut history_total = Sum::default();
    for offset in 0..(end.days() - start.days()) {
        let (entry, length) = day_entry(&start.add_days(offset).ok_or_else(internal)?.iso());
        if let Some(length) = length {
            history_total.add(length);
        }
        history_days.push(entry);
    }
    let history = json!({
        "period": period, "start": start_key, "end": end_key, "previous": previous.iso(), "next": following.iso(),
        "seconds": history_total.value(), "days": history_days, "session_count": history_sessions.len(),
        "subjects": history_subjects.items.iter().map(|(key, length)| named(key, json!(length))).collect::<Vec<_>>(),
    });
    let mut keys: Vec<&String> = daily.items.iter().map(|(key, _)| key).collect();
    keys.sort();
    let stats = json!({
        "timezone": timezone, "today": today_key, "week_start": monday.iso(),
        "today_seconds": daily.get(&today_key).map_or(json!(0), |length| json!(length)),
        "week_seconds": span(monday), "month_seconds": span(first_of_month(today)), "total_seconds": total,
        "daily_streak": streak(&completed_days, today, 1), "weekly_streak": streak(&completed_weeks, monday, 7),
        "session_count": sessions.len(), "history": history,
        "days": keys.into_iter().map(|key| day_entry(key).0).collect::<Vec<_>>(),
        "subjects": subjects.items.iter().map(|(key, length)| named(key, json!(length))).collect::<Vec<_>>(),
        "goals": goals,
    });
    history_sessions.reverse();
    Ok((stats, json!(history_sessions)))
}
