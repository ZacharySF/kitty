//! Request targets, parsed exactly as `kitty.web_server.parse_target` parses them.

use percent_encoding::percent_decode_str;

/// A decoded path and its query (each key once, in arrival order).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Target {
    pub path: String,
    pub query: Vec<(String, String)>,
}

/// A request-target error: the status and message the Python server answers with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Invalid(pub u16, pub &'static str);

const MAX_TARGET: usize = 8192;
/// `parse_qs(..., max_num_fields=20)`.
const MAX_FIELDS: usize = 20;

impl Target {
    pub fn get(&self, key: &str) -> Option<&str> {
        self.query.iter().find(|(name, _)| name == key).map(|(_, value)| value.as_str())
    }

    /// The query without `key`, re-encoded (for a target whose ticket the front already spent).
    pub fn query_without(&self, key: &str) -> String {
        let mut out = form_urlencoded::Serializer::new(String::new());
        for (name, value) in self.query.iter().filter(|(name, _)| name != key) {
            out.append_pair(name, value);
        }
        out.finish()
    }

    /// One GET target as a link ticket is bound to it: the path and its sorted query.
    pub fn canonical(&self) -> String {
        let mut pairs = self.query.clone();
        pairs.sort();
        serde_json::to_string(&(&self.path, pairs)).expect("strings always serialize")
    }
}

/// Python's `unquote(text.replace('+', ' '), errors='replace')` for query fields.
fn query_part(text: &str) -> String {
    percent_decode_str(&text.replace('+', " ")).decode_utf8_lossy().into_owned()
}

pub fn parse_target(raw: &str) -> Result<Target, Invalid> {
    const BAD_TARGET: Invalid = Invalid(400, "Invalid request target");
    if raw.len() > MAX_TARGET || !raw.starts_with('/') || raw.starts_with("//") {
        return Err(BAD_TARGET);
    }
    let (rest, fragment) = raw.split_once('#').unwrap_or((raw, ""));
    if !fragment.is_empty() {
        return Err(BAD_TARGET);
    }
    let (raw_path, raw_query) = rest.split_once('?').unwrap_or((rest, ""));
    let fields = if raw_query.is_empty() { 0 } else { 1 + raw_query.matches('&').count() };
    if fields > MAX_FIELDS {
        return Err(BAD_TARGET);
    }
    let mut query: Vec<(String, String)> = Vec::new();
    for field in raw_query.split('&').filter(|field| !field.is_empty()) {
        let (name, value) = field.split_once('=').unwrap_or((field, ""));
        let name = query_part(name);
        if query.iter().any(|(seen, _)| *seen == name) {
            return Err(Invalid(400, "Repeated query parameters are not allowed"));
        }
        query.push((name, query_part(value)));
    }
    let path = percent_decode_str(raw_path).decode_utf8().map_err(|_| BAD_TARGET)?.into_owned();
    if path.contains('\0') || path.contains('\\') || path.split('/').any(|part| part == "." || part == "..") {
        return Err(Invalid(400, "Invalid request path"));
    }
    Ok(Target { path, query })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(raw: &str) -> Target {
        parse_target(raw).unwrap_or_else(|error| panic!("{raw}: {error:?}"))
    }

    #[test]
    fn paths_and_queries_decode_like_python() {
        let target = ok("/api/notes/caf%C3%A9?q=a+b%20c&empty=&flag");
        assert_eq!(target.path, "/api/notes/café");
        assert_eq!(target.query, [("q".into(), "a b c".into()), ("empty".into(), "".into()), ("flag".into(), "".into())]);
        assert_eq!(ok("/a?%zz=1").get("%zz"), Some("1"), "an invalid escape stays literal");
        assert_eq!(ok("/a?x=%FF").get("x"), Some("\u{fffd}"), "query bytes decode with replacement");
        assert_eq!(ok("/a?&&x=1&").query.len(), 1, "empty fields are skipped");
    }

    #[test]
    fn unsafe_targets_are_refused_with_pythons_messages() {
        for raw in ["api", "//evil/x", "/a#frag", "/caf%FF"] {
            assert_eq!(parse_target(raw), Err(Invalid(400, "Invalid request target")), "{raw}");
        }
        assert_eq!(parse_target(&format!("/{}", "a".repeat(8192))), Err(Invalid(400, "Invalid request target")));
        for raw in ["/a/../b", "/a/./b", "/%2e%2e/x", "/a%5Cb", "/a%00b"] {
            assert_eq!(parse_target(raw), Err(Invalid(400, "Invalid request path")), "{raw}");
        }
        assert_eq!(parse_target("/a?x=1&x=2"), Err(Invalid(400, "Repeated query parameters are not allowed")));
        let many = (0..21).map(|i| format!("k{i}=v")).collect::<Vec<_>>().join("&");
        assert!(parse_target(&format!("/a?{many}")).is_err(), "more than 20 fields");
        let twenty = (0..20).map(|i| format!("k{i}=v")).collect::<Vec<_>>().join("&");
        assert!(parse_target(&format!("/a?{twenty}")).is_ok());
    }

    #[test]
    fn the_canonical_form_ignores_query_order_and_drops_a_spent_ticket() {
        assert_eq!(ok("/api/x?b=2&a=1").canonical(), ok("/api/x?a=1&b=2").canonical());
        assert_ne!(ok("/api/x?a=1").canonical(), ok("/api/y?a=1").canonical());
        assert_eq!(ok("/api/x?a=1+2&ticket=t&b=%26").query_without("ticket"), "a=1+2&b=%26");
    }
}
