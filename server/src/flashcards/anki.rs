//! CSV/TSV and Anki package (`.apkg`) import and export.
//!
//! Imported collections are opened read-only with `trusted_schema=OFF` and never run archive
//! code; executable media (HTML, SVG) is skipped, never copied in. rika! exports keep the
//! exact FSRS state in a `kitty_card_state` table besides Anki's own scheduling fields.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io::{Cursor, Read, Write};
use std::path::PathBuf;

use rusqlite::types::Value as Sql;
use rusqlite::{params, Connection, OpenFlags};
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use super::render::{self, TOKEN};
use super::scheduler::{Card, State};
use super::store::{self, settings, store_media, validate_type, Config};
use super::{loads, require, review, rows, transaction, value_error, Error, R};
use super::kit::{clock, token_hex};
use super::{pyjson, pytime};

const MAX_ARCHIVE: usize = 128 * 1024 * 1024;

// ------------------------------------------------------------------ CSV

/// `csv.reader(io.StringIO(text), delimiter=...)`, the excel dialect.
fn read_csv(text: &str, delimiter: char) -> R<Vec<Vec<String>>> {
    #[derive(PartialEq, Clone, Copy)]
    enum S { StartRecord, StartField, InField, InQuoted, QuoteInQuoted, EatCrnl }
    let mut records = Vec::new();
    let mut fields: Vec<String> = Vec::new();
    let mut field = String::new();
    let mut state = S::StartRecord;
    let newline = || Error::Internal; // csv.Error: not a ValueError, Python answered 500
    for line in text.split_inclusive('\n') {
        for c in line.chars() {
            state = match state {
                S::StartRecord | S::StartField => {
                    if c == '\n' || c == '\r' {
                        if state == S::StartField {
                            fields.push(std::mem::take(&mut field));
                        }
                        S::EatCrnl
                    } else if c == '"' {
                        S::InQuoted
                    } else if c == delimiter {
                        fields.push(std::mem::take(&mut field));
                        S::StartField
                    } else {
                        field.push(c);
                        S::InField
                    }
                }
                S::InField => {
                    if c == '\n' || c == '\r' {
                        fields.push(std::mem::take(&mut field));
                        S::EatCrnl
                    } else if c == delimiter {
                        fields.push(std::mem::take(&mut field));
                        S::StartField
                    } else {
                        field.push(c);
                        S::InField
                    }
                }
                S::InQuoted => {
                    if c == '"' { S::QuoteInQuoted } else {
                        field.push(c);
                        S::InQuoted
                    }
                }
                S::QuoteInQuoted => {
                    if c == '"' {
                        field.push('"');
                        S::InQuoted
                    } else if c == delimiter {
                        fields.push(std::mem::take(&mut field));
                        S::StartField
                    } else if c == '\n' || c == '\r' {
                        fields.push(std::mem::take(&mut field));
                        S::EatCrnl
                    } else {
                        field.push(c);
                        S::InField
                    }
                }
                S::EatCrnl => {
                    if c == '\n' || c == '\r' { S::EatCrnl } else { return Err(newline()) }
                }
            };
        }
        // The end of a line.
        state = match state {
            S::InQuoted => S::InQuoted,
            S::StartRecord | S::EatCrnl => {
                records.push(std::mem::take(&mut fields));
                S::StartRecord
            }
            _ => {
                fields.push(std::mem::take(&mut field));
                records.push(std::mem::take(&mut fields));
                S::StartRecord
            }
        };
    }
    if state == S::InQuoted {
        fields.push(std::mem::take(&mut field));
        records.push(fields);
    }
    Ok(records)
}

/// `csv.writer(output, delimiter=...).writerow(row)`: minimal quoting, `\r\n` endings.
fn write_csv_row(out: &mut String, row: &[String], delimiter: char) {
    if row.len() == 1 && row[0].is_empty() {
        out.push_str("\"\"\r\n");
        return;
    }
    for (index, field) in row.iter().enumerate() {
        if index > 0 {
            out.push(delimiter);
        }
        if field.contains([delimiter, '"', '\r', '\n']) {
            out.push('"');
            out.push_str(&field.replace('"', "\"\""));
            out.push('"');
        } else {
            out.push_str(field);
        }
    }
    out.push_str("\r\n");
}

fn names_of(kind: &Map<String, Value>) -> R<Vec<String>> {
    Ok(loads(&kind["fields"])?.as_array().ok_or(Error::Internal)?.iter().filter_map(|n| n.as_str().map(str::to_owned)).collect())
}

/// `import_text`.
pub fn import_text(conn: &Connection, text: &str, deck: i64, kind_id: i64, delimiter: char) -> R<Value> {
    let kind = require(conn, "flash_types", &Value::from(kind_id))?;
    let names = names_of(&kind)?;
    let mut records = read_csv(text, delimiter)?;
    if records.first().is_some_and(|first| first.iter().take(names.len()).eq(names.iter())) {
        records.remove(0);
    }
    if records.len() > 10000 {
        return Err(value_error("At most 10000 notes per import"));
    }
    transaction(conn, || {
        let mut count = 0;
        for record in &records {
            if record.is_empty() {
                continue;
            }
            if record.len() != names.len() && record.len() != names.len() + 1 {
                return Err(value_error("Each row must have note fields and optional final space-separated tags"));
            }
            let fields: Map<String, Value> = names.iter().zip(record).map(|(n, v)| (n.clone(), json!(v))).collect();
            let tags: Vec<&str> = record.get(names.len()).map(|t| t.split_whitespace().collect()).unwrap_or_default();
            let body = json!({"deck_id": deck, "type_id": kind_id, "fields": fields, "tags": tags});
            store::save_note_inner(conn, body.as_object().expect("an object"))?;
            count += 1;
        }
        Ok(json!({ "imported": count }))
    })
}

fn browser_notes(conn: &Connection, deck: Option<i64>) -> R<Vec<Value>> {
    Ok(review::browser(conn, deck, "", 100_000, 0)?["notes"].as_array().cloned().unwrap_or_default())
}

/// `export_text`.
pub fn export_text(conn: &Connection, deck: Option<i64>, delimiter: char) -> R<Vec<u8>> {
    let notes = browser_notes(conn, deck)?;
    let kinds: BTreeSet<i64> = notes.iter().filter_map(|n| n["type_id"].as_i64()).collect();
    if kinds.len() > 1 {
        return Err(value_error("CSV export requires one note type; use APKG for mixed types"));
    }
    let mut out = String::new();
    if let Some(first) = notes.first() {
        let names = names_of(&require(conn, "flash_types", &first["type_id"])?)?;
        let mut header = names.clone();
        header.push("Tags".into());
        write_csv_row(&mut out, &header, delimiter);
        for note in &notes {
            let mut row: Vec<String> = names.iter().map(|n| note["fields"].get(n).and_then(Value::as_str).unwrap_or("").to_owned()).collect();
            row.push(note["tags"].as_array().map(|t| t.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(" ")).unwrap_or_default());
            write_csv_row(&mut out, &row, delimiter);
        }
    }
    Ok(out.into_bytes())
}

// ------------------------------------------------------------------ archives

/// `_decompress`: a Zstandard member expanded, within the archive limit.
fn decompress(data: Vec<u8>) -> R<Vec<u8>> {
    let data = if data.starts_with(b"\x28\xb5\x2f\xfd") {
        let invalid = || value_error("Invalid Zstandard-compressed Anki archive member");
        let mut source = Cursor::new(data);
        let decoder = ruzstd::decoding::StreamingDecoder::new(&mut source).map_err(|_| invalid())?;
        let mut out = Vec::new();
        decoder.take(MAX_ARCHIVE as u64 + 1).read_to_end(&mut out).map_err(|_| invalid())?;
        out
    } else {
        data
    };
    if data.len() > MAX_ARCHIVE {
        return Err(value_error("Expanded archive exceeds 128 MiB"));
    }
    Ok(data)
}

#[derive(Clone, Debug)]
enum Wire {
    Int(u128),
    Bytes(Vec<u8>),
}

/// `_protobuf`: Anki's public protobuf wire fields, without running archive code.
fn protobuf(data: &[u8]) -> R<HashMap<u64, Vec<Wire>>> {
    let mut position = 0usize;
    let mut fields: HashMap<u64, Vec<Wire>> = HashMap::new();
    let varint = |position: &mut usize| -> R<u128> {
        let mut value: u128 = 0;
        for shift in (0..70).step_by(7) {
            if *position >= data.len() {
                break;
            }
            let byte = data[*position];
            *position += 1;
            value |= u128::from(byte & 127) << shift;
            if byte < 128 {
                return Ok(value);
            }
        }
        Err(value_error("Malformed Anki protobuf integer"))
    };
    while position < data.len() {
        let tag = varint(&mut position)?;
        let (number, wire) = ((tag >> 3) as u64, tag & 7);
        if number == 0 {
            return Err(value_error("Malformed Anki protobuf field"));
        }
        let value = match wire {
            0 => Wire::Int(varint(&mut position)?),
            1 | 2 | 5 => {
                let size = if wire == 2 { varint(&mut position)? } else if wire == 1 { 8 } else { 4 };
                if size > (data.len() - position) as u128 {
                    return Err(value_error("Truncated Anki protobuf field"));
                }
                let size = size as usize;
                let value = data[position..position + size].to_vec();
                position += size;
                Wire::Bytes(value)
            }
            _ => return Err(value_error("Unsupported Anki protobuf wire type")),
        };
        fields.entry(number).or_default().push(value);
    }
    Ok(fields)
}

fn last(fields: &HashMap<u64, Vec<Wire>>, number: u64) -> Option<&Wire> {
    fields.get(&number).and_then(|values| values.last())
}

/// A row of the imported collection, columns by name.
type SourceRow = HashMap<String, Sql>;

fn source_rows(source: &Connection, query: &str, args: &[Sql]) -> R<Vec<SourceRow>> {
    let mut statement = source.prepare(query)?;
    let names: Vec<String> = statement.column_names().into_iter().map(|n| n.to_string()).collect();
    let mut found = statement.query(rusqlite::params_from_iter(args.iter()))?;
    let mut result = Vec::new();
    while let Some(row) = found.next()? {
        let mut map = HashMap::new();
        for (index, name) in names.iter().enumerate() {
            map.insert(name.clone(), row.get::<_, Sql>(index)?);
        }
        result.push(map);
    }
    Ok(result)
}

fn column<'a>(row: &'a SourceRow, name: &str) -> R<&'a Sql> {
    row.get(name).ok_or_else(|| value_error(format!("No item with that key")))
}

fn int_of(row: &SourceRow, name: &str) -> R<i64> {
    match column(row, name)? {
        Sql::Integer(n) => Ok(*n),
        Sql::Real(f) => Ok(*f as i64),
        _ => Err(Error::Internal),
    }
}

fn text_of(row: &SourceRow, name: &str) -> R<String> {
    match column(row, name)? {
        Sql::Text(t) => Ok(t.clone()),
        _ => Err(Error::Internal),
    }
}

fn blob_of(row: &SourceRow, name: &str) -> R<Vec<u8>> {
    match column(row, name)? {
        Sql::Blob(b) => Ok(b.clone()),
        Sql::Text(t) => Ok(t.clone().into_bytes()),
        Sql::Null => Err(value_error("a bytes-like object is required, not 'NoneType'")),
        _ => Err(Error::Internal),
    }
}

struct Model {
    name: String,
    kind: i64,
    fields: Vec<(i64, String)>,
    templates: Vec<(i64, String, String, String)>,
}

/// `_anki_models_decks`: note types and deck names, legacy JSON or schema 15+ tables.
fn models_decks(source: &Connection, col: &SourceRow, tables: &BTreeSet<String>) -> R<(Vec<(String, Model)>, Vec<(String, String)>)> {
    if !tables.contains("notetypes") {
        let invalid = || value_error("Invalid Anki note types or decks");
        let models: Value = serde_json::from_str(&text_of(col, "models").map_err(|_| invalid())?).map_err(|_| invalid())?;
        let decks: Value = serde_json::from_str(&text_of(col, "decks").map_err(|_| invalid())?).map_err(|_| invalid())?;
        let (Some(models), Some(decks)) = (models.as_object(), decks.as_object()) else { return Err(invalid()) };
        if models.values().any(|m| !m.is_object()) || decks.values().any(|d| !d.get("name").is_some_and(Value::is_string)) {
            return Err(invalid());
        }
        let mut result = Vec::new();
        for (key, model) in models {
            let get = |name: &str| model.get(name).ok_or_else(|| value_error(format!("'{name}'")));
            let list = |name: &str| -> R<Vec<Value>> { get(name)?.as_array().cloned().ok_or(Error::Internal) };
            let field_list = list("flds")?.iter().map(|f| Ok((f.get("ord").and_then(Value::as_i64).ok_or(Error::Internal)?,
                                                             f.get("name").and_then(Value::as_str).ok_or(Error::Internal)?.to_owned()))).collect::<R<Vec<_>>>()?;
            let templates = list("tmpls")?.iter().map(|t| Ok((t.get("ord").and_then(Value::as_i64).ok_or(Error::Internal)?,
                                                            t.get("name").and_then(Value::as_str).ok_or(Error::Internal)?.to_owned(),
                                                            t.get("qfmt").and_then(Value::as_str).ok_or(Error::Internal)?.to_owned(),
                                                            t.get("afmt").and_then(Value::as_str).ok_or(Error::Internal)?.to_owned()))).collect::<R<Vec<_>>>()?;
            result.push((key.clone(), Model {
                name: get("name")?.as_str().ok_or(Error::Internal)?.to_owned(),
                kind: model.get("type").and_then(Value::as_i64).unwrap_or(0),
                fields: field_list,
                templates,
            }));
        }
        let decks = decks.iter().map(|(key, deck)| (key.clone(), deck["name"].as_str().unwrap_or("").to_owned())).collect();
        return Ok((result, decks));
    }
    let mut models = Vec::new();
    for row in source_rows(source, "SELECT id,name,config FROM notetypes", &[])? {
        let config = protobuf(&blob_of(&row, "config")?)?;
        let kind = match last(&config, 1) {
            None => 0,
            Some(Wire::Int(kind @ (0 | 1))) => *kind as i64,
            Some(_) => return Err(value_error("Unknown Anki note type kind")),
        };
        let id = int_of(&row, "id")?;
        let fields = source_rows(source, "SELECT ord,name FROM fields WHERE ntid=? ORDER BY ord", &[Sql::Integer(id)])?
            .iter().map(|f| Ok((int_of(f, "ord")?, text_of(f, "name")?))).collect::<R<Vec<_>>>()?;
        let mut templates = Vec::new();
        for template in source_rows(source, "SELECT ord,name,config FROM templates WHERE ntid=? ORDER BY ord", &[Sql::Integer(id)])? {
            let formats = protobuf(&blob_of(&template, "config")?)?;
            let side = |number| match last(&formats, number) {
                None => Ok(String::new()),
                Some(Wire::Bytes(bytes)) => String::from_utf8(bytes.clone()).map_err(|_| value_error("'utf-8' codec can't decode bytes")),
                Some(Wire::Int(_)) => Err(value_error("Malformed Anki template format")),
            };
            templates.push((int_of(&template, "ord")?, text_of(&template, "name")?, side(1)?, side(2)?));
        }
        models.push((id.to_string(), Model { name: text_of(&row, "name")?, kind, fields, templates }));
    }
    let mut decks = Vec::new();
    for row in source_rows(source, "SELECT id,name FROM decks", &[])? {
        let name = text_of(&row, "name").map_err(|_| value_error("Invalid Anki deck name"))?;
        decks.push((int_of(&row, "id")?.to_string(), name.replace('\x1f', "::")));
    }
    Ok((models, decks))
}

/// `_anki_media`: (member, filename, size, SHA-1) for each media file.
fn media_entries(data: &[u8], modern: bool) -> R<Vec<(String, String, Option<u128>, Option<Vec<u8>>)>> {
    if !modern {
        let invalid = || value_error("Anki media manifest must map numbered members to filenames");
        let manifest: Value = serde_json::from_slice(data).map_err(|_| invalid())?;
        let manifest = manifest.as_object().ok_or_else(invalid)?;
        let mut result = Vec::new();
        for (key, name) in manifest {
            if key.is_empty() || !key.chars().all(|c| c.is_numeric()) {
                return Err(invalid());
            }
            result.push((key.clone(), name.as_str().ok_or_else(invalid)?.to_owned(), None, None));
        }
        return Ok(result);
    }
    let mut result = Vec::new();
    let outer = protobuf(data)?;
    for (index, raw) in outer.get(&1).cloned().unwrap_or_default().iter().enumerate() {
        let Wire::Bytes(raw) = raw else { return Err(value_error("Malformed Anki media entry")) };
        let entry = protobuf(raw)?;
        let name = match last(&entry, 1) {
            None => String::new(),
            Some(Wire::Bytes(bytes)) => String::from_utf8(bytes.clone()).map_err(|_| value_error("'utf-8' codec can't decode bytes"))?,
            Some(Wire::Int(_)) => return Err(value_error("Malformed Anki media filename")),
        };
        let size = match last(&entry, 2) { None => Some(0), Some(Wire::Int(size)) => Some(*size), _ => None };
        let checksum = match last(&entry, 3) { Some(Wire::Bytes(sum)) if sum.len() == 20 => Some(sum.clone()), _ => None };
        let (Some(size), Some(checksum)) = (size, checksum) else { return Err(value_error("Invalid Anki media size or SHA-1 checksum")) };
        let member = match last(&entry, 255) { Some(Wire::Int(n)) => n.to_string(), _ => index.to_string() };
        result.push((member, name, Some(size), Some(checksum)));
    }
    Ok(result)
}

fn timestamp(seconds: f64) -> i64 {
    (seconds * 1_000_000.0).round_ties_even() as i64
}

/// `_validate_import_state`: an imported card's schedule, normalized to UTC.
fn validate_import_state(values: &mut Map<String, Value>) -> R<()> {
    let state = values["state"].as_str().unwrap_or("").to_owned();
    if !matches!(state.as_str(), "new" | "learning" | "review" | "relearning") {
        return Err(value_error("Imported card has an unknown scheduling state"));
    }
    for key in ["due", "buried_until"] {
        if key == "buried_until" && values[key].is_null() {
            continue;
        }
        let Some(raw) = values[key].as_str() else { return Err(value_error("fromisoformat: argument must be str")) };
        let parsed = pytime::parse(raw).ok_or_else(|| value_error(format!("Invalid isoformat string: '{raw}'")))?;
        let Some(moment) = parsed.utc() else { return Err(value_error("Imported schedule timestamps must include a timezone")) };
        values.insert(key.into(), json!(pytime::iso(moment)));
    }
    for key in ["suspended", "leech"] {
        let ok = match &values[key] { Value::Bool(_) => true, Value::Number(n) => n.as_f64().is_some_and(|f| f == 0.0 || f == 1.0), _ => false };
        if !ok {
            return Err(value_error("Imported flags must be 0 or 1"));
        }
    }
    for key in ["lapses", "reviews"] {
        if !values[key].as_i64().filter(|_| values[key].is_i64()).is_some_and(|n| (0..=100_000_000).contains(&n)) {
            return Err(value_error("Imported review counters are invalid"));
        }
    }
    if super::truthy(values.get("fsrs")) {
        let raw = values["fsrs"].as_str().ok_or(Error::Internal)?;
        let mut card = Card::from_json(raw).map_err(Error::Value)?;
        let expected = match state.as_str() { "learning" => Some(State::Learning), "relearning" => Some(State::Relearning), "review" => Some(State::Review), _ => None };
        if expected != Some(card.state) {
            return Err(value_error("Imported FSRS state disagrees with card state"));
        }
        if card.due.offset.is_none() || card.last_review.is_some_and(|last| last.offset.is_none()) {
            return Err(value_error("Imported FSRS timestamps must include a timezone"));
        }
        if card.stability.is_some_and(|s| !(s > 0.0 && s <= 1_000_000.0)) || card.difficulty.is_some_and(|d| !(1.0..=10.0).contains(&d)) {
            return Err(value_error("Imported FSRS stability/difficulty are invalid"));
        }
        if card.state == State::Review && (card.stability.is_none() || card.difficulty.is_none()) {
            return Err(value_error("A reviewed FSRS card needs stability and difficulty"));
        }
        if card.state != State::Review && !card.step.as_i64().filter(|_| card.step.is_i64()).is_some_and(|s| (0..=10).contains(&s)) {
            return Err(value_error("Imported learning step is invalid"));
        }
        let due = card.due.utc().expect("aware");
        if values["due"].as_str() != Some(pytime::iso(due).as_str()) {
            return Err(value_error("Imported FSRS due timestamp disagrees with card due"));
        }
        card.due = pytime::Parsed { wall: due, offset: Some(0) };
        if let Some(last) = card.last_review {
            card.last_review = Some(pytime::Parsed { wall: last.utc().expect("aware"), offset: Some(0) });
        }
        values.insert("fsrs".into(), json!(card.to_json()));
    } else if state != "new" {
        return Err(value_error("Scheduled cards need FSRS state"));
    }
    Ok(())
}

/// A private scratch folder, removed when dropped.
struct Scratch(PathBuf);

impl Scratch {
    fn new(prefix: &str) -> R<Self> {
        use std::os::unix::fs::DirBuilderExt;
        let path = std::env::temp_dir().join(format!("{prefix}{}", token_hex(8)));
        std::fs::DirBuilder::new().mode(0o700).create(&path).map_err(|_| Error::Internal)?;
        Ok(Scratch(path))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn zip_error(error: zip::result::ZipError) -> Error {
    value_error(error.to_string())
}

fn read_member(archive: &mut zip::ZipArchive<Cursor<&[u8]>>, name: &str) -> R<Vec<u8>> {
    let mut file = archive.by_name(name).map_err(zip_error)?;
    let mut data = Vec::new();
    (&mut file).take(MAX_ARCHIVE as u64 + 1).read_to_end(&mut data).map_err(|error| value_error(error.to_string()))?;
    Ok(data)
}

/// `import_apkg`.
pub fn import_apkg(conn: &Connection, payload: &[u8]) -> R<Value> {
    if payload.len() > MAX_ARCHIVE {
        return Err(value_error("Archive exceeds 128 MiB"));
    }
    let mut archive = zip::ZipArchive::new(Cursor::new(payload)).map_err(zip_error)?;
    let mut declared: u64 = 0;
    let mut names = BTreeSet::new();
    for index in 0..archive.len() {
        let file = archive.by_index_raw(index).map_err(zip_error)?;
        declared += file.size();
        names.insert(file.name().to_owned());
    }
    if declared > MAX_ARCHIVE as u64 {
        return Err(value_error("Expanded archive exceeds 128 MiB"));
    }
    let collection = ["collection.anki21b", "collection.anki21", "collection.anki2"].into_iter().find(|name| names.contains(*name))
        .ok_or_else(|| value_error("Archive has no Anki collection"))?;
    let db_bytes = decompress(read_member(&mut archive, collection)?)?;
    if !db_bytes.starts_with(b"SQLite format 3\x00") {
        return Err(value_error("Anki collection is not SQLite"));
    }
    let scratch = Scratch::new("kitty-anki-")?;
    let database = scratch.0.join("collection.sqlite");
    std::fs::write(&database, &db_bytes).map_err(|_| Error::Internal)?;
    let source = Connection::open_with_flags(&database, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    source.execute_batch("PRAGMA trusted_schema=OFF")?;
    let tables: BTreeSet<String> = source.prepare("SELECT name FROM sqlite_master WHERE type='table'")?
        .query_map([], |row| row.get(0))?.collect::<Result<_, _>>()?;
    if !["col", "notes", "cards", "revlog"].iter().all(|t| tables.contains(*t)) {
        return Err(value_error("Anki collection is missing required tables"));
    }
    let col = source_rows(&source, "SELECT * FROM col", &[])?.into_iter().next().ok_or_else(|| value_error("Anki collection metadata is missing"))?;
    let (models, decks) = models_decks(&source, &col, &tables)?;
    let note_rows = source_rows(&source, "SELECT * FROM notes LIMIT 10001", &[])?;
    let card_rows = source_rows(&source, "SELECT * FROM cards LIMIT 100001", &[])?;
    if note_rows.len() > 10000 || card_rows.len() > 100000 {
        return Err(value_error("Archive exceeds 10000 notes or 100000 cards"));
    }
    let mut last_reviews: HashMap<i64, f64> = HashMap::new();
    for row in source_rows(&source, "SELECT cid,MAX(id) AS reviewed FROM revlog GROUP BY cid", &[])? {
        if let (Ok(cid), Ok(reviewed)) = (int_of(&row, "cid"), int_of(&row, "reviewed")) {
            last_reviews.insert(cid, reviewed as f64 / 1000.0);
        }
    }
    let mut custom: HashMap<i64, Map<String, Value>> = HashMap::new();
    if tables.contains("kitty_card_state") {
        for row in source_rows(&source, "SELECT * FROM kitty_card_state", &[])? {
            let state: Value = serde_json::from_str(&text_of(&row, "state")?).map_err(|_| value_error("Invalid rika! scheduling metadata"))?;
            let Value::Object(state) = state else { return Err(value_error("Invalid rika! scheduling metadata")) };
            custom.insert(int_of(&row, "id")?, state);
        }
    }
    let mut metadata: HashMap<String, Value> = HashMap::new();
    if tables.contains("kitty_metadata") {
        for row in source_rows(&source, "SELECT * FROM kitty_metadata", &[])? {
            let value: Value = serde_json::from_str(&text_of(&row, "value")?).map_err(|_| value_error("Invalid rika! deck/note metadata"))?;
            metadata.insert(text_of(&row, "key")?, value);
        }
        for key in ["decks", "notes"] {
            let ok = match metadata.get(key) { None => true, Some(Value::Object(values)) => values.values().all(Value::is_object), _ => false };
            if !ok {
                return Err(value_error("Invalid rika! deck/note metadata"));
            }
        }
    }
    let empty = Map::new();
    let meta_decks = metadata.get("decks").and_then(Value::as_object).unwrap_or(&empty).clone();
    let meta_notes = metadata.get("notes").and_then(Value::as_object).unwrap_or(&empty).clone();
    transaction(conn, || {
        let modern = collection == "collection.anki21b";
        let media = if names.contains("media") { media_entries(&decompress(read_member(&mut archive, "media")?)?, modern)? } else { Vec::new() };
        let mut skipped = Vec::new();
        let mut expanded = db_bytes.len();
        for (member, filename, size, checksum) in media {
            if !names.contains(&member) {
                return Err(value_error("Missing archive media member"));
            }
            let data = decompress(read_member(&mut archive, &member)?)?;
            expanded += data.len();
            if expanded > MAX_ARCHIVE {
                return Err(value_error("Expanded archive exceeds 128 MiB"));
            }
            if let (Some(size), Some(checksum)) = (size, checksum) {
                if data.len() as u128 != size || Sha1::digest(&data).as_slice() != checksum.as_slice() {
                    return Err(value_error("Anki media size or SHA-1 checksum does not match"));
                }
            }
            match store_media(conn, Some(&json!(filename)), &data) {
                Ok(_) => {}
                // Executable media is deliberately never copied into the workspace.
                Err(Error::Value(message)) if message.contains("HTML/SVG") => skipped.push(filename),
                Err(error) => return Err(error),
            }
        }
        let mut deck_map: HashMap<i64, i64> = HashMap::new();
        let mut sorted_decks = decks.clone();
        sorted_decks.sort_by(|a, b| a.1.cmp(&b.1));
        for (key, name) in &sorted_decks {
            let parent = store::ensure_ancestors(conn, name, true)?.ok_or(Error::Internal)?;
            let key_number: i64 = key.parse().map_err(|_| value_error(format!("invalid literal for int() with base 10: '{key}'")))?;
            deck_map.insert(key_number, parent);
            if let Some(imported) = meta_decks.get(key).filter(|v| super::truthy(Some(v))) {
                let has_notes = super::scalar(conn, "SELECT 1 FROM flash_notes WHERE deck_id=? LIMIT 1", &[parent.into()])?.is_some();
                if !has_notes {
                    let config = settings(Some(imported.get("settings").ok_or_else(|| value_error("'settings'"))?))?;
                    conn.execute("UPDATE flash_decks SET settings=?,archived=?,revision=revision+1 WHERE id=?",
                                 params![pyjson::compact(&Value::Object(config)), i64::from(super::truthy(imported.get("archived"))), parent])?;
                }
            }
        }
        let mut type_map: HashMap<i64, (i64, Vec<String>)> = HashMap::new();
        for (key, model) in &models {
            let mut fields = model.fields.clone();
            fields.sort_by_key(|(ord, _)| *ord);
            let fields: Vec<String> = fields.into_iter().map(|(_, name)| name).collect();
            let mut templates = model.templates.clone();
            templates.sort_by_key(|(ord, ..)| *ord);
            let templates: Vec<Value> = templates.into_iter().map(|(_, name, front, back)| json!({"name": name, "front": front, "back": back})).collect();
            let kind = if model.kind == 1 { "cloze" } else { "template" };
            validate_type(&model.name, &json!(kind), Some(&json!(fields)), Some(&json!(templates)))?;
            let mut name = model.name.clone();
            if let Some(old) = super::first(conn, "SELECT * FROM flash_types WHERE name=?", &[name.clone().into()])? {
                if loads(&old["fields"])? != json!(fields) || loads(&old["templates"])? != json!(templates) {
                    name = format!("{name} (import {key})");
                }
            }
            conn.execute("INSERT OR IGNORE INTO flash_types(name,kind,fields,templates) VALUES(?,?,?,?)",
                         params![name, kind, pyjson::compact(&json!(fields)), pyjson::compact(&json!(templates))])?;
            let id: i64 = conn.query_row("SELECT id FROM flash_types WHERE name=?", params![name], |row| row.get(0))?;
            type_map.insert(key.parse().map_err(|_| Error::Internal)?, (id, fields));
        }
        let mut by_note: HashMap<i64, Vec<&SourceRow>> = HashMap::new();
        for card in &card_rows {
            by_note.entry(int_of(card, "nid")?).or_default().push(card);
        }
        let (mut imported, mut duplicate) = (0, 0);
        let epoch = timestamp(int_of(&col, "crt")? as f64);
        let now = clock::now();
        for original in &note_rows {
            let guid = text_of(original, "guid")?;
            if super::scalar(conn, "SELECT 1 FROM flash_notes WHERE guid=?", &[guid.clone().into()])?.is_some() {
                duplicate += 1;
                continue;
            }
            let Some(cards) = by_note.get(&int_of(original, "id")?) else { continue };
            let card_decks: BTreeSet<i64> = cards.iter().map(|c| int_of(c, "did")).collect::<R<_>>()?;
            if card_decks.len() > 1 {
                return Err(value_error("Cards of one note are split across decks; reunite them before import"));
            }
            let mid = int_of(original, "mid")?;
            let (type_id, field_names) = type_map.get(&mid).cloned().ok_or_else(|| value_error(mid.to_string()))?;
            let values: Vec<String> = text_of(original, "flds")?.split('\x1f').map(str::to_owned).collect();
            if values.len() != field_names.len() {
                return Err(value_error("Anki note field count does not match model"));
            }
            let did = int_of(cards[0], "did")?;
            let deck = *deck_map.get(&did).ok_or_else(|| value_error(did.to_string()))?;
            let archived = meta_notes.get(&guid).map(|n| super::truthy(n.get("archived"))).unwrap_or(false);
            let fields: Map<String, Value> = field_names.iter().cloned().zip(values.into_iter().map(Value::String)).collect();
            let tags: Vec<String> = text_of(original, "tags")?.split_whitespace().map(str::to_owned).collect();
            let body = json!({"guid": guid, "deck_id": deck, "type_id": type_id, "fields": fields, "tags": tags, "archived": archived});
            let note = store::save_note_inner(conn, body.as_object().expect("an object"))?;
            let note_id = note["id"].as_i64().ok_or(Error::Internal)?;
            let ordinals: BTreeSet<i64> = conn.prepare("SELECT ordinal FROM flash_cards WHERE note_id=?")?
                .query_map(params![note_id], |row| row.get(0))?.collect::<Result<_, _>>()?;
            let wanted: BTreeSet<i64> = cards.iter().map(|c| int_of(c, "ord")).collect::<R<_>>()?;
            if wanted != ordinals {
                return Err(value_error("Anki cards differ from generated templates; remove empty/unsupported cards before import"));
            }
            let config = Config::of(&require(conn, "flash_decks", &note["deck_id"])?)?;
            for card in cards {
                let state = match int_of(card, "type")? {
                    0 => "new",
                    1 => "learning",
                    2 => "review",
                    3 => "relearning",
                    _ => return Err(value_error("Unknown Anki scheduling state")),
                };
                let due_number = int_of(card, "due")?;
                let ivl = int_of(card, "ivl")?;
                let due = if state == "new" {
                    now
                } else if matches!(state, "learning" | "relearning") && due_number > 1_000_000_000 {
                    timestamp(due_number as f64)
                } else {
                    epoch + due_number.max(0) * pytime::MICROS_PER_DAY
                };
                if !pytime::in_range(due) {
                    return Err(value_error("date value out of range"));
                }
                let ord = int_of(card, "ord")?;
                let ident: i64 = conn.query_row("SELECT id FROM flash_cards WHERE note_id=? AND ordinal=?", params![note_id, ord], |row| row.get(0))?;
                let mut fsrs_json = Value::Null;
                if state != "new" {
                    let data = match card.get("data") { Some(Sql::Text(text)) if !text.is_empty() => text.clone(), _ => "{}".into() };
                    let data = match serde_json::from_str::<Value>(&data) { Ok(Value::Object(map)) => map, _ => Map::new() };
                    let (mut stability, mut difficulty) = (data.get("s").filter(|v| !v.is_null()).and_then(Value::as_f64),
                                                           data.get("d").filter(|v| !v.is_null()).and_then(Value::as_f64));
                    if stability.is_none() || difficulty.is_none() {
                        stability = Some((ivl as f64).max(0.1));
                        difficulty = Some(5.0);
                    }
                    let last_review = match data.get("lrt").and_then(Value::as_f64).or_else(|| last_reviews.get(&int_of(card, "id").unwrap_or(0)).copied()) {
                        Some(seconds) => timestamp(seconds),
                        None => due - ivl.max(1) * pytime::MICROS_PER_DAY,
                    };
                    let steps = config.steps(if state == "relearning" { "relearning_steps" } else { "learning_steps" })?;
                    let left = int_of(card, "left")?;
                    let step = if state != "review" {
                        json!((steps.len() as i64 - 1).max(0).min((steps.len() as i64 - left.rem_euclid(1000)).max(0)))
                    } else {
                        Value::Null
                    };
                    let kind = match state { "learning" => State::Learning, "review" => State::Review, _ => State::Relearning };
                    fsrs_json = json!(Card {
                        card_id: ident, state: kind, step, stability, difficulty,
                        due: pytime::Parsed { wall: due, offset: Some(0) },
                        last_review: Some(pytime::Parsed { wall: last_review, offset: Some(0) }),
                    }.to_json());
                }
                let queue = int_of(card, "queue")?;
                let mut values = Map::new();
                values.insert("state".into(), json!(state));
                values.insert("due".into(), json!(pytime::iso(due)));
                values.insert("fsrs".into(), fsrs_json);
                values.insert("suspended".into(), json!(i64::from(queue == -1)));
                values.insert("buried_until".into(), if matches!(queue, -2 | -3) { json!(pytime::iso(now + pytime::MICROS_PER_DAY)) } else { Value::Null });
                values.insert("lapses".into(), json!(int_of(card, "lapses")?));
                values.insert("reviews".into(), json!(int_of(card, "reps")?));
                values.insert("leech".into(), json!(0));
                if let Some(state) = custom.get(&int_of(card, "id")?) {
                    for (key, value) in state {
                        if values.contains_key(key) {
                            values.insert(key.clone(), value.clone());
                        }
                    }
                    if super::truthy(values.get("fsrs")) {
                        let raw = values["fsrs"].as_str().ok_or(Error::Internal)?;
                        let mut parsed: Value = serde_json::from_str(raw).map_err(|error| value_error(error.to_string()))?;
                        parsed.as_object_mut().ok_or(Error::Internal)?.insert("card_id".into(), json!(ident));
                        values.insert("fsrs".into(), json!(pyjson::compact(&parsed)));
                    }
                }
                validate_import_state(&mut values)?;
                let columns: Vec<&String> = values.keys().collect();
                let mut args: Vec<Sql> = values.values().map(super::sql).collect::<R<_>>()?;
                args.push(Sql::Integer(ident));
                conn.execute(&format!("UPDATE flash_cards SET {} WHERE id=?", columns.iter().map(|c| format!("{c}=?")).collect::<Vec<_>>().join(",")),
                             rusqlite::params_from_iter(args))?;
            }
            imported += 1;
        }
        Ok(json!({
            "imported": imported, "duplicates": duplicate, "skipped_unsafe_media": skipped,
            "warning": "Anki FSRS memory is preserved when present; older schedules use interval-based stability estimates. rika! exports retain exact FSRS state.",
        }))
    })
}

/// `_anki_requirements`: legacy clients' per-template field requirements.
fn requirements(fields: &[String], templates: &[Value]) -> R<Vec<Value>> {
    let mut result = Vec::new();
    for (ordinal, template) in templates.iter().enumerate() {
        let front = template.get("front").and_then(Value::as_str).unwrap_or("");
        let question = |present: &BTreeSet<&str>| -> R<String> {
            let values: Map<String, Value> = fields.iter().map(|n| (n.clone(), json!(if present.contains(n.as_str()) { "1" } else { "" }))).collect();
            let source = render::conditional(front, &values)?;
            Ok(TOKEN.replace_all(&source, |found: &regex::Captures| {
                let key = found[1].rsplit(':').next().unwrap_or("");
                values.get(key).and_then(Value::as_str).unwrap_or("").to_owned()
            }).into_owned())
        };
        let empty = question(&BTreeSet::new())?;
        let all: BTreeSet<&str> = fields.iter().map(String::as_str).collect();
        if question(&all)? == empty {
            result.push(json!([ordinal, "none", []]));
            continue;
        }
        let mut any = Vec::new();
        let mut required = Vec::new();
        for (index, name) in fields.iter().enumerate() {
            if question(&BTreeSet::from([name.as_str()]))? != empty {
                any.push(index);
            }
            let mut without = all.clone();
            without.remove(name.as_str());
            if question(&without)? == empty {
                required.push(index);
            }
        }
        result.push(if required.is_empty() { json!([ordinal, "any", any]) } else { json!([ordinal, "all", required]) });
    }
    Ok(result)
}

/// `export_apkg`: a legacy (schema 11) package any Anki opens, plus rika!'s exact state.
pub fn export_apkg(conn: &Connection, deck: Option<i64>) -> R<Vec<u8>> {
    let notes = browser_notes(conn, deck)?;
    let all_decks = rows(conn, "SELECT * FROM flash_decks", &[])?;
    let selected_name = match deck { Some(id) => Some(require(conn, "flash_decks", &Value::from(id))?["name"].as_str().unwrap_or("").to_owned()), None => None };
    let selected: BTreeSet<i64> = all_decks.iter().filter(|d| {
        let name = d["name"].as_str().unwrap_or("");
        match &selected_name {
            None => true,
            Some(chosen) => name == chosen || name.starts_with(&format!("{chosen}::")) || chosen.starts_with(&format!("{name}::")),
        }
    }).filter_map(|d| d["id"].as_i64()).collect();
    let mut kinds: BTreeSet<i64> = notes.iter().filter_map(|n| n["type_id"].as_i64()).collect();
    if kinds.is_empty() {
        kinds = conn.prepare("SELECT id FROM flash_types")?.query_map([], |row| row.get(0))?.collect::<Result<_, _>>()?;
    }
    let moment = clock::now();
    let stamp = moment.div_euclid(pytime::MICROS_PER_SECOND);
    let first_deck = selected.iter().next().copied().unwrap_or(1);
    let mut decks = Map::new();
    for id in &selected {
        let row = require(conn, "flash_decks", &Value::from(*id))?;
        decks.insert(id.to_string(), json!({"id": id, "name": row["name"], "mod": stamp, "usn": -1, "desc": "", "dyn": 0, "conf": id,
            "extendNew": 0, "extendRev": 0, "collapsed": false, "newToday": [0, 0], "revToday": [0, 0], "lrnToday": [0, 0], "timeToday": [0, 0]}));
    }
    let mut models = Map::new();
    for id in &kinds {
        let row = require(conn, "flash_types", &Value::from(*id))?;
        let fields = names_of(&row)?;
        let templates = loads(&row["templates"])?.as_array().cloned().unwrap_or_default();
        models.insert(id.to_string(), json!({
            "id": id, "name": row["name"], "type": i64::from(row["kind"] == "cloze"), "mod": stamp, "usn": -1, "sortf": 0, "did": first_deck,
            "css": ".card {font-family: sans-serif; font-size: 20px;}", "latexPre": "", "latexPost": "", "req": requirements(&fields, &templates)?,
            "flds": fields.iter().enumerate().map(|(i, name)| json!({"name": name, "ord": i, "sticky": false, "rtl": false, "font": "Arial", "size": 20, "media": []})).collect::<Vec<_>>(),
            "tmpls": templates.iter().enumerate().map(|(i, t)| json!({"name": t["name"], "ord": i, "qfmt": t["front"], "afmt": t["back"], "bqfmt": "", "bafmt": "", "did": null})).collect::<Vec<_>>(),
            "tags": [], "vers": [],
        }));
    }
    let scratch = Scratch::new("kitty-export-")?;
    let path = scratch.0.join("collection.anki2");
    {
        let database = Connection::open(&path)?;
        database.execute_batch("CREATE TABLE col(id INTEGER PRIMARY KEY,crt INTEGER,mod INTEGER,scm INTEGER,ver INTEGER,dty INTEGER,usn INTEGER,ls INTEGER,conf TEXT,models TEXT,decks TEXT,dconf TEXT,tags TEXT); CREATE TABLE notes(id INTEGER PRIMARY KEY,guid TEXT,mid INTEGER,mod INTEGER,usn INTEGER,tags TEXT,flds TEXT,sfld TEXT,csum INTEGER,flags INTEGER,data TEXT); CREATE TABLE cards(id INTEGER PRIMARY KEY,nid INTEGER,did INTEGER,ord INTEGER,mod INTEGER,usn INTEGER,type INTEGER,queue INTEGER,due INTEGER,ivl INTEGER,factor INTEGER,reps INTEGER,lapses INTEGER,left INTEGER,odue INTEGER,odid INTEGER,flags INTEGER,data TEXT); CREATE TABLE revlog(id INTEGER PRIMARY KEY,cid INTEGER,usn INTEGER,ease INTEGER,ivl INTEGER,lastIvl INTEGER,factor INTEGER,time INTEGER,type INTEGER); CREATE TABLE graves(usn INTEGER,oid INTEGER,type INTEGER); CREATE TABLE kitty_card_state(id INTEGER PRIMARY KEY,state TEXT);")?;
        database.execute_batch("CREATE TABLE kitty_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")?;
        database.execute_batch("BEGIN")?;
        let mut meta_decks = Map::new();
        for d in all_decks.iter().filter(|d| d["id"].as_i64().is_some_and(|id| selected.contains(&id))) {
            meta_decks.insert(d["id"].to_string(), json!({"settings": loads(&d["settings"])?, "archived": d["archived"]}));
        }
        let meta_notes: Map<String, Value> = notes.iter().map(|n| (n["guid"].as_str().unwrap_or("").to_owned(), json!({"archived": n["archived"]}))).collect();
        for (key, value) in [("decks", Value::Object(meta_decks)), ("notes", Value::Object(meta_notes))] {
            database.execute("INSERT INTO kitty_metadata VALUES(?,?)", params![key, pyjson::compact(&value)])?;
        }
        let epoch = pytime::whole_days(moment) * pytime::MICROS_PER_DAY;
        let mut dconf = Map::new();
        for d in &all_decks {
            let Some(id) = d["id"].as_i64().filter(|id| selected.contains(id)) else { continue };
            let config = Config::of(d)?;
            let minutes = |key: &str| -> R<Vec<Value>> { Ok(config.steps(key)?.into_iter().map(|s| pyjson::number(s as f64 / 60.0)).collect()) };
            dconf.insert(id.to_string(), json!({"id": id, "name": d["name"], "mod": stamp, "usn": -1, "maxTaken": 60, "autoplay": true, "timer": 0, "replayq": true,
                "new": {"delays": minutes("learning_steps")?, "ints": [1, 4], "initialFactor": 2500, "order": 1, "perDay": config.int("new_limit")?, "bury": config.flag("bury_siblings")?},
                "rev": {"perDay": config.int("review_limit")?, "ease4": 1.3, "fuzz": 0.05, "ivlFct": 1, "maxIvl": config.int("maximum_interval")?, "bury": config.flag("bury_siblings")?, "minSpace": 1},
                "lapse": {"delays": minutes("relearning_steps")?, "mult": 0, "minInt": 1, "leechFails": config.int("leech_threshold")?, "leechAction": 0}}));
        }
        database.execute("INSERT INTO col VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", params![
            1, epoch / pytime::MICROS_PER_SECOND, stamp * 1000, stamp * 1000, 11, 0, -1, 0,
            pyjson::compact(&json!({"nextPos": notes.len() + 1, "activeDecks": selected.iter().collect::<Vec<_>>(), "curDeck": first_deck})),
            pyjson::compact(&Value::Object(models)), pyjson::compact(&Value::Object(decks)), pyjson::compact(&Value::Object(dconf)), "{}"])?;
        let now = pytime::iso(moment);
        for note in &notes {
            let names = names_of(&require(conn, "flash_types", &note["type_id"])?)?;
            let fields: Vec<String> = names.iter().map(|n| note["fields"].get(n).and_then(Value::as_str).unwrap_or("").to_owned()).collect();
            let digest = hex::encode(Sha1::digest(fields[0].as_bytes()));
            let checksum = i64::from_str_radix(&digest[..8], 16).unwrap_or(0);
            let tags: Vec<&str> = note["tags"].as_array().map(|t| t.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
            database.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?)", params![
                note["id"].as_i64(), note["guid"].as_str(), note["type_id"].as_i64(), stamp, -1, format!(" {} ", tags.join(" ")),
                fields.join("\x1f"), fields[0], checksum, 0, ""])?;
            for entry in note["cards"].as_array().cloned().unwrap_or_default() {
                let card = require(conn, "flash_cards", &entry["id"])?;
                let due_at = pytime::parse(card["due"].as_str().unwrap_or("")).and_then(|p| p.utc()).ok_or(Error::Internal)?;
                let state = match card["state"].as_str().unwrap_or("") { "new" => 0, "learning" => 1, "review" => 2, "relearning" => 3, _ => return Err(Error::Internal) };
                let buried = card["buried_until"].as_str().is_some_and(|until| !until.is_empty() && until > now.as_str());
                let queue = if super::truthy(card.get("suspended")) { -1 } else if buried { -2 } else if matches!(state, 1 | 3) { 1 } else { state };
                let id = card["id"].as_i64().unwrap_or(0);
                let due = match state { 0 => id, 1 | 3 => due_at.div_euclid(pytime::MICROS_PER_SECOND), _ => pytime::whole_days(due_at - epoch).max(0) };
                let fsrs = match card["fsrs"].as_str().filter(|raw| !raw.is_empty()) { Some(raw) => serde_json::from_str(raw).map_err(|_| Error::Internal)?, None => json!({}) };
                let interval = (fsrs.get("stability").and_then(Value::as_f64).filter(|s| *s != 0.0).unwrap_or(1.0) as i64).max(1);
                let config = Config::of(&require(conn, "flash_decks", &note["deck_id"])?)?;
                let steps = config.steps(if state == 3 { "relearning_steps" } else { "learning_steps" })?;
                let left = if matches!(state, 1 | 3) { (steps.len() as i64 - fsrs.get("step").and_then(Value::as_i64).unwrap_or(0)).max(0) * 1001 } else { 0 };
                let mut card_data = Map::new();
                if let (Some(s), Some(d)) = (fsrs.get("stability").filter(|v| !v.is_null()), fsrs.get("difficulty").filter(|v| !v.is_null())) {
                    card_data.insert("s".into(), s.clone());
                    card_data.insert("d".into(), d.clone());
                    card_data.insert("dr".into(), config.map.get("retention").cloned().unwrap_or(Value::Null));
                    if let Some(last) = fsrs.get("last_review").and_then(Value::as_str).filter(|l| !l.is_empty()) {
                        let last = pytime::parse(last).and_then(|p| p.utc()).ok_or(Error::Internal)?;
                        card_data.insert("lrt".into(), json!(last.div_euclid(pytime::MICROS_PER_SECOND)));
                    }
                }
                database.execute("INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", params![
                    id, note["id"].as_i64(), note["deck_id"].as_i64(), card["ordinal"].as_i64(), stamp, -1, state, queue, due, interval, 2500,
                    card["reviews"].as_i64(), card["lapses"].as_i64(), left, 0, 0, 0,
                    if card_data.is_empty() { String::new() } else { pyjson::compact(&Value::Object(card_data)) }])?;
                database.execute("INSERT INTO kitty_card_state VALUES(?,?)", params![id, pyjson::compact(&Value::Object(card.clone()))])?;
            }
        }
        database.execute_batch("COMMIT")?;
    }
    let collection = std::fs::read(&path).map_err(|_| Error::Internal)?;
    let mut references = BTreeSet::new();
    for note in &notes {
        if let Some(fields) = note["fields"].as_object() {
            for value in fields.values() {
                render::media_references(&mut references, value.as_str().unwrap_or(""));
            }
        }
        for card in note["cards"].as_array().cloned().unwrap_or_default() {
            for answer in [false, true] {
                let source = render::source(conn, note["id"].as_i64().unwrap_or(0), card["ordinal"].as_i64().unwrap_or(0), answer)?;
                render::media_references(&mut references, &source);
            }
        }
    }
    let mut output = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let options = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    output.start_file("collection.anki2", options).map_err(zip_error)?;
    output.write_all(&collection).map_err(|_| Error::Internal)?;
    let mut manifest: BTreeMap<usize, String> = BTreeMap::new();
    let mut statement = conn.prepare("SELECT name,content FROM flash_media ORDER BY name")?;
    let media: Vec<(String, Vec<u8>)> = statement.query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?.collect::<Result<_, _>>()?;
    for (name, content) in media {
        if !references.contains(&name) {
            continue;
        }
        let index = manifest.len();
        manifest.insert(index, name);
        output.start_file(index.to_string(), options).map_err(zip_error)?;
        output.write_all(&content).map_err(|_| Error::Internal)?;
    }
    let manifest: Map<String, Value> = manifest.into_iter().map(|(index, name)| (index.to_string(), json!(name))).collect();
    output.start_file("media", options).map_err(zip_error)?;
    output.write_all(pyjson::compact(&Value::Object(manifest)).as_bytes()).map_err(|_| Error::Internal)?;
    Ok(output.finish().map_err(zip_error)?.into_inner())
}
