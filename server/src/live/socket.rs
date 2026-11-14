//! The RFC 6455 framing rika!'s live channel needs (`kitty.web_socket` in Python): the server
//! side for every window (masked client frames in, unmasked frames out, the same refusals and
//! close codes) and the client side for the front's own stream from the worker.

use base64::Engine;
use sha1::{Digest, Sha1};

const GUID: &[u8] = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
/// `web_socket.MAX_MESSAGE`: the largest message (and frame) a window may send.
pub const MAX_MESSAGE: usize = 1024 * 1024;

pub const TEXT: u8 = 1;
pub const BINARY: u8 = 2;
pub const CLOSE: u8 = 8;
pub const PING: u8 = 9;
pub const PONG: u8 = 10;

/// `Sec-WebSocket-Accept` for a client's key.
pub fn accept_key(key: &str) -> String {
    let mut hash = Sha1::new();
    hash.update(key.as_bytes());
    hash.update(GUID);
    base64::engine::general_purpose::STANDARD.encode(hash.finalize())
}

/// A key the client may send: 16 random bytes in canonical base64 (`base64.b64decode(validate=True)`).
pub fn valid_key(key: &str) -> bool {
    base64::engine::general_purpose::STANDARD.decode(key).is_ok_and(|bytes| bytes.len() == 16)
}

/// One frame as the server sends it: final, unmasked.
pub fn frame(opcode: u8, payload: &[u8]) -> Vec<u8> {
    let length = payload.len();
    let mut out = Vec::with_capacity(length + 10);
    out.push(0x80 | opcode);
    if length < 126 {
        out.push(length as u8);
    } else if length < 65536 {
        out.push(126);
        out.extend_from_slice(&(length as u16).to_be_bytes());
    } else {
        out.push(127);
        out.extend_from_slice(&(length as u64).to_be_bytes());
    }
    out.extend_from_slice(payload);
    out
}

/// One frame as a client sends it: final and masked (the front's frames to the worker).
pub fn client_frame(opcode: u8, payload: &[u8]) -> Vec<u8> {
    let mut mask = [0u8; 4];
    getrandom::fill(&mut mask).expect("the system's random source");
    let length = payload.len();
    let mut out = Vec::with_capacity(length + 14);
    out.push(0x80 | opcode);
    if length < 126 {
        out.push(0x80 | length as u8);
    } else if length < 65536 {
        out.push(0x80 | 126);
        out.extend_from_slice(&(length as u16).to_be_bytes());
    } else {
        out.push(0x80 | 127);
        out.extend_from_slice(&(length as u64).to_be_bytes());
    }
    out.extend_from_slice(&mask);
    out.extend(payload.iter().enumerate().map(|(i, byte)| byte ^ mask[i % 4]));
    out
}

/// The close frame that ends a stream with `code`.
pub fn close(code: u16) -> Vec<u8> {
    frame(CLOSE, &code.to_be_bytes())
}

/// `web_socket.close_code`.
pub fn close_code(payload: &[u8]) -> u16 {
    if payload.len() >= 2 { u16::from_be_bytes([payload[0], payload[1]]) } else { 1000 }
}

/// Why the peer's frames end the stream: the close code the server answers with.
#[derive(Debug, PartialEq, Eq)]
pub struct Closed(pub u16);

/// `web_socket.Reader`: an incremental parser of one side's frames. A server reads masked
/// client frames (anything unmasked is a protocol error); the front's client of the worker's
/// stream reads unmasked ones.
pub struct Reader {
    buffer: Vec<u8>,
    fragments: Vec<u8>,
    fragmented: bool,
    kind: u8,
    limit: usize,
    masked: bool,
}

impl Reader {
    /// The window side: masked client frames, at most [`MAX_MESSAGE`] per message.
    pub fn server() -> Self {
        Self { buffer: Vec::new(), fragments: Vec::new(), fragmented: false, kind: 0, limit: MAX_MESSAGE, masked: true }
    }

    /// The worker stream's side: unmasked server frames; its messages are the front's own.
    pub fn client() -> Self {
        Self { masked: false, limit: usize::MAX, ..Self::server() }
    }

    pub fn feed(&mut self, data: &[u8]) {
        self.buffer.extend_from_slice(data);
    }

    /// The next complete message (`(opcode, payload)`), `Ok(None)` when more bytes are needed.
    pub fn next(&mut self) -> Result<Option<(u8, Vec<u8>)>, Closed> {
        loop {
            if self.buffer.len() < 2 {
                return Ok(None);
            }
            let (first, second) = (self.buffer[0], self.buffer[1]);
            let opcode = first & 0x0f;
            let mut length = u64::from(second & 0x7f);
            let mut offset = 2;
            if first & 0x70 != 0 || (second & 0x80 != 0) != self.masked {
                return Err(Closed(1002));
            }
            if length == 126 {
                if self.buffer.len() < 4 {
                    return Ok(None);
                }
                length = u64::from(u16::from_be_bytes([self.buffer[2], self.buffer[3]]));
                offset = 4;
            } else if length == 127 {
                if self.buffer.len() < 10 {
                    return Ok(None);
                }
                length = u64::from_be_bytes(self.buffer[2..10].try_into().expect("eight bytes"));
                offset = 10;
            }
            if length > self.limit as u64 {
                return Err(Closed(1009));
            }
            let length = length as usize;
            let mask_length = if self.masked { 4 } else { 0 };
            if self.buffer.len() < offset + mask_length + length {
                return Ok(None);
            }
            let start = offset + mask_length;
            let mut payload = self.buffer[start..start + length].to_vec();
            if self.masked {
                let mask = [self.buffer[offset], self.buffer[offset + 1], self.buffer[offset + 2], self.buffer[offset + 3]];
                payload.iter_mut().enumerate().for_each(|(i, byte)| *byte ^= mask[i % 4]);
            }
            self.buffer.drain(..start + length);
            let last = first & 0x80 != 0;
            if opcode >= 8 {
                if !last || length > 125 || !matches!(opcode, CLOSE | PING | PONG) {
                    return Err(Closed(1002));
                }
                return Ok(Some((opcode, payload)));
            }
            if opcode == TEXT || opcode == BINARY {
                if self.fragmented {
                    return Err(Closed(1002));
                }
                self.kind = opcode;
            } else if opcode != 0 || (!self.fragmented && self.kind == 0) {
                return Err(Closed(1002));
            }
            self.fragments.extend_from_slice(&payload);
            self.fragmented = true;
            if self.fragments.len() > self.limit {
                return Err(Closed(1009));
            }
            if last {
                let message = std::mem::take(&mut self.fragments);
                let kind = std::mem::replace(&mut self.kind, 0);
                self.fragmented = false;
                return Ok(Some((kind, message)));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn masked(first: u8, payload: &[u8]) -> Vec<u8> {
        let mut out = client_frame(first & 0x0f, payload);
        out[0] = first;
        out
    }

    #[test]
    fn the_accept_key_is_rfc_6455s_example() {
        assert_eq!(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=");
        assert!(valid_key("dGhlIHNhbXBsZSBub25jZQ=="));
        assert!(!valid_key("dGhlIHNhbXBsZSBub25jZQ") && !valid_key("c2hvcnQ=") && !valid_key("dGhlIHNhbXBsZSBub25jZQ==\n"));
    }

    #[test]
    fn masked_fragments_join_and_control_frames_interleave() {
        let mut reader = Reader::server();
        reader.feed(&masked(0x01, b"he"));
        reader.feed(&masked(0x89, b"ping"));
        reader.feed(&masked(0x80, b"llo"));
        assert_eq!(reader.next(), Ok(Some((PING, b"ping".to_vec()))));
        assert_eq!(reader.next(), Ok(Some((TEXT, b"hello".to_vec()))));
        assert_eq!(reader.next(), Ok(None));
        let long = vec![b'x'; 70_000];
        let bytes = client_frame(TEXT, &long);
        reader.feed(&bytes[..100]);
        assert_eq!(reader.next(), Ok(None));
        reader.feed(&bytes[100..]);
        assert_eq!(reader.next(), Ok(Some((TEXT, long))));
    }

    #[test]
    fn protocol_errors_close_as_python_does() {
        let refuse = |bytes: Vec<u8>| {
            let mut reader = Reader::server();
            reader.feed(&bytes);
            reader.next()
        };
        assert_eq!(refuse(frame(TEXT, b"unmasked")), Err(Closed(1002)));
        assert_eq!(refuse(masked(0xc1, b"rsv")), Err(Closed(1002)));
        assert_eq!(refuse(masked(0x09, b"fragmented ping")), Err(Closed(1002)));
        assert_eq!(refuse(masked(0x8b, b"")), Err(Closed(1002)));
        assert_eq!(refuse(masked(0x80, b"continuation first")), Err(Closed(1002)));
        assert_eq!(refuse(masked(0x83, b"reserved opcode")), Err(Closed(1002)));
        let mut two = masked(0x01, b"a");
        two.extend(masked(0x81, b"b"));
        assert_eq!(refuse(two), Err(Closed(1002)));
        let mut big = vec![0x81, 0xff];
        big.extend_from_slice(&((MAX_MESSAGE as u64) + 1).to_be_bytes());
        assert_eq!(refuse(big), Err(Closed(1009)));
        let mut pieces = Vec::new();
        for _ in 0..17 {
            pieces.extend(masked(if pieces.is_empty() { 0x01 } else { 0x00 }, &[b'x'; 65_536]));
        }
        assert_eq!(refuse(pieces), Err(Closed(1009)));
    }

    #[test]
    fn the_client_side_reads_unmasked_frames_only() {
        let mut reader = Reader::client();
        reader.feed(&frame(TEXT, b"{\"type\":\"nudge\"}"));
        assert_eq!(reader.next(), Ok(Some((TEXT, b"{\"type\":\"nudge\"}".to_vec()))));
        reader.feed(&client_frame(TEXT, b"masked"));
        assert_eq!(reader.next(), Err(Closed(1002)));
        assert_eq!(close_code(&close(1001)[2..]), 1001);
        assert_eq!(close_code(b""), 1000);
    }
}
