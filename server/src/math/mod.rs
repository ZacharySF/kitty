//! The math routes (`kitty.web_math` and `kitty.core.math` in Python), answered over the
//! workspace database: topics and the next task, problems, answers, lessons, sprints, mixed
//! review, Grinstead & Snell exercises and own-book problems.
//!
//! Problem generation is Python's to the character (crate::math::pyrandom is CPython's
//! `random.Random`, crate::math::templates the generators), so a stored `(template_id, seed)`
//! gives the same problem on either side. Two things stay on the worker: SymPy (an expression
//! answer's equivalence and its TeX, the worker's `POST /api/internal/math/expression`, which
//! pages cannot reach) and `POST /api/math/explain` (an AI review proposal, forwarded).
//!
//! `rika-server math` (test-only) answers generation, checks, lesson draws, sprints and the
//! textbook's topic links on stdin for tests/test_math_parity.py.

mod big;
mod checking;
mod frac;
mod library;
mod mastery;
mod mixed;
mod practice;
mod problems;
mod pyrandom;
mod sprints;
mod templates;
mod textbook;
mod topics;

use std::path::PathBuf;

use bytes::Bytes;
use http_body_util::{BodyExt, Full, Limited};
use hyper::body::Incoming;
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use serde_json::{json, Map, Value};
use tokio::net::TcpStream;

use crate::front::{content_length, json_object, refuse, send_json, Backend, Body, Front, Refusal};
use crate::ported::db;
use crate::ported::time::Dt;
use crate::ported::{disabled, fail, internal, Done, Fault};
use crate::target::{parse_target, Target};
use problems::{Answer, Problem};

const PREFIX: &str = "/api/math";
/// Answered by the worker: the AI review proposal.
const EXPLAIN: &str = "/api/math/explain";
/// The worker's SymPy checks (`kitty.web_math`); pages never reach `/api/internal/*`.
const EXPRESSION: &str = "/api/internal/math/expression";

/// The folder of the textbook's `exercises.json`, as the worker reports it.
pub fn start(textbook_dir: Option<PathBuf>) {
    if let Some(directory) = textbook_dir {
        textbook::set_directory(directory);
    }
}

fn owns(path: &str, mutation: bool) -> bool {
    (path == PREFIX || path.starts_with("/api/math/")) && !(mutation && path == EXPLAIN)
}

/// The math routes; None for any other request (and for the forwarded explain route).
pub async fn route(front: &Front, target: &Target, forward_target: &str, request: &mut Request<Incoming>, mutation: bool,
                   upgrade: bool) -> Option<Result<Response<Body>, Refusal>> {
    if upgrade || !owns(&target.path, mutation) {
        return None;
    }
    let head = request.method() == Method::HEAD;
    Some(answer(front, forward_target, request, mutation).await.map(|value| send_json(StatusCode::OK, &value, head)))
}

fn refused(fault: Fault) -> Refusal {
    refuse(fault.status, fault.message)
}

/// A mutation's JSON object body (`RequestHandler._body`).
async fn body(request: &mut Request<Incoming>) -> Result<Map<String, Value>, Refusal> {
    let length = content_length(request.headers())?;
    let collected = Limited::new(request.body_mut(), length as usize).collect().await.map_err(|_| refuse(400, "Incomplete JSON body"))?.to_bytes();
    if collected.len() as u64 != length {
        return Err(refuse(400, "Incomplete JSON body"));
    }
    json_object(&collected)
}

/// `WebApplication.guard`: the module, then its pack.
fn guard(front: &Front) -> Done<()> {
    if !front.workspace.active.iter().any(|module| module == "math") {
        return Err(disabled("math"));
    }
    if front.workspace.missing.iter().any(|pack| pack == "math") {
        let label = crate::modules::registry().packs.iter().find(|pack| pack.id == "math").map_or("math", |pack| pack.label.as_str());
        return Err(fail(409, format!("{label} isn't installed: download it in Settings > Modules, then restart rika!")));
    }
    Ok(())
}

/// `_keys(body, allowed, required)`.
fn keys(body: &Map<String, Value>, allowed: &[&str], required: &[&str]) -> Done<()> {
    if body.keys().any(|key| !allowed.contains(&key.as_str())) || required.iter().any(|key| !body.contains_key(*key)) {
        let mut sorted = allowed.to_vec();
        sorted.sort_unstable();
        return Err(fail(400, format!("Expected {}", sorted.join(", "))));
    }
    Ok(())
}

fn no_query(target: &Target) -> Done<()> {
    if target.query.is_empty() { Ok(()) } else { Err(fail(400, "This route does not accept query parameters")) }
}

/// One step on the workspace connection, off the async workers.
fn on_db<T>(front: &Front, work: impl FnOnce(&rusqlite::Connection) -> Done<T>) -> Done<T> {
    tokio::task::block_in_place(|| db::with(front.bootstrap.db_path(), work))
}

/// `[0-9a-f]{32}`.
fn is_id(text: &str) -> bool {
    text.len() == 32 && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// `[a-z][a-z0-9.-]{0,63}`.
fn is_topic(text: &str) -> bool {
    let bytes = text.as_bytes();
    (1..=64).contains(&bytes.len()) && bytes[0].is_ascii_lowercase()
        && bytes[1..].iter().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'.' || *b == b'-')
}

/// `[a-z]{1,8}(?:\.[0-9a-z]{1,4}){1,4}`.
fn is_exercise(text: &str) -> bool {
    let mut parts = text.split('.');
    let head = parts.next().unwrap_or("");
    let rest: Vec<&str> = parts.collect();
    (1..=8).contains(&head.len()) && head.bytes().all(|b| b.is_ascii_lowercase()) && (1..=4).contains(&rest.len())
        && rest.iter().all(|part| (1..=4).contains(&part.len()) && part.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit()))
}

/// The id in `/api/math/<group>/<id><tail>`.
fn id_in<'a>(path: &'a str, group: &str, tail: &str) -> Option<&'a str> {
    path.strip_prefix(group)?.strip_suffix(tail).filter(|id| is_id(id))
}

async fn answer(front: &Front, forward_target: &str, request: &mut Request<Incoming>, mutation: bool) -> Result<Value, Refusal> {
    // The query as the worker would have seen it: a spent link ticket removed.
    let target = parse_target(forward_target)?;
    let body = if mutation {
        no_query(&target).map_err(refused)?;
        Some(body(request).await?)
    } else {
        None
    };
    guard(front).map_err(refused)?;
    let path = target.path.as_str();
    if !path.starts_with("/api/math/") {
        return Err(refuse(404, "API route not found"));
    }
    let moment = Dt::now_utc();
    match body {
        None => get(front, path, &target, &moment).await,
        Some(body) => post(front, path, &body, &moment).await,
    }.map_err(refused)
}

async fn get(front: &Front, path: &str, target: &Target, moment: &Dt) -> Done<Value> {
    let query = &target.query;
    if let Some(topic) = path.strip_prefix("/api/math/lessons/").filter(|topic| is_topic(topic)) {
        let (mut lesson, example) = on_db(front, |conn| {
            topics::sync(conn)?;
            no_query(target)?;
            practice::lesson(conn, topic, moment)
        })?;
        if example.answer_kind == "expression" {
            lesson["example"]["answer_md"] = json!(expression(front, &example, None).await?.1.ok_or_else(internal)?);
        }
        return Ok(lesson);
    }
    on_db(front, |conn| {
        topics::sync(conn)?;
        match path {
            "/api/math/topics" => {
                no_query(target)?;
                Ok(json!({"topics": mastery::states(conn, moment)?}))
            }
            "/api/math/next" => {
                no_query(target)?;
                mastery::next_task(conn, moment)
            }
            "/api/math/sprints/history" => sprints::history(conn, query, moment),
            "/api/math/mixed" => {
                no_query(target)?;
                mixed::queue(conn, moment)
            }
            "/api/math/textbook" => library::textbook_list(conn, query, moment),
            "/api/math/books" => {
                no_query(target)?;
                library::books(conn, moment)
            }
            _ => match path.strip_prefix("/api/math/textbook/").filter(|id| is_exercise(id)) {
                Some(id) => {
                    no_query(target)?;
                    library::textbook_item(conn, id, moment)
                }
                None => Err(fail(404, "API route not found")),
            },
        }
    })
}

async fn post(front: &Front, path: &str, body: &Map<String, Value>, moment: &Dt) -> Done<Value> {
    const ANSWER_KEYS: [&str; 3] = ["problem_id", "answer", "elapsed_ms"];
    if path == "/api/math/answers" {
        let begun = on_db(front, |conn| {
            topics::sync(conn)?;
            keys(body, &ANSWER_KEYS, &ANSWER_KEYS)?;
            practice::begin_answer(conn, &body["problem_id"], &body["answer"], &body["elapsed_ms"])
        })?;
        let (verdict, canonical) = match &begun.check {
            practice::Check::Done(verdict) => (verdict.clone(), begun.problem.canonical()),
            practice::Check::Expression => {
                let (verdict, canonical) = expression(front, &begun.problem, Some(&begun.value)).await?;
                (verdict.ok_or_else(internal)?, canonical)
            }
        };
        let (mut out, revealed) = on_db(front, |conn| practice::finish_answer(conn, begun, &verdict, moment))?;
        if revealed {
            out.insert("canonical".into(), json!(canonical.ok_or_else(internal)?));
        }
        return Ok(Value::Object(out));
    }
    on_db(front, |conn| {
        topics::sync(conn)?;
        match path {
            "/api/math/problems" => {
                keys(body, &["topic_id", "seed"], &["topic_id"])?;
                practice::issue(conn, &body["topic_id"], body.get("seed"), moment)
            }
            "/api/math/sprints" => sprints::start(conn, body, moment),
            "/api/math/textbook/problems" => {
                keys(body, &["exercise_id"], &["exercise_id"])?;
                library::textbook_issue(conn, &body["exercise_id"], moment)
            }
            "/api/math/textbook/answers" => {
                keys(body, &ANSWER_KEYS, &ANSWER_KEYS)?;
                library::textbook_answer(conn, &body["problem_id"], &body["answer"], &body["elapsed_ms"], moment)
            }
            "/api/math/textbook/ratings" => library::textbook_rate(conn, body, moment),
            "/api/math/books" => library::add_book_problem(conn, body, moment),
            _ => {
                if let Some(id) = id_in(path, "/api/math/sprints/", "/result") {
                    return sprints::finish(conn, id, body, moment);
                }
                if let Some(id) = id_in(path, "/api/math/books/", "/ratings") {
                    return library::rate_book_problem(conn, id, body, moment);
                }
                if let Some(id) = id_in(path, "/api/math/books/", "/delete") {
                    keys(body, &[], &[])?;
                    return library::delete_book_problem(conn, id);
                }
                if let Some(id) = id_in(path, "/api/math/books/", "") {
                    return library::edit_book_problem(conn, id, body, moment);
                }
                Err(fail(404, "API route not found"))
            }
        }
    })
}

/// SymPy on the worker for an expression problem: the verdict on `answer` (when given) and
/// the answer's TeX (`Problem.canonical`; None if SymPy could not write it).
async fn expression(front: &Front, problem: &Problem, answer: Option<&Value>) -> Done<(Option<checking::Verdict>, Option<String>)> {
    let expected = problem.answer.text().ok_or_else(internal)?;
    let mistakes: Vec<Value> = problem.mistakes.iter().filter_map(|(value, message)| match value {
        Answer::Text(text) => Some(json!([text, message])),
        Answer::Num(_) => None,
    }).collect();
    let mut request = json!({"expected": expected, "variables": problem.variables, "up_to_constant": problem.up_to_constant, "mistakes": mistakes});
    if let Some(answer) = answer {
        request["answer"] = answer.clone();
    }
    let done = call(&front.backend, EXPRESSION, &request).await?;
    let verdict = done.get("kind").and_then(Value::as_str)
        .map(|kind| checking::from_kind(kind, done.get("feedback").and_then(Value::as_str).map(str::to_owned)));
    Ok((verdict, done.get("canonical").and_then(Value::as_str).map(str::to_owned)))
}

/// POST `body` to one of the worker's internal routes, as a forwarded mutation (its Host, UI
/// secret and front secret, `X-Kitty-Request`, JSON): its answer, or its error as Python sent it.
async fn call(backend: &Backend, path: &str, body: &Value) -> Done<Value> {
    let busy = || fail(503, "Local workspace is busy or unavailable; try again");
    let data = serde_json::to_vec(body).expect("JSON values serialize");
    let request = Request::builder()
        .method(Method::POST)
        .uri(path)
        .header("host", &backend.authority)
        .header("x-kitty-secret", &backend.secret)
        .header("x-kitty-front", &backend.front_secret)
        .header("x-kitty-request", "1")
        .header("content-type", "application/json")
        .header("content-length", data.len())
        .body(Full::new(Bytes::from(data)))
        .map_err(|_| internal())?;
    let stream = TcpStream::connect(&backend.authority).await.map_err(|_| busy())?;
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|_| busy())?;
    tokio::spawn(async move {
        let _ = connection.await;
    });
    let response = sender.send_request(request).await.map_err(|_| busy())?;
    let status = response.status().as_u16();
    let data = response.into_body().collect().await.map_err(|_| busy())?.to_bytes();
    let answer: Value = serde_json::from_slice(&data).map_err(|_| internal())?;
    if status == 200 {
        return Ok(answer);
    }
    Err(Fault { status, message: answer.get("error").and_then(Value::as_str).ok_or_else(internal)?.to_owned() })
}

/// `rika-server math` (test-only): one JSON request per stdin line, one answer per stdout line,
/// for tests/test_math_parity.py:
///
/// - `{"generate": template_id, "seed", "step"?}`: the problem as the parity test reads it;
/// - `{"pick": topic_id, "seed"}`: the template id;
/// - `{"check": template_id, "seed", "step"?, "answer"}`: a number or choice verdict;
/// - `{"lesson": topic_id, "rng": n}`: the example's and guided problem's (template, seed)
///   after `random.Random(n)`'s draws;
/// - `{"sprint": mode, "seed", "ops", "count"}`: the problems;
/// - `{"textbook": directory}`: every exercise's topics and problem.
pub fn test_command() -> i32 {
    use std::io::BufRead;
    for line in std::io::stdin().lock().lines() {
        let Ok(request) = line.map_err(|_| ()).and_then(|line| serde_json::from_str::<Value>(&line).map_err(|_| ())) else {
            eprintln!("each line is a JSON object");
            return 2;
        };
        let answer = std::panic::catch_unwind(|| command(&request)).unwrap_or_else(|_| json!({"error": "panic"}));
        println!("{answer}");
    }
    0
}

fn problem_json(problem: &Problem) -> Value {
    let answer = |value: &Answer| match value {
        Answer::Num(number) => json!(["num", number.to_string()]),
        Answer::Text(text) => json!(["text", text]),
    };
    json!({
        "template_id": problem.template_id, "topic_id": problem.topic_id, "prompt_md": problem.prompt_md, "answer": answer(&problem.answer),
        "answer_kind": problem.answer_kind,
        "steps": problem.steps.iter().map(|step| json!([step.why, step.tex, step.value.map(|value| value.to_string())])).collect::<Vec<_>>(),
        "steps_md": problem.steps.iter().map(|step| step.md(false)).collect::<Vec<_>>(),
        "blank_md": problem.steps.iter().map(|step| step.md(true)).collect::<Vec<_>>(),
        "mistakes": problem.mistakes.iter().map(|(value, message)| json!([answer(value), message])).collect::<Vec<_>>(),
        "choices": problem.choices, "tolerance": problem.tolerance.map(|value| value.to_string()), "sigfigs": problem.sigfigs,
        "variables": problem.variables, "target_s": problem.target_s, "up_to_constant": problem.up_to_constant,
        "canonical": problem.canonical(),
    })
}

fn command(request: &Value) -> Value {
    let seed = request["seed"].as_i64().unwrap_or(0);
    let step = request["step"].as_u64().map(|step| step as usize);
    let problem = |id: &str| problems::generate(id, seed).ok().and_then(|problem| match step {
        Some(step) => problem.step_problem(step),
        None => Some(problem),
    });
    if let Some(id) = request["generate"].as_str() {
        return problem(id).map_or(json!({"error": "unknown"}), |problem| problem_json(&problem));
    }
    if let Some(topic) = request["pick"].as_str() {
        return json!(problems::pick(topic, seed).map(|template| template.id));
    }
    if let Some(id) = request["check"].as_str() {
        let Some(problem) = problem(id) else { return json!({"error": "unknown"}) };
        let verdict = checking::check(&problem, &request["answer"]);
        return json!({"correct": verdict.correct, "kind": verdict.kind, "feedback": verdict.feedback});
    }
    if let Some(topic) = request["lesson"].as_str() {
        let mut rng = pyrandom::Random::from_int(request["rng"].as_u64().map_or(0, u128::from));
        return match practice::lesson_problems(topic, &mut rng) {
            Ok((example, guided)) => json!([[example.template_id, example.seed], [guided.template_id, guided.seed]]),
            Err(_) => json!({"error": "lesson"}),
        };
    }
    if let Some(mode) = request["sprint"].as_str() {
        let ops: Vec<&str> = request["ops"].as_array().map(|ops| ops.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
        return json!(sprints::problems(mode, seed, &ops, request["count"].as_u64().unwrap_or(0) as usize));
    }
    if let Some(directory) = request["textbook"].as_str() {
        textbook::set_directory(PathBuf::from(directory));
        return match textbook::exercises() {
            Ok(exercises) => json!(exercises.iter().map(|exercise| json!({
                "id": exercise.id, "topics": exercise.topics, "problem": textbook::problem(exercise).map(|problem| problem_json(&problem)),
            })).collect::<Vec<_>>()),
            Err(fault) => json!({"error": fault.message}),
        };
    }
    json!({"error": "unknown request"})
}
