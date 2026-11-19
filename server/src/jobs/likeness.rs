//! `kitty.core.likeness.rescore`: every job's TF-IDF likeness to my favorites minus my skips,
//! stored in `jobs.like_score` and `like_terms` after a favorite, unfavorite or hand skip.
//!
//! The arithmetic is Python's step for step (dicts in insertion order, `sum` with CPython
//! 3.12's compensated summation, `round(score, 4)`), so the stored scores are the same floats.

use indexmap::IndexMap;
use rusqlite::{params, Connection};
use serde_json::Value;

use crate::ported::{internal, py, Done};

/// `HAND_NOT_ELIGIBLE`.
pub const HAND_NOT_ELIGIBLE: &str = "marked not eligible by hand";

/// `FIELD_WEIGHTS`, in its order.
const FIELDS: [(&str, f64); 3] = [("title", 0.5), ("jd", 0.3), ("meta", 0.2)];
const TOP_TERMS: usize = 5;

const STOPWORDS: &[&str] = &[
    "a", "about", "all", "also", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by", "can", "do", "for", "from",
    "has", "have", "how", "if", "in", "into", "is", "it", "its", "may", "more", "most", "must", "not", "of", "on", "or", "our",
    "out", "over", "so", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those",
    "through", "to", "up", "us", "was", "we", "were", "what", "when", "where", "which", "while", "who", "will", "with",
    "within", "without", "would", "you", "your", "yours", "intern", "interns", "internship", "internships", "summer", "fall",
    "spring", "winter", "co", "op", "coop", "student", "students",
];

type Vector = IndexMap<String, f64>;
type Counts = IndexMap<String, u64>;

/// CPython 3.12's `sum()` of floats: Neumaier's compensated summation.
fn sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let (mut total, mut compensation) = (0.0f64, 0.0f64);
    for value in values {
        let next = total + value;
        if total.abs() >= value.abs() {
            compensation += (total - next) + value;
        } else {
            compensation += (value - next) + total;
        }
        total = next;
    }
    if compensation != 0.0 && compensation.is_finite() {
        total += compensation;
    }
    total
}

/// `tokens`: `[a-z][a-z0-9+#]+` runs of the lower-cased text, stop words left out.
fn tokens(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    let bytes = lower.as_bytes();
    let mut out = Vec::new();
    let mut at = 0;
    while at < bytes.len() {
        if !bytes[at].is_ascii_lowercase() {
            at += 1;
            continue;
        }
        let mut end = at + 1;
        while end < bytes.len() && (bytes[end].is_ascii_lowercase() || bytes[end].is_ascii_digit() || bytes[end] == b'+' || bytes[end] == b'#') {
            end += 1;
        }
        if end - at >= 2 {
            let word = &lower[at..end];
            if !STOPWORDS.contains(&word) {
                out.push(word.to_owned());
            }
        }
        at = end;
    }
    out
}

fn counter(items: impl IntoIterator<Item = String>) -> Counts {
    let mut counts = Counts::new();
    for item in items {
        *counts.entry(item).or_insert(0) += 1;
    }
    counts
}

/// `str(value)` of a column an f-string formats.
fn text_of(value: &Value) -> Done<String> {
    super::reads::python_str(value)
}

/// `_documents`: every job's term counts per field, in job id order.
fn documents(conn: &Connection) -> Done<Vec<(i64, [Counts; 3])>> {
    let mut tracks: IndexMap<i64, Vec<String>> = IndexMap::new();
    for found in crate::ported::db::rows(conn, "SELECT job_id, track FROM job_tracks ORDER BY job_id, track", [])? {
        let job_id = found.get("job_id").and_then(Value::as_i64).ok_or_else(internal)?;
        tracks.entry(job_id).or_default().push(text_of(found.get("track").ok_or_else(internal)?)?);
    }
    let mut docs = Vec::new();
    for found in crate::ported::db::rows(conn, "SELECT j.id, j.title, j.jd_text, c.display_name FROM jobs j \
                                                JOIN companies c ON c.id = j.company_id ORDER BY j.id", [])? {
        let job_id = found.get("id").and_then(Value::as_i64).ok_or_else(internal)?;
        let field = |key: &str| -> Done<String> {
            // `(text or "").lower()`: a falsy value reads as empty, other text as itself.
            match found.get(key).ok_or_else(internal)? {
                value if !py::truthy(value) => Ok(String::new()),
                Value::String(text) => Ok(text.clone()),
                _ => Err(internal()),
            }
        };
        let mut meta = vec![format!("company {}", text_of(found.get("display_name").ok_or_else(internal)?)?)];
        meta.extend(tracks.get(&job_id).into_iter().flatten().map(|name| format!("track {name}")));
        docs.push((job_id, [counter(tokens(&field("title")?)), counter(tokens(&field("jd_text")?)), counter(meta)]));
    }
    Ok(docs)
}

/// `_normalised`: scaled to length 1; empty when its length is 0.
fn normalised(vector: Vector) -> Vector {
    let length = sum(vector.values().map(|value| value * value)).sqrt();
    if length == 0.0 {
        return Vector::new();
    }
    vector.into_iter().map(|(key, value)| (key, value / length)).collect()
}

/// `vectors`: one weighted TF-IDF vector per job, keyed `field:term`.
fn vectors(docs: &[(i64, [Counts; 3])]) -> Vec<(i64, Vector)> {
    let total = docs.len() as f64;
    let mut frequency: [IndexMap<&str, u64>; 3] = Default::default();
    for (_, fields) in docs {
        for (index, counts) in fields.iter().enumerate() {
            for term in counts.keys() {
                *frequency[index].entry(term.as_str()).or_insert(0) += 1;
            }
        }
    }
    docs.iter().map(|(job_id, fields)| {
        let mut combined = Vector::new();
        for (index, counts) in fields.iter().enumerate() {
            let weights: Vector = counts.iter().map(|(term, count)| {
                let seen = frequency[index][term.as_str()] as f64;
                (term.clone(), (1.0 + (*count as f64).ln()) * (total / seen).ln())
            }).collect();
            let (name, weight) = FIELDS[index];
            for (term, value) in normalised(weights) {
                combined.insert(format!("{name}:{term}"), weight * value);
            }
        }
        (*job_id, normalised(combined))
    }).collect()
}

/// `centroid`: the normalised average of these jobs' vectors.
fn centroid(vectors: &IndexMap<i64, Vector>, ids: &[i64]) -> Vector {
    let mut total = Vector::new();
    for job_id in ids {
        if let Some(vector) = vectors.get(job_id) {
            for (key, value) in vector {
                *total.entry(key.clone()).or_insert(0.0) += value;
            }
        }
    }
    normalised(total)
}

/// `cosine` of two normalised vectors (iterating the shorter).
fn cosine(vector: &Vector, other: &Vector) -> f64 {
    let (vector, other) = if other.len() < vector.len() { (other, vector) } else { (vector, other) };
    sum(vector.iter().map(|(key, value)| value * other.get(key).copied().unwrap_or(0.0)))
}

/// `shared_terms`: the terms adding most to this job's likeness to my favorites.
fn shared_terms(vector: &Vector, favorites: &Vector) -> Vec<String> {
    let mut contributions: IndexMap<&str, f64> = IndexMap::new();
    for (key, value) in vector {
        let weight = value * favorites.get(key).copied().unwrap_or(0.0);
        if weight > 0.0 {
            let term = key.split_once(':').map_or("", |(_, term)| term);
            *contributions.entry(term).or_insert(0.0) += weight;
        }
    }
    let mut ranked: Vec<(&str, f64)> = contributions.into_iter().collect();
    ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal).then_with(|| a.0.cmp(b.0)));
    ranked.into_iter().take(TOP_TERMS).map(|(term, _)| term.to_owned()).collect()
}

/// `round(value, 4)`: the nearest four-decimal value, ties to even, as a float.
fn round4(value: f64) -> f64 {
    if !value.is_finite() {
        return value;
    }
    format!("{value:.4}").parse().unwrap_or(value)
}

fn ids(conn: &Connection, sql: &str, params: impl rusqlite::Params) -> Done<Vec<i64>> {
    let mut statement = conn.prepare(sql)?;
    let found = statement.query_map(params, |row| row.get::<_, i64>(0))?.collect::<Result<Vec<_>, _>>()?;
    Ok(found)
}

/// `rescore` inside the caller's transaction: recompute and store every job's like score.
pub fn rescore(conn: &Connection) -> Done<()> {
    let favorites = ids(conn, "SELECT id FROM jobs WHERE favorited_at IS NOT NULL ORDER BY id", [])?;
    if favorites.is_empty() {
        conn.execute("UPDATE jobs SET like_score = NULL, like_terms = NULL WHERE like_score IS NOT NULL OR like_terms IS NOT NULL", [])?;
        return Ok(());
    }
    let disliked = ids(conn, "SELECT id FROM jobs WHERE favorited_at IS NULL AND (queue_state = 'skipped' OR eligibility_reason = ?) ORDER BY id",
                       [HAND_NOT_ELIGIBLE])?;
    let vectors: IndexMap<i64, Vector> = vectors(&documents(conn)?).into_iter().collect();
    let liked = centroid(&vectors, &favorites);
    let disliked = centroid(&vectors, &disliked);
    let mut update = conn.prepare("UPDATE jobs SET like_score = ?, like_terms = ? WHERE id = ?")?;
    for (job_id, vector) in &vectors {
        let score = round4(cosine(vector, &liked) - cosine(vector, &disliked));
        let terms = shared_terms(vector, &liked);
        let stored = (!terms.is_empty()).then(|| py::dumps(&Value::from(terms)));
        update.execute(params![score, stored, job_id])?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sums_compensate_as_cpython_does() {
        assert_eq!(sum([0.1; 10]), 1.0);
        assert_eq!(sum([1e100, 1.0, -1e100]), 1.0);
        assert_eq!(sum(std::iter::empty()), 0.0);
    }

    #[test]
    fn rounding_is_pythons() {
        assert_eq!(round4(0.03125), 0.0312);
        assert_eq!(round4(0.12345678), 0.1235);
        assert_eq!(round4(-0.00001).to_bits(), (-0.0f64).to_bits());
    }

    #[test]
    fn tokens_follow_the_pattern() {
        assert_eq!(tokens("The C++ Intern, FPGA/RTL x c# İstanbul"), ["c++", "fpga", "rtl", "c#", "stanbul"]);
    }
}
