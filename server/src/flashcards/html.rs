//! Python 3.12's `html.parser.HTMLParser` (`convert_charrefs=True`) as the flashcard code fed
//! it, once and without `close()`: the same tag, attribute and character-reference rules (its
//! own regular expressions, with their look-arounds), so the review surface sanitizes
//! exactly what it did. Trailing text the parser would still wait for (an unfinished tag or
//! reference, an unclosed `<script>`) is dropped, as the unclosed Python parser dropped it.

use std::collections::HashMap;
use std::sync::LazyLock;

use fancy_regex::Regex as Fancy;
use regex::Regex;

use super::entities::{ENTITIES, INVALID_CHARREFS, INVALID_CODEPOINTS};

#[derive(Debug, PartialEq)]
pub enum Event {
    Start { tag: String, attrs: Vec<(String, Option<String>)> },
    End { tag: String },
    Data(String),
}

fn fancy(pattern: &str) -> Fancy {
    Fancy::new(pattern).expect("a valid pattern")
}

static TAGFIND: LazyLock<Fancy> = LazyLock::new(|| fancy(r"^([a-zA-Z][^\t\n\r\f />]*)(?:[\t\n\r\f ]|/(?!>))*"));
/// `attrfind_tolerant`, matched one character early so its look-behind sees that character.
static ATTRFIND: LazyLock<Fancy> = LazyLock::new(|| fancy(concat!(
    r#"^(?s:.)((?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*)"#,
    r#"([\t\n\r\f ]*=[\t\n\r\f ]*('[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?"#,
    r"(?:[\t\n\r\f ]|/(?!>))*",
)));
static LOCATETAGEND: LazyLock<Fancy> = LazyLock::new(|| fancy(concat!(
    r"^[a-zA-Z][^\t\n\r\f />]*[\t\n\r\f /]*",
    r#"(?:(?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*"#,
    r#"(?:[\t\n\r\f ]*=[\t\n\r\f ]*(?:'[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?"#,
    r"[\t\n\r\f /]*)*>?",
)));
static COMMENTCLOSE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"--!?>").expect("a valid pattern"));
static COMMENTABRUPTCLOSE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^-?>").expect("a valid pattern"));
static CHARREF: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\x0c <&#;]{1,32};?)").expect("a valid pattern")
});
static NAMED: LazyLock<HashMap<&'static str, &'static str>> = LazyLock::new(|| ENTITIES.iter().copied().collect());

/// `html.unescape`.
pub fn unescape(text: &str) -> String {
    if !text.contains('&') {
        return text.to_owned();
    }
    CHARREF.replace_all(text, |captures: &regex::Captures| {
        let reference = &captures[1];
        if let Some(number) = reference.strip_prefix('#') {
            let (digits, radix) = match number.strip_prefix(['x', 'X']) {
                Some(hex) => (hex, 16),
                None => (number, 10),
            };
            let code = u64::from_str_radix(digits.trim_end_matches(';'), radix).unwrap_or(u64::MAX);
            if let Some((_, replacement)) = INVALID_CHARREFS.iter().find(|(key, _)| u64::from(*key) == code) {
                return (*replacement).to_owned();
            }
            if (0xD800..=0xDFFF).contains(&code) || code > 0x10FFFF {
                return "\u{fffd}".to_owned();
            }
            if INVALID_CODEPOINTS.iter().any(|(low, high)| (u64::from(*low)..=u64::from(*high)).contains(&code)) {
                return String::new();
            }
            return char::from_u32(code as u32).map(String::from).unwrap_or_default();
        }
        if let Some(value) = NAMED.get(reference) {
            return (*value).to_owned();
        }
        let boundaries: Vec<usize> = reference.char_indices().map(|(at, _)| at).collect();
        for x in (2..boundaries.len()).rev() {
            if let Some(value) = NAMED.get(&reference[..boundaries[x]]) {
                return format!("{value}{}", &reference[boundaries[x]..]);
            }
        }
        format!("&{reference}")
    }).into_owned()
}

/// `html.escape(text, quote=True)`.
pub fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            c => out.push(c),
        }
    }
    out
}

const CDATA: &[&str] = &["script", "style", "xmp", "iframe", "noembed", "noframes"];
const RCDATA: &[&str] = &["textarea", "title"];

struct Parser<'a> {
    raw: &'a str,
    events: Vec<Event>,
    cdata: Option<String>,
    escapable: bool,
}

fn is_tag_letter(text: &str, at: usize) -> bool {
    text.as_bytes().get(at).is_some_and(u8::is_ascii_alphabetic)
}

/// The end of a `locatetagend` match starting at `from`.
fn locate_tag_end(raw: &str, from: usize) -> usize {
    match LOCATETAGEND.find(&raw[from..]) {
        Ok(Some(found)) => from + found.end(),
        _ => from,
    }
}

impl Parser<'_> {
    fn data(&mut self, text: &str) {
        let text = if self.escapable { unescape(text) } else { text.to_owned() };
        self.events.push(Event::Data(text));
    }

    /// `interesting.search` in CDATA mode: `</elem` followed by a delimiter, any case.
    fn cdata_close(&self, from: usize, elem: &str) -> Option<usize> {
        let bytes = self.raw.as_bytes();
        let mut at = from;
        while let Some(found) = self.raw[at..].find("</") {
            let start = at + found;
            let name_end = start + 2 + elem.len();
            if name_end < bytes.len() && self.raw.is_char_boundary(name_end)
                && self.raw[start + 2..name_end].eq_ignore_ascii_case(elem)
                && matches!(bytes[name_end], b'\t' | b'\n' | b'\r' | 0x0c | b' ' | b'/' | b'>')
            {
                return Some(start);
            }
            at = start + 2;
        }
        None
    }

    fn parse_starttag(&mut self, i: usize) -> Option<usize> {
        let end = locate_tag_end(self.raw, i + 1);
        if !self.raw[..end].ends_with('>') {
            return None;
        }
        let found = TAGFIND.captures(&self.raw[i + 1..]).ok().flatten()?;
        let tag = found.get(1)?.as_str().to_lowercase();
        let mut k = i + 1 + found.get(0)?.end();
        let mut attrs = Vec::new();
        while k < end {
            let before = self.raw[..k].char_indices().next_back().map_or(0, |(at, _)| at);
            let Ok(Some(m)) = ATTRFIND.captures(&self.raw[before..]) else { break };
            let name = m.get(1).map(|g| g.as_str().to_lowercase()).unwrap_or_default();
            let value = match (m.get(2), m.get(3)) {
                (None, _) => None,
                (Some(rest), _) if rest.as_str().is_empty() => None,
                (Some(_), value) => {
                    let value = value.map_or("", |g| g.as_str());
                    let quoted = (value.starts_with('\'') && value.ends_with('\'')) || (value.starts_with('"') && value.ends_with('"'));
                    let value = if quoted { value.get(1..value.len().saturating_sub(1).max(1)).unwrap_or("") } else { value };
                    Some(if value.is_empty() { String::new() } else { unescape(value) })
                }
            };
            attrs.push((name, value));
            k = before + m.get(0).map_or(0, |g| g.end());
        }
        let rest = self.raw[k.min(end)..end].trim();
        if rest != ">" && rest != "/>" {
            self.data_raw(&self.raw[i..end].to_owned());
            return Some(end);
        }
        if rest.ends_with("/>") {
            self.events.push(Event::Start { tag: tag.clone(), attrs });
            self.events.push(Event::End { tag });
        } else {
            let cdata = CDATA.contains(&tag.as_str());
            let rcdata = RCDATA.contains(&tag.as_str());
            self.events.push(Event::Start { tag: tag.clone(), attrs });
            if cdata || tag == "plaintext" {
                self.cdata = Some(tag);
                self.escapable = false;
            } else if rcdata {
                self.cdata = Some(tag);
                self.escapable = true;
            }
        }
        Some(end)
    }

    /// `handle_data` of markup text: unescaped as other data is.
    fn data_raw(&mut self, text: &str) {
        self.events.push(Event::Data(text.to_owned()));
    }

    fn parse_endtag(&mut self, i: usize) -> Option<usize> {
        self.raw[i + 2..].find('>')?;
        if !is_tag_letter(self.raw, i + 2) {
            if self.raw[i + 2..].starts_with('>') {
                return Some(i + 3);
            }
            return self.raw[i + 2..].find('>').map(|at| i + 2 + at + 1);
        }
        let end = locate_tag_end(self.raw, i + 2);
        if !self.raw[..end].ends_with('>') {
            return None;
        }
        let found = TAGFIND.captures(&self.raw[i + 2..]).ok().flatten()?;
        let tag = found.get(1)?.as_str().to_lowercase();
        self.events.push(Event::End { tag });
        self.cdata = None;
        self.escapable = true;
        Some(end)
    }

    fn parse_comment(&self, i: usize) -> Option<usize> {
        if let Some(found) = COMMENTCLOSE.find(&self.raw[i + 4..]) {
            return Some(i + 4 + found.end());
        }
        COMMENTABRUPTCLOSE.find(&self.raw[i + 4..]).map(|found| i + 4 + found.end())
    }

    fn parse_declaration(&self, i: usize) -> Option<usize> {
        let rest = &self.raw[i..];
        if rest.starts_with("<!--") {
            return self.parse_comment(i);
        }
        if rest.starts_with("<![CDATA[") {
            return rest[9..].find("]]>").map(|at| i + 9 + at + 3);
        }
        if rest.len() >= 9 && rest.is_char_boundary(9) && rest[..9].eq_ignore_ascii_case("<!doctype") {
            return rest[9..].find('>').map(|at| i + 9 + at + 1);
        }
        if rest.starts_with("<![") {
            return rest[3..].find('>').map(|at| i + 3 + at + 1);
        }
        rest[2..].find('>').map(|at| i + 2 + at + 1)
    }

    /// `goahead(0)`.
    fn run(&mut self) {
        let raw = self.raw;
        let n = raw.len();
        let mut i = 0;
        while i < n {
            let j = match &self.cdata {
                None => match raw[i..].find('<') {
                    Some(at) => i + at,
                    None => {
                        // Wait for the rest of a trailing reference: never, unfed.
                        let chars: Vec<(usize, char)> = raw[i..].char_indices().collect();
                        let window = chars.len().saturating_sub(34);
                        let amp = chars[window..].iter().rev().find(|(_, c)| *c == '&').map(|(at, _)| i + at);
                        if let Some(amp) = amp {
                            if !raw[amp..].contains(['\t', '\n', '\r', '\x0c', ' ', ';']) {
                                break;
                            }
                        }
                        n
                    }
                },
                // `<plaintext>` never closes: its `interesting` is the end of the text.
                Some(elem) if elem == "plaintext" => n,
                Some(elem) => match self.cdata_close(i, &elem.clone()) {
                    Some(at) => at,
                    None => break,
                },
            };
            if i < j {
                let text = &raw[i..j];
                self.data(text);
            }
            i = j;
            if i == n {
                break;
            }
            let rest = &raw[i..];
            let k = if is_tag_letter(raw, i + 1) && rest.starts_with('<') {
                self.parse_starttag(i)
            } else if rest.starts_with("</") {
                self.parse_endtag(i)
            } else if rest.starts_with("<!--") {
                self.parse_comment(i)
            } else if rest.starts_with("<?") {
                rest[2..].find('>').map(|at| i + 2 + at + 1)
            } else if rest.starts_with("<!") {
                self.parse_declaration(i)
            } else if i + 1 < n {
                self.events.push(Event::Data("<".into()));
                Some(i + 1)
            } else {
                break;
            };
            match k {
                Some(k) => i = k,
                None => break,
            }
        }
    }
}

/// The events `HTMLParser.feed(text)` delivers before anything is closed.
pub fn parse(text: &str) -> Vec<Event> {
    let mut parser = Parser { raw: text, events: Vec::new(), cdata: None, escapable: true };
    parser.run();
    parser.events
}

/// The text of every data event (`_PlainText`).
pub fn plain_text(text: &str) -> String {
    parse(text).into_iter().filter_map(|event| match event { Event::Data(data) => Some(data), _ => None }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unescape_matches_python() {
        assert_eq!(unescape("a &amp; b &lt;&#x41;&#66;&copy &notit; &#0; &bogus;"), "a & b <AB© ¬it; \u{fffd} &bogus;");
    }

    #[test]
    fn tags_attributes_and_cdata() {
        let events = parse(r#"<b class="x">bold</b><img src="local.png"><script>alert("x")</script>tail &amp; <br/>"#);
        assert_eq!(events[0], Event::Start { tag: "b".into(), attrs: vec![("class".into(), Some("x".into()))] });
        assert_eq!(events[3], Event::Start { tag: "img".into(), attrs: vec![("src".into(), Some("local.png".into()))] });
        assert_eq!(events[5], Event::Data("alert(\"x\")".into()));
        assert_eq!(events[7], Event::Data("tail & ".into()));
        assert_eq!(events[8], Event::Start { tag: "br".into(), attrs: vec![] });
        assert_eq!(events[9], Event::End { tag: "br".into() });
        // An unfinished tag and an unclosed script are never delivered.
        assert_eq!(parse("text<b"), vec![Event::Data("text".into())]);
        assert_eq!(parse("<script>alert(1)").len(), 1);
    }
}
