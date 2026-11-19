//! Block tokens, auto-tag exclusions, history words and actor names with CPython 3.12
//! Unicode semantics. Every exposed span is in code points, not UTF-8 bytes.
use super::{difflib::{self, Op}, tables};
use crate::builds::value::in_ranges;
use crate::search::text::{casefold, is_alnum, is_space, split};
use regex::Regex;
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};
use std::sync::LazyLock;

pub static REF: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\(\(([0-9a-fA-F-]{36})(?:@[0-9]{1,18})?\)\)|\{\{embed:([0-9a-fA-F-]{36})\}\}").unwrap());
static WIKI: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\[\[([^\[\]\n|]{1,200})(?:\|[^\[\]\n]{0,200})?\]\]").unwrap());

fn captures(pattern: &Regex, text: &str) -> Vec<(usize, usize, String)> {
    let (mut byte, mut point) = (0, 0);
    pattern.captures_iter(text).map(|capture| {
        let whole = capture.get(0).unwrap();
        let start = point + text[byte..whole.start()].chars().count();
        let end = start + whole.as_str().chars().count();
        (byte, point) = (whole.end(), end);
        (start, end, capture.get(1).or_else(|| capture.get(2)).unwrap().as_str().to_owned())
    }).collect()
}

pub fn refs(text: &str) -> Vec<(usize, usize, String)> { captures(&REF, text) }
pub fn wikis(text: &str) -> Vec<(usize, usize, String)> { captures(&WIKI, text) }
pub fn without_wikis(text: &str) -> String { WIKI.replace_all(text, "").into_owned() }
pub fn is_word(c: char) -> bool { c == '_' || is_alnum(c) }

pub fn tags(text: &str) -> Vec<(usize, usize, String)> {
    let c: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i + 1 < c.len() {
        if c[i] == '#' && (i == 0 || (!is_word(c[i - 1]) && c[i - 1] != '#')) && is_word(c[i + 1]) {
            let mut end = i + 2;
            while end < c.len() && end < i + 101 && (is_word(c[end]) || matches!(c[end], '/' | '-')) { end += 1; }
            out.push((i, end, c[i + 1..end].iter().collect()));
            i = end;
        } else { i += 1; }
    }
    out
}

fn lower(c: char) -> u32 {
    match tables::LOWER.binary_search_by_key(&(c as u32), |&(code, _)| code) {
        Ok(i) => tables::LOWER[i].1, Err(_) => c as u32,
    }
}

/// Literal `re.I`, not full casefold (which would expand e.g. sharp s).
fn same_case(pattern: char, c: char) -> bool {
    if !in_ranges(tables::CASED, pattern as u32) { return pattern == c; }
    let (a, b) = (lower(pattern), lower(c));
    a == b || tables::CASE_FIXES.binary_search(&(a, b)).is_ok()
}

pub fn mentions(title: &str, text: &str) -> bool {
    let needle: Vec<char> = title.chars().collect();
    let c: Vec<char> = text.chars().collect();
    if needle.len() > c.len() { return false; }
    (0..=c.len() - needle.len()).any(|i| {
        (i == 0 || !is_word(c[i - 1])) && (i + needle.len() == c.len() || !is_word(c[i + needle.len()]))
            && needle.iter().zip(&c[i..]).all(|(&a, &b)| same_case(a, b))
    })
}

fn alpha(c: char) -> bool { c.is_ascii_alphabetic() || matches!(c, 'İ' | 'ı' | 'ſ' | 'K') }
fn boundary(c: &[char], i: usize) -> bool {
    (i > 0 && is_word(c[i - 1])) != (i < c.len() && is_word(c[i]))
}
fn url_char(c: char) -> bool { !is_space(c) && !matches!(c, '<' | '>' | '"' | '\'') }
fn domain_char(c: char) -> bool { is_word(c) || c == '-' }

fn skip_at(c: &[char], i: usize) -> Option<usize> {
    let n = c.len();
    if c[i..].starts_with(&['`', '`', '`']) {
        // The lazy repeat takes the first closing fence, or the absolute end.
        let end = (i + 3..n.saturating_sub(2)).find(|&j| c[j..].starts_with(&['`', '`', '`']));
        return Some(end.map_or(n, |j| j + 3));
    }
    if c[i] == '`' {
        let mut j = i + 1;
        while j < n && !matches!(c[j], '`' | '\n') { j += 1; }
        if j > i + 1 && j < n && c[j] == '`' { return Some(j + 1); }
    }
    if boundary(c, i) {
        if alpha(c[i]) {
            let mut j = i + 1;
            while j < n && (alpha(c[j]) || c[j].is_ascii_digit() || matches!(c[j], '+' | '.' | '-')) { j += 1; }
            if c[j..].starts_with(&[':', '/', '/']) {
                let start = j + 3;
                j = start;
                while j < n && url_char(c[j]) { j += 1; }
                if j > start { return Some(j); }
            }
        }
        if i + 4 <= n && c[i..i + 3].iter().all(|&x| same_case('w', x)) && c[i + 3] == '.' {
            let mut j = i + 4;
            while j < n && url_char(c[j]) { j += 1; }
            if j > i + 4 { return Some(j); }
        }
    }
    // Greedy local part, then a domain with at least one complete .label.
    let mut j = i;
    while j < n && (is_word(c[j]) || matches!(c[j], '.' | '+' | '-')) { j += 1; }
    if j == i || j == n || c[j] != '@' { return None; }
    j += 1;
    let first = j;
    while j < n && domain_char(c[j]) { j += 1; }
    if j == first { return None; }
    let mut end = None;
    while j < n && c[j] == '.' {
        j += 1;
        let first = j;
        while j < n && domain_char(c[j]) { j += 1; }
        if j == first { break; }
        end = Some(j);
    }
    end
}

pub fn skip_matches(text: &str) -> Vec<(usize, usize)> {
    let c: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < c.len() {
        if let Some(end) = skip_at(&c, i) { out.push((i, end)); i = end; } else { i += 1; }
    }
    out
}

pub fn skip_spans(text: &str, code_marks: &[(usize, usize)]) -> Vec<(usize, usize)> {
    let mut out = skip_matches(text);
    for matches in [tags(text), wikis(text), refs(text)] {
        out.extend(matches.into_iter().map(|(start, end, _)| (start, end)));
    }
    out.extend_from_slice(code_marks);
    out.sort_unstable();
    out
}

fn unit_prefix(text: &str) -> Vec<usize> {
    let mut prefix = Vec::with_capacity(text.chars().count() + 1);
    prefix.push(0);
    for c in text.chars() { prefix.push(prefix.last().unwrap() + c.len_utf16()); }
    prefix
}

/// Valid code-point indices (0..=length) to UTF-16 units.
pub fn utf16(text: &str, offsets: &[usize]) -> Vec<usize> {
    if text.chars().all(|c| c.len_utf16() == 1) { return offsets.to_vec(); }
    let prefix = unit_prefix(text);
    offsets.iter().map(|&i| prefix[i]).collect()
}

/// Clamp to the text and round down inside surrogate pairs.
pub fn code_points(text: &str, offsets: &[i64]) -> Vec<usize> {
    let prefix = unit_prefix(text);
    offsets.iter().map(|&i| prefix.partition_point(|&unit| unit <= i.max(0) as usize) - 1).collect()
}

pub fn code_marks(text: &str, marks: &Value) -> Vec<(usize, usize)> {
    let Some(marks) = marks.as_array() else { return Vec::new(); };
    // JSON has unsigned integers beyond i64 too: all clamp to the end of text.
    let integer = |v: &Value| v.as_i64().map(i128::from).or_else(|| v.as_u64().map(i128::from));
    let mut flat = Vec::new();
    for mark in marks {
        if mark.get("t").and_then(Value::as_str) != Some("c") { continue; }
        if let (Some(s), Some(e)) = (mark.get("s").and_then(integer), mark.get("e").and_then(integer)) {
            if e > s { flat.extend([s.clamp(i64::MIN as i128, i64::MAX as i128) as i64, e.clamp(i64::MIN as i128, i64::MAX as i128) as i64]); }
        }
    }
    code_points(text, &flat).chunks_exact(2).map(|p| (p[0], p[1])).collect()
}

pub fn normalize_term(value: &str) -> String { split(&casefold(value)).collect::<Vec<_>>().join(" ") }

pub fn clean_name(value: &Value) -> Option<String> {
    let value = value.as_str()?;
    let filtered: String = value.chars().filter(|&c| is_space(c) || in_ranges(tables::PRINTABLE, c as u32)).collect();
    let joined = split(&filtered).collect::<Vec<_>>().join(" ");
    let result: String = joined.chars().take(100).collect();
    (!result.is_empty()).then_some(result)
}

pub fn word_pieces(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut chars = text.char_indices().peekable();
    while let Some((start, c)) = chars.next() {
        let space = is_space(c);
        let word = is_word(c);
        if space || word {
            while chars.peek().is_some_and(|&(_, next)| if space { is_space(next) } else { is_word(next) }) { chars.next(); }
        }
        let end = chars.peek().map_or(text.len(), |&(i, _)| i);
        out.push(&text[start..end]);
    }
    out
}

pub fn word_diff(before: &str, after: &str) -> Vec<(&'static str, String)> {
    let (a, b) = (word_pieces(before), word_pieces(after));
    let mut out = Vec::new();
    for (op, i1, i2, j1, j2) in difflib::opcodes(&a, &b) {
        if matches!(op, Op::Equal | Op::Delete | Op::Replace) && i2 > i1 {
            out.push((if op == Op::Equal { "equal" } else { "delete" }, a[i1..i2].concat()));
        }
        if matches!(op, Op::Insert | Op::Replace) && j2 > j1 { out.push(("insert", b[j1..j2].concat())); }
    }
    out
}

/// Hidden parity-test interface: one JSON result per input line.
pub fn text_command() -> i32 {
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let line = line.expect("read text case");
        let case: Value = serde_json::from_str(&line).expect("JSON text case");
        let text = case["text"].as_str().unwrap_or("");
        let marks = code_marks(text, &case["marks"]);
        let diff: Vec<Value> = word_diff(text, case["other"].as_str().unwrap_or("")).into_iter()
            .map(|(op, text)| json!({"op":op,"text":text})).collect();
        let answer = json!({
            "refs": refs(text), "wikis": wikis(text), "tags": tags(text), "without_wikis": without_wikis(text),
            "skip": skip_matches(text), "skip_spans": skip_spans(text, &marks),
            "utf16": utf16(text, &(0..=text.chars().count()).collect::<Vec<_>>()),
            "code_points": code_points(text, &(0..=text.encode_utf16().count() as i64).collect::<Vec<_>>()),
            "code_marks": marks, "normalize": normalize_term(text), "words": word_pieces(text), "word_diff": diff,
            "mentions": mentions(case["title"].as_str().unwrap_or(""), text), "clean_name": clean_name(&case["text"]),
        });
        serde_json::to_writer(&mut stdout, &answer).expect("write text answer");
        writeln!(stdout).expect("write newline");
    }
    stdout.flush().expect("flush text answers");
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tokens_and_units() {
        assert_eq!(tags("😀a#b ##x #_ #-x #yes/no"), vec![(9,11,"_".into()), (16,23,"yes/no".into())]);
        assert_eq!(wikis("😀[[a|]]"), vec![(1,7,"a".into())]);
        assert_eq!(utf16("a😀b", &[0,1,2,3]), vec![0,1,3,4]);
        assert_eq!(code_points("a😀b", &[-1,0,1,2,3,4,9]), vec![0,0,1,1,2,3,3]);
    }
    #[test]
    fn python_case_not_casefold() {
        assert!(mentions("iſKΣ", "İSKς"));
        assert!(!mentions("ss", "ß"));
        assert!(!mentions("a", "ba"));
        assert!(mentions("", "!"));
        assert_eq!(clean_name(&json!("\u{7} A\n\u{200b}B ")), Some("A B".into()));
        assert_eq!(skip_matches("`x` ```unclosed"), vec![(0,3),(4,15)]);
    }
}
