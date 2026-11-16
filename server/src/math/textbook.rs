//! `kitty.core.math.textbook`: Grinstead & Snell's exercises linked to rika!'s topics.
//!
//! The exercise text is GNU FDL data outside the MIT tree, read at run time from the folder
//! the worker reports (`textbook.directory()`: `third_party/grinstead-snell` in a checkout,
//! the frozen app's `grinstead-snell`), the same file Python reads; it is never compiled in.
//! rika!'s own links and answers are `src/kitty/core/math/textbook.json`, compiled in.

use std::path::PathBuf;
use std::sync::{Arc, LazyLock, Mutex, OnceLock};

use regex::{Regex, RegexBuilder};
use serde::Deserialize;
use serde_json::{Map, Value};

use super::frac::F;
use super::problems::{Answer, Problem};
use super::topics;
use crate::ported::{fail, internal, Done};

const LINKS: &str = include_str!("../../../src/kitty/core/math/textbook.json");

#[derive(Deserialize)]
struct StoredAnswer {
    value: String,
    part: Option<String>,
    sigfigs: Option<u32>,
    tolerance: Option<String>,
}

#[derive(Deserialize)]
struct Links {
    source: Map<String, Value>,
    target_s: u32,
    max_topics: usize,
    sections: Map<String, Value>,
    keywords: Vec<(String, Vec<String>)>,
    primary: Map<String, Value>,
    answers: Map<String, Value>,
}

static DATA: LazyLock<Links> = LazyLock::new(|| serde_json::from_str(LINKS).expect("textbook.json is valid"));
static KEYWORDS: LazyLock<Vec<(Regex, &'static [String])>> = LazyLock::new(|| {
    DATA.keywords.iter().map(|(pattern, topics)| {
        (RegexBuilder::new(pattern).case_insensitive(true).build().expect("a valid keyword pattern"), topics.as_slice())
    }).collect()
});

/// A numeric answer authored by rika! (our own computation), never copied from the book.
pub struct AnswerData {
    pub value: F,
    /// "b" when the number answers part (b) only.
    pub part: Option<String>,
    pub sigfigs: Option<u32>,
    pub tolerance: Option<F>,
}

pub struct Exercise {
    pub id: String,
    pub label: String,
    pub section: String,
    pub section_title: String,
    pub starred: Value,
    /// Markdown with `$…$` / `$$…$$` TeX, KaTeX-safe.
    pub text_md: String,
    /// At least one topic id; the first is the primary topic.
    pub topics: Vec<String>,
    pub program: Value,
    pub figure: Value,
    pub answer: Option<AnswerData>,
}

impl Exercise {
    /// `Exercise.graded`.
    pub fn graded(&self) -> &'static str {
        if self.answer.is_some() { "number" } else { "self" }
    }
}

/// `Fraction(text)` for the integers and `n/d` the answers table holds.
fn fraction(text: &str) -> F {
    let (top, bottom) = text.split_once('/').unwrap_or((text, "1"));
    F::new(top.trim().parse::<i128>().expect("an integer"), bottom.trim().parse::<i128>().expect("an integer"))
}

fn answer_of(id: &str) -> Option<AnswerData> {
    let stored: StoredAnswer = serde_json::from_value(DATA.answers.get(id)?.clone()).expect("a valid answer");
    Some(AnswerData { value: fraction(&stored.value), part: stored.part, sigfigs: stored.sigfigs, tolerance: stored.tolerance.as_deref().map(fraction) })
}

/// `SOURCE`.
pub fn source() -> &'static Map<String, Value> {
    &DATA.source
}

static DIRECTORY: OnceLock<PathBuf> = OnceLock::new();
static LOADED: Mutex<Option<Arc<Vec<Exercise>>>> = Mutex::new(None);

/// The folder of `exercises.json` the worker reports (its `textbook.directory()`).
pub fn set_directory(directory: PathBuf) {
    let _ = DIRECTORY.set(directory);
}

/// `topics_for`: primary first: an exercise's override, its section's topics, then keyword topics.
pub fn topics_for(id: &str, section: &str, text: &str) -> Done<Vec<String>> {
    let mut ordered: Vec<String> = DATA.primary.get(id).and_then(Value::as_str).map(str::to_owned).into_iter().collect();
    let section = DATA.sections.get(section).and_then(Value::as_array).ok_or_else(internal)?;
    ordered.extend(section.iter().filter_map(Value::as_str).map(str::to_owned));
    for (pattern, topics) in KEYWORDS.iter() {
        if pattern.is_match(text) {
            ordered.extend(topics.iter().cloned());
        }
    }
    let mut unique: Vec<String> = Vec::new();
    for topic in ordered {
        if !unique.contains(&topic) {
            unique.push(topic);
        }
    }
    unique.truncate(DATA.max_topics);
    Ok(unique)
}

/// A list of text as Python's `repr` writes it (`['a', 'b']`).
fn repr_list(items: &[String]) -> String {
    format!("[{}]", items.iter().map(|item| format!("'{item}'")).collect::<Vec<_>>().join(", "))
}

fn load() -> Done<Vec<Exercise>> {
    let directory = DIRECTORY.get().ok_or_else(internal)?;
    let text = std::fs::read_to_string(directory.join("exercises.json")).map_err(|_| internal())?;
    let data: Value = serde_json::from_str(&text).map_err(|_| internal())?;
    let items = data.get("exercises").and_then(Value::as_array).ok_or_else(internal)?;
    let mut out = Vec::new();
    for item in items {
        let field = |key: &str| item.get(key).ok_or_else(internal);
        let string = |key: &str| field(key).and_then(|value| value.as_str().map(str::to_owned).ok_or_else(internal));
        let (id, section, text_md) = (string("id")?, string("section")?, string("text_md")?);
        let topics = topics_for(&id, &section, &text_md)?;
        let unknown: Vec<String> = topics.iter().filter(|topic| topics::by_id(topic).is_none()).cloned().collect();
        if !unknown.is_empty() {
            return Err(fail(400, format!("{id} links unknown topics {}", repr_list(&unknown))));
        }
        out.push(Exercise {
            label: string("label")?, section_title: string("section_title")?, starred: field("starred")?.clone(), program: field("program")?.clone(),
            figure: field("figure")?.clone(), answer: answer_of(&id), topics, id, section, text_md,
        });
    }
    let mut missing: Vec<String> = DATA.answers.keys().filter(|id| !out.iter().any(|exercise| &exercise.id == *id)).cloned().collect();
    if !missing.is_empty() {
        missing.sort();
        return Err(fail(400, format!("Answers for unknown exercises {}", repr_list(&missing))));
    }
    Ok(out)
}

/// `exercises()`: every bundled exercise, in book order (loaded once it is first needed).
pub fn exercises() -> Done<Arc<Vec<Exercise>>> {
    let mut loaded = LOADED.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(exercises) = loaded.as_ref() {
        return Ok(exercises.clone());
    }
    let exercises = Arc::new(load()?);
    *loaded = Some(exercises.clone());
    Ok(exercises)
}

/// `problem(exercise)`: the exercise as a checkable problem (one with an answer).
pub fn problem(exercise: &Exercise) -> Option<Problem> {
    let answer = exercise.answer.as_ref()?;
    let prompt = match &answer.part {
        Some(part) => format!("{}\n\nAnswer part ({part}).", exercise.text_md),
        None => exercise.text_md.clone(),
    };
    let whole = answer.value.is_int() && answer.sigfigs.is_none() && answer.tolerance.is_none();
    Some(Problem {
        template_id: format!("textbook:{}", exercise.id),
        topic_id: exercise.topics[0].clone(),
        seed: 0,
        prompt_md: prompt,
        answer: Answer::Num(answer.value),
        answer_kind: if whole { "number" } else { "fraction" },
        steps: Vec::new(),
        mistakes: Vec::new(),
        choices: None,
        tolerance: answer.tolerance,
        sigfigs: answer.sigfigs,
        variables: Vec::new(),
        target_s: DATA.target_s,
        up_to_constant: false,
    })
}
