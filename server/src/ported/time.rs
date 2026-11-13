//! Python's `datetime` as the ported modules' Python code used it: wall-clock values with a
//! zone and PEP 495's `fold`, `ZoneInfo` offsets (a gap or a repeated hour resolved by
//! `fold`), the C `fromisoformat` grammar, `isoformat`, mixed-zone comparison and
//! subtraction rules, and `dateutil.tz.datetime_exists`. Zones come from jiff's bundled
//! database; a name resolves only if it is exactly an IANA key, as `ZoneInfo` requires.

use std::cmp::Ordering;
use std::sync::Arc;

use jiff::tz::{AmbiguousOffset, TimeZone};
use jiff::Timestamp;

pub const SECOND: i64 = 1_000_000;
pub const MINUTE: i64 = 60 * SECOND;
pub const HOUR: i64 = 60 * MINUTE;
pub const DAY: i64 = 24 * HOUR;

/// Days from 1970-01-01 to 0001-01-01 and to 10000-01-01: Python's `datetime` range.
const FIRST_DAY: i64 = -719_162;
const END_DAY: i64 = 2_932_897;

// ------------------------------------------------------------------------------ dates

pub fn days_in_month(year: i64, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if is_leap(year) => 29,
        _ => 28,
    }
}

pub fn is_leap(year: i64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

/// A proleptic Gregorian date, as `datetime.date`.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct Date {
    pub year: i64,
    pub month: u32,
    pub day: u32,
}

impl Date {
    pub fn new(year: i64, month: u32, day: u32) -> Option<Self> {
        ((1..=9999).contains(&year) && (1..=12).contains(&month) && day >= 1 && day <= days_in_month(year, month))
            .then_some(Self { year, month, day })
    }

    /// Days since 1970-01-01 (Hinnant's `days_from_civil`).
    pub fn days(self) -> i64 {
        let year = self.year - i64::from(self.month <= 2);
        let era = year.div_euclid(400);
        let yoe = year.rem_euclid(400);
        let month = i64::from(self.month);
        let doy = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + i64::from(self.day) - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        era * 146_097 + doe - 719_468
    }

    pub fn from_days(days: i64) -> Self {
        let z = days + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z.rem_euclid(146_097);
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
        let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
        Self { year: yoe + era * 400 + i64::from(month <= 2), month, day }
    }

    /// Monday is 0, as `date.weekday()`.
    pub fn weekday(self) -> u32 {
        (self.days() + 3).rem_euclid(7) as u32
    }

    /// Day of the year, 1-based.
    pub fn ordinal(self) -> u32 {
        (self.days() - Date { year: self.year, month: 1, day: 1 }.days() + 1) as u32
    }

    pub fn add_days(self, days: i64) -> Option<Self> {
        let target = self.days().checked_add(days)?;
        (FIRST_DAY..END_DAY).contains(&target).then(|| Self::from_days(target))
    }

    pub fn iso(self) -> String {
        format!("{:04}-{:02}-{:02}", self.year, self.month, self.day)
    }

    /// `date.fromisoformat` of exactly `YYYY-MM-DD` (the only form the callers accept).
    pub fn parse_dashed(text: &str) -> Option<Self> {
        let bytes = text.as_bytes();
        if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
            return None;
        }
        let number = |range: std::ops::Range<usize>| -> Option<i64> {
            let part = &text[range];
            part.bytes().all(|b| b.is_ascii_digit()).then(|| part.parse().ok()).flatten()
        };
        Self::new(number(0..4)?, number(5..7)? as u32, number(8..10)? as u32)
    }
}

// ------------------------------------------------------------------------------ zones

/// A `tzinfo`: `timezone.utc`, another fixed `timezone`, or a `ZoneInfo` by key.
#[derive(Clone, Debug)]
pub enum Zone {
    Utc,
    Fixed(i64),
    Iana(Arc<str>, TimeZone),
}

impl Zone {
    /// `ZoneInfo(name)`: only an exact IANA key (jiff's lookup ignores case; Python's does not).
    pub fn named(name: &str) -> Option<Self> {
        if name.is_empty() || name.starts_with('/') || name.contains('\0') {
            return None;
        }
        let zone = TimeZone::get(name).ok()?;
        (zone.iana_name() == Some(name)).then(|| Self::Iana(Arc::from(name), zone))
    }

    /// Python's `is` on two tzinfo objects: `ZoneInfo` instances are cached by key and
    /// `timezone.utc` is one object; other fixed offsets never take that shortcut.
    pub fn same(&self, other: &Zone) -> bool {
        match (self, other) {
            (Zone::Utc, Zone::Utc) => true,
            (Zone::Iana(a, _), Zone::Iana(b, _)) => a == b,
            _ => false,
        }
    }

    pub fn key(&self) -> Option<&str> {
        match self {
            Zone::Iana(name, _) => Some(name),
            _ => None,
        }
    }

    /// `utcoffset` of a wall time with its fold (PEP 495: fold 0 takes the offset before a
    /// transition, fold 1 the offset after, for a gap and a repeated hour alike).
    fn offset_at(&self, wall: i64, fold: bool) -> i64 {
        match self {
            Zone::Utc => 0,
            Zone::Fixed(offset) => *offset,
            Zone::Iana(_, zone) => {
                let Some(civil) = civil(wall) else { return 0 };
                match zone.to_ambiguous_timestamp(civil).offset() {
                    AmbiguousOffset::Unambiguous { offset } => i64::from(offset.seconds()) * SECOND,
                    AmbiguousOffset::Gap { before, after } | AmbiguousOffset::Fold { before, after } => {
                        i64::from(if fold { after } else { before }.seconds()) * SECOND
                    }
                }
            }
        }
    }

    /// Whether a wall time is the second of a repeated hour, given the offset it got.
    fn fold_of(&self, wall: i64, offset: i64) -> bool {
        let Zone::Iana(_, zone) = self else { return false };
        let Some(civil) = civil(wall) else { return false };
        match zone.to_ambiguous_timestamp(civil).offset() {
            AmbiguousOffset::Fold { before, after } => before != after && i64::from(after.seconds()) * SECOND == offset,
            _ => false,
        }
    }

    fn is_gap(&self, wall: i64) -> bool {
        let Zone::Iana(_, zone) = self else { return false };
        let Some(civil) = civil(wall) else { return false };
        matches!(zone.to_ambiguous_timestamp(civil).offset(), AmbiguousOffset::Gap { .. })
    }

    /// `ZoneInfo.fromutc`.
    fn from_utc(&self, utc: i64) -> Option<(i64, bool)> {
        match self {
            Zone::Utc => Some((utc, false)),
            Zone::Fixed(offset) => Some((utc + offset, false)),
            Zone::Iana(_, zone) => {
                let stamp = Timestamp::from_microsecond(utc).ok()?;
                let offset = i64::from(zone.to_offset(stamp).seconds()) * SECOND;
                let wall = utc + offset;
                Some((wall, self.fold_of(wall, offset)))
            }
        }
    }
}

fn civil(wall: i64) -> Option<jiff::civil::DateTime> {
    let date = Date::from_days(wall.div_euclid(DAY));
    let rest = wall.rem_euclid(DAY);
    jiff::civil::DateTime::new(
        date.year as i16, date.month as i8, date.day as i8,
        (rest / HOUR) as i8, (rest % HOUR / MINUTE) as i8, (rest % MINUTE / SECOND) as i8,
        (rest % SECOND * 1000) as i32,
    ).ok()
}

// ------------------------------------------------------------------------------ datetimes

/// Python's `OverflowError`/`ValueError` from arithmetic leaving years 1-9999.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OutOfRange;

/// A `datetime`: microseconds of wall time since 1970-01-01 00:00, its tzinfo and fold.
#[derive(Clone, Debug)]
pub struct Dt {
    pub wall: i64,
    pub zone: Option<Zone>,
    pub fold: bool,
}

fn in_range(wall: i64) -> Result<i64, OutOfRange> {
    (FIRST_DAY * DAY..END_DAY * DAY).contains(&wall).then_some(wall).ok_or(OutOfRange)
}

impl Dt {
    /// `datetime.now(timezone.utc)`. `KITTY_TEST_NOW` (an ISO timestamp with an offset) is a
    /// test-only clock, as the Python tests monkeypatched the modules' `now`.
    pub fn now_utc() -> Self {
        if let Some(fixed) = std::env::var("KITTY_TEST_NOW").ok().and_then(|text| fromisoformat(&text)) {
            if let Ok(fixed) = fixed.astimezone(&Zone::Utc) {
                return fixed;
            }
        }
        let elapsed = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        Self { wall: elapsed.as_micros() as i64, zone: Some(Zone::Utc), fold: false }
    }

    /// `datetime.combine(date, time.min, zone)`.
    pub fn midnight(date: Date, zone: Option<Zone>) -> Self {
        Self { wall: date.days() * DAY, zone, fold: false }
    }

    pub fn date(&self) -> Date {
        Date::from_days(self.wall.div_euclid(DAY))
    }

    /// Microseconds since midnight of the wall time.
    pub fn time_of_day(&self) -> i64 {
        self.wall.rem_euclid(DAY)
    }

    pub fn utcoffset(&self) -> Option<i64> {
        self.zone.as_ref().map(|zone| zone.offset_at(self.wall, self.fold))
    }

    /// The instant (aware values only; a naive one is taken as UTC).
    pub fn utc(&self) -> i64 {
        self.wall - self.utcoffset().unwrap_or(0)
    }

    pub fn naive(&self) -> Self {
        Self { wall: self.wall, zone: None, fold: self.fold }
    }

    /// `replace(tzinfo=zone, fold=fold)`.
    pub fn with_zone(&self, zone: Zone, fold: bool) -> Self {
        Self { wall: self.wall, zone: Some(zone), fold }
    }

    /// `astimezone(zone)`: the same object when it already has that tzinfo.
    pub fn astimezone(&self, zone: &Zone) -> Result<Self, OutOfRange> {
        if self.zone.as_ref().is_some_and(|own| own.same(zone)) {
            return Ok(self.clone());
        }
        let (wall, fold) = zone.from_utc(self.utc()).ok_or(OutOfRange)?;
        Ok(Self { wall: in_range(wall)?, zone: Some(zone.clone()), fold })
    }

    /// `self + timedelta`: wall-clock arithmetic, the tzinfo kept and the fold reset.
    pub fn add(&self, micros: i64) -> Result<Self, OutOfRange> {
        let wall = self.wall.checked_add(micros).ok_or(OutOfRange)?;
        Ok(Self { wall: in_range(wall)?, zone: self.zone.clone(), fold: false })
    }

    /// `self - other` in microseconds: wall difference under the same tzinfo, else instants.
    pub fn sub(&self, other: &Dt) -> i64 {
        match (&self.zone, &other.zone) {
            (Some(a), Some(b)) if !a.same(b) => {
                let (mine, theirs) = (self.utcoffset().unwrap_or(0), other.utcoffset().unwrap_or(0));
                self.wall - other.wall - mine + theirs
            }
            _ => self.wall - other.wall,
        }
    }

    /// `<`/`>` (`datetime._cmp` without `allow_mixed`).
    pub fn compare(&self, other: &Dt) -> Ordering {
        match (&self.zone, &other.zone) {
            (Some(a), Some(b)) if !a.same(b) => {
                let (mine, theirs) = (self.utcoffset().unwrap_or(0), other.utcoffset().unwrap_or(0));
                if mine == theirs { self.wall.cmp(&other.wall) } else { self.utc().cmp(&other.utc()) }
            }
            _ => self.wall.cmp(&other.wall),
        }
    }

    pub fn lt(&self, other: &Dt) -> bool {
        self.compare(other) == Ordering::Less
    }

    pub fn le(&self, other: &Dt) -> bool {
        self.compare(other) != Ordering::Greater
    }

    /// `==` (`_cmp` with `allow_mixed`): across two tzinfos, a time whose offset depends on
    /// its fold equals nothing.
    pub fn eq(&self, other: &Dt) -> bool {
        if let (Some(a), Some(b)) = (&self.zone, &other.zone) {
            if !a.same(b) {
                let flips = |dt: &Dt, zone: &Zone| zone.offset_at(dt.wall, dt.fold) != zone.offset_at(dt.wall, !dt.fold);
                if flips(self, a) || flips(other, b) {
                    return false;
                }
            }
        }
        self.compare(other) == Ordering::Equal
    }

    pub fn max(self, other: Dt) -> Dt {
        // Python's max keeps the first of equal values.
        if other.compare(&self) == Ordering::Greater { other } else { self }
    }

    pub fn min(self, other: Dt) -> Dt {
        if other.compare(&self) == Ordering::Less { other } else { self }
    }

    /// `dateutil.tz.datetime_exists`: false inside a forward transition's gap.
    pub fn exists(&self) -> bool {
        self.zone.as_ref().is_none_or(|zone| !zone.is_gap(self.wall))
    }

    /// `isoformat()`.
    pub fn isoformat(&self) -> String {
        let mut out = format!("{}T{}", self.date().iso(), clock(self.time_of_day()));
        if let Some(offset) = self.utcoffset() {
            out.push_str(&format_offset(offset));
        }
        out
    }

    /// `strftime('%Y%m%dT%H%M%SZ')` of a UTC value.
    pub fn compact_utc(&self) -> String {
        let date = self.date();
        let rest = self.time_of_day();
        format!("{:04}{:02}{:02}T{:02}{:02}{:02}Z", date.year, date.month, date.day, rest / HOUR, rest % HOUR / MINUTE, rest % MINUTE / SECOND)
    }
}

fn clock(micros: i64) -> String {
    let base = format!("{:02}:{:02}:{:02}", micros / HOUR, micros % HOUR / MINUTE, micros % MINUTE / SECOND);
    match micros % SECOND {
        0 => base,
        fraction => format!("{base}.{fraction:06}"),
    }
}

/// `datetime._format_offset`.
pub fn format_offset(offset: i64) -> String {
    let sign = if offset < 0 { '-' } else { '+' };
    let value = offset.abs();
    let mut out = format!("{sign}{:02}:{:02}", value / HOUR, value % HOUR / MINUTE);
    let (seconds, micros) = (value % MINUTE / SECOND, value % SECOND);
    if seconds != 0 || micros != 0 {
        out.push_str(&format!(":{seconds:02}"));
        if micros != 0 {
            out.push_str(&format!(".{micros:06}"));
        }
    }
    out
}

/// `datetime.now(timezone.utc).isoformat()`.
pub fn now_iso() -> String {
    Dt::now_utc().isoformat()
}

// ------------------------------------------------------------------------------ fromisoformat

fn digits(chars: &[char], at: usize, count: usize) -> Option<i64> {
    let part = chars.get(at..at + count)?;
    part.iter().all(char::is_ascii_digit).then(|| part.iter().fold(0, |acc, c| acc * 10 + i64::from(*c as u8 - b'0')))
}

/// `_find_isoformat_datetime_separator` of CPython's C implementation.
fn separator(chars: &[char]) -> Option<usize> {
    let len = chars.len();
    if len == 7 {
        return Some(7);
    }
    if len < 7 {
        return None;
    }
    if chars[4] == '-' {
        if chars[5] == 'W' {
            if len < 8 {
                return None;
            }
            if len > 8 && chars[8] == '-' {
                if len == 9 {
                    return None;
                }
                if len > 10 && chars[10].is_ascii_digit() {
                    return Some(8);
                }
                return Some(10);
            }
            return Some(8);
        }
        return Some(10);
    }
    if chars[4] == 'W' {
        let mut index = 7;
        while index < len && chars[index].is_ascii_digit() {
            index += 1;
        }
        if index < 9 {
            return Some(index);
        }
        return Some(if index % 2 == 0 { 7 } else { 8 });
    }
    Some(8)
}

fn iso_week(year: i64, week: i64, day: i64) -> Option<Date> {
    if !(1..=9999).contains(&year) || !(1..=7).contains(&day) {
        return None;
    }
    if !(1..=53).contains(&week) {
        return None;
    }
    let jan4 = Date { year, month: 1, day: 4 };
    let first_monday = jan4.days() - i64::from(jan4.weekday());
    if week == 53 {
        // Only years starting on a Thursday, or leap years starting on a Wednesday, have 53 weeks.
        let first = Date { year, month: 1, day: 1 }.weekday();
        if !(first == 3 || (first == 2 && is_leap(year))) {
            return None;
        }
    }
    let days = first_monday + (week - 1) * 7 + (day - 1);
    let date = Date::from_days(days);
    (1..=9999).contains(&date.year).then_some(date)
}

/// `parse_isoformat_date`: the part before the separator; trailing characters are not checked.
fn parse_date(chars: &[char]) -> Option<Date> {
    let len = chars.len();
    let year = digits(chars, 0, 4)?;
    let dashed = chars.get(4) == Some(&'-');
    let mut at = 4 + usize::from(dashed);
    if chars.get(at) == Some(&'W') {
        at += 1;
        let week = digits(chars, at, 2)?;
        at += 2;
        let mut day = 1;
        if at < len {
            if dashed {
                if chars[at] != '-' {
                    return None;
                }
                at += 1;
            }
            day = digits(chars, at, 1)?;
        }
        return iso_week(year, week, day);
    }
    let month = digits(chars, at, 2)?;
    at += 2;
    if dashed {
        if chars.get(at) != Some(&'-') {
            return None;
        }
        at += 1;
    }
    let day = digits(chars, at, 2)?;
    Date::new(year, month as u32, day as u32)
}

/// `parse_hh_mm_ss_ff` over `chars[..end]`: the parts, and whether a character other than
/// the string's end followed (the C function's return value 1).
fn parse_clock(chars: &[char], end: usize) -> Option<([i64; 4], bool)> {
    let mut parts = [0i64; 4];
    let mut at = 0;
    let mut colons = true;
    for index in 0..3 {
        parts[index] = digits(&chars[..end], at, 2)?;
        at += 2;
        let next = chars.get(at).copied();
        at += 1;
        if index == 0 {
            colons = next == Some(':');
        }
        if at >= end {
            return Some((parts, next.is_some()));
        } else if colons && next == Some(':') {
            continue;
        } else if matches!(next, Some('.' | ',')) {
            break;
        } else if !colons {
            at -= 1;
        } else {
            return None;
        }
    }
    let take = (end - at).min(6);
    if take == 0 {
        return None;
    }
    let mut micro = digits(&chars[..end], at, take)?;
    for _ in take..6 {
        micro *= 10;
    }
    parts[3] = micro;
    at += take;
    while at < end && chars[at].is_ascii_digit() {
        at += 1;
    }
    Some((parts, chars.get(at).is_some()))
}

/// `datetime.fromisoformat` (CPython 3.12's C implementation).
pub fn fromisoformat(text: &str) -> Option<Dt> {
    let chars: Vec<char> = text.chars().collect();
    let split = separator(&chars)?;
    let date = parse_date(chars.get(..split)?)?;
    // The separator itself may be any character.
    let rest: &[char] = chars.get(split + 1..).unwrap_or(&[]);
    let mut wall = date.days() * DAY;
    let mut zone = None;
    if !rest.is_empty() {
        let tz_at = rest.iter().position(|c| matches!(c, 'Z' | '+' | '-')).unwrap_or(rest.len());
        let ([hour, minute, second, micro], more) = parse_clock(rest, tz_at)?;
        if hour > 23 || minute > 59 || second > 59 {
            return None;
        }
        wall += hour * HOUR + minute * MINUTE + second * SECOND + micro;
        if tz_at == rest.len() {
            if more {
                return None;
            }
        } else if rest[tz_at] == 'Z' {
            if tz_at + 1 != rest.len() {
                return None;
            }
            zone = Some(Zone::Utc);
        } else {
            let tz = &rest[tz_at + 1..];
            let ([h, m, s, f], more) = parse_clock(tz, tz.len())?;
            if more {
                return None;
            }
            let offset = h * HOUR + m * MINUTE + s * SECOND + f;
            if offset >= DAY {
                return None;
            }
            zone = Some(if offset == 0 { Zone::Utc } else { Zone::Fixed(if rest[tz_at] == '-' { -offset } else { offset }) });
        }
    }
    Some(Dt { wall, zone, fold: false })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zone_names_are_exact_keys() {
        assert!(Zone::named("America/Chicago").is_some());
        assert!(Zone::named("america/chicago").is_none());
        assert!(Zone::named("UTC").is_some());
        assert!(Zone::named("Not/AZone").is_none());
        assert!(Zone::named("").is_none());
    }

    #[test]
    fn offsets_folds_and_gaps_follow_pep_495() {
        let chicago = Zone::named("America/Chicago").unwrap();
        let fall = fromisoformat("2026-11-01T01:30").unwrap();
        assert_eq!(fall.with_zone(chicago.clone(), false).isoformat(), "2026-11-01T01:30:00-05:00");
        assert_eq!(fall.with_zone(chicago.clone(), true).isoformat(), "2026-11-01T01:30:00-06:00");
        let gap = fromisoformat("2027-03-14T02:30").unwrap().with_zone(chicago.clone(), false);
        assert!(!gap.exists());
        assert_eq!(gap.isoformat(), "2027-03-14T02:30:00-06:00");
        let utc = fromisoformat("2026-11-01T07:30:00Z").unwrap();
        let local = utc.astimezone(&chicago).unwrap();
        assert!(local.fold);
        assert_eq!(local.isoformat(), "2026-11-01T01:30:00-06:00");
    }

    #[test]
    fn fromisoformat_follows_the_c_grammar() {
        for (text, expected) in [
            ("2026-10-26T09:00:00-05:00", Some("2026-10-26T09:00:00-05:00")),
            ("2026-10-26 09:00", Some("2026-10-26T09:00:00")),
            ("20261026T0900", Some("2026-10-26T09:00:00")),
            ("2026-10-26T09:00:00.5Z", Some("2026-10-26T09:00:00.500000+00:00")),
            ("2026-W01-1", Some("2025-12-29T00:00:00")),
            ("2026-10-26T24:00", None),
            ("2026-02-30", None),
            ("2026-10-26T09:00+0530", Some("2026-10-26T09:00:00+05:30")),
            ("2026-10-26T09", Some("2026-10-26T09:00:00")),
            ("2026-10-26T9", None),
        ] {
            assert_eq!(fromisoformat(text).map(|dt| dt.isoformat()).as_deref(), expected, "{text}");
        }
    }
}
