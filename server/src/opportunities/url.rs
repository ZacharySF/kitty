//! `kitty.core.opportunities.canonical_url` after its text check: CPython 3.12's
//! `urllib.parse` (`urlsplit` with its bracket and NFKC checks, the netloc's user, host and
//! port, `parse_qsl`, `urlencode`) and `ipaddress` (what a bracketed host may be), so a URL
//! is refused or rewritten exactly as Python does it. tests/test_opportunities_parity.py
//! compares a table of awkward URLs with Python's answers.

/// `fail('Use a valid public URL')`: `urlsplit` or `.port` raised `ValueError`.
pub const INVALID: &str = "Use a valid public URL";
/// The scheme, host or credentials refusal.
pub const NOT_PUBLIC: &str = "Use a public http or https URL without credentials";

/// Every character whose NFKC form holds one of `/?#@:` (Unicode 15.0, Python 3.12's):
/// what `_checknetloc` refuses in a host. No composition takes one of those five back.
const NFKC_DELIMITERS: [char; 19] = [
    '\u{2047}', '\u{2048}', '\u{2049}', '\u{2100}', '\u{2101}', '\u{2105}', '\u{2106}', '\u{2a74}', '\u{fe13}', '\u{fe16}',
    '\u{fe55}', '\u{fe56}', '\u{fe5f}', '\u{fe6b}', '\u{ff03}', '\u{ff0f}', '\u{ff1a}', '\u{ff1f}', '\u{ff20}',
];

struct Split {
    scheme: String,
    netloc: String,
    path: String,
    query: String,
}

/// `_BaseV4._ip_int_from_string`.
fn ipv4(text: &str) -> Option<u32> {
    let octets: Vec<&str> = text.split('.').collect();
    if octets.len() != 4 {
        return None;
    }
    let mut address = 0u32;
    for octet in octets {
        let digits = !octet.is_empty() && octet.len() <= 3 && octet.bytes().all(|byte| byte.is_ascii_digit());
        if !digits || (octet != "0" && octet.starts_with('0')) {
            return None;
        }
        let value: u32 = octet.parse().ok().filter(|value| *value <= 255)?;
        address = address << 8 | value;
    }
    Some(address)
}

/// Whether `ipaddress.IPv6Address(text)` accepts it (a scope id included).
fn ipv6(text: &str) -> bool {
    if text.contains('/') {
        return false;
    }
    let address = match text.split_once('%') {
        None => text,
        Some((_, scope)) if scope.is_empty() || scope.contains('%') => return false,
        Some((address, _)) => address,
    };
    if address.is_empty() || address.chars().count() > 45 {
        return false;
    }
    let mut parts: Vec<String> = address.splitn(10, ':').map(str::to_owned).collect();
    if parts.len() < 3 {
        return false;
    }
    if parts.last().is_some_and(|last| last.contains('.')) {
        let Some(suffix) = parts.pop().and_then(|last| ipv4(&last)) else { return false };
        parts.push(format!("{:x}", suffix >> 16));
        parts.push(format!("{:x}", suffix & 0xffff));
    }
    if parts.len() > 9 {
        return false;
    }
    let last = parts.len() - 1;
    let mut skip = None;
    for index in 1..last {
        if parts[index].is_empty() {
            if skip.is_some() {
                return false;
            }
            skip = Some(index);
        }
    }
    let (high, low) = match skip {
        Some(index) => {
            let (mut high, mut low) = (index, last - index);
            if parts[0].is_empty() {
                high -= 1;
                if high != 0 {
                    return false;
                }
            }
            if parts[last].is_empty() {
                low -= 1;
                if low != 0 {
                    return false;
                }
            }
            if high + low > 7 {
                return false;
            }
            (high, low)
        }
        None => {
            if parts.len() != 8 || parts[0].is_empty() || parts[last].is_empty() {
                return false;
            }
            (8, 0)
        }
    };
    let hextet = |part: &String| !part.is_empty() && part.len() <= 4 && part.bytes().all(|byte| byte.is_ascii_hexdigit());
    parts[..high].iter().chain(&parts[parts.len() - low..]).all(hextet)
}

/// `_check_bracketed_host`: an IPvFuture literal or an IPv6 address.
fn bracketed_host(hostname: &str) -> bool {
    match hostname.strip_prefix('v') {
        // `\Av[a-fA-F0-9]+\..+\Z`
        Some(rest) => {
            let digits = rest.bytes().take_while(u8::is_ascii_hexdigit).count();
            digits > 0 && rest[digits..].starts_with('.') && rest.len() > digits + 1
        }
        None => ipv6(hostname),
    }
}

/// `_check_bracketed_netloc`, run when the netloc holds both brackets anywhere.
fn bracketed_netloc(netloc: &str) -> bool {
    let host_port = netloc.rsplit_once('@').map_or(netloc, |(_, after)| after);
    let hostname = match host_port.split_once('[') {
        Some((before, bracketed)) => {
            let (hostname, port) = bracketed.split_once(']').unwrap_or((bracketed, ""));
            if !before.is_empty() || !(port.is_empty() || port.starts_with(':')) {
                return false;
            }
            hostname
        }
        None => host_port.split_once(':').map_or(host_port, |(hostname, _)| hostname),
    };
    bracketed_host(hostname)
}

/// `urlsplit(url)` without its fragment; None is its `ValueError`.
fn urlsplit(url: &str) -> Option<Split> {
    let url: String = url.trim_start_matches(|c: char| c <= ' ').chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
    let mut scheme = String::new();
    let mut rest = url.as_str();
    if let Some(colon) = url.find(':') {
        let named = url.starts_with(|c: char| c.is_ascii_alphabetic())
            && url[..colon].chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'));
        if named {
            scheme = url[..colon].to_ascii_lowercase();
            rest = &url[colon + 1..];
        }
    }
    let mut netloc = "";
    if let Some(after) = rest.strip_prefix("//") {
        let end = after.find(['/', '?', '#']).unwrap_or(after.len());
        (netloc, rest) = after.split_at(end);
        let (open, close) = (netloc.contains('['), netloc.contains(']'));
        if open != close || (open && !bracketed_netloc(netloc)) {
            return None;
        }
    }
    let rest = rest.split_once('#').map_or(rest, |(before, _)| before);
    let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
    // `_checknetloc`.
    if !netloc.is_ascii() && netloc.chars().any(|c| NFKC_DELIMITERS.contains(&c)) {
        return None;
    }
    Some(Split { scheme, netloc: netloc.to_owned(), path: path.to_owned(), query: query.to_owned() })
}

/// `_hostinfo`: the host name and the port's text.
fn hostinfo(netloc: &str) -> (&str, &str) {
    let hostinfo = netloc.rsplit_once('@').map_or(netloc, |(_, after)| after);
    match hostinfo.split_once('[') {
        Some((_, bracketed)) => {
            let (hostname, port) = bracketed.split_once(']').unwrap_or((bracketed, ""));
            (hostname, port.split_once(':').map_or("", |(_, port)| port))
        }
        None => hostinfo.split_once(':').unwrap_or((hostinfo, "")),
    }
}

/// `.port` does not raise: no port, or ASCII digits naming 0 to 65535.
fn port_reads(port: &str) -> bool {
    port.bytes().all(|byte| byte.is_ascii_digit()) && {
        let digits = port.trim_start_matches('0');
        digits.len() <= 5 && digits.parse::<u32>().map_or(digits.is_empty(), |value| value <= 65535)
    }
}

/// `.username or .password`.
fn credentials(netloc: &str) -> bool {
    netloc.rsplit_once('@').is_some_and(|(userinfo, _)| {
        let (username, password) = userinfo.split_once(':').unwrap_or((userinfo, ""));
        !username.is_empty() || !password.is_empty()
    })
}

/// `unquote_plus(text)`: each run of ASCII is unescaped to bytes and read as UTF-8, a wrong
/// sequence as U+FFFD.
fn unquote_plus(text: &str) -> String {
    let text = text.replace('+', " ");
    if !text.contains('%') {
        return text;
    }
    let hex = |byte: Option<&u8>| byte.and_then(|byte| (*byte as char).to_digit(16));
    let mut out = String::with_capacity(text.len());
    let mut rest = text.as_str();
    while !rest.is_empty() {
        let (run, tail) = rest.split_at(rest.bytes().take_while(u8::is_ascii).count());
        let run = run.as_bytes();
        let mut bytes = Vec::with_capacity(run.len());
        let mut at = 0;
        while at < run.len() {
            match (run[at], hex(run.get(at + 1)), hex(run.get(at + 2))) {
                (b'%', Some(high), Some(low)) => {
                    bytes.push((high * 16 + low) as u8);
                    at += 3;
                }
                (byte, _, _) => {
                    bytes.push(byte);
                    at += 1;
                }
            }
        }
        out.push_str(&String::from_utf8_lossy(&bytes));
        let (kept, next) = tail.split_at(tail.find(|c: char| c.is_ascii()).unwrap_or(tail.len()));
        out.push_str(kept);
        rest = next;
    }
    out
}

/// `quote_plus(text)` with nothing safe but letters, digits and `_.-~`.
fn quote_plus(out: &mut String, text: &str) {
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(byte as char),
            b' ' => out.push('+'),
            byte => out.push_str(&format!("%{byte:02X}")),
        }
    }
}

/// The canonical form of a stripped, non-empty URL, or the refusal's message.
pub fn canonical(value: &str) -> Result<String, &'static str> {
    let parts = urlsplit(value).ok_or(INVALID)?;
    let (hostname, port) = hostinfo(&parts.netloc);
    if !port_reads(port) {
        return Err(INVALID);
    }
    if !matches!(parts.scheme.as_str(), "http" | "https") || hostname.is_empty() || credentials(&parts.netloc) {
        return Err(NOT_PUBLIC);
    }
    // `parse_qsl(query, keep_blank_values=True)` without the tracking fields, sorted.
    let mut query: Vec<(String, String)> = Vec::new();
    for field in parts.query.split('&').filter(|field| !field.is_empty()) {
        let (name, value) = field.split_once('=').unwrap_or((field, ""));
        let name = unquote_plus(name);
        let lower = name.to_lowercase();
        if !lower.starts_with("utm_") && !matches!(lower.as_str(), "fbclid" | "gclid") {
            query.push((name, unquote_plus(value)));
        }
    }
    query.sort();
    let path = parts.path.trim_end_matches('/');
    let mut out = format!("https://{}{}", parts.netloc.to_lowercase(), if path.is_empty() { "/" } else { path });
    for (index, (name, value)) in query.iter().enumerate() {
        out.push(if index == 0 { '?' } else { '&' });
        quote_plus(&mut out, name);
        out.push('=');
        quote_plus(&mut out, value);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// tests/fixtures/opportunity_urls.json holds Python 3.12's answers; tests/test_opportunities_parity.py
    /// checks that it still does, and sends the same table through the front.
    #[test]
    fn urls_are_canonical_as_python_makes_them() {
        let table: Vec<(String, bool, String)> = serde_json::from_str(include_str!("../../../tests/fixtures/opportunity_urls.json")).expect("the table");
        assert!(table.len() > 100);
        for (url, accepted, answer) in table {
            let expected = if accepted { Ok(answer) } else { Err(answer) };
            assert_eq!(canonical(&url).map_err(str::to_owned), expected, "{url:?}");
        }
    }

    #[test]
    fn bracketed_hosts_are_ipv6_as_ipaddress_reads_it() {
        for host in ["::1", "::", "1::", "2001:db8::8a2e:370:7334", "1:2:3:4:5:6:7:8", "::ffff:192.0.2.1", "fe80::1%eth0", "1::2:3:4:5:6:7", "v1.x", "vFF.a:b"] {
            assert!(bracketed_host(host), "{host}");
        }
        for host in ["", "1", "1.2.3.4", ":::", "1:2:3:4:5:6:7", "1:2:3:4:5:6:7:8:9", "1::2::3", ":1::2", "1::2:", "12345::", "g::1", "::1%", "::1%a%b",
                     "::ffff:192.0.2.01", "::ffff:192.0.2.256", "1:2:3:4:5:6:7::8", "v.x", "v1", "v1.", "vg.x", "::1/64"] {
            assert!(!bracketed_host(host), "{host}");
        }
    }
}
