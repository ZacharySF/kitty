//! `kitty.core.builds.project`: the build's settings in SQLite, a projection of the file.
//!
//! The same rows (`ai_settings`, `note_preferences`, `study_settings`, `opportunity_settings`,
//! `tag_settings`, `build_state`), the same UPDATE statements with their revision bumps, the
//! same one transaction per change, so every other reader (Python's AI budget check, the study
//! timer, the monitoring scheduler, the tagger, history compaction, pairing) sees exactly what
//! the Python projection wrote. tests/test_web_build.py compares the rows with Python's.

use std::path::Path;
use std::time::Duration;

use rusqlite::types::Value as Sql;
use rusqlite::{params_from_iter, Connection, OpenFlags, TransactionBehavior};

use super::clock::Moment;
use super::document::{self, Problem};
use super::failure::Failure;
use super::store::{self, changed, parse_document, section, Store};
use super::value::{py_eq, scalar, text, Dict, Int, Text, Value};

/// `project.Synced`: the build a process should use now.
#[derive(Clone)]
pub struct Synced {
    /// The build file's text and its revision, as on disk (may be invalid).
    pub text: Text,
    pub revision: String,
    /// The complete active build: the file's when valid, else the last good one.
    pub build: Value,
    /// The revision of the text `build` came from.
    pub active_revision: String,
    /// Problems in the file on disk (errors mean `build` is the last good one).
    pub problems: Vec<Problem>,
}

impl Synced {
    pub fn invalid(&self) -> bool {
        self.problems.iter().any(|p| p.severity == "error")
    }
}

/// `kitty.db.connect`: the pragmas every connection to the workspace database gets.
pub fn open(db_path: &Path) -> Result<Connection, Failure> {
    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    conn.execute_batch("PRAGMA foreign_keys = ON")?;
    conn.query_row("PRAGMA journal_mode = WAL", [], |_| Ok(()))?;
    conn.execute_batch("PRAGMA synchronous = NORMAL")?;
    conn.busy_timeout(Duration::from_millis(5000))?;
    Ok(conn)
}

fn begin(conn: &mut Connection) -> Result<rusqlite::Transaction<'_>, Failure> {
    Ok(conn.transaction_with_behavior(TransactionBehavior::Immediate)?)
}

/// `build[a][b]...`; a missing key is Python's KeyError (a 500).
fn at<'a>(build: &'a Value, path: &[&str]) -> Result<&'a Value, Failure> {
    path.iter().try_fold(build, |node, key| node.as_dict().and_then(|d| d.get_str(key)).ok_or_else(Failure::internal))
}

/// A build value as `sqlite3` binds it (a bool is the int it is; other types raise).
fn bind(value: &Value) -> Result<Sql, Failure> {
    Ok(match value {
        Value::Null => Sql::Null,
        Value::Bool(b) => Sql::Integer(i64::from(*b)),
        Value::Int(Int::Small(i)) => Sql::Integer(i64::try_from(*i).map_err(|_| Failure::internal())?),
        Value::Float(f) => Sql::Real(*f),
        Value::Str(s) => Sql::Text(scalar(s).ok_or_else(Failure::internal)?),
        _ => return Err(Failure::internal()),
    })
}

/// A column as Python's `sqlite3` returns it, as a JSON value.
fn value_of(sql: &Sql) -> Result<Value, Failure> {
    Ok(match sql {
        Sql::Null => Value::Null,
        Sql::Integer(i) => Value::Int(Int::Small(i128::from(*i))),
        Sql::Real(f) => Value::Float(*f),
        Sql::Text(s) => Value::Str(text(s)),
        Sql::Blob(_) => return Err(Failure::internal()),
    })
}

/// Python's `bool(column)`.
fn truthy(sql: &Sql) -> bool {
    match sql {
        Sql::Null => false,
        Sql::Integer(i) => *i != 0,
        Sql::Real(f) => *f != 0.0,
        Sql::Text(s) => !s.is_empty(),
        Sql::Blob(b) => !b.is_empty(),
    }
}

/// Python's `bool(value)` of a build value.
fn value_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Int(Int::Small(i)) => *i != 0,
        Value::Int(Int::Big { .. }) => true,
        Value::Float(f) => *f != 0.0,
        Value::Str(s) => !s.is_empty(),
        Value::List(items) => !items.is_empty(),
        Value::Dict(d) => d.len() > 0,
    }
}

/// `float(Decimal(column))`.
fn decimal_float(sql: &Sql) -> Result<f64, Failure> {
    match sql {
        Sql::Integer(i) => Ok(*i as f64),
        Sql::Real(f) => Ok(*f),
        Sql::Text(s) => s.trim().replace('_', "").parse().map_err(|_| Failure::internal()),
        _ => Err(Failure::internal()),
    }
}

fn row(conn: &Connection, sql: &str) -> Result<Vec<Sql>, Failure> {
    conn.query_row(sql, [], |row| (0..row.as_ref().column_count()).map(|i| row.get::<_, Sql>(i)).collect())
        .map_err(|error| match error {
            rusqlite::Error::QueryReturnedNoRows => Failure::internal(),
            other => other.into(),
        })
}

fn dict(entries: Vec<(&str, Value)>) -> Value {
    let mut out = Dict::new();
    for (key, value) in entries {
        out.insert(text(key), value);
    }
    Value::Dict(out)
}

/// `settings_from_rows`: the build's `settings` as the SQLite rows hold them now.
pub fn settings_from_rows(conn: &Connection) -> Result<Value, Failure> {
    let ai = row(conn, concat!("SELECT model, routing, max_output_tokens, daily_budget_usd, rika_fast_model, rika_standard_model,",
        " rika_deep_model FROM ai_settings WHERE id=1"))?;
    let notes = row(conn, "SELECT allow_ai, retention_days FROM note_preferences WHERE id=1")?;
    let study = row(conn, "SELECT focus_seconds, short_break_seconds, sound, notifications FROM study_settings WHERE id=1")?;
    let monitoring = row(conn, "SELECT enabled, interval_hours FROM opportunity_settings WHERE id=1")?;
    let tags = row(conn, "SELECT mode FROM tag_settings WHERE id=1")?;
    let state = row(conn, "SELECT history_retention_days, tern_plugin FROM build_state WHERE id=1")?;
    Ok(dict(vec![
        ("ai", dict(vec![
            ("model", value_of(&ai[0])?), ("routing", value_of(&ai[1])?), ("max_output_tokens", value_of(&ai[2])?),
            ("daily_budget_usd", Value::Float(decimal_float(&ai[3])?)), ("rika_fast_model", value_of(&ai[4])?),
            ("rika_standard_model", value_of(&ai[5])?), ("rika_deep_model", value_of(&ai[6])?),
        ])),
        ("notes", dict(vec![("allow_ai", Value::Bool(truthy(&notes[0]))), ("attachment_retention_days", value_of(&notes[1])?)])),
        ("history", dict(vec![("retention_days", value_of(&state[0])?)])),
        ("tags", dict(vec![("mode", value_of(&tags[0])?)])),
        ("study", dict(vec![
            ("focus_seconds", value_of(&study[0])?), ("short_break_seconds", value_of(&study[1])?),
            ("sound", Value::Bool(truthy(&study[2]))), ("notifications", Value::Bool(truthy(&study[3]))),
        ])),
        ("opportunities", dict(vec![("monitoring", Value::Bool(truthy(&monitoring[0]))), ("interval_hours", value_of(&monitoring[1])?)])),
        ("tern", dict(vec![("plugin", Value::Bool(truthy(&state[1])))])),
    ]))
}

fn execute(conn: &Connection, sql: &str, values: Vec<Sql>) -> Result<usize, Failure> {
    Ok(conn.execute(sql, params_from_iter(values))?)
}

/// `apply`: write a complete build's settings into the rows (inside the caller's transaction).
/// A row's `revision` moves only when one of its values changes.
pub fn apply(conn: &Connection, build: &Value) -> Result<(), Failure> {
    let settings = at(build, &["settings"])?;
    let ai = |key: &str| at(settings, &["ai", key]);
    let budget = document::cents_text(ai("daily_budget_usd")?).ok_or_else(Failure::internal)?;
    let values = vec![
        bind(ai("model")?)?, bind(ai("routing")?)?, bind(ai("max_output_tokens")?)?, Sql::Text(budget),
        bind(ai("rika_fast_model")?)?, bind(ai("rika_standard_model")?)?, bind(ai("rika_deep_model")?)?,
    ];
    execute(conn,
        concat!("UPDATE ai_settings SET model=?, routing=?, max_output_tokens=?, daily_budget_usd=?, rika_fast_model=?,",
                " rika_standard_model=?, rika_deep_model=?, revision=revision+1 WHERE id=1 AND (model IS NOT ? OR routing IS NOT ?",
                " OR max_output_tokens IS NOT ? OR daily_budget_usd IS NOT ? OR rika_fast_model IS NOT ? OR rika_standard_model IS NOT ?",
                " OR rika_deep_model IS NOT ?)"),
        [values.clone(), values].concat())?;
    let notes = |key: &str| at(settings, &["notes", key]);
    let values = vec![Sql::Integer(i64::from(value_truthy(notes("allow_ai")?))), bind(notes("attachment_retention_days")?)?];
    execute(conn,
        "UPDATE note_preferences SET allow_ai=?, retention_days=?, revision=revision+1 \
         WHERE id=1 AND (allow_ai IS NOT ? OR retention_days IS NOT ?)",
        [values.clone(), values].concat())?;
    let timer = |key: &str| at(settings, &["study", key]);
    let values = vec![
        bind(timer("focus_seconds")?)?, bind(timer("short_break_seconds")?)?,
        Sql::Integer(i64::from(value_truthy(timer("sound")?))), Sql::Integer(i64::from(value_truthy(timer("notifications")?))),
    ];
    let changed = execute(conn,
        "UPDATE study_settings SET focus_seconds=?, short_break_seconds=?, sound=?, notifications=?, revision=revision+1 \
         WHERE id=1 AND (focus_seconds IS NOT ? OR short_break_seconds IS NOT ? OR sound IS NOT ? OR notifications IS NOT ?)",
        [values.clone(), values].concat())?;
    if changed > 0 {
        settle_idle_timer(conn)?;
    }
    let monitoring = at(settings, &["opportunities", "monitoring"])?;
    let interval = at(settings, &["opportunities", "interval_hours"])?;
    let current = row(conn, "SELECT enabled, interval_hours FROM opportunity_settings WHERE id=1")?;
    let same = py_eq(&Value::Bool(truthy(&current[0])), monitoring) && py_eq(&value_of(&current[1])?, interval);
    if !same {
        let on = value_truthy(monitoring);
        execute(conn, "UPDATE opportunity_settings SET enabled=?, interval_hours=?, next_run=?, revision=revision+1 WHERE id=1",
                vec![Sql::Integer(i64::from(on)), bind(interval)?, if on { Sql::Text(Moment::now().isoformat()) } else { Sql::Null }])?;
    }
    let mode = bind(at(settings, &["tags", "mode"])?)?;
    execute(conn, "UPDATE tag_settings SET mode=?, revision=revision+1 WHERE id=1 AND mode IS NOT ?", vec![mode.clone(), mode])?;
    execute(conn, "UPDATE build_state SET history_retention_days=?, tern_plugin=? WHERE id=1",
            vec![bind(at(settings, &["history", "retention_days"])?)?,
                 Sql::Integer(i64::from(value_truthy(at(settings, &["tern", "plugin"])?)))])?;
    Ok(())
}

/// `study.settle_idle_timer`: an idle timer takes its phase's configured length; the whole row is
/// written back with its revision bumped, as `_save_timer` does.
fn settle_idle_timer(conn: &Connection) -> Result<(), Failure> {
    let (names, mut values) = named_row(conn, "SELECT * FROM study_timer WHERE id=1")?;
    let column = |name: &str| names.iter().position(|n| n == name).ok_or_else(Failure::internal);
    if values[column("status")?] != Sql::Text("idle".into()) {
        return Ok(());
    }
    let duration = match &values[column("phase")?] {
        Sql::Text(phase) if phase == "priming" => Sql::Integer(300),
        Sql::Text(phase) => {
            let (settings_names, settings) = named_row(conn, "SELECT * FROM study_settings WHERE id=1")?;
            let key = format!("{phase}_seconds");
            let index = settings_names.iter().position(|n| *n == key).ok_or_else(Failure::internal)?;
            settings[index].clone()
        }
        _ => return Err(Failure::internal()),
    };
    values[column("duration_seconds")?] = duration;
    let revision = column("revision")?;
    values[revision] = match values[revision] {
        Sql::Integer(i) => Sql::Integer(i.checked_add(1).ok_or_else(Failure::internal)?),
        Sql::Real(f) => Sql::Real(f + 1.0),
        _ => return Err(Failure::internal()),
    };
    let (columns, bound): (Vec<&String>, Vec<Sql>) = names.iter().zip(values).filter(|(name, _)| *name != "id").unzip();
    let sql = format!("UPDATE study_timer SET {} WHERE id=1", columns.iter().map(|c| format!("{c}=?")).collect::<Vec<_>>().join(","));
    execute(conn, &sql, bound)?;
    Ok(())
}

fn named_row(conn: &Connection, sql: &str) -> Result<(Vec<String>, Vec<Sql>), Failure> {
    let mut statement = conn.prepare(sql)?;
    let names: Vec<String> = statement.column_names().into_iter().map(str::to_owned).collect();
    let values = statement.query_row([], |row| (0..names.len()).map(|i| row.get::<_, Sql>(i)).collect::<Result<Vec<_>, _>>())
        .map_err(|error| match error {
            rusqlite::Error::QueryReturnedNoRows => Failure::internal(),
            other => other.into(),
        })?;
    Ok((names, values))
}

/// `_record`.
fn record(conn: &Connection, text: &[u32]) -> Result<(), Failure> {
    execute(conn, "UPDATE build_state SET applied_hash=?, applied_text=?, applied_at=? WHERE id=1",
            vec![Sql::Text(store::revision(text)), Sql::Text(scalar(text).ok_or_else(Failure::internal)?), Sql::Text(Moment::now().isoformat())])?;
    Ok(())
}

/// `_state`: `(applied_hash, applied_text)`.
fn state(conn: &Connection) -> Result<(Sql, Sql), Failure> {
    let values = row(conn, "SELECT applied_hash, applied_text FROM build_state WHERE id=1")?;
    Ok((values[0].clone(), values[1].clone()))
}

/// `_move_rows_into`: settings the file does not mention are taken from the rows.
fn move_rows_into(source: &[u32], rows: Value) -> Result<Text, Failure> {
    changed(source, |build| {
        let Value::Dict(rows) = rows else { return Err(Failure::internal()) };
        for (name, values) in rows.iter() {
            if *name == text("history") || *name == text("tern") {
                continue; // no older table held these; their defaults apply
            }
            let name = super::value::lossy(name);
            let target = section(build, &["settings", &name]);
            let Value::Dict(values) = values else { continue };
            for (key, value) in values.iter() {
                if !target.contains(key) {
                    target.insert(key.clone(), value.clone());
                }
            }
        }
        Ok(())
    })
}

/// `_last_good`: the build last projected, else the defaults.
fn last_good(conn: &Connection) -> Result<(Value, String), Failure> {
    let (hash, applied) = state(conn)?;
    if let Sql::Text(applied) = &applied {
        if !applied.is_empty() {
            let checked = document::check(&text(applied))?;
            if let Some(build) = checked.build {
                let hash = match hash {
                    Sql::Text(hash) => hash,
                    _ => String::new(),
                };
                return Ok((build, hash));
            }
        }
    }
    let mut empty = Dict::new();
    empty.insert(text("schema"), Value::Int(Int::Small(document::SCHEMA_VERSION)));
    Ok((document::normalize(&empty), String::new()))
}

/// `sync`: project the build file into SQLite when it changed; return what is active. A file
/// with an error changes nothing: the last good build stays active.
pub fn sync(conn: &mut Connection, store: &Store) -> Result<Synced, Failure> {
    let mut source = store.ensure()?;
    let mut checked = document::check(&source)?;
    if checked.build.is_some() && state(conn)?.0 == Sql::Null {
        let _lock = store.lock()?;
        if let Some(now) = store.read_text()?.filter(|t| !t.is_empty()) {
            source = now;
        }
        checked = document::check(&source)?;
        if checked.build.is_some() {
            let moved = move_rows_into(&source, settings_from_rows(conn)?)?;
            if moved != source {
                store.write_text(&moved)?;
                checked = document::check(&moved)?;
                source = moved;
            }
        }
    }
    let Some(build) = checked.build else {
        let (build, active) = last_good(conn)?;
        return Ok(Synced { revision: store::revision(&source), text: source, build, active_revision: active, problems: checked.problems });
    };
    let revision = store::revision(&source);
    let projected = |conn: &Connection| -> Result<bool, Failure> { Ok(state(conn)?.0 == Sql::Text(revision.clone())) };
    if !projected(conn)? {
        let tx = begin(conn)?;
        if !projected(&tx)? {
            apply(&tx, &build)?;
            record(&tx, &source)?;
        }
        tx.commit()?;
    }
    Ok(Synced { text: source, revision: revision.clone(), build, active_revision: revision, problems: checked.problems })
}

/// `document.require`: the normalized build; Python's BuildInvalid is a 500 here.
fn require(source: &[u32]) -> Result<Value, Failure> {
    document::check(source)?.build.ok_or_else(Failure::internal)
}

/// `editing`: change the build from a Settings screen. `change` mutates the document as written
/// (and may read or write rows: it runs inside the same `BEGIN IMMEDIATE`); the new document is
/// validated, written atomically and projected before the commit. A file with an error is
/// refused (409) rather than overwritten.
pub fn editing(conn: &mut Connection, store: &Store,
               change: impl FnOnce(&mut Dict, &Connection) -> Result<(), Failure>) -> Result<(), Failure> {
    store.ensure()?;
    let _lock = store.lock()?;
    let source = store.read_text()?.filter(|t| !t.is_empty()).unwrap_or_else(|| store.initial_text());
    store::refuse_invalid(&document::check(&source)?)?;
    let tx = begin(conn)?;
    let mut data = parse_document(&source)?;
    change(&mut data, &tx)?;
    let new = changed(&source, |build| {
        *build = data;
        Ok(())
    })?;
    let built = require(&new)?;
    apply(&tx, &built)?;
    record(&tx, &new)?;
    if new != source {
        store.write_text(&new)?;
    }
    tx.commit()?;
    Ok(())
}

/// `replace`: make `text` the active build in one step (install, rollback, the build editor);
/// with `history` the build it replaces is kept for rollback first. Returns the new revision.
pub fn replace(conn: &mut Connection, store: &Store, source: &[u32], history: Option<(&str, &[u32])>) -> Result<String, Failure> {
    let built = require(source)?;
    store.ensure()?;
    let _lock = store.lock()?;
    let previous = store.read_text()?;
    let tx = begin(conn)?;
    apply(&tx, &built)?;
    record(&tx, source)?;
    if let (Some((reason, from)), Some(previous)) = (history, &previous) {
        if previous.as_slice() != source {
            store.push_history(previous, reason, from)?;
        }
    }
    store.write_text(source)?;
    tx.commit()?;
    Ok(store::revision(source))
}

/// `claim_local_preferences` (in the caller's transaction): false when a browser already moved its own.
pub fn claim_local_preferences(conn: &Connection) -> Result<bool, Failure> {
    Ok(conn.execute("UPDATE build_state SET local_preferences_moved=1 WHERE id=1 AND local_preferences_moved=0", [])? == 1)
}

pub fn local_preferences_moved(conn: &Connection) -> Result<bool, Failure> {
    Ok(truthy(&row(conn, "SELECT local_preferences_moved FROM build_state WHERE id=1")?[0]))
}
