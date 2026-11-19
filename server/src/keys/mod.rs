//! Settings → Keys (`web_keys` in Python): the status of each key, never its value.
//!
//! ```text
//! GET  /api/keys          {keys: [{name, label, env, custom, set, backend, in_file, error}],
//!                          keyring, keyring_label, store, account_passwords, warnings}
//! POST /api/keys/set      {name, value} -> the same status (the value is never echoed)
//! POST /api/keys/remove   {name}
//! POST /api/keys/move     {name}: its owner-only file copy into the OS keyring
//! ```
//!
//! A value arrives once in a request body and goes straight to the keyring
//! (crate::keys::secret_service) or the owner-only file (crate::keys::store); it is never
//! logged, echoed or included in an error message. Python's `kitty.core.keys` reads the same
//! store for the AI providers and `kitty keys`.

mod http;
mod secret_service;
mod store;

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use hyper::body::Incoming;
use hyper::{Method, Request, Response, StatusCode};
use serde_json::Value;

use crate::front::{Body, Front};
use crate::target::{parse_target, Target};
use http::{content_length, failure, json_object, read_body, send_json, Refusal};
use store::{strip, Error, Keys};

pub const PATH: &str = "/api/keys";
const ACTIONS: &str = "/api/keys/";

/// The workspace the worker opened and the environment its keys fall back to.
struct State {
    root: PathBuf,
    demo: bool,
    /// The worker's `os.environ`: this process's environment plus the root's `.env`, which
    /// `Settings.load` adds at start without overriding.
    environment: HashMap<String, String>,
}

static STATE: OnceLock<State> = OnceLock::new();
/// Python ran every mutation under its mutation lock; key changes stay one at a time.
static MUTATIONS: Mutex<()> = Mutex::new(());

/// Called once at start-up, with the workspace the worker opened.
pub fn start(root: PathBuf, demo: bool) {
    let environment = environment(&root);
    let _ = STATE.set(State { root, demo, environment });
}

fn environment(root: &Path) -> HashMap<String, String> {
    let mut values: HashMap<String, String> = std::env::vars_os()
        .filter_map(|(key, value)| Some((key.into_string().ok()?, value.to_string_lossy().into_owned())))
        .collect();
    if let Ok(text) = std::fs::read_to_string(root.join(".env")) {
        for (key, value) in crate::settings::dotenv(&text) {
            if let Some(value) = value {
                values.entry(key).or_insert(value);
            }
        }
    }
    values
}

/// `for_settings`: a demo workspace uses only its own throwaway files, never the OS keyring
/// or the environment.
fn for_settings(state: &State) -> Keys<'_> {
    if state.demo {
        return Keys { root: state.root.clone(), store: None, environment: None };
    }
    Keys { root: state.root.clone(), store: keyring(&state.environment), environment: Some(&state.environment) }
}

/// `keyring()`: the Secret Service, unless `KITTY_KEYRING` turns it off or none answers.
fn keyring(environment: &HashMap<String, String>) -> Option<Box<dyn store::Keyring + '_>> {
    let get = |name: &str| environment.get(name).map_or("", |value| strip(value));
    if matches!(get("KITTY_KEYRING").to_lowercase().as_str(), "none" | "off" | "0") {
        return None;
    }
    let service = Some(get("KITTY_KEYRING_SERVICE")).filter(|service| !service.is_empty()).unwrap_or(store::DEFAULT_SERVICE);
    let address = environment.get("DBUS_SESSION_BUS_ADDRESS").map(String::as_str);
    secret_service::SecretService::open(address, service).map(|ring| Box::new(ring) as Box<dyn store::Keyring>)
}

impl From<Error> for Refusal {
    fn from(error: Error) -> Self {
        match error {
            Error::Keys(status, message) => Refusal::new(status, message),
            Error::Internal => Refusal::new(500, "Local operation failed; inspect CLI diagnostics"),
        }
    }
}

/// The other routes' use of the store (Settings → AI's OpenRouter key, crate::insights): the
/// same `for_settings` store and mutation lock; refusals are (status, message).
fn other<T>(work: impl FnOnce(&mut Keys<'_>) -> Result<T, Error>) -> Result<T, (u16, String)> {
    let state = STATE.get().ok_or_else(|| (500, "Local operation failed; inspect CLI diagnostics".to_owned()))?;
    let _held = MUTATIONS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    work(&mut for_settings(state)).map_err(|error| match error {
        Error::Keys(status, message) => (status, message.to_owned()),
        Error::Internal => (500, "Local operation failed; inspect CLI diagnostics".to_owned()),
    })
}

/// `keys.read(settings, name)`: the value, or "" when unset.
pub fn read(name: &str) -> Result<String, (u16, String)> {
    other(|keys| keys.read(name))
}

/// `Keys.set(name, value)`.
pub fn set(name: &str, value: &str) -> Result<(), (u16, String)> {
    other(|keys| keys.set(&Value::String(name.to_owned()), &Value::String(value.to_owned())))
}

/// `Keys.remove(name)`.
pub fn remove(name: &str) -> Result<(), (u16, String)> {
    other(|keys| keys.remove(&Value::String(name.to_owned())).map(|_| ()))
}

/// The front's route for `/api/keys` and `/api/keys/*`; None for every other request.
pub async fn route(_front: &Front, target: &Target, forward_target: &str, request: &mut Request<Incoming>, mutation: bool, upgrade: bool) -> Option<Response<Body>> {
    if upgrade || !(target.path == PATH || target.path.starts_with(ACTIONS)) {
        return None;
    }
    let head = request.method() == Method::HEAD;
    Some(match answer(target, forward_target, request, mutation).await {
        Ok(status) => send_json(StatusCode::OK, &status, head),
        Err(refusal) => failure(refusal, head),
    })
}

async fn answer(target: &Target, forward_target: &str, request: &mut Request<Incoming>, mutation: bool) -> Result<Value, Refusal> {
    let state = STATE.get().ok_or_else(|| Refusal::from(Error::Internal))?;
    if !mutation {
        if target.path != PATH {
            return Err(Refusal::new(404, "API route not found"));
        }
        no_query(forward_target)?;
        return blocking(move || for_settings(state).status()).await;
    }
    no_query(forward_target)?;
    let length = content_length(request.headers())?;
    let body = json_object(&read_body(request.body_mut(), length).await?)?;
    let action = target.path.strip_prefix(ACTIONS).unwrap_or("").to_owned();
    if !matches!(action.as_str(), "set" | "remove" | "move") {
        return Err(Refusal::new(404, "API route not found"));
    }
    let fields: &[&str] = if action == "set" { &["name", "value"] } else { &["name"] };
    if body.len() != fields.len() || !fields.iter().all(|field| body.contains_key(*field)) {
        return Err(Refusal::new(400, if action == "set" { "Expected {\"name\", \"value\"}" } else { "Expected {\"name\"}" }));
    }
    blocking(move || {
        let _held = MUTATIONS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let mut keys = for_settings(state);
        match action.as_str() {
            "set" => keys.set(&body["name"], &body["value"])?,
            "remove" => {
                keys.remove(&body["name"])?;
            }
            _ => keys.move_to_keyring(&body["name"])?,
        }
        keys.status()
    })
    .await
}

/// `_no_query` on the query the worker would have seen (a spent link ticket removed).
fn no_query(forward_target: &str) -> Result<(), Refusal> {
    let parsed = parse_target(forward_target).map_err(|invalid| Refusal::new(invalid.0, invalid.1))?;
    if !parsed.query.is_empty() {
        return Err(Refusal::new(400, "This route does not accept query parameters"));
    }
    Ok(())
}

/// Files and D-Bus calls block: off the request threads.
async fn blocking(work: impl FnOnce() -> Result<Value, Error> + Send + 'static) -> Result<Value, Refusal> {
    match tokio::task::spawn_blocking(work).await {
        Ok(outcome) => outcome.map_err(Refusal::from),
        Err(_) => Err(Error::Internal.into()),
    }
}
