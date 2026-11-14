//! The live channel: live push to every window over one same-origin WebSocket per window
//! (`web_live` in Python before it moved here).
//!
//! ```text
//! POST /api/live/session  {}  ->  {"token", "socket"}
//!     Header-protected like every mutation, so only rika!'s own page gets a token.
//! GET  /api/live/socket?token=<token>   (WebSocket upgrade)
//!     Exact Origin, the UI secret as a subprotocol and a single-use token, as for the terminal.
//! ```
//!
//! The channel belongs to the always-on core: block changes need the workspace module, but
//! the active build is pushed to every window in every workspace.
//!
//! Every window receives `{"type": "hello", "seq", "device_id", "build"}` on connect (`build`
//! is the active build's revision, so a window that loaded an older one fetches
//! `GET /api/build` again), then, whoever wrote them:
//!
//! - one `{"type": "change", "seq", "txn_id", "page_id", "at", "device_id", "actor", "root",
//!   "ops"}` per committed `op_log` row: a window's transaction, the Notes HTTP API, a
//!   background import or another process (an agent's MCP or CLI writes too). `actor` is who
//!   made it (kitty.core.blocks.actor) and `root` is `{"type", "number"}` of the page the
//!   change belongs to. One thread polls `op_log` (every 0.1 s, and at once when the worker
//!   says a mutation committed); it starts with the first window;
//! - every other message the front or the worker pushes to every window: `{"type": "build",
//!   ...}` whenever the build file changes, the page mirror's frames (kitty.web_mirror).
//!
//! A window sends:
//!
//! - `{"type": "txn", "txn_id"?, "ops": [...], "base"?: {page_id: revision}}`, applied by the
//!   worker's block store (`POST /api/internal/live/txn`, kitty.web_live), which answers the
//!   sender `{"type": "ack", ...}` or `{"type": "reject", "txn_id", "status", "error"}`;
//! - `{"type": "resume", "after": seq}`: replays the changes it missed while away, or
//!   `{"type": "reset", "horizon"}` when history before the horizon was compacted;
//! - `{"type": "subscribe", "pages": [page_id, ...] | null}`: limits live block changes
//!   (every other message always arrives).
//!
//! The worker reaches the windows through its event stream (crate::live::worker). The front
//! never forwards a page's request for `/api/internal/...`, the worker's routes for the front.

mod changes;
mod http;
mod socket;
mod window;
mod worker;

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::time::{Duration, Instant};

use bytes::Bytes;
use hyper::body::Incoming;
use hyper::header::HeaderValue;
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use tokio::net::TcpStream;
use tokio::sync::mpsc::{unbounded_channel, UnboundedSender};

use crate::front::{Backend, Body, Front};
use crate::target::Target;
use changes::{Change, BATCH};
use http::{failure, refuse, Refusal};

pub const SESSION_PATH: &str = "/api/live/session";
pub const SOCKET_PATH: &str = "/api/live/socket";
const INTERNAL: &str = "/api/internal";
/// `web_live.POLL_SECONDS`.
const POLL: Duration = Duration::from_millis(100);
/// `web_socket.TOKEN_TTL`.
const TOKEN_TTL: Duration = Duration::from_secs(30);

static HUB: OnceLock<Arc<Hub>> = OnceLock::new();

/// What a window's socket sends besides its own answers.
pub enum Push {
    Change(Arc<Change>),
    Message(Arc<str>),
}

struct State {
    /// The newest sequence pushed to every window.
    seq: i64,
    device_id: String,
    started: bool,
    next: u64,
    windows: HashMap<u64, UnboundedSender<Push>>,
}

/// Tokens for the socket, the open windows and the poller fanning committed changes out.
pub struct Hub {
    db_path: PathBuf,
    backend: Backend,
    tokens: Mutex<HashMap<String, Instant>>,
    state: Mutex<State>,
    wake: (Mutex<bool>, Condvar),
}

/// Open the workspace database as the front's other readers do.
fn open(path: &Path) -> rusqlite::Result<Connection> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    conn.busy_timeout(Duration::from_millis(5000))?;
    Ok(conn)
}

/// Start the live channel of this front: follow the worker's event stream.
pub fn start(front: &Front, db_path: PathBuf) {
    let hub = Arc::new(Hub {
        db_path,
        backend: front.backend.clone(),
        tokens: Mutex::new(HashMap::new()),
        state: Mutex::new(State { seq: 0, device_id: String::new(), started: false, next: 0, windows: HashMap::new() }),
        wake: (Mutex::new(false), Condvar::new()),
    });
    if HUB.set(hub.clone()).is_ok() {
        tokio::spawn(worker::follow(hub));
    }
}

/// Send one already-serialized JSON message (not a block change) to every open window now.
pub fn broadcast_text(message: String) {
    if let Some(hub) = HUB.get() {
        hub.broadcast_text(message);
    }
}

impl Hub {
    fn broadcast_text(&self, message: String) {
        let message: Arc<str> = message.into();
        let mut state = self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        state.windows.retain(|_, window| window.send(Push::Message(message.clone())).is_ok());
    }

    /// A mutation committed: push it now rather than at the next poll.
    fn nudge(&self) {
        *self.wake.0.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = true;
        self.wake.1.notify_one();
    }

    /// One event from the worker's stream.
    fn event(&self, payload: &[u8]) {
        let Ok(event) = serde_json::from_slice::<Value>(payload) else { return };
        if event.get("type").and_then(Value::as_str) == Some("nudge") {
            self.nudge();
            // A Settings screen of the worker may have written the build file (`web_build.mutated`).
            tokio::task::spawn_blocking(crate::builds::service::refresh);
        }
    }

    /// The active build's revision every window's hello carries (crate::builds::service).
    fn build_revision(&self) -> Option<String> {
        crate::builds::service::active_revision()
    }

    fn issue(&self) -> String {
        let token = crate::auth::token_hex(32);
        let now = Instant::now();
        let mut tokens = self.tokens.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        tokens.retain(|_, expiry| *expiry > now);
        tokens.insert(token.clone(), now + TOKEN_TTL);
        token
    }

    fn consume(&self, token: &str) -> bool {
        let expiry = self.tokens.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).remove(token);
        expiry.is_some_and(|expiry| expiry > Instant::now())
    }

    /// A new window: its hello's sequence, and from then on every change after it.
    fn subscribe(self: &Arc<Self>) -> rusqlite::Result<window::Window> {
        let mut state = self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if !state.started {
            let (seq, device_id) = changes::position(&open(&self.db_path)?)?;
            state.seq = seq;
            state.device_id = device_id;
            state.started = true;
            let hub = self.clone();
            std::thread::Builder::new().name("rika-live".into()).spawn(move || hub.poll()).expect("a thread for the live channel");
        }
        let (sender, pushes) = unbounded_channel();
        let id = state.next;
        state.next += 1;
        state.windows.insert(id, sender);
        Ok(window::Window { id, seq: state.seq, device_id: state.device_id.clone(), pushes })
    }

    fn unsubscribe(&self, id: u64) {
        self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).windows.remove(&id);
    }

    /// `Live._run`: fan committed changes out to every window.
    fn poll(&self) {
        let mut conn: Option<Connection> = None;
        loop {
            {
                let woken = self.wake.0.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                let (mut woken, _) = self.wake.1.wait_timeout_while(woken, POLL, |woken| !*woken).unwrap_or_else(|poisoned| poisoned.into_inner());
                *woken = false;
            }
            if conn.is_none() {
                conn = open(&self.db_path).ok();
            }
            let Some(db) = conn.as_ref() else { continue };
            let mut after = self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).seq;
            loop {
                let batch = match changes::read(db, after, None, BATCH) {
                    Ok(batch) => batch,
                    // Busy or locked: the next poll retries (a broken connection is reopened).
                    Err(_) => {
                        conn = None;
                        break;
                    }
                };
                let Some(last) = batch.last().map(|change| change.seq) else { break };
                let full = batch.len() == BATCH;
                let mut state = self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                state.seq = last;
                for change in batch.into_iter().map(Arc::new) {
                    state.windows.retain(|_, window| window.send(Push::Change(change.clone())).is_ok());
                }
                drop(state);
                after = last;
                if !full {
                    break;
                }
            }
        }
    }
}

fn is_internal(path: &str) -> bool {
    path == INTERNAL || path.starts_with(&format!("{INTERNAL}/"))
}

/// The live channel's routes, and the refusal of every worker-internal route to pages.
pub async fn route(front: &Front, target: &Target, _forward_target: &str, request: &mut Request<Incoming>, mutation: bool, upgrade: bool) -> Option<Response<Body>> {
    let head = request.method() == Method::HEAD;
    if is_internal(&target.path) {
        return Some(failure(refuse(404, "API route not found"), head));
    }
    let hub = HUB.get()?;
    let answer = match (target.path.as_str(), mutation, upgrade) {
        (SESSION_PATH, true, _) => session(hub, target, request).await,
        (SOCKET_PATH, false, false) => Err(refuse(426, "Open the live channel with a WebSocket upgrade")),
        (SOCKET_PATH, false, true) => socket(front, hub, target, request),
        _ => return None,
    };
    Some(answer.unwrap_or_else(|refusal| failure(refusal, head)))
}

async fn session(hub: &Hub, target: &Target, request: &mut Request<Incoming>) -> Result<Response<Body>, Refusal> {
    if !target.query.is_empty() {
        return Err(refuse(400, "This route does not accept query parameters"));
    }
    let headers = request.headers().clone();
    if !http::body(&headers, request.body_mut()).await?.is_empty() {
        return Err(refuse(400, "A live session request expects an empty object"));
    }
    Ok(http::send_json(&json!({ "token": hub.issue(), "socket": SOCKET_PATH }), false))
}

/// The upgrade, checked as `RequestHandler._upgrade` and `web_live.upgrade` check it.
fn socket(front: &Front, hub: &Arc<Hub>, target: &Target, request: &mut Request<Incoming>) -> Result<Response<Body>, Refusal> {
    let headers = request.headers();
    if http::all(headers, "origin") != [front.origin.as_str()] {
        return Err(refuse(403, "Cross-origin requests are not allowed"));
    }
    if !front.auth.protocol_secret(http::all(headers, "sec-websocket-protocol")) {
        return Err(refuse(401, crate::auth::REFUSAL));
    }
    if target.query.iter().any(|(name, _)| name != "token") {
        return Err(refuse(400, "Unexpected live socket parameters"));
    }
    let connection = headers.get("connection").and_then(|value| value.to_str().ok()).unwrap_or("");
    let is_upgrade = headers.get("upgrade").and_then(|value| value.to_str().ok()).is_some_and(|value| value.eq_ignore_ascii_case("websocket"))
        && connection.split(',').any(|part| part.trim().eq_ignore_ascii_case("upgrade"))
        && http::all(headers, "sec-websocket-version") == ["13"];
    if !is_upgrade {
        return Err(refuse(400, "Expected a WebSocket version 13 upgrade"));
    }
    let keys = http::all(headers, "sec-websocket-key");
    if keys.len() != 1 || !socket::valid_key(keys[0]) {
        return Err(refuse(400, "Invalid Sec-WebSocket-Key"));
    }
    let accept = socket::accept_key(keys[0]);
    if !hub.consume(target.get("token").unwrap_or("")) {
        return Err(refuse(403, "Live token is missing, expired or already used"));
    }
    let window = hub.subscribe().map_err(|_| refuse(503, "Local workspace is busy or unavailable; try again"))?;
    let upgraded = hyper::upgrade::on(&mut *request);
    let hub = hub.clone();
    tokio::spawn(async move {
        let Ok(upgraded) = upgraded.await else {
            hub.unsubscribe(window.id);
            return;
        };
        // Acks and pushes are small and back to back; Nagle's algorithm would hold one behind the other.
        match upgraded.downcast::<TokioIo<TcpStream>>() {
            Ok(parts) => {
                let stream = parts.io.into_inner();
                let _ = stream.set_nodelay(true);
                window::serve(hub, window, stream, parts.read_buf).await;
            }
            Err(upgraded) => window::serve(hub, window, TokioIo::new(upgraded), Bytes::new()).await,
        }
    });
    let mut response = Response::new(http::empty());
    *response.status_mut() = StatusCode::SWITCHING_PROTOCOLS;
    let out = response.headers_mut();
    out.insert("upgrade", HeaderValue::from_static("websocket"));
    out.insert("connection", HeaderValue::from_static("Upgrade"));
    out.insert("sec-websocket-accept", HeaderValue::from_str(&accept).expect("base64 is a valid header"));
    out.insert("sec-websocket-protocol", HeaderValue::from_static(crate::auth::PROTOCOL));
    out.insert("server", HeaderValue::from_static("rika!"));
    Ok(response)
}
