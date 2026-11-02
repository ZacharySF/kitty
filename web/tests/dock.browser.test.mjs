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

async function harness(search = "") {
  const context = await browser.newContext({ viewport: { width: 1000, height: 800 } });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await open(page, search);
  return { page, problems, context };
}
async function open(page, search = "") {
  await page.goto(new URL(`tests/dock-harness.html${search}`, base).href);
  await page.waitForFunction(() => !!window.dockHarness);
}
const dock = (page) => page.locator('[data-kitty-panel="dock"]');
const height = async (locator) => Math.round((await locator.boundingBox()).height);
/** The dock as saved in the (stand-in) build file: `ui.dock`. */
const saved = (page) => page.evaluate(() => window.harnessBuild.build.ui.dock);
const dockSpace = (page) => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--dock-space").trim());

test("opens on herdr's terminal, sized to the empty area below the main panel", async () => {
  const { page, problems, context } = await harness();
  // Window 800 high, main panel content ends at 500: the dock takes the 300 below.
  await dock(page).waitFor();
  assert.equal(await height(dock(page)), 300);
  assert.equal(await dockSpace(page), "300px");
  assert.deepEqual(await saved(page), { mode: "terminal", height: 300, shown: "terminal" });
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
  assert.equal(await page.evaluate(() => window.harnessBuild.build.ui.terminal.coding_agent), "codex");
  await page.getByRole("button", { name: "Attach external agent" }).click();
  const instructions = page.getByRole("region", { name: "External agent attachment" });
  await instructions.waitFor();
  assert.match(await instructions.innerText(), /--session 'kitty-sample'/);
  assert.match(await instructions.innerText(), /tab create.*--label/);
  assert.match(await instructions.innerText(), /\/sample\/workspace/);
  assert.deepEqual(problems, []);
  await context.close();
});

test("without an installed Tern nothing Tern-related renders in the dock", async () => {
  const { page, problems, context } = await harness();
  await page.getByRole("button", { name: "Agents" }).click();
  await page.getByRole("list", { name: "herdr agent activity" }).waitFor();
  await page.waitForFunction(() => window.dockHarness.calls.some((call) => call.url === "/api/terminal/tern"));
  assert.equal(await page.getByRole("region", { name: "Tern" }).count(), 0);
  assert.equal(await dock(page).getByText(/tern/i).count(), 0);
  assert.deepEqual(await page.evaluate(() => window.dockHarness.calls.filter((call) => call.method === "POST" && call.url.includes("tern"))), []);
  assert.deepEqual(problems, []);
  await context.close();
});

test("an installed Tern lists kitty!'s panes in the dock: run a line, focus, capture, open the note", async () => {
  const { page, problems, context } = await harness("?tern=1");
  const posted = (url) => page.evaluate((target) => window.dockHarness.calls
    .filter((call) => call.url === target && call.method === "POST")
    .map((call) => ({ body: call.body, guarded: call.headers["X-Kitty-Request"] === "1" })), url);
  await page.getByRole("button", { name: "Agents" }).click();
  const tern = page.getByRole("region", { name: "Tern" });
  const panes = tern.getByRole("list", { name: "Tern panes" });
  await panes.waitFor();
  // herdr's agent strip stays; Tern is additive.
  await page.getByRole("list", { name: "herdr agent activity" }).waitFor();
  assert.match(await panes.getByRole("listitem").filter({ hasText: "cargo test" }).innerText(), /working/i);
  assert.match(await panes.getByRole("listitem").filter({ hasText: "make check" }).innerText(), /exited 3/i);
  assert.match(await tern.getByRole("list", { name: "Recent Tern activity" }).innerText(), /pane 5 exited 3/i);

  // The focused pane is the default target; an exited pane cannot take a command.
  const target = tern.getByRole("combobox", { name: "Tern pane" });
  assert.equal(await target.inputValue(), "4");
  const command = tern.getByRole("textbox", { name: "Command line for the Tern pane" });
  await command.fill("echo kitty-$((6*7))");
  await tern.getByRole("button", { name: "Run" }).click();
  await panes.getByRole("listitem").filter({ hasText: "echo kitty-$((6*7))" }).waitFor();
  assert.deepEqual(await posted("/api/terminal/tern/run"), [{ body: { block: 4, text: "echo kitty-$((6*7))" }, guarded: true }]);
  assert.equal(await command.inputValue(), "");
  await target.selectOption("5");
  await command.fill("ls");
  assert.equal(await tern.getByRole("button", { name: "Run" }).isDisabled(), true);

  await tern.getByRole("button", { name: "Focus make check in Tern" }).click();
  await tern.getByRole("status").filter({ hasText: "Showing make check in kitty!'s Tern window." }).waitFor();
  assert.deepEqual(await posted("/api/terminal/tern/focus"), [{ body: { block: 5 }, guarded: true }]);

  await tern.getByRole("button", { name: "Capture echo kitty-$((6*7)) to a quick note" }).click();
  await tern.getByRole("status").filter({ hasText: "Saved to quick note #7." }).waitFor();
  assert.deepEqual(await posted("/api/terminal/tern/capture"), [{ body: { block: 4 }, guarded: true }]);
  await tern.getByRole("button", { name: "Open note" }).click();
  assert.deepEqual((await page.evaluate(() => window.dockHarness.opened)).map((item) => item.url), ["/#quick-note/7"]);

  await tern.getByRole("button", { name: "New pane" }).click();
  await panes.getByRole("listitem").nth(2).waitFor();
  assert.deepEqual(await posted("/api/terminal/tern/new"), [{ body: {}, guarded: true }]);
  // The new pane becomes the Run target.
  assert.equal(await target.inputValue(), "6");
  await tern.getByRole("button", { name: "Open in Tern" }).click();
  assert.deepEqual(await posted("/api/terminal/tern/open"), [{ body: {}, guarded: true }]);
  // Tern draws in its own window: no Tern action attached a terminal (only herdr's, before Agents).
  assert.equal(await page.evaluate(() => window.dockHarness.sockets.length), 1);
  assert.deepEqual(problems, []);
  await context.close();
});

test("a refused Tern command is reported where it was typed", async () => {
  const { page, problems, context } = await harness("?tern=1");
  await page.evaluate(() => { window.dockHarness.ternErrors["/api/terminal/tern/run"] = "A command for Tern is one line of at most 4096 characters, without control characters"; });
  await page.getByRole("button", { name: "Agents" }).click();
  const tern = page.getByRole("region", { name: "Tern" });
  await tern.getByRole("textbox", { name: "Command line for the Tern pane" }).fill("echo hi");
  await tern.getByRole("button", { name: "Run" }).click();
  await tern.getByRole("alert").filter({ hasText: "one line of at most 4096 characters" }).waitFor();
  assert.equal(await tern.getByRole("textbox", { name: "Command line for the Tern pane" }).inputValue(), "echo hi");
  assert.deepEqual(problems, []);
  await context.close();
});
