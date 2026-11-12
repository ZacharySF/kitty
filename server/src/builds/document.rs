//! `kitty.core.builds.document`: reading a build file. `check` returns every problem with the
//! line and column of the offending value (or key) and, when there is no error, the normalized
//! build; the results equal the Python module's (tests/test_build_reader_parity.py).

use std::sync::LazyLock;

use super::css;
use super::json::{self, dotted, Failure, Part, Positions};
use super::schema::Validator;
use super::value::{float_repr, int_gt, push, text, Dict, Text, TooDeep, Value};

pub const SCHEMA_VERSION: i128 = 1;
pub const MAX_BYTES: usize = 256 * 1024;
/// The roles rika!'s themes define; their tokens must be hex colours.
const COLOR_TOKENS: [&str; 23] = [
    "void", "panel", "inset", "raised", "line", "lineHot", "ink", "mute", "dim", "structure", "signal", "go",
    "reference", "caution", "alternate", "danger", "graph1", "graph2", "graph3", "graph4", "graph5", "graph6",
    "shadow",
];
/// Stack for one check: values may nest [`json::MAX_DEPTH`] deep, and comparing, printing and
/// dropping them recurses.
const STACK: usize = 256 * 1024 * 1024;

pub struct Problem {
    pub message: Text,
    pub line: usize,
    pub column: usize,
    pub path: Text,
    pub severity: &'static str,
}

pub struct Checked {
    /// The complete build to apply (None when there is any error).
    pub build: Option<Value>,
    pub problems: Vec<Problem>,
}

/// Where Python's `check` raises instead of returning.
#[derive(Debug)]
pub enum Raised {
    /// `RecursionError`: comparing deeply nested list items for `uniqueItems`.
    RecursionError,
    /// `ValueError`: an integer past Python's 4300-digit string conversion limit.
    ValueError,
}

impl Raised {
    pub fn name(&self) -> &'static str {
        match self {
            Raised::RecursionError => "RecursionError",
            Raised::ValueError => "ValueError",
        }
    }
}

impl From<TooDeep> for Raised {
    fn from(_: TooDeep) -> Raised {
        Raised::RecursionError
    }
}

static SCHEMA: LazyLock<Value> = LazyLock::new(|| {
    let source = text(include_str!("../../../src/kitty/core/builds/kitty-build.schema.json"));
    json::parse(&source).ok().expect("the build schema is valid JSON").value
});
static DEFAULTS: LazyLock<Value> = LazyLock::new(|| defaults_of(schema()));

/// The published schema (kitty-build.schema.json), parsed as `json.loads` has it.
pub fn schema() -> &'static Dict {
    SCHEMA.as_dict().expect("the build schema is an object")
}

fn node<'a>(from: &'a Dict, path: &[&str]) -> &'a Dict {
    path.iter().fold(from, |d, key| d.get_str(key).and_then(Value::as_dict).expect("a schema node"))
}

fn defaults_of(node: &Dict) -> Value {
    if let Some(default) = node.get_str("default") {
        return default.clone();
    }
    let is_object = node.get_str("type").and_then(Value::as_str).is_some_and(|t| *t == text("object"));
    if let (true, Some(Value::Dict(properties))) = (is_object, node.get_str("properties")) {
        let mut out = Dict::new();
        for (key, child) in properties.iter() {
            let child = child.as_dict().expect("a subschema");
            if *key != text("$schema") && ["default", "properties", "const"].iter().any(|k| child.get_str(k).is_some()) {
                out.insert(key.clone(), defaults_of(child));
            }
        }
        return Value::Dict(out);
    }
    node.get_str("const").cloned().unwrap_or(Value::Null)
}

/// The complete default build (what `{"schema": 1}` means).
pub fn defaults() -> &'static Dict {
    DEFAULTS.as_dict().expect("the defaults are an object")
}

/// The built-in theme ids (the schema's `theme.base` examples).
pub fn theme_ids() -> &'static [Value] {
    match node(schema(), &["properties", "theme", "properties", "base"]).get_str("examples") {
        Some(Value::List(ids)) => ids,
        _ => &[],
    }
}

fn default_base() -> &'static Value {
    node(defaults(), &["theme"]).get_str("base").expect("a default theme")
}

fn cat(parts: &[&[u32]]) -> Text {
    parts.concat()
}

fn problem(message: Text, (line, column): (usize, usize), path: Text, severity: &'static str) -> Problem {
    Problem { message, line, column, path, severity }
}

fn lone(message: &str, line: usize, column: usize) -> Checked {
    Checked { build: None, problems: vec![problem(text(message), (line, column), Text::new(), "error")] }
}

/// `_schema_problem`.
fn schema_problem(error: super::schema::SchemaError, data: &Dict, positions: &Positions, out: &mut Vec<Problem>) {
    let path = &error.path;
    let mut where_ = dotted(path);
    if where_.is_empty() {
        where_ = text("build");
    }
    if error.keyword == "additionalProperties" {
        for key in error.extras {
            let mut full = path.clone();
            full.push(Part::Key(key.clone()));
            let message = cat(&[&text("unknown key '"), &key, &text("' is ignored")]);
            out.push(problem(message, positions.of(&full, true), dotted(&full), "warning"));
        }
        return;
    }
    let place = positions.of(path, false);
    let is_schema = matches!(&path[..], [Part::Key(k)] if *k == text("schema"));
    if is_schema || (path.is_empty() && error.keyword == "required") {
        let version = if is_schema { data.get_str("schema") } else { None };
        let message = match version {
            Some(v @ Value::Int(i)) if int_gt(v, SCHEMA_VERSION) => format!(
                "this build uses build format {}, made by a newer rika!; this rika! reads format {SCHEMA_VERSION}. Update rika! to use it",
                i.decimal()
            ),
            _ => format!("a build starts with \"schema\": {SCHEMA_VERSION}"),
        };
        out.push(problem(text(&message), place, text("schema"), "error"));
        return;
    }
    if matches!(path.last(), Some(Part::Key(k)) if *k == text("css")) && error.keyword == "anyOf" {
        out.push(problem(text("css must be a string or a list of strings (lines)"), place, where_, "error"));
        return;
    }
    let message = cat(&[&where_, &text(": "), &error.message]);
    out.push(problem(message, place, where_, "error"));
}

fn is_hex_colour(value: &[u32]) -> bool {
    let hex = |c: &u32| char::from_u32(*c).is_some_and(|c| c.is_ascii_hexdigit());
    matches!(value.len(), 4 | 7) && value[0] == u32::from('#') && value[1..].iter().all(hex)
}

/// `_semantic`: theme names, token values and the CSS policy.
fn semantic(data: &Dict, positions: &Positions, out: &mut Vec<Problem>) {
    let Some(Value::Dict(theme)) = data.get_str("theme") else { return };
    let key = |s: &str| Part::Key(text(s));
    if let Some(Value::Str(base)) = theme.get_str("base") {
        if !theme_ids().iter().any(|id| id.as_str() == Some(base)) {
            let fallback = default_base().as_str().expect("a theme id");
            let message = cat(&[&text("theme '"), base, &text("' is not built into this rika!; using '"), fallback, &text("'")]);
            out.push(problem(message, positions.of(&[key("theme"), key("base")], false), text("theme.base"), "warning"));
        }
    }
    if let Some(Value::Dict(tokens)) = theme.get_str("tokens") {
        for (name, value) in tokens.iter() {
            let Value::Str(value) = value else { continue };
            let place = positions.of(&[key("theme"), key("tokens"), Part::Key(name.clone())], false);
            let path = cat(&[&text("theme.tokens."), name]);
            if COLOR_TOKENS.iter().any(|c| *name == text(c)) {
                if !is_hex_colour(value) {
                    let message = cat(&[&path, &text(" must be a hex colour such as #1c1c1c")]);
                    out.push(problem(message, place, path.clone(), "error"));
                }
            } else {
                for issue in css::check_value(value) {
                    out.push(problem(cat(&[&path, &text(": "), &issue.message]), place, path.clone(), "error"));
                }
            }
        }
    }
    match theme.get_str("css") {
        Some(Value::List(lines)) if lines.iter().all(|l| matches!(l, Value::Str(_))) => {
            let joined = lines.iter().filter_map(Value::as_str).cloned().collect::<Vec<_>>().join(&u32::from('\n'));
            for issue in css::check_css(&joined) {
                let index = issue.line - 1;
                let (line, column) = positions.of(&[key("theme"), key("css"), Part::Index(index)], false);
                let message = cat(&[&text("CSS: "), &issue.message]);
                out.push(problem(message, (line, column + issue.column), text(&format!("theme.css[{index}]")), "error"));
            }
        }
        Some(Value::Str(stylesheet)) => {
            for issue in css::check_css(stylesheet) {
                let place = positions.of(&[key("theme"), key("css")], false);
                let head = format!("CSS line {}, column {}: ", issue.line, issue.column);
                out.push(problem(cat(&[&text(&head), &issue.message]), place, text("theme.css"), "error"));
            }
        }
        _ => {}
    }
}

/// UTF-8 length with `surrogatepass`.
fn utf8_len(t: &[u32]) -> usize {
    t.iter().map(|&c| match c {
        0..=0x7f => 1,
        0x80..=0x7ff => 2,
        0x800..=0xffff => 3,
        _ => 4,
    }).sum()
}

/// Parse, validate and normalize a build file's text (`document.check`).
pub fn check(text: &[u32]) -> Result<Checked, Raised> {
    std::thread::scope(|scope| {
        std::thread::Builder::new()
            .stack_size(STACK)
            .spawn_scoped(scope, || check_here(text))
            .expect("a thread for the build check")
            .join()
            .unwrap_or_else(|panic| std::panic::resume_unwind(panic))
    })
}

fn check_here(source: &[u32]) -> Result<Checked, Raised> {
    if utf8_len(source) > MAX_BYTES {
        return Ok(lone(&format!("a build file is limited to {} KB", MAX_BYTES / 1024), 1, 1));
    }
    let parsed = match json::parse(source) {
        Ok(parsed) => parsed,
        Err(Failure::Syntax(message, offset)) => {
            let (line, column) = Positions::place_in(source, offset);
            return Ok(lone(&format!("not valid JSON: {message}"), line, column));
        }
        Err(Failure::TooDeep) => return Ok(lone("nested too deeply", 1, 1)),
        Err(Failure::LongInt) => return Err(Raised::ValueError),
    };
    let positions = &parsed.positions;
    let mut problems: Vec<Problem> = parsed
        .found
        .iter()
        .map(|f| problem(f.message.clone(), positions.place(f.offset), f.path.clone(), "error"))
        .collect();
    let Value::Dict(data) = &parsed.value else {
        return Ok(lone("a build is a JSON object: { \"schema\": 1, ... }", 1, 1));
    };
    let mut errors = Vec::new();
    Validator { root: schema() }.errors(schema(), &parsed.value, &mut Vec::new(), &mut errors)?;
    let order = |e: &super::schema::SchemaError| -> Vec<Text> {
        e.path.iter().map(|p| match p {
            Part::Key(k) => k.clone(),
            Part::Index(i) => text(&i.to_string()),
        }).collect()
    };
    errors.sort_by_cached_key(order);
    for error in errors {
        schema_problem(error, data, positions, &mut problems);
    }
    semantic(data, positions, &mut problems);
    problems.sort_by_key(|p| (p.line, p.column));
    if problems.iter().any(|p| p.severity == "error") {
        return Ok(Checked { build: None, problems });
    }
    Ok(Checked { build: Some(normalize(data)), problems })
}

/// `_merge`: known keys of `base` (the defaults), each taken from `over` when given there.
fn merge(base: &Value, over: &Value) -> Value {
    match (base, over) {
        (Value::Dict(b), Value::Dict(o)) if b.len() > 0 => {
            let mut out = Dict::new();
            for (key, value) in b.iter() {
                out.insert(key.clone(), match o.get(key) {
                    Some(given) => merge(value, given),
                    None => value.clone(),
                });
            }
            Value::Dict(out)
        }
        _ => over.clone(),
    }
}

/// `Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)` as a float.
fn cents(value: &Value) -> f64 {
    let written = match value {
        Value::Int(i) => i.decimal(),
        Value::Float(f) => float_repr(*f),
        _ => return 0.0,
    };
    let (negative, written) = match written.strip_prefix('-') {
        Some(rest) => (true, rest.to_owned()),
        None => (false, written),
    };
    let (mantissa, exponent) = match written.split_once('e') {
        Some((m, e)) => (m.to_owned(), e.parse::<i64>().unwrap_or(0)),
        None => (written, 0),
    };
    let (whole, fraction) = mantissa.split_once('.').unwrap_or((&mantissa, ""));
    let mut digits: Vec<u8> = format!("{whole}{fraction}").bytes().map(|b| b - b'0').collect();
    // value = digits * 10^scale
    let scale = exponent - fraction.len() as i64;
    let mut hundredths: Vec<u8> = if scale >= -2 {
        digits.extend(std::iter::repeat_n(0, (scale + 2) as usize));
        digits
    } else {
        let drop = (-2 - scale) as usize;
        let keep = digits.len().saturating_sub(drop);
        let first_dropped = if drop <= digits.len() { digits[keep] } else { 0 };
        let mut kept = digits[..keep].to_vec();
        if first_dropped >= 5 {
            let mut i = kept.len();
            loop {
                if i == 0 {
                    kept.insert(0, 1);
                    break;
                }
                i -= 1;
                if kept[i] == 9 {
                    kept[i] = 0;
                } else {
                    kept[i] += 1;
                    break;
                }
            }
        }
        kept
    };
    if hundredths.is_empty() {
        hundredths.push(0);
    }
    let number: String = hundredths.iter().map(|d| char::from(b'0' + d)).collect();
    let exact: f64 = format!("{number}e-2").parse().unwrap_or(0.0);
    if negative { -exact } else { exact }
}

/// `normalize`: the complete build a valid document describes.
pub fn normalize(data: &Dict) -> Value {
    let Value::Dict(mut build) = merge(&Value::Dict(defaults().clone()), &Value::Dict(data.clone())) else {
        unreachable!("the defaults are an object")
    };
    build.insert(text("schema"), Value::Int(super::value::Int::Small(SCHEMA_VERSION)));
    let order = match node(schema(), &["properties", "modules", "items"]).get_str("enum") {
        Some(Value::List(order)) => order.clone(),
        _ => Vec::new(),
    };
    if let Some(Value::List(modules)) = build.get_str("modules") {
        let kept = order.into_iter().filter(|m| modules.iter().any(|g| g.as_str().is_some() && g.as_str() == m.as_str())).collect();
        build.insert(text("modules"), Value::List(kept));
    }
    if let Some(Value::Dict(theme)) = build.get_str_mut("theme") {
        if let Some(Value::List(lines)) = theme.get_str("css") {
            let joined = lines.iter().filter_map(Value::as_str).cloned().collect::<Vec<_>>().join(&u32::from('\n'));
            theme.insert(text("css"), Value::Str(joined));
        }
        let known = theme.get_str("base").is_some_and(|b| theme_ids().iter().any(|id| id.as_str().is_some() && id.as_str() == b.as_str()));
        if !known {
            theme.insert(text("base"), default_base().clone());
        }
    }
    if let Some(Value::Dict(settings)) = build.get_str_mut("settings") {
        if let Some(Value::Dict(ai)) = settings.get_str_mut("ai") {
            if let Some(budget) = ai.get_str("daily_budget_usd") {
                let rounded = cents(budget);
                ai.insert(text("daily_budget_usd"), Value::Float(rounded));
            }
        }
    }
    Value::Dict(build)
}

impl Positions {
    /// `JSONDecodeError`'s `lineno` and `colno` of an offset in `source`.
    pub fn place_in(source: &[u32], offset: usize) -> (usize, usize) {
        let before = &source[..offset.min(source.len())];
        let line = before.iter().filter(|&&c| c == u32::from('\n')).count() + 1;
        let column = offset - before.iter().rposition(|&c| c == u32::from('\n')).map_or(0, |i| i + 1) + 1;
        (line, column)
    }
}

pub fn push_problem_json(p: &Problem, out: &mut String) {
    use super::value::dump_str;
    out.push_str("{\"message\":");
    dump_str(&p.message, out);
    out.push_str(&format!(",\"line\":{},\"column\":{},\"path\":", p.line, p.column));
    dump_str(&p.path, out);
    out.push_str(",\"severity\":");
    let mut severity = Text::new();
    push(&mut severity, p.severity);
    dump_str(&severity, out);
    out.push('}');
}
