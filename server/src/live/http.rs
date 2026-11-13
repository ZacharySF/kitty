//! Answers in the front's own form (front.rs `send`, `failure`, `_body`'s framing): the same
//! security headers, `{"error"}` refusals that close the connection, and JSON bodies read
//! with Python's rules.

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

pub fn empty() -> Body {
    Empty::new().map_err(|never: Infallible| match never {}).boxed()
}

/// An answer with the headers `RequestHandler._send` puts on everything.
pub fn send(status: StatusCode, data: Vec<u8>, content_type: &str, head: bool) -> Response<Body> {
    let length = data.len();
    let body = if head { empty() } else { Full::new(Bytes::from(data)).map_err(|never: Infallible| match never {}).boxed() };
    let mut response = Response::new(body);
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

pub fn send_json(value: &Value, head: bool) -> Response<Body> {
    send(StatusCode::OK, value.to_string().into_bytes(), "application/json; charset=utf-8", head)
}

/// A refusal: status and message.
#[derive(Debug)]
pub struct Refusal(pub u16, pub String);

pub fn refuse(status: u16, message: impl Into<String>) -> Refusal {
    Refusal(status, message.into())
}

/// An error answer; like Python's, it closes the connection.
pub fn failure(Refusal(status, message): Refusal, head: bool) -> Response<Body> {
    let status = StatusCode::from_u16(status).expect("a valid status");
    let mut response = send(status, json!({ "error": message }).to_string().into_bytes(), "application/json; charset=utf-8", head);
    response.headers_mut().insert("connection", HeaderValue::from_static("close"));
    if status == StatusCode::UNAUTHORIZED {
        response.headers_mut().insert("www-authenticate", HeaderValue::from_static("Kitty realm=\"rika!\""));
    }
    response
}

/// Every value of one header; a value that is not visible ASCII never matches anything.
pub fn all<'a>(headers: &'a HeaderMap, name: &str) -> Vec<&'a str> {
    headers.get_all(name).iter().map(|value| value.to_str().unwrap_or("\u{fffd}")).collect()
}

/// `RequestHandler._body`: one plain Content-Length, a complete body, one JSON object with
/// unique keys and finite numbers.
pub async fn body(headers: &HeaderMap, body: &mut Incoming) -> Result<Map<String, Value>, Refusal> {
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
    let raw = Limited::new(body, length as usize).collect().await.map_err(|_| refuse(400, "Incomplete JSON body"))?.to_bytes();
    if raw.len() as u64 != length {
        return Err(refuse(400, "Incomplete JSON body"));
    }
    match serde_json::from_slice::<Strict>(&raw) {
        Ok(Strict(Value::Object(object))) => Ok(object),
        Ok(_) => Err(refuse(400, "JSON body must be an object")),
        Err(_) => Err(refuse(400, "Malformed JSON body")),
    }
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
