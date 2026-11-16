//! `kitty.core.math.library`: book sources on their own FSRS schedules.
//!
//! - **Textbook.** An exercise with an authored numeric answer is issued and graded like a
//!   topic problem (three tries; the first decided answer rates its card by correctness and
//!   time); a self-graded one is rated by the learner after a reveal. Rows hold exercise ids,
//!   never the book's text.
//! - **Own books.** A problem is logged by reference only (book, section, problem number) with
//!   the learner's answer and confidence, its card's first rating.

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::checking::{self, Verdict};
use super::mastery::{self, AGAIN};
use super::practice::{self, conflict, not_found, MAX_TRIES};
use super::problems::Problem;
use super::textbook::{self, Exercise};
use super::topics;
use crate::ported::db::{self, transaction};
use crate::ported::time::{fromisoformat, Dt};
use crate::ported::{fail, internal, py, Done, Fault};

const FIELDS: [&str; 5] = ["book", "section", "problem", "answer", "topic_id"];

fn str_of<'a>(row: &'a Map<String, Value>, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

/// `_rating(value, name)`.
fn rating(value: &Value, name: &str) -> Done<u8> {
    py::int(value).filter(|value| (1..=4).contains(value)).map(|value| value as u8)
        .ok_or_else(|| fail(400, format!("{name} must be 1 (Again), 2 (Hard), 3 (Good) or 4 (Easy)")))
}

/// `_card(row, moment)`.
fn card(row: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    let due = fromisoformat(str_of(row, "due_at")).ok_or_else(internal)?.le(moment);
    Ok(json!({"due_at": row.get("due_at"), "due": due, "reviews": row.get("reviews"), "lapses": row.get("lapses"), "last_rating": row.get("last_rating")}))
}

/// `_scheduled(card_json, rating, moment)`: the reviewed card's JSON and due time.
fn scheduled(card_json: Option<&str>, grade: u8, moment: &Dt) -> Done<(String, String)> {
    Ok(mastery::stored(&mastery::review(card_json, grade, moment)?))
}

// Textbook -------------------------------------------------------------------------------

/// `_exercise(exercise_id)`.
fn exercise<'a>(exercises: &'a [Exercise], exercise_id: &Value) -> Done<&'a Exercise> {
    let Some(id) = exercise_id.as_str() else { return Err(fail(400, "exercise_id must be text")) };
    exercises.iter().find(|exercise| exercise.id == id).ok_or_else(|| not_found("Unknown textbook exercise"))
}

/// `_summary(exercise, card, moment)`.
fn summary(exercise: &Exercise, card_row: Option<&Map<String, Value>>, moment: &Dt) -> Done<Map<String, Value>> {
    let mut out = Map::new();
    out.insert("id".into(), json!(exercise.id));
    out.insert("label".into(), json!(exercise.label));
    out.insert("section".into(), json!(exercise.section));
    out.insert("section_title".into(), json!(exercise.section_title));
    out.insert("topics".into(), json!(exercise.topics));
    out.insert("graded".into(), json!(exercise.graded()));
    out.insert("program".into(), exercise.program.clone());
    out.insert("figure".into(), exercise.figure.clone());
    out.insert("starred".into(), exercise.starred.clone());
    out.insert("card".into(), match card_row {
        Some(row) => card(row, moment)?,
        None => Value::Null,
    });
    Ok(out)
}

/// `source()`.
fn source() -> Value {
    let all = textbook::source();
    let mut out = Map::new();
    for key in ["title", "short", "version", "licence", "url"] {
        out.insert(key.into(), all.get(key).cloned().unwrap_or(Value::Null));
    }
    Value::Object(out)
}

fn cards(conn: &Connection) -> Done<Vec<Map<String, Value>>> {
    db::rows(conn, "SELECT * FROM math_textbook_cards", [])
}

/// `textbook_list(conn, query)`: every exercise, or a topic's: never reviewed first, then
/// due, then the rest.
pub fn textbook_list(conn: &Connection, query: &[(String, String)], moment: &Dt) -> Done<Value> {
    if query.iter().any(|(key, _)| key != "topic_id") {
        return Err(fail(400, "Unknown textbook parameter"));
    }
    let topic_id = query.iter().find(|(key, _)| key == "topic_id").map(|(_, value)| value.as_str());
    if topic_id.is_some_and(|topic| topics::by_id(topic).is_none()) {
        return Err(not_found("Unknown math topic"));
    }
    let exercises = textbook::exercises()?;
    let mut chosen: Vec<&Exercise> = match topic_id {
        None => exercises.iter().collect(),
        Some(topic) => exercises.iter().filter(|exercise| exercise.topics.iter().any(|item| item == topic)).collect(),
    };
    let cards = cards(conn)?;
    let card_of = |id: &str| cards.iter().find(|row| str_of(row, "exercise_id") == id);
    if topic_id.is_some() {
        let mut ranked = Vec::new();
        for exercise in chosen {
            let rank = match card_of(&exercise.id) {
                None => 0,
                Some(row) if fromisoformat(str_of(row, "due_at")).ok_or_else(internal)?.le(moment) => 1,
                Some(_) => 2,
            };
            ranked.push((rank, exercise));
        }
        ranked.sort_by_key(|(rank, _)| *rank); // stable: book order within each group
        chosen = ranked.into_iter().map(|(_, exercise)| exercise).collect();
    }
    let mut listed = Vec::new();
    for exercise in chosen {
        listed.push(Value::Object(summary(exercise, card_of(&exercise.id), moment)?));
    }
    Ok(json!({"source": source(), "exercises": listed}))
}

/// `textbook_item(conn, exercise_id)`.
pub fn textbook_item(conn: &Connection, exercise_id: &str, moment: &Dt) -> Done<Value> {
    let exercises = textbook::exercises()?;
    let exercise = exercise(&exercises, &json!(exercise_id))?;
    let card_row = db::row(conn, "SELECT * FROM math_textbook_cards WHERE exercise_id=?", [&exercise.id])?;
    let mut out = summary(exercise, card_row.as_ref(), moment)?;
    out.insert("text_md".into(), json!(exercise.text_md));
    out.insert("part".into(), json!(exercise.answer.as_ref().and_then(|answer| answer.part.clone())));
    out.insert("source".into(), source());
    Ok(Value::Object(out))
}

/// `_rate_textbook`: one rating of an exercise's card inside the caller's transaction.
fn rate_textbook(conn: &Connection, exercise_id: &str, grade: u8, moment: &Dt) -> Done<Map<String, Value>> {
    let stamp = moment.isoformat();
    let row = db::row(conn, "SELECT * FROM math_textbook_cards WHERE exercise_id=?", [exercise_id])?;
    let (card_json, due_at) = scheduled(row.as_ref().and_then(|row| row.get("fsrs")).and_then(Value::as_str), grade, moment)?;
    if row.is_none() {
        conn.execute("INSERT INTO math_textbook_cards(exercise_id, fsrs, due_at, reviews, last_rating, created_at, updated_at) VALUES (?,?,?,1,?,?,?)",
                     rusqlite::params![exercise_id, card_json, due_at, grade, stamp, stamp])?;
    } else {
        conn.execute("UPDATE math_textbook_cards SET fsrs=?, due_at=?, reviews=reviews+1, lapses=lapses+?, last_rating=?, updated_at=? WHERE exercise_id=?",
                     rusqlite::params![card_json, due_at, i64::from(grade == AGAIN), grade, stamp, exercise_id])?;
    }
    db::row(conn, "SELECT * FROM math_textbook_cards WHERE exercise_id=?", [exercise_id])?.ok_or_else(internal)
}

/// `textbook_rate(conn, body)`: a self-graded exercise's rating after the reveal.
pub fn textbook_rate(conn: &Connection, body: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    if body.len() != 2 || !body.contains_key("exercise_id") || !body.contains_key("rating") {
        return Err(fail(400, "Expected exercise_id and rating"));
    }
    let exercises = textbook::exercises()?;
    let exercise = exercise(&exercises, &body["exercise_id"])?;
    let grade = rating(&body["rating"], "rating")?;
    if exercise.answer.is_some() {
        return Err(conflict("This exercise has an answer to check; answer it instead"));
    }
    let row = transaction(conn, true, || rate_textbook(conn, &exercise.id, grade, moment))?;
    Ok(json!({"card": card(&row, moment)?}))
}

/// `textbook_issue(conn, exercise_id)`.
pub fn textbook_issue(conn: &Connection, exercise_id: &Value, moment: &Dt) -> Done<Value> {
    let exercises = textbook::exercises()?;
    let exercise = exercise(&exercises, exercise_id)?;
    let Some(problem) = textbook::problem(exercise) else { return Err(conflict("This exercise is self-graded; rate it after the reveal")) };
    let ident = crate::auth::token_hex(16);
    transaction(conn, false, || {
        conn.execute("INSERT INTO math_textbook_attempts(id, exercise_id, created_at) VALUES (?,?,?)", rusqlite::params![ident, exercise.id, moment.isoformat()])?;
        Ok(())
    })?;
    let mut out = Map::new();
    out.insert("problem_id".into(), json!(ident));
    out.insert("exercise_id".into(), json!(exercise.id));
    out.insert("prompt_md".into(), json!(problem.prompt_md));
    out.insert("answer_kind".into(), json!(problem.answer_kind));
    if let Some(sigfigs) = problem.sigfigs {
        out.insert("sigfigs".into(), json!(sigfigs));
    }
    if let Some(tolerance) = problem.tolerance {
        out.insert("tolerance".into(), json!(tolerance.to_f64()));
    }
    Ok(Value::Object(out))
}

/// `textbook_answer(conn, problem_id, value, elapsed_ms)`: one try; the first decided answer
/// rates the exercise's card.
pub fn textbook_answer(conn: &Connection, problem_id: &Value, value: &Value, elapsed_ms: &Value, moment: &Dt) -> Done<Value> {
    let elapsed_ms = practice::validate_answer(value, elapsed_ms)?;
    let Some(id) = problem_id.as_str() else { return Err(fail(400, "problem_id must be text")) };
    let row = db::row(conn, "SELECT * FROM math_textbook_attempts WHERE id=?", [id])?.ok_or_else(|| not_found("Unknown textbook problem"))?;
    let mut entries: Vec<Value> = serde_json::from_str(str_of(&row, "answers"))?;
    let decided = practice::decided(&entries);
    if row.get("solved_at").is_some_and(py::truthy) || decided >= MAX_TRIES {
        return Err(conflict("This exercise is finished; start it again for another try"));
    }
    let exercises = textbook::exercises()?;
    let exercise = exercise(&exercises, &json!(str_of(&row, "exercise_id")))?;
    let problem: Problem = textbook::problem(exercise).ok_or_else(|| fail(400, format!("{} is self-graded", exercise.id)))?;
    let verdict: Verdict = checking::check(&problem, value);
    let card_row = transaction(conn, true, || {
        let current = db::row(conn, "SELECT answers, graded_at FROM math_textbook_attempts WHERE id=?", [id])?.ok_or_else(internal)?;
        if current.get("answers") != row.get("answers") {
            return Err(conflict("This exercise was answered in another window; reload it"));
        }
        entries.push(practice::entry(value, &verdict, elapsed_ms, moment));
        let mut updates: Vec<(&str, rusqlite::types::Value)> = vec![("answers", py::dumps(&Value::Array(entries.clone())).into())];
        if let Some(correct) = verdict.correct {
            if current.get("graded_at").is_none_or(Value::is_null) {
                let grade = mastery::rating(correct, Some(elapsed_ms), problem.target_s);
                updates.push(("correct", i64::from(correct).into()));
                updates.push(("elapsed_ms", elapsed_ms.into()));
                updates.push(("rating", i64::from(grade).into()));
                updates.push(("graded_at", moment.isoformat().into()));
                rate_textbook(conn, str_of(&row, "exercise_id"), grade, moment)?;
            }
        }
        if verdict.correct == Some(true) {
            updates.push(("solved_at", moment.isoformat().into()));
        }
        let assignments: Vec<String> = updates.iter().map(|(key, _)| format!("{key}=?")).collect();
        let mut parameters: Vec<rusqlite::types::Value> = updates.into_iter().map(|(_, value)| value).collect();
        parameters.push(id.to_owned().into());
        conn.execute(&format!("UPDATE math_textbook_attempts SET {} WHERE id=?", assignments.join(", ")), rusqlite::params_from_iter(parameters))?;
        db::row(conn, "SELECT * FROM math_textbook_cards WHERE exercise_id=?", [str_of(&row, "exercise_id")])
    })?;
    let tries = decided + usize::from(verdict.correct.is_some());
    let revealed = verdict.correct == Some(true) || (verdict.correct == Some(false) && tries >= MAX_TRIES);
    let mut out = Map::new();
    out.insert("correct".into(), json!(verdict.correct));
    out.insert("kind".into(), json!(verdict.kind));
    out.insert("tries_left".into(), json!(if verdict.correct == Some(true) { 0 } else { MAX_TRIES as i64 - tries as i64 }));
    out.insert("revealed".into(), json!(revealed));
    out.insert("card".into(), match &card_row {
        Some(row) => card(row, moment)?,
        None => Value::Null,
    });
    if let Some(feedback) = verdict.feedback.as_ref().filter(|feedback| !feedback.is_empty()) {
        out.insert("feedback".into(), json!(feedback));
    }
    if revealed {
        out.insert("canonical".into(), json!(problem.canonical()));
    }
    Ok(Value::Object(out))
}

/// `due_textbook(conn, moment)`: (exercise, card row) for every due card, oldest due first.
pub fn due_textbook(conn: &Connection, moment: &Dt) -> Done<Vec<(Map<String, Value>, Map<String, Value>)>> {
    let rows = db::rows(conn, "SELECT * FROM math_textbook_cards WHERE due_at <= ? ORDER BY due_at", [moment.isoformat()])?;
    if rows.is_empty() {
        return Ok(Vec::new()); // the book's data loads only once it is in use
    }
    let exercises = textbook::exercises()?;
    let mut out = Vec::new();
    for row in rows {
        // An exercise dropped from a later edition of the data is skipped.
        if let Some(exercise) = exercises.iter().find(|exercise| exercise.id == str_of(&row, "exercise_id")) {
            let primary = &exercise.topics[0];
            let strategy = topics::by_id(primary).map_or("", |topic| topic.strategy.as_str());
            let mut item = Map::new();
            item.insert("kind".into(), json!("textbook"));
            item.insert("exercise_id".into(), json!(exercise.id));
            item.insert("label".into(), json!(exercise.label));
            item.insert("topic_id".into(), json!(primary));
            item.insert("title".into(), json!(exercise.section_title));
            item.insert("strategy".into(), json!(strategy));
            item.insert("due_at".into(), row.get("due_at").cloned().unwrap_or(Value::Null));
            item.insert("graded".into(), json!(exercise.graded()));
            out.push((item, row));
        }
    }
    Ok(out)
}

// Own books ------------------------------------------------------------------------------

/// `_text(name, value)`.
fn text(name: &str, value: &Value) -> Done<String> {
    let (low, high) = match name {
        "book" => (1, 120),
        "section" => (0, 60),
        "problem" => (1, 40),
        _ => (0, 200),
    };
    let Some(value) = value.as_str() else { return Err(fail(400, format!("{name} must be text"))) };
    let value = py::strip(value);
    if !(low..=high).contains(&py::len(value)) {
        return Err(fail(400, if low > 0 { format!("{name} must be {low}–{high} characters") } else { format!("{name} must be at most {high} characters") }));
    }
    if value.chars().any(|c| (c as u32) < 32 || c as u32 == 127) {
        return Err(fail(400, format!("{name} must be one line without control characters")));
    }
    Ok(value.to_owned())
}

/// `_topic_id(value)`.
fn topic_id(value: &Value) -> Done<Option<String>> {
    match value {
        Value::Null => Ok(None),
        Value::String(text) if text.is_empty() => Ok(None),
        Value::String(text) if topics::by_id(text).is_some() => Ok(Some(text.clone())),
        _ => Err(not_found("Unknown math topic")),
    }
}

/// `_clean(body)`: the given fields, checked, in `_FIELDS` order.
fn clean(body: &Map<String, Value>) -> Done<Vec<(&'static str, Option<String>)>> {
    let mut out = Vec::new();
    for name in FIELDS {
        if let Some(value) = body.get(name) {
            out.push((name, if name == "topic_id" { topic_id(value)? } else { Some(text(name, value)?) }));
        }
    }
    Ok(out)
}

/// `_entry(row, moment)`.
fn entry(row: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    let topic = row.get("topic_id").and_then(Value::as_str).filter(|id| !id.is_empty()).and_then(topics::by_id);
    let mut out = Map::new();
    for key in ["id", "book", "section", "problem", "answer", "confidence", "topic_id"] {
        out.insert(key.into(), row.get(key).cloned().unwrap_or(Value::Null));
    }
    out.insert("topic_title".into(), json!(topic.map(|topic| topic.title.as_str())));
    out.insert("created_at".into(), row.get("created_at").cloned().unwrap_or(Value::Null));
    out.insert("updated_at".into(), row.get("updated_at").cloned().unwrap_or(Value::Null));
    out.insert("card".into(), card(row, moment)?);
    Ok(Value::Object(out))
}

/// `_book_row(conn, entry_id)`.
fn book_row(conn: &Connection, entry_id: &str) -> Done<Map<String, Value>> {
    db::row(conn, "SELECT * FROM math_book_problems WHERE id=?", [entry_id])?.ok_or_else(|| not_found("Unknown book problem"))
}

/// One write of a book problem: `sqlite3.IntegrityError` (the same reference twice, or a value
/// a check refuses) is `_duplicate()`'s 409.
fn execute(conn: &Connection, sql: &str, parameters: impl rusqlite::Params) -> Done<()> {
    match conn.execute(sql, parameters) {
        Ok(_) => Ok(()),
        Err(rusqlite::Error::SqliteFailure(failure, _)) if failure.code == rusqlite::ErrorCode::ConstraintViolation => {
            Err(conflict("That problem is already logged; review it instead"))
        }
        Err(error) => Err(Fault::from(error)),
    }
}

/// `books(conn)`.
pub fn books(conn: &Connection, moment: &Dt) -> Done<Value> {
    let rows = db::rows(conn, "SELECT * FROM math_book_problems ORDER BY due_at, created_at", [])?;
    let mut statement = conn.prepare("SELECT book FROM math_book_problems GROUP BY book ORDER BY count(*) DESC, max(updated_at) DESC, book")?;
    let names = statement.query_map([], |row| row.get::<_, String>(0))?.collect::<Result<Vec<_>, _>>()?;
    let mut problems = Vec::new();
    let mut due = 0;
    for row in &rows {
        let item = entry(row, moment)?;
        due += i64::from(item["card"]["due"] == true);
        problems.push(item);
    }
    Ok(json!({"problems": problems, "books": names, "due": due}))
}

/// `add_book_problem(conn, body)`.
pub fn add_book_problem(conn: &Connection, body: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    let allowed = |key: &str| FIELDS.contains(&key) || key == "confidence";
    if body.keys().any(|key| !allowed(key)) || ["book", "problem", "confidence"].iter().any(|key| !body.contains_key(*key)) {
        return Err(fail(400, "Expected book, problem, confidence and optional section, answer and topic_id"));
    }
    let cleaned = clean(body)?;
    let field = |name: &str, default: Option<String>| cleaned.iter().find(|(key, _)| *key == name).map_or(default, |(_, value)| value.clone());
    let confidence = rating(&body["confidence"], "confidence")?;
    let (card_json, due_at) = scheduled(None, confidence, moment)?;
    let (ident, stamp) = (crate::auth::token_hex(16), moment.isoformat());
    transaction(conn, true, || {
        execute(conn, "INSERT INTO math_book_problems(id, book, section, problem, answer, confidence, topic_id, fsrs, due_at, \
            reviews, last_rating, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
                rusqlite::params![ident, field("book", None), field("section", Some(String::new())), field("problem", None),
                                  field("answer", Some(String::new())), confidence, field("topic_id", None), card_json, due_at, confidence, stamp, stamp])
    })?;
    entry(&book_row(conn, &ident)?, moment)
}

/// `edit_book_problem(conn, entry_id, body)`.
pub fn edit_book_problem(conn: &Connection, entry_id: &str, body: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    if body.is_empty() || body.keys().any(|key| !FIELDS.contains(&key.as_str())) {
        return Err(fail(400, "Expected some of book, section, problem, answer and topic_id"));
    }
    book_row(conn, entry_id)?;
    let cleaned = clean(body)?;
    let assignments: Vec<String> = cleaned.iter().map(|(key, _)| format!("{key}=?")).collect();
    let mut parameters: Vec<rusqlite::types::Value> = cleaned.into_iter().map(|(_, value)| value.map_or(rusqlite::types::Value::Null, Into::into)).collect();
    parameters.push(moment.isoformat().into());
    parameters.push(entry_id.to_owned().into());
    transaction(conn, true, || {
        execute(conn, &format!("UPDATE math_book_problems SET {}, updated_at=? WHERE id=?", assignments.join(", ")), rusqlite::params_from_iter(parameters))
    })?;
    entry(&book_row(conn, entry_id)?, moment)
}

/// `rate_book_problem(conn, entry_id, body)`.
pub fn rate_book_problem(conn: &Connection, entry_id: &str, body: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    if !body.contains_key("rating") || body.keys().any(|key| key != "rating" && key != "answer") {
        return Err(fail(400, "Expected rating and an optional answer"));
    }
    let grade = rating(&body["rating"], "rating")?;
    let answer = body.get("answer").map(|value| text("answer", value)).transpose()?;
    let row = book_row(conn, entry_id)?;
    let (card_json, due_at) = scheduled(row.get("fsrs").and_then(Value::as_str), grade, moment)?;
    transaction(conn, true, || {
        conn.execute("UPDATE math_book_problems SET fsrs=?, due_at=?, reviews=reviews+1, lapses=lapses+?, last_rating=?, \
            answer=coalesce(?, answer), updated_at=? WHERE id=?",
                     rusqlite::params![card_json, due_at, i64::from(grade == AGAIN), grade, answer, moment.isoformat(), entry_id])?;
        Ok(())
    })?;
    entry(&book_row(conn, entry_id)?, moment)
}

/// `delete_book_problem(conn, entry_id)`.
pub fn delete_book_problem(conn: &Connection, entry_id: &str) -> Done<Value> {
    book_row(conn, entry_id)?;
    transaction(conn, true, || {
        conn.execute("DELETE FROM math_book_problems WHERE id=?", [entry_id])?;
        Ok(())
    })?;
    Ok(json!({"ok": true}))
}

/// `due_books(conn, moment)`, as mixed review's items.
pub fn due_books(conn: &Connection, moment: &Dt) -> Done<Vec<Map<String, Value>>> {
    let mut out = Vec::new();
    for row in db::rows(conn, "SELECT * FROM math_book_problems WHERE due_at <= ? ORDER BY due_at", [moment.isoformat()])? {
        let topic = row.get("topic_id").and_then(Value::as_str).filter(|id| !id.is_empty()).and_then(topics::by_id);
        let mut item = Map::new();
        item.insert("kind".into(), json!("book"));
        item.insert("entry_id".into(), row.get("id").cloned().unwrap_or(Value::Null));
        item.insert("label".into(), json!(book_label(&row)));
        item.insert("topic_id".into(), json!(topic.map(|topic| topic.id.as_str())));
        // Without a topic the method is unknown, so the problem never blocks another.
        item.insert("strategy".into(), json!(topic.map_or_else(|| format!("book:{}", str_of(&row, "id")), |topic| topic.strategy.clone())));
        item.insert("due_at".into(), row.get("due_at").cloned().unwrap_or(Value::Null));
        out.push(item);
    }
    Ok(out)
}

/// `book_label(row)`: 'Zhou §2.3 #14', the reference, nothing quoted.
fn book_label(row: &Map<String, Value>) -> String {
    let section = str_of(row, "section");
    [str_of(row, "book").to_owned(), if section.is_empty() { String::new() } else { format!("§{section}") }, format!("#{}", str_of(row, "problem"))]
        .into_iter().filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" ")
}
