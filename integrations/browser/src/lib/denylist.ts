// Which pages are never mirrored or sent to rika! (platform design Q11 privacy rules).
//
// Built in, and not switchable: anything that is not an http(s) web page (browser pages,
// files, extension pages, the extension stores), private/incognito windows, banking and
// payment sites, password managers, and sign-in pages (by host and by path). At run time a
// page with a password field is refused too (see content.ts). People add their own rules:
// one per line, `example.com` (that site and its subdomains), `*.example.com`,
// `*shop*` (a wildcard over the host) or `example.com/private` (only under that path).

export type DenyCategory = "browser" | "private" | "banking" | "passwords" | "sign-in" | "yours" | "password-field";
export type Denied = { ok: false; category: DenyCategory; reason: string };
export type Verdict = { ok: true } | Denied;

export const REASONS: Record<DenyCategory, string> = {
  browser: "Browser pages, files and extension pages are never mirrored.",
  private: "Private and incognito windows are never mirrored.",
  banking: "Banking and payment sites are never mirrored.",
  passwords: "Password managers are never mirrored.",
  "sign-in": "Sign-in pages are never mirrored.",
  yours: "This page matches your own deny list.",
  "password-field": "This page has a password field, so it is not mirrored.",
};

const deny = (category: DenyCategory): Denied => ({ ok: false, category, reason: REASONS[category] });

/** Banking, payment and brokerage sites (their subdomains too). */
export const BANKING = [
  "paypal.com", "paypal.me", "venmo.com", "cash.app", "zellepay.com", "wise.com", "revolut.com", "n26.com", "monzo.com",
  "chase.com", "wellsfargo.com", "citi.com", "capitalone.com", "americanexpress.com", "discover.com",
  "usaa.com", "pnc.com", "truist.com", "ally.com", "sofi.com", "chime.com", "schwab.com", "fidelity.com", "vanguard.com",
  "etrade.com", "robinhood.com", "coinbase.com", "kraken.com", "binance.com", "hsbc.com", "hsbc.co.uk", "barclays.co.uk",
  "lloydsbank.com", "natwest.com", "santander.com", "ing.com", "bnpparibas", "commerzbank.de", "sparkasse.de",
  "intuit.com", "mint.com", "ynab.com", "checkout.stripe.com", "dashboard.stripe.com", "pay.google.com",
  "payments.google.com", "klarna.com", "affirm.com", "afterpay.com",
];
/** A host label containing one of these is treated as a bank. */
const BANK_WORDS = /bank|banca|banque|credit-?union|sparkasse|kasse/i;

export const PASSWORD_MANAGERS = [
  "1password.com", "1password.ca", "1password.eu", "bitwarden.com", "bitwarden.eu", "lastpass.com", "dashlane.com",
  "keepersecurity.com", "keepersecurity.eu", "nordpass.com", "pass.proton.me", "roboform.com", "enpass.io", "passbolt.com",
  "keeper.io", "vault.zoho.com", "vault.bitwarden.com",
];

export const SIGN_IN_HOSTS = [
  "accounts.google.com", "myaccount.google.com", "login.microsoftonline.com", "login.live.com", "account.live.com",
  "account.microsoft.com", "appleid.apple.com", "idmsa.apple.com", "signin.aws.amazon.com", "okta.com", "oktapreview.com",
  "auth0.com", "onelogin.com", "duosecurity.com", "login.yahoo.com", "id.atlassian.com", "account.proton.me",
  "accounts.firefox.com", "auth.openai.com", "login.gov",
];
/** First host labels of sign-in hosts (login.example.com, sso.example.org). */
const SIGN_IN_LABELS = /^(login|logon|signin|sign-in|auth|sso|idp|accounts?|secure|passport|oauth)$/i;
/** Path segments of sign-in, sign-up and account security pages (also prefixed: wp-login.php, user_login). */
const SIGN_IN_PATH = /(^|\/)([a-z0-9]+[-_])?(log-?in|log-?on|sign-?in|sign_in|sign-?up|sign_up|register|auth|oauth2?|authorize|sso|saml2?|cas|2fa|mfa|otp|verify|password|passwd|reset-password|forgot(-password)?|session|sessions|account\/security|security\/settings)(\/|\.|$)/i;

/** Hosts whose pages no extension may script (the browsers' own add-on stores). */
const STORES = ["chromewebstore.google.com", "chrome.google.com", "addons.mozilla.org", "microsoftedge.microsoft.com"];

function under(host: string, domain: string): boolean {
  return host === domain || host.endsWith(`.${domain}`);
}

export type Rule = { host: RegExp | string; path: string };

/**
 * One line of the user's deny list, or null when it is not a rule. A pasted URL counts:
 * its scheme is dropped. `#` starts a comment.
 */
export function parseRule(line: string): Rule | null {
  let text = line.replace(/#.*/, "").trim().toLowerCase();
  if (!text) return null;
  text = text.replace(/^[a-z][a-z0-9+.-]*:\/\//, "");
  const slash = text.indexOf("/");
  const host = (slash < 0 ? text : text.slice(0, slash)).replace(/:\d+$/, "");
  const path = slash < 0 ? "" : text.slice(slash).replace(/[?#].*$/, "");
  if (!/^[a-z0-9*.-]+$/.test(host) || !/[a-z0-9]/.test(host) || host.includes("..")) return null;
  if (host.startsWith("*.") && !host.slice(2).includes("*")) return { host: host.slice(2), path: path === "/" ? "" : path };
  if (host.includes("*")) {
    const pattern = host.split("*").map((part) => part.replace(/[.\\+?^${}()|[\]-]/g, "\\$&")).join("[a-z0-9.-]*");
    return { host: new RegExp(`^${pattern}$`), path: path === "/" ? "" : path };
  }
  return { host, path: path === "/" ? "" : path };
}

/** The user's rules from the panel's text: the good lines, and the lines that are not rules. */
export function parseRules(text: string): { rules: string[]; invalid: string[] } {
  const rules: string[] = [];
  const invalid: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    (parseRule(line) ? rules : invalid).push(line);
  }
  return { rules, invalid };
}

function matches(rule: Rule, host: string, path: string): boolean {
  const hostOk = typeof rule.host === "string" ? under(host, rule.host) : rule.host.test(host);
  return hostOk && (!rule.path || path === rule.path || path.startsWith(rule.path.endsWith("/") ? rule.path : `${rule.path}/`));
}

/** May this page be mirrored or sent? `url` undefined means the browser hid it (a browser page). */
export function checkPage(url: string | undefined, { incognito = false, rules = [] as string[] } = {}): Verdict {
  if (incognito) return deny("private");
  let parsed: URL;
  try {
    parsed = new URL(url ?? "");
  } catch {
    return deny("browser");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return deny("browser");
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  const path = decodeURIComponentSafe(parsed.pathname).toLowerCase();
  if (STORES.some((store) => under(host, store)) && (host !== "chrome.google.com" || path.startsWith("/webstore"))) return deny("browser");
  if (PASSWORD_MANAGERS.some((domain) => under(host, domain))) return deny("passwords");
  if (BANKING.some((domain) => under(host, domain)) || host.split(".").some((label) => BANK_WORDS.test(label))) return deny("banking");
  if (SIGN_IN_HOSTS.some((domain) => under(host, domain)) || SIGN_IN_LABELS.test(host.split(".")[0]) || SIGN_IN_PATH.test(path)) {
    return deny("sign-in");
  }
  for (const line of rules) {
    const rule = parseRule(line);
    if (rule && matches(rule, host, path)) return deny("yours");
  }
  return { ok: true };
}

function decodeURIComponentSafe(path: string): string {
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}

/** The verdict for a page found to hold a visible password field. */
export function passwordField(): Denied {
  return deny("password-field");
}
