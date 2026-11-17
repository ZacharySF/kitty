//! `GET /api/reports` (`kitty.core.stats.response_report` and `report_summary`) and the export
//! download (`kitty.core.export`'s tables as one JSON document, a CSV file or a zip of CSVs).

use std::collections::BTreeSet;
use std::io::Write;

use indexmap::IndexMap;
use rusqlite::types::ValueRef;
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::ported::time::{fromisoformat, Date, Dt};
use crate::ported::{fail, internal, py, Done};

/// `export.QUERIES`, in order.
pub const QUERIES: [(&str, &str); 3] = [
    ("jobs", "
        SELECT j.*, c.display_name AS company,
               (SELECT group_concat(jt.track, ';') FROM job_tracks jt WHERE jt.job_id = j.id) AS tracks,
               (SELECT group_concat(js.source, ';') FROM job_sources js WHERE js.job_id = j.id) AS sources
          FROM jobs j
          LEFT JOIN companies c ON c.id = j.company_id
         ORDER BY j.id
    "),
    ("applications", "
        SELECT a.*, c.display_name AS company, j.title AS title, j.url AS url, r.label AS resume
          FROM applications a
          JOIN jobs j ON j.id = a.job_id
          LEFT JOIN companies c ON c.id = j.company_id
          LEFT JOIN resumes r ON r.id = a.resume_id
         ORDER BY a.id
    "),
    ("status_events", "
        SELECT e.*, a.job_id AS job_id, c.display_name AS company, j.title AS title
          FROM status_events e
          JOIN applications a ON a.id = e.application_id
          JOIN jobs j ON j.id = a.job_id
          LEFT JOIN companies c ON c.id = j.company_id
         ORDER BY e.id
    "),
];

/// `models.OPEN_STATUSES`.
const OPEN: [&str; 4] = ["applied", "oa", "interview", "offer"];
const ADVANCED: [&str; 3] = ["oa", "interview", "offer"];

/// `kitty.dates.parse(value)`: the stored stamp (or bare date), else None. Python calls
/// `value.strip()`, so a stored number other than zero raised (a 500).
pub fn parse(value: ValueRef<'_>) -> Done<Option<Dt>> {
    let text = match value {
        ValueRef::Null => return Ok(None),
        ValueRef::Integer(0) => return Ok(None),
        ValueRef::Real(number) if number == 0.0 => return Ok(None),
        ValueRef::Text(text) => std::str::from_utf8(text).map_err(|_| internal())?,
        _ => return Err(internal()),
    };
    if text.is_empty() {
        return Ok(None);
    }
    Ok(fromisoformat(&py::strip(text).replace('Z', "+00:00")))
}

/// `kitty.dates.now().date()`.
fn today() -> Date {
    Dt::now_utc().date()
}

/// `tracks.weekly_targets(conn)` summed (a later duplicate name replaces an earlier one).
fn weekly_target(conn: &Connection) -> Done<i64> {
    let mut statement = conn.prepare("SELECT name, weekly_target FROM tracks WHERE active = 1 ORDER BY sort_order")?;
    let mut rows = statement.query([])?;
    let mut targets: IndexMap<String, i64> = IndexMap::new();
    while let Some(row) = rows.next()? {
        let name: String = row.get(0).map_err(|_| internal())?;
        let target = match row.get_ref(1)? {
            ValueRef::Integer(number) => number,
            _ => return Err(internal()),
        };
        targets.insert(name, target);
    }
    targets.values().try_fold(0_i64, |sum, value| sum.checked_add(*value)).ok_or_else(internal)
}

/// One column's dates as `parse(...).date()`, unparseable stamps skipped.
fn dates(conn: &Connection, sql: &str) -> Done<Vec<Date>> {
    let mut statement = conn.prepare(sql)?;
    let mut rows = statement.query([])?;
    let mut out = Vec::new();
    while let Some(row) = rows.next()? {
        if let Some(stamp) = parse(row.get_ref(0)?)? {
            out.push(stamp.date());
        }
    }
    Ok(out)
}

/// `stats.report_summary(conn)`: the last eight weeks, this week and this month so far.
pub fn summary(conn: &Connection) -> Done<Value> {
    const WEEKS: i64 = 8;
    let day = today();
    let shift = |date: Date, days: i64| date.add_days(days).ok_or_else(internal);
    let monday = shift(day, -i64::from(day.weekday()))?;
    let first = shift(monday, -7 * (WEEKS - 1))?;
    let month = Date { day: 1, ..day };
    let last_month = shift(month, -1)?;
    let previous = Date { day: 1, ..last_month };
    let every = dates(conn, "SELECT applied_at FROM applications")?;
    let applied: Vec<Date> = every.iter().copied().filter(|when| *when <= day).collect();
    let mut per_week = vec![0_i64; WEEKS as usize];
    for when in &applied {
        if *when >= first {
            per_week[((when.days() - first.days()) / 7) as usize] += 1;
        }
    }
    let mut events: Vec<(i64, String, Date)> = Vec::new();
    {
        let mut statement = conn.prepare(
            "
        SELECT application_id, to_status, occurred_at FROM status_events
         WHERE state = 'confirmed' AND undoes_event_id IS NULL
           AND id NOT IN (SELECT undoes_event_id FROM status_events WHERE undoes_event_id IS NOT NULL)
        ",
        )?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            if let Some(stamp) = parse(row.get_ref(2)?)? {
                let application: i64 = row.get(0).map_err(|_| internal())?;
                let status: String = row.get(1).map_err(|_| internal())?;
                events.push((application, status, stamp.date()));
            }
        }
    }
    let period = |start: Date| -> Map<String, Value> {
        let inside: Vec<(i64, &str)> = events.iter().filter(|(_, _, when)| start <= *when && *when <= day)
            .map(|(app, status, _)| (*app, status.as_str())).collect();
        let distinct = |keep: &dyn Fn(&str) -> bool| inside.iter().filter(|(_, status)| keep(status)).map(|(app, _)| *app).collect::<BTreeSet<_>>().len();
        let mut out = Map::new();
        out.insert("start".into(), json!(start.iso()));
        out.insert("applied".into(), json!(applied.iter().filter(|when| **when >= start).count()));
        out.insert("replies".into(), json!(distinct(&|status| ADVANCED.contains(&status) || status == "rejected")));
        out.insert("advanced".into(), json!(distinct(&|status| ADVANCED.contains(&status))));
        out.insert("offers".into(), json!(distinct(&|status| status == "offer")));
        out
    };
    // `streak_days(conn, today=day)`: every recorded day, a today without one not breaking it.
    let applied_days: BTreeSet<Date> = every.into_iter().collect();
    let mut cursor = if applied_days.contains(&day) { day } else { shift(day, -1)? };
    let mut streak = 0;
    while applied_days.contains(&cursor) {
        streak += 1;
        cursor = shift(cursor, -1)?;
    }
    let weeks: Vec<Value> = per_week.iter().enumerate()
        .map(|(index, count)| Ok(json!({"start": shift(first, 7 * index as i64)?.iso(), "applied": count})))
        .collect::<Done<_>>()?;
    let mut month_period = period(month);
    month_period.insert("previous_applied".into(), json!(applied.iter().filter(|when| previous <= **when && **when < month).count()));
    Ok(json!({
        "weeks": weeks,
        "weekly_total": per_week[WEEKS as usize - 1],
        "weekly_target": weekly_target(conn)?,
        "streak_days": streak,
        "week": Value::Object(period(monday)),
        "month": Value::Object(month_period),
    }))
}

/// `stats.REPORT_GROUPS`: each grouping's query, in the order they are shown.
const GROUPS: [(&str, &str); 4] = [
    ("resume", "
        SELECT COALESCE(r.label, '(none)') AS key, a.status AS status, COUNT(*) AS n
          FROM applications a
          LEFT JOIN resumes r ON r.id = a.resume_id
         GROUP BY key, a.status
        "),
    ("track", "
        SELECT COALESCE(a.track, '(none)') AS key, a.status AS status, COUNT(*) AS n
          FROM applications a
         GROUP BY key, a.status
        "),
    ("source", "
        SELECT js.source AS key, a.status AS status, COUNT(*) AS n
          FROM applications a
          JOIN job_sources js ON js.job_id = a.job_id
         GROUP BY key, a.status
        "),
    ("tier", "
        SELECT COALESCE(j.tier, 'untriaged') AS key, a.status AS status, COUNT(*) AS n
          FROM applications a
          JOIN jobs j ON j.id = a.job_id
         GROUP BY key, a.status
        "),
];

/// `_response_rates`: outcome counts per key, most applications first, then by key.
fn rates(conn: &Connection, sql: &str) -> Done<Vec<Value>> {
    let mut statement = conn.prepare(sql)?;
    let mut rows = statement.query([])?;
    let mut grouped: IndexMap<String, IndexMap<String, i64>> = IndexMap::new();
    while let Some(row) = rows.next()? {
        // `row["key"] or "(none)"`: an empty key (or NULL) is "(none)".
        let key = match row.get_ref(0)? {
            ValueRef::Null => "(none)".to_owned(),
            ValueRef::Text(text) if text.is_empty() => "(none)".to_owned(),
            ValueRef::Text(text) => String::from_utf8(text.to_vec()).map_err(|_| internal())?,
            _ => return Err(internal()),
        };
        let status: String = row.get(1).map_err(|_| internal())?;
        let count: i64 = row.get(2).map_err(|_| internal())?;
        *grouped.entry(key).or_default().entry(status).or_insert(0) += count;
    }
    let mut results: Vec<(String, i64, i64, i64, i64, i64)> = grouped.into_iter().map(|(key, counts)| {
        let get = |name: &str| counts.get(name).copied().unwrap_or(0);
        let total: i64 = counts.values().sum();
        let advanced = get("oa") + get("interview") + get("offer");
        let open = counts.iter().filter(|(status, _)| OPEN.contains(&status.as_str())).map(|(_, n)| n).sum();
        (key, total, advanced + get("rejected"), advanced, get("offer"), open)
    }).collect();
    results.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    Ok(results.into_iter().map(|(key, applications, responses, advanced, offers, open)| {
        let rate = |count: i64| if applications != 0 { count as f64 / applications as f64 } else { 0.0 };
        json!({"key": key, "applications": applications, "responses": responses, "advanced": advanced, "offers": offers,
               "open_count": open, "response_rate": rate(responses), "advance_rate": rate(advanced)})
    }).collect())
}

/// `GET /api/reports`.
pub fn report(conn: &Connection) -> Done<Value> {
    let mut sections = Map::new();
    for (name, sql) in GROUPS {
        sections.insert(name.into(), Value::Array(rates(conn, sql)?));
    }
    Ok(json!({"sections": sections, "summary": summary(conn)?,
              "datasets": QUERIES.iter().map(|(name, _)| *name).collect::<Vec<_>>()}))
}

// ------------------------------------------------------------------------------ export

/// One exported cell as Python's `sqlite3` returns it.
enum Cell {
    Null,
    Int(i64),
    Real(f64),
    Text(String),
    Blob(Vec<u8>),
}

/// `export.table_rows`: the column names and every row.
fn table_rows(conn: &Connection, table: &str) -> Done<(Vec<String>, Vec<Vec<Cell>>)> {
    let sql = QUERIES.iter().find(|(name, _)| *name == table).ok_or_else(internal)?.1;
    let mut statement = conn.prepare(sql)?;
    let columns: Vec<String> = statement.column_names().into_iter().map(str::to_owned).collect();
    let mut rows = statement.query([])?;
    let mut out = Vec::new();
    while let Some(row) = rows.next()? {
        let mut cells = Vec::with_capacity(columns.len());
        for index in 0..columns.len() {
            cells.push(match row.get_ref(index)? {
                ValueRef::Null => Cell::Null,
                ValueRef::Integer(number) => Cell::Int(number),
                ValueRef::Real(number) => Cell::Real(number),
                ValueRef::Text(text) => Cell::Text(String::from_utf8(text.to_vec()).map_err(|_| internal())?),
                ValueRef::Blob(bytes) => Cell::Blob(bytes.to_vec()),
            });
        }
        out.push(cells);
    }
    Ok((columns, out))
}

/// `json.dumps(text, ensure_ascii=False)`.
fn json_text(text: &str, out: &mut String) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `json.dumps(document, ensure_ascii=False, indent=2)` of `{"exported_at", <table>: [rows]}`;
/// each row is `dict(zip(columns, row))` (a repeated column keeps its first place, last value).
fn json_document(conn: &Connection, tables: &[&str], exported_at: &str) -> Done<Vec<u8>> {
    let mut out = String::from("{\n  \"exported_at\": ");
    json_text(exported_at, &mut out);
    for table in tables {
        let (columns, rows) = table_rows(conn, table)?;
        out.push_str(",\n  ");
        json_text(table, &mut out);
        out.push_str(": ");
        if rows.is_empty() {
            out.push_str("[]");
            continue;
        }
        out.push('[');
        for (index, row) in rows.iter().enumerate() {
            let mut record: IndexMap<&str, &Cell> = IndexMap::new();
            for (name, cell) in columns.iter().zip(row) {
                record.insert(name, cell);
            }
            out.push_str(if index == 0 { "\n    " } else { ",\n    " });
            if record.is_empty() {
                out.push_str("{}");
                continue;
            }
            out.push('{');
            for (position, (name, cell)) in record.iter().enumerate() {
                out.push_str(if position == 0 { "\n      " } else { ",\n      " });
                json_text(name, &mut out);
                out.push_str(": ");
                match cell {
                    Cell::Null => out.push_str("null"),
                    Cell::Int(number) => out.push_str(&number.to_string()),
                    Cell::Real(number) => out.push_str(&py::float_repr(*number)),
                    Cell::Text(text) => json_text(text, &mut out),
                    // `bytes` is not JSON serializable: Python's answer failed.
                    Cell::Blob(_) => return Err(internal()),
                }
            }
            out.push_str("\n    }");
        }
        out.push_str("\n  ]");
    }
    out.push_str("\n}");
    Ok(out.into_bytes())
}

/// `str(value)` of a `bytes` cell: `repr(bytes)`.
fn bytes_repr(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') { '"' } else { '\'' };
    let mut out = format!("b{quote}");
    for &b in bytes {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b if b == quote as u8 => {
                out.push('\\');
                out.push(quote);
            }
            0x20..=0x7e => out.push(b as char),
            _ => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote);
    out
}

/// One field as `csv.writer` (excel dialect, QUOTE_MINIMAL) writes it.
fn csv_field(text: &str, out: &mut String) {
    if text.contains([',', '"', '\r', '\n']) {
        out.push('"');
        out.push_str(&text.replace('"', "\"\""));
        out.push('"');
    } else {
        out.push_str(text);
    }
}

fn csv_row(fields: &[String], out: &mut String) {
    // A row of one empty field is written `""` (an empty line would read as no row).
    if let [only] = fields {
        if only.is_empty() {
            out.push_str("\"\"\r\n");
            return;
        }
    }
    for (index, field) in fields.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        csv_field(field, out);
    }
    out.push_str("\r\n");
}

/// One table as CSV: the header, then every row with `export.escape_cell`'s formula guard.
fn csv_bytes(conn: &Connection, table: &str) -> Done<Vec<u8>> {
    let (columns, rows) = table_rows(conn, table)?;
    let mut out = String::new();
    csv_row(&columns, &mut out);
    for row in rows {
        let fields: Vec<String> = row.into_iter().map(|cell| match cell {
            Cell::Null => String::new(),
            Cell::Int(number) => number.to_string(),
            Cell::Real(number) => float_str(number),
            Cell::Text(text) if text.starts_with(['=', '+', '-', '@']) => format!("'{text}"),
            Cell::Text(text) => text,
            Cell::Blob(bytes) => bytes_repr(&bytes),
        }).collect();
        csv_row(&fields, &mut out);
    }
    Ok(out.into_bytes())
}

/// `str(float)`: `repr`, with Python's names for the special values.
fn float_str(number: f64) -> String {
    if number.is_nan() {
        "nan".into()
    } else if number.is_infinite() {
        if number > 0.0 { "inf".into() } else { "-inf".into() }
    } else {
        py::float_repr(number)
    }
}

/// `kitty.dates.now_iso()`: whole seconds with a `Z`.
pub fn now_z() -> String {
    let now = Dt::now_utc();
    let rest = now.time_of_day() / crate::ported::time::SECOND;
    format!("{}T{:02}:{:02}:{:02}Z", now.date().iso(), rest / 3600, rest % 3600 / 60, rest % 60)
}

/// `web_insights.download`: (bytes, content type, file name) for `?format=&dataset=`.
pub fn export(conn: &Connection, query: &[(String, String)]) -> Done<(Vec<u8>, &'static str, String)> {
    if query.iter().any(|(name, _)| name != "format" && name != "dataset") {
        return Err(fail(400, "Unknown parameter"));
    }
    let get = |name: &str, default: &'static str| query.iter().find(|(key, _)| key == name).map_or(default, |(_, value)| value.as_str());
    let (format, dataset) = (get("format", "json"), get("dataset", "all"));
    let known = QUERIES.iter().any(|(name, _)| *name == dataset);
    if !matches!(format, "json" | "csv") || !(dataset == "all" || known) {
        return Err(fail(400, "Unknown export format or dataset"));
    }
    let tables: Vec<&str> = if dataset == "all" { QUERIES.iter().map(|(name, _)| *name).collect() } else { vec![dataset] };
    if format == "json" {
        return Ok((json_document(conn, &tables, &now_z())?, "application/json", format!("kitty-{dataset}.json")));
    }
    if dataset != "all" {
        return Ok((csv_bytes(conn, dataset)?, "text/csv; charset=utf-8", format!("{dataset}.csv")));
    }
    let mut archive = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated)
        .unix_permissions(0o600)
        .last_modified_time(local_stamp());
    for table in &tables {
        let data = csv_bytes(conn, table)?;
        archive.start_file(format!("{table}.csv"), options).map_err(|_| internal())?;
        archive.write_all(&data).map_err(|_| internal())?;
    }
    let cursor = archive.finish().map_err(|_| internal())?;
    Ok((cursor.into_inner(), "application/zip", "kitty-all-csv.zip".into()))
}

/// `ZipInfo`'s stamp for `writestr`: `time.localtime(time.time())`, the computer's wall clock.
fn local_stamp() -> zip::DateTime {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |elapsed| elapsed.as_secs());
    let seconds = libc::time_t::try_from(now).unwrap_or(0);
    // SAFETY: `localtime_r` writes only into the `tm` it is given.
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    if unsafe { libc::localtime_r(&seconds, &mut tm) }.is_null() {
        return zip::DateTime::default();
    }
    zip::DateTime::from_date_and_time((tm.tm_year + 1900) as u16, (tm.tm_mon + 1) as u8, tm.tm_mday as u8,
                                      tm.tm_hour as u8, tm.tm_min as u8, tm.tm_sec as u8)
        .unwrap_or_default()
}
