//! `kitty.core.backlinks`' text rules over Python's `str` and `re` semantics: word tokens
//! (`[^\W_]+`, runs of `str.isalnum()`), the light singular, phrases of labels, `[[wikilinks]]`,
//! the FTS5 phrase queries, and snippets with links, e-mail addresses, paths and long
//! token-like strings redacted. Positions are code points, as Python's string indices are.
//!
//! The five `REDACT` expressions are matched by hand as CPython's `re` matches them (leftmost,
//! then greedy with backtracking), with `\w` = `str.isalnum()` or `_`, `\s` = `str.isspace()`,
//! `\d` = `str.isdecimal()`, and `re.I` letting `ſ` match `s` and `İ`/`ı` match `i`.

use crate::search::text::{casefold, is_alnum, is_decimal, is_space};

pub const SNIPPET: usize = 140;

pub static STOPWORDS: &[&str] = &[
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each",
    "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in",
    "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off", "on", "once",
    "only", "or", "other", "our", "ours", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours",
    "new", "note", "notes", "page", "untitled", "todo", "task", "tasks", "thing", "things", "stuff", "idea", "ideas", "topic", "topics",
];

pub fn stopword(token: &str) -> bool {
    STOPWORDS.contains(&token)
}

/// A label's normalized tokens.
pub type Phrase = Vec<String>;

fn length(text: &str) -> usize {
    text.chars().count()
}

/// `_singular`.
pub fn singular(token: String) -> String {
    let count = length(&token);
    if count > 4 && token.ends_with("ies") {
        return format!("{}y", &token[..token.len() - 3]);
    }
    if count > 4 && ["ses", "xes", "zes", "ches", "shes"].iter().any(|end| token.ends_with(end)) {
        return token[..token.len() - 2].to_owned();
    }
    if count > 3 && token.ends_with('s') && !["ss", "us", "is"].iter().any(|end| token.ends_with(end)) {
        return token[..token.len() - 1].to_owned();
    }
    token
}

/// `TOKEN.findall(text)`: the runs of `str.isalnum()` characters.
pub fn runs(text: &str) -> impl Iterator<Item = &str> {
    text.split(|c: char| !is_alnum(c)).filter(|run| !run.is_empty())
}

/// `phrase`: a label's token tuple, or None when it is too short or only stopwords.
pub fn phrase(label: &str) -> Option<Phrase> {
    let folded = casefold(label);
    let tokens: Vec<&str> = runs(&folded).collect();
    if tokens.iter().map(|token| length(token)).sum::<usize>() < 3 || tokens.iter().all(|token| stopword(token)) {
        return None;
    }
    Some(tokens.into_iter().take(12).map(|token| singular(token.to_owned())).collect())
}

/// `_tokens`: each word's singular case-folded form with its code-point span.
pub fn tokens(text: &str) -> Vec<(String, usize, usize)> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut start = 0;
    let mut index = 0;
    for c in text.chars() {
        if is_alnum(c) {
            if current.is_empty() {
                start = index;
            }
            current.push(c);
        } else if !current.is_empty() {
            out.push((singular(casefold(&current)), start, index));
            current.clear();
        }
        index += 1;
    }
    if !current.is_empty() {
        out.push((singular(casefold(&current)), start, index));
    }
    out
}

/// The tokens alone.
pub fn token_words(text: &str) -> Vec<String> {
    tokens(text).into_iter().map(|(token, _, _)| token).collect()
}

/// `_stem`: an index prefix every raw spelling of this singular starts with.
pub fn stem(token: &str) -> &str {
    if length(token) >= 3 && token.ends_with('y') { &token[..token.len() - 1] } else { token }
}

/// `phrase_query`: an FTS5 phrase of every spelling `_singular` folds onto `tokens`.
pub fn phrase_query(tokens: &[String], column: Option<&str>) -> String {
    let expression = tokens.iter().map(|token| format!("\"{}\"*", stem(token))).collect::<Vec<_>>().join(" + ");
    match column {
        Some(column) => format!("{{{column}}} : ({expression})"),
        None => expression,
    }
}

/// `_contains`: whether `needle` occurs in `haystack` as a contiguous run.
pub fn contains(haystack: &[String], needle: &[String]) -> bool {
    !needle.is_empty() && needle.len() <= haystack.len() && haystack.windows(needle.len()).any(|window| window == needle)
}

/// `WIKILINK.finditer(text)`: the target of every `[[target]]` / `[[target|label]]`
/// (`\[\[([^\[\]\n|]{1,200})(?:\|[^\[\]\n]{0,200})?\]\]`).
pub fn wikilinks(text: &str) -> Vec<String> {
    let c: Vec<char> = text.chars().collect();
    let n = c.len();
    let target = |x: char| !matches!(x, '[' | ']' | '\n' | '|');
    let label = |x: char| !matches!(x, '[' | ']' | '\n');
    let mut out = Vec::new();
    let mut i = 0;
    while i + 1 < n {
        if c[i] == '[' && c[i + 1] == '[' {
            let first = i + 2;
            let mut p = first;
            while p < n && target(c[p]) {
                p += 1;
            }
            let run = p - first;
            let mut end = None;
            if (1..=200).contains(&run) {
                if p < n && c[p] == '|' {
                    let mut q = p + 1;
                    while q < n && label(c[q]) {
                        q += 1;
                    }
                    if q - (p + 1) <= 200 && q + 1 < n && c[q] == ']' && c[q + 1] == ']' {
                        end = Some(q + 2);
                    }
                } else if p + 1 < n && c[p] == ']' && c[p + 1] == ']' {
                    end = Some(p + 2);
                }
            }
            if let Some(end) = end {
                out.push(c[first..p].iter().collect());
                i = end;
                continue;
            }
        }
        i += 1;
    }
    out
}

// Redaction --------------------------------------------------------------------

fn word(c: char) -> bool {
    c == '_' || is_alnum(c)
}

/// `\b` at `at`.
fn boundary(c: &[char], at: usize) -> bool {
    let before = at > 0 && word(c[at - 1]);
    let after = at < c.len() && word(c[at]);
    before != after
}

/// One pattern letter under `re.I`.
fn letter(c: char, wanted: char) -> bool {
    match wanted {
        's' => matches!(c, 's' | 'S' | 'ſ'),
        'i' => matches!(c, 'i' | 'I' | 'İ' | 'ı'),
        _ => c.is_ascii() && c.to_ascii_lowercase() == wanted,
    }
}

fn literal(c: &[char], at: usize, wanted: &str, fold: bool) -> Option<usize> {
    let mut p = at;
    for w in wanted.chars() {
        if p >= c.len() || !(if fold { letter(c[p], w) } else { c[p] == w }) {
            return None;
        }
        p += 1;
    }
    Some(p)
}

fn run_end(c: &[char], from: usize, class: impl Fn(char) -> bool) -> usize {
    let mut p = from;
    while p < c.len() && class(c[p]) {
        p += 1;
    }
    p
}

/// `\S+` from `at`: the end of a non-empty run, or None.
fn non_space(c: &[char], at: usize) -> Option<usize> {
    let end = run_end(c, at, |x| !is_space(x));
    (end > at).then_some(end)
}

/// `\b(?:https?|ftp|file)://\S+` (re.I).
fn link(c: &[char], i: usize) -> Option<usize> {
    if !boundary(c, i) {
        return None;
    }
    let schemes: [&[&str]; 3] = [&["https", "http"], &["ftp"], &["file"]];
    for alternatives in schemes {
        for scheme in alternatives {
            if let Some(p) = literal(c, i, scheme, true).and_then(|p| literal(c, p, "://", false)) {
                if let Some(end) = non_space(c, p) {
                    return Some(end);
                }
            }
        }
    }
    None
}

/// `\bwww\.\S+` (re.I).
fn www(c: &[char], i: usize) -> Option<usize> {
    if !boundary(c, i) {
        return None;
    }
    let p = literal(c, i, "www", true).and_then(|p| literal(c, p, ".", false))?;
    non_space(c, p)
}

/// `[\w.+-]+@[\w-]+(?:\.[\w-]+)+`, tried at `i`; `Err(next)` names the next start worth trying.
fn email(c: &[char], i: usize) -> Result<usize, usize> {
    let local = |x: char| word(x) || matches!(x, '.' | '+' | '-');
    let domain = |x: char| word(x) || x == '-';
    if !local(c[i]) {
        return Err(i + 1);
    }
    let at = run_end(c, i, local);
    // Every start inside this run reaches the same character after it.
    if at >= c.len() || c[at] != '@' {
        return Err(at + 1);
    }
    let mut p = run_end(c, at + 1, domain);
    if p == at + 1 {
        return Err(at + 1);
    }
    let mut groups = 0;
    while p < c.len() && c[p] == '.' {
        let end = run_end(c, p + 1, domain);
        if end == p + 1 {
            break;
        }
        p = end;
        groups += 1;
    }
    if groups == 0 { Err(at + 1) } else { Ok(p) }
}

/// `(?:\b[A-Za-z]:)?(?:[\\/][\w.~-]+){2,}[\\/]?|~[\\/][\w.~/-]+`.
fn path(c: &[char], i: usize) -> Option<usize> {
    let sep = |x: char| x == '/' || x == '\\';
    let part = |x: char| word(x) || matches!(x, '.' | '~' | '-');
    let groups = |from: usize| -> Option<usize> {
        let (mut p, mut count) = (from, 0);
        while p + 1 < c.len() && sep(c[p]) && part(c[p + 1]) {
            p = run_end(c, p + 1, part);
            count += 1;
        }
        (count >= 2).then(|| if p < c.len() && sep(c[p]) { p + 1 } else { p })
    };
    if boundary(c, i) && i + 1 < c.len() && c[i].is_ascii_alphabetic() && c[i + 1] == ':' {
        if let Some(end) = groups(i + 2) {
            return Some(end);
        }
    }
    if let Some(end) = groups(i) {
        return Some(end);
    }
    if i + 2 < c.len() && c[i] == '~' && sep(c[i + 1]) {
        let end = run_end(c, i + 2, |x| part(x) || x == '/');
        if end > i + 2 {
            return Some(end);
        }
    }
    None
}

/// `\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}\b`.
fn token_like(c: &[char], i: usize) -> Option<usize> {
    if !boundary(c, i) {
        return None;
    }
    let class = |x: char| x.is_ascii_alphanumeric() || x == '_' || x == '-';
    let end = run_end(c, i, class);
    let run = &c[i..end];
    let digit = run.iter().any(char::is_ascii_digit) || (end < c.len() && is_decimal(c[end]));
    if !digit || !run.iter().any(char::is_ascii_alphabetic) || run.len() < 24 {
        return None;
    }
    (i + 24..=end).rev().find(|&at| boundary(c, at))
}

fn substitute(text: &str, replacement: &str, find: impl Fn(&[char], usize) -> Result<usize, usize>) -> String {
    let c: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let (mut i, mut copied) = (0, 0);
    while i < c.len() {
        match find(&c, i) {
            Ok(end) => {
                out.extend(&c[copied..i]);
                out.push_str(replacement);
                i = end;
                copied = end;
            }
            Err(next) => i = next.max(i + 1),
        }
    }
    out.extend(&c[copied..]);
    out
}

fn at(found: Option<usize>, i: usize) -> Result<usize, usize> {
    found.ok_or(i + 1)
}

/// `redact`.
pub fn redact(value: &str) -> String {
    let value = substitute(value, "[link]", |c, i| at(link(c, i), i));
    let value = substitute(&value, "[link]", |c, i| at(www(c, i), i));
    let value = substitute(&value, "[email]", email);
    let value = substitute(&value, "[path]", |c, i| at(path(c, i), i));
    substitute(&value, "[redacted]", |c, i| at(token_like(c, i), i))
}

/// `snippet`: about `SNIPPET` characters of whitespace-collapsed context around
/// `[start, end)` (code points of `text`), with sensitive spans redacted.
pub fn snippet(text: &str, start: usize, end: usize) -> String {
    let c: Vec<char> = text.chars().collect();
    let n = c.len();
    let pad = ((SNIPPET as i64 - (end as i64 - start as i64)).div_euclid(2)).max(20) as usize;
    let mut left = start.saturating_sub(pad);
    let mut right = (end + pad).min(n);
    if left > 0 {
        if let Some(space) = (left..start).find(|&at| is_space(c[at])) {
            left = space + 1;
        }
    }
    if right < n {
        if let Some(space) = (end..right).rev().find(|&at| is_space(c[at])) {
            right = space;
        }
    }
    let window: String = c[left..right].iter().collect();
    let redacted = redact(&window);
    let value = crate::search::text::split(&redacted).collect::<Vec<_>>().join(" ");
    format!("{}{}{}", if left > 0 { "…" } else { "" }, value, if right < n { "…" } else { "" })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phrases_fold_plurals_and_skip_short_labels() {
        assert_eq!(phrase("Neural Networks"), phrase("neural-network"));
        assert_eq!(phrase("Studies"), Some(vec!["study".to_owned()]));
        assert_eq!(phrase("AI"), None);
        assert_eq!(phrase("the of"), None);
        assert_eq!(phrase_query(&phrase("linear algebras").unwrap(), Some("title")), "{title} : (\"linear\"* + \"algebra\"*)");
        assert_eq!(phrase_query(&phrase("Study").unwrap(), None), "\"stud\"*");
    }

    #[test]
    fn wikilinks_match_as_the_expression_does() {
        assert_eq!(wikilinks("See [[A|b]] and [[[C]] [[d\n]] [[e|f|g]] [[]]"), ["A", "C", "e"]);
        assert_eq!(wikilinks(&format!("[[{}]]", "x".repeat(201))), Vec::<String>::new());
    }

    #[test]
    fn redaction_replaces_links_addresses_paths_and_tokens() {
        assert_eq!(redact("go https://example.invalid/x?y and www.Example.org now"), "go [link] and [link] now");
        assert_eq!(redact("mail a.b+c@ex-ample.co.uk, x@y"), "mail [email], x@y");
        assert_eq!(redact("at /home/someone/file.txt or C:\\Users\\x or ~/notes"), "at [path] or [path] or [path]");
        assert_eq!(redact("key abcdefabcdef0123456789abcdef end"), "key [redacted] end");
        assert_eq!(redact("fİle://x ſtp httpſ://y"), "[link] ſtp [link]");
        assert_eq!(redact("/a only"), "/a only");
    }

    #[test]
    fn snippets_cut_at_white_space() {
        let text = format!("{} needle {}", "word ".repeat(40), "tail ".repeat(40));
        let at = text.find("needle").unwrap();
        let shown = snippet(&text, at, at + 6);
        assert!(shown.starts_with('…') && shown.ends_with('…') && shown.contains("needle"));
    }
}
