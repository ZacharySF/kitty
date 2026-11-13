//! The front's answer and body helpers (crate::front `send`, `failure`, `content_length`,
//! `read_body`, `json_object`), as `RequestHandler._send`, `_json` and `_body` behave.

use std::convert::Infallible;

use bytes::Bytes;
use http_body_util::{BodyExt, Empty, Full, Limited};
use hyper::body::Incoming;
use hyper::header::{HeaderMap, HeaderValue};
use hyper::{Response, StatusCode};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{json, Map, Value};

use crate::front::{Body, MAX_BODY};

const CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src http://127.0.0.1:3210; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

/// An `APIError`: status and message.
pub struct Refusal {
    status: StatusCode,
    message: String,
}

impl Refusal {
    pub fn new(status: u16, message: impl Into<String>) -> Self {
        Self { status: StatusCode::from_u16(status).expect("a valid status"), message: message.into() }
    }
}

pub fn send_json(status: StatusCode, value: &Value, head: bool) -> Response<Body> {
    let data = serde_json::to_vec(value).expect("JSON values serialize");
    let length = data.len();
    let body = if head {
        Empty::new().map_err(|never: Infallible| match never {}).boxed()
    } else {
        Full::new(Bytes::from(data)).map_err(|never: Infallible| match never {}).boxed()
    };
    let mut response = Response::new(body);
    *response.status_mut() = status;
    let headers = response.headers_mut();
    for (name, value) in [
        ("content-type", "application/json; charset=utf-8"),
        ("cache-control", "no-store"),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "no-referrer"),
        ("cross-origin-resource-policy", "same-origin"),
        ("content-security-policy", CSP),
        ("server", "rika!"),
    ] {
        headers.insert(name, HeaderValue::from_static(value));
    }
    headers.insert("content-length", HeaderValue::from(length));
    response
}

/// An error answer; like Python's, it closes the connection.
pub fn failure(refusal: Refusal, head: bool) -> Response<Body> {
    let mut response = send_json(refusal.status, &json!({ "error": refusal.message }), head);
    response.headers_mut().insert("connection", HeaderValue::from_static("close"));
    response
}

fn all<'a>(headers: &'a HeaderMap, name: &str) -> Vec<&'a str> {
    headers.get_all(name).iter().map(|value| value.to_str().unwrap_or("\u{fffd}")).collect()
}

/// `_body`'s framing rules: one Content-Length, no Transfer-Encoding.
pub fn content_length(headers: &HeaderMap) -> Result<u64, Refusal> {
    let lengths = all(headers, "content-length");
    if headers.contains_key("transfer-encoding") || lengths.len() != 1 {
        return Err(Refusal::new(400, "A single Content-Length is required"));
    }
    let value = lengths[0];
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Refusal::new(400, "Content-Length must be an integer"));
    }
    let length: u64 = value.parse().expect("ten digits fit");
    if !(1..=MAX_BODY).contains(&length) {
        return Err(Refusal::new(400, format!("Content-Length must be between 1 and {MAX_BODY}")));
    }
    Ok(length)
}

pub async fn read_body(body: &mut Incoming, length: u64) -> Result<Bytes, Refusal> {
    let collected = Limited::new(body, length as usize).collect().await.map_err(|_| Refusal::new(400, "Incomplete JSON body"))?.to_bytes();
    if collected.len() as u64 != length {
        return Err(Refusal::new(400, "Incomplete JSON body"));
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

pub fn json_object(raw: &[u8]) -> Result<Map<String, Value>, Refusal> {
    match serde_json::from_slice::<Strict>(raw) {
        Ok(Strict(Value::Object(object))) => Ok(object),
        Ok(_) => Err(Refusal::new(400, "JSON body must be an object")),
        Err(_) => Err(Refusal::new(400, "Malformed JSON body")),
    }
}
