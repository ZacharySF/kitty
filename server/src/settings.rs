//! Where the workspace is: `kitty.settings.find_project_root` and the database path of
//! `Settings.load`, so rika-server can migrate the database before the worker starts.
//!
//! The worker resolves the same root (the front passes `--root` on, or the desktop shell
//! gives both the same folder) and refuses a database it finds behind the files, so a
//! disagreement fails closed instead of opening an unmigrated database.

use std::collections::HashMap;
use std::path::{Component, Path, PathBuf};

/// Markers of a project root (`kitty.settings.ROOT_MARKERS`).
const ROOT_MARKERS: [&str; 3] = ["config/build.json", "config/modules.yaml", "config/tracks.yaml"];

/// The resolved workspace: its root and its SQLite database.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Paths {
    pub root: PathBuf,
    pub db_path: PathBuf,
}

/// `Path.expanduser`: a leading `~` or `~/` is the home folder.
fn expand_user(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if text == "~" || text.starts_with("~/") {
        if let Some(home) = std::env::var_os("HOME") {
            return PathBuf::from(home).join(text.trim_start_matches('~').trim_start_matches('/'));
        }
    }
    path.to_path_buf()
}

/// `Path.resolve()` (not strict): absolute, symbolic links followed as far as the path exists,
/// `.` and `..` folded in the part that does not.
pub fn resolve(path: &Path) -> PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("/")).join(path)
    };
    let mut resolved = PathBuf::from("/");
    let mut components = absolute.components().peekable();
    while let Some(component) = components.next() {
        match component {
            Component::RootDir | Component::Prefix(_) | Component::CurDir => {}
            Component::ParentDir => {
                resolved.pop();
            }
            Component::Normal(part) => {
                resolved.push(part);
                if let Ok(real) = std::fs::canonicalize(&resolved) {
                    resolved = real;
                }
            }
        }
    }
    resolved
}

/// `find_project_root`: `$KITTY_HOME`, else the nearest folder at or above the current one
/// holding a root marker, else the current folder.
pub fn find_project_root() -> PathBuf {
    if let Some(home) = std::env::var_os("KITTY_HOME").filter(|home| !home.is_empty()) {
        return resolve(&expand_user(Path::new(&home)));
    }
    let here = resolve(&std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    here.ancestors()
        .find(|candidate| ROOT_MARKERS.iter().any(|marker| candidate.join(marker).exists()))
        .map(Path::to_path_buf)
        .unwrap_or(here)
}

/// `Settings.load(root=root)`'s root and database: `$KITTY_DB` (from the environment, else
/// the root's `.env`, as `load_dotenv` leaves set variables alone), relative to the root,
/// else `data/apply.db`.
pub fn load(root: Option<&Path>) -> Paths {
    let root = resolve(&root.map(expand_user).unwrap_or_else(find_project_root));
    let configured = match std::env::var_os("KITTY_DB") {
        Some(value) => Some(PathBuf::from(value)),
        None => std::fs::read_to_string(root.join(".env"))
            .ok()
            .and_then(|text| dotenv(&text).remove("KITTY_DB").flatten())
            .map(PathBuf::from),
    };
    let db = configured.filter(|path| !path.as_os_str().is_empty()).unwrap_or_else(|| PathBuf::from("data/apply.db"));
    let db_path = if db.is_absolute() { db } else { root.join(db) };
    Paths { root, db_path }
}

fn is_space(c: char) -> bool {
    c.is_whitespace() && c != '\r' && c != '\n'
}

/// `dotenv_values`: python-dotenv's bindings with `${NAME}` / `${NAME:-default}` filled from
/// the environment first, then the file's earlier values. A line it cannot parse is skipped.
pub fn dotenv(text: &str) -> HashMap<String, Option<String>> {
    let mut values: HashMap<String, Option<String>> = HashMap::new();
    for (key, raw) in bindings(text) {
        let value = raw.map(|raw| interpolate(&raw, &values));
        values.insert(key, value);
    }
    values
}

fn interpolate(value: &str, earlier: &HashMap<String, Option<String>>) -> String {
    let mut out = String::new();
    let mut rest = value;
    while let Some(start) = rest.find("${") {
        let Some(length) = rest[start + 2..].find('}') else { break };
        let inner = &rest[start + 2..start + 2 + length];
        let (name, default) = match inner.find(":-") {
            Some(split) if !inner[..split].contains(':') => (&inner[..split], Some(&inner[split + 2..])),
            _ if inner.contains(':') => {
                // Not a variable python-dotenv recognises: kept as written.
                out.push_str(&rest[..start + 3 + length]);
                rest = &rest[start + 3 + length..];
                continue;
            }
            _ => (inner, None),
        };
        out.push_str(&rest[..start]);
        let found = std::env::var(name).ok().or_else(|| earlier.get(name).cloned().flatten());
        out.push_str(&found.or(default.map(str::to_owned)).unwrap_or_default());
        rest = &rest[start + 3 + length..];
    }
    out.push_str(rest);
    out
}

fn escapes(value: &str, all: bool) -> String {
    let mut out = String::new();
    let mut chars = value.chars();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        let next = chars.clone().next();
        let decoded = match next {
            Some('\\') => Some('\\'),
            Some('\'') => Some('\''),
            Some('"') if all => Some('"'),
            Some('a') if all => Some('\x07'),
            Some('b') if all => Some('\x08'),
            Some('f') if all => Some('\x0c'),
            Some('n') if all => Some('\n'),
            Some('r') if all => Some('\r'),
            Some('t') if all => Some('\t'),
            Some('v') if all => Some('\x0b'),
            _ => None,
        };
        match decoded {
            Some(decoded) => {
                chars.next();
                out.push(decoded);
            }
            None => out.push('\\'),
        }
    }
    out
}

/// A quoted value starting at `text[0]` (the quote): its body and the rest after the closing quote.
fn quoted(text: &str, quote: char) -> Option<(&str, &str)> {
    let mut escaped = false;
    for (index, c) in text.char_indices().skip(1) {
        if escaped {
            escaped = false;
        } else if c == '\\' {
            escaped = true;
        } else if c == quote {
            return Some((&text[1..index], &text[index + 1..]));
        }
    }
    None
}

/// python-dotenv's parser (`dotenv.parser.parse_stream`), keys and raw values.
fn bindings(text: &str) -> Vec<(String, Option<String>)> {
    let mut found = Vec::new();
    let mut rest = text;
    loop {
        rest = rest.trim_start();
        if rest.is_empty() {
            return found;
        }
        match binding(rest) {
            Some((key, value, after)) => {
                if let Some(key) = key {
                    found.push((key, value));
                }
                rest = after;
            }
            // `_rest_of_line`: an unparsable line is skipped.
            None => rest = rest.find(['\n', '\r']).map(|end| &rest[end + 1..]).unwrap_or(""),
        }
    }
}

type Binding<'a> = (Option<String>, Option<String>, &'a str);

fn binding(text: &str) -> Option<Binding<'_>> {
    let mut rest = text;
    if let Some(after) = rest.strip_prefix("export") {
        let trimmed = after.trim_start_matches(is_space);
        if trimmed.len() < after.len() {
            rest = trimmed;
        }
    }
    let key = if rest.starts_with('#') {
        None
    } else if rest.starts_with('\'') {
        let end = rest[1..].find('\'').filter(|&end| end > 0)?;
        let key = rest[1..1 + end].to_owned();
        rest = &rest[end + 2..];
        Some(key)
    } else {
        let end = rest.find(|c: char| c == '=' || c == '#' || c.is_whitespace()).unwrap_or(rest.len());
        if end == 0 {
            return None;
        }
        let key = rest[..end].to_owned();
        rest = &rest[end..];
        Some(key)
    };
    rest = rest.trim_start_matches(is_space);
    let mut value = None;
    if let Some(after) = rest.strip_prefix('=') {
        rest = after.trim_start_matches(is_space);
        value = Some(if rest.starts_with('\'') {
            let (body, after) = quoted(rest, '\'')?;
            rest = after;
            escapes(body, false)
        } else if rest.starts_with('"') {
            let (body, after) = quoted(rest, '"')?;
            rest = after;
            escapes(body, true)
        } else {
            let end = rest.find(['\r', '\n']).unwrap_or(rest.len());
            let line = &rest[..end];
            rest = &rest[end..];
            // `re.sub(r"\s+#.*", "", part).rstrip()`
            let cut = line.char_indices().find(|&(index, c)| {
                c.is_whitespace() && line[index..].trim_start().starts_with('#')
            });
            match cut {
                Some((index, _)) => line[..index].trim_end().to_owned(),
                None => line.trim_end().to_owned(),
            }
        });
    }
    // `_comment`, then `_end_of_line`.
    rest = rest.trim_start_matches(is_space);
    if rest.starts_with('#') {
        rest = rest.find(['\r', '\n']).map(|end| &rest[end..]).unwrap_or("");
    }
    if rest.starts_with("\r\n") {
        rest = &rest[2..];
    } else if rest.starts_with(['\r', '\n']) {
        rest = &rest[1..];
    } else if !rest.is_empty() {
        return None;
    }
    Some((key, value, rest))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dotenv_values_as_python_reads_them() {
        let text = "# comment\nexport KITTY_DB = data/web.db # here\nA='x # y'\nB=\"a\\nb\"\nC=${A}/z\nbroken 'line\nD\n";
        let values = dotenv(text);
        assert_eq!(values["KITTY_DB"].as_deref(), Some("data/web.db"));
        assert_eq!(values["A"].as_deref(), Some("x # y"));
        assert_eq!(values["B"].as_deref(), Some("a\nb"));
        assert_eq!(values["C"].as_deref(), Some("x # y/z"));
        assert_eq!(values["D"], None);
        assert!(!values.contains_key("broken"));
    }

    #[test]
    fn resolve_folds_the_missing_part() {
        assert_eq!(resolve(Path::new("/nonexistent-kitty/a/../b/./c")), PathBuf::from("/nonexistent-kitty/b/c"));
    }
}
