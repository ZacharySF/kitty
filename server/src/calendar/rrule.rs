//! `dateutil.rrule` as `rrulestr(text, dtstart=...)` builds and iterates it: the same
//! defaults from DTSTART, the same masks per year (weeks, nth weekdays, Easter), BYSETPOS,
//! COUNT and UNTIL, and the same period stepping. Occurrences are wall times in DTSTART's
//! zone with fold 0, as dateutil reconstructs them. Only YEARLY to DAILY iterate: the
//! calendar refuses finer frequencies before expanding anything.

use std::collections::VecDeque;

use crate::ported::time::{days_in_month, is_leap, Date, Dt, Zone, DAY, HOUR, MINUTE, SECOND};

pub const YEARLY: u8 = 0;
pub const MONTHLY: u8 = 1;
pub const WEEKLY: u8 = 2;
pub const DAILY: u8 = 3;

/// A rule `rrulestr` refused (its `ValueError`, `KeyError` or `TypeError`).
#[derive(Debug)]
pub struct Invalid;

#[derive(Clone, Debug)]
pub struct Rule {
    pub freq: u8,
    pub interval: i64,
    pub count: Option<i64>,
    until: Option<Dt>,
    wkst: u32,
    bysetpos: Vec<i64>,
    bymonth: Vec<u32>,
    byweekno: Vec<i64>,
    byyearday: Vec<i64>,
    byeaster: Vec<i64>,
    bymonthday: Vec<i64>,
    bynmonthday: Vec<i64>,
    byweekday: Option<Vec<u32>>,
    bynweekday: Option<Vec<(u32, i64)>>,
    /// Microseconds of the day, sorted.
    timeset: Vec<i64>,
    dtstart: Dt,
}

/// Python's `int(text)` for the characters a calendar RRULE may hold.
fn int(text: &str) -> Result<i64, Invalid> {
    let digits = text.strip_prefix(['+', '-']).unwrap_or(text);
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Invalid);
    }
    text.parse::<i64>().map_err(|_| Invalid)
}

fn ints(text: &str) -> Result<Vec<i64>, Invalid> {
    text.split(',').map(int).collect()
}

fn weekday(name: &str) -> Result<u32, Invalid> {
    ["MO", "TU", "WE", "TH", "FR", "SA", "SU"].iter().position(|day| *day == name).map(|at| at as u32).ok_or(Invalid)
}

/// `dateutil.parser.parse` of the UNTIL forms a calendar RRULE can carry (`YYYYMMDD`,
/// `YYYYMMDDTHHMMSS`, with `Z` or a `+HHMM` offset). A naive value is kept naive.
fn until(text: &str) -> Result<Dt, Invalid> {
    let (stamp, zone) = if let Some(stamp) = text.strip_suffix('Z') {
        (stamp, Some(Zone::Utc))
    } else if let Some(at) = text.rfind(['+', '-']).filter(|at| *at > 8) {
        let offset = &text[at + 1..];
        if offset.len() != 4 || !offset.bytes().all(|b| b.is_ascii_digit()) {
            return Err(Invalid);
        }
        let value = (int(&offset[..2])? * HOUR + int(&offset[2..])? * MINUTE) * if text.as_bytes()[at] == b'-' { -1 } else { 1 };
        (&text[..at], Some(if value == 0 { Zone::Utc } else { Zone::Fixed(value) }))
    } else {
        (text, None)
    };
    let number = |range: std::ops::Range<usize>| -> Result<i64, Invalid> {
        let part = stamp.get(range).ok_or(Invalid)?;
        if part.bytes().all(|b| b.is_ascii_digit()) { part.parse().map_err(|_| Invalid) } else { Err(Invalid) }
    };
    let date = Date::new(number(0..4)?, number(4..6)? as u32, number(6..8)? as u32).ok_or(Invalid)?;
    let time = match stamp.len() {
        8 => 0,
        15 if stamp.as_bytes()[8] == b'T' => {
            let (h, m, s) = (number(9..11)?, number(11..13)?, number(13..15)?);
            if h > 23 || m > 59 || s > 59 {
                return Err(Invalid);
            }
            h * HOUR + m * MINUTE + s * SECOND
        }
        _ => return Err(Invalid),
    };
    Ok(Dt { wall: date.days() * DAY + time, zone, fold: false })
}

impl Rule {
    /// `rrulestr(text, dtstart=dtstart)` for a single `RRULE` value.
    pub fn parse(text: &str, dtstart: &Dt) -> Result<Rule, Invalid> {
        let text = text.trim().to_uppercase();
        if text.is_empty() {
            return Err(Invalid);
        }
        let value = if text.contains(':') {
            let parts: Vec<&str> = text.split(':').collect();
            if parts.len() != 2 || parts[0] != "RRULE" {
                return Err(Invalid);
            }
            parts[1].to_owned()
        } else {
            text
        };
        let mut freq = None;
        let (mut interval, mut count, mut until_at, mut wkst) = (1, None, None, None);
        let (mut bysetpos, mut bymonth, mut bymonthday, mut byyearday, mut byeaster, mut byweekno) = (None, None, None, None, None, None);
        let (mut byweekday, mut byhour, mut byminute, mut bysecond) = (None, None, None, None);
        for pair in value.split(';') {
            let parts: Vec<&str> = pair.split('=').collect();
            if parts.len() != 2 {
                return Err(Invalid);
            }
            let (name, value) = (parts[0], parts[1]);
            match name {
                "FREQ" => freq = Some(["YEARLY", "MONTHLY", "WEEKLY", "DAILY", "HOURLY", "MINUTELY", "SECONDLY"]
                    .iter().position(|known| *known == value).ok_or(Invalid)? as u8),
                "INTERVAL" => interval = int(value)?,
                "COUNT" => count = Some(int(value)?),
                "UNTIL" => until_at = Some(until(value)?),
                "WKST" => wkst = Some(weekday(value)?),
                "BYSETPOS" => bysetpos = Some(ints(value)?),
                "BYMONTH" => bymonth = Some(ints(value)?),
                "BYMONTHDAY" => bymonthday = Some(ints(value)?),
                "BYYEARDAY" => byyearday = Some(ints(value)?),
                "BYEASTER" => byeaster = Some(ints(value)?),
                "BYWEEKNO" => byweekno = Some(ints(value)?),
                "BYHOUR" => byhour = Some(ints(value)?),
                "BYMINUTE" => byminute = Some(ints(value)?),
                "BYSECOND" => bysecond = Some(ints(value)?),
                "BYDAY" | "BYWEEKDAY" => {
                    let mut days = Vec::new();
                    for item in value.split(',') {
                        if item.is_empty() {
                            return Err(Invalid);
                        }
                        let split = item.find(|c: char| !"+-0123456789".contains(c)).ok_or(Invalid)?;
                        let n = if split == 0 { None } else { Some(int(&item[..split])?) };
                        if n == Some(0) {
                            return Err(Invalid);
                        }
                        days.push((weekday(&item[split..])?, n));
                    }
                    byweekday = Some(days);
                }
                _ => return Err(Invalid),
            }
        }
        let freq = freq.ok_or(Invalid)?;
        // `rrule.__init__`.
        let dtstart = Dt { wall: dtstart.wall - dtstart.wall.rem_euclid(SECOND), zone: dtstart.zone.clone(), fold: dtstart.fold };
        if let Some(until) = &until_at {
            if until.zone.is_some() != dtstart.zone.is_some() {
                return Err(Invalid);
            }
        }
        let start = dtstart.date();
        let clock = dtstart.time_of_day();
        let bysetpos = bysetpos.unwrap_or_default();
        if bysetpos.iter().any(|pos| *pos == 0 || !(-366..=366).contains(pos)) {
            return Err(Invalid);
        }
        if byweekno.is_none() && byyearday.is_none() && bymonthday.is_none() && byweekday.is_none() && byeaster.is_none() {
            match freq {
                YEARLY => {
                    if bymonth.is_none() {
                        bymonth = Some(vec![i64::from(start.month)]);
                    }
                    bymonthday = Some(vec![i64::from(start.day)]);
                }
                MONTHLY => bymonthday = Some(vec![i64::from(start.day)]),
                WEEKLY => byweekday = Some(vec![(start.weekday(), None)]),
                _ => {}
            }
        }
        let sorted = |values: Option<Vec<i64>>| -> Vec<i64> {
            let mut values = values.unwrap_or_default();
            values.sort_unstable();
            values.dedup();
            values
        };
        let bymonth: Vec<u32> = sorted(bymonth).into_iter().filter_map(|m| u32::try_from(m).ok().or(Some(u32::MAX))).collect();
        let days = sorted(bymonthday);
        let (mut plain, mut nth) = (Vec::new(), Vec::new());
        let had_weekdays = byweekday.is_some();
        for (day, n) in byweekday.unwrap_or_default() {
            match n {
                Some(n) if freq <= MONTHLY => nth.push((day, n)),
                _ => plain.push(day),
            }
        }
        plain.sort_unstable();
        plain.dedup();
        nth.sort_unstable();
        nth.dedup();
        let (byweekday, bynweekday) = if !had_weekdays {
            (None, None)
        } else if plain.is_empty() {
            (None, Some(nth))
        } else if nth.is_empty() {
            (Some(plain), None)
        } else {
            (Some(plain), Some(nth))
        };
        let mut timeset = Vec::new();
        if freq <= DAILY {
            let set = |values: Option<Vec<i64>>, default: i64, limit: i64| -> Result<Vec<i64>, Invalid> {
                let values = sorted(values.or(Some(vec![default])));
                if values.iter().any(|value| !(0..limit).contains(value)) { Err(Invalid) } else { Ok(values) }
            };
            for hour in set(byhour, clock / HOUR, 24)? {
                for minute in set(byminute.clone(), clock % HOUR / MINUTE, 60)? {
                    for second in set(bysecond.clone(), clock % MINUTE / SECOND, 60)? {
                        timeset.push(hour * HOUR + minute * MINUTE + second * SECOND);
                    }
                }
            }
            timeset.sort_unstable();
        }
        Ok(Rule {
            freq, interval, count, until: until_at, wkst: wkst.unwrap_or(0), bysetpos, bymonth,
            byweekno: sorted(byweekno), byyearday: sorted(byyearday), byeaster: {
                let mut values = byeaster.unwrap_or_default();
                values.sort_unstable();
                values
            },
            bymonthday: days.iter().copied().filter(|day| *day > 0).collect(),
            bynmonthday: days.iter().copied().filter(|day| *day < 0).collect(),
            byweekday, bynweekday, timeset, dtstart,
        })
    }

    /// Every occurrence in order; `with_count` false iterates as `rule.replace(count=None)`.
    pub fn iter(&self, with_count: bool) -> Occurrences<'_> {
        let start = self.dtstart.date();
        let mut info = Info::default();
        info.rebuild(self, start.year, start.month);
        Occurrences {
            rule: self, year: start.year, month: start.month, day: i64::from(start.day), weekday: start.weekday(),
            info, queue: VecDeque::new(), left: if with_count { self.count } else { None }, done: self.freq > DAILY || self.interval < 1,
        }
    }
}

/// `easter.easter(year)` (the Western method).
fn easter(year: i64) -> Date {
    let g = year % 19;
    let c = year / 100;
    let h = (c - c / 4 - (8 * c + 13) / 25 + 19 * g + 15) % 30;
    let i = h - (h / 28) * (1 - (h / 28) * (29 / (h + 1)) * ((21 - g) / 11));
    let j = (year + year / 4 + i + 2 - c + c / 4) % 7;
    let p = i - j;
    let d = 1 + (p + 27 + (p + 6) / 40) % 31;
    let m = 3 + (p + 26) / 30;
    Date { year, month: m as u32, day: d as u32 }
}

/// `_iterinfo`: the masks of one year (and the nth-weekday mask of one month).
#[derive(Default)]
struct Info {
    last: Option<(i64, u32)>,
    yearlen: usize,
    nextyearlen: usize,
    /// Days since 1970-01-01 of 1 January.
    yearday0: i64,
    mmask: Vec<u32>,
    mdaymask: Vec<i64>,
    nmdaymask: Vec<i64>,
    wdaymask: Vec<u32>,
    mrange: Vec<usize>,
    wnomask: Option<Vec<bool>>,
    nwdaymask: Option<Vec<bool>>,
    eastermask: Option<Vec<bool>>,
}

fn mark(mask: &mut [bool], index: i64) {
    if let Some(slot) = usize::try_from(index).ok().and_then(|at| mask.get_mut(at)) {
        *slot = true;
    }
}

impl Info {
    fn rebuild(&mut self, rule: &Rule, year: i64, month: u32) {
        let new_year = self.last.is_none_or(|(last, _)| last != year);
        if new_year {
            self.yearlen = 365 + usize::from(is_leap(year));
            self.nextyearlen = 365 + usize::from(is_leap(year + 1));
            let first = Date { year, month: 1, day: 1 };
            self.yearday0 = first.days();
            let yearweekday = first.weekday();
            self.mmask.clear();
            self.mdaymask.clear();
            self.nmdaymask.clear();
            self.mrange = vec![0];
            for m in 1..=12u32 {
                let length = days_in_month(year, m);
                for d in 1..=length {
                    self.mmask.push(m);
                    self.mdaymask.push(i64::from(d));
                    self.nmdaymask.push(i64::from(d) - i64::from(length) - 1);
                }
                self.mrange.push(self.mrange.last().copied().unwrap_or(0) + length as usize);
            }
            for d in 1..=7 {
                self.mmask.push(1);
                self.mdaymask.push(d);
                self.nmdaymask.push(d - 32);
            }
            self.wdaymask = (0..self.yearlen + 30).map(|i| (yearweekday + i as u32) % 7).collect();
            self.wnomask = None;
            if !rule.byweekno.is_empty() {
                let yearlen = self.yearlen as i64;
                let wkst = i64::from(rule.wkst);
                let mut mask = vec![false; self.yearlen + 7];
                let yw = i64::from(yearweekday);
                let firstwkst = (7 - yw + wkst).rem_euclid(7);
                let mut no1wkst = firstwkst;
                let wyearlen = if no1wkst >= 4 {
                    no1wkst = 0;
                    yearlen + (yw - wkst).rem_euclid(7)
                } else {
                    yearlen - no1wkst
                };
                let numweeks = wyearlen / 7 + (wyearlen % 7) / 4;
                let wday = |i: i64| self.wdaymask.get(i as usize).copied().map(i64::from);
                let mut fill = |mask: &mut Vec<bool>, mut i: i64| {
                    for _ in 0..7 {
                        mark(mask, i);
                        i += 1;
                        if wday(i) == Some(wkst) {
                            break;
                        }
                    }
                };
                for &week in &rule.byweekno {
                    let n = if week < 0 { week + numweeks + 1 } else { week };
                    if !(0 < n && n <= numweeks) {
                        continue;
                    }
                    let mut i = if n > 1 { no1wkst + (n - 1) * 7 } else { no1wkst };
                    if n > 1 && no1wkst != firstwkst {
                        i -= 7 - firstwkst;
                    }
                    fill(&mut mask, i);
                }
                if rule.byweekno.contains(&1) {
                    let mut i = no1wkst + numweeks * 7;
                    if no1wkst != firstwkst {
                        i -= 7 - firstwkst;
                    }
                    if i < yearlen {
                        fill(&mut mask, i);
                    }
                }
                if no1wkst != 0 {
                    let lnumweeks = if !rule.byweekno.contains(&-1) {
                        let last = Date { year: year - 1, month: 1, day: 1 };
                        let lyearweekday = i64::from(if year > 1 { last.weekday() } else { 0 });
                        let lno1wkst = (7 - lyearweekday + wkst).rem_euclid(7);
                        let lyearlen = 365 + i64::from(is_leap(year - 1));
                        if lno1wkst >= 4 {
                            52 + (lyearlen + (lyearweekday - wkst).rem_euclid(7)) % 7 / 4
                        } else {
                            52 + (yearlen - no1wkst) % 7 / 4
                        }
                    } else {
                        -1
                    };
                    if rule.byweekno.contains(&lnumweeks) {
                        for i in 0..no1wkst {
                            mark(&mut mask, i);
                        }
                    }
                }
                self.wnomask = Some(mask);
            }
        }
        if let Some(nth) = &rule.bynweekday {
            if self.last != Some((year, month)) {
                let ranges: Vec<(usize, usize)> = match rule.freq {
                    YEARLY if !rule.bymonth.is_empty() => rule.bymonth.iter()
                        .filter(|m| (1..=12).contains(*m))
                        .map(|m| (self.mrange[*m as usize - 1], self.mrange[*m as usize])).collect(),
                    YEARLY => vec![(0, self.yearlen)],
                    MONTHLY => vec![(self.mrange[month as usize - 1], self.mrange[month as usize])],
                    _ => Vec::new(),
                };
                if !ranges.is_empty() {
                    let mut mask = vec![false; self.yearlen];
                    for (first, last) in ranges {
                        let (first, last) = (first as i64, last as i64 - 1);
                        for &(wday, n) in nth {
                            let wday = i64::from(wday);
                            let at = |i: i64| usize::try_from(i).ok().and_then(|i| self.wdaymask.get(i)).map(|w| i64::from(*w));
                            let i = if n < 0 {
                                let i = last + (n + 1) * 7;
                                match at(i) { Some(w) => i - (w - wday).rem_euclid(7), None => continue }
                            } else {
                                let i = first + (n - 1) * 7;
                                match at(i) { Some(w) => i + (7 - w + wday).rem_euclid(7), None => continue }
                            };
                            if first <= i && i <= last {
                                mark(&mut mask, i);
                            }
                        }
                    }
                    self.nwdaymask = Some(mask);
                }
            }
        }
        if !rule.byeaster.is_empty() && new_year {
            let len = self.yearlen + 7;
            let mut mask = vec![false; len];
            let eyday = easter(year).days() - self.yearday0;
            for offset in &rule.byeaster {
                let index = eyday + offset;
                mark(&mut mask, if index < 0 { index + len as i64 } else { index });
            }
            self.eastermask = Some(mask);
        }
        self.last = Some((year, month));
    }
}

pub struct Occurrences<'a> {
    rule: &'a Rule,
    year: i64,
    month: u32,
    day: i64,
    weekday: u32,
    info: Info,
    queue: VecDeque<i64>,
    left: Option<i64>,
    done: bool,
}

impl Occurrences<'_> {
    fn dayset(&self) -> (Vec<Option<usize>>, usize, usize) {
        let info = &self.info;
        match self.rule.freq {
            YEARLY => ((0..info.yearlen).map(Some).collect(), 0, info.yearlen),
            MONTHLY => {
                let (start, end) = (info.mrange[self.month as usize - 1], info.mrange[self.month as usize]);
                let mut set = vec![None; info.yearlen];
                for i in start..end {
                    set[i] = Some(i);
                }
                (set, start, end)
            }
            WEEKLY => {
                let mut set = vec![None; info.yearlen + 7];
                let mut i = (Date { year: self.year, month: self.month, day: self.day as u32 }.days() - info.yearday0) as usize;
                let start = i;
                for _ in 0..7 {
                    set[i] = Some(i);
                    i += 1;
                    if info.wdaymask[i] == self.rule.wkst {
                        break;
                    }
                }
                (set, start, i)
            }
            _ => {
                let mut set = vec![None; info.yearlen];
                let i = (Date { year: self.year, month: self.month, day: self.day as u32 }.days() - info.yearday0) as usize;
                set[i] = Some(i);
                (set, i, i + 1)
            }
        }
    }

    fn excluded(&self, i: usize) -> bool {
        let (rule, info) = (self.rule, &self.info);
        let index = i as i64;
        (!rule.bymonth.is_empty() && !rule.bymonth.contains(&info.mmask[i]))
            || info.wnomask.as_ref().is_some_and(|mask| !mask.get(i).copied().unwrap_or(false))
            || rule.byweekday.as_ref().is_some_and(|days| !days.contains(&info.wdaymask[i]))
            || info.nwdaymask.as_ref().is_some_and(|mask| !mask.is_empty() && !mask.get(i).copied().unwrap_or(false))
            || info.eastermask.as_ref().is_some_and(|mask| !mask.get(i).copied().unwrap_or(false))
            || ((!rule.bymonthday.is_empty() || !rule.bynmonthday.is_empty())
                && !rule.bymonthday.contains(&info.mdaymask[i]) && !rule.bynmonthday.contains(&info.nmdaymask[i]))
            || (!rule.byyearday.is_empty() && {
                let (len, next) = (info.yearlen as i64, info.nextyearlen as i64);
                if index < len {
                    !rule.byyearday.contains(&(index + 1)) && !rule.byyearday.contains(&(index - len))
                } else {
                    !rule.byyearday.contains(&(index + 1 - len)) && !rule.byyearday.contains(&(index - len - next))
                }
            })
    }

    /// The candidates of one period, then the step to the next (`rrule._iter`'s loop body).
    fn period(&mut self) {
        let (mut set, start, end) = self.dayset();
        for i in start..end {
            if set[i].is_some() && self.excluded(i) {
                set[i] = None;
            }
        }
        let rule = self.rule;
        let base = self.info.yearday0;
        let mut candidates = Vec::new();
        if !rule.bysetpos.is_empty() && !rule.timeset.is_empty() {
            let days: Vec<usize> = set[start..end].iter().flatten().copied().collect();
            let times = rule.timeset.len() as i64;
            for &pos in &rule.bysetpos {
                let (daypos, timepos) = if pos < 0 { (pos.div_euclid(times), pos.rem_euclid(times)) } else { ((pos - 1).div_euclid(times), (pos - 1).rem_euclid(times)) };
                let index = if daypos < 0 { days.len() as i64 + daypos } else { daypos };
                let Some(day) = usize::try_from(index).ok().and_then(|at| days.get(at)) else { continue };
                let wall = (base + *day as i64) * DAY + rule.timeset[timepos as usize];
                if !candidates.contains(&wall) {
                    candidates.push(wall);
                }
            }
            candidates.sort_unstable();
        } else {
            for day in set[start..end].iter().flatten() {
                for time in &rule.timeset {
                    candidates.push((base + *day as i64) * DAY + time);
                }
            }
        }
        for wall in candidates {
            let at = Dt { wall, zone: rule.dtstart.zone.clone(), fold: false };
            if rule.until.as_ref().is_some_and(|until| at.compare(until).is_gt()) {
                self.done = true;
                return;
            }
            if at.compare(&rule.dtstart).is_ge() {
                if let Some(left) = &mut self.left {
                    *left -= 1;
                    if *left < 0 {
                        self.done = true;
                        return;
                    }
                }
                self.queue.push_back(wall);
            }
        }
        self.step();
    }

    fn step(&mut self) {
        let rule = self.rule;
        let mut fixday = false;
        match rule.freq {
            YEARLY => {
                self.year += rule.interval;
                if self.year > 9999 {
                    self.done = true;
                    return;
                }
                self.info.rebuild(rule, self.year, self.month);
            }
            MONTHLY => {
                let mut month = i64::from(self.month) + rule.interval;
                if month > 12 {
                    let (div, rem) = (month.div_euclid(12), month.rem_euclid(12));
                    month = rem;
                    self.year += div;
                    if month == 0 {
                        month = 12;
                        self.year -= 1;
                    }
                    if self.year > 9999 {
                        self.done = true;
                        return;
                    }
                }
                self.month = month as u32;
                self.info.rebuild(rule, self.year, self.month);
            }
            WEEKLY => {
                let (wkst, weekday) = (i64::from(rule.wkst), i64::from(self.weekday));
                self.day += if wkst > weekday { -(weekday + 1 + (6 - wkst)) } else { -(weekday - wkst) } + rule.interval * 7;
                self.weekday = rule.wkst;
                fixday = true;
            }
            _ => {
                self.day += rule.interval;
                fixday = true;
            }
        }
        if fixday && self.day > 28 {
            let mut length = i64::from(days_in_month(self.year, self.month));
            if self.day > length {
                while self.day > length {
                    self.day -= length;
                    self.month += 1;
                    if self.month == 13 {
                        self.month = 1;
                        self.year += 1;
                        if self.year > 9999 {
                            self.done = true;
                            return;
                        }
                    }
                    length = i64::from(days_in_month(self.year, self.month));
                }
                self.info.rebuild(rule, self.year, self.month);
            }
        }
    }
}

impl Iterator for Occurrences<'_> {
    type Item = Dt;

    fn next(&mut self) -> Option<Dt> {
        loop {
            if let Some(wall) = self.queue.pop_front() {
                return Some(Dt { wall, zone: self.rule.dtstart.zone.clone(), fold: false });
            }
            if self.done {
                return None;
            }
            self.period();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ported::time::fromisoformat;

    fn starts(rule: &str, start: &str, zone: &str, take: usize) -> Vec<String> {
        let zone = Zone::named(zone).unwrap();
        let dtstart = fromisoformat(start).unwrap().astimezone(&zone).unwrap();
        Rule::parse(rule, &dtstart).unwrap().iter(true).take(take).map(|dt| dt.isoformat()).collect()
    }

    #[test]
    fn weekly_keeps_wall_clock_across_dst() {
        assert_eq!(starts("FREQ=WEEKLY;COUNT=3", "2026-10-26T09:00:00-05:00", "America/Chicago", 10),
                   ["2026-10-26T09:00:00-05:00", "2026-11-02T09:00:00-06:00", "2026-11-09T09:00:00-06:00"]);
    }

    #[test]
    fn monthly_nth_weekday_and_setpos() {
        assert_eq!(starts("FREQ=MONTHLY;BYDAY=-1FR;COUNT=2", "2026-01-01T10:00:00Z", "UTC", 5),
                   ["2026-01-30T10:00:00+00:00", "2026-02-27T10:00:00+00:00"]);
        assert_eq!(starts("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=2", "2026-01-01T10:00:00Z", "UTC", 5),
                   ["2026-01-30T10:00:00+00:00", "2026-02-27T10:00:00+00:00"]);
        assert_eq!(starts("FREQ=MONTHLY;COUNT=3", "2026-01-31T10:00:00Z", "UTC", 5),
                   ["2026-01-31T10:00:00+00:00", "2026-03-31T10:00:00+00:00", "2026-05-31T10:00:00+00:00"]);
    }

    #[test]
    fn refusals() {
        let start = fromisoformat("2026-01-01T10:00:00Z").unwrap();
        for rule in ["FREQ=DAILY;UNTIL=20270101T000000", "INTERVAL=2", "FREQ=DAILY;BYDAY=0MO", "FREQ=DAILY;", "FREQ=DAILY;BYHOUR=24"] {
            assert!(Rule::parse(rule, &start).is_err(), "{rule}");
        }
    }
}
