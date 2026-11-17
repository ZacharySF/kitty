//! `share.fetch`: the text at an https build link, with its SSRF rules.
//!
//! Only https, no user name or password, at most three redirects (each re-checked), every
//! address the host resolves to must be global (`ipaddress.is_global`), and the request goes to
//! exactly the addresses that were checked (pinned: a second DNS answer cannot point it at this
//! machine). 10 s connect and read timeouts, a 256 KB cap on the declared length and the body,
//! UTF-8 only; the refusals carry `ShareError`'s messages.

use std::future::Future;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::time::Duration;

use reqwest::Url;

use super::document::MAX_BYTES;

pub const MAX_REDIRECTS: usize = 3;
const TIMEOUT: Duration = Duration::from_secs(10);
const ACCEPT: &str = "application/json, text/plain;q=0.9";

const NOT_A_LINK: &str = "That is not a valid link";
const NOT_HTTPS: &str = "Build links must use https (for example a GitHub gist or raw file link)";
const NO_HOST: &str = "Build links need a host and cannot carry a user name or password";
const NOT_PUBLIC: &str = "Build links must point to a public https address, not this machine or a local network";

// Addresses -------------------------------------------------------------------------------

fn in_v4(address: Ipv4Addr, network: [u8; 4], prefix: u32) -> bool {
    let mask = if prefix == 0 { 0 } else { u32::MAX << (32 - prefix) };
    u32::from(address) & mask == u32::from(Ipv4Addr::from(network)) & mask
}

fn in_v6(address: Ipv6Addr, network: [u16; 8], prefix: u32) -> bool {
    let mask = if prefix == 0 { 0 } else { u128::MAX << (128 - prefix) };
    u128::from(address) & mask == u128::from(Ipv6Addr::from(network)) & mask
}

/// `IPv4Address.is_global` (CPython 3.12).
pub fn v4_is_global(address: Ipv4Addr) -> bool {
    const PRIVATE: [([u8; 4], u32); 14] = [
        ([0, 0, 0, 0], 8), ([10, 0, 0, 0], 8), ([127, 0, 0, 0], 8), ([169, 254, 0, 0], 16), ([172, 16, 0, 0], 12),
        ([192, 0, 0, 0], 24), ([192, 0, 0, 170], 31), ([192, 0, 2, 0], 24), ([192, 168, 0, 0], 16), ([198, 18, 0, 0], 15),
        ([198, 51, 100, 0], 24), ([203, 0, 113, 0], 24), ([240, 0, 0, 0], 4), ([255, 255, 255, 255], 32),
    ];
    const EXCEPTIONS: [([u8; 4], u32); 2] = [([192, 0, 0, 9], 32), ([192, 0, 0, 10], 32)];
    let private = PRIVATE.iter().any(|&(n, p)| in_v4(address, n, p)) && !EXCEPTIONS.iter().any(|&(n, p)| in_v4(address, n, p));
    !in_v4(address, [100, 64, 0, 0], 10) && !private
}

/// `IPv6Address.is_global` (CPython 3.12): an IPv4-mapped address answers as its IPv4 address.
pub fn v6_is_global(address: Ipv6Addr) -> bool {
    const PRIVATE: [([u16; 8], u32); 11] = [
        ([0, 0, 0, 0, 0, 0, 0, 1], 128), ([0; 8], 128), ([0, 0, 0, 0, 0, 0xffff, 0, 0], 96), ([0x64, 0xff9b, 1, 0, 0, 0, 0, 0], 48),
        ([0x100, 0, 0, 0, 0, 0, 0, 0], 64), ([0x2001, 0, 0, 0, 0, 0, 0, 0], 23), ([0x2001, 0xdb8, 0, 0, 0, 0, 0, 0], 32),
        ([0x2002, 0, 0, 0, 0, 0, 0, 0], 16), ([0x3fff, 0, 0, 0, 0, 0, 0, 0], 20), ([0xfc00, 0, 0, 0, 0, 0, 0, 0], 7),
        ([0xfe80, 0, 0, 0, 0, 0, 0, 0], 10),
    ];
    const EXCEPTIONS: [([u16; 8], u32); 6] = [
        ([0x2001, 1, 0, 0, 0, 0, 0, 1], 128), ([0x2001, 1, 0, 0, 0, 0, 0, 2], 128), ([0x2001, 3, 0, 0, 0, 0, 0, 0], 32),
        ([0x2001, 4, 0x112, 0, 0, 0, 0, 0], 48), ([0x2001, 0x20, 0, 0, 0, 0, 0, 0], 28), ([0x2001, 0x30, 0, 0, 0, 0, 0, 0], 28),
    ];
    if let Some(mapped) = address.to_ipv4_mapped() {
        return v4_is_global(mapped);
    }
    let private = PRIVATE.iter().any(|&(n, p)| in_v6(address, n, p)) && !EXCEPTIONS.iter().any(|&(n, p)| in_v6(address, n, p));
    !private
}

pub fn is_global(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(v4) => v4_is_global(v4),
        IpAddr::V6(v6) => v6_is_global(v6),
    }
}

// Links -----------------------------------------------------------------------------------

/// What `urllib.parse.urlsplit` makes of a link: its scheme and network location.
struct Split {
    scheme: String,
    netloc: String,
}

/// `urlsplit`'s scheme and netloc; `Err` where it raises ValueError.
fn urlsplit(url: &str) -> Result<Split, ()> {
    let url: String = url.trim_start_matches(|c: char| c <= ' ').chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
    let mut rest = url.as_str();
    let mut scheme = String::new();
    if let Some(i) = rest.find(':') {
        let candidate = &rest[..i];
        let first = candidate.chars().next();
        if i > 0 && first.is_some_and(|c| c.is_ascii_alphabetic())
            && candidate.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
        {
            scheme = candidate.to_ascii_lowercase();
            rest = &rest[i + 1..];
        }
    }
    let mut netloc = String::new();
    if let Some(after) = rest.strip_prefix("//") {
        let end = after.find(['/', '?', '#']).unwrap_or(after.len());
        netloc = after[..end].to_owned();
        let (open, close) = (netloc.contains('['), netloc.contains(']'));
        if open != close {
            return Err(());
        }
        if open {
            let bracketed = netloc.split_once('[').map(|(_, b)| b).unwrap_or("").split(']').next().unwrap_or("");
            let valid = bracketed.starts_with('v')
                || bracketed.split('%').next().is_some_and(|address| address.parse::<Ipv6Addr>().is_ok());
            if !valid {
                return Err(());
            }
        }
    }
    Ok(Split { scheme, netloc })
}

impl Split {
    fn userinfo(&self) -> Option<(&str, Option<&str>)> {
        let (userinfo, _) = self.netloc.rsplit_once('@')?;
        Some(match userinfo.split_once(':') {
            Some((user, password)) => (user, Some(password)),
            None => (userinfo, None),
        })
    }

    /// `SplitResult.hostname`.
    fn hostname(&self) -> Option<String> {
        let hostinfo = self.netloc.rsplit_once('@').map_or(self.netloc.as_str(), |(_, host)| host);
        let host = match hostinfo.split_once('[') {
            Some((_, bracketed)) => bracketed.split(']').next().unwrap_or(""),
            None => hostinfo.split(':').next().unwrap_or(""),
        };
        if host.is_empty() {
            return None;
        }
        Some(match host.split_once('%') {
            Some((address, zone)) => format!("{}%{zone}", address.to_lowercase()),
            None => host.to_lowercase(),
        })
    }
}

/// `_check_url`: the hostname of an https link without credentials.
fn check_url(url: &str) -> Result<String, String> {
    let parts = urlsplit(url).map_err(|()| NOT_A_LINK.to_owned())?;
    if parts.scheme != "https" {
        return Err(NOT_HTTPS.into());
    }
    let credentials = parts.userinfo().is_some_and(|(user, password)| !user.is_empty() || password.is_some_and(|p| !p.is_empty()));
    match parts.hostname() {
        Some(host) if !credentials => Ok(host),
        _ => Err(NO_HOST.into()),
    }
}

// Transport -------------------------------------------------------------------------------

/// The resolver `share.fetch` checks every address of (`socket.getaddrinfo` there).
pub trait Resolver: Sync {
    fn resolve(&self, host: &str) -> impl Future<Output = std::io::Result<Vec<IpAddr>>> + Send;
}

/// One answer: status, its `Location` and `Content-Length`, and the body in chunks.
pub struct Reply {
    pub status: u16,
    pub location: Option<String>,
    pub content_length: Option<String>,
    pub body: Body,
}

pub enum Body {
    Live(reqwest::Response),
    #[cfg_attr(not(test), allow(dead_code))]
    Chunks(std::vec::IntoIter<Vec<u8>>),
}

impl Body {
    async fn next(&mut self) -> Result<Option<Vec<u8>>, &'static str> {
        match self {
            Body::Live(response) => response.chunk().await.map(|chunk| chunk.map(|bytes| bytes.to_vec())).map_err(|e| error_name(&e)),
            Body::Chunks(chunks) => Ok(chunks.next()),
        }
    }
}

/// The HTTPS client: one GET to exactly `pinned` (the checked addresses), no redirects followed.
/// An `Err` names the failure as httpx's exception class would.
pub trait Transport: Sync {
    fn get(&self, url: &Url, pinned: &[IpAddr]) -> impl Future<Output = Result<Reply, &'static str>> + Send;
}

/// httpx's exception class names for what went wrong.
fn error_name(error: &reqwest::Error) -> &'static str {
    if error.is_timeout() {
        if error.is_connect() { "ConnectTimeout" } else { "ReadTimeout" }
    } else if error.is_connect() {
        "ConnectError"
    } else if error.is_body() || error.is_decode() {
        "ReadError"
    } else {
        "RemoteProtocolError"
    }
}

/// `getaddrinfo` through tokio.
pub struct System;

impl Resolver for System {
    async fn resolve(&self, host: &str) -> std::io::Result<Vec<IpAddr>> {
        Ok(tokio::net::lookup_host((host, 443)).await?.map(|address| address.ip()).collect())
    }
}

/// reqwest with rustls (crate::packs' stack), no proxy (it would connect elsewhere than the
/// checked addresses), every name resolved to the pinned addresses.
pub struct Https;

impl Transport for Https {
    async fn get(&self, url: &Url, pinned: &[IpAddr]) -> Result<Reply, &'static str> {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let mut builder = reqwest::Client::builder()
            .user_agent(concat!("rika-server/", env!("CARGO_PKG_VERSION")))
            .https_only(true)
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(TIMEOUT)
            .read_timeout(TIMEOUT);
        if let Some(domain) = url.domain() {
            let addresses: Vec<SocketAddr> = pinned.iter().map(|ip| SocketAddr::new(*ip, 0)).collect();
            builder = builder.resolve_to_addrs(domain, &addresses);
        }
        let client = builder.build().map_err(|_| "ConnectError")?;
        let response = client.get(url.clone()).header("accept", ACCEPT).send().await.map_err(|e| error_name(&e))?;
        let latin1 = |name: &str| response.headers().get(name).map(|value| value.as_bytes().iter().map(|&b| char::from(b)).collect());
        Ok(Reply {
            status: response.status().as_u16(),
            location: latin1("location"),
            content_length: latin1("content-length"),
            body: Body::Live(response),
        })
    }
}

/// `share.fetch`: the text at `url`, or the `ShareError` message refusing it.
pub async fn fetch(url: &str, resolver: &impl Resolver, transport: &impl Transport) -> Result<String, String> {
    if url.chars().count() > 2048 {
        return Err(NOT_A_LINK.into());
    }
    let mut url = url.to_owned();
    for _hop in 0..=MAX_REDIRECTS {
        let host = check_url(&url)?;
        let parsed = match Url::parse(&url) {
            Ok(parsed) => parsed,
            // Python resolved its hostname before httpx read the link: a host only getaddrinfo
            // reads (an IPv6 literal with a zone) is refused with the reason Python gave.
            Err(_) => {
                let addresses = resolver.resolve(&host).await.map_err(|_| format!("Could not find {host}"))?;
                if addresses.is_empty() {
                    return Err(format!("Could not find {host}"));
                }
                if !addresses.iter().all(|address| is_global(*address)) {
                    return Err(NOT_PUBLIC.into());
                }
                return Err(NOT_A_LINK.into());
            }
        };
        let addresses = match parsed.host() {
            None => return Err(NO_HOST.into()),
            Some(_) if parsed.domain().is_none() => {
                let literal = parsed.host_str().unwrap_or("").trim_start_matches('[').trim_end_matches(']');
                vec![literal.parse::<IpAddr>().map_err(|_| NOT_A_LINK.to_owned())?]
            }
            Some(_) => resolver.resolve(parsed.domain().unwrap_or("")).await.map_err(|_| format!("Could not find {host}"))?,
        };
        if addresses.is_empty() {
            return Err(format!("Could not find {host}"));
        }
        if !addresses.iter().all(|address| is_global(*address)) {
            return Err(NOT_PUBLIC.into());
        }
        let fetching = |name: &str| format!("Could not fetch the link ({name})");
        let mut reply = transport.get(&parsed, &addresses).await.map_err(fetching)?;
        if matches!(reply.status, 301 | 302 | 303 | 307 | 308) {
            if let Some(location) = &reply.location {
                url = parsed.join(location).map_err(|_| NOT_A_LINK.to_owned())?.to_string();
                if urlsplit(&url).map(|parts| parts.scheme).unwrap_or_default() != "https" {
                    return Err("That link redirects to a non-https address; refusing it".into());
                }
                continue;
            }
        }
        if reply.status != 200 {
            return Err(format!("The link answered {}", reply.status));
        }
        let limited = || format!("Builds are limited to {} KB", MAX_BYTES / 1024);
        if let Some(declared) = &reply.content_length {
            // `declared.isdigit() and int(declared) > MAX_BYTES`: a number too long to parse is larger.
            let digits = !declared.is_empty() && declared.bytes().all(|b| b.is_ascii_digit());
            if digits && declared.parse::<u64>().map_or(true, |n| n > MAX_BYTES as u64) {
                return Err(limited());
            }
        }
        let mut body = Vec::new();
        while let Some(chunk) = reply.body.next().await.map_err(fetching)? {
            body.extend_from_slice(&chunk);
            if body.len() > MAX_BYTES {
                return Err(limited());
            }
        }
        return String::from_utf8(body).map_err(|_| "The link did not return UTF-8 text".to_owned());
    }
    Err(format!("That link redirects more than {MAX_REDIRECTS} times"))
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::sync::Mutex;

    use super::*;

    struct Fake;

    impl Resolver for Fake {
        async fn resolve(&self, host: &str) -> std::io::Result<Vec<IpAddr>> {
            if host.contains("%25") {
                return Err(std::io::Error::from(std::io::ErrorKind::NotFound));
            }
            Ok(vec![if host == "evil.internal" { "127.0.0.1" } else { "140.82.112.3" }.parse().unwrap()])
        }
    }

    /// Canned answers by URL; records where each request was pinned.
    struct Routes {
        answers: HashMap<String, (u16, Option<String>, Vec<u8>)>,
        pinned: Mutex<Vec<(String, Vec<IpAddr>)>>,
    }

    impl Transport for Routes {
        async fn get(&self, url: &Url, pinned: &[IpAddr]) -> Result<Reply, &'static str> {
            self.pinned.lock().unwrap().push((url.to_string(), pinned.to_vec()));
            let (status, location, body) = self.answers.get(url.as_str()).cloned().ok_or("ConnectError")?;
            Ok(Reply {
                status,
                location,
                content_length: Some(body.len().to_string()),
                body: Body::Chunks(body.chunks(1000).map(<[u8]>::to_vec).collect::<Vec<_>>().into_iter()),
            })
        }
    }

    fn to(location: &str) -> Option<String> {
        Some(location.to_owned())
    }

    fn routes() -> Routes {
        let good = br#"{"schema": 1, "name": "gist"}"#.to_vec();
        let mut answers = HashMap::new();
        answers.insert("https://gist.githubusercontent.com/u/1/raw/kitty-build.json".to_owned(), (200, None, good.clone()));
        answers.insert("https://github.com/u/r/raw/main/kitty-build.json".to_owned(),
                       (302, to("https://raw.githubusercontent.com/u/r/main/kitty-build.json"), Vec::new()));
        answers.insert("https://raw.githubusercontent.com/u/r/main/kitty-build.json".to_owned(), (200, None, good));
        answers.insert("https://gist.githubusercontent.com/u/2/raw".to_owned(), (301, to("http://plain.example/x"), Vec::new()));
        answers.insert("https://gist.githubusercontent.com/u/3/raw".to_owned(), (200, None, vec![b'x'; MAX_BYTES + 1]));
        answers.insert("https://gist.githubusercontent.com/u/4/raw".to_owned(), (404, None, Vec::new()));
        answers.insert("https://gist.githubusercontent.com/u/5/raw".to_owned(), (200, None, vec![0xff, 0xfe]));
        answers.insert("https://gist.githubusercontent.com/u/6/raw".to_owned(), (304, to("https://x.example/"), Vec::new()));
        answers.insert("https://gist.githubusercontent.com/u/7/raw".to_owned(), (302, to("/u/1/raw/kitty-build.json"), Vec::new()));
        for n in 0..6 {
            answers.insert(format!("https://loop.example/{n}"), (302, Some(format!("https://loop.example/{}", n + 1)), Vec::new()));
        }
        answers.insert("https://redirect.example/inside".to_owned(), (302, to("https://evil.internal/x"), Vec::new()));
        Routes { answers, pinned: Mutex::new(Vec::new()) }
    }

    fn run(url: &str, routes: &Routes) -> Result<String, String> {
        tokio::runtime::Builder::new_current_thread().build().unwrap().block_on(fetch(url, &Fake, routes))
    }

    /// tests/test_builds.py's `test_fetching_a_build_link_follows_only_https_and_caps_its_size`.
    #[test]
    fn a_build_link_follows_only_https_and_caps_its_size() {
        let routes = routes();
        let good = r#"{"schema": 1, "name": "gist"}"#;
        assert_eq!(run("https://gist.githubusercontent.com/u/1/raw/kitty-build.json", &routes).as_deref(), Ok(good));
        assert_eq!(run("https://github.com/u/r/raw/main/kitty-build.json", &routes).as_deref(), Ok(good));
        assert_eq!(run("https://gist.githubusercontent.com/u/7/raw", &routes).as_deref(), Ok(good));
        for (url, reason) in [
            ("http://gist.githubusercontent.com/u/1/raw", "https"),
            ("https://gist.githubusercontent.com/u/2/raw", "non-https"),
            ("https://gist.githubusercontent.com/u/3/raw", "limited"),
            ("https://gist.githubusercontent.com/u/4/raw", "404"),
            ("https://gist.githubusercontent.com/u/5/raw", "UTF-8"),
            ("https://gist.githubusercontent.com/u/6/raw", "answered 304"),
            ("https://loop.example/0", "redirects more than"),
            ("https://user:pw@gist.githubusercontent.com/x", "user name"),
            ("https://:pw@gist.githubusercontent.com/x", "user name"),
            ("https:///no-host", "need a host"),
            ("https://evil.internal/build.json", "public https address"),
            ("https://redirect.example/inside", "public https address"),
            ("https://127.0.0.1/build.json", "public https address"),
            ("https://[::ffff:10.0.0.1]/build.json", "public https address"),
            ("https://[::1/build.json", "valid link"),
            ("https://[fe80::1%25eth0]/build.json", "Could not find fe80::1%25eth0"),
            ("file:///etc/passwd", "https"),
            ("gist.githubusercontent.com/u/1", "https"),
            ("https://nowhere.example/", "fetch the link (ConnectError)"),
        ] {
            let refused = run(url, &routes).expect_err(url);
            assert!(refused.contains(reason), "{url}: {refused}");
        }
        assert_eq!(run(&format!("https://gist.githubusercontent.com/{}", "x".repeat(2048)), &routes).unwrap_err(), NOT_A_LINK);
    }

    #[test]
    fn every_request_goes_to_the_addresses_that_were_checked() {
        let routes = routes();
        run("https://github.com/u/r/raw/main/kitty-build.json", &routes).unwrap();
        let public: Vec<IpAddr> = vec!["140.82.112.3".parse().unwrap()];
        let pinned = routes.pinned.lock().unwrap().clone();
        assert_eq!(pinned, vec![
            ("https://github.com/u/r/raw/main/kitty-build.json".to_owned(), public.clone()),
            ("https://raw.githubusercontent.com/u/r/main/kitty-build.json".to_owned(), public),
        ]);
        routes.pinned.lock().unwrap().clear();
        assert!(run("https://evil.internal/x", &routes).is_err());
        assert!(routes.pinned.lock().unwrap().is_empty(), "a refused address is never contacted");
    }

    /// `ipaddress.is_global` of CPython 3.12, address by address.
    #[test]
    fn global_addresses_are_python_s() {
        let table = [
            ("140.82.112.3", true), ("127.0.0.1", false), ("10.1.2.3", false), ("100.64.0.1", false), ("100.128.0.1", true),
            ("169.254.1.1", false), ("172.16.0.1", false), ("172.32.0.1", true), ("192.0.0.9", true), ("192.0.0.10", true),
            ("192.0.0.8", false), ("192.0.0.171", false), ("192.0.2.1", false), ("192.168.1.1", false), ("198.18.0.1", false),
            ("198.51.100.1", false), ("203.0.113.1", false), ("224.0.0.1", true), ("240.0.0.1", false),
            ("255.255.255.255", false), ("0.1.2.3", false), ("8.8.8.8", true), ("::1", false), ("::", false),
            ("::ffff:127.0.0.1", false), ("::ffff:8.8.8.8", true), ("64:ff9b::808:808", true), ("64:ff9b:1::1", false),
            ("100::1", false), ("2001::1", false), ("2001:1::1", true), ("2001:1::3", false), ("2001:3::1", true),
            ("2001:4:112::1", true), ("2001:20::1", true), ("2001:30::1", true), ("2001:db8::1", false), ("2002::1", false),
            ("3fff::1", false), ("fc00::1", false), ("fe80::1", false), ("2606:4700::1111", true), ("ff02::1", true),
            ("::7f00:1", true), ("2001:200::1", true),
        ];
        for (address, global) in table {
            assert_eq!(is_global(address.parse().unwrap()), global, "{address}");
        }
    }
}
