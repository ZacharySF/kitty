//! `kitty.dates` as the job routes use it: stored stamps are `%Y-%m-%dT%H:%M:%SZ` in UTC,
//! read back with `datetime.fromisoformat` (`Z` taken as UTC, a naive stamp as UTC), and
//! what the owner types in a date field (`parse_user_date`).

use crate::ported::time::{fromisoformat, Dt, Zone, DAY, HOUR, MINUTE, SECOND};
use crate::ported::{internal, py, Done};
use crate::search::text::{decimal_value, is_decimal};

/// `now()`: the test clock (`KITTY_TEST_CLOCK`) or the system's, in UTC.
pub fn now() -> Dt {
    Dt::now_utc()
}

/// `to_iso`: the moment in UTC as `strftime("%Y-%m-%dT%H:%M:%SZ")` (glibc's `%Y` is unpadded).
pub fn to_iso(moment: &Dt) -> Done<String> {
    let utc = moment.astimezone(&Zone::Utc)?;
    let date = utc.date();
    let rest = utc.time_of_day();
    Ok(format!("{}-{:02}-{:02}T{:02}:{:02}:{:02}Z", date.year, date.month, date.day, rest / HOUR, rest % HOUR / MINUTE,
               rest % MINUTE / SECOND))
}

/// `now_iso()`.
pub fn now_iso() -> Done<String> {
    to_iso(&now())
}

/// `parse(value)`: None for an empty or unparseable stamp; a naive one is UTC.
pub fn parse(value: Option<&str>) -> Option<Dt> {
    let value = value.filter(|text| !text.is_empty())?;
    let parsed = fromisoformat(&py::strip(value).replace('Z', "+00:00"))?;
    Some(if parsed.zone.is_some() { parsed } else { parsed.with_zone(Zone::Utc, false) })
}

/// `days_since(value)`: whole days from the stamp to now, never negative; 0 when unknown.
pub fn days_since(value: Option<&str>) -> i64 {
    parse(value).map_or(0, |stamp| now().sub(&stamp).div_euclid(DAY).max(0))
}

/// Characters `str.isdigit` accepts that `int()` refuses (Numeric_Type=Digit, Unicode 15).
const DIGIT_ONLY: &[(u32, u32)] = &[
    (0xB2, 0xB3), (0xB9, 0xB9), (0x1369, 0x1371), (0x19DA, 0x19DA), (0x2070, 0x2070), (0x2074, 0x2079),
    (0x2080, 0x2089), (0x2460, 0x2468), (0x2474, 0x247C), (0x2488, 0x2490), (0x24EA, 0x24EA), (0x24F5, 0x24FD),
    (0x24FF, 0x24FF), (0x2776, 0x277E), (0x2780, 0x2788), (0x278A, 0x2792), (0x10A40, 0x10A43), (0x10E60, 0x10E68),
    (0x11052, 0x1105A), (0x1F100, 0x1F10A),
];

fn is_digit(c: char) -> bool {
    is_decimal(c) || DIGIT_ONLY.iter().any(|(low, high)| (*low..=*high).contains(&(c as u32)))
}

/// `timedelta(days=count)` added to a moment: past year 9999 (or past `timedelta`'s own
/// limit) is Python's `OverflowError`.
fn days(count: u128) -> Done<i64> {
    i64::try_from(count).ok().and_then(|count| count.checked_mul(DAY)).ok_or_else(internal)
}

/// `parse_user_date(value)`: `today`, `tomorrow`, `+7`/`7d` (days from now), or a stamp.
pub fn parse_user_date(value: &str) -> Done<Option<String>> {
    let text = py::strip(value).to_lowercase();
    if text.is_empty() {
        return Ok(None);
    }
    if text == "today" {
        return to_iso(&now()).map(Some);
    }
    if text == "tomorrow" {
        return to_iso(&now().add(DAY)?).map(Some);
    }
    let offset = text.strip_prefix('+').unwrap_or(&text);
    let offset = offset.strip_suffix('d').unwrap_or(offset);
    if !offset.is_empty() && offset.chars().all(is_digit) {
        // `int()` reads decimal digits of any script; a superscript is its ValueError.
        let count = decimal_value(offset).filter(|_| offset.chars().all(is_decimal)).ok_or_else(internal)?;
        return to_iso(&now().add(days(count)?)?).map(Some);
    }
    parse(Some(value)).map(|stamp| to_iso(&stamp)).transpose()
}
