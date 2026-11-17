//! Agent sessions as Settings → Agents lists them (`kitty.core.agent_sessions` `describe`,
//! `listing`, `detail`, `end`, `planted_changes`, and `agent_access.journal`). Sessions are made
//! and confined by the worker's launch exchange; here they are read, held and ended.

use std::collections::BTreeSet;
use std::path::Path;

use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::ported::db::{exists, fail, internal, row, rows, Done};
use crate::ported::py;

/// `agent_sessions.PLANTED`: files an agent could plant in a writable project to run later.
const PLANTED: [&str; 7] = [".envrc", ".git/config", ".git/hooks", ".git/info", ".vscode/tasks.json", ".vscode/settings.json",
                            ".devcontainer/devcontainer.json"];
/// The fields `describe` lists, in its order.
const FIELDS: [&str; 15] = ["id", "kind", "name", "host", "pane", "cwd", "state", "guarantee", "abi", "pid", "message", "policy_version",
                            "created_at", "confined_at", "ended_at"];

fn no_such() -> crate::ported::db::Fault {
    fail(404, "No such agent session")
}

/// `json.loads(text) if text else None` of a stored column.
fn stored_json(value: Option<&Value>) -> Done<Value> {
    match value {
        Some(Value::String(text)) if !text.is_empty() => serde_json::from_str(text).map_err(|_| internal()),
        None | Some(Value::Null) | Some(Value::String(_)) => Ok(Value::Null),
        // A falsy number is no JSON; any other value is not text `json.loads` takes.
        Some(other) if !py::truthy(other) => Ok(Value::Null),
        Some(_) => Err(internal()),
    }
}

/// `agent_sessions.describe`: a session as the API lists it.
fn describe(found: &Map<String, Value>) -> Done<Map<String, Value>> {
    let mut out = Map::new();
    for field in FIELDS {
        out.insert(field.into(), found.get(field).cloned().ok_or_else(internal)?);
    }
    out.insert("report".into(), stored_json(found.get("report"))?);
    out.insert("token".into(), Value::Bool(!found.get("client_id").ok_or_else(internal)?.is_null()));
    Ok(out)
}

/// `agent_sessions.listing`: the newest hundred sessions with their journal size and hold.
pub fn listing(conn: &Connection) -> Done<Value> {
    let found = rows(conn, "SELECT s.*, (SELECT COUNT(*) FROM agent_access a WHERE a.session=s.id) AS accesses, \
                            (SELECT COUNT(*) FROM retention_holds h WHERE h.session=s.id AND h.released_at IS NULL) AS held \
                            FROM agent_sessions s ORDER BY s.created_at DESC, s.id LIMIT ?", [100])?;
    let mut out = Vec::with_capacity(found.len());
    for item in &found {
        let mut listed = describe(item)?;
        listed.insert("accesses".into(), item["accesses"].clone());
        listed.insert("held".into(), Value::Bool(py::truthy(&item["held"])));
        out.push(Value::Object(listed));
    }
    Ok(Value::Array(out))
}

/// `agent_sessions.detail`: one session, a page of its journal (newest first), its hold and
/// the planted-file candidates changed since its launch.
pub fn detail(conn: &Connection, session: &str, before: Option<i64>) -> Done<Value> {
    const LIMIT: i64 = 200;
    let found = row(conn, "SELECT * FROM agent_sessions WHERE id=?", [session])?.ok_or_else(no_such)?;
    let mut journal = rows(conn, "SELECT id, tool, op, purpose, block_id, seq, decision, mode, policy_version, at FROM agent_access \
                                  WHERE session=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?", params![session, before, before, LIMIT + 1])?;
    let more = journal.len() as i64 > LIMIT;
    journal.truncate(LIMIT as usize);
    let next = if more { journal.last().and_then(|last| last.get("id")).cloned().unwrap_or(Value::Null) } else { Value::Null };
    let held = exists(conn, "SELECT 1 FROM retention_holds WHERE session=? AND released_at IS NULL", [session])?;
    let planted = planted_changes(&stored_json(found.get("baseline"))?)?;
    Ok(json!({"session": describe(&found)?, "journal": journal, "next": next, "held": held, "planted": planted}))
}

/// `agent_sessions.end`: the session's token is revoked; the journal stays.
pub fn end(conn: &Connection, session: &str, message: &str, stamp: &str) -> Done<()> {
    let found = row(conn, "SELECT client_id, state FROM agent_sessions WHERE id=?", [session])?.ok_or_else(no_such)?;
    if py::truthy(&found["client_id"]) {
        let client = crate::ported::db::bind(&found["client_id"])?;
        conn.execute("UPDATE clients SET revoked_at=? WHERE id=? AND revoked_at IS NULL", params![stamp, client])?;
    }
    if matches!(found["state"].as_str(), Some("pending" | "confined" | "unconfined")) {
        conn.execute("UPDATE agent_sessions SET state='ended', ended_at=?, message=? WHERE id=?", params![stamp, message, session])?;
    }
    Ok(())
}

/// `str(Path(base) / name)`: pathlib drops empty and `.` parts and keeps a leading `//`.
fn joined(base: &str, name: &str) -> String {
    let (root, rest) = if base.starts_with("//") && !base.starts_with("///") {
        ("//", &base[2..])
    } else if base.starts_with('/') {
        ("/", base.trim_start_matches('/'))
    } else {
        ("", base)
    };
    let parts: Vec<&str> = rest.split('/').chain(name.split('/')).filter(|part| !part.is_empty() && *part != ".").collect();
    format!("{root}{}", parts.join("/"))
}

/// `agent_sessions._digest`: the file's SHA-256, or None when it cannot be read.
fn digest(path: &str) -> Value {
    match std::fs::read(path) {
        Ok(bytes) => Value::String(hex::encode(Sha256::digest(&bytes))),
        Err(_) => Value::Null,
    }
}

/// `agent_sessions.planted_baseline(directories)["files"]`: the candidates under each
/// writable project directory, hashed (a folder's first 200 files by name, and the folder).
fn planted_files(directories: &[&str]) -> Done<Map<String, Value>> {
    let mut files = Map::new();
    for directory in directories {
        for name in PLANTED {
            let target = joined(directory, name);
            if Path::new(&target).is_dir() {
                let mut children = Vec::new();
                for entry in std::fs::read_dir(&target).map_err(|_| internal())? {
                    let entry = entry.map_err(|_| internal())?;
                    children.push(entry.file_name().into_string().map_err(|_| internal())?);
                }
                children.sort_unstable();
                for child in children.iter().take(200) {
                    let path = format!("{target}/{child}");
                    if Path::new(&path).is_file() {
                        let hashed = digest(&path);
                        files.insert(path, hashed);
                    }
                }
                files.insert(format!("{target}/"), json!("dir"));
            } else {
                let hashed = digest(&target);
                files.insert(target, hashed);
            }
        }
    }
    Ok(files)
}

/// `agent_sessions.planted_changes`: candidates created, changed or removed since the launch.
fn planted_changes(baseline: &Value) -> Done<Vec<Value>> {
    if !py::truthy(baseline) {
        return Ok(Vec::new());
    }
    let (Some(Value::Array(dirs)), Some(Value::Object(before))) = (baseline.get("dirs"), baseline.get("files")) else {
        return Err(internal());
    };
    let dirs: Vec<&str> = dirs.iter().map(|dir| dir.as_str().ok_or_else(internal)).collect::<Done<_>>()?;
    let now = planted_files(&dirs)?;
    let paths: BTreeSet<&String> = before.keys().chain(now.keys()).collect();
    let mut out = Vec::new();
    for path in paths {
        let (was, is) = (before.get(path.as_str()).unwrap_or(&Value::Null), now.get(path.as_str()).unwrap_or(&Value::Null));
        if py::eq(was, is) || path.ends_with('/') {
            continue;
        }
        let change = if was.is_null() { "created" } else if is.is_null() { "removed" } else { "changed" };
        out.push(json!({"path": path, "change": change}));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::joined;

    #[test]
    fn joins_as_pathlib_does() {
        assert_eq!(joined("/a/b/", ".envrc"), "/a/b/.envrc");
        assert_eq!(joined("/a//./b", ".git/hooks"), "/a/b/.git/hooks");
        assert_eq!(joined("//net/x", ".envrc"), "//net/x/.envrc");
        assert_eq!(joined("///x", ".envrc"), "/x/.envrc");
        assert_eq!(joined("rel/../x", ".envrc"), "rel/../x/.envrc");
        assert_eq!(joined("", ".envrc"), ".envrc");
    }
}
