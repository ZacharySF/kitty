//! `jobs.JobFilter` and the queries it builds (`_where`, `_ORDER_SQL`, `_best_order`,
//! `list_jobs`, `count_jobs`), with `WebApplication.job_filter`'s views.

use rusqlite::types::Value as Sql;
use rusqlite::Connection;
use serde_json::Value;

use super::config::{JobConfig, ViewFilters};
use super::dates;
use super::model::{self, Job};
use crate::ported::time::DAY;
use crate::ported::{fail, internal, py, Done};

/// `jobs.NOT_FILTERED_OUT`.
pub const NOT_FILTERED_OUT: [&str; 4] = ["boosted", "eligible", "ambiguous", "unknown"];

fn owned(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| (*value).to_owned()).collect()
}

/// `JobFilter`; None (or an empty list) is not filtered on.
#[derive(Debug, Clone, Default)]
pub struct Filter {
    pub tracks: Option<Vec<String>>,
    pub tiers: Option<Vec<String>>,
    pub statuses: Option<Vec<String>>,
    pub queue_states: Option<Vec<String>>,
    pub sources: Option<Vec<String>>,
    pub eligibility: Option<Vec<String>>,
    pub school_selective: Option<bool>,
    /// A day count; a saved view's may be any integer Python holds.
    pub posted_within_days: Option<Value>,
    pub has_deadline: Option<bool>,
    pub next_action_due: Option<bool>,
    pub not_applied: Option<bool>,
    pub applied_only: Option<bool>,
    pub include_closed: bool,
    pub favorites_only: Option<bool>,
    pub text: Option<String>,
    pub limit: Option<i64>,
    pub order: String,
    pub prefer_cities: Vec<String>,
    pub prefer_remote: bool,
    pub company_text: Option<String>,
}

/// `WebApplication.job_filter(view)`.
pub fn for_view(view: &str, config: &JobConfig) -> Done<Filter> {
    if !matches!(view, "jobs" | "inbox" | "queue" | "applications" | "favorites") {
        return Err(fail(400, "Unknown job view"));
    }
    Ok(Filter {
        queue_states: match view {
            "queue" => Some(owned(&["queued"])),
            "inbox" => Some(owned(&["new"])),
            _ => None,
        },
        not_applied: Some(matches!(view, "queue" | "inbox")),
        applied_only: Some(view == "applications"),
        favorites_only: Some(view == "favorites"),
        include_closed: matches!(view, "applications" | "favorites"),
        order: "best".into(),
        prefer_cities: config.prefer_cities(),
        prefer_remote: config.open_to_remote,
        ..Filter::default()
    })
}

fn set(list: &Option<Vec<String>>) -> bool {
    list.as_ref().is_some_and(|values| !values.is_empty())
}

/// A list field of a saved view over the page's: kept as is when the page set none, else
/// narrowed to both (`['__no_match__']` when they share nothing).
fn narrow(field: &mut Option<Vec<String>>, value: &Option<Vec<String>>) {
    let Some(value) = value else { return };
    if set(field) {
        let existing = field.as_ref().expect("checked above");
        let both: Vec<String> = value.iter().filter(|item| existing.contains(item)).cloned().collect();
        *field = Some(if both.is_empty() { owned(&["__no_match__"]) } else { both });
    } else {
        *field = Some(value.clone());
    }
}

impl Filter {
    /// `web_jobs.get`'s merge of `JobFilter.from_view_filters(configured.filters)` into the
    /// page's filter.
    pub fn apply_view(&mut self, filters: &ViewFilters) {
        narrow(&mut self.tracks, &filters.track);
        narrow(&mut self.tiers, &filters.tier);
        narrow(&mut self.statuses, &filters.status);
        narrow(&mut self.queue_states, &filters.queue_state);
        narrow(&mut self.sources, &filters.source);
        narrow(&mut self.eligibility, &filters.eligibility);
        if filters.school_selective.is_some() {
            self.school_selective = filters.school_selective;
        }
        if filters.posted_within_days.is_some() {
            self.posted_within_days = filters.posted_within_days.clone();
        }
        if filters.has_deadline.is_some() {
            self.has_deadline = filters.has_deadline;
        }
        if filters.next_action_due.is_some() {
            self.next_action_due = filters.next_action_due;
        }
        if let Some(value) = filters.not_applied {
            self.not_applied = Some(self.not_applied == Some(true) || value);
        }
        // `include_closed=bool(filters.include_closed)` is always a value.
        self.include_closed = self.include_closed || filters.include_closed == Some(true);
        if filters.text.is_some() {
            self.text = filters.text.clone();
        }
    }

    /// The query's own value for one list field: alone, or narrowed to what is already chosen.
    pub fn choose(field: &mut Option<Vec<String>>, value: &str) {
        let keep = !set(field) || field.as_ref().is_some_and(|values| values.iter().any(|known| known == value));
        *field = Some(vec![if keep { value.to_owned() } else { "__no_match__".to_owned() }]);
    }

    /// `_where`: the clause and its parameters.
    fn where_(&self) -> Done<(String, Vec<Sql>)> {
        let mut clauses: Vec<String> = Vec::new();
        let mut params: Vec<Sql> = Vec::new();
        let within = |column: &str, values: &[String], params: &mut Vec<Sql>| {
            params.extend(values.iter().map(|value| Sql::Text(value.clone())));
            format!("{column} IN ({})", vec!["?"; values.len()].join(","))
        };
        if !self.include_closed {
            clauses.push("j.is_active = 1".into());
        }
        if let Some(tracks) = self.tracks.as_ref().filter(|values| !values.is_empty()) {
            clauses.push(format!("EXISTS (SELECT 1 FROM job_tracks jt WHERE jt.job_id = j.id AND jt.track IN ({}))", vec!["?"; tracks.len()].join(",")));
            params.extend(tracks.iter().map(|value| Sql::Text(value.clone())));
        }
        if let Some(sources) = self.sources.as_ref().filter(|values| !values.is_empty()) {
            clauses.push(format!("EXISTS (SELECT 1 FROM job_sources js WHERE js.job_id = j.id AND js.source IN ({}))", vec!["?"; sources.len()].join(",")));
            params.extend(sources.iter().map(|value| Sql::Text(value.clone())));
        }
        for (column, values) in [("j.tier", &self.tiers), ("j.queue_state", &self.queue_states), ("j.eligibility", &self.eligibility),
                                 ("a.status", &self.statuses)] {
            if let Some(values) = values.as_ref().filter(|values| !values.is_empty()) {
                let clause = within(column, values, &mut params);
                clauses.push(clause);
            }
        }
        if let Some(flag) = self.school_selective {
            clauses.push("j.school_selective = ?".into());
            params.push(Sql::Integer(i64::from(flag)));
        }
        if let Some(days) = &self.posted_within_days {
            let span = days.as_i64().and_then(|days| days.checked_mul(DAY)).ok_or_else(internal)?;
            let cutoff = dates::to_iso(&dates::now().add(span.checked_neg().ok_or_else(internal)?)?)?;
            clauses.push("COALESCE(j.posted_at, j.first_seen_at) >= ?".into());
            params.push(Sql::Text(cutoff));
        }
        if let Some(flag) = self.has_deadline {
            clauses.push(if flag { "COALESCE(j.deadline_at, j.source_deadline_at) IS NOT NULL" } else { "COALESCE(j.deadline_at, j.source_deadline_at) IS NULL" }.into());
        }
        if self.next_action_due == Some(true) {
            clauses.push("a.next_action_at IS NOT NULL AND a.next_action_at <= ?".into());
            params.push(Sql::Text(dates::now_iso()?));
        }
        if self.not_applied == Some(true) {
            clauses.push("a.id IS NULL".into());
        }
        if self.applied_only == Some(true) {
            clauses.push("a.id IS NOT NULL".into());
        }
        if self.favorites_only == Some(true) {
            clauses.push("j.favorited_at IS NOT NULL".into());
        }
        if let Some(company) = self.company_text.as_deref().map(py::strip).filter(|text| !text.is_empty()) {
            clauses.push("instr(lower(c.display_name), lower(?)) > 0".into());
            params.push(Sql::Text(company.to_owned()));
        }
        if let Some(text) = self.text.as_deref().filter(|text| !text.is_empty()) {
            let like = format!("%{}%", py::strip(text));
            clauses.push("(j.title LIKE ? OR c.display_name LIKE ? OR j.location LIKE ?)".into());
            params.extend([Sql::Text(like.clone()), Sql::Text(like.clone()), Sql::Text(like)]);
        }
        let clause = if clauses.is_empty() { String::new() } else { format!(" WHERE {}", clauses.join(" AND ")) };
        Ok((clause, params))
    }

    /// `jobs.count_jobs`.
    pub fn count(&self, conn: &Connection) -> Done<i64> {
        let (clause, params) = self.where_()?;
        let sql = format!("SELECT COUNT(*) AS n FROM jobs j JOIN companies c ON c.id = j.company_id LEFT JOIN applications a ON a.job_id = j.id{clause}");
        Ok(conn.query_row(&sql, rusqlite::params_from_iter(params), |row| row.get(0))?)
    }

    /// `jobs.list_jobs`.
    pub fn list(&self, conn: &Connection) -> Done<Vec<Job>> {
        let (clause, mut params) = self.where_()?;
        let order = if self.order == "best" { best_order(&self.prefer_cities, self.prefer_remote, &mut params) } else { order_sql(&self.order).to_owned() };
        let mut tail = format!("{clause}{order}");
        if let Some(limit) = self.limit.filter(|limit| *limit != 0) {
            tail.push_str(" LIMIT ?");
            params.push(Sql::Integer(limit));
        }
        model::select(conn, &tail, &params)
    }
}

/// `_ORDER_SQL.get(order, _ORDER_SQL["newest"])`.
fn order_sql(order: &str) -> &'static str {
    match order {
        "company" => " ORDER BY c.display_name COLLATE NOCASE, j.title COLLATE NOCASE, j.id DESC",
        "deadline" => " ORDER BY COALESCE(j.deadline_at, j.source_deadline_at) IS NULL, COALESCE(j.deadline_at, j.source_deadline_at), j.id DESC",
        "score" => " ORDER BY j.like_score IS NULL, j.like_score DESC, COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC",
        "track" => " ORDER BY NOT EXISTS (SELECT 1 FROM job_tracks jt WHERE jt.job_id=j.id), \
                    (SELECT MIN(jt.track) FROM job_tracks jt WHERE jt.job_id=j.id) COLLATE NOCASE, \
                    c.display_name COLLATE NOCASE, j.title COLLATE NOCASE, j.id DESC",
        _ => " ORDER BY COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC",
    }
}

/// `_best_order`: who I can apply to, then fit, then place, then age.
fn best_order(prefer_cities: &[String], prefer_remote: bool, params: &mut Vec<Sql>) -> String {
    let mut places: Vec<&str> = prefer_cities.iter().map(|_| "j.location LIKE ?").collect();
    params.extend(prefer_cities.iter().map(|name| Sql::Text(format!("%{name}%"))));
    if prefer_remote {
        places.extend(["j.is_remote = 1", "j.location LIKE '%remote%'", "j.location LIKE '%virtual%'"]);
    }
    let place = if places.is_empty() { String::new() } else { format!("CASE WHEN {} THEN 0 ELSE 1 END, ", places.join(" OR ")) };
    format!(" ORDER BY CASE j.eligibility WHEN 'boosted' THEN 0 WHEN 'eligible' THEN 1 WHEN 'ambiguous' THEN 2 WHEN 'unknown' THEN 3 ELSE 4 END, \
             CASE j.tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 3 ELSE 2 END, \
             c.priority DESC, CAST((COALESCE(j.like_score, 0) + 1) / 0.05 AS INTEGER) DESC, {place}COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC")
}
