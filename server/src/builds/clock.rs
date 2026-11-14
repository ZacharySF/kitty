//! UTC timestamps as the Python build code writes them.

use std::time::{SystemTime, UNIX_EPOCH};

/// One moment in UTC, to the microsecond (`datetime.now(timezone.utc)`).
#[derive(Clone, Copy)]
pub struct Moment {
    pub seconds: i64,
    pub micros: u32,
}

impl Moment {
    pub fn now() -> Moment {
        let elapsed = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
        Moment { seconds: elapsed.as_secs() as i64, micros: elapsed.subsec_micros() }
    }

    fn parts(self) -> (i64, u32, u32, i64, i64, i64) {
        let (year, month, day) = civil(self.seconds.div_euclid(86_400));
        let second = self.seconds.rem_euclid(86_400);
        (year, month, day, second / 3600, second / 60 % 60, second % 60)
    }

    /// `datetime.isoformat()` of an aware UTC datetime: microseconds only when not zero.
    pub fn isoformat(self) -> String {
        let (year, month, day, hour, minute, second) = self.parts();
        let fraction = if self.micros == 0 { String::new() } else { format!(".{:06}", self.micros) };
        format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{fraction}+00:00")
    }

    /// `strftime("%Y%m%dT%H%M%S%fZ")`: a history entry's id.
    pub fn history_id(self) -> String {
        let (year, month, day, hour, minute, second) = self.parts();
        format!("{year:04}{month:02}{day:02}T{hour:02}{minute:02}{second:02}{:06}Z", self.micros)
    }

    /// `kitty.dates.now_iso()`: `%Y-%m-%dT%H:%M:%SZ`.
    pub fn iso_seconds(self) -> String {
        let (year, month, day, hour, minute, second) = self.parts();
        format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
    }
}

/// Hinnant's `civil_from_days`.
fn civil(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (yoe + era * 400 + i64::from(month <= 2), month, day)
}

#[cfg(test)]
mod tests {
    use super::Moment;

    #[test]
    fn timestamps_print_as_python_does() {
        let moment = Moment { seconds: 1_794_571_200, micros: 42 };
        assert_eq!(moment.isoformat(), "2026-11-13T12:00:00.000042+00:00");
        assert_eq!(Moment { micros: 0, ..moment }.isoformat(), "2026-11-13T12:00:00+00:00");
        assert_eq!(moment.history_id(), "20261113T120000000042Z");
        assert_eq!(moment.iso_seconds(), "2026-11-13T12:00:00Z");
        assert_eq!(Moment { seconds: 951_782_400, micros: 0 }.iso_seconds(), "2000-02-29T00:00:00Z");
    }
}
