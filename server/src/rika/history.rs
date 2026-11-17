//! `kitty.core.rika.store.history`: messages, actions, approvals, plans and email drafts
//! older than `before`, the newest fifty returned oldest first, in `store`'s and
//! `mail.draft_shape`'s public shapes (a plan's items through `dumps.public_items`).

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::ported::{db, internal, py, Done};
use crate::search::text::split;

const PAGE: usize = 50;
/// The tools whose effect is `outward` (`kitty.core.rika.tools.registry()`): a plan item
/// calling one of them says so. The parity scenario calls every registered tool.
const OUTWARD: [&str; 5] = ["delete_page", "run_command", "send_reply", "set_modules", "start_worker"];
/// `mail.HISTORY_TABLE`.
const MAIL: &str = "(SELECT d.*, m.sender AS original_sender, m.sender_email AS original_email, m.subject AS original_subject, \
                    m.received_at AS original_received, m.body AS original_body FROM rika_mail_drafts d LEFT JOIN \
                    rika_mail_messages m ON m.id=d.mail_id WHERE d.status!='discarded')";

type Row = Map<String, Value>;

/// `row[key]`.
fn at<'a>(row: &'a Row, key: &str) -> Done<&'a Value> {
    row.get(key).ok_or_else(internal)
}

fn field(row: &Row, key: &str) -> Done<Value> {
    at(row, key).cloned()
}

/// `float(value) if value is not None else None`.
fn money(value: &Value) -> Done<Value> {
    let number = match value {
        Value::Null => return Ok(Value::Null),
        Value::String(text) => crate::search::text::strip(text).parse::<f64>().map_err(|_| internal())?,
        Value::Number(number) => number.as_f64().ok_or_else(internal)?,
        _ => return Err(internal()),
    };
    serde_json::Number::from_f64(number).map(Value::Number).ok_or_else(internal)
}

/// `json.loads(text)` of a stored column.
fn loads(value: &Value) -> Done<Value> {
    Ok(serde_json::from_str(value.as_str().ok_or_else(internal)?)?)
}

/// `dict.get` on a decoded JSON value, which must be an object.
fn get<'a>(object: &'a Value, key: &str) -> Done<Option<&'a Value>> {
    Ok(object.as_object().ok_or_else(internal)?.get(key))
}

/// `store._usage`.
fn usage(row: &Row) -> Done<Value> {
    let counts = [field(row, "tokens_in")?, field(row, "tokens_cached")?, field(row, "tokens_out")?];
    let route_json = at(row, "route_json")?;
    if at(row, "role")? != "rika" || (route_json.is_null() && counts.iter().all(Value::is_null)) {
        return Ok(Value::Null);
    }
    let route = if py::truthy(route_json) { loads(route_json)? } else { json!({}) };
    let [tokens_in, tokens_cached, tokens_out] = counts;
    Ok(json!({"tokens_in": tokens_in, "tokens_cached": tokens_cached, "tokens_out": tokens_out,
              "path": get(&route, "path")?.cloned().unwrap_or_else(|| json!([])),
              "signature": get(&route, "signature")?.cloned().unwrap_or_else(|| json!("")),
              "remembered": get(&route, "remembered")?.is_some_and(py::truthy),
              "shortcut": get(&route, "shortcut")?.is_some_and(py::truthy)}))
}

fn message(row: &Row) -> Done<Value> {
    Ok(json!({"kind": "message", "id": field(row, "id")?, "conversation_id": field(row, "conversation_id")?,
              "role": field(row, "role")?, "text": field(row, "text")?, "reply_to": field(row, "reply_to")?,
              "tier": field(row, "tier")?, "model": field(row, "model")?, "status": field(row, "status")?,
              "source": field(row, "source")?, "cost_usd": money(at(row, "cost_usd")?)?, "usage": usage(row)?,
              "created_at": field(row, "created_at")?}))
}

fn action(row: &Row) -> Done<Value> {
    Ok(json!({"kind": "action", "id": field(row, "id")?, "message_id": field(row, "message_id")?, "tool": field(row, "tool")?,
              "summary": field(row, "summary")?, "reversible": py::truthy(at(row, "reversible")?), "status": field(row, "status")?,
              "created_at": field(row, "created_at")?}))
}

fn approval(row: &Row) -> Done<Value> {
    Ok(json!({"kind": "approval", "id": field(row, "id")?, "message_id": field(row, "message_id")?, "tool": field(row, "tool")?,
              "summary": field(row, "summary")?, "detail": field(row, "detail")?, "cost_usd": money(at(row, "cost_usd")?)?,
              "status": field(row, "status")?, "created_at": field(row, "created_at")?}))
}

/// `value[key]` on a decoded JSON object (a `KeyError` or `TypeError` is a 500).
fn item<'a>(value: &'a Value, key: &str) -> Done<&'a Value> {
    get(value, key)?.ok_or_else(internal)
}

/// A list Python iterates.
fn list(value: &Value) -> Done<&Vec<Value>> {
    value.as_array().ok_or_else(internal)
}

/// `len(value)`.
fn length(value: &Value) -> Done<usize> {
    Ok(match value {
        Value::Array(items) => items.len(),
        Value::Object(map) => map.len(),
        Value::String(text) => text.chars().count(),
        _ => return Err(internal()),
    })
}

/// `entry["question"]["choices"][entry["choice"] or 0]["calls"]`, negative indices from the end.
fn chosen_calls<'a>(question: &'a Value, choice: &Value) -> Done<&'a Value> {
    let choices = list(item(question, "choices")?)?;
    let index = match choice {
        _ if !py::truthy(choice) => 0,
        Value::Bool(true) => 1,
        Value::Number(number) => number.as_i64().ok_or_else(internal)?,
        _ => return Err(internal()),
    };
    let position = if index < 0 { i64::try_from(choices.len()).map_err(|_| internal())? + index } else { index };
    let choice = usize::try_from(position).ok().and_then(|position| choices.get(position)).ok_or_else(internal)?;
    item(choice, "calls")
}

/// `dumps.public_items`: plan items for the page, without tool arguments.
fn public_items(items: &Value) -> Done<Vec<Value>> {
    let mut shown = Vec::new();
    for entry in list(items)? {
        let question = item(entry, "question")?;
        let own = item(entry, "calls")?;
        let mut calls: Vec<&Value> = Vec::new();
        if py::truthy(own) {
            calls.extend(list(own)?);
        } else if py::truthy(question) {
            if let Some(choices) = get(question, "choices")? {
                for choice in list(choices)? {
                    calls.extend(list(item(choice, "calls")?)?);
                }
            }
        }
        let count = length(if question.is_null() { own } else { chosen_calls(question, item(entry, "choice")?)? })?;
        let mut outward = false;
        for call in calls {
            match item(call, "tool")? {
                Value::String(tool) if OUTWARD.contains(&tool.as_str()) => {
                    outward = true;
                    break;
                }
                Value::Array(_) | Value::Object(_) => return Err(internal()),
                _ => {}
            }
        }
        let shown_question = if question.is_null() {
            Value::Null
        } else {
            let labels = list(item(question, "choices")?)?.iter().map(|choice| item(choice, "label").cloned()).collect::<Done<Vec<_>>>()?;
            json!({"text": item(question, "text")?, "choices": labels})
        };
        let slot = get(entry, "slot")?.filter(|slot| py::truthy(slot));
        shown.push(json!({"id": item(entry, "id")?, "area": item(entry, "area")?, "title": item(entry, "title")?,
                          "detail": item(entry, "detail")?, "source": item(entry, "source")?, "count": count, "outward": outward,
                          "question": shown_question, "choice": item(entry, "choice")?,
                          "picked": get(entry, "picked")?.cloned().unwrap_or(Value::Null), "result": item(entry, "result")?,
                          "date": match slot { Some(slot) => json!({"time": item(slot, "time")?}), None => Value::Null }}));
    }
    Ok(shown)
}

fn plan(conn: &Connection, row: &Row) -> Done<Value> {
    let page = at(row, "note_page_id")?;
    let note = if page.is_null() {
        Value::Null
    } else {
        let found = db::row(conn, "SELECT id,title FROM note_page_index WHERE id=?", [db::bind(page)?])?;
        found.map_or(Value::Null, |found| json!({"page_id": found["id"], "title": found["title"]}))
    };
    Ok(json!({"kind": "plan", "plan_kind": field(row, "kind")?, "id": field(row, "id")?, "message_id": field(row, "message_id")?,
              "request_id": field(row, "request_id")?, "items": public_items(&loads(at(row, "items_json")?)?)?,
              "duration": field(row, "duration_s")?, "note": note, "status": field(row, "status")?,
              "created_at": field(row, "created_at")?, "decided_at": field(row, "decided_at")?}))
}

/// `mail._snippet`.
fn snippet(body: &Value) -> Done<String> {
    let body = if py::truthy(body) { body.as_str().ok_or_else(internal)? } else { "" };
    Ok(split(body).collect::<Vec<_>>().join(" ").chars().take(200).collect())
}

/// `mail.draft_shape`.
fn draft(row: &Row) -> Done<Value> {
    let original = if at(row, "original_email")?.is_null() {
        Value::Null
    } else {
        json!({"sender": field(row, "original_sender")?, "sender_email": field(row, "original_email")?,
               "subject": field(row, "original_subject")?, "received_at": field(row, "original_received")?,
               "snippet": snippet(at(row, "original_body")?)?})
    };
    Ok(json!({"kind": "mail", "id": field(row, "id")?, "mail_id": field(row, "mail_id")?, "message_id": field(row, "message_id")?,
              "thread_id": field(row, "thread_id")?, "to": field(row, "to_address")?, "subject": field(row, "subject")?,
              "body": field(row, "body")?, "reply_kind": field(row, "kind")?, "status": field(row, "status")?,
              "auto": py::truthy(at(row, "auto")?), "send_after": field(row, "send_after")?, "sent_at": field(row, "sent_at")?,
              "error": field(row, "error")?, "original": original, "created_at": field(row, "created_at")?,
              "updated_at": field(row, "updated_at")?}))
}

/// `store.history(conn, before)`.
pub fn history(conn: &Connection, before: Option<&str>) -> Done<Value> {
    let bound = before.unwrap_or("~"); // sorts after every ISO timestamp
    let mut items: Vec<(String, Value)> = Vec::new();
    for table in ["rika_messages", "rika_actions", "rika_approvals", "rika_plans", MAIL] {
        let sql = format!("SELECT * FROM {table} WHERE created_at<? ORDER BY created_at DESC LIMIT ?");
        for row in db::rows(conn, &sql, rusqlite::params![bound, PAGE as i64 + 1])? {
            let shape = match table {
                "rika_messages" => message(&row)?,
                "rika_actions" => action(&row)?,
                "rika_approvals" => approval(&row)?,
                "rika_plans" => plan(conn, &row)?,
                _ => draft(&row)?,
            };
            items.push((at(&row, "created_at")?.as_str().ok_or_else(internal)?.to_owned(), shape));
        }
    }
    // `sort(key=..., reverse=True)` is stable: ties keep the tables' order.
    items.sort_by(|a, b| b.0.cmp(&a.0));
    let more = items.len() > PAGE;
    items.truncate(PAGE);
    items.reverse();
    let next = if more { items.first().map_or(Value::Null, |(stamp, _)| Value::from(stamp.as_str())) } else { Value::Null };
    Ok(json!({"items": items.into_iter().map(|(_, item)| item).collect::<Vec<_>>(), "has_more": more, "next_before": next}))
}
