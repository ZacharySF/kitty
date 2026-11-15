//! py-fsrs 6's `Scheduler.review_card` with fuzzing off, as `kitty.core.flashcards` used it:
//! the learning and relearning steps, the card's state machine and the FSRS-6 memory model
//! (initial, short-term, recall and forget stability, difficulty, forgetting curve) with
//! py-fsrs's default parameters, in the same f64 operations in the same order, so a review
//! stores the number py-fsrs stored (tests/test_flashcard_scheduling_parity.py).
//!
//! The stored `fsrs` column is py-fsrs's `Card.to_json()`; both read and write it.

use serde_json::{json, Value};

use super::pyjson;
use super::pytime::{self, Parsed, MICROS_PER_DAY, MICROS_PER_SECOND};

/// `fsrs.scheduler.DEFAULT_PARAMETERS`.
const W: [f64; 21] = [0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796, 1.4835, 0.0614, 0.2629, 1.6483,
    0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542];
/// `fsrs.scheduler.FSRS_DEFAULT_DECAY` (`Scheduler().parameters[20]`).
pub const FSRS_DEFAULT_DECAY: f64 = W[20];
/// `Scheduler._DECAY`.
const DECAY: f64 = -FSRS_DEFAULT_DECAY;
const STABILITY_MIN: f64 = 0.001;
const E: f64 = std::f64::consts::E;

/// `Scheduler._FACTOR`.
fn factor() -> f64 {
    0.9f64.powf(1.0 / DECAY) - 1.0
}

fn clamp_difficulty(difficulty: f64) -> f64 {
    difficulty.max(1.0).min(10.0)
}

/// `_initial_difficulty`.
fn initial_difficulty(rating: u8, clamp: bool) -> f64 {
    let difficulty = W[4] - E.powf(W[5] * f64::from(rating - 1)) + 1.0;
    if clamp { clamp_difficulty(difficulty) } else { difficulty }
}

/// `_short_term_stability`: a review on the day of the last one.
fn short_term_stability(stability: f64, rating: u8) -> f64 {
    let mut increase = E.powf(W[17] * (f64::from(rating) - 3.0 + W[18])) * stability.powf(-W[19]);
    if rating >= 2 {
        increase = increase.max(1.0);
    }
    (stability * increase).max(STABILITY_MIN)
}

/// `_next_difficulty`.
fn next_difficulty(difficulty: f64, rating: u8) -> f64 {
    let easy = initial_difficulty(4, false);
    let delta = -(W[6] * (f64::from(rating) - 3.0));
    let damped = difficulty + (10.0 - difficulty) * delta / 9.0;
    clamp_difficulty(W[7] * easy + (1.0 - W[7]) * damped)
}

/// `_next_stability`: forgotten (again) or recalled.
fn next_stability(difficulty: f64, stability: f64, retrievability: f64, rating: u8) -> f64 {
    let next = if rating == 1 {
        let long_term = W[11] * difficulty.powf(-W[12]) * ((stability + 1.0).powf(W[13]) - 1.0) * E.powf((1.0 - retrievability) * W[14]);
        let short_term = stability / E.powf(W[17] * W[18]);
        long_term.min(short_term)
    } else {
        let hard_penalty = if rating == 2 { W[15] } else { 1.0 };
        let easy_bonus = if rating == 4 { W[16] } else { 1.0 };
        stability * (1.0 + E.powf(W[8]) * (11.0 - difficulty) * stability.powf(-W[9]) * (E.powf((1.0 - retrievability) * W[10]) - 1.0) * hard_penalty * easy_bonus)
    };
    next.max(STABILITY_MIN)
}

/// `fsrs.State`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {
    Learning = 1,
    Review = 2,
    Relearning = 3,
}

impl State {
    pub fn name(self) -> &'static str {
        match self {
            State::Learning => "learning",
            State::Review => "review",
            State::Relearning => "relearning",
        }
    }

    fn from_number(value: i64) -> Option<Self> {
        match value {
            1 => Some(State::Learning),
            2 => Some(State::Review),
            3 => Some(State::Relearning),
            _ => None,
        }
    }
}

/// `fsrs.Card`.
#[derive(Clone, Debug)]
pub struct Card {
    pub card_id: i64,
    pub state: State,
    /// The step as stored: an integer, or None for a review card (anything else is kept as is).
    pub step: Value,
    pub stability: Option<f64>,
    pub difficulty: Option<f64>,
    pub due: Parsed,
    pub last_review: Option<Parsed>,
}

/// The settings `_scheduler` passes: retention, steps in seconds and the maximum interval.
pub struct Settings {
    pub retention: f64,
    pub learning_steps: Vec<i64>,
    pub relearning_steps: Vec<i64>,
    pub maximum_interval: i64,
}

fn float_field(value: Option<&Value>) -> Result<Option<f64>, String> {
    // `float(x) if x else None`: a zero or missing value is None.
    match value {
        None | Some(Value::Null) | Some(Value::Bool(false)) => Ok(None),
        Some(Value::Number(number)) => {
            let value = number.as_f64().unwrap_or(0.0);
            Ok(if value == 0.0 { None } else { Some(value) })
        }
        Some(Value::String(text)) if text.is_empty() => Ok(None),
        Some(Value::String(text)) => text.trim().parse().map(Some).map_err(|_| format!("could not convert string to float: '{text}'")),
        Some(_) => Err("float() argument must be a string or a real number".into()),
    }
}

fn integer_field(value: Option<&Value>, name: &str) -> Result<i64, String> {
    match value {
        Some(Value::Number(number)) => number.as_i64().or_else(|| number.as_f64().map(|f| f.trunc() as i64)).ok_or_else(|| format!("Invalid {name}")),
        Some(Value::Bool(flag)) => Ok(*flag as i64),
        Some(Value::String(text)) => text.trim().parse().map_err(|_| format!("invalid literal for int() with base 10: '{text}'")),
        _ => Err(format!("'{name}'")),
    }
}

fn time_field(value: Option<&Value>) -> Result<Parsed, String> {
    match value {
        Some(Value::String(text)) => pytime::parse(text).ok_or_else(|| format!("Invalid isoformat string: '{text}'")),
        _ => Err("fromisoformat: argument must be str".into()),
    }
}

impl Card {
    /// `Card(card_id=id, due=due)`: a new card in its first learning step.
    pub fn new(card_id: i64, due: Parsed) -> Self {
        Card { card_id, state: State::Learning, step: json!(0), stability: None, difficulty: None, due, last_review: None }
    }

    /// `Card.from_json`.
    pub fn from_json(text: &str) -> Result<Self, String> {
        let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
        let object = value.as_object().ok_or("Invalid FSRS card")?;
        let state = State::from_number(integer_field(object.get("state"), "state")?)
            .ok_or_else(|| format!("{} is not a valid State", object.get("state").map(Value::to_string).unwrap_or_default()))?;
        let mut step = object.get("step").cloned().ok_or("'step'")?;
        if state == State::Learning && step.is_null() {
            step = json!(0);
        }
        let last_review = match object.get("last_review") {
            None => return Err("'last_review'".into()),
            Some(Value::Null) | Some(Value::Bool(false)) => None,
            Some(Value::String(text)) if text.is_empty() => None,
            other => Some(time_field(other)?),
        };
        Ok(Card {
            card_id: integer_field(object.get("card_id"), "card_id")?,
            state,
            step,
            stability: float_field(object.get("stability"))?,
            difficulty: float_field(object.get("difficulty"))?,
            due: time_field(object.get("due"))?,
            last_review,
        })
    }

    /// `Card.to_json()`.
    pub fn to_json(&self) -> String {
        pyjson::dumps(&json!({
            "card_id": self.card_id,
            "state": self.state as i64,
            "step": self.step,
            "stability": self.stability.map(pyjson::number),
            "difficulty": self.difficulty.map(pyjson::number),
            "due": pytime::iso_parsed(self.due),
            "last_review": self.last_review.map(pytime::iso_parsed),
        }))
    }

    fn step_number(&self) -> Result<i64, String> {
        self.step.as_i64().ok_or_else(|| "Invalid FSRS learning step".to_owned())
    }
}

/// `Scheduler.get_card_retrievability` (default parameters).
pub fn retrievability(card: &Card, now: i64) -> f64 {
    let (Some(last), Some(stability)) = (card.last_review.and_then(Parsed::utc), card.stability) else { return 0.0 };
    let elapsed = pytime::whole_days(now - last).max(0);
    (1.0 + factor() * elapsed as f64 / stability).powf(DECAY)
}

/// `Scheduler._next_interval`: whole days, at least one, at most the maximum.
fn next_interval(settings: &Settings, stability: f64) -> i64 {
    let days = (stability / factor()) * (settings.retention.powf(1.0 / DECAY) - 1.0);
    (days.round_ties_even() as i64).max(1).min(settings.maximum_interval)
}

/// `timedelta(seconds=s) * factor`, rounded to the microsecond as Python rounds it.
fn seconds(value: i64) -> i64 {
    value * MICROS_PER_SECOND
}

/// `Scheduler.review_card(card, rating, review_datetime=now)`: the updated card.
pub fn review(settings: &Settings, card: &Card, rating: u8, now: i64) -> Result<Card, String> {
    let mut card = card.clone();
    let days_since = card.last_review.and_then(Parsed::utc).map(|last| pytime::whole_days(now - last));
    let same_day = days_since.is_some_and(|days| days < 1);
    let (stability, difficulty) = match (card.stability, card.difficulty) {
        (Some(stability), Some(difficulty)) => {
            let stability = if same_day { short_term_stability(stability, rating) } else { next_stability(difficulty, stability, retrievability(&card, now), rating) };
            (stability, next_difficulty(difficulty, rating))
        }
        _ if card.state == State::Learning => (W[usize::from(rating - 1)].max(STABILITY_MIN), initial_difficulty(rating, true)),
        _ => return Err("A reviewed FSRS card needs stability and difficulty".into()),
    };
    card.stability = Some(stability);
    card.difficulty = Some(difficulty);

    let to_review = |card: &mut Card| {
        card.state = State::Review;
        card.step = Value::Null;
        next_interval(settings, stability) * MICROS_PER_DAY
    };
    let interval = match card.state {
        State::Learning | State::Relearning => {
            let steps = if card.state == State::Learning { &settings.learning_steps } else { &settings.relearning_steps };
            let step = card.step_number()?;
            if steps.is_empty() || (step >= steps.len() as i64 && rating >= 2) {
                to_review(&mut card)
            } else {
                let at = |index: i64| steps.get(usize::try_from(index).ok()?).copied();
                let out_of_range = || "tuple index out of range".to_owned();
                match rating {
                    1 => {
                        card.step = json!(0);
                        seconds(steps[0])
                    }
                    2 if step == 0 && steps.len() == 1 => seconds(steps[0]) * 3 / 2,
                    2 if step == 0 => (seconds(steps[0]) + seconds(steps[1])) / 2,
                    2 => seconds(at(step).ok_or_else(out_of_range)?),
                    3 if step + 1 == steps.len() as i64 => to_review(&mut card),
                    3 => {
                        card.step = json!(step + 1);
                        seconds(at(step + 1).ok_or_else(out_of_range)?)
                    }
                    _ => to_review(&mut card),
                }
            }
        }
        State::Review => {
            if rating == 1 && !settings.relearning_steps.is_empty() {
                card.state = State::Relearning;
                card.step = json!(0);
                seconds(settings.relearning_steps[0])
            } else {
                next_interval(settings, stability) * MICROS_PER_DAY
            }
        }
    };
    let due = now + interval;
    if !pytime::in_range(due) {
        return Err("date value out of range".into());
    }
    card.due = Parsed { wall: due, offset: Some(0) };
    card.last_review = Some(Parsed { wall: now, offset: Some(0) });
    Ok(card)
}

/// Hidden, test-only `rika-server fsrs-review`: a JSON list of reviews on stdin, each
/// `{"card": <Card JSON or null>, "card_id", "due", "rating", "now", "settings"}`, and the list
/// of reviewed cards (`Card.to_json()`, or `{"error"}`) on stdout.
pub fn review_command() -> i32 {
    let mut input = String::new();
    if std::io::Read::read_to_string(&mut std::io::stdin(), &mut input).is_err() {
        return 2;
    }
    let Ok(Value::Array(cases)) = serde_json::from_str::<Value>(&input) else { return 2 };
    let mut output = Vec::new();
    for case in cases {
        let outcome = (|| -> Result<String, String> {
            let card = match case["card"].as_str() {
                Some(text) => Card::from_json(text)?,
                None => Card::new(case["card_id"].as_i64().unwrap_or(1), pytime::parse(case["due"].as_str().unwrap_or("")).ok_or("due")?),
            };
            let settings = &case["settings"];
            let steps = |key: &str| settings[key].as_array().map(|steps| steps.iter().filter_map(Value::as_i64).collect()).unwrap_or_default();
            let settings = Settings {
                retention: settings["retention"].as_f64().unwrap_or(0.9),
                learning_steps: steps("learning_steps"),
                relearning_steps: steps("relearning_steps"),
                maximum_interval: settings["maximum_interval"].as_i64().unwrap_or(36500),
            };
            let now = pytime::parse(case["now"].as_str().unwrap_or("")).and_then(Parsed::utc).ok_or("now")?;
            Ok(review(&settings, &card, case["rating"].as_u64().unwrap_or(3) as u8, now)?.to_json())
        })();
        output.push(match outcome {
            Ok(card) => Value::String(card),
            Err(error) => json!({ "error": error }),
        });
    }
    println!("{}", Value::Array(output));
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> Settings {
        Settings { retention: 0.9, learning_steps: vec![60, 600], relearning_steps: vec![600], maximum_interval: 36500 }
    }

    #[test]
    fn first_ratings_follow_the_learning_steps() {
        let now = pytime::parse("2026-10-14T12:00:00+00:00").unwrap().utc().unwrap();
        let card = Card::new(1, Parsed { wall: now, offset: Some(0) });
        let minutes = |card: &Card| (card.due.utc().unwrap() - now) / MICROS_PER_SECOND;
        assert_eq!(minutes(&review(&settings(), &card, 1, now).unwrap()), 60);
        assert_eq!(minutes(&review(&settings(), &card, 2, now).unwrap()), 330);
        assert_eq!(minutes(&review(&settings(), &card, 3, now).unwrap()), 600);
        let easy = review(&settings(), &card, 4, now).unwrap();
        assert_eq!(easy.state, State::Review);
        assert_eq!(easy.stability, Some(8.2956));
        assert_eq!(easy.to_json(), format!(
            "{{\"card_id\": 1, \"state\": 2, \"step\": null, \"stability\": 8.2956, \"difficulty\": {}, \"due\": \"2026-10-22T12:00:00+00:00\", \"last_review\": \"2026-10-14T12:00:00+00:00\"}}",
            pyjson::float(easy.difficulty.unwrap())));
        let again = Card::from_json(&easy.to_json()).unwrap();
        assert_eq!(again.to_json(), easy.to_json());
    }
}
