//! Chromium's DevTools protocol over loopback, and the resume page geometry rules.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use serde_json::{json, Value};
use tungstenite::{Message, WebSocket};

pub struct Cdp {
    socket: WebSocket<TcpStream>,
    serial: u64,
}

fn loopback(port: u16) -> Result<TcpStream, String> {
    let stream = TcpStream::connect(("127.0.0.1", port)).map_err(|e| format!("Chromium is unreachable: {e}"))?;
    stream.set_read_timeout(Some(Duration::from_secs(30))).map_err(|e| e.to_string())?;
    stream.set_write_timeout(Some(Duration::from_secs(30))).map_err(|e| e.to_string())?;
    Ok(stream)
}

/// The DevTools WebSocket path of Chromium's page target (not workers or extensions).
fn page_target(port: u16) -> Result<String, String> {
    let mut stream = loopback(port)?;
    write!(stream, "GET /json HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n").map_err(|e| e.to_string())?;
    // Chromium keeps the connection open: read the headers, then exactly Content-Length bytes.
    let mut raw = Vec::new();
    let mut chunk = [0u8; 8192];
    let (start, length) = loop {
        let read = stream.read(&mut chunk).map_err(|e| format!("Chromium's target list: {e}"))?;
        if read == 0 || raw.len() > 1024 * 1024 {
            return Err("Chromium sent no target list".into());
        }
        raw.extend_from_slice(&chunk[..read]);
        if let Some(end) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
            let headers = String::from_utf8_lossy(&raw[..end]).to_ascii_lowercase();
            let length = headers.lines()
                .find_map(|line| line.strip_prefix("content-length:").and_then(|v| v.trim().parse::<usize>().ok()))
                .filter(|length| *length <= 1024 * 1024)
                .ok_or("Chromium's target list has no length")?;
            break (end + 4, length);
        }
    };
    while raw.len() < start + length {
        let read = stream.read(&mut chunk).map_err(|e| format!("Chromium's target list: {e}"))?;
        if read == 0 {
            return Err("Chromium's target list was cut short".into());
        }
        raw.extend_from_slice(&chunk[..read]);
    }
    let targets: Value = serde_json::from_slice(&raw[start..start + length])
        .map_err(|_| "Chromium's target list is unreadable".to_string())?;
    let url = targets.as_array().into_iter().flatten()
        .find(|item| item["type"] == "page")
        .and_then(|item| item["webSocketDebuggerUrl"].as_str())
        .ok_or("Chromium has no page target")?;
    let prefix = format!("ws://127.0.0.1:{port}/");
    url.strip_prefix(&prefix).map(|path| format!("/{path}")).ok_or_else(|| "Chromium's page target is not on loopback".into())
}

impl Cdp {
    pub fn connect(port: u16) -> Result<Self, String> {
        let path = page_target(port)?;
        let url = format!("ws://127.0.0.1:{port}{path}");
        let (socket, _) = tungstenite::client::client(url, loopback(port)?).map_err(|e| format!("Chromium handshake failed: {e}"))?;
        Ok(Self { socket, serial: 0 })
    }

    pub fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        self.serial += 1;
        let request = json!({"id": self.serial, "method": method, "params": params});
        self.socket.send(Message::text(request.to_string())).map_err(|e| format!("Chromium {method}: {e}"))?;
        loop {
            let message = self.socket.read().map_err(|e| format!("Chromium {method}: {e}"))?;
            let Message::Text(text) = message else { continue };
            let reply: Value = serde_json::from_str(text.as_str()).map_err(|_| format!("Chromium {method}: unreadable reply"))?;
            if reply["id"] != self.serial {
                continue;
            }
            if let Some(error) = reply.get("error") {
                return Err(format!("Chromium {method}: {}", error["message"].as_str().unwrap_or("failed")));
            }
            return Ok(reply.get("result").cloned().unwrap_or(json!({})));
        }
    }

    pub fn evaluate(&mut self, expression: &str) -> Result<Value, String> {
        let result = self.call("Runtime.evaluate", json!({"expression": expression, "returnByValue": true, "awaitPromise": true}))?;
        if result.get("exceptionDetails").is_some() {
            return Err("Unable to inspect resume layout".into());
        }
        Ok(result["result"].get("value").cloned().unwrap_or(Value::Null))
    }
}

fn inches(value: &str) -> Result<f64, String> {
    let refused = || format!("Unsupported @page dimension {value:?}; use physical lengths");
    let trimmed = value.trim();
    let split = trimmed.find(|c: char| !(c.is_ascii_digit() || c == '.')).unwrap_or(trimmed.len());
    let (number, unit) = trimmed.split_at(split);
    let number: f64 = number.parse().map_err(|_| refused())?;
    let factor = match unit.trim() {
        "in" => 1.0,
        "pt" => 1.0 / 72.0,
        "px" => 1.0 / 96.0,
        "mm" => 1.0 / 25.4,
        "cm" => 1.0 / 2.54,
        "" if number == 0.0 => 1.0,
        _ => return Err(refused()),
    };
    Ok(number * factor)
}

/// Paper width, height and margins (top, right, bottom, left) in inches from the document's
/// `@page` rules: Letter or A4 portrait only, so the one-page check means something.
pub fn geometry(rules: &Value) -> Result<(f64, f64, [f64; 4]), String> {
    let (mut width, mut height, mut margins) = (8.5, 11.0, [0.4; 4]);
    for rule in rules.as_array().into_iter().flatten() {
        if rule["selector"].as_str().is_some_and(|s| !s.is_empty()) {
            return Err("Named or pseudo @page rules are not supported for one-page validation".into());
        }
        let size = rule["size"].as_str().unwrap_or("").to_lowercase();
        let parts: Vec<&str> = size.split_whitespace().collect();
        match parts.as_slice() {
            [] => {}
            ["letter"] | ["auto"] => (width, height) = (8.5, 11.0),
            ["a4"] => (width, height) = (210.0 / 25.4, 297.0 / 25.4),
            [w, h] => (width, height) = (inches(w)?, inches(h)?),
            _ => return Err("Use Letter, A4, or explicit portrait @page size".into()),
        }
        for (index, value) in rule["margins"].as_array().into_iter().flatten().take(4).enumerate() {
            if let Some(value) = value.as_str().filter(|v| !v.is_empty()) {
                margins[index] = inches(value)?;
            }
        }
    }
    if !((7.0..=9.0).contains(&width) && (10.0..=12.0).contains(&height)) || margins.iter().any(|m| *m < 0.0) {
        return Err("Resume must use readable Letter/A4-sized portrait paper".into());
    }
    Ok((width, height, margins))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_rules_accept_letter_a4_and_physical_lengths_only() {
        assert_eq!(geometry(&json!([])).unwrap(), (8.5, 11.0, [0.4; 4]));
        let a4 = geometry(&json!([{"selector": "", "size": "A4", "margins": ["18mm", "", "0", "1in"]}])).unwrap();
        assert!((a4.0 - 8.2677).abs() < 1e-3 && (a4.2[0] - 0.7087).abs() < 1e-3 && a4.2[2] == 0.0 && a4.2[3] == 1.0);
        assert!(geometry(&json!([{"selector": ":first", "size": "", "margins": []}])).is_err());
        assert!(geometry(&json!([{"selector": "", "size": "11in 8.5in", "margins": []}])).is_err());
        assert!(geometry(&json!([{"selector": "", "size": "", "margins": ["2em"]}])).is_err());
    }
}
