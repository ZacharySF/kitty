//! Build files (`kitty.core.builds`): the reader, ported with Python-identical results, the
//! store, the SQLite projection, sharing, and the live build's routes and watcher.

mod clock;
mod css;
pub mod document;
mod failure;
mod fetch;
pub mod json;
mod project;
mod regex;
mod routes;
mod schema;
pub mod service;
mod share;
mod store;
mod tasks;
mod unicode;
pub mod value;

pub use routes::route;

use std::io::{Read, Write};

/// `rika-server check-build`: test-only. Reads a build file's text on stdin (UTF-8; lone
/// surrogates allowed, as Python's `surrogatepass` writes them) and prints what the reader
/// makes of it: `{"ok", "build", "problems"}`, or `{"raises"}` where Python's `check` raises.
/// tests/test_build_reader_parity.py compares it with `document.check`.
pub fn check_build_command() -> i32 {
    let mut bytes = Vec::new();
    if std::io::stdin().read_to_end(&mut bytes).is_err() {
        eprintln!("check-build: cannot read stdin");
        return 2;
    }
    let Some(source) = decode(&bytes) else {
        eprintln!("check-build: stdin is not UTF-8");
        return 2;
    };
    let mut out = String::new();
    match document::check(&source) {
        Err(raised) => out.push_str(&format!("{{\"raises\":\"{}\"}}", raised.name())),
        Ok(checked) => {
            out.push_str(&format!("{{\"ok\":{},\"build\":", checked.build.is_some()));
            match &checked.build {
                Some(build) => value::dump(build, &mut out),
                None => out.push_str("null"),
            }
            out.push_str(",\"problems\":[");
            for (i, problem) in checked.problems.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                document::push_problem_json(problem, &mut out);
            }
            out.push_str("]}");
        }
    }
    out.push('\n');
    let mut stdout = std::io::stdout().lock();
    if stdout.write_all(out.as_bytes()).and_then(|()| stdout.flush()).is_err() {
        return 2;
    }
    0
}

/// UTF-8 that may encode surrogate code points (each on its own, as `surrogatepass` does).
pub(crate) fn decode(bytes: &[u8]) -> Option<Vec<u32>> {
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let b = bytes[i];
        let (len, init) = match b {
            0x00..=0x7f => (1, u32::from(b)),
            0xc2..=0xdf => (2, u32::from(b & 0x1f)),
            0xe0..=0xef => (3, u32::from(b & 0x0f)),
            0xf0..=0xf4 => (4, u32::from(b & 0x07)),
            _ => return None,
        };
        let tail = bytes.get(i + 1..i + len)?;
        let mut c = init;
        for &t in tail {
            if t & 0xc0 != 0x80 {
                return None;
            }
            c = c << 6 | u32::from(t & 0x3f);
        }
        let shortest = match len {
            3 => c >= 0x800,
            4 => (0x10000..=0x10ffff).contains(&c),
            _ => true,
        };
        if !shortest {
            return None;
        }
        out.push(c);
        i += len;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::document::{check, schema};
    use super::regex::Pattern;
    use super::schema::{ANNOTATIONS, KEYWORDS};
    use super::value::{lossy, text, Value};

    /// Every keyword in the schema is one the validator implements or one that never fails, and
    /// every pattern is one the small regex reader supports.
    #[test]
    fn the_validator_covers_the_whole_schema() {
        fn walk(value: &Value, parent_is_map: bool) {
            match value {
                Value::Dict(node) => {
                    for (key, child) in node.iter() {
                        let name = lossy(key);
                        if !parent_is_map {
                            assert!(
                                KEYWORDS.contains(&name.as_str()) || ANNOTATIONS.contains(&name.as_str()) || name == "$ref",
                                "unsupported schema keyword {name}"
                            );
                            if name == "pattern" {
                                assert!(Pattern::compile(child.as_str().unwrap()).is_some(), "pattern {child:?}");
                            }
                            if name == "patternProperties" {
                                for pattern in child.as_dict().unwrap().keys() {
                                    assert!(Pattern::compile(pattern).is_some());
                                }
                            }
                        }
                        let maps = !parent_is_map && matches!(name.as_str(), "properties" | "$defs" | "patternProperties");
                        let data = !parent_is_map && matches!(name.as_str(), "default" | "examples" | "enum" | "const");
                        if !data {
                            walk(child, maps);
                        }
                    }
                }
                Value::List(items) => items.iter().for_each(|item| walk(item, false)),
                _ => {}
            }
        }
        walk(&Value::Dict(schema().clone()), false);
    }

    #[test]
    fn an_empty_build_means_every_default() {
        let checked = check(&text("{\"schema\": 1}")).unwrap();
        assert!(checked.build.is_some() && checked.problems.is_empty());
        let checked = check(&text("{\"schema\": 1, \"theme\": {\"css\": \"@import 'x';\"}}")).unwrap();
        assert!(checked.build.is_none());
        assert_eq!(lossy(&checked.problems[0].message), "CSS line 1, column 1: @import is not allowed: a build cannot load other stylesheets");
    }

    #[test]
    fn python_dollar_matches_before_a_final_newline() {
        let p = Pattern::compile(&text("^[a-z]{0,3}$")).unwrap();
        assert!(p.search(&text("abc")) && p.search(&text("abc\n")) && !p.search(&text("abc\n\n")) && !p.search(&text("abcd")));
    }
}
