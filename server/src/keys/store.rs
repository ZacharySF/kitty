//! `kitty.core.keys.Keys` for the routes: the OS keyring when one answers, else an
//! owner-only file under the workspace's `.private/`, else the key's environment variable
//! (never for employer account passwords, `APPLY_PW_*`). The same names, files, checks,
//! refusals and status rows as Python, which keeps reading the same store (the AI providers,
//! `kitty keys`). No value ever appears in an answer or an error.

use std::collections::{BTreeSet, HashMap};
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use crate::private::{self, Mode};
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

/// `KNOWN`: name, label, environment variable, file name under `.private/` (else `keys/NAME`).
const KNOWN: &[(&str, &str, &str, Option<&str>)] = &[
    ("OPENROUTER", "OpenRouter API key", "OPENROUTER_API_KEY", Some("openrouter-key")),
    ("ANTHROPIC", "Anthropic API key", "ANTHROPIC_API_KEY", None),
    ("USAJOBS", "USAJOBS API key", "USAJOBS_API_KEY", None),
    ("BRAVE", "Brave Search API key", "BRAVE_SEARCH_API_KEY", None),
    // Only for installing modules from private repositories (kitty.core.external_modules).
    ("GITHUB", "GitHub token", "GITHUB_TOKEN", None),
];
pub const DEFAULT_SERVICE: &str = "kitty";
const ACCOUNT_PREFIX: &str = "APPLY_PW_";
const ACCOUNT_FILE_WARNING: &str = "No OS keyring is available, so employer account passwords are stored in owner-only files under .private/keys/. Unlock or install a keyring and move them there.";
pub const KEYRING_LABEL: &str = "OS keyring (Secret Service)";
/// `tempfile`'s name characters.
const TEMPORARY: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";

/// A keyring operation failed; what failed is never reported (it may carry a value).
#[derive(Debug)]
pub struct Fault;

/// A secret store keyed by name; implementations never report values in errors.
pub trait Keyring {
    fn get(&mut self, name: &str) -> Result<Option<String>, Fault>;
    fn set(&mut self, name: &str, value: &str) -> Result<(), Fault>;
    fn delete(&mut self, name: &str) -> Result<bool, Fault>;
    fn names(&mut self) -> Result<Vec<String>, Fault>;
}

#[derive(Debug, PartialEq, Eq)]
pub enum Error {
    /// `KeysError(message, status)`.
    Keys(u16, &'static str),
    /// Anything Python had no handler for (an `OSError` outside the checks).
    Internal,
}

type Result<T, E = Error> = std::result::Result<T, E>;

fn refuse(status: u16, message: &'static str) -> Error {
    Error::Keys(status, message)
}

fn internal<E>(_: E) -> Error {
    Error::Internal
}

/// `str.isspace()`.
fn python_space(c: char) -> bool {
    matches!(c, '\t'..='\r' | '\x1c'..='\x20' | '\u{85}' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}'
        | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

/// `str.strip()`.
pub fn strip(text: &str) -> &str {
    text.trim_matches(python_space)
}

/// `NAME_RE.fullmatch`: `[A-Z][A-Z0-9_]{0,63}`.
fn is_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    (1..=64).contains(&bytes.len()) && bytes[0].is_ascii_uppercase()
        && bytes[1..].iter().all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || *b == b'_')
}

/// `canonical`: upper-case NAME, or the refusal for anything that is not a key name.
pub fn canonical(name: &Value) -> Result<String> {
    let Value::String(name) = name else { return Err(refuse(400, "A key name is required")) };
    let upper = strip(name).to_uppercase();
    if !is_name(&upper) {
        return Err(refuse(400, "Key names use A-Z, 0-9 and _ and start with a letter"));
    }
    Ok(upper)
}

/// `_check_value`: one line of printable ASCII without spaces, at most 4096 characters.
fn check_value(value: &Value) -> Result<&str> {
    let Value::String(value) = value else { return Err(refuse(400, "Enter the key as text")) };
    let value = strip(value);
    if !(1..=4096).contains(&value.len()) || !value.bytes().all(|b| (0x21..=0x7e).contains(&b)) {
        return Err(refuse(400, "A key is one line of printable characters without spaces (at most 4096)"));
    }
    Ok(value)
}

fn known(name: &str) -> Option<&'static (&'static str, &'static str, &'static str, Option<&'static str>)> {
    KNOWN.iter().find(|spec| spec.0 == name)
}

/// `env_name`: the environment variable a key is read from.
fn env_name(name: &str) -> &str {
    known(name).map_or(name, |spec| spec.2)
}

fn is_symlink(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|meta| meta.file_type().is_symlink())
}

/// `Path.mkdir(mode=0o700, exist_ok=True)`.
fn make_private_dir(path: &Path) -> Result<()> {
    match DirBuilder::new().mode(0o700).create(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::AlreadyExists && path.is_dir() => Ok(()),
        Err(_) => Err(Error::Internal),
    }
}

/// `tempfile.mkstemp(dir=folder, prefix=".key-")`: a new owner-only file no one else named.
fn temporary_file(folder: &Path) -> Result<(File, PathBuf)> {
    loop {
        let mut random = [0u8; 8];
        getrandom::fill(&mut random).map_err(internal)?;
        let name: String = random.iter().map(|b| TEMPORARY[*b as usize % TEMPORARY.len()] as char).collect();
        let path = folder.join(format!(".key-{name}"));
        match private::no_follow(OpenOptions::new().read(true).write(true).create_new(true).mode(0o600)).open(&path) {
            Ok(file) => return Ok((file, path)),
            Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(Error::Internal),
        }
    }
}

/// Named keys of one workspace (`Keys`); construct with [`super::for_settings`].
pub struct Keys<'a> {
    pub root: PathBuf,
    pub store: Option<Box<dyn Keyring + 'a>>,
    /// The worker's environment; None when keys are never read from it (demo).
    pub environment: Option<&'a HashMap<String, String>>,
}

impl Keys<'_> {
    fn private(&self) -> PathBuf {
        self.root.join(".private")
    }

    pub fn file_path(&self, name: &str) -> PathBuf {
        match known(name).and_then(|spec| spec.3) {
            Some(file) => self.private().join(file),
            None => self.private().join("keys").join(name),
        }
    }

    fn refuse_links(&self, path: &Path) -> Result<()> {
        let parent = path.parent().unwrap_or(path);
        if [self.private().as_path(), parent, path].into_iter().any(is_symlink) {
            return Err(refuse(409, "Private key storage must not be a symbolic link"));
        }
        Ok(())
    }

    fn read_file(&self, name: &str) -> Result<Option<String>> {
        let path = self.file_path(name);
        self.refuse_links(&path)?;
        let file = match private::no_follow(OpenOptions::new().read(true)).open(&path) {
            Ok(file) => file,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(refuse(409, "Cannot read a private key file")),
        };
        let info = file.metadata().map_err(internal)?;
        if !info.file_type().is_file() {
            return Err(refuse(409, "A private key path is not a regular file"));
        }
        if !private::owner_only(&info) {
            return Err(refuse(409, "Private key files must be owner-only (chmod 600)"));
        }
        if !private::owned_by_me(&info) {
            return Err(refuse(409, "Private key files must belong to you"));
        }
        let mut raw = Vec::new();
        file.take(8192).read_to_end(&mut raw).map_err(internal)?;
        let text = String::from_utf8(raw).ok().filter(|text| text.is_ascii())
            .ok_or(refuse(409, "A private key file is not plain text"))?;
        let value = strip(&text);
        Ok((!value.is_empty()).then(|| value.to_owned()))
    }

    fn write_file(&self, name: &str, value: &str) -> Result<()> {
        let path = self.file_path(name);
        self.refuse_links(&path)?;
        make_private_dir(&self.private())?;
        let folder = path.parent().expect("a key file has a folder");
        make_private_dir(folder)?;
        self.refuse_links(&path)?;
        let (mut file, temporary) = temporary_file(folder)?;
        let written = (|| {
            private::set_mode(&temporary, 0o600)?;
            file.write_all(value.as_bytes())?;
            file.flush()?;
            file.sync_all()?;
            drop(file);
            fs::rename(&temporary, &path)
        })();
        let _ = fs::remove_file(&temporary);
        written.map_err(internal)
    }

    fn delete_file(&self, name: &str) -> Result<bool> {
        let path = self.file_path(name);
        self.refuse_links(&path)?;
        match fs::remove_file(&path) {
            Ok(()) => Ok(true),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(false),
            Err(_) => Err(Error::Internal),
        }
    }

    fn file_names(&self) -> Result<BTreeSet<String>> {
        let folder = self.private().join("keys");
        if is_symlink(&folder) || !folder.is_dir() {
            return Ok(BTreeSet::new());
        }
        let mut names = BTreeSet::new();
        for entry in fs::read_dir(&folder).map_err(internal)? {
            if let Some(name) = entry.map_err(internal)?.file_name().to_str() {
                if is_name(name) {
                    names.insert(name.to_owned());
                }
            }
        }
        Ok(names)
    }

    fn keyring_get(&mut self, name: &str) -> Result<Option<String>> {
        let Some(store) = self.store.as_mut() else { return Ok(None) };
        let value = store.get(name).map_err(|Fault| refuse(503, "The OS keyring did not answer (is it locked?)"))?;
        Ok(value.map(|value| strip(&value).to_owned()).filter(|value| !value.is_empty()))
    }

    /// `lookup`: (value, backend) for NAME, trying keyring, file, then environment.
    fn lookup(&mut self, name: &str) -> Result<Option<(String, &'static str)>> {
        let name = canonical(&Value::String(name.to_owned()))?;
        if let Some(value) = self.keyring_get(&name)? {
            return Ok(Some((value, "keyring")));
        }
        if let Some(value) = self.read_file(&name)? {
            return Ok(Some((value, "file")));
        }
        if let Some(environment) = self.environment.filter(|_| !name.starts_with(ACCOUNT_PREFIX)) {
            let value = environment.get(env_name(&name)).map_or("", |value| strip(value));
            if !value.is_empty() {
                return Ok(Some((value.to_owned(), "env")));
            }
        }
        Ok(None)
    }

    /// `read`: NAME's value, or "" when unset.
    pub fn read(&mut self, name: &str) -> Result<String> {
        Ok(self.lookup(name)?.map(|(value, _)| value).unwrap_or_default())
    }

    /// `set`: the keyring when one exists (dropping a stale file copy), else the owner-only file.
    pub fn set(&mut self, name: &Value, value: &Value) -> Result<()> {
        let name = canonical(name)?;
        let value = check_value(value)?;
        if let Some(store) = self.store.as_mut() {
            store.set(&name, value).map_err(|Fault| refuse(503, "The OS keyring refused the key (is it locked?)"))?;
            self.delete_file(&name)?;
            return Ok(());
        }
        self.write_file(&name, value)
    }

    /// `remove`: from the keyring and the file; an environment variable stays.
    pub fn remove(&mut self, name: &Value) -> Result<bool> {
        let name = canonical(name)?;
        let mut removed = false;
        if let Some(store) = self.store.as_mut() {
            removed = store.delete(&name).map_err(|Fault| refuse(503, "The OS keyring refused the removal (is it locked?)"))?;
        }
        Ok(self.delete_file(&name)? || removed)
    }

    /// `move_to_keyring`: the file copy into the keyring, verified before the file goes.
    pub fn move_to_keyring(&mut self, name: &Value) -> Result<()> {
        let name = canonical(name)?;
        if self.store.is_none() {
            return Err(refuse(409, "No OS keyring is available"));
        }
        let value = self.read_file(&name)?.ok_or(refuse(404, "That key has no file copy to move"))?;
        let store = self.store.as_mut().expect("checked above");
        let refused = |Fault| refuse(503, "The OS keyring refused the key (is it locked?)");
        store.set(&name, &value).map_err(refused)?;
        let stored = store.get(&name).map_err(refused)?;
        if stored.as_deref().map(strip) != Some(value.as_str()) {
            return Err(refuse(503, "The OS keyring did not keep the key; the file copy stays"));
        }
        self.delete_file(&name)?;
        Ok(())
    }

    /// `names`: the known names first, then user-defined ones stored anywhere, sorted.
    fn names(&mut self) -> Result<Vec<String>> {
        let mut custom = self.file_names()?;
        if let Some(store) = self.store.as_mut() {
            // A locked keyring still lists the known names.
            if let Ok(names) = store.names() {
                custom.extend(names);
            }
        }
        let mut names: Vec<String> = KNOWN.iter().map(|spec| spec.0.to_owned()).collect();
        names.extend(custom.into_iter().filter(|name| known(name).is_none() && is_name(name)));
        Ok(names)
    }

    /// `status`: what the Keys screen shows, never a value.
    pub fn status(&mut self) -> Result<Value> {
        let mut rows = Vec::new();
        let (mut count, mut in_files) = (0, 0);
        for name in self.names()? {
            if name.starts_with(ACCOUNT_PREFIX) {
                // Listed per employer in Settings → Apply → Accounts; here only how many there
                // are and where they live.
                count += 1;
                match self.read_file(&name) {
                    Ok(found) => in_files += found.is_some() as u32,
                    Err(Error::Keys(..)) => {}
                    Err(error) => return Err(error),
                }
                continue;
            }
            let spec = known(&name);
            let (found, error) = match self.lookup(&name) {
                Ok(found) => (found, None),
                Err(Error::Keys(_, message)) => (None, Some(message)),
                Err(error) => return Err(error),
            };
            let in_file = match self.read_file(&name) {
                Ok(found) => found.is_some(),
                Err(Error::Keys(..)) => false,
                Err(error) => return Err(error),
            };
            let mut row = Map::new();
            row.insert("name".into(), json!(name));
            row.insert("label".into(), json!(spec.map_or(name.as_str(), |spec| spec.1)));
            row.insert("env".into(), json!(env_name(&name)));
            row.insert("custom".into(), json!(spec.is_none()));
            row.insert("set".into(), json!(found.is_some()));
            row.insert("backend".into(), json!(found.as_ref().map(|found| found.1)));
            row.insert("in_file".into(), json!(in_file));
            row.insert("error".into(), json!(error));
            rows.push(Value::Object(row));
        }
        let keyring = self.store.is_some();
        let mut status = Map::new();
        status.insert("keys".into(), Value::Array(rows));
        status.insert("keyring".into(), json!(keyring));
        status.insert("keyring_label".into(), json!(keyring.then_some(KEYRING_LABEL)));
        status.insert("store".into(), json!(if keyring { "keyring" } else { "file" }));
        let mut accounts = Map::new();
        accounts.insert("count".into(), json!(count));
        accounts.insert("in_file".into(), json!(in_files));
        status.insert("account_passwords".into(), Value::Object(accounts));
        status.insert("warnings".into(), json!(if in_files > 0 { vec![ACCOUNT_FILE_WARNING] } else { vec![] }));
        Ok(Value::Object(status))
    }
}

// The tests set and read Unix modes.
#[cfg(all(test, unix))]
mod tests {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    use super::*;

    const VALUE: &str = "synthetic-key-value-0123456789";

    struct Workspace(PathBuf);

    impl Workspace {
        fn new(tag: &str) -> Self {
            let mut random = [0u8; 6];
            getrandom::fill(&mut random).unwrap();
            let path = std::env::temp_dir().join(format!("rika-keys-{tag}-{}", hex::encode(random)));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }

        fn keys(&self) -> Keys<'static> {
            Keys { root: self.0.clone(), store: None, environment: None }
        }

        fn private_file(&self, name: &str, value: &str, mode: u32) -> PathBuf {
            let path = self.keys().file_path(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, value).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(mode)).unwrap();
            path
        }
    }

    impl Drop for Workspace {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn text(value: &str) -> Value {
        Value::String(value.into())
    }

    fn mode(path: &Path) -> u32 {
        fs::metadata(path).unwrap().mode() & 0o777
    }

    #[test]
    fn names_are_canonical_and_values_checked_like_python() {
        assert_eq!(canonical(&text(" my_tool_key\u{3000}")), Ok("MY_TOOL_KEY".into()));
        assert_eq!(canonical(&text("ﬀ")), Ok("FF".into()));
        for bad in ["", "1KEY", "MY-KEY", "A B", "_X", &"X".repeat(65)] {
            assert_eq!(canonical(&text(bad)), Err(refuse(400, "Key names use A-Z, 0-9 and _ and start with a letter")));
        }
        assert_eq!(canonical(&json!(42)), Err(refuse(400, "A key name is required")));
        assert_eq!(check_value(&text("\u{2003}abc\n")), Ok("abc"));
        for bad in ["", "two words", "line\nbreak", &"x".repeat(4097), "é"] {
            assert!(check_value(&text(bad)).is_err());
        }
        assert_eq!(check_value(&Value::Null), Err(refuse(400, "Enter the key as text")));
    }

    #[test]
    fn file_writes_are_owner_only_and_atomic_and_legacy_openrouter_is_kept() {
        let workspace = Workspace::new("write");
        let mut keys = workspace.keys();
        keys.set(&text("dummy"), &text(VALUE)).unwrap();
        let path = workspace.0.join(".private/keys/DUMMY");
        assert_eq!(fs::read_to_string(&path).unwrap(), VALUE);
        assert_eq!((mode(&path), mode(&workspace.0.join(".private")), mode(path.parent().unwrap())), (0o600, 0o700, 0o700));
        let entries: Vec<_> = fs::read_dir(path.parent().unwrap()).unwrap().map(|entry| entry.unwrap().file_name()).collect();
        assert_eq!(entries, ["DUMMY"]);
        keys.set(&text("OPENROUTER"), &text(VALUE)).unwrap();
        assert_eq!(fs::read_to_string(workspace.0.join(".private/openrouter-key")).unwrap(), VALUE);
        assert_eq!(keys.lookup("OPENROUTER"), Ok(Some((VALUE.into(), "file"))));
        assert_eq!(keys.remove(&text("dummy")), Ok(true));
        assert_eq!(keys.remove(&text("dummy")), Ok(false));
        assert!(!path.exists());
    }

    #[test]
    fn unsafe_files_are_refused_without_their_value() {
        let workspace = Workspace::new("modes");
        workspace.private_file("BRAVE", VALUE, 0o640);
        assert_eq!(workspace.keys().lookup("BRAVE"), Err(refuse(409, "Private key files must be owner-only (chmod 600)")));
        workspace.private_file("USAJOBS", "caf\u{e9}", 0o600);
        assert_eq!(workspace.keys().lookup("USAJOBS"), Err(refuse(409, "A private key file is not plain text")));
        fs::create_dir_all(workspace.0.join(".private/keys/ANTHROPIC")).unwrap();
        assert_eq!(workspace.keys().lookup("ANTHROPIC"), Err(refuse(409, "A private key path is not a regular file")));
        workspace.private_file("SPACES", " \x1c\n ", 0o600);
        assert_eq!(workspace.keys().lookup("SPACES"), Ok(None));
        let long = format!("{VALUE}{}", "y".repeat(9000));
        workspace.private_file("LONG", &long, 0o600);
        assert_eq!(workspace.keys().read_file("LONG").unwrap().unwrap().len(), 8192);
    }

    #[test]
    fn symbolic_links_are_refused_for_reading_and_writing() {
        for link in ["file", "keys-dir", "private-dir"] {
            let workspace = Workspace::new("links");
            let outside = Workspace::new("outside");
            let target = outside.0.join("DUMMY");
            fs::write(&target, VALUE).unwrap();
            fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).unwrap();
            let private = workspace.0.join(".private");
            match link {
                "private-dir" => std::os::unix::fs::symlink(&outside.0, &private).unwrap(),
                "keys-dir" => {
                    DirBuilder::new().mode(0o700).create(&private).unwrap();
                    std::os::unix::fs::symlink(&outside.0, private.join("keys")).unwrap();
                }
                _ => {
                    fs::create_dir_all(private.join("keys")).unwrap();
                    std::os::unix::fs::symlink(&target, private.join("keys/DUMMY")).unwrap();
                }
            }
            let refused = refuse(409, "Private key storage must not be a symbolic link");
            let mut keys = workspace.keys();
            assert_eq!(keys.lookup("DUMMY").unwrap_err(), refused);
            assert_eq!(keys.set(&text("DUMMY"), &text("synthetic-other")).unwrap_err(), refused);
            assert_eq!(keys.remove(&text("DUMMY")).unwrap_err(), refused);
            assert_eq!(fs::read_to_string(&target).unwrap(), VALUE);
        }
    }

    #[test]
    fn environment_is_the_last_source_and_never_for_account_passwords() {
        let workspace = Workspace::new("env");
        let environment = HashMap::from([
            ("ANTHROPIC_API_KEY".to_owned(), format!(" {VALUE} ")),
            ("APPLY_PW_ACME".to_owned(), VALUE.to_owned()),
            ("DUMMY".to_owned(), VALUE.to_owned()),
        ]);
        let mut keys = Keys { environment: Some(&environment), ..workspace.keys() };
        assert_eq!(keys.lookup("ANTHROPIC"), Ok(Some((VALUE.into(), "env"))));
        assert_eq!(keys.lookup("DUMMY"), Ok(Some((VALUE.into(), "env"))));
        assert_eq!(keys.lookup("APPLY_PW_ACME"), Ok(None));
        workspace.private_file("ANTHROPIC", "synthetic-file", 0o600);
        assert_eq!(keys.lookup("ANTHROPIC"), Ok(Some(("synthetic-file".into(), "file"))));
        assert_eq!(Keys { environment: None, ..workspace.keys() }.lookup("DUMMY"), Ok(None));
    }

    #[derive(Default)]
    struct Fake {
        items: std::rc::Rc<std::cell::RefCell<HashMap<String, String>>>,
        fail: bool,
    }

    impl Keyring for Fake {
        fn get(&mut self, name: &str) -> Result<Option<String>, Fault> {
            if self.fail { Err(Fault) } else { Ok(self.items.borrow().get(name).cloned()) }
        }
        fn set(&mut self, name: &str, value: &str) -> Result<(), Fault> {
            if self.fail { return Err(Fault) }
            self.items.borrow_mut().insert(name.into(), value.into());
            Ok(())
        }
        fn delete(&mut self, name: &str) -> Result<bool, Fault> {
            if self.fail { Err(Fault) } else { Ok(self.items.borrow_mut().remove(name).is_some()) }
        }
        fn names(&mut self) -> Result<Vec<String>, Fault> {
            if self.fail { Err(Fault) } else { Ok(self.items.borrow().keys().cloned().collect()) }
        }
    }

    #[test]
    fn status_lists_known_then_sorted_custom_and_counts_account_passwords() {
        let workspace = Workspace::new("status");
        let items = std::rc::Rc::new(std::cell::RefCell::new(HashMap::from([("ZED_TOOL".to_owned(), VALUE.to_owned()), ("bad".to_owned(), VALUE.to_owned())])));
        workspace.private_file("DUMMY", VALUE, 0o600);
        workspace.private_file("APPLY_PW_ACME", VALUE, 0o600);
        workspace.private_file("BRAVE", VALUE, 0o644);
        let mut keys = Keys { store: Some(Box::new(Fake { items: items.clone(), fail: false })), ..workspace.keys() };
        let status = keys.status().unwrap();
        assert!(!status.to_string().contains(VALUE));
        let names: Vec<_> = status["keys"].as_array().unwrap().iter().map(|row| row["name"].as_str().unwrap().to_owned()).collect();
        assert_eq!(names, ["OPENROUTER", "ANTHROPIC", "USAJOBS", "BRAVE", "GITHUB", "DUMMY", "ZED_TOOL"]);
        assert_eq!(status["keys"][3]["error"], "Private key files must be owner-only (chmod 600)");
        assert_eq!(status["keys"][5]["backend"], "file");
        assert_eq!(status["keys"][6]["backend"], "keyring");
        assert_eq!(status["account_passwords"], json!({"count": 1, "in_file": 1}));
        assert_eq!(status["warnings"], json!([ACCOUNT_FILE_WARNING]));
        assert_eq!((status["keyring_label"].as_str(), status["store"].as_str()), (Some(KEYRING_LABEL), Some("keyring")));

        keys.move_to_keyring(&text("dummy")).unwrap();
        assert_eq!(items.borrow().get("DUMMY").map(String::as_str), Some(VALUE));
        assert!(!workspace.0.join(".private/keys/DUMMY").exists());
        assert_eq!(keys.move_to_keyring(&text("dummy")), Err(refuse(404, "That key has no file copy to move")));
        let mut broken = Keys { store: Some(Box::new(Fake { items, fail: true })), ..workspace.keys() };
        assert_eq!(broken.set(&text("DUMMY"), &text(VALUE)), Err(refuse(503, "The OS keyring refused the key (is it locked?)")));
        assert_eq!(broken.remove(&text("DUMMY")), Err(refuse(503, "The OS keyring refused the removal (is it locked?)")));
        assert_eq!(broken.status().unwrap()["keys"][0]["error"], "The OS keyring did not answer (is it locked?)");
        assert_eq!(workspace.keys().move_to_keyring(&text("DUMMY")), Err(refuse(409, "No OS keyring is available")));
    }
}
