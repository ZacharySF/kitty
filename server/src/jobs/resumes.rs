//! The resume library's reads: `GET /api/resumes` (the deleted `WebApplication._applyai_get`
//! branch), `/api/resumes/workflow` and `/api/resumes/versions/<id>` (the deleted reads of
//! `web_resumes.get`), with `resume_versions.get_version`'s audit checks and
//! `web_server._pdf_bytes`' checks of a registered PDF. Registration, generation, the coach
//! (pypdf), promotion, rejection, rollback and the downloads stay on the worker.

use std::path::{Path, PathBuf};

use rusqlite::Connection;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use super::model::{self, Resume};
use super::reads::default_resume;
use super::Env;
use crate::ported::db::{exists, row, rows};
use crate::ported::{fail, internal, py, Call, Done, Fault};

/// `web_server.MAX_FILE`.
const MAX_FILE: u64 = 32 * 1024 * 1024;

/// `PurePath.suffix`: the last dotted part of the name, unless the name only starts with it.
fn suffix(path: &Path) -> String {
    let name = path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
    match name.rfind('.') {
        Some(at) if at > 0 && at < name.len() - 1 => name[at..].to_owned(),
        _ => String::new(),
    }
}

/// `filler.resume_file`: stored paths are relative to the project root.
fn resume_file(root: &Path, resume: &Resume) -> Done<PathBuf> {
    let stored = Path::new(resume.file_path.as_str().ok_or_else(internal)?);
    Ok(if stored.is_absolute() { stored.to_owned() } else { root.join(stored) })
}

/// `web_server._pdf_bytes`: the registered PDF, unchanged since registration.
pub fn pdf_bytes(root: &Path, resume: &Resume) -> Done<Vec<u8>> {
    let path = resume_file(root, resume)?;
    let missing = || fail(404, "Registered PDF is unavailable");
    let metadata = std::fs::metadata(&path).ok().filter(|metadata| metadata.is_file());
    if suffix(&path).to_lowercase() != ".pdf" || metadata.is_none_or(|metadata| metadata.len() > MAX_FILE) {
        return Err(missing());
    }
    let data = std::fs::read(&path).map_err(|_| missing())?;
    if !data.starts_with(b"%PDF-") || data.len() as u64 > MAX_FILE {
        return Err(missing());
    }
    if resume.file_hash.as_str() != Some(hex::encode(Sha256::digest(&data)).as_str()) {
        return Err(fail(409, "Registered PDF has changed; review it with the CLI"));
    }
    Ok(data)
}

/// `web_server._pdf_available`.
fn available(root: &Path, resume: &Resume) -> Done<bool> {
    match pdf_bytes(root, resume) {
        Ok(_) => Ok(true),
        Err(fault) if fault.status == 404 || fault.status == 409 => Ok(false),
        Err(fault) => Err(fault),
    }
}

/// Why `get_version` failed: `ValueError`/`OSError` (the audit route's 409) or anything else.
enum Broken {
    Changed,
    Fault(Fault),
}

impl From<Fault> for Broken {
    fn from(fault: Fault) -> Self {
        Self::Fault(fault)
    }
}

/// `resumes.file_hash`.
fn file_hash(path: &Path) -> Result<String, Broken> {
    std::fs::read(path).map(|data| hex::encode(Sha256::digest(&data))).map_err(|_| Broken::Changed)
}

fn is_file(path: &Path) -> bool {
    std::fs::metadata(path).is_ok_and(|metadata| metadata.is_file())
}

/// `[*values]` of a JSON value: a list's items, a string's characters, a mapping's keys.
fn unpack(value: &Value) -> Done<Vec<Value>> {
    Ok(match value {
        Value::Array(items) => items.clone(),
        Value::String(text) => text.chars().map(|c| Value::String(c.to_string())).collect(),
        Value::Object(map) => map.keys().map(|key| Value::String(key.clone())).collect(),
        _ => return Err(internal()),
    })
}

/// `resume_versions.get_version`: the manifest with the row's status and resume, the artifacts
/// checked again, the track's decision history and whether it is the track's default.
fn get_version(conn: &Connection, version_id: &str) -> Result<Map<String, Value>, Broken> {
    // `_load`.
    let found = row(conn, "SELECT * FROM resume_versions WHERE id=?", [version_id])?.ok_or(Broken::Changed)?;
    let get = |key: &str| found.get(key).ok_or_else(internal);
    let manifest_path = PathBuf::from(get("manifest_path")?.as_str().ok_or_else(internal)?);
    if !is_file(&manifest_path) || Value::String(file_hash(&manifest_path)?) != *get("manifest_hash")? {
        return Err(Broken::Changed);
    }
    let raw = std::fs::read(&manifest_path).map_err(|_| Broken::Changed)?;
    let text = String::from_utf8(raw).map_err(|_| Broken::Changed)?;
    let manifest: Value = serde_json::from_str(&text).map_err(|_| Broken::Changed)?;
    let manifest = manifest.as_object().ok_or_else(internal)?;
    let mut result = manifest.clone();
    result.insert("status".into(), get("status")?.clone());
    result.insert("resume_id".into(), get("resume_id")?.clone());
    // `_integrity`.
    let folder = PathBuf::from(manifest.get("manifest_path").ok_or_else(internal)?.as_str().ok_or_else(internal)?);
    let folder = folder.parent().map(Path::to_owned).unwrap_or_default();
    let mut errors = Vec::new();
    for (name, digest) in manifest.get("artifacts").ok_or_else(internal)?.as_object().ok_or_else(internal)? {
        let path = folder.join(name);
        if !is_file(&path) || Value::String(file_hash(&path)?) != *digest {
            errors.push(Value::String(format!("Immutable artifact missing or changed: {name}")));
        }
    }
    let validation = manifest.get("validation").ok_or_else(internal)?.as_object().ok_or_else(internal)?;
    let mut checked = validation.clone();
    let valid = py::truthy(validation.get("valid").ok_or_else(internal)?) && errors.is_empty();
    checked.insert("valid".into(), Value::Bool(valid));
    let mut all = match validation.get("errors") {
        Some(value) => unpack(value)?,
        None => Vec::new(),
    };
    all.extend(errors);
    checked.insert("errors".into(), Value::Array(all));
    result.insert("validation".into(), Value::Object(checked));
    let track = get("track")?.clone();
    let track_bound = crate::ported::db::bind(&track)?;
    result.insert("history".into(), Value::Array(
        rows(conn, "SELECT * FROM resume_version_history WHERE track=? ORDER BY id", [&track_bound])?.into_iter().map(Value::Object).collect()));
    let resume_id = get("resume_id")?;
    let is_default = !resume_id.is_null() && exists(conn, "SELECT 1 FROM resume_default_overrides WHERE track=? AND resume_id=?",
                                                     [track_bound.clone(), crate::ported::db::bind(resume_id)?])?;
    result.insert("is_default".into(), Value::Bool(is_default));
    Ok(result)
}

/// `next(iter(value), None)` of a resume's stored default tracks.
fn first(value: &Value) -> Done<Value> {
    Ok(match value {
        Value::Array(items) => items.first().cloned().unwrap_or(Value::Null),
        Value::Object(map) => map.keys().next().map(|key| Value::String(key.clone())).unwrap_or(Value::Null),
        Value::String(text) => text.chars().next().map(|c| Value::String(c.to_string())).unwrap_or(Value::Null),
        _ => return Err(internal()),
    })
}

/// `GET /api/resumes`.
fn library(env: &Env, call: &Call) -> Done<Value> {
    call.no_query()?;
    let (conn, config) = (env.conn, env.config()?);
    let mut defaults: Vec<(i64, Vec<String>)> = Vec::new();
    for track in &config.tracks {
        if let Some(selected) = default_resume(conn, config, &track.name)? {
            match defaults.iter_mut().find(|(id, _)| *id == selected.id) {
                Some((_, names)) => names.push(track.name.clone()),
                None => defaults.push((selected.id, vec![track.name.clone()])),
            }
        }
    }
    let mut resumes = Vec::new();
    for resume in model::list_resumes(conn)? {
        let chosen = defaults.iter().find(|(id, _)| *id == resume.id);
        let track = match chosen {
            Some((_, names)) => names.first().map(|name| Value::String(name.clone())).unwrap_or(Value::Null),
            None => first(&resume.default_tracks)?,
        };
        resumes.push(json!({"id": resume.id, "label": resume.label, "track": track, "available": available(env.root, &resume)?,
                            "is_default": chosen.is_some()}));
    }
    let mut versions = Vec::new();
    for found in rows(conn, "SELECT id FROM resume_versions ORDER BY created_at DESC, rowid DESC", [])? {
        let id = found.get("id").and_then(Value::as_str).ok_or_else(internal)?;
        let version = get_version(conn, id).map_err(|broken| match broken {
            Broken::Changed => internal(),
            Broken::Fault(fault) => fault,
        })?;
        let mut item = Map::new();
        for key in ["id", "track", "status", "is_default", "created_at"] {
            item.insert(key.into(), version.get(key).cloned().ok_or_else(internal)?);
        }
        versions.push(Value::Object(item));
    }
    Ok(json!({"resumes": resumes, "versions": versions}))
}

/// `_keys(query, ())`.
fn no_fields(call: &Call) -> Done<()> {
    if call.query.is_empty() { Ok(()) } else { Err(fail(400, "Unknown resume request fields")) }
}

/// `GET /api/resumes/workflow`.
fn workflow(env: &Env, call: &Call) -> Done<Value> {
    no_fields(call)?;
    let (conn, config) = (env.conn, env.config()?);
    let mut defaults: Vec<(String, Option<Resume>)> = Vec::new();
    for name in config.track_names() {
        let selected = default_resume(conn, config, name)?;
        match defaults.iter_mut().find(|(known, _)| known == name) {
            Some(entry) => entry.1 = selected,
            None => defaults.push((name.to_owned(), selected)),
        }
    }
    let mut resumes = Vec::new();
    for item in model::list_resumes(conn)? {
        let tracks: Vec<&str> = defaults.iter().filter(|(_, selected)| selected.as_ref().is_some_and(|selected| selected.id == item.id))
            .map(|(name, _)| name.as_str()).collect();
        resumes.push(json!({"id": item.id, "label": item.label, "description": item.description,
                            "available": available(env.root, &item)?, "default_tracks": tracks}));
    }
    let named: Map<String, Value> = defaults.iter().map(|(name, selected)| (name.clone(), selected.as_ref().map_or(Value::Null, |item| item.label.clone()))).collect();
    let versions: Vec<Value> = rows(conn, "SELECT id,track,status,created_at FROM resume_versions ORDER BY created_at DESC,rowid DESC", [])?
        .into_iter().map(Value::Object).collect();
    Ok(json!({"tracks": config.track_names().collect::<Vec<_>>(), "defaults": named, "resumes": resumes, "versions": versions}))
}

/// `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`.
pub fn is_version(text: &str) -> bool {
    let parts: Vec<&str> = text.split('-').collect();
    parts.len() == 5 && parts.iter().zip([8, 4, 4, 4, 12]).all(|(part, size)| part.len() == size
        && part.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
}

/// `GET /api/resumes/versions/<id>`.
fn version(env: &Env, call: &Call, version_id: &str) -> Done<Value> {
    no_fields(call)?;
    let conn = env.conn;
    if !exists(conn, "SELECT 1 FROM resume_versions WHERE id=?", [version_id])? {
        return Err(fail(404, "Resume version not found"));
    }
    match get_version(conn, version_id) {
        Ok(found) => Ok(json!({"version": found})),
        Err(Broken::Changed) => Err(fail(409, "Resume audit is unavailable or has changed; restore immutable artifacts")),
        Err(Broken::Fault(fault)) => Err(fault),
    }
}

pub fn answer(env: &Env, call: &Call) -> Done<Option<Value>> {
    Ok(Some(match call.path {
        "/api/resumes" => library(env, call)?,
        "/api/resumes/workflow" => workflow(env, call)?,
        path => match path.strip_prefix("/api/resumes/versions/").filter(|id| is_version(id)) {
            Some(id) => version(env, call, id)?,
            None => return Ok(None),
        },
    }))
}
