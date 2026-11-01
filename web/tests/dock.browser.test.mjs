// Dock behaviour (docs/terminal-dock-design.md Q1, Q3, Q4, Q8) in a real browser
// against a stand-in API and WebSocket; no herdr session is involved.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/dock.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
let server;
let browser;
let base;

before(async () => {
  server = await createServer({ root, logLevel: "error", server: { host: "127.0.0.1", port: 0 } });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  await server?.close();
});

async function harness() {
  const context = await browser.newContext({ viewport: { width: 1000, height: 800 } });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await open(page);
  return { page, problems, context };
}
async function open(page) {
  await page.goto(new URL("tests/dock-harness.html", base).href);
  await page.waitForFunction(() => !!window.dockHarness);
}
const dock = (page) => page.locator('[data-kitty-panel="dock"]');
const height = async (locator) => Math.round((await locator.boundingBox()).height);
const saved = (page) => page.evaluate(() => JSON.parse(localStorage.getItem("kitty.dock")));
const dockSpace = (page) => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--dock-space").trim());

test("opens on herdr's terminal, sized to the empty area below the main panel", async () => {
  const { page, problems, context } = await harness();
  // Window 800 high, main panel content ends at 500: the dock takes the 300 below.
  await dock(page).waitFor();
  assert.equal(await height(dock(page)), 300);
  assert.equal(await dockSpace(page), "300px");
  assert.deepEqual(await saved(page), { mode: "terminal", height: 300, shown: "terminal", float: false });
  const terminal = dock(page).locator("[data-kitty-terminal]");
  await terminal.waitFor();
  await page.waitForFunction(() => window.dockHarness.sockets.length === 1);
  const [request] = await page.evaluate(() => window.dockHarness.calls.filter((call) => call.url === "/api/terminal/session"));
  assert.equal(request.method, "POST");
  assert.equal(request.headers["X-Kitty-Request"], "1");
  const url = new URL(await page.evaluate(() => window.dockHarness.sockets[0].url));
  assert.equal(url.origin, new URL(base).origin.replace("http", "ws"));
  assert.equal(url.pathname, "/api/terminal/socket");
  assert.equal(url.searchParams.get("token"), "token-1");
  assert.ok(Number(url.searchParams.get("cols")) > 20 && Number(url.searchParams.get("rows")) > 3);
  await page.evaluate(() => window.dockHarness.emit("SAMPLE-HERDR ready\r\n"));
  await page.waitForFunction(() => document.querySelector("[data-kitty-terminal] .xterm-rows")?.textContent.includes("SAMPLE-HERDR ready"));
  // Transparent: the dock's glass, not an opaque terminal background, shows through.
  const background = await terminal.locator(".xterm-viewport").evaluate((node) => getComputedStyle(node).backgroundColor);
  assert.match(background, /rgba\(0, 0, 0, 0\)|transparent/);
  assert.deepEqual(problems, []);
  await context.close();
});

test("dragging the divider resizes the dock and the height is remembered", async () => {
  const { page, problems, context } = await harness();
  await page.waitForFunction(() => window.dockHarness.sockets[0]?.readyState === 1);
  const rows = Number(new URL(await page.evaluate(() => window.dockHarness.sockets[0].url)).searchParams.get("rows"));
  const divider = page.getByRole("separator", { name: "Resize dock" });
  const box = await divider.boundingBox();
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y - 60, { steps: 4 });
  await page.mouse.move(x, y - 120, { steps: 4 });
  await page.mouse.up();
  assert.equal(await height(dock(page)), 420);
  assert.equal(await dockSpace(page), "420px");
  // The terminal follows the dock: herdr is told the taller size.
  await page.waitForFunction((before) => window.dockHarness.sent().some((item) =>
    item.text && JSON.parse(item.text).type === "resize" && JSON.parse(item.text).rows > before), rows);
  await divider.focus();
  await page.keyboard.press("ArrowUp");
  assert.equal(await height(dock(page)), 436);
  assert.equal(await divider.getAttribute("aria-valuenow"), "436");
  // A reload keeps the remembered height instead of measuring the empty area again.
  await open(page);
  await dock(page).waitFor();
  assert.equal(await height(dock(page)), 436);
  assert.deepEqual(problems, []);
  await context.close();
});

test("bare f reaches the shell; Ctrl+Shift+F fullscreens the terminal; Escape exits", async () => {
  const { page, problems, context } = await harness();
  await page.waitForFunction(() => window.dockHarness.sockets[0]?.readyState === 1);
  await dock(page).locator("[data-kitty-terminal] .xterm-helper-textarea").focus();
  await page.keyboard.press("f");
  await page.waitForFunction(() => window.dockHarness.sent().some((item) => item.binary === "f"));
  assert.equal(await page.evaluate(() => window.dockHarness.fullscreen()), null);
  await page.keyboard.press("Control+Shift+F");
  assert.equal(await page.evaluate(() => window.dockHarness.fullscreen()), "terminal");
  const viewport = page.viewportSize();
  const box = await dock(page).locator('[data-kitty-panel="terminal"]').boundingBox();
  assert.equal(Math.round(box.height), viewport.height);
  // The pinned top bar's blur paints over anything stacked below it, even while hidden.
  const [dockLayer, topLayer] = await page.evaluate(() => [
    document.querySelector('[data-kitty-panel="dock"]'), document.querySelector(".workbench-top"),
  ].map((node) => Number(getComputedStyle(node).zIndex)));
  assert.ok(dockLayer > topLayer, `fullscreen dock layer ${dockLayer} is below the top bar's ${topLayer}`);
  await page.keyboard.press("Escape");
  assert.equal(await page.evaluate(() => window.dockHarness.fullscreen()), null);
  assert.ok(!(await page.evaluate(() => window.dockHarness.sent())).some((item) => item.binary === "F"));
  assert.deepEqual(problems, []);
  await context.close();
});

test("the agent-activity strip shows herdr's states per workspace and detaches the terminal", async () => {
  const { page, problems, context } = await harness();
  await page.waitForFunction(() => window.dockHarness.sockets.length === 1);
  await page.getByRole("button", { name: "Agents" }).click();
  await page.waitForFunction(() => window.dockHarness.sockets[0].readyState === 3);
  assert.equal(await dock(page).locator("[data-kitty-terminal]").count(), 0);
  const strip = page.getByRole("list", { name: "herdr agent activity" });
  await strip.waitFor();
  const api = strip.getByRole("listitem").filter({ hasText: "api" });
  assert.match(await api.innerText(), /working/i);
  assert.match(await api.getByText("Codex").locator("..").innerText(), /blocked/i);
  assert.match(await strip.getByRole("listitem").filter({ hasText: "docs" }).innerText(), /idle/i);
  assert.equal(await page.getByRole("button", { name: "Agents" }).getAttribute("aria-pressed"), "true");
  assert.equal((await saved(page)).mode, "activity");
  await open(page);
  await page.getByRole("list", { name: "herdr agent activity" }).waitFor();
  assert.equal(await page.evaluate(() => window.dockHarness.sockets.length), 0, "the strip must not attach a terminal");
  assert.deepEqual(problems, []);
  await context.close();
});

test("hiding leaves only a thin handle that brings the dock back", async () => {
  const { page, problems, context } = await harness();
  await page.getByRole("button", { name: "Agents" }).click();
  await page.getByRole("button", { name: "Hide dock" }).click();
  assert.equal(await dock(page).count(), 0);
  const handle = page.getByRole("button", { name: "Show dock" });
  assert.ok((await height(handle)) <= 12, "the handle is a thin strip");
  assert.equal(await dockSpace(page), `${await height(handle)}px`);
  assert.equal((await saved(page)).mode, "hidden");
  await open(page);
  await handle.click();
  await dock(page).waitFor();
  assert.equal(await height(dock(page)), 300);
  // The handle brings back what was shown before hiding, even after a reload.
  await page.getByRole("list", { name: "herdr agent activity" }).waitFor();
  assert.equal((await saved(page)).mode, "activity");
  assert.deepEqual(problems, []);
  await context.close();
});

test("the terminal dock opens the full herdr session as a view", async () => {
  const { page, problems, context } = await harness();
  await page.getByRole("button", { name: "Open herdr view" }).click();
  assert.deepEqual(await page.evaluate(() => window.dockHarness.events), ["open-herdr-view"]);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Agent launch chooses a coding agent, remembers it, and exposes named-session attachment", async () => {
  const { page, problems, context } = await harness();
  await page.getByRole("button", { name: "New Agent tab" }).click();
  const chooser = page.getByRole("combobox", { name: "Coding agent" });
  assert.equal(await chooser.inputValue(), "omp");
  await chooser.selectOption("codex");
  await page.getByRole("button", { name: "Start agent" }).click();
  await page.getByRole("status").filter({ hasText: "Agent tab opened" }).waitFor();
  await page.getByRole("button", { name: "New Agent tab" }).click();
  assert.equal(await chooser.inputValue(), "codex");
  await page.getByRole("button", { name: "Attach external agent" }).click();
  const instructions = page.getByRole("region", { name: "External agent attachment" });
  await instructions.waitFor();
  assert.match(await instructions.innerText(), /--session 'kitty-sample'/);
  assert.match(await instructions.innerText(), /tab create.*--label/);
  assert.match(await instructions.innerText(), /\/sample\/workspace/);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Float detaches the dock into a bottom-right card that frees the page; Hide leaves a pill; Dock puts it back", async () => {
  const { page, problems, context } = await harness();
  await dock(page).waitFor();
  await page.getByRole("button", { name: "Float dock" }).click();
  const card = dock(page);
  await page.waitForFunction(() => document.querySelector('[data-kitty-panel="dock"]')?.dataset.float === "true");
  assert.equal(await dockSpace(page), "0px", "a floating dock still reserves page space");
  const box = await card.boundingBox();
  assert.ok(box.x > 400 && box.x + box.width <= 1000 - 8 && box.y + box.height <= 800 - 8, `card at ${JSON.stringify(box)} is not inset bottom-right`);
  assert.ok(box.width < 1000 - 100, "floating card spans the window");
  assert.equal((await saved(page)).float, true);
  await page.getByRole("button", { name: "Hide dock" }).click();
  const pill = page.getByRole("button", { name: "Show dock" });
  await pill.waitFor();
  const pillBox = await pill.boundingBox();
  assert.ok(pillBox.width < 220 && pillBox.x > 700, `pill at ${JSON.stringify(pillBox)} is not a small bottom-right pill`);
  await pill.click();
  await page.getByRole("button", { name: "Dock to bottom" }).click();
  await page.waitForFunction(() => document.querySelector('[data-kitty-panel="dock"]')?.dataset.float === "false");
  assert.notEqual(await dockSpace(page), "0px");
  assert.equal((await saved(page)).float, false);
  assert.deepEqual(problems, []);
  await context.close();
});
