// The kitty! browser extension's Firefox build (integrations/browser/dist/firefox, rebuilt
// here) loaded into a real headless Firefox as a temporary add-on over WebDriver BiDi
// (`webExtension.install`), with a throwaway profile, against a real kitty! (`kitty-web
// --demo`; tests/kittyServer.js): the sidebar page pairs, mirroring a local article sends
// frames that kitty! lists, and switching off drops them.
// Playwright's Firefox cannot load extensions, so this drives Firefox directly. It needs a
// Firefox binary: KITTY_FIREFOX, else `firefox` on PATH (skipped when there is none).
// Run from web/ with Node >=22 and uv: node --test tests/browserMirrorFirefox.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { startKitty } from "./kittyServer.js";

const extensionRoot = fileURLToPath(new URL("../../integrations/browser", import.meta.url));
const extension = join(extensionRoot, "dist", "firefox");
const GECKO_ID = "kitty-browser@kitty";
const UUID = "5f0c2a64-1d7e-4c1b-9a53-3e6f0b9d2c71";
const TITLE = "Firefox Mirror Article";
const ARTICLE = `<!doctype html><html><head><meta charset="utf-8"><title>${TITLE}</title></head><body><article><h1>${TITLE}</h1>
${Array.from({ length: 8 }, (_, index) => `<p>Paragraph ${index + 1} about foxes, long enough for Readability to keep it as article text.</p>`).join("\n")}
</article></body></html>`;

function firefoxBinary() {
  if (process.env.KITTY_FIREFOX) return process.env.KITTY_FIREFOX;
  try {
    return execFileSync("sh", ["-c", "command -v firefox"], { encoding: "utf8" }).trim() || null;
  } catch {
    return null;
  }
}
const binary = firefoxBinary();

let kitty;
let site;
let siteOrigin;
let profile;
let firefox;
let socket;
let nextId = 0;
const pending = new Map();

/** One WebDriver BiDi command; resolves with its result or rejects with its error. */
function send(method, params = {}) {
  const id = ++nextId;
  const { promise, resolve, reject } = Promise.withResolvers();
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
  return promise;
}

async function evaluate(context, expression) {
  const result = await send("script.evaluate", { expression, target: { context }, awaitPromise: true, resultOwnership: "none" });
  if (result.type === "exception") throw new Error(`${expression}: ${result.exceptionDetails.text}`);
  return result.result.value;
}

async function until(check, what, timeout = 30_000) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) assert.fail(`timed out waiting until ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}

before(async () => {
  if (!binary) return;
  execFileSync("node", ["build.mjs", "firefox"], { cwd: extensionRoot, stdio: "inherit" });
  kitty = await startKitty();
  site = createServer((request, response) => {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(ARTICLE);
  });
  await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
  siteOrigin = `http://127.0.0.1:${site.address().port}`;
  profile = mkdtempSync(join(tmpdir(), "kitty-firefox-profile-"));
  writeFileSync(join(profile, "user.js"), [
    // A known moz-extension:// address for the panel page.
    `user_pref("extensions.webextensions.uuids", ${JSON.stringify(JSON.stringify({ [GECKO_ID]: UUID }))});`,
    // Grant the manifest's host access at install, as the install prompt would.
    `user_pref("extensions.originControls.grantByDefault", true);`,
    `user_pref("browser.shell.checkDefaultBrowser", false);`,
    `user_pref("datareporting.policy.dataSubmissionEnabled", false);`,
  ].join("\n"));
  // --remote-allow-system-access lets BiDi open the extension's own moz-extension:// page.
  firefox = spawn(binary, ["--headless", "--no-remote", "--profile", profile, "--remote-debugging-port", "0", "--remote-allow-system-access", "about:blank"],
    { stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, MOZ_HEADLESS: "1" } });
  let output = "";
  const endpoint = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Firefox did not start:\n${output}`)), 60_000);
    const read = (chunk) => {
      output += chunk;
      const match = /WebDriver BiDi listening on (ws:\/\/[^\s]+)/.exec(output);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    };
    firefox.stdout.on("data", read);
    firefox.stderr.on("data", read);
    firefox.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Firefox exited (${code}):\n${output}`)); });
  });
  socket = new WebSocket(`${endpoint}/session`);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = (event) => {
    const message = JSON.parse(String(event.data));
    const waiting = pending.get(message.id);
    if (!waiting) return;
    pending.delete(message.id);
    if (message.type === "error") waiting.reject(new Error(`${message.error}: ${message.message}`));
    else waiting.resolve(message.result);
  };
  await send("session.new", { capabilities: {} });
});

after(async () => {
  try { socket?.close(); } catch { /* closing anyway */ }
  firefox?.kill("SIGTERM");
  if (firefox && firefox.exitCode === null) await new Promise((resolve) => firefox.once("exit", resolve));
  site?.close();
  await kitty?.stop();
  if (profile) rmSync(profile, { recursive: true, force: true });
});

test("Firefox loads the extension, pairs from its sidebar page and mirrors a tab", { skip: !binary && "no Firefox binary" }, async () => {
  const installed = await send("webExtension.install", { extensionData: { type: "path", path: extension } });
  assert.equal(installed.extension, GECKO_ID);

  const { contexts } = await send("browsingContext.getTree", {});
  const articleTab = contexts[0].context;
  await send("browsingContext.navigate", { context: articleTab, url: `${siteOrigin}/article`, wait: "complete" });
  const { context: panel } = await send("browsingContext.create", { type: "tab" });
  const base = `moz-extension://${UUID}/panel.html`;
  await send("browsingContext.navigate", { context: panel, url: base, wait: "complete" });
  const tabId = await evaluate(panel, `browser.tabs.query({}).then((tabs) => tabs.find((tab) => (tab.url ?? "").startsWith(${JSON.stringify(siteOrigin)}))?.id ?? JSON.stringify(tabs))`);
  assert.ok(Number.isInteger(tabId), `the article's tab id (tabs: ${tabId})`);
  await send("browsingContext.navigate", { context: panel, url: `${base}?tab=${tabId}`, wait: "complete" });
  const text = (id) => evaluate(panel, `document.getElementById(${JSON.stringify(id)}).textContent.trim()`);
  await until(async () => (await text("connection")) === "Not connected to kitty! yet.", "the panel is ready");
  assert.equal(await evaluate(panel, "browser.permissions.contains({ origins: ['<all_urls>'] })"), true, "host access granted");

  await evaluate(panel, `document.getElementById("address").value = ${JSON.stringify(kitty.origin)}; document.getElementById("connect").click(); true`);
  const code = await until(async () => (await text("code")) || null, "a pairing code");
  const request = await until(async () => (await kitty.request("/clients")).requests.find((item) => item.code === code), "kitty! lists the request");
  assert.equal(request.name, "kitty! for Firefox");
  assert.match(request.origin, new RegExp(`^moz-extension://${UUID}$`));
  await kitty.request("/clients/approve", { id: request.id });
  await until(async () => (await text("connection")).startsWith("Connected to kitty!"), "the panel is connected");

  await evaluate(panel, `document.getElementById("mirror").click(); true`);
  await send("browsingContext.activate", { context: articleTab });
  const mirror = await until(async () => (await kitty.request("/mirror")).mirrors[0], "kitty! receives a frame");
  assert.equal(mirror.title, TITLE);
  assert.equal(mirror.url, `${siteOrigin}/article`);
  assert.match(mirror.text, /Paragraph 1 about foxes/);
  assert.equal(mirror.client.name, "kitty! for Firefox");
  // A screenshot, once the article is the visible tab.
  const shot = await until(async () => (await kitty.request("/mirror")).mirrors[0]?.image, "a screenshot arrives", 20_000);
  assert.equal(shot.type, "image/jpeg");

  await evaluate(panel, `document.getElementById("mirror").click(); true`);
  await until(async () => (await kitty.request("/mirror")).mirrors.length === 0, "switching off drops the mirror");
});
