//! The front's link to the worker for the live channel: its event stream,
//! `GET /api/internal/live/events` (a WebSocket upgrade on the worker, kitty.web_live): a
//! binary frame `{"type": "nudge"}` after every mutation the worker committed (push now, not
//! at the next poll), and each text frame one message another Python extension pushes to
//! every window, forwarded verbatim.

use std::sync::Arc;
use std::time::Duration;

use base64::Engine;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

use super::socket::{self, Reader};
use super::Hub;
use crate::auth;

pub const EVENTS_PATH: &str = "/api/internal/live/events";
/// How long the front waits before opening the stream again after it dropped.
const RECONNECT: Duration = Duration::from_millis(500);

/// Keep the worker's event stream open for the front's whole life.
pub async fn follow(hub: Arc<Hub>) {
    loop {
        let _ = stream(&hub).await;
        tokio::time::sleep(RECONNECT).await;
    }
}

async fn stream(hub: &Hub) -> std::io::Result<()> {
    let broken = || std::io::Error::other("the worker refused its event stream");
    let backend = &hub.backend;
    let mut connection = TcpStream::connect(&backend.authority).await?;
    connection.set_nodelay(true)?;
    let mut nonce = [0u8; 16];
    getrandom::fill(&mut nonce).map_err(|_| broken())?;
    let key = base64::engine::general_purpose::STANDARD.encode(nonce);
    let request = format!(
        "GET {EVENTS_PATH} HTTP/1.1\r\nHost: {host}\r\nOrigin: {origin}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
         Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: {protocol}, {prefix}{secret}\r\n\
         X-Kitty-Front: {front}\r\n\r\n",
        host = backend.authority, origin = backend.origin, protocol = auth::PROTOCOL, prefix = auth::PROTOCOL_PREFIX,
        secret = backend.secret, front = backend.front_secret,
    );
    connection.write_all(request.as_bytes()).await?;
    let mut head = Vec::new();
    let mut buffer = [0u8; 65536];
    let rest = loop {
        let read = connection.read(&mut buffer).await?;
        if read == 0 {
            return Err(broken());
        }
        head.extend_from_slice(&buffer[..read]);
        if let Some(end) = head.windows(4).position(|window| window == b"\r\n\r\n") {
            break head.split_off(end + 4);
        }
        if head.len() > 16384 {
            return Err(broken());
        }
    };
    let text = String::from_utf8_lossy(&head);
    let expected = format!("sec-websocket-accept: {}", socket::accept_key(&key)).to_ascii_lowercase();
    if !text.starts_with("HTTP/1.1 101") || !text.lines().any(|line| line.trim().to_ascii_lowercase() == expected) {
        return Err(broken());
    }
    let mut reader = Reader::client();
    reader.feed(&rest);
    loop {
        while let Some((opcode, payload)) = reader.next().map_err(|_| broken())? {
            match opcode {
                socket::TEXT => hub.broadcast_text(String::from_utf8(payload).map_err(|_| broken())?),
                socket::BINARY => hub.event(&payload),
                socket::PING => connection.write_all(&socket::client_frame(socket::PONG, &payload)).await?,
                socket::CLOSE => return Ok(()),
                _ => {}
            }
        }
        let read = connection.read(&mut buffer).await?;
        if read == 0 {
            return Ok(());
        }
        reader.feed(&buffer[..read]);
    }
}
