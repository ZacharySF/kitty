//! `kitty.core.math.practice`: issued problems (`math_attempts` rows), graded answers and
//! lessons. The expected answer never leaves the server until the learner solves the problem
//! or uses up [`MAX_TRIES`] decided tries; only the first decided answer is graded into
//! mastery, and undecided answers cost nothing.
//!
//! An answer is checked in two steps around the check itself, as Python checked it outside
//! its write lock (SymPy can take seconds): [`begin_answer`] reads and validates, the check
//! runs (an expression on the worker, crate::math), and [`finish_answer`] writes.

use std::sync::{LazyLock, Mutex};

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::checking::{self, Verdict};
use super::mastery;
use super::problems::{self, Problem};
use super::pyrandom::Random;
use super::topics;
use crate::ported::db::{self, transaction};
use crate::ported::time::Dt;
use crate::ported::{fail, internal, py, Done};

pub const MAX_TRIES: usize = 3;
const SEED_MAX: i64 = (1 << 31) - 1;
const STOP_AFTER_MISSES: i64 = 3;
const GUIDED_STEPS: usize = 2;

pub fn not_found(message: &str) -> crate::ported::Fault {
    fail(404, message)
}

pub fn conflict(message: impl Into<String>) -> crate::ported::Fault {
    fail(409, message)
}

/// `_topic`: the topic's state, or 404 for an unknown topic and 409 for a locked one.
fn topic_state(conn: &Connection, topic_id: &Value, moment: &Dt) -> Done<(String, Map<String, Value>)> {
    let Some(topic) = topic_id.as_str().and_then(topics::by_id) else { return Err(not_found("Unknown math topic")) };
    let states = mastery::states(conn, moment)?;
    let state_of = |id: &str| states.iter().find(|item| item["id"] == id).cloned().ok_or_else(internal);
    let state = state_of(&topic.id)?;
    if state["state"] == "locked" {
        let mut missing = Vec::new();
        for prereq in &topic.prereqs {
            if state_of(prereq)?["state"] != "mastered" {
                missing.push(topics::by_id(prereq).map_or("", |topic| topic.title.as_str()));
            }
        }
        return Err(conflict(format!("Master {} first", missing.join(", "))));
    }
    Ok((topic.id.clone(), state))
}

/// `KITTY_TEST_MATH_RANDOM=<n>` (test-only): `secrets` draws come from one `random.Random(n)`,
/// as tests/test_math_parity.py makes Python's draw, so both pick the same seeds.
static TEST_RANDOM: LazyLock<Option<Mutex<Random>>> = LazyLock::new(|| {
    std::env::var("KITTY_TEST_MATH_RANDOM").ok().and_then(|seed| seed.parse::<u128>().ok()).map(|seed| Mutex::new(Random::from_int(seed)))
});

fn os_bits() -> u64 {
    let mut bytes = [0u8; 8];
    getrandom::fill(&mut bytes).expect("the operating system's random source failed");
    u64::from_le_bytes(bytes)
}

/// `secrets.randbelow(bound)`.
pub fn randbelow(bound: u64) -> u64 {
    if let Some(random) = TEST_RANDOM.as_ref() {
        return random.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).randbelow(bound);
    }
    let bits = 64 - bound.leading_zeros();
    loop {
        let value = os_bits() >> (64 - bits);
        if value < bound {
            return value;
        }
    }
}

/// `secrets.randbits(64)`.
fn randbits64() -> u64 {
    match TEST_RANDOM.as_ref() {
        Some(random) => random.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).getrandbits(64),
        None => os_bits(),
    }
}

/// `_seed(seed)`.
fn seed(value: Option<&Value>) -> Done<i64> {
    match value {
        None | Some(Value::Null) => Ok(randbelow(SEED_MAX as u64 + 1) as i64),
        Some(value) => py::int(value).filter(|seed| (0..=SEED_MAX).contains(seed))
            .ok_or_else(|| fail(400, format!("Seed must be an integer from 0 to {SEED_MAX}"))),
    }
}

/// `_public(problem, attempt_id, purpose)`.
fn public(problem: &Problem, attempt_id: &str, purpose: &str) -> Map<String, Value> {
    let mut out = Map::new();
    out.insert("problem_id".into(), json!(attempt_id));
    out.insert("topic_id".into(), json!(problem.topic_id));
    out.insert("purpose".into(), json!(purpose));
    out.insert("prompt_md".into(), json!(problem.prompt_md));
    out.insert("answer_kind".into(), json!(problem.answer_kind));
    if let Some(choices) = &problem.choices {
        out.insert("choices".into(), json!(choices));
    }
    if let Some(tolerance) = problem.tolerance {
        out.insert("tolerance".into(), json!(tolerance.to_f64()));
    }
    if let Some(sigfigs) = problem.sigfigs {
        out.insert("sigfigs".into(), json!(sigfigs));
    }
    if !problem.variables.is_empty() {
        out.insert("variables".into(), json!(problem.variables));
    }
    out
}

/// `_insert`.
fn insert(conn: &Connection, problem: &Problem, purpose: &str, moment: &Dt, step: Option<usize>) -> Done<String> {
    let ident = crate::auth::token_hex(16);
    conn.execute("INSERT INTO math_attempts(id, topic_id, template_id, seed, step, purpose, created_at) VALUES (?,?,?,?,?,?,?)",
                 rusqlite::params![ident, problem.topic_id, problem.template_id, problem.seed, step.map(|step| step as i64), purpose, moment.isoformat()])?;
    Ok(ident)
}

fn generate(template_id: &str, seed: i64) -> Done<Problem> {
    problems::generate(template_id, seed).map_err(|_| internal())
}

/// `issue(conn, topic_id, seed)`: a review once the topic is mastered, a lesson problem before.
pub fn issue(conn: &Connection, topic_id: &Value, seed_value: Option<&Value>, moment: &Dt) -> Done<Value> {
    let (topic_id, state) = topic_state(conn, topic_id, moment)?;
    let seed = seed(seed_value)?;
    let template = problems::pick(&topic_id, seed).ok_or_else(internal)?;
    let problem = generate(template.id, seed)?;
    let purpose = if state["state"] == "mastered" { "review" } else { "lesson" };
    let ident = transaction(conn, false, || insert(conn, &problem, purpose, moment, None))?;
    Ok(Value::Object(public(&problem, &ident, purpose)))
}

/// `_problem_for(row)`.
fn problem_for(row: &Map<String, Value>) -> Done<Problem> {
    let problem = generate(row.get("template_id").and_then(Value::as_str).unwrap_or(""), row.get("seed").and_then(Value::as_i64).unwrap_or(0))?;
    match row.get("step").and_then(Value::as_i64) {
        Some(step) => problem.step_problem(step as usize).ok_or_else(|| fail(400, "That step can't be faded")),
        None => Ok(problem),
    }
}

/// `_attempt(conn, problem_id)`.
fn attempt(conn: &Connection, problem_id: &Value) -> Done<Map<String, Value>> {
    let Some(id) = problem_id.as_str() else { return Err(fail(400, "problem_id must be text")) };
    db::row(conn, "SELECT * FROM math_attempts WHERE id=?", [id])?.ok_or_else(|| not_found("Unknown math problem"))
}

/// `_decided(entries)`.
pub fn decided(entries: &[Value]) -> usize {
    entries.iter().filter(|entry| entry["kind"] != "undecided").count()
}

/// The checks every answer route makes of the answer and its time first.
pub fn validate_answer(value: &Value, elapsed_ms: &Value) -> Done<i64> {
    if !matches!(value, Value::String(_) | Value::Number(_)) {
        return Err(fail(400, "answer must be text or a number"));
    }
    py::int(elapsed_ms).filter(|elapsed| (0..=86_400_000).contains(elapsed))
        .ok_or_else(|| fail(400, "elapsed_ms must be an integer from 0 to 86400000"))
}

/// The stored try: the answer (text cut to `MAX_ANSWER` characters), the verdict, the time.
pub fn entry(value: &Value, verdict: &Verdict, elapsed_ms: i64, moment: &Dt) -> Value {
    let answer = match value {
        Value::String(text) => json!(text.chars().take(checking::MAX_ANSWER).collect::<String>()),
        other => other.clone(),
    };
    json!({"answer": answer, "kind": verdict.kind, "feedback": verdict.feedback, "elapsed_ms": elapsed_ms, "at": moment.isoformat()})
}

/// What an answer needs checked: done here, or an expression for the worker.
pub enum Check {
    Done(Verdict),
    Expression,
}

/// An answer read and validated, before its check's result is written.
pub struct Begun {
    pub problem: Problem,
    row: Map<String, Value>,
    entries: Vec<Value>,
    pub value: Value,
    elapsed_ms: i64,
    pub check: Check,
}

/// `answer(...)` up to the check.
pub fn begin_answer(conn: &Connection, problem_id: &Value, value: &Value, elapsed_ms: &Value) -> Done<Begun> {
    let elapsed_ms = validate_answer(value, elapsed_ms)?;
    let row = attempt(conn, problem_id)?;
    let entries: Vec<Value> = serde_json::from_str(row.get("answers").and_then(Value::as_str).unwrap_or("[]"))?;
    if row.get("solved_at").is_some_and(py::truthy) || decided(&entries) >= MAX_TRIES {
        return Err(conflict("This problem is finished; ask for a new one"));
    }
    let problem = problem_for(&row)?;
    let check = if problem.answer_kind == "expression" {
        checking::expression_precheck(value).map_or(Check::Expression, Check::Done)
    } else {
        Check::Done(checking::check(&problem, value))
    };
    Ok(Begun { problem, row, entries, value: value.clone(), elapsed_ms, check })
}

/// `{key: state[key] for key in ("state", "progress", "due_at") if key in state}`.
fn summary(state: &Map<String, Value>) -> Value {
    let mut out = Map::new();
    for key in ["state", "progress", "due_at"] {
        if let Some(value) = state.get(key) {
            out.insert(key.into(), value.clone());
        }
    }
    Value::Object(out)
}

/// `answer(...)` from the check on: the try's row and mastery written in one immediate
/// transaction, then the answer without its `canonical` (added by the caller when `revealed`).
pub fn finish_answer(conn: &Connection, begun: Begun, verdict: &Verdict, moment: &Dt) -> Done<(Map<String, Value>, bool)> {
    let Begun { problem, row, mut entries, value, elapsed_ms, .. } = begun;
    let id = row.get("id").and_then(Value::as_str).unwrap_or("").to_owned();
    let topic_id = row.get("topic_id").and_then(Value::as_str).unwrap_or("").to_owned();
    transaction(conn, true, || {
        let current = db::row(conn, "SELECT answers, graded_at FROM math_attempts WHERE id=?", [&id])?.ok_or_else(internal)?;
        if current.get("answers") != row.get("answers") {
            return Err(conflict("This problem was answered in another window; reload it"));
        }
        entries.push(entry(&value, verdict, elapsed_ms, moment));
        let mut updates: Vec<(&str, rusqlite::types::Value)> = vec![("answers", py::dumps(&Value::Array(entries.clone())).into())];
        if let Some(correct) = verdict.correct {
            if current.get("graded_at").is_none_or(Value::is_null) {
                updates.push(("correct", i64::from(correct).into()));
                updates.push(("elapsed_ms", elapsed_ms.into()));
                updates.push(("graded_at", moment.isoformat().into()));
                if row.get("purpose").and_then(Value::as_str) != Some("guided") {
                    let rating = mastery::record(conn, &topic_id, correct, Some(elapsed_ms), problem.target_s, moment)?;
                    updates.push(("rating", rating.map_or(rusqlite::types::Value::Null, |rating| i64::from(rating).into())));
                }
            }
        }
        if verdict.correct == Some(true) {
            updates.push(("solved_at", moment.isoformat().into()));
        }
        let assignments: Vec<String> = updates.iter().map(|(key, _)| format!("{key}=?")).collect();
        let mut parameters: Vec<rusqlite::types::Value> = updates.into_iter().map(|(_, value)| value).collect();
        parameters.push(id.clone().into());
        conn.execute(&format!("UPDATE math_attempts SET {} WHERE id=?", assignments.join(", ")), rusqlite::params_from_iter(parameters))?;
        Ok(())
    })?;
    let tries = decided(&entries);
    let revealed = verdict.correct == Some(true) || (verdict.correct == Some(false) && tries >= MAX_TRIES);
    let mut out = Map::new();
    out.insert("correct".into(), json!(verdict.correct));
    out.insert("kind".into(), json!(verdict.kind));
    out.insert("tries_left".into(), json!(if verdict.correct == Some(true) { 0 } else { MAX_TRIES as i64 - tries as i64 }));
    out.insert("revealed".into(), json!(revealed));
    out.insert("mastery".into(), summary(&mastery::state_of(conn, &topic_id, moment)?));
    if let Some(feedback) = verdict.feedback.as_ref().filter(|feedback| !feedback.is_empty()) {
        out.insert("feedback".into(), json!(feedback));
    }
    Ok((out, revealed))
}

/// `_fadeable(problem)`.
fn fadeable(problem: &Problem) -> Vec<usize> {
    problem.steps.iter().enumerate().filter(|(_, step)| step.value.is_some()).map(|(index, _)| index).collect()
}

/// `lesson(conn, topic_id)`: a worked example, the same shape with its last steps faded, then
/// solo problems. The example's `answer_md` is left for the caller when it is an expression
/// (the returned problem's canonical is SymPy's).
pub fn lesson(conn: &Connection, topic_id: &str, moment: &Dt) -> Done<(Value, Problem)> {
    let (topic_id, state) = topic_state(conn, &json!(topic_id), moment)?;
    let mut rng = Random::from_int(u128::from(randbits64()));
    let (example, guided_problem) = lesson_problems(&topic_id, &mut rng)?;
    let guided = transaction(conn, false, || {
        let mut guided = Vec::new();
        let indices = fadeable(&guided_problem);
        for &index in &indices[indices.len().saturating_sub(GUIDED_STEPS)..] {
            let step = guided_problem.step_problem(index).ok_or_else(|| fail(400, "That step can't be faded"))?;
            let ident = insert(conn, &guided_problem, "guided", moment, Some(index))?;
            let mut item = public(&step, &ident, "guided");
            let mut steps: Vec<String> = guided_problem.steps[..index].iter().map(|line| line.md(false)).collect();
            steps.push(guided_problem.steps[index].md(true));
            item.insert("steps".into(), json!(steps));
            item.insert("blank_step".into(), json!(index));
            guided.push(Value::Object(item));
        }
        Ok(guided)
    })?;
    let recent: Vec<i64> = db::rows(conn, "SELECT correct FROM math_attempts WHERE topic_id=? AND purpose != 'guided' AND graded_at IS NOT NULL \
        ORDER BY graded_at DESC LIMIT 5", [&topic_id])?.iter().map(|row| row.get("correct").and_then(Value::as_i64).unwrap_or(0)).collect();
    let mut solo = 5;
    let progress = state.get("progress").and_then(Value::as_f64).unwrap_or(0.0);
    if (state["state"] == "learning" || state["state"] == "mastered") && progress > 0.0 {
        solo = 3; // expertise reversal: less scaffolding once it's going well
    }
    if recent.len() >= 3 && (recent.iter().sum::<i64>() as f64) / (recent.len() as f64) < 0.5 {
        solo = 8;
    }
    let title = topics::by_id(&topic_id).map_or("", |topic| topic.title.as_str());
    let answer_md = example.canonical();
    let answer = json!({
        "topic_id": topic_id, "title": title,
        "example": {"prompt_md": example.prompt_md, "steps": example.steps.iter().map(|step| step.md(false)).collect::<Vec<_>>(), "answer_md": answer_md},
        "guided": guided, "solo_count": solo, "stop_after_misses": STOP_AFTER_MISSES,
    });
    Ok((answer, example))
}

/// The lesson's example and guided problem for a seeded `random.Random`: the templates sorted
/// by (no fadeable step at seed 0, a random key), the first one's problems at two random seeds.
pub fn lesson_problems(topic_id: &str, rng: &mut Random) -> Done<(Problem, Problem)> {
    let mut keyed = Vec::new();
    for template in problems::for_topic(topic_id) {
        let plain = fadeable(&generate(template.id, 0)?).is_empty();
        keyed.push((plain, rng.random(), template));
    }
    keyed.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.total_cmp(&b.1)));
    let chosen = keyed.first().ok_or_else(internal)?.2;
    let example = generate(chosen.id, rng.randbelow(SEED_MAX as u64) as i64)?;
    let guided = generate(chosen.id, rng.randbelow(SEED_MAX as u64) as i64)?;
    Ok((example, guided))
}
