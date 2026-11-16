//! `kitty.core.math.templates`: the problem generators, one module per strand, each a port of
//! its Python module with the same draws from the seeded `random.Random` in the same order.

pub mod arithmetic;
pub mod calculus;
pub mod counting;
pub mod discrete;
pub mod expectation;
pub mod linalg;
pub mod probability;
pub mod statistics;

use super::problems::Template;

/// Every strand in Python's import order (`templates/__init__.py`), so the registry keeps
/// `TEMPLATES`' insertion order.
pub static STRANDS: &[&[Template]] = &[
    arithmetic::TEMPLATES,
    calculus::TEMPLATES,
    counting::TEMPLATES,
    discrete::TEMPLATES,
    expectation::TEMPLATES,
    linalg::TEMPLATES,
    probability::TEMPLATES,
    statistics::TEMPLATES,
];

/// Python's `max(items, key=key)`: the first item with the largest key.
pub fn max_by<T: Clone, K: PartialOrd>(items: &[T], key: impl Fn(&T) -> K) -> T {
    let mut best = &items[0];
    for item in &items[1..] {
        if key(item) > key(best) {
            best = item;
        }
    }
    best.clone()
}
