//! The small regular expressions the build schema uses, matched as Python's `re.search` does.
//!
//! Only what the schema needs: an optional `^`, literal characters and `[...]` classes
//! (ranges allowed, no negation or escapes), each optionally followed by `*`, `+`, `?`, `{n}`
//! or `{m,n}`, and an optional final `$`. As in Python, `$` also matches before a final
//! newline. `compile` returns None for anything else (a unit test compiles every schema pattern).

use super::value::Text;

pub struct Pattern {
    anchored: bool,
    atoms: Vec<Atom>,
    dollar: bool,
}

struct Atom {
    ranges: Vec<(u32, u32)>,
    min: usize,
    max: usize,
}

const SPECIAL: &str = ".*+?()|{}\\^$[]";

impl Pattern {
    pub fn compile(pattern: &[u32]) -> Option<Pattern> {
        let mut p = pattern;
        let anchored = p.first() == Some(&u32::from('^'));
        if anchored {
            p = &p[1..];
        }
        let dollar = p.last() == Some(&u32::from('$'));
        if dollar {
            p = &p[..p.len() - 1];
        }
        let mut atoms = Vec::new();
        let mut i = 0;
        while i < p.len() {
            let c = p[i];
            let ranges = if c == u32::from('[') {
                let mut ranges = Vec::new();
                i += 1;
                if p.get(i) == Some(&u32::from('^')) {
                    return None;
                }
                loop {
                    let &first = p.get(i)?;
                    if first == u32::from(']') && !ranges.is_empty() {
                        i += 1;
                        break;
                    }
                    if first == u32::from('\\') {
                        return None;
                    }
                    if p.get(i + 1) == Some(&u32::from('-')) && p.get(i + 2).is_some_and(|&c| c != u32::from(']')) {
                        ranges.push((first, p[i + 2]));
                        i += 3;
                    } else {
                        ranges.push((first, first));
                        i += 1;
                    }
                }
                ranges
            } else if char::from_u32(c).is_some_and(|c| SPECIAL.contains(c)) {
                return None;
            } else {
                i += 1;
                vec![(c, c)]
            };
            let (min, max) = match p.get(i).and_then(|&c| char::from_u32(c)) {
                Some('*') => (0, usize::MAX),
                Some('+') => (1, usize::MAX),
                Some('?') => (0, 1),
                Some('{') => {
                    let close = i + p[i..].iter().position(|&c| c == u32::from('}'))?;
                    let inner: String = p[i + 1..close].iter().map(|&c| char::from_u32(c).unwrap_or('x')).collect();
                    i = close;
                    match inner.split_once(',') {
                        Some((m, n)) => (m.parse().ok()?, n.parse().ok()?),
                        None => {
                            let n = inner.parse().ok()?;
                            (n, n)
                        }
                    }
                }
                _ => (1, 1),
            };
            if (min, max) != (1, 1) {
                i += 1;
            }
            atoms.push(Atom { ranges, min, max });
        }
        Some(Pattern { anchored, atoms, dollar })
    }

    /// `re.search(pattern, text) is not None`.
    pub fn search(&self, text: &[u32]) -> bool {
        let last = if self.anchored { 0 } else { text.len() };
        (0..=last).any(|start| self.matches(0, text, start))
    }

    fn matches(&self, atom: usize, text: &[u32], at: usize) -> bool {
        let Some(current) = self.atoms.get(atom) else {
            return !self.dollar || at == text.len() || (at + 1 == text.len() && text[at] == u32::from('\n'));
        };
        let fits = |c: &u32| current.ranges.iter().any(|&(a, b)| a <= *c && *c <= b);
        let most = text[at..].iter().take(current.max).take_while(|c| fits(c)).count();
        (current.min..=most).rev().any(|count| self.matches(atom + 1, text, at + count))
    }
}

pub fn compile_str(pattern: &Text) -> Pattern {
    Pattern::compile(pattern).unwrap_or_else(|| panic!("unsupported schema pattern {}", super::value::lossy(pattern)))
}
