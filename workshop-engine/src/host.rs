//! Host-side operations: validate a request, run the sandbox stages, verify every artifact.

use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::discover::{bundle, bundle_mount, executable, unavailable};
use crate::sandbox::{artifact, run};
use crate::{Failure, Outcome};

const MAX_ARTIFACT: u64 = 16 * 1024 * 1024;
const MAX_RESULT: u64 = 4 * 1024 * 1024;
const STAGE: Duration = Duration::from_secs(95);

fn string<'a>(body: &'a Value, key: &str) -> Outcome<&'a str> {
    body.get(key).and_then(Value::as_str).ok_or_else(|| Failure::new(400, format!("Engine request needs {key}")))
}

fn only(body: &Value, allowed: &[&str]) -> Outcome<()> {
    let unknown = body.as_object().into_iter().flat_map(Map::keys).any(|key| !allowed.contains(&key.as_str()));
    if unknown { Err(Failure::new(400, "Unknown engine request fields")) } else { Ok(()) }
}

/// The job folder: an existing absolute directory, used as given (never created here).
fn folder(body: &Value) -> Outcome<PathBuf> {
    let path = PathBuf::from(string(body, "folder")?);
    if !path.is_absolute() || !path.is_dir() {
        return Err(Failure::new(400, "Job folder must be an existing absolute directory"));
    }
    path.canonicalize().map_err(|_| Failure::new(400, "Job folder is unreadable"))
}

/// A project-relative file path: no absolute parts, parent hops or control characters.
fn relative(value: &str) -> Outcome<&str> {
    let path = Path::new(value);
    let plain = !value.is_empty()
        && value.len() <= 512
        && !value.chars().any(|c| c.is_control() || c == '\\' || c == ':')
        && path.components().all(|c| matches!(c, Component::Normal(_)));
    if plain { Ok(value) } else { Err(Failure::new(400, "Files must have safe, relative project paths")) }
}

fn sha256(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}

fn result_json(folder: &Path) -> Outcome<Value> {
    serde_json::from_slice(&artifact(&folder.join("result.json"), MAX_RESULT)?)
        .map_err(|_| Failure::new(422, "Sandbox worker produced an unreadable result"))
}

/// The compiler command for a TeX or Typst project, as run inside the sandbox.
fn engine_command(engine: &str, binary: &Path, entrypoint: &str) -> Outcome<Vec<String>> {
    let binary = binary.to_string_lossy().into_owned();
    let source = format!("/job/project/{entrypoint}");
    Ok(match engine {
        "typst" => vec![binary, "compile".into(), "--root".into(), "/job/project".into(), source, "/job/output.pdf".into()],
        "tectonic" => {
            let bundle = bundle().ok_or_else(|| Failure::new(503, unavailable("tectonic").unwrap_or_default()))?;
            vec![binary, "--untrusted".into(), "--only-cached".into(), "--bundle".into(), bundle_mount(&bundle),
                 "--outdir".into(), "/job/out".into(), source]
        }
        // Paranoid TeX writes refuse absolute outputs; the worker already runs in /job/project
        // and TEXMFOUTPUT is /job/out.
        "pdflatex" | "lualatex" => vec![binary, "-no-shell-escape".into(), "-interaction=nonstopmode".into(),
                                        "-halt-on-error".into(), "-output-directory=/job/out".into(), entrypoint.into()],
        _ => return Err(Failure::new(400, "Unsupported document engine")),
    })
}

/// Compile a prepared job (`project/` holds the files; HTML also has `frozen.html`), then
/// inspect and rasterize the PDF. Leaves `output.pdf` and `page-N.png` in the folder.
pub fn compile(body: &Value) -> Outcome<Value> {
    only(body, &["folder", "format", "engine", "entrypoint"])?;
    let folder = folder(body)?;
    let format = string(body, "format")?;
    let engine = string(body, "engine")?;
    let entrypoint = relative(string(body, "entrypoint")?)?;
    let expected: &[&str] = match format {
        "html" => &["chromium"],
        "typst" => &["typst"],
        "latex" => &["tectonic", "pdflatex", "lualatex"],
        _ => return Err(Failure::new(400, "Format must be html, typst, or latex")),
    };
    if !expected.contains(&engine) {
        return Err(Failure::new(400, format!("{format} documents cannot use the {engine} engine")));
    }
    if let Some(reason) = unavailable(engine) {
        return Err(Failure::new(503, reason));
    }
    let binary = executable(engine).ok_or_else(|| Failure::new(503, "Compiler is unavailable"))?;
    if format == "html" {
        if !folder.join("frozen.html").is_file() {
            return Err(Failure::new(400, "HTML jobs need frozen.html"));
        }
        run(&folder, &json!({"mode": "html", "binary": binary}), STAGE)?;
    } else {
        std::fs::create_dir_all(folder.join("out")).map_err(|e| Failure::new(500, e.to_string()))?;
        let command = engine_command(engine, &binary, entrypoint)?;
        run(&folder, &json!({"mode": "engine", "command": command}), STAGE)?;
        if engine != "typst" {
            let stem = Path::new(entrypoint).file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
            let pdf = artifact(&folder.join("out").join(format!("{stem}.pdf")), MAX_ARTIFACT)?;
            std::fs::write(folder.join("output.pdf"), pdf).map_err(|e| Failure::new(500, e.to_string()))?;
        }
    }
    run(&folder, &json!({"mode": "pdf", "input": "output.pdf", "raster": true}), STAGE)?;
    let inspection = result_json(&folder)?;
    let pdf = artifact(&folder.join("output.pdf"), MAX_ARTIFACT)?;
    if !pdf.starts_with(b"%PDF-") {
        return Err(Failure::new(422, "Compiler did not produce a PDF"));
    }
    let pages = inspection["pages"].as_u64().filter(|n| (1..=40).contains(n))
        .ok_or_else(|| Failure::new(422, "Sandbox worker reported an invalid page count"))?;
    let mut hashes = Map::new();
    for page in 1..=pages {
        let name = format!("page-{page}.png");
        let raw = artifact(&folder.join(&name), MAX_ARTIFACT)?;
        if !raw.starts_with(b"\x89PNG\r\n\x1a\n") {
            return Err(Failure::new(422, "Raster worker did not produce PNG"));
        }
        hashes.insert(name, sha256(&raw).into());
    }
    Ok(json!({"pages": pages, "pdf_hash": sha256(&pdf), "page_hashes": hashes, "checks": inspection["checks"]}))
}

/// Text (and optionally OCR) of an uploaded PDF the caller wrote to `folder/<input>`.
pub fn inspect(body: &Value) -> Outcome<Value> {
    only(body, &["folder", "input", "ocr"])?;
    let folder = folder(body)?;
    let input = string(body, "input")?;
    if relative(input)?.contains('/') {
        return Err(Failure::new(400, "Inspected input must be a job file name"));
    }
    let ocr = body.get("ocr").and_then(Value::as_bool).unwrap_or(false);
    let tesseract = if ocr { executable("tesseract") } else { None };
    run(&folder, &json!({"mode": "pdf", "input": input, "ocr": ocr, "tesseract": tesseract}), STAGE)?;
    let result = result_json(&folder)?;
    Ok(json!({"pages": result["pages"], "checks": result["checks"]}))
}

/// Paragraph text of an uploaded DOCX the caller wrote to `folder/input.docx`.
pub fn docx(body: &Value) -> Outcome<Value> {
    only(body, &["folder"])?;
    let folder = folder(body)?;
    run(&folder, &json!({"mode": "docx"}), STAGE)?;
    let result = result_json(&folder)?;
    Ok(json!({"text": result["text"]}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn project_paths_cannot_escape_or_name_absolute_files() {
        for bad in ["../x.tex", "/etc/passwd", "a/../../b", "", "a\\b", "c:x", "a/\u{0}b"] {
            assert!(relative(bad).is_err(), "{bad:?} accepted");
        }
        assert_eq!(relative("sections/main.tex").unwrap(), "sections/main.tex");
    }

    #[test]
    fn tex_engines_run_offline_without_shell_escape() {
        let command = engine_command("pdflatex", Path::new("/bin/pdflatex"), "main.tex").unwrap();
        assert!(command.contains(&"-no-shell-escape".into()));
        assert_eq!(command.last().unwrap(), "main.tex");
        let typst = engine_command("typst", Path::new("/bin/typst"), "r.typ").unwrap();
        assert_eq!(typst[2..4], ["--root", "/job/project"]);
    }

    #[test]
    fn requests_with_unknown_fields_or_mismatched_engines_are_refused() {
        let base = std::env::temp_dir();
        let extra = json!({"folder": base, "format": "typst", "engine": "typst", "entrypoint": "r.typ", "shell": true});
        assert_eq!(compile(&extra).unwrap_err().status, 400);
        let mismatch = json!({"folder": base, "format": "typst", "engine": "pdflatex", "entrypoint": "r.typ"});
        assert_eq!(compile(&mismatch).unwrap_err().status, 400);
        let relative_folder = json!({"folder": "job", "format": "typst", "engine": "typst", "entrypoint": "r.typ"});
        assert_eq!(compile(&relative_folder).unwrap_err().status, 400);
    }
}
