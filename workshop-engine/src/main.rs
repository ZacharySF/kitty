//! rika! Resume Workshop engine.
//!
//! Host commands read one JSON request on stdin and print one JSON reply on stdout:
//! `{"ok": true, "result": ...}` or `{"ok": false, "status": <http>, "error": "..."}`.
//!
//! * `capabilities` - which compilers, the sandbox and OCR are usable, plus a runtime digest
//!   per engine for compile caches.
//! * `compile` - compile a prepared job folder in the sandbox, then inspect and rasterize it.
//! * `inspect` - read an uploaded PDF's text (optionally OCR) in the sandbox.
//! * `docx` - read an uploaded DOCX's paragraphs in the sandbox.
//!
//! `worker` is the private in-sandbox half; it only ever runs inside bubblewrap.

mod ats;
mod cdp;
mod discover;
mod docx;
mod host;
mod pdf;
mod sandbox;
mod worker;

use std::io::Read;

use serde_json::{json, Value};

/// A refusal or failure with the HTTP status the Python layer reports it under.
#[derive(Debug)]
pub struct Failure {
    pub status: u16,
    pub message: String,
}

impl Failure {
    pub fn new(status: u16, message: impl Into<String>) -> Self {
        Self { status, message: message.into() }
    }
}

pub type Outcome<T> = Result<T, Failure>;

/// Largest request a host command accepts (paths and small options only, never documents).
const MAX_REQUEST: u64 = 64 * 1024;

fn main() {
    let command = std::env::args().nth(1).unwrap_or_default();
    if command == "worker" {
        worker::main();
    }
    let outcome = match command.as_str() {
        "capabilities" => Ok(discover::capabilities()),
        "compile" | "inspect" | "docx" => request().and_then(|body| match command.as_str() {
            "compile" => host::compile(&body),
            "inspect" => host::inspect(&body),
            _ => host::docx(&body),
        }),
        _ => {
            eprintln!("usage: kitty-workshop capabilities|compile|inspect|docx < request.json");
            std::process::exit(2);
        }
    };
    let reply = match outcome {
        Ok(result) => json!({"ok": true, "result": result}),
        Err(failure) => json!({"ok": false, "status": failure.status, "error": failure.message}),
    };
    println!("{reply}");
}

fn request() -> Outcome<Value> {
    let mut raw = String::new();
    std::io::stdin()
        .take(MAX_REQUEST + 1)
        .read_to_string(&mut raw)
        .map_err(|_| Failure::new(400, "Engine request must be UTF-8 JSON"))?;
    if raw.len() as u64 > MAX_REQUEST {
        return Err(Failure::new(400, "Engine request is too large"));
    }
    let body: Value = serde_json::from_str(&raw).map_err(|_| Failure::new(400, "Engine request must be JSON"))?;
    if !body.is_object() {
        return Err(Failure::new(400, "Engine request must be a JSON object"));
    }
    Ok(body)
}
