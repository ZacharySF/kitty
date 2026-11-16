//! `backlinks.related`: the note pages and cards linked with, mentioning or related to a text,
//! ranked with bm25 over the full index. *Linked*: a `[[link]]` either way between the text and
//! the record; *mentions*: the record's title appears in the text, or the excluded source's
//! title in the record; *related*: records sharing the text's most distinctive terms (tf·idf
//! against the index's document frequencies), ranked by bm25 summed over each record's matching
//! documents and normalised to the best one. Each group shows at most `RELATED_LIMIT`.

use std::collections::{HashMap, HashSet};

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::text::{contains, phrase, phrase_query, runs, singular, snippet, stem, stopword, token_words, tokens, wikilinks, Phrase};
use super::{searched, str_of};
use crate::flashcards::pyjson;
use crate::ported::{fail, internal, Done};
use crate::search::text::{casefold, decimal_value, length};

const RELATED_TERMS: usize = 24;
const RELATED_LIMIT: usize = 12;
const KINDS_SQL: &str = "(d.kind='flashcard_note' OR (d.kind='block' AND r.type='page'))";
const EXCLUDABLE: [&str; 5] = ["note_page", "flashcard_note", "flashcard_deck", "study_subject", "schema_node"];

/// A record: `('note_page', root block id)` or `('flashcard_note', note id)`.
type Key = (&'static str, String);

fn sql(rank: &str) -> String {
    format!("SELECT d.kind,d.ref,d.page_id,d.title,d.body,{rank} AS score FROM search_fts
    JOIN search_docs d ON d.id=search_fts.rowid LEFT JOIN blocks r ON d.kind='block' AND r.id=d.page_id
    WHERE search_fts MATCH ? AND {KINDS_SQL}")
}

struct Hit {
    kind: String,
    reference: String,
    page_id: Option<String>,
    title: String,
    body: String,
    score: f64,
}

impl Hit {
    /// `_related_key`.
    fn key(&self) -> Done<Key> {
        Ok(if self.kind == "block" { ("note_page", self.page_id.clone().ok_or_else(internal)?) } else { ("flashcard_note", self.reference.clone()) })
    }
}

fn hits(conn: &Connection, rank: &str, expression: &str) -> Done<Vec<Hit>> {
    let mut statement = conn.prepare(&sql(rank))?;
    let mut rows = statement.query([expression])?;
    let mut out = Vec::new();
    while let Some(row) = rows.next()? {
        let score = match row.get_ref("score")? {
            rusqlite::types::ValueRef::Real(value) => value,
            rusqlite::types::ValueRef::Integer(value) => value as f64,
            _ => return Err(internal()),
        };
        out.push(Hit { kind: row.get("kind")?, reference: row.get("ref")?, page_id: row.get("page_id")?, title: row.get("title")?,
                       body: row.get("body")?, score });
    }
    Ok(out)
}

/// `_related_records`: `{key: (id, title, content)}`; a page is its title and every text block,
/// a card all its fields.
fn records(conn: &Connection, keys: &[Key]) -> Done<HashMap<Key, (Value, String, String)>> {
    let pages: Vec<&String> = keys.iter().filter(|key| key.0 == "note_page").map(|key| &key.1).collect();
    let cards: Vec<&String> = keys.iter().filter(|key| key.0 == "flashcard_note").map(|key| &key.1).collect();
    let marks = |count: usize| vec!["?"; count].join(",");
    let mut found = HashMap::new();
    for chunk in pages.chunks(500) {
        let mut bodies: HashMap<String, Vec<String>> = HashMap::new();
        let mut statement = conn.prepare(&format!("SELECT page_id,body FROM search_docs WHERE kind='block' AND page_id IN ({}) \
            AND ref<>page_id ORDER BY id", marks(chunk.len())))?;
        let mut rows = statement.query(rusqlite::params_from_iter(chunk.iter()))?;
        while let Some(row) = rows.next()? {
            bodies.entry(row.get::<_, String>(0)?).or_default().push(row.get(1)?);
        }
        let mut statement = conn.prepare(&format!("SELECT id,text,CAST(json_extract(properties,'$.number') AS INTEGER) AS number FROM blocks \
            WHERE id IN ({})", marks(chunk.len())))?;
        let mut rows = statement.query(rusqlite::params_from_iter(chunk.iter()))?;
        while let Some(row) = rows.next()? {
            let ident: String = row.get(0)?;
            let text: String = row.get(1)?;
            let number: Option<i64> = row.get(2)?;
            let mut content = text.clone();
            for body in bodies.get(&ident).into_iter().flatten() {
                content.push('\n');
                content.push_str(body);
            }
            found.insert(("note_page", ident), (json!(number), text, content));
        }
    }
    for chunk in cards.chunks(500) {
        let mut statement = conn.prepare(&format!("SELECT ref,title,body,label FROM search_docs WHERE kind='flashcard_note' \
            AND ref IN ({})", marks(chunk.len())))?;
        let mut rows = statement.query(rusqlite::params_from_iter(chunk.iter()))?;
        while let Some(row) = rows.next()? {
            let reference: String = row.get(0)?;
            let (title, body): (String, String) = (row.get(1)?, row.get(2)?);
            let content = [title, body].into_iter().filter(|part| !part.is_empty()).collect::<Vec<_>>().join("\n");
            let ident = reference.trim().parse::<i64>().map_err(|_| internal())?;
            found.insert(("flashcard_note", reference), (json!(ident), row.get(3)?, content));
        }
    }
    Ok(found)
}

/// `_related_source`: the excluded record's key and title (only note pages and cards have one).
fn source(conn: &Connection, identity: Option<(&str, &str)>) -> Done<(Option<Key>, String)> {
    let Some((kind, ident)) = identity else { return Ok((None, String::new())) };
    if kind == "note_page" && !ident.is_empty() && ident.chars().all(|c| crate::builds::value::is_digit(c as u32)) {
        // `int(ident)`: a digit that is no decimal (`²`) is Python's ValueError, a number past
        // SQLite's integers its OverflowError.
        let Some(number) = decimal_value(ident) else {
            return Err(fail(400, format!("invalid literal for int() with base 10: '{ident}'")));
        };
        let number = i64::try_from(number).map_err(|_| internal())?;
        let mut statement = conn.prepare("SELECT block_id,title FROM note_page_index WHERE id=?")?;
        let mut rows = statement.query([number])?;
        return Ok(match rows.next()? {
            Some(row) => (Some(("note_page", row.get(0)?)), row.get(1)?),
            None => (None, String::new()),
        });
    }
    if kind == "flashcard_note" {
        let mut statement = conn.prepare("SELECT label FROM search_docs WHERE kind='flashcard_note' AND ref=?")?;
        let mut rows = statement.query([ident])?;
        return Ok(match rows.next()? {
            Some(row) => (Some(("flashcard_note", ident.to_owned())), row.get(0)?),
            None => (None, String::new()),
        });
    }
    Ok((None, String::new()))
}

/// `_terms_of`: the counted words of more than two characters, stopwords aside, in first-seen order.
fn terms_of(value: &str) -> (Vec<String>, HashMap<String, usize>) {
    let (mut order, mut counts) = (Vec::new(), HashMap::new());
    for (token, _, _) in tokens(value) {
        if length(&token) > 2 && !stopword(&token) {
            let count = counts.entry(token.clone()).or_insert(0);
            if *count == 0 {
                order.push(token);
            }
            *count += 1;
        }
    }
    (order, counts)
}

/// `-weight[token], token`, ascending.
fn by_weight<'a>(weight: &'a HashMap<String, f64>) -> impl Fn(&String, &String) -> std::cmp::Ordering + 'a {
    move |a, b| weight[b].partial_cmp(&weight[a]).unwrap_or(std::cmp::Ordering::Equal).then_with(|| a.cmp(b))
}

struct Item {
    value: Map<String, Value>,
    score: f64,
    folded: String,
    ident: String,
}

fn ranked(items: &mut [Item]) {
    items.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.folded.cmp(&b.folded)).then_with(|| a.ident.cmp(&b.ident)));
}

pub fn related(conn: &Connection, text: &Value, exclude: &Value) -> Done<Value> {
    let Some(text) = text.as_str().filter(|text| length(text) <= 20000 && !text.contains('\0')) else {
        return Err(fail(400, "Text must be a string of at most 20000 characters"));
    };
    let identity = match exclude {
        Value::Null => None,
        Value::String(exclude) => {
            let parsed = exclude.split_once(':').filter(|(kind, ident)| {
                EXCLUDABLE.contains(kind) && (1..=80).contains(&length(ident)) && !ident.contains(':')
            });
            Some(parsed.ok_or_else(|| fail(400, "Exclude must look like <kind>:<id>"))?)
        }
        _ => return Err(fail(400, "Exclude must look like <kind>:<id>")),
    };
    searched(crate::search::query::synced(conn))?;
    let (source_key, source_title) = source(conn, identity)?;
    let source_phrase = phrase(&source_title);
    let (query_order, query) = terms_of(text);
    let query_tokens = token_words(text);
    let explicit: HashSet<Phrase> = wikilinks(text).iter().filter_map(|link| phrase(link)).collect();
    let records_count: i64 = conn.query_row(&format!("SELECT COUNT(*) FROM search_docs d LEFT JOIN blocks r ON d.kind='block' AND r.id=d.page_id \
        WHERE {KINDS_SQL}"), [], |row| row.get(0))?;
    let folded = casefold(text);
    let mut spellings: HashMap<String, HashSet<String>> = HashMap::new();
    for raw in runs(&folded) {
        spellings.entry(singular(raw.to_owned())).or_default().insert(raw.to_owned());
    }
    let mut raws: Vec<&String> = spellings.values().flatten().collect::<HashSet<_>>().into_iter().collect();
    raws.sort();
    let mut frequency: HashMap<String, i64> = HashMap::new();
    for chunk in raws.chunks(500) {
        let mut statement = conn.prepare(&format!("SELECT term,doc FROM search_vocab WHERE term IN ({})", vec!["?"; chunk.len()].join(",")))?;
        let mut rows = statement.query(rusqlite::params_from_iter(chunk.iter()))?;
        while let Some(row) = rows.next()? {
            frequency.insert(row.get(0)?, row.get(1)?);
        }
    }
    let mut weight: HashMap<String, f64> = HashMap::new();
    for token in &query_order {
        let seen = spellings.get(token).into_iter().flatten().map(|raw| frequency.get(raw).copied().unwrap_or(0)).max().unwrap_or(0);
        let idf = 1.0 + ((records_count + 2) as f64 / (seen + 1) as f64).ln();
        weight.insert(token.clone(), (1.0 + (query[token] as f64).ln()) * idf);
    }
    let mut top: Vec<String> = query_order.clone();
    top.sort_by(by_weight(&weight));
    top.truncate(RELATED_TERMS);
    // `scores`, in first-seen order.
    let (mut order, mut scores): (Vec<Key>, HashMap<Key, f64>) = (Vec::new(), HashMap::new());
    if !top.is_empty() {
        let expression = top.iter().map(|token| format!("\"{}\"*", stem(token))).collect::<Vec<_>>().join(" OR ");
        for hit in hits(conn, "bm25(search_fts,8.0,1.0)", &expression)? {
            let key = hit.key()?;
            let score = scores.entry(key.clone()).or_insert_with(|| {
                order.push(key);
                0.0
            });
            *score -= hit.score;
        }
    }
    let best = order.iter().map(|key| scores[key]).fold(None, |best: Option<f64>, score| Some(best.map_or(score, |best| if score > best { score } else { best })))
        .unwrap_or(0.0);
    let best = if best == 0.0 { 1.0 } else { best };
    let score_of = |key: &Key| scores.get(key).copied().unwrap_or(0.0);
    let (mut linked, mut mentioned): (HashSet<Key>, HashSet<Key>) = (HashSet::new(), HashSet::new());
    // Titles the text links to or mentions, and records that link to or mention the source's title.
    let mut title_queries: Vec<(String, Option<&Phrase>)> = explicit.iter().map(|target| (phrase_query(target, Some("title")), Some(target))).collect();
    let mut stems: Vec<&str> = query_tokens.iter().filter(|token| !stopword(token)).map(|token| stem(token)).collect::<HashSet<_>>().into_iter().collect();
    stems.sort();
    for chunk in stems.chunks(200) {
        let alternatives = chunk.iter().map(|stem| format!("\"{stem}\"*")).collect::<Vec<_>>().join(" OR ");
        title_queries.push((format!("{{title}} : ({alternatives})"), None));
    }
    for (expression, target) in &title_queries {
        for hit in hits(conn, "0", expression)? {
            if hit.kind == "block" && Some(&hit.reference) != hit.page_id.as_ref() {
                continue;
            }
            let title = phrase(&hit.title);
            match target {
                Some(target) => {
                    if title.as_ref() == Some(*target) {
                        linked.insert(hit.key()?);
                    }
                }
                None => {
                    if title.as_ref().is_some_and(|title| contains(&query_tokens, title)) {
                        mentioned.insert(hit.key()?);
                    }
                }
            }
        }
    }
    if let Some(source_phrase) = &source_phrase {
        for hit in hits(conn, "0", &phrase_query(source_phrase, None))? {
            let content = if hit.title.is_empty() { &hit.body } else { &hit.title };
            if wikilinks(content).iter().any(|link| phrase(link).as_ref() == Some(source_phrase)) {
                linked.insert(hit.key()?);
            } else if contains(&token_words(content), source_phrase) {
                mentioned.insert(hit.key()?);
            }
        }
    }
    mentioned.retain(|key| !linked.contains(key));
    let mut by_score = order.clone();
    by_score.sort_by(|a, b| scores[b].partial_cmp(&scores[a]).unwrap_or(std::cmp::Ordering::Equal));
    let related_keys: Vec<Key> = by_score.into_iter().filter(|key| !linked.contains(key) && !mentioned.contains(key)).collect();
    let set_keys = |set: &HashSet<Key>| {
        let mut keys: Vec<Key> = set.iter().cloned().collect();
        keys.sort();
        keys
    };
    let mut result = Map::new();
    for (bucket, keys) in [("linked", set_keys(&linked)), ("unlinked", set_keys(&mentioned)), ("related", related_keys)] {
        let mut keys: Vec<Key> = keys.into_iter().filter(|key| Some(key) != source_key.as_ref()).collect();
        if bucket != "related" {
            keys.sort_by(|a, b| score_of(b).partial_cmp(&score_of(a)).unwrap_or(std::cmp::Ordering::Equal));
        }
        let mut items: Vec<Item> = Vec::new();
        let step = RELATED_LIMIT * 4;
        let mut start = 0;
        while start < keys.len() {
            let chunk = &keys[start..(start + step).min(keys.len())];
            let found = records(conn, chunk)?;
            for key in chunk {
                let Some((record_id, title, content)) = found.get(key) else { continue };
                let (_, bag) = terms_of(content);
                let mut shared: Vec<String> = query.keys().filter(|token| bag.contains_key(*token)).cloned().collect();
                shared.sort_by(by_weight(&weight));
                shared.truncate(8);
                if bucket == "related" && shared.is_empty() {
                    continue;
                }
                let title: String = title.chars().take(200).collect();
                let score = pyjson::round(score_of(key) / best, 6);
                let mut value = Map::new();
                value.insert("kind".into(), json!(key.0));
                value.insert("id".into(), record_id.clone());
                value.insert("title".into(), json!(title));
                value.insert("snippet".into(), json!(snippet(content, 0, length(content).min(60))));
                value.insert("shared_terms".into(), json!(shared));
                value.insert("score".into(), pyjson::number(score));
                value.insert("view".into(), json!(if key.0 == "note_page" { "notes" } else { "flashcards" }));
                items.push(Item { value, score, folded: casefold(&title), ident: str_of(record_id) });
            }
            ranked(&mut items);
            let end = start + chunk.len();
            // Scores only fall from here on, so a full group cannot change further.
            if items.len() >= RELATED_LIMIT && (end >= keys.len() || score_of(&keys[end]) / best < items[RELATED_LIMIT - 1].score) {
                break;
            }
            start = end;
        }
        ranked(&mut items);
        items.truncate(RELATED_LIMIT);
        result.insert(bucket.into(), Value::Array(items.into_iter().map(|item| Value::Object(item.value)).collect()));
    }
    Ok(Value::Object(result))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scores_round_as_python_does() {
        assert_eq!(pyjson::round(0.0078125, 6), 0.007812);
        assert_eq!(pyjson::round(1.0, 6), 1.0);
        assert_eq!(pyjson::round(0.1234565, 6), 0.123456);
    }
}
