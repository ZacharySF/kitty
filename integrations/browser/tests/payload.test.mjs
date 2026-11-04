// Throttling, payload building, message checking, the rika! client and the manifests.
import test from "node:test";
import assert from "node:assert/strict";
import { CaptureGate, FRAME_GAP_MS, HEARTBEAT_MS, Pacer } from "../src/lib/pace.ts";
import { buildFrame, buildSnapshot, cut, dataUrlBytes, fitsImage, isExtraction, LIMITS } from "../src/lib/frame.ts";
import { parseMessage } from "../src/lib/messages.ts";
import { Kitty, KittyError, normalizeAddress } from "../src/lib/kitty.ts";
import { CSP, GECKO_ID, manifest } from "../manifest.mjs";

test("frames coalesce: at most one per gap, the first at once", () => {
  const pacer = new Pacer();
  assert.equal(pacer.schedule(1000), 0);
  assert.equal(pacer.schedule(1100), null, "already scheduled: the change rides along");
  pacer.ran(1000);
  assert.equal(pacer.schedule(1500), FRAME_GAP_MS - 500);
  assert.equal(pacer.schedule(1600), null);
  pacer.ran(4000);
  assert.equal(pacer.schedule(9000), 0, "a quiet page sends at once");
  pacer.cancel();
  assert.equal(pacer.schedule(9001), 0, "a cancelled frame frees the slot");
  pacer.ran(9001);
  assert.equal(pacer.quiet(9001 + HEARTBEAT_MS - 1), false);
  assert.equal(pacer.quiet(9001 + HEARTBEAT_MS), true);
});

test("screenshots across tabs stay at least 600 ms apart (Chrome allows two a second)", () => {
  const gate = new CaptureGate();
  assert.equal(gate.reserve(0), 0);
  assert.equal(gate.reserve(100), 500);
  assert.equal(gate.reserve(100), 1100);
  assert.equal(gate.reserve(5000), 0);
});

const page = (fields = {}) => ({ url: "https://example.com/a", title: "  A   page\n", text: " Body \n\n text ", selection: " picked ", passwordField: false, ...fields });
const jpeg = (bytes) => `data:image/jpeg;base64,${Buffer.alloc(bytes, 7).toString("base64")}`;

test("a frame is cut to rika!'s limits and keeps only a screenshot that fits", () => {
  const frame = buildFrame(12, page(), jpeg(1000));
  assert.deepEqual(frame, { tab: "12", url: "https://example.com/a", title: "A page", text: "Body \n\n text", selection: "picked", image: jpeg(1000) });
  assert.equal(buildFrame(1, page(), jpeg(LIMITS.imageBytes + 1)).image, undefined);
  assert.equal(buildFrame(1, page(), "data:image/gif;base64,R0lGOD").image, undefined);
  assert.equal(buildFrame(1, page(), null).image, undefined);
  const long = buildFrame(1, page({ title: "t".repeat(400), text: "x".repeat(LIMITS.text + 5), selection: "s".repeat(LIMITS.selection + 5) }), null);
  assert.equal(long.title.length, LIMITS.title);
  assert.equal(long.text.length, LIMITS.text);
  assert.equal(long.selection.length, LIMITS.selection);
  assert.equal(buildFrame(1, page({ title: "  " }), null).title, "https://example.com/a", "an untitled page is named by its address");
  assert.deepEqual(buildSnapshot(page()), { url: "https://example.com/a", title: "A page", selection: "picked" });
});

test("cutting never splits a surrogate pair; data URL sizes", () => {
  assert.equal(cut("ab😀", 3), "ab");
  assert.equal(cut("ab😀", 4), "ab😀");
  assert.equal(dataUrlBytes(jpeg(1000)), 1000);
  assert.equal(dataUrlBytes(jpeg(1001)), 1001);
  assert.equal(fitsImage(jpeg(10)), true);
  assert.equal(fitsImage("data:image/svg+xml;base64,PHN2Zy8+"), false);
});

test("extractions from the content script are type-checked", () => {
  assert.equal(isExtraction(page()), true);
  for (const bad of [null, "x", {}, page({ passwordField: "no" }), page({ text: 3 })]) assert.equal(isExtraction(bad), false);
});

const ID = "abcdefghijklmnopabcdefghijklmnop";
const BASE = `chrome-extension://${ID}/`;

test("messages: the panel may ask anything; a web page's tab may only say its page changed", () => {
  const panel = { id: ID, url: `${BASE}panel.html` };
  assert.deepEqual(parseMessage({ kind: "mirror", tabId: 4, on: true }, panel, ID, BASE), { from: "panel", message: { kind: "mirror", tabId: 4, on: true } });
  assert.deepEqual(parseMessage({ kind: "status", tabId: 4 }, panel, ID, BASE)?.message, { kind: "status", tabId: 4 });
  assert.deepEqual(parseMessage({ kind: "send-selection", tabId: 4 }, panel, ID, BASE)?.message, { kind: "send-selection", tabId: 4 });
  assert.deepEqual(parseMessage({ kind: "settings-changed" }, panel, ID, BASE)?.message, { kind: "settings-changed" });
  // The panel opened as a page in a tab is still the panel.
  assert.equal(parseMessage({ kind: "status", tabId: 4 }, { ...panel, tab: { id: 9 } }, ID, BASE)?.from, "panel");
  for (const bad of [{ kind: "mirror", tabId: 4, on: "yes" }, { kind: "mirror", tabId: -1, on: true }, { kind: "status" }, { kind: "nope", tabId: 1 }, null, "status"]) {
    assert.equal(parseMessage(bad, panel, ID, BASE), null, JSON.stringify(bad));
  }
  const content = { id: ID, url: "https://example.com/a", tab: { id: 7 } };
  assert.deepEqual(parseMessage({ kind: "dirty" }, content, ID, BASE), { from: "content", tabId: 7, message: { kind: "dirty" } });
  for (const bad of [{ kind: "mirror", tabId: 7, on: true }, { kind: "status", tabId: 7 }, { kind: "settings-changed" }, { kind: "dirty", tabId: 3 }]) {
    assert.equal(parseMessage(bad, content, ID, BASE), null, `a page cannot send ${JSON.stringify(bad)}`);
  }
  assert.equal(parseMessage({ kind: "dirty" }, { ...content, id: "another-extension" }, ID, BASE), null);
  assert.equal(parseMessage({ kind: "status", tabId: 1 }, { id: ID, url: "https://evil.example/" }, ID, BASE), null);
});

test("rika!'s address: loopback only, localhost becomes 127.0.0.1, kitty-web's port by default", () => {
  assert.equal(normalizeAddress("http://127.0.0.1:8765"), "http://127.0.0.1:8765");
  assert.equal(normalizeAddress("127.0.0.1:41234/#settings"), "http://127.0.0.1:41234");
  assert.equal(normalizeAddress("localhost:9000"), "http://127.0.0.1:9000");
  assert.equal(normalizeAddress(" http://127.0.0.1 "), "http://127.0.0.1:8765");
  assert.equal(normalizeAddress("http://127.0.0.1:80/"), "http://127.0.0.1:80");
  for (const bad of ["", "https://127.0.0.1:8765", "http://192.168.1.4:8765", "http://evil.example:8765", "http://user:pw@127.0.0.1:1", "ftp://127.0.0.1"]) {
    assert.equal(normalizeAddress(bad), null, bad);
  }
});

function fakeFetch(answer) {
  const calls = [];
  const fetcher = async (url, init = {}) => {
    calls.push({ url, ...init });
    const { status = 200, body = {}, headers = {} } = await answer(url, init);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
  };
  return { fetcher, calls };
}

test("the client sends the token, JSON bodies and no cookies, and turns errors into KittyError", async () => {
  const { fetcher, calls } = fakeFetch(async (url) => url.endsWith("/mirror/frame") ? { body: { mirror: "m", frame: 1, expires_in: 150 } }
    : url.endsWith("/mirror/stop") ? { status: 401, body: { error: "A paired client token is required" } }
      : { status: 429, body: { error: "slow down" }, headers: { "Retry-After": "2" } });
  const kitty = new Kitty("http://127.0.0.1:9", "kitty_token", fetcher);
  assert.deepEqual(await kitty.frame({ tab: "1", url: "https://e.com/", title: "E", text: "", selection: "" }), { mirror: "m", frame: 1, expires_in: 150 });
  assert.equal(calls[0].url, "http://127.0.0.1:9/api/ingress/mirror/frame");
  assert.equal(calls[0].headers.Authorization, "Bearer kitty_token");
  assert.equal(calls[0].headers["Content-Type"], "application/json");
  assert.equal(calls[0].credentials, "omit");
  await assert.rejects(kitty.stop(1), (error) => error instanceof KittyError && error.status === 401);
  await assert.rejects(kitty.snapshot({ url: "https://e.com/", title: "E", selection: "s" }),
    (error) => error instanceof KittyError && error.status === 429 && error.retryAfter === 2 && error.message === "slow down");
  const offline = new Kitty("http://127.0.0.1:9", null, async () => { throw new TypeError("Failed to fetch"); });
  await assert.rejects(offline.pair("x"), (error) => error.status === 0 && /not reachable/.test(error.message));
});

test("pairing asks for the capture scope only, without a token", async () => {
  const { fetcher, calls } = fakeFetch(async () => ({ status: 202, body: { status: "pending", request: "r", code: "ABC-DEF", expires_in: 300 } }));
  await new Kitty("http://127.0.0.1:9", "stale", fetcher).pair("rika! for Chromium");
  assert.equal(calls[0].headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(calls[0].body), { name: "rika! for Chromium", kind: "browser-extension", scopes: ["capture"] });
});

test("the probe recognizes rika! by its 401 and nothing else", async () => {
  const kittyLike = fakeFetch(async () => ({ status: 401, body: { error: "A paired client token is required" }, headers: { "WWW-Authenticate": 'Bearer realm="kitty"' } }));
  await new Kitty("http://127.0.0.1:9", null, kittyLike.fetcher).probe();
  assert.equal(kittyLike.calls[0].url, "http://127.0.0.1:9/api/ingress/client");
  const other = fakeFetch(async () => ({ status: 200, body: { hello: "world" } }));
  await assert.rejects(new Kitty("http://127.0.0.1:9", null, other.fetcher).probe(), /did not answer like rika!/);
  await assert.rejects(new Kitty("http://127.0.0.1:9", null, async () => { throw new TypeError("refused"); }).probe(), /Nothing answered/);
});

test("manifests: Chrome-family side panel and service worker, Firefox sidebar and event page, MV3 both", () => {
  const chromium = manifest("chromium", "1.2.3");
  const firefox = manifest("firefox", "1.2.3");
  for (const item of [chromium, firefox]) {
    assert.equal(item.manifest_version, 3);
    assert.equal(item.version, "1.2.3");
    assert.deepEqual(item.host_permissions, ["<all_urls>"]);
    assert.equal(item.content_security_policy.extension_pages, CSP);
    assert.equal(item.content_scripts, undefined, "no content script runs unless a tab is mirrored");
    assert.ok(!item.permissions.includes("tabs") && !item.permissions.includes("cookies") && !item.permissions.includes("history"));
  }
  assert.match(CSP, /connect-src http:\/\/127\.0\.0\.1:\*;/);
  assert.deepEqual(chromium.background, { service_worker: "background.js" });
  assert.deepEqual(chromium.side_panel, { default_path: "panel.html" });
  assert.ok(chromium.permissions.includes("sidePanel"));
  assert.equal(chromium.sidebar_action, undefined);
  assert.deepEqual(firefox.background, { scripts: ["background.js"] });
  assert.equal(firefox.sidebar_action.default_panel, "panel.html");
  assert.equal(firefox.browser_specific_settings.gecko.id, GECKO_ID);
  assert.deepEqual(firefox.browser_specific_settings.gecko.data_collection_permissions.required, ["browsingActivity", "websiteContent"]);
  assert.ok(!firefox.permissions.includes("sidePanel"));
  assert.throws(() => manifest("safari", "1"));
});
