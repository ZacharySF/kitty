//! `kitty.core.builds.store`: the active build file and its history on disk.
//!
//! The same file (`config/build.json`), the same exclusive `flock` on the config directory
//! around every read-modify-write, the same atomic write (temporary file, fsync, rename,
//! directory fsync, never through a symbolic link) and the same history entries in
//! `data/build-history/` as the Python store, so rika-server, the worker and the CLI can
//! share one workspace.

use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use crate::private::{self, Mode};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use super::clock::Moment;
use super::document::{self, Checked};
use super::failure::{first_error, Failure};
use super::json;
use super::value::{dump_document, dump_sorted, dump_str, scalar, text, utf8_surrogatepass, Dict, Text, Value};

pub const BUILD_FILE: &str = "build.json";
const LEGACY_MODULES_FILE: &str = "modules.yaml";
pub const HISTORY_KEEP: usize = 10;

/// A workspace's build file and history.
pub struct Store {
    /// `Settings.root`.
    pub root: PathBuf,
    /// `kitty.__version__` of the worker, written into a new build file and an export.
    pub version: String,
}

/// `revision`: a build text's identity, the SHA-256 of its `surrogatepass` UTF-8.
pub fn revision(text: &[u32]) -> String {
    hex::encode(Sha256::digest(utf8_surrogatepass(text)))
}

/// `_HISTORY_ID`: `[0-9]{8}T[0-9]{12}Z`.
fn history_id(name: &str) -> bool {
    let bytes = name.as_bytes();
    bytes.len() == 22
        && bytes[..8].iter().all(u8::is_ascii_digit)
        && bytes[8] == b'T'
        && bytes[9..21].iter().all(u8::is_ascii_digit)
        && bytes[21] == b'Z'
}

/// The parsed JSON of a text the reader accepted (`json.loads`).
pub fn parse_document(text: &[u32]) -> Result<Dict, Failure> {
    match json::parse(text) {
        Ok(parsed) => match parsed.value {
            Value::Dict(data) => Ok(data),
            _ => Err(Failure::internal()),
        },
        Err(_) => Err(Failure::internal()),
    }
}

/// A held `flock` on the config directory (`store.locked`); released on drop.
pub struct Locked(#[allow(dead_code)] File);

impl Store {
    pub fn folder(&self) -> PathBuf {
        self.root.join("config")
    }

    pub fn build_path(&self) -> PathBuf {
        self.folder().join(BUILD_FILE)
    }

    fn history_dir(&self) -> PathBuf {
        self.root.join("data").join("build-history")
    }

    /// `(st_mtime_ns, st_size, st_ino)` of the build file, not following a link.
    pub fn signature(&self) -> Option<(i128, u64, u64)> {
        let meta = fs::symlink_metadata(self.build_path()).ok()?;
        Some(private::signature(&meta))
    }

    /// `locked`.
    pub fn lock(&self) -> Result<Locked, Failure> {
        let folder = self.folder();
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&folder)?;
        let handle = private::open_for_lock(&folder)?;
        handle.lock()?;
        Ok(Locked(handle))
    }

    /// `read_text`: the file's text or None; never follows a symbolic link; bad UTF-8 replaced.
    pub fn read_text(&self) -> Result<Option<Text>, Failure> {
        let file = match private::no_follow(OpenOptions::new().read(true)).open(self.build_path()) {
            Ok(file) => file,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
            Err(error) if private::is_link_refusal(&error) => {
                return Err(Failure::new(409, format!("config/{BUILD_FILE} is a symbolic link; replace it with a regular file")));
            }
            Err(error) => return Err(error.into()),
        };
        let kind = file.metadata()?.file_type();
        // Python's `os.fdopen` raises IsADirectoryError before the regular-file check.
        if kind.is_dir() {
            return Err(Failure::internal());
        }
        if !kind.is_file() {
            return Err(Failure::new(409, format!("config/{BUILD_FILE} is not a regular file")));
        }
        let mut data = Vec::new();
        file.take(document::MAX_BYTES as u64 + 1).read_to_end(&mut data)?;
        Ok(Some(String::from_utf8_lossy(&data).chars().map(u32::from).collect()))
    }

    /// `initial_text(None)`.
    pub fn initial_text(&self) -> Text {
        let mut data = Dict::new();
        data.insert(text("schema"), Value::Int(super::value::Int::Small(document::SCHEMA_VERSION)));
        data.insert(text("kitty_version"), Value::Str(text(&self.version)));
        dump_document(&Value::Dict(data))
    }

    /// `ensure`: the file's text, writing a new workspace's first. The worker moved a pre-build
    /// `modules.yaml` into the build when it opened the workspace; one written by hand later is
    /// read only by the next start (as crate::modules refuses it).
    pub fn ensure(&self) -> Result<Text, Failure> {
        if let Some(text) = self.read_text()? {
            return Ok(text);
        }
        let _lock = self.lock()?;
        if let Some(text) = self.read_text()? {
            return Ok(text);
        }
        if fs::symlink_metadata(self.folder().join(LEGACY_MODULES_FILE)).is_ok() {
            return Err(Failure::new(400, "config/modules.yaml is read only when rika! starts; restart rika! to move it into config/build.json"));
        }
        let text = self.initial_text();
        self.write_text(&text)?;
        Ok(text)
    }

    /// `write_text` (the caller holds the lock).
    pub fn write_text(&self, content: &[u32]) -> Result<(), Failure> {
        atomic_write(&self.build_path(), content)
    }

    /// `push_history`: keep a copy of a build about to be replaced; prune to the newest ten.
    pub fn push_history(&self, previous: &[u32], reason: &str, source: &[u32]) -> Result<String, Failure> {
        let folder = self.history_dir();
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&folder)?;
        let mut now = Moment::now();
        let mut id = now.history_id();
        while folder.join(format!("{id}.json")).exists() {
            now.micros = (now.micros + 1) % 1_000_000;
            id = now.history_id();
        }
        // `json.dumps({...})` with its default separators, ASCII only.
        let mut body = String::from("{\"saved_at\": ");
        dump_str(&text(&now.isoformat()), &mut body);
        body.push_str(", \"reason\": ");
        dump_str(&text(reason), &mut body);
        body.push_str(", \"source\": ");
        dump_str(&source[..source.len().min(500)], &mut body);
        body.push_str(", \"text\": ");
        dump_str(previous, &mut body);
        body.push_str("}\n");
        atomic_write(&folder.join(format!("{id}.json")), &text(&body))?;
        let mut entries: Vec<String> = fs::read_dir(&folder)?
            .filter_map(|entry| entry.ok()?.file_name().into_string().ok())
            .filter(|name| name.strip_suffix(".json").is_some_and(history_id))
            .collect();
        entries.sort();
        let excess = entries.len().saturating_sub(HISTORY_KEEP);
        for old in &entries[..excess] {
            let _ = fs::remove_file(folder.join(old));
        }
        Ok(id)
    }

    /// `list_history`: saved builds, newest first.
    pub fn list_history(&self) -> Result<Vec<HistoryEntry>, Failure> {
        let folder = self.history_dir();
        if !folder.is_dir() {
            return Ok(Vec::new());
        }
        let mut names: Vec<String> = fs::read_dir(&folder)?
            .filter_map(|entry| entry.ok()?.file_name().into_string().ok())
            .filter(|name| name.ends_with(".json") && !name.starts_with('.'))
            .collect();
        names.sort();
        names.reverse();
        let mut result = Vec::new();
        for name in names {
            let stem = &name[..name.len() - 5];
            let path = folder.join(&name);
            if !history_id(stem) || fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_symlink()) {
                continue;
            }
            let Some(entry) = read_entry(&path) else { continue };
            let Some(Value::Str(saved)) = entry.get_str("text") else { continue };
            let checked = document::check(saved)?;
            let name = checked.build.as_ref().and_then(|build| build.as_dict()?.get_str("name").cloned());
            let field = |key: &str| entry.get_str(key).cloned().unwrap_or(Value::Str(Text::new()));
            result.push(HistoryEntry {
                id: stem.to_owned(),
                saved_at: field("saved_at"),
                reason: field("reason"),
                source: field("source"),
                name: name.unwrap_or(Value::Str(Text::new())),
                valid: checked.build.is_some(),
                revision: revision(saved),
            });
        }
        Ok(result)
    }

    /// `history_text`.
    pub fn history_text(&self, id: &Value) -> Result<Text, Failure> {
        let id = match id {
            Value::Str(id) => scalar(id).filter(|id| history_id(id)),
            _ => None,
        };
        let Some(id) = id else {
            return Err(Failure::new(400, "Choose a saved build from the history"));
        };
        let path = self.history_dir().join(format!("{id}.json"));
        let linked = fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_symlink());
        let entry = if linked { None } else { read_entry(&path) };
        match entry.as_ref().and_then(|entry| entry.get_str("text")) {
            Some(Value::Str(saved)) => Ok(saved.clone()),
            _ => Err(Failure::new(404, "That saved build is no longer in the history")),
        }
    }
}

/// One `list_history` item: values from the entry file as Python's `json.loads` has them.
pub struct HistoryEntry {
    pub id: String,
    pub saved_at: Value,
    pub reason: Value,
    pub source: Value,
    pub name: Value,
    pub valid: bool,
    pub revision: String,
}

impl HistoryEntry {
    pub fn json(&self, out: &mut String) {
        use super::value::dump;
        out.push_str("{\"id\":");
        dump_str(&text(&self.id), out);
        for (key, value) in [("saved_at", &self.saved_at), ("reason", &self.reason), ("source", &self.source), ("name", &self.name)] {
            out.push_str(&format!(",\"{key}\":"));
            dump(value, out);
        }
        out.push_str(&format!(",\"valid\":{},\"revision\":\"{}\"}}", self.valid, self.revision));
    }
}

/// `_read_entry`: a history file whose JSON is an object with a `text` string.
fn read_entry(path: &Path) -> Option<Dict> {
    let data = fs::read(path).ok()?;
    let source: Text = std::str::from_utf8(&data).ok()?.chars().map(u32::from).collect();
    match json::parse(&source).ok()?.value {
        Value::Dict(entry) if matches!(entry.get_str("text"), Some(Value::Str(_))) => Some(entry),
        _ => None,
    }
}

/// `_atomic_write`: temporary file, fsync, rename, directory fsync; never through a link; a
/// file that exists keeps its mode, a new one is owner-only.
fn atomic_write(target: &Path, content: &[u32]) -> Result<(), Failure> {
    // Python's UTF-8 writer refuses a lone surrogate.
    let content = scalar(content).ok_or_else(Failure::internal)?;
    let folder = target.parent().ok_or_else(Failure::internal)?;
    let name = target.file_name().and_then(|name| name.to_str()).ok_or_else(Failure::internal)?;
    fs::DirBuilder::new().recursive(true).mode(0o700).create(folder)?;
    let mut mode = 0o600;
    if let Ok(meta) = fs::symlink_metadata(target) {
        if meta.file_type().is_symlink() {
            return Err(Failure::new(409, format!("{name} is a symbolic link; replace it with a regular file")));
        }
        if !meta.is_file() {
            return Err(Failure::new(409, format!("{name} is not a regular file")));
        }
        mode = private::mode_of(&meta);
    }
    let temporary = folder.join(format!(".{name}.{}", crate::auth::token_hex(6)));
    let written = (|| -> std::io::Result<()> {
        let mut handle = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&temporary)?;
        handle.write_all(content.as_bytes())?;
        handle.sync_all()?;
        private::set_mode(&temporary, mode)?;
        fs::rename(&temporary, target)
    })();
    if let Err(error) = written {
        let _ = fs::remove_file(&temporary);
        return Err(error.into());
    }
    File::open(folder)?.sync_all()?;
    Ok(())
}

/// `changed`: the text after `mutate`, validated; an unchanged document keeps its exact text.
pub fn changed(source: &[u32], mutate: impl FnOnce(&mut Dict) -> Result<(), Failure>) -> Result<Text, Failure> {
    let checked = document::check(source)?;
    refuse_invalid(&checked)?;
    let mut data = parse_document(source)?;
    let mut before = String::new();
    dump_sorted(&Value::Dict(data.clone()), &mut before);
    mutate(&mut data)?;
    let data = Value::Dict(data);
    let mut after = String::new();
    dump_sorted(&data, &mut after);
    if after == before {
        return Ok(source.to_vec());
    }
    let new = dump_document(&data);
    let result = document::check(&new)?;
    if result.build.is_none() {
        return Err(Failure::with(400, format!("That change makes the build invalid: {}", first_error(&result.problems)), result.problems));
    }
    Ok(new)
}

/// The 409 a settings change gets while the file has an error.
pub fn refuse_invalid(checked: &Checked) -> Result<(), Failure> {
    if checked.build.is_none() {
        return Err(Failure::with(
            409,
            format!("config/{BUILD_FILE} has an error ({}); fix it in the build editor first", first_error(&checked.problems)),
            checked.problems.clone(),
        ));
    }
    Ok(())
}

/// `section`: the object at `path` in a build document, created when missing.
pub fn section<'a>(data: &'a mut Dict, path: &[&str]) -> &'a mut Dict {
    let mut node = data;
    for key in path {
        let key = text(key);
        if !matches!(node.get(&key), Some(Value::Dict(_))) {
            node.insert(key.clone(), Value::Dict(Dict::new()));
        }
        node = match node.get_mut(&key) {
            Some(Value::Dict(child)) => child,
            _ => unreachable!("inserted above"),
        };
    }
    node
}

// The tests read Unix modes.
#[cfg(all(test, unix))]
mod tests {
    use std::os::unix::fs::PermissionsExt;
    use super::*;

    fn store() -> (Store, PathBuf) {
        let root = std::env::temp_dir().join(format!("rika-store-{}", crate::auth::token_hex(6)));
        fs::create_dir_all(&root).unwrap();
        (Store { root: root.clone(), version: "9.9.9".into() }, root)
    }

    #[test]
    fn a_new_workspace_gets_an_owner_only_build() {
        let (store, root) = store();
        let written = store.ensure().unwrap();
        assert_eq!(String::from_iter(written.iter().map(|&c| char::from_u32(c).unwrap())),
                   "{\n  \"schema\": 1,\n  \"kitty_version\": \"9.9.9\"\n}\n");
        assert_eq!(fs::metadata(store.build_path()).unwrap().permissions().mode() & 0o777, 0o600);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn history_keeps_the_newest_ten_and_refuses_other_names() {
        let (store, root) = store();
        for index in 0..HISTORY_KEEP + 3 {
            store.push_history(&text(&format!("{{\"schema\": 1, \"name\": \"n{index}\"}}")), "install", &text("")).unwrap();
        }
        let entries = store.list_history().unwrap();
        assert_eq!(entries.len(), HISTORY_KEEP);
        assert!(matches!(&entries[0].name, Value::Str(name) if *name == text("n12")));
        assert_eq!(store.history_text(&Value::Str(text("../../config/build"))).err().map(|f| f.status), Some(400));
        assert_eq!(store.history_text(&Value::Str(text("20000101T000000000000Z"))).err().map(|f| f.status), Some(404));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn a_symlinked_build_file_is_never_followed() {
        let (store, root) = store();
        fs::create_dir_all(store.folder()).unwrap();
        fs::write(root.join("elsewhere.json"), "{\"schema\": 1}").unwrap();
        std::os::unix::fs::symlink(root.join("elsewhere.json"), store.build_path()).unwrap();
        assert_eq!(store.read_text().err().map(|f| f.status), Some(409));
        assert_eq!(store.write_text(&text("{}")).err().map(|f| f.status), Some(409));
        assert_eq!(fs::read_to_string(root.join("elsewhere.json")).unwrap(), "{\"schema\": 1}");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn revisions_hash_surrogatepass_utf8() {
        assert_eq!(revision(&text("")), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        assert_eq!(revision(&[0xd800]), hex::encode(Sha256::digest([0xed, 0xa0, 0x80])));
    }
}
