//! Settings → Agents (`kitty.web_agents`' page routes in Python), over the workspace database:
//!
//! - `GET /api/agents`: `{host, sessions, policy, versions, retention, purposes, tools}`;
//! - `GET /api/agents/session?id=<id>[&before=<journal id>]`: `{session, journal, next, held, planted}`;
//! - `POST /api/agents/sessions/hold {id, hold}`: the user hold on a session's journal, on or off;
//! - `POST /api/agents/sessions/end {id}`: the session ends and its token is revoked.
//!
//! Answers, order and refusals are Python's; tests/test_agents_parity.py compares them.
//! `POST /api/agents/policy/{simulate, apply}` stay in the worker: a policy is YAML that
//! PyYAML parses (its YAML 1.1 values and error text are the answer). The agent ingress
//! (`/api/ingress/agent/*`: the launch exchange and the mediated tools) stays there too.

mod confine;
mod policy;
mod retention;
mod sessions;

use serde_json::{json, Value};

use crate::ported::db::{self, fail, internal, Done};
use crate::ported::time::{Dt, DAY, SECOND};
use crate::ported::{Call, Ctx, Group};

pub static GROUP: Group = Group { owns, module: None, answer };

const OVERVIEW: &str = "/api/agents";
const SESSION: &str = "/api/agents/session";
const HOLD: &str = "/api/agents/sessions/hold";
const END: &str = "/api/agents/sessions/end";

fn owns(path: &str, mutation: bool) -> bool {
    if mutation { path == HOLD || path == END } else { path == OVERVIEW || path == SESSION }
}

/// `agent_sessions.now()`: `datetime.now(UTC).isoformat(timespec="seconds")`.
fn stamp() -> Done<String> {
    let now = Dt::now_utc();
    Ok(now.add(-(now.time_of_day() % SECOND))?.isoformat())
}

/// `web_server._integer`.
fn integer(text: &str, name: &str, minimum: i64, maximum: i64) -> Done<i64> {
    if text.is_empty() || text.len() > 10 || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(fail(400, format!("{name} must be an integer")));
    }
    let number: i64 = text.parse().map_err(|_| internal())?;
    if !(minimum..=maximum).contains(&number) {
        return Err(fail(400, format!("{name} must be between {minimum} and {maximum}")));
    }
    Ok(number)
}

/// `web_agents._overview`.
fn overview(conn: &rusqlite::Connection) -> Done<Value> {
    let policy = policy::active(conn)?;
    // `web_agents._cutoff`: the History window (`history.retention_days`) back from now.
    let days: i64 = conn.query_row("SELECT history_retention_days FROM build_state WHERE id=1", [], |row| row.get::<_, Option<i64>>(0))?.ok_or_else(internal)?;
    let cutoff = Dt::now_utc().add(-days.checked_mul(DAY).ok_or_else(internal)?)?.isoformat();
    Ok(json!({"host": confine::host(), "sessions": sessions::listing(conn)?, "policy": policy, "versions": policy::versions(conn)?,
              "retention": retention::status(conn, &cutoff)?, "purposes": policy::purposes(), "tools": policy::tools()}))
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    let conn = ctx.conn;
    if let Some(body) = call.body {
        let Some(Value::String(session)) = body.get("id") else {
            return Err(fail(400, "Expected {\"id\"}"));
        };
        let hold = if call.path == HOLD {
            let Some(Value::Bool(hold)) = body.get("hold").filter(|_| body.len() == 2) else {
                return Err(fail(400, "Expected {\"id\", \"hold\": true|false}"));
            };
            Some(*hold)
        } else if body.len() != 1 {
            return Err(fail(400, "Expected {\"id\"}"));
        } else {
            None
        };
        return db::transaction(conn, true, || {
            let now = stamp()?;
            match hold {
                Some(true) => retention::hold(conn, session, &now)?,
                Some(false) => retention::release(conn, session, &now)?,
                None => sessions::end(conn, session, "Ended in Settings → Agents; its token was revoked", &now)?,
            }
            sessions::detail(conn, session, None).map(Some)
        });
    }
    if call.path == OVERVIEW {
        call.no_query()?;
        return overview(conn).map(Some);
    }
    let Some(session) = call.get("id").filter(|_| call.only(&["id", "before"])) else {
        return Err(fail(400, "Expected ?id= (and optionally before=)"));
    };
    let before = call.get("before").map(|text| integer(text, "before", 1, 1 << 62)).transpose()?;
    sessions::detail(conn, session, before).map(Some)
}
