//! CPython 3.12 `SequenceMatcher(None, a, b, autojunk=False)`.
//! No popular-element suppression or junk predicate; ties favor earliest a, then b.
use std::collections::HashMap;
use std::hash::Hash;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Op { Equal, Replace, Delete, Insert }

pub fn opcodes<T: Eq + Hash>(a: &[T], b: &[T]) -> Vec<(Op, usize, usize, usize, usize)> {
    let mut positions: HashMap<&T, Vec<usize>> = HashMap::new();
    for (j, value) in b.iter().enumerate() {
        positions.entry(value).or_default().push(j);
    }
    let mut queue = vec![(0, a.len(), 0, b.len())];
    let mut blocks = Vec::new();
    let mut lengths = HashMap::<usize, usize>::new();
    let mut next = HashMap::<usize, usize>::new();
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (mut best_i, mut best_j, mut size) = (alo, blo, 0);
        lengths.clear();
        for i in alo..ahi {
            next.clear();
            if let Some(js) = positions.get(&a[i]) {
                for &j in js {
                    if j < blo { continue; }
                    if j >= bhi { break; }
                    let k = if j == 0 { 1 } else { lengths.get(&(j - 1)).copied().unwrap_or(0) + 1 };
                    next.insert(j, k);
                    if k > size { (best_i, best_j, size) = (i + 1 - k, j + 1 - k, k); }
                }
            }
            std::mem::swap(&mut lengths, &mut next);
        }
        // CPython extends the best block through non-junk; with no junk these
        // loops are still part of find_longest_match's contract.
        while best_i > alo && best_j > blo && a[best_i - 1] == b[best_j - 1] {
            best_i -= 1; best_j -= 1; size += 1;
        }
        while best_i + size < ahi && best_j + size < bhi && a[best_i + size] == b[best_j + size] {
            size += 1;
        }
        if size != 0 {
            blocks.push((best_i, best_j, size));
            if alo < best_i && blo < best_j { queue.push((alo, best_i, blo, best_j)); }
            if best_i + size < ahi && best_j + size < bhi {
                queue.push((best_i + size, ahi, best_j + size, bhi));
            }
        }
    }
    blocks.sort_unstable();
    let mut merged: Vec<(usize, usize, usize)> = Vec::with_capacity(blocks.len() + 1);
    for (i, j, size) in blocks {
        if let Some(last) = merged.last_mut() {
            if last.0 + last.2 == i && last.1 + last.2 == j { last.2 += size; continue; }
        }
        merged.push((i, j, size));
    }
    merged.push((a.len(), b.len(), 0));
    let (mut i, mut j) = (0, 0);
    let mut out = Vec::new();
    for (ai, bj, size) in merged {
        let tag = match (i < ai, j < bj) {
            (true, true) => Some(Op::Replace), (true, false) => Some(Op::Delete),
            (false, true) => Some(Op::Insert), (false, false) => None,
        };
        if let Some(tag) = tag { out.push((tag, i, ai, j, bj)); }
        if size != 0 { out.push((Op::Equal, ai, ai + size, bj, bj + size)); }
        (i, j) = (ai + size, bj + size);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ties_and_empty_sequences() {
        assert_eq!(opcodes(b"ab", b"ba"), vec![(Op::Insert,0,0,0,1), (Op::Equal,0,1,1,2), (Op::Delete,1,2,2,2)]);
        assert_eq!(opcodes(b"", b"a"), vec![(Op::Insert,0,0,0,1)]);
        assert!(opcodes::<u8>(b"", b"").is_empty());
        assert_eq!(opcodes(b"abc", b"axc"), vec![(Op::Equal,0,1,0,1), (Op::Replace,1,2,1,2), (Op::Equal,2,3,2,3)]);
    }
    #[test]
    fn no_autojunk() {
        let a = vec![1; 300];
        let mut b = vec![2]; b.extend_from_slice(&a);
        assert_eq!(opcodes(&a, &b), vec![(Op::Insert,0,0,0,1), (Op::Equal,0,300,1,301)]);
    }
}
