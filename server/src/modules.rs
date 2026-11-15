//! `/api/modules`: the module selection (`kitty.modules`, `kitty.core.bootstrap`), answered by
//! the front.
//!
//! ```text
//! GET  /api/modules               {enabled, available, error?, configured, restart_required}
//! POST /api/modules {enabled}     persist a pending selection; the same state back
//! ```
//!
//! `enabled` is the worker's frozen snapshot (its readiness line); `configured` is what
//! `config/build.json` selects now, applied on the next start. A save provisions the missing
//! careers templates first when applyai is chosen, then rewrites the build file atomically
//! under the same exclusive lock on the config directory the Python store takes, so the two
//! processes never interleave writes. The worker's build watcher picks the save up and pushes
//! it to every window.

use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use crate::private::{self, Mode};
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value as Json};

use crate::builds::document::{self, Checked, Problem};
use crate::builds::json;
use crate::builds::value::{float_repr, lossy, repr_text, text, Text, Value};

pub const PATH: &str = "/api/modules";
/// `kitty.modules.REGISTRY`: the same file Python reads, so the two lists never drift.
const REGISTRY: &str = include_str!("../../src/kitty/modules.json");

#[derive(serde::Deserialize)]
pub struct ModuleInfo {
    pub id: String,
    pub label: String,
    pub description: String,
    /// The packs (crate::packs) this module needs before it can be turned on.
    pub packs: Vec<String>,
}

#[derive(serde::Deserialize)]
pub struct PackInfo {
    pub id: String,
    pub label: String,
    pub description: String,
}

#[derive(serde::Deserialize)]
pub struct Registry {
    pub modules: Vec<ModuleInfo>,
    /// `modules.DEFAULT_MODULES`: the selection of a workspace without one.
    pub default: Vec<String>,
    pub packs: Vec<PackInfo>,
}

/// `kitty.modules.MODULE_IDS` and their metadata, in canonical order.
static PARSED: std::sync::LazyLock<Registry> =
    std::sync::LazyLock::new(|| serde_json::from_str(REGISTRY).expect("src/kitty/modules.json is a valid registry"));

pub fn registry() -> &'static Registry {
    &PARSED
}

fn module_ids() -> impl Iterator<Item = &'static str> {
    registry().modules.iter().map(|module| module.id.as_str())
}
const BUILD_FILE: &str = "build.json";
const LEGACY_MODULES_FILE: &str = "modules.yaml";
/// `kitty.config.CONFIG_FILES`: every careers file has a public template, provisioned when
/// applyai is chosen (`bootstrap.copy_examples`). A template in the workspace's own config
/// directory wins over the one built in.
const CAREERS_TEMPLATES: [(&str, &str); 11] = [
    ("profile.yaml", include_str!("../../config/profile.yaml.example")),
    ("tracks.yaml", include_str!("../../config/tracks.yaml.example")),
    ("sources.yaml", include_str!("../../config/sources.yaml.example")),
    ("watchlist.yaml", include_str!("../../config/watchlist.yaml.example")),
    ("answers.yaml", include_str!("../../config/answers.yaml.example")),
    ("skills.yaml", include_str!("../../config/skills.yaml.example")),
    ("conferences.yaml", include_str!("../../config/conferences.yaml.example")),
    ("views.yaml", include_str!("../../config/views.yaml.example")),
    ("resume_terms.yaml", include_str!("../../config/resume_terms.yaml.example")),
    ("triage.yaml", include_str!("../../config/triage.yaml.example")),
    ("apply.yaml", include_str!("../../config/apply.yaml.example")),
];

/// The workspace as the worker opened it.
pub struct Workspace {
    /// `Settings.config_dir`.
    pub config_dir: PathBuf,
    /// The worker's active modules, frozen for its lifetime.
    pub active: Vec<String>,
    /// `kitty.__version__`, written into a new build file.
    pub version: String,
    /// The packs the active modules need that the worker lacks (`kitty.packs.missing`).
    pub missing: Vec<String>,
}

/// An answer other than 200: `{"error"}`, as `RequestHandler._dispatch` sends it.
pub struct Failure {
    pub status: u16,
    pub message: String,
}

fn fail(status: u16, message: impl Into<String>) -> Failure {
    Failure { status, message: message.into() }
}

/// Python's catch-all for an exception no handler turned into an answer.
fn internal() -> Failure {
    fail(500, "Local operation failed; inspect CLI diagnostics")
}

fn errors(checked: &Checked) -> impl Iterator<Item = &Problem> {
    checked.problems.iter().filter(|p| p.severity == "error")
}

/// `str(Problem)`.
fn problem_text(p: &Problem) -> String {
    let path = lossy(&p.path);
    let place = if path.is_empty() { String::new() } else { format!(" ({path})") };
    format!("line {}, column {}{place}: {}", p.line, p.column, lossy(&p.message))
}

/// `str(ConfigProblem)`.
fn config_problem(file: &Path, line: Option<usize>, location: &str, message: &str, hint: &str) -> String {
    let place = match line {
        Some(line) => format!("{}:{line}", file.display()),
        None => file.display().to_string(),
    };
    let head = if location.is_empty() { format!("{place}  {message}") } else { format!("{place}  {location}: {message}") };
    format!("{head}\n    fix: {hint}")
}

/// Why a store operation cannot proceed (`store.BuildStoreError`'s message), or a failure
/// Python raises without a handler (a 500).
enum StoreError {
    Refused(String),
    Internal,
}

impl From<std::io::Error> for StoreError {
    fn from(_: std::io::Error) -> Self {
        StoreError::Internal
    }
}

impl From<StoreError> for Failure {
    /// `save_modules` turns a store refusal into the `ValueError` `set_modules` answers 400 with.
    fn from(error: StoreError) -> Self {
        match error {
            StoreError::Refused(message) => fail(400, message),
            StoreError::Internal => internal(),
        }
    }
}

/// The selection the build file would get from a pre-build modules.yaml. The worker moves that
/// file into the build at start-up (`store.ensure`); one written by hand after that is read
/// only by the next start.
const LEGACY_LATE: &str = "config/modules.yaml is read only when rika! starts";
const LEGACY_HINT: &str = "restart rika! to move it into config/build.json";

impl Workspace {
    fn root(&self) -> &Path {
        self.config_dir.parent().unwrap_or(&self.config_dir)
    }

    fn folder(&self) -> PathBuf {
        self.root().join("config")
    }

    fn build_path(&self) -> PathBuf {
        self.folder().join(BUILD_FILE)
    }

    fn legacy_present(&self) -> bool {
        fs::symlink_metadata(self.config_dir.join(LEGACY_MODULES_FILE)).is_ok()
    }

    /// `store.read_text`: never follows a symbolic link; invalid UTF-8 is replaced.
    fn read_text(&self) -> Result<Option<Text>, StoreError> {
        let file = match private::no_follow(OpenOptions::new().read(true)).open(self.build_path()) {
            Ok(file) => file,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
            Err(error) if private::is_link_refusal(&error) => {
                return Err(StoreError::Refused(format!("config/{BUILD_FILE} is a symbolic link; replace it with a regular file")));
            }
            Err(error) => return Err(error.into()),
        };
        let kind = file.metadata()?.file_type();
        // Python's `os.fdopen` raises IsADirectoryError before the regular-file check.
        if kind.is_dir() {
            return Err(StoreError::Internal);
        }
        if !kind.is_file() {
            return Err(StoreError::Refused(format!("config/{BUILD_FILE} is not a regular file")));
        }
        let mut data = Vec::new();
        file.take(document::MAX_BYTES as u64 + 1).read_to_end(&mut data)?;
        Ok(Some(String::from_utf8_lossy(&data).chars().map(u32::from).collect()))
    }

    /// `modules.load_modules`: the persisted selection, or the `ConfigError` text that says why not.
    fn load(&self) -> Result<Vec<String>, Result<String, ()>> {
        let path = self.config_dir.join(BUILD_FILE);
        let source = match self.read_text() {
            Ok(source) => source,
            Err(StoreError::Refused(message)) => {
                return Err(Ok(config_problem(&path, None, "", &message, "replace it with a regular file")));
            }
            Err(StoreError::Internal) => return Err(Err(())),
        };
        let Some(source) = source else {
            if self.legacy_present() {
                return Err(Ok(config_problem(&self.config_dir.join(LEGACY_MODULES_FILE), None, "", LEGACY_LATE, LEGACY_HINT)));
            }
            // `modules.DEFAULT_MODULES`, in canonical order.
            return Ok(module_ids().filter(|id| registry().default.iter().any(|d| d == id)).map(str::to_owned).collect());
        };
        let checked = document::check(&source).map_err(|_| Err(()))?;
        let Some(build) = checked.build.as_ref() else {
            let hint = "fix it in Settings > Build or any editor; rika! checks every save";
            return Err(Ok(errors(&checked)
                .map(|p| config_problem(&path, Some(p.line), &lossy(&p.path), &lossy(&p.message), hint))
                .collect::<Vec<_>>()
                .join("\n")));
        };
        match build.as_dict().and_then(|b| b.get_str("modules")) {
            Some(Value::List(items)) => Ok(items.iter().filter_map(|item| item.as_str().map(|s| lossy(s))).collect()),
            _ => Err(Err(())),
        }
    }

    /// What the next start selects: the build's modules, or the active ones while it is unreadable.
    pub fn configured(&self) -> Vec<String> {
        self.load().unwrap_or_else(|_| self.active.clone())
    }

    /// `modules.modules_state`.
    pub fn state(&self) -> Result<Json, Failure> {
        let mut state = Map::new();
        state.insert("enabled".into(), json!(self.active));
        state.insert("available".into(), Json::Array(registry().modules.iter().map(|m| {
            json!({ "id": m.id, "label": m.label, "description": m.description, "packs": m.packs })
        }).collect()));
        let configured = match self.load() {
            Ok(configured) => configured,
            Err(Ok(error)) => {
                state.insert("error".into(), json!(error));
                self.active.clone()
            }
            Err(Err(())) => return Err(internal()),
        };
        // Python's key order: enabled, available, error?, configured, restart_required.
        state.insert("configured".into(), json!(configured));
        state.insert("restart_required".into(), json!(configured != self.active));
        state.insert("missing".into(), json!(self.missing));
        Ok(Json::Object(state))
    }

    /// `WebApplication.set_modules` → `bootstrap.configure_modules`. A module turned on here
    /// needs its packs ready first (`ready`, crate::packs); one already selected keeps its place.
    pub fn configure(&self, body: &[u8], ready: impl Fn(&str) -> bool) -> Result<Json, Failure> {
        let expected = || fail(400, "Expected {\"enabled\": [module ids]}");
        // The front already refused repeated keys and non-finite numbers; reading the body
        // again as Python does keeps every value (big integers too) as Python's repr shows it.
        let source: Text = std::str::from_utf8(body).map_err(|_| expected())?.chars().map(u32::from).collect();
        let parsed = json::parse(&source).map_err(|_| expected())?.value;
        let body = parsed.as_dict().ok_or_else(expected)?;
        let enabled = match body.get_str("enabled") {
            Some(Value::List(items)) if body.len() == 1 => items,
            _ => return Err(expected()),
        };
        let selection = normalize(enabled).map_err(|message| fail(400, message))?;
        let before = self.configured();
        for module in registry().modules.iter().filter(|m| selection.contains(&m.id) && !before.contains(&m.id)) {
            if let Some(pack) = module.packs.iter().find(|pack| !ready(pack)) {
                let label = registry().packs.iter().find(|p| &p.id == pack).map_or(pack.as_str(), |p| p.label.as_str());
                return Err(fail(409, format!("{} needs its download first: download {label} in Settings > Modules, then turn it on", module.label)));
            }
        }
        fs::create_dir_all(&self.config_dir).map_err(|_| internal())?;
        if selection.iter().any(|id| id == "applyai") {
            self.copy_templates().map_err(|_| internal())?;
        }
        self.edit(&selection)?;
        self.state()
    }

    /// `bootstrap.copy_examples`: create each missing careers file; never overwrite.
    fn copy_templates(&self) -> std::io::Result<()> {
        for (name, built_in) in CAREERS_TEMPLATES {
            let target = self.config_dir.join(name);
            if fs::symlink_metadata(&target).is_ok() {
                continue;
            }
            let own = self.config_dir.join(format!("{name}.example"));
            let template = if own.is_file() { fs::read_to_string(&own)? } else { built_in.to_owned() };
            let mut output = match OpenOptions::new().write(true).create_new(true).mode(0o600).open(&target) {
                Ok(output) => output,
                Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            };
            private::set_mode(&target, 0o600)?;
            output.write_all(template.as_bytes())?;
        }
        Ok(())
    }

    /// `store.locked`: the exclusive lock on the config directory every build write takes.
    fn lock(&self) -> Result<File, StoreError> {
        let folder = self.folder();
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&folder)?;
        let handle = private::open_for_lock(&folder)?;
        handle.lock()?;
        Ok(handle)
    }

    /// `store.initial_text(None)`.
    fn initial_text(&self) -> Text {
        text(&format!("{{\n  \"schema\": 1,\n  \"kitty_version\": {}\n}}\n", serde_json::to_string(&self.version).expect("a string serializes")))
    }

    /// `store.ensure`: write a new workspace's build file when there is none.
    fn ensure(&self) -> Result<(), StoreError> {
        if self.read_text()?.is_some() {
            return Ok(());
        }
        let _lock = self.lock()?;
        if self.read_text()?.is_some() {
            return Ok(());
        }
        if self.legacy_present() {
            return Err(StoreError::Refused(format!("{LEGACY_LATE}; {LEGACY_HINT}")));
        }
        self.write(&self.initial_text())
    }

    /// `modules.save_modules` → `store.edit`: set `modules`, validated, written only when changed.
    fn edit(&self, selection: &[String]) -> Result<(), StoreError> {
        self.ensure()?;
        let _lock = self.lock()?;
        let current = self.read_text()?.filter(|t| !t.is_empty()).unwrap_or_else(|| self.initial_text());
        let checked = document::check(&current).map_err(|_| StoreError::Internal)?;
        if checked.build.is_none() {
            let first = errors(&checked).next().map(problem_text).unwrap_or_default();
            return Err(StoreError::Refused(format!("config/{BUILD_FILE} has an error ({first}); fix it in the build editor first")));
        }
        let Value::Dict(mut data) = json::parse(&current).map_err(|_| StoreError::Internal)?.value else {
            return Err(StoreError::Internal);
        };
        let chosen = Value::List(selection.iter().map(|id| Value::Str(text(id))).collect());
        let unchanged = match (data.get_str("modules"), &chosen) {
            (Some(Value::List(old)), Value::List(new)) => old.len() == new.len()
                && old.iter().zip(new).all(|(a, b)| matches!((a, b), (Value::Str(a), Value::Str(b)) if a == b)),
            _ => false,
        };
        if unchanged {
            return Ok(());
        }
        data.insert(text("modules"), chosen);
        let mut new = Text::new();
        pretty(&Value::Dict(data), 0, &mut new);
        new.push(u32::from('\n'));
        let result = document::check(&new).map_err(|_| StoreError::Internal)?;
        if result.build.is_none() {
            let first = errors(&result).next().map(problem_text).unwrap_or_default();
            return Err(StoreError::Refused(format!("That change makes the build invalid: {first}")));
        }
        if new != current {
            self.write(&new)?;
        }
        Ok(())
    }

    /// `store._atomic_write`: temporary file, fsync, rename, directory fsync; never through a link.
    fn write(&self, content: &Text) -> Result<(), StoreError> {
        // Python's UTF-8 writer refuses a lone surrogate.
        let content: String = content.iter().map(|&c| char::from_u32(c)).collect::<Option<_>>().ok_or(StoreError::Internal)?;
        let folder = self.folder();
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&folder)?;
        let target = folder.join(BUILD_FILE);
        let mut mode = 0o600;
        if let Ok(meta) = fs::symlink_metadata(&target) {
            if meta.file_type().is_symlink() {
                return Err(StoreError::Refused(format!("{BUILD_FILE} is a symbolic link; replace it with a regular file")));
            }
            if !meta.is_file() {
                return Err(StoreError::Refused(format!("{BUILD_FILE} is not a regular file")));
            }
            mode = private::mode_of(&meta);
        }
        let temporary = folder.join(format!(".{BUILD_FILE}.{}", crate::auth::token_hex(6)));
        let written = (|| -> std::io::Result<()> {
            let mut handle = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&temporary)?;
            handle.write_all(content.as_bytes())?;
            handle.sync_all()?;
            private::set_mode(&temporary, mode)?;
            fs::rename(&temporary, &target)
        })();
        if let Err(error) = written {
            let _ = fs::remove_file(&temporary);
            return Err(error.into());
        }
        File::open(&folder)?.sync_all()?;
        Ok(())
    }
}

/// `modules.normalize_modules`: known ids, no duplicates, canonical order; else Python's message.
fn normalize(selection: &[Value]) -> Result<Vec<String>, String> {
    let mut chosen: Vec<&str> = Vec::new();
    for item in selection {
        let known = item.as_str().map(|s| lossy(s)).and_then(|s| module_ids().find(|id| *id == s));
        let Some(id) = known else {
            let ids = module_ids().map(|id| format!("'{id}'")).collect::<Vec<_>>().join(", ");
            return Err(format!("unknown module {}; use [{ids}]", lossy(&repr_text(item))));
        };
        if chosen.contains(&id) {
            return Err(format!("duplicate module {}", lossy(&repr_text(item))));
        }
        chosen.push(id);
    }
    Ok(module_ids().filter(|id| chosen.contains(id)).map(str::to_owned).collect())
}

/// `json.dumps(value, indent=2, ensure_ascii=False)`.
fn pretty(v: &Value, level: usize, out: &mut Text) {
    let push = |out: &mut Text, s: &str| out.extend(s.chars().map(u32::from));
    let indent = |out: &mut Text, level: usize| {
        out.push(u32::from('\n'));
        out.extend(std::iter::repeat_n(u32::from(' '), level * 2));
    };
    match v {
        Value::Null => push(out, "null"),
        Value::Bool(b) => push(out, if *b { "true" } else { "false" }),
        Value::Int(i) => push(out, &i.decimal()),
        Value::Float(f) if f.is_nan() => push(out, "NaN"),
        Value::Float(f) if f.is_infinite() => push(out, if *f > 0.0 { "Infinity" } else { "-Infinity" }),
        Value::Float(f) => push(out, &float_repr(*f)),
        Value::Str(s) => string(s, out),
        Value::List(items) if items.is_empty() => push(out, "[]"),
        Value::List(items) => {
            out.push(u32::from('['));
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(u32::from(','));
                }
                indent(out, level + 1);
                pretty(item, level + 1, out);
            }
            indent(out, level);
            out.push(u32::from(']'));
        }
        Value::Dict(d) if d.len() == 0 => push(out, "{}"),
        Value::Dict(d) => {
            out.push(u32::from('{'));
            for (i, (key, item)) in d.iter().enumerate() {
                if i > 0 {
                    out.push(u32::from(','));
                }
                indent(out, level + 1);
                string(key, out);
                push(out, ": ");
                pretty(item, level + 1, out);
            }
            indent(out, level);
            out.push(u32::from('}'));
        }
    }
}

/// A JSON string as `ensure_ascii=False` writes it: only `"`, `\` and controls are escaped.
fn string(s: &[u32], out: &mut Text) {
    out.push(u32::from('"'));
    for &c in s {
        let escaped = match c {
            0x22 => Some("\\\"".to_owned()),
            0x5c => Some("\\\\".to_owned()),
            0x0a => Some("\\n".to_owned()),
            0x0d => Some("\\r".to_owned()),
            0x09 => Some("\\t".to_owned()),
            0x08 => Some("\\b".to_owned()),
            0x0c => Some("\\f".to_owned()),
            0..=0x1f => Some(format!("\\u{c:04x}")),
            _ => None,
        };
        match escaped {
            Some(escape) => out.extend(escape.chars().map(u32::from)),
            None => out.push(c),
        }
    }
    out.push(u32::from('"'));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pretty_matches_python_indent_two() {
        let parsed = json::parse(&text("{\"a\": [], \"b\": {}, \"c\": [1, 2.5, \"é\\n\"], \"d\": {\"e\": null}}")).ok().unwrap().value;
        let mut out = Text::new();
        pretty(&parsed, 0, &mut out);
        assert_eq!(lossy(&out), "{\n  \"a\": [],\n  \"b\": {},\n  \"c\": [\n    1,\n    2.5,\n    \"é\\n\"\n  ],\n  \"d\": {\n    \"e\": null\n  }\n}");
    }

    #[test]
    fn normalize_reports_python_reprs() {
        let items = |s: &str| match json::parse(&text(s)).ok().unwrap().value {
            Value::List(items) => items,
            _ => unreachable!(),
        };
        assert_eq!(normalize(&items("[\"terminal\", \"applyai\"]")), Ok(vec!["applyai".to_owned(), "terminal".to_owned()]));
        assert_eq!(normalize(&items("[1]")).unwrap_err(), "unknown module 1; use ['applyai', 'workspace', 'math', 'learnharness', 'terminal', 'voice', 'brain']");
        assert_eq!(normalize(&items("[true]")).unwrap_err(), "unknown module True; use ['applyai', 'workspace', 'math', 'learnharness', 'terminal', 'voice', 'brain']");
        assert_eq!(normalize(&items("[\"applyai\", \"applyai\"]")).unwrap_err(), "duplicate module 'applyai'");
    }
}
