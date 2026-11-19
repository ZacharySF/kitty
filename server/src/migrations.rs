//! The workspace migration runner: `kitty.db.migrations.migrate` in Rust, over the same
//! numbered `.sql` files (compiled in by build.rs) and the same `schema_version` table, so
//! either side can open a database the other migrated.
//!
//! Each pending migration runs as one script inside `BEGIN ... COMMIT`, recorded in
//! `schema_version` in the same transaction; `PRAGMA foreign_key_check` must come back empty
//! before the commit. A script whose first line is `-- foreign_keys: off` runs with foreign
//! keys off (a table rebuild), set around the transaction as SQLite requires.
//!
//! Two migrations name a Python conversion on their first line (`-- python: ...`, 033's
//! notes-to-blocks conversion and 039's tag backfill). Both only convert data that a database
//! from before the block backend (rika! 0.3.33 and older) can hold; on any other database
//! they are no-ops and the script alone is the whole migration, which this runner applies.
//! When such a database does hold that data, [`migrate`] stops before the migration and
//! reports [`Outcome::NeedsPython`]: the caller runs the Python runner once for the rest of
//! the chain (crate::startup).

use std::collections::BTreeSet;
use crate::private::Mode;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::fallible_iterator::FallibleIterator;
use rusqlite::{Batch, Connection};

/// One numbered migration file.
pub struct Migration {
    pub version: u32,
    pub name: &'static str,
    pub sql: &'static str,
}

include!(concat!(env!("OUT_DIR"), "/migrations.rs"));

const SCHEMA_VERSION_DDL: &str = "
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
";

/// The Python conversions a migration may name, and the query that says whether this
/// database has anything for it to convert (Python's step is a no-op otherwise).
const CONVERSIONS: &[(&str, &str)] = &[
    // kitty.core.blocks.legacy.migrate_notes converts every row of its CONTENT tables.
    ("-- python: kitty.core.blocks.legacy:migrate_notes",
     "SELECT EXISTS (SELECT 1 FROM note_pages) OR EXISTS (SELECT 1 FROM documents) OR EXISTS (SELECT 1 FROM quick_notes)"),
    // kitty.core.blocks.tags.backfill makes a tag record of every #tag already written.
    ("-- python after: kitty.core.blocks.tags:backfill",
     "SELECT EXISTS (SELECT 1 FROM block_topics WHERE kind = 'tag')"),
];

/// How a run ended.
#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Every migration is applied; these were applied now.
    Current(Vec<u32>),
    /// These were applied now; migration `version` has data to convert that only the Python
    /// runner can convert.
    NeedsPython { applied: Vec<u32>, version: u32 },
}

#[derive(Debug)]
pub struct Error(pub String);

impl std::fmt::Display for Error {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<rusqlite::Error> for Error {
    fn from(error: rusqlite::Error) -> Self {
        Self(error.to_string())
    }
}

/// `kitty.db.connect`: the pragmas every writer of the workspace database uses. A missing
/// parent folder is created owner-only.
pub fn connect(path: &Path) -> Result<Connection, Error> {
    if let Some(parent) = path.parent().filter(|parent| !parent.as_os_str().is_empty()) {
        std::fs::DirBuilder::new().recursive(true).mode(0o700).create(parent)
            .map_err(|error| Error(format!("creating {}: {error}", parent.display())))?;
    }
    let conn = Connection::open(path)?;
    conn.busy_timeout(Duration::from_millis(5000))?;
    conn.execute_batch("PRAGMA foreign_keys = ON")?;
    conn.query_row("PRAGMA journal_mode = WAL", [], |_| Ok(()))?;
    conn.execute_batch("PRAGMA synchronous = NORMAL")?;
    Ok(conn)
}

/// `kitty.dates.now_iso`: `YYYY-MM-DDTHH:MM:SSZ` in UTC.
pub fn now_iso() -> String {
    let seconds = SystemTime::now().duration_since(UNIX_EPOCH).map(|elapsed| elapsed.as_secs()).unwrap_or(0);
    let (days, rest) = ((seconds / 86_400) as i64, seconds % 86_400);
    let (year, month, day) = civil(days);
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z", rest / 3600, rest % 3600 / 60, rest % 60)
}

/// The proleptic Gregorian date `days` after 1970-01-01 (Howard Hinnant's `civil_from_days`).
pub fn civil(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (yoe + era * 400 + i64::from(month <= 2), month, day)
}

fn first_line(sql: &str) -> &str {
    sql.trim_start().split('\n').next().unwrap_or("").trim()
}

/// Run every statement of `sql` to completion (rows a statement returns are read and dropped,
/// as `executescript` does).
fn run_script(conn: &Connection, sql: &str) -> Result<(), rusqlite::Error> {
    let mut batch = Batch::new(conn, sql);
    while let Some(mut statement) = batch.next()? {
        let mut rows = statement.raw_query();
        while rows.next()?.is_some() {}
    }
    Ok(())
}

fn apply(conn: &Connection, migration: &Migration) -> Result<(), Error> {
    // The name is [a-z0-9_] (build.rs) and the timestamp machine-made, as in Python.
    let record = format!(
        "INSERT INTO schema_version (version, name, applied_at) VALUES ({}, '{}', '{}');",
        migration.version, migration.name, now_iso()
    );
    let keys_were_on: bool = conn.query_row("PRAGMA foreign_keys", [], |row| row.get(0))?;
    let keys_off = first_line(migration.sql).eq_ignore_ascii_case("-- foreign_keys: off");
    if keys_off {
        conn.execute_batch("PRAGMA foreign_keys = OFF")?;
    }
    let outcome = (|| -> Result<(), Error> {
        run_script(conn, &format!("BEGIN;\n{}\n{record}", migration.sql))?;
        let mut check = conn.prepare("PRAGMA foreign_key_check")?;
        let broken: Vec<String> = check.query_map([], |row| row.get(0))?.collect::<Result<_, _>>()?;
        if let Some(table) = broken.first() {
            return Err(Error(format!(
                "migration {} leaves {} broken foreign key(s), first in table {table}",
                migration.version, broken.len()
            )));
        }
        conn.execute_batch("COMMIT")?;
        Ok(())
    })();
    if outcome.is_err() && !conn.is_autocommit() {
        let _ = conn.execute_batch("ROLLBACK");
    }
    if keys_off && keys_were_on {
        conn.execute_batch("PRAGMA foreign_keys = ON")?;
    }
    outcome
}

/// `kitty.db.migrations._python_step`'s header: `-- python[ after]: module:function`.
fn names_python_step(line: &str) -> bool {
    let Some(rest) = line.strip_prefix("-- python after: ").or_else(|| line.strip_prefix("-- python: ")) else { return false };
    let Some((module, function)) = rest.split_once(':') else { return false };
    let word = |text: &str, more: &dyn Fn(u8) -> bool| {
        text.bytes().next().is_some_and(|b| b.is_ascii_lowercase() || b == b'_')
            && text.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || more(b))
    };
    word(module, &|b| b == b'.') && word(function, &|_| false)
}

/// Whether this database has data for the Python conversion `migration` names.
fn needs_python(conn: &Connection, migration: &Migration) -> bool {
    let line = first_line(migration.sql);
    if !names_python_step(line) {
        return false;
    }
    match CONVERSIONS.iter().find(|(header, _)| *header == line) {
        // A probe that fails (a table it reads is missing) leaves the verdict to Python.
        Some((_, probe)) => conn.query_row(probe, [], |row| row.get::<_, bool>(0)).unwrap_or(true),
        None => true,
    }
}

/// Apply every pending migration (only those in `only`, when given: the tests' historical
/// schemas), in version order. A lower number that lands after a higher one still applies.
pub fn migrate(conn: &Connection, only: Option<&BTreeSet<u32>>) -> Result<Outcome, Error> {
    conn.execute_batch(SCHEMA_VERSION_DDL)?;
    let done: BTreeSet<u32> = {
        let mut statement = conn.prepare("SELECT version FROM schema_version")?;
        let versions = statement.query_map([], |row| row.get(0))?.collect::<Result<_, _>>()?;
        versions
    };
    let mut applied = Vec::new();
    for migration in MIGRATIONS {
        if done.contains(&migration.version) || only.is_some_and(|only| !only.contains(&migration.version)) {
            continue;
        }
        if needs_python(conn, migration) {
            return Ok(Outcome::NeedsPython { applied, version: migration.version });
        }
        apply(conn, migration)?;
        applied.push(migration.version);
    }
    Ok(Outcome::Current(applied))
}

/// Hidden and test-only: `rika-server migrate DB [--only N,N,...]`, for
/// tests/test_migration_parity.py. Prints the applied versions, or `needs-python N`.
pub fn migrate_command(args: &[String]) -> i32 {
    let (path, only) = match args {
        [path] => (path, None),
        [path, flag, list] if flag == "--only" => {
            match list.split(',').filter(|item| !item.is_empty()).map(str::parse).collect::<Result<BTreeSet<u32>, _>>() {
                Ok(only) => (path, Some(only)),
                Err(_) => {
                    eprintln!("--only takes comma-separated migration numbers");
                    return 2;
                }
            }
        }
        _ => {
            eprintln!("usage: rika-server migrate DB [--only N,N,...]");
            return 2;
        }
    };
    let outcome = connect(Path::new(path)).and_then(|conn| migrate(&conn, only.as_ref()));
    match outcome {
        Ok(Outcome::Current(applied)) => {
            println!("{}", applied.iter().map(u32::to_string).collect::<Vec<_>>().join(","));
            0
        }
        Ok(Outcome::NeedsPython { version, .. }) => {
            println!("needs-python {version}");
            3
        }
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dates_are_civil() {
        assert_eq!(civil(0), (1970, 1, 1));
        assert_eq!(civil(19_723), (2024, 1, 1));
        assert_eq!(civil(11_016), (2000, 2, 29));
        assert_eq!(civil(-1), (1969, 12, 31));
    }

    #[test]
    fn migrations_are_ordered_and_unique() {
        assert!(MIGRATIONS.windows(2).all(|pair| pair[0].version < pair[1].version));
        assert!(MIGRATIONS.iter().all(|m| !m.sql.is_empty()));
    }

    #[test]
    fn a_fresh_database_migrates_once() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("PRAGMA foreign_keys = ON").unwrap();
        let Outcome::Current(applied) = migrate(&conn, None).unwrap() else { panic!("a fresh database needs no Python") };
        assert_eq!(applied, MIGRATIONS.iter().map(|m| m.version).collect::<Vec<_>>());
        assert_eq!(migrate(&conn, None).unwrap(), Outcome::Current(Vec::new()));
    }
}
