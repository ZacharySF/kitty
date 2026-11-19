//! One window's socket (`web_live._serve` and `_handle` in Python): its hello, every push
//! while it is open, and what it sends: block transactions (applied by the front's block
//! engine), resume after a reconnect and its page subscription.

use std::collections::HashSet;
use std::sync::Arc;

use bytes::Bytes;
use serde_json::{json, Value};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::mpsc::UnboundedReceiver;

use super::changes::{self, BATCH};
use super::socket::{self, Closed, Reader};
use super::{Hub, Push};

/// What ends a window's stream: a close frame to answer with, or a connection already gone.
enum End {
    Close(u16),
    Gone,
}

impl From<std::io::Error> for End {
    fn from(_: std::io::Error) -> Self {
        End::Gone
    }
}

impl From<Closed> for End {
    fn from(Closed(code): Closed) -> Self {
        End::Close(code)
    }
}

pub struct Window {
    pub id: u64,
    /// The last sequence this window had when it connected (its hello).
    pub seq: i64,
    pub device_id: String,
    pub pushes: UnboundedReceiver<Push>,
}

async fn send<S: AsyncWrite + Unpin>(io: &mut S, text: &str) -> std::io::Result<()> {
    io.write_all(&socket::frame(socket::TEXT, text.as_bytes())).await
}

pub async fn serve<S: AsyncRead + AsyncWrite + Unpin>(hub: Arc<Hub>, mut window: Window, mut io: S, leftover: Bytes) {
    let mut pages: Option<HashSet<String>> = None;
    let end = async {
        let hello = json!({ "type": "hello", "seq": window.seq, "device_id": window.device_id, "build": hub.build_revision() });
        send(&mut io, &hello.to_string()).await?;
        let mut reader = Reader::server();
        reader.feed(&leftover);
        let mut buffer = vec![0u8; 65536];
        loop {
            while let Some((opcode, payload)) = reader.next()? {
                match opcode {
                    socket::TEXT => handle(&hub, &window, &mut pages, &mut io, &payload).await?,
                    socket::PING => io.write_all(&socket::frame(socket::PONG, &payload)).await?,
                    socket::CLOSE => return Err(End::Close(socket::close_code(&payload))),
                    socket::BINARY => return Err(End::Close(1003)),
                    _ => {}
                }
            }
            tokio::select! {
                push = window.pushes.recv() => match push {
                    Some(Push::Message(text)) => send(&mut io, &text).await?,
                    Some(Push::Change(change)) => {
                        if pages.as_ref().is_none_or(|pages| pages.contains(&change.page_id)) {
                            send(&mut io, &change.text).await?;
                        }
                    }
                    None => return Err(End::Close(1001)),
                },
                read = io.read(&mut buffer) => match read? {
                    0 => return Err(End::Gone),
                    read => reader.feed(&buffer[..read]),
                },
            }
        }
    };
    let end: Result<std::convert::Infallible, End> = end.await;
    if let Err(End::Close(code)) = end {
        let _ = io.write_all(&socket::close(code)).await;
    }
    hub.unsubscribe(window.id);
}

/// `web_live._handle`: one text message from the window.
async fn handle<S: AsyncWrite + Unpin>(hub: &Arc<Hub>, window: &Window, pages: &mut Option<HashSet<String>>, io: &mut S, payload: &[u8]) -> Result<(), End> {
    let message: Value = serde_json::from_slice(payload).map_err(|_| End::Close(1007))?;
    match message.get("type").and_then(Value::as_str) {
        Some("txn") => send(io, &transact(hub, message).await.to_string()).await?,
        Some("resume") => {
            for text in resume(hub, window, message.get("after")).await {
                send(io, &text).await?;
            }
        }
        Some("subscribe") => match message.get("pages") {
            None | Some(Value::Null) => *pages = None,
            Some(Value::Array(items)) if items.len() <= 1000 && items.iter().all(Value::is_string) => {
                *pages = Some(items.iter().filter_map(Value::as_str).map(str::to_owned).collect());
            }
            Some(_) => send(io, &json!({ "type": "error", "error": "Subscribe to a list of page IDs or null for all" }).to_string()).await?,
        },
        _ => send(io, &json!({ "type": "error", "error": "Unknown message type" }).to_string()).await?,
    }
    Ok(())
}

/// `Live.transact`: the front's block engine applies the transaction on the workspace
/// connection the ported routes share and answers the ack or reject. The committed change is
/// fanned out before the ack (Python's worker answered over HTTP while the poller caught up),
/// so a window opened after the ack starts from this transaction.
async fn transact(hub: &Arc<Hub>, message: Value) -> Value {
    let (db, modules, hub) = (hub.db_path.clone(), hub.modules.clone(), hub.clone());
    let applied = tokio::task::spawn_blocking(move || {
        let answer = crate::ported::db::with(&db, |conn| {
            let answer = crate::blocks::live::transact(conn, &modules, &message);
            if answer.get("type").and_then(Value::as_str) == Some("ack") {
                // A failed read leaves the change to the poller.
                let _ = hub.advance(conn);
            }
            Ok(answer)
        });
        answer.unwrap_or_else(|_| crate::blocks::live::busy(&message))
    }).await;
    applied.unwrap_or_else(|_| json!({ "type": "reject", "txn_id": Value::Null, "status": 503, "error": crate::blocks::live::BUSY }))
}

/// `Live.resume`: the changes a reconnecting window missed, up to its hello, or a reset when
/// history before its place was compacted.
async fn resume(hub: &Hub, window: &Window, after: Option<&Value>) -> Vec<String> {
    let after = match after.and_then(Value::as_i64) {
        Some(after) if after >= 0 => after,
        _ => return vec![json!({ "type": "error", "error": "Resume needs the last sequence number seen" }).to_string()],
    };
    let upto = window.seq;
    let db = hub.db_path.clone();
    let read = tokio::task::spawn_blocking(move || -> rusqlite::Result<Vec<String>> {
        let conn = super::open(&db)?;
        let horizon = changes::horizon(&conn)?;
        if after < horizon {
            return Ok(vec![json!({ "type": "reset", "horizon": horizon }).to_string()]);
        }
        let (mut missed, mut cursor) = (Vec::new(), after);
        loop {
            let batch = changes::read(&conn, cursor, Some(upto), BATCH)?;
            let Some(last) = batch.last() else { break };
            cursor = last.seq;
            missed.extend(batch.into_iter().map(|change| change.text));
        }
        Ok(missed)
    });
    match read.await {
        Ok(Ok(messages)) => messages,
        // Python's socket thread ends on an error it does not expect; a busy database is the
        // one a window can retry.
        _ => vec![json!({ "type": "error", "error": "Local workspace is busy; resume again" }).to_string()],
    }
}
