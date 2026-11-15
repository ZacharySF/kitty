//! Which local runtimes exist: compilers, bubblewrap, Tesseract and the Tectonic bundle.

use std::ffi::CString;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// Program names searched on PATH per runtime; `KITTY_<NAME>` overrides with an absolute path.
pub const RUNTIMES: [(&str, &[&str]); 7] = [
    ("chromium", &["chromium", "chromium-browser", "google-chrome"]),
    ("typst", &["typst"]),
    ("tectonic", &["tectonic"]),
    ("pdflatex", &["pdflatex"]),
    ("lualatex", &["lualatex"]),
    ("bwrap", &["bwrap"]),
    ("tesseract", &["tesseract"]),
];

const ENGINES: [(&str, &str); 5] = [
    ("chromium", "HTML / Chromium"),
    ("typst", "Typst"),
    ("tectonic", "LaTeX / Tectonic"),
    ("pdflatex", "pdfLaTeX"),
    ("lualatex", "LuaLaTeX"),
];

fn is_executable(path: &Path) -> bool {
    let Ok(raw) = CString::new(path.as_os_str().as_bytes()) else { return false };
    // SAFETY: `raw` is a valid NUL-terminated path for the duration of the call.
    path.is_file() && unsafe { libc::access(raw.as_ptr(), libc::X_OK) } == 0
}

/// The executable for a runtime. An explicit `KITTY_<NAME>` must be an absolute executable
/// file; a configured but unusable value never falls back to PATH.
pub fn executable(name: &str) -> Option<PathBuf> {
    if let Ok(configured) = std::env::var(format!("KITTY_{}", name.to_uppercase())) {
        if !configured.is_empty() {
            let path = PathBuf::from(configured);
            return (path.is_absolute() && is_executable(&path)).then_some(path);
        }
    }
    let candidates = RUNTIMES.iter().find(|(id, _)| *id == name)?.1;
    let search = std::env::var_os("PATH")?;
    candidates.iter().find_map(|program| {
        std::env::split_paths(&search)
            .filter(|directory| directory.is_absolute())
            .map(|directory| directory.join(program))
            .find(|path| is_executable(path))
    })
}

/// A provisioned local Tectonic bundle: a `.ttb`/`.zip` file, or a complete bundle directory
/// (it carries `SHA256SUM`). Runtime downloads stay disabled inside the sandbox.
pub fn bundle() -> Option<PathBuf> {
    let value = std::env::var("KITTY_TECTONIC_BUNDLE").ok()?;
    let path = Path::new(&value);
    if !path.is_absolute() {
        return None;
    }
    let path = path.canonicalize().ok()?;
    let file = path.is_file()
        && matches!(path.extension().and_then(|e| e.to_str()), Some("ttb" | "zip"));
    (file || (path.is_dir() && path.join("SHA256SUM").is_file())).then_some(path)
}

/// Where the bundle appears inside the sandbox.
pub fn bundle_mount(bundle: &Path) -> String {
    match bundle.extension().and_then(|e| e.to_str()) {
        Some(extension) if bundle.is_file() => format!("/bundle.{extension}"),
        _ => "/bundle".into(),
    }
}

/// Why an engine cannot run, or `None` when it can.
pub fn unavailable(engine: &str) -> Option<String> {
    if executable("bwrap").is_none() {
        Some("bubblewrap sandbox is required; install bwrap or set KITTY_BWRAP".into())
    } else if executable(engine).is_none() {
        Some(format!("Install {engine} or set KITTY_{} to its absolute executable path", engine.to_uppercase()))
    } else if engine == "tectonic" && bundle().is_none() {
        Some("Set KITTY_TECTONIC_BUNDLE to a local Tectonic bundle (.ttb/.zip file or package directory); runtime downloads are disabled".into())
    } else {
        None
    }
}

/// Everything a compile's output depends on besides the document: this engine, the compiler
/// binary, the Tectonic bundle and system fonts. A change to any invalidates cached previews.
fn runtime_digest(engine: &str) -> String {
    let mut paths: Vec<PathBuf> = Vec::new();
    paths.extend(std::env::current_exe().ok());
    paths.extend(executable(engine).and_then(|path| path.canonicalize().ok()));
    if engine == "tectonic" {
        paths.extend(bundle());
    }
    for directory in ["/usr/share/fonts", "/usr/local/share/fonts", "/etc/fonts"] {
        let mut found = Vec::new();
        walk(Path::new(directory), &mut found, 0);
        found.sort();
        paths.extend(found);
    }
    let mut hash = Sha256::new();
    for path in paths {
        hash.update(path.as_os_str().as_bytes());
        match std::fs::metadata(&path) {
            Ok(info) => {
                use std::os::unix::fs::MetadataExt;
                hash.update(format!("\0{}\0{}.{}\n", info.len(), info.mtime(), info.mtime_nsec()));
            }
            Err(_) => hash.update(b"\0missing\n"),
        }
    }
    format!("{:x}", hash.finalize())
}

fn walk(directory: &Path, found: &mut Vec<PathBuf>, depth: usize) {
    let Ok(entries) = std::fs::read_dir(directory) else { return };
    for entry in entries.flatten() {
        let Ok(kind) = entry.file_type() else { continue };
        if kind.is_dir() && depth < 8 {
            walk(&entry.path(), found, depth + 1);
        } else if kind.is_file() {
            found.push(entry.path());
        }
    }
}

pub fn capabilities() -> Value {
    let sandbox = executable("bwrap").is_some();
    let engines: Vec<Value> = ENGINES
        .iter()
        .map(|(id, label)| {
            let reason = unavailable(id);
            json!({"id": id, "label": label, "available": reason.is_none(), "reason": reason,
                   "runtime": runtime_digest(id)})
        })
        .collect();
    json!({
        "engines": engines,
        "formats": ["html", "typst", "latex"],
        "sandbox": sandbox,
        "ocr": {"available": sandbox && executable("tesseract").is_some(),
                "reason": "Local Tesseract and its language data plus bubblewrap are required; OCR must be reviewed"},
        "imports": ["source", "zip", "pdf", "docx", "markdown", "json-resume"],
    })
}
