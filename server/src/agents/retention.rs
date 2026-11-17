//! What History compaction may not fold yet, for Settings → Agents (`kitty.core.blocks.retention`):
//! the floor, its reasons and the size cap, and the user holds on one session's journal.
//! Compaction itself (`floor(release=True)`, `prune_journal`) stays with Python's History.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use crate::ported::db::{column, fail, internal, Done};

/// `retention.MAX_RETAINED`: op_log rows older than the History window that reasons may keep.
const MAX_RETAINED: i64 = 200_000;
static PINNED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\(\(([0-9a-f-]{36})@([0-9]{1,18})\)\)").expect("a valid pattern"));

struct Reason {
    reason: &'static str,
    seq: i64,
}

/// `retention.reasons`: every live reason with the lowest op_log seq it needs, lowest first.
fn reasons(conn: &Connection, cutoff: &str) -> Done<Vec<Reason>> {
    let mut out = Vec::new();
    let (lowest, count): (Option<i64>, i64) = conn.query_row("SELECT MIN(seq), COUNT(*) FROM agent_access WHERE at>=?", [cutoff], |row| Ok((row.get(0)?, row.get(1)?)))?;
    if count > 0 {
        out.push(Reason { reason: "replay_window", seq: lowest.ok_or_else(internal)? });
    }
    // `retention.pinned`: one reason per distinct (block, seq) a live block's text cites.
    let mut cited = HashSet::new();
    let mut statement = conn.prepare("SELECT id, text FROM blocks WHERE deleted_at IS NULL AND text LIKE '%((%@%))%'")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let text: String = row.get(1)?;
        for found in PINNED.captures_iter(&text) {
            let seq: i64 = found[2].parse().map_err(|_| internal())?;
            if cited.insert((found[1].to_owned(), seq)) {
                out.push(Reason { reason: "pinned_citation", seq });
            }
        }
    }
    let mut statement = conn.prepare("SELECT seq FROM retention_holds WHERE released_at IS NULL")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        out.push(Reason { reason: "user_hold", seq: row.get(0)? });
    }
    out.sort_by(|a, b| (a.seq, a.reason).cmp(&(b.seq, b.reason)));
    Ok(out)
}

/// `retention.status`: the current floor, its reasons, and the horizon (`floor` without releasing).
pub fn status(conn: &Connection, cutoff: &str) -> Done<Value> {
    let mut live = reasons(conn, cutoff)?;
    let mut released = Vec::new();
    if let Some(first) = live.first() {
        let lowest = first.seq;
        let kept: i64 = conn.query_row("SELECT COUNT(*) FROM op_log WHERE seq>=? AND at<?", params![lowest, cutoff], |row| row.get(0))?;
        if kept > MAX_RETAINED {
            let limit: i64 = conn
                .query_row("SELECT seq FROM op_log WHERE at<? ORDER BY seq DESC LIMIT 1 OFFSET ?", params![cutoff, MAX_RETAINED - 1], |row| row.get(0))
                .optional()?
                .unwrap_or(lowest);
            let (dropped, kept): (Vec<Reason>, Vec<Reason>) = live.into_iter().partition(|item| item.seq < limit);
            released = dropped;
            live = kept;
        }
    }
    let horizon = conn.query_row("SELECT horizon_seq FROM block_history WHERE id=1", [], |row| Ok(column(row.get_ref(0)?))).optional()?.ok_or_else(internal)??;
    let mut counts = Map::new();
    for item in &live {
        let count = counts.get(item.reason).and_then(Value::as_i64).unwrap_or(0);
        counts.insert(item.reason.into(), json!(count + 1));
    }
    let journal: i64 = conn.query_row("SELECT COUNT(*) FROM agent_access", [], |row| row.get(0))?;
    Ok(json!({"floor": live.first().map(|item| item.seq), "horizon_seq": horizon, "reasons": counts, "cap": MAX_RETAINED,
              "over_cap": released.iter().map(|item| json!({"reason": item.reason, "seq": item.seq})).collect::<Vec<_>>(),
              "journal_rows": journal}))
}

/// `retention.hold`: keep one agent session's journal (and the op_log rows it cites) past the window.
pub fn hold(conn: &Connection, session: &str, stamp: &str) -> Done<()> {
    if !crate::ported::db::exists(conn, "SELECT 1 FROM agent_sessions WHERE id=?", [session])? {
        return Err(fail(404, "No such agent session"));
    }
    if crate::ported::db::exists(conn, "SELECT 1 FROM retention_holds WHERE session=? AND released_at IS NULL", [session])? {
        return Ok(());
    }
    let oldest: Option<i64> = conn.query_row("SELECT MIN(seq) FROM agent_access WHERE session=?", [session], |row| row.get(0))?;
    let seq = match oldest {
        Some(seq) => seq,
        None => conn.query_row("SELECT COALESCE(MAX(seq), 0) + 1 FROM op_log", [], |row| row.get(0))?,
    };
    conn.execute("INSERT INTO retention_holds(session, seq, note, created_at) VALUES (?,?,NULL,?)", params![session, seq, stamp])?;
    Ok(())
}

/// `retention.release`: the user lets the session's journal go with the window again.
pub fn release(conn: &Connection, session: &str, stamp: &str) -> Done<()> {
    conn.execute("UPDATE retention_holds SET released_at=?, released_by='user' WHERE session=? AND released_at IS NULL", params![stamp, session])?;
    Ok(())
}
