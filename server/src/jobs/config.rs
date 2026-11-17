//! What the job routes read from the worker's validated config and memory.
//!
//! The YAML files are loaded and validated by the worker (pydantic), and only it re-reads them
//! (`WebApplication.reload_config`, after a profile or source edit, a finder approval or
//! `/api/reload`, all of which reach it as forwarded mutations). The front asks for the
//! projection the routes need (`GET /api/internal/jobs/config`) and keeps it until the next
//! forwarded mutation, so an answer always uses the config the worker holds at that moment,
//! as Python's handlers used `app.config`. The browser controller's state lives only in the
//! worker (`GET /api/internal/jobs/automation`).

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use bytes::Bytes;
use http_body_util::{BodyExt, Empty};
use hyper::{Method, Request};
use hyper_util::rt::TokioIo;
use serde::Deserialize;
use serde_json::Value;
use tokio::net::TcpStream;

use crate::front::Backend;
use crate::ported::{fail, internal, Done};

const CONFIG: &str = "/api/internal/jobs/config";
const AUTOMATION: &str = "/api/internal/jobs/automation";

/// One configured track (`config.tracks.tracks`, in file order).
#[derive(Debug, Deserialize)]
pub struct Track {
    pub name: String,
    pub prefer_cities: Vec<String>,
    pub default_resume: Option<String>,
}

/// A saved view's filters (`ViewFilters.model_dump`); a number outside 64 bits stays a JSON
/// value, as Python's `int` would have overflowed where it is used.
#[derive(Debug, Default, Deserialize)]
pub struct ViewFilters {
    pub track: Option<Vec<String>>,
    pub tier: Option<Vec<String>>,
    pub status: Option<Vec<String>>,
    pub queue_state: Option<Vec<String>>,
    pub source: Option<Vec<String>>,
    pub eligibility: Option<Vec<String>>,
    pub school_selective: Option<bool>,
    pub posted_within_days: Option<Value>,
    pub has_deadline: Option<bool>,
    pub next_action_due: Option<bool>,
    pub not_applied: Option<bool>,
    pub include_closed: Option<bool>,
    pub text: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct View {
    pub key: i64,
    pub title: String,
    pub filters: ViewFilters,
}

/// The projection the worker answers (`web_jobs._config`).
#[derive(Debug, Deserialize)]
pub struct JobConfig {
    pub tracks: Vec<Track>,
    pub open_to_remote: bool,
    pub views: Vec<View>,
    pub followup_days: Vec<Value>,
}

impl JobConfig {
    /// `config.track_names`.
    pub fn track_names(&self) -> impl Iterator<Item = &str> {
        self.tracks.iter().map(|track| track.name.as_str())
    }

    pub fn has_track(&self, name: &str) -> bool {
        self.track_names().any(|known| known == name)
    }

    /// `config.track(name)`: the first track of that name.
    pub fn track(&self, name: &str) -> Option<&Track> {
        self.tracks.iter().find(|track| track.name == name)
    }

    /// `config.prefer_cities`: every track's preferred cities once each, in file order.
    pub fn prefer_cities(&self) -> Vec<String> {
        let mut seen: Vec<String> = Vec::new();
        for city in self.tracks.iter().flat_map(|track| &track.prefer_cities) {
            if !seen.contains(city) {
                seen.push(city.clone());
            }
        }
        seen
    }
}

/// The browser controller's state (`AutomationController.snapshot`), as bulk delete reads it.
#[derive(Debug, Deserialize)]
pub struct Automation {
    pub running: bool,
    pub job_ids: Vec<Value>,
}

/// Bumped after every mutation forwarded to the worker: any of them may have reloaded its config.
static GENERATION: AtomicU64 = AtomicU64::new(0);
static CACHED: Mutex<Option<(u64, Arc<JobConfig>)>> = Mutex::new(None);

/// A mutation the worker answered: its config may have changed.
pub fn forwarded_mutation() {
    GENERATION.fetch_add(1, Ordering::SeqCst);
}

/// The worker's config as it is now.
pub async fn current(backend: &Backend) -> Done<Arc<JobConfig>> {
    let generation = GENERATION.load(Ordering::SeqCst);
    if let Some((seen, config)) = CACHED.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).as_ref() {
        if *seen == generation {
            return Ok(config.clone());
        }
    }
    let config: Arc<JobConfig> = Arc::new(serde_json::from_value(ask(backend, CONFIG).await?).map_err(|_| internal())?);
    // A mutation forwarded while this was asked leaves the generation behind: asked again next time.
    *CACHED.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some((generation, config.clone()));
    Ok(config)
}

/// The browser controller's state now.
pub async fn automation(backend: &Backend) -> Done<Automation> {
    serde_json::from_value(ask(backend, AUTOMATION).await?).map_err(|_| internal())
}

/// GET one of the worker's internal routes (its Host, UI secret and front secret): its answer,
/// or its error as Python sent it.
async fn ask(backend: &Backend, path: &str) -> Done<Value> {
    let busy = || fail(503, "Local workspace is busy or unavailable; try again");
    let request = Request::builder()
        .method(Method::GET)
        .uri(path)
        .header("host", &backend.authority)
        .header("x-kitty-secret", &backend.secret)
        .header("x-kitty-front", &backend.front_secret)
        .body(Empty::<Bytes>::new())
        .map_err(|_| internal())?;
    let stream = TcpStream::connect(&backend.authority).await.map_err(|_| busy())?;
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|_| busy())?;
    tokio::spawn(async move {
        let _ = connection.await;
    });
    let response = sender.send_request(request).await.map_err(|_| busy())?;
    let status = response.status().as_u16();
    let data = response.into_body().collect().await.map_err(|_| busy())?.to_bytes();
    let answer: Value = serde_json::from_slice(&data).map_err(|_| internal())?;
    if status != 200 {
        let message = answer.get("error").and_then(Value::as_str).unwrap_or("Local operation failed; inspect CLI diagnostics");
        return Err(fail(status, message));
    }
    Ok(answer)
}
