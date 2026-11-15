//! The opportunities routes (`kitty.web_opportunities` and `kitty.core.opportunities` in
//! Python): the ranked list with its sources and schedule, one opportunity with its
//! evidence and project, manual entries and imports, edits, favorites, deletion, the
//! project and its items, and the deadline's calendar event. Same values, stored rows,
//! order of checks and refusals as Python. `POST /api/opportunities/settings` (it edits the
//! build file and starts or stops the scheduler), `/discover` (a background task that
//! fetches the public sources) and `/sources/<id>` stay with the Python worker, as does
//! `kitty.core.opportunities`: discovery and the settings route call it, and it is the
//! reference of tests/test_opportunities_parity.py.

mod url;

use std::collections::BTreeSet;
use std::sync::LazyLock;

use regex::Regex;
use rusqlite::types::Value as Sql;
use rusqlite::{params, params_from_iter, Connection};
use serde_json::{json, Map, Value};

use crate::calendar::core::{create_event, uuid};
use crate::ported::time::{self, Date, Dt, Zone, HOUR, MINUTE, SECOND};
use crate::ported::{db, fail, internal, py, Call, Ctx, Done, Group};
use crate::search::text::casefold;

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

type Obj = Map<String, Value>;

const ROOT: &str = "/api/opportunities";
const IMPORT: &str = "/api/opportunities/import";
const FAVORITES: &str = "/api/opportunities/favorites";
const STATUSES: [&str; 8] = ["discovered", "new", "saved", "queued", "applied", "participating", "completed", "skipped"];
/// What a source reports about an opportunity; a field the person edits stops following it.
const FACT_FIELDS: [&str; 11] = ["title", "url", "category", "description", "location", "remote", "start", "end", "deadline", "timezone", "availability"];
const AVAILABILITY: [&str; 4] = ["unknown", "future", "active", "closed"];
const FILTERS: [&str; 9] = ["search", "source", "category", "status", "location", "remote", "deadline", "favorites", "availability"];
/// `opportunity_sources.DEFAULTS`: id, name, url, kind, category, timezone.
const SOURCES: [[&str; 6]; 8] = [
    ["tamu-engineering", "TAMU Engineering calendar", "https://calendar.tamu.edu/live/ical/events/group/College%20of%20Engineering", "ical", "engineering", "America/Chicago"],
    ["ieee-tamu", "IEEE TAMU public events", "https://ieeetamu.org/api/events", "ieee", "engineering", "America/Chicago"],
    ["mlh", "Major League Hacking official season", "https://mlh.com/events", "mlh", "hackathon", "UTC"],
    ["devpost", "Devpost public hackathons", "https://devpost.com/hackathons", "devpost", "hackathon", "UTC"],
    ["imc-prosperity", "IMC Prosperity", "https://prosperity.imc.com/", "page", "quant", "UTC"],
    ["jane-street", "Jane Street programs and events", "https://www.janestreet.com/join-jane-street/programs-and-events/", "jane", "quant", "America/New_York"],
    ["rotman", "Rotman International Trading Competition", "https://rotman.utoronto.ca/faculty-and-research/education-labs/bmo-financial-group-finance-research-and-trading-lab/rotman-international-trading-competition/overview/", "page", "quant", "America/Toronto"],
    ["citadel", "Citadel Datathons", "https://www.citadel.com/careers/programs-and-events/datathons/", "page", "data science", "America/New_York"],
];

static ONE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^/api/opportunities/([1-9][0-9]*)$").expect("valid"));
static CHANGE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^/api/opportunities/([1-9][0-9]*)(?:/(delete|project|items|project-details|calendar))?$").expect("valid"));
static DASHED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$").expect("valid"));

/// The routes that moved; settings, discovery and the sources are the worker's.
fn owns(path: &str, mutation: bool) -> bool {
    if mutation { matches!(path, ROOT | IMPORT | FAVORITES) || CHANGE.is_match(path) } else { path == ROOT || ONE.is_match(path) }
}

fn bad(message: impl Into<String>) -> crate::ported::Fault {
    fail(400, message)
}

// Values -----------------------------------------------------------------------

fn st<'a>(row: &'a Obj, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

fn truthy(row: &Obj, key: &str) -> bool {
    row.get(key).is_some_and(py::truthy)
}

/// Whether the body names only `allowed`.
fn only(body: &Obj, allowed: &[&str]) -> bool {
    body.keys().all(|key| allowed.contains(&key.as_str()))
}

/// `set(body) == {...}`.
fn exactly(body: &Obj, names: &[&str]) -> bool {
    body.len() == names.len() && names.iter().all(|name| body.contains_key(*name))
}

/// `type(value) is int`, of any size.
fn is_int(value: &Value) -> bool {
    value.as_number().is_some_and(|number| number.is_i64() || number.is_u64())
}

/// An id SQLite can hold; Python's `OverflowError` for a larger one is a 500.
fn stored_id(value: &Value) -> Done<i64> {
    value.as_i64().ok_or_else(internal)
}

/// `kitty.dates.now_iso`: UTC to the second, with a `Z`.
fn now_iso() -> String {
    let at = Dt::now_utc();
    let of_day = at.time_of_day();
    format!("{}T{:02}:{:02}:{:02}Z", at.date().iso(), of_day / HOUR, of_day / MINUTE % 60, of_day / SECOND % 60)
}

/// `text(value, name, maximum)`: the stripped text.
fn text(value: &Value, name: &str, maximum: usize) -> Done<String> {
    match value.as_str() {
        Some(text) if py::len(text) <= maximum && !text.contains('\0') => Ok(py::strip(text).to_owned()),
        _ => Err(bad(format!("{name} must be text of at most {maximum} characters"))),
    }
}

/// `canonical_url`: `https`, the host in lower case, no trailing slash, no tracking fields, the query sorted.
fn canonical_url(value: &Value) -> Done<String> {
    let value = text(value, "URL", 4000)?;
    if value.is_empty() {
        return Ok(value);
    }
    url::canonical(&value).map_err(bad)
}

/// `valid_date`: nothing, a date, or a timestamp with an offset in `isoformat()` text.
fn valid_date(value: &Value) -> Done<Value> {
    if value.is_null() || value.as_str() == Some("") {
        return Ok(Value::Null);
    }
    let value = text(value, "Date", 100)?;
    let invalid = || bad("Use YYYY-MM-DD or an aware ISO8601 timestamp");
    if DASHED.is_match(&value) {
        return Date::parse_dashed(&value).map(|date| Value::String(date.iso())).ok_or_else(invalid);
    }
    let parsed = time::fromisoformat(&value.replace('Z', "+00:00")).ok_or_else(invalid)?;
    if parsed.zone.is_none() {
        return Err(bad("Timed dates require an explicit UTC offset; date-only uses YYYY-MM-DD"));
    }
    Ok(Value::String(parsed.isoformat()))
}

/// `validate_fields`: the fields as they are stored, in the order they were sent.
fn validate_fields<'a>(body: impl Iterator<Item = (&'a String, &'a Value)>) -> Done<Obj> {
    let mut result = Obj::new();
    for (key, value) in body {
        let checked = match key.as_str() {
            "title" | "category" | "location" | "notes" | "description" | "timezone" => {
                let checked = text(value, key, if matches!(key.as_str(), "notes" | "description") { 20000 } else { 500 })?;
                if matches!(key.as_str(), "title" | "category") && checked.is_empty() {
                    return Err(bad(format!("{key} cannot be empty")));
                }
                Value::String(checked)
            }
            "url" => Value::String(canonical_url(value)?),
            "deadline" | "start" | "end" => valid_date(value)?,
            "favorite" => value.as_bool().map(Value::Bool).ok_or_else(|| bad("favorite must be a boolean"))?,
            "remote" if value.is_null() || value.is_boolean() => value.clone(),
            "remote" => return Err(bad("remote must be a boolean or null")),
            "status" if value.as_str().is_some_and(|status| STATUSES.contains(&status)) => value.clone(),
            "status" => return Err(bad("Unknown opportunity review status")),
            "availability" if value.as_str().is_some_and(|state| AVAILABILITY.contains(&state)) => value.clone(),
            "availability" => return Err(bad("Unknown availability")),
            "tags" => {
                let Some(items) = value.as_array().filter(|items| items.len() <= 100) else { return Err(bad("Tags must be a list of at most 100 strings")) };
                let mut tags: Vec<String> = Vec::new();
                for item in items {
                    let tag = text(item, "Tag", 100)?;
                    if !tags.contains(&tag) {
                        tags.push(tag);
                    }
                }
                Value::String(py::dumps(&json!(tags)))
            }
            _ => return Err(bad(format!("Unknown opportunity field: {key}"))),
        };
        result.insert(key.clone(), checked);
    }
    if result.get("timezone").is_some_and(|zone| Zone::named(zone.as_str().unwrap_or("")).is_none()) {
        return Err(bad("Use a valid IANA timezone"));
    }
    Ok(result)
}

/// A URL field that is set.
fn url_of(fields: &Obj) -> Option<String> {
    fields.get("url").and_then(Value::as_str).filter(|url| !url.is_empty()).map(str::to_owned)
}

fn binds<'a>(values: impl Iterator<Item = &'a Value>) -> Done<Vec<Sql>> {
    values.map(db::bind).collect()
}

// Records ----------------------------------------------------------------------

/// `_decode`: the JSON columns read, the flags as booleans.
fn decode(mut row: Obj) -> Done<Obj> {
    for key in ["tags", "overrides"] {
        let read: Value = serde_json::from_str(row.get(key).and_then(Value::as_str).ok_or_else(internal)?).map_err(|_| internal())?;
        row.insert(key.to_owned(), read);
    }
    for key in ["favorite", "deadline_override"] {
        let flag = truthy(&row, key);
        row.insert(key.to_owned(), Value::Bool(flag));
    }
    if row.get("remote").is_some_and(|remote| !remote.is_null()) {
        let flag = truthy(&row, "remote");
        row.insert("remote".to_owned(), Value::Bool(flag));
    }
    Ok(row)
}

/// `detail`: one opportunity with its evidence, newest first, and its project.
fn detail(conn: &Connection, ident: i64) -> Done<Obj> {
    let row = db::row(conn, "SELECT * FROM opportunities WHERE id=?", [ident])?.ok_or_else(|| fail(404, "Opportunity not found"))?;
    let mut result = decode(row)?;
    let mut evidence = Vec::new();
    for mut seen in db::rows(conn, "SELECT * FROM opportunity_evidence WHERE opportunity_id=? ORDER BY observed_at DESC", [ident])? {
        let facts: Value = serde_json::from_str(seen.get("facts").and_then(Value::as_str).ok_or_else(internal)?).map_err(|_| internal())?;
        seen.insert("facts".to_owned(), facts);
        evidence.push(Value::Object(seen));
    }
    result.insert("evidence".to_owned(), Value::Array(evidence));
    let project = match db::row(conn, "SELECT * FROM opportunity_projects WHERE opportunity_id=?", [ident])? {
        None => Value::Null,
        Some(mut project) => {
            let items = db::rows(conn, "SELECT * FROM opportunity_project_items WHERE project_id=? ORDER BY kind,due,id", [db::bind(&project["id"])?])?;
            project.insert("items".to_owned(), json!(items));
            Value::Object(project)
        }
    };
    result.insert("project".to_owned(), project);
    Ok(result)
}

/// `_revision`: the caller must hold the row's revision.
fn revision(row: &Obj, sent: Option<&Value>) -> Done<i64> {
    sent.and_then(py::int).filter(|sent| row.get("revision").and_then(Value::as_i64) == Some(*sent)).ok_or_else(|| fail(409, "Record changed; reload before saving"))
}

/// `_insert_manual`: one opportunity entered by hand; every fact it names is the person's.
fn insert_manual(conn: &Connection, body: &Obj) -> Done<i64> {
    let mut fields = validate_fields(body.iter())?;
    if !truthy(&fields, "title") {
        return Err(bad("A title is required"));
    }
    let url = url_of(&fields);
    if let Some(url) = &url {
        if db::exists(conn, "SELECT 1 FROM opportunities WHERE url=?", [url])? {
            return Err(fail(409, "This canonical URL is already in your opportunities"));
        }
    }
    let overrides: Vec<&str> = fields.keys().map(String::as_str).filter(|key| FACT_FIELDS.contains(key)).collect();
    let overrides = py::dumps(&json!(overrides));
    let deadline = fields.contains_key("deadline");
    fields.insert("identity".to_owned(), Value::String(url.unwrap_or_else(|| format!("manual:{}", uuid()))));
    fields.insert("source".to_owned(), json!("manual"));
    fields.insert("created_at".to_owned(), Value::String(now_iso()));
    fields.insert("updated_at".to_owned(), Value::String(now_iso()));
    fields.insert("overrides".to_owned(), Value::String(overrides));
    fields.insert("deadline_override".to_owned(), json!(i64::from(deadline)));
    let columns: Vec<&str> = fields.keys().map(String::as_str).collect();
    let sql = format!("INSERT INTO opportunities ({}) VALUES ({})", columns.join(","), vec!["?"; columns.len()].join(","));
    conn.execute(&sql, params_from_iter(binds(fields.values())?))?;
    Ok(conn.last_insert_rowid())
}

fn create(conn: &Connection, body: &Obj) -> Done<Obj> {
    let ident = db::transaction(conn, false, || insert_manual(conn, body))?;
    detail(conn, ident)
}

/// Every entry or none.
fn import_items(conn: &Connection, items: &Value) -> Done<Value> {
    let Some(items) = items.as_array().filter(|items| (1..=500).contains(&items.len()) && items.iter().all(Value::is_object)) else {
        return Err(bad("Import requires 1–500 JSON opportunity objects"));
    };
    let ids = db::transaction(conn, false, || items.iter().filter_map(Value::as_object).map(|item| insert_manual(conn, item)).collect::<Done<Vec<i64>>>())?;
    let made = ids.iter().map(|ident| detail(conn, *ident)).collect::<Done<Vec<Obj>>>()?;
    Ok(json!({"items": made, "count": ids.len()}))
}

/// An edit: the facts it names become the person's, and a source's refresh leaves them alone.
fn update(conn: &Connection, ident: Done<i64>, body: &Obj) -> Done<Obj> {
    let mut fields = validate_fields(body.iter().filter(|(key, _)| *key != "revision"))?;
    let ident = db::transaction(conn, false, || {
        let ident = ident?;
        let current = detail(conn, ident)?;
        let sent = revision(&current, body.get("revision"))?;
        let url = url_of(&fields);
        if let Some(url) = &url {
            if db::exists(conn, "SELECT 1 FROM opportunities WHERE url=? AND id<>?", params![url, ident])? {
                return Err(fail(409, "This canonical URL is already in your opportunities"));
            }
        }
        if fields.contains_key("url") && st(&current, "source") == "manual" {
            fields.insert("identity".to_owned(), Value::String(url.unwrap_or_else(|| format!("manual:{}", uuid()))));
        }
        let mut overrides = BTreeSet::new();
        for name in current.get("overrides").and_then(Value::as_array).ok_or_else(internal)? {
            overrides.insert(name.as_str().ok_or_else(internal)?.to_owned());
        }
        overrides.extend(fields.keys().filter(|key| FACT_FIELDS.contains(&key.as_str())).cloned());
        let deadline = overrides.contains("deadline");
        fields.insert("overrides".to_owned(), Value::String(py::dumps(&json!(overrides))));
        fields.insert("updated_at".to_owned(), Value::String(now_iso()));
        fields.insert("revision".to_owned(), json!(sent.checked_add(1).ok_or_else(internal)?));
        fields.insert("deadline_override".to_owned(), json!(i64::from(deadline)));
        let sql = format!("UPDATE opportunities SET {} WHERE id=?", fields.keys().map(|key| format!("{key}=?")).collect::<Vec<_>>().join(","));
        let mut values = binds(fields.values())?;
        values.push(Sql::Integer(ident));
        conn.execute(&sql, params_from_iter(values))?;
        Ok(ident)
    })?;
    detail(conn, ident)
}

fn remove(conn: &Connection, ident: Done<i64>, sent: &Value) -> Done<Value> {
    db::transaction(conn, false, || {
        let ident = ident?;
        revision(&detail(conn, ident)?, Some(sent))?;
        conn.execute("DELETE FROM opportunities WHERE id=?", [ident])?;
        Ok(())
    })?;
    Ok(json!({"ok": true}))
}

/// Mark or unmark several at once: all of them, or none when one changed meanwhile.
fn favorites(conn: &Connection, selections: &Value, value: &Value) -> Done<Value> {
    let (Some(value), Some(selections)) = (value.as_bool(), selections.as_array().filter(|items| (1..=500).contains(&items.len()))) else {
        return Err(bad("Select 1–500 opportunities and a boolean favorite value"));
    };
    let chosen: Vec<&Obj> = selections.iter().filter_map(Value::as_object).filter(|chosen| exactly(chosen, &["id", "revision"]) && is_int(&chosen["id"])).collect();
    if chosen.len() != selections.len() {
        return Err(bad("Selections require id and revision"));
    }
    if chosen.iter().enumerate().any(|(index, one)| chosen[..index].iter().any(|other| other["id"] == one["id"])) {
        return Err(bad("Selections must be unique"));
    }
    db::transaction(conn, false, || {
        for one in &chosen {
            revision(&detail(conn, stored_id(&one["id"])?)?, one.get("revision"))?;
        }
        for one in &chosen {
            conn.execute("UPDATE opportunities SET favorite=?,status=CASE WHEN ?=1 AND status IN ('new','discovered') THEN 'saved' ELSE status END,revision=revision+1,updated_at=? WHERE id=?",
                         params![value, value, now_iso(), stored_id(&one["id"])?])?;
        }
        Ok(())
    })?;
    Ok(json!({"ok": true, "count": chosen.len()}))
}

// The list ---------------------------------------------------------------------

/// `settings`: the monitoring row, its profile read.
fn settings(conn: &Connection) -> Done<Obj> {
    let mut row = db::row(conn, "SELECT * FROM opportunity_settings WHERE id=1", [])?.ok_or_else(internal)?;
    let profile: Value = serde_json::from_str(row.get("profile").and_then(Value::as_str).ok_or_else(internal)?).map_err(|_| internal())?;
    row.insert("profile".to_owned(), profile);
    let enabled = truthy(&row, "enabled");
    row.insert("enabled".to_owned(), Value::Bool(enabled));
    Ok(row)
}

/// `opportunity_sources.sources`: the built-in public sources are there (added once, in
/// their order), then every source by name.
fn sources(conn: &Connection) -> Done<Vec<Obj>> {
    let known = format!("SELECT COUNT(*) FROM opportunity_sources WHERE id IN ({})", vec!["?"; SOURCES.len()].join(","));
    let present: i64 = conn.query_row(&known, params_from_iter(SOURCES.iter().map(|source| source[0])), |row| row.get(0))?;
    if present < SOURCES.len() as i64 {
        let sql = format!("INSERT OR IGNORE INTO opportunity_sources(id,name,url,kind,category,timezone) VALUES {}", vec!["(?,?,?,?,?,?)"; SOURCES.len()].join(","));
        conn.execute(&sql, params_from_iter(SOURCES.iter().flatten()))?;
    }
    db::rows(conn, "SELECT * FROM opportunity_sources ORDER BY name", [])
}

/// `rank`: ten points for each way the opportunity meets the profile, with the reasons.
fn rank(item: &Obj, profile: &Value) -> Done<(i64, Vec<String>)> {
    let part = |key: &str| profile.get(key).ok_or_else(internal);
    let shown = |key: &str| match item.get(key) {
        Some(value) if py::truthy(value) => value.as_str().map_or_else(|| value.to_string(), str::to_owned),
        _ => String::new(),
    };
    let haystack = casefold(&["title", "description", "category", "source"].map(shown).join(" "));
    let mut reasons = Vec::new();
    let interests = part("interests")?.as_array().ok_or_else(internal)?;
    let (major, track) = (part("major")?, part("track")?);
    for (label, value) in interests.iter().map(|value| ("Interest", value)).chain([("Major", major), ("Track", track)]) {
        if py::truthy(value) {
            let value = value.as_str().ok_or_else(internal)?;
            if haystack.contains(&casefold(value)) {
                reasons.push(format!("{label} match: {value}"));
            }
        }
    }
    let place = casefold(item.get("location").and_then(Value::as_str).ok_or_else(internal)?);
    for location in part("locations")?.as_array().ok_or_else(internal)? {
        let location = location.as_str().ok_or_else(internal)?;
        if place.contains(&casefold(location)) {
            reasons.push(format!("Location match: {location}"));
        }
    }
    if py::truthy(part("prefer_remote")?) && truthy(item, "remote") {
        reasons.push("Remote preference match".to_owned());
    }
    Ok((reasons.len() as i64 * 10, reasons))
}

/// `availability`: closed, future, active or unknown from the stored dates at `now`. A
/// date without a time lasts until the end of that day in the opportunity's zone.
fn availability(item: &Obj, now: &Dt) -> Done<&'static str> {
    if st(item, "availability") == "closed" {
        return Ok("closed");
    }
    let instant = |key: &str| -> Done<Option<Dt>> {
        let Some(value) = item.get(key).filter(|value| py::truthy(value)) else { return Ok(None) };
        let value = value.as_str().ok_or_else(internal)?;
        if py::len(value) == 10 {
            let next = Date::parse_dashed(value).and_then(|date| date.add_days(1)).ok_or_else(internal)?;
            return Ok(Some(Dt::midnight(next, Some(Zone::named(st(item, "timezone")).ok_or_else(internal)?))));
        }
        Ok(Some(time::fromisoformat(&value.replace('Z', "+00:00")).ok_or_else(internal)?))
    };
    let (end, start, deadline) = (instant("end")?, instant("start")?, instant("deadline")?);
    // Python cannot order a time without an offset against the clock's.
    let before = |a: &Dt, b: &Dt| if a.zone.is_some() && b.zone.is_some() { Ok(a.lt(b)) } else { Err(internal()) };
    if let Some(end) = &end {
        if before(end, now)? {
            return Ok("closed");
        }
    }
    if let Some(deadline) = &deadline {
        if before(deadline, now)? && start.is_none() {
            return Ok("closed");
        }
    }
    if let Some(start) = &start {
        if before(now, start)? {
            return Ok("future");
        }
        if let Some(end) = &end {
            if !before(now, start)? && !before(end, now)? {
                return Ok("active");
            }
        }
    }
    if let Some(deadline) = &deadline {
        if !before(deadline, now)? {
            return Ok("future");
        }
    }
    Ok("unknown")
}

/// `list_items`: the filtered opportunities, best match first, then the nearest deadline, then the newest.
fn list_items(conn: &Connection, call: &Call) -> Done<Vec<Obj>> {
    if !call.only(&FILTERS) {
        return Err(bad("Unknown opportunity filter"));
    }
    let filter = |key: &str| call.get(key).filter(|value| !value.is_empty());
    let mut clauses: Vec<String> = Vec::new();
    let mut values: Vec<Sql> = Vec::new();
    for key in ["source", "category", "status"] {
        if let Some(value) = filter(key) {
            clauses.push(format!("{key}=?"));
            values.push(Sql::Text(value.to_owned()));
        }
    }
    for (key, columns) in [("search", "title||' '||description||' '||notes||' '||tags"), ("location", "location")] {
        if let Some(value) = filter(key) {
            clauses.push(format!("instr(lower({columns}),lower(?))>0"));
            values.push(Sql::Text(value.to_owned()));
        }
    }
    for (key, column) in [("remote", "remote"), ("favorites", "favorite")] {
        match filter(key) {
            None => {}
            Some("unknown") if key == "remote" => clauses.push(format!("{column} IS NULL")),
            Some(value @ ("true" | "false")) => {
                clauses.push(format!("{column}=?"));
                values.push(Sql::Integer(i64::from(value == "true")));
            }
            Some(_) => return Err(bad("Invalid boolean filter")),
        }
    }
    match filter("deadline") {
        None => {}
        Some("unknown") => clauses.push("deadline IS NULL".to_owned()),
        Some(value) => {
            let until = valid_date(&json!(value))?;
            clauses.push("substr(deadline,1,10)<=?".to_owned());
            values.push(Sql::Text(until.as_str().ok_or_else(internal)?.chars().take(10).collect()));
        }
    }
    let profile = settings(conn)?.remove("profile").ok_or_else(internal)?;
    let sql = format!("SELECT * FROM opportunities{}", if clauses.is_empty() { String::new() } else { format!(" WHERE {}", clauses.join(" AND ")) });
    let now = Dt::now_utc();
    let mut rows = Vec::new();
    for row in db::rows(conn, &sql, params_from_iter(values))? {
        let mut item = decode(row)?;
        let state = availability(&item, &now)?;
        item.insert("availability".to_owned(), json!(state));
        let (score, reasons) = rank(&item, &profile)?;
        item.insert("score".to_owned(), json!(score));
        item.insert("reasons".to_owned(), json!(reasons));
        rows.push(item);
    }
    if let Some(wanted) = filter("availability") {
        if !AVAILABILITY.contains(&wanted) {
            return Err(bad("Invalid availability filter"));
        }
        rows.retain(|row| st(row, "availability") == wanted);
    }
    let key = |row: &Obj| (-row["score"].as_i64().unwrap_or(0), row["deadline"].as_str().filter(|deadline| !deadline.is_empty()).unwrap_or("9999").to_owned(), -row["id"].as_i64().unwrap_or(0));
    rows.sort_by_cached_key(key);
    Ok(rows)
}

// Projects ---------------------------------------------------------------------

/// The opportunity's project, made once from its title.
fn project(conn: &Connection, ident: Done<i64>, body: &Obj) -> Done<Obj> {
    let ident = db::transaction(conn, false, || {
        let ident = ident?;
        let item = detail(conn, ident)?;
        revision(&item, body.get("revision"))?;
        if item["project"].is_null() {
            conn.execute("INSERT INTO opportunity_projects(opportunity_id,title) VALUES(?,?)", params![ident, st(&item, "title")])?;
            conn.execute("UPDATE opportunities SET revision=revision+1,updated_at=? WHERE id=?", params![now_iso(), ident])?;
        }
        Ok(ident)
    })?;
    detail(conn, ident)
}

/// Add, change or delete one task, milestone or resource of the project.
fn project_item(conn: &Connection, ident: Done<i64>, body: &Obj) -> Done<Obj> {
    if !only(body, &["revision", "id", "kind", "title", "due", "url", "notes", "done", "delete"]) {
        return Err(bad("Unknown project item field"));
    }
    let ident = db::transaction(conn, false, || {
        let ident = ident?;
        let opportunity = detail(conn, ident)?;
        let Some(project) = opportunity["project"].as_object() else { return Err(fail(409, "Create the project first")) };
        revision(project, body.get("revision"))?;
        let project_id = stored_id(&project["id"])?;
        let existing = match body.get("id") {
            None => None,
            Some(item_id) if is_int(item_id) => {
                let found = db::row(conn, "SELECT * FROM opportunity_project_items WHERE id=? AND project_id=?", params![stored_id(item_id)?, project_id])?;
                Some(found.ok_or_else(|| fail(404, "Project item not found"))?)
            }
            Some(_) => return Err(bad("Item id must be an integer")),
        };
        if body.get("delete") == Some(&Value::Bool(true)) {
            let Some(existing) = &existing else { return Err(bad("Select an item to delete")) };
            conn.execute("DELETE FROM opportunity_project_items WHERE id=?", [stored_id(&existing["id"])?])?;
        } else {
            let field = |key: &str, new: Value| body.get(key).or_else(|| existing.as_ref().and_then(|item| item.get(key))).cloned().unwrap_or(new);
            let kind = field("kind", json!("task"));
            if !matches!(kind.as_str(), Some("task" | "milestone" | "resource")) {
                return Err(bad("Invalid project item kind"));
            }
            let title = text(&field("title", json!("")), "Title", 500)?;
            if title.is_empty() {
                return Err(bad("A title is required"));
            }
            let notes = text(&field("notes", json!("")), "Notes", 20000)?;
            let url = canonical_url(&field("url", json!("")))?;
            let due = valid_date(&field("due", Value::Null))?;
            let done = field("done", json!(0));
            if !py::eq_int(&done, 0) && !py::eq_int(&done, 1) {
                return Err(bad("done must be boolean"));
            }
            let mut values = vec![db::bind(&kind)?, Sql::Text(title), db::bind(&due)?, Sql::Text(url), Sql::Text(notes), db::bind(&done)?];
            match &existing {
                Some(existing) => {
                    values.push(Sql::Integer(stored_id(&existing["id"])?));
                    conn.execute("UPDATE opportunity_project_items SET kind=?,title=?,due=?,url=?,notes=?,done=?,revision=revision+1 WHERE id=?", params_from_iter(values))?;
                }
                None => {
                    values.insert(0, Sql::Integer(project_id));
                    conn.execute("INSERT INTO opportunity_project_items(project_id,kind,title,due,url,notes,done) VALUES(?,?,?,?,?,?,?)", params_from_iter(values))?;
                }
            }
        }
        conn.execute("UPDATE opportunity_projects SET revision=revision+1 WHERE id=?", [project_id])?;
        Ok(ident)
    })?;
    detail(conn, ident)
}

fn update_project(conn: &Connection, ident: Done<i64>, body: &Obj) -> Done<Obj> {
    if !only(body, &["revision", "title", "notes"]) {
        return Err(bad("Unknown project field"));
    }
    let ident = db::transaction(conn, false, || {
        let ident = ident?;
        let opportunity = detail(conn, ident)?;
        let Some(project) = opportunity["project"].as_object() else { return Err(fail(404, "Project not found")) };
        revision(project, body.get("revision"))?;
        let title = text(body.get("title").unwrap_or(&project["title"]), "Title", 500)?;
        if title.is_empty() {
            return Err(bad("A title is required"));
        }
        let notes = text(body.get("notes").unwrap_or(&project["notes"]), "Notes", 20000)?;
        conn.execute("UPDATE opportunity_projects SET title=?,notes=?,revision=revision+1 WHERE id=?", params![title, notes, stored_id(&project["id"])?])?;
        Ok(ident)
    })?;
    detail(conn, ident)
}

/// The deadline as a calendar event: a day for a date, half an hour for a time. Asked
/// again, the calendar answers the event it already made.
fn export_deadline(conn: &Connection, ident: Done<i64>) -> Done<Obj> {
    let ident = ident?;
    let item = detail(conn, ident)?;
    if !truthy(&item, "deadline") {
        return Err(bad("No verified or manually supplied deadline; set one before exporting"));
    }
    let deadline = item["deadline"].as_str().ok_or_else(internal)?;
    let all_day = py::len(deadline) == 10;
    let end = if all_day {
        Date::parse_dashed(deadline).and_then(|date| date.add_days(1)).ok_or_else(internal)?.iso()
    } else {
        time::fromisoformat(deadline).ok_or_else(internal)?.add(30 * MINUTE)?.isoformat()
    };
    let event = json!({
        "title": format!("{} — deadline", st(&item, "title")), "start": deadline, "end": end, "timezone": item["timezone"], "all_day": all_day,
        "description": if truthy(&item, "notes") { &item["notes"] } else { &item["description"] }, "source_url": item["url"],
        "source_key": format!("opportunity:{ident}:deadline"),
    });
    create_event(conn, event.as_object().ok_or_else(internal)?)
}

// Routes -----------------------------------------------------------------------

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    // Python reads an id of any length; SQLite holds 63 bits (`OverflowError`, a 500, at the first query).
    let id = |text: &str| text.parse::<i64>().map_err(|_| internal());
    if !call.mutation() {
        if call.path == ROOT {
            let items = list_items(conn, call)?;
            let mut schedule = settings(conn)?;
            let profile = schedule.shift_remove("profile");
            return Ok(Some(json!({"items": items, "total": items.len(), "sources": sources(conn)?, "profile": profile, "schedule": schedule, "statuses": STATUSES})));
        }
        let Some(found) = ONE.captures(call.path) else { return Ok(None) };
        if !call.query.is_empty() {
            return Err(bad("Detail does not accept query parameters"));
        }
        return Ok(Some(Value::Object(detail(conn, id(&found[1])?)?)));
    }
    let body = call.body();
    Ok(Some(match call.path {
        ROOT => Value::Object(create(conn, body)?),
        IMPORT => {
            if !exactly(body, &["items"]) {
                return Err(bad("Import requires an items array"));
            }
            import_items(conn, &body["items"])?
        }
        FAVORITES => {
            if !exactly(body, &["selections", "favorite"]) {
                return Err(bad("Expected selections and favorite"));
            }
            favorites(conn, &body["selections"], &body["favorite"])?
        }
        path => {
            let Some(found) = CHANGE.captures(path) else { return Ok(None) };
            let ident = id(&found[1]);
            match found.get(2).map(|action| action.as_str()) {
                Some("calendar") => {
                    if !body.is_empty() {
                        return Err(bad("Calendar export expects an empty object"));
                    }
                    json!({"event": export_deadline(conn, ident)?})
                }
                Some("delete") => {
                    if !exactly(body, &["revision"]) {
                        return Err(bad("Deletion requires revision"));
                    }
                    remove(conn, ident, &body["revision"])?
                }
                Some("project") => {
                    if !exactly(body, &["revision"]) {
                        return Err(bad("Project creation requires opportunity revision"));
                    }
                    Value::Object(project(conn, ident, body)?)
                }
                Some("items") => Value::Object(project_item(conn, ident, body)?),
                Some("project-details") => Value::Object(update_project(conn, ident, body)?),
                _ => Value::Object(update(conn, ident, body)?),
            }
        }
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(text: &str) -> Dt {
        time::fromisoformat(text).expect("a timestamp")
    }

    fn item(fields: Value) -> Obj {
        fields.as_object().expect("an object").clone()
    }

    #[test]
    fn dates_keep_pythons_isoformat() {
        for (sent, stored) in [
            ("2027-01-11", Some("2027-01-11")), ("2027-01-11T12:00:00Z", Some("2027-01-11T12:00:00+00:00")),
            ("2027-01-11 12:00-06:00", Some("2027-01-11T12:00:00-06:00")), ("2027-01-11T12:00:00.5+05:30", Some("2027-01-11T12:00:00.500000+05:30")),
            ("20270111T1200Z", Some("2027-01-11T12:00:00+00:00")), ("2027-02-30", None), ("tomorrow", None), ("2027-01-11T25:00Z", None),
        ] {
            assert_eq!(valid_date(&json!(sent)).ok(), stored.map(|text| json!(text)), "{sent}");
        }
        assert_eq!(valid_date(&json!("2027-01-11T12:00:00")).unwrap_err().message, "Timed dates require an explicit UTC offset; date-only uses YYYY-MM-DD");
        assert_eq!(valid_date(&json!("20270111")).unwrap_err().message, "Timed dates require an explicit UTC offset; date-only uses YYYY-MM-DD");
        assert_eq!(valid_date(&json!(7)).unwrap_err().message, "Date must be text of at most 100 characters");
        assert_eq!(valid_date(&Value::Null).unwrap(), Value::Null);
        assert_eq!(valid_date(&json!("")).unwrap(), Value::Null);
    }

    #[test]
    fn availability_follows_the_dates() {
        let now = at("2026-10-14T12:00:00+00:00");
        for (fields, state) in [
            (json!({"availability": "closed", "start": "2099-01-01", "timezone": "UTC"}), "closed"),
            (json!({"start": "2099-01-01", "timezone": "America/Chicago"}), "future"),
            (json!({"start": "2000-01-01", "end": "2000-01-02", "timezone": "UTC"}), "closed"),
            (json!({"start": "2026-10-13", "end": "2026-10-14", "timezone": "UTC"}), "active"),
            // A date lasts through its day where the opportunity is: over in Tokyo, not yet in Chicago.
            (json!({"deadline": "2026-10-14", "timezone": "America/Chicago"}), "future"),
            (json!({"deadline": "2026-10-13", "timezone": "Asia/Tokyo"}), "closed"),
            (json!({"deadline": "2026-10-13", "start": "2026-10-01T00:00:00+00:00", "timezone": "UTC"}), "unknown"),
            (json!({"deadline": "2026-10-14T12:00:00+00:00", "timezone": "UTC"}), "future"),
            (json!({"deadline": "2026-10-14T06:59:59-05:00", "timezone": "UTC"}), "closed"),
            (json!({"timezone": "UTC"}), "unknown"),
        ] {
            assert_eq!(availability(&item(fields.clone()), &now).unwrap(), state, "{fields}");
        }
        assert!(availability(&item(json!({"end": "2026-10-14T12:00:00", "timezone": "UTC"})), &now).is_err());
    }

    #[test]
    fn rank_counts_each_match_once() {
        let profile = json!({"major": "Engineering", "track": "", "interests": ["HARDWARE", "", "stra\u{df}e"], "locations": ["college station"], "prefer_remote": true});
        let found = item(json!({"title": "Engineering hardware", "description": "STRASSE", "category": "quant", "source": "manual", "location": "College Station, TX", "remote": true}));
        let (score, reasons) = rank(&found, &profile).unwrap();
        assert_eq!(score, 50);
        assert_eq!(reasons, ["Interest match: HARDWARE", "Interest match: stra\u{df}e", "Major match: Engineering", "Location match: college station", "Remote preference match"]);
    }

    #[test]
    fn only_the_moved_routes_are_owned() {
        for path in [ROOT, "/api/opportunities/7", "/api/opportunities/123456789012345678901234567890"] {
            assert!(owns(path, false) && owns(path, true), "{path}");
        }
        for path in [IMPORT, FAVORITES, "/api/opportunities/7/delete", "/api/opportunities/7/project", "/api/opportunities/7/items", "/api/opportunities/7/project-details", "/api/opportunities/7/calendar"] {
            assert!(owns(path, true) && !owns(path, false), "{path}");
        }
        for path in ["/api/opportunities/settings", "/api/opportunities/discover", "/api/opportunities/sources/mlh", "/api/opportunities/0", "/api/opportunities/7/nope", "/api/opportunities/"] {
            assert!(!owns(path, true) && !owns(path, false), "{path}");
        }
    }
}
