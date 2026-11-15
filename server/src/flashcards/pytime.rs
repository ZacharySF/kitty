//! Python's `datetime` formats and `zoneinfo` arithmetic, as the module routes stored and
//! compared them: timestamps are ISO strings compared as text in SQL, so the exact
//! `datetime.isoformat()` spelling matters.

use jiff::civil::Date;
use jiff::tz::TimeZone;
use jiff::Timestamp;

pub const MICROS_PER_SECOND: i64 = 1_000_000;
pub const MICROS_PER_DAY: i64 = 86_400 * MICROS_PER_SECOND;

/// `datetime.fromisoformat`'s result: the wall-clock time and its offset, if any.
#[derive(Clone, Copy, Debug)]
pub struct Parsed {
    /// The wall-clock time as if it were UTC, in microseconds since the epoch.
    pub wall: i64,
    /// The UTC offset in microseconds; None for a naive value.
    pub offset: Option<i64>,
}

impl Parsed {
    /// The instant, for an aware value.
    pub fn utc(self) -> Option<i64> {
        self.offset.map(|offset| self.wall - offset)
    }
}

fn digits(text: &str) -> Option<i64> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    text.parse().ok()
}

/// Days since 1970-01-01 of a proleptic Gregorian date (Howard Hinnant's `days_from_civil`).
pub fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let yoe = year.rem_euclid(400);
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn month_days(year: i64, month: i64) -> i64 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        _ => 28,
    }
}

/// `date.fromisoformat` for `YYYY-MM-DD` (or `YYYYMMDD`): days since the epoch.
pub fn parse_date(text: &str) -> Option<i64> {
    let (year, month, day) = match text.len() {
        10 if text.as_bytes()[4] == b'-' && text.as_bytes()[7] == b'-' => (&text[0..4], &text[5..7], &text[8..10]),
        8 => (&text[0..4], &text[4..6], &text[6..8]),
        _ => return None,
    };
    let (year, month, day) = (digits(year)?, digits(month)?, digits(day)?);
    if !(1..=9999).contains(&year) || !(1..=12).contains(&month) || day < 1 || day > month_days(year, month) {
        return None;
    }
    Some(days_from_civil(year, month, day))
}

/// `HH[:MM[:SS[.f...]]]` (or the basic forms) as microseconds into the day.
fn parse_clock(text: &str) -> Option<i64> {
    let (main, fraction) = match text.find(['.', ',']) {
        Some(at) => (&text[..at], Some(&text[at + 1..])),
        None => (text, None),
    };
    let parts: Vec<&str> = if main.contains(':') { main.split(':').collect() } else {
        match main.len() {
            2 => vec![&main[0..2]],
            4 => vec![&main[0..2], &main[2..4]],
            6 => vec![&main[0..2], &main[2..4], &main[4..6]],
            _ => return None,
        }
    };
    if parts.is_empty() || parts.len() > 3 || parts.iter().any(|part| part.len() != 2) {
        return None;
    }
    let hour = digits(parts[0])?;
    let minute = parts.get(1).map_or(Some(0), |part| digits(part))?;
    let second = parts.get(2).map_or(Some(0), |part| digits(part))?;
    if hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    let mut micros = 0;
    if let Some(fraction) = fraction {
        if parts.len() != 3 || fraction.is_empty() || !fraction.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        let kept: String = fraction.chars().take(6).collect();
        micros = kept.parse::<i64>().ok()? * 10i64.pow(6 - kept.len() as u32);
    }
    Some(((hour * 60 + minute) * 60 + second) * MICROS_PER_SECOND + micros)
}

/// `datetime.fromisoformat` (Python 3.12) for the forms rika! stores and imports.
pub fn parse(text: &str) -> Option<Parsed> {
    if !text.is_ascii() {
        return None;
    }
    if text.len() <= 10 {
        return Some(Parsed { wall: parse_date(text)? * MICROS_PER_DAY, offset: None });
    }
    let date_length = if text.as_bytes()[4] == b'-' { 10 } else { 8 };
    let day = parse_date(&text[..date_length])?;
    let rest = &text[date_length + 1..];
    let (clock, zone) = match rest.find(['+', '-', 'Z']) {
        Some(at) => (&rest[..at], Some(&rest[at..])),
        None => (rest, None),
    };
    let wall = day * MICROS_PER_DAY + parse_clock(clock)?;
    let offset = match zone {
        None => None,
        Some("Z") => Some(0),
        Some(zone) => {
            let sign = if zone.starts_with('-') { -1 } else { 1 };
            let magnitude = parse_clock(&zone[1..])?;
            if magnitude >= MICROS_PER_DAY {
                return None;
            }
            Some(sign * magnitude)
        }
    };
    Some(Parsed { wall, offset })
}

/// `(year, month, day)` of days since the epoch.
pub fn civil(days: i64) -> (i64, i64, i64) {
    let (year, month, day) = crate::migrations::civil(days);
    (year, month as i64, day as i64)
}

/// `date.isoformat()`.
pub fn date_iso(days: i64) -> String {
    let (year, month, day) = civil(days);
    format!("{year:04}-{month:02}-{day:02}")
}

fn clock_iso(micros: i64) -> String {
    let seconds = micros.div_euclid(MICROS_PER_SECOND);
    let fraction = micros.rem_euclid(MICROS_PER_SECOND);
    let text = format!("{:02}:{:02}:{:02}", seconds / 3600, seconds % 3600 / 60, seconds % 60);
    if fraction == 0 { text } else { format!("{text}.{fraction:06}") }
}

/// `datetime.isoformat()` of an aware UTC moment: `YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`.
pub fn iso(moment: i64) -> String {
    let days = moment.div_euclid(MICROS_PER_DAY);
    format!("{}T{}+00:00", date_iso(days), clock_iso(moment.rem_euclid(MICROS_PER_DAY)))
}

/// `datetime.isoformat(timespec="milliseconds")` of an aware UTC moment.
pub fn iso_milliseconds(moment: i64) -> String {
    let days = moment.div_euclid(MICROS_PER_DAY);
    let micros = moment.rem_euclid(MICROS_PER_DAY);
    let seconds = micros / MICROS_PER_SECOND;
    format!("{}T{:02}:{:02}:{:02}.{:03}+00:00", date_iso(days), seconds / 3600, seconds % 3600 / 60, seconds % 60,
            micros % MICROS_PER_SECOND / 1000)
}

/// `datetime.isoformat()` of a parsed value, in its own offset (as Python keeps it).
pub fn iso_parsed(value: Parsed) -> String {
    let days = value.wall.div_euclid(MICROS_PER_DAY);
    let mut text = format!("{}T{}", date_iso(days), clock_iso(value.wall.rem_euclid(MICROS_PER_DAY)));
    if let Some(offset) = value.offset {
        let sign = if offset < 0 { '-' } else { '+' };
        let magnitude = offset.abs();
        let seconds = magnitude / MICROS_PER_SECOND;
        text.push_str(&format!("{sign}{:02}:{:02}", seconds / 3600, seconds % 3600 / 60));
        if seconds % 60 != 0 || magnitude % MICROS_PER_SECOND != 0 {
            text.push_str(&format!(":{:02}", seconds % 60));
            if magnitude % MICROS_PER_SECOND != 0 {
                text.push_str(&format!(".{:06}", magnitude % MICROS_PER_SECOND));
            }
        }
    }
    text
}

/// Python's `datetime` range: years 1 to 9999.
pub fn in_range(moment: i64) -> bool {
    const LOW: i64 = -62_135_596_800 * MICROS_PER_SECOND;
    const HIGH: i64 = 253_402_300_800 * MICROS_PER_SECOND;
    (LOW..HIGH).contains(&moment)
}

/// `ZoneInfo(name)`: an IANA zone from the system's database, or None.
pub fn zone(name: &str) -> Option<TimeZone> {
    if name.is_empty() || name.starts_with('/') || name.split('/').any(|part| part == ".." || part == ".") {
        return None;
    }
    TimeZone::get(name).ok()
}

/// The local date (days since the epoch) of a moment in a zone.
pub fn local_day(moment: i64, zone: &TimeZone) -> i64 {
    let Ok(timestamp) = Timestamp::from_microsecond(moment) else { return moment.div_euclid(MICROS_PER_DAY) };
    let date = timestamp.to_zoned(zone.clone()).date();
    days_from_civil(date.year() as i64, date.month() as i64, date.day() as i64)
}

/// `datetime.combine(day, time.min, tzinfo=zone)` as a UTC moment. jiff's compatible
/// disambiguation is Python's fold=0: the earlier offset of a repeated hour, and for a skipped
/// one the offset before the transition.
pub fn local_midnight(days: i64, zone: &TimeZone) -> i64 {
    let (year, month, day) = civil(days);
    let wall = days * MICROS_PER_DAY;
    let Ok(date) = Date::new(year as i16, month as i8, day as i8) else { return wall };
    date.to_zoned(zone.clone()).map(|zoned| zoned.timestamp().as_microsecond()).unwrap_or(wall)
}

/// The UTC offset of a zone at a moment, in microseconds.
pub fn offset_at(moment: i64, zone: &TimeZone) -> i64 {
    match Timestamp::from_microsecond(moment) {
        Ok(timestamp) => zone.to_offset(timestamp).seconds() as i64 * MICROS_PER_SECOND,
        Err(_) => 0,
    }
}

/// Python's `timedelta.days` of a difference in microseconds (floor).
pub fn whole_days(micros: i64) -> i64 {
    micros.div_euclid(MICROS_PER_DAY)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isoformat_matches_python() {
        let moment = (days_from_civil(2026, 10, 14) * 86_400 + 12 * 3600) * MICROS_PER_SECOND;
        assert_eq!(iso(moment), "2026-10-14T12:00:00+00:00");
        assert_eq!(iso(moment + 1234), "2026-10-14T12:00:00.001234+00:00");
        assert_eq!(iso_milliseconds(moment + 1234), "2026-10-14T12:00:00.001+00:00");
        assert_eq!(parse("2026-10-14T12:00:00+00:00").unwrap().utc(), Some(moment));
        assert_eq!(parse("2026-10-14T14:00:00.5+02:00").unwrap().utc(), Some(moment + 500_000));
        assert_eq!(parse("2026-10-14 12:00:00Z").unwrap().utc(), Some(moment));
        assert_eq!(parse("2026-10-14").unwrap().offset, None);
        assert!(parse("2026-02-30T00:00:00").is_none());
    }

    #[test]
    fn local_days_follow_the_zone() {
        let zone = zone("America/New_York").unwrap();
        let moment = (days_from_civil(2026, 10, 15) * 86_400 + 3 * 3600 + 59 * 60) * MICROS_PER_SECOND;
        assert_eq!(date_iso(local_day(moment, &zone)), "2026-10-14");
        let midnight = local_midnight(days_from_civil(2026, 10, 15), &zone);
        assert_eq!(iso(midnight), "2026-10-15T04:00:00+00:00");
    }
}
