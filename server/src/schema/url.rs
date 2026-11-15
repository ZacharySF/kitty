//! `schema_maps._url`'s test of a link: what `urllib.parse.urlsplit` (Python 3.12) accepts as
//! an absolute HTTP or HTTPS URL with a host, no credentials and a port that is a number.

use std::net::Ipv6Addr;
use std::str::FromStr;

/// The characters whose NFKC form holds one of `/?#@:` (`urllib.parse._checknetloc`), from
/// `unicodedata` 15.0: a host may not hide a separator in them.
const SEPARATOR_LOOKALIKES: [u32; 19] = [
    0x2047, 0x2048, 0x2049, 0x2100, 0x2101, 0x2105, 0x2106, 0x2A74, 0xFE13, 0xFE16, 0xFE55, 0xFE56, 0xFE5F, 0xFE6B, 0xFF03, 0xFF0F,
    0xFF1A, 0xFF1F, 0xFF20,
];

/// `ipaddress.IPv6Address(text)` succeeds: an address, with an optional `%scope`.
fn ipv6(text: &str) -> bool {
    let address = match text.split_once('%') {
        Some((_, scope)) if scope.is_empty() || scope.contains('%') => return false,
        Some((address, _)) => address,
        None => text,
    };
    Ipv6Addr::from_str(address).is_ok()
}

/// `_check_bracketed_host`: an IPvFuture literal or an IPv6 address.
fn bracketed_host(host: &str) -> bool {
    match host.strip_prefix('v') {
        Some(rest) => {
            let digits = rest.chars().take_while(char::is_ascii_hexdigit).count();
            digits > 0 && rest[digits..].starts_with('.') && rest.len() > digits + 1
        }
        None => ipv6(host),
    }
}

/// `_check_bracketed_netloc`.
fn bracketed(netloc: &str) -> bool {
    let host_and_port = netloc.rsplit_once('@').map_or(netloc, |(_, rest)| rest);
    match host_and_port.split_once('[') {
        Some((before, inside)) => {
            let (host, port) = inside.split_once(']').unwrap_or((inside, ""));
            before.is_empty() && (port.is_empty() || port.starts_with(':')) && bracketed_host(host)
        }
        None => bracketed_host(host_and_port.split_once(':').map_or(host_and_port, |(host, _)| host)),
    }
}

/// Whether a stripped, non-empty link passes `_url`'s checks.
pub fn valid(value: &str) -> bool {
    if value.chars().any(|c| (c as u32) < 33) {
        return false;
    }
    let Some((scheme, rest)) = value.split_once(':') else { return false };
    if !scheme.eq_ignore_ascii_case("http") && !scheme.eq_ignore_ascii_case("https") {
        return false;
    }
    let Some(rest) = rest.strip_prefix("//") else { return false };
    let netloc = &rest[..rest.find(['/', '?', '#']).unwrap_or(rest.len())];
    let (open, close) = (netloc.contains('['), netloc.contains(']'));
    if open != close || (open && !bracketed(netloc)) {
        return false;
    }
    if netloc.chars().any(|c| SEPARATOR_LOOKALIKES.contains(&(c as u32))) {
        return false;
    }
    let (userinfo, hostinfo) = netloc.rsplit_once('@').unwrap_or(("", netloc));
    // A user name or a password that is not empty.
    if !matches!(userinfo, "" | ":") {
        return false;
    }
    let (host, port) = match hostinfo.split_once('[') {
        Some((_, inside)) => {
            let (host, after) = inside.split_once(']').unwrap_or((inside, ""));
            (host, after.split_once(':').map_or("", |(_, port)| port))
        }
        None => hostinfo.split_once(':').unwrap_or((hostinfo, "")),
    };
    if host.is_empty() {
        return false;
    }
    // `SplitResult.port`: digits, at most 65535.
    port.is_empty() || (port.bytes().all(|b| b.is_ascii_digit()) && port.trim_start_matches('0').len() <= 5 && port.parse::<u32>().is_ok_and(|port| port <= 65535))
}

#[cfg(test)]
mod tests {
    use super::valid;

    /// Each answer is what `schema_maps._url` gives for the same text (Python 3.12.14).
    #[test]
    fn links_are_judged_as_urlsplit_judges_them() {
        for good in ["https://example.invalid/a", "HTTP://Example.invalid", "http://h:80/x", "http://h:", "http://@h/", "http://:@h/", "http://[::1]:8080/",
                     "http://[fe80::1%eth0]/", "http://[v1.x]/", "http://h:00080/", "https://h?q#f", "http://h/a@b:c", "http://bücher.invalid/", "http://h:65535"] {
            assert!(valid(good), "{good}");
        }
        for bad in ["javascript:alert(1)", "https://user:pass@example.invalid/", "//example.invalid/", "http:/h", "http://", "http:///path", "http://u@h/",
                    "http://:p@h/", "http://h:x/", "http://h:65536/", "http://h:99999999999/", "http://[::1/", "http://::1]/", "http://[1.2.3.4]/", "http://a[::1]/",
                    "http://[::1]x/", "http://[::1%]/", "http://h/a b", "http://h\u{ff0f}x/", "ftp://h/", "http://[v.x]/", "h", "http://[x]@h/", "http://h:٣/"] {
            assert!(!valid(bad), "{bad}");
        }
    }
}
