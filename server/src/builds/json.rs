//! JSON read as CPython 3.12's `json.loads` reads it (the C scanner), with `document.py`'s
//! `_Positions` folded in: the offset of every value and object key by path, and the
//! `NaN`/`Infinity` and duplicate-key problems it reports.
//!
//! Offsets count code points, as Python indexes `str`. The errors carry `json`'s messages and
//! positions; nesting past [`MAX_DEPTH`] is the `RecursionError` `json.loads` raises, and an
//! integer of more than 4300 digits the `ValueError` of Python's integer string limit.

use std::collections::HashMap;

use super::value::{push, Dict, Int, Text, Value};

/// The deepest nesting `json.loads` reads inside `document.check`: CPython's C recursion limit
/// (10000) less the calls already on the stack, which depends on the caller (9997 from a script,
/// somewhat fewer under pytest); measured from a script.
pub const MAX_DEPTH: usize = 9997;
/// `sys.get_int_max_str_digits()`.
const MAX_INT_DIGITS: usize = 4300;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Part {
    Key(Text),
    Index(usize),
}

pub enum Failure {
    /// A `JSONDecodeError`: its message and offset.
    Syntax(&'static str, usize),
    /// The `RecursionError`.
    TooDeep,
    /// The `ValueError` of an over-long integer.
    LongInt,
}

/// A `_Positions` problem: message, offset and dotted path.
pub struct Found {
    pub message: Text,
    pub offset: usize,
    pub path: Text,
}

/// `_Positions`: where each path's value (and key) starts. Paths are a trie so deep nesting stays linear.
pub struct Positions {
    starts: Vec<usize>,
    values: Vec<Option<usize>>,
    keys: Vec<Option<usize>>,
    children: HashMap<(usize, Part), usize>,
}

impl Positions {
    fn new(text: &[u32]) -> Positions {
        let mut starts = vec![0];
        starts.extend(text.iter().enumerate().filter(|(_, &c)| c == u32::from('\n')).map(|(i, _)| i + 1));
        Positions { starts, values: vec![None], keys: vec![None], children: HashMap::new() }
    }

    fn child(&mut self, parent: usize, part: Part) -> usize {
        let next = self.values.len();
        let id = *self.children.entry((parent, part)).or_insert(next);
        if id == next {
            self.values.push(None);
            self.keys.push(None);
        }
        id
    }

    /// Line and column (1-based) of an offset.
    pub fn place(&self, offset: usize) -> (usize, usize) {
        let line = self.starts.partition_point(|&start| start <= offset);
        (line, offset - self.starts[line - 1] + 1)
    }

    /// The position of a path's key (when asked and known) or value, else its nearest parent's.
    pub fn of(&self, path: &[Part], key: bool) -> (usize, usize) {
        let mut nodes = vec![Some(0)];
        for part in path {
            let parent = *nodes.last().unwrap_or(&None);
            nodes.push(parent.and_then(|id| self.children.get(&(id, part.clone())).copied()));
        }
        let mut key = key;
        for node in nodes.iter().rev() {
            if let Some(id) = *node {
                if key {
                    if let Some(offset) = self.keys[id] {
                        return self.place(offset);
                    }
                }
                if let Some(offset) = self.values[id] {
                    return self.place(offset);
                }
            }
            key = false;
        }
        (1, 1)
    }
}

/// `document._dotted`.
pub fn dotted(path: &[Part]) -> Text {
    let mut out = Text::new();
    for part in path {
        match part {
            Part::Index(i) => push(&mut out, &format!("[{i}]")),
            Part::Key(key) => {
                if !out.is_empty() {
                    out.push(u32::from('.'));
                }
                out.extend(key);
            }
        }
    }
    out
}

pub struct Parsed {
    pub value: Value,
    pub positions: Positions,
    pub found: Vec<Found>,
}

/// `json.loads(text)` with positions.
pub fn parse(text: &[u32]) -> Result<Parsed, Failure> {
    if text.first() == Some(&0xfeff) {
        return Err(Failure::Syntax("Unexpected UTF-8 BOM (decode using utf-8-sig)", 0));
    }
    let mut parser = Parser { s: text, positions: Positions::new(text), found: Vec::new(), path: Vec::new(), depth: 0 };
    let start = parser.skip(0);
    let (value, end) = parser.value(start, 0)?;
    let end = parser.skip(end);
    if end != text.len() {
        return Err(Failure::Syntax("Extra data", end));
    }
    Ok(Parsed { value, positions: parser.positions, found: parser.found })
}

struct Parser<'a> {
    s: &'a [u32],
    positions: Positions,
    found: Vec<Found>,
    path: Vec<Part>,
    depth: usize,
}

fn is_ws(c: u32) -> bool {
    matches!(c, 0x20 | 0x09 | 0x0a | 0x0d)
}

fn is_digit(c: u32) -> bool {
    (0x30..=0x39).contains(&c)
}

fn hex4(digits: &[u32]) -> Option<u32> {
    digits.iter().try_fold(0, |acc, &c| char::from_u32(c)?.to_digit(16).map(|d| acc << 4 | d))
}

impl Parser<'_> {
    fn skip(&self, mut i: usize) -> usize {
        while i < self.s.len() && is_ws(self.s[i]) {
            i += 1;
        }
        i
    }

    fn at(&self, i: usize) -> u32 {
        self.s.get(i).copied().unwrap_or(u32::MAX)
    }

    /// Whether `word` is at `i` (the C scanner's `idx + len - 1 < length` and character checks).
    fn literal(&self, i: usize, word: &str) -> bool {
        let word: Vec<u32> = word.chars().map(u32::from).collect();
        self.s.get(i..i + word.len()) == Some(&word[..])
    }

    /// `scan_once`: one value at `i`, recorded at `node`; returns it and the offset after it.
    fn value(&mut self, i: usize, node: usize) -> Result<(Value, usize), Failure> {
        self.positions.values[node] = Some(i);
        let Some(&c) = self.s.get(i) else {
            return Err(Failure::Syntax("Expecting value", i));
        };
        match char::from_u32(c) {
            Some('"') => {
                let (s, end) = self.string(i + 1)?;
                Ok((Value::Str(s), end))
            }
            Some('{') | Some('[') => {
                if self.depth >= MAX_DEPTH {
                    return Err(Failure::TooDeep);
                }
                self.depth += 1;
                let out = if c == u32::from('{') { self.object(i + 1, node) } else { self.array(i + 1, node) };
                self.depth -= 1;
                out
            }
            Some('n') if self.literal(i, "null") => Ok((Value::Null, i + 4)),
            Some('t') if self.literal(i, "true") => Ok((Value::Bool(true), i + 4)),
            Some('f') if self.literal(i, "false") => Ok((Value::Bool(false), i + 5)),
            Some('N') if self.literal(i, "NaN") => Ok((self.constant(i, "NaN", f64::NAN), i + 3)),
            Some('I') if self.literal(i, "Infinity") => Ok((self.constant(i, "Infinity", f64::INFINITY), i + 8)),
            Some('-') if self.literal(i, "-Infinity") => {
                Ok((self.constant(i, "-Infinity", f64::NEG_INFINITY), i + 9))
            }
            _ => self.number(i),
        }
    }

    fn constant(&mut self, i: usize, literal: &str, value: f64) -> Value {
        let mut message = Text::new();
        push(&mut message, &format!("{literal} is not allowed in JSON"));
        self.found.push(Found { message, offset: i, path: dotted(&self.path) });
        Value::Float(value)
    }

    /// `_match_number_unicode`.
    fn number(&mut self, start: usize) -> Result<(Value, usize), Failure> {
        let s = self.s;
        let n = s.len();
        let mut i = start;
        if self.at(i) == u32::from('-') {
            i += 1;
            if i >= n {
                return Err(Failure::Syntax("Expecting value", start));
            }
        }
        let c = s[i];
        if (0x31..=0x39).contains(&c) {
            i += 1;
            while i < n && is_digit(s[i]) {
                i += 1;
            }
        } else if c == u32::from('0') {
            i += 1;
        } else {
            return Err(Failure::Syntax("Expecting value", start));
        }
        let mut is_float = false;
        if i + 1 < n && s[i] == u32::from('.') && is_digit(s[i + 1]) {
            is_float = true;
            i += 2;
            while i < n && is_digit(s[i]) {
                i += 1;
            }
        }
        if i + 1 < n && (s[i] == u32::from('e') || s[i] == u32::from('E')) {
            let e_start = i;
            i += 1;
            if i + 1 < n && (s[i] == u32::from('-') || s[i] == u32::from('+')) {
                i += 1;
            }
            while i < n && is_digit(s[i]) {
                i += 1;
            }
            if is_digit(s[i - 1]) {
                is_float = true;
            } else {
                i = e_start;
            }
        }
        let literal: String = s[start..i].iter().map(|&c| char::from(c as u8)).collect();
        if is_float {
            return Ok((Value::Float(literal.parse().unwrap_or(f64::NAN)), i));
        }
        if literal.trim_start_matches('-').len() > MAX_INT_DIGITS {
            return Err(Failure::LongInt);
        }
        Ok((Value::Int(Int::parse(&literal)), i))
    }

    /// `scanstring_unicode`; `end` is just past the opening quote.
    fn string(&self, mut end: usize) -> Result<(Text, usize), Failure> {
        let s = self.s;
        let n = s.len();
        let begin = end - 1;
        let mut out = Text::new();
        loop {
            let mut next = end;
            while next < n && s[next] != u32::from('"') && s[next] != u32::from('\\') {
                if s[next] <= 0x1f {
                    return Err(Failure::Syntax("Invalid control character at", next));
                }
                next += 1;
            }
            if next >= n {
                return Err(Failure::Syntax("Unterminated string starting at", begin));
            }
            out.extend_from_slice(&s[end..next]);
            if s[next] == u32::from('"') {
                return Ok((out, next + 1));
            }
            next += 1;
            if next == n {
                return Err(Failure::Syntax("Unterminated string starting at", begin));
            }
            let c = s[next];
            if c != u32::from('u') {
                end = next + 1;
                let decoded = match char::from_u32(c) {
                    Some(c @ ('"' | '\\' | '/')) => u32::from(c),
                    Some('b') => 0x08,
                    Some('f') => 0x0c,
                    Some('n') => 0x0a,
                    Some('r') => 0x0d,
                    Some('t') => 0x09,
                    _ => return Err(Failure::Syntax("Invalid \\escape", end - 2)),
                };
                out.push(decoded);
                continue;
            }
            next += 1;
            end = next + 4;
            if end >= n {
                return Err(Failure::Syntax("Invalid \\uXXXX escape", next - 1));
            }
            let mut c = hex4(&s[next..end]).ok_or(Failure::Syntax("Invalid \\uXXXX escape", end - 5))?;
            if (0xd800..0xdc00).contains(&c) && end + 6 < n && s[end] == u32::from('\\') && s[end + 1] == u32::from('u') {
                let low = hex4(&s[end + 2..end + 6]).ok_or(Failure::Syntax("Invalid \\uXXXX escape", end + 1))?;
                if (0xdc00..0xe000).contains(&low) {
                    c = 0x10000 + ((c - 0xd800) << 10) + (low - 0xdc00);
                    end += 6;
                }
            }
            out.push(c);
        }
    }

    fn object(&mut self, i: usize, node: usize) -> Result<(Value, usize), Failure> {
        let mut dict = Dict::new();
        let mut i = self.skip(i);
        if self.at(i) != u32::from('}') {
            loop {
                if self.at(i) != u32::from('"') {
                    return Err(Failure::Syntax("Expecting property name enclosed in double quotes", i));
                }
                let key_start = i;
                let (key, after) = self.string(i + 1)?;
                self.path.push(Part::Key(key.clone()));
                if dict.contains(&key) {
                    let mut message = Text::new();
                    push(&mut message, "duplicate key '");
                    message.extend(&key);
                    message.push(u32::from('\''));
                    self.found.push(Found { message, offset: key_start, path: dotted(&self.path) });
                }
                let child = self.positions.child(node, Part::Key(key.clone()));
                self.positions.keys[child] = Some(key_start);
                i = self.skip(after);
                if self.at(i) != u32::from(':') {
                    return Err(Failure::Syntax("Expecting ':' delimiter", i));
                }
                i = self.skip(i + 1);
                let (value, after) = self.value(i, child)?;
                self.path.pop();
                dict.insert(key, value);
                i = self.skip(after);
                if self.at(i) == u32::from('}') {
                    break;
                }
                if self.at(i) != u32::from(',') {
                    return Err(Failure::Syntax("Expecting ',' delimiter", i));
                }
                i = self.skip(i + 1);
            }
        }
        Ok((Value::Dict(dict), i + 1))
    }

    fn array(&mut self, i: usize, node: usize) -> Result<(Value, usize), Failure> {
        let mut items = Vec::new();
        let mut i = self.skip(i);
        if self.at(i) != u32::from(']') {
            loop {
                let child = self.positions.child(node, Part::Index(items.len()));
                self.path.push(Part::Index(items.len()));
                let (value, after) = self.value(i, child)?;
                self.path.pop();
                items.push(value);
                i = self.skip(after);
                if self.at(i) == u32::from(']') {
                    break;
                }
                if self.at(i) != u32::from(',') {
                    return Err(Failure::Syntax("Expecting ',' delimiter", i));
                }
                i = self.skip(i + 1);
            }
        }
        Ok((Value::List(items), i + 1))
    }
}
