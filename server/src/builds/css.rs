//! `kitty.core.builds.css`: the policy for a build's custom CSS and theme token values, on CSS
//! tokens (CSS Syntax Level 3) so escapes are decoded before names are compared. The messages,
//! lines and columns are the Python module's, character for character.

use super::unicode;
use super::value::{in_ranges, lower, push, strip, Text};

pub const MAX_CSS: usize = 65536;
const MAX_DEPTH: usize = 32;
const ALLOWED_AT_RULES: [&str; 3] = ["media", "supports", "container"];
const FORBIDDEN_FUNCTIONS: [&str; 11] = [
    "url", "src", "image", "image-set", "-webkit-image-set", "cross-fade", "-webkit-cross-fade",
    "element", "-moz-element", "expression", "paint",
];
const FORBIDDEN_PROPERTIES: [&str; 4] = ["behavior", "-moz-binding", "-o-link", "-o-link-source"];
const URL_REFUSED: &str = "url() may only hold an inline data:image (PNG, JPEG, GIF, WebP or AVIF, base64); \
                           remote and local addresses are not allowed";

pub struct CssProblem {
    pub message: Text,
    pub line: usize,
    pub column: usize,
}

#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Ws,
    Comment,
    Str,
    Ident,
    Function,
    At,
    Hash,
    Number,
    Delim,
    Url,
    Open,
    Close,
    Semicolon,
    Colon,
    Comma,
    Cdx,
}

struct Token {
    kind: Kind,
    value: Text,
    line: usize,
    column: usize,
}

fn is(c: Option<u32>, chars: &str) -> bool {
    c.and_then(char::from_u32).is_some_and(|c| chars.contains(c))
}

fn eq(t: &[u32], s: &str) -> bool {
    t.iter().copied().eq(s.chars().map(u32::from))
}

fn is_hex(c: Option<u32>) -> bool {
    c.and_then(char::from_u32).is_some_and(|c| c.is_ascii_hexdigit())
}

/// `str.isdigit()` of one character (False at the end of the text).
fn is_digit(c: Option<u32>) -> bool {
    c.is_some_and(|c| in_ranges(unicode::DIGIT, c))
}

/// `char.isalpha()` where it matters: below U+0080 (above it every character counts anyway).
fn ascii_alpha(c: u32) -> bool {
    char::from_u32(c).is_some_and(|c| c.is_ascii_alphabetic())
}

fn name_char(c: u32) -> bool {
    char::from_u32(c).is_some_and(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') || c >= 0x80
}

/// Whether an identifier starts at `t[i]` (CSS Syntax 4.3.9).
fn starts_name(t: &[u32], i: usize) -> bool {
    let valid_escape = |j: usize| j + 1 < t.len() && t[j] == u32::from('\\') && t[j + 1] != u32::from('\n');
    let Some(&first) = t.get(i) else {
        return false;
    };
    if first == u32::from('-') {
        return match t.get(i + 1) {
            None => false,
            Some(&second) => {
                ascii_alpha(second) || is(Some(second), "-_") || second >= 0x80 || valid_escape(i + 1)
            }
        };
    }
    ascii_alpha(first) || first == u32::from('_') || first >= 0x80 || valid_escape(i)
}

struct Tokenizer<'a> {
    t: &'a [u32],
    i: usize,
    line: usize,
    column: usize,
    problems: Vec<CssProblem>,
}

impl Tokenizer<'_> {
    fn advance(&mut self, count: usize) -> &[u32] {
        let end = (self.i + count).min(self.t.len());
        let chunk = &self.t[self.i..end];
        for &c in chunk {
            if c == u32::from('\n') {
                self.line += 1;
                self.column = 1;
            } else {
                self.column += 1;
            }
        }
        self.i = end;
        chunk
    }

    fn take(&mut self, out: &mut Text) {
        let c = self.advance(1).to_vec();
        out.extend(c);
    }

    fn peek(&self, offset: usize) -> Option<u32> {
        self.t.get(self.i + offset).copied()
    }

    fn problem(&mut self, message: &str, line: usize, column: usize) {
        let mut text = Text::new();
        push(&mut text, message);
        self.problems.push(CssProblem { message: text, line, column });
    }

    /// An escape after the backslash: the character it stands for.
    fn escape(&mut self) -> u32 {
        let Some(c) = self.peek(0) else {
            return 0xfffd;
        };
        if is_hex(Some(c)) {
            let mut code = 0u32;
            let mut digits = 0;
            while digits < 6 && is_hex(self.peek(0)) {
                code = code << 4 | char::from_u32(self.advance(1)[0]).and_then(|c| c.to_digit(16)).unwrap_or(0);
                digits += 1;
            }
            if is(self.peek(0), " \t\n") {
                self.advance(1);
            }
            return if 0 < code && code <= 0x10ffff && !(0xd800..=0xdfff).contains(&code) { code } else { 0xfffd };
        }
        self.advance(1)[0]
    }

    fn name(&mut self) -> Text {
        let mut out = Text::new();
        while let Some(c) = self.peek(0) {
            if name_char(c) {
                self.take(&mut out);
            } else if c == u32::from('\\') && self.peek(1).is_some_and(|n| n != u32::from('\n')) {
                self.advance(1);
                out.push(self.escape());
            } else {
                break;
            }
        }
        out
    }

    fn string(&mut self, quote: u32, line: usize, column: usize) -> Token {
        self.advance(1);
        let mut value = Text::new();
        loop {
            let Some(c) = self.peek(0) else {
                self.problem("unterminated string", line, column);
                break;
            };
            if c == quote {
                self.advance(1);
                break;
            }
            if c == u32::from('\n') {
                self.problem("unterminated string (a line break inside quotes)", line, column);
                break;
            }
            if c == u32::from('\\') {
                self.advance(1);
                if self.peek(0) == Some(u32::from('\n')) {
                    self.advance(1);
                } else {
                    value.push(self.escape());
                }
                continue;
            }
            self.take(&mut value);
        }
        Token { kind: Kind::Str, value, line, column }
    }

    fn skip_spaces(&mut self) {
        while is(self.peek(0), " \t\n") {
            self.advance(1);
        }
    }

    /// An unquoted url( token; `url(` is consumed.
    fn url(&mut self, line: usize, column: usize) -> Token {
        self.skip_spaces();
        let mut value = Text::new();
        loop {
            let Some(c) = self.peek(0) else {
                self.problem("unterminated url(", line, column);
                break;
            };
            if c == u32::from(')') {
                self.advance(1);
                break;
            }
            if c == u32::from('\\') && self.peek(1).is_some_and(|n| n != u32::from('\n')) {
                self.advance(1);
                value.push(self.escape());
                continue;
            }
            self.take(&mut value);
        }
        Token { kind: Kind::Url, value: strip(&value).to_vec(), line, column }
    }

    fn number(&mut self, line: usize, column: usize) -> Token {
        let start = self.i;
        if is(self.peek(0), "+-") {
            self.advance(1);
        }
        while is_digit(self.peek(0)) {
            self.advance(1);
        }
        if self.peek(0) == Some(u32::from('.')) && is_digit(self.peek(1)) {
            self.advance(1);
            while is_digit(self.peek(0)) {
                self.advance(1);
            }
        }
        if is(self.peek(0), "eE") && (is_digit(self.peek(1)) || (is(self.peek(1), "+-") && is_digit(self.peek(2)))) {
            self.advance(2);
            while is_digit(self.peek(0)) {
                self.advance(1);
            }
        }
        let mut value = self.t[start..self.i].to_vec();
        if starts_name(self.t, self.i) {
            value.extend(self.name());
        } else if self.peek(0) == Some(u32::from('%')) {
            self.take(&mut value);
        }
        Token { kind: Kind::Number, value, line, column }
    }

    fn starts_with(&self, s: &str) -> bool {
        let n = s.chars().count();
        self.t.get(self.i..self.i + n).is_some_and(|t| eq(t, s))
    }

    fn tokens(&mut self) -> Vec<Token> {
        let mut out = Vec::new();
        let token = |kind, value: &str, line, column| {
            let mut text = Text::new();
            push(&mut text, value);
            Token { kind, value: text, line, column }
        };
        while let Some(c) = self.peek(0) {
            let (line, column) = (self.line, self.column);
            let ch = char::from_u32(c).unwrap_or('\u{fffd}');
            if " \t\n\r\x0c".contains(ch) {
                while is(self.peek(0), " \t\n\r\x0c") {
                    self.advance(1);
                }
                out.push(token(Kind::Ws, " ", line, column));
            } else if ch == '/' && self.peek(1) == Some(u32::from('*')) {
                let end = (self.i + 2..self.t.len().saturating_sub(1))
                    .find(|&j| self.t[j] == u32::from('*') && self.t[j + 1] == u32::from('/'));
                match end {
                    None => {
                        self.problem("unterminated comment (/* without */)", line, column);
                        self.advance(self.t.len() - self.i);
                    }
                    Some(end) => {
                        self.advance(end + 2 - self.i);
                    }
                }
                out.push(token(Kind::Comment, "", line, column));
            } else if ch == '"' || ch == '\'' {
                let string = self.string(c, line, column);
                out.push(string);
            } else if ch == '#' {
                self.advance(1);
                let value = self.name();
                out.push(Token { kind: Kind::Hash, value, line, column });
            } else if ch == '@' {
                self.advance(1);
                if starts_name(self.t, self.i) {
                    let value = lower(&self.name());
                    out.push(Token { kind: Kind::At, value, line, column });
                } else {
                    out.push(token(Kind::Delim, "@", line, column));
                }
            } else if is_digit(Some(c))
                || (ch == '.' && is_digit(self.peek(1)))
                || ("+-".contains(ch)
                    && (is_digit(self.peek(1)) || (self.peek(1) == Some(u32::from('.')) && is_digit(self.peek(2)))))
            {
                let number = self.number(line, column);
                out.push(number);
            } else if ch == '<' && self.starts_with("<!--") {
                self.advance(4);
                out.push(token(Kind::Cdx, "<!--", line, column));
            } else if ch == '-' && self.starts_with("-->") {
                self.advance(3);
                out.push(token(Kind::Cdx, "-->", line, column));
            } else if starts_name(self.t, self.i) {
                let name = self.name();
                if self.peek(0) == Some(u32::from('(')) {
                    self.advance(1);
                    let lowered = lower(&name);
                    if eq(&lowered, "url") {
                        // url( followed by a quote is a function holding a string token. (So is
                        // url( at the end of the text: Python's `"" in "\"'"` is True.)
                        self.skip_spaces();
                        if self.peek(0).is_none() || is(self.peek(0), "\"'") {
                            out.push(token(Kind::Function, "url", line, column));
                        } else {
                            let url = self.url(line, column);
                            out.push(url);
                        }
                    } else {
                        out.push(Token { kind: Kind::Function, value: lowered, line, column });
                    }
                } else {
                    out.push(Token { kind: Kind::Ident, value: name, line, column });
                }
            } else if "{[(".contains(ch) {
                self.advance(1);
                out.push(Token { kind: Kind::Open, value: vec![c], line, column });
            } else if "}])".contains(ch) {
                self.advance(1);
                out.push(Token { kind: Kind::Close, value: vec![c], line, column });
            } else if ch == ';' {
                self.advance(1);
                out.push(token(Kind::Semicolon, ";", line, column));
            } else if ch == ':' {
                self.advance(1);
                out.push(token(Kind::Colon, ":", line, column));
            } else if ch == ',' {
                self.advance(1);
                out.push(token(Kind::Comma, ",", line, column));
            } else if ch == '\\' && self.peek(1) == Some(u32::from('\n')) {
                self.problem("invalid escape (backslash before a line break)", line, column);
                self.advance(1);
            } else if c == 0 {
                self.problem("NUL characters are not allowed", line, column);
                self.advance(1);
            } else {
                self.advance(1);
                out.push(Token { kind: Kind::Delim, value: vec![c], line, column });
            }
        }
        out
    }
}

/// re.I's equality with a lowercase ASCII pattern character, folds included (İ, ı ~ i; ſ ~ s; K ~ k).
fn fold(c: u32) -> u32 {
    match c {
        0x130 | 0x131 => u32::from('i'),
        0x17f => u32::from('s'),
        0x212a => u32::from('k'),
        0x41..=0x5a => c + 0x20,
        _ => c,
    }
}

/// `DATA_IMAGE.fullmatch(value)`: `data:image/(png|jpeg|gif|webp|avif);base64,[A-Za-z0-9+/=\s]*`, re.I.
fn data_image(value: &[u32]) -> bool {
    let literal = |at: usize, s: &str| -> Option<usize> {
        let n = s.chars().count();
        let part = value.get(at..at + n)?;
        part.iter().zip(s.chars()).all(|(&c, p)| fold(c) == u32::from(p)).then_some(at + n)
    };
    let Some(at) = literal(0, "data:image/") else {
        return false;
    };
    let Some(at) = ["png", "jpeg", "gif", "webp", "avif"].iter().find_map(|kind| literal(at, kind)) else {
        return false;
    };
    let Some(at) = literal(at, ";base64,") else {
        return false;
    };
    value[at..].iter().all(|&c| {
        let f = fold(c);
        char::from_u32(f).is_some_and(|f| f.is_ascii_lowercase() || f.is_ascii_digit() || "+/=".contains(f))
            || in_ranges(unicode::SPACE, c)
    })
}

fn add(problems: &mut Vec<CssProblem>, message: Text, token: &Token) {
    problems.push(CssProblem { message, line: token.line, column: token.column });
}

fn msg(s: &str) -> Text {
    let mut out = Text::new();
    push(&mut out, s);
    out
}

/// `_check`: the policy over a token stream; `value` is a single property value (theme token).
fn policy(tokens: &[Token], problems: &mut Vec<CssProblem>, value: bool) {
    let significant: Vec<&Token> = tokens.iter().filter(|t| !matches!(t.kind, Kind::Ws | Kind::Comment)).collect();
    let mut stack: Vec<(u32, usize, usize)> = Vec::new();
    for (index, token) in significant.iter().enumerate() {
        let following = significant.get(index + 1);
        let v = &token.value[..];
        match token.kind {
            Kind::Url => {
                if !data_image(v) {
                    add(problems, msg(URL_REFUSED), token);
                }
            }
            Kind::Function => {
                if eq(v, "url") {
                    let inner = match following {
                        Some(next) if next.kind == Kind::Str => &next.value[..],
                        _ => &[],
                    };
                    if !data_image(inner) {
                        add(problems, msg(URL_REFUSED), token);
                    }
                } else if FORBIDDEN_FUNCTIONS.iter().any(|f| eq(v, f)) {
                    let mut m = v.to_vec();
                    push(&mut m, "() can load or run outside resources and is not allowed");
                    add(problems, m, token);
                }
                stack.push((u32::from('('), token.line, token.column));
                if stack.len() > MAX_DEPTH {
                    add(problems, msg("nested too deeply"), token);
                    return;
                }
            }
            Kind::At => {
                if value {
                    add(problems, msg("at-rules are not allowed in a token value"), token);
                } else if eq(v, "import") {
                    add(problems, msg("@import is not allowed: a build cannot load other stylesheets"), token);
                } else if !ALLOWED_AT_RULES.iter().any(|a| eq(v, a)) {
                    let mut m = msg("@");
                    m.extend(v);
                    push(&mut m, " is not allowed; use @media, @supports or @container");
                    add(problems, m, token);
                }
            }
            Kind::Ident
                if following.is_some_and(|next| next.kind == Kind::Colon)
                    && FORBIDDEN_PROPERTIES.iter().any(|p| eq(&lower(v), p)) =>
            {
                let mut m = msg("the ");
                m.extend(v);
                push(&mut m, " property is not allowed");
                add(problems, m, token);
            }
            Kind::Cdx => add(problems, msg("HTML comment markers are not allowed"), token),
            Kind::Open => {
                if value && eq(v, "{") {
                    add(problems, msg("braces are not allowed in a token value"), token);
                }
                stack.push((v[0], token.line, token.column));
                if stack.len() > MAX_DEPTH {
                    add(problems, msg("nested too deeply"), token);
                    return;
                }
            }
            Kind::Close => {
                if value && eq(v, "}") {
                    add(problems, msg("braces are not allowed in a token value"), token);
                    return;
                }
                let closes = |open: u32| match char::from_u32(open) {
                    Some('{') => u32::from('}'),
                    Some('[') => u32::from(']'),
                    _ => u32::from(')'),
                };
                if stack.last().is_none_or(|&(open, _, _)| closes(open) != v[0]) {
                    let mut m = msg("unexpected '");
                    m.extend(v);
                    m.push(u32::from('\''));
                    add(problems, m, token);
                    return;
                }
                stack.pop();
            }
            Kind::Semicolon if value => add(problems, msg("semicolons are not allowed in a token value"), token),
            Kind::Delim if value && eq(v, "!") => add(problems, msg("!important is not allowed in a token value"), token),
            _ => {}
        }
    }
    for (open, line, column) in stack {
        let mut m = msg("'");
        m.push(open);
        push(&mut m, "' is never closed");
        problems.push(CssProblem { message: m, line, column });
    }
}

fn run(text: &[u32], value: bool) -> Vec<CssProblem> {
    let mut tokenizer = Tokenizer { t: text, i: 0, line: 1, column: 1, problems: Vec::new() };
    let tokens = tokenizer.tokens();
    let mut problems = tokenizer.problems;
    policy(&tokens, &mut problems, value);
    problems
}

/// `check_css`: problems with a custom stylesheet; empty when it may be applied as written.
pub fn check_css(text: &[u32]) -> Vec<CssProblem> {
    if text.len() > MAX_CSS {
        return vec![CssProblem { message: msg(&format!("custom CSS is limited to {MAX_CSS} characters")), line: 1, column: 1 }];
    }
    let mut problems = run(text, false);
    problems.sort_by_key(|p| (p.line, p.column));
    problems
}

/// `check_value`: problems with one theme token's CSS value (no blocks, rules or loads).
pub fn check_value(text: &[u32]) -> Vec<CssProblem> {
    let mut problems = run(text, true);
    if strip(text).is_empty() {
        problems.push(CssProblem { message: msg("a token value cannot be empty"), line: 1, column: 1 });
    }
    problems.sort_by_key(|p| (p.line, p.column));
    problems
}
