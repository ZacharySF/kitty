//! Decks, note types, notes and their generated cards, media, and card actions.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use jiff::tz::TimeZone;
use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use super::render::{self, CLOZE, TOKEN};
use super::{loads, py_str, require, rows, same_int, sql, transaction, truthy, value_error, Error, R};
use super::kit::clock;
use super::{pyjson, pytime};

pub const MAX_MEDIA: usize = 8 * 1024 * 1024;

/// `DEFAULT_SETTINGS`.
pub fn default_settings() -> Map<String, Value> {
    let Value::Object(map) = json!({"new_limit": 20, "review_limit": 200, "retention": 0.9,
                                    "learning_steps": [60, 600], "relearning_steps": [600],
                                    "maximum_interval": 36500, "bury_siblings": true,
                                    "leech_threshold": 8, "timezone": "UTC"}) else { unreachable!() };
    map
}

pub fn now_iso() -> String {
    pytime::iso(clock::now())
}

fn exact_int(value: &Value) -> Option<i64> {
    // `type(x) is int`: JSON integers only.
    if value.is_i64() || value.is_u64() { value.as_i64() } else { None }
}

/// `settings(value)`: a deck's settings, defaults filled in, validated.
pub fn settings(value: Option<&Value>) -> R<Map<String, Value>> {
    let empty = Value::Object(Map::new());
    let Value::Object(given) = value.unwrap_or(&empty) else { return Err(value_error("Unknown deck setting")) };
    let mut result = default_settings();
    if given.keys().any(|key| !result.contains_key(key)) {
        return Err(value_error("Unknown deck setting"));
    }
    for (key, item) in given {
        result.insert(key.clone(), item.clone());
    }
    for (key, maximum) in [("new_limit", 10000), ("review_limit", 10000), ("leech_threshold", 100), ("maximum_interval", 36500)] {
        let ok = exact_int(&result[key]).is_some_and(|n| (0..=maximum).contains(&n) && !(n == 0 && matches!(key, "leech_threshold" | "maximum_interval")));
        if !ok {
            return Err(value_error(format!("Invalid {key}")));
        }
    }
    let retention = match &result["retention"] {
        Value::Number(number) => number.as_f64(),
        Value::Bool(flag) => Some(f64::from(u8::from(*flag))),
        _ => None,
    };
    if !retention.is_some_and(|r| (0.7..=0.99).contains(&r)) {
        return Err(value_error("Retention must be between 0.70 and 0.99"));
    }
    for key in ["learning_steps", "relearning_steps"] {
        let ok = match &result[key] {
            Value::Array(steps) => steps.len() <= 10 && {
                let numbers: Vec<Option<i64>> = steps.iter().map(exact_int).collect();
                numbers.iter().all(|n| n.is_some_and(|n| (1..=86400).contains(&n))) && numbers.windows(2).all(|pair| pair[0] <= pair[1])
            },
            _ => false,
        };
        if !ok {
            return Err(value_error("Learning steps must be increasing seconds (1–86400)"));
        }
    }
    if !result["bury_siblings"].is_boolean() {
        return Err(value_error("Sibling bury must be boolean"));
    }
    if !result["timezone"].as_str().is_some_and(|name| pytime::zone(name).is_some()) {
        return Err(value_error("Unknown IANA timezone"));
    }
    Ok(result)
}

/// A deck's stored settings.
pub struct Config {
    pub map: Map<String, Value>,
}

impl Config {
    pub fn of(deck: &Map<String, Value>) -> R<Self> {
        match loads(&deck["settings"])? {
            Value::Object(map) => Ok(Config { map }),
            _ => Err(Error::Internal),
        }
    }

    fn get(&self, key: &str) -> R<&Value> {
        self.map.get(key).ok_or_else(|| value_error(format!("'{key}'")))
    }

    pub fn int(&self, key: &str) -> R<i64> {
        self.get(key)?.as_i64().ok_or(Error::Internal)
    }

    pub fn float(&self, key: &str) -> R<f64> {
        self.get(key)?.as_f64().ok_or(Error::Internal)
    }

    pub fn flag(&self, key: &str) -> R<bool> {
        Ok(truthy(Some(self.get(key)?)))
    }

    pub fn steps(&self, key: &str) -> R<Vec<i64>> {
        self.get(key)?.as_array().ok_or(Error::Internal)?.iter().map(|step| step.as_i64().ok_or(Error::Internal)).collect()
    }

    pub fn zone(&self) -> R<TimeZone> {
        let name = self.get("timezone")?.as_str().ok_or(Error::Internal)?;
        pytime::zone(name).ok_or_else(|| value_error(format!("'No time zone found with key {name}'")))
    }

    pub fn scheduler(&self) -> R<super::scheduler::Settings> {
        Ok(super::scheduler::Settings {
            retention: self.float("retention")?,
            learning_steps: self.steps("learning_steps")?,
            relearning_steps: self.steps("relearning_steps")?,
            maximum_interval: self.int("maximum_interval")?,
        })
    }
}

/// The next local midnight after `moment` in `zone`, as an ISO UTC moment.
pub fn next_midnight(moment: i64, zone: &TimeZone) -> String {
    pytime::iso(pytime::local_midnight(pytime::local_day(moment, zone) + 1, zone))
}

/// `bootstrap`: the Default deck and the three built-in note types.
pub fn bootstrap(conn: &Connection) -> R<()> {
    conn.execute("INSERT OR IGNORE INTO flash_decks(name,settings,created_at) VALUES(?,?,?)",
                 params!["Default", pyjson::compact(&Value::Object(default_settings())), now_iso()])?;
    for (name, kind, fields, templates) in [
        ("Basic", "basic", json!(["Front", "Back"]), json!([{"name": "Card 1", "front": "{{Front}}", "back": "{{FrontSide}}<hr>{{Back}}"}])),
        ("Basic (reversed)", "reversed", json!(["Front", "Back"]), json!([{"name": "Forward", "front": "{{Front}}", "back": "{{Back}}"}, {"name": "Reverse", "front": "{{Back}}", "back": "{{Front}}"}])),
        ("Cloze", "cloze", json!(["Text", "Extra"]), json!([{"name": "Cloze", "front": "{{cloze:Text}}", "back": "{{cloze:Text}}<hr>{{Extra}}"}])),
    ] {
        conn.execute("INSERT OR IGNORE INTO flash_types(name,kind,fields,templates) VALUES(?,?,?,?)",
                     params![name, kind, pyjson::compact(&fields), pyjson::compact(&templates)])?;
    }
    Ok(())
}

fn chars(text: &str) -> usize {
    text.chars().count()
}

fn deck_id_of(conn: &Connection, name: &str) -> R<i64> {
    Ok(conn.query_row("SELECT id FROM flash_decks WHERE name=?", params![name], |row| row.get(0))?)
}

/// Create the ancestors of `name` (`Parent::Child`): the parent's id, if any.
pub fn ensure_ancestors(conn: &Connection, name: &str, include_self: bool) -> R<Option<i64>> {
    let parts: Vec<&str> = name.split("::").collect();
    let mut parent: Option<i64> = None;
    let last = if include_self { parts.len() + 1 } else { parts.len() };
    for index in 1..last {
        let ancestor = parts[..index].join("::");
        conn.execute("INSERT OR IGNORE INTO flash_decks(name,parent_id,settings,created_at) VALUES(?,?,?,?)",
                     params![ancestor, parent, pyjson::compact(&Value::Object(default_settings())), now_iso()])?;
        parent = Some(deck_id_of(conn, &ancestor)?);
    }
    Ok(parent)
}

/// `save_deck`.
pub fn save_deck(conn: &Connection, body: &Map<String, Value>) -> R<Map<String, Value>> {
    let name = body.get("name").map(py_str).unwrap_or_default().trim().to_owned();
    if name.is_empty() || chars(&name) > 200 || name.split("::").any(|part| part.trim().is_empty()) {
        return Err(value_error("Deck name required; use Parent::Child for subdecks"));
    }
    let config = settings(body.get("settings"))?;
    let given = body.get("id").cloned().unwrap_or(Value::Null);
    transaction(conn, || {
        let parent = ensure_ancestors(conn, &name, false)?;
        let ident = if truthy(Some(&given)) {
            let old = require(conn, "flash_decks", &given)?;
            if !same_int(&old["revision"], body.get("revision")) {
                return Err(value_error("Deck changed; reload before saving"));
            }
            let old_name = old["name"].as_str().unwrap_or("").to_owned();
            if name != old_name {
                let prefix = format!("{old_name}::");
                let descendants = rows(conn, "SELECT id,name FROM flash_decks WHERE substr(name,1,?)=?",
                                       &[(chars(&old_name) as i64 + 2).into(), prefix.clone().into()])?;
                if name.starts_with(&prefix) {
                    return Err(value_error("Cannot move a deck inside itself"));
                }
                for child in descendants {
                    let child_name = child["name"].as_str().unwrap_or("");
                    let rest: String = child_name.chars().skip(chars(&old_name)).collect();
                    conn.execute("UPDATE flash_decks SET name=?,revision=revision+1 WHERE id=?", params![format!("{name}{rest}"), child["id"].as_i64()])?;
                }
            }
            conn.execute("UPDATE flash_decks SET name=?,parent_id=?,settings=?,archived=?,revision=revision+1 WHERE id=?",
                         rusqlite::params_from_iter([name.clone().into(), parent.into(), pyjson::compact(&Value::Object(config.clone())).into(),
                                                     i64::from(truthy(body.get("archived"))).into(), sql(&given)?]))?;
            given.clone()
        } else {
            conn.execute("INSERT INTO flash_decks(name,parent_id,settings,created_at) VALUES(?,?,?,?)",
                         params![name, parent, pyjson::compact(&Value::Object(config.clone())), now_iso()])?;
            Value::from(conn.last_insert_rowid())
        };
        require(conn, "flash_decks", &ident)
    })
}

static ANKI_TOKEN_FILTERS: &[&str] = &["cloze", "type", "text", "type:cloze", "type:text"];

/// `_validate_type`.
pub fn validate_type(name: &str, kind: &Value, fields: Option<&Value>, templates: Option<&Value>) -> R<()> {
    if name.is_empty() || chars(name) > 200 || !matches!(kind.as_str(), Some("basic" | "reversed" | "cloze" | "template")) {
        return Err(value_error("Invalid note type"));
    }
    let names: Option<Vec<&str>> = fields.and_then(Value::as_array).map(|items| items.iter().filter_map(Value::as_str).collect());
    let valid_fields = match (fields.and_then(Value::as_array), &names) {
        (Some(items), Some(names)) => (1..=30).contains(&items.len()) && names.len() == items.len()
            && names.iter().all(|f| !f.trim().is_empty() && chars(f) <= 80 && !f.chars().any(|c| "{}:".contains(c) || (c as u32) < 32))
            && names.iter().collect::<BTreeSet<_>>().len() == names.len(),
        _ => false,
    };
    if !valid_fields {
        return Err(value_error("Provide unique field names"));
    }
    let Some(templates) = templates.and_then(Value::as_array).filter(|items| (1..=20).contains(&items.len())) else {
        return Err(value_error("Provide 1–20 card templates"));
    };
    let presence: Map<String, Value> = names.unwrap_or_default().iter().map(|f| (f.to_string(), json!("present"))).collect();
    for template in templates {
        let ok = template.as_object().is_some_and(|t| ["name", "front", "back"].iter().all(|k| t.get(*k).and_then(Value::as_str).is_some_and(|s| chars(s) <= 20000)));
        if !ok {
            return Err(value_error("Each template needs name, front and back"));
        }
        for side in ["front", "back"] {
            let source = template[side].as_str().unwrap_or("");
            render::conditional(source, &presence)?;
            for found in TOKEN.captures_iter(source) {
                let token = &found[1];
                if let Some((filter, _)) = token.rsplit_once(':') {
                    if !ANKI_TOKEN_FILTERS.contains(&filter) {
                        return Err(value_error("Unsupported Anki template field filter; supported filters are cloze, type and text"));
                    }
                }
            }
        }
    }
    Ok(())
}

/// `save_type`.
pub fn save_type(conn: &Connection, body: &Map<String, Value>) -> R<Map<String, Value>> {
    let name = body.get("name").map(py_str).unwrap_or_default().trim().to_owned();
    let kind = body.get("kind").cloned().unwrap_or_else(|| json!("template"));
    let (fields, templates) = (body.get("fields"), body.get("templates"));
    validate_type(&name, &kind, fields, templates)?;
    let (fields, templates) = (fields.cloned().unwrap_or_default(), templates.cloned().unwrap_or_default());
    let kind = kind.as_str().unwrap_or("").to_owned();
    transaction(conn, || {
        let given = body.get("id").cloned().unwrap_or(Value::Null);
        let ident = if truthy(Some(&given)) {
            let old = require(conn, "flash_types", &given)?;
            if !same_int(&old["revision"], body.get("revision")) {
                return Err(value_error("Note type changed; reload"));
            }
            let in_use = super::scalar(conn, "SELECT 1 FROM flash_notes WHERE type_id=? LIMIT 1", &[sql(&given)?])?.is_some();
            if loads(&old["fields"])? != fields && in_use {
                return Err(value_error("Fields of an in-use type cannot be renamed; create a new type and edit notes"));
            }
            conn.execute("UPDATE flash_types SET name=?,kind=?,fields=?,templates=?,revision=revision+1 WHERE id=?",
                         rusqlite::params_from_iter([name.clone().into(), kind.clone().into(), pyjson::compact(&fields).into(),
                                                     pyjson::compact(&templates).into(), sql(&given)?]))?;
            let templates = templates.as_array().cloned().unwrap_or_default();
            for note in rows(conn, "SELECT * FROM flash_notes WHERE type_id=?", &[sql(&given)?])? {
                sync_cards(conn, &note, &kind, &templates)?;
            }
            given.clone()
        } else {
            conn.execute("INSERT INTO flash_types(name,kind,fields,templates) VALUES(?,?,?,?)",
                         params![name, kind, pyjson::compact(&fields), pyjson::compact(&templates)])?;
            Value::from(conn.last_insert_rowid())
        };
        require(conn, "flash_types", &ident)
    })
}

static CLOZE_FIELD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{\{(?:type:)?cloze:([^{}]+)\}\}").expect("a valid pattern"));

/// `_sync_cards`: one card per nonempty front (per cloze number), schedules kept.
pub fn sync_cards(conn: &Connection, note: &Map<String, Value>, kind: &str, templates: &[Value]) -> R<()> {
    let note_id = note["id"].as_i64().ok_or(Error::Internal)?;
    let fields = match loads(&note["fields"])? {
        Value::Object(map) => map,
        _ => return Err(Error::Internal),
    };
    let mut ordinals = BTreeSet::new();
    if kind == "cloze" {
        let front = templates.first().and_then(|t| t.get("front")).and_then(Value::as_str).unwrap_or("");
        let names: BTreeSet<&str> = CLOZE_FIELD.captures_iter(front).map(|found| found.get(1).expect("a group").as_str()).collect();
        for name in names {
            let text = fields.get(name).and_then(Value::as_str).unwrap_or("");
            for found in CLOZE.captures_iter(text) {
                ordinals.insert(found[1].parse::<i64>().unwrap_or(i64::MAX) - 1);
            }
        }
        if ordinals.is_empty() {
            return Err(value_error("Cloze front needs {{cloze:Field}} and text with {{c1::answer::optional hint}}"));
        }
    } else {
        for ordinal in 0..templates.len() as i64 {
            if render::has_content(&render::render(conn, note_id, ordinal, false)?) {
                ordinals.insert(ordinal);
            }
        }
        if ordinals.is_empty() {
            return Err(value_error("A note needs at least one nonempty card front"));
        }
    }
    let existing: BTreeSet<i64> = conn.prepare("SELECT ordinal FROM flash_cards WHERE note_id=?")?
        .query_map(params![note_id], |row| row.get(0))?.collect::<Result<_, _>>()?;
    for ordinal in ordinals.difference(&existing) {
        conn.execute("INSERT INTO flash_cards(note_id,ordinal,due) VALUES(?,?,?)", params![note_id, ordinal, now_iso()])?;
    }
    for ordinal in existing.difference(&ordinals) {
        conn.execute("DELETE FROM flash_cards WHERE note_id=? AND ordinal=?", params![note_id, ordinal])?;
    }
    conn.execute("UPDATE flash_cards SET revision=revision+1 WHERE note_id=?", params![note_id])?;
    Ok(())
}

static TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[^\s]{1,80}$").expect("a valid pattern"));

/// `_save_note` (inside the caller's transaction).
pub fn save_note_inner(conn: &Connection, body: &Map<String, Value>) -> R<Map<String, Value>> {
    let kind = require(conn, "flash_types", body.get("type_id").unwrap_or(&Value::Null))?;
    require(conn, "flash_decks", body.get("deck_id").unwrap_or(&Value::Null))?;
    let names: BTreeSet<String> = loads(&kind["fields"])?.as_array().ok_or(Error::Internal)?
        .iter().filter_map(|name| name.as_str().map(str::to_owned)).collect();
    let fields = match body.get("fields") {
        Some(Value::Object(fields)) if fields.keys().cloned().collect::<BTreeSet<_>>() == names
            && fields.values().all(|value| value.as_str().is_some_and(|text| chars(text) <= 100_000)) => fields.clone(),
        _ => return Err(value_error("Fields must match note type (up to 100000 characters each)")),
    };
    let empty = json!([]);
    let tags = match body.get("tags").unwrap_or(&empty) {
        Value::Array(tags) if tags.len() <= 100 && tags.iter().all(|tag| tag.as_str().is_some_and(|tag| TAG.is_match(tag))) => {
            tags.iter().filter_map(|tag| tag.as_str().map(str::to_owned)).collect::<BTreeSet<String>>()
        }
        _ => return Err(value_error("Tags must be short words without spaces")),
    };
    let tags = pyjson::compact(&json!(tags.into_iter().collect::<Vec<_>>()));
    let fields_text = pyjson::compact(&Value::Object(fields));
    let archived = i64::from(truthy(body.get("archived")));
    let timestamp = now_iso();
    let given = body.get("id").cloned().unwrap_or(Value::Null);
    let deck = sql(&body["deck_id"])?;
    let kind_id = sql(&body["type_id"])?;
    let ident = if truthy(Some(&given)) {
        let old = require(conn, "flash_notes", &given)?;
        if !same_int(&old["revision"], body.get("revision")) {
            return Err(value_error("Note changed; reload before editing"));
        }
        conn.execute("UPDATE flash_notes SET deck_id=?,type_id=?,fields=?,tags=?,archived=?,updated_at=?,revision=revision+1 WHERE id=?",
                     rusqlite::params_from_iter([deck, kind_id, fields_text.into(), tags.into(), archived.into(), timestamp.into(), sql(&given)?]))?;
        given
    } else {
        let guid = match body.get("guid") {
            Some(value) if truthy(Some(value)) => sql(value)?,
            _ => super::kit::uuid4_hex().into(),
        };
        conn.execute("INSERT INTO flash_notes(guid,deck_id,type_id,fields,tags,archived,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     rusqlite::params_from_iter([guid, deck, kind_id, fields_text.into(), tags.into(), archived.into(), timestamp.clone().into(), timestamp.into()]))?;
        Value::from(conn.last_insert_rowid())
    };
    let note = require(conn, "flash_notes", &ident)?;
    let templates = loads(&kind["templates"])?.as_array().cloned().unwrap_or_default();
    sync_cards(conn, &note, kind["kind"].as_str().unwrap_or(""), &templates)?;
    Ok(note)
}

/// `save_note`.
pub fn save_note(conn: &Connection, body: &Map<String, Value>) -> R<Map<String, Value>> {
    transaction(conn, || save_note_inner(conn, body))
}

/// `media_mime`: only images and audio, never HTML or SVG.
pub fn media_mime(data: &[u8]) -> R<&'static str> {
    Ok(if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        "image/png"
    } else if data.starts_with(b"\xff\xd8\xff") {
        "image/jpeg"
    } else if data.starts_with(b"GIF87a") || data.starts_with(b"GIF89a") {
        "image/gif"
    } else if data.get(..4) == Some(b"RIFF") && data.get(8..12) == Some(b"WEBP") {
        "image/webp"
    } else if data.get(..4) == Some(b"RIFF") && data.get(8..12) == Some(b"WAVE") {
        "audio/wav"
    } else if data.starts_with(b"OggS") {
        "audio/ogg"
    } else if data.starts_with(b"ID3") || (data.len() > 2 && data[0] == 255 && data[1] & 224 == 224) {
        "audio/mpeg"
    } else {
        return Err(value_error("Media must be PNG/JPEG/GIF/WebP or MP3/Ogg/WAV; HTML/SVG are not allowed"));
    })
}

/// `_store_media` (inside the caller's transaction).
pub fn store_media(conn: &Connection, name: Option<&Value>, data: &[u8]) -> R<Value> {
    let name = match name {
        Some(Value::String(name)) if !name.is_empty() && chars(name) <= 200 && !name.contains(['/', '\\'])
            && name != "." && name != ".." && !name.chars().any(|c| (c as u32) < 32) => name.clone(),
        _ => return Err(value_error("Media filename must be a safe basename")),
    };
    if data.len() > MAX_MEDIA {
        return Err(value_error("Media exceeds 8 MiB"));
    }
    let mime = media_mime(data)?;
    let digest = hex::encode(Sha256::digest(data));
    let old: Option<String> = conn.query_row("SELECT sha256 FROM flash_media WHERE name=?", params![name], |row| row.get(0)).optional()?;
    if old.is_some_and(|old| old != digest) {
        return Err(value_error(format!("Media name {name} already contains different content; rename before importing")));
    }
    conn.execute("INSERT OR IGNORE INTO flash_media(name,mime,content,sha256) VALUES(?,?,?,?)", params![name, mime, data, digest])?;
    Ok(json!({ "name": name, "mime": mime }))
}

/// `action`: delete or archive a note; suspend or bury a card.
pub fn action(conn: &Connection, body: &Map<String, Value>) -> R<Value> {
    let ident = body.get("id").cloned().unwrap_or(Value::Null);
    let operation = body.get("action").and_then(Value::as_str).unwrap_or("");
    let table = if matches!(operation, "delete" | "archive") { "flash_notes" } else { "flash_cards" };
    let on = || i64::from(truthy(Some(body.get("value").unwrap_or(&Value::Bool(true)))));
    transaction(conn, || {
        let old = require(conn, table, &ident)?;
        if !same_int(&old["revision"], body.get("revision")) {
            return Err(value_error("Item changed; reload before editing"));
        }
        let id = sql(&ident)?;
        match operation {
            "delete" => conn.execute("DELETE FROM flash_notes WHERE id=?", [id])?,
            "archive" => conn.execute("UPDATE flash_notes SET archived=?,revision=revision+1 WHERE id=?", rusqlite::params_from_iter([on().into(), id]))?,
            "suspend" => conn.execute("UPDATE flash_cards SET suspended=?,revision=revision+1 WHERE id=?", rusqlite::params_from_iter([on().into(), id]))?,
            "bury" => {
                let note = require(conn, "flash_notes", &old["note_id"])?;
                let config = super::store::Config::of(&require(conn, "flash_decks", &note["deck_id"])?)?;
                let until = if on() == 1 { Some(next_midnight(clock::now(), &config.zone()?)) } else { None };
                conn.execute("UPDATE flash_cards SET buried_until=?,revision=revision+1 WHERE id=?", rusqlite::params_from_iter([until.into(), id]))?
            }
            _ => return Err(value_error("Unknown card action")),
        };
        Ok(json!({ "ok": true }))
    })
}

/// `fronts`: each note's first field in exactly this deck.
pub fn fronts(conn: &Connection, deck: i64) -> R<Vec<Value>> {
    require(conn, "flash_decks", &Value::from(deck))?;
    let mut result = Vec::new();
    for row in rows(conn, "SELECT n.fields,t.fields AS names FROM flash_notes n JOIN flash_types t ON t.id=n.type_id WHERE n.deck_id=? ORDER BY n.id", &[deck.into()])? {
        let fields = loads(&row["fields"])?;
        let names = loads(&row["names"])?;
        let first_name = names.get(0).and_then(Value::as_str).ok_or_else(|| value_error("list index out of range"))?;
        result.push(fields.get(first_name).cloned().unwrap_or_else(|| json!("")));
    }
    Ok(result)
}
