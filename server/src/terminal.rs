//! The Terminal module's routes answered over local state (`kitty.web_terminal` in Python):
//!
//! - `GET /api/terminal/agents`: `{binary, session, cwd, default, kinds}`, the herdr binary and
//!   session the worker resolved (its readiness line's `terminal`: `$KITTY_HERDR`, the workspace's
//!   `.env` and the herdr pack) and the coding agents an Agent tab can start;
//! - `GET /api/terminal/socket` without an upgrade: 426.
//!
//! Module and pack guard as `WebApplication.guard`: Terminal on, then the herdr pack loaded.
//! Everything that runs herdr stays in the worker: the terminal WebSocket and its single-use
//! tokens (`POST /api/terminal/session`), `GET /api/terminal/activity`, `POST
//! /api/terminal/agents` (the mediated launch) and `/focus`; so do Tern and agent skills.
//! tests/test_agents_parity.py compares the answers with Python's.

use std::sync::OnceLock;

use hyper::{Method, Request, Response, StatusCode};
use serde_json::{json, Value};

use crate::front::{refuse, send_json, Body, Front, Refusal};
use crate::target::Target;

const AGENTS: &str = "/api/terminal/agents";
const SOCKET: &str = "/api/terminal/socket";
/// `kitty.core.terminal.CODING_AGENTS`; the first is the default.
const CODING_AGENTS: [&str; 3] = ["omp", "claude", "codex"];

/// The worker's herdr runtime (`HerdrRuntime.binary`, `.session`) and `Settings.root`.
#[derive(Clone, Debug, serde::Deserialize)]
pub struct Herdr {
    pub binary: Option<String>,
    pub session: String,
    pub cwd: String,
}

static HERDR: OnceLock<Option<Herdr>> = OnceLock::new();

/// The worker's readiness `terminal` (None while the Terminal module is off).
pub fn start(herdr: Option<Herdr>) {
    let _ = HERDR.set(herdr);
}

pub fn route<B>(front: &Front, target: &Target, request: &Request<B>, mutation: bool, upgrade: bool) -> Option<Result<Response<Body>, Refusal>> {
    if mutation || upgrade || (target.path != AGENTS && target.path != SOCKET) {
        return None;
    }
    let head = request.method() == Method::HEAD;
    Some(answer(front, &target.path).map(|value| send_json(StatusCode::OK, &value, head)))
}

fn answer(front: &Front, path: &str) -> Result<Value, Refusal> {
    let workspace = &front.workspace;
    if !workspace.active.iter().any(|module| module == "terminal") {
        let disabled = crate::ported::disabled("terminal");
        return Err(refuse(disabled.status, disabled.message));
    }
    if workspace.missing.iter().any(|pack| pack == "herdr") {
        let label = crate::modules::registry().packs.iter().find(|pack| pack.id == "herdr").map_or("herdr", |pack| pack.label.as_str());
        return Err(refuse(409, format!("{label} isn't installed: download it in Settings > Modules, then restart rika!")));
    }
    if path == SOCKET {
        return Err(refuse(426, "Open the terminal with a WebSocket upgrade"));
    }
    let herdr = HERDR.get().cloned().flatten().ok_or_else(|| refuse(500, "Local operation failed; inspect CLI diagnostics"))?;
    Ok(json!({"binary": herdr.binary, "session": herdr.session, "cwd": herdr.cwd, "default": CODING_AGENTS[0], "kinds": CODING_AGENTS}))
}
