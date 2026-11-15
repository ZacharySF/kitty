//! The front's calls to the worker's internal routes (`kitty.web_effects_worker`): a POST
//! exactly like a forwarded mutation (the worker's Host, UI secret and front secret,
//! `X-Kitty-Request`, JSON with one Content-Length), so the worker runs it under its
//! mutation lock.

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::{Method, Request};
use hyper_util::rt::TokioIo;
use serde_json::Value;
use tokio::net::TcpStream;

use super::{fail, internal_error, Failure};
use crate::front::Backend;

/// POST `body` to the worker's `path`: its JSON answer, or its error as the page would
/// have received it from Python (status, message and the changed preconditions).
pub async fn call(backend: &Backend, path: &str, body: &Value) -> Result<Value, Failure> {
    let busy = || fail(503, "Local workspace is busy or unavailable; try again");
    let data = serde_json::to_vec(body).expect("JSON values serialize");
    let request = Request::builder()
        .method(Method::POST)
        .uri(path)
        .header("host", &backend.authority)
        .header("x-kitty-secret", &backend.secret)
        .header("x-kitty-front", &backend.front_secret)
        .header("x-kitty-request", "1")
        .header("content-type", "application/json")
        .header("content-length", data.len())
        .body(Full::new(Bytes::from(data)))
        .map_err(|_| internal_error())?;
    let stream = TcpStream::connect(&backend.authority).await.map_err(|_| busy())?;
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|_| busy())?;
    tokio::spawn(async move {
        let _ = connection.await;
    });
    let response = sender.send_request(request).await.map_err(|_| busy())?;
    let status = response.status().as_u16();
    let data = response.into_body().collect().await.map_err(|_| busy())?.to_bytes();
    let answer: Value = serde_json::from_slice(&data).map_err(|_| internal_error())?;
    if status == 200 {
        return Ok(answer);
    }
    let message = answer.get("error").and_then(Value::as_str).ok_or_else(internal_error)?;
    let changed = answer
        .get("problems")
        .and_then(Value::as_array)
        .map(|problems| problems.iter().filter_map(|problem| problem.get("precondition")?.as_str().map(str::to_owned)).collect())
        .unwrap_or_default();
    Err(Failure { status, message: message.to_owned(), changed })
}
