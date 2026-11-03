// The rika! browser extension (Q11/Q12) end to end: the real extension
// (integrations/browser/dist/chromium, rebuilt here) loaded into Chromium, a real rika!
// (`kitty-web --demo`, a disposable synthetic workspace; tests/kittyServer.js) and a local
// test article. Pair from the extension's panel and approve in rika!, switch mirroring on,
// see the frame (screenshot, title, text, selection) in the Mirror pane beside a note, quote
// passages into the note, send a selection to the Inbox, stop on a page with a password
// field, and pair again after rika! revokes the extension.
// Run from web/ with Node >=22, uv, npm deps in integrations/browser and Playwright's
// full Chromium (extensions need it; the headless shell cannot load them):
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/browserMirror.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { startHarness, startKitty } from "./kittyServer.js";

const extensionRoot = fileURLToPath(new URL("../../integrations/browser", import.meta.url));
const extension = join(extensionRoot, "dist", "chromium");
// Screenshots for review land here when the folder can be made; the test never depends on them.
const SHOTS = process.env.KITTY_MIRROR_SHOTS ?? "/tmp/kitty-crew/browser-mirror";
const TITLE = "The Mirror Test Article";
const PARAGRAPHS = [
  "Kittens learn to hunt by watching their mother stalk and pounce in the long grass of the garden.",
  "The second paragraph explains how a cat's whiskers measure gaps before the body tries to squeeze through.",
  "A third paragraph closes the article with notes on sleep: cats rest for more than twelve hours a day.",
];
const article = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${TITLE}</title>
<style>body{font:18px/1.6 serif;max-width:40em;margin:2em auto;background:#fdf6e3;color:#222} h1{color:#b5382b}</style></head>
<body><header><nav>Home · Articles · About</nav></header><main><article><h1>${TITLE}</h1>
${PARAGRAPHS.map((text, index) => `<p id="p${index + 1}">${text}</p>`).join("\n")}
${Array.from({ length: 12 }, (_, index) => `<p>Filler paragraph ${index + 1} keeps the page long enough to scroll and for Readability to find the article body.</p>`).join("\n")}
</article></main><footer>© Synthetic test page</footer></body></html>`;
const members = `<!doctype html><html><head><title>Members area</title></head><body><h1>Members</h1>
<p>This synthetic page holds a password field, so the extension must stop mirroring it.</p>
<form><label>Password <input type="password" name="pw"></label></form></body></html>`;

let kitty;
let vite;
let site;
let siteOrigin;
let profile;
let context;
let kittyBrowser;
let kittyPage;
let extensionId;
let tabPage;
let panel;
let pageNumber;
const problems = [];

async function shot(page, name) {
  try {
    mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({ path: join(SHOTS, `${name}.png`) });
  } catch { /* Optional evidence only. */ }
}

async function until(check, what, timeout = 30_000) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) assert.fail(`timed out waiting until ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

const pageBlocks = async () => (await kitty.request(`/blocks/page?type=page&number=${pageNumber}`)).blocks;
const mirrorPane = () => kittyPage.getByRole("complementary", { name: "Mirror" });

before(async () => {
  execFileSync("node", ["build.mjs", "chromium"], { cwd: extensionRoot, stdio: "inherit" });
  kitty = await startKitty();
  vite = await startHarness(kitty.origin);
  site = createServer((request, response) => {
    const body = request.url.startsWith("/members") ? members : article;
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(body);
  });
  await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
  siteOrigin = `http://127.0.0.1:${site.address().port}`;
  const notebook = await kitty.request("/notes/notebooks", { title: "Reading notes" });
  pageNumber = (await kitty.request("/notes/pages", { notebook_id: notebook.id, title: "Cats, a reading" })).id;

  // The browser the person reads in: its own throwaway profile, the extension loaded.
  profile = mkdtempSync(join(tmpdir(), "kitty-extension-profile-"));
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    viewport: { width: 1100, height: 800 },
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] ?? await context.waitForEvent("serviceworker");
  extensionId = new URL(worker.url()).host;
  tabPage = context.pages()[0] ?? await context.newPage();
  tabPage.on("pageerror", (error) => problems.push(`article: ${error.message}`));
  await tabPage.goto(`${siteOrigin}/article`);
  panel = await context.newPage();
  panel.on("pageerror", (error) => problems.push(`panel: ${error.message}`));
  await panel.goto(`chrome-extension://${extensionId}/panel.html`);
  const tabId = await panel.evaluate(async (url) => (await chrome.tabs.query({ url: `${url}/*` }))[0]?.id, siteOrigin);
  assert.ok(Number.isInteger(tabId), "the article's tab");
  // The side panel follows the active tab; opened as a page it follows the tab it is given.
  await panel.goto(`chrome-extension://${extensionId}/panel.html?tab=${tabId}`);

  // rika!'s own window, in another browser, as on the desktop.
  kittyBrowser = await chromium.launch();
  const kittyContext = await kittyBrowser.newContext({ viewport: { width: 1400, height: 900 } });
  await kitty.authorize(kittyContext);
  await kittyContext.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  // The note beside the Mirror pane needs the room the terminal dock takes in the demo.
  await kittyContext.route("**/api/build/preferences", (route) => route.fulfill({ json: { ok: true } }));
  await kittyContext.route("**/api/build", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.build.ui.dock = { mode: "hidden", height: null, shown: "terminal" };
    await route.fulfill({ response, json: body });
  });
  kittyPage = await kittyContext.newPage();
  kittyPage.on("pageerror", (error) => problems.push(`kitty: ${error.message}`));
  await kittyPage.goto(new URL(`#notes/${pageNumber}`, vite.resolvedUrls.local[0]).href, { timeout: 90_000 });
  await kittyPage.getByRole("textbox", { name: "Page title" }).waitFor({ timeout: 90_000 });
});

after(async () => {
  await context?.close();
  await kittyBrowser?.close();
  await vite?.close();
  await kitty?.stop();
  site?.close();
  if (profile) rmSync(profile, { recursive: true, force: true });
});

test("pair from the panel: the same code in both, approved in rika!", async () => {
  await panel.getByText("Not connected to rika! yet.").waitFor();
  await panel.getByLabel("rika!'s address").fill(kitty.origin.replace("127.0.0.1", "localhost"));
  await panel.getByRole("button", { name: "Connect" }).click();
  const code = await until(async () => (await panel.locator("#code").textContent())?.trim(), "the panel shows a code");
  assert.match(code, /^[A-Z2-9]{3}-[A-Z2-9]{3}$/);
  await shot(panel, "1-panel-pairing-code");
  const bar = kittyPage.getByText(`Approve only if it shows code ${code}`);
  await bar.waitFor({ timeout: 20_000 });
  await shot(kittyPage, "2-kitty-pairing-request");
  await kittyPage.getByRole("button", { name: "Approve" }).click();
  await panel.getByText(/Connected to rika! at http:\/\/127\.0\.0\.1:\d+ as “rika! for Chromium”/).waitFor({ timeout: 20_000 });
  assert.equal(await panel.getByLabel("rika!'s address").inputValue(), kitty.origin, "localhost became 127.0.0.1");
  const { clients, address } = await kitty.request("/clients");
  assert.equal(address, kitty.origin);
  assert.deepEqual(clients.map((client) => [client.name, client.kind, client.scopes, client.origin]),
    [["rika! for Chromium", "browser-extension", ["capture"], `chrome-extension://${extensionId}`]]);
});

test("mirror the tab: screenshot, title, URL and readable text appear in the Mirror pane", async () => {
  await panel.getByRole("switch", { name: "Mirror this tab to rika!" }).check();
  await tabPage.bringToFront();
  await kittyPage.getByRole("button", { name: /^Mirror/ }).click();
  const pane = mirrorPane();
  await pane.getByRole("link", { name: TITLE }).waitFor({ timeout: 20_000 });
  assert.equal(await pane.getByRole("link", { name: TITLE }).getAttribute("href"), `${siteOrigin}/article`);
  for (const text of PARAGRAPHS) await pane.getByText(text, { exact: true }).waitFor();
  assert.equal(await pane.getByText("Home · Articles · About").count(), 0, "Readability left the page chrome out");
  // The screenshot comes once the tab is the visible one (frames are a few seconds apart).
  const image = pane.getByRole("img", { name: `Screenshot of ${TITLE}` });
  await image.waitFor({ timeout: 20_000 });
  await until(() => image.evaluate((element) => element.complete && element.naturalWidth > 0), "the screenshot loads");
  const size = await image.evaluate((element) => [element.naturalWidth, element.naturalHeight]);
  assert.ok(size[0] >= 800 && size[1] >= 500, `a real viewport capture, got ${size}`);
  const listed = await kitty.request("/mirror");
  assert.equal(listed.mirrors.length, 1);
  assert.equal(listed.mirrors[0].image.type, "image/jpeg");
  assert.equal(listed.retention.memory_only, true);
  await shot(kittyPage, "3-kitty-mirror-pane");
  await shot(panel, "4-panel-mirroring");
});

test("quote a passage into the note: a callout linked to the page, with the time, holding the quote", async () => {
  const pane = mirrorPane();
  await pane.getByRole("button", { name: "Quote passage 2" }).click();
  const quoted = await until(async () => {
    const blocks = await pageBlocks();
    const quote = blocks.find((block) => block.type === "quote" && block.text === PARAGRAPHS[1]);
    return quote && { quote, head: blocks.find((block) => block.id === quote.parent_id) };
  }, "the quote is in the note");
  assert.equal(quoted.head.type, "callout");
  assert.match(quoted.head.text, new RegExp(`^${TITLE} · \\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}$`));
  assert.equal(quoted.head.properties.source_url, `${siteOrigin}/article`);
  assert.deepEqual(quoted.head.properties.marks, [{ s: 0, e: TITLE.length, t: "a", href: `${siteOrigin}/article` }]);
  assert.ok(Date.parse(quoted.head.properties.quoted_at));
  // The editor shows it as soon as it is written.
  await kittyPage.getByRole("textbox", { name: /Block/ }).filter({ hasText: PARAGRAPHS[1] }).waitFor();
});

test("the selection made in the browser reaches rika! and can be quoted; text selected in the pane too", async () => {
  await tabPage.evaluate(() => {
    const range = document.createRange();
    range.selectNodeContents(document.getElementById("p3"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  const pane = mirrorPane();
  const selected = pane.getByRole("region", { name: "Selected in the browser" });
  await selected.getByText(/cats rest for more than twelve hours/).waitFor({ timeout: 20_000 });
  await pane.getByRole("button", { name: "Quote the browser selection" }).click();
  await until(async () => (await pageBlocks()).some((block) => block.type === "quote" && block.text === PARAGRAPHS[2]), "the browser selection is quoted");

  // Select part of a paragraph in the Mirror pane itself.
  await pane.getByText(PARAGRAPHS[0], { exact: true }).evaluate((element) => {
    const text = element.firstChild;
    const range = document.createRange();
    range.setStart(text, 0);
    range.setEnd(text, "Kittens learn to hunt".length);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await pane.getByRole("button", { name: "Quote selection" }).click();
  await until(async () => (await pageBlocks()).some((block) => block.type === "quote" && block.text === "Kittens learn to hunt"), "the pane selection is quoted");
  const quotes = (await pageBlocks()).filter((block) => block.type === "quote").map((block) => block.text);
  assert.deepEqual(quotes, [PARAGRAPHS[1], PARAGRAPHS[2], "Kittens learn to hunt"], "quotes follow each other in reading order");
  await shot(kittyPage, "5-kitty-quotes-in-note");
});

test("send the selection to rika!'s Inbox from the panel", async () => {
  await panel.getByRole("button", { name: "Send selection to rika! Inbox" }).click();
  await panel.getByText(/Sent to rika!'s Inbox \(page \d+\)/).waitFor({ timeout: 20_000 });
  const number = Number(/page (\d+)/.exec(await panel.locator("#send-result").textContent())[1]);
  const inbox = await kitty.request(`/blocks/page?type=page&number=${number}`);
  const head = inbox.blocks.find((block) => block.type === "callout" && block.properties.source_url === `${siteOrigin}/article`);
  assert.ok(head, "a callout linked to the page");
  assert.ok(inbox.blocks.some((block) => block.parent_id === head.id && block.type === "quote" && block.text === PARAGRAPHS[2]));
});

test("a page with a password field stops mirroring; rika! drops the frame at once", async () => {
  await tabPage.goto(`${siteOrigin}/members`);
  await panel.getByText(/Mirroring stopped: This page has a password field/).waitFor({ timeout: 20_000 });
  assert.equal(await panel.getByRole("switch", { name: "Mirror this tab to rika!" }).isChecked(), false);
  await mirrorPane().getByText("No tab is being mirrored.", { exact: false }).waitFor({ timeout: 20_000 });
  assert.deepEqual((await kitty.request("/mirror")).mirrors, []);
  // Nothing from the members page reached rika!: no mirror, no block.
  assert.ok(!(await pageBlocks()).some((block) => /Members/.test(block.text)));
  await shot(panel, "6-panel-stopped-password-field");
});

test("revoked in rika!: the next frame gets 401 and the panel pairs again by itself", async () => {
  await tabPage.goto(`${siteOrigin}/article`);
  const toggle = panel.getByRole("switch", { name: "Mirror this tab to rika!" });
  await until(async () => !(await toggle.isDisabled()), "the switch is available again");
  await toggle.check();
  await tabPage.bringToFront();
  await until(async () => (await kitty.request("/mirror")).mirrors.length === 1, "mirroring again");
  const [client] = (await kitty.request("/clients")).clients;
  await kitty.request("/clients/revoke", { id: client.id });
  assert.deepEqual((await kitty.request("/mirror")).mirrors, [], "revoking drops its mirror");
  await tabPage.mouse.wheel(0, 600);
  const code = await until(async () => (await panel.locator("#code").isVisible()) && (await panel.locator("#code").textContent())?.trim(),
    "the panel shows a new pairing code", 30_000);
  const waiting = await until(async () => (await kitty.request("/clients")).requests.find((request) => request.code === code), "rika! lists the new request");
  assert.equal(waiting.name, "rika! for Chromium");
  await shot(panel, "7-panel-pairing-again");
  assert.deepEqual(problems, []);
});
