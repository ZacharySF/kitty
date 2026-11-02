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

/** Opens the harness; `seed` is saved dock state written once before the first load. */
async function harness(seed) {
  const context = await browser.newContext({ viewport: { width: 1000, height: 800 } });
  if (seed)
    await context.addInitScript((value) => {
      if (!sessionStorage.getItem("seeded")) {
        localStorage.setItem("kitty.dock", JSON.stringify(value));
        sessionStorage.setItem("seeded", "1");
      }
    }, seed);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await open(page);
  return { page, problems, context };
}
const FLOATING = { mode: "terminal", shown: "terminal", float: true, step: "m", size: null, offset: { right: 16, bottom: 16 } };
async function open(page) {
  await page.goto(new URL("tests/dock-harness.html", base).href);
  await page.waitForFunction(() => !!window.dockHarness);
}
const dock = (page) => page.locator('[data-kitty-panel="dock"]');
const height = async (locator) => Math.round((await locator.boundingBox()).height);
const box = async (locator) => {
  const { x, y, width, height } = await locator.boundingBox();
  return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) };
};
const saved = (page) => page.evaluate(() => JSON.parse(localStorage.getItem("kitty.dock")));
const CORNER = { right: 16, bottom: 16 };
/** The 1000×800 window minus the default card: min(720px, 92vw) × min(440px, 60vh), 16px from the corner. */
const DEFAULT_CARD = { x: 1000 - 16 - 720, y: 800 - 16 - 440, width: 720, height: 440 };
/** Q56: the Agents view shows one live card per herdr agent. */
const agentCards = (page) => page.getByRole("region", { name: "Agent activity" }).getByRole("article");

/** The docked panel: medium is 40% of the 800px window, full width at the bottom. */
const DOCKED = { x: 0, y: 800 - 320, width: 1000, height: 320 };
const dockSpace = (page) => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--dock-space").trim());

/** The card floats over the page: the page keeps the whole window and reserves nothing. */
async function assertFloating(page) {
  assert.deepEqual(await box(page.locator("#view")), { x: 0, y: 0, width: 1000, height: 800 });
  assert.equal(await dockSpace(page), "0px");
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('[data-kitty-panel="dock"]')).position), "fixed");
  assert.equal(await page.getByRole("separator", { name: "Resize dock" }).count(), 0, "the docked height divider is back");
  assert.equal(await page.getByRole("button", { name: "Dock to bottom" }).count(), 1);
}

/** Docked at the bottom: the page ends above the panel, and nothing drags its height. */
async function assertDocked(page, height) {
  assert.deepEqual(await box(dock(page)), { x: 0, y: 800 - height, width: 1000, height });
  assert.equal(await dockSpace(page), `${height}px`);
  assert.deepEqual(await box(page.locator("#view")), { x: 0, y: 0, width: 1000, height: 800 - height });
  assert.equal(await page.getByRole("separator").count(), 0, "a height divider is offered");
  assert.equal(await page.getByRole("button", { name: "Resize dock" }).count(), 0, "a resize grip is offered while docked");
  assert.equal(await page.getByRole("button", { name: "Float dock" }).count(), 1);
  const resizable = await dock(page).evaluate((node) =>
    [node, ...node.querySelectorAll("*")].filter((el) => /resize/.test(getComputedStyle(el).cursor)).length);
  assert.equal(resizable, 0, "something in the docked panel shows a resize cursor");
}

/**
 * Drags with the mouse while watching the page: resolves to how often the page
 * (#view) was resized or moved, and how many resize messages herdr got mid-drag.
 */
async function watchedDrag(page, from, by) {
  await page.evaluate(() => {
    const view = document.querySelector("#view");
    const first = JSON.stringify(view.getBoundingClientRect());
    window.pageWatch = { resized: 0, moved: 0 };
    let initial = true;
    window.pageWatch.observer = new ResizeObserver(() => { if (!initial) window.pageWatch.resized += 1; initial = false; });
    window.pageWatch.observer.observe(view);
    const sample = () => {
      if (!window.pageWatch) return;
      if (JSON.stringify(view.getBoundingClientRect()) !== first) window.pageWatch.moved += 1;
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  const before = (await page.evaluate(() => window.dockHarness.sent())).length;
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  for (let step = 1; step <= 12; step += 1) await page.mouse.move(from.x + (by.x * step) / 12, from.y + (by.y * step) / 12);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const midDrag = (await page.evaluate(() => window.dockHarness.sent())).slice(before)
    .filter((item) => item.text && JSON.parse(item.text).type === "resize").length;
  await page.mouse.up();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const watched = await page.evaluate(() => {
    const { resized, moved, observer } = window.pageWatch;
    observer.disconnect();
    window.pageWatch = null;
    return { resized, moved };
  });
  return { ...watched, midDrag };
}

test("opens on herdr's terminal docked at the bottom at a fixed height; the page ends above it", async () => {
  const { page, problems, context } = await harness();
  await dock(page).waitFor();
  await assertDocked(page, DOCKED.height);
  assert.deepEqual(await saved(page), null, "a fresh dock writes nothing until it changes");
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

test("the docked height has no drag: pointer drags on its top edge change nothing; S/M/L set it in one step", async () => {
  const { page, problems, context } = await harness();
  await dock(page).waitFor();
  const top = await dock(page).boundingBox();
  const watched = await watchedDrag(page, { x: 500, y: top.y + 2 }, { x: 0, y: -200 });
  assert.equal(watched.midDrag, 0);
  await assertDocked(page, DOCKED.height);
  const steps = dock(page).getByRole("group", { name: "Dock height" });
  assert.equal(await steps.getByRole("button", { name: "Medium dock" }).getAttribute("aria-pressed"), "true");
  await steps.getByRole("button", { name: "Large dock" }).click();
  await assertDocked(page, 480);
  assert.equal((await saved(page)).step, "l");
  await steps.getByRole("button", { name: "Small dock" }).click();
  await assertDocked(page, 200);
  await open(page);
  await dock(page).waitFor();
  await assertDocked(page, 200);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Float turns the panel into the corner card that frees the page; Dock to bottom puts it back", async () => {
  const { page, problems, context } = await harness();
  await dock(page).waitFor();
  await page.getByRole("button", { name: "Float dock" }).click();
  assert.deepEqual(await box(dock(page)), DEFAULT_CARD);
  await assertFloating(page);
  assert.equal((await saved(page)).float, true);
  await open(page);
  await dock(page).waitFor();
  await assertFloating(page);
  await page.getByRole("button", { name: "Dock to bottom" }).click();
  await assertDocked(page, DOCKED.height);
  assert.equal((await saved(page)).float, false);
  assert.deepEqual(problems, []);
  await context.close();
});

test("a v0.3.22 floating-only dock loads docked and keeps its card size and position for Float", async () => {
  const { page, problems, context } = await harness({ mode: "activity", shown: "activity", size: { width: 600, height: 380 }, offset: { right: 40, bottom: 90 } });
  await agentCards(page).first().waitFor();
  await assertDocked(page, DOCKED.height);
  await page.getByRole("button", { name: "Float dock" }).click();
  assert.deepEqual(await box(dock(page)), { x: 1000 - 40 - 600, y: 800 - 90 - 380, width: 600, height: 380 });
  await assertFloating(page);
  assert.deepEqual(problems, []);
  await context.close();
});

test("the corner grip resizes the card on release without re-laying out the page; the size is remembered", async () => {
  const { page, problems, context } = await harness(FLOATING);
  await page.waitForFunction(() => window.dockHarness.sockets[0]?.readyState === 1);
  const rows = Number(new URL(await page.evaluate(() => window.dockHarness.sockets[0].url)).searchParams.get("rows"));
  const grip = page.getByRole("button", { name: "Resize dock" });
  const handle = await grip.boundingBox();
  const watched = await watchedDrag(page, { x: handle.x + handle.width / 2, y: handle.y + handle.height / 2 }, { x: -60, y: -120 });
  assert.deepEqual(watched, { resized: 0, moved: 0, midDrag: 0 }, "dragging the grip re-laid out the page or refit the terminal every frame");
  // Anchored bottom-right, the card grows up and to the left.
  assert.deepEqual(await box(dock(page)), { x: DEFAULT_CARD.x - 60, y: DEFAULT_CARD.y - 120, width: 780, height: 560 });
  assert.deepEqual((await saved(page)).size, { width: 780, height: 560 });
  // The terminal follows the card once: herdr is told the taller size.
  await page.waitForFunction((before) => window.dockHarness.sent().some((item) =>
    item.text && JSON.parse(item.text).type === "resize" && JSON.parse(item.text).rows > before), rows);
  await grip.focus();
  await page.keyboard.press("ArrowUp");
  assert.equal(await height(dock(page)), 576);
  await page.keyboard.press("ArrowRight");
  assert.equal(Math.round((await dock(page).boundingBox()).width), 764);
  // Never larger than the window, never smaller than usable.
  const far = await grip.boundingBox();
  await watchedDrag(page, { x: far.x + 4, y: far.y + 4 }, { x: -2000, y: -2000 });
  assert.deepEqual(await box(dock(page)), { x: 8, y: 8, width: 1000 - 16, height: 800 - 16 });
  // A reload keeps the remembered size.
  await grip.focus();
  await page.keyboard.press("ArrowDown");
  await open(page);
  await dock(page).waitFor();
  assert.equal(await height(dock(page)), 800 - 16 - 16);
  await assertFloating(page);
  assert.deepEqual(problems, []);
  await context.close();
});

test("dragging the header moves the card without re-laying out the page; it stays on screen and is remembered", async () => {
  const { page, problems, context } = await harness(FLOATING);
  await dock(page).waitFor();
  const bar = dock(page).locator(".dock-bar");
  const tabs = await dock(page).getByRole("group", { name: "Dock contents" }).boundingBox();
  const from = { x: tabs.x + tabs.width + 24, y: tabs.y + tabs.height / 2 };
  assert.ok(await bar.evaluate((node, [x, y]) => node === document.elementFromPoint(x, y), [from.x, from.y]), "the header has no free space to grab");
  const watched = await watchedDrag(page, from, { x: -200, y: -100 });
  assert.deepEqual(watched, { resized: 0, moved: 0, midDrag: 0 }, "moving the card re-laid out the page or refit the terminal");
  assert.deepEqual(await box(dock(page)), { ...DEFAULT_CARD, x: DEFAULT_CARD.x - 200, y: DEFAULT_CARD.y - 100 });
  assert.deepEqual((await saved(page)).offset, { right: 216, bottom: 116 });
  // Flung past the edge, the card stops at the window's margin.
  await watchedDrag(page, { x: from.x - 200, y: from.y - 100 }, { x: -3000, y: -3000 });
  assert.deepEqual(await box(dock(page)), { ...DEFAULT_CARD, x: 8, y: 8 });
  await open(page);
  await dock(page).waitFor();
  assert.deepEqual(await box(dock(page)), { ...DEFAULT_CARD, x: 8, y: 8 });
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

test("the agent-activity cards show herdr's states with each agent's workspace and detach the terminal", async () => {
  const { page, problems, context } = await harness();
  await page.waitForFunction(() => window.dockHarness.sockets.length === 1);
  await page.getByRole("button", { name: "Agents" }).click();
  await page.waitForFunction(() => window.dockHarness.sockets[0].readyState === 3);
  assert.equal(await dock(page).locator("[data-kitty-terminal]").count(), 0);
  const cards = agentCards(page);
  await cards.first().waitFor();
  assert.equal(await cards.count(), 3, "not one card per agent (a workspace without agents has none)");
  const card = (name) => cards.filter({ has: page.locator("header strong", { hasText: new RegExp(`^${name}$`) }) });
  assert.match(await card("omp").innerText(), /working/i);
  assert.match(await card("omp").innerText(), /\bapi\b/i);
  assert.match(await card("Codex").innerText(), /blocked/i);
  assert.match(await card("Codex").innerText(), /\bapi\b/i);
  assert.match(await card("claude").innerText(), /idle/i);
  assert.match(await card("claude").innerText(), /\bdocs\b/i);
  assert.equal(await page.getByRole("button", { name: "Agents" }).getAttribute("aria-pressed"), "true");
  assert.equal((await saved(page)).mode, "activity");
  await open(page);
  await agentCards(page).first().waitFor();
  assert.equal(await page.evaluate(() => window.dockHarness.sockets.length), 0, "the agent cards must not attach a terminal");
  assert.deepEqual(problems, []);
  await context.close();
});

test("hiding the docked panel leaves a thin bottom handle that brings back what was shown", async () => {
  const { page, problems, context } = await harness();
  await page.getByRole("button", { name: "Agents" }).click();
  await page.getByRole("button", { name: "Hide dock" }).click();
  assert.equal(await dock(page).count(), 0);
  const handle = page.getByRole("button", { name: "Show dock" });
  const strip = await box(handle);
  assert.ok(strip.height <= 12 && strip.width === 1000 && strip.y + strip.height === 800, `handle ${JSON.stringify(strip)} is not a thin bottom strip`);
  assert.equal(await dockSpace(page), `${strip.height}px`);
  assert.deepEqual(await box(page.locator("#view")), { x: 0, y: 0, width: 1000, height: 800 - strip.height });
  assert.equal((await saved(page)).mode, "hidden");
  await open(page);
  await handle.click();
  await dock(page).waitFor();
  // The handle brings back what was shown before hiding, even after a reload.
  await agentCards(page).first().waitFor();
  assert.equal((await saved(page)).mode, "activity");
  await assertDocked(page, DOCKED.height);
  assert.deepEqual(problems, []);
  await context.close();
});

test("hiding the floating card collapses it to a small bottom-right pill that brings it back", async () => {
  const { page, problems, context } = await harness(FLOATING);
  await page.getByRole("button", { name: "Agents" }).click();
  await page.getByRole("button", { name: "Hide dock" }).click();
  assert.equal(await dock(page).count(), 0);
  const pill = page.getByRole("button", { name: "Show dock" });
  const pillBox = await pill.boundingBox();
  assert.ok(pillBox.width < 220 && pillBox.height < 48 && pillBox.x > 700 && pillBox.y > 700, `pill at ${JSON.stringify(pillBox)} is not a small bottom-right pill`);
  assert.match(await pill.innerText(), /Agents/);
  assert.deepEqual(await box(page.locator("#view")), { x: 0, y: 0, width: 1000, height: 800 });
  await open(page);
  await pill.click();
  await dock(page).waitFor();
  assert.deepEqual(await box(dock(page)), DEFAULT_CARD);
  await agentCards(page).first().waitFor();
  await assertFloating(page);
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
  const chooser = page.getByRole("group", { name: "Coding agent" });
  const pressed = () => chooser.locator('[aria-pressed="true"]').textContent();
  assert.equal(await pressed(), "omp");
  await chooser.getByRole("button", { name: "codex" }).click();
  await page.getByRole("button", { name: "Start agent" }).click();
  await page.getByRole("status").filter({ hasText: "Agent tab opened" }).waitFor();
  await page.getByRole("button", { name: "New Agent tab" }).click();
  assert.equal(await pressed(), "codex");
  await page.getByRole("button", { name: "Attach external agent" }).click();
  const instructions = page.getByRole("region", { name: "External agent attachment" });
  await instructions.waitFor();
  assert.match(await instructions.innerText(), /--session 'kitty-sample'/);
  assert.match(await instructions.innerText(), /tab create.*--label/);
  assert.match(await instructions.innerText(), /\/sample\/workspace/);
  assert.deepEqual(problems, []);
  await context.close();
});
