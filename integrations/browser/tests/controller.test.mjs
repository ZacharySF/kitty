// The background's mirroring logic and the panel's pairing, driven with a fake browser,
// a fake clock and a fake kitty!.
import test from "node:test";
import assert from "node:assert/strict";
import { Controller } from "../src/lib/controller.ts";
import { Kitty, KittyError } from "../src/lib/kitty.ts";
import { Pairing } from "../src/lib/pairing.ts";
import { FRAME_GAP_MS, HEARTBEAT_MS } from "../src/lib/pace.ts";

const shot = (fill = 1, bytes = 600) => `data:image/jpeg;base64,${Buffer.alloc(bytes, fill).toString("base64")}`;

/** A fake browser with tabs, a manual clock and timers, and a fake kitty! recording requests. */
function world() {
  let now = 1_000_000;
  let timers = [];
  const tabs = new Map([[1, { id: 1, windowId: 10, url: "https://example.com/a", title: "A", active: true, incognito: false }]]);
  const pages = new Map([[1, { url: "https://example.com/a", title: "A", text: "Alpha.\n\nBeta.", selection: "", passwordField: false }]]);
  const sent = [];
  const settings = { address: "http://127.0.0.1:9", token: "kitty_t", rules: [] };
  let answer = () => ({ mirror: "m", frame: sent.length, expires_in: 150 });
  let captures = 0;
  let captureImage = () => shot();
  const changed = [];
  let saved = [];
  const fetcher = async (url, init = {}) => {
    const path = url.replace(/^.*\/api\/ingress/, "");
    sent.push({ path, body: init.body ? JSON.parse(init.body) : null, auth: init.headers?.Authorization });
    try {
      return new Response(JSON.stringify(answer(path)), { status: 200, headers: { "Content-Type": "application/json" } });
    } catch (error) {
      return new Response(JSON.stringify({ error: error.message }), { status: error.status, headers: { "Retry-After": "1" } });
    }
  };
  const controller = new Controller(
    {
      now: () => now,
      setTimer: (run, ms) => { const timer = { at: now + ms, run }; timers.push(timer); return timer; },
      clearTimer: (timer) => { timers = timers.filter((item) => item !== timer); },
      tab: async (id) => (tabs.has(id) ? { ...tabs.get(id) } : null),
      extract: async (id) => {
        if (!pages.has(id)) throw new Error("no receiver");
        return { ...pages.get(id) };
      },
      capture: async () => { captures += 1; return captureImage(); },
    },
    {
      kitty: async () => (settings.token ? new Kitty(settings.address, settings.token, fetcher) : null),
      unpaired: async () => { settings.token = null; },
      rules: async () => settings.rules,
    },
    { save: async (ids) => { saved = ids; }, load: async () => saved, changed: (id) => changed.push(id) },
  );
  /** Advance the clock by `ms`, running due timers (and the work they start) in order. */
  async function advance(ms) {
    const end = now + ms;
    for (;;) {
      await new Promise((resolve) => setImmediate(resolve));
      const due = timers.filter((timer) => timer.at <= end).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      timers = timers.filter((timer) => timer !== due);
      now = Math.max(now, due.at);
      due.run();
      for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setImmediate(resolve));
    }
    now = end;
  }
  const frames = () => sent.filter((item) => item.path === "/mirror/frame").map((item) => item.body);
  return {
    controller, tabs, pages, sent, settings, changed, advance, frames,
    get captures() { return captures; },
    get saved() { return saved; },
    set saved(value) { saved = value; },
    set answer(fn) { answer = fn; },
    set captureImage(fn) { captureImage = fn; },
  };
}

test("switching on sends a frame at once with text, selection and screenshot; off drops it in kitty!", async () => {
  const w = world();
  const status = await w.controller.set(1, true);
  assert.equal(status.on, true);
  assert.deepEqual(w.saved, [1], "kept across background restarts");
  await w.advance(0);
  const [frame] = w.frames();
  assert.deepEqual({ ...frame, image: frame.image.slice(0, 23) }, {
    tab: "1", url: "https://example.com/a", title: "A", text: "Alpha.\n\nBeta.", selection: "", image: "data:image/jpeg;base64,",
  });
  assert.equal(w.sent[0].auth, "Bearer kitty_t");
  assert.ok((await w.controller.status(1)).lastFrame);
  await w.controller.set(1, false);
  assert.deepEqual(w.sent.at(-1), { path: "/mirror/stop", body: { tab: "1" }, auth: "Bearer kitty_t" });
  assert.deepEqual(w.saved, []);
  w.controller.schedule(1);
  await w.advance(10_000);
  assert.equal(w.frames().length, 1, "nothing after switching off");
});

test("changes coalesce into one frame per few seconds; a still page sends a heartbeat", async () => {
  const w = world();
  await w.controller.set(1, true);
  await w.advance(0);
  for (let i = 0; i < 20; i += 1) {
    w.controller.schedule(1);
    await w.advance(100);
  }
  // 2 s of scrolling after the first frame: one more frame, at the 3 s mark.
  assert.equal(w.frames().length, 1);
  await w.advance(FRAME_GAP_MS);
  assert.equal(w.frames().length, 2);
  w.controller.heartbeat();
  await w.advance(1000);
  assert.equal(w.frames().length, 2, "not quiet yet");
  await w.advance(HEARTBEAT_MS);
  w.controller.heartbeat();
  await w.advance(0);
  assert.equal(w.frames().length, 3);
});

test("a background tab sends text without a screenshot; a tab switch during capture discards it", async () => {
  const w = world();
  w.tabs.get(1).active = false;
  await w.controller.set(1, true);
  await w.advance(0);
  assert.equal(w.frames()[0].image, undefined);
  assert.equal(w.captures, 0, "the visible tab is another page: never captured");
  assert.match((await w.controller.status(1)).notice, /text only/);
  w.tabs.get(1).active = true;
  w.captureImage = () => { w.tabs.get(1).active = false; return shot(); };
  w.controller.schedule(1);
  await w.advance(FRAME_GAP_MS);
  assert.equal(w.frames().length, 2);
  assert.equal(w.frames()[1].image, undefined);
});

test("an oversized screenshot is retaken at lower quality, then left out", async () => {
  const w = world();
  let calls = 0;
  w.captureImage = () => (++calls === 1 ? shot(1, 3 * 1024 * 1024) : shot(2));
  await w.controller.set(1, true);
  await w.advance(2000);
  assert.equal(calls, 2);
  assert.equal(w.frames()[0].image, shot(2));
  w.captureImage = () => shot(1, 3 * 1024 * 1024);
  w.controller.schedule(1);
  await w.advance(FRAME_GAP_MS + 2000);
  assert.equal(w.frames()[1].image, undefined);
});

test("denied pages are never mirrored: switching on refuses, navigating there stops and tells kitty!", async () => {
  const w = world();
  w.tabs.get(1).url = "https://www.paypal.com/myaccount";
  const refused = await w.controller.set(1, true);
  assert.equal(refused.on, false);
  assert.equal(refused.verdict.category, "banking");
  assert.equal(w.sent.length, 0);

  w.tabs.get(1).url = "https://example.com/a";
  await w.controller.set(1, true);
  await w.advance(0);
  w.tabs.get(1).url = "https://accounts.google.com/signin";
  await w.controller.navigated(1);
  assert.equal(w.sent.at(-1).path, "/mirror/stop");
  const status = await w.controller.status(1);
  assert.equal(status.on, false);
  assert.match(status.notice, /Mirroring stopped: Sign-in pages/);

  // A page that turns out to hold a password field stops too, without sending it.
  w.tabs.get(1).url = "https://example.com/a";
  await w.controller.set(1, true);
  w.pages.get(1).passwordField = true;
  const before = w.frames().length;
  await w.advance(FRAME_GAP_MS);
  assert.equal(w.frames().length, before);
  assert.match((await w.controller.status(1)).notice, /password field/);

  // Private windows and the person's own rules.
  w.tabs.get(1).incognito = true;
  assert.equal((await w.controller.set(1, true)).verdict.category, "private");
  w.tabs.get(1).incognito = false;
  w.pages.get(1).passwordField = false;
  w.settings.rules = ["example.com"];
  assert.equal((await w.controller.set(1, true)).verdict.category, "yours");
});

test("the deny list changing stops a mirrored tab it now covers", async () => {
  const w = world();
  await w.controller.set(1, true);
  await w.advance(0);
  w.settings.rules = ["example.com/a"];
  await w.controller.recheck();
  assert.equal((await w.controller.status(1)).on, false);
  assert.equal(w.sent.at(-1).path, "/mirror/stop");
});

test("a 401 forgets the token so the panel pairs again; frames wait until it does", async () => {
  const w = world();
  w.answer = (path) => {
    if (path === "/mirror/frame") throw Object.assign(new Error("A paired client token is required"), { status: 401 });
    return {};
  };
  await w.controller.set(1, true);
  await w.advance(0);
  assert.equal(w.settings.token, null);
  const status = await w.controller.status(1);
  assert.equal(status.paired, false);
  assert.equal(status.on, true, "still switched on: it resumes once paired again");
  w.controller.schedule(1);
  await w.advance(FRAME_GAP_MS);
  assert.equal(w.sent.length, 1, "nothing is sent while unpaired");
  assert.match((await w.controller.status(1)).notice, /Connect to kitty!/);
  w.settings.token = "kitty_new";
  w.answer = () => ({ mirror: "m", frame: 1, expires_in: 150 });
  await w.controller.recheck();
  await w.advance(FRAME_GAP_MS);
  assert.equal(w.sent.at(-1).auth, "Bearer kitty_new");
});

test("kitty! saying 'too fast' retries after Retry-After; unreachable kitty! is shown", async () => {
  const w = world();
  let busy = true;
  w.answer = (path) => {
    if (busy) throw Object.assign(new Error("Frames are coming too fast"), { status: 429 });
    return { mirror: "m", frame: 1, expires_in: 150 };
  };
  await w.controller.set(1, true);
  await w.advance(0);
  busy = false;
  await w.advance(FRAME_GAP_MS + 1000);
  assert.equal(w.frames().length, 2);
  w.answer = () => { throw Object.assign(new Error("Local operation failed"), { status: 500 }); };
  w.controller.schedule(1);
  await w.advance(FRAME_GAP_MS);
  assert.match((await w.controller.status(1)).notice, /Local operation failed/);
});

test("closing a mirrored tab stops it; a restarted background carries on with saved tabs", async () => {
  const w = world();
  await w.controller.set(1, true);
  await w.advance(0);
  await w.controller.stop(1, "");
  assert.equal(w.sent.at(-1).path, "/mirror/stop");
  await w.controller.set(1, true);
  // A restarted background (MV3 suspends it) carries on with the saved tabs that still exist.
  const restarted = world();
  restarted.saved = [1, 99];
  await restarted.controller.restore();
  assert.deepEqual(restarted.controller.mirrored(), [1]);
  restarted.controller.schedule(1);
  await restarted.advance(0);
  assert.equal(restarted.frames().length, 1);
});

test("send selection: refused on denied pages and without a selection, else a snapshot in the Inbox", async () => {
  const w = world();
  w.answer = (path) => (path === "/mirror" ? { page: 7, block: "b" } : {});
  assert.deepEqual(await w.controller.sendSelection(1), { ok: false, error: "Select some text on the page first." });
  w.pages.get(1).selection = "  Beta.  ";
  assert.deepEqual(await w.controller.sendSelection(1), { ok: true, page: 7 });
  assert.deepEqual(w.sent.at(-1), { path: "/mirror", body: { url: "https://example.com/a", title: "A", selection: "Beta." }, auth: "Bearer kitty_t" });
  w.tabs.get(1).url = w.pages.get(1).url = "https://my.1password.com/";
  const denied = await w.controller.sendSelection(1);
  assert.equal(denied.ok, false);
  assert.match(denied.error, /Password managers/);
  assert.equal(w.sent.length, 1);
  w.settings.token = null;
  w.tabs.get(1).url = w.pages.get(1).url = "https://example.com/a";
  assert.deepEqual(await w.controller.sendSelection(1), { ok: false, error: "Connect to kitty! first." });
});

/** A fake panel host for Pairing: settings in memory, kitty! answering from a script. */
function pairingWorld(script) {
  let settings = { address: null, token: null, client: null, pending: null, needsPairing: false, rules: [] };
  let now = 0;
  let changes = 0;
  const calls = [];
  const fetcher = async (url, init = {}) => {
    const path = url.replace(/^.*\/api\/ingress/, "");
    calls.push(path);
    const { status = 200, body = {}, headers = {} } = script(path, init);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
  };
  const pairing = new Pairing({
    load: async () => ({ ...settings }),
    save: async (patch) => (settings = { ...settings, ...patch }),
    kitty: (address, token) => new Kitty(address, token, fetcher),
    now: () => now,
    changed: () => { changes += 1; },
  }, "kitty! for Chromium");
  return { pairing, calls, get settings() { return settings; }, set settings(value) { settings = value; }, tick: (ms) => { now += ms; }, get changes() { return changes; } };
}

const KITTY_401 = { status: 401, body: { error: "A paired client token is required" }, headers: { "WWW-Authenticate": 'Bearer realm="kitty"' } };

test("pairing: probe, code, approval, token kept", async () => {
  let approved = false;
  const w = pairingWorld((path) => {
    if (path === "/client" && !approved) return KITTY_401;
    if (path === "/pair") return { status: 202, body: { status: "pending", request: "secret", code: "K7Q-2MX", expires_in: 300 } };
    if (path === "/pair/status") return { body: approved ? { status: "approved", token: "kitty_tok", client: { id: "c1", name: "kitty! for Chromium", scopes: ["capture"] } } : { status: "pending" } };
    return { body: { id: "c1", name: "kitty! for Chromium", scopes: ["capture"] } };
  });
  assert.deepEqual(await w.pairing.check(), { state: "unpaired", message: "Not connected to kitty! yet." });
  assert.deepEqual(await w.pairing.connect("localhost:9"), { state: "waiting", code: "K7Q-2MX", address: "http://127.0.0.1:9" });
  assert.deepEqual(w.calls, ["/client", "/pair"]);
  assert.equal((await w.pairing.poll()).state, "waiting");
  approved = true;
  assert.deepEqual(await w.pairing.poll(), { state: "paired", address: "http://127.0.0.1:9", name: "kitty! for Chromium" });
  assert.equal(w.settings.token, "kitty_tok");
  assert.equal(w.settings.pending, null);
  assert.equal(w.changes, 1, "the background re-checks mirrored tabs");
  assert.equal((await w.pairing.check()).state, "paired");
});

test("pairing: a revoked token (401) is forgotten and pairing starts again by itself", async () => {
  const w = pairingWorld((path) => (path === "/pair" ? { status: 202, body: { status: "pending", request: "again", code: "NEW-COD", expires_in: 300 } } : KITTY_401));
  w.settings = { ...w.settings, address: "http://127.0.0.1:9", token: "kitty_old", client: { id: "c", name: "x" } };
  assert.deepEqual(await w.pairing.check(), { state: "waiting", code: "NEW-COD", address: "http://127.0.0.1:9" });
  assert.equal(w.settings.token, null);
  assert.equal(w.settings.pending.request, "again");
});

test("pairing: denied, expired, wrong addresses and something that is not kitty!", async () => {
  let outcome = "denied";
  const w = pairingWorld((path) => path === "/client" ? KITTY_401
    : path === "/pair" ? { status: 202, body: { status: "pending", request: "r", code: "AAA-BBB", expires_in: 300 } } : { body: { status: outcome } });
  await w.pairing.connect("127.0.0.1:9");
  assert.deepEqual(await w.pairing.poll(), { state: "unpaired", message: "kitty! denied the request." });
  await w.pairing.connect("127.0.0.1:9");
  w.tick(301_000);
  assert.match((await w.pairing.poll()).message, /expired/);
  outcome = "expired";
  await w.pairing.connect("127.0.0.1:9");
  assert.match((await w.pairing.poll()).message, /expired/);
  assert.equal((await w.pairing.connect("https://example.com")).state, "error");
  const other = pairingWorld(() => ({ status: 200, body: {} }));
  const result = await other.pairing.connect("127.0.0.1:9");
  assert.equal(result.state, "error");
  assert.match(result.message, /did not answer like kitty!/);
  assert.deepEqual(other.calls, ["/client"], "never asks a non-kitty! server to pair");
  assert.equal((await w.pairing.forget()).state, "unpaired");
  assert.equal(w.settings.token, null);
});

test("KittyError keeps status and Retry-After", () => {
  const error = new KittyError("x", 429, 3);
  assert.equal(error.status, 429);
  assert.equal(error.retryAfter, 3);
});
