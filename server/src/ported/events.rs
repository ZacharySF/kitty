//! `kitty.core.record_events.capture` for the mutations the front answers: one append-only
//! `record_events` row per successful content change, holding only route-derived identifiers.
//! A capture failure never fails the request (Python logged it).

use std::sync::LazyLock;

use regex::Regex;
use rusqlite::{params, Connection};
use serde_json::{Map, Value};

use super::time::Dt;

/// `SECTION_PREFIXES`.
const SECTIONS: &[(&str, &str)] = &[
    ("notes", "notes"), ("documents", "documents"), ("quick-notes", "quick-notes"), ("flashcards", "flashcards"),
    ("study", "study"), ("calendar", "calendar"), ("schema", "schema"), ("opportunities", "opportunities"),
    ("jobs", "jobs"), ("job-tools", "jobs"), ("reminders", "reminders"),
];

fn compile(pattern: &str) -> Regex {
    Regex::new(&format!("^(?:{pattern})$")).expect("a valid route pattern")
}

/// `SKIP`: routes inside content sections that are not content changes.
static SKIP: LazyLock<Vec<Regex>> = LazyLock::new(|| [
    r"/api/notes/pages/[0-9]+/viewport", r"/api/notes/(daily|resolve)", r"/api/notes/(preferences|cleanup)",
    r"/api/flashcards/(review|reveal|export|media)", r"/api/flashcards/(grade|undo)",
    r"/api/calendar/(time|availability|import/preview|reminders/dismiss)", r"/api/calendar/google/.*",
    r"/api/study/(timer|settings)", r"/api/documents/(prepare|topics)", r"/api/opportunities/(settings|discover|sources/.*)",
    r"/api/jobs/metadata",
].iter().map(|pattern| compile(pattern)).collect());

const ID: &str = r"(?P<id>[1-9][0-9]{0,17})";

/// How one known route names its entity, action and id.
enum Name {
    Fixed(&'static str),
    Group,
    Opportunity,
    /// `/api/flashcards/action`: the body's action, on the note when it deletes or archives, else on the card.
    Flashcard,
    /// `_body_action()`: the body's `action` when it is a word, else "save".
    Body,
}

struct Route {
    pattern: Regex,
    entity: &'static str,
    action: Name,
    /// `_calendar_undone`: the entity and id come from the history row.
    undone: bool,
    /// The entity is the singular of the path's first group (`subjects` -> `subject`).
    grouped: bool,
    /// `logged`: a new record (no `id` in the body) has its own history and is not captured.
    own_history_when_new: bool,
    /// The id is the body's field of this name, whatever the path and the result say.
    ident_field: Option<&'static str>,
    /// `/api/notes/reorder`: the entity is the body's `kind` (notebooks, folders), else a page.
    entity_from_kind: bool,
    /// `logged`: the core keeps its own history for these body actions; not captured.
    logged_actions: &'static [&'static str],
    /// `_single_id`: the id is the body's only `ids` entry.
    single_id: bool,
}

/// The `ROUTES` of the sections whose mutations the front answers.
static ROUTES: LazyLock<Vec<Route>> = LazyLock::new(|| {
    let route = |pattern: &str, entity, action, undone| Route { pattern: compile(pattern), entity, action, undone, grouped: false, own_history_when_new: false, ident_field: None, entity_from_kind: false, logged_actions: &[], single_id: false };
    vec![
        route(&format!(r"/api/notes/pages/{ID}/blocks"), "page", Name::Fixed("outline"), false),
        route(&format!(r"/api/notes/pages/{ID}/organize"), "page", Name::Fixed("organize"), false),
        route(&format!(r"/api/notes/pages/{ID}/delete"), "page", Name::Fixed("delete"), false),
        route(&format!(r"/api/notes/pages/{ID}"), "page", Name::Fixed("save"), false),
        route(r"/api/notes/pages", "page", Name::Fixed("create"), false),
        route(r"/api/notes/import", "page", Name::Fixed("import"), false),
        route(r"/api/notes/attachments", "attachment", Name::Fixed("upload"), false),
        Route { entity_from_kind: true, ..route(r"/api/notes/reorder", "page", Name::Fixed("reorder"), false) },
        route(&format!(r"/api/notes/notebooks/{ID}"), "notebook", Name::Fixed("save"), false),
        route(r"/api/notes/notebooks", "notebook", Name::Fixed("create"), false),
        route(&format!(r"/api/notes/folders/{ID}/delete"), "folder", Name::Fixed("delete"), false),
        route(&format!(r"/api/notes/folders/{ID}"), "folder", Name::Fixed("save"), false),
        route(r"/api/notes/folders", "folder", Name::Fixed("create"), false),
        Route { grouped: true, ..route(r"/api/flashcards/(deck|type|note)", "", Name::Fixed("save"), false) },
        route(r"/api/flashcards/action", "card", Name::Flashcard, false),
        Route { ident_field: Some("deck_id"), ..route(r"/api/flashcards/import", "deck", Name::Fixed("import"), false) },
        Route { grouped: true, ..route(r"/api/study/(subjects|projects|targets|tasks|exams|plan)", "", Name::Body, false) },
        Route { own_history_when_new: true, ..route(r"/api/study/sessions", "session", Name::Body, false) },
        route(r"/api/study/guide", "guide", Name::Fixed("save"), false),
        route(r"/api/study/generate-plan", "plan", Name::Fixed("generate"), false),
        route(r"/api/calendar/calendars/delete", "calendar", Name::Fixed("delete"), false),
        route(r"/api/calendar/calendars", "calendar", Name::Fixed("save"), false),
        route(r"/api/calendar/events/(?P<id>[A-Za-z0-9_-]{1,100})", "event", Name::Fixed("save"), false),
        route(r"/api/calendar/events", "event", Name::Fixed("create"), false),
        route(r"/api/calendar/tasks", "task", Name::Fixed("save"), false),
        route(r"/api/calendar/undo", "event", Name::Fixed("undo"), true),
        route(r"/api/calendar/import(-jobs)?", "event", Name::Fixed("import"), false),
        route(r"/api/schema/(create|import|add-node|save|delete|undo|import-jobs)", "map", Name::Group, false),
        route(r"/api/schema/backlinks/apply", "map", Name::Fixed("link"), false),
        route(r"/api/opportunities", "opportunity", Name::Fixed("create"), false),
        route(r"/api/opportunities/import", "opportunity", Name::Fixed("import"), false),
        route(r"/api/opportunities/favorites", "opportunity", Name::Fixed("favorite"), false),
        route(&format!(r"/api/opportunities/{ID}(?:/(delete|project|items|project-details|calendar))?"), "opportunity", Name::Opportunity, false),
        Route { single_id: true, ..route(r"/api/jobs/bulk", "job", Name::Body, false) },
        Route { logged_actions: &["status", "applied"], ..route(&format!(r"/api/jobs/{ID}"), "job", Name::Body, false) },
        Route { logged_actions: &["undo"], ..route(&format!(r"/api/job-tools/{ID}"), "job", Name::Body, false) },
        route(r"/api/reminders", "reminder", Name::Fixed("create"), false),
        route(&format!(r"/api/reminders/{ID}/complete"), "reminder", Name::Fixed("complete"), false),
    ]
});

static TOKEN: LazyLock<Regex> = LazyLock::new(|| compile("[A-Za-z0-9_-]{1,100}"));
static WORD: LazyLock<Regex> = LazyLock::new(|| compile("[a-z][a-z_-]{0,39}"));
static DIGITS: LazyLock<Regex> = LazyLock::new(|| compile("[0-9]+"));

fn word(value: &str, default: &str) -> String {
    if WORD.is_match(value) { value.to_owned() } else { default.to_owned() }
}

fn singular(word: &str) -> &str {
    if word.ends_with('s') && !word.ends_with("ss") { &word[..word.len() - 1] } else { word }
}

/// `_token`: a positive integer or a short opaque token, never free text.
fn token(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::Number(number) => number.as_i64().filter(|n| *n > 0).map(|n| n.to_string()),
        Value::String(text) if TOKEN.is_match(text) => Some(text.clone()),
        _ => None,
    }
}

fn result_id(result: &Value) -> Option<String> {
    let map = result.as_object()?;
    if let Some(found) = token(map.get("id")) {
        return Some(found);
    }
    let nested: Vec<&Map<String, Value>> = map.values().filter_map(Value::as_object).filter(|item| item.contains_key("id")).collect();
    if nested.len() == 1 { token(nested[0].get("id")) } else { None }
}

/// `_shape`: entity, id and action of `/api/<section>/<resource>/<id?>/<action?>`.
fn shape(path: &str, section: &str) -> (String, Option<String>, String) {
    let segments: Vec<&str> = path.split('/').skip(3).filter(|part| !part.is_empty()).collect();
    let index = segments.iter().position(|part| DIGITS.is_match(part));
    let head = match index { Some(at) => &segments[..at], None => &segments[..] };
    let entity = singular(head.first().copied().unwrap_or(section)).to_owned();
    let entity = if head.is_empty() { singular(section).to_owned() } else { entity };
    let tail: &[&str] = match index { Some(at) => &segments[at + 1..], None => segments.get(1..).unwrap_or(&[]) };
    let action = tail.last().copied().unwrap_or("save");
    (word(&entity, "item"), index.map(|at| segments[at].to_owned()), word(action, "save"))
}

/// `classify`: the row a successful mutation records, or None.
fn classify(conn: &Connection, path: &str, body: &Map<String, Value>, result: &Value) -> Option<(String, String, Option<String>, String)> {
    let parts: Vec<&str> = path.split('/').collect();
    if parts.len() < 3 || parts[1] != "api" {
        return None;
    }
    let section = SECTIONS.iter().find(|(prefix, _)| *prefix == parts[2])?.1;
    if SKIP.iter().any(|pattern| pattern.is_match(path)) {
        return None;
    }
    if result.as_object().is_some_and(|map| map.contains_key("task")) {
        return None;
    }
    let (entity, path_id, ident, mut action) = match ROUTES.iter().find_map(|route| route.pattern.captures(path).map(|found| (route, found))) {
        None => {
            let (entity, path_id, action) = shape(path, section);
            let ident = path_id.clone().or_else(|| result_id(result)).or_else(|| token(body.get("id")));
            (entity, path_id, ident, action)
        }
        Some((route, found)) => {
            if route.own_history_when_new && body.get("id").is_none_or(Value::is_null) {
                return None;
            }
            if body.get("action").and_then(Value::as_str).is_some_and(|action| route.logged_actions.contains(&action)) {
                return None;
            }
            let action = match &route.action {
                Name::Body | Name::Flashcard => body.get("action").and_then(Value::as_str).map_or_else(|| "save".to_owned(), |action| word(action, "save")),
                Name::Fixed(action) => (*action).to_owned(),
                Name::Group => found.get(1).map(|m| m.as_str().to_owned()).unwrap_or_default(),
                Name::Opportunity => found.get(2).map(|m| m.as_str().to_owned()).unwrap_or_else(|| "save".into()),
            };
            let path_id = found.name("id").map(|m| m.as_str().to_owned());
            let mut entity = if route.grouped { found.get(1).map(|m| singular(m.as_str()).to_owned()).unwrap_or_default() } else { route.entity.to_owned() };
            if route.entity_from_kind {
                entity = match body.get("kind").and_then(Value::as_str) {
                    Some("notebooks") => "notebook".into(),
                    Some("folders") => "folder".into(),
                    _ => route.entity.to_owned(),
                };
            }
            if matches!(route.action, Name::Flashcard) && matches!(body.get("action").and_then(Value::as_str), Some("delete" | "archive")) {
                entity = "note".into();
            }
            let ident = if route.undone {
                let history = body.get("id").and_then(Value::as_i64);
                let row = history.and_then(|id| conn.query_row("SELECT entity, entity_id FROM calendar_history WHERE id = ?", [id],
                    |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))).ok());
                match row {
                    Some((kind, id)) => {
                        entity = kind;
                        token(Some(&Value::String(id)))
                    }
                    None => None,
                }
            } else if route.single_id {
                match body.get("ids") {
                    Some(Value::Array(ids)) if ids.len() == 1 => token(ids.first()),
                    _ => None,
                }
            } else if let Some(field) = route.ident_field {
                token(body.get(field))
            } else {
                path_id.clone().or_else(|| result_id(result)).or_else(|| token(body.get("id")))
            };
            (entity, path_id, ident, action)
        }
    };
    if action == "save" && path_id.is_none() && body.get("id").is_none_or(Value::is_null) {
        action = "create".into();
    }
    Some((section.to_owned(), entity, ident, action))
}

/// `op_log_mark`: the block backend's newest sequence, or None without it.
pub fn op_log_mark(conn: &Connection) -> Option<i64> {
    conn.query_row("SELECT COALESCE(MAX(seq), 0) FROM op_log", [], |row| row.get(0)).ok()
}

/// `capture`: record one successful mutation; failures are dropped.
pub fn capture(conn: &Connection, path: &str, body: &Map<String, Value>, result: &Value, op_log_before: Option<i64>) {
    if let Some(before) = op_log_before {
        if op_log_mark(conn).unwrap_or(0) > before {
            return;
        }
    }
    if let Some((section, entity, ident, action)) = classify(conn, path, body, result) {
        let at = Dt::now_utc();
        // `isoformat(timespec="milliseconds")`.
        let mut stamp = at.isoformat();
        let millis = format!(".{:03}", at.time_of_day() % super::time::SECOND / 1000);
        let cut = stamp.find('+').unwrap_or(stamp.len());
        let clock_end = stamp[..cut].find('.').unwrap_or(cut);
        stamp.replace_range(clock_end..cut, &millis);
        let _ = conn.execute("INSERT INTO record_events(at, section, entity, entity_id, action, route) VALUES (?, ?, ?, ?, ?, ?)",
                             params![stamp, section, entity, ident, action, path]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shapes_follow_python() {
        assert_eq!(shape("/api/opportunities/sources/x", "opportunities"), ("source".into(), None, "x".into()));
        assert_eq!(shape("/api/calendar", "calendar"), ("calendar".into(), None, "save".into()));
    }
}
