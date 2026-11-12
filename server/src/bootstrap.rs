//! `GET /api/bootstrap`: the shell's first answer (`WebApplication._bootstrap` in Python).
//!
//! Always `{"demo", "modules"}`; while ApplyAI is active also its tracks, the four job counts
//! (`jobs.count_jobs` with `WebApplication.job_filter`'s views), this week's total and streak,
//! and today's digest as text (`today.build_digest` + `today.digest_text`), read from the
//! workspace database. The two digest settings from `config/apply.yaml` are the ones the
//! worker validated at start (its readiness line), as Python's answer used the config it
//! loaded.

use std::collections::{BTreeSet, HashMap};
use std::path::PathBuf;
use std::sync::{LazyLock, Mutex};

use regex::Regex;
use rusqlite::types::Value as Sql;
use rusqlite::{params, Connection, OpenFlags};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::migrations::civil;
use crate::modules::{Failure, Workspace};

pub const PATH: &str = "/api/bootstrap";

/// `ApplyFile.deadline_window_days` and `archive_after_days`, as the worker loaded them.
#[derive(Debug, Clone, Copy, Deserialize)]
pub struct DigestSettings {
    pub deadline_window_days: i64,
    pub archive_after_days: i64,
}

/// What the answer reads besides the module state.
pub struct Bootstrap {
    /// The database the worker opened (and the front migrated).
    db_path: PathBuf,
    /// The workspace root, whose `.env` the worker loaded into its environment.
    root: PathBuf,
    demo: bool,
    /// Present while ApplyAI is active.
    digest: Option<DigestSettings>,
    /// Kept open between answers: opening one parses the whole schema (most of a cold
    /// answer's time); SQLite re-reads it only when a migration changed it.
    connection: Mutex<Option<Connection>>,
}

fn fail(status: u16, message: &str) -> Failure {
    Failure { status, message: message.into() }
}

fn internal() -> Failure {
    fail(500, "Local operation failed; inspect CLI diagnostics")
}

/// Python's answer to a `sqlite3.OperationalError` (busy, locked, unreadable) and to anything else.
fn database(error: rusqlite::Error) -> Failure {
    use rusqlite::ErrorCode::*;
    match &error {
        rusqlite::Error::SqliteFailure(failure, _) if matches!(failure.code,
            Unknown | PermissionDenied | OperationAborted | DatabaseBusy | DatabaseLocked | ReadOnly
            | OperationInterrupted | SystemIoFailure | DiskFull | CannotOpen | FileLockingProtocolFailed | SchemaChanged) =>
            fail(503, "Local workspace is busy or unavailable; try again"),
        _ => internal(),
    }
}

impl Bootstrap {
    pub fn new(db_path: PathBuf, root: PathBuf, demo: bool, digest: Option<DigestSettings>) -> Self {
        Self { db_path, root, demo, digest, connection: Mutex::new(None) }
    }

    pub fn answer(&self, workspace: &Workspace) -> Result<Value, Failure> {
        let mut result = Map::new();
        result.insert("demo".into(), Value::Bool(self.demo));
        result.insert("modules".into(), workspace.state()?);
        if workspace.active.iter().any(|module| module == "applyai") {
            let settings = self.digest.ok_or_else(internal)?;
            let mut held = self.connection.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
            if held.is_none() {
                let conn = Connection::open_with_flags(&self.db_path, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX)
                    .map_err(database)?;
                conn.busy_timeout(std::time::Duration::from_millis(5000)).map_err(database)?;
                *held = Some(conn);
            }
            let conn = held.as_ref().expect("opened above");
            let outcome = applyai(conn, &mut result, settings, Now::current(), &self.environment());
            if let Err(error) = outcome {
                // A connection that failed is not trusted for the next answer.
                *held = None;
                return Err(match error {
                    Problem::Sql(error) => database(error),
                    Problem::Python => internal(),
                });
            }
        }
        Ok(Value::Object(result))
    }

    /// The worker's `os.environ`: this process's environment plus the root's `.env` (which
    /// `load_dotenv` adds without overriding), for `safe_error`'s redaction.
    fn environment(&self) -> Vec<(String, String)> {
        let mut values: Vec<(String, String)> = std::env::vars().collect();
        if let Ok(text) = std::fs::read_to_string(self.root.join(".env")) {
            for (key, value) in crate::settings::dotenv(&text) {
                if let Some(value) = value {
                    if std::env::var_os(&key).is_none() {
                        values.push((key, value));
                    }
                }
            }
        }
        values
    }
}

enum Problem {
    Sql(rusqlite::Error),
    /// Where Python would have raised (a value of a type its code does not expect).
    Python,
}

impl From<rusqlite::Error> for Problem {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sql(error)
    }
}

type Result2<T> = std::result::Result<T, Problem>;

// ------------------------------------------------------------------------------ time

/// One moment, in microseconds since the epoch (UTC), as `kitty.dates.now()`.
#[derive(Clone, Copy)]
struct Now {
    micros: i64,
}

impl Now {
    fn current() -> Self {
        let elapsed = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        Self { micros: elapsed.as_micros() as i64 }
    }

    fn day(self) -> Date {
        Date::from_days(self.micros.div_euclid(86_400_000_000))
    }

    /// `week_start_iso()`: Monday 00:00 UTC of this week.
    fn week_start_iso(self) -> String {
        let today = self.day();
        let monday = Date::from_days(today.days() - today.weekday());
        format!("{}T00:00:00Z", monday.iso())
    }
}

/// A calendar date (proleptic Gregorian).
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
struct Date {
    year: i64,
    month: u32,
    day: u32,
}

fn days_in_month(year: i64, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        _ => 28,
    }
}

impl Date {
    fn new(year: i64, month: u32, day: u32) -> Option<Self> {
        ((1..=9999).contains(&year) && (1..=12).contains(&month) && day >= 1 && day <= days_in_month(year, month))
            .then_some(Self { year, month, day })
    }

    fn from_days(days: i64) -> Self {
        let (year, month, day) = civil(days);
        Self { year, month, day }
    }

    /// Days since 1970-01-01 (Hinnant's `days_from_civil`).
    fn days(self) -> i64 {
        let year = self.year - i64::from(self.month <= 2);
        let era = year.div_euclid(400);
        let yoe = year.rem_euclid(400);
        let month = i64::from(self.month);
        let doy = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + i64::from(self.day) - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        era * 146_097 + doe - 719_468
    }

    /// Monday is 0, as `date.weekday()`.
    fn weekday(self) -> i64 {
        (self.days() + 3).rem_euclid(7)
    }

    fn iso(self) -> String {
        format!("{:04}-{:02}-{:02}", self.year, self.month, self.day)
    }

    fn add_days(self, days: i64) -> Self {
        Self::from_days(self.days() + days)
    }
}

/// A parsed stored timestamp: its own calendar date and the moment it names.
#[derive(Clone, Copy)]
struct Stamp {
    date: Date,
    micros: i64,
}

fn digits(text: &str) -> Option<i64> {
    (!text.is_empty() && text.bytes().all(|b| b.is_ascii_digit())).then(|| text.parse().ok()).flatten()
}

/// `_parse_hh_mm_ss_ff`: hours, minutes, seconds, microseconds.
fn hh_mm_ss_ff(text: &str) -> Option<[i64; 4]> {
    let bytes = text.as_bytes();
    let mut parts = [0i64; 4];
    let mut pos = 0;
    let mut separated = false;
    for component in 0..3 {
        if bytes.len() < pos + 2 {
            return None;
        }
        parts[component] = digits(text.get(pos..pos + 2)?)?;
        pos += 2;
        let next = bytes.get(pos).copied();
        if component == 0 {
            separated = next == Some(b':');
        }
        if next.is_none() || component >= 2 {
            break;
        }
        if separated && next != Some(b':') {
            return None;
        }
        pos += usize::from(separated);
    }
    if pos < bytes.len() {
        if !matches!(bytes[pos], b'.' | b',') {
            return None;
        }
        pos += 1;
        let rest = text.get(pos..)?;
        let take = rest.len().min(6);
        let mut micros = digits(rest.get(..take)?)?;
        for _ in take..6 {
            micros *= 10;
        }
        if !rest[take..].bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        parts[3] = micros;
    }
    Some(parts)
}

/// The date part's length (`_find_isoformat_datetime_separator`, week dates aside).
fn separator(text: &str) -> Option<usize> {
    let bytes = text.as_bytes();
    if bytes.len() == 7 {
        return Some(7);
    }
    if bytes.get(4) == Some(&b'-') {
        if bytes.get(5) == Some(&b'W') {
            return None;
        }
        Some(10)
    } else if bytes.get(4) == Some(&b'W') {
        None
    } else {
        Some(8)
    }
}

/// `kitty.dates.parse`: `datetime.fromisoformat` after `Z` -> `+00:00`, naive read as UTC.
/// ISO week dates (which rika! never stores) read as unparseable.
fn parse(value: &str) -> Option<Stamp> {
    let text = value.trim().replace('Z', "+00:00");
    if text.chars().count() < 7 || !text.is_ascii() {
        return None;
    }
    let split = separator(&text)?;
    let (date_text, time_text) = (text.get(..split)?, text.get(split + 1..).unwrap_or(""));
    let date = match date_text.len() {
        10 if &date_text[4..5] == "-" && &date_text[7..8] == "-" => {
            Date::new(digits(&date_text[..4])?, digits(&date_text[5..7])? as u32, digits(&date_text[8..10])? as u32)?
        }
        8 if !date_text.contains('-') => {
            Date::new(digits(&date_text[..4])?, digits(&date_text[4..6])? as u32, digits(&date_text[6..8])? as u32)?
        }
        _ => return None,
    };
    let mut micros = date.days() * 86_400_000_000;
    let mut date = date;
    if !time_text.is_empty() {
        if time_text.len() < 2 {
            return None;
        }
        let zone = time_text.find('-').or_else(|| time_text.find('+'));
        let clock = hh_mm_ss_ff(&time_text[..zone.unwrap_or(time_text.len())])?;
        let mut offset = 0i64;
        if let Some(at) = zone {
            let zone_text = &time_text[at + 1..];
            if matches!(zone_text.len(), 0 | 1 | 3) {
                return None;
            }
            let parts = hh_mm_ss_ff(zone_text)?;
            let magnitude = ((parts[0] * 60 + parts[1]) * 60 + parts[2]) * 1_000_000 + parts[3];
            if magnitude >= 86_400_000_000 {
                return None;
            }
            offset = if time_text.as_bytes()[at] == b'-' { -magnitude } else { magnitude };
        }
        let [hour, minute, second, fraction] = clock;
        if hour == 24 && minute == 0 && second == 0 && fraction == 0 {
            date = date.add_days(1);
            micros += 86_400_000_000;
        } else if hour > 23 || minute > 59 || second > 59 {
            return None;
        } else {
            micros += ((hour * 60 + minute) * 60 + second) * 1_000_000 + fraction;
        }
        micros -= offset;
    }
    Some(Stamp { date, micros })
}

/// `days_since`: whole days from the stamp to now, never negative; 0 when unparseable.
fn days_since(value: Option<&str>, now: Now) -> i64 {
    value.and_then(parse).map(|stamp| (now.micros - stamp.micros).div_euclid(86_400_000_000).max(0)).unwrap_or(0)
}

/// `_short_date`: the stamp's date, else the text as stored (`?` when missing).
fn short_date(value: Option<&str>) -> String {
    match value {
        Some(text) => parse(text).map(|stamp| stamp.date.iso()).unwrap_or_else(|| text.to_owned()),
        None => "?".into(),
    }
}

// ------------------------------------------------------------------------------ values

/// `str(value)` for what SQLite returns: `None`, the integer, the float's repr, the text.
fn py_str(value: &Sql) -> Result2<String> {
    Ok(match value {
        Sql::Null => "None".into(),
        Sql::Integer(number) => number.to_string(),
        Sql::Real(number) => py_float(*number),
        Sql::Text(text) => text.clone(),
        Sql::Blob(_) => return Err(Problem::Python),
    })
}

/// Python's `repr(float)`.
fn py_float(number: f64) -> String {
    if number.is_nan() {
        return "nan".into();
    }
    if number.is_infinite() {
        return if number > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let scientific = format!("{number:e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("{:e} has an exponent");
    let exponent: i32 = exponent.parse().expect("an integer exponent");
    if (-4..16).contains(&exponent) {
        let plain = format!("{number}");
        if plain.contains('.') { plain } else { format!("{plain}.0") }
    } else {
        let mantissa = if mantissa.contains('.') { mantissa.to_owned() } else { mantissa.to_owned() };
        format!("{mantissa}e{}{:02}", if exponent < 0 { '-' } else { '+' }, exponent.abs())
    }
}

fn truthy(value: &Sql) -> bool {
    match value {
        Sql::Null => false,
        Sql::Integer(number) => *number != 0,
        Sql::Real(number) => *number != 0.0,
        Sql::Text(text) => !text.is_empty(),
        Sql::Blob(bytes) => !bytes.is_empty(),
    }
}

/// A value Python reads as text: text or NULL; anything else is a type error there.
fn text(value: Sql) -> Result2<Option<String>> {
    match value {
        Sql::Null => Ok(None),
        Sql::Text(text) => Ok(Some(text)),
        _ => Err(Problem::Python),
    }
}

fn json(value: Sql) -> Result2<Value> {
    Ok(match value {
        Sql::Null => Value::Null,
        Sql::Integer(number) => json!(number),
        Sql::Real(number) => serde_json::Number::from_f64(number).map(Value::Number).ok_or(Problem::Python)?,
        Sql::Text(text) => Value::String(text),
        Sql::Blob(_) => return Err(Problem::Python),
    })
}

/// `str.title()`: the first cased character of each run upper-cased, the rest lower-cased.
fn title(text: &str) -> String {
    let mut out = String::new();
    let mut previous_cased = false;
    for c in text.chars() {
        let cased = c.is_lowercase() || c.is_uppercase();
        if previous_cased {
            out.extend(c.to_lowercase());
        } else {
            out.extend(c.to_uppercase());
        }
        previous_cased = cased;
    }
    out
}

// ------------------------------------------------------------------------------ sources

/// `safe_error`'s patterns and replacements, in order.
static PATTERNS: LazyLock<[(Regex, &'static str); 4]> = LazyLock::new(|| {
    let compile = |pattern: &str| Regex::new(pattern).expect("a valid redaction pattern");
    [
        (compile(r"(https?://)[^/\s@]+@"), "${1}[redacted]@"),
        (compile(r"(https?://[^\s?#]+)[?#][^\s]*"), "${1}?[redacted]"),
        (compile(r#"(?ix)(\b(?:[\w-]*(?:token|secret|password|api[_-]?key)|authorization)\b
            ["']?\s*[:=]\s*["']?)(?:bearer\s+|basic\s+)?[^\s,;}"']+"#), "${1}[redacted]"),
        (compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), "${1} [redacted]"),
    ]
});

/// `sources.safe_error`.
fn safe_error(detail: Option<&str>, environment: &[(String, String)]) -> String {
    let mut text = detail.filter(|detail| !detail.is_empty()).unwrap_or("no detail").to_owned();
    for (key, value) in environment {
        let key = key.to_uppercase();
        if value.chars().count() >= 4 && ["TOKEN", "SECRET", "PASSWORD", "API_KEY"].iter().any(|word| key.contains(word)) {
            text = text.replace(value.as_str(), "[redacted]");
        }
    }
    for (pattern, replacement) in PATTERNS.iter() {
        text = pattern.replace_all(&text, *replacement).into_owned();
    }
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `sources.recovery_actions`, in their order.
fn recovery_actions(status: Option<&str>, detail: Option<&str>) -> Vec<&'static str> {
    let error = detail.unwrap_or("").to_lowercase();
    let any = |terms: &[&str]| terms.iter().any(|term| error.contains(term));
    let mut actions = Vec::new();
    if error.contains("robots") {
        actions.push("Respect robots restrictions; use a permitted direct source or manual review.");
    }
    if any(&["dns", "nameresolution", "name resolution", "getaddrinfo", "failed to resolve", "name or service not known",
             "nodename nor servname"]) {
        actions.push("Check resolver, network and provider endpoint; use an existing direct source, not full reruns.");
    }
    if any(&["cap reached", "page cap", "query cap", "result cap", "truncat", "pagination limit", "query limit"]) {
        actions.push("Coverage is incomplete; narrow targeted queries or refresh individual postings.");
    }
    if status == Some("rate_limited") || any(&["429", "rate limit", "rate_limit", "too many requests"]) {
        actions.push("Respect Retry-After/cooldown before a targeted later attempt.");
    }
    if any(&["401", "authentication", "authenticated", "unauthorized", "api key", "api_key", "sign in", "login required",
             "credentials"]) {
        actions.push("Setup required: configure authorized access or keys; use permitted manual access meanwhile.");
    }
    if actions.is_empty() {
        actions.push("Retry only this source once later; investigate if the error persists.");
    }
    actions
}

// ------------------------------------------------------------------------------ the digest

/// `today.TEXT_LIMIT`.
const TEXT_LIMIT: usize = 8;

/// `today._section`.
fn section(lines: &mut Vec<String>, heading: &str, items: Vec<String>) {
    if items.is_empty() {
        return;
    }
    lines.push(format!("{heading} ({})", items.len()));
    lines.extend(items.iter().take(TEXT_LIMIT).map(|item| format!("  {item}")));
    if items.len() > TEXT_LIMIT {
        lines.push(format!("  ... and {} more", items.len() - TEXT_LIMIT));
    }
    lines.push(String::new());
}

fn count(conn: &Connection, where_: &str) -> Result2<i64> {
    let sql = format!("SELECT COUNT(*) AS n FROM jobs j JOIN companies c ON c.id = j.company_id LEFT JOIN applications a ON a.job_id = j.id WHERE {where_}");
    Ok(conn.query_row(&sql, [], |row| row.get(0))?)
}

/// `today.due_reminders` as digest lines.
fn reminders(conn: &Connection, day: Date) -> Result2<Vec<String>> {
    let mut statement = conn.prepare(
        "SELECT r.id, r.kind, r.due_at, r.note, COALESCE(r.job_id, a.job_id) AS job_id,
                c.display_name AS company_name, j.title AS title
           FROM reminders r
           LEFT JOIN applications a ON a.id = r.application_id
           LEFT JOIN jobs j         ON j.id = COALESCE(r.job_id, a.job_id)
           LEFT JOIN companies c    ON c.id = j.company_id
          WHERE r.done_at IS NULL AND r.due_at <= ?
            AND NOT (r.kind = 'followup' AND a.status IS NOT NULL AND a.status != 'applied')
          ORDER BY r.due_at, r.id")?;
    let mut rows = statement.query(params![format!("{}T23:59:59Z", day.iso())])?;
    let mut lines = Vec::new();
    while let Some(row) = rows.next()? {
        let (kind, due, note, company, title_value): (Sql, Sql, Sql, Sql, Sql) = (row.get(1)?, row.get(2)?, row.get(3)?, row.get(5)?, row.get(6)?);
        let label = if truthy(&title_value) {
            format!("{} - {}", py_str(&company)?, py_str(&title_value)?)
        } else if truthy(&note) {
            py_str(&note)?
        } else {
            py_str(&kind)?
        };
        let due = text(due)?;
        let mut line = format!("{} {}: {label}", short_date(due.as_deref()), py_str(&kind)?);
        if truthy(&note) && py_str(&note)? != label {
            line.push_str(&format!(" ({})", py_str(&note)?));
        }
        lines.push(line);
    }
    Ok(lines)
}

/// `today.upcoming_deadlines` as digest lines.
fn deadlines(conn: &Connection, day: Date, window: i64) -> Result2<Vec<String>> {
    let last = day.add_days(window);
    let mut statement = conn.prepare(
        "SELECT j.deadline_at, j.source_deadline_at, j.queue_state, c.display_name, j.title
           FROM jobs j
           JOIN companies c     ON c.id = j.company_id
           LEFT JOIN resumes r  ON r.id = j.resume_id
           LEFT JOIN applications a ON a.job_id = j.id
          WHERE j.is_active = 1 AND COALESCE(j.deadline_at, j.source_deadline_at) IS NOT NULL AND a.id IS NULL
          ORDER BY COALESCE(j.deadline_at, j.source_deadline_at) IS NULL, COALESCE(j.deadline_at, j.source_deadline_at), j.id DESC")?;
    let mut rows = statement.query([])?;
    let mut lines = Vec::new();
    while let Some(row) = rows.next()? {
        let (own, source, queue, company, title_value): (Sql, Sql, Sql, Sql, Sql) = (row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?);
        // `Job.deadline_at = row["deadline_at"] or row["source_deadline_at"]`
        let deadline = text(if truthy(&own) { own } else { source })?;
        let Some(stamp) = deadline.as_deref().and_then(parse) else { continue };
        if matches!(&queue, Sql::Text(state) if state == "skipped" || state == "not_eligible") {
            continue;
        }
        if day <= stamp.date && stamp.date <= last {
            lines.push(format!("{} {} - {}", short_date(deadline.as_deref()), py_str(&company)?, py_str(&title_value)?));
        }
    }
    Ok(lines)
}

/// `today.source_problems` as `source_warning(..., detail_limit=160)` lines.
fn source_problems(conn: &Connection, environment: &[(String, String)]) -> Result2<Vec<String>> {
    let mut statement = conn.prepare("SELECT name, enabled, last_status, last_error FROM sources ORDER BY enabled DESC, name")?;
    let mut rows = statement.query([])?;
    let mut lines = Vec::new();
    while let Some(row) = rows.next()? {
        let (name, enabled, status, error): (Sql, Sql, Sql, Sql) = (row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?);
        let status = text(status)?;
        if !truthy(&enabled) || !matches!(status.as_deref(), Some("partial" | "error" | "rate_limited")) {
            continue;
        }
        let error = if truthy(&error) { text(error)? } else { None };
        let mut detail = safe_error(error.as_deref(), environment);
        if detail.chars().count() > 160 {
            detail = format!("{}...", detail.chars().take(157).collect::<String>());
        }
        let actions = recovery_actions(status.as_deref(), error.as_deref()).join(" ");
        lines.push(format!("{}: {} - {detail} Action: {actions}", py_str(&name)?, status.unwrap_or_default()));
    }
    Ok(lines)
}

/// `pipeline.stale_applications` as digest lines.
fn stale(conn: &Connection, days: i64, now: Now) -> Result2<Vec<String>> {
    let mut statement = conn.prepare(
        "SELECT c.display_name, j.title, a.last_activity_at FROM applications a
           JOIN jobs j ON j.id = a.job_id JOIN companies c ON c.id = j.company_id
          WHERE a.status IN ('applied', 'oa') ORDER BY a.last_activity_at")?;
    let mut rows = statement.query([])?;
    let mut lines = Vec::new();
    while let Some(row) = rows.next()? {
        let (company, title_value, activity): (Sql, Sql, Sql) = (row.get(0)?, row.get(1)?, row.get(2)?);
        let quiet = days_since(text(activity)?.as_deref(), now);
        if quiet >= days {
            lines.push(format!("{} - {} ({quiet} days)", py_str(&company)?, py_str(&title_value)?));
        }
    }
    Ok(lines)
}

/// `discovery.openings.opening_soon` as digest lines.
fn opens_soon(conn: &Connection, today: Date) -> Result2<Vec<String>> {
    const CYCLE_START_MONTH: u32 = 7;
    const WINDOW: i64 = 30;
    let cycle_start = |day: Date| Date { year: if day.month >= CYCLE_START_MONTH { day.year } else { day.year - 1 }, month: CYCLE_START_MONTH, day: 1 };
    let this_cycle = cycle_start(today);
    let last_cycle = Date { year: this_cycle.year - 1, ..this_cycle };
    let mut statement = conn.prepare(
        "SELECT c.display_name, c.expected_open_months,
                MIN(CASE WHEN j.first_seen_at >= ? AND j.first_seen_at < ? THEN j.first_seen_at END) AS last_cycle_first_seen,
                COALESCE(MAX(j.is_active), 0) AS has_open
           FROM companies c LEFT JOIN jobs j ON j.company_id = c.id
          WHERE c.from_watchlist = 1
          GROUP BY c.id")?;
    let mut rows = statement.query(params![last_cycle.iso(), this_cycle.iso()])?;
    let mut found: Vec<(Date, String, &'static str)> = Vec::new();
    while let Some(row) = rows.next()? {
        let (company, months, first_seen, open): (Sql, Sql, Sql, Sql) = (row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?);
        if truthy(&open) {
            continue;
        }
        let months_text = text(months)?.filter(|text| !text.is_empty()).unwrap_or_else(|| "[]".into());
        let months: Vec<Value> = serde_json::from_str(&months_text).map_err(|_| Problem::Python)?;
        let estimate = if !months.is_empty() {
            let mut candidates = Vec::new();
            for month in months {
                let month = month.as_i64().filter(|month| (1..=12).contains(month)).ok_or(Problem::Python)? as u32;
                let year = if month >= CYCLE_START_MONTH { this_cycle.year } else { this_cycle.year + 1 };
                candidates.push(Date { year, month, day: 1 });
            }
            candidates.sort();
            let floor = today.add_days(-WINDOW);
            candidates.into_iter().find(|day| *day >= floor).map(|day| (day, "watchlist months"))
        } else {
            text(first_seen)?.as_deref().and_then(parse).map(|stamp| {
                let seen = stamp.date;
                let next = Date::new(seen.year + 1, seen.month, seen.day).unwrap_or(Date { year: seen.year + 1, month: seen.month, day: 28 });
                (next, "last cycle")
            })
        };
        let Some((opens_on, basis)) = estimate else { continue };
        let distance = opens_on.days() - today.days();
        if (-WINDOW..=WINDOW).contains(&distance) {
            found.push((opens_on, py_str(&company)?, basis));
        }
    }
    found.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
    Ok(found.into_iter().map(|(day, company, basis)| format!("{} {company} ({basis})", day.iso())).collect())
}

fn applyai(conn: &Connection, result: &mut Map<String, Value>, settings: DigestSettings, now: Now,
           environment: &[(String, String)]) -> Result2<()> {
    let today = now.day();
    let week = now.week_start_iso();

    // tracks.weekly_targets: active tracks in tracks.yaml order.
    let mut targets: Vec<(String, Sql)> = Vec::new();
    {
        let mut statement = conn.prepare("SELECT name, weekly_target FROM tracks WHERE active = 1 ORDER BY sort_order")?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            let name = text(row.get(0)?)?.ok_or(Problem::Python)?;
            let target: Sql = row.get(1)?;
            match targets.iter_mut().find(|(known, _)| *known == name) {
                Some(entry) => entry.1 = target,
                None => targets.push((name, target)),
            }
        }
    }
    let mut applied: HashMap<Option<String>, i64> = HashMap::new();
    {
        let mut statement = conn.prepare("SELECT track, COUNT(*) AS n FROM applications WHERE applied_at >= ? GROUP BY track")?;
        let mut rows = statement.query(params![week])?;
        while let Some(row) = rows.next()? {
            applied.insert(text(row.get(0)?)?, row.get(1)?);
        }
    }
    let weekly_total: i64 = conn.query_row("SELECT COUNT(*) AS n FROM applications WHERE applied_at >= ?", params![week], |row| row.get(0))?;
    let streak = {
        let mut days = BTreeSet::new();
        let mut statement = conn.prepare("SELECT applied_at FROM applications")?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            if let Some(stamp) = text(row.get(0)?)?.as_deref().and_then(parse) {
                days.insert(stamp.date);
            }
        }
        let mut day = if days.contains(&today) { today } else { today.add_days(-1) };
        let mut streak = 0i64;
        while days.contains(&day) {
            streak += 1;
            day = day.add_days(-1);
        }
        streak
    };

    // today.new_by_track: every active track (sort_order, name), A and B counts.
    let mut new_by_track: Vec<(String, [i64; 2])> = Vec::new();
    {
        let mut statement = conn.prepare("SELECT name FROM tracks WHERE active = 1 ORDER BY sort_order, name")?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            let name = text(row.get(0)?)?.ok_or(Problem::Python)?;
            if !new_by_track.iter().any(|(known, _)| *known == name) {
                new_by_track.push((name, [0, 0]));
            }
        }
        let mut statement = conn.prepare(
            "SELECT jt.track AS track, j.tier AS tier, COUNT(*) AS n
               FROM jobs j JOIN job_tracks jt ON jt.job_id = j.id LEFT JOIN applications a ON a.job_id = j.id
              WHERE j.is_active = 1 AND j.queue_state = 'new' AND a.id IS NULL AND j.tier IN ('A', 'B')
              GROUP BY jt.track, j.tier")?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            let (track, tier, n): (Sql, Sql, i64) = (row.get(0)?, row.get(1)?, row.get(2)?);
            if let (Sql::Text(track), Sql::Text(tier)) = (track, tier) {
                if let Some((_, counts)) = new_by_track.iter_mut().find(|(known, _)| *known == track) {
                    counts[usize::from(tier == "B")] = n;
                }
            }
        }
    }

    let mut lines = vec![format!("kitty today - {}", today.iso()), String::new()];
    section(&mut lines, "Due", reminders(conn, today)?);
    section(&mut lines, "Deadlines soon", deadlines(conn, today, settings.deadline_window_days)?);
    section(&mut lines, "Source problems", source_problems(conn, environment)?);
    section(&mut lines, "Quiet, consider archiving", stale(conn, settings.archive_after_days, now)?);
    section(&mut lines, "Opens soon", opens_soon(conn, today)?);
    let streak_text = if streak != 0 { format!(", streak {streak} day{}", if streak != 1 { "s" } else { "" }) } else { String::new() };
    lines.push(format!("This week: {weekly_total} applied{streak_text}"));
    for (track, [a, b]) in &new_by_track {
        let week = match targets.iter().find(|(name, _)| name == track) {
            Some((_, target)) => format!("{}/{} this week", applied.get(&Some(track.clone())).copied().unwrap_or(0), py_str(target)?),
            None => "-".into(),
        };
        lines.push(format!("  {track}: {week}, new A {a} B {b}"));
    }

    let tracks = targets.into_iter().map(|(name, target)| {
        Ok(json!({ "name": name, "label": title(&name.replace('_', " ")), "weekly_target": json(target)? }))
    }).collect::<Result2<Vec<Value>>>()?;
    result.insert("tracks".into(), Value::Array(tracks));
    result.insert("counts".into(), json!({
        "jobs": count(conn, "j.is_active = 1")?,
        "queued": count(conn, "j.is_active = 1 AND j.queue_state IN ('queued') AND a.id IS NULL")?,
        "applications": count(conn, "a.id IS NOT NULL")?,
        "favorites": count(conn, "j.favorited_at IS NOT NULL")?,
    }));
    result.insert("weekly_total".into(), json!(weekly_total));
    result.insert("streak_days".into(), json!(streak));
    result.insert("today".into(), Value::String(lines.join("\n")));
    Ok(())
}

/// Hidden and test-only: `rika-server bootstrap-digest DB ROOT DEADLINE_DAYS ARCHIVE_DAYS`
/// prints the ApplyAI half of the answer, for tests/test_web_bootstrap.py.
pub fn digest_command(args: &[String]) -> i32 {
    let [db, root, window, archive] = args else {
        eprintln!("usage: rika-server bootstrap-digest DB ROOT DEADLINE_DAYS ARCHIVE_DAYS");
        return 2;
    };
    let (Ok(window), Ok(archive)) = (window.parse(), archive.parse()) else {
        eprintln!("the day counts are integers");
        return 2;
    };
    let digest = DigestSettings { deadline_window_days: window, archive_after_days: archive };
    let bootstrap = Bootstrap::new(db.into(), root.into(), false, Some(digest));
    let conn = match Connection::open(&bootstrap.db_path) {
        Ok(conn) => conn,
        Err(error) => {
            eprintln!("{error}");
            return 1;
        }
    };
    let mut result = Map::new();
    match applyai(&conn, &mut result, digest, Now::current(), &bootstrap.environment()) {
        Ok(()) => {
            println!("{}", Value::Object(result));
            0
        }
        Err(Problem::Sql(error)) => {
            eprintln!("{error}");
            1
        }
        Err(Problem::Python) => {
            eprintln!("a value Python could not have used");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stored_timestamps_parse_as_fromisoformat() {
        let day = |text: &str| parse(text).map(|stamp| stamp.date.iso());
        assert_eq!(day("2026-11-12T10:00:00Z").as_deref(), Some("2026-11-12"));
        assert_eq!(day("2026-11-12").as_deref(), Some("2026-11-12"));
        assert_eq!(day("20261112").as_deref(), Some("2026-11-12"));
        assert_eq!(day("2026-11-12 23:30:00-05:00").as_deref(), Some("2026-11-12"));
        assert_eq!(day("2026-11-12T24:00:00").as_deref(), Some("2026-11-13"));
        assert_eq!(day("2026-11-12T10:00:00.5+01:00").as_deref(), Some("2026-11-12"));
        assert!(parse("2026-02-30").is_none() && parse("soon").is_none() && parse("2026-11-12T25:00").is_none());
        let utc = parse("2026-11-12T00:00:00+01:00").unwrap();
        assert_eq!(utc.micros, parse("2026-11-11T23:00:00Z").unwrap().micros);
    }

    #[test]
    fn weeks_start_on_monday() {
        // 2026-11-12 is a Thursday.
        let thursday = Date::new(2026, 11, 12).unwrap();
        assert_eq!(thursday.weekday(), 3);
        assert_eq!(Date::from_days(thursday.days()), thursday);
        let now = Now { micros: thursday.days() * 86_400_000_000 + 1 };
        assert_eq!(now.week_start_iso(), "2026-11-09T00:00:00Z");
    }

    #[test]
    fn python_text_helpers() {
        assert_eq!(title("part time 2nd"), "Part Time 2Nd");
        assert_eq!(py_float(1.0), "1.0");
        assert_eq!(py_float(1e16), "1e+16");
        assert_eq!(py_float(0.0001), "0.0001");
        assert_eq!(py_float(0.00001), "1e-05");
        assert_eq!(safe_error(Some("GET https://user:pw@example.invalid/x?token=abc failed  "), &[]),
                   "GET https://[redacted]@example.invalid/x?[redacted] failed");
        assert_eq!(safe_error(Some("Authorization: Bearer abc.def"), &[]), "Authorization: [redacted]");
        assert_eq!(safe_error(None, &[]), "no detail");
    }
}
