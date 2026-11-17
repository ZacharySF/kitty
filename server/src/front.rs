//! The browser-facing server: the same rules as `kitty.web_server.RequestHandler`.
//!
//! Order of checks, as in `_dispatch`: the paired-client ingress goes straight to the
//! backend (it has its own bearer tokens); every other request passes the page checks
//! (`_security`: no Authorization, exact Host and Origin, `Sec-Fetch-Site`, and for a
//! mutation `X-Kitty-Request: 1` plus `application/json`), then the UI secret (`_authorize`:
//! the header, or a single-use link ticket on a GET, or the code exchange); upgrades need
//! the exact Origin and the secret as a subprotocol. The session routes, the ported routes
//! (`/api/modules`, crate::modules) and the built website are answered here; every other
//! route is forwarded to the Python worker with
//! the worker's own secrets, so the page never learns the worker's address or secrets.
//! External modules (`kitty.web_external_modules`) stay the worker's: their routes
//! (`/api/modules/<id>`) and element scripts (`/modules/<id>/<version>/...`) are forwarded,
//! the module state answered here adds the worker's `GET /api/external-modules`, and the
//! Content-Security-Policy on every answer frames their servers' ports (its readiness line).

use std::convert::Infallible;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use bytes::Bytes;
use http_body_util::combinators::BoxBody;
use http_body_util::{BodyExt, Empty, Full, Limited};
use hyper::body::Incoming;
use hyper::header::{HeaderMap, HeaderName, HeaderValue};
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{json, Map, Value};
use tokio::net::TcpStream;

use crate::auth::{self, UiAuth};
use crate::modules::{self, Workspace};
use crate::packs::{self, Packs};
use crate::target::{parse_target, Invalid, Target};

pub type Body = BoxBody<Bytes, hyper::Error>;

/// `web_server.MAX_BODY`: bounded canvas JSON plus a base64 raster import.
pub const MAX_BODY: u64 = 20 * 1024 * 1024;
const INGRESS: &str = "/api/ingress";
const CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src http://127.0.0.1:3210; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";
/// The worker's own route for its external modules' state (`kitty.web_external_modules`).
const EXTERNAL_PATH: &str = "/api/external-modules";
/// Element view scripts of external modules, served by the worker (`RequestHandler._module_asset`).
const MODULE_ASSETS: &str = "/modules/";

/// `RequestHandler._send`'s policy: LearnHarness's fixed frame plus the worker's framed
/// external-module servers, whose ports are fixed at its start-up (its readiness line).
pub fn csp(frame_ports: &[u16]) -> HeaderValue {
    let frames: String = frame_ports.iter().map(|port| format!(" http://127.0.0.1:{port}")).collect();
    let policy = CSP.replacen("frame-src http://127.0.0.1:3210;", &format!("frame-src http://127.0.0.1:3210{frames};"), 1);
    HeaderValue::from_str(&policy).expect("loopback origins are valid header text")
}
/// `web_server.STATIC_TYPES`: anything else under the website is a 404.
const STATIC_TYPES: &[(&str, &str)] = &[
    ("html", "text/html; charset=utf-8"), ("js", "text/javascript; charset=utf-8"),
    ("css", "text/css; charset=utf-8"), ("svg", "image/svg+xml"), ("png", "image/png"),
    ("jpg", "image/jpeg"), ("jpeg", "image/jpeg"), ("webp", "image/webp"),
    ("ico", "image/x-icon"), ("woff", "font/woff"), ("woff2", "font/woff2"), ("ttf", "font/ttf"),
];
/// Never copied between the two connections.
const HOP_BY_HOP: &[&str] = &["connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"];
/// Set by the front itself; a value a client sends is dropped, never forwarded.
const FRONT_ONLY: &[&str] = &["x-kitty-front", "x-kitty-peer-port", "x-kitty-front-port"];

/// Where the worker listens and the secrets it requires.
#[derive(Clone)]
pub struct Backend {
    pub authority: String,
    pub origin: String,
    pub secret: String,
    pub front_secret: String,
}

pub struct Front {
    /// `127.0.0.1:<port>`.
    pub authority: String,
    /// `http://127.0.0.1:<port>`.
    pub origin: String,
    pub port: u16,
    pub auth: UiAuth,
    /// The built website (`web/dist`), canonical.
    pub static_dir: PathBuf,
    /// Module packs (crate::packs): listed, installed and served from the user's data folder.
    pub packs: Arc<Packs>,
    pub backend: Backend,
    /// The workspace the worker opened: the routes answered here read and write its files.
    pub workspace: Workspace,
    /// What `/api/bootstrap` reads besides the module state (crate::bootstrap).
    pub bootstrap: crate::bootstrap::Bootstrap,
    /// The Content-Security-Policy every answer carries ([`csp`]).
    pub csp: HeaderValue,
}

/// A refusal the Python server would also send: status, message and extra headers.
pub(crate) struct Refusal {
    status: StatusCode,
    message: String,
    headers: Vec<(&'static str, &'static str)>,
}

pub(crate) fn refuse(status: u16, message: impl Into<String>) -> Refusal {
    Refusal { status: StatusCode::from_u16(status).expect("a valid status"), message: message.into(), headers: Vec::new() }
}

fn unauthorized() -> Refusal {
    Refusal { headers: vec![("www-authenticate", "Kitty realm=\"rika!\"")], ..refuse(401, auth::REFUSAL) }
}

impl From<modules::Failure> for Refusal {
    fn from(failure: modules::Failure) -> Self {
        refuse(failure.status, failure.message)
    }
}

impl From<Invalid> for Refusal {
    fn from(Invalid(status, message): Invalid) -> Self {
        refuse(status, message)
    }
}

fn full(data: impl Into<Bytes>) -> Body {
    Full::new(data.into()).map_err(|never: Infallible| match never {}).boxed()
}

fn empty() -> Body {
    Empty::new().map_err(|never: Infallible| match never {}).boxed()
}

/// `RequestHandler._send`: the same security headers on everything the front answers.
fn send(status: StatusCode, data: Vec<u8>, content_type: &str, head: bool) -> Response<Body> {
    let length = data.len();
    let mut response = Response::new(if head { empty() } else { full(data) });
    *response.status_mut() = status;
    let headers = response.headers_mut();
    for (name, value) in [
        ("content-type", content_type),
        ("cache-control", "no-store"),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "no-referrer"),
        ("cross-origin-resource-policy", "same-origin"),
        ("content-security-policy", CSP),
        ("server", "rika!"),
    ] {
        headers.insert(name, HeaderValue::from_str(value).expect("static header values are valid"));
    }
    headers.insert("content-length", HeaderValue::from(length));
    response
}

pub(crate) fn send_json(status: StatusCode, value: &Value, head: bool) -> Response<Body> {
    send(status, serde_json::to_vec(value).expect("JSON values serialize"), "application/json; charset=utf-8", head)
}

/// An error answer; like Python's, it closes the connection.
fn failure(refusal: Refusal, head: bool) -> Response<Body> {
    let mut response = send_json(refusal.status, &json!({ "error": refusal.message }), head);
    response.headers_mut().insert("connection", HeaderValue::from_static("close"));
    for (name, value) in refusal.headers {
        response.headers_mut().insert(name, HeaderValue::from_static(value));
    }
    response
}

/// Every value of one header, as Python's `headers.get_all` returns them; a value that is not
/// visible ASCII never matches anything.
fn all<'a>(headers: &'a HeaderMap, name: &str) -> Vec<&'a str> {
    headers.get_all(name).iter().map(|value| value.to_str().unwrap_or("\u{fffd}")).collect()
}

fn is_ingress(raw_target: &str) -> bool {
    let path = raw_target.split('?').next().unwrap_or("");
    path == INGRESS || path.starts_with(&format!("{INGRESS}/"))
}

fn is_api(path: &str) -> bool {
    path == "/api" || path.starts_with("/api/")
}

impl Front {
    /// `RequestHandler._security`.
    fn security(&self, headers: &HeaderMap, mutation: bool) -> Result<(), Refusal> {
        if headers.contains_key("authorization") {
            return Err(refuse(403, "Paired client tokens reach only /api/ingress"));
        }
        if all(headers, "host") != [self.authority.as_str()] {
            return Err(refuse(403, "Use the server's loopback address"));
        }
        let origins = all(headers, "origin");
        if !origins.is_empty() && origins != [self.origin.as_str()] {
            return Err(refuse(403, "Cross-origin requests are not allowed"));
        }
        if let Some(site) = headers.get("sec-fetch-site") {
            if !matches!(site.to_str(), Ok("same-origin" | "none")) {
                return Err(refuse(403, "Cross-site requests are not allowed"));
            }
        }
        if mutation {
            if all(headers, "x-kitty-request") != ["1"] {
                return Err(refuse(403, "Missing local request header"));
            }
            if all(headers, "content-type") != ["application/json"] {
                return Err(refuse(415, "Use application/json"));
            }
        }
        Ok(())
    }

    /// `RequestHandler._authorize`: the target to forward, with a spent ticket removed.
    fn authorize(&self, headers: &HeaderMap, method: &Method, target: &Target, mutation: bool) -> Result<Option<String>, Refusal> {
        if mutation && target.path == auth::SESSION_PATH {
            return Ok(None);
        }
        let presented = all(headers, auth::HEADER);
        if presented.is_empty() && *method == Method::GET {
            if let Some(ticket) = target.get(auth::TICKET) {
                let rest = Target { path: target.path.clone(), query: target.query.iter().filter(|(name, _)| name != auth::TICKET).cloned().collect() };
                if self.auth.follow(ticket, &rest.canonical()) {
                    return Ok(Some(target.query_without(auth::TICKET)));
                }
                return Err(unauthorized());
            }
        }
        if presented.len() != 1 || !self.auth.valid(presented[0]) {
            return Err(unauthorized());
        }
        Ok(None)
    }

    /// `RequestHandler._session`: the UI secret's own two routes.
    fn session(&self, path: &str, body: Map<String, Value>) -> Result<Value, Refusal> {
        if path == auth::SESSION_PATH {
            let code = match (body.len(), body.get("code")) {
                (1, Some(code)) => code,
                _ => return Err(refuse(400, "A session request carries only its code")),
            };
            let secret = code.as_str().and_then(|code| self.auth.redeem(code)).ok_or_else(unauthorized)?;
            return Ok(json!({ "secret": secret }));
        }
        let target = match (body.len(), body.get("path")) {
            (1, Some(Value::String(target))) => target.clone(),
            _ => return Err(refuse(400, "A link request carries only its path")),
        };
        let link = parse_target(&target)?;
        if !link.path.starts_with("/api/") || is_ingress(&link.path) || link.path == auth::SESSION_PATH
            || link.path.starts_with(&format!("{}/", auth::SESSION_PATH)) || link.get(auth::TICKET).is_some()
        {
            return Err(refuse(400, "Links are only for rika!'s own API downloads"));
        }
        let ticket = self.auth.issue_link(link.canonical());
        let joiner = if target.contains('?') { '&' } else { '?' };
        Ok(json!({ "url": format!("{target}{joiner}{}={ticket}", auth::TICKET) }))
    }

    /// `RequestHandler._static`: the built website, `index.html` for app routes.
    async fn serve_static(&self, path: &str, head: bool) -> Result<Response<Body>, Refusal> {
        if path.split('/').any(|part| part.starts_with('.')) {
            return Err(refuse(404, "Not found"));
        }
        let root = &self.static_dir;
        let inside = |candidate: &Path| candidate.starts_with(root);
        let mut target = tokio::fs::canonicalize(root.join(path.trim_start_matches('/'))).await.ok();
        if target.as_deref().is_some_and(|found| !inside(found)) {
            return Err(refuse(404, "Not found"));
        }
        let is_file = match &target {
            Some(found) => tokio::fs::metadata(found).await.map(|meta| meta.is_file()).unwrap_or(false),
            None => false,
        };
        if !is_file && (path == "/" || Path::new(path).extension().is_none()) {
            target = tokio::fs::canonicalize(root.join("index.html")).await.ok();
        }
        // A file the website lacks may come from an installed pack (its web chunks).
        let target = match target.filter(|found| inside(found)) {
            Some(found) => found,
            None => self.packs.web_file(path).ok_or_else(|| refuse(404, "Website not built; run npm run build in web/"))?,
        };
        let extension = target.extension().and_then(|ext| ext.to_str()).unwrap_or("").to_ascii_lowercase();
        let kind = STATIC_TYPES.iter().find(|(ext, _)| *ext == extension).map(|(_, kind)| *kind).ok_or_else(|| refuse(404, "Not found"))?;
        let data = tokio::fs::read(&target).await.map_err(|_| refuse(404, "Website not built; run npm run build in web/"))?;
        Ok(send(StatusCode::OK, data, kind, head))
    }
}

/// `RequestHandler._body`'s framing rules: one Content-Length, no Transfer-Encoding.
pub(crate) fn content_length(headers: &HeaderMap) -> Result<u64, Refusal> {
    let lengths = all(headers, "content-length");
    if headers.contains_key("transfer-encoding") || lengths.len() != 1 {
        return Err(refuse(400, "A single Content-Length is required"));
    }
    let value = lengths[0];
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(refuse(400, "Content-Length must be an integer"));
    }
    let length: u64 = value.parse().expect("ten digits fit");
    if !(1..=MAX_BODY).contains(&length) {
        return Err(refuse(400, format!("Content-Length must be between 1 and {MAX_BODY}")));
    }
    Ok(length)
}

pub(crate) async fn read_body(body: Incoming, length: u64) -> Result<Bytes, Refusal> {
    let collected = Limited::new(body, length as usize).collect().await.map_err(|_| refuse(400, "Incomplete JSON body"))?.to_bytes();
    if collected.len() as u64 != length {
        return Err(refuse(400, "Incomplete JSON body"));
    }
    Ok(collected)
}

/// A JSON value that refuses repeated object keys, as `_body`'s `unique_pairs` does.
struct Strict(Value);

impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct StrictVisitor;
        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = Value;
            fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
                formatter.write_str("JSON")
            }
            fn visit_bool<E>(self, value: bool) -> Result<Value, E> { Ok(Value::Bool(value)) }
            fn visit_i64<E>(self, value: i64) -> Result<Value, E> { Ok(value.into()) }
            fn visit_u64<E>(self, value: u64) -> Result<Value, E> { Ok(value.into()) }
            fn visit_f64<E>(self, value: f64) -> Result<Value, E> { Ok(value.into()) }
            fn visit_str<E>(self, value: &str) -> Result<Value, E> { Ok(value.into()) }
            fn visit_string<E>(self, value: String) -> Result<Value, E> { Ok(value.into()) }
            fn visit_unit<E>(self) -> Result<Value, E> { Ok(Value::Null) }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
                let mut items = Vec::new();
                while let Some(Strict(item)) = seq.next_element()? {
                    items.push(item);
                }
                Ok(Value::Array(items))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
                let mut object = Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if object.contains_key(&key) {
                        return Err(de::Error::custom("Repeated JSON key"));
                    }
                    let Strict(value) = map.next_value()?;
                    object.insert(key, value);
                }
                Ok(Value::Object(object))
            }
        }
        deserializer.deserialize_any(StrictVisitor).map(Strict)
    }
}

pub(crate) fn json_object(raw: &[u8]) -> Result<Map<String, Value>, Refusal> {
    match serde_json::from_slice::<Strict>(raw) {
        Ok(Strict(Value::Object(object))) => Ok(object),
        Ok(_) => Err(refuse(400, "JSON body must be an object")),
        Err(_) => Err(refuse(400, "Malformed JSON body")),
    }
}

/// What a forwarded request is, which decides the headers the worker gets.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    /// `/api/ingress/*`: its own bearer tokens and CORS; the page's Origin passes through.
    Ingress,
    /// Any other `/api` request: the worker's UI secret instead of the front's.
    Api,
    /// A WebSocket upgrade: the worker's secret as the subprotocol.
    Upgrade,
}

impl Front {
    pub async fn handle(self: Arc<Self>, peer_port: u16, request: Request<Incoming>) -> Result<Response<Body>, Infallible> {
        let head = request.method() == Method::HEAD;
        let mut response = match self.dispatch(peer_port, request).await {
            Ok(response) => response,
            Err(refusal) => failure(refusal, head),
        };
        // Each route's answer carries the fixed policy; the framed module ports are this launch's.
        if let Some(policy) = response.headers_mut().get_mut("content-security-policy") {
            *policy = self.csp.clone();
        }
        Ok(response)
    }

    /// Python's `modules_state`: the built-in selection plus the worker's external modules,
    /// with a restart also required while an install, update or removal waits for one.
    async fn add_external(&self, state: &mut Value) -> Result<(), Refusal> {
        let busy = || refuse(503, "Local workspace is busy or unavailable; try again");
        let request = Request::builder()
            .method(Method::GET)
            .uri(EXTERNAL_PATH)
            .header("host", &self.backend.authority)
            .header("x-kitty-secret", &self.backend.secret)
            .header("x-kitty-front", &self.backend.front_secret)
            .body(empty())
            .map_err(|_| busy())?;
        let mut sender = self.connect(false).await?;
        let response = sender.send_request(request).await.map_err(|_| busy())?;
        let status = response.status();
        let data = response.into_body().collect().await.map_err(|_| busy())?.to_bytes();
        let answer: Value = serde_json::from_slice(&data).map_err(|_| busy())?;
        if status != StatusCode::OK {
            let message = answer.get("error").and_then(Value::as_str).unwrap_or("Local operation failed; inspect CLI diagnostics");
            return Err(Refusal { status, message: message.to_owned(), headers: Vec::new() });
        }
        let (Some(external), Some(pending), Some(object)) = (answer.get("external"), answer.get("pending").and_then(Value::as_bool), state.as_object_mut()) else {
            return Err(refuse(500, "Local operation failed; inspect CLI diagnostics"));
        };
        object.insert("external".into(), external.clone());
        let restart = object.get("restart_required").and_then(Value::as_bool).unwrap_or(false) || pending;
        object.insert("restart_required".into(), Value::Bool(restart));
        Ok(())
    }

    async fn dispatch(&self, peer_port: u16, mut request: Request<Incoming>) -> Result<Response<Body>, Refusal> {
        let raw = request.uri().path_and_query().map(|value| value.as_str().to_owned()).unwrap_or_else(|| request.uri().path().to_owned());
        if is_ingress(&raw) {
            return self.forward(request, raw, Kind::Ingress, peer_port).await;
        }
        let method = request.method().clone();
        let mutation = match method {
            Method::GET | Method::HEAD => false,
            Method::POST => true,
            Method::OPTIONS => return Err(refuse(405, "Cross-origin preflight is not supported")),
            _ => return Err(refuse(501, "Unsupported method")),
        };
        self.security(request.headers(), mutation)?;
        let target = parse_target(&raw)?;
        let upgrade = method == Method::GET && request.headers().contains_key("upgrade");
        let mut forward_target = raw.clone();
        if is_api(&target.path) && !upgrade {
            if let Some(query) = self.authorize(request.headers(), &method, &target, mutation)? {
                let path = raw.split('?').next().unwrap_or("");
                forward_target = if query.is_empty() { path.to_owned() } else { format!("{path}?{query}") };
            }
        }
        if mutation && (target.path == auth::SESSION_PATH || target.path == auth::LINK_PATH) {
            if !target.query.is_empty() {
                return Err(refuse(400, "This route does not accept query parameters"));
            }
            let length = content_length(request.headers())?;
            let body = json_object(&read_body(request.into_body(), length).await?)?;
            return Ok(send_json(StatusCode::OK, &self.session(&target.path, body)?, false));
        }
        if target.path == crate::bootstrap::PATH && !mutation && !upgrade {
            // The query as the worker would have seen it: a spent link ticket removed.
            if !parse_target(&forward_target)?.query.is_empty() {
                return Err(refuse(400, "This route does not accept query parameters"));
            }
            let mut answer = tokio::task::block_in_place(|| self.bootstrap.answer(&self.workspace))?;
            if let Some(state) = answer.get_mut("modules") {
                self.add_external(state).await?;
            }
            return Ok(send_json(StatusCode::OK, &answer, method == Method::HEAD));
        }
        if target.path == modules::PATH && !upgrade {
            if !target.query.is_empty() {
                return Err(refuse(400, "This route does not accept query parameters"));
            }
            let mut state = if mutation {
                let length = content_length(request.headers())?;
                let raw = read_body(request.into_body(), length).await?;
                json_object(&raw)?;
                tokio::task::block_in_place(|| self.workspace.configure(&raw, |pack| self.packs.ready(pack)))?
            } else {
                tokio::task::block_in_place(|| self.workspace.state())?
            };
            self.add_external(&mut state).await?;
            return Ok(send_json(StatusCode::OK, &state, method == Method::HEAD));
        }
        if (target.path == packs::PATH || target.path.starts_with("/api/packs/")) && !upgrade {
            if !target.query.is_empty() {
                return Err(refuse(400, "This route does not accept query parameters"));
            }
            let listing = match (mutation, target.path.as_str()) {
                (false, packs::PATH) => self.packs.listing().await,
                (true, packs::INSTALL_PATH | packs::UNINSTALL_PATH) => {
                    let length = content_length(request.headers())?;
                    let body = json_object(&read_body(request.into_body(), length).await?)?;
                    if target.path == packs::INSTALL_PATH {
                        self.packs.install(&body).await?
                    } else {
                        let configured = tokio::task::block_in_place(|| self.workspace.configured());
                        self.packs.uninstall(&body, &configured).await?
                    }
                }
                _ => return Err(refuse(404, "Not found")),
            };
            return Ok(send_json(StatusCode::OK, &listing, method == Method::HEAD));
        }
        if let Some(response) = crate::keys::route(self, &target, &forward_target, &mut request, mutation, upgrade).await { return Ok(response); }
        if let Some(response) = crate::effects::route(self, &target, &forward_target, &mut request, mutation, upgrade).await { return Ok(response); }
        if let Some(response) = crate::live::route(self, &target, &forward_target, &mut request, mutation, upgrade).await { return Ok(response); }
        if let Some(response) = crate::builds::route(self, &target, &forward_target, &mut request, mutation, upgrade).await { return Ok(response); }
        if let Some(answer) = crate::math::route(self, &target, &forward_target, &mut request, mutation, upgrade).await { return answer; }
        if !mutation && !upgrade && crate::search::owns(&target.path) {
            return Ok(send_json(StatusCode::OK, &crate::search::answer(self.bootstrap.db_path(), &self.workspace, &forward_target).await?, method == Method::HEAD));
        }
        if !mutation && !upgrade && crate::documents::original_id(&target.path).is_some() {
            let target = parse_target(&forward_target)?;
            let (data, name) = tokio::task::block_in_place(|| crate::documents::download(&self.workspace, &self.bootstrap, &target))?;
            let mut response = send(StatusCode::OK, data, "application/octet-stream", method == Method::HEAD);
            response.headers_mut().insert("content-disposition", HeaderValue::from_str(&format!("attachment; filename=\"{name}\"")).expect("ASCII download name"));
            return Ok(response);
        }
        // The module routes ported to Rust over the workspace database (crate::ported).
        if !upgrade && crate::ported::owns(&target.path, mutation) {
            let target = parse_target(&forward_target)?;
            let body = if mutation { crate::ported::no_query(&target)?; let length = content_length(request.headers())?; Some(json_object(&read_body(request.into_body(), length).await?)?) } else { None };
            let answer = tokio::task::block_in_place(|| crate::ported::answer(&self.workspace, &self.bootstrap, &target, body))?;
            if mutation {
                // `WebApplication.mutated`: windows get a committed block change at once.
                crate::live::nudge();
            }
            return Ok(send_json(StatusCode::OK, &answer, method == Method::HEAD));
        }
        if mutation {
            // Native desktop commands additionally require an explicit page origin.
            if target.path == "/api/desktop/command" && !request.headers().contains_key("origin") {
                return Err(refuse(403, "Missing local request origin"));
            }
            return self.forward(request, forward_target, Kind::Api, peer_port).await;
        }
        if upgrade {
            if all(request.headers(), "origin") != [self.origin.as_str()] {
                return Err(refuse(403, "Cross-origin requests are not allowed"));
            }
            if !self.auth.protocol_secret(all(request.headers(), "sec-websocket-protocol")) {
                return Err(unauthorized());
            }
            return self.forward_upgrade(&mut request, forward_target, peer_port).await;
        }
        if is_api(&target.path) || target.path.starts_with(MODULE_ASSETS) {
            // An external module's element script: the worker checks `?retry=<n>` and the file.
            return self.forward(request, forward_target, Kind::Api, peer_port).await;
        }
        // `?safe` opens one window in safe mode and `?code` is the one-time code: the page reads both.
        if target.query.iter().any(|(name, _)| name != "safe" && name != "code") {
            return Err(refuse(400, "This route does not accept query parameters"));
        }
        self.serve_static(&target.path, method == Method::HEAD).await
    }

    /// The request as the worker must see it: its Host, Origin and secrets; the front's dropped.
    fn backend_request(&self, request: &Request<Incoming>, target: &str, kind: Kind, peer_port: u16, body: Body) -> Result<Request<Body>, Refusal> {
        let backend = &self.backend;
        let mut forwarded = Request::builder().method(request.method()).uri(target);
        let headers = forwarded.headers_mut().expect("a fresh builder has headers");
        for (name, value) in request.headers() {
            let lower = name.as_str();
            let ours = HOP_BY_HOP.contains(&lower) || FRONT_ONLY.contains(&lower)
                || matches!(lower, "host" | "content-length" | "x-kitty-secret" | "sec-websocket-protocol")
                || (lower == "origin" && kind != Kind::Ingress);
            if !ours {
                headers.append(name.clone(), value.clone());
            }
        }
        let set = |headers: &mut HeaderMap, name: &'static str, value: &str| {
            headers.insert(HeaderName::from_static(name), HeaderValue::from_str(value).expect("loopback values are valid headers"));
        };
        set(headers, "host", &backend.authority);
        if kind != Kind::Ingress && request.headers().contains_key("origin") {
            set(headers, "origin", &backend.origin);
        }
        match kind {
            Kind::Api => set(headers, "x-kitty-secret", &backend.secret),
            Kind::Upgrade => {
                set(headers, "sec-websocket-protocol", &format!("{}, {}{}", auth::PROTOCOL, auth::PROTOCOL_PREFIX, backend.secret));
                set(headers, "connection", "Upgrade");
                if let Some(upgrade) = request.headers().get("upgrade") {
                    headers.insert("upgrade", upgrade.clone());
                }
            }
            Kind::Ingress => {}
        }
        set(headers, "x-kitty-front", &backend.front_secret);
        set(headers, "x-kitty-peer-port", &peer_port.to_string());
        set(headers, "x-kitty-front-port", &self.port.to_string());
        forwarded.body(body).map_err(|_| refuse(400, "Invalid request target"))
    }

    async fn connect(&self, upgrades: bool) -> Result<hyper::client::conn::http1::SendRequest<Body>, Refusal> {
        let busy = || refuse(503, "Local workspace is busy or unavailable; try again");
        let stream = TcpStream::connect(&self.backend.authority).await.map_err(|_| busy())?;
        let (sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|_| busy())?;
        tokio::spawn(async move {
            let _ = if upgrades { connection.with_upgrades().await } else { connection.await };
        });
        Ok(sender)
    }

    async fn forward(&self, request: Request<Incoming>, target: String, kind: Kind, peer_port: u16) -> Result<Response<Body>, Refusal> {
        let has_body = request.method() == Method::POST;
        let (body, length) = if has_body {
            let length = content_length(request.headers())?;
            let forwarded = self.backend_request(&request, &target, kind, peer_port, empty())?;
            let data = read_body(request.into_body(), length).await?;
            let (parts, _) = forwarded.into_parts();
            (Request::from_parts(parts, full(data)), Some(length))
        } else {
            (self.backend_request(&request, &target, kind, peer_port, empty())?, None)
        };
        let mut body = body;
        if let Some(length) = length {
            body.headers_mut().insert("content-length", HeaderValue::from(length));
        }
        let mut sender = self.connect(false).await?;
        let response = sender.send_request(body).await.map_err(|_| refuse(503, "Local workspace is busy or unavailable; try again"))?;
        let (mut parts, incoming) = response.into_parts();
        for name in HOP_BY_HOP {
            parts.headers.remove(*name);
        }
        Ok(Response::from_parts(parts, incoming.boxed()))
    }

    async fn forward_upgrade(&self, request: &mut Request<Incoming>, target: String, peer_port: u16) -> Result<Response<Body>, Refusal> {
        let forwarded = self.backend_request(request, &target, Kind::Upgrade, peer_port, empty())?;
        let mut sender = self.connect(true).await?;
        let mut response = sender.send_request(forwarded).await.map_err(|_| refuse(503, "Local workspace is busy or unavailable; try again"))?;
        if response.status() != StatusCode::SWITCHING_PROTOCOLS {
            let (mut parts, incoming) = response.into_parts();
            for name in HOP_BY_HOP {
                parts.headers.remove(*name);
            }
            return Ok(Response::from_parts(parts, incoming.boxed()));
        }
        let backend_side = hyper::upgrade::on(&mut response);
        let page_side = hyper::upgrade::on(request);
        tokio::spawn(async move {
            if let (Ok(page), Ok(backend)) = (page_side.await, backend_side.await) {
                let _ = tokio::io::copy_bidirectional(&mut TokioIo::new(page), &mut TokioIo::new(backend)).await;
            }
        });
        let mut answer = Response::new(empty());
        *answer.status_mut() = StatusCode::SWITCHING_PROTOCOLS;
        for (name, value) in response.headers() {
            answer.headers_mut().append(name.clone(), value.clone());
        }
        Ok(answer)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bodies_are_json_objects_with_unique_keys() {
        assert!(json_object(br#"{"code": "x"}"#).is_ok());
        assert_eq!(json_object(br#"{"a": 1, "a": 2}"#).err().map(|r| r.message), Some("Malformed JSON body".into()));
        assert_eq!(json_object(br#"{"a": {"b": 1, "b": 1}}"#).err().map(|r| r.status.as_u16()), Some(400));
        assert_eq!(json_object(b"[1]").err().map(|r| r.message), Some("JSON body must be an object".into()));
        assert_eq!(json_object(b"{\"a\": NaN}").err().map(|r| r.message), Some("Malformed JSON body".into()));
        assert!(json_object(b"\xff").is_err());
    }

    #[test]
    fn only_one_plain_content_length_frames_a_body() {
        let mut headers = HeaderMap::new();
        assert!(content_length(&headers).is_err());
        headers.insert("content-length", HeaderValue::from_static("12"));
        assert_eq!(content_length(&headers).ok(), Some(12));
        headers.insert("transfer-encoding", HeaderValue::from_static("chunked"));
        assert!(content_length(&headers).is_err());
        let mut headers = HeaderMap::new();
        headers.insert("content-length", HeaderValue::from_static("+5"));
        assert_eq!(content_length(&headers).err().map(|r| r.message), Some("Content-Length must be an integer".into()));
        headers.insert("content-length", HeaderValue::from_static("0"));
        assert!(content_length(&headers).is_err());
        headers.insert("content-length", HeaderValue::from(MAX_BODY + 1));
        assert!(content_length(&headers).is_err());
    }

    #[test]
    fn the_ingress_is_its_own_prefix() {
        assert!(is_ingress("/api/ingress"));
        assert!(is_ingress("/api/ingress/agent/call?x=1"));
        assert!(!is_ingress("/api/ingressx"));
        assert!(!is_ingress("/api/notes"));
    }
}
