//! Card faces: Anki templates filled from a note (`_render_source`) and the allowlisted HTML
//! the review surface gets (`safe_html`): formatting tags without attributes, stored media as
//! `data:` URIs, everything else (scripts, remote images, links, styles) dropped.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use base64::Engine;
use regex::{Captures, Regex};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{Map, Value};

use super::html::{self, Event};
use super::{require, Error, R};

fn pattern(text: &str) -> Regex {
    Regex::new(text).expect("a valid pattern")
}

pub static CLOZE: LazyLock<Regex> = LazyLock::new(|| pattern(r"(?s)\{\{c([1-9][0-9]*)::(.*?)(?:::(.*?))?\}\}"));
static CONDITIONAL: LazyLock<Regex> = LazyLock::new(|| pattern(r"\{\{([#^/])([^{}]+)\}\}"));
pub static TOKEN: LazyLock<Regex> = LazyLock::new(|| pattern(r"\{\{([^{}]+)\}\}"));
static SOUND: LazyLock<Regex> = LazyLock::new(|| pattern(r"\[sound:([^\]\n]+)\]"));
static STRONG: LazyLock<Regex> = LazyLock::new(|| pattern(r"\*\*([^*\n]+)\*\*"));
static CODE: LazyLock<Regex> = LazyLock::new(|| pattern(r"`([^`\n]+)`"));
static MEDIA_TAG: LazyLock<Regex> = LazyLock::new(|| pattern(r"<(?:img|audio)\b"));

const TAGS: &[&str] = &["b", "strong", "i", "em", "u", "s", "br", "p", "div", "span", "ul", "ol", "li", "blockquote", "pre",
                        "code", "hr", "sub", "sup", "h1", "h2", "h3", "table", "thead", "tbody", "tr", "td", "th"];
const HIDDEN: &[&str] = &["script", "style", "iframe", "object"];

/// `urllib.parse.unquote`.
pub fn unquote(text: &str) -> String {
    percent_encoding::percent_decode_str(text).decode_utf8_lossy().into_owned()
}

/// Whether a field value is truthy (`bool(fields.get(name, ""))`).
fn present(fields: &Map<String, Value>, name: &str) -> bool {
    match fields.get(name) {
        None | Some(Value::Null) | Some(Value::Bool(false)) => false,
        Some(Value::String(text)) => !text.is_empty(),
        Some(Value::Number(number)) => number.as_f64() != Some(0.0),
        Some(Value::Array(items)) => !items.is_empty(),
        Some(Value::Object(map)) => !map.is_empty(),
        Some(Value::Bool(true)) => true,
    }
}

/// A field's text (`fields.get(name, "")`, which is a string for every stored note).
fn field<'a>(fields: &'a Map<String, Value>, name: &str) -> &'a str {
    fields.get(name).and_then(Value::as_str).unwrap_or("")
}

/// `_conditional_template`: `{{#Field}}`/`{{^Field}}` sections kept or dropped.
pub fn conditional(source: &str, fields: &Map<String, Value>) -> R<String> {
    let mut parts = String::new();
    let mut stack: Vec<(String, bool)> = Vec::new();
    let mut visible = true;
    let mut position = 0;
    for found in CONDITIONAL.captures_iter(source) {
        let whole = found.get(0).expect("a match");
        if visible {
            parts.push_str(&source[position..whole.start()]);
        }
        let (operation, name) = (&found[1], &found[2]);
        if operation == "/" {
            match stack.pop() {
                Some((open, before)) if open == name => visible = before,
                _ => return Err(Error::Value("Template conditional sections must be correctly nested".into())),
            }
        } else {
            stack.push((name.to_owned(), visible));
            visible = visible && (present(fields, name) == (operation == "#"));
        }
        position = whole.end();
    }
    if !stack.is_empty() {
        return Err(Error::Value("Template conditional section is not closed".into()));
    }
    if visible {
        parts.push_str(&source[position..]);
    }
    Ok(parts)
}

fn json_map(text: &Value) -> R<Map<String, Value>> {
    match serde_json::from_str::<Value>(text.as_str().unwrap_or("")) {
        Ok(Value::Object(map)) => Ok(map),
        _ => Err(Error::Internal),
    }
}

fn json_list(text: &Value) -> R<Vec<Value>> {
    match serde_json::from_str::<Value>(text.as_str().unwrap_or("")) {
        Ok(Value::Array(items)) => Ok(items),
        _ => Err(Error::Internal),
    }
}

fn template_text(template: &Value, side: &str) -> String {
    template.get(side).and_then(Value::as_str).unwrap_or("").to_owned()
}

/// `_render_source(conn, {"note_id", "ordinal"}, answer)`.
pub fn source(conn: &Connection, note_id: i64, ordinal: i64, answer: bool) -> R<String> {
    let note = require(conn, "flash_notes", &Value::from(note_id))?;
    let kind = require(conn, "flash_types", &note["type_id"])?;
    let mut fields = json_map(&note["fields"])?;
    let templates = json_list(&kind["templates"])?;
    let index = if kind["kind"] == "cloze" { 0 } else { ordinal };
    let template = usize::try_from(index).ok().and_then(|at| templates.get(at)).cloned()
        .ok_or_else(|| Error::Value("list index out of range".into()))?;
    let deck = require(conn, "flash_decks", &note["deck_id"])?;
    let deck_name = deck["name"].as_str().unwrap_or("").to_owned();
    let tags: Vec<String> = json_list(&note["tags"])?.iter().filter_map(|tag| tag.as_str().map(str::to_owned)).collect();
    for (key, value) in [("Tags", tags.join(" ")), ("Type", kind["name"].as_str().unwrap_or("").to_owned()),
                         ("Deck", deck_name.clone()), ("Subdeck", deck_name.rsplit("::").next().unwrap_or("").to_owned()),
                         ("Card", template.get("name").and_then(Value::as_str).unwrap_or("").to_owned())] {
        fields.entry(key).or_insert(Value::String(value));
    }
    let front = template_text(&template, "front");
    fill(&template_text(&template, if answer { "back" } else { "front" }), answer, true, &front, &fields, ordinal)
}

fn fill(source: &str, reveal: bool, allow_front_side: bool, front: &str, fields: &Map<String, Value>, ordinal: i64) -> R<String> {
    let source = conditional(source, fields)?;
    let mut failure = None;
    let filled = TOKEN.replace_all(&source, |found: &Captures| {
        let mut key = &found[1];
        if key == "FrontSide" {
            if !allow_front_side {
                return String::new();
            }
            return fill(front, false, false, front, fields, ordinal).unwrap_or_else(|error| {
                failure = Some(error);
                String::new()
            });
        }
        if let Some(rest) = key.strip_prefix("type:") {
            if !reveal {
                return "[type your answer]".into();
            }
            key = rest;
        }
        if let Some(name) = key.strip_prefix("cloze:") {
            return CLOZE.replace_all(field(fields, name), |cloze: &Captures| {
                let target = cloze[1].parse::<i64>().map(|number| number - 1 == ordinal).unwrap_or(false);
                if reveal || !target {
                    cloze[2].to_owned()
                } else {
                    format!("[{}]", cloze.get(3).map(|hint| hint.as_str()).filter(|hint| !hint.is_empty()).unwrap_or("…"))
                }
            }).into_owned();
        }
        if let Some(name) = key.strip_prefix("text:") {
            return html::escape(&html::plain_text(field(fields, name)));
        }
        field(fields, key).to_owned()
    }).into_owned();
    match failure {
        Some(error) => Err(error),
        None => Ok(filled),
    }
}

/// `SafeHTML.attachment`: a stored file as an inline image or audio player.
fn attachment(conn: &Connection, name: Option<&str>, image: bool) -> R<String> {
    let lookup = |key: &str| -> R<Option<(String, Vec<u8>)>> {
        Ok(conn.query_row("SELECT mime,content FROM flash_media WHERE name=?", params![key], |row| Ok((row.get(0)?, row.get(1)?))).optional()?)
    };
    let found = match name {
        Some(name) => match lookup(name)? {
            Some(found) => Some(found),
            None => lookup(&unquote(name))?,
        },
        // `<img src>`: Python looked up None, then failed to unquote it.
        None => return Err(Error::Value("argument of type 'NoneType' is not iterable".into())),
    };
    let Some((mime, content)) = found else { return Ok("[media unavailable]".into()) };
    let uri = format!("data:{mime};base64,{}", base64::engine::general_purpose::STANDARD.encode(content));
    Ok(if image && mime.starts_with("image/") {
        format!("<img src=\"{uri}\" alt=\"{}\" loading=\"lazy\">", html::escape(name.unwrap_or("")))
    } else if !image && mime.starts_with("audio/") {
        format!("<audio controls preload=\"none\" src=\"{uri}\"></audio>")
    } else {
        "[unsupported media reference]".into()
    })
}

/// `safe_html`.
pub fn safe_html(conn: &Connection, text: &str) -> R<String> {
    let mut audio = Vec::new();
    let mut failure = None;
    let text = SOUND.replace_all(text, |found: &Captures| {
        match attachment(conn, Some(&found[1]), false) {
            Ok(fragment) => audio.push(fragment),
            Err(error) => {
                failure = Some(error);
                audio.push(String::new());
            }
        }
        format!("KITTYAUDIOPLACEHOLDER{}END", audio.len() - 1)
    }).into_owned();
    if let Some(error) = failure {
        return Err(error);
    }
    let text = STRONG.replace_all(&text, "<strong>$1</strong>");
    let text = CODE.replace_all(&text, "<code>$1</code>");
    let mut parts = String::new();
    let mut hidden = 0usize;
    for event in html::parse(&text.replace('\n', "<br>")) {
        match event {
            Event::Start { tag, attrs } => {
                if HIDDEN.contains(&tag.as_str()) {
                    hidden += 1;
                } else if hidden > 0 {
                } else if TAGS.contains(&tag.as_str()) {
                    parts.push_str(&format!("<{tag}>"));
                } else if tag == "img" {
                    // `dict(attrs).get("src", "")`: the last `src` wins.
                    let source = match attrs.iter().rev().find(|(name, _)| name == "src") {
                        Some((_, value)) => value.as_deref(),
                        None => Some(""),
                    };
                    parts.push_str(&attachment(conn, source, true)?);
                }
            }
            Event::End { tag } => {
                if HIDDEN.contains(&tag.as_str()) {
                    hidden = hidden.saturating_sub(1);
                } else if hidden == 0 && TAGS.contains(&tag.as_str()) && tag != "br" && tag != "hr" {
                    parts.push_str(&format!("</{tag}>"));
                }
            }
            Event::Data(data) => {
                if hidden == 0 {
                    parts.push_str(&html::escape(&data));
                }
            }
        }
    }
    for (index, fragment) in audio.iter().enumerate() {
        parts = parts.replace(&format!("KITTYAUDIOPLACEHOLDER{index}END"), fragment);
    }
    Ok(parts)
}

/// `render(conn, card, answer)`.
pub fn render(conn: &Connection, note_id: i64, ordinal: i64, answer: bool) -> R<String> {
    safe_html(conn, &source(conn, note_id, ordinal, answer)?)
}

/// Whether a front shows anything (`_sync_cards`): text, an image or audio.
pub fn has_content(front: &str) -> bool {
    !html::plain_text(front).trim().is_empty() || MEDIA_TAG.is_match(front)
}

/// `_MediaReferences.collect`: the media names a source refers to.
pub fn media_references(names: &mut BTreeSet<String>, source: &str) {
    for event in html::parse(source) {
        if let Event::Start { tag, attrs } = event {
            if tag == "img" {
                if let Some((_, Some(value))) = attrs.iter().rev().find(|(name, _)| name == "src") {
                    if !value.is_empty() {
                        names.insert(value.clone());
                        names.insert(unquote(value));
                    }
                }
            }
        }
    }
    for found in SOUND.captures_iter(source) {
        let name = html::unescape(&found[1]);
        names.insert(unquote(&name));
        names.insert(name);
    }
}
