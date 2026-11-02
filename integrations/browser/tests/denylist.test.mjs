// The deny list: what is never mirrored or sent to kitty!.
import test from "node:test";
import assert from "node:assert/strict";
import { checkPage, parseRule, parseRules, passwordField } from "../src/lib/denylist.ts";

const category = (url, options) => {
  const verdict = checkPage(url, options);
  return verdict.ok ? "ok" : verdict.category;
};

test("ordinary web pages may be mirrored", () => {
  for (const url of ["https://example.com/article", "http://en.wikipedia.org/wiki/Cat", "https://news.ycombinator.com/item?id=1",
    "https://github.com/rust-lang/rust/issues/1", "https://docs.python.org/3/library/re.html", "https://example.com/authors/jane"]) {
    assert.equal(category(url), "ok", url);
  }
});

test("browser pages, files, extension pages and the add-on stores are never mirrored", () => {
  for (const url of ["about:config", "about:blank", "chrome://settings", "edge://flags", "brave://wallet", "file:///home/me/notes.txt",
    "chrome-extension://abc/panel.html", "moz-extension://1234/panel.html", "view-source:https://example.com", "data:text/html,hi",
    "javascript:alert(1)", undefined, "", "not a url", "https://chromewebstore.google.com/detail/x", "https://chrome.google.com/webstore/x",
    "https://addons.mozilla.org/en-US/firefox/addon/x", "https://microsoftedge.microsoft.com/addons/detail/x"]) {
    assert.equal(category(url), "browser", String(url));
  }
  assert.equal(category("https://chrome.google.com/intl/en/chrome/"), "ok", "only the store path of chrome.google.com");
});

test("private and incognito windows are never mirrored", () => {
  assert.equal(category("https://example.com/", { incognito: true }), "private");
});

test("banking, payment and brokerage sites are never mirrored", () => {
  for (const url of ["https://www.paypal.com/myaccount", "https://secure07c.chase.com/web/auth", "https://www.bankofamerica.com/",
    "https://online.mybank.co.uk/", "https://www.sparkasse-koeln.de/", "https://checkout.stripe.com/pay/x", "https://www.coinbase.com/",
    "https://my.creditunion.org/", "https://app.ynab.com/"]) {
    assert.equal(category(url), "banking", url);
  }
});

test("password managers are never mirrored", () => {
  for (const url of ["https://my.1password.com/vaults", "https://vault.bitwarden.com/#/vault", "https://lastpass.com/",
    "https://pass.proton.me/", "https://app.dashlane.com/"]) {
    assert.equal(category(url), "passwords", url);
  }
});

test("sign-in pages are never mirrored, by host and by path", () => {
  for (const url of ["https://accounts.google.com/v3/signin", "https://login.microsoftonline.com/common/oauth2", "https://github.com/login",
    "https://github.com/session", "https://example.com/users/sign_in", "https://example.com/sign-in?next=/", "https://example.com/oauth/authorize",
    "https://acme.okta.com/app/x", "https://login.example.org/", "https://sso.example.org/", "https://example.com/account/security",
    "https://example.com/reset-password/abc", "https://example.com/login.php", "https://example.com/wp-login.php"]) {
    assert.notEqual(category(url), "ok", url);
  }
  assert.equal(category("https://github.com/login"), "sign-in");
});

test("the person's own rules", () => {
  const rules = ["intranet.example.com", "*.corp.example", "*shop*", "news.example.org/private", "https://pasted.example.net/path?x=1"];
  assert.equal(category("https://intranet.example.com/wiki", { rules }), "yours");
  assert.equal(category("https://team.intranet.example.com/", { rules }), "yours", "subdomains too");
  assert.equal(category("https://example.com/", { rules }), "ok");
  assert.equal(category("https://a.corp.example/x", { rules }), "yours");
  assert.equal(category("https://myshop.example/", { rules }), "yours");
  assert.equal(category("https://news.example.org/private/2026", { rules }), "yours");
  assert.equal(category("https://news.example.org/private", { rules }), "yours");
  assert.equal(category("https://news.example.org/privateer", { rules }), "ok", "path segments, not prefixes");
  assert.equal(category("https://news.example.org/public", { rules }), "ok");
  assert.equal(category("https://pasted.example.net/path/sub", { rules }), "yours");
  assert.equal(category("https://pasted.example.net/other", { rules }), "ok");
});

test("rules parse from the panel's text; non-rules are reported", () => {
  assert.deepEqual(parseRules("example.com\n# a comment\n\n  *.corp.example  \nnot a rule!\n*\nfoo..bar"), {
    rules: ["example.com", "*.corp.example"], invalid: ["not a rule!", "*", "foo..bar"],
  });
  assert.deepEqual(parseRule("Example.COM/Path/"), { host: "example.com", path: "/path/" });
  assert.equal(parseRule("# only a comment"), null);
  assert.equal(parseRule("exa mple.com"), null);
});

test("a password field has its own reason", () => {
  const verdict = passwordField();
  assert.equal(verdict.ok, false);
  assert.equal(verdict.category, "password-field");
  assert.match(verdict.reason, /password field/);
});
