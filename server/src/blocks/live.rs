//! A window's block transaction over the live channel (`web_live.Live.transact` in Python):
//! `{"type": "txn", "txn_id"?, "ops", "base"?}`, applied as the person at this device, answered
//! `{"type": "ack", ...}` or `{"type": "reject", "txn_id", "status", "error"}`.

use rusqlite::Connection;
use serde_json::{json, Value};

use super::actor::Actor;
use super::doc::refuse;
use super::store;
use crate::ported::py;

pub const BUSY: &str = "Local workspace is busy; send the transaction again";

/// The reject a window gets when the transaction could not be applied for a reason other than
/// the transaction itself: what the front answered when the worker failed (its `txn_id` the
/// message's, when it is a string).
pub fn busy(message: &Value) -> Value {
    let txn_id = message.get("txn_id").filter(|id| id.is_string()).cloned().unwrap_or(Value::Null);
    json!({ "type": "reject", "txn_id": txn_id, "status": 503, "error": BUSY })
}

/// `Live.transact`.
pub fn transact(conn: &Connection, modules: &[String], message: &Value) -> Value {
    let txn_id = match message.get("txn_id").filter(|id| py::truthy(id)) {
        Some(id) => id.clone(),
        None => match store::uuid4() {
            Ok(id) => Value::String(id),
            Err(_) => return busy(message),
        },
    };
    let reject = |status: u16, error: &str| json!({ "type": "reject", "txn_id": txn_id, "status": status, "error": error });
    if !modules.iter().any(|module| module == "workspace") {
        return reject(403, "Block edits need the workspace module");
    }
    let applied = (|| {
        let fields = message.as_object().map(|message| message.keys().all(|key| matches!(key.as_str(), "type" | "txn_id" | "ops" | "base")));
        if fields != Some(true) {
            return Err(refuse("Unexpected transaction fields"));
        }
        let ops = message.get("ops").unwrap_or(&Value::Null);
        store::transact(conn, ops, message.get("base"), Some(&txn_id), true, &Actor::user())
    })();
    match applied {
        Ok(result) => {
            let mut ack = serde_json::Map::new();
            ack.insert("type".into(), "ack".into());
            ack.extend(result);
            Value::Object(ack)
        }
        // `sqlite3.OperationalError`, caught by Python with the transaction's ID.
        Err(fault) if fault.status == 503 => reject(503, BUSY),
        // Any other exception failed the worker's request: the front's own reject.
        Err(fault) if fault.status >= 500 => busy(message),
        Err(fault) => reject(fault.status, &fault.message),
    }
}
