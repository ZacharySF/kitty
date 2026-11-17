//! The backlink routes (`kitty.core.backlinks` behind `web_schema.py` and `web_backlinks.py`
//! in Python), answered over the full-text index (crate::search):
//!
//! - `GET /api/schema/backlinks?map_id=<id>`: each map node's suggested references;
//!   `?term=<text>[&exclude=<kind>:<id>]`: the linked mentions of a free term;
//! - `POST /api/schema/backlinks/apply {map_id, revision, node_ids}`: link the suggested
//!   references of the chosen nodes into the map (a reference node and a "mentioned in" edge);
//! - `POST /api/backlinks/related {text, exclude?}`: the note pages and cards linked with,
//!   mentioning or related to a text.
//!
//! Matching, ranking, snippets (with their redaction), the stored map documents and every
//! refusal are Python's; tests/test_backlinks_parity.py compares them. Without ApplyAI jobs and
//! ApplyAI calendar copies are not searched and job sources show no live data.
//! `rika-server backlinks-text` (test-only) runs the text rules (tokens, phrases, wikilinks,
//! redaction, snippets) on stdin for the same test.

mod related;
mod scan;
pub(crate) mod text;

use serde_json::Value;

use crate::ported::{fail, internal, Call, Ctx, Done, Fault, Group};
use crate::schema::{self, Maps};
use crate::search::docs;

pub static GROUP: Group = Group { owns, module: Some("workspace"), answer };

const SCHEMA: &str = "/api/schema/backlinks";
const APPLY: &str = "/api/schema/backlinks/apply";
const RELATED: &str = "/api/backlinks/related";

fn owns(path: &str, mutation: bool) -> bool {
    if mutation { path == APPLY || path == RELATED } else { path == SCHEMA }
}

/// A search index failure as Python answered it: no index is its `SearchUnavailable`, a 500.
fn searched<T>(result: docs::Result<T>) -> Done<T> {
    result.map_err(|failure| match failure {
        docs::Failure::Database(error) => Fault::from(error),
        docs::Failure::Data(_) => internal(),
    })
}

/// `str(value)` of a record id: an integer, a string, or `None`.
fn str_of(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// `rika-server backlinks-text` (test-only): for every stdin line `{"text", "start", "end"}`,
/// one line `{"redact", "phrase", "tokens", "wikilinks", "snippet"}` as `kitty.core.backlinks`
/// computes them, for tests/test_backlinks_parity.py.
pub fn text_command() -> i32 {
    use std::io::BufRead;
    for line in std::io::stdin().lock().lines() {
        let Ok(request) = line.map_err(|_| ()).and_then(|line| serde_json::from_str::<Value>(&line).map_err(|_| ())) else {
            eprintln!("each line is a JSON object");
            return 2;
        };
        let given = request["text"].as_str().unwrap_or("");
        let span = |name: &str| request[name].as_u64().unwrap_or(0) as usize;
        let answer = serde_json::json!({
            "redact": text::redact(given),
            "phrase": text::phrase(given),
            "tokens": text::tokens(given),
            "wikilinks": text::wikilinks(given),
            "snippet": text::snippet(given, span("start"), span("end")),
        });
        println!("{answer}");
    }
    0
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let maps = Maps { conn: ctx.conn, applyai: ctx.has("applyai") };
    if let Some(body) = call.body {
        return Ok(Some(match call.path {
            APPLY => scan::apply(&maps, body)?,
            RELATED => {
                if body.keys().any(|key| key != "text" && key != "exclude") || !body.contains_key("text") {
                    return Err(fail(400, "Expected text and optional exclude"));
                }
                related::related(ctx.conn, &body["text"], body.get("exclude").unwrap_or(&Value::Null))?
            }
            _ => return Ok(None),
        }));
    }
    if call.path != SCHEMA {
        return Ok(None);
    }
    if call.query.len() == 1 && call.get("map_id").is_some() {
        let map_id = schema::parse_int(call.get("map_id").unwrap_or("")).map(schema::query_int).ok_or_else(|| fail(400, "Map ID must be an integer"))?;
        return Ok(Some(scan::suggestions(&maps, &map_id)?));
    }
    if let Some(term) = call.get("term").filter(|_| call.only(&["term", "exclude"])) {
        return Ok(Some(scan::mentions(ctx.conn, term, call.get("exclude"), maps.applyai)?));
    }
    Err(fail(400, "Provide map_id, or term with an optional exclude=<kind>:<id>"))
}
