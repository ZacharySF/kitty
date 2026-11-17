//! Agent sessions and undoing everything one of them did (`kitty.core.blocks.sessions`, paper §14.3).
//!
//! Every `op_log` row names its actor; an agent's rows share its session. `list_sessions`
//! lists the sessions in the kept history (merges from a paired device included, by the actors
//! they carried). `undo_session` computes the inverse of every transaction of one session,
//! field by field against the blocks' current state (a field someone else changed since is
//! kept and reported as a conflict), and applies it as ONE new forward transaction.

use std::collections::{BTreeSet, HashMap, HashSet};

use indexmap::IndexMap;
use rusqlite::{params_from_iter, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use super::actor::{from_row, is_session, Actor, KINDS};
use super::doc::{refuse, slice, Block, PageDoc, Titles};
use super::history::within;
use super::json::{dumps, CANONICAL};
use super::store::{load_page, logged_ops, read_block, replay, transact};
use crate::ported::{db, fail, internal, Done, Fault};

pub const MAX_SESSIONS: i64 = 200;
/// Conflicts and reverted blocks listed in a report (the counts are always exact).
pub const MAX_LISTED: usize = 200;
const SNIPPET: usize = 120;

/// One block's state, field by field (`_fields`): `place` is `[page_id, parent_id, order_key]`.
type State = Map<String, Value>;

fn session_of(value: &Value) -> Done<String> {
    match value {
        Value::String(text) if is_session(text) => Ok(text.clone()),
        _ => Err(refuse("Name the agent session by its ID (from the sessions list or History)")),
    }
}

struct LogRow {
    seq: i64,
    txn_id: String,
    page_id: String,
    at: Value,
    actor: String,
    actor_name: Option<String>,
    actor_session: Option<String>,
    actor_via: Option<String>,
}

impl LogRow {
    /// Reads `seq,txn_id,page_id,at,…,actor,actor_name,actor_session,actor_via` by name.
    fn read(row: &rusqlite::Row) -> Done<LogRow> {
        Ok(LogRow {
            seq: row.get("seq")?,
            txn_id: row.get("txn_id")?,
            page_id: row.get("page_id")?,
            at: db::column(row.get_ref("at")?)?,
            actor: row.get("actor")?,
            actor_name: row.get("actor_name")?,
            actor_session: row.get("actor_session")?,
            actor_via: row.get("actor_via")?,
        })
    }

    fn who(&self) -> Done<Actor> {
        from_row(&self.actor, self.actor_name.clone(), self.actor_session.clone(), self.actor_via.clone())
    }

    /// `_carried`: the sessions a log row belongs to (its own, or for a merge the ones it carried).
    fn carried(&self) -> Done<Vec<Actor>> {
        let who = self.who()?;
        let leaves = if who.kind == "peer" { who.via } else { vec![who] };
        Ok(leaves.into_iter().filter(|item| item.session.is_some()).collect())
    }
}

fn log_rows(conn: &Connection, sql: &str, params: impl rusqlite::Params) -> Done<Vec<LogRow>> {
    let mut statement = conn.prepare(sql)?;
    let mut found = statement.query(params)?;
    let mut out = Vec::new();
    while let Some(row) = found.next()? {
        out.push(LogRow::read(row)?);
    }
    Ok(out)
}

// Sessions -------------------------------------------------------------------------------

struct Listed {
    session: String,
    actor: Value,
    start: Value,
    end: Value,
    last_seq: i64,
    transactions: HashSet<String>,
    device: Value,
}

/// `sessions.list_sessions` (`kind` None: every kind with sessions).
pub fn list_sessions(conn: &Connection, kind: Option<&str>, limit: i64) -> Done<Value> {
    if kind.is_some_and(|kind| !KINDS.contains(&kind)) {
        return Err(refuse(format!("Filter by one of: {}", KINDS.join(", "))));
    }
    if !(1..=MAX_SESSIONS).contains(&limit) {
        return Err(refuse(format!("Limit must be 1-{MAX_SESSIONS}")));
    }
    let listed = within(conn, false, || {
        let mut found: IndexMap<String, Listed> = IndexMap::new();
        let mut pages: HashMap<String, indexmap::IndexSet<String>> = HashMap::new();
        let rows = log_rows(conn, "SELECT seq,txn_id,page_id,at,device_id,actor,actor_name,actor_session,actor_via FROM op_log \
                                   WHERE actor_session IS NOT NULL OR actor_via IS NOT NULL ORDER BY seq", [])?;
        for row in &rows {
            for item in row.carried()? {
                if kind.is_some_and(|kind| item.kind != kind) {
                    continue;
                }
                let session = item.session.clone().ok_or_else(internal)?;
                let entry = found.entry(session.clone()).or_insert_with(|| Listed {
                    session: session.clone(), actor: item.as_dict(), start: row.at.clone(), end: row.at.clone(),
                    last_seq: row.seq, transactions: HashSet::new(),
                    device: if row.actor != "peer" { Value::Null } else { row.actor_name.clone().map_or(Value::Null, Value::String) },
                });
                entry.end = row.at.clone();
                entry.last_seq = row.seq;
                entry.transactions.insert(row.txn_id.clone());
                pages.entry(session).or_default().insert(row.page_id.clone());
            }
        }
        let mut listed: Vec<Listed> = found.into_values().collect();
        listed.sort_by(|a, b| b.last_seq.cmp(&a.last_seq));
        listed.truncate(usize::try_from(limit).map_err(|_| internal())?);
        let mut out = Vec::new();
        for entry in listed {
            let touched = &pages[&entry.session];
            let mut summaries = Vec::new();
            for page_id in touched.iter().take(20) {
                summaries.push(page_summary(conn, page_id)?);
            }
            let mut item = Map::new();
            item.insert("session".into(), entry.session.clone().into());
            item.insert("actor".into(), entry.actor);
            item.insert("start".into(), entry.start);
            item.insert("end".into(), entry.end);
            item.insert("last_seq".into(), entry.last_seq.into());
            item.insert("transactions".into(), entry.transactions.len().into());
            item.insert("device".into(), entry.device);
            item.insert("pages".into(), Value::Array(summaries));
            item.insert("page_count".into(), touched.len().into());
            out.push(Value::Object(item));
        }
        Ok(out)
    })?;
    Ok(json!({"sessions": listed}))
}

fn page_summary(conn: &Connection, page_id: &str) -> Done<Value> {
    let row = db::row(conn, "SELECT type, substr(text,1,200) AS title, json_extract(properties,'$.number') AS number, deleted_at \
                             FROM blocks WHERE id=?", [page_id])?;
    Ok(match row {
        None => json!({"page_id": page_id, "type": null, "title": null, "number": null, "deleted": true}),
        Some(row) => json!({"page_id": page_id, "type": row["type"], "title": row["title"], "number": row["number"],
                            "deleted": !row["deleted_at"].is_null()}),
    })
}

// Block states ---------------------------------------------------------------------------

fn fields(item: &Block) -> State {
    let mut state = Map::new();
    state.insert("place".into(), json!([item.page_id, item.parent_id, item.order_key]));
    state.insert("type".into(), item.kind.clone().into());
    state.insert("text".into(), item.text.clone().into());
    state.insert("live".into(), item.live().into());
    for (key, value) in &item.properties {
        if key != "revision" {
            state.insert(format!("props.{key}"), dumps(value, CANONICAL).into());
        }
    }
    state
}

fn differs(before: &State, after: &State) -> Vec<String> {
    let keys: BTreeSet<&String> = before.keys().chain(after.keys()).collect();
    keys.into_iter().filter(|key| before.get(*key) != after.get(*key)).cloned().collect()
}

fn head(field: &str) -> String {
    field.split('.').next().unwrap_or(field).to_owned()
}

struct Txn {
    seq: i64,
    /// block id -> (state before, state after) (None: absent from every page it touched).
    changes: IndexMap<String, (Option<State>, Option<State>)>,
}

impl Txn {
    fn note(&mut self, bid: &str, before: Option<&State>, after: Option<&State>) {
        let entry = self.changes.entry(bid.to_owned()).or_insert((None, None));
        if let Some(before) = before {
            entry.0 = Some(before.clone());
        }
        if let Some(after) = after {
            entry.1 = Some(after.clone());
        }
    }
}

/// `_session_rows`: the session's log rows, and merges that carried it together with others.
fn session_rows(conn: &Connection, session: &str) -> Done<(Vec<LogRow>, Vec<Value>)> {
    let pattern = format!("%\"session\":\"{session}\"%");
    let rows = log_rows(conn, "SELECT seq,txn_id,page_id,at,ops,actor,actor_name,actor_session,actor_via FROM op_log \
                               WHERE actor_session=? OR (actor='peer' AND actor_via LIKE ?) ORDER BY seq",
                        rusqlite::params![session, pattern])?;
    let (mut mine, mut mixed) = (Vec::new(), Vec::new());
    for row in rows {
        if row.actor_session.as_deref() == Some(session) {
            mine.push(row);
            continue;
        }
        let carried = row.who()?.via;
        if !carried.iter().any(|item| item.session.as_deref() == Some(session)) {
            continue;
        }
        if carried.iter().all(|item| item.session.as_deref() == Some(session)) {
            mine.push(row);
        } else {
            mixed.push(json!({"seq": row.seq, "page_id": row.page_id, "device": row.actor_name,
                              "reasons": ["merged from a paired device together with other people's changes"]}));
        }
    }
    Ok((mine, mixed))
}

/// `_history`: each session transaction with every block state it changed, before and after.
fn history(conn: &Connection, rows: &[LogRow]) -> Done<Vec<Txn>> {
    let mut txns: IndexMap<String, Txn> = IndexMap::new();
    let mut spans: IndexMap<String, (i64, i64)> = IndexMap::new();
    for row in rows {
        txns.entry(row.txn_id.clone()).or_insert_with(|| Txn { seq: row.seq, changes: IndexMap::new() });
        spans.entry(row.page_id.clone()).or_insert((row.seq, row.seq)).1 = row.seq;
    }
    let owned: HashMap<i64, &str> = rows.iter().map(|row| (row.seq, row.txn_id.as_str())).collect();
    let mut ignore = |_: &Value| -> Done<Option<String>> { Ok(None) };
    let titles: &mut Titles = &mut ignore;
    for (page_id, (first, last)) in &spans {
        let mut doc = replay(conn, page_id, Some(first - 1))?;
        let mut states: HashMap<String, State> = doc.blocks.iter().map(|(bid, item)| (bid.clone(), fields(item))).collect();
        let mut statement = conn.prepare("SELECT seq,at,ops FROM op_log WHERE page_id=? AND seq>=? AND seq<=? ORDER BY seq")?;
        let mut found = statement.query(rusqlite::params![page_id, first, last])?;
        while let Some(row) = found.next()? {
            let seq: i64 = row.get(0)?;
            let at: String = row.get(1)?;
            doc.changed.clear();
            doc.removed.clear();
            for op in logged_ops(row.get_ref(2)?)? {
                let Value::Object(op) = op else { return Err(refuse("Each operation must be an object")) };
                doc.apply(&op, &at, titles)?;
            }
            let txn = owned.get(&seq).copied();
            let touched: Vec<String> = doc.changed.iter().chain(doc.removed.iter()).cloned().collect();
            for bid in touched {
                let after = doc.blocks.get(&bid).map(fields);
                if let Some(txn) = txn {
                    let txn = txns.get_mut(txn).ok_or_else(internal)?;
                    txn.note(&bid, states.get(&bid), after.as_ref());
                }
                match after {
                    None => { states.remove(&bid); }
                    Some(after) => { states.insert(bid, after); }
                }
            }
        }
    }
    let mut out: Vec<Txn> = txns.into_values().collect();
    out.sort_by_key(|txn| txn.seq);
    Ok(out)
}

fn current_blocks(conn: &Connection, ids: &[String]) -> Done<IndexMap<String, Block>> {
    let mut found = IndexMap::new();
    for chunk in ids.chunks(500) {
        let marks = vec!["?"; chunk.len()].join(",");
        let mut statement = conn.prepare(&format!("SELECT * FROM blocks WHERE id IN ({marks})"))?;
        let mut rows = statement.query(params_from_iter(chunk.iter()))?;
        while let Some(row) = rows.next()? {
            let item = read_block(row)?;
            found.insert(item.id.clone(), item);
        }
    }
    Ok(found)
}

// The plan -------------------------------------------------------------------------------

fn props_of(state: &State) -> Done<Map<String, Value>> {
    let mut out = Map::new();
    for (key, value) in state {
        if let Some(name) = key.strip_prefix("props.") {
            out.insert(name.to_owned(), serde_json::from_str(value.as_str().ok_or_else(internal)?)?);
        }
    }
    Ok(out)
}

fn place(state: &State) -> Done<(Option<String>, Option<String>, Option<String>)> {
    let Some(Value::Array(parts)) = state.get("place") else { return Err(internal()) };
    let part = |index: usize| parts.get(index).and_then(Value::as_str).map(str::to_owned);
    Ok((part(0), part(1), part(2)))
}

fn live(state: &State) -> bool {
    state.get("live").and_then(Value::as_bool).unwrap_or(false)
}

fn snippet(item: Option<&Block>) -> String {
    slice(item.map_or("", |item| item.text.as_str()), 0, SNIPPET).to_owned()
}

fn op(pairs: Vec<(&str, Value)>) -> Map<String, Value> {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// A BlockError (caught by `_Plan.emit`), as opposed to a database or internal failure.
fn is_block_error(fault: &Fault) -> bool {
    fault.status < 500
}

struct Conflict {
    reasons: Vec<String>,
    fields: BTreeSet<String>,
}

struct Plan<'c> {
    conn: &'c Connection,
    current: IndexMap<String, Block>,
    now: HashMap<String, State>,
    wanted: HashMap<String, Option<State>>,
    conflicts: HashMap<String, Conflict>,
    touched: HashSet<String>,
    created: HashSet<String>,
    pages: IndexMap<String, PageDoc>,
    ops: Vec<Value>,
}

impl<'c> Plan<'c> {
    fn new(conn: &'c Connection, current: IndexMap<String, Block>) -> Plan<'c> {
        let now: HashMap<String, State> = current.iter().map(|(bid, item)| (bid.clone(), fields(item))).collect();
        let wanted = now.iter().map(|(bid, state)| (bid.clone(), Some(state.clone()))).collect();
        Plan {
            conn, current, now, wanted, conflicts: HashMap::new(), touched: HashSet::new(), created: HashSet::new(),
            pages: IndexMap::new(), ops: Vec::new(),
        }
    }

    fn wanted(&self, bid: &str) -> Option<&State> {
        self.wanted.get(bid).and_then(Option::as_ref)
    }

    fn conflict(&mut self, bid: &str, reason: &str, fields: &[String]) {
        let entry = self.conflicts.entry(bid.to_owned()).or_insert_with(|| Conflict { reasons: Vec::new(), fields: BTreeSet::new() });
        if !entry.reasons.iter().any(|known| known == reason) {
            entry.reasons.push(reason.to_owned());
        }
        entry.fields.extend(fields.iter().map(|field| head(field)));
    }

    fn revert(&mut self, txn: &Txn) {
        for (bid, (before, after)) in &txn.changes {
            let wanted = self.wanted(bid).cloned();
            let Some(before) = before else {
                let Some(after) = after else { continue };
                // The session created this block (here, or on the device it was merged from).
                let Some(wanted) = wanted else { continue }; // Purged since.
                let mut trashed = after.clone();
                trashed.insert("live".into(), false.into());
                if &wanted == after {
                    let mut gone = wanted;
                    gone.insert("live".into(), false.into());
                    self.wanted.insert(bid.clone(), Some(gone));
                    self.touched.insert(bid.clone());
                    self.created.insert(bid.clone());
                } else if wanted != trashed {
                    self.conflict(bid, "changed by someone else after the agent wrote it", &differs(after, &wanted));
                }
                continue;
            };
            let Some(after) = after else {
                if wanted.is_none() {
                    self.wanted.insert(bid.clone(), Some(before.clone()));
                    self.touched.insert(bid.clone());
                } else {
                    self.conflict(bid, "came back after the agent removed it", &[]);
                }
                continue;
            };
            if wanted.is_none() {
                self.conflict(bid, "was removed for good since", &[]);
                continue;
            }
            for field in differs(before, after) {
                let Some(Some(wanted)) = self.wanted.get_mut(bid) else { break };
                if wanted.get(&field) == after.get(&field) {
                    match before.get(&field) {
                        None => { wanted.shift_remove(&field); }
                        Some(value) => { wanted.insert(field.clone(), value.clone()); }
                    }
                    self.touched.insert(bid.clone());
                } else if wanted.get(&field) != before.get(&field) {
                    self.conflict(bid, "changed by someone else since", std::slice::from_ref(&field));
                }
            }
        }
    }

    // Operations against the current pages ---------------------------------------------

    fn page(&mut self, page_id: &str) -> Done<&mut PageDoc> {
        if !self.pages.contains_key(page_id) {
            let doc = load_page(self.conn, page_id)?;
            self.pages.insert(page_id.to_owned(), doc);
        }
        self.pages.get_mut(page_id).ok_or_else(internal)
    }

    fn block(&mut self, bid: Option<&str>) -> Done<Option<Block>> {
        let Some(bid) = bid else { return Ok(None) };
        for doc in self.pages.values() {
            if let Some(item) = doc.blocks.get(bid) {
                return Ok(Some(item.clone()));
            }
        }
        let page: Option<String> = self.conn.query_row("SELECT page_id FROM blocks WHERE id=?", [bid], |row| row.get(0)).optional()?;
        match page {
            Some(page_id) => Ok(self.page(&page_id)?.blocks.get(bid).cloned()),
            None => Ok(None),
        }
    }

    fn try_emit(&mut self, bid: &str, op: &Map<String, Value>) -> Done<()> {
        let item = self.block(Some(bid))?;
        let parent = match op.get("parent") {
            None | Some(Value::Null) => None,
            Some(Value::String(parent)) => self.block(Some(parent))?,
            Some(_) => return Err(internal()),
        };
        let kind = op.get("op").and_then(Value::as_str);
        if kind == Some("insert") {
            let parent = parent.ok_or_else(|| refuse("its parent is gone"))?;
            return self.page(&parent.page_id)?.apply_ordered(op, "undo");
        }
        let item = item.ok_or_else(|| refuse("it is gone"))?;
        match parent {
            Some(parent) if kind == Some("move") && parent.page_id != item.page_id => {
                let source = self.page(&item.page_id)?;
                let mut rows = vec![item.as_dict()];
                for child in source.descendants(bid) {
                    rows.push(source.blocks.get(&child).ok_or_else(internal)?.as_dict());
                }
                for row in &mut rows {
                    row.shift_remove("page_id");
                }
                source.apply_ordered(&op_release(bid), "undo")?;
                let adopt = json!({"op": "adopt", "parent": parent.id, "key": op.get("key").cloned().unwrap_or(Value::Null),
                                   "blocks": rows});
                let Value::Object(adopt) = adopt else { return Err(internal()) };
                self.page(&parent.page_id)?.apply_ordered(&adopt, "undo")
            }
            _ => self.page(&item.page_id)?.apply_ordered(op, "undo"),
        }
    }

    /// `_Plan.emit`: apply `op` to the planned pages and keep it; one that cannot apply is a conflict.
    fn emit(&mut self, bid: &str, op: Map<String, Value>) -> Done<bool> {
        match self.try_emit(bid, &op) {
            Ok(()) => {
                self.ops.push(Value::Object(op));
                Ok(true)
            }
            Err(fault) if is_block_error(&fault) => {
                self.conflict(bid, &format!("could not be put back: {}", fault.message), &[]);
                Ok(false)
            }
            Err(fault) => Err(fault),
        }
    }

    fn state(&self, bid: &str) -> Option<&State> {
        self.wanted(bid).or_else(|| self.now.get(bid))
    }

    fn depth(&self, bid: &str) -> usize {
        let (mut level, mut seen) = (0, HashSet::new());
        let mut bid = bid.to_owned();
        let mut state = self.state(&bid);
        while let Some(current) = state {
            let Some(parent) = current.get("place").and_then(|place| place.get(1)).and_then(Value::as_str) else { break };
            if !seen.insert(bid.clone()) {
                break;
            }
            bid = parent.to_owned();
            level += 1;
            state = self.state(&bid);
        }
        level
    }

    fn build(&mut self) -> Done<()> {
        let mut ordered: Vec<(usize, String)> = self.touched.iter()
            .filter(|bid| self.wanted(bid) != self.now.get(*bid))
            .map(|bid| (self.depth(bid), bid.clone())).collect();
        ordered.sort();
        let ordered: Vec<String> = ordered.into_iter().map(|(_, bid)| bid).collect();
        for bid in &ordered {
            // Removed for good by the session: inserted again, parents first.
            let Some(state) = self.wanted(bid).cloned() else { continue };
            if self.now.contains_key(bid) {
                continue;
            }
            let (_, parent, key) = place(&state)?;
            let insert = op(vec![
                ("op", "insert".into()), ("id", bid.clone().into()), ("type", state["type"].clone()),
                ("parent", parent.map_or(Value::Null, Value::String)), ("key", key.map_or(Value::Null, Value::String)),
                ("props", Value::Object(props_of(&state)?)), ("text", state["text"].clone()),
            ]);
            self.emit(bid, insert)?;
        }
        for bid in &ordered {
            // Back from Trash, parents first.
            let item = self.block(Some(bid))?;
            let wanted_live = self.wanted(bid).map(live);
            if let (Some(item), Some(wanted_live)) = (item, wanted_live) {
                if wanted_live && !item.live() {
                    self.emit(bid, op(vec![("op", "restore".into()), ("id", bid.clone().into())]))?;
                }
            }
        }
        for bid in &ordered {
            // Back to the old place.
            let item = self.block(Some(bid))?;
            let (Some(item), Some(state)) = (item, self.wanted(bid).cloned()) else { continue };
            if item.parent_id.is_none() {
                continue;
            }
            let (page_id, parent, key) = place(&state)?;
            if (Some(&item.page_id), item.parent_id.as_ref(), Some(&item.order_key)) != (page_id.as_ref(), parent.as_ref(), key.as_ref()) {
                let target = self.block(parent.as_deref())?;
                if target.is_none_or(|target| !target.live()) {
                    self.conflict(bid, "its old place is gone", &["place".to_owned()]);
                    continue;
                }
                self.emit(bid, op(vec![
                    ("op", "move".into()), ("id", bid.clone().into()), ("parent", parent.map_or(Value::Null, Value::String)),
                    ("key", key.map_or(Value::Null, Value::String)),
                ]))?;
            }
        }
        for bid in &ordered {
            // Type, properties and text.
            let item = self.block(Some(bid))?;
            let (Some(item), Some(state)) = (item, self.wanted(bid).cloned()) else { continue };
            let now = fields(&item);
            let mut keys: indexmap::IndexSet<&String> = now.keys().collect();
            keys.extend(state.keys());
            let mut props = Map::new();
            for key in keys {
                let Some(name) = key.strip_prefix("props.") else { continue };
                if now.get(key) == state.get(key) {
                    continue;
                }
                let value = match state.get(key) {
                    Some(value) => serde_json::from_str(value.as_str().ok_or_else(internal)?)?,
                    None => Value::Null,
                };
                props.insert(name.to_owned(), value);
            }
            if !props.is_empty() || now["type"] != state["type"] {
                let mut set = op(vec![("op", "set".into()), ("id", bid.clone().into()), ("props", Value::Object(props))]);
                if now["type"] != state["type"] {
                    set.insert("type".into(), state["type"].clone());
                }
                self.emit(bid, set)?;
            }
            if now["text"] != state["text"] {
                self.emit(bid, op(vec![("op", "text".into()), ("id", bid.clone().into()), ("text", state["text"].clone())]))?;
            }
        }
        for bid in &ordered {
            // To Trash, parents first; never with someone else's blocks inside.
            let item = self.block(Some(bid))?;
            let (Some(item), Some(wanted_live)) = (item, self.wanted(bid).map(live)) else { continue };
            if wanted_live || !item.live() {
                continue;
            }
            let page = self.page(&item.page_id)?;
            let inside: Vec<(String, bool)> = page.descendants(bid).into_iter()
                .filter_map(|child| page.blocks.get(&child).map(|block| (child, block.live()))).collect();
            let kept = inside.iter().any(|(child, alive)| *alive && self.wanted(child).is_none_or(live));
            if kept {
                self.conflict(bid, "holds blocks someone else added or changed since", &[]);
                continue;
            }
            self.emit(bid, op(vec![("op", "delete".into()), ("id", bid.clone().into())]))?;
        }
        Ok(())
    }

    /// `_Plan.outcome`: every block the planned operations change.
    fn outcome(&mut self) -> Done<Vec<Value>> {
        let mut touched: Vec<String> = self.touched.iter().cloned().collect();
        touched.sort();
        let mut found = Vec::new();
        for bid in touched {
            let item = self.block(Some(&bid))?;
            let before = self.now.get(&bid);
            let after = item.as_ref().map(fields);
            if after.as_ref() == before {
                continue;
            }
            let changed: Vec<String> = if self.created.contains(&bid) && after.as_ref().is_some_and(|after| !live(after)) {
                vec!["created".to_owned()]
            } else {
                let empty = Map::new();
                let heads: BTreeSet<String> = differs(before.unwrap_or(&empty), after.as_ref().unwrap_or(&empty)).iter().map(|field| head(field)).collect();
                heads.into_iter().collect()
            };
            let original = self.current.get(&bid);
            let shown = item.as_ref().or(original);
            found.push(json!({
                "id": bid,
                "page_id": shown.map(|item| item.page_id.clone()),
                "current": snippet(original),
                "text": snippet(shown),
                "live": after.as_ref().is_some_and(live),
                "fields": changed,
            }));
        }
        Ok(found)
    }
}

fn op_release(bid: &str) -> Map<String, Value> {
    op(vec![("op", "release".into()), ("id", bid.into())])
}

/// `sessions.undo_session`: revert everything one agent session did, as one new transaction.
/// The undoer is the person at this device (`Actor::user()`, never `<session>:undo`).
pub fn undo_session(conn: &Connection, session: &Value, dry_run: &Value) -> Done<Value> {
    let session = session_of(session)?;
    let Some(dry_run) = dry_run.as_bool() else { return Err(refuse("dry_run is true or false")) };
    within(conn, true, || {
        let (rows, mixed) = session_rows(conn, &session)?;
        if rows.is_empty() && mixed.is_empty() {
            return Err(fail(404, "No kept history belongs to that session"));
        }
        let mut who = None;
        'rows: for row in &rows {
            for item in row.carried()? {
                if item.session.as_deref() == Some(session.as_str()) {
                    who = Some(item);
                    break 'rows;
                }
            }
        }
        let txns = history(conn, &rows)?;
        let ids: BTreeSet<String> = txns.iter().flat_map(|txn| txn.changes.keys().cloned()).collect();
        let ids: Vec<String> = ids.into_iter().collect();
        let mut plan = Plan::new(conn, current_blocks(conn, &ids)?);
        for txn in txns.iter().rev() {
            plan.revert(txn);
        }
        plan.build()?;
        let reverted = plan.outcome()?;
        let mut sorted: Vec<(&String, &Conflict)> = plan.conflicts.iter().collect();
        sorted.sort_by(|a, b| a.0.cmp(b.0));
        let mut conflicts: Vec<Value> = sorted.into_iter().map(|(bid, entry)| json!({
            "id": bid,
            "reasons": entry.reasons,
            "fields": entry.fields.iter().collect::<Vec<_>>(),
            "page_id": plan.current.get(bid).map(|item| item.page_id.clone()),
            "text": snippet(plan.current.get(bid)),
        })).collect();
        conflicts.extend(mixed);
        let mut report = Map::new();
        report.insert("session".into(), session.clone().into());
        report.insert("actor".into(), who.as_ref().map_or(Value::Null, Actor::as_dict));
        report.insert("transactions".into(), txns.len().into());
        report.insert("dry_run".into(), dry_run.into());
        report.insert("applied".into(), false.into());
        report.insert("reverted_count".into(), reverted.len().into());
        report.insert("conflict_count".into(), conflicts.len().into());
        report.insert("reverted".into(), Value::Array(reverted.into_iter().take(MAX_LISTED).collect()));
        report.insert("conflicts".into(), Value::Array(conflicts.into_iter().take(MAX_LISTED).collect()));
        if dry_run || plan.ops.is_empty() {
            return Ok(Value::Object(report));
        }
        let ops = Value::Array(std::mem::take(&mut plan.ops));
        let result = transact(conn, &ops, None, None, false, &Actor::user())?;
        report.insert("applied".into(), true.into());
        for key in ["txn_id", "seq", "revisions"] {
            report.insert(key.into(), result.get(key).cloned().ok_or_else(internal)?);
        }
        Ok(Value::Object(report))
    })
}
