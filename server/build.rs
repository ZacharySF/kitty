//! Compiles the workspace migrations (`src/kitty/db/migrations/NNN_name.sql`, the files the
//! Python runner reads too) into the binary as `MIGRATIONS` (crate::migrations).

use std::fmt::Write as _;
use std::path::PathBuf;

fn main() {
    let folder = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../src/kitty/db/migrations");
    println!("cargo:rerun-if-changed={}", folder.display());
    let mut found = Vec::new();
    for entry in std::fs::read_dir(&folder).expect("the migrations folder is readable") {
        let path = entry.expect("a readable migrations entry").path();
        if path.extension().and_then(|ext| ext.to_str()) != Some("sql") {
            continue;
        }
        println!("cargo:rerun-if-changed={}", path.display());
        let file = path.file_name().and_then(|name| name.to_str()).expect("a UTF-8 migration name").to_owned();
        // `kitty.db.migrations._FILENAME`: ^(\d{3})_([a-z0-9_]+)\.sql$
        let stem = file.strip_suffix(".sql").expect("a .sql file");
        let valid = stem.len() > 4
            && stem.as_bytes()[..3].iter().all(u8::is_ascii_digit)
            && stem.as_bytes()[3] == b'_'
            && stem[4..].bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_');
        assert!(valid, "migration {file} must be named NNN_lower_snake.sql");
        let version: u32 = stem[..3].parse().expect("three digits");
        found.push((version, stem[4..].to_owned(), path.canonicalize().expect("a migration path")));
    }
    found.sort();
    let mut code = String::from("pub static MIGRATIONS: &[Migration] = &[\n");
    for (version, name, path) in &found {
        writeln!(code, "    Migration {{ version: {version}, name: {name:?}, sql: include_str!({:?}) }},", path.display().to_string())
            .expect("writing to a String");
    }
    code.push_str("];\n");
    let out = PathBuf::from(std::env::var("OUT_DIR").expect("cargo sets OUT_DIR")).join("migrations.rs");
    std::fs::write(out, code).expect("OUT_DIR is writable");
}
