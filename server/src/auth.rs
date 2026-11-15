//! The per-launch UI secret, the browser-mode one-time code and link tickets.
//!
//! The same contract as `kitty.web_auth` (read its module docstring for why each piece
//! exists): a fresh 256-bit secret kept only in memory and handed to rika!'s own windows;
//! a one-time code a browser tab trades for it; single-use, short-lived tickets for GET
//! navigations that cannot carry a header.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use subtle::ConstantTimeEq;
use tokio::sync::Notify;

pub const HEADER: &str = "x-kitty-secret";
/// The WebSocket subprotocol the server selects when the secret travels as a subprotocol.
pub const PROTOCOL: &str = "kitty";
pub const PROTOCOL_PREFIX: &str = "kitty-secret.";
/// POST {"code"} -> {"secret"}: the browser-mode exchange; needs the code, not the secret.
pub const SESSION_PATH: &str = "/api/session";
/// POST {"path"} -> {"url"}: a single-use link for one GET navigation.
pub const LINK_PATH: &str = "/api/session/link";
pub const TICKET: &str = "ticket";
pub const CODE_TTL: Duration = Duration::from_secs(600);
pub const LINK_TTL: Duration = Duration::from_secs(60);
pub const MAX_LINKS: usize = 64;
/// The one message for every refusal: it says nothing about which part was wrong.
pub const REFUSAL: &str = "rika! did not recognise this window; open rika! or the link kitty-web printed";

/// Monotonic time, injectable so tests can expire codes and tickets.
pub type Clock = Arc<dyn Fn() -> Instant + Send + Sync>;

/// `bytes` random bytes as lowercase hex (Python's `secrets.token_hex(bytes)`).
pub fn token_hex(bytes: usize) -> String {
    let mut buffer = vec![0u8; bytes];
    getrandom::fill(&mut buffer).expect("the operating system's random source failed");
    hex::encode(buffer)
}

/// Constant-time comparison of two strings (Python's `hmac.compare_digest`).
pub fn same(a: &str, b: &str) -> bool {
    a.as_bytes().ct_eq(b.as_bytes()).into()
}

struct State {
    code: Option<(String, Instant)>,
    links: HashMap<String, (String, Instant)>,
}

/// The secret, the outstanding one-time code and the link tickets of one server.
pub struct UiAuth {
    secret: String,
    clock: Clock,
    state: Mutex<State>,
    /// Woken whenever the outstanding code is used, replaced or expires.
    changed: Notify,
}

impl std::fmt::Debug for UiAuth {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("UiAuth(<secret>)")
    }
}

impl UiAuth {
    pub fn new() -> Self {
        Self::with_clock(Arc::new(Instant::now))
    }

    pub fn with_clock(clock: Clock) -> Self {
        Self {
            secret: token_hex(32),
            clock,
            state: Mutex::new(State { code: None, links: HashMap::new() }),
            changed: Notify::new(),
        }
    }

    /// The secret itself: only for the desktop shell's private readiness line (`--desktop`).
    pub fn secret(&self) -> &str {
        &self.secret
    }

    /// Constant-time check of a presented secret.
    pub fn valid(&self, value: &str) -> bool {
        value.is_ascii() && same(value, &self.secret)
    }

    /// Whether a WebSocket upgrade's subprotocols are exactly `PROTOCOL` plus the secret.
    pub fn protocol_secret<'a>(&self, offered: impl IntoIterator<Item = &'a str>) -> bool {
        let names: Vec<&str> = offered
            .into_iter()
            .flat_map(|value| value.split(','))
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .collect();
        let carried: Vec<&str> = names.iter().filter_map(|name| name.strip_prefix(PROTOCOL_PREFIX)).collect();
        names.len() == 2
            && names.iter().filter(|name| **name == PROTOCOL).count() == 1
            && carried.len() == 1
            && self.valid(carried[0])
    }

    /// A fresh one-time code; it replaces any code still outstanding.
    pub fn issue_code(&self) -> String {
        let code = token_hex(16);
        self.state.lock().unwrap().code = Some((code.clone(), (self.clock)() + CODE_TTL));
        self.changed.notify_waiters();
        code
    }

    /// The secret for the outstanding code, once; None for anything else.
    pub fn redeem(&self, code: &str) -> Option<&str> {
        if !code.is_ascii() {
            return None;
        }
        let mut state = self.state.lock().unwrap();
        let current = state.code.clone()?;
        if !same(code, &current.0) {
            return None;
        }
        state.code = None;
        drop(state);
        self.changed.notify_waiters();
        (current.1 > (self.clock)()).then_some(self.secret.as_str())
    }

    /// Wait until `code` is used, replaced or expired.
    pub async fn wait_code(&self, code: &str) {
        loop {
            let notified = self.changed.notified();
            let remaining = {
                let mut state = self.state.lock().unwrap();
                match &state.code {
                    Some((current, expires)) if current == code => {
                        let now = (self.clock)();
                        if *expires <= now {
                            state.code = None;
                            return;
                        }
                        *expires - now
                    }
                    _ => return,
                }
            };
            // Re-check at least twice a second, as kitty.web_auth does.
            let _ = tokio::time::timeout(remaining.min(Duration::from_millis(500)), notified).await;
        }
    }

    /// A single-use ticket for exactly `target` (a canonical GET target).
    pub fn issue_link(&self, target: String) -> String {
        let ticket = token_hex(32);
        let now = (self.clock)();
        let mut state = self.state.lock().unwrap();
        state.links.retain(|_, (_, expires)| *expires > now);
        if state.links.len() >= MAX_LINKS {
            let oldest = state.links.iter().min_by_key(|(_, (_, expires))| *expires).map(|(key, _)| key.clone());
            if let Some(oldest) = oldest {
                state.links.remove(&oldest);
            }
        }
        state.links.insert(ticket.clone(), (target, now + LINK_TTL));
        ticket
    }

    /// Whether `ticket` was issued for exactly `target`; it is spent either way.
    pub fn follow(&self, ticket: &str, target: &str) -> bool {
        let entry = self.state.lock().unwrap().links.remove(ticket);
        matches!(entry, Some((issued_for, expires)) if expires > (self.clock)() && same(&issued_for, target))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    fn clocked() -> (UiAuth, Arc<AtomicU64>) {
        let start = Instant::now();
        let elapsed = Arc::new(AtomicU64::new(0));
        let seen = elapsed.clone();
        let auth = UiAuth::with_clock(Arc::new(move || start + Duration::from_secs(seen.load(Ordering::SeqCst))));
        (auth, elapsed)
    }

    #[test]
    fn the_secret_is_fresh_hex_and_never_printed_by_debug() {
        let (one, two) = (UiAuth::new(), UiAuth::new());
        assert_eq!(one.secret().len(), 64);
        assert!(one.secret().bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()));
        assert_ne!(one.secret(), two.secret());
        assert_eq!(format!("{one:?}"), "UiAuth(<secret>)");
    }

    #[test]
    fn only_the_exact_secret_is_valid() {
        let auth = UiAuth::new();
        let secret = auth.secret().to_owned();
        assert!(auth.valid(&secret));
        assert!(!auth.valid(&secret[1..]));
        assert!(!auth.valid(&secret.to_uppercase()));
        assert!(!auth.valid(&format!("{secret} ")));
        assert!(!auth.valid(""));
    }

    #[test]
    fn an_upgrade_needs_exactly_the_protocol_and_the_secret() {
        let auth = UiAuth::new();
        let carried = format!("{PROTOCOL_PREFIX}{}", auth.secret());
        assert!(auth.protocol_secret([format!("kitty, {carried}").as_str()]));
        assert!(auth.protocol_secret(["kitty", carried.as_str()]));
        assert!(!auth.protocol_secret([carried.as_str()]), "the protocol itself is required");
        assert!(!auth.protocol_secret([format!("kitty, kitty, {carried}").as_str()]));
        assert!(!auth.protocol_secret([format!("kitty, {carried}, other").as_str()]));
        assert!(!auth.protocol_secret(["kitty, kitty-secret.wrong"]));
    }

    #[test]
    fn a_code_works_once_and_a_new_code_replaces_the_old() {
        let (auth, _) = clocked();
        let first = auth.issue_code();
        let second = auth.issue_code();
        assert_eq!(auth.redeem(&first), None);
        assert_eq!(auth.redeem(&second), Some(auth.secret()));
        assert_eq!(auth.redeem(&second), None);
    }

    #[test]
    fn codes_and_tickets_follow_their_clock() {
        let (auth, elapsed) = clocked();
        let code = auth.issue_code();
        elapsed.store(CODE_TTL.as_secs(), Ordering::SeqCst);
        assert_eq!(auth.redeem(&code), None, "a code expires after CODE_TTL");

        elapsed.store(0, Ordering::SeqCst);
        let ticket = auth.issue_link("target".into());
        elapsed.store(LINK_TTL.as_secs() - 1, Ordering::SeqCst);
        let late = auth.issue_link("target".into());
        elapsed.store(LINK_TTL.as_secs(), Ordering::SeqCst);
        assert!(!auth.follow(&ticket, "target"), "a ticket expires after LINK_TTL");
        assert!(auth.follow(&late, "target"));
    }

    #[test]
    fn a_ticket_opens_one_exact_target_once() {
        let auth = UiAuth::new();
        let ticket = auth.issue_link("[\"/api/a\",[]]".into());
        assert!(!auth.follow(&ticket, "[\"/api/b\",[]]"), "the wrong target spends it");
        assert!(!auth.follow(&ticket, "[\"/api/a\",[]]"));
        let ticket = auth.issue_link("[\"/api/a\",[]]".into());
        assert!(auth.follow(&ticket, "[\"/api/a\",[]]"));
        assert!(!auth.follow(&ticket, "[\"/api/a\",[]]"));
    }

    #[test]
    fn at_most_max_links_tickets_are_outstanding_and_the_oldest_goes_first() {
        let (auth, elapsed) = clocked();
        let first = auth.issue_link("t".into());
        elapsed.store(1, Ordering::SeqCst);
        let rest: Vec<String> = (0..MAX_LINKS).map(|_| auth.issue_link("t".into())).collect();
        assert!(!auth.follow(&first, "t"));
        assert!(rest.iter().all(|ticket| auth.follow(ticket, "t")));
    }

    #[tokio::test]
    async fn waiting_for_a_code_ends_when_it_is_used() {
        let auth = Arc::new(UiAuth::new());
        let code = auth.issue_code();
        let waiter = {
            let (auth, code) = (auth.clone(), code.clone());
            tokio::spawn(async move { auth.wait_code(&code).await })
        };
        tokio::task::yield_now().await;
        assert!(auth.redeem(&code).is_some());
        tokio::time::timeout(Duration::from_secs(2), waiter).await.unwrap().unwrap();
    }
}
