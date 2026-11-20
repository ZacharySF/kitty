// The app shell (top bar, Rika's side panel) in a real browser against the disposable
// synthetic backend (`kitty-web --demo`); no private workspace is read.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/shell.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";
import { routeBuild } from "./buildRoutes.mjs";
import { announcement, authorize, exchange } from "./kittyServer.js";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const VIEWPORT = { width: 1568, height: 942 };

let backend;
let secret;
let server;
let browser;
let base;

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createNetServer().once("error", reject).listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

before(async () => {
  backend = spawn("uv", ["run", "--quiet", "kitty-web", "--demo", "--port", String(await freePort())], {
    cwd: repository,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const { origin, code } = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`demo backend did not start:\n${output}`)), 120_000);
    const read = (chunk) => {
      output += chunk;
      const found = announcement(output);
      if (found) { clearTimeout(timer); resolve(found); }
    };
    backend.stdout.on("data", read);
    backend.stderr.on("data", read);
    backend.once("exit", (code) => { clearTimeout(timer); reject(new Error(`demo backend exited (${code}):\n${output}`)); });
  });
  secret = await exchange(origin, code);
  server = await createServer({
    root,
    logLevel: "error",
    server: {
      host: "127.0.0.1",
      port: 0,
      proxy: {
        "/api": {
          target: origin,
          changeOrigin: true,
          ws: true,
          configure: (proxy) => { for (const event of ["proxyReq", "proxyReqWs"]) proxy.on(event, (request) => request.setHeader("Origin", origin)); },
        },
      },
    },
  });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await server?.close();
  backend?.kill();
});

/** A returning user's fresh profile on `hash`, with its own build layer (plus `build`) and no Rika alerts toasting. */
async function open(hash, build = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT });
  await authorize(context, secret);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  await routeBuild(context, build);
  await context.route("**/api/rika/alerts", (route) => route.fulfill({ json: { alerts: [] } }));
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`#${hash}`, base).href);
  return { page, problems, context };
}
const rect = (locator) => locator.evaluate((node) => node.getBoundingClientRect().toJSON());
/** Records whether the page claimed the last Ctrl + R (a browser would reload otherwise). Registered after
 *  the app's own window listeners, so in the capture phase it runs after them and before any element's. */
const watchReload = (page) => page.evaluate(() => window.addEventListener("keydown", (event) => {
  if (event.key.toLowerCase() === "r") window.__reloadClaimed = event.defaultPrevented;
}, { capture: true }));

describe("shell: Settings in the top bar", () => {
  test("a Settings icon button sits right of search at the bar's end and opens Settings", async () => {
    const { page, problems, context } = await open("calendar");
    const bar = page.getByRole("group", { name: "Workspace controls" });
    const search = bar.getByRole("button", { name: /^Search pages/ });
    const settings = bar.getByRole("button", { name: "Settings", exact: true });
    await settings.waitFor();
    assert.ok(await settings.getAttribute("title"), "the Settings button has no tooltip");
    assert.equal((await settings.innerText()).trim(), "", "the Settings button carries words, not an icon");
    const [searchBox, settingsBox, barBox] = await Promise.all([rect(search), rect(settings), rect(page.locator(".system-bar"))]);
    assert.ok(settingsBox.left >= searchBox.right - 1, `Settings (${settingsBox.left}) is not right of search (${searchBox.right})`);
    assert.ok(Math.abs((settingsBox.top + settingsBox.bottom) / 2 - (searchBox.top + searchBox.bottom) / 2) <= 2, "Settings is not on search's row");
    assert.ok(barBox.right - settingsBox.right <= 40, `Settings is not at the bar's end (${barBox.right - settingsBox.right}px short)`);
    await settings.click();
    await page.waitForFunction(() => location.hash.startsWith("#settings"));
    await page.getByRole("heading", { level: 1, name: "Settings" }).waitFor();
    assert.equal(await settings.getAttribute("aria-current"), "page");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("shell: Rika side panel", () => {
  test("Ctrl + R docks Rika at home's width on any page without reloading; Ctrl + R or Escape closes it", async () => {
    const { page, problems, context } = await open("today");
    const home = page.locator(".home .rika-panel");
    await home.waitFor();
    const homeBox = await rect(home);
    // Rika stands on the left of home; the side panel stands on the right, inside home's right margin.
    const margin = await page.locator(".home").evaluate((node) => node.getBoundingClientRect().right - parseFloat(getComputedStyle(node).paddingRight));
    await page.evaluate(() => (location.hash = "calendar"));
    await page.waitForFunction(() => location.hash === "#calendar");
    // The calendar opens on the week, whose way back reads "This week".
    const opener = page.getByRole("button", { name: "This week", exact: true });
    await opener.waitFor();
    await opener.focus();
    await watchReload(page);
    await page.evaluate(() => (window.__shellMarker = "still here"));

    await page.keyboard.press("Control+r");
    const dock = page.getByRole("complementary", { name: "Rika side panel" });
    await dock.waitFor();
    assert.equal(await page.evaluate(() => window.__reloadClaimed), true, "Ctrl + R was left to the browser, which reloads");
    assert.equal(await page.evaluate(() => window.__shellMarker), "still here", "the page reloaded");
    assert.equal(await page.evaluate(() => location.hash), "#calendar", "Ctrl + R navigated away");
    const field = dock.getByRole("textbox", { name: "Ask Rika" });
    await page.waitForFunction(() => document.activeElement?.closest("aside")?.getAttribute("aria-label") === "Rika side panel"
      && document.activeElement.getAttribute("aria-label") === "Ask Rika");
    const dockBox = await rect(page.locator(".rika-dock .rika-panel"));
    assert.ok(Math.abs(dockBox.width - homeBox.width) <= 2, `the panel is ${dockBox.width}px wide, home's ${homeBox.width}px`);
    assert.ok(Math.abs(dockBox.right - margin) <= 2, `the panel's right edge ${dockBox.right} is not home's right margin ${margin}`);
    assert.ok(Math.abs(dockBox.bottom - homeBox.bottom) <= 2, `the panel's foot ${dockBox.bottom} is not home's ${homeBox.bottom}`);
    // The page stays usable beside it: its content ends before the panel begins.
    const mainBox = await rect(page.locator("main"));
    assert.ok(mainBox.right <= dockBox.left, `the page (to ${mainBox.right}) runs under the panel (from ${dockBox.left})`);
    await field.fill("hello");
    assert.equal(await field.inputValue(), "hello");

    await page.keyboard.press("Control+r");
    await dock.waitFor({ state: "detached" });
    assert.ok(await opener.evaluate((node) => node === document.activeElement), "focus did not return to where it was");
    assert.equal(await page.evaluate(() => window.__shellMarker), "still here", "the page reloaded");

    await page.keyboard.press("Control+r");
    await dock.waitFor();
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Ask Rika");
    await page.keyboard.press("Escape");
    await dock.waitFor({ state: "detached" });
    assert.ok(await opener.evaluate((node) => node === document.activeElement), "focus did not return after Escape");

    await page.keyboard.press("Control+r");
    await dock.getByRole("button", { name: "Close Rika" }).click();
    await dock.waitFor({ state: "detached" });
    assert.equal(await page.evaluate(() => window.__shellMarker), "still here");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("on home and on the Rika page Ctrl + R focuses the Rika already shown, with no second panel", async () => {
    const { page, problems, context } = await open("today");
    await page.locator(".home .rika-panel").waitFor();
    await watchReload(page);
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("Control+r");
    await page.waitForFunction(() => document.activeElement?.closest(".home") && document.activeElement.getAttribute("aria-label") === "Ask Rika");
    assert.equal(await page.evaluate(() => window.__reloadClaimed), true, "Ctrl + R was left to the browser on home");
    assert.equal(await page.getByRole("complementary", { name: "Rika side panel" }).count(), 0, "a second Rika docked over home");
    await page.getByRole("navigation", { name: "Workspace groups" }).getByRole("button", { name: "Rika" }).click();
    await page.waitForFunction(() => location.hash === "#rika");
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("Control+r");
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Ask Rika");
    assert.equal(await page.getByRole("complementary", { name: "Rika side panel" }).count(), 0, "a second Rika docked over the Rika page");
    assert.equal(await page.evaluate(() => location.hash), "#rika");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("shell: tab and theme keys", () => {
  /** The open tabs' names in order and which one is active, read from the tab switcher. */
  async function tabState(page) {
    await page.locator(".tab-trigger").click();
    const list = page.getByRole("dialog", { name: "Open tabs" });
    await list.waitFor();
    const names = await list.locator(".tab-name").allTextContents();
    const active = names.indexOf(await list.locator('.tab-row[aria-current="page"] .tab-name').textContent());
    await page.keyboard.press("Escape");
    await list.waitFor({ state: "detached" });
    return { names, active };
  }
  /** Two more tabs on different pages: Home, Calendar, Study. */
  async function threeTabs(page) {
    const groups = page.getByRole("navigation", { name: "Workspace groups" });
    await groups.getByRole("button", { name: "Plan" }).waitFor();
    for (const group of ["Plan", "Study"]) {
      await page.keyboard.press("Control+t");
      await groups.getByRole("button", { name: group }).click();
    }
    await page.getByRole("button", { name: "3 open tabs" }).waitFor();
  }

  test("Ctrl + Tab steps to the next tab and Ctrl + Shift + Tab back, wrapping, even from inside an editor", async () => {
    const { page, problems, context } = await open("today");
    await threeTabs(page);
    const start = await tabState(page);
    assert.equal(start.names.length, 3);
    await page.keyboard.press("Control+Tab");
    assert.equal((await tabState(page)).active, (start.active + 1) % 3);
    await page.keyboard.press("Control+Shift+Tab");
    await page.keyboard.press("Control+Shift+Tab");
    assert.equal((await tabState(page)).active, (start.active + 2) % 3);
    // An editor that takes Tab for itself still lets Ctrl + Tab through.
    await page.evaluate(() => {
      const field = Object.assign(document.createElement("textarea"), { ariaLabel: "Tab-hungry editor" });
      field.addEventListener("keydown", (event) => { if (event.key === "Tab") { event.preventDefault(); event.stopPropagation(); } });
      document.getElementById("main-content").append(field);
      field.focus();
    });
    await page.keyboard.press("Control+Tab");
    assert.equal((await tabState(page)).active, start.active);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Ctrl + O lays every tab out as a card: Escape changes nothing, arrows and Enter or a click switch", async () => {
    const { page, problems, context } = await open("today");
    await threeTabs(page);
    const start = await tabState(page);
    await page.evaluate(() => window.addEventListener("keydown", (event) => {
      if (event.key.toLowerCase() === "o") window.__openClaimed = event.defaultPrevented;
    }, { capture: true }));
    await page.keyboard.press("Control+o");
    const overview = page.getByRole("dialog", { name: "All tabs" });
    await overview.waitFor();
    assert.equal(await page.evaluate(() => window.__openClaimed), true, "Ctrl + O was left to the browser's open-file");
    const cards = overview.getByRole("button", { name: /^Go to / });
    assert.deepEqual((await cards.allTextContents()).map((text) => start.names.find((name) => text.includes(name))), start.names);
    assert.equal(await cards.nth(start.active).getAttribute("aria-current"), "page");
    assert.ok(await cards.nth(start.active).evaluate((node) => node === document.activeElement), "the active tab's card is not focused");
    const boxes = await Promise.all([0, 1, 2].map((index) => rect(cards.nth(index))));
    assert.ok(boxes.every((box) => box.width >= 160 && box.height >= 100), "the cards are too small to pick");
    await page.keyboard.press("Escape");
    await overview.waitFor({ state: "detached" });
    assert.equal((await tabState(page)).active, start.active);
    await page.keyboard.press("Control+o");
    await overview.waitFor();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Enter");
    await overview.waitFor({ state: "detached" });
    assert.equal((await tabState(page)).active, (start.active + 1) % 3);
    await page.keyboard.press("Control+o");
    await cards.nth(start.active).click();
    await overview.waitFor({ state: "detached" });
    assert.equal((await tabState(page)).active, start.active);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Ctrl + Y slides to the next theme and shows it live; Escape puts the old one back, Enter keeps it after a reload", async () => {
    const { page, problems, context } = await open("today", { ui: { themes: { installed: ["aero", "blossom", "nord"] } } });
    await page.locator(".home .rika-panel").waitFor();
    const theme = () => page.evaluate(() => document.documentElement.dataset.theme);
    const first = await theme();
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("Control+y");
    const carousel = page.getByRole("dialog", { name: "Themes" });
    await carousel.waitFor();
    await page.waitForFunction((first) => document.documentElement.dataset.theme !== first, first);
    const second = await theme();
    const centre = carousel.locator('[aria-current="true"]');
    assert.equal(await centre.count(), 1);
    await page.keyboard.press("Control+y");
    await page.waitForFunction((second) => document.documentElement.dataset.theme !== second, second);
    const third = await theme();
    assert.notEqual(third, first);
    // Once the strip has slid into place, the centred card is the biggest.
    await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState !== "running" || !animation.effect?.target?.matches?.("[data-theme-card]")));
    const sizes = await carousel.locator("[data-theme-card]").evaluateAll((nodes) => nodes.map((node) => [node.getAttribute("aria-current") === "true", node.getBoundingClientRect().width]));
    const middle = sizes.find(([on]) => on)[1];
    assert.ok(sizes.every(([on, width]) => on || width < middle), "the centred theme is not the largest card");
    await page.keyboard.press("Escape");
    await carousel.waitFor({ state: "detached" });
    assert.equal(await theme(), first, "Escape kept the previewed theme");
    await page.keyboard.press("Control+y");
    await carousel.waitFor();
    await page.waitForFunction((first) => document.documentElement.dataset.theme !== first, first);
    const kept = await theme();
    const saved = page.waitForResponse((response) => response.url().endsWith("/api/build/preferences"));
    await page.keyboard.press("Enter");
    await carousel.waitFor({ state: "detached" });
    assert.equal((await saved).status(), 200);
    await page.reload();
    await page.locator(".home .rika-panel").waitFor();
    assert.equal(await theme(), kept, "the kept theme did not survive a reload");
    // In a text field Ctrl + Y stays the field's own (redo).
    await page.getByRole("textbox", { name: "Ask Rika" }).first().focus();
    await page.keyboard.press("Control+y");
    await page.waitForTimeout(200);
    assert.equal(await carousel.count(), 0, "Ctrl + Y in a text field opened the carousel");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("shell: arrow keys", () => {
  /** The label of the active button in a bar. */
  const active = (bar) => bar.locator('[aria-current="page"], [aria-pressed="true"]').first().innerText();

  test("← / → step through the page's own tabs, never while typing; Ctrl + ← / → step through the top bar's groups", async () => {
    const { page, problems, context } = await open("study");
    const study = page.getByRole("navigation", { name: "Study tools" });
    await study.getByRole("button", { name: "Session", exact: true }).waitFor();
    await page.locator("body").click({ position: { x: 5, y: 600 } });
    assert.equal((await active(study)).trim(), "Session");
    await page.keyboard.press("ArrowRight");
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Study tools"] [aria-pressed="true"]')?.textContent?.trim() === "Progress");
    await page.keyboard.press("ArrowLeft");
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Study tools"] [aria-pressed="true"]')?.textContent?.trim() === "Session");
    // In a text field the arrows move the caret, not the tabs.
    await page.evaluate(() => {
      const field = Object.assign(document.createElement("input"), { ariaLabel: "Scratch field", value: "abc" });
      document.getElementById("main-content").append(field);
      field.focus();
    });
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Control+ArrowRight");
    await page.waitForTimeout(200);
    assert.equal((await active(study)).trim(), "Session", "an arrow in a text field changed the page's tab");
    assert.equal(await page.evaluate(() => location.hash), "#study", "Ctrl + → in a text field changed the group");
    await page.evaluate(() => document.querySelector('[aria-label="Scratch field"]').remove());

    // Ctrl + → / ← walk the top bar's groups in its order.
    const groups = page.getByRole("navigation", { name: "Workspace groups" });
    const names = (await groups.getByRole("button").allInnerTexts()).map((text) => text.trim());
    const at = names.indexOf("Study");
    await page.locator("body").click({ position: { x: 5, y: 600 } });
    await page.keyboard.press("Control+ArrowRight");
    await page.waitForFunction((name) => [...document.querySelectorAll('nav[aria-label="Workspace groups"] button')].find((button) => button.getAttribute("aria-current") === "page")?.textContent?.trim() === name, names[(at + 1) % names.length]);
    await page.keyboard.press("Control+ArrowLeft");
    await page.waitForFunction(() => [...document.querySelectorAll('nav[aria-label="Workspace groups"] button')].find((button) => button.getAttribute("aria-current") === "page")?.textContent?.trim() === "Study");

    // Career opens on its Overview, marked on its rail; the rail is links, not page tabs, so → stays put.
    await groups.getByRole("button", { name: "Career" }).click();
    const career = page.getByRole("navigation", { name: "Career" });
    await page.waitForFunction(() => location.hash === "#career");
    assert.equal((await active(career)).trim(), "Overview");
    await page.locator("body").click({ position: { x: 5, y: 600 } });
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => location.hash), "#career", "→ left the Career overview");
    assert.deepEqual(problems, []);
    await context.close();
  });
});
