//! The part of `kitty.core.effects` the pending-approvals route uses, over the same tables
//! (migration 053): loading effects and their events, ownership of the `prompt` kinds, the
//! claim transaction of `approve` and `deny`, expiry and the digest re-check, and the
//! listing. `delete_block`'s preconditions are plain SQL (`effect_kinds._delete_preconditions`);
//! `submit_application`'s are read by the worker (crate::effects::internal).

use rusqlite::types::Value as Sql;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use super::canonical::{canonical, changed_names, digest, sha256_hex};
use super::{database, fail, internal_error, Failure};
use crate::migrations::civil;

/// `effect_kinds.SUBMIT_HANDOFF`.
pub const SUBMIT_HANDOFF: &str = "rika! never submits applications. Your approval is recorded; open the application in your \
                                  browser, check it, and press Submit yourself. Then mark the job applied.";
/// `effects.DEFAULT_APPROVAL_LIFETIME`, which both prompt kinds keep.
const APPROVAL_LIFETIME_MICROS: i64 = 2 * 60 * 1_000_000;
/// `effects.pending`'s and `effects.recent`'s limits.
const PENDING_LIMIT: usize = 50;
const RECENT_LIMIT: i64 = 20;

/// The kinds reviewed in the pending-approvals list (`REGISTRY.reviewed_in("prompt")`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    DeleteBlock,
    SubmitApplication,
}

impl Kind {
    pub fn of(name: &str) -> Option<Self> {
        match name {
            "delete_block" => Some(Self::DeleteBlock),
            "submit_application" => Some(Self::SubmitApplication),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::DeleteBlock => "delete_block",
            Self::SubmitApplication => "submit_application",
        }
    }

    fn title(self) -> &'static str {
        match self {
            Self::DeleteBlock => "Delete a note block",
            Self::SubmitApplication => "Submit an application",
        }
    }

    /// An `Executable` kind; `submit_application` is `ProposalOnly`.
    pub fn executable(self) -> bool {
        self == Self::DeleteBlock
    }

    pub fn handoff(self) -> Option<&'static str> {
        (self == Self::SubmitApplication).then_some(SUBMIT_HANDOFF)
    }
}

/// A stored effect and its latest state (`effects.Effect`).
pub struct Effect {
    pub id: String,
    pub kind: String,
    pub payload: Value,
    preconditions: Value,
    preview: Value,
    digest: String,
    actor: Value,
    pub state: String,
    created_at: String,
    expires_at: String,
    approval_expires_at: Option<String>,
}

impl Effect {
    /// The kind, which the caller already checked is one this route owns.
    pub fn owned_kind(&self) -> Kind {
        Kind::of(&self.kind).expect("an owned effect has a prompt kind")
    }
}

fn actor(kind: String, name: Option<String>, session: Option<String>) -> Value {
    json!({ "kind": kind, "name": name, "session": session })
}

fn parse_json(text: &str) -> Result<Value, Failure> {
    serde_json::from_str(text).map_err(|_| internal_error())
}

/// `effects._load`: 404 for anything that is not a stored effect's ID.
pub fn load(conn: &Connection, id: Option<&Value>) -> Result<Effect, Failure> {
    let not_found = || fail(404, "Effect not found");
    let id = match id {
        Some(Value::String(id)) if id.len() == 32 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) => id,
        _ => return Err(not_found()),
    };
    let row = conn
        .query_row(
            "SELECT id, kind, payload, preconditions, preview, digest, actor, actor_name, actor_session, created_at, expires_at
               FROM effects WHERE id=?",
            [id],
            |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?, row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?, row.get::<_, String>(5)?, row.get::<_, String>(6)?, row.get::<_, Option<String>>(7)?,
                    row.get::<_, Option<String>>(8)?, row.get::<_, String>(9)?, row.get::<_, String>(10)?))
            },
        )
        .optional()
        .map_err(database)?
        .ok_or_else(not_found)?;
    let latest = conn
        .query_row("SELECT state, expires_at FROM effect_events WHERE effect_id=? ORDER BY seq DESC LIMIT 1", [id], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
        })
        .optional()
        .map_err(database)?;
    let (state, approval_expires_at) = match latest {
        Some((state, expires)) if state == "approved" => (state, expires),
        Some((state, _)) => (state, None),
        None => ("proposed".to_owned(), None),
    };
    Ok(Effect {
        id: row.0,
        kind: row.1,
        payload: parse_json(&row.2)?,
        preconditions: parse_json(&row.3)?,
        preview: parse_json(&row.4)?,
        digest: row.5,
        actor: actor(row.6, row.7, row.8),
        state,
        created_at: row.9,
        expires_at: row.10,
        approval_expires_at,
    })
}

/// `effects._open`: the effect, refused untouched (403) unless it is a prompt kind.
pub fn open(conn: &Connection, id: Option<&Value>) -> Result<Effect, Failure> {
    let effect = load(conn, id)?;
    if Kind::of(&effect.kind).is_none() {
        return Err(fail(403, "Review this effect in its own view"));
    }
    Ok(effect)
}

/// `effects.events`: every transition, oldest first.
fn events(conn: &Connection, id: &str) -> Result<Vec<Value>, Failure> {
    let mut statement = conn
        .prepare("SELECT state, actor, actor_name, actor_session, digest, detail, at, expires_at FROM effect_events WHERE effect_id=? ORDER BY seq")
        .map_err(database)?;
    let rows = statement
        .query_map([id], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, Option<String>>(2)?, row.get::<_, Option<String>>(3)?,
                row.get::<_, Option<String>>(4)?, row.get::<_, Option<String>>(5)?, row.get::<_, String>(6)?, row.get::<_, Option<String>>(7)?))
        })
        .map_err(database)?;
    let mut found = Vec::new();
    for row in rows {
        let (state, kind, name, session, digest, detail, at, expires_at) = row.map_err(database)?;
        let detail = match detail.filter(|text| !text.is_empty()) {
            Some(text) => parse_json(&text)?,
            None => Value::Null,
        };
        found.push(json!({ "state": state, "actor": actor(kind, name, session), "digest": digest, "detail": detail,
                           "at": at, "expires_at": expires_at }));
    }
    Ok(found)
}

/// An effect as the list shows it: `Effect.as_dict` plus `executable`, `handoff` and the events.
pub fn view(conn: &Connection, effect: &Effect) -> Result<Value, Failure> {
    let kind = Kind::of(&effect.kind);
    Ok(json!({
        "id": effect.id, "kind": effect.kind, "title": kind.map(Kind::title).unwrap_or(&effect.kind),
        "payload": effect.payload, "preconditions": effect.preconditions, "preview": effect.preview,
        "digest": effect.digest, "actor": effect.actor, "state": effect.state,
        "created_at": effect.created_at, "expires_at": effect.expires_at,
        "approval_expires_at": effect.approval_expires_at,
        "executable": kind.is_some_and(Kind::executable), "handoff": kind.and_then(Kind::handoff),
        "events": events(conn, &effect.id)?,
    }))
}

fn ids(conn: &Connection, sql: &str, parameter: &dyn rusqlite::ToSql) -> Result<Vec<String>, Failure> {
    let mut statement = conn.prepare(sql).map_err(database)?;
    let rows = statement.query_map([parameter], |row| row.get::<_, String>(0)).map_err(database)?;
    rows.collect::<Result<_, _>>().map_err(database)
}

/// The list: the pending prompt-kind proposals (`effects.pending`), oldest first, and the latest
/// decided effects of those kinds, newest first.
pub fn listing(conn: &Connection, moment: i64) -> Result<Map<String, Value>, Failure> {
    let mut pending = Vec::new();
    let waiting = ids(conn, "SELECT e.id FROM effects e JOIN effect_events v ON v.effect_id=e.id
                             WHERE v.seq=(SELECT MAX(seq) FROM effect_events WHERE effect_id=e.id) AND v.state='proposed'
                             AND e.expires_at>? ORDER BY e.created_at, e.id", &stamp(moment))?;
    for id in waiting {
        let effect = load(conn, Some(&Value::String(id)))?;
        if Kind::of(&effect.kind).is_some() {
            pending.push(view(conn, &effect)?);
        }
        if pending.len() >= PENDING_LIMIT {
            break;
        }
    }
    let mut recent = Vec::new();
    let decided = ids(conn, "SELECT e.id FROM effects e JOIN effect_events v ON v.effect_id=e.id
                             WHERE v.seq=(SELECT MAX(seq) FROM effect_events WHERE effect_id=e.id) AND v.state<>'proposed'
                             ORDER BY v.seq DESC LIMIT ?", &RECENT_LIMIT)?;
    for id in decided {
        let effect = load(conn, Some(&Value::String(id)))?;
        if Kind::of(&effect.kind).is_some() {
            recent.push(view(conn, &effect)?);
        }
    }
    let mut result = Map::new();
    result.insert("pending".into(), Value::Array(pending));
    result.insert("recent".into(), Value::Array(recent));
    Ok(result)
}

// ------------------------------------------------------------------------------ claims

/// `effects._event` for the person at the front's window session.
fn event(conn: &Connection, effect: &Effect, state: &str, session: &str, at: i64, detail: Option<&Value>,
         expires_at: Option<i64>) -> Result<(), Failure> {
    conn.execute(
        "INSERT INTO effect_events(effect_id,state,actor,actor_name,actor_session,digest,detail,at,expires_at)
         VALUES(?,?,'user',NULL,?,?,?,?,?)",
        params![effect.id, state, session, effect.digest, detail.map(canonical), stamp(at), expires_at.map(stamp)],
    )
    .map(|_| ())
    .map_err(database)
}

/// A refusal found inside a claim (`effects._Problem`): recorded and committed.
struct Problem {
    state: &'static str,
    message: &'static str,
    changed: Vec<String>,
}

/// What ends a claim: a refusal to record, or a failure that rolls everything back.
enum Stop {
    Refuse(Effect, Problem),
    Fail(Failure),
}

impl From<Failure> for Stop {
    fn from(failure: Failure) -> Self {
        Self::Fail(failure)
    }
}

/// `effects._Claim`: one immediate transaction. A refusal found inside it is recorded and
/// committed (refusals are final); any other failure rolls everything back.
fn claim(conn: &Connection, session: &str, moment: i64, work: impl FnOnce() -> Result<(), Stop>) -> Result<(), Failure> {
    conn.execute_batch("BEGIN IMMEDIATE").map_err(database)?;
    let outcome = match work() {
        Ok(()) => conn.execute_batch("COMMIT").map_err(database),
        Err(Stop::Refuse(effect, problem)) => {
            let mut detail = Map::new();
            detail.insert("reason".into(), Value::String(problem.message.into()));
            if !problem.changed.is_empty() {
                detail.insert("changed".into(), json!(problem.changed));
            }
            event(conn, &effect, problem.state, session, moment, Some(&Value::Object(detail)), None)
                .and_then(|()| conn.execute_batch("COMMIT").map_err(database))
                .and(Err(Failure { status: 409, message: problem.message.into(), changed: problem.changed }))
        }
        Err(Stop::Fail(failure)) => Err(failure),
    };
    if outcome.is_err() && !conn.is_autocommit() {
        let _ = conn.execute_batch("ROLLBACK");
    }
    outcome
}

fn matches(effect: &Effect, presented: Option<&Value>) -> bool {
    matches!(presented, Some(Value::String(presented)) if crate::auth::same(presented, &effect.digest))
}

fn object(value: &Value) -> Result<&Map<String, Value>, Failure> {
    value.as_object().ok_or_else(internal_error)
}

/// `effects._problem`: expiry, the presented digest, then every precondition and the
/// recomputed digest, read inside the claim.
fn problem(conn: &Connection, effect: &Effect, presented: Option<&Value>, moment: i64,
           read: impl FnOnce(&Connection, &Effect) -> Result<Value, Failure>) -> Result<Option<Problem>, Failure> {
    let approved = effect.state == "approved";
    let deadline = if approved { effect.approval_expires_at.as_deref() } else { Some(effect.expires_at.as_str()) };
    let expired = match deadline {
        None => true,
        Some(text) => parse_stamp(text).ok_or_else(internal_error)? <= moment,
    };
    if expired {
        let message = if approved { "This approval expired; ask again" } else { "This proposal expired; propose it again" };
        return Ok(Some(Problem { state: "expired", message, changed: Vec::new() }));
    }
    if !matches(effect, presented) {
        return Ok(Some(Problem { state: "refused", message: "The reviewed effect differs from this one; review it again", changed: Vec::new() }));
    }
    let current = read(conn, effect)?;
    let current = object(&current)?;
    if digest(&effect.kind, &effect.payload, current) != effect.digest {
        return Ok(Some(Problem {
            state: "refused",
            message: "Something this effect depends on changed since it was proposed; review it again",
            changed: changed_names(object(&effect.preconditions)?, current),
        }));
    }
    Ok(None)
}

/// `effects.approve` of an owned effect: re-checked inside the claim, then approved for
/// `session`, single-use and short-lived. `read` gives the current precondition values.
pub fn approve(conn: &Connection, id: &str, presented: Option<&Value>, session: &str, moment: i64,
               read: impl FnOnce(&Connection, &Effect) -> Result<Value, Failure>) -> Result<(), Failure> {
    claim(conn, session, moment, || {
        let effect = load(conn, Some(&Value::String(id.to_owned())))?;
        if effect.state != "proposed" {
            return Err(Stop::Fail(fail(409, format!("This effect is already {}", effect.state))));
        }
        if let Some(problem) = problem(conn, &effect, presented, moment, read)? {
            return Err(Stop::Refuse(effect, problem));
        }
        event(conn, &effect, "approved", session, moment, None, Some(moment + APPROVAL_LIFETIME_MICROS))?;
        Ok(())
    })
}

/// `effects.deny`: the person says no to a proposed or approved effect of an owned kind.
pub fn deny(conn: &Connection, id: Option<&Value>, session: &str, moment: i64) -> Result<Effect, Failure> {
    let mut denied = None;
    claim(conn, session, moment, || {
        let effect = open(conn, id)?;
        if effect.state != "proposed" && effect.state != "approved" {
            return Err(Stop::Fail(fail(409, format!("This effect is already {}", effect.state))));
        }
        event(conn, &effect, "denied", session, moment, None, None)?;
        denied = Some(effect.id);
        Ok(())
    })?;
    load(conn, denied.map(Value::String).as_ref())
}

// ------------------------------------------------------------------------------ delete_block

/// A value as `sqlite3` hands it to Python and `json.dumps` writes it.
fn sql_json(value: Sql) -> Result<Value, Failure> {
    Ok(match value {
        Sql::Null => Value::Null,
        Sql::Integer(number) => json!(number),
        Sql::Real(number) => json!(number),
        Sql::Text(text) => Value::String(text),
        Sql::Blob(_) => return Err(internal_error()),
    })
}

/// `str(value)` inside an f-string.
fn sql_str(value: &Sql) -> Result<String, Failure> {
    Ok(match value {
        Sql::Null => "None".into(),
        Sql::Integer(number) => number.to_string(),
        Sql::Text(text) => text.clone(),
        Sql::Real(_) | Sql::Blob(_) => return Err(internal_error()),
    })
}

/// `effect_kinds._sha`: SHA-256 of the text, `""` for NULL.
fn text_sha(value: &Sql) -> Result<String, Failure> {
    match value {
        Sql::Text(text) => Ok(sha256_hex(text)),
        Sql::Null | Sql::Integer(0) => Ok(sha256_hex("")),
        _ => Err(internal_error()),
    }
}

/// `effect_kinds._delete_preconditions`: the target block and its whole live subtree.
pub fn delete_preconditions(conn: &Connection, payload: &Value) -> Result<Value, Failure> {
    let block = match payload.get("block").ok_or_else(internal_error)? {
        Value::String(text) => Sql::Text(text.clone()),
        Value::Null => Sql::Null,
        Value::Number(number) => Sql::Integer(number.as_i64().ok_or_else(internal_error)?),
        _ => return Err(internal_error()),
    };
    let row = conn
        .query_row(
            "SELECT b.id, b.page_id, b.parent_id, b.type, b.text, b.deleted_at, r.deleted_at AS root_deleted
               FROM blocks b JOIN blocks r ON r.id=b.page_id WHERE b.id=?",
            [&block],
            |row| Ok((row.get::<_, Sql>(0)?, row.get::<_, Sql>(1)?, row.get::<_, Sql>(2)?, row.get::<_, Sql>(3)?,
                      row.get::<_, Sql>(4)?, row.get::<_, Sql>(5)?, row.get::<_, Sql>(6)?)),
        )
        .optional()
        .map_err(database)?;
    let Some((id, page_id, parent_id, kind, text, deleted_at, root_deleted)) = row else {
        return Ok(json!({ "target": null, "subtree": null }));
    };
    if deleted_at != Sql::Null || root_deleted != Sql::Null {
        return Ok(json!({ "target": null, "subtree": null }));
    }
    let mut statement = conn
        .prepare("WITH RECURSIVE tree(id) AS (SELECT id FROM blocks WHERE parent_id=? AND deleted_at IS NULL
                   UNION ALL SELECT b.id FROM blocks b JOIN tree t ON b.parent_id=t.id WHERE b.deleted_at IS NULL)
                  SELECT b.id, b.type, b.text FROM blocks b JOIN tree USING(id) ORDER BY b.id")
        .map_err(database)?;
    let children = statement
        .query_map([&id], |row| Ok((row.get::<_, Sql>(0)?, row.get::<_, Sql>(1)?, row.get::<_, Sql>(2)?)))
        .map_err(database)?;
    let mut lines = Vec::new();
    for child in children {
        let (child_id, child_type, child_text) = child.map_err(database)?;
        lines.push(format!("{}\t{}\t{}", sql_str(&child_id)?, sql_str(&child_type)?, text_sha(&child_text)?));
    }
    Ok(json!({
        "target": { "page_id": sql_json(page_id)?, "parent_id": sql_json(parent_id)?, "type": sql_json(kind)?,
                    "text_sha256": text_sha(&text)? },
        "subtree": { "blocks": lines.len(), "sha256": sha256_hex(&lines.join("\n")) },
    }))
}

// ------------------------------------------------------------------------------ time

/// Now, in microseconds since the epoch (UTC), as `effects.now()`.
pub fn now() -> i64 {
    let elapsed = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    elapsed.as_micros() as i64
}

const DAY: i64 = 86_400_000_000;

/// `effects._stamp`: `isoformat(timespec="microseconds")` in UTC.
pub fn stamp(micros: i64) -> String {
    let (year, month, day) = civil(micros.div_euclid(DAY));
    let rest = micros.rem_euclid(DAY);
    let (hour, minute, second, fraction) = (rest / 3_600_000_000, rest / 60_000_000 % 60, rest / 1_000_000 % 60, rest % 1_000_000);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{fraction:06}+00:00")
}

/// Days since 1970-01-01 (Howard Hinnant's `days_from_civil`).
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = year - i64::from(month <= 2);
    let era = year.div_euclid(400);
    let yoe = year.rem_euclid(400);
    let doy = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// `datetime.fromisoformat` of an aware stamp as `_stamp` writes them
/// (`YYYY-MM-DDTHH:MM:SS[.ffffff]` and an offset, `Z` or `±HH:MM`), in microseconds; None
/// for anything else (a naive stamp cannot be compared with Python's aware moment either).
pub fn parse_stamp(text: &str) -> Option<i64> {
    let number = |part: &str| (!part.is_empty() && part.bytes().all(|b| b.is_ascii_digit())).then(|| part.parse::<i64>().ok()).flatten();
    if !text.is_ascii() || text.len() < 20 {
        return None;
    }
    let (date, time) = (&text[..10], &text[11..]);
    if !matches!(text.as_bytes()[10], b'T' | b' ') || date.as_bytes()[4] != b'-' || date.as_bytes()[7] != b'-' {
        return None;
    }
    let (year, month, day) = (number(&date[..4])?, number(&date[5..7])?, number(&date[8..10])?);
    let days_in_month = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) => 29,
        2 => 28,
        _ => return None,
    };
    if year < 1 || day < 1 || day > days_in_month {
        return None;
    }
    let split = time.find(['+', '-', 'Z'])?;
    let (clock, offset) = time.split_at(split);
    let (whole, fraction) = clock.split_once('.').unwrap_or((clock, ""));
    let parts: Vec<&str> = whole.split(':').collect();
    let [hour, minute, second] = parts[..] else { return None };
    if hour.len() != 2 || minute.len() != 2 || second.len() != 2 || fraction.len() > 6 || (clock.contains('.') && fraction.is_empty()) {
        return None;
    }
    let (hour, minute, second) = (number(hour)?, number(minute)?, number(second)?);
    let micros = if fraction.is_empty() { 0 } else { number(fraction)? * 10i64.pow(6 - fraction.len() as u32) };
    if hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    let shift = match offset {
        "Z" => 0,
        _ if offset.len() == 6 && offset.as_bytes()[3] == b':' => {
            let (hours, minutes) = (number(&offset[1..3])?, number(&offset[4..6])?);
            if hours > 23 || minutes > 59 {
                return None;
            }
            let shift = (hours * 60 + minutes) * 60_000_000;
            if offset.starts_with('-') { -shift } else { shift }
        }
        _ => return None,
    };
    Some(days_from_civil(year, month, day) * DAY + ((hour * 60 + minute) * 60 + second) * 1_000_000 + micros - shift)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stamps_are_written_and_read_as_python_does() {
        // datetime(2026, 11, 13, 9, 5, 7, 42, tzinfo=UTC).isoformat(timespec="microseconds")
        let moment = days_from_civil(2026, 11, 13) * DAY + ((9 * 60 + 5) * 60 + 7) * 1_000_000 + 42;
        assert_eq!(stamp(moment), "2026-11-13T09:05:07.000042+00:00");
        assert_eq!(parse_stamp(&stamp(moment)), Some(moment));
        assert_eq!(parse_stamp("2026-11-13T10:05:07.000042+01:00"), Some(moment));
        assert_eq!(parse_stamp("2026-11-13T09:05:07Z"), Some(moment - 42));
        assert_eq!(stamp(0), "1970-01-01T00:00:00.000000+00:00");
        for naive in ["2026-11-13T09:05:07", "2026-02-30T00:00:00+00:00", "2026-11-13T09:05:07.+00:00", "garbage"] {
            assert_eq!(parse_stamp(naive), None, "{naive}");
        }
    }
}
