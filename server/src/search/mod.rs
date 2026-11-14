//! Search over the full-text index (`kitty.core.search`): `GET /api/search`, answered here.
//!
//! `GET /api/search?q=<text>[&limit=1-200][&kinds=note_page,job,...]` answers
//! `{query, hits: [{kind, id, title, view, match, title_marks, snippet}], total, truncated}`;
//! `title_marks` and `snippet` are `[[text, is_hit], ...]` segments. Only the kinds of the
//! active modules are searched (jobs and ApplyAI calendar copies need ApplyAI; everything else
//! the workspace module). Every query first folds the `search_pending` queue (`sync`), so it
//! covers every committed write, the worker's included.
//!
//! The index, its documents and its queries are the same as Python's (`docs`, `query`), checked
//! by tests/test_search_parity.py; `rika-server search ...` (test-only) runs them on a database.

pub mod docs;
pub mod query;
mod tables;
pub mod text;
pub mod web;

use std::path::Path;

use serde_json::Value;

use crate::modules::Workspace;
use web::{fail, Failure};

pub const PATH: &str = "/api/search";

/// Whether a GET is this module's.
pub fn owns(path: &str) -> bool {
    path == PATH
}

fn refused(error: query::Error) -> Failure {
    match error {
        query::Error::Refused(query::Refused(message)) => fail(400, message),
        query::Error::Failed(failure) => failure.into(),
    }
}

/// `web_search.get`.
pub async fn answer(db_path: &Path, workspace: &Workspace, forward_target: &str) -> Result<Value, Failure> {
    let target = web::query_of(forward_target)?;
    if target.query.iter().any(|(name, _)| !matches!(name.as_str(), "q" | "limit" | "kinds")) || target.get("q").is_none() {
        return Err(fail(400, "Search takes q and optional limit and kinds"));
    }
    let limit = target.get("limit").unwrap_or("20");
    let digits = limit.as_bytes();
    if digits.is_empty() || digits.len() > 3 || digits[0] == b'0' || !digits.iter().all(u8::is_ascii_digit) {
        return Err(fail(400, format!("Limit must be 1-{}", query::MAX_LIMIT)));
    }
    let limit: i64 = limit.parse().expect("three digits");
    let kinds: Option<Vec<String>> = target.get("kinds").map(|kinds| kinds.split(',').map(str::to_owned).collect());
    let text = target.get("q").expect("checked above").to_owned();
    tokio::task::block_in_place(|| {
        web::workspace(db_path).with(|conn| query::search(conn, &text, limit, kinds.as_deref(), &workspace.active).map_err(refused))
    })
}

/// `rika-server search DB` (test-only): `search.search` on one database for every line of
/// stdin, `{"q", "limit", "kinds", "modules"}` (kinds may be null), printing one answer (or
/// `{"error"}` for a refusal) per line, for tests/test_search_parity.py.
pub fn search_command(args: &[String]) -> i32 {
    use std::io::BufRead;
    let [db] = args else {
        eprintln!("usage: rika-server search DB < queries");
        return 2;
    };
    let conn = match web::open(Path::new(db)) {
        Ok(conn) => conn,
        Err(error) => {
            eprintln!("{error}");
            return 1;
        }
    };
    let strings = |value: &Value| -> Option<Vec<String>> {
        value.as_array().map(|items| items.iter().filter_map(|item| item.as_str().map(str::to_owned)).collect())
    };
    for line in std::io::stdin().lock().lines() {
        let Ok(request) = line.map_err(|_| ()).and_then(|line| serde_json::from_str::<Value>(&line).map_err(|_| ())) else {
            eprintln!("each line is a JSON query");
            return 2;
        };
        let text = request["q"].as_str().unwrap_or("");
        let limit = request["limit"].as_i64().unwrap_or(20);
        let kinds = strings(&request["kinds"]);
        let modules = strings(&request["modules"]).unwrap_or_default();
        let answer = match query::search(&conn, text, limit, kinds.as_deref(), &modules) {
            Ok(value) => value,
            Err(query::Error::Refused(query::Refused(message))) => serde_json::json!({ "error": message }),
            Err(query::Error::Failed(failure)) => {
                eprintln!("{failure:?}");
                return 1;
            }
        };
        println!("{answer}");
    }
    0
}

/// `rika-server search-sync DB` (test-only): `search.sync` on one database; prints how many
/// records it refreshed.
pub fn sync_command(args: &[String]) -> i32 {
    let [db] = args else {
        eprintln!("usage: rika-server search-sync DB");
        return 2;
    };
    match web::open(Path::new(db)).map_err(docs::Failure::Database).and_then(|conn| docs::sync(&conn)) {
        Ok(count) => {
            println!("{count}");
            0
        }
        Err(failure) => {
            eprintln!("{failure:?}");
            1
        }
    }
}
