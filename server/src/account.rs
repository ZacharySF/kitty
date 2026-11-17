//! The account's local half (`kitty.web_account`): `GET /api/account` (whether Google sign-in
//! is configured, the connected identity, the welcome and tutorial flags) and `POST
//! /api/account/onboarding`. Google sign-in itself (`/api/account/google/{start, poll, cancel,
//! disconnect}`) stays on the worker: its pending attempts, PKCE verifiers and the loopback
//! redirect listener live in the worker's `GoogleConnection`, and disconnect also closes it.

use std::sync::atomic::{AtomicBool, Ordering};

use rusqlite::Connection;
use serde_json::{json, Value};

use crate::ported::{db, fail, internal, py, Call, Ctx, Done, Group};

pub static GROUP: Group = Group { owns, module: None, answer };

/// `app.google_connection.configured`, as the worker's readiness line reports it (the worker
/// read `KITTY_GOOGLE_CLIENT_ID` and `_SECRET` after the workspace's `.env`).
static CONFIGURED: AtomicBool = AtomicBool::new(false);

pub fn start(configured: bool) {
    CONFIGURED.store(configured, Ordering::Relaxed);
}

const FLAGS: [&str; 2] = ["welcome_completed", "tutorial_completed"];

fn owns(path: &str, mutation: bool) -> bool {
    path == if mutation { "/api/account/onboarding" } else { "/api/account" }
}

/// `web_account.state`.
fn state(conn: &Connection) -> Done<Value> {
    let row = db::row(conn, "SELECT * FROM workspace_onboarding WHERE id=1", [])?.ok_or_else(internal)?;
    let field = |key: &str| row.get(key).cloned().ok_or_else(internal);
    let identity = if row.get("google_sub").is_some_and(py::truthy) {
        json!({"sub": field("google_sub")?, "email": field("google_email")?, "name": field("google_name")?})
    } else {
        Value::Null
    };
    Ok(json!({"configured": CONFIGURED.load(Ordering::Relaxed), "identity": identity,
              "onboarding": {"welcome_completed": py::truthy(&field("welcome_completed")?),
                             "tutorial_completed": py::truthy(&field("tutorial_completed")?)}}))
}

fn answer(ctx: &Ctx, call: &Call) -> Done<Option<Value>> {
    if !owns(call.path, call.mutation()) {
        return Ok(None);
    }
    let conn = ctx.conn;
    if !call.mutation() {
        call.no_query()?;
        return state(conn).map(Some);
    }
    let body = call.body();
    if body.is_empty() || body.iter().any(|(key, value)| !FLAGS.contains(&key.as_str()) || !value.is_boolean()) {
        return Err(fail(400, "Expected welcome_completed or tutorial_completed booleans"));
    }
    db::transaction(conn, false, || {
        for (key, value) in body {
            conn.execute(&format!("UPDATE workspace_onboarding SET {key}=? WHERE id=1"), [i64::from(value.as_bool() == Some(true))])?;
        }
        Ok(())
    })?;
    state(conn).map(Some)
}
