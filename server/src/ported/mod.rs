//! What every module route the front answers over the workspace database shares: the
//! routing table front.rs consults (one registration there for all of them), the module
//! guard, `WebApplication.mutate`'s bookkeeping around a mutation (`record_events`), the
//! database connection and Python's value and `datetime` semantics. The module code lives
//! in its own folder (crate::calendar, ...).

pub mod db;
pub mod events;
pub mod py;
pub mod time;

use std::path::Path;

use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::bootstrap::Bootstrap;
use crate::modules::{Failure, Workspace};
use crate::target::Target;
pub use db::{fail, internal, Done, Fault};

/// What a handler may read about the running workspace.
pub struct Ctx<'a> {
    pub conn: &'a Connection,
    /// The worker's active modules, frozen for its lifetime.
    pub modules: &'a [String],
    pub demo: bool,
    /// `Settings.root`.
    pub root: &'a Path,
    pub db_path: &'a Path,
}

impl Ctx<'_> {
    pub fn has(&self, module: &str) -> bool {
        self.modules.iter().any(|active| active == module)
    }
}

/// One request: its decoded path, its query (each name once) and, for a POST, its body.
pub struct Call<'a> {
    pub path: &'a str,
    pub query: &'a [(String, String)],
    pub body: Option<&'a Map<String, Value>>,
}

static EMPTY: std::sync::LazyLock<Map<String, Value>> = std::sync::LazyLock::new(Map::new);

impl Call<'_> {
    pub fn mutation(&self) -> bool {
        self.body.is_some()
    }

    pub fn get(&self, name: &str) -> Option<&str> {
        self.query.iter().find(|(key, _)| key == name).map(|(_, value)| value.as_str())
    }

    pub fn names(&self) -> impl Iterator<Item = &str> {
        self.query.iter().map(|(key, _)| key.as_str())
    }

    /// Whether the query names only `allowed`.
    pub fn only(&self, allowed: &[&str]) -> bool {
        self.names().all(|name| allowed.contains(&name))
    }

    /// `WebApplication._no_query`.
    pub fn no_query(&self) -> Done<()> {
        if self.query.is_empty() { Ok(()) } else { Err(fail(400, "This route does not accept query parameters")) }
    }

    pub fn body(&self) -> &Map<String, Value> {
        self.body.unwrap_or(&EMPTY)
    }
}

/// A route group: which paths it answers, the module it belongs to (None: the core) and its
/// handler, which answers None for a path it does not know (Python's 404).
pub struct Group {
    pub owns: fn(path: &str, mutation: bool) -> bool,
    pub module: Option<&'static str>,
    pub answer: fn(&Ctx, &Call) -> Done<Option<Value>>,
}

/// Every ported route group, one line per module.
static GROUPS: &[&Group] = &[
    &crate::calendar::GROUP,
    &crate::quick_notes::GROUP,
    &crate::goals::GROUP,
    &crate::study::GROUP,
    &crate::flashcards::GROUP,
    &crate::opportunities::GROUP,
    &crate::schema::GROUP,
    &crate::schema::JOBS,
    &crate::backlinks::GROUP,
    &crate::agents::GROUP,
    &crate::blocks::routes::GROUP,
    &crate::documents::GROUP,
];

fn groups() -> impl Iterator<Item = &'static Group> {
    GROUPS.iter().copied()
}

/// Whether the front answers this request itself.
pub fn owns(path: &str, mutation: bool) -> bool {
    groups().any(|group| (group.owns)(path, mutation))
}

/// `WebApplication._no_query` for a mutation, checked before its body is read.
pub fn no_query(target: &Target) -> Result<(), Failure> {
    if target.query.is_empty() { Ok(()) } else { Err(Failure { status: 400, message: "This route does not accept query parameters".into() }) }
}

/// `ModuleDisabledError`'s message.
pub fn disabled(module: &str) -> Fault {
    fail(403, format!("The '{module}' module is disabled. Enable it in Settings or list it under \"modules\" in config/build.json, then restart rika! (changes apply on the next start)."))
}

/// Answer one owned request: the module guard, the handler, and for a mutation the
/// `record_events` capture `WebApplication.mutate` ran after it.
pub fn answer(workspace: &Workspace, bootstrap: &Bootstrap, target: &Target, body: Option<Map<String, Value>>) -> Result<Value, Failure> {
    let group = groups().into_iter().find(|group| (group.owns)(&target.path, body.is_some())).ok_or_else(|| Failure { status: 404, message: "API route not found".into() })?;
    let outcome = (|| {
        if let Some(module) = group.module {
            if !workspace.active.iter().any(|active| active == module) {
                return Err(disabled(module));
            }
        }
        db::with(bootstrap.db_path(), |conn| {
            let ctx = Ctx { conn, modules: &workspace.active, demo: bootstrap.demo(), root: bootstrap.root(), db_path: bootstrap.db_path() };
            let call = Call { path: &target.path, query: &target.query, body: body.as_ref() };
            let before = call.mutation().then(|| events::op_log_mark(conn)).flatten();
            let result = (group.answer)(&ctx, &call)?.ok_or_else(|| fail(404, "API route not found"))?;
            if let Some(body) = &body {
                events::capture(conn, &target.path, body, &result, before);
            }
            Ok(result)
        })
    })();
    outcome.map_err(|fault| Failure { status: fault.status, message: fault.message })
}
